from __future__ import annotations

import json
import math
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from flashcards.transcription import (  # noqa: E402
    TRANSCRIPTION_CACHE_VERSION,
    CachedTranscriber,
    TranscribedSegment,
    TranscribedWord,
    TranscriptionCache,
    TranscriptionError,
    TranscriptionResult,
    TranscriptionValidationError,
    utterances_from_transcription,
)


def make_result(
    segments: tuple[TranscribedSegment, ...] = (),
    *,
    engine: str = "fake-asr",
    model: str = "fake-model",
    version: str = "1.2.3",
    settings: tuple[str, ...] = ("beam_size=2",),
) -> TranscriptionResult:
    return TranscriptionResult(
        language="vi",
        segments=segments,
        engine=engine,
        model=model,
        engine_version=version,
        settings=settings,
    )


class FakeTranscriber:
    def __init__(
        self,
        result: TranscriptionResult,
        identity: dict[str, Any] | None = None,
    ) -> None:
        self.result = result
        self.identity_value = identity or {
            "engine": result.engine,
            "model": result.model,
            "engine_version": result.engine_version,
            "settings": result.settings,
        }
        self.calls: list[tuple[Path, str]] = []

    def identity(self) -> dict[str, Any]:
        return dict(self.identity_value)

    def transcribe(self, audio_path: Path, language: str) -> TranscriptionResult:
        self.calls.append((audio_path, language))
        return self.result


class FlashcardTranscriptionTests(unittest.TestCase):
    def test_dataclass_round_trip_is_deterministic(self) -> None:
        result = make_result(
            (
                TranscribedSegment(
                    text="Tôi học.",
                    start_ms=100,
                    end_ms=900,
                    words=(
                        TranscribedWord(" Tôi", 100, 300, 0.9),
                        TranscribedWord(" học", 350, 700, 0.8),
                    ),
                    confidence=0.75,
                ),
            )
        )
        serialized = result.to_dict()

        self.assertEqual(TranscriptionResult.from_dict(serialized), result)
        self.assertEqual(TranscriptionResult.from_dict(serialized).to_dict(), serialized)
        self.assertEqual(
            json.dumps(result.to_dict(), ensure_ascii=False, separators=(",", ":")),
            json.dumps(serialized, ensure_ascii=False, separators=(",", ":")),
        )

    def test_dataclasses_reject_nonfinite_invalid_intervals_and_confidence(self) -> None:
        invalid_words = (
            {"text": "x", "start_ms": -1, "end_ms": 2},
            {"text": "x", "start_ms": 2, "end_ms": 2},
            {"text": "x", "start_ms": 0, "end_ms": math.inf},
            {"text": "x", "start_ms": math.nan, "end_ms": 2},
            {"text": "x", "start_ms": 0, "end_ms": 2, "confidence": 1.1},
            {"text": "x", "start_ms": False, "end_ms": 2},
        )
        for kwargs in invalid_words:
            with self.subTest(kwargs=kwargs), self.assertRaises(TranscriptionValidationError):
                TranscribedWord(**kwargs)

        with self.assertRaises(TranscriptionValidationError):
            TranscribedSegment("x", 0, 1, words=("not-a-word",))
        with self.assertRaises(TranscriptionValidationError):
            TranscriptionResult("vi", (), "engine", "model", "version", ("",))

    def test_repeated_words_are_mapped_sequentially_and_punctuation_is_untimed(self) -> None:
        segment = TranscribedSegment(
            "go go.",
            0,
            500,
            words=(
                TranscribedWord(" go", 50, 150, 0.8),
                TranscribedWord(" go.", 200, 350, 0.6),
            ),
        )

        utterance = utterances_from_transcription(make_result((segment,)), "media-1", 500)[0]

        self.assertEqual([token.text for token in utterance.tokens], ["go", "go", "."])
        self.assertEqual(
            [(token.start_ms, token.end_ms) for token in utterance.tokens],
            [(50, 150), (200, 350), (None, None)],
        )
        self.assertAlmostEqual(utterance.confidence or 0, 0.7)

    def test_vietnamese_word_timing_keeps_normalized_source_spans(self) -> None:
        segment = TranscribedSegment(
            "  To\u0302i học.  ",
            0,
            1_000,
            words=(
                TranscribedWord(" To\u0302i", 10, 300, 0.95),
                TranscribedWord(" học", 350, 700, 0.85),
            ),
        )

        utterance = utterances_from_transcription(make_result((segment,)), "media-vi", 1_000)[0]

        self.assertEqual(utterance.text, "Tôi học.")
        self.assertEqual([token.text for token in utterance.tokens], ["Tôi", "học", "."])
        self.assertEqual((utterance.tokens[0].start_ms, utterance.tokens[0].end_ms), (10, 300))
        self.assertEqual(utterance.tokens[0].span.extract(utterance.text), "Tôi")
        self.assertIsNone(utterance.tokens[-1].start_ms)

    def test_cjk_word_timing_is_assigned_to_every_contained_base_token(self) -> None:
        segment = TranscribedSegment(
            "我是学生。",
            0,
            1_000,
            words=(
                TranscribedWord("我", 0, 150, 0.9),
                TranscribedWord("是", 150, 300, 0.9),
                TranscribedWord("学生", 300, 800, 0.8),
            ),
        )

        tokens = utterances_from_transcription(make_result((segment,)), "media-zh", 1_000)[
            0
        ].tokens

        self.assertEqual([token.text for token in tokens], ["我", "是", "学", "生", "。"])
        self.assertEqual(
            [(tokens[index].start_ms, tokens[index].end_ms) for index in (2, 3)],
            [(300, 800), (300, 800)],
        )
        self.assertIsNone(tokens[4].start_ms)

    def test_unmatched_word_does_not_prevent_later_exact_match(self) -> None:
        segment = TranscribedSegment(
            "hello world!",
            0,
            1_000,
            words=(
                TranscribedWord("missing", 0, 100, 0.2),
                TranscribedWord(" world", 300, 700, 0.9),
            ),
        )

        tokens = utterances_from_transcription(make_result((segment,)), "media", 1_000)[0].tokens

        self.assertEqual([token.text for token in tokens], ["hello", "world", "!"])
        self.assertIsNone(tokens[0].start_ms)
        self.assertEqual((tokens[1].start_ms, tokens[1].end_ms), (300, 700))
        self.assertIsNone(tokens[2].start_ms)

    def test_tiny_overruns_are_clamped_without_zero_length_timing(self) -> None:
        segment = TranscribedSegment(
            "ok x",
            0,
            1_000.5,
            words=(
                TranscribedWord("ok", 900, 1_000.5, 0.8),
                TranscribedWord("x", 1_000, 1_000.5, 0.7),
            ),
        )

        utterance = utterances_from_transcription(make_result((segment,)), "media", 1_000)[0]

        self.assertEqual((utterance.start_ms, utterance.end_ms), (0, 1_000))
        self.assertEqual(
            (utterance.tokens[0].start_ms, utterance.tokens[0].end_ms),
            (900, 1_000),
        )
        self.assertIsNone(utterance.tokens[1].start_ms)

    def test_ids_are_stable_after_nfc_normalization_and_provenance_is_complete(self) -> None:
        decomposed = TranscribedSegment(" To\u0302i ", 0, 500)
        composed = TranscribedSegment("Tôi", 0.0, 500.0)
        first = utterances_from_transcription(make_result((decomposed,)), "media", 500)[0]
        second = utterances_from_transcription(make_result((composed,)), "media", 500)[0]

        self.assertEqual(first.id, second.id)
        self.assertEqual(first.text, "Tôi")
        for expected in (
            "transcript:asr",
            "engine:fake-asr",
            "model:fake-model",
            "version:1.2.3",
            "beam_size=2",
        ):
            self.assertIn(expected, first.provenance)

    def test_source_audio_offset_is_applied_to_segment_and_word_times(self) -> None:
        segment = TranscribedSegment(
            "hello",
            0,
            500,
            words=(TranscribedWord("hello", 50, 400, 0.9),),
        )
        utterance = utterances_from_transcription(
            make_result((segment,)),
            "media",
            3_000,
            time_offset_ms=2_000,
        )[0]
        self.assertEqual((utterance.start_ms, utterance.end_ms), (2_000, 2_500))
        self.assertEqual(
            (utterance.tokens[0].start_ms, utterance.tokens[0].end_ms),
            (2_050, 2_400),
        )
        self.assertIn("source-offset-ms:2000", utterance.provenance)

    def test_no_valid_speech_raises_clear_error(self) -> None:
        with self.assertRaisesRegex(TranscriptionError, "no speech"):
            utterances_from_transcription(make_result(), "media", 1_000)
        outside_media = TranscribedSegment("hello", 1_000, 1_500)
        with self.assertRaisesRegex(TranscriptionError, "no speech"):
            utterances_from_transcription(make_result((outside_media,)), "media", 1_000)

    def test_cache_hit_refresh_and_identity_changes(self) -> None:
        segment = TranscribedSegment("hello", 0, 100)
        first_result = make_result((segment,))
        with tempfile.TemporaryDirectory() as temporary_directory:
            cache = TranscriptionCache(Path(temporary_directory) / "transcriptions.json")
            backend = FakeTranscriber(first_result)
            cached = CachedTranscriber(backend, cache)

            first = cached.transcribe(Path("audio.wav"), "a" * 64, "vi")
            second = cached.transcribe(Path("audio.wav"), "a" * 64, "vi")
            refreshed = cached.transcribe(
                Path("audio.wav"),
                "a" * 64,
                "vi",
                refresh=True,
            )
            cached.transcribe(Path("audio.wav"), "b" * 64, "vi")

        self.assertEqual((first, second, refreshed), (first_result,) * 3)
        self.assertEqual(len(backend.calls), 3)

    def test_backend_result_must_match_identity_before_cache_write(self) -> None:
        result = make_result(model="returned-model")
        backend = FakeTranscriber(
            result,
            {
                "engine": result.engine,
                "model": "configured-model",
                "engine_version": result.engine_version,
                "settings": result.settings,
            },
        )
        with tempfile.TemporaryDirectory() as temporary_directory:
            cache_path = Path(temporary_directory) / "transcriptions.json"
            service = CachedTranscriber(backend, TranscriptionCache(cache_path))
            with self.assertRaisesRegex(TranscriptionValidationError, "model"):
                service.transcribe(Path("audio.wav"), "a" * 64, "vi")
            self.assertFalse(cache_path.exists())

    def test_corrupt_and_unsupported_caches_are_ignored_until_put(self) -> None:
        result = make_result()
        for payload in (
            "not JSON\n",
            json.dumps({"schema_version": 99, "entries": {}}),
        ):
            with self.subTest(payload=payload), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "transcriptions.json"
                path.write_text(payload, encoding="utf-8")
                cache = TranscriptionCache(path)

                self.assertIsNone(cache.get("missing"))
                self.assertEqual(path.read_text(encoding="utf-8"), payload)
                cache.put("key", result)
                recovered = json.loads(path.read_text(encoding="utf-8"))

                self.assertEqual(
                    recovered["schema_version"],
                    TRANSCRIPTION_CACHE_VERSION,
                )
                self.assertEqual(TranscriptionResult.from_dict(recovered["entries"]["key"]), result)

    def test_cache_permissions_are_private_when_new_and_preserved_afterward(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "transcriptions.json"
            cache = TranscriptionCache(path)
            cache.put("first", make_result())
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

            os.chmod(path, 0o640)
            cache.put("second", make_result())
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o640)


if __name__ == "__main__":
    unittest.main()
