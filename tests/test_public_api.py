from pathlib import Path

from flashcards import CardDraft, DraftBatch, FlashcardApp
from flashcards.adapters.anki import AnkiConnect
from flashcards.recipes import ClassicalMigration, SubtitleBatch


class FakePublisher:
    def __init__(self, failing: set[str] = set()):
        self.failing = failing
        self.ids = []

    def publish(self, card):
        if card.id in self.failing:
            raise RuntimeError("failed")
        self.ids.append(card.id)
        return len(self.ids)


def draft(identity="one"):
    return CardDraft(identity, {"Front": "hello"}, "Basic", "Test")


def test_public_app_publishes_a_batch_and_reports_individual_failures():
    publisher = FakePublisher({"two"})
    report = FlashcardApp().publish(DraftBatch((draft(), draft("two"))), publisher)
    assert report.created_or_updated == 1
    assert report.failed == 1
    assert report.errors == ("two: failed",)


def test_classical_recipe_exposes_repeatable_audio_repair(monkeypatch):
    monkeypatch.setattr(
        "scripts.classical_cloze.mms_audio.sync_audio",
        lambda tempo: {"notes": 1, "updated": int(tempo == 0.9), "synthesized": 1},
    )
    assert ClassicalMigration(tempo=0.9).repair_audio()["updated"] == 1


def test_anki_connect_is_the_shared_transport(monkeypatch):
    class Response:
        def __enter__(self): return self
        def __exit__(self, *args): pass

    response = Response()
    monkeypatch.setattr("urllib.request.urlopen", lambda *args, **kwargs: response)
    monkeypatch.setattr("json.load", lambda value: {"result": [1], "error": None})
    assert AnkiConnect().invoke("findNotes", query="deck:Test") == [1]


def test_subtitle_recipe_selects_parser_and_exposes_batch_api(monkeypatch, tmp_path):
    source = tmp_path / "lesson.srt"
    source.write_text("unused")
    monkeypatch.setattr(
        "flashcards.batch_srt.generate", lambda *args, **kwargs: {"cards": 3}
    )
    assert SubtitleBatch(source, "Test", "ja").apply() == {"cards": 3}
