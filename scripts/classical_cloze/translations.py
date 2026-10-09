from __future__ import annotations

import re
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
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
    "phi0448.phi001": ("canonical-latinLit", "phi0448", "phi001", "phi0448.phi001.perseus-eng2"),
    "phi0690.phi003": ("canonical-latinLit", "phi0690", "phi003", "phi0690.phi003.perseus-eng2"),
    "phi0914.phi001": ("canonical-latinLit", "phi0914", "phi001", "phi0914.phi001.perseus-eng1"),
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
    if re.fullmatch(r"\d+\.\d+-\d+\.\d+", citation):
        return citation.rsplit(".", 1)[0]
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
    return len(set(EDITIONS.values()))


def clean_text(value: str) -> str:
    return re.sub(r"\s+([,.;:?!])", r"\1", " ".join(value.split())).strip()


def translator(root: ET.Element) -> str:
    for element in root.findall(f".//{TEI}editor"):
        if element.get("role") == "translator":
            return clean_text("".join(element.itertext()))
    return "Perseus Digital Library"


def split_sentences(text: str) -> list[str]:
    return [part.strip() for part in re.split(r"(?<=[.!?])\s+(?=[\"'‘“A-Z])", text) if part.strip()]


def milestone_passage(root: ET.Element, reference: str) -> str:
    collecting = False
    parts: list[str] = []

    def walk(element: ET.Element) -> None:
        nonlocal collecting
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


def div_passage(root: ET.Element, parts: list[str]) -> str:
    candidates = [root]
    for part in parts:
        found = [
            child for parent in candidates for child in parent.iter(f"{TEI}div")
            if child.get("n") == part
        ]
        if not found:
            return ""
        candidates = found
    return clean_text(" ".join(candidates[0].itertext())) if candidates else ""


def line_passage(root: ET.Element, book: str, start: int, end: int) -> str:
    books = [element for element in root.iter(f"{TEI}div") if element.get("n") == book]
    if not books:
        return ""
    lines = []
    for line in books[0].iter(f"{TEI}l"):
        number = line.get("n", "")
        if number.isdigit() and start <= int(number) <= end:
            lines.append(clean_text("".join(line.itertext())))
    return clean_text(" ".join(lines))


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
    sentence_number = 0
    line_match = re.fullmatch(r"(\d+)\.(\d+)-(\d+)\.(\d+)", citation)
    if line_match:
        book, start, end, sentence = map(int, line_match.groups())
        passage = line_passage(root, str(book), start, end)
        sentence_number = sentence
    elif re.fullmatch(r"\d+[a-z]", citation):
        passage = milestone_passage(root, citation)
    else:
        parts = citation.split(".")
        if parts and parts[-1].isdigit() and edition_key(document_id).startswith("phi"):
            sentence_number = int(parts.pop())
        passage = div_passage(root, parts)
    sentences = split_sentences(passage)
    if sentence_number and sentence_number <= len(sentences):
        passage = sentences[sentence_number - 1]
        level = "sentence"
    if not passage:
        return None
    return Translation(passage, urn, translator(root), level)


def passage_url(urn: str, citation: str) -> str:
    namespace = "greekLit" if urn.startswith("tlg") else "latinLit"
    return f"https://scaife.perseus.org/reader/urn:cts:{namespace}:{urn}:{citation}/"
