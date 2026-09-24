from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from flashcards import (  # noqa: E402
    AudioSpan,
    concat_audio,
    materialize_audio_span,
    normalize_audio,
    probe_duration_ms,
    sha256_file,
    trim_audio,
)


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "ffmpeg is required")
class FlashcardMediaTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.source = self.root / "source.mp3"
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-loglevel",
                "error",
                "-f",
                "lavfi",
                "-i",
                "sine=frequency=440:duration=1.2",
                str(self.source),
            ],
            check=True,
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_normalize_probe_hash_and_trim(self) -> None:
        normalized = normalize_audio(self.source, self.root / "normalized.wav")
        self.assertTrue(normalized.is_file())
        self.assertGreater(probe_duration_ms(normalized), 1_100)
        self.assertRegex(sha256_file(normalized), r"^[0-9a-f]{64}$")

        clip = materialize_audio_span(
            normalized,
            self.root / "clip.mp3",
            AudioSpan("media", 300.25, 700.75, 100.5, 125.25),
        )
        self.assertTrue(clip.is_file())
        self.assertGreater(probe_duration_ms(clip), 600)
        self.assertLess(probe_duration_ms(clip), 750)

    def test_concat_audio_preserves_inputs(self) -> None:
        first = trim_audio(self.source, self.root / "first.mp3", 0, 300)
        second = trim_audio(self.source, self.root / "second.mp3", 300, 600)
        combined = concat_audio((first, second), self.root / "combined.mp3")
        self.assertTrue(first.is_file())
        self.assertTrue(second.is_file())
        self.assertGreater(probe_duration_ms(combined), 500)

    def test_invalid_clip_range_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "end_ms"):
            trim_audio(self.source, self.root / "clip.mp3", 500, 100)

    def test_single_concat_rejects_mismatched_container_extension(self) -> None:
        with self.assertRaisesRegex(ValueError, "matching input and output formats"):
            concat_audio((self.source,), self.root / "not-really-a-wav.wav")


if __name__ == "__main__":
    unittest.main()
