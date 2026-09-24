from __future__ import annotations

import json
import sys
import unittest
from dataclasses import FrozenInstanceError
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from flashcards import (  # noqa: E402
    CURRENT_SCHEMA_VERSION,
    AudioSpan,
    BaseToken,
    CardDraft,
    ClozePolicy,
    LearningComponent,
    LearningUnit,
    LearningUnitKind,
    MediaSource,
    ProjectManifest,
    TextSpan,
    Utterance,
    build_cloze,
    stable_card_id,
)


class FlashcardModelTests(unittest.TestCase):
    def make_utterance(self) -> Utterance:
        text = "Tôi là học sinh."
        return Utterance(
            id="utt-1",
            media_id="media-1",
            text=text,
            start_ms=100,
            end_ms=1_400,
            tokens=(
                BaseToken("Tôi", TextSpan(0, 3), 150, 300, 0.99),
                BaseToken("là", TextSpan(4, 6), 350, 500, 0.98),
                BaseToken("học", TextSpan(7, 10), 550, 800, 0.97),
                BaseToken("sinh", TextSpan(11, 15), 820, 1_100, 0.96),
                BaseToken(".", TextSpan(15, 16), 1_100, 1_150),
            ),
            translation="I am a student.",
            provenance=("manual-alignment",),
        )

    def make_manifest(self) -> ProjectManifest:
        utterance = self.make_utterance()
        target_span = utterance.span_for_tokens(2, 4)
        unit = LearningUnit(
            id="unit-1",
            utterance_id=utterance.id,
            span=target_span,
            kind=LearningUnitKind.COMPOUND,
            token_start=2,
            token_end=4,
            contextual_gloss="student",
            components=(
                LearningComponent("học", "study", TextSpan(7, 10)),
                LearningComponent("sinh", "born/person", TextSpan(11, 15)),
            ),
            confidence=0.95,
            evidence=("dictionary", "context"),
        )
        card_id = stable_card_id(
            "media-1", utterance.id, target_span, ClozePolicy.EXPLICIT_SPAN
        )
        card = CardDraft(
            id=card_id,
            media_id="media-1",
            utterance_id=utterance.id,
            language="vi",
            text=utterance.text,
            policy=ClozePolicy.EXPLICIT_SPAN,
            target_span=target_span,
            target_gloss="student",
            sentence_translation="I am a student.",
            audio_span=AudioSpan("media-1", 550, 1_100),
            tags=("vietnamese", "noun"),
            provenance=("test",),
        )
        return ProjectManifest(
            id="project-1",
            title="Vietnamese foundations",
            language="vi",
            media=(
                MediaSource(
                    id="media-1",
                    path="audio/lesson-1.mp3",
                    language="vi",
                    title="Lesson 1",
                    source_url="https://example.test/lesson-1",
                    checksum_sha256="a" * 64,
                    duration_ms=2_000,
                ),
            ),
            utterances=(utterance,),
            learning_units=(unit,),
            cards=(card,),
        )

    def test_repeated_target_uses_exact_offset(self) -> None:
        text = "fish and fish"
        self.assertEqual(
            build_cloze(text, TextSpan(9, 13)),
            "fish and {{c1::fish}}",
        )
        self.assertEqual(
            build_cloze(text, TextSpan(0, 4), cloze_number=2),
            "{{c2::fish}} and fish",
        )

    def test_html_escaping_applies_to_every_segment_and_hint(self) -> None:
        text = '<p class="x">fish & chips</p>'
        self.assertEqual(
            build_cloze(
                text,
                TextSpan(13, 25),
                hint="<food>",
                escape_html=True,
            ),
            "&lt;p class=&quot;x&quot;&gt;{{c1::fish &amp; chips::&lt;food&gt;}}&lt;/p&gt;",
        )

    def test_anki_delimiters_in_source_text_cannot_create_extra_clozes(self) -> None:
        text = "literal {{c2::trap}} and key::value"
        result = build_cloze(text, TextSpan(25, 35), escape_html=True)
        self.assertEqual(result.count("{{c"), 1)
        self.assertIn("&#123;&#123;c2&#58;&#58;trap&#125;&#125;", result)
        self.assertIn("{{c1::key&#58;&#58;value}}", result)

    def test_vietnamese_multi_syllable_learning_unit(self) -> None:
        utterance = self.make_utterance()
        span = utterance.span_for_tokens(2, 4)
        unit = LearningUnit(
            "student",
            utterance.id,
            span,
            LearningUnitKind.COMPOUND,
            token_start=2,
            token_end=4,
            components=(LearningComponent("học"), LearningComponent("sinh")),
        )
        self.assertEqual(span.extract(utterance.text), "học sinh")
        self.assertEqual(unit.span, TextSpan(7, 15))

    def test_token_span_and_audio_time_derivation_with_fallback(self) -> None:
        utterance = Utterance(
            "utt",
            "media",
            "one two three",
            100,
            1_000,
            (
                BaseToken("one", TextSpan(0, 3), 150, 300),
                BaseToken("two", TextSpan(4, 7)),
                BaseToken("three", TextSpan(8, 13), 650, 900),
            ),
        )
        self.assertEqual(utterance.span_for_tokens(0, 2), TextSpan(0, 7))
        self.assertEqual(utterance.audio_times_for_tokens(0, 1), (150, 300))
        self.assertEqual(utterance.audio_times_for_tokens(0, 2), (150, 1_000))
        self.assertEqual(utterance.audio_times_for_tokens(1, 3), (100, 900))

    def test_fractional_millisecond_timings_are_supported(self) -> None:
        token = BaseToken("word", TextSpan(0, 4), 100.25, 250.75)
        utterance = Utterance("utt", "media", "word", 50.5, 300.125, (token,))
        source = MediaSource("media", "audio.wav", "en", duration_ms=500.5)
        card = CardDraft(
            "card",
            "media",
            "utt",
            "en",
            "word",
            ClozePolicy.EXPLICIT_SPAN,
            target_span=TextSpan(0, 4),
            audio_span=AudioSpan("media", 100.25, 250.75, 50.5, 75.25),
        )
        manifest = ProjectManifest("project", "Title", "en", (source,), (utterance,), (), (card,))
        self.assertEqual(ProjectManifest.from_json(manifest.to_json()), manifest)

    def test_stable_card_ids_are_deterministic_and_input_sensitive(self) -> None:
        span = TextSpan(7, 15)
        first = stable_card_id("media", "utt", span, ClozePolicy.EXPLICIT_SPAN)
        second = stable_card_id("media", "utt", span, "explicit_span")
        self.assertEqual(first, second)
        self.assertRegex(first, r"^[0-9a-f]{64}$")
        self.assertNotEqual(
            first,
            stable_card_id("media", "utt", TextSpan(7, 14), ClozePolicy.EXPLICIT_SPAN),
        )
        self.assertNotEqual(
            first,
            stable_card_id("media", "utt", span, ClozePolicy.PROGRESSIVE_MASK),
        )

    def test_card_text_methods_respect_policy(self) -> None:
        explicit = self.make_manifest().cards[0]
        self.assertEqual(explicit.target_text(), "học sinh")
        self.assertEqual(
            explicit.cloze_text(hint="student"),
            "Tôi là {{c1::học sinh::student}}.",
        )
        progressive = CardDraft(
            "progressive",
            "media-1",
            "utt-1",
            "vi",
            "<học sinh>",
            ClozePolicy.PROGRESSIVE_MASK,
        )
        self.assertEqual(progressive.target_text(), "<học sinh>")
        self.assertEqual(progressive.cloze_text(escape_html=True), "&lt;học sinh&gt;")

    def test_manifest_round_trip_is_lossless_and_deterministic(self) -> None:
        manifest = self.make_manifest()
        self.assertEqual(manifest.schema_version, CURRENT_SCHEMA_VERSION)
        self.assertEqual(ProjectManifest.from_dict(manifest.to_dict()), manifest)
        payload = manifest.to_json()
        self.assertEqual(payload, manifest.to_json())
        self.assertEqual(ProjectManifest.from_json(payload), manifest)
        self.assertEqual(json.loads(payload), manifest.to_dict())
        self.assertIn("học sinh", payload)

    def test_manifest_rejects_bad_references(self) -> None:
        utterance = self.make_utterance()
        with self.assertRaisesRegex(ValueError, "unknown media"):
            ProjectManifest(
                "project",
                "Title",
                "vi",
                (),
                (utterance,),
                (),
                (),
            )

        media = MediaSource("media-1", "audio.mp3", "vi", duration_ms=2_000)
        bad_unit = LearningUnit(
            "unit", "missing", TextSpan(0, 3), LearningUnitKind.WORD
        )
        with self.assertRaisesRegex(ValueError, "unknown utterance"):
            ProjectManifest("project", "Title", "vi", (media,), (utterance,), (bad_unit,), ())

    def test_manifest_rejects_span_and_token_range_disagreement(self) -> None:
        utterance = self.make_utterance()
        media = MediaSource("media-1", "audio.mp3", "vi", duration_ms=2_000)
        bad_unit = LearningUnit(
            "unit",
            utterance.id,
            TextSpan(7, 10),
            LearningUnitKind.COMPOUND,
            token_start=2,
            token_end=4,
        )
        with self.assertRaisesRegex(ValueError, "does not match its token range"):
            ProjectManifest(
                "project", "Title", "vi", (media,), (utterance,), (bad_unit,), ()
            )

    def test_unsupported_schema_versions_are_rejected(self) -> None:
        manifest = self.make_manifest()
        with self.assertRaisesRegex(ValueError, "unsupported schema version"):
            ProjectManifest(
                manifest.id,
                manifest.title,
                manifest.language,
                manifest.media,
                manifest.utterances,
                manifest.learning_units,
                manifest.cards,
                schema_version=2,
            )
        data = manifest.to_dict()
        data["schema_version"] = 2
        with self.assertRaisesRegex(ValueError, "unsupported schema version"):
            ProjectManifest.from_dict(data)
        with self.assertRaisesRegex(ValueError, "unsupported schema version"):
            ProjectManifest.from_json(json.dumps(data))

    def test_manifest_rejects_non_array_components(self) -> None:
        data = self.make_manifest().to_dict()
        data["learning_units"][0]["components"] = False
        with self.assertRaisesRegex(ValueError, "components must be an array"):
            ProjectManifest.from_dict(data)

    def test_validation_errors(self) -> None:
        with self.assertRaises(ValueError):
            TextSpan(2, 2)
        with self.assertRaises(ValueError):
            TextSpan(0, 4).extract("abc")
        with self.assertRaises(ValueError):
            BaseToken("word", TextSpan(0, 4), start_ms=10)
        with self.assertRaises(ValueError):
            BaseToken("word", TextSpan(0, 4), confidence=1.1)
        with self.assertRaises(ValueError):
            Utterance(
                "utt",
                "media",
                "one two",
                0,
                500,
                (BaseToken("wrong", TextSpan(0, 3)),),
            )
        with self.assertRaises(ValueError):
            Utterance(
                "utt",
                "media",
                "one two",
                0,
                500,
                (
                    BaseToken("two", TextSpan(4, 7)),
                    BaseToken("one", TextSpan(0, 3)),
                ),
            )
        with self.assertRaises(ValueError):
            LearningUnit(
                "unit",
                "utt",
                TextSpan(0, 3),
                LearningUnitKind.WORD,
                token_start=0,
            )
        with self.assertRaises(ValueError):
            CardDraft(
                "card", "media", "utt", "vi", "text", ClozePolicy.EXPLICIT_SPAN
            )
        with self.assertRaises(ValueError):
            AudioSpan("media", 500, 100)
        with self.assertRaises(ValueError):
            build_cloze("word", TextSpan(0, 4), cloze_number=0)

    def test_models_are_frozen_and_normalize_sequences_to_tuples(self) -> None:
        utterance = Utterance("utt", "media", "word", 0, 500, [BaseToken("word", TextSpan(0, 4))])
        self.assertIsInstance(utterance.tokens, tuple)
        with self.assertRaises(FrozenInstanceError):
            utterance.text = "changed"


if __name__ == "__main__":
    unittest.main()
