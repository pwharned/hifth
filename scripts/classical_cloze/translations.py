from __future__ import annotations

import re
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path


TEI = "{http://www.tei-c.org/ns/1.0}"
BASE_URL = "https://raw.githubusercontent.com/PerseusDL/{repo}/master/data/{author}/{work}/{file}.xml"

# Prefer editions whose canonical divisions match the source editions used by
# the corpus. Additional works can be added without changing the pipeline.
EDITIONS = {
    "0059-001": ("canonical-greekLit", "tlg0059", "tlg001", "tlg0059.tlg001.perseus-eng2"),
    "0059-002": ("canonical-greekLit", "tlg0059", "tlg002", "tlg0059.tlg002.perseus-eng2"),
    "0059-003": ("canonical-greekLit", "tlg0059", "tlg003", "tlg0059.tlg003.perseus-eng2"),
    "0059-030": ("canonical-greekLit", "tlg0059", "tlg030", "tlg0059.tlg030.perseus-eng2"),
    "0086-010": ("canonical-greekLit", "tlg0086", "tlg010", "tlg0086.tlg010.perseus-eng2"),
    "tlg0012.tlg001": ("canonical-greekLit", "tlg0012", "tlg001", "tlg0012.tlg001.perseus-eng3"),
    "phi0448.phi001": ("canonical-latinLit", "phi0448", "phi001", "phi0448.phi001.perseus-eng2"),
    "phi0690.phi003": ("canonical-latinLit", "phi0690", "phi003", "phi0690.phi003.perseus-eng2"),
    "phi0914.phi001": ("canonical-latinLit", "phi0914", "phi001", "phi0914.phi001.perseus-eng1"),
}
TREEBANKS = {
    "tlg0012.tlg001.perseus-grc1.tb.xml": (
        "https://raw.githubusercontent.com/PerseusDL/treebank_data/master/"
        "v2.1/Greek/texts/tlg0012.tlg001.perseus-grc1.tb.xml"
    ),
}


@dataclass(frozen=True)
class Translation:
    text: str
    urn: str
    translator: str
    level: str


def edition_key(document_id: str) -> str:
    if document_id in EDITIONS:
        return document_id
    match = re.match(r"((?:tlg|phi)\d+\.(?:tlg|phi)\d+)", document_id)
    return match.group(1) if match else document_id


def edition_urn(document_id: str) -> str | None:
    spec = EDITIONS.get(edition_key(document_id))
    return spec[3] if spec else None


def canonical_citation(document_id: str, citation: str) -> str:
    match = re.fullmatch(r"(\d+)\.(\d+[a-z]?)-(\d+[a-z]?)\.\d+", citation)
    if match:
        book, start, end = match.groups()
        return f"{book}.{start}-{book}.{end}"
    if edition_key(document_id).startswith("phi") and re.fullmatch(
        r"(?:[^.]+\.)+\d+", citation
    ):
        return citation.rsplit(".", 1)[0]
    return citation


def fetch_translations(destination: Path) -> int:
    destination.mkdir(parents=True, exist_ok=True)
    for repo, author, work, filename in dict.fromkeys(EDITIONS.values()):
        url = BASE_URL.format(repo=repo, author=author, work=work, file=filename)
        request = urllib.request.Request(url, headers={"User-Agent": "classical-cloze/1"})
        with urllib.request.urlopen(request, timeout=120) as response:
            (destination / f"{filename}.xml").write_bytes(response.read())
    for filename, url in TREEBANKS.items():
        request = urllib.request.Request(url, headers={"User-Agent": "classical-cloze/1"})
        with urllib.request.urlopen(request, timeout=120) as response:
            (destination / filename).write_bytes(response.read())
    return len(set(EDITIONS.values()))


@lru_cache(maxsize=None)
def treebank_references(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    return {
        sentence.get("id", ""): sentence.get("subdoc", "")
        for sentence in ET.parse(path).getroot().iter("sentence")
        if sentence.get("id") and sentence.get("subdoc")
    }


def canonical_treebank_reference(cache: Path, sent_id: str) -> str | None:
    if "@" not in sent_id:
        return None
    filename, source_id = sent_id.rsplit("@", 1)
    if filename not in TREEBANKS:
        return None
    return treebank_references(cache / filename).get(source_id)


def clean_text(value: str) -> str:
    return re.sub(r"\s+([,.;:?!])", r"\1", " ".join(value.split())).strip()


def translator(root: ET.Element) -> str:
    for element in root.findall(f".//{TEI}editor"):
        if element.get("role") == "translator":
            return clean_text("".join(element.itertext()))
    for statement in root.findall(f".//{TEI}respStmt"):
        responsibility = " ".join(
            clean_text("".join(element.itertext()))
            for element in statement.findall(f"{TEI}resp")
        )
        if "translator" in responsibility.casefold():
            name = statement.find(f"{TEI}name")
            if name is not None:
                return clean_text("".join(name.itertext()))
    return "Perseus Digital Library"


EXCLUDED = {f"{TEI}note", f"{TEI}label"}


def passage_text(element: ET.Element) -> str:
    parts: list[str] = []

    def walk(node: ET.Element) -> None:
        if node.tag in EXCLUDED:
            return
        if node.text:
            parts.append(node.text)
        for child in node:
            walk(child)
            if child.tail:
                parts.append(child.tail)

    walk(element)
    return clean_text(" ".join(parts))


def milestone_passage(root: ET.Element, reference: str) -> str:
    collecting = False
    parts: list[str] = []

    def walk(element: ET.Element) -> None:
        nonlocal collecting
        if element.tag in EXCLUDED:
            return
        if element.tag == f"{TEI}milestone" and element.get("unit") in {"section", "page"}:
            marker = element.get("n", "")
            if marker == reference:
                collecting = True
            elif collecting:
                collecting = False
            return
        if collecting and element.text and element.tag not in {f"{TEI}note", f"{TEI}label"}:
            parts.append(element.text)
        for child in element:
            walk(child)
            if collecting and child.tail:
                parts.append(child.tail)

    walk(root)
    return clean_text(" ".join(parts))


def numbered_children(element: ET.Element) -> list[ET.Element]:
    output = []
    for child in element:
        if child.tag != f"{TEI}div":
            continue
        if child.get("n") is not None:
            output.append(child)
        else:
            output.extend(numbered_children(child))
    return output


def div_passage(root: ET.Element, parts: list[str]) -> str:
    if not parts:
        return ""
    candidates = [
        element for element in root.iter(f"{TEI}div")
        if element.get("n") == parts[0]
    ]
    for part in parts[1:]:
        candidates = [
            child for parent in candidates for child in numbered_children(parent)
            if child.get("n") == part
        ]
        if not candidates:
            return ""
    return passage_text(candidates[0]) if candidates else ""


def verse_passage(root: ET.Element, book: str, start: str, end: str) -> str:
    books = [
        element for element in root.iter(f"{TEI}div")
        if element.get("subtype", "").casefold() == "book" and element.get("n") == book
    ]
    if not books:
        return ""
    cards = [
        element for element in books[0].iter(f"{TEI}div")
        if element.get("subtype") == "card" and element.get("n", "").isdigit()
    ]
    selected = []
    start_number = int(start.rstrip("abcdefghijklmnopqrstuvwxyz"))
    end_number = int(end.rstrip("abcdefghijklmnopqrstuvwxyz"))
    for index, card in enumerate(cards):
        card_start = int(card.get("n", "0"))
        card_end = int(cards[index + 1].get("n", "0")) - 1 if index + 1 < len(cards) else 10**9
        if card_start <= end_number and start_number <= card_end:
            selected.append(passage_text(card))
    return clean_text(" ".join(selected))


def iliad_passage(root: ET.Element, reference: str) -> str:
    match = re.fullmatch(r"(\d+)\.(\d+)(?:-(?:(\d+)\.)?(\d+))?", reference)
    if not match:
        return ""
    book, start, end_book, end = match.groups()
    if end_book and end_book != book:
        return ""
    return verse_passage(root, book, start, end or start)


def lookup(cache: Path, document_id: str, citation: str) -> Translation | None:
    spec = EDITIONS.get(edition_key(document_id))
    if spec is None:
        return None
    urn = spec[3]
    path = cache / f"{urn}.xml"
    if not path.exists():
        return None
    root = ET.parse(path).getroot()
    passage = ""
    level = "passage"
    if edition_key(document_id) == "tlg0012.tlg001":
        passage = iliad_passage(root, citation)
        if not passage:
            return None
        return Translation(passage, urn, translator(root), level)
    line_match = re.fullmatch(r"(\d+)\.(\d+[a-z]?)-(\d+[a-z]?)\.(\d+)", citation)
    if line_match:
        book, start, end, _ = line_match.groups()
        passage = verse_passage(root, book, start, end)
    elif re.fullmatch(r"\d+[a-z]", citation):
        passage = milestone_passage(root, citation)
    else:
        parts = citation.split(".")
        if parts and parts[-1].isdigit() and edition_key(document_id).startswith("phi"):
            parts.pop()
        if edition_key(document_id) == "phi0448.phi001" and len(parts) == 3:
            parts.pop()
        passage = div_passage(root, parts)
    if not passage:
        return None
    return Translation(passage, urn, translator(root), level)


def passage_url(urn: str, citation: str) -> str:
    namespace = "greekLit" if urn.startswith("tlg") else "latinLit"
    return f"https://scaife.perseus.org/reader/urn:cts:{namespace}:{urn}:{citation}/"
