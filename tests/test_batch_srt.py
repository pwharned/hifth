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


def test_language_code_validation_and_model_names():
    import pytest
    from flashcards.batch_srt import language_code, model_name

    assert language_code("vi") == "vi"
    assert language_code("zh-CN") == "zh-CN"
    for bad in ("", "english", "ja&tl=fr", "j", "ja "):
        with pytest.raises(ValueError):
            language_code(bad)
    assert model_name("ja") == "SRT Sentence Audio"
    assert model_name("vi") == "SRT Sentence Audio (vi)"


def test_generate_rejects_cache_for_other_language(tmp_path):
    import json
    import pytest
    from flashcards import batch_srt

    srt = tmp_path / "a.srt"
    srt.write_text("1\n00:00:01,000 --> 00:00:02,000\nXin chào\n", encoding="utf-8")
    import hashlib
    cache = tmp_path / "cache.json"
    cache.write_text(json.dumps({
        "schema_version": 1, "source_sha256": hashlib.sha256(srt.read_bytes()).hexdigest(),
        "lang": "ja", "translate_to": "en", "cues": {},
    }))
    with pytest.raises(RuntimeError, match="different language"):
        batch_srt.generate(srt, "Deck", cache, 0, lang="vi")
