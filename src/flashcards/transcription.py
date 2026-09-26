from __future__ import annotations

import hashlib
import json
import math
import os
import stat
import tempfile
import threading
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, Self, runtime_checkable

from .models import BaseToken, Utterance
from .subtitles import tokenize_base_units


TRANSCRIPTION_CACHE_VERSION = 1
TRANSCRIPTION_CONVERTER_VERSION = "1"

_BOUNDARY_TOLERANCE_MS = 250.0


class TranscriptionError(Exception):
    """Base error for transcription infrastructure and ASR failures."""


class TranscriptionValidationError(TranscriptionError, ValueError):
    """Raised when transcription data does not match the required shape."""


def _require_string(value: object, context: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise TranscriptionValidationError(f"{context} must be a nonempty string")
    return value


def _require_number(value: object, context: str) -> int | float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TranscriptionValidationError(f"{context} must be a finite nonnegative number")
    if not math.isfinite(value) or value < 0:
        raise TranscriptionValidationError(f"{context} must be a finite nonnegative number")
    return value


def _validate_interval(start_ms: object, end_ms: object, context: str) -> None:
    start = _require_number(start_ms, f"{context}.start_ms")
    end = _require_number(end_ms, f"{context}.end_ms")
    if end <= start:
        raise TranscriptionValidationError(
            f"{context}.end_ms must be greater than {context}.start_ms"
        )


def _validate_confidence(value: object, context: str) -> None:
    if value is None:
        return
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TranscriptionValidationError(f"{context} must be a finite number between 0 and 1")
    if not math.isfinite(value) or not 0 <= value <= 1:
        raise TranscriptionValidationError(f"{context} must be between 0 and 1")


def _require_mapping(value: object, context: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise TranscriptionValidationError(f"{context} must be an object")
    return value


def _require_sequence(value: object, context: str) -> Sequence[Any]:
    if isinstance(value, (str, bytes, bytearray)) or not isinstance(value, Sequence):
        raise TranscriptionValidationError(f"{context} must be an array")
    return value


def _require_keys(
    data: Mapping[str, Any],
    required: set[str],
    context: str,
    *,
    optional: set[str] | None = None,
) -> None:
    optional = optional or set()
    keys = set(data)
    missing = sorted(required - keys)
    if missing:
        raise TranscriptionValidationError(f"missing {context}.{missing[0]}")
    unexpected = keys - required - optional
    if unexpected:
        key = min((repr(item) for item in unexpected))
        raise TranscriptionValidationError(f"unexpected {context} field {key}")


@dataclass(frozen=True, slots=True)
class TranscribedWord:
    text: str
    start_ms: float
    end_ms: float
    confidence: float | None = None

    def __post_init__(self) -> None:
        _require_string(self.text, "TranscribedWord.text")
        _validate_interval(self.start_ms, self.end_ms, "TranscribedWord")
        _validate_confidence(self.confidence, "TranscribedWord.confidence")

    def to_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "start_ms": self.start_ms,
            "end_ms": self.end_ms,
            "confidence": self.confidence,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> Self:
        data = _require_mapping(value, "transcribed word")
        _require_keys(
            data,
            {"text", "start_ms", "end_ms"},
            "transcribed word",
            optional={"confidence"},
        )
        return cls(
            text=data["text"],
            start_ms=data["start_ms"],
            end_ms=data["end_ms"],
            confidence=data.get("confidence"),
        )


@dataclass(frozen=True, slots=True)
class TranscribedSegment:
    text: str
    start_ms: float
    end_ms: float
    words: tuple[TranscribedWord, ...] = ()
    confidence: float | None = None

    def __post_init__(self) -> None:
        _require_string(self.text, "TranscribedSegment.text")
        _validate_interval(self.start_ms, self.end_ms, "TranscribedSegment")
        _validate_confidence(self.confidence, "TranscribedSegment.confidence")
        words = _require_sequence(self.words, "TranscribedSegment.words")
        for index, word in enumerate(words):
            if not isinstance(word, TranscribedWord):
                raise TranscriptionValidationError(
                    f"TranscribedSegment.words[{index}] must be a TranscribedWord"
                )
        object.__setattr__(self, "words", tuple(words))

    def to_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "start_ms": self.start_ms,
            "end_ms": self.end_ms,
            "words": [word.to_dict() for word in self.words],
            "confidence": self.confidence,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> Self:
        data = _require_mapping(value, "transcribed segment")
        _require_keys(
            data,
            {"text", "start_ms", "end_ms", "words"},
            "transcribed segment",
            optional={"confidence"},
        )
        words = _require_sequence(data["words"], "transcribed segment.words")
        return cls(
            text=data["text"],
            start_ms=data["start_ms"],
            end_ms=data["end_ms"],
            words=tuple(TranscribedWord.from_dict(word) for word in words),
            confidence=data.get("confidence"),
        )


@dataclass(frozen=True, slots=True)
class TranscriptionResult:
    language: str
    segments: tuple[TranscribedSegment, ...]
    engine: str
    model: str
    engine_version: str
    settings: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _require_string(self.language, "TranscriptionResult.language")
        _require_string(self.engine, "TranscriptionResult.engine")
        _require_string(self.model, "TranscriptionResult.model")
        _require_string(self.engine_version, "TranscriptionResult.engine_version")

        segments = _require_sequence(self.segments, "TranscriptionResult.segments")
        for index, segment in enumerate(segments):
            if not isinstance(segment, TranscribedSegment):
                raise TranscriptionValidationError(
                    f"TranscriptionResult.segments[{index}] must be a TranscribedSegment"
                )
        object.__setattr__(self, "segments", tuple(segments))

        settings = _require_sequence(self.settings, "TranscriptionResult.settings")
        for index, setting in enumerate(settings):
            _require_string(setting, f"TranscriptionResult.settings[{index}]")
        object.__setattr__(self, "settings", tuple(settings))

    def to_dict(self) -> dict[str, Any]:
        return {
            "language": self.language,
            "segments": [segment.to_dict() for segment in self.segments],
            "engine": self.engine,
            "model": self.model,
            "engine_version": self.engine_version,
            "settings": list(self.settings),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> Self:
        data = _require_mapping(value, "transcription result")
        fields = {
            "language",
            "segments",
            "engine",
            "model",
            "engine_version",
            "settings",
        }
        _require_keys(data, fields, "transcription result")
        segments = _require_sequence(data["segments"], "transcription result.segments")
        settings = _require_sequence(data["settings"], "transcription result.settings")
        return cls(
            language=data["language"],
            segments=tuple(TranscribedSegment.from_dict(segment) for segment in segments),
            engine=data["engine"],
            model=data["model"],
            engine_version=data["engine_version"],
            settings=tuple(settings),
        )


@runtime_checkable
class Transcriber(Protocol):
    def identity(self) -> Mapping[str, Any]: ...

    def transcribe(self, audio_path: Path, language: str) -> TranscriptionResult: ...


class TranscriptionCache:
    def __init__(self, path: str | os.PathLike[str]) -> None:
        self.path = Path(path)
        self._lock = threading.RLock()
        self._loaded = False
        self._entries: dict[str, dict[str, Any]] = {}

    def _load_locked(self) -> None:
        if self._loaded:
            return
        entries: dict[str, dict[str, Any]] = {}
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, UnicodeDecodeError, json.JSONDecodeError):
            self._entries = entries
            self._loaded = True
            return
        if (
            not isinstance(value, Mapping)
            or set(value) != {"schema_version", "entries"}
            or type(value.get("schema_version")) is not int
            or value.get("schema_version") != TRANSCRIPTION_CACHE_VERSION
            or not isinstance(value.get("entries"), Mapping)
        ):
            self._entries = entries
            self._loaded = True
            return
        for key, entry in value["entries"].items():
            if not isinstance(key, str) or not key or not isinstance(entry, Mapping):
                continue
            try:
                entries[key] = TranscriptionResult.from_dict(entry).to_dict()
            except (TranscriptionValidationError, TypeError, ValueError):
                continue
        self._entries = entries
        self._loaded = True

    def get(self, key: str) -> TranscriptionResult | None:
        if not isinstance(key, str) or not key:
            raise ValueError("cache key must be a nonempty string")
        with self._lock:
            self._load_locked()
            value = self._entries.get(key)
            if value is None:
                return None
            try:
                return TranscriptionResult.from_dict(value)
            except (TranscriptionValidationError, TypeError, ValueError):
                return None

    def put(self, key: str, result: TranscriptionResult) -> None:
        if not isinstance(key, str) or not key:
            raise ValueError("cache key must be a nonempty string")
        if not isinstance(result, TranscriptionResult):
            raise TypeError("result must be a TranscriptionResult")
        serialized = result.to_dict()
        with self._lock:
            self._load_locked()
            entries = dict(self._entries)
            entries[key] = serialized
            self._write_locked(
                {
                    "schema_version": TRANSCRIPTION_CACHE_VERSION,
                    "entries": entries,
                }
            )
            self._entries = entries

    def _write_locked(self, payload: Mapping[str, Any]) -> None:
        encoded = (
            json.dumps(
                payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
            + "\n"
        )
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            try:
                mode = stat.S_IMODE(self.path.stat().st_mode)
            except FileNotFoundError:
                mode = 0o600
            descriptor, temporary_name = tempfile.mkstemp(
                prefix=f".{self.path.name}.",
                suffix=".tmp",
                dir=self.path.parent,
            )
        except OSError as exc:
            raise TranscriptionError(
                f"Could not prepare transcription cache {self.path}: {exc}"
            ) from exc

        temporary_path = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
                if hasattr(os, "fchmod"):
                    os.fchmod(handle.fileno(), mode)
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            if not hasattr(os, "fchmod"):
                os.chmod(temporary_path, mode)
            os.replace(temporary_path, self.path)
        except OSError as exc:
            try:
                temporary_path.unlink()
            except FileNotFoundError:
                pass
            raise TranscriptionError(
                f"Could not write transcription cache {self.path}: {exc}"
            ) from exc


class CachedTranscriber:
    def __init__(self, transcriber: Transcriber, cache: TranscriptionCache) -> None:
        if not callable(getattr(transcriber, "identity", None)) or not callable(
            getattr(transcriber, "transcribe", None)
        ):
            raise TypeError("transcriber must implement identity() and transcribe()")
        if not isinstance(cache, TranscriptionCache):
            raise TypeError("cache must be a TranscriptionCache")
        self.transcriber = transcriber
        self.cache = cache
        self._lock = threading.RLock()

    def transcribe(
        self,
        audio_path: Path,
        media_checksum: str,
        language: str,
        refresh: bool = False,
    ) -> TranscriptionResult:
        try:
            path = Path(audio_path)
        except TypeError as exc:
            raise TypeError("audio_path must be path-like") from exc
        media_checksum = _require_string(media_checksum, "media_checksum")
        language = _require_string(language, "language")
        if type(refresh) is not bool:
            raise TypeError("refresh must be a boolean")

        with self._lock:
            identity = self._validated_identity()
            key = self._cache_key(media_checksum, language, identity)
            if not refresh:
                cached = self.cache.get(key)
                if cached is not None:
                    try:
                        self._validate_result_identity(cached, identity, language)
                    except TranscriptionValidationError:
                        pass
                    else:
                        return cached

            result = self.transcriber.transcribe(path, language)
            self._validate_result_identity(result, identity, language)
            self.cache.put(key, result)
            return result

    def _validated_identity(self) -> dict[str, Any]:
        value = self.transcriber.identity()
        if not isinstance(value, Mapping):
            raise TranscriptionValidationError("transcriber identity must be an object")
        identity = dict(value)
        for key in identity:
            if not isinstance(key, str) or not key:
                raise TranscriptionValidationError(
                    "transcriber identity keys must be nonempty strings"
                )
        try:
            json.dumps(
                identity,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
        except (TypeError, ValueError) as exc:
            raise TranscriptionValidationError(
                "transcriber identity must contain deterministic JSON values"
            ) from exc
        return identity

    @staticmethod
    def _cache_key(
        media_checksum: str,
        language: str,
        identity: Mapping[str, Any],
    ) -> str:
        value = {
            "media_checksum": media_checksum,
            "language": language,
            "converter_version": TRANSCRIPTION_CONVERTER_VERSION,
            "transcriber": identity,
        }
        canonical = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    @staticmethod
    def _validate_result_identity(
        result: TranscriptionResult,
        identity: Mapping[str, Any],
        requested_language: str,
    ) -> None:
        if not isinstance(result, TranscriptionResult):
            raise TranscriptionValidationError(
                "transcriber must return a TranscriptionResult"
            )
        result_language = (
            result.language.strip().casefold().replace("_", "-").split("-", 1)[0]
        )
        expected_language = (
            requested_language.strip().casefold().replace("_", "-").split("-", 1)[0]
        )
        if result_language != expected_language:
            raise TranscriptionValidationError(
                f"transcription language {result.language!r} does not match "
                f"requested language {requested_language!r}"
            )
        comparisons = (
            ("engine", result.engine),
            ("backend", result.engine),
            ("model", result.model),
            ("engine_version", result.engine_version),
            ("version", result.engine_version),
        )
        for key, actual in comparisons:
            if key in identity and identity[key] != actual:
                raise TranscriptionValidationError(
                    f"transcription {key} does not match transcriber identity"
                )
        if "settings" in identity:
            settings = identity["settings"]
            if isinstance(settings, (str, bytes, bytearray)) or not isinstance(
                settings, Sequence
            ):
                raise TranscriptionValidationError(
                    "transcriber identity settings must be an array"
                )
            if tuple(settings) != result.settings:
                raise TranscriptionValidationError(
                    "transcription settings do not match transcriber identity"
                )


def _clamp_interval(
    start_ms: int | float,
    end_ms: int | float,
    lower_ms: int | float,
    upper_ms: int | float,
) -> tuple[int | float, int | float] | None:
    if start_ms < lower_ms:
        if lower_ms - start_ms > _BOUNDARY_TOLERANCE_MS:
            return None
        start_ms = lower_ms
    if end_ms > upper_ms:
        if end_ms - upper_ms > _BOUNDARY_TOLERANCE_MS:
            return None
        end_ms = upper_ms
    if start_ms >= upper_ms or end_ms <= lower_ms or end_ms <= start_ms:
        return None
    return start_ms, end_ms


def _can_receive_word_timing(token: BaseToken) -> bool:
    return any(
        character == "_" or unicodedata.category(character)[0] in {"L", "M", "N"}
        for character in token.text
    )


def _utterance_id(
    media_id: str,
    start_ms: int | float,
    end_ms: int | float,
    text: str,
) -> str:
    identity = {
        "media_id": media_id,
        "start_ms": float(start_ms).hex(),
        "end_ms": float(end_ms).hex(),
        "text": text,
    }
    canonical = json.dumps(
        identity,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return f"utterance-{digest[:24]}"


def utterances_from_transcription(
    result: TranscriptionResult,
    media_id: str,
    media_duration_ms: float,
    time_offset_ms: float = 0,
) -> tuple[Utterance, ...]:
    if not isinstance(result, TranscriptionResult):
        raise TypeError("result must be a TranscriptionResult")
    media_id = _require_string(media_id, "media_id")
    duration = _require_number(media_duration_ms, "media_duration_ms")
    time_offset = _require_number(time_offset_ms, "time_offset_ms")
    if duration == 0:
        raise TranscriptionValidationError("media_duration_ms must be greater than 0")

    provenance = (
        "transcript:asr",
        f"engine:{result.engine}",
        f"model:{result.model}",
        f"version:{result.engine_version}",
        f"source-offset-ms:{time_offset:g}",
        *result.settings,
    )
    utterances: list[Utterance] = []
    for segment in result.segments:
        segment_interval = _clamp_interval(
            segment.start_ms + time_offset,
            segment.end_ms + time_offset,
            0,
            duration,
        )
        if segment_interval is None:
            continue
        start_ms, end_ms = segment_interval
        text = unicodedata.normalize("NFC", segment.text).strip()
        if not text:
            continue
        try:
            base_tokens = tokenize_base_units(text)
        except ValueError:
            continue
        if not base_tokens:
            continue

        timings: dict[int, tuple[int | float, int | float, float | None]] = {}
        timed_confidences: list[float] = []
        cursor = 0
        previous_interval: tuple[int | float, int | float] | None = None
        for word in segment.words:
            surface = unicodedata.normalize("NFC", word.text).strip()
            if not surface:
                continue
            match_start = text.find(surface, cursor)
            if match_start < 0:
                continue
            match_end = match_start + len(surface)
            cursor = match_end

            word_interval = _clamp_interval(
                word.start_ms + time_offset,
                word.end_ms + time_offset,
                start_ms,
                end_ms,
            )
            if word_interval is None:
                continue
            word_start, word_end = word_interval
            if previous_interval is not None and (
                word_start < previous_interval[0] or word_end < previous_interval[1]
            ):
                continue
            token_indexes = [
                index
                for index, token in enumerate(base_tokens)
                if token.span.start_char >= match_start
                and token.span.end_char <= match_end
                and _can_receive_word_timing(token)
            ]
            if not token_indexes:
                continue
            for index in token_indexes:
                timings[index] = (word_start, word_end, word.confidence)
            previous_interval = word_interval
            if word.confidence is not None:
                timed_confidences.append(float(word.confidence))

        tokens = tuple(
            BaseToken(
                text=token.text,
                span=token.span,
                start_ms=None if index not in timings else timings[index][0],
                end_ms=None if index not in timings else timings[index][1],
                confidence=None if index not in timings else timings[index][2],
            )
            for index, token in enumerate(base_tokens)
        )
        confidence = (
            sum(timed_confidences) / len(timed_confidences)
            if timed_confidences
            else segment.confidence
        )
        try:
            utterance = Utterance(
                id=_utterance_id(media_id, start_ms, end_ms, text),
                media_id=media_id,
                text=text,
                start_ms=start_ms,
                end_ms=end_ms,
                tokens=tokens,
                confidence=confidence,
                provenance=provenance,
            )
        except ValueError:
            continue
        utterances.append(utterance)

    if not utterances:
        raise TranscriptionError("no speech was detected within the media duration")
    return tuple(utterances)
