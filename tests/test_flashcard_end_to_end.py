from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from flashcards import MediaArtifact  # noqa: E402
from flashcards.subtitles import artifact_from_subtitles  # noqa: E402


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "ffmpeg is required")
class FlashcardEndToEndTests(unittest.TestCase):
    def test_subtitle_media_artifact_is_stateless_and_roundtrips(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir_name:
            root = Path(temp_dir_name)
            media = root / "lesson.mp4"
            subtitles = root / "lesson.vi.srt"
            artifact_path = root / "lesson.json"
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
            artifact = artifact_from_subtitles(
                media,
                subtitles,
                language="vi",
                title="Vietnamese lesson",
                stored_media_path=media.name,
            )
            payload = artifact.to_json(indent=2)
            artifact_path.write_text(payload, encoding="utf-8")

            restored = MediaArtifact.from_json(artifact_path.read_text(encoding="utf-8"))
            self.assertEqual(restored, artifact)
            self.assertEqual(restored.utterances[0].tokens[2].text, "học")
            self.assertNotIn('"cards"', payload)
            self.assertNotIn('"learning_units"', payload)


if __name__ == "__main__":
    unittest.main()
