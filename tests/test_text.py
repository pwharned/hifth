from __future__ import annotations

from pathlib import Path

from flashcards.text import generate_text_cards, split_sentences


def test_split_sentences_preserves_order_across_scripts():
    result = split_sentences("Salve! Quid agis?\n\nτί πράττεις; καλῶς.")
    assert [item.position for item in result] == [1, 2, 3, 4]
    assert [item.text for item in result] == ["Salve!", "Quid agis?", "τί πράττεις;", "καλῶς."]


def test_unpunctuated_paragraph_is_retained():
    assert [item.text for item in split_sentences("BOOK ONE\n\nA sentence.")] == [
        "BOOK ONE", "A sentence."
    ]


class FakeTranslator:
    def translate(self, text, source, target):
        return f"translated:{text}"


class FakeSynthesizer:
    def synthesize(self, text, language, output: Path):
        output.write_bytes(text.encode())


class FakeAnki:
    def __init__(self):
        self.notes = {}
        self.next_id = 0

    def invoke(self, action, **params):
        if action == "modelNames":
            return [] if not self.notes else ["Ordered Sentence Audio (la)"]
        if action in {"createModel", "createDeck"}:
            return None
        if action == "storeMediaFile":
            return params["filename"]
        if action == "findNotes":
            sentence_id = params["query"].split("SentenceID:", 1)[1]
            return [note_id for note_id, note in self.notes.items() if note["SentenceID"] == sentence_id]
        if action == "addNote":
            self.next_id += 1
            self.notes[self.next_id] = params["note"]["fields"]
            return self.next_id
        if action == "updateNoteFields":
            self.notes[params["note"]["id"]].update(params["note"]["fields"])
            return None
        raise AssertionError(action)


def test_text_generation_is_ordered_and_idempotent(tmp_path):
    source = tmp_path / "book.txt"
    source.write_text("Prima. Secunda.", encoding="utf-8")
    anki = FakeAnki()
    options = dict(
        language="la", target_language="en", deck="Latin::Book",
        cache_path=tmp_path / "cache.json", delay=0,
        anki=anki, translator=FakeTranslator(), synthesizer=FakeSynthesizer(),
    )
    assert generate_text_cards(source, **options) == {"sentences": 2, "created": 2, "updated": 0}
    assert [note["Position"] for note in anki.notes.values()] == ["1", "2"]
    assert generate_text_cards(source, **options) == {"sentences": 2, "created": 0, "updated": 2}
    assert len(anki.notes) == 2
