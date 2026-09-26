from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import stat
import subprocess
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from .media import MediaProcessingError, normalize_audio, probe_duration_ms, sha256_file
from .models import MediaArtifact, MediaSource, Utterance
from .subtitles import artifact_from_subtitles
from .transcription import (
    CachedTranscriber,
    Transcriber,
    TranscriptionCache,
    utterances_from_transcription,
)


TEXT_SUBTITLE_CODECS = frozenset(
    {
        "ass",
        "arib_caption",
        "dvb_teletext",
        "eia_608",
        "jacosub",
        "microdvd",
        "mpl2",
        "mov_text",
        "pjs",
        "realtext",
        "sami",
        "srt",
        "ssa",
        "stl",
        "subrip",
        "subviewer",
        "subviewer1",
        "text",
        "ttml",
        "vplayer",
        "webvtt",
    }
)


class _TranscriberRequiredError(ValueError):
    pass


def _source_signature(path: Path) -> tuple[int, int]:
    file_stat = path.stat()
    return file_stat.st_size, file_stat.st_mtime_ns


def _require_unchanged(path: Path, signature: tuple[int, int], operation: str) -> None:
    if _source_signature(path) != signature:
        raise MediaProcessingError(f"media changed while {operation}")


@dataclass(frozen=True, slots=True)
class SubtitleStream:
    index: int
    codec_name: str
    language: str | None = None
    title: str | None = None
    default: bool = False
    forced: bool = False

    def __post_init__(self) -> None:
        if type(self.index) is not int or self.index < 0:
            raise ValueError("subtitle stream index must be a nonnegative integer")
        if not isinstance(self.codec_name, str) or not self.codec_name.strip():
            raise ValueError("subtitle stream codec_name must be a nonempty string")
        object.__setattr__(self, "codec_name", self.codec_name.strip().casefold())
        for field_name in ("language", "title"):
            value = getattr(self, field_name)
            if value is not None:
                if not isinstance(value, str) or not value.strip():
                    raise ValueError(
                        f"subtitle stream {field_name} must be a nonempty string or None"
                    )
                object.__setattr__(self, field_name, value.strip())
        if type(self.default) is not bool:
            raise ValueError("subtitle stream default must be a boolean")
        if type(self.forced) is not bool:
            raise ValueError("subtitle stream forced must be a boolean")

    @property
    def is_text(self) -> bool:
        return self.codec_name in TEXT_SUBTITLE_CODECS

    @property
    def usable(self) -> bool:
        return self.is_text

    @property
    def is_usable(self) -> bool:
        return self.is_text


@dataclass(frozen=True, slots=True)
class AudioStream:
    index: int
    start_time_ms: float = 0
    language: str | None = None
    title: str | None = None
    default: bool = False

    def __post_init__(self) -> None:
        if type(self.index) is not int or self.index < 0:
            raise ValueError("audio stream index must be a nonnegative integer")
        if isinstance(self.start_time_ms, bool) or not isinstance(
            self.start_time_ms, (int, float)
        ):
            raise ValueError("audio stream start_time_ms must be finite")
        if not math.isfinite(self.start_time_ms):
            raise ValueError("audio stream start_time_ms must be finite")
        for field_name in ("language", "title"):
            value = getattr(self, field_name)
            if value is not None and (not isinstance(value, str) or not value.strip()):
                raise ValueError(f"audio stream {field_name} must be nonempty or None")
            if isinstance(value, str):
                object.__setattr__(self, field_name, value.strip())
        if type(self.default) is not bool:
            raise ValueError("audio stream default must be a boolean")


def probe_audio_streams(
    media_path: str | Path,
    ffprobe: str = "ffprobe",
) -> tuple[AudioStream, ...]:
    media_file = Path(media_path)
    if not media_file.is_file():
        raise FileNotFoundError(f"Media file not found: {media_file}")
    command = [
        ffprobe,
        "-v",
        "error",
        "-select_streams",
        "a",
        "-show_entries",
        "stream=index,start_time:stream_tags=language,title:stream_disposition=default:format=start_time",
        "-of",
        "json",
        str(media_file),
    ]
    try:
        result = subprocess.run(command, capture_output=True, text=True, shell=False)
    except FileNotFoundError as exc:
        raise MediaProcessingError(f"{ffprobe} is not installed or not on PATH") from exc
    except OSError as exc:
        raise MediaProcessingError(f"could not run {ffprobe}: {exc}") from exc
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "unknown error"
        raise MediaProcessingError(f"{ffprobe} failed while probing audio streams: {detail}")
    try:
        payload = json.loads(result.stdout)
    except (TypeError, json.JSONDecodeError) as exc:
        raise MediaProcessingError(f"{ffprobe} returned invalid JSON for audio streams") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("streams"), list):
        raise MediaProcessingError(f"{ffprobe} returned invalid audio stream data")
    raw_format = payload.get("format") or {}
    if not isinstance(raw_format, dict):
        raise MediaProcessingError(f"{ffprobe} returned invalid audio format data")
    format_start = raw_format.get("start_time", 0)
    if format_start in (None, "N/A"):
        format_start = 0
    try:
        format_start_ms = float(format_start) * 1000
    except (TypeError, ValueError) as exc:
        raise MediaProcessingError(f"{ffprobe} returned invalid media start time") from exc
    streams = []
    indexes: set[int] = set()
    for position, raw_stream in enumerate(payload["streams"]):
        if not isinstance(raw_stream, dict) or type(raw_stream.get("index")) is not int:
            raise MediaProcessingError(f"ffprobe returned invalid audio stream {position}")
        if raw_stream["index"] in indexes:
            raise MediaProcessingError(
                f"ffprobe returned duplicate audio stream index {raw_stream['index']}"
            )
        indexes.add(raw_stream["index"])
        start_time = raw_stream.get("start_time", 0)
        if start_time in (None, "N/A"):
            start_time = 0
        try:
            start_time_ms = float(start_time) * 1000 - format_start_ms
        except (TypeError, ValueError) as exc:
            raise MediaProcessingError(
                f"ffprobe returned invalid start time for audio stream {raw_stream['index']}"
            ) from exc
        tags = raw_stream.get("tags") or {}
        disposition = raw_stream.get("disposition") or {}
        if not isinstance(tags, dict) or not isinstance(disposition, dict):
            raise MediaProcessingError(
                f"ffprobe returned invalid metadata for audio stream {raw_stream['index']}"
            )
        streams.append(
            AudioStream(
                index=raw_stream["index"],
                start_time_ms=start_time_ms,
                language=_optional_ffprobe_tag(tags.get("language"), "audio language"),
                title=_optional_ffprobe_tag(tags.get("title"), "audio title"),
                default=_ffprobe_disposition(disposition.get("default"), "audio default"),
            )
        )
    return tuple(streams)


def _optional_ffprobe_tag(value: object, context: str) -> str | None:
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        raise MediaProcessingError(f"ffprobe returned an invalid {context}")
    value = value.strip()
    return value or None


def _ffprobe_disposition(value: object, context: str) -> bool:
    if value is None:
        return False
    if type(value) is bool:
        return value
    if type(value) is int and value in (0, 1):
        return bool(value)
    raise MediaProcessingError(f"ffprobe returned an invalid {context} disposition")


def probe_subtitle_streams(
    media_path: str | Path,
    ffprobe: str = "ffprobe",
) -> tuple[SubtitleStream, ...]:
    media_file = Path(media_path)
    if not media_file.is_file():
        raise FileNotFoundError(f"Media file not found: {media_file}")
    if not isinstance(ffprobe, str) or not ffprobe.strip():
        raise ValueError("ffprobe must be a nonempty string")

    command = [
        ffprobe,
        "-v",
        "error",
        "-select_streams",
        "s",
        "-show_entries",
        "stream=index,codec_name:stream_tags=language,title:stream_disposition=default,forced",
        "-of",
        "json",
        str(media_file),
    ]
    try:
        result = subprocess.run(command, capture_output=True, text=True, shell=False)
    except FileNotFoundError as exc:
        raise MediaProcessingError(f"{ffprobe} is not installed or not on PATH") from exc
    except OSError as exc:
        raise MediaProcessingError(f"could not run {ffprobe}: {exc}") from exc
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "unknown error"
        raise MediaProcessingError(f"{ffprobe} failed while probing subtitle streams: {detail}")

    try:
        payload = json.loads(result.stdout)
    except (TypeError, json.JSONDecodeError) as exc:
        raise MediaProcessingError(f"{ffprobe} returned invalid JSON for subtitle streams") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("streams"), list):
        raise MediaProcessingError(f"{ffprobe} returned invalid subtitle stream data")

    streams: list[SubtitleStream] = []
    indexes: set[int] = set()
    for position, raw_stream in enumerate(payload["streams"]):
        context = f"subtitle stream {position}"
        if not isinstance(raw_stream, dict):
            raise MediaProcessingError(f"ffprobe returned an invalid {context}")
        index = raw_stream.get("index")
        codec_name = raw_stream.get("codec_name")
        if type(index) is not int or index < 0:
            raise MediaProcessingError(f"ffprobe returned an invalid index for {context}")
        if index in indexes:
            raise MediaProcessingError(f"ffprobe returned duplicate stream index {index}")
        indexes.add(index)
        if not isinstance(codec_name, str) or not codec_name.strip():
            raise MediaProcessingError(f"ffprobe returned an invalid codec for stream {index}")
        tags = raw_stream.get("tags", {})
        disposition = raw_stream.get("disposition", {})
        if tags is None:
            tags = {}
        if disposition is None:
            disposition = {}
        if not isinstance(tags, dict) or not isinstance(disposition, dict):
            raise MediaProcessingError(f"ffprobe returned invalid metadata for stream {index}")
        streams.append(
            SubtitleStream(
                index=index,
                codec_name=codec_name,
                language=_optional_ffprobe_tag(tags.get("language"), "stream language"),
                title=_optional_ffprobe_tag(tags.get("title"), "stream title"),
                default=_ffprobe_disposition(disposition.get("default"), "default"),
                forced=_ffprobe_disposition(disposition.get("forced"), "forced"),
            )
        )
    return tuple(streams)


_LANGUAGE_GROUPS = (
    ("af", "afr", "afrikaans"),
    ("am", "amh", "amharic"),
    ("as", "asm", "assamese"),
    ("sq", "sqi", "alb", "albanian"),
    ("ar", "ara", "arabic"),
    ("az", "aze", "azerbaijani"),
    ("ba", "bak", "bashkir"),
    ("be", "bel", "belarusian"),
    ("hy", "hye", "arm", "armenian"),
    ("eu", "eus", "baq", "basque"),
    ("bn", "ben", "bengali"),
    ("bo", "bod", "tib", "tibetan"),
    ("br", "bre", "breton"),
    ("bs", "bos", "bosnian"),
    ("bg", "bul", "bulgarian"),
    ("ca", "cat", "catalan"),
    ("zh", "zho", "chi", "chinese"),
    ("hr", "hrv", "croatian"),
    ("cs", "ces", "cze", "czech"),
    ("da", "dan", "danish"),
    ("nl", "nld", "dut", "dutch"),
    ("en", "eng", "english"),
    ("et", "est", "estonian"),
    ("fi", "fin", "finnish"),
    ("fo", "fao", "faroese"),
    ("fr", "fra", "fre", "french"),
    ("de", "deu", "ger", "german"),
    ("el", "ell", "gre", "greek"),
    ("he", "heb", "hebrew"),
    ("hi", "hin", "hindi"),
    ("hu", "hun", "hungarian"),
    ("is", "isl", "ice", "icelandic"),
    ("id", "ind", "indonesian"),
    ("km", "khm", "khmer"),
    ("ga", "gle", "irish"),
    ("gl", "glg", "galician"),
    ("gu", "guj", "gujarati"),
    ("ha", "hau", "hausa"),
    ("haw", "hawaiian"),
    ("ht", "hat", "haitian", "haitian creole"),
    ("it", "ita", "italian"),
    ("ja", "jpn", "japanese"),
    ("jw", "jv", "jav", "javanese"),
    ("ka", "kat", "geo", "georgian"),
    ("kk", "kaz", "kazakh"),
    ("kn", "kan", "kannada"),
    ("ko", "kor", "korean"),
    ("lt", "lit", "lithuanian"),
    ("la", "lat", "latin"),
    ("lb", "ltz", "luxembourgish"),
    ("ln", "lin", "lingala"),
    ("lv", "lav", "latvian"),
    ("ms", "msa", "may", "malay"),
    ("mg", "mlg", "malagasy"),
    ("mi", "mri", "mao", "maori"),
    ("mk", "mkd", "mac", "macedonian"),
    ("my", "mya", "bur", "burmese", "myanmar"),
    ("lo", "lao", "lao"),
    ("ml", "mal", "malayalam"),
    ("mr", "mar", "marathi"),
    ("mt", "mlt", "maltese"),
    ("mn", "mon", "mongolian"),
    ("ne", "nep", "nepali"),
    ("no", "nor", "norwegian"),
    ("nn", "nno", "nynorsk"),
    ("oc", "oci", "occitan"),
    ("pa", "pan", "punjabi"),
    ("fa", "fas", "per", "persian"),
    ("ps", "pus", "pashto"),
    ("pl", "pol", "polish"),
    ("pt", "por", "portuguese"),
    ("ro", "ron", "rum", "romanian"),
    ("ru", "rus", "russian"),
    ("sr", "srp", "serbian"),
    ("sa", "san", "sanskrit"),
    ("sd", "snd", "sindhi"),
    ("si", "sin", "sinhala"),
    ("sn", "sna", "shona"),
    ("so", "som", "somali"),
    ("sk", "slk", "slo", "slovak"),
    ("sl", "slv", "slovenian"),
    ("es", "spa", "spanish"),
    ("sv", "swe", "swedish"),
    ("sw", "swa", "swahili"),
    ("su", "sun", "sundanese"),
    ("ta", "tam", "tamil"),
    ("te", "tel", "telugu"),
    ("tl", "tgl", "filipino", "tagalog"),
    ("th", "tha", "thai"),
    ("tg", "tgk", "tajik"),
    ("tk", "tuk", "turkmen"),
    ("tt", "tat", "tatar"),
    ("tr", "tur", "turkish"),
    ("uk", "ukr", "ukrainian"),
    ("ur", "urd", "urdu"),
    ("uz", "uzb", "uzbek"),
    ("vi", "vie", "vietnamese"),
    ("cy", "cym", "wel", "welsh"),
    ("yi", "yid", "yiddish"),
    ("yo", "yor", "yoruba"),
    ("yue", "cantonese"),
)
_LANGUAGE_KEYS = {
    alias: group[0]
    for group in _LANGUAGE_GROUPS
    for alias in group
}
_UNLABELLED_LANGUAGE_KEYS = frozenset({"und", "unknown", "undetermined"})


def _language_key(language: str) -> str:
    normalized = language.strip().casefold().replace("_", "-")
    base = normalized.split("-", 1)[0]
    return _LANGUAGE_KEYS.get(normalized, _LANGUAGE_KEYS.get(base, base))


def _ambiguous_subtitle_error(streams: Sequence[SubtitleStream], language: str) -> ValueError:
    indexes = ", ".join(str(stream.index) for stream in streams)
    return ValueError(
        f"multiple text subtitle streams match language {language!r} "
        f"(stream indexes: {indexes}); choose one with --subtitle-stream"
    )


def select_embedded_subtitle(
    streams: Sequence[SubtitleStream],
    language: str,
    requested_index: int | None = None,
) -> SubtitleStream | None:
    if isinstance(streams, (str, bytes)) or not isinstance(streams, Sequence):
        raise ValueError("streams must be a sequence of SubtitleStream values")
    candidates = tuple(streams)
    for position, stream in enumerate(candidates):
        if not isinstance(stream, SubtitleStream):
            raise ValueError(f"streams[{position}] must be a SubtitleStream")
    if not isinstance(language, str) or not language.strip():
        raise ValueError("language must be a nonempty string")
    if requested_index is not None and (type(requested_index) is not int or requested_index < 0):
        raise ValueError("requested subtitle stream index must be a nonnegative integer")

    if requested_index is not None:
        matches = tuple(stream for stream in candidates if stream.index == requested_index)
        if not matches:
            raise ValueError(f"subtitle stream index {requested_index} does not exist")
        if len(matches) > 1:
            raise ValueError(f"subtitle stream index {requested_index} is not unique")
        selected = matches[0]
        if not selected.is_text:
            raise ValueError(
                f"subtitle stream {requested_index} uses unsupported non-text codec "
                f"{selected.codec_name!r}; choose a text stream with --subtitle-stream"
            )
        return selected

    text_streams = tuple(stream for stream in candidates if stream.is_text)
    requested_language = _language_key(language)
    unlabelled = tuple(
        stream
        for stream in text_streams
        if (stream.language is None or _language_key(stream.language) in _UNLABELLED_LANGUAGE_KEYS)
        and (
            stream.title is None
            or _language_key(stream.title) in _UNLABELLED_LANGUAGE_KEYS
        )
        and not stream.forced
    )
    language_matches = tuple(
        stream
        for stream in text_streams
        if (
            stream.language is not None
            and _language_key(stream.language) not in _UNLABELLED_LANGUAGE_KEYS
            and _language_key(stream.language) == requested_language
        )
        or (stream.title is not None and _language_key(stream.title) == requested_language)
    )
    if language_matches:
        non_forced = tuple(stream for stream in language_matches if not stream.forced)
        if not non_forced:
            unlabelled_non_forced = tuple(stream for stream in unlabelled if not stream.forced)
            if len(unlabelled_non_forced) == 1:
                return unlabelled_non_forced[0]
            if len(unlabelled_non_forced) > 1:
                raise _ambiguous_subtitle_error(unlabelled_non_forced, language)
            return None
        preferred = non_forced
        defaults = tuple(stream for stream in preferred if stream.default)
        if len(defaults) == 1:
            return defaults[0]
        if len(preferred) == 1:
            return preferred[0]
        raise _ambiguous_subtitle_error(preferred, language)

    if len(unlabelled) == 1:
        return unlabelled[0]
    if len(unlabelled) > 1:
        raise _ambiguous_subtitle_error(unlabelled, language)
    return None


def extract_embedded_subtitle(
    media_path: str | Path,
    stream: SubtitleStream,
    output_path: str | Path,
    ffmpeg: str = "ffmpeg",
) -> Path:
    media_file = Path(media_path)
    output_file = Path(output_path)
    if not media_file.is_file():
        raise FileNotFoundError(f"Media file not found: {media_file}")
    if not isinstance(stream, SubtitleStream):
        raise ValueError("stream must be a SubtitleStream")
    if not stream.is_text:
        raise ValueError(
            f"subtitle stream {stream.index} uses unsupported non-text codec {stream.codec_name!r}"
        )
    if output_file.suffix.casefold() != ".srt":
        raise ValueError("embedded subtitle output must use the .srt extension")
    if media_file.resolve() == output_file.resolve():
        raise ValueError("embedded subtitle output must differ from the source media")
    if not isinstance(ffmpeg, str) or not ffmpeg.strip():
        raise ValueError("ffmpeg must be a nonempty string")

    output_file.parent.mkdir(parents=True, exist_ok=True)
    command = [
        ffmpeg,
        "-y",
        "-loglevel",
        "error",
        "-i",
        str(media_file),
        "-map",
        f"0:{stream.index}",
        "-c:s",
        "srt",
        str(output_file),
    ]
    try:
        result = subprocess.run(command, capture_output=True, text=True, shell=False)
    except FileNotFoundError as exc:
        raise MediaProcessingError(f"{ffmpeg} is not installed or not on PATH") from exc
    except OSError as exc:
        raise MediaProcessingError(f"could not run {ffmpeg}: {exc}") from exc
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "unknown error"
        raise MediaProcessingError(
            f"{ffmpeg} failed to extract subtitle stream {stream.index}: {detail}"
        )
    return output_file


def _artifact_from_utterances(
    media_file: Path,
    utterances: tuple[Utterance, ...],
    *,
    language: str,
    title: str | None,
    source_url: str | None,
    stored_media_path: str | None,
    checksum: str,
    duration_ms: float,
) -> MediaArtifact:
    media_id = f"media-{checksum[:24]}"
    artifact_title = title or media_file.stem
    artifact_identity = f"{media_id}\0{language}\0{artifact_title}"
    artifact_id = f"artifact-{hashlib.sha256(artifact_identity.encode('utf-8')).hexdigest()[:24]}"
    source = MediaSource(
        id=media_id,
        path=stored_media_path or str(media_file.resolve()),
        language=language,
        title=artifact_title,
        source_url=source_url,
        checksum_sha256=checksum,
        duration_ms=duration_ms,
    )
    return MediaArtifact(
        id=artifact_id,
        title=artifact_title,
        language=language,
        media=(source,),
        utterances=utterances,
    )


def artifact_from_media(
    media_path: str | Path,
    *,
    language: str,
    subtitle_path: str | Path | None = None,
    subtitle_stream: int | None = None,
    force_transcribe: bool = False,
    transcriber: Transcriber | None = None,
    transcription_cache: TranscriptionCache | None = None,
    refresh_transcription: bool = False,
    title: str | None = None,
    source_url: str | None = None,
    stored_media_path: str | None = None,
) -> MediaArtifact:
    media_file = Path(media_path)
    if not media_file.is_file():
        raise FileNotFoundError(f"Media file not found: {media_file}")
    if not isinstance(language, str) or not language.strip():
        raise ValueError("language must be a nonempty string")
    if type(force_transcribe) is not bool:
        raise ValueError("force_transcribe must be a boolean")
    if type(refresh_transcription) is not bool:
        raise ValueError("refresh_transcription must be a boolean")
    if subtitle_stream is not None and (type(subtitle_stream) is not int or subtitle_stream < 0):
        raise ValueError("subtitle_stream must be a nonnegative integer or None")
    if subtitle_path is not None and force_transcribe:
        raise ValueError("external subtitles cannot be combined with force_transcribe")
    if subtitle_path is not None and subtitle_stream is not None:
        raise ValueError("external subtitles cannot be combined with subtitle_stream")
    if force_transcribe and subtitle_stream is not None:
        raise ValueError("subtitle_stream cannot be combined with force_transcribe")

    source_signature = _source_signature(media_file)
    audio_streams = probe_audio_streams(media_file)
    _require_unchanged(media_file, source_signature, "streams were being probed")
    if not audio_streams:
        raise MediaProcessingError(f"media contains no usable audio stream: {media_file}")
    if len(audio_streams) > 1:
        indexes = ", ".join(str(stream.index) for stream in audio_streams)
        raise MediaProcessingError(
            "media contains multiple audio streams "
            f"({indexes}); select or remux one audio track before importing"
        )
    audio_stream = audio_streams[0]

    if subtitle_path is not None:
        artifact = artifact_from_subtitles(
            media_file,
            subtitle_path,
            language=language,
            title=title,
            source_url=source_url,
            stored_media_path=stored_media_path,
            validate_audio=False,
        )
        _require_unchanged(media_file, source_signature, "subtitles were being imported")
        return artifact

    selected_stream = None
    if not force_transcribe:
        selected_stream = select_embedded_subtitle(
            probe_subtitle_streams(media_file),
            language,
            subtitle_stream,
        )
    if selected_stream is not None:
        with tempfile.TemporaryDirectory(prefix="flashcards-subtitles-") as temp_dir_name:
            subtitle_file = extract_embedded_subtitle(
                media_file,
                selected_stream,
                Path(temp_dir_name) / "embedded.srt",
            )
            artifact = artifact_from_subtitles(
                media_file,
                subtitle_file,
                language=language,
                title=title,
                source_url=source_url,
                stored_media_path=stored_media_path,
                validate_audio=False,
            )
        provenance = (
            "subtitle:embedded",
            f"subtitle:stream={selected_stream.index}",
            f"subtitle:codec={selected_stream.codec_name}",
            f"subtitle:language={selected_stream.language or 'und'}",
        )
        artifact = replace(
            artifact,
            utterances=tuple(
                replace(utterance, provenance=provenance)
                for utterance in artifact.utterances
            ),
        )
        _require_unchanged(media_file, source_signature, "subtitles were being imported")
        return artifact

    if transcriber is None:
        raise _TranscriberRequiredError(
            "no usable subtitles were found and no transcriber was provided for ASR"
        )

    checksum = sha256_file(media_file)
    _require_unchanged(media_file, source_signature, "its checksum was being calculated")
    media_id = f"media-{checksum[:24]}"
    duration_ms = probe_duration_ms(media_file)
    asr_language = _language_key(language)
    source_offset_ms = max(0.0, audio_stream.start_time_ms)
    transcriber.identity()

    with tempfile.TemporaryDirectory(prefix="flashcards-asr-") as temp_dir_name:
        normalized_audio = Path(temp_dir_name) / "audio.wav"
        try:
            normalize_audio(
                media_file,
                normalized_audio,
                sample_rate=16_000,
                channels=1,
            )
        except MediaProcessingError as exc:
            detail = str(exc).casefold()
            no_audio_markers = (
                "matches no streams",
                "contains no stream",
                "does not contain any stream",
                "could not find codec parameters for stream",
                "no audio stream",
            )
            if any(marker in detail for marker in no_audio_markers):
                raise MediaProcessingError(
                    f"media contains no usable audio stream: {media_file}"
                ) from exc
            raise
        _require_unchanged(media_file, source_signature, "it was being prepared for transcription")
        cache_identity = f"{checksum}:audio-stream={audio_stream.index}"
        if transcription_cache is None:
            transcription = transcriber.transcribe(normalized_audio, asr_language)
        else:
            transcription = CachedTranscriber(transcriber, transcription_cache).transcribe(
                normalized_audio,
                cache_identity,
                asr_language,
                refresh=refresh_transcription,
            )

    _require_unchanged(media_file, source_signature, "it was being transcribed")
    if _language_key(transcription.language) != asr_language:
        raise ValueError(
            f"ASR returned language {transcription.language!r}, "
            f"but {asr_language!r} was requested"
        )

    utterances = tuple(
        utterances_from_transcription(
            transcription,
            media_id,
            duration_ms,
            time_offset_ms=source_offset_ms,
        )
    )
    if not utterances:
        raise ValueError("ASR transcription produced no utterances")
    if any(not utterance.provenance for utterance in utterances):
        utterances = tuple(
            utterance
            if utterance.provenance
            else replace(utterance, provenance=("transcript:asr",))
            for utterance in utterances
        )
    utterances = tuple(
        replace(
            utterance,
            provenance=tuple(
                dict.fromkeys(
                    (
                        *utterance.provenance,
                        f"audio-stream:{audio_stream.index}",
                    )
                )
            ),
        )
        for utterance in utterances
    )
    return _artifact_from_utterances(
        media_file,
        utterances,
        language=language,
        title=title,
        source_url=source_url,
        stored_media_path=stored_media_path,
        checksum=checksum,
        duration_ms=duration_ms,
    )


def _nonnegative_int(value: str) -> int:
    try:
        result = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be a nonnegative integer") from exc
    if result < 0:
        raise argparse.ArgumentTypeError("must be a nonnegative integer")
    return result


def _positive_int(value: str) -> int:
    result = _nonnegative_int(value)
    if result == 0:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return result


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Create a media transcript artifact from external subtitles, embedded subtitles, "
            "or local speech recognition."
        )
    )
    parser.add_argument("media", type=Path)
    parser.add_argument("--subtitles", type=Path, help="Explicit SRT or WebVTT subtitle file")
    parser.add_argument("--language", required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--title")
    parser.add_argument("--source-url")
    parser.add_argument("--subtitle-stream", type=_nonnegative_int)
    parser.add_argument("--force-transcribe", action="store_true")
    parser.add_argument("--asr-model", default="medium")
    parser.add_argument("--device", choices=("auto", "cpu", "cuda"))
    parser.add_argument("--compute-type")
    parser.add_argument("--cpu-threads", type=_positive_int)
    parser.add_argument("--asr-cache-dir", type=Path)
    parser.add_argument("--refresh-transcription", action="store_true")
    parser.add_argument("--force", action="store_true", help="Overwrite an existing artifact")
    return parser.parse_args(argv)


def _transcript_source(artifact: MediaArtifact) -> str:
    provenance = artifact.utterances[0].provenance if artifact.utterances else ()
    if "subtitle:embedded" in provenance:
        stream = next(
            (
                item.removeprefix("subtitle:stream=")
                for item in provenance
                if item.startswith("subtitle:stream=")
            ),
            "?",
        )
        return f"embedded subtitles (stream {stream})"
    if any(item.startswith("subtitle:") for item in provenance):
        return "external subtitles"
    return "ASR transcription"


def _cli_transcriber(args: argparse.Namespace) -> tuple[Transcriber, TranscriptionCache]:
    from .faster_whisper_backend import FasterWhisperConfig, FasterWhisperTranscriber

    config_options: dict[str, Any] = {"model": args.asr_model}
    if args.device is not None:
        config_options["device"] = args.device
    if args.compute_type is not None:
        config_options["compute_type"] = args.compute_type
    if args.cpu_threads is not None:
        config_options["cpu_threads"] = args.cpu_threads
    if args.asr_cache_dir is not None:
        config_options["cache_dir"] = args.asr_cache_dir
    transcriber = FasterWhisperTranscriber(FasterWhisperConfig(**config_options))
    cache_path = args.out.parent / f"{args.out.stem}.transcription-cache.json"
    return transcriber, TranscriptionCache(cache_path)


def main(argv: Sequence[str] | None = None) -> None:
    args = _parse_args(argv)
    _require_distinct_output(args.out, args.media, args.subtitles)
    if args.out.exists() and not args.force:
        raise SystemExit(f"Output already exists: {args.out}; use --force to overwrite it")
    if args.subtitles is not None and args.force_transcribe:
        raise SystemExit("--subtitles cannot be combined with --force-transcribe")

    args.out.parent.mkdir(parents=True, exist_ok=True)
    try:
        stored_path = os.path.relpath(args.media.resolve(), args.out.parent.resolve())
    except ValueError:
        stored_path = str(args.media.resolve())
    common_options = {
        "language": args.language,
        "subtitle_path": args.subtitles,
        "subtitle_stream": args.subtitle_stream,
        "refresh_transcription": args.refresh_transcription,
        "title": args.title,
        "source_url": args.source_url,
        "stored_media_path": stored_path,
    }
    try:
        artifact = artifact_from_media(
            args.media,
            force_transcribe=args.force_transcribe,
            **common_options,
        )
    except _TranscriberRequiredError:
        transcriber, transcription_cache = _cli_transcriber(args)
        artifact = artifact_from_media(
            args.media,
            force_transcribe=True,
            transcriber=transcriber,
            transcription_cache=transcription_cache,
            **common_options,
        )

    _write_artifact(args.out, artifact, force=args.force)
    print(
        f"Imported {len(artifact.utterances)} utterance(s) from "
        f"{_transcript_source(artifact)} -> {args.out}"
    )


def _write_artifact(path: Path, artifact: MediaArtifact, *, force: bool) -> None:
    permissions = 0o600
    if path.exists():
        if not force:
            raise FileExistsError(f"Output already exists: {path}")
        permissions = stat.S_IMODE(path.stat().st_mode)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as output_file:
            if hasattr(os, "fchmod"):
                os.fchmod(output_file.fileno(), permissions)
            output_file.write(artifact.to_json(indent=2) + "\n")
            output_file.flush()
            os.fsync(output_file.fileno())
        if not hasattr(os, "fchmod"):
            os.chmod(temporary_path, permissions)
        if not force and path.exists():
            raise FileExistsError(f"Output already exists: {path}")
        os.replace(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)


def _require_distinct_output(output: Path, *inputs: Path | None) -> None:
    resolved_output = output.resolve()
    for input_path in inputs:
        if input_path is not None and input_path.resolve() == resolved_output:
            raise SystemExit(f"Output path must differ from input path: {input_path}")


if __name__ == "__main__":
    main()
