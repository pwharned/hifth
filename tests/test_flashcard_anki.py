from __future__ import annotations

import json
import hashlib
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from flashcards import (  # noqa: E402
    CardDraft,
    ClozePolicy,
    PreparedCard,
    ProjectManifest,
    MediaSource,
    Utterance,
    TextSpan,
    export_apkg,
    render_fields,
)
from flashcards.export import export_manifest_apkg  # noqa: E402


class FlashcardAnkiTests(unittest.TestCase):
    def make_card(self) -> CardDraft:
        text = "Tôi là học sinh & thích học."
        return CardDraft(
            id="a" * 64,
            media_id="media",
            utterance_id="utterance",
            language="vi",
            text=text,
            policy=ClozePolicy.EXPLICIT_SPAN,
            target_span=TextSpan(7, 15),
            target_gloss="student",
            sentence_translation="I am a student & like studying.",
            analysis="Lexical compound",
            source_title="Example <video>",
            source_url="https://example.test/watch?v=1&lang=vi",
            tags=("Vietnamese media",),
        )

    def test_render_fields_are_offset_safe_and_html_escaped(self) -> None:
        fields = render_fields(self.make_card(), audio_filename="clip.mp3")
        self.assertEqual(
            fields["Text"],
            "Tôi là {{c1::học sinh}} &amp; thích học.",
        )
        self.assertEqual(fields["Audio"], "[sound:clip.mp3]")
        self.assertIn("Example &lt;video&gt;", fields["Source"])
        self.assertIn("&amp;lang=vi", fields["Source"])

    def test_unsafe_audio_filename_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "safe filename"):
            render_fields(self.make_card(), audio_filename="../clip.mp3")

    def test_apkg_contains_deterministically_named_audio(self) -> None:
        try:
            import genanki  # noqa: F401
        except ImportError:
            self.skipTest("genanki is not installed")
        with tempfile.TemporaryDirectory() as temp_dir_name:
            temp_dir = Path(temp_dir_name)
            audio = temp_dir / "input.mp3"
            audio.write_bytes(b"test audio bytes")
            output = export_apkg(
                (PreparedCard(self.make_card(), audio),),
                temp_dir / "deck.apkg",
                deck_name="Vietnamese::Media",
            )
            self.assertTrue(output.is_file())
            with zipfile.ZipFile(output) as package:
                media = json.loads(package.read("media"))
                digest = hashlib.sha256(b"test audio bytes").hexdigest()
                self.assertIn(f"flashcard_audio_{digest[:24]}.mp3", media.values())

    def test_apkg_deduplicates_identical_audio(self) -> None:
        try:
            import genanki  # noqa: F401
        except ImportError:
            self.skipTest("genanki is not installed")
        with tempfile.TemporaryDirectory() as temp_dir_name:
            temp_dir = Path(temp_dir_name)
            audio = temp_dir / "input.mp3"
            audio.write_bytes(b"shared audio")
            first = self.make_card()
            second = CardDraft(
                id="b" * 64,
                media_id=first.media_id,
                utterance_id=first.utterance_id,
                language=first.language,
                text=first.text,
                policy=first.policy,
                target_span=TextSpan(24, 27),
            )
            output = export_apkg(
                (PreparedCard(first, audio), PreparedCard(second, audio)),
                temp_dir / "deck.apkg",
            )
            with zipfile.ZipFile(output) as package:
                media = json.loads(package.read("media"))
                self.assertEqual(len(media), 1)

    def test_manifest_can_be_exported_without_audio(self) -> None:
        try:
            import genanki  # noqa: F401
        except ImportError:
            self.skipTest("genanki is not installed")
        card = self.make_card()
        manifest = ProjectManifest(
            "project",
            "Vietnamese::Media",
            "vi",
            (MediaSource("media", "movie.mp4", "vi"),),
            (Utterance("utterance", "media", card.text, 0, 1_000, ()),),
            (),
            (card,),
        )
        with tempfile.TemporaryDirectory() as temp_dir_name:
            output = export_manifest_apkg(manifest, Path(temp_dir_name) / "deck.apkg")
            self.assertTrue(output.is_file())


if __name__ == "__main__":
    unittest.main()
