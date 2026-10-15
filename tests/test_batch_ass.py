import pytest

from flashcards.batch_ass import parse_ass
from flashcards.batch_srt import prepare_cues

ASS = """[Script Info]
Title: test

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
Dialogue: 0,0:00:08.83,0:00:19.77,Default,,0,0,0,,{\\an8\\i1}English lyric{\\i}
Dialogue: 0,0:00:12.00,0:00:14.00,JP-ja,NTP,0,0,0,,（狡噛）二番目,と　カンマ
Dialogue: 0,0:00:10.00,0:00:11.00,JP-ja,NTP,0,0,0,,{\\an8}一番目\\N改行
Dialogue: 0,0:00:15.00,0:00:15.00,JP-ja,NTP,0,0,0,,zero length
"""


def test_parse_ass_keeps_only_requested_style_in_time_order():
    cues = parse_ass(ASS)
    assert [cue.text for cue in cues] == ["一番目 改行", "（狡噛）二番目,と カンマ"]
    assert [cue.id for cue in cues] == ["1", "2"]
    assert cues[0].start_ms == 10000
    assert [cue.text for cue in prepare_cues(cues)] == ["一番目 改行", "二番目,と カンマ"]


def test_parse_ass_rejects_missing_style():
    with pytest.raises(ValueError):
        parse_ass(ASS, frozenset({"nope"}))


def test_parse_ass_error_lists_available_styles():
    with pytest.raises(ValueError, match="JP-ja"):
        parse_ass(ASS, frozenset({"nope"}))
