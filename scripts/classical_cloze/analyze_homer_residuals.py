"""Classify remaining Greek cards against local Homeric GLAUx texts."""

from __future__ import annotations

import json
import re
import unicodedata
from collections import Counter
from pathlib import Path
from typing import Any

from scripts.classical_cloze.pipeline import (
    UNMATCHED_DECK,
    anki,
    is_greek,
    normalize,
    read_glaux,
)


GREEK_WORD = re.compile(r"[\u0370-\u03ff\u1f00-\u1fff\u0300-\u036f]+")


def lexical(value: str) -> str:
    return normalize(value, "greek", loose=True).strip("’'᾽")


def terms(value: str) -> list[str]:
    return [
        lexical(match.group())
        for match in GREEK_WORD.finditer(value)
        if len(lexical(match.group())) > 1
    ]


def is_subsequence(needles: list[str], haystack: list[str], max_gap: int | None) -> bool:
    position = -1
    for needle in needles:
        found = next(
            (index for index in range(position + 1, len(haystack)) if haystack[index] == needle),
            None,
        )
        if found is None or (max_gap is not None and position >= 0 and found - position - 1 > max_gap):
            return False
        position = found
    return True


def contiguous(needles: list[str], haystack: list[str]) -> bool:
    return any(
        haystack[start:start + len(needles)] == needles
        for start in range(len(haystack) - len(needles) + 1)
    )


def contiguous_compatible(needles: list[str], sentence: dict[str, Any]) -> bool:
    return any(
        all(
            needle in {sentence["forms"][start + offset], sentence["lemmas"][start + offset]}
            for offset, needle in enumerate(needles)
        )
        for start in range(len(sentence["forms"]) - len(needles) + 1)
    )


def compatible_subsequence(
    needles: list[str], sentence: dict[str, Any], max_gap: int | None,
) -> bool:
    position = -1
    for needle in needles:
        found = next((
            index for index in range(position + 1, len(sentence["forms"]))
            if needle in {sentence["forms"][index], sentence["lemmas"][index]}
        ), None)
        if found is None or (max_gap is not None and position >= 0 and found - position - 1 > max_gap):
            return False
        position = found
    return True


def classify(front: str, sentences: list[dict[str, Any]], vocabulary: set[str]) -> str:
    wanted = terms(front)
    if not wanted:
        return "no_greek_target"
    if len(wanted) == 1:
        return "single_attested" if wanted[0] in vocabulary else "single_unattested_or_variant"

    for sentence in sentences:
        if contiguous(wanted, sentence["forms"]):
            return "exact_surface_phrase"
    for sentence in sentences:
        if contiguous_compatible(wanted, sentence):
            return "contiguous_lemma_equivalent"
    for sentence in sentences:
        if compatible_subsequence(wanted, sentence, 3):
            return "ordered_near_phrase"
    for sentence in sentences:
        if compatible_subsequence(wanted, sentence, None):
            return "ordered_same_sentence"
    if all(term in vocabulary for term in wanted):
        return "independently_attested_components"
    if any(term in vocabulary for term in wanted):
        return "partially_attested_components"
    return "unattested_phrase_or_annotation"


def load_homer(root: Path) -> list[dict[str, Any]]:
    output = []
    for name in ("0012-001.xml", "0012-002.xml"):
        for sentence in read_glaux(root / name):
            forms = [lexical(token.form) for token in sentence.tokens]
            lemmas = [lexical(token.lemma) for token in sentence.tokens]
            output.append({
                "work": sentence.work,
                "reference": sentence.sentence_id,
                "text": sentence.text,
                "forms": forms,
                "lemmas": lemmas,
                "mixed": [lemma if lemma else form for form, lemma in zip(forms, lemmas, strict=True)],
            })
    return output


def main() -> None:
    corpus_root = Path("data/classical_cloze/corpora/GLAUx/xml")
    sentences = load_homer(corpus_root)
    vocabulary = {
        value for sentence in sentences
        for value in sentence["forms"] + sentence["lemmas"]
    }
    note_ids = anki("findNotes", query=f'deck:"{UNMATCHED_DECK}" note:Basic')
    notes = []
    for start in range(0, len(note_ids), 500):
        notes.extend(anki("notesInfo", notes=note_ids[start:start + 500]))
    results = []
    for note in notes:
        front = note["fields"]["Front"]["value"]
        if "Greek" not in note["tags"] and not is_greek(front):
            continue
        results.append({
            "note_id": note["noteId"],
            "front": front,
            "back": note["fields"]["Back"]["value"],
            "terms": terms(front),
            "classification": classify(front, sentences, vocabulary),
        })
    counts = Counter(item["classification"] for item in results)
    output = {
        "schema_version": 1,
        "notes": len(results),
        "homer_sentences": len(sentences),
        "counts": dict(counts),
        "results": results,
    }
    path = Path("data/classical_cloze/homer-residual-analysis.json")
    path.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(path), "counts": dict(counts)}, indent=2))


if __name__ == "__main__":
    main()
