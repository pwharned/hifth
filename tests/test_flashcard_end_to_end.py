from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from flashcards.review import ReviewProject  # noqa: E402
from flashcards.subtitles import manifest_from_subtitles  # noqa: E402


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "ffmpeg is required")
class FlashcardEndToEndTests(unittest.TestCase):
    def test_subtitle_selection_source_audio_and_apkg_export(self) -> None:
        try:
            import genanki  # noqa: F401
        except ImportError:
            self.skipTest("genanki is not installed")

        with tempfile.TemporaryDirectory() as temp_dir_name:
            root = Path(temp_dir_name)
            media = root / "lesson.mp4"
            subtitles = root / "lesson.vi.srt"
            manifest_path = root / "lesson.json"
            subprocess.run(
                [
                    "ffmpeg",
                    "-y",
                    "-loglevel",
                    "error",
                    "-f",
                    "lavfi",
                    "-i",
                    "color=c=black:s=160x90:d=1.2",
                    "-f",
                    "lavfi",
                    "-i",
                    "sine=frequency=440:duration=1.2",
                    "-shortest",
                    "-c:v",
                    "mpeg4",
                    "-c:a",
                    "aac",
                    str(media),
                ],
                check=True,
            )
            subtitles.write_text(
                "1\n00:00:00,100 --> 00:00:01,000\nTôi là học sinh.\n",
                encoding="utf-8",
            )
            manifest = manifest_from_subtitles(
                media,
                subtitles,
                language="vi",
                title="Vietnamese lesson",
                stored_media_path=media.name,
            )
            manifest_path.write_text(manifest.to_json(indent=2), encoding="utf-8")

            reviewer = ReviewProject(manifest_path)
            card = reviewer.upsert_card(
                {
                    "utterance_id": manifest.utterances[0].id,
                    "token_start": 2,
                    "token_end": 4,
                    "kind": "compound",
                    "target_gloss": "student",
                    "sentence_translation": "I am a student.",
                    "tags": ["vietnamese"],
                }
            )
            self.assertEqual(card.target_text(), "học sinh")
            output = reviewer.export()
            self.assertTrue(output.is_file())
            with zipfile.ZipFile(output) as package:
                self.assertIn("collection.anki2", package.namelist())
                self.assertGreater(len(package.read("media")), 2)


if __name__ == "__main__":
    unittest.main()
