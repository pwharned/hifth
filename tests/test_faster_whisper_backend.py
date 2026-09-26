from __future__ import annotations

import contextlib
import io
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import flashcards.faster_whisper_backend as backend  # noqa: E402
from flashcards.faster_whisper_backend import (  # noqa: E402
    DEFAULT_ASR_MODEL,
    FasterWhisperConfig,
    FasterWhisperTranscriber,
    main_prepare,
    prepare_asr_model,
)
from flashcards.transcription import TranscriptionError  # noqa: E402


class FakeWhisperModel:
    def __init__(self, segments: tuple[object, ...] = ()) -> None:
        self.segments = segments
        self.transcribe_calls: list[tuple[tuple[object, ...], dict[str, object]]] = []
        self.materialized = False
        self.closed = False

    def transcribe(self, *args: object, **kwargs: object) -> tuple[object, object]:
        self.transcribe_calls.append((args, kwargs))

        def generate() -> object:
            for segment in self.segments:
                yield segment
            self.materialized = True

        return generate(), SimpleNamespace(language="vi")

    def close(self) -> None:
        self.closed = True


class RecordingFactory:
    def __init__(
        self,
        segments: tuple[object, ...] = (),
        error: BaseException | None = None,
    ) -> None:
        self.segments = segments
        self.error = error
        self.calls: list[tuple[tuple[object, ...], dict[str, object]]] = []
        self.instances: list[FakeWhisperModel] = []

    def __call__(self, *args: object, **kwargs: object) -> FakeWhisperModel:
        self.calls.append((args, kwargs))
        if self.error is not None:
            raise self.error
        instance = FakeWhisperModel(self.segments)
        self.instances.append(instance)
        return instance


def fake_segment() -> object:
    return SimpleNamespace(
        text=" Tôi học.",
        start=0.1,
        end=1.25,
        probability=0.75,
        words=(
            SimpleNamespace(word=" Tôi", start=0.1, end=0.4, probability=0.95),
            SimpleNamespace(word=" học", start=0.5, end=1.0, probability=0.85),
        ),
    )


class FasterWhisperBackendTests(unittest.TestCase):
    def test_constructor_is_lazy(self) -> None:
        probe_calls = 0

        def cuda_probe() -> bool:
            nonlocal probe_calls
            probe_calls += 1
            return False

        factory = RecordingFactory()
        transcriber = FasterWhisperTranscriber(
            FasterWhisperConfig(),
            model_factory=factory,
            backend_version="test-version",
            cuda_available=cuda_probe,
        )

        self.assertEqual(probe_calls, 0)
        self.assertEqual(factory.calls, [])
        self.assertEqual(transcriber.config.model, DEFAULT_ASR_MODEL)

    def test_transcribe_uses_exact_offline_model_and_inference_arguments(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            config = FasterWhisperConfig(
                model="small",
                device="cpu",
                compute_type="auto",
                cpu_threads=3,
                beam_size=4,
                vad_filter=False,
                cache_dir=Path(temporary_directory) / "models",
            )
            factory = RecordingFactory((fake_segment(),))
            transcriber = FasterWhisperTranscriber(
                config,
                model_factory=factory,
                backend_version="9.8.7",
            )
            audio_path = Path(temporary_directory) / "lesson.wav"
            audio_path.write_bytes(b"fake wav")
            result = transcriber.transcribe(audio_path, "Vietnamese")

        self.assertEqual(
            factory.calls,
            [
                (
                    ("small",),
                    {
                        "device": "cpu",
                        "compute_type": "int8",
                        "download_root": str(config.cache_dir),
                        "cpu_threads": 3,
                        "local_files_only": True,
                    },
                )
            ],
        )
        model = factory.instances[0]
        self.assertEqual(
            model.transcribe_calls,
            [
                (
                    (str(audio_path),),
                    {
                        "language": "Vietnamese",
                        "beam_size": 4,
                        "word_timestamps": True,
                        "vad_filter": False,
                        "condition_on_previous_text": False,
                    },
                )
            ],
        )
        self.assertTrue(model.materialized)
        self.assertTrue(model.closed)
        self.assertEqual(result.language, "vi")
        self.assertEqual(result.engine, "faster-whisper")
        self.assertEqual(result.model, "small")
        self.assertEqual(result.engine_version, "9.8.7")
        self.assertEqual((result.segments[0].start_ms, result.segments[0].end_ms), (100, 1_250))
        self.assertEqual(result.segments[0].words[0].text, " Tôi")
        self.assertEqual(result.segments[0].words[0].confidence, 0.95)

    def test_word_without_timestamps_is_skipped_without_losing_segment(self) -> None:
        segment = SimpleNamespace(
            text=" hello world",
            start=0.0,
            end=1.0,
            words=(
                SimpleNamespace(word=" hello", start=0.0, end=0.4, probability=0.9),
                SimpleNamespace(word=" world", start=None, end=None, probability=0.8),
            ),
        )
        with tempfile.TemporaryDirectory() as temporary_directory:
            audio_path = Path(temporary_directory) / "audio.wav"
            audio_path.write_bytes(b"fake wav")
            result = FasterWhisperTranscriber(
                FasterWhisperConfig(device="cpu"),
                model_factory=RecordingFactory((segment,)),
                backend_version="1",
            ).transcribe(audio_path, "en")
        self.assertEqual(result.segments[0].text, " hello world")
        self.assertEqual([word.text for word in result.segments[0].words], [" hello"])

    def test_resolved_local_model_directory_is_fingerprinted_and_passed_to_backend(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            model_dir = root / "model"
            model_dir.mkdir()
            for name in (
                "model.bin",
                "config.json",
                "preprocessor_config.json",
                "tokenizer.json",
                "vocabulary.json",
            ):
                (model_dir / name).write_bytes(f"contents:{name}".encode())
            audio_path = root / "audio.wav"
            audio_path.write_bytes(b"fake wav")
            factory = RecordingFactory((fake_segment(),))
            transcriber = FasterWhisperTranscriber(
                FasterWhisperConfig(device="cpu"),
                model_factory=factory,
                model_resolver=lambda _config: model_dir,
                backend_version="1",
            )
            identity = transcriber.identity()
            transcriber.transcribe(audio_path, "vi")

        self.assertEqual(factory.calls[0][0], (str(model_dir),))
        self.assertTrue(
            any(setting.startswith("model_digest=") for setting in identity["settings"])
        )
        self.assertTrue(
            any(
                setting.startswith("ctranslate2_version=")
                for setting in identity["settings"]
            )
        )

    def test_missing_dependency_message_has_install_command(self) -> None:
        with mock.patch.object(backend.importlib, "import_module", side_effect=ImportError("missing")):
            with self.assertRaises(TranscriptionError) as raised:
                backend._load_dependencies()

        self.assertIn('pip install -e ".[asr]"', str(raised.exception))

    def test_missing_local_model_has_exact_prepare_command_and_never_downloads(self) -> None:
        factory = RecordingFactory(error=FileNotFoundError("model files not found"))
        config = FasterWhisperConfig(model="medium", device="cpu")
        transcriber = FasterWhisperTranscriber(
            config,
            model_factory=factory,
            backend_version="1",
        )

        with tempfile.TemporaryDirectory() as temporary_directory:
            audio_path = Path(temporary_directory) / "audio.wav"
            audio_path.write_bytes(b"fake wav")
            with self.assertRaises(TranscriptionError) as raised:
                transcriber.transcribe(audio_path, "en")

        self.assertIn("flashcards-prepare-asr --model medium", str(raised.exception))
        self.assertIn(f"--cache-dir {config.cache_dir}", str(raised.exception))
        self.assertIs(factory.calls[0][1]["local_files_only"], True)

    def test_auto_cpu_configuration_resolves_to_int8(self) -> None:
        transcriber = FasterWhisperTranscriber(
            FasterWhisperConfig(device="auto", compute_type="auto", cpu_threads=2),
            model_factory=RecordingFactory(),
            backend_version="1",
            cuda_available=lambda: False,
        )

        identity = transcriber.identity()

        self.assertIn("device=cpu", identity["settings"])
        self.assertIn("compute_type=int8", identity["settings"])

    def test_auto_cuda_configuration_resolves_to_int8_float16(self) -> None:
        transcriber = FasterWhisperTranscriber(
            FasterWhisperConfig(device="auto", compute_type="auto"),
            model_factory=RecordingFactory(),
            backend_version="1",
            cuda_available=lambda: True,
        )

        identity = transcriber.identity()

        self.assertIn("device=cuda", identity["settings"])
        self.assertIn("compute_type=int8_float16", identity["settings"])

    def test_cuda_probe_uses_declared_ctranslate2_dependency(self) -> None:
        module = SimpleNamespace(get_cuda_device_count=lambda: 1)
        with mock.patch.object(backend.importlib, "import_module", return_value=module) as load:
            self.assertTrue(backend._cuda_is_available())
        load.assert_called_once_with("ctranslate2")

    def test_explicit_unavailable_cuda_raises_without_cpu_fallback(self) -> None:
        factory = RecordingFactory()
        transcriber = FasterWhisperTranscriber(
            FasterWhisperConfig(device="cuda"),
            model_factory=factory,
            backend_version="1",
            cuda_available=lambda: False,
        )

        with tempfile.TemporaryDirectory() as temporary_directory:
            audio_path = Path(temporary_directory) / "audio.wav"
            audio_path.write_bytes(b"fake wav")
            with self.assertRaisesRegex(TranscriptionError, "explicitly requested"):
                transcriber.transcribe(audio_path, "en")
        self.assertEqual(factory.calls, [])

    def test_prepare_is_the_explicit_download_path(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            config = FasterWhisperConfig(
                model="tiny",
                device="cpu",
                compute_type="float32",
                cpu_threads=2,
                cache_dir=Path(temporary_directory) / "asr",
            )
            factory = RecordingFactory()
            with mock.patch.object(backend, "_load_dependencies", return_value=factory):
                prepared = prepare_asr_model(config)

            self.assertEqual(prepared, config.cache_dir)
            self.assertTrue(config.cache_dir.is_dir())

        self.assertEqual(
            factory.calls,
            [
                (
                    ("tiny",),
                    {
                        "device": "cpu",
                        "compute_type": "float32",
                        "download_root": str(config.cache_dir),
                        "cpu_threads": 2,
                        "local_files_only": False,
                    },
                )
            ],
        )
        self.assertTrue(factory.instances[0].closed)

    def test_prepare_cli_states_that_download_is_explicit_and_forwards_options(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            cache_dir = Path(temporary_directory) / "cache"
            output = io.StringIO()
            with mock.patch.object(
                backend,
                "prepare_asr_model",
                return_value=cache_dir,
            ) as prepare, contextlib.redirect_stdout(output):
                main_prepare(
                    [
                        "--model",
                        "tiny",
                        "--cache-dir",
                        str(cache_dir),
                        "--device",
                        "cpu",
                        "--compute-type",
                        "float32",
                        "--cpu-threads",
                        "2",
                    ]
                )

        config = prepare.call_args.args[0]
        self.assertEqual(config.model, "tiny")
        self.assertEqual(config.cache_dir, cache_dir)
        self.assertEqual(config.cpu_threads, 2)
        self.assertIn("Explicit download requested", output.getvalue())

    def test_default_cache_follows_xdg_or_home_cache_layout(self) -> None:
        with mock.patch.dict(os.environ, {"XDG_CACHE_HOME": "/tmp/example-xdg"}):
            self.assertEqual(
                backend._default_cache_dir(),
                Path("/tmp/example-xdg/hifth-flashcards/asr"),
            )
        with mock.patch.dict(os.environ, {}, clear=True), mock.patch.object(
            backend.Path,
            "home",
            return_value=Path("/home/example"),
        ):
            self.assertEqual(
                backend._default_cache_dir(),
                Path("/home/example/.cache/hifth-flashcards/asr"),
            )

    def test_config_rejects_invalid_runtime_values(self) -> None:
        invalid = (
            {"device": "metal"},
            {"cpu_threads": 0},
            {"beam_size": 0},
            {"vad_filter": 1},
            {"compute_type": ""},
        )
        for kwargs in invalid:
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                FasterWhisperConfig(**kwargs)


if __name__ == "__main__":
    unittest.main()
