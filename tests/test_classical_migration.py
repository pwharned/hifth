"""Idempotency tests for the Anki-driven migration, using an in-memory AnkiConnect."""

import json
import re

import pytest

from scripts.classical_cloze import mms_audio, pipeline
from scripts.classical_cloze.pipeline import Candidate, Sentence, Token


class FakeAnki:
    def __init__(self):
        self.notes: dict[int, dict] = {}
        self.suspended: set[int] = set()
        self.decks: dict[int, str] = {}
        self.media: set[str] = set()
        self.next_id = 1000
        self.calls: list[str] = []

    def add(self, deck, model, fields, tags):
        self.next_id += 1
        self.notes[self.next_id] = {
            "noteId": self.next_id, "modelName": model, "tags": list(tags),
            "fields": {k: {"value": v} for k, v in fields.items()}, "cards": [self.next_id],
        }
        self.decks[self.next_id] = deck
        return self.next_id

    def in_deck(self, deck):
        return [i for i, d in self.decks.items() if d == deck or d.startswith(deck + "::")]

    def __call__(self, action, **p):
        self.calls.append(action)
        if action == "multi":
            return [{"result": self(a["action"], **a["params"]), "error": None} for a in p["actions"]]
        if action == "findNotes":
            query = p["query"]
            deck = re.search(r'deck:"([^"]+)"', query).group(1)
            ids = self.in_deck(deck)
            if "note:Basic" in query:
                ids = [i for i in ids if self.notes[i]["modelName"] == "Basic"]
            model = re.search(r'note:"([^"]+)"', query)
            if model:
                ids = [i for i in ids if self.notes[i]["modelName"] == model.group(1)]
            return ids
        if action == "notesInfo":
            return [self.notes[i] for i in p["notes"]]
        if action == "findCards":
            if p["query"].startswith("nid:"):
                return [int(p["query"][4:])]
            return self("findNotes", **p)
        if action == "cardsInfo":
            return [{"cardId": c, "note": c, "queue": -1 if c in self.suspended else 0} for c in p["cards"]]
        if action == "addNotes":
            return [self.add(n["deckName"], n["modelName"], n["fields"], n["tags"]) for n in p["notes"]]
        if action == "suspend":
            self.suspended |= set(p["cards"])
        elif action == "unsuspend":
            self.suspended -= set(p["cards"])
        elif action == "addTags":
            for i in p["notes"]:
                self.notes[i]["tags"] += [t for t in p["tags"].split() if t not in self.notes[i]["tags"]]
        elif action == "changeDeck":
            for c in p["cards"]:
                self.decks[c] = p["deck"]
        elif action == "deleteNotes":
            for i in p["notes"]:
                self.notes.pop(i)
                self.decks.pop(i)
        elif action == "updateNoteFields":
            for k, v in p["note"]["fields"].items():
                self.notes[p["note"]["id"]]["fields"][k]["value"] = v
        elif action == "getMediaFilesNames":
            return sorted(self.media)
        elif action == "storeMediaFile":
            self.media.add(p["filename"])
            return p["filename"]
        elif action == "modelNames":
            return [pipeline.MODEL_NAME]
        elif action in {"createDeck", "modelFieldNames", "updateModelTemplates", "updateModelStyling"}:
            return ["Gloss"] if action == "modelFieldNames" else None
        else:
            raise AssertionError(f"unexpected AnkiConnect action {action}")
        return None


@pytest.fixture
def anki(monkeypatch):
    fake = FakeAnki()
    for module in (pipeline, mms_audio):
        monkeypatch.setattr(module, "anki", fake)

    class FakeSynthesizer:
        rendered: list[str] = []

        def render(self, language, text, tempo, output):
            FakeSynthesizer.rendered.append(text)
            output.write_bytes(text.encode())

        def close(self):
            pass

    FakeSynthesizer.rendered = []
    monkeypatch.setattr(mms_audio, "Synthesizer", FakeSynthesizer)
    fake.synth = FakeSynthesizer
    return fake


def sentence(words):
    return Sentence(
        "latin", "Livy", "Ab Urbe Condita", "classical", "test", "doc", "1", " ".join(words),
        tuple(Token(w, w, "NOUN", {}) for w in words),
    )


def test_audio_is_a_pure_function_of_the_displayed_text(anki):
    note = anki.add(
        pipeline.DESTINATION_DECK, pipeline.MODEL_NAME,
        {"Text": "sub {{c1::titulo}} prima[sound:wrong_clip_of_another_sentence.wav]"},
        ["language::latin"],
    )
    assert mms_audio.sync_audio()["updated"] == 1
    field = anki.notes[note]["fields"]["Text"]["value"]
    expected = mms_audio.audio_filename("latin", 0.8, "sub titulo prima")
    assert field == f"sub {{{{c1::titulo}}}} prima[sound:{expected}]"
    assert anki.synth.rendered == ["sub titulo prima"]
    # second run: nothing to do, nothing synthesized
    assert mms_audio.sync_audio() == {"notes": 1, "updated": 0, "synthesized": 0}
    # editing the text regenerates only that note's clip
    anki.notes[note]["fields"]["Text"]["value"] = field.replace("prima", "secunda")
    assert mms_audio.sync_audio()["synthesized"] == 1
    assert anki.synth.rendered[-1] == "sub titulo secunda"


def test_default_tempo_is_80_percent():
    assert mms_audio.DEFAULT_TEMPO == 0.8
    assert "_t80_" in mms_audio.audio_filename("greek", 0.8, "λόγος")


def test_migrate_is_idempotent(anki, monkeypatch, tmp_path):
    original = anki.add("Greek and Latin", "Basic", {"Front": "titulus", "Back": "inscription"}, ["Latin"])
    anki.add("Greek and Latin", "Basic", {"Front": "xyzzy", "Back": "nothing"}, ["Latin"])
    candidate = Candidate(sentence(["haud", "titulo", "prima"]), 1, 2, "lemma")
    monkeypatch.setattr(pipeline, "load_corpus", lambda root: [])
    monkeypatch.setattr(pipeline, "build_indexes", lambda sentences: ({}, {}))
    monkeypatch.setattr(
        pipeline, "find_candidates",
        lambda front, language, lemma, form: ([candidate], None) if front == "titulus" else ([], "unmatched"),
    )
    monkeypatch.setattr(pipeline, "lookup", lambda *args: None)
    archive = tmp_path / "archive.json"

    first = pipeline.migrate(tmp_path, archive)
    assert first["created"] == 1 and first["moved_to_unmatched"] == 1 and first["originals_retired"] == 1
    assert original not in anki.notes
    replacement = anki.in_deck(pipeline.DESTINATION_DECK)[0]
    text = anki.notes[replacement]["fields"]["Text"]["value"]
    assert text.startswith("haud {{c1::titulo}} prima[sound:classical_cloze_mms_latin_t80_")
    assert anki.notes[replacement]["fields"]["OriginalNoteID"]["value"] == str(original)
    assert json.loads(archive.read_text())["notes"][0]["noteId"] == original

    snapshot = json.dumps(anki.notes, sort_keys=True), dict(anki.decks), set(anki.suspended), set(anki.media)
    second = pipeline.migrate(tmp_path, archive)
    assert second["created"] == 0 and second["originals_retired"] == 0 and second["audio_updated"] == 0
    assert second["moved_to_unmatched"] == 0
    assert (json.dumps(anki.notes, sort_keys=True), dict(anki.decks), set(anki.suspended), set(anki.media)) == snapshot
