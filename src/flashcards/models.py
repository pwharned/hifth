from __future__ import annotations

import json
import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Self


CURRENT_SCHEMA_VERSION = 1
MEDIA_ARTIFACT_TYPE = "media"


def _require_string(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a nonempty string")
    return value


def _optional_string(value: object, name: str) -> str | None:
    if value is None:
        return None
    return _require_string(value, name)


def _require_int(value: object, name: str, *, minimum: int = 0) -> int:
    if type(value) is not int or value < minimum:
        raise ValueError(f"{name} must be an integer greater than or equal to {minimum}")
    return value


def _require_number(value: object, name: str, *, minimum: float = 0) -> int | float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a number greater than or equal to {minimum}")
    if not math.isfinite(value) or value < minimum:
        raise ValueError(f"{name} must be a number greater than or equal to {minimum}")
    return value


def _validate_interval(start: object, end: object, prefix: str) -> None:
    start_value = _require_number(start, f"{prefix}.start_ms")
    end_value = _require_number(end, f"{prefix}.end_ms")
    if end_value <= start_value:
        raise ValueError(f"{prefix}.end_ms must be greater than {prefix}.start_ms")


def _validate_confidence(value: object, name: str) -> None:
    if value is None:
        return
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a number between 0 and 1")
    if not math.isfinite(value) or not 0 <= value <= 1:
        raise ValueError(f"{name} must be between 0 and 1")


def _as_tuple(value: object, name: str, *, none_as_empty: bool = False) -> tuple[Any, ...]:
    if value is None and none_as_empty:
        return ()
    if isinstance(value, (str, bytes, bytearray)) or not isinstance(value, Sequence):
        raise ValueError(f"{name} must be a sequence")
    return tuple(value)


def _string_tuple(value: object, name: str) -> tuple[str, ...]:
    values = _as_tuple(value, name, none_as_empty=True)
    for index, item in enumerate(values):
        _require_string(item, f"{name}[{index}]")
    return values


@dataclass(frozen=True, slots=True)
class TextSpan:
    start_char: int
    end_char: int

    def __post_init__(self) -> None:
        _require_int(self.start_char, "start_char")
        _require_int(self.end_char, "end_char")
        if self.end_char <= self.start_char:
            raise ValueError("end_char must be greater than start_char")

    def extract(self, text: str) -> str:
        if not isinstance(text, str):
            raise ValueError("text must be a string")
        if self.end_char > len(text):
            raise ValueError(
                f"span [{self.start_char}, {self.end_char}) exceeds text length {len(text)}"
            )
        return text[self.start_char : self.end_char]


@dataclass(frozen=True, slots=True)
class MediaSource:
    id: str
    path: str
    language: str
    title: str | None = None
    source_url: str | None = None
    checksum_sha256: str | None = None
    duration_ms: float | None = None

    def __post_init__(self) -> None:
        _require_string(self.id, "MediaSource.id")
        _require_string(self.path, "MediaSource.path")
        _require_string(self.language, "MediaSource.language")
        _optional_string(self.title, "MediaSource.title")
        _optional_string(self.source_url, "MediaSource.source_url")
        if self.checksum_sha256 is not None:
            if not isinstance(self.checksum_sha256, str) or not re.fullmatch(
                r"[0-9a-fA-F]{64}", self.checksum_sha256
            ):
                raise ValueError(
                    "MediaSource.checksum_sha256 must contain 64 hexadecimal characters"
                )
        if self.duration_ms is not None:
            _require_number(self.duration_ms, "MediaSource.duration_ms")
            if self.duration_ms == 0:
                raise ValueError("MediaSource.duration_ms must be greater than 0")


@dataclass(frozen=True, slots=True)
class BaseToken:
    text: str
    span: TextSpan
    start_ms: float | None = None
    end_ms: float | None = None
    confidence: float | None = None

    def __post_init__(self) -> None:
        _require_string(self.text, "BaseToken.text")
        if not isinstance(self.span, TextSpan):
            raise ValueError("BaseToken.span must be a TextSpan")
        if (self.start_ms is None) != (self.end_ms is None):
            raise ValueError("BaseToken.start_ms and BaseToken.end_ms must be provided together")
        if self.start_ms is not None:
            _validate_interval(self.start_ms, self.end_ms, "BaseToken")
        _validate_confidence(self.confidence, "BaseToken.confidence")


@dataclass(frozen=True, slots=True)
class Utterance:
    id: str
    media_id: str
    text: str
    start_ms: float
    end_ms: float
    tokens: tuple[BaseToken, ...]
    translation: str | None = None
    confidence: float | None = None
    provenance: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _require_string(self.id, "Utterance.id")
        _require_string(self.media_id, "Utterance.media_id")
        _require_string(self.text, "Utterance.text")
        _validate_interval(self.start_ms, self.end_ms, "Utterance")
        _optional_string(self.translation, "Utterance.translation")
        _validate_confidence(self.confidence, "Utterance.confidence")

        tokens = _as_tuple(self.tokens, "Utterance.tokens")
        provenance = _string_tuple(self.provenance, "Utterance.provenance")
        object.__setattr__(self, "tokens", tokens)
        object.__setattr__(self, "provenance", provenance)

        previous_span: TextSpan | None = None
        previous_timed_token: BaseToken | None = None
        for index, token in enumerate(tokens):
            if not isinstance(token, BaseToken):
                raise ValueError(f"Utterance.tokens[{index}] must be a BaseToken")
            if token.span.extract(self.text) != token.text:
                raise ValueError(
                    f"Utterance.tokens[{index}].text does not match its source span"
                )
            if previous_span is not None and token.span.start_char < previous_span.end_char:
                raise ValueError("Utterance token character spans must be ordered and non-overlapping")
            previous_span = token.span

            if token.start_ms is not None:
                if token.start_ms < self.start_ms or token.end_ms > self.end_ms:
                    raise ValueError(f"Utterance.tokens[{index}] falls outside utterance audio times")
                if previous_timed_token is not None and (
                    token.start_ms < previous_timed_token.start_ms
                    or token.end_ms < previous_timed_token.end_ms
                ):
                    raise ValueError("Utterance token audio times must be ordered")
                previous_timed_token = token

    def _validate_token_range(self, start: int, end: int) -> None:
        _require_int(start, "start token index")
        _require_int(end, "end token index")
        if start >= end:
            raise ValueError("end token index must be greater than start token index")
        if end > len(self.tokens):
            raise ValueError(f"token range [{start}, {end}) exceeds {len(self.tokens)} tokens")

    def span_for_tokens(self, start: int, end: int) -> TextSpan:
        self._validate_token_range(start, end)
        return TextSpan(self.tokens[start].span.start_char, self.tokens[end - 1].span.end_char)

    def audio_times_for_tokens(self, start: int, end: int) -> tuple[float, float]:
        self._validate_token_range(start, end)
        start_ms = self.tokens[start].start_ms
        end_ms = self.tokens[end - 1].end_ms
        return (
            self.start_ms if start_ms is None else start_ms,
            self.end_ms if end_ms is None else end_ms,
        )


@dataclass(frozen=True, slots=True)
class AudioSpan:
    media_id: str
    start_ms: float
    end_ms: float
    padding_before_ms: float = 250
    padding_after_ms: float = 250

    def __post_init__(self) -> None:
        _require_string(self.media_id, "AudioSpan.media_id")
        _validate_interval(self.start_ms, self.end_ms, "AudioSpan")
        _require_number(self.padding_before_ms, "AudioSpan.padding_before_ms")
        _require_number(self.padding_after_ms, "AudioSpan.padding_after_ms")


@dataclass(frozen=True, slots=True)
class MediaArtifact:
    id: str
    title: str
    language: str
    media: tuple[MediaSource, ...]
    utterances: tuple[Utterance, ...]
    schema_version: int = CURRENT_SCHEMA_VERSION
    artifact_type: str = MEDIA_ARTIFACT_TYPE

    def __post_init__(self) -> None:
        if self.artifact_type != MEDIA_ARTIFACT_TYPE:
            raise ValueError(
                f"unsupported artifact type {self.artifact_type!r}; "
                f"expected {MEDIA_ARTIFACT_TYPE!r}"
            )
        if self.schema_version != CURRENT_SCHEMA_VERSION or type(self.schema_version) is not int:
            raise ValueError(
                f"unsupported schema version {self.schema_version!r}; "
                f"expected {CURRENT_SCHEMA_VERSION}"
            )
        _require_string(self.id, "MediaArtifact.id")
        _require_string(self.title, "MediaArtifact.title")
        _require_string(self.language, "MediaArtifact.language")

        media = _as_tuple(self.media, "MediaArtifact.media")
        utterances = _as_tuple(self.utterances, "MediaArtifact.utterances")
        if not media:
            raise ValueError("MediaArtifact.media must contain at least one source")
        object.__setattr__(self, "media", media)
        object.__setattr__(self, "utterances", utterances)

        for index, source in enumerate(media):
            if not isinstance(source, MediaSource):
                raise ValueError(f"MediaArtifact.media[{index}] must be a MediaSource")
        for index, utterance in enumerate(utterances):
            if not isinstance(utterance, Utterance):
                raise ValueError(f"MediaArtifact.utterances[{index}] must be an Utterance")

        media_by_id: dict[str, MediaSource] = {}
        for source in media:
            if source.id in media_by_id:
                raise ValueError(f"duplicate media id: {source.id!r}")
            media_by_id[source.id] = source

        utterance_ids: set[str] = set()
        for utterance in utterances:
            if utterance.id in utterance_ids:
                raise ValueError(f"duplicate utterance id: {utterance.id!r}")
            utterance_ids.add(utterance.id)

        for utterance in utterances:
            source = media_by_id.get(utterance.media_id)
            if source is None:
                raise ValueError(
                    f"utterance {utterance.id!r} references unknown media "
                    f"{utterance.media_id!r}"
                )
            if source.duration_ms is not None and utterance.end_ms > source.duration_ms:
                raise ValueError(f"utterance {utterance.id!r} exceeds its media duration")

    def to_dict(self) -> dict[str, Any]:
        return {
            "artifact_type": self.artifact_type,
            "schema_version": self.schema_version,
            "id": self.id,
            "title": self.title,
            "language": self.language,
            "media": [
                {
                    "id": source.id,
                    "path": source.path,
                    "language": source.language,
                    "title": source.title,
                    "source_url": source.source_url,
                    "checksum_sha256": source.checksum_sha256,
                    "duration_ms": source.duration_ms,
                }
                for source in self.media
            ],
            "utterances": [
                {
                    "id": utterance.id,
                    "media_id": utterance.media_id,
                    "text": utterance.text,
                    "start_ms": utterance.start_ms,
                    "end_ms": utterance.end_ms,
                    "tokens": [
                        {
                            "text": token.text,
                            "span": _span_to_dict(token.span),
                            "start_ms": token.start_ms,
                            "end_ms": token.end_ms,
                            "confidence": token.confidence,
                        }
                        for token in utterance.tokens
                    ],
                    "translation": utterance.translation,
                    "confidence": utterance.confidence,
                    "provenance": list(utterance.provenance),
                }
                for utterance in self.utterances
            ],
        }

    def to_json(self, *, indent: int | None = None) -> str:
        separators = (",", ":") if indent is None else None
        return json.dumps(
            self.to_dict(),
            allow_nan=False,
            ensure_ascii=False,
            indent=indent,
            separators=separators,
            sort_keys=True,
        )

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Self:
        root = _require_mapping(data, "media artifact")
        root_keys = {
            "artifact_type",
            "schema_version",
            "id",
            "title",
            "language",
            "media",
            "utterances",
        }
        _require_keys(
            root,
            root_keys,
            root_keys,
            "media artifact",
        )
        artifact_type = root["artifact_type"]
        if artifact_type != MEDIA_ARTIFACT_TYPE:
            raise ValueError(
                f"unsupported artifact type {artifact_type!r}; expected {MEDIA_ARTIFACT_TYPE!r}"
            )
        version = root["schema_version"]
        if version != CURRENT_SCHEMA_VERSION or type(version) is not int:
            raise ValueError(
                f"unsupported schema version {version!r}; expected {CURRENT_SCHEMA_VERSION}"
            )
        media_values = _require_sequence(root["media"], "media artifact.media")
        utterance_values = _require_sequence(root["utterances"], "media artifact.utterances")
        return cls(
            id=root["id"],
            title=root["title"],
            language=root["language"],
            media=tuple(_media_from_dict(item) for item in media_values),
            utterances=tuple(_utterance_from_dict(item) for item in utterance_values),
            schema_version=version,
            artifact_type=artifact_type,
        )

    @classmethod
    def from_json(cls, payload: str) -> Self:
        if not isinstance(payload, str):
            raise ValueError("media artifact JSON payload must be a string")
        return cls.from_dict(json.loads(payload))


def _span_to_dict(span: TextSpan) -> dict[str, int]:
    return {"start_char": span.start_char, "end_char": span.end_char}


def _require_mapping(value: object, context: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{context} must be an object")
    return value


def _require_sequence(value: object, context: str) -> Sequence[Any]:
    if isinstance(value, (str, bytes, bytearray)) or not isinstance(value, Sequence):
        raise ValueError(f"{context} must be an array")
    return value


def _require_keys(
    data: Mapping[str, Any], allowed: set[str], required: set[str], context: str
) -> None:
    keys = set(data)
    unsupported = keys - allowed
    if unsupported:
        names = ", ".join(sorted(str(name) for name in unsupported))
        raise ValueError(f"{context} contains unsupported field(s): {names}")
    missing = required - keys
    if missing:
        raise ValueError(f"missing {context}.{min(missing)}")


def _parse_span(value: object, context: str) -> TextSpan:
    data = _require_mapping(value, context)
    _require_keys(
        data,
        {"start_char", "end_char"},
        {"start_char", "end_char"},
        context,
    )
    return TextSpan(start_char=data["start_char"], end_char=data["end_char"])


def _media_from_dict(value: object) -> MediaSource:
    data = _require_mapping(value, "media item")
    _require_keys(
        data,
        {
            "id",
            "path",
            "language",
            "title",
            "source_url",
            "checksum_sha256",
            "duration_ms",
        },
        {"id", "path", "language"},
        "media item",
    )
    return MediaSource(
        id=data["id"],
        path=data["path"],
        language=data["language"],
        title=data.get("title"),
        source_url=data.get("source_url"),
        checksum_sha256=data.get("checksum_sha256"),
        duration_ms=data.get("duration_ms"),
    )


def _token_from_dict(value: object) -> BaseToken:
    data = _require_mapping(value, "token")
    _require_keys(
        data,
        {"text", "span", "start_ms", "end_ms", "confidence"},
        {"text", "span"},
        "token",
    )
    return BaseToken(
        text=data["text"],
        span=_parse_span(data["span"], "token.span"),
        start_ms=data.get("start_ms"),
        end_ms=data.get("end_ms"),
        confidence=data.get("confidence"),
    )


def _utterance_from_dict(value: object) -> Utterance:
    data = _require_mapping(value, "utterance")
    _require_keys(
        data,
        {
            "id",
            "media_id",
            "text",
            "start_ms",
            "end_ms",
            "tokens",
            "translation",
            "confidence",
            "provenance",
        },
        {"id", "media_id", "text", "start_ms", "end_ms", "tokens", "provenance"},
        "utterance",
    )
    token_values = _require_sequence(data["tokens"], "utterance.tokens")
    provenance = _require_sequence(data["provenance"], "utterance.provenance")
    return Utterance(
        id=data["id"],
        media_id=data["media_id"],
        text=data["text"],
        start_ms=data["start_ms"],
        end_ms=data["end_ms"],
        tokens=tuple(_token_from_dict(item) for item in token_values),
        translation=data.get("translation"),
        confidence=data.get("confidence"),
        provenance=tuple(provenance),
    )
