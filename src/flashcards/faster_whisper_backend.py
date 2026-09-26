from __future__ import annotations

import argparse
import gc
import hashlib
import importlib
import importlib.metadata
import math
import os
import re
import shlex
import threading
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .transcription import (
    TranscribedSegment,
    TranscribedWord,
    TranscriptionError,
    TranscriptionResult,
    TranscriptionValidationError,
)


DEFAULT_ASR_MODEL = "medium"
FASTER_WHISPER_ENGINE = "faster-whisper"


def _default_cache_dir() -> Path:
    xdg_cache_home = os.environ.get("XDG_CACHE_HOME")
    root = Path(xdg_cache_home).expanduser() if xdg_cache_home else Path.home() / ".cache"
    return root / "hifth-flashcards" / "asr"


DEFAULT_ASR_CACHE_DIR = _default_cache_dir()


def _default_cpu_threads() -> int:
    return max(1, os.cpu_count() or 1)


@dataclass(frozen=True, slots=True)
class FasterWhisperConfig:
    model: str = DEFAULT_ASR_MODEL
    device: str = "auto"
    compute_type: str = "auto"
    cpu_threads: int = _default_cpu_threads()
    beam_size: int = 5
    vad_filter: bool = True
    cache_dir: Path = DEFAULT_ASR_CACHE_DIR

    def __post_init__(self) -> None:
        if not isinstance(self.model, str) or not self.model.strip():
            raise ValueError("model must be a nonempty string")
        if self.model != self.model.strip():
            raise ValueError("model must not have leading or trailing whitespace")
        if not isinstance(self.device, str) or self.device not in {"auto", "cpu", "cuda"}:
            raise ValueError("device must be one of: auto, cpu, cuda")
        if not isinstance(self.compute_type, str) or not self.compute_type.strip():
            raise ValueError("compute_type must be 'auto' or an explicit compute type")
        if self.compute_type != self.compute_type.strip():
            raise ValueError("compute_type must not have leading or trailing whitespace")
        if type(self.cpu_threads) is not int or self.cpu_threads <= 0:
            raise ValueError("cpu_threads must be a positive integer")
        if type(self.beam_size) is not int or self.beam_size <= 0:
            raise ValueError("beam_size must be a positive integer")
        if type(self.vad_filter) is not bool:
            raise ValueError("vad_filter must be a boolean")
        if isinstance(self.cache_dir, str) and not self.cache_dir.strip():
            raise ValueError("cache_dir must be a nonempty path")
        try:
            cache_dir = Path(self.cache_dir).expanduser()
        except TypeError as exc:
            raise ValueError("cache_dir must be path-like") from exc
        object.__setattr__(self, "cache_dir", cache_dir)


def _missing_dependency_error() -> TranscriptionError:
    return TranscriptionError(
        'faster-whisper is not installed; run `pip install -e ".[asr]"`'
    )


def _load_dependencies() -> Callable[..., Any]:
    """Load the optional ASR dependency; tests can replace this factory seam."""
    try:
        module = importlib.import_module("faster_whisper")
    except ImportError as exc:
        raise _missing_dependency_error() from exc
    model_factory = getattr(module, "WhisperModel", None)
    if not callable(model_factory):
        raise _missing_dependency_error()
    return model_factory


def _load_torch() -> Any | None:
    try:
        return importlib.import_module("torch")
    except (ImportError, OSError):
        return None


def _cuda_is_available() -> bool:
    try:
        ctranslate2 = importlib.import_module("ctranslate2")
    except (ImportError, OSError):
        return False
    try:
        return int(ctranslate2.get_cuda_device_count()) > 0
    except (AttributeError, RuntimeError, TypeError, ValueError):
        return False


def _installed_backend_version() -> str:
    try:
        version = importlib.metadata.version("faster-whisper")
    except (importlib.metadata.PackageNotFoundError, OSError, ValueError):
        return "unknown"
    return version if isinstance(version, str) and version.strip() else "unknown"


def _installed_ctranslate2_version() -> str:
    try:
        version = importlib.metadata.version("ctranslate2")
    except (importlib.metadata.PackageNotFoundError, OSError, ValueError):
        return "unknown"
    return version if isinstance(version, str) and version.strip() else "unknown"


def _resolve_local_model_path(config: FasterWhisperConfig) -> Path:
    configured_path = Path(config.model).expanduser()
    if configured_path.is_dir():
        model_path = configured_path.resolve()
    else:
        try:
            utilities = importlib.import_module("faster_whisper.utils")
            download_model = getattr(utilities, "download_model")
            model_path = Path(
                download_model(
                    config.model,
                    local_files_only=True,
                    cache_dir=str(config.cache_dir),
                )
            )
        except (ImportError, AttributeError) as exc:
            raise _missing_dependency_error() from exc
        except Exception as exc:
            raise _missing_model_error(config) from exc
    missing = [
        name
        for name in ("model.bin", "config.json", "tokenizer.json")
        if not (model_path / name).is_file()
    ]
    if not any(model_path.glob("vocabulary.*")):
        missing.append("vocabulary.*")
    if missing:
        raise TranscriptionError(
            f"local faster-whisper model {config.model!r} is incomplete "
            f"(missing {', '.join(missing)}); run `{_prepare_command(config)}`"
        )
    return model_path


def _model_fingerprint(model_path: Path) -> str:
    if model_path.parent.name == "snapshots" and re.fullmatch(
        r"[0-9a-fA-F]{40,64}", model_path.name
    ):
        return model_path.name.casefold()
    digest = hashlib.sha256()
    for name in (
        "config.json",
        "preprocessor_config.json",
        "tokenizer.json",
        "model.bin",
    ):
        path = model_path / name
        digest.update(name.encode("utf-8"))
        if not path.exists():
            digest.update(b"<missing>")
            continue
        with path.open("rb") as model_file:
            while chunk := model_file.read(1024 * 1024):
                digest.update(chunk)
    for path in sorted(model_path.glob("vocabulary.*")):
        digest.update(path.name.encode("utf-8"))
        with path.open("rb") as vocabulary_file:
            while chunk := vocabulary_file.read(1024 * 1024):
                digest.update(chunk)
    return digest.hexdigest()


def _resolve_runtime(
    config: FasterWhisperConfig,
    cuda_probe: Callable[[], bool] | None = None,
) -> tuple[str, str]:
    cuda_probe = cuda_probe or _cuda_is_available
    if config.device == "cpu":
        device = "cpu"
    else:
        try:
            cuda_available = bool(cuda_probe())
        except Exception as exc:
            if config.device == "cuda":
                raise TranscriptionError(f"Could not determine CUDA availability: {exc}") from exc
            cuda_available = False
        if config.device == "cuda" and not cuda_available:
            raise TranscriptionError(
                "CUDA was explicitly requested, but torch.cuda.is_available() is false"
            )
        device = "cuda" if cuda_available else "cpu"

    if config.compute_type == "auto":
        compute_type = "int8_float16" if device == "cuda" else "int8"
    else:
        compute_type = config.compute_type
    return device, compute_type


def _field(value: object, name: str, default: Any = None) -> Any:
    if isinstance(value, Mapping):
        return value.get(name, default)
    return getattr(value, name, default)


def _milliseconds(value: object, context: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TranscriptionValidationError(f"{context} must be a finite nonnegative number")
    if not math.isfinite(value) or value < 0:
        raise TranscriptionValidationError(f"{context} must be a finite nonnegative number")
    return float(value) * 1000.0


def _probability(value: object, context: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TranscriptionValidationError(f"{context} must be a number between 0 and 1")
    probability = float(value)
    if not math.isfinite(probability) or not 0 <= probability <= 1:
        raise TranscriptionValidationError(f"{context} must be between 0 and 1")
    return probability


def _convert_word(value: object, index: int) -> TranscribedWord | None:
    text = _field(value, "word")
    if text is None:
        text = _field(value, "text")
    if _field(value, "start") is None or _field(value, "end") is None:
        return None
    try:
        return TranscribedWord(
            text=text,
            start_ms=_milliseconds(_field(value, "start"), f"word[{index}].start"),
            end_ms=_milliseconds(_field(value, "end"), f"word[{index}].end"),
            confidence=_probability(
                _field(value, "probability"),
                f"word[{index}].probability",
            ),
        )
    except TranscriptionValidationError:
        return None


def _convert_segment(value: object, index: int) -> TranscribedSegment | None:
    text = _field(value, "text")
    if not isinstance(text, str):
        raise TranscriptionValidationError(f"segment[{index}].text must be a string")
    if not text.strip():
        return None
    raw_words = _field(value, "words", ())
    if raw_words is None:
        raw_words = ()
    if isinstance(raw_words, (str, bytes, bytearray)):
        raise TranscriptionValidationError(f"segment[{index}].words must be iterable")
    try:
        words = tuple(
            converted
            for word_index, word in enumerate(raw_words)
            if (converted := _convert_word(word, word_index)) is not None
        )
    except TypeError as exc:
        raise TranscriptionValidationError(f"segment[{index}].words must be iterable") from exc
    probability = _field(value, "probability")
    if probability is None:
        probability = _field(value, "confidence")
    return TranscribedSegment(
        text=text,
        start_ms=_milliseconds(_field(value, "start"), f"segment[{index}].start"),
        end_ms=_milliseconds(_field(value, "end"), f"segment[{index}].end"),
        words=words,
        confidence=_probability(probability, f"segment[{index}].probability"),
    )


def _detected_language(info: object, requested_language: str) -> str:
    language = _field(info, "language")
    if language is None:
        language = requested_language
    if not isinstance(language, str) or not language.strip():
        raise TranscriptionValidationError(
            "faster-whisper returned an invalid detected language"
        )
    return language


def _looks_like_missing_model(error: BaseException) -> bool:
    if isinstance(error, FileNotFoundError):
        return True
    detail = f"{error.__class__.__name__}: {error}".lower()
    markers = (
        "localentrynotfound",
        "local entry",
        "local_files_only",
        "not found",
        "no such file",
        "does not exist",
        "missing model",
        "model missing",
        "unable to open file",
        "model.bin",
        "couldn't find",
        "cannot find",
    )
    return any(marker in detail for marker in markers)


def _prepare_command(config: FasterWhisperConfig) -> str:
    return (
        f"flashcards-prepare-asr --model {shlex.quote(config.model)} "
        f"--cache-dir {shlex.quote(str(config.cache_dir))}"
    )


def _missing_model_error(config: FasterWhisperConfig) -> TranscriptionError:
    command = _prepare_command(config)
    return TranscriptionError(
        f"faster-whisper model {config.model!r} is not available locally; "
        f"run `{command}` to download it explicitly"
    )


def _close_model(model: Any | None) -> None:
    if model is not None:
        close = getattr(model, "close", None)
        if callable(close):
            try:
                close()
            except Exception:
                pass


def _cleanup_runtime(device: str) -> None:
    gc.collect()
    if device != "cuda":
        return
    torch = _load_torch()
    if torch is None:
        return
    try:
        torch.cuda.empty_cache()
    except (AttributeError, RuntimeError):
        pass


class FasterWhisperTranscriber:
    def __init__(
        self,
        config: FasterWhisperConfig | None = None,
        *,
        model_factory: Callable[..., Any] | None = None,
        model_resolver: Callable[[FasterWhisperConfig], Path] | None = None,
        backend_version: str | None = None,
        cuda_available: Callable[[], bool] | None = None,
    ) -> None:
        if config is not None and not isinstance(config, FasterWhisperConfig):
            raise TypeError("config must be a FasterWhisperConfig")
        if model_factory is not None and not callable(model_factory):
            raise TypeError("model_factory must be callable")
        if model_resolver is not None and not callable(model_resolver):
            raise TypeError("model_resolver must be callable")
        if backend_version is not None and (
            not isinstance(backend_version, str) or not backend_version.strip()
        ):
            raise ValueError("backend_version must be a nonempty string")
        if cuda_available is not None and not callable(cuda_available):
            raise TypeError("cuda_available must be callable")
        self.config = config or FasterWhisperConfig()
        self._model_factory = model_factory
        self._model_resolver = model_resolver
        self._provided_backend_version = backend_version
        self._cuda_probe = cuda_available or _cuda_is_available
        self._runtime: tuple[str, str] | None = None
        self._backend_version: str | None = None
        self._model_reference: str | None = None
        self._model_digest: str | None = None
        self._lock = threading.RLock()

    def _resolved_runtime(self) -> tuple[str, str]:
        with self._lock:
            if self._runtime is None:
                self._runtime = _resolve_runtime(self.config, self._cuda_probe)
            return self._runtime

    def _resolved_backend_version(self) -> str:
        with self._lock:
            if self._backend_version is None:
                self._backend_version = (
                    self._provided_backend_version or _installed_backend_version()
                )
            return self._backend_version

    def _resolved_model(self) -> tuple[str, str]:
        with self._lock:
            if self._model_reference is None or self._model_digest is None:
                if self._model_resolver is not None:
                    model_path = Path(self._model_resolver(self.config))
                    self._model_reference = str(model_path)
                    self._model_digest = _model_fingerprint(model_path)
                elif self._model_factory is not None:
                    self._model_reference = self.config.model
                    self._model_digest = f"injected:{self.config.model}"
                else:
                    model_path = _resolve_local_model_path(self.config)
                    self._model_reference = str(model_path)
                    self._model_digest = _model_fingerprint(model_path)
            return self._model_reference, self._model_digest

    def identity(self) -> Mapping[str, Any]:
        device, compute_type = self._resolved_runtime()
        _model_reference, model_digest = self._resolved_model()
        settings = (
            f"device={device}",
            f"compute_type={compute_type}",
            f"cpu_threads={self.config.cpu_threads}",
            f"beam_size={self.config.beam_size}",
            f"vad_filter={str(self.config.vad_filter).lower()}",
            f"model_digest={model_digest}",
            f"ctranslate2_version={_installed_ctranslate2_version()}",
        )
        return {
            "engine": FASTER_WHISPER_ENGINE,
            "engine_version": self._resolved_backend_version(),
            "model": self.config.model,
            "settings": settings,
        }

    def transcribe(self, audio_path: Path, language: str) -> TranscriptionResult:
        try:
            path = Path(audio_path)
        except TypeError as exc:
            raise TypeError("audio_path must be path-like") from exc
        if not isinstance(language, str) or not language.strip():
            raise ValueError("language must be a nonempty string")
        if not path.is_file():
            raise FileNotFoundError(f"Audio file not found: {path}")

        identity = self.identity()
        device, compute_type = self._resolved_runtime()
        model_reference, _model_digest = self._resolved_model()
        model_factory = self._model_factory or _load_dependencies()
        model: Any | None = None
        try:
            try:
                model = model_factory(
                    model_reference,
                    device=device,
                    compute_type=compute_type,
                    download_root=str(self.config.cache_dir),
                    cpu_threads=self.config.cpu_threads,
                    local_files_only=True,
                )
            except Exception as exc:
                if _looks_like_missing_model(exc):
                    raise _missing_model_error(self.config) from exc
                prepare_command = _prepare_command(self.config)
                raise TranscriptionError(
                    f"Could not load faster-whisper model {self.config.model!r}: {exc}. "
                    f"If its local files are absent, run `{prepare_command}`"
                ) from exc

            try:
                raw_segments, info = model.transcribe(
                    str(path),
                    language=language,
                    beam_size=self.config.beam_size,
                    word_timestamps=True,
                    vad_filter=self.config.vad_filter,
                    condition_on_previous_text=False,
                )
                materialized_segments = tuple(raw_segments)
                segments = tuple(
                    converted
                    for index, segment in enumerate(materialized_segments)
                    if (converted := _convert_segment(segment, index)) is not None
                )
                detected_language = _detected_language(info, language)
            except TranscriptionError:
                raise
            except Exception as exc:
                raise TranscriptionError(f"faster-whisper transcription failed: {exc}") from exc

            return TranscriptionResult(
                language=detected_language,
                segments=segments,
                engine=identity["engine"],
                model=identity["model"],
                engine_version=identity["engine_version"],
                settings=tuple(identity["settings"]),
            )
        finally:
            _close_model(model)
            model = None
            _cleanup_runtime(device)


def prepare_asr_model(config: FasterWhisperConfig) -> Path:
    if not isinstance(config, FasterWhisperConfig):
        raise TypeError("config must be a FasterWhisperConfig")
    device, compute_type = _resolve_runtime(config)
    model_factory = _load_dependencies()
    try:
        config.cache_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise TranscriptionError(f"Could not create ASR cache {config.cache_dir}: {exc}") from exc

    model: Any | None = None
    try:
        try:
            model = model_factory(
                config.model,
                device=device,
                compute_type=compute_type,
                download_root=str(config.cache_dir),
                cpu_threads=config.cpu_threads,
                local_files_only=False,
            )
        except Exception as exc:
            raise TranscriptionError(
                f"Could not explicitly download faster-whisper model {config.model!r}: {exc}"
            ) from exc
    finally:
        _close_model(model)
        model = None
        _cleanup_runtime(device)
    return config.cache_dir


def _prepare_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="flashcards-prepare-asr",
        description=(
            "Explicitly download a faster-whisper model into the local ASR cache. "
            "Normal transcription never downloads models."
        ),
    )
    parser.add_argument("--model", default=DEFAULT_ASR_MODEL)
    parser.add_argument("--cache-dir", type=Path, default=DEFAULT_ASR_CACHE_DIR)
    parser.add_argument("--device", choices=("auto", "cpu", "cuda"), default="auto")
    parser.add_argument("--compute-type", "--compute", default="auto")
    parser.add_argument(
        "--cpu-threads",
        "--threads",
        type=int,
        default=_default_cpu_threads(),
    )
    return parser


def main_prepare(argv: Sequence[str] | None = None) -> None:
    args = _prepare_parser().parse_args(None if argv is None else list(argv))
    config = FasterWhisperConfig(
        model=args.model,
        cache_dir=args.cache_dir,
        device=args.device,
        compute_type=args.compute_type,
        cpu_threads=args.cpu_threads,
    )
    print(
        f"Explicit download requested for faster-whisper model {config.model!r} "
        f"into {config.cache_dir}."
    )
    prepared_path = prepare_asr_model(config)
    print(f"Prepared faster-whisper model {config.model!r} in {prepared_path}.")


if __name__ == "__main__":
    main_prepare()
