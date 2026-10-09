from __future__ import annotations

import argparse
import base64
import hashlib
import html
import json
import re
import shutil
import subprocess
import tempfile
import unicodedata
import urllib.request
import xml.etree.ElementTree as ET
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable

from scripts.classical_cloze.translations import (
    canonical_citation, edition_urn, fetch_translations, lookup, passage_url,
)


ANKI_URL = "http://127.0.0.1:8765"
SOURCE_DECK = "Greek and Latin"
DESTINATION_DECK = "Greek and Latin::Corpus Cloze"
MODEL_NAME = "Classical Corpus Cloze"
TRANSLATION_ROOT = Path("data/classical_cloze/translations")

GREEK_DOCS = {
    "tlg0003.tlg001": ("Thucydides", "Histories", "classical"),
    "tlg0011.tlg001": ("Sophocles", "Ajax", "classical"),
    "tlg0011.tlg002": ("Sophocles", "Electra", "classical"),
    "tlg0011.tlg003": ("Sophocles", "Oedipus Tyrannus", "classical"),
    "tlg0011.tlg004": ("Sophocles", "Antigone", "classical"),
    "tlg0011.tlg005": ("Sophocles", "Trachiniae", "classical"),
    "tlg0012.tlg001": ("Homer", "Iliad", "homeric"),
    "tlg0013.tlg002": ("Homer", "Hymn to Demeter", "homeric"),
    "tlg0016.tlg001": ("Herodotus", "Histories", "classical"),
    "tlg0020.tlg001": ("Aesop", "Fables", "classical"),
    "tlg0020.tlg002": ("Aesop", "Fables", "classical"),
    "tlg0020.tlg003": ("Aesop", "Fables", "classical"),
    "tlg0085.tlg001": ("Aeschylus", "Works", "classical"),
}

LATIN_DOCS = {
    "phi0448.phi001": ("Julius Caesar", "Gallic War"),
    "phi0474.phi013": ("Cicero", "In Catilinam"),
    "phi0620.phi001": ("Propertius", "Elegies"),
    "phi0631.phi001": ("Sallust", "Bellum Catilinae"),
    "phi0690.phi003": ("Virgil", "Aeneid"),
    "phi0959.phi006": ("Ovid", "Metamorphoses"),
    "phi0972.phi001": ("Petronius", "Satyricon"),
    "phi0975.phi001": ("Phaedrus", "Fables"),
    "phi1221.phi007": ("Suetonius", "Life of Augustus"),
    "phi1348.abo012": ("Tacitus", "Histories"),
    "phi1351.phi005": ("Augustus", "Res Gestae"),
}

GLAUX_WORKS = {
    "0059-001": ("Plato", "Euthyphro"),
    "0059-002": ("Plato", "Apology"),
    "0059-003": ("Plato", "Crito"),
    "0059-030": ("Plato", "Republic"),
    "0086-010": ("Aristotle", "Nicomachean Ethics"),
}

AGDT_UPOS = {
    "n": "NOUN", "v": "VERB", "a": "ADJ", "d": "ADV", "l": "DET",
    "g": "PART", "c": "CCONJ", "r": "ADP", "p": "PRON", "m": "NUM",
    "i": "INTJ", "e": "INTJ", "u": "PUNCT", "x": "X",
}

FEATURE_ORDER = (
    "Degree", "Tense", "Aspect", "Voice", "Mood", "VerbForm", "Person",
    "Case", "Number", "Gender",
)
FEATURE_WORDS = {
    "Nom": "nominative", "Acc": "accusative", "Gen": "genitive",
    "Dat": "dative", "Voc": "vocative", "Abl": "ablative",
    "Sing": "singular", "Plur": "plural", "Dual": "dual",
    "Masc": "masculine", "Fem": "feminine", "Neut": "neuter",
    "Pres": "present", "Past": "past", "Fut": "future",
    "Perf": "perfect", "Imp": "imperative", "Ind": "indicative",
    "Sub": "subjunctive", "Opt": "optative", "Inf": "infinitive",
    "Part": "participle", "Fin": "finite", "Act": "active",
    "Pass": "passive", "Mid": "middle", "1": "first-person",
    "2": "second-person", "3": "third-person", "Pos": "positive",
    "Cmp": "comparative", "Sup": "superlative", "Abs": "absolute",
}


@dataclass(frozen=True)
class Token:
    form: str
    lemma: str
    upos: str
    features: dict[str, str]
    space_after: bool = True


@dataclass(frozen=True)
class Sentence:
    language: str
    author: str
    work: str
    period: str
    corpus: str
    document_id: str
    sentence_id: str
    text: str
    tokens: tuple[Token, ...]


@dataclass
class Candidate:
    sentence: Sentence
    start: int
    end: int
    match_type: str
    score: tuple[Any, ...] = field(default_factory=tuple)

    @property
    def target(self) -> str:
        return render_tokens(self.sentence.tokens[self.start:self.end])


def anki(action: str, **params: Any) -> Any:
    payload = json.dumps({"action": action, "version": 6, "params": params}).encode()
    request = urllib.request.Request(
        ANKI_URL, data=payload, headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(request, timeout=120) as response:
        result = json.load(response)
    if result.get("error"):
        raise RuntimeError(f"AnkiConnect {action}: {result['error']}")
    return result["result"]


def strip_html(value: str) -> str:
    value = re.sub(r"\[sound:[^]]+]", "", value)
    value = re.sub(r"<[^>]+>", " ", value)
    return html.unescape(value).strip()


def is_greek(value: str) -> bool:
    return any("GREEK" in unicodedata.name(char, "") for char in value)


def normalize(value: str, language: str, *, loose: bool = False) -> str:
    value = unicodedata.normalize("NFC", strip_html(value)).casefold().strip()
    value = re.sub(r"\s+", " ", value)
    if language == "latin":
        value = value.replace("j", "i").replace("v", "u")
    if loose:
        value = "".join(
            char for char in unicodedata.normalize("NFD", value)
            if unicodedata.category(char) != "Mn"
        )
    return unicodedata.normalize("NFC", value)


def parse_features(raw: str) -> dict[str, str]:
    if raw == "_":
        return {}
    return dict(item.split("=", 1) for item in raw.split("|") if "=" in item)


def render_tokens(tokens: Iterable[Token]) -> str:
    output = ""
    for token in tokens:
        output += token.form
        if token.space_after:
            output += " "
    return output.rstrip()


def metadata_for(
    language: str, corpus: str, metadata: dict[str, str]
) -> tuple[str, str, str] | None:
    document = metadata.get("newdoc id", "")
    if corpus == "UD_Ancient_Greek-Perseus":
        for prefix, result in GREEK_DOCS.items():
            if document.startswith(prefix):
                return result
        return None
    if corpus == "UD_Latin-Perseus":
        for prefix, (author, work) in LATIN_DOCS.items():
            if document.startswith(prefix):
                return author, work, "classical"
        return None
    source = metadata.get("source", "")
    if corpus == "UD_Ancient_Greek-PROIEL" and source.startswith("Histories"):
        return "Herodotus", "Histories", "classical"
    if corpus == "UD_Latin-PROIEL":
        if source.startswith("Caesar's Gallic War"):
            return "Julius Caesar", "Gallic War", "classical"
        if source.startswith("De officiis"):
            return "Cicero", "De Officiis", "classical"
        if source.startswith("Epistulae ad Atticum"):
            return "Cicero", "Letters to Atticus", "classical"
    return None


def read_conllu(path: Path, language: str) -> Iterable[Sentence]:
    metadata: dict[str, str] = {}
    tokens: list[Token] = []
    corpus = path.parent.name

    def finish() -> Sentence | None:
        if not tokens:
            return None
        source = metadata_for(language, corpus, metadata)
        if source is None:
            return None
        author, work, period = source
        text = metadata.get("text") or render_tokens(tokens)
        return Sentence(
            language=language,
            author=author,
            work=work,
            period=period,
            corpus=corpus,
            document_id=metadata.get("newdoc id", metadata.get("source", "")),
            sentence_id=metadata.get("sent_id", ""),
            text=text,
            tokens=tuple(tokens),
        )

    with path.open(encoding="utf-8") as handle:
        for raw_line in handle:
            line = raw_line.rstrip("\n")
            if not line:
                sentence = finish()
                if sentence:
                    yield sentence
                metadata = {
                    key: value for key, value in metadata.items() if key == "newdoc id"
                }
                tokens = []
                continue
            if line.startswith("# ") and " = " in line:
                key, value = line[2:].split(" = ", 1)
                metadata[key] = value
                continue
            columns = line.split("\t")
            if len(columns) != 10 or not columns[0].isdigit():
                continue
            misc = columns[9]
            tokens.append(Token(
                form=columns[1], lemma=columns[2], upos=columns[3],
                features=parse_features(columns[5]),
                space_after="SpaceAfter=No" not in misc,
            ))
    sentence = finish()
    if sentence:
        yield sentence


def load_corpus(root: Path) -> list[Sentence]:
    sentences: list[Sentence] = []
    for path in sorted(root.glob("**/*.conllu")):
        language = "greek" if path.name.startswith("grc_") else "latin"
        sentences.extend(read_conllu(path, language))
    for path in sorted((root / "GLAUx" / "xml").glob("*.xml")):
        sentences.extend(read_glaux(path))
    latin_json = root / "canonical-latinLit" / "annotated.json"
    if latin_json.exists():
        sentences.extend(read_annotated_json(latin_json))
    return sentences


def agdt_features(postag: str) -> dict[str, str]:
    if len(postag) < 9:
        return {}
    names = (None, "Person", "Number", "Tense", "Mood", "Voice", "Gender", "Case", "Degree")
    values = {
        "Person": {"1": "1", "2": "2", "3": "3"},
        "Number": {"s": "Sing", "p": "Plur", "d": "Dual"},
        "Tense": {"p": "Pres", "i": "Past", "f": "Fut", "a": "Past", "r": "Past", "l": "Past"},
        "Mood": {"i": "Ind", "s": "Sub", "o": "Opt", "m": "Imp", "n": "Inf", "p": "Part"},
        "Voice": {"a": "Act", "m": "Mid", "p": "Pass", "e": "Mid"},
        "Gender": {"m": "Masc", "f": "Fem", "n": "Neut"},
        "Case": {"n": "Nom", "g": "Gen", "d": "Dat", "a": "Acc", "v": "Voc", "b": "Abl"},
        "Degree": {"p": "Pos", "c": "Cmp", "s": "Sup"},
    }
    result = {}
    for index, name in enumerate(names):
        if name and postag[index] in values[name]:
            result[name] = values[name][postag[index]]
    return result


def read_glaux(path: Path) -> Iterable[Sentence]:
    document_id = path.stem
    if document_id not in GLAUX_WORKS:
        return
    author, work = GLAUX_WORKS[document_id]
    for _, element in ET.iterparse(path, events=("end",)):
        if element.tag != "sentence":
            continue
        tokens = []
        reference = ""
        for word in element.findall("word"):
            if word.get("artificial"):
                continue
            form = unicodedata.normalize("NFC", word.get("form", ""))
            lemma = unicodedata.normalize("NFC", word.get("lemma", form))
            postag = word.get("postag", "x--------")
            if not reference:
                reference = (
                    word.get("div_stephanus_section")
                    or word.get("div_perseus_section")
                    or word.get("div_bekker_page")
                    or word.get("div_book")
                    or ""
                )
            tokens.append(Token(
                form=form, lemma=lemma, upos=AGDT_UPOS.get(postag[0], "X"),
                features=agdt_features(postag),
                space_after=form not in {"(", "[", "“", "‘"},
            ))
        for index, token in enumerate(tokens):
            if token.upos == "PUNCT" and index:
                previous = tokens[index - 1]
                tokens[index - 1] = Token(previous.form, previous.lemma, previous.upos, previous.features, False)
        if tokens:
            yield Sentence(
                "greek", author, work, "classical", "GLAUx-v2.1",
                document_id, reference or element.get("id", ""), render_tokens(tokens), tuple(tokens),
            )
        element.clear()


def read_annotated_json(path: Path) -> Iterable[Sentence]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    for item in payload["sentences"]:
        yield Sentence(
            language="latin", author=item["author"], work=item["work"],
            period="classical", corpus=payload["corpus"],
            document_id=item["document_id"], sentence_id=item["citation"],
            text=item["text"],
            tokens=tuple(Token(
                form=token["form"], lemma=token["lemma"], upos=token["upos"],
                features=token["features"], space_after=token["space_after"],
            ) for token in item["tokens"]),
        )


def build_indexes(sentences: Iterable[Sentence]) -> tuple[dict, dict]:
    lemma_index: dict[tuple[str, str], list[tuple[Sentence, int]]] = defaultdict(list)
    form_index: dict[tuple[str, str], list[tuple[Sentence, int]]] = defaultdict(list)
    for sentence in sentences:
        for index, token in enumerate(sentence.tokens):
            lemma_index[(sentence.language, normalize(token.lemma, sentence.language, loose=True))].append((sentence, index))
            form_index[(sentence.language, normalize(token.form, sentence.language, loose=True))].append((sentence, index))
    return lemma_index, form_index


def author_priority(sentence: Sentence) -> int:
    if sentence.language == "greek":
        return {"Homer": 0, "Plato": 1, "Aristotle": 2}.get(sentence.author, 3)
    return {"Virgil": 0, "Julius Caesar": 1, "Livy": 2}.get(sentence.author, 3)


def rank(candidate: Candidate) -> tuple[Any, ...]:
    sentence = candidate.sentence
    word_count = sum(token.upos != "PUNCT" for token in sentence.tokens)
    proper_names = sum(token.upos == "PROPN" for token in sentence.tokens)
    period_priority = 0 if sentence.period == "homeric" else 1
    preferred_length = 0 if 5 <= word_count <= 20 else 1 if word_count <= 30 else 2
    distance = abs(word_count - 12)
    duplicate_targets = sum(
        normalize(token.lemma, sentence.language, loose=True)
        == normalize(sentence.tokens[candidate.start].lemma, sentence.language, loose=True)
        for token in sentence.tokens
    )
    return (
        period_priority if sentence.language == "greek" else 0,
        author_priority(sentence), preferred_length, duplicate_targets != 1,
        proper_names, distance, word_count, sentence.sentence_id,
    )


def source_terms(front: str, language: str) -> list[str]:
    cleaned = normalize(front, language)
    # Imported dictionary records consistently put the headword before the
    # first comma. Taking that explicit field is safer than interpreting a
    # principal-parts list as an attested phrase.
    cleaned = re.sub(r"(?<=\w)\s*\d+$", "", cleaned)
    if re.search(r"[,،]", cleaned):
        cleaned = re.split(r"[,،]", cleaned, maxsplit=1)[0]
    cleaned = re.sub(
        r"\s*\((?:adv|adu|adj|noun|verb|uerb|pron|prep|conj|interj|\d+)\.?(?:\s+\d+)?\)\s*$",
        "", cleaned, flags=re.IGNORECASE,
    )
    punctuation = " \t\r\n,،.;:!?··()[]{}<>\"“”‘’"
    return [term for raw in cleaned.split() if (term := raw.strip(punctuation))]


def literal_terms(front: str, language: str) -> list[str]:
    cleaned = normalize(front, language)
    punctuation = " \t\r\n,،.;:!?··()[]{}<>\"“”‘’"
    return [term for raw in cleaned.split() if (term := raw.strip(punctuation))]


def phrase_candidates(terms: list[str], language: str, form_index: dict) -> list[Candidate]:
    wanted = [normalize(term, language, loose=True) for term in terms]
    if len(wanted) < 2:
        return []
    candidates: list[Candidate] = []
    seen: set[tuple[str, int]] = set()
    for sentence, _ in form_index.get((language, wanted[0]), []):
        lexical = [
            (index, normalize(token.form, language, loose=True))
            for index, token in enumerate(sentence.tokens) if token.upos != "PUNCT"
        ]
        forms = [form for _, form in lexical]
        for lexical_start in range(len(forms) - len(wanted) + 1):
            if forms[lexical_start:lexical_start + len(wanted)] != wanted:
                continue
            start = lexical[lexical_start][0]
            end = lexical[lexical_start + len(wanted) - 1][0] + 1
            key = (sentence.sentence_id, start)
            if key not in seen:
                seen.add(key)
                candidates.append(Candidate(sentence, start, end, "phrase"))
    return candidates


def unmatched_status(front: str, language: str) -> str:
    terms = source_terms(front, language)
    if not terms:
        return "nonlexical"
    if language == "greek" and not is_greek(front):
        return "nonlexical"
    if language == "latin" and is_greek(front):
        return "nonlexical"
    if len(terms) > 12:
        return "unmatched_long_text"
    if re.search(r"[()/\[\]]", front):
        return "annotation_unknown"
    if len(terms) >= 3 and re.search(r"\b(?:fut|aor|perf|ipf|plup|part|inf)\.?\b", front, re.IGNORECASE):
        return "paradigm"
    if len(terms) > 1:
        return "unmatched_phrase"
    return "unmatched"


def find_candidates(
    front: str,
    language: str,
    lemma_index: dict,
    form_index: dict,
) -> tuple[list[Candidate], str | None]:
    terms = source_terms(front, language)
    if not terms:
        return [], "empty"
    candidates: list[Candidate] = []
    raw_terms = literal_terms(front, language)
    if raw_terms != terms:
        candidates = phrase_candidates(raw_terms, language, form_index)
    if len(terms) == 1:
        key = (language, normalize(terms[0], language, loose=True))
        lemma_hits = lemma_index.get(key, [])
        form_hits = form_index.get(key, [])
        hits = lemma_hits or form_hits
        match_type = "lemma" if lemma_hits else "surface"
        if not candidates:
            for sentence, index in hits:
                candidates.append(Candidate(sentence, index, index + 1, match_type))
    else:
        wanted = [normalize(term, language, loose=True) for term in terms]
        if not candidates:
            candidates = phrase_candidates(terms, language, form_index)
        if not candidates:
            first_key = (language, wanted[0])
            lemma_hits = lemma_index.get(first_key, [])
            same_lemma = bool(lemma_hits)
            for term in wanted[1:]:
                analyses = {
                    normalize(sentence.tokens[index].lemma, language, loose=True)
                    for sentence, index in form_index.get((language, term), [])
                }
                if wanted[0] not in analyses:
                    same_lemma = False
                    break
            if same_lemma:
                for sentence, index in lemma_hits:
                    candidates.append(Candidate(sentence, index, index + 1, "principal_parts"))
    if not candidates:
        return [], "unmatched"
    for candidate in candidates:
        candidate.score = rank(candidate)
    candidates.sort(key=lambda item: item.score)
    return candidates, None


def morphology(candidate: Candidate) -> str:
    parts: list[str] = []
    for token in candidate.sentence.tokens[candidate.start:candidate.end]:
        features = [
            FEATURE_WORDS.get(token.features[key], token.features[key].lower())
            for key in FEATURE_ORDER if key in token.features
        ]
        description = token.upos.lower()
        if features:
            description += ", " + " ".join(features)
        parts.append(f"{token.form} - {token.lemma}; {description}")
    return "<br>".join(html.escape(part) for part in parts)


def cloze_text(candidate: Candidate) -> str:
    before = render_tokens(candidate.sentence.tokens[:candidate.start])
    target = render_tokens(candidate.sentence.tokens[candidate.start:candidate.end])
    after = render_tokens(candidate.sentence.tokens[candidate.end:])
    if before and candidate.sentence.tokens[candidate.start - 1].space_after:
        before += " "
    if after and candidate.sentence.tokens[candidate.end - 1].space_after:
        target += " "
    return f"{before}{{{{c1::{target.rstrip()}}}}}{' ' if target.endswith(' ') else ''}{after}"


def citation(candidate: Candidate) -> str:
    sentence = candidate.sentence
    if sentence.document_id and not sentence.document_id.startswith(("tlg", "phi")):
        reference = f"{sentence.document_id}; sentence {sentence.sentence_id}"
    else:
        reference = sentence.sentence_id or sentence.document_id
    return f"{sentence.author}, <i>{sentence.work}</i>, {html.escape(reference)} ({sentence.corpus})"


def linked_citation(candidate: Candidate, translation_urn: str | None = None) -> str:
    rendered = citation(candidate)
    urn = translation_urn or edition_urn(candidate.sentence.document_id)
    if not urn:
        return rendered
    reference = canonical_citation(
        candidate.sentence.document_id, candidate.sentence.sentence_id
    )
    url = passage_url(urn, reference)
    return f'{rendered} · <a href="{html.escape(url, quote=True)}">Open passage in Perseus</a>'


def candidate_json(candidate: Candidate) -> dict[str, Any]:
    sentence = candidate.sentence
    return {
        "text": sentence.text,
        "target": candidate.target,
        "lemma": sentence.tokens[candidate.start].lemma,
        "morphology": morphology(candidate),
        "author": sentence.author,
        "work": sentence.work,
        "period": sentence.period,
        "corpus": sentence.corpus,
        "document_id": sentence.document_id,
        "sentence_id": sentence.sentence_id,
        "match_type": candidate.match_type,
        "score": list(candidate.score),
        "translation": None,
    }


def note_state(note_id: int) -> str:
    cards = anki("findCards", query=f"nid:{note_id}")
    info = anki("cardsInfo", cards=cards)
    return "suspended" if info and all(card["queue"] == -1 for card in info) else "active"


def export_source_notes() -> list[dict[str, Any]]:
    note_ids = anki("findNotes", query=f'deck:"{SOURCE_DECK}" note:Basic')
    notes = anki("notesInfo", notes=note_ids)
    cards = anki("findCards", query=f'deck:"{SOURCE_DECK}" note:Basic')
    card_info = anki("cardsInfo", cards=cards)
    states: dict[int, list[int]] = defaultdict(list)
    for card in card_info:
        states[card["note"]].append(card["queue"])
    output = []
    for note in notes:
        front = strip_html(note["fields"]["Front"]["value"])
        language = "greek" if "Greek" in note["tags"] or is_greek(front) else "latin"
        queues = states.get(note["noteId"], [])
        output.append({
            "note_id": note["noteId"],
            "front": front,
            "language": language,
            "original_tags": note["tags"],
            "original_state": "suspended" if queues and all(queue == -1 for queue in queues) else "active",
        })
    return output


def build_manifest(corpus_root: Path, pilot_per_language: int) -> dict[str, Any]:
    sentences = load_corpus(corpus_root)
    lemma_index, form_index = build_indexes(sentences)
    counts = defaultdict(int)
    entries: list[dict[str, Any]] = []
    for source in export_source_notes():
        candidates, reason = find_candidates(
            source["front"], source["language"], lemma_index, form_index
        )
        if not candidates:
            status = unmatched_status(source["front"], source["language"])
            entries.append({**source, "status": status if reason == "unmatched" else reason, "candidates": []})
            continue
        if counts[source["language"]] >= pilot_per_language:
            continue
        primary = candidates[0]
        alternatives = candidates[1:6]
        sentence = primary.sentence
        aligned = lookup(
            TRANSLATION_ROOT, sentence.document_id, sentence.sentence_id
        )
        tags = [
            "generated::corpus_cloze", f"language::{source['language']}",
            f"period::{sentence.period}",
            "author::" + re.sub(r"[^a-z0-9]+", "_", sentence.author.casefold()).strip("_"),
            "corpus_cloze::translation_" + (aligned.level if aligned else "missing"),
        ]
        entries.append({
            **source,
            "status": "matched",
            "text": cloze_text(primary),
            "translation": aligned.text if aligned else "",
            "translation_level": aligned.level if aligned else None,
            "translation_urn": aligned.urn if aligned else None,
            "citation_html": linked_citation(primary, aligned.urn if aligned else None)
            + (f"<br><small>English: {html.escape(aligned.translator)}, Perseus Digital Library ({aligned.level}-level alignment)</small>" if aligned else ""),
            "lemma": primary.sentence.tokens[primary.start].lemma,
            "morphology_html": morphology(primary),
            "tags": tags,
            "primary": candidate_json(primary),
            "alternatives": [candidate_json(item) for item in alternatives],
        })
        counts[source["language"]] += 1
    return {
        "schema_version": 1,
        "kind": "classical-cloze-pilot",
        "pilot_per_language": pilot_per_language,
        "corpus_sentence_count": len(sentences),
        "matched_counts": dict(counts),
        "entries": entries,
    }


def alternatives_html(alternatives: list[dict[str, Any]]) -> str:
    if not alternatives:
        return ""
    items = []
    for item in alternatives:
        source = f"{item['author']}, <i>{item['work']}</i>, {html.escape(item['sentence_id'])}"
        items.append(f"<li><span lang='{item['period']}'>{html.escape(item['text'])}</span><br><small>{source}</small></li>")
    return "<details><summary>Alternative examples</summary><ol>" + "".join(items) + "</ol></details>"


def ensure_model() -> None:
    anki("createDeck", deck=DESTINATION_DECK)
    if MODEL_NAME in anki("modelNames"):
        return
    front = "{{cloze:Text}}"
    back = """{{cloze:Text}}
<div class="translation">{{Translation}}</div>
<div class="details">{{Morphology}}<br>{{Citation}}</div>
{{Alternatives}}"""
    css = """.card { font-family: Arial, sans-serif; font-size: 20px; line-height: 1.5; text-align: center; }
.cloze { color: blue; font-weight: bold; }
.nightMode .cloze { color: lightblue; }
.translation { margin-top: 1rem; }
.details { color: #777; font-size: 15px; margin-top: 1rem; }
details { margin-top: 1rem; text-align: left; }
li { margin-bottom: .75rem; }"""
    anki(
        "createModel", modelName=MODEL_NAME,
        inOrderFields=["Text", "Translation", "Citation", "Alternatives", "Lemma", "Morphology", "OriginalNoteID"],
        css=css,
        isCloze=True,
        cardTemplates=[{"Name": "Cloze", "Front": front, "Back": back}],
    )


def synthesize(text: str, language: str, media_dir: Path) -> tuple[str, bytes]:
    executable = shutil.which("espeak-ng") or shutil.which("espeak")
    if not executable:
        raise RuntimeError("eSpeak NG is required (install espeak-ng)")
    voice = "grc" if language == "greek" else "la"
    digest = hashlib.sha256(f"{voice}\0{text}".encode()).hexdigest()[:20]
    filename = f"classical_cloze_{voice}_{digest}.wav"
    path = media_dir / filename
    subprocess.run([executable, "-v", voice, "-w", str(path), text], check=True)
    return filename, path.read_bytes()


def write_pilot(manifest_path: Path) -> None:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    ensure_model()
    with tempfile.TemporaryDirectory() as directory:
        media_dir = Path(directory)
        for entry in manifest["entries"]:
            if entry["status"] != "matched" or entry.get("generated_note_id"):
                continue
            plain_text = entry["primary"]["text"]
            filename, audio = synthesize(plain_text, entry["language"], media_dir)
            stored = anki("storeMediaFile", filename=filename, data=base64.b64encode(audio).decode())
            note_id = anki("addNote", note={
                "deckName": DESTINATION_DECK,
                "modelName": MODEL_NAME,
                "fields": {
                    "Text": f"{entry['text']}[sound:{stored}]",
                    "Translation": entry["translation"],
                    "Citation": entry["citation_html"],
                    "Alternatives": alternatives_html(entry["alternatives"]),
                    "Lemma": entry["lemma"],
                    "Morphology": entry["morphology_html"],
                    "OriginalNoteID": str(entry["note_id"]),
                },
                "options": {"allowDuplicate": False},
                "tags": entry["tags"],
            })
            cards = anki("findCards", query=f"nid:{note_id}")
            anki("suspend", cards=cards)
            entry["generated_note_id"] = note_id
            entry["audio_filename"] = stored
            manifest_path.write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )


def chunks(items: list[Any], size: int) -> Iterable[list[Any]]:
    for start in range(0, len(items), size):
        yield items[start:start + size]


def import_existing_replacements(manifest: dict[str, Any]) -> None:
    existing_ids = anki("findNotes", query=f'deck:"{DESTINATION_DECK}"')
    if not existing_ids:
        return
    by_original = {}
    for note in anki("notesInfo", notes=existing_ids):
        original = note["fields"].get("OriginalNoteID", {}).get("value", "")
        if original.isdigit():
            by_original[int(original)] = note
    for entry in manifest["entries"]:
        note = by_original.get(entry["note_id"])
        if note is None:
            continue
        entry["generated_note_id"] = note["noteId"]
        sound = re.search(r"\[sound:([^]]+)\]", note["fields"]["Text"]["value"])
        if sound:
            entry["audio_filename"] = sound.group(1)
            if "_mms_" in sound.group(1):
                entry["audio_provider"] = MODEL_IDS_FOR_LANGUAGE[entry["language"]]
                entry["audio_tempo"] = 0.9


def update_existing_replacements(manifest: dict[str, Any]) -> None:
    entries = [
        entry for entry in manifest["entries"]
        if entry.get("generated_note_id") and entry["status"] == "matched"
    ]
    for batch in chunks(entries, 100):
        actions = [{
            "action": "updateNoteFields", "version": 6,
            "params": {"note": {
                "id": entry["generated_note_id"],
                "fields": {
                    "Translation": entry["translation"],
                    "Citation": entry["citation_html"],
                },
            }},
        } for entry in batch]
        anki("multi", actions=actions)
        note_ids = [entry["generated_note_id"] for entry in batch]
        anki("removeTags", notes=note_ids, tags="corpus_cloze::translation_missing corpus_cloze::translation_passage corpus_cloze::translation_sentence")
        for level in ("missing", "passage", "sentence"):
            ids = [
                entry["generated_note_id"] for entry in batch
                if (entry.get("translation_level") or "missing") == level
            ]
            if ids:
                anki("addTags", notes=ids, tags=f"corpus_cloze::translation_{level}")


MODEL_IDS_FOR_LANGUAGE = {
    "greek": "facebook/mms-tts-grc",
    "latin": "facebook/mms-tts-lat",
}


def note_payload(entry: dict[str, Any]) -> dict[str, Any]:
    return {
        "deckName": DESTINATION_DECK,
        "modelName": MODEL_NAME,
        "fields": {
            "Text": entry["text"],
            "Translation": entry["translation"],
            "Citation": entry["citation_html"],
            "Alternatives": alternatives_html(entry["alternatives"]),
            "Lemma": entry["lemma"],
            "Morphology": entry["morphology_html"],
            "OriginalNoteID": str(entry["note_id"]),
        },
        "options": {"allowDuplicate": True},
        "tags": entry["tags"],
    }


def write_migration(manifest_path: Path) -> None:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    ensure_model()
    import_existing_replacements(manifest)
    update_existing_replacements(manifest)
    pending = [
        entry for entry in manifest["entries"]
        if entry["status"] == "matched" and not entry.get("generated_note_id")
    ]
    for batch in chunks(pending, 100):
        note_ids = anki("addNotes", notes=[note_payload(entry) for entry in batch])
        if any(note_id is None for note_id in note_ids):
            raise RuntimeError("Anki rejected one or more migration notes")
        actions = [
            {"action": "findCards", "version": 6, "params": {"query": f"nid:{note_id}"}}
            for note_id in note_ids
        ]
        card_results = anki("multi", actions=actions)
        cards = [card for result in card_results for card in result["result"]]
        anki("suspend", cards=cards)
        for entry, note_id in zip(batch, note_ids, strict=True):
            entry["generated_note_id"] = note_id
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )


def cards_for_notes(note_ids: list[int]) -> list[int]:
    actions = [
        {"action": "findCards", "version": 6, "params": {"query": f"nid:{note_id}"}}
        for note_id in note_ids
    ]
    results = anki("multi", actions=actions)
    return [card for result in results for card in result["result"]]


def commit_migration(manifest_path: Path) -> None:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    completed = [
        entry for entry in manifest["entries"]
        if entry["status"] == "matched" and entry.get("generated_note_id")
        and entry.get("audio_provider") == MODEL_IDS_FOR_LANGUAGE[entry["language"]]
    ]
    if len(completed) != sum(manifest["matched_counts"].values()):
        raise RuntimeError("refusing to commit: not every matched note has MMS audio")
    for batch in chunks(completed, 100):
        original_ids = [entry["note_id"] for entry in batch]
        generated_active = [
            entry["generated_note_id"] for entry in batch if entry["original_state"] == "active"
        ]
        anki("addTags", notes=original_ids, tags="corpus_cloze::replaced")
        anki("suspend", cards=cards_for_notes(original_ids))
        if generated_active:
            anki("unsuspend", cards=cards_for_notes(generated_active))
    manifest["migration_committed"] = True
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def tag_unmatched(manifest_path: Path) -> None:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for entry in manifest["entries"]:
        if entry["status"] != "matched":
            anki("addTags", notes=[entry["note_id"]], tags=f"corpus_cloze::{entry['status']}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    build = subparsers.add_parser("build-manifest", help="read Anki and create a read-only pilot manifest")
    build.add_argument("--corpora", type=Path, default=Path("data/classical_cloze/corpora"))
    build.add_argument("--output", type=Path, default=Path("data/classical_cloze/pilot.json"))
    build.add_argument("--pilot-per-language", type=int, default=50)
    write = subparsers.add_parser("write-pilot", help="create suspended notes and audio from a manifest")
    write.add_argument("manifest", type=Path)
    tags = subparsers.add_parser("tag-unmatched", help="tag source notes that the manifest could not match")
    tags.add_argument("manifest", type=Path)
    migration = subparsers.add_parser("write-migration", help="idempotently create all matched notes suspended")
    migration.add_argument("manifest", type=Path)
    commit = subparsers.add_parser("commit-migration", help="activate replacements and suspend completed originals")
    subparsers.add_parser("fetch-translations", help="download canonical Perseus English editions")
    commit.add_argument("manifest", type=Path)
    args = parser.parse_args()
    if args.command == "build-manifest":
        manifest = build_manifest(args.corpora, args.pilot_per_language)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({"output": str(args.output), "matched_counts": manifest["matched_counts"]}))
    elif args.command == "write-pilot":
        write_pilot(args.manifest)
    elif args.command == "write-migration":
        write_migration(args.manifest)
    elif args.command == "commit-migration":
        commit_migration(args.manifest)
    elif args.command == "fetch-translations":
        print(json.dumps({"downloaded": fetch_translations(TRANSLATION_ROOT), "directory": str(TRANSLATION_ROOT)}))
    else:
        tag_unmatched(args.manifest)


if __name__ == "__main__":
    main()
