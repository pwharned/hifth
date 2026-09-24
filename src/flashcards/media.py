from __future__ import annotations

import hashlib
import math
import shutil
import subprocess
import tempfile
from collections.abc import Sequence
from pathlib import Path

from .models import AudioSpan


class MediaProcessingError(RuntimeError):
    pass


def _path(value: str | Path, name: str) -> Path:
    try:
        return Path(value)
    except TypeError as exc:
        raise ValueError(f"{name} must be a filesystem path") from exc


def _nonnegative_number(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a nonnegative number")
    if not math.isfinite(value) or value < 0:
        raise ValueError(f"{name} must be a nonnegative number")
    return float(value)


def _positive_int(value: object, name: str) -> int:
    if type(value) is not int or value < 1:
        raise ValueError(f"{name} must be a positive integer")
    return value


def _run(command: list[str], executable_name: str) -> None:
    try:
        result = subprocess.run(command, capture_output=True, text=True)
    except FileNotFoundError as exc:
        raise MediaProcessingError(f"{executable_name} is not installed or not on PATH") from exc
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "unknown error"
        raise MediaProcessingError(f"{executable_name} failed: {detail}")


def sha256_file(path: str | Path, *, chunk_size: int = 1024 * 1024) -> str:
    source = _path(path, "path")
    if not source.is_file():
        raise FileNotFoundError(f"Media file not found: {source}")
    _positive_int(chunk_size, "chunk_size")
    digest = hashlib.sha256()
    with source.open("rb") as media_file:
        while chunk := media_file.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def probe_duration_ms(path: str | Path, *, ffprobe: str = "ffprobe") -> float:
    source = _path(path, "path")
    if not source.is_file():
        raise FileNotFoundError(f"Media file not found: {source}")
    command = [
        ffprobe,
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(source),
    ]
    try:
        result = subprocess.run(command, capture_output=True, text=True)
    except FileNotFoundError as exc:
        raise MediaProcessingError(f"{ffprobe} is not installed or not on PATH") from exc
    if result.returncode != 0:
        detail = result.stderr.strip() or "unknown error"
        raise MediaProcessingError(f"{ffprobe} failed: {detail}")
    try:
        duration_ms = float(result.stdout.strip()) * 1000
    except ValueError as exc:
        raise MediaProcessingError(f"{ffprobe} returned an invalid duration") from exc
    if not math.isfinite(duration_ms) or duration_ms <= 0:
        raise MediaProcessingError(f"{ffprobe} returned an invalid duration")
    return duration_ms


def normalize_audio(
    input_path: str | Path,
    output_path: str | Path,
    *,
    sample_rate: int = 16_000,
    channels: int = 1,
    sample_width_bytes: int = 2,
    ffmpeg: str = "ffmpeg",
) -> Path:
    source = _path(input_path, "input_path")
    destination = _path(output_path, "output_path")
    if not source.is_file():
        raise FileNotFoundError(f"Media file not found: {source}")
    if destination.suffix.lower() != ".wav":
        raise ValueError("normalized audio output must use the .wav extension")
    _positive_int(sample_rate, "sample_rate")
    _positive_int(channels, "channels")
    codecs = {1: "pcm_u8", 2: "pcm_s16le", 3: "pcm_s24le", 4: "pcm_s32le"}
    if sample_width_bytes not in codecs:
        raise ValueError("sample_width_bytes must be one of 1, 2, 3, or 4")

    destination.parent.mkdir(parents=True, exist_ok=True)
    command = [
        ffmpeg,
        "-y",
        "-loglevel",
        "error",
        "-i",
        str(source),
        "-map",
        "0:a:0",
        "-vn",
        "-ac",
        str(channels),
        "-ar",
        str(sample_rate),
        "-c:a",
        codecs[sample_width_bytes],
        str(destination),
    ]
    _run(command, ffmpeg)
    return destination


def trim_audio(
    input_path: str | Path,
    output_path: str | Path,
    start_ms: float,
    end_ms: float,
    *,
    padding_before_ms: float = 0,
    padding_after_ms: float = 0,
    sample_rate: int = 44_100,
    channels: int = 1,
    bitrate: str = "64k",
    ffmpeg: str = "ffmpeg",
) -> Path:
    source = _path(input_path, "input_path")
    destination = _path(output_path, "output_path")
    if not source.is_file():
        raise FileNotFoundError(f"Media file not found: {source}")
    start = _nonnegative_number(start_ms, "start_ms")
    end = _nonnegative_number(end_ms, "end_ms")
    before = _nonnegative_number(padding_before_ms, "padding_before_ms")
    after = _nonnegative_number(padding_after_ms, "padding_after_ms")
    if end <= start:
        raise ValueError("end_ms must be greater than start_ms")
    _positive_int(sample_rate, "sample_rate")
    _positive_int(channels, "channels")
    if not isinstance(bitrate, str) or not bitrate.strip():
        raise ValueError("bitrate must be a nonempty string")

    suffix = destination.suffix.lower()
    if suffix not in {".mp3", ".wav"}:
        raise ValueError("audio clips must use the .mp3 or .wav extension")

    clip_start_ms = max(0.0, start - before)
    clip_end_ms = end + after
    duration_ms = clip_end_ms - clip_start_ms
    destination.parent.mkdir(parents=True, exist_ok=True)
    command = [
        ffmpeg,
        "-y",
        "-loglevel",
        "error",
        "-ss",
        f"{clip_start_ms / 1000:.6f}",
        "-i",
        str(source),
        "-t",
        f"{duration_ms / 1000:.6f}",
        "-map",
        "0:a:0",
        "-vn",
        "-ac",
        str(channels),
        "-ar",
        str(sample_rate),
    ]
    if suffix == ".mp3":
        command.extend(["-b:a", bitrate])
    else:
        command.extend(["-c:a", "pcm_s16le"])
    command.append(str(destination))
    _run(command, ffmpeg)
    return destination


def materialize_audio_span(
    input_path: str | Path,
    output_path: str | Path,
    span: AudioSpan,
    **options: object,
) -> Path:
    if not isinstance(span, AudioSpan):
        raise ValueError("span must be an AudioSpan")
    return trim_audio(
        input_path,
        output_path,
        span.start_ms,
        span.end_ms,
        padding_before_ms=span.padding_before_ms,
        padding_after_ms=span.padding_after_ms,
        **options,
    )


def _ffconcat_path(path: Path) -> str:
    value = str(path.resolve())
    if "\n" in value or "\r" in value:
        raise ValueError("audio paths may not contain newlines")
    return value.replace("'", "'\\''")


def concat_audio(
    input_paths: Sequence[str | Path],
    output_path: str | Path,
    *,
    ffmpeg: str = "ffmpeg",
) -> Path:
    if isinstance(input_paths, (str, bytes)) or not isinstance(input_paths, Sequence):
        raise ValueError("input_paths must be a sequence of paths")
    sources = tuple(_path(path, "input path") for path in input_paths)
    if not sources:
        raise ValueError("input_paths must not be empty")
    for source in sources:
        if not source.is_file():
            raise FileNotFoundError(f"Audio file not found: {source}")

    destination = _path(output_path, "output_path")
    destination.parent.mkdir(parents=True, exist_ok=True)
    if len(sources) == 1:
        if sources[0].suffix.lower() != destination.suffix.lower():
            raise ValueError("single-file concatenation requires matching input and output formats")
        shutil.copy2(sources[0], destination)
        return destination

    list_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w", suffix=".ffconcat", encoding="utf-8", delete=False
        ) as list_file:
            list_path = Path(list_file.name)
            list_file.write("ffconcat version 1.0\n")
            for source in sources:
                list_file.write(f"file '{_ffconcat_path(source)}'\n")
        command = [
            ffmpeg,
            "-y",
            "-loglevel",
            "error",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(list_path),
            "-c",
            "copy",
            str(destination),
        ]
        _run(command, ffmpeg)
    finally:
        if list_path is not None:
            list_path.unlink(missing_ok=True)
    return destination
