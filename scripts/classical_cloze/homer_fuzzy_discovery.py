"""Discover Homeric examples for malformed Greek forms through GLAUx."""

from __future__ import annotations

import argparse
import json
import time
import unicodedata
from pathlib import Path
from typing import Any

from scripts.classical_cloze.analyze_homer_residuals import terms
from scripts.classical_cloze.glaux_discovery import (
    render_sentence,
    search,
    sentence_details,
)
from scripts.classical_cloze.manual_campaign import (
    expanded_greek_search,
    glaux_suggestions,
    normalize_greek,
    related_greek_lemmas,
    safe_lemma_suggestions,
)
from scripts.classical_cloze.pipeline import UNMATCHED_DECK, anki, is_greek, strip_html


LOW_VALUE = {
    "αλλα", "απο", "αρα", "αυτοσ", "γαρ", "δε", "δη", "ει", "ειμι", "ειναι",
    "εισ", "εγω", "η", "και", "κατα", "μεν", "μη", "ο", "οδε", "οπωσ", "οτι",
    "ου", "ουδε", "ουτε", "ουτοσ", "παρα", "περι", "προσ", "συ", "τε", "τισ", "ωσ",
}


def pending_greek_notes() -> list[dict[str, Any]]:
    ids = anki("findNotes", query=f'deck:"{UNMATCHED_DECK}" note:Basic')
    notes = []
    for start in range(0, len(ids), 500):
        notes.extend(anki("notesInfo", notes=ids[start:start + 500]))
    return [
        note for note in notes
        if "Greek" in note["tags"] or is_greek(strip_html(note["fields"]["Front"]["value"]))
    ]


def candidate_terms(front: str) -> list[str]:
    values = list(dict.fromkeys(terms(front)))
    return sorted(
        (value for value in values if len(value) >= 5 and value not in LOW_VALUE),
        key=lambda value: -len(value),
    )[:1]


def fuzzy_surface_forms(value: str) -> list[str]:
    suggestions = []
    for trim in range(0, min(5, max(1, len(value) - 5))):
        prefix = value[:len(value) - trim] if trim else value
        suggestions.extend(
            unicodedata.normalize("NFC", item["word"])
            for item in glaux_suggestions("getwordSuggestion.php", prefix)
        )
    return safe_lemma_suggestions(value, list(dict.fromkeys(suggestions)))


def homer_hits(value: str) -> tuple[list[dict[str, Any]], str]:
    attempts = []
    hits, _, note = expanded_greek_search(value)
    if hits:
        attempts.append((hits, note or value))
    for lemma in related_greek_lemmas(value)[:5]:
        related = search(lemma, "lemma")
        if related:
            attempts.append((related, f"related lemma: {lemma}"))
    for form in fuzzy_surface_forms(value):
        related = search(form, "word")
        if related:
            attempts.append((related, f"related surface: {form}"))

    for found, interpretation in attempts:
        sentence_ids = list(dict.fromkeys(hit["sentenceid"] for hit in found))[:150]
        details = sentence_details(sentence_ids)
        homer_ids = {
            sentence_id for sentence_id in sentence_ids
            if details["sentenceAuthorTitles"].get(str(sentence_id), [""])[0] == "Homerus"
        }
        selected = [hit for hit in found if hit["sentenceid"] in homer_ids]
        if selected:
            return selected, interpretation
    return [], ""


def discover(output_path: Path, delay: float) -> None:
    output = (
        json.loads(output_path.read_text(encoding="utf-8"))
        if output_path.exists()
        else {"schema_version": 1, "kind": "glaux-homer-fuzzy", "results": {}}
    )
    output["kind"] = "glaux-homer-fuzzy"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    for note in pending_greek_notes():
        key = str(note["noteId"])
        if key in output["results"]:
            continue
        examples = []
        searched = ""
        interpretation = ""
        for value in candidate_terms(note["fields"]["Front"]["value"]):
            hits, interpretation = homer_hits(value)
            if not hits:
                continue
            searched = value
            sentence_ids = list(dict.fromkeys(hit["sentenceid"] for hit in hits))[:50]
            details = sentence_details(sentence_ids)
            hits_by_sentence: dict[int, list[dict[str, Any]]] = {}
            for hit in hits:
                hits_by_sentence.setdefault(hit["sentenceid"], []).append(hit)
            for sentence_id in sentence_ids:
                sid = str(sentence_id)
                words = details["sentenceBuildInfo"].get(sid, [])
                metadata = details["sentenceAuthorTitles"].get(sid, [])
                if not words or len(words) > 50:
                    continue
                examples.append({
                    "sentence_id": sentence_id,
                    "author": metadata[0] if metadata else "",
                    "work": metadata[1] if len(metadata) > 1 else "",
                    "genre": metadata[2] if len(metadata) > 2 else "",
                    "text": render_sentence(words),
                    "translation": details["sentenceTranslations"].get(sid),
                    "interpretation": interpretation,
                    "matches": [{
                        "glaux_id": hit["word_1_glaux_id"],
                        "form": unicodedata.normalize("NFC", hit["word_1_wordstring"]),
                        "lemma": unicodedata.normalize("NFC", hit["word_1_lemmastring"]),
                    } for hit in hits_by_sentence.get(sentence_id, [])],
                    "tokens": [{
                        "glaux_id": token["glaux_id"],
                        "form": unicodedata.normalize(
                            "NFC", token.get("form_original") or token["word"]
                        ),
                        "lemma": unicodedata.normalize("NFC", token["lemma"]),
                        "postag": token["postag"],
                    } for token in words],
                })
            break
        output["results"][key] = {
            "front": note["fields"]["Front"]["value"],
            "searched": searched,
            "interpretation": interpretation,
            "examples": examples,
        }
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        time.sleep(delay)
    print(json.dumps({
        "queried": len(output["results"]),
        "with_homer_hits": sum(bool(item["examples"]) for item in output["results"].values()),
        "output": str(output_path),
    }, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output", type=Path,
        default=Path("data/classical_cloze/corpora/glaux-homer-fuzzy.json"),
    )
    parser.add_argument("--delay", type=float, default=3.0)
    args = parser.parse_args()
    discover(args.output, args.delay)


if __name__ == "__main__":
    main()
