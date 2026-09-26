from __future__ import annotations

import sys
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from flashcards.subtitles import (  # noqa: E402
    artifact_from_subtitles,
    parse_subtitles,
    tokenize_base_units,
)


class SubtitleTests(unittest.TestCase):
    def test_srt_parsing_and_markup_cleanup(self) -> None:
        cues = parse_subtitles(
            """1
00:00:01,250 --> 00:00:03,500
<i>Tôi là học sinh.</i>

2
00:00:04,000 --> 00:00:05,250
Tôi thích &amp; học tiếng Việt.
"""
        )
        self.assertEqual(len(cues), 2)
        self.assertEqual(cues[0].text, "Tôi là học sinh.")
        self.assertEqual((cues[0].start_ms, cues[0].end_ms), (1_250, 3_500))
        self.assertEqual(cues[1].text, "Tôi thích & học tiếng Việt.")

    def test_unterminated_html_entities_and_angle_brackets_are_preserved(self) -> None:
        cues = parse_subtitles(
            "1\n00:00:00,000 --> 00:00:01,000\nAT&T says 2 < 3\n"
        )
        self.assertEqual(cues[0].text, "AT&T says 2 < 3")

    def test_entities_are_decoded_once_and_break_tags_add_space(self) -> None:
        cues = parse_subtitles(
            "1\n00:00:00,000 --> 00:00:01,000\n&amp;lt;one&amp;gt;<br>two\n"
        )
        self.assertEqual(cues[0].text, "&lt;one&gt; two")

    def test_webvtt_cue_identifiers_and_settings(self) -> None:
        cues = parse_subtitles(
            """WEBVTT

X-TIMESTAMP-MAP=LOCAL:00:00:00.000,MPEGTS:900000

intro
00:01.000 --> 00:03.250 align:start position:10%
Xin <00:02.000>chào!
"""
        )
        self.assertEqual(cues[0].id, "intro")
        self.assertEqual((cues[0].start_ms, cues[0].end_ms), (1_000, 3_250))
        self.assertEqual(cues[0].text, "Xin chào!")

    def test_vietnamese_tokens_keep_exact_source_offsets(self) -> None:
        text = "Tôi là học sinh."
        tokens = tokenize_base_units(text)
        self.assertEqual([token.text for token in tokens], ["Tôi", "là", "học", "sinh", "."])
        self.assertEqual(tokens[2].span.extract(text), "học")
        self.assertEqual(text[tokens[2].span.start_char : tokens[3].span.end_char], "học sinh")

    def test_combining_marks_remain_part_of_the_base_token(self) -> None:
        decomposed_vietnamese = "To\u0302i"
        arabic_with_mark = "بِسْمِ"
        self.assertEqual(
            [token.text for token in tokenize_base_units(decomposed_vietnamese)],
            [decomposed_vietnamese],
        )
        self.assertEqual(
            [token.text for token in tokenize_base_units(arabic_with_mark)],
            [arabic_with_mark],
        )

    def test_no_space_scripts_use_character_sized_base_units(self) -> None:
        chinese = tokenize_base_units("我是学生。")
        self.assertEqual([token.text for token in chinese], ["我", "是", "学", "生", "。"])
        self.assertEqual(
            chinese[2].span.extract("我是学生。") + chinese[3].span.extract("我是学生。"),
            "学生",
        )
        self.assertEqual(
            [token.text for token in tokenize_base_units("ﾆﾎﾝｺﾞ")],
            ["ﾆ", "ﾎ", "ﾝ", "ｺ", "ﾞ"],
        )

    def test_bad_timing_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "later"):
            parse_subtitles("1\n00:00:02,000 --> 00:00:01,000\nBad\n")

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "ffmpeg is required")
    def test_media_manifest_import_clamps_subtitle_end(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir_name:
            root = Path(temp_dir_name)
            media = root / "lesson.mp3"
            subtitles = root / "lesson.vi.srt"
            subprocess.run(
                [
                    "ffmpeg",
                    "-y",
                    "-loglevel",
                    "error",
                    "-f",
                    "lavfi",
                    "-i",
                    "sine=frequency=440:duration=1",
                    str(media),
                ],
                check=True,
            )
            subtitles.write_text(
                "1\n00:00:00,100 --> 00:00:01,500\nTôi là học sinh.\n",
                encoding="utf-8",
            )
            manifest = artifact_from_subtitles(media, subtitles, language="vi")
            self.assertEqual(len(manifest.utterances), 1)
            self.assertLessEqual(manifest.utterances[0].end_ms, manifest.media[0].duration_ms)
            self.assertEqual(manifest.utterances[0].tokens[2].text, "học")


if __name__ == "__main__":
    unittest.main()
