"""Build a conservative Homer-only salvage manifest from remaining originals."""

from __future__ import annotations

import argparse
import html
import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any

from scripts.classical_cloze.analyze_homer_residuals import lexical, terms
from scripts.classical_cloze.pipeline import (
    Candidate,
    TRANSLATION_ROOT,
    candidate_json,
    citation,
    cloze_text,
    export_source_notes,
    morphology,
    read_glaux,
)
from scripts.classical_cloze.translations import lookup


CONTENT_POS = {"NOUN", "VERB", "ADJ", "ADV", "PROPN", "NUM"}
LOW_VALUE = {
    "αλλα", "απο", "αρα", "αυτοσ", "γαρ", "δε", "δη", "ει", "ειμι", "ειναι",
    "εισ", "εγω", "η", "και", "κατα", "μεν", "μη", "ο", "οδε", "οπωσ",
    "οτι", "ου", "ουδε", "ουτε", "ουτοσ", "παρα", "περι", "προσ", "συ",
    "τε", "τισ", "ωσ",
}


def homer_sentences(root: Path):
    for filename in ("0012-001.xml", "0012-002.xml"):
        yield from read_glaux(root / filename)


def indexes(sentences):
    lemma = defaultdict(list)
    form = defaultdict(list)
    for sentence in sentences:
        for index, token in enumerate(sentence.tokens):
            if token.upos in CONTENT_POS:
                lemma[lexical(token.lemma)].append((sentence, index))
                form[lexical(token.form)].append((sentence, index))
    return lemma, form


def phrase_matches(wanted: list[str], sentences) -> list[Candidate]:
    if not 2 <= len(wanted) <= 6:
        return []
    output = []
    for sentence in sentences:
        if len(sentence.tokens) > 35:
            continue
        for start in range(len(sentence.tokens) - len(wanted) + 1):
            if all(
                term in {
                    lexical(sentence.tokens[start + offset].form),
                    lexical(sentence.tokens[start + offset].lemma),
                }
                for offset, term in enumerate(wanted)
            ):
                output.append(Candidate(sentence, start, start + len(wanted), "salvaged_phrase"))
    output.sort(key=lambda item: (abs(len(item.sentence.tokens) - 12), len(item.sentence.tokens)))
    return output


def component_match(wanted: list[str], lemma_index, form_index) -> Candidate | None:
    choices = []
    for source_order, term in enumerate(wanted):
        if len(term) < 5 or term in LOW_VALUE:
            continue
        hits = lemma_index.get(term) or form_index.get(term) or []
        hits = [
            hit for hit in hits
            if lexical(hit[0].tokens[hit[1]].lemma) not in LOW_VALUE
            and lexical(hit[0].tokens[hit[1]].form) not in LOW_VALUE
        ]
        if not hits:
            continue
        usable = [hit for hit in hits if len(hit[0].tokens) <= 35]
        if not usable:
            continue
        sentence, index = min(
            usable,
            key=lambda hit: (
                0 if 5 <= len(hit[0].tokens) <= 20 else 1,
                abs(len(hit[0].tokens) - 12),
            ),
        )
        choices.append((-len(term), len(hits), source_order, sentence, index))
    if not choices:
        return None
    _, _, _, sentence, index = min(
        choices, key=lambda item: (item[0], item[1], item[2])
    )
    return Candidate(sentence, index, index + 1, "salvaged_component")


def matched_entry(source: dict[str, Any], candidate: Candidate) -> dict[str, Any]:
    aligned = lookup(
        TRANSLATION_ROOT,
        candidate.sentence.document_id,
        candidate.sentence.sentence_id,
    )
    original_gloss = source.get("gloss_html", "")
    gloss = (
        '<small><b>Original note (may not describe selected target):</b> '
        + original_gloss + "</small>"
    )
    return {
        **source,
        "status": "matched",
        "text": cloze_text(candidate),
        "gloss_html": gloss,
        "translation": aligned.text if aligned else "",
        "translation_level": aligned.level if aligned else None,
        "translation_urn": aligned.urn if aligned else None,
        "citation_html": citation(candidate),
        "lemma": candidate.sentence.tokens[candidate.start].lemma,
        "morphology_html": morphology(candidate),
        "tags": [
            "generated::corpus_cloze",
            "generated::homer_salvage",
            f"homer_salvage::{candidate.match_type.removeprefix('salvaged_')}",
            "language::greek",
            "corpus_cloze::translation_" + (aligned.level if aligned else "missing"),
        ],
        "primary": candidate_json(candidate),
        "alternatives": [],
        "salvage_method": candidate.match_type,
    }


def build(root: Path) -> dict[str, Any]:
    sentences = list(homer_sentences(root))
    lemma_index, form_index = indexes(sentences)
    entries = []
    counts = defaultdict(int)
    for source in export_source_notes():
        if source["language"] != "greek":
            entries.append({**source, "status": "not_greek_salvage", "candidates": []})
            continue
        wanted = terms(source["front"])
        phrases = phrase_matches(wanted, sentences)
        candidate = phrases[0] if phrases else component_match(wanted, lemma_index, form_index)
        if candidate is None:
            entries.append({**source, "status": "homer_salvage_unmatched", "candidates": []})
            continue
        entries.append(matched_entry(source, candidate))
        counts[candidate.match_type] += 1
    return {
        "schema_version": 1,
        "kind": "classical-cloze-homer-salvage",
        "corpus_sentence_count": len(sentences),
        "matched_counts": {"greek": sum(counts.values())},
        "salvage_counts": dict(counts),
        "entries": entries,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--corpora", type=Path,
        default=Path("data/classical_cloze/corpora/GLAUx/xml"),
    )
    parser.add_argument(
        "--output", type=Path,
        default=Path("data/classical_cloze/homer-salvage.json"),
    )
    args = parser.parse_args()
    manifest = build(args.corpora)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps({
        "output": str(args.output),
        "matched_counts": manifest["matched_counts"],
        "salvage_counts": manifest["salvage_counts"],
    }, indent=2))


if __name__ == "__main__":
    main()
