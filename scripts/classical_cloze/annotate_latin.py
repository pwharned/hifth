"""Annotate selected canonical Perseus Latin texts with LatinCy.

Run this module with the isolated Python environment documented in README.md.
The output is deterministic JSON consumed by the main pipeline.
"""

from __future__ import annotations

import argparse
import importlib
import json
import re
import unicodedata
import xml.etree.ElementTree as ET
from pathlib import Path


NS = {"tei": "http://www.tei-c.org/ns/1.0"}
SOURCES = {
    "caesar-gallic-war.xml": ("Julius Caesar", "Gallic War", "phi0448.phi001.perseus-lat2", None),
    "virgil-aeneid.xml": ("Virgil", "Aeneid", "phi0690.phi003.perseus-lat2", None),
    "livy-auc.xml": ("Livy", "Ab Urbe Condita", "phi0914.phi001.perseus-lat2", {"1", "2"}),
}


def clean_text(element: ET.Element) -> str:
    text = " ".join("".join(element.itertext()).split())
    return unicodedata.normalize("NFC", re.sub(r"\s+([,.;:?!])", r"\1", text))


def ancestor_citation(parents: dict[ET.Element, ET.Element], element: ET.Element) -> str:
    parts = []
    current = element
    while current in parents:
        current = parents[current]
        if current.tag.endswith("div") and current.get("n"):
            subtype = current.get("subtype", "")
            if subtype in {"book", "chapter", "section"}:
                parts.append(current.get("n", ""))
    return ".".join(reversed(parts))


def extract_blocks(path: Path, allowed_books: set[str] | None):
    root = ET.parse(path).getroot()
    parents = {child: parent for parent in root.iter() for child in parent}
    if path.name == "virgil-aeneid.xml":
        for book in root.findall('.//tei:div[@subtype="book"]', NS):
            book_number = book.get("n", "")
            buffered = []
            start_line = ""
            end_line = ""
            for line in book.findall(".//tei:l", NS):
                text = clean_text(line)
                if not text:
                    continue
                start_line = start_line or line.get("n", "")
                end_line = line.get("n", "")
                buffered.append(text)
                if re.search(r"[.!?;:]$", text) or len(buffered) >= 8:
                    yield f"{book_number}.{start_line}-{end_line}", " ".join(buffered)
                    buffered, start_line, end_line = [], "", ""
            if buffered:
                yield f"{book_number}.{start_line}-{end_line}", " ".join(buffered)
        return
    for paragraph in root.findall(".//tei:p", NS):
        citation = ancestor_citation(parents, paragraph)
        book = citation.split(".", 1)[0]
        if allowed_books is not None and book not in allowed_books:
            continue
        text = clean_text(paragraph)
        if text:
            yield citation, text


def annotate(input_dir: Path, output: Path) -> None:
    latin_model = importlib.import_module("la_core_web_lg")
    nlp = latin_model.load()
    nlp.max_length = 2_000_000
    sentences = []
    for filename, (author, work, document_id, allowed_books) in SOURCES.items():
        for citation, block in extract_blocks(input_dir / filename, allowed_books):
            document = nlp(block)
            for sentence_number, sentence in enumerate(document.sents, 1):
                tokens = [token for token in sentence if not token.is_space]
                if not tokens:
                    continue
                sentences.append({
                    "author": author,
                    "work": work,
                    "document_id": document_id,
                    "citation": f"{citation}.{sentence_number}",
                    "text": sentence.text.strip(),
                    "tokens": [{
                        "form": token.text,
                        "lemma": token.lemma_,
                        "upos": token.pos_,
                        "features": token.morph.to_dict(),
                        "space_after": bool(token.whitespace_),
                    } for token in tokens],
                })
    output.write_text(json.dumps({
        "schema_version": 1,
        "corpus": "Perseus-TEI+LatinCy-3.9.8",
        "sentences": sentences,
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(output), "sentences": len(sentences)}))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, default=Path("data/classical_cloze/corpora/canonical-latinLit"))
    parser.add_argument("--output", type=Path, default=Path("data/classical_cloze/corpora/canonical-latinLit/annotated.json"))
    args = parser.parse_args()
    annotate(args.input, args.output)


if __name__ == "__main__":
    main()
