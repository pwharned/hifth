from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from flashcards.import_media import (  # noqa: E402
    AudioStream,
    SubtitleStream,
    extract_embedded_subtitle,
    main,
    artifact_from_media,
    probe_subtitle_streams,
    probe_audio_streams,
    select_embedded_subtitle,
)
from flashcards.media import MediaProcessingError  # noqa: E402
from flashcards.models import MediaArtifact  # noqa: E402
from flashcards.subtitles import parse_subtitles  # noqa: E402
from flashcards.transcription import (  # noqa: E402
    TranscribedSegment,
    TranscriptionCache,
    TranscriptionResult,
)


SUBTITLE_PAYLOAD = "1\n00:00:00,100 --> 00:00:00,900\nXin chào.\n"


class FakeTranscriber:
    def __init__(self) -> None:
        self.calls: list[tuple[Path, str]] = []

    def identity(self) -> dict[str, object]:
        return {
            "engine": "fake-asr",
            "model": "fake-model",
            "engine_version": "1",
            "settings": ("test=true",),
        }

    def transcribe(self, audio_path: Path, language: str) -> TranscriptionResult:
        self.calls.append((audio_path, language))
        if audio_path.suffix != ".wav" or not audio_path.is_file():
            raise AssertionError("transcriber did not receive a temporary WAV")
        return TranscriptionResult(
            language=language,
            segments=(TranscribedSegment("Xin chào.", 100, 900),),
            engine="fake-asr",
            model="fake-model",
            engine_version="1",
            settings=("test=true",),
        )


def write_normalized_audio(_source: Path, destination: Path, **_options: object) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(b"fake wav")
    return destination


class FlashcardImportMediaTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.media = self.root / "lesson.mp4"
        self.media.write_bytes(b"not real media, probes are mocked")
        self.subtitles = self.root / "lesson.vi.srt"
        self.subtitles.write_text(SUBTITLE_PAYLOAD, encoding="utf-8")

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _asr_manifest(
        self,
        *,
        streams: tuple[SubtitleStream, ...] = (),
        force_transcribe: bool = False,
        cache: TranscriptionCache | None = None,
        refresh: bool = False,
        audio_streams: tuple[AudioStream, ...] = (AudioStream(1),),
    ) -> tuple[MediaArtifact, FakeTranscriber]:
        transcriber = FakeTranscriber()
        with (
            patch("flashcards.import_media.probe_subtitle_streams", return_value=streams),
            patch("flashcards.import_media.probe_duration_ms", return_value=1_000),
            patch("flashcards.import_media.probe_audio_streams", return_value=audio_streams),
            patch("flashcards.import_media.normalize_audio", side_effect=write_normalized_audio),
        ):
            manifest = artifact_from_media(
                self.media,
                language="vi",
                force_transcribe=force_transcribe,
                transcriber=transcriber,
                transcription_cache=cache,
                refresh_transcription=refresh,
                stored_media_path="../media/lesson.mp4",
            )
        return manifest, transcriber

    def test_subtitle_stream_is_frozen_validated_and_marks_image_codecs_unusable(self) -> None:
        text = SubtitleStream(2, "SUBRIP", "vie", "Vietnamese", True, False)
        image = SubtitleStream(3, "hdmv_pgs_subtitle", "eng")

        self.assertEqual(text.codec_name, "subrip")
        self.assertTrue(text.is_text)
        self.assertTrue(text.usable)
        self.assertFalse(image.is_text)
        self.assertFalse(image.is_usable)
        with self.assertRaisesRegex(ValueError, "nonnegative integer"):
            SubtitleStream(-1, "subrip")
        with self.assertRaisesRegex(ValueError, "boolean"):
            SubtitleStream(1, "subrip", default=1)  # type: ignore[arg-type]
        with self.assertRaisesRegex(AttributeError, "cannot assign"):
            text.index = 4  # type: ignore[misc]

    def test_probe_parses_text_and_image_stream_json(self) -> None:
        payload = {
            "streams": [
                {
                    "index": 2,
                    "codec_name": "subrip",
                    "tags": {"language": "vie", "title": "Full"},
                    "disposition": {"default": 1, "forced": 0},
                },
                {
                    "index": 5,
                    "codec_name": "hdmv_pgs_subtitle",
                    "tags": {"language": "eng"},
                    "disposition": {"default": 0, "forced": 1},
                },
            ]
        }
        completed = SimpleNamespace(
            returncode=0,
            stdout=json.dumps(payload),
            stderr="",
        )
        with patch("flashcards.import_media.subprocess.run", return_value=completed) as run:
            streams = probe_subtitle_streams(self.media, ffprobe="custom-ffprobe")

        self.assertEqual(
            streams,
            (
                SubtitleStream(2, "subrip", "vie", "Full", True, False),
                SubtitleStream(5, "hdmv_pgs_subtitle", "eng", None, False, True),
            ),
        )
        self.assertFalse(streams[1].usable)
        command = run.call_args.args[0]
        self.assertEqual(command[0], "custom-ffprobe")
        self.assertEqual(command[-1], str(self.media))
        self.assertIn("-select_streams", command)
        self.assertEqual(run.call_args.kwargs["shell"], False)

    def test_probe_reports_invalid_json_and_ffprobe_failures(self) -> None:
        with patch(
            "flashcards.import_media.subprocess.run",
            return_value=SimpleNamespace(returncode=0, stdout="not json", stderr=""),
        ):
            with self.assertRaisesRegex(MediaProcessingError, "invalid JSON"):
                probe_subtitle_streams(self.media)

    def test_audio_stream_probe_reads_source_timeline_offset(self) -> None:
        payload = {
            "format": {"start_time": "1.125"},
            "streams": [
                {
                    "index": 1,
                    "start_time": "2.125",
                    "tags": {"language": "vie"},
                    "disposition": {"default": 1},
                }
            ]
        }
        completed = SimpleNamespace(returncode=0, stdout=json.dumps(payload), stderr="")
        with patch("flashcards.import_media.subprocess.run", return_value=completed):
            streams = probe_audio_streams(self.media)
        self.assertEqual(streams, (AudioStream(1, 1_000, "vie", None, True),))

        with patch(
            "flashcards.import_media.subprocess.run",
            return_value=SimpleNamespace(returncode=1, stdout="", stderr="bad container"),
        ):
            with self.assertRaisesRegex(MediaProcessingError, "bad container"):
                probe_subtitle_streams(self.media)

    def test_language_alias_default_and_non_forced_selection(self) -> None:
        default = SubtitleStream(2, "subrip", "vie", default=True)
        alternate = SubtitleStream(3, "ass", "vi")
        english = SubtitleStream(4, "mov_text", "eng", default=True)
        self.assertIs(
            select_embedded_subtitle((alternate, default, english), "Vietnamese"),
            default,
        )
        self.assertIs(select_embedded_subtitle((default, english), "en"), english)

        forced_default = SubtitleStream(7, "subrip", "vi", default=True, forced=True)
        non_forced = SubtitleStream(8, "subrip", "vie")
        self.assertIs(
            select_embedded_subtitle((forced_default, non_forced), "vi"),
            non_forced,
        )
        unlabelled_full = SubtitleStream(9, "subrip", default=True)
        self.assertIs(
            select_embedded_subtitle((forced_default, unlabelled_full), "vi"),
            unlabelled_full,
        )
        self.assertIsNone(select_embedded_subtitle((forced_default,), "vi"))
        for language, tag in (
            ("af", "afr"),
            ("be", "bel"),
            ("gu", "guj"),
            ("kk", "kaz"),
            ("pa", "pan"),
        ):
            with self.subTest(language=language, tag=tag):
                stream = SubtitleStream(10, "subrip", tag)
                self.assertIs(select_embedded_subtitle((stream,), language), stream)

    def test_ambiguous_matching_streams_require_explicit_index(self) -> None:
        streams = (
            SubtitleStream(2, "subrip", "vie"),
            SubtitleStream(4, "ass", "vi"),
        )
        with self.assertRaisesRegex(ValueError, "--subtitle-stream"):
            select_embedded_subtitle(streams, "vi")
        self.assertIs(select_embedded_subtitle(streams, "vi", 4), streams[1])

    def test_unlabelled_stream_is_fallback_but_image_stream_is_not(self) -> None:
        unlabelled = SubtitleStream(2, "webvtt")
        english = SubtitleStream(3, "subrip", "eng")
        image = SubtitleStream(4, "dvd_subtitle", "vie")

        self.assertIs(select_embedded_subtitle((english, unlabelled), "vi"), unlabelled)
        self.assertIsNone(select_embedded_subtitle((english, image), "vi"))
        with self.assertRaisesRegex(ValueError, "non-text codec"):
            select_embedded_subtitle((image,), "vi", requested_index=4)
        with self.assertRaisesRegex(ValueError, "--subtitle-stream"):
            select_embedded_subtitle(
                (SubtitleStream(8, "subrip"), SubtitleStream(9, "ass")),
                "vi",
            )

    def test_incompatible_subtitle_selection_flags_are_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "subtitle_stream"):
            artifact_from_media(
                self.media,
                language="vi",
                subtitle_path=self.subtitles,
                subtitle_stream=2,
            )
        with self.assertRaisesRegex(ValueError, "subtitle_stream"):
            artifact_from_media(
                self.media,
                language="vi",
                force_transcribe=True,
                subtitle_stream=2,
                transcriber=FakeTranscriber(),
            )

    def test_extract_maps_the_exact_global_stream_without_a_shell(self) -> None:
        output = self.root / "captions.srt"
        completed = SimpleNamespace(returncode=0, stdout="", stderr="")
        with patch("flashcards.import_media.subprocess.run", return_value=completed) as run:
            result = extract_embedded_subtitle(
                self.media,
                SubtitleStream(7, "mov_text", "vie"),
                output,
                ffmpeg="custom-ffmpeg",
            )

        self.assertEqual(result, output)
        command = run.call_args.args[0]
        map_position = command.index("-map")
        self.assertEqual(command[map_position + 1], "0:7")
        self.assertEqual(command[command.index("-c:s") + 1], "srt")
        self.assertEqual(run.call_args.kwargs["shell"], False)

    def test_external_subtitles_never_probe_or_invoke_asr(self) -> None:
        transcriber = FakeTranscriber()
        with (
            patch(
                "flashcards.import_media.probe_subtitle_streams",
                side_effect=AssertionError("embedded streams must not be probed"),
            ),
            patch(
                "flashcards.import_media.probe_audio_streams",
                return_value=(AudioStream(1),),
            ),
            patch("flashcards.subtitles.probe_duration_ms", return_value=1_000),
        ):
            manifest = artifact_from_media(
                self.media,
                language="vi",
                subtitle_path=self.subtitles,
                transcriber=transcriber,
            )

        self.assertEqual(transcriber.calls, [])
        self.assertEqual(manifest.utterances[0].provenance, ("subtitle:srt",))

    def test_malformed_explicit_subtitles_never_fall_back_to_asr(self) -> None:
        self.subtitles.write_text("this is not a subtitle file", encoding="utf-8")
        transcriber = FakeTranscriber()
        with (
            patch("flashcards.subtitles.probe_duration_ms", return_value=1_000),
            patch(
                "flashcards.import_media.probe_audio_streams",
                return_value=(AudioStream(1),),
            ),
        ):
            with self.assertRaisesRegex(ValueError, "no timing line"):
                artifact_from_media(
                    self.media,
                    language="vi",
                    subtitle_path=self.subtitles,
                    transcriber=transcriber,
                )
        self.assertEqual(transcriber.calls, [])

    def test_external_subtitles_cannot_be_combined_with_force_transcription(self) -> None:
        with self.assertRaisesRegex(ValueError, "cannot be combined"):
            artifact_from_media(
                self.media,
                language="vi",
                subtitle_path=self.subtitles,
                force_transcribe=True,
                transcriber=FakeTranscriber(),
            )

    def test_embedded_subtitle_import_replaces_provenance_and_skips_asr(self) -> None:
        stream = SubtitleStream(4, "mov_text", "vie", "Vietnamese", True, False)
        transcriber = FakeTranscriber()

        def extract(_media: Path, selected: SubtitleStream, output: Path) -> Path:
            self.assertIs(selected, stream)
            output.write_text(SUBTITLE_PAYLOAD, encoding="utf-8")
            return output

        with (
            patch(
                "flashcards.import_media.probe_audio_streams",
                return_value=(AudioStream(1),),
            ),
            patch("flashcards.import_media.probe_subtitle_streams", return_value=(stream,)),
            patch("flashcards.import_media.extract_embedded_subtitle", side_effect=extract),
            patch("flashcards.subtitles.probe_duration_ms", return_value=1_000),
        ):
            manifest = artifact_from_media(
                self.media,
                language="vi",
                transcriber=transcriber,
            )

        self.assertEqual(transcriber.calls, [])
        self.assertEqual(
            manifest.utterances[0].provenance,
            (
                "subtitle:embedded",
                "subtitle:stream=4",
                "subtitle:codec=mov_text",
                "subtitle:language=vie",
            ),
        )

    def test_no_matching_subtitles_uses_asr_and_cache(self) -> None:
        cache_path = self.root / "transcription-cache.json"
        cache = TranscriptionCache(cache_path)
        manifest, transcriber = self._asr_manifest(cache=cache, refresh=True)

        self.assertEqual(len(transcriber.calls), 1)
        self.assertEqual(transcriber.calls[0][1], "vi")
        self.assertFalse(transcriber.calls[0][0].exists())
        self.assertTrue(cache_path.is_file())
        cached_manifest, cached_transcriber = self._asr_manifest(cache=cache)
        self.assertEqual(cached_transcriber.calls, [])
        self.assertEqual(cached_manifest.utterances, manifest.utterances)
        self.assertEqual(manifest.media[0].path, "../media/lesson.mp4")
        self.assertEqual(
            manifest.utterances[0].provenance,
            (
                "transcript:asr",
                "engine:fake-asr",
                "model:fake-model",
                "version:1",
                "source-offset-ms:0",
                "test=true",
                "audio-stream:1",
            ),
        )

    def test_asr_timestamps_include_source_audio_offset(self) -> None:
        manifest, _transcriber = self._asr_manifest(
            audio_streams=(AudioStream(1, 50),),
        )
        self.assertEqual(
            (manifest.utterances[0].start_ms, manifest.utterances[0].end_ms),
            (150, 950),
        )
        self.assertIn("audio-stream:1", manifest.utterances[0].provenance)

    def test_multiple_audio_streams_fail_instead_of_transcribing_wrong_track(self) -> None:
        with self.assertRaisesRegex(MediaProcessingError, "multiple audio streams"):
            self._asr_manifest(
                audio_streams=(AudioStream(1), AudioStream(2)),
            )

    def test_source_mutation_during_asr_is_rejected(self) -> None:
        media = self.media

        class MutatingTranscriber(FakeTranscriber):
            def transcribe(self, audio_path: Path, language: str) -> TranscriptionResult:
                result = super().transcribe(audio_path, language)
                media.write_bytes(b"replacement media with a different size")
                return result

        transcriber = MutatingTranscriber()
        with (
            patch("flashcards.import_media.probe_subtitle_streams", return_value=()),
            patch("flashcards.import_media.probe_duration_ms", return_value=1_000),
            patch(
                "flashcards.import_media.probe_audio_streams",
                return_value=(AudioStream(1),),
            ),
            patch("flashcards.import_media.normalize_audio", side_effect=write_normalized_audio),
        ):
            with self.assertRaisesRegex(MediaProcessingError, "changed while"):
                artifact_from_media(
                    self.media,
                    language="vi",
                    transcriber=transcriber,
                )

    def test_language_name_is_normalized_for_asr(self) -> None:
        transcriber = FakeTranscriber()
        with (
            patch("flashcards.import_media.probe_subtitle_streams", return_value=()),
            patch("flashcards.import_media.probe_duration_ms", return_value=1_000),
            patch(
                "flashcards.import_media.probe_audio_streams",
                return_value=(AudioStream(1),),
            ),
            patch("flashcards.import_media.normalize_audio", side_effect=write_normalized_audio),
        ):
            artifact_from_media(
                self.media,
                language="Vietnamese",
                transcriber=transcriber,
            )
        self.assertEqual(transcriber.calls[0][1], "vi")

    def test_asr_language_mismatch_is_rejected(self) -> None:
        class MismatchedLanguageTranscriber(FakeTranscriber):
            def transcribe(self, audio_path: Path, language: str) -> TranscriptionResult:
                result = super().transcribe(audio_path, language)
                return TranscriptionResult(
                    language="en",
                    segments=result.segments,
                    engine=result.engine,
                    model=result.model,
                    engine_version=result.engine_version,
                    settings=result.settings,
                )

        with (
            patch("flashcards.import_media.probe_subtitle_streams", return_value=()),
            patch("flashcards.import_media.probe_duration_ms", return_value=1_000),
            patch(
                "flashcards.import_media.probe_audio_streams",
                return_value=(AudioStream(1),),
            ),
            patch("flashcards.import_media.normalize_audio", side_effect=write_normalized_audio),
        ):
            with self.assertRaisesRegex(ValueError, "ASR returned language"):
                artifact_from_media(
                    self.media,
                    language="vi",
                    transcriber=MismatchedLanguageTranscriber(),
                )

    def test_image_subtitle_falls_back_to_asr(self) -> None:
        image_stream = SubtitleStream(2, "hdmv_pgs_subtitle", "vie", default=True)
        manifest, transcriber = self._asr_manifest(streams=(image_stream,))

        self.assertEqual(len(transcriber.calls), 1)
        self.assertEqual(manifest.utterances[0].provenance[0], "transcript:asr")

    def test_force_transcription_does_not_probe_embedded_subtitles(self) -> None:
        transcriber = FakeTranscriber()
        with (
            patch(
                "flashcards.import_media.probe_subtitle_streams",
                side_effect=AssertionError("force transcription must skip subtitle probing"),
            ),
            patch("flashcards.import_media.probe_duration_ms", return_value=1_000),
            patch(
                "flashcards.import_media.probe_audio_streams",
                return_value=(AudioStream(1),),
            ),
            patch("flashcards.import_media.normalize_audio", side_effect=write_normalized_audio),
        ):
            manifest = artifact_from_media(
                self.media,
                language="vi",
                force_transcribe=True,
                transcriber=transcriber,
            )

        self.assertEqual(len(transcriber.calls), 1)
        self.assertTrue(manifest.utterances)

    def test_no_subtitles_without_transcriber_has_clear_error(self) -> None:
        with (
            patch("flashcards.import_media.probe_subtitle_streams", return_value=()),
            patch(
                "flashcards.import_media.probe_audio_streams",
                return_value=(AudioStream(1),),
            ),
        ):
            with self.assertRaisesRegex(ValueError, "no transcriber"):
                artifact_from_media(self.media, language="vi")

    def test_media_without_audio_has_clear_error(self) -> None:
        with (
            patch("flashcards.import_media.probe_subtitle_streams", return_value=()),
            patch("flashcards.import_media.probe_duration_ms", return_value=1_000),
            patch("flashcards.import_media.probe_audio_streams", return_value=()),
        ):
            with self.assertRaisesRegex(MediaProcessingError, "no usable audio"):
                artifact_from_media(
                    self.media,
                    language="vi",
                    transcriber=FakeTranscriber(),
                )

    def test_asr_manifest_roundtrips(self) -> None:
        manifest, _transcriber = self._asr_manifest()
        restored = MediaArtifact.from_json(manifest.to_json())
        self.assertEqual(restored, manifest)
        self.assertRegex(manifest.media[0].checksum_sha256 or "", r"^[0-9a-f]{64}$")
        self.assertEqual(manifest.media[0].duration_ms, 1_000)

    def test_cli_refuses_to_overwrite_existing_output(self) -> None:
        output = self.root / "project.json"
        output.write_text("keep me", encoding="utf-8")
        with patch("flashcards.import_media.probe_subtitle_streams") as probe:
            with self.assertRaisesRegex(SystemExit, "--force"):
                main(
                    [
                        str(self.media),
                        "--language",
                        "vi",
                        "--out",
                        str(output),
                    ]
                )
        probe.assert_not_called()
        self.assertEqual(output.read_text(encoding="utf-8"), "keep me")

    def test_cli_never_overwrites_media_or_subtitle_inputs(self) -> None:
        for output in (self.media, self.subtitles):
            original = output.read_bytes()
            with self.subTest(output=output), self.assertRaisesRegex(
                SystemExit, "must differ from input"
            ):
                main(
                    [
                        str(self.media),
                        "--subtitles",
                        str(self.subtitles),
                        "--language",
                        "vi",
                        "--out",
                        str(output),
                        "--force",
                    ]
                )
            self.assertEqual(output.read_bytes(), original)

    def test_cli_force_overwrites_and_stores_relative_media_path(self) -> None:
        output = self.root / "projects" / "lesson.json"
        output.parent.mkdir()
        output.write_text("old", encoding="utf-8")
        with (
            patch(
                "flashcards.import_media.probe_audio_streams",
                return_value=(AudioStream(1),),
            ),
            patch("flashcards.subtitles.probe_duration_ms", return_value=1_000),
            patch("builtins.print") as print_output,
        ):
            main(
                [
                    str(self.media),
                    "--subtitles",
                    str(self.subtitles),
                    "--language",
                    "vi",
                    "--out",
                    str(output),
                    "--force",
                ]
            )

        payload = output.read_text(encoding="utf-8")
        manifest = MediaArtifact.from_json(payload)
        self.assertEqual(manifest.media[0].path, "../lesson.mp4")
        self.assertNotIn("cards", json.loads(payload))
        self.assertNotIn("learning_units", json.loads(payload))
        self.assertIn("external subtitles", print_output.call_args.args[0])

    def test_cli_lazily_configures_asr_and_persists_cache_next_to_manifest(self) -> None:
        output = self.root / "projects" / "lesson.json"
        model_cache = self.root / "models"
        backend = FakeTranscriber()
        with (
            patch("flashcards.import_media.probe_subtitle_streams", return_value=()) as probe,
            patch("flashcards.import_media.probe_duration_ms", return_value=1_000),
            patch(
                "flashcards.import_media.probe_audio_streams",
                return_value=(AudioStream(1),),
            ),
            patch("flashcards.import_media.normalize_audio", side_effect=write_normalized_audio),
            patch(
                "flashcards.faster_whisper_backend.FasterWhisperTranscriber",
                return_value=backend,
            ) as constructor,
            patch("builtins.print") as print_output,
        ):
            main(
                [
                    str(self.media),
                    "--language",
                    "vi",
                    "--out",
                    str(output),
                    "--asr-model",
                    "small",
                    "--device",
                    "cpu",
                    "--compute-type",
                    "int8",
                    "--cpu-threads",
                    "2",
                    "--asr-cache-dir",
                    str(model_cache),
                    "--refresh-transcription",
                ]
            )

        config = constructor.call_args.args[0]
        self.assertEqual(config.model, "small")
        self.assertEqual(config.device, "cpu")
        self.assertEqual(config.compute_type, "int8")
        self.assertEqual(config.cpu_threads, 2)
        self.assertEqual(config.cache_dir, model_cache)
        self.assertEqual(probe.call_count, 1)
        self.assertEqual(len(backend.calls), 1)
        self.assertTrue((output.parent / "lesson.transcription-cache.json").is_file())
        manifest = MediaArtifact.from_json(output.read_text(encoding="utf-8"))
        self.assertEqual(manifest.utterances[0].provenance[0], "transcript:asr")
        self.assertIn("ASR transcription", print_output.call_args.args[0])


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "ffmpeg is required")
class FlashcardImportMediaIntegrationTests(unittest.TestCase):
    def test_real_embedded_srt_is_probed_selected_and_extracted(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir_name:
            root = Path(temp_dir_name)
            subtitles = root / "captions.srt"
            subtitles.write_text(SUBTITLE_PAYLOAD, encoding="utf-8")
            media = root / "lesson.mkv"
            subprocess.run(
                [
                    "ffmpeg",
                    "-y",
                    "-loglevel",
                    "error",
                    "-f",
                    "lavfi",
                    "-i",
                    "color=c=black:s=16x16:r=1:d=1",
                    "-f",
                    "lavfi",
                    "-i",
                    "sine=frequency=440:duration=1",
                    "-i",
                    str(subtitles),
                    "-map",
                    "0:v:0",
                    "-map",
                    "1:a:0",
                    "-map",
                    "2:s:0",
                    "-c:v",
                    "ffv1",
                    "-c:s",
                    "srt",
                    "-metadata:s:s:0",
                    "language=vie",
                    str(media),
                ],
                check=True,
            )

            streams = probe_subtitle_streams(media)
            selected = select_embedded_subtitle(streams, "vi")
            self.assertIsNotNone(selected)
            assert selected is not None
            extracted = extract_embedded_subtitle(media, selected, root / "extracted.srt")

            cues = parse_subtitles(extracted.read_text(encoding="utf-8"))
            self.assertEqual(cues[0].text, "Xin chào.")


if __name__ == "__main__":
    unittest.main()
