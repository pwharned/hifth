from __future__ import annotations

import html
import json
import re
import tempfile
import unicodedata
import urllib.parse
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from scripts.classical_cloze.glaux_discovery import search as glaux_search
from scripts.classical_cloze.glaux_discovery import sentence_details
from scripts.classical_cloze.glaux_discovery import request_json as glaux_request_json
from scripts.classical_cloze.mms_audio import generate as generate_audio
from scripts.classical_cloze.pipeline import (
    DESTINATION_DECK,
    MODEL_NAME,
    UNMATCHED_DECK,
    anki,
    ensure_model,
    is_greek,
    strip_html,
)
from scripts.classical_cloze.tesserae_discovery import ERA_PRIORITY
from scripts.classical_cloze.tesserae_discovery import search as tesserae_search


WORD_RE = re.compile(r"[^\W\d_]+(?:[’'][^\W\d_]+)?", re.UNICODE)
PUNCTUATION = {",", ".", ";", ":", "?", "!", "·", "·"}


@dataclass(frozen=True)
class Candidate:
    id: str
    language: str
    text: str
    cloze: str
    target: str
    lemma: str
    translation: str
    author: str
    work: str
    citation: str
    citation_html: str
    corpus: str
    search_note: str = ""

    def to_json(self) -> dict[str, str]:
        return self.__dict__


def language_for(note: dict[str, Any]) -> str:
    front = strip_html(note["fields"]["Front"]["value"])
    return "greek" if "Greek" in note["tags"] or is_greek(front) else "latin"


def replace_word(text: str, index: int) -> tuple[str, str]:
    matches = list(WORD_RE.finditer(text))
    if not 0 <= index < len(matches):
        raise ValueError("matched word is outside the sentence")
    match = matches[index]
    return text[:match.start()] + "{{c1::" + match.group() + "}}" + text[match.end():], match.group()


def normalize_greek(value: str) -> str:
    decomposed = unicodedata.normalize("NFD", value).casefold()
    return "".join(
        character for character in decomposed
        if unicodedata.category(character) != "Mn"
    ).replace("ς", "σ")


def edit_distance(left: str, right: str) -> int:
    previous = list(range(len(right) + 1))
    for left_index, left_character in enumerate(left, 1):
        current = [left_index]
        for right_index, right_character in enumerate(right, 1):
            current.append(min(
                current[-1] + 1,
                previous[right_index] + 1,
                previous[right_index - 1] + (left_character != right_character),
            ))
        previous = current
    return previous[-1]


def shared_prefix(left: str, right: str) -> int:
    return next(
        (index for index, pair in enumerate(zip(left, right)) if pair[0] != pair[1]),
        min(len(left), len(right)),
    )


def safe_lemma_suggestions(target: str, suggestions: list[str]) -> list[str]:
    wanted = normalize_greek(target)
    ranked = []
    for suggestion in suggestions:
        normalized = normalize_greek(suggestion)
        distance = edit_distance(wanted, normalized)
        prefix = shared_prefix(wanted, normalized)
        if distance <= 2 and prefix >= max(5, min(len(wanted), len(normalized)) - 3):
            ranked.append((distance, -prefix, abs(len(wanted) - len(normalized)), suggestion))
    return [item[-1] for item in sorted(set(ranked))[:5]]


def rank_related_lemmas(target: str, suggestions: list[str]) -> list[str]:
    wanted = normalize_greek(target)
    ranked = []
    for suggestion in suggestions:
        normalized = normalize_greek(suggestion)
        prefix = shared_prefix(wanted, normalized)
        if normalized == wanted or prefix < 5:
            continue
        ranked.append((
            -prefix,
            edit_distance(wanted, normalized),
            abs(len(wanted) - len(normalized)),
            suggestion,
        ))
    return [item[-1] for item in sorted(set(ranked))[:10]]


def glaux_suggestions(endpoint: str, value: str) -> list[dict[str, Any]]:
    query = urllib.parse.urlencode({"string": value})
    return glaux_request_json(f"https://glaux.be/AJAX/{endpoint}?{query}")


def expanded_greek_search(target: str) -> tuple[list[dict[str, Any]], str, str]:
    hits = glaux_search(target, "lemma")
    if hits:
        return hits, "lemma", ""
    hits = glaux_search(target, "word")
    if hits:
        return hits, "word", ""

    wanted = normalize_greek(target)
    surface_suggestions = glaux_suggestions("getwordSuggestion.php", target)
    for item in surface_suggestions:
        form = unicodedata.normalize("NFC", item["word"])
        if normalize_greek(form) != wanted:
            continue
        hits = glaux_search(form, "word")
        if hits:
            return hits, "word", f"accent-normalized surface form: {form}"

    suggestions = []
    for trim in range(0, min(4, max(1, len(target) - 5))):
        prefix = target[:len(target) - trim] if trim else target
        suggestions.extend(
            unicodedata.normalize("NFC", item["lemma"])
            for item in glaux_suggestions("getLemmaSuggestion.php", prefix)
        )
    for lemma in safe_lemma_suggestions(target, list(dict.fromkeys(suggestions))):
        hits = glaux_search(lemma, "lemma")
        if hits:
            return hits, "lemma", f"interpreted as lemma: {lemma}"
    return [], "lemma", ""


def related_greek_lemmas(target: str) -> list[str]:
    suggestions = []
    minimum = min(5, len(target))
    for length in range(len(target), minimum - 1, -1):
        suggestions.extend(
            unicodedata.normalize("NFC", item["lemma"])
            for item in glaux_suggestions("getLemmaSuggestion.php", target[:length])
        )
        if len(suggestions) >= 10:
            break
    return rank_related_lemmas(target, list(dict.fromkeys(suggestions)))


def greek_candidates(target: str) -> list[Candidate]:
    hits, field, search_note = expanded_greek_search(target)
    sentence_ids = list(dict.fromkeys(hit["sentenceid"] for hit in hits))[:150]
    if not sentence_ids:
        return []
    details = sentence_details(sentence_ids)
    hits_by_sentence: dict[int, list[dict[str, Any]]] = {}
    for hit in hits:
        hits_by_sentence.setdefault(hit["sentenceid"], []).append(hit)
    output = []
    for sentence_id in sentence_ids:
        key = str(sentence_id)
        words = details["sentenceBuildInfo"].get(key, [])
        metadata = details["sentenceAuthorTitles"].get(key, [])
        matching = hits_by_sentence.get(sentence_id, [])
        if not words or not matching or len(words) > 50:
            continue
        matched_id = matching[0]["word_1_glaux_id"]
        token_index = next(
            (index for index, word in enumerate(words) if word["glaux_id"] == matched_id), None
        )
        if token_index is None:
            continue
        text, _, _ = render_glaux(words)
        cloze, surface, _ = render_glaux(words, matched_id)
        author = metadata[0] if metadata else ""
        work = metadata[1] if len(metadata) > 1 else ""
        url = f"https://glaux.be/detail.php?id={sentence_id}"
        output.append(Candidate(
            id=str(uuid.uuid4()), language="greek", text=text, cloze=cloze,
            target=surface, lemma=matching[0]["word_1_lemmastring"],
            translation=details["sentenceTranslations"].get(key) or "",
            author=author, work=work, citation=str(sentence_id),
            citation_html=(
                f"{html.escape(author)}, <i>{html.escape(work)}</i>, "
                f'<a href="{url}">GLAUx sentence {sentence_id}</a>'
            ), corpus="GLAUx-online",
            search_note=search_note,
        ))
    return rank_candidates(output)


def render_glaux(
    words: list[dict[str, Any]], matched_id: int | None = None,
) -> tuple[str, str, str]:
    output = ""
    target = ""
    lemma = ""
    for word in words:
        form = unicodedata.normalize("NFC", word.get("form_original") or word["word"])
        if word["glaux_id"] == matched_id:
            target = form
            lemma = unicodedata.normalize("NFC", word["lemma"])
            form = "{{c1::" + form + "}}"
        if form in PUNCTUATION:
            output = output.rstrip() + form + " "
        else:
            output += form + " "
    return output.strip(), target, lemma


def latin_candidates(target: str) -> list[Candidate]:
    field, results = tesserae_search(target)
    output = []
    for result in results:
        text = result.get("text", "")
        words = list(WORD_RE.finditer(text))
        if not words or len(words) > 50:
            continue
        if field == "lemma" and result.get("highlight_indices"):
            index = result["highlight_indices"][0]
        else:
            wanted = normalize_latin(target)
            index = next(
                (i for i, word in enumerate(words) if normalize_latin(word.group()) == wanted),
                None,
            )
        if index is None or index >= len(words):
            continue
        cloze, surface = replace_word(text, index)
        work = result.get("title") or result.get("work", "")
        citation = result.get("locus", "")
        output.append(Candidate(
            id=str(uuid.uuid4()), language="latin", text=text, cloze=cloze,
            target=surface,
            lemma=(result.get("matched_lemmas") or [target])[0],
            translation="", author=result.get("author", ""), work=work,
            citation=citation,
            citation_html=(
                f"{html.escape(result.get('author', ''))}, "
                f"<i>{html.escape(work)}</i>, {html.escape(citation)} (Tesserae)"
            ), corpus="Tesserae-online",
            search_note="",
        ))
    return rank_candidates(output)


def normalize_latin(value: str) -> str:
    return unicodedata.normalize("NFD", value).casefold().replace("v", "u").replace("j", "i")


def rank_candidates(candidates: list[Candidate]) -> list[Candidate]:
    def rank(candidate: Candidate) -> tuple[Any, ...]:
        words = len(WORD_RE.findall(candidate.text))
        era = 0
        if candidate.language == "latin":
            era = min(
                (priority for name, priority in ERA_PRIORITY.items() if name.casefold() in candidate.citation_html.casefold()),
                default=4,
            )
        return (0 if 5 <= words <= 20 else 1, era, abs(words - 12), words)

    return sorted(candidates, key=rank)[:10]


def repair_completed_clozes(state_path: Path) -> dict[str, int]:
    state = json.loads(state_path.read_text(encoding="utf-8"))
    completed = {
        original: item for original, item in state["completed"].items()
        if item["corpus"] == "GLAUx-online"
    }
    details = sentence_details([int(item["citation"]) for item in completed.values()])
    repaired = 0
    ambiguous = 0
    for item in completed.values():
        words = details["sentenceBuildInfo"].get(str(item["citation"]), [])
        matches = [
            word for word in words
            if normalize_greek(word["lemma"]) == normalize_greek(item["lemma"])
        ]
        if len(matches) != 1:
            ambiguous += 1
            continue
        cloze, target, _ = render_glaux(words, matches[0]["glaux_id"])
        note = anki("notesInfo", notes=[item["replacement_note_id"]])[0]
        sound = re.search(r"\[sound:[^]]+]", note["fields"]["Text"]["value"])
        anki("updateNoteFields", note={
            "id": item["replacement_note_id"],
            "fields": {"Text": cloze + (sound.group() if sound else "")},
        })
        item["target"] = target
        repaired += 1
    temporary = state_path.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    temporary.replace(state_path)
    return {"repaired": repaired, "ambiguous": ambiguous}


class Campaign:
    def __init__(self, state_path: Path, archive_path: Path):
        self.state_path = state_path
        self.archive_path = archive_path
        self.candidates: dict[str, Candidate] = {}
        self.state = self._load_state()
        self.notes = self._load_notes()

    def _load_state(self) -> dict[str, Any]:
        if self.state_path.exists():
            return json.loads(self.state_path.read_text(encoding="utf-8"))
        return {"schema_version": 1, "completed": {}, "skipped": {}}

    def _save_state(self) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.state_path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(self.state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        temporary.replace(self.state_path)

    def _load_notes(self) -> dict[int, dict[str, Any]]:
        ids = anki("findNotes", query=f'deck:"{UNMATCHED_DECK}" note:Basic')
        notes = []
        for start in range(0, len(ids), 500):
            notes.extend(anki("notesInfo", notes=ids[start:start + 500]))
        return {note["noteId"]: note for note in notes}

    def current(self) -> dict[str, Any]:
        pending = [
            note for note_id, note in self.notes.items()
            if str(note_id) not in self.state["skipped"]
        ]
        if not pending:
            return {"done": True, "progress": self.progress()}
        note = pending[0]
        return {
            "done": False,
            "note": self.note_json(note),
            "progress": self.progress(),
        }

    def note_json(self, note: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": note["noteId"],
            "front": note["fields"]["Front"]["value"],
            "back": note["fields"]["Back"]["value"],
            "language": language_for(note),
            "tags": note["tags"],
        }

    def progress(self) -> dict[str, int]:
        return {
            "pending": len(self.notes) - len(self.state["skipped"]),
            "completed": len(self.state["completed"]),
            "skipped": len(self.state["skipped"]),
        }

    def search(self, note_id: int, target: str) -> dict[str, Any]:
        note = self.notes.get(note_id)
        if note is None:
            raise ValueError("note is no longer pending")
        target = strip_html(target).strip(" ,.;:!?()[]{}\"“”‘’")
        if not target:
            raise ValueError("select or enter a target")
        found = greek_candidates(target) if language_for(note) == "greek" else latin_candidates(target)
        self.candidates = {candidate.id: candidate for candidate in found}
        suggestions = (
            related_greek_lemmas(target)
            if language_for(note) == "greek" and not found else []
        )
        return {
            "candidates": [candidate.to_json() for candidate in found],
            "suggestions": suggestions,
        }

    def skip(self, note_id: int, reason: str) -> None:
        if note_id not in self.notes:
            raise ValueError("note is no longer pending")
        self.state["skipped"][str(note_id)] = reason or "skipped"
        anki("addTags", notes=[note_id], tags="manual_migration::skipped")
        self._save_state()

    def approve(self, note_id: int, candidate_id: str, translation: str) -> int:
        note = self.notes.get(note_id)
        candidate = self.candidates.get(candidate_id)
        if note is None or candidate is None:
            raise ValueError("note or candidate is no longer available")
        ensure_model()
        existing = anki(
            "findNotes",
            query=(
                f'deck:"{DESTINATION_DECK}" '
                f'OriginalNoteID:{note_id} tag:generated::manual_campaign'
            ),
        )
        for existing_id in existing:
            existing_note = anki("notesInfo", notes=[existing_id])[0]
            existing_cards = anki("findCards", query=f"nid:{existing_id}")
            card_info = anki("cardsInfo", cards=existing_cards)
            incomplete = (
                "[sound:" not in existing_note["fields"]["Text"]["value"]
                and card_info
                and all(card["queue"] == -1 for card in card_info)
            )
            if not incomplete:
                raise RuntimeError(
                    f"original note {note_id} already has replacement {existing_id}"
                )
            anki("deleteNotes", notes=[existing_id])
        replacement = anki("addNote", note={
            "deckName": DESTINATION_DECK,
            "modelName": MODEL_NAME,
            "fields": {
                "Text": candidate.cloze,
                "Gloss": note["fields"]["Back"]["value"],
                "Translation": translation.strip(),
                "Citation": candidate.citation_html,
                "Alternatives": "",
                "Lemma": candidate.lemma,
                "Morphology": "",
                "OriginalNoteID": str(note_id),
            },
            "options": {"allowDuplicate": True},
            "tags": [
                "generated::corpus_cloze", "generated::manual_campaign",
                f"language::{candidate.language}",
                "corpus_cloze::translation_" + ("passage" if translation.strip() else "missing"),
            ],
        })
        cards = anki("findCards", query=f"nid:{replacement}")
        anki("suspend", cards=cards)
        audio_manifest = {
            "entries": [{
                "generated_note_id": replacement,
                "language": candidate.language,
                "primary": {"text": candidate.text},
                "text": candidate.cloze,
            }]
        }
        with tempfile.TemporaryDirectory() as directory:
            manifest_path = Path(directory) / "manual-audio.json"
            manifest_path.write_text(json.dumps(audio_manifest), encoding="utf-8")
            generate_audio(manifest_path, 0.8)
        replacement_note = anki("notesInfo", notes=[replacement])[0]
        if "[sound:" not in replacement_note["fields"]["Text"]["value"]:
            raise RuntimeError("replacement audio was not attached")
        self._archive(note_id, note, anki("cardsInfo", cards=anki("findCards", query=f"nid:{note_id}")))
        anki("unsuspend", cards=cards)
        anki("deleteNotes", notes=[note_id])
        self.state["completed"][str(note_id)] = {
            "replacement_note_id": replacement,
            "target": candidate.target,
            "lemma": candidate.lemma,
            "corpus": candidate.corpus,
            "citation": candidate.citation,
        }
        self.notes.pop(note_id)
        self._save_state()
        return replacement

    def _archive(
        self, note_id: int, note: dict[str, Any], cards: list[dict[str, Any]],
    ) -> None:
        archive = (
            json.loads(self.archive_path.read_text(encoding="utf-8"))
            if self.archive_path.exists()
            else {"schema_version": 1, "notes": [], "cards": []}
        )
        if not any(item["noteId"] == note_id for item in archive["notes"]):
            archive["notes"].append(note)
            archive["cards"].extend(cards)
        self.archive_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.archive_path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(archive, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        temporary.replace(self.archive_path)
