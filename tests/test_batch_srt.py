from flashcards.batch_srt import clean_text, prepare_cues
from flashcards.subtitles import SubtitleCue


def test_batch_srt_cleans_directions_and_merges_continuations():
    cues = (
        SubtitleCue("1", 1000, 2000, "（槙島）《どこかの誰かが→"),
        SubtitleCue("2", 2000, 3000, "「愚かな人類ども」と 言ったとして→"),
        SubtitleCue("3", 3000, 4000, "その人類には 当然》"),
        SubtitleCue("4", 5000, 6000, "（足音）"),
        SubtitleCue("5", 7000, 8000, "♪♪～"),
    )
    prepared = prepare_cues(cues)
    assert len(prepared) == 1
    assert prepared[0].source_ids == ("1", "2", "3")
    assert prepared[0].text == "どこかの誰かが 「愚かな人類ども」と 言ったとして その人類には 当然"
    assert clean_text("（狡噛）くそっ！") == "くそっ！"
