from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from anki_export import quran_data as qd  # noqa: E402


class QuranDataContextTests(unittest.TestCase):
    def assert_context_segments(
        self, quarter_hizb_id: int, expected: list[tuple[int, int, int]]
    ) -> None:
        segments = qd.segments_for_qh_with_context(quarter_hizb_id)
        actual = [
            (segment.surah_number, segment.start_ayah, segment.end_ayah)
            for segment in segments
        ]
        self.assertEqual(actual, expected)
        self.assertEqual(
            [segment.seg_idx for segment in segments],
            list(range(len(segments))),
        )

    def test_target_segments_remain_unexpanded(self) -> None:
        self.assertEqual(
            qd.segments_for_qh(2),
            [qd.Segment(surah_number=2, start_ayah=26, end_ayah=43, seg_idx=0)],
        )

    def test_same_surah_context_and_hizb_transition(self) -> None:
        self.assert_context_segments(2, [(2, 25, 44)])
        self.assert_context_segments(4, [(2, 59, 75)])
        self.assert_context_segments(5, [(2, 74, 92)])

    def test_context_crosses_surah_boundaries(self) -> None:
        self.assert_context_segments(20, [(2, 282, 286), (3, 1, 15)])
        self.assert_context_segments(30, [(3, 185, 200), (4, 1, 1)])
        self.assert_context_segments(31, [(3, 200, 200), (4, 1, 12)])

    def test_first_qh_clips_at_start_of_quran(self) -> None:
        self.assert_context_segments(1, [(1, 1, 7), (2, 1, 26)])

    def test_last_qh_clips_at_end_of_quran(self) -> None:
        self.assertEqual(qd.SURAH_AYAH_COUNTS[106:], [7, 3, 6, 3, 5, 4, 5, 6])
        self.assertEqual(sum(qd.SURAH_AYAH_COUNTS), 6236)
        self.assert_context_segments(
            240,
            [
                (100, 8, 11),
                (101, 1, 11),
                (102, 1, 8),
                (103, 1, 3),
                (104, 1, 9),
                (105, 1, 5),
                (106, 1, 4),
                (107, 1, 7),
                (108, 1, 3),
                (109, 1, 6),
                (110, 1, 3),
                (111, 1, 5),
                (112, 1, 4),
                (113, 1, 5),
                (114, 1, 6),
            ],
        )

    def test_invalid_qh_ids_are_rejected(self) -> None:
        for quarter_hizb_id in (0, 241):
            with self.subTest(quarter_hizb_id=quarter_hizb_id):
                with self.assertRaises(ValueError):
                    qd.segments_for_qh_with_context(quarter_hizb_id)


class QuranAnkiPackageTests(unittest.TestCase):
    def test_generator_packages_context_under_existing_guid(self) -> None:
        try:
            import genanki  # noqa: F401
        except ImportError:
            self.skipTest("genanki is not installed")

        with tempfile.TemporaryDirectory() as temp_dir_name:
            temp_dir = Path(temp_dir_name)
            output = temp_dir / "qh2.apkg"
            subprocess.run(
                [
                    sys.executable,
                    "-B",
                    str(REPO_ROOT / "scripts" / "generate_anki_cards.py"),
                    "--qh-start",
                    "2",
                    "--qh-end",
                    "2",
                    "--no-audio",
                    "--out",
                    str(output),
                ],
                cwd=REPO_ROOT,
                check=True,
                capture_output=True,
                text=True,
            )

            collection_path = temp_dir / "collection.anki2"
            with zipfile.ZipFile(output) as package:
                collection_path.write_bytes(package.read("collection.anki2"))

            with sqlite3.connect(collection_path) as collection:
                rows = collection.execute("SELECT guid, flds FROM notes").fetchall()

            self.assertEqual(len(rows), 1)
            guid, raw_fields = rows[0]
            fields = raw_fields.split("\x1f")
            words = json.loads(fields[2])
            self.assertEqual(guid, "sSkVEfS8ui")
            self.assertEqual(fields[0], "2")
            self.assertEqual(len(words), 346)
            self.assertEqual({word["a"] for word in words}, set(range(25, 45)))
            self.assertEqual({word["s"] for word in words}, {0})


if __name__ == "__main__":
    unittest.main()
