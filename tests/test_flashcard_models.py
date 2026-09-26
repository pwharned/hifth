from __future__ import annotations

import json
import math
import sys
import unittest
from dataclasses import FrozenInstanceError, fields
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import flashcards  # noqa: E402
from flashcards import (  # noqa: E402
    CURRENT_SCHEMA_VERSION,
    MEDIA_ARTIFACT_TYPE,
    AudioSpan,
    BaseToken,
    MediaArtifact,
    MediaSource,
    TextSpan,
    Utterance,
)


class FlashcardModelTests(unittest.TestCase):
    def make_artifact(self) -> MediaArtifact:
        text = "Tôi là học sinh."
        utterance = Utterance(
            id="utterance-1",
            media_id="media-1",
            text=text,
            start_ms=100.25,
            end_ms=1_500.75,
            tokens=[
                BaseToken("Tôi", TextSpan(0, 3), 100.25, 300.5, 0.99),
                BaseToken("là", TextSpan(4, 6), 320.25, 500.5, 0.98),
                BaseToken("học", TextSpan(7, 10)),
                BaseToken("sinh", TextSpan(11, 15), 810.25, 1_100.5, 0.96),
                BaseToken(".", TextSpan(15, 16)),
            ],
            translation="I am a student.",
            confidence=0.975,
            provenance=["fixture"],
        )
        source = MediaSource(
            id="media-1",
            path="lesson.mp3",
            language="vi",
            title="Lesson",
            source_url="https://example.test/lesson",
            checksum_sha256="a" * 64,
            duration_ms=2_000.5,
        )
        return MediaArtifact(
            id="artifact-1",
            title="Vietnamese lesson",
            language="vi",
            media=[source],
            utterances=[utterance],
        )

    def test_artifact_roundtrip_is_deterministic_and_stateless(self) -> None:
        artifact = self.make_artifact()
        expected_keys = {
            "artifact_type",
            "schema_version",
            "id",
            "title",
            "language",
            "media",
            "utterances",
        }
        data = artifact.to_dict()

        self.assertEqual({field.name for field in fields(MediaArtifact)}, expected_keys)
        self.assertEqual(set(data), expected_keys)
        self.assertEqual(artifact.artifact_type, MEDIA_ARTIFACT_TYPE)
        self.assertEqual(artifact.schema_version, CURRENT_SCHEMA_VERSION)
        self.assertEqual(MediaArtifact.from_dict(data), artifact)
        payload = artifact.to_json()
        self.assertEqual(payload, artifact.to_json())
        self.assertEqual(json.loads(payload), data)
        self.assertEqual(MediaArtifact.from_json(payload), artifact)
        self.assertNotIn("cards", data)
        self.assertNotIn("learning_units", data)

    def test_artifact_deserialization_requires_exact_keys(self) -> None:
        for extra in ("cards", "learning_units", "review_state"):
            data = self.make_artifact().to_dict()
            data[extra] = []
            with self.subTest(extra=extra), self.assertRaisesRegex(
                ValueError, rf"unsupported field.*{extra}"
            ):
                MediaArtifact.from_dict(data)

        missing = self.make_artifact().to_dict()
        del missing["title"]
        with self.assertRaisesRegex(ValueError, "missing media artifact.title"):
            MediaArtifact.from_dict(missing)

        nested = self.make_artifact().to_dict()
        nested["utterances"][0]["analysis"] = "stateful"
        with self.assertRaisesRegex(ValueError, "unsupported field.*analysis"):
            MediaArtifact.from_dict(nested)

    def test_exact_codepoint_spans_and_timing_fallback(self) -> None:
        self.assertEqual(TextSpan(1, 2).extract("a😀b"), "😀")
        text = "😀 one two three"
        utterance = Utterance(
            "utterance",
            "media",
            text,
            100.125,
            1_000.875,
            (
                BaseToken("😀", TextSpan(0, 1)),
                BaseToken("one", TextSpan(2, 5), 150.25, 300.5),
                BaseToken("two", TextSpan(6, 9)),
                BaseToken("three", TextSpan(10, 15), 650.75, 900.5),
            ),
        )

        span = utterance.span_for_tokens(1, 3)
        self.assertEqual(span, TextSpan(2, 9))
        self.assertEqual(span.extract(text), "one two")
        self.assertEqual(utterance.audio_times_for_tokens(1, 2), (150.25, 300.5))
        self.assertEqual(utterance.audio_times_for_tokens(1, 3), (150.25, 1_000.875))
        self.assertEqual(utterance.audio_times_for_tokens(2, 4), (100.125, 900.5))

    def test_token_and_timing_validation(self) -> None:
        with self.assertRaises(ValueError):
            TextSpan(2, 2)
        with self.assertRaises(ValueError):
            TextSpan(0, 4).extract("abc")
        with self.assertRaises(ValueError):
            BaseToken("word", TextSpan(0, 4), start_ms=10)
        with self.assertRaises(ValueError):
            BaseToken("word", TextSpan(0, 4), confidence=math.nan)
        with self.assertRaisesRegex(ValueError, "does not match"):
            Utterance(
                "utterance",
                "media",
                "one two",
                0,
                500,
                (BaseToken("wrong", TextSpan(0, 3)),),
            )
        with self.assertRaisesRegex(ValueError, "ordered and non-overlapping"):
            Utterance(
                "utterance",
                "media",
                "one two",
                0,
                500,
                (
                    BaseToken("one", TextSpan(0, 3)),
                    BaseToken("e t", TextSpan(2, 5)),
                ),
            )
        for invalid_end in (math.inf, math.nan):
            with self.subTest(end_ms=invalid_end), self.assertRaises(ValueError):
                Utterance("utterance", "media", "word", 0, invalid_end, ())
        with self.assertRaises(ValueError):
            AudioSpan("media", 500, 100)
        with self.assertRaises(ValueError):
            AudioSpan("media", 100, 200, padding_before_ms=math.inf)

    def test_checksum_references_and_duration_are_validated(self) -> None:
        with self.assertRaisesRegex(ValueError, "64 hexadecimal"):
            MediaSource("media", "lesson.mp3", "en", checksum_sha256="bad")
        with self.assertRaisesRegex(ValueError, "greater than 0"):
            MediaSource("media", "lesson.mp3", "en", duration_ms=0)

        utterance = Utterance(
            "utterance", "media", "word", 0.25, 500.5, (BaseToken("word", TextSpan(0, 4)),)
        )
        with self.assertRaisesRegex(ValueError, "unknown media"):
            MediaArtifact(
                "artifact",
                "Title",
                "en",
                (MediaSource("other", "lesson.mp3", "en"),),
                (utterance,),
            )
        with self.assertRaisesRegex(ValueError, "exceeds its media duration"):
            MediaArtifact(
                "artifact",
                "Title",
                "en",
                (MediaSource("media", "lesson.mp3", "en", duration_ms=500.25),),
                (utterance,),
            )
        with self.assertRaisesRegex(ValueError, "duplicate media id"):
            MediaArtifact(
                "artifact",
                "Title",
                "en",
                (
                    MediaSource("media", "one.mp3", "en"),
                    MediaSource("media", "two.mp3", "en"),
                ),
                (),
            )

    def test_fractional_values_roundtrip(self) -> None:
        artifact = self.make_artifact()
        restored = MediaArtifact.from_json(artifact.to_json())
        self.assertEqual(restored.media[0].duration_ms, 2_000.5)
        self.assertEqual(restored.utterances[0].start_ms, 100.25)
        self.assertEqual(restored.utterances[0].tokens[0].end_ms, 300.5)
        self.assertEqual(
            AudioSpan("media", 100.25, 250.75, 50.5, 75.25).padding_after_ms,
            75.25,
        )

    def test_models_are_frozen_and_sequences_normalize_to_tuples(self) -> None:
        artifact = self.make_artifact()
        self.assertIsInstance(artifact.media, tuple)
        self.assertIsInstance(artifact.utterances, tuple)
        self.assertIsInstance(artifact.utterances[0].tokens, tuple)
        self.assertIsInstance(artifact.utterances[0].provenance, tuple)
        with self.assertRaises(FrozenInstanceError):
            artifact.title = "changed"
        with self.assertRaises(FrozenInstanceError):
            artifact.utterances[0].text = "changed"

    def test_obsolete_python_symbols_are_not_exported(self) -> None:
        for name in (
            "ProjectManifest",
            "CardDraft",
            "ClozePolicy",
            "LearningComponent",
            "LearningUnit",
            "LearningUnitKind",
            "build_cloze",
            "stable_card_id",
        ):
            with self.subTest(name=name):
                self.assertFalse(hasattr(flashcards, name))


if __name__ == "__main__":
    unittest.main()
