from __future__ import annotations

import argparse
import html
import json
import re
import unicodedata
import xml.etree.ElementTree as ET
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable

from flashcards.adapters import AnkiConnect

from scripts.classical_cloze.translations import (
    canonical_citation, canonical_treebank_reference, edition_urn,
    fetch_translations, lookup, passage_url,
)


ANKI_URL = "http://127.0.0.1:8765"
_ANKI = AnkiConnect(ANKI_URL)
SOURCE_DECK = "Greek and Latin"
DESTINATION_DECK = "Greek and Latin::Corpus Cloze"
UNMATCHED_DECK = "Greek and Latin::Unmatched Originals"
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
    "0001-001": ("Apollonius Rhodius", "Argonautica"),
    "0004-001": ("Diogenes Laertius", "Lives of Eminent Philosophers"),
    "0005-001": ("Theocritus", "Idylls"),
    "0008-001": ("Athenaeus", "Deipnosophistae"),
    "0012-001": ("Homer", "Iliad"),
    "0012-002": ("Homer", "Odyssey"),
    "0081-001": ("Dionysius of Halicarnassus", "Roman Antiquities"),
    "0099-001": ("Strabo", "Geography"),
    "0543-001": ("Polybius", "Histories"),
    "0542-001": ("Julius Pollux", "Onomasticon"),
    "0545-001": ("Claudius Aelianus", "On the Nature of Animals"),
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
    aliases: tuple[str, ...] = ()


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
    translation: str | None = None


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
    return _ANKI.invoke(action, **params)


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
            sentence_id=(
                canonical_treebank_reference(
                    TRANSLATION_ROOT, metadata.get("sent_id", "")
                ) or metadata.get("sent_id", "")
            ),
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
    glaux_online = root / "glaux-online.json"
    if glaux_online.exists():
        sentences.extend(read_glaux_online(glaux_online))
    glaux_homer_fuzzy = root / "glaux-homer-fuzzy.json"
    if glaux_homer_fuzzy.exists():
        sentences.extend(read_glaux_online(glaux_homer_fuzzy))
    tesserae_online = root / "tesserae-online.json"
    if tesserae_online.exists():
        sentences.extend(read_tesserae_online(tesserae_online))
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
        line_references = []
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
            if word.get("line"):
                line_references.append(word.get("line", ""))
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
            if line_references:
                reference = line_references[0]
                if line_references[-1] != reference:
                    reference += "-" + line_references[-1]
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


def read_glaux_online(path: Path) -> Iterable[Sentence]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    corpus_name = (
        "GLAUx-Homer-fuzzy"
        if payload.get("kind") == "glaux-homer-fuzzy" else "GLAUx-online"
    )
    seen = set()
    for result in payload["results"].values():
        aliases_by_id = {
            match["glaux_id"]: (result["searched"],)
            for example in result["examples"]
            for match in example.get("matches", [])
            if result.get("searched")
        }
        for example in result["examples"]:
            key = example["sentence_id"]
            if key in seen or not example.get("tokens"):
                continue
            seen.add(key)
            tokens = []
            for item in example["tokens"]:
                form = item["form"]
                tokens.append(Token(
                    form=form,
                    lemma=item["lemma"],
                    upos=AGDT_UPOS.get(item["postag"][0], "X"),
                    features=agdt_features(item["postag"]),
                    space_after=True,
                    aliases=aliases_by_id.get(item["glaux_id"], ()),
                ))
            for index, token in enumerate(tokens):
                if token.upos == "PUNCT" and index:
                    previous = tokens[index - 1]
                    tokens[index - 1] = Token(
                        previous.form, previous.lemma, previous.upos,
                        previous.features, False, previous.aliases,
                    )
            yield Sentence(
                language="greek",
                author=example["author"],
                work=example["work"],
                period="ancient",
                corpus=corpus_name,
                document_id=f"glaux:{example['author']}:{example['work']}",
                sentence_id=str(example["sentence_id"]),
                text=example["text"],
                tokens=tuple(tokens),
                translation=example.get("translation"),
            )


def read_tesserae_online(path: Path) -> Iterable[Sentence]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    seen = set()
    for result in payload["results"].values():
        query = result["query"]
        for example in result["examples"]:
            text = example["text"]
            if len(text.split()) > 50:
                continue
            key = (example["text_id"], example["locus"], text)
            if key in seen:
                continue
            seen.add(key)
            forms = example.get("tokens") or re.findall(r"[^\W\d_]+|[^\w\s]", text)
            highlighted = set(example.get("highlight_indices", []))
            tokens = []
            for index, form in enumerate(forms):
                is_match = index in highlighted or (
                    result["search_field"] == "word"
                    and normalize(form, "latin", loose=True)
                    == normalize(query, "latin", loose=True)
                )
                tokens.append(Token(
                    form=form,
                    lemma=query if is_match else form,
                    upos="X" if form.isalpha() else "PUNCT",
                    features={},
                    space_after=True,
                ))
            for index, token in enumerate(tokens):
                if token.upos == "PUNCT" and index:
                    previous = tokens[index - 1]
                    tokens[index - 1] = Token(
                        previous.form, previous.lemma, previous.upos,
                        previous.features, False,
                    )
            yield Sentence(
                language="latin",
                author=example["author"],
                work=example.get("title") or example.get("work", ""),
                period=example.get("era", "ancient").casefold().replace(" ", "_"),
                corpus="Tesserae-online",
                document_id=example["text_id"],
                sentence_id=example["locus"],
                text=text,
                tokens=tuple(tokens),
            )


def build_indexes(sentences: Iterable[Sentence]) -> tuple[dict, dict]:
    lemma_index: dict[tuple[str, str], list[tuple[Sentence, int]]] = defaultdict(list)
    form_index: dict[tuple[str, str], list[tuple[Sentence, int]]] = defaultdict(list)
    for sentence in sentences:
        for index, token in enumerate(sentence.tokens):
            lemma_index[(sentence.language, normalize(token.lemma, sentence.language, loose=True))].append((sentence, index))
            for alias in token.aliases:
                lemma_index[(sentence.language, normalize(alias, sentence.language, loose=True))].append((sentence, index))
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
    if language == "greek":
        greek = r"\u0370-\u03ff\u1f00-\u1fff"
        # Dictionary headwords may show their derivational boundary with a
        # hyphen (e.g. δια-λέγομαι); it is not part of the lexical form.
        cleaned = re.sub(
            rf"(?<=[{greek}])[-‐‑](?=[{greek}])", "", cleaned
        )
        cleaned = re.sub(
            r"^(?:mid|pass|act|part|perf|aor|fut|comp)\.?\s+",
            "", cleaned, flags=re.IGNORECASE,
        )
        cleaned = re.sub(
            r"\((?:[a-z]|[^)]*[a-z][^)]*)\)", " ", cleaned,
            flags=re.IGNORECASE,
        )
        if re.match(rf"^[{greek}]{{1,2}}\.\s", cleaned):
            return []
        first, separator, remainder = cleaned.partition(" ")
        if separator and (
            re.match(r"(?:1st|2nd|3rd)\b", remainder, re.IGNORECASE)
            or re.match(
                r"\b(?:fut|aor|aor2|perf|plup|ipf|pres|imper|inf|part|opt|mid|pass)\.?\b",
                remainder, re.IGNORECASE,
            )
            or (
                "," in remainder[:80]
                and re.search(
                    r"\b(?:fut|aor|aor2|perf|plup|ipf|pres|imper|inf|part|opt|mid|pass)\.?\b",
                    remainder[:80], re.IGNORECASE,
                )
            )
            or "[select]" in remainder
        ):
            cleaned = first
        elif len(first) >= 3 and re.search(r"\s{2,}", front) and re.search(
            r"(?:ω|ομαι|μι|μαι|ος|ον|η|α|ις|εύς|οίξ)$", first
        ):
            cleaned = cleaned.split(maxsplit=1)[0]
        else:
            english = re.search(r"\s+(?=[a-z])", cleaned, flags=re.IGNORECASE)
            if english and not re.search(rf"[{greek}]", cleaned[english.end():]):
                cleaned = cleaned[:english.start()]
    else:
        if cleaned.startswith("--"):
            return []
        english_words = re.findall(r"[a-z]+", cleaned)
        english_markers = {
            "and", "are", "before", "for", "from", "that", "the", "then",
            "this", "to", "was", "were", "when", "with",
        }
        if sum(word in english_markers for word in english_words) >= 2:
            return []
        cleaned = re.sub(
            r"\s*\((?:adj|adv|conj|prep|pron|reflex\.? pron|defective verb|"
            r"indeclinable adj|pl\.[^)]*|[+ ]*(?:acc|abl|dat|gen|nom)\.?(?:,?\s*(?:acc|abl|dat|gen|nom)\.?)*)\)\s*$",
            "", cleaned, flags=re.IGNORECASE,
        )
        if "/" in cleaned and " " not in cleaned.split("/", 1)[0]:
            cleaned = cleaned.split("/", 1)[0]
        latin_terms = re.findall(r"[a-zā-ž]+", cleaned, re.IGNORECASE)
        if latin_terms:
            first = latin_terms[0]
            personal_paradigm = (
                len(latin_terms) == 6
                and len({term[:2] for term in latin_terms}) <= 2
            )
            principal_parts = (
                len(latin_terms) >= 2
                and re.search(r"(?:o|or)$", first)
                and (
                    re.search(r"(?:re|ri|are|ēre|ere|ire|ari|eri|i)$", latin_terms[1])
                    or len(latin_terms) >= 3
                )
            )
            dictionary_gender = (
                len(latin_terms) >= 2
                and latin_terms[-1] in {"m", "f", "n", "mf", "mn"}
            )
            adjective_forms = (
                len(latin_terms) == 3
                and latin_terms[1].endswith("is")
                and latin_terms[2].endswith(("e", "a", "um"))
            )
            if principal_parts or personal_paradigm or dictionary_gender or adjective_forms:
                cleaned = first
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
    if sentence.corpus.startswith("GLAUx-"):
        return (
            f"{html.escape(sentence.author)}, <i>{html.escape(sentence.work)}</i>, "
            f'<a href="https://glaux.be/detail.php?id={sentence.sentence_id}">'
            f"GLAUx sentence {sentence.sentence_id}</a>"
        )
    if sentence.document_id and not sentence.document_id.startswith(("tlg", "phi")):
        reference = f"{sentence.document_id}; sentence {sentence.sentence_id}"
    else:
        reference = sentence.sentence_id or sentence.document_id
    return f"{sentence.author}, <i>{sentence.work}</i>, {html.escape(reference)} ({sentence.corpus})"


def linked_citation(candidate: Candidate, translation_urn: str | None = None) -> str:
    rendered = citation(candidate)
    if candidate.sentence.corpus.startswith("GLAUx-"):
        return rendered
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
        "translation": sentence.translation,
    }


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
            "gloss_html": note["fields"]["Back"]["value"],
            "language": language,
            "original_tags": note["tags"],
            "original_state": "suspended" if queues and all(queue == -1 for queue in queues) else "active",
        })
    return output


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
    front = "{{cloze:Text}}"
    back = """{{cloze:Text}}
<div class="gloss">{{Gloss}}</div>
<div class="translation">{{Translation}}</div>
<div class="details">{{Morphology}}<br>{{Citation}}</div>
{{Alternatives}}"""
    css = """.card { font-family: Arial, sans-serif; font-size: 20px; line-height: 1.5; text-align: center; }
.cloze { color: blue; font-weight: bold; }
.nightMode .cloze { color: lightblue; }
.gloss { margin-top: 1rem; font-weight: bold; }
.translation { margin-top: 1rem; }
.details { color: #777; font-size: 15px; margin-top: 1rem; }
details { margin-top: 1rem; text-align: left; }
li { margin-bottom: .75rem; }"""
    if MODEL_NAME not in anki("modelNames"):
        anki(
            "createModel", modelName=MODEL_NAME,
            inOrderFields=["Text", "Gloss", "Translation", "Citation", "Alternatives", "Lemma", "Morphology", "OriginalNoteID"],
            css=css,
            isCloze=True,
            cardTemplates=[{"Name": "Cloze", "Front": front, "Back": back}],
        )
        return
    if "Gloss" not in anki("modelFieldNames", modelName=MODEL_NAME):
        anki("modelFieldAdd", modelName=MODEL_NAME, fieldName="Gloss", index=1)
    anki("updateModelTemplates", model={
        "name": MODEL_NAME,
        "templates": {"Cloze": {"Front": front, "Back": back}},
    })
    anki("updateModelStyling", model={"name": MODEL_NAME, "css": css})


def chunks(items: list[Any], size: int) -> Iterable[list[Any]]:
    for start in range(0, len(items), size):
        yield items[start:start + size]


def batched_note_info(note_ids: list[int]) -> list[dict[str, Any]]:
    return [
        note for batch in chunks(note_ids, 500)
        for note in anki("notesInfo", notes=batch)
    ]


def cards_for_notes(note_ids: list[int]) -> list[int]:
    actions = [
        {"action": "findCards", "version": 6, "params": {"query": f"nid:{note_id}"}}
        for note_id in note_ids
    ]
    results = anki("multi", actions=actions)
    return [card for result in results for card in result["result"]]


CORPUS_ROOT = Path("data/classical_cloze/corpora")
ARCHIVE_PATH = Path("data/classical_cloze/archive/migrated-originals.json")


def replacement_note(source: dict[str, Any], candidates: list[Candidate]) -> dict[str, Any]:
    primary, alternatives = candidates[0], candidates[1:6]
    sentence = primary.sentence
    aligned = lookup(TRANSLATION_ROOT, sentence.document_id, sentence.sentence_id)
    level = aligned.level if aligned else "passage" if sentence.translation else None
    gloss = source["gloss_html"]
    if sentence.corpus == "GLAUx-Homer-fuzzy":
        gloss = f"<small><b>Original note (may not describe selected target):</b> {gloss}</small>"
    citation_html = linked_citation(primary, aligned.urn if aligned else None)
    if aligned:
        citation_html += (
            f"<br><small>English: {html.escape(aligned.translator)}, "
            f"Perseus Digital Library ({aligned.level}-level alignment)</small>"
        )
    return {
        "deckName": DESTINATION_DECK,
        "modelName": MODEL_NAME,
        "fields": {
            "Text": cloze_text(primary),
            "Gloss": gloss,
            "Translation": aligned.text if aligned else sentence.translation or "",
            "Citation": citation_html,
            "Alternatives": alternatives_html([candidate_json(item) for item in alternatives]),
            "Lemma": sentence.tokens[primary.start].lemma,
            "Morphology": morphology(primary),
            "OriginalNoteID": str(source["note_id"]),
        },
        "options": {"allowDuplicate": True},
        "tags": [
            "generated::corpus_cloze", f"language::{source['language']}",
            f"period::{sentence.period}",
            "author::" + re.sub(r"[^a-z0-9]+", "_", sentence.author.casefold()).strip("_"),
            "corpus_cloze::translation_" + (level or "missing"),
        ],
    }


def archive_originals(path: Path, note_ids: list[int]) -> None:
    archive = (
        json.loads(path.read_text(encoding="utf-8"))
        if path.exists() else {"schema_version": 1, "notes": [], "cards": []}
    )
    archived = {note["noteId"] for note in archive["notes"]}
    new_ids = [note_id for note_id in note_ids if note_id not in archived]
    archive["notes"].extend(batched_note_info(new_ids))
    for batch in chunks(new_ids, 100):
        archive["cards"].extend(anki("cardsInfo", cards=cards_for_notes(batch)))
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(archive, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)
    saved = {note["noteId"] for note in json.loads(path.read_text(encoding="utf-8"))["notes"]}
    if not set(note_ids) <= saved:
        raise RuntimeError("refusing to delete originals: archive verification failed")


def migrate(
    corpus_root: Path = CORPUS_ROOT,
    archive_path: Path = ARCHIVE_PATH,
    *,
    tempo: float = 0.8,
) -> dict[str, int]:
    """Bring the collection to its migrated state. Safe to run any number of times.

    Anki is the only state. An original `Basic` note under `Greek and Latin` is
    in one of three states, and each run advances it as far as it can:
    no replacement yet -> match it (create a suspended replacement, or move it to
    the unmatched deck); replacement without audio -> add audio; replacement with
    audio -> activate it if the original was active, archive the original, delete it.
    Existing replacements are never rewritten, so manual edits survive.
    """
    from scripts.classical_cloze.mms_audio import SOUND_RE, sync_audio

    ensure_model()
    sources = export_source_notes()
    replacements: dict[int, int] = {}
    for note in batched_note_info(anki("findNotes", query=f'deck:"{DESTINATION_DECK}"')):
        original = note["fields"].get("OriginalNoteID", {}).get("value", "")
        if original.isdigit():
            replacements[int(original)] = note["noteId"]

    pending = [source for source in sources if source["note_id"] not in replacements]
    create: list[tuple[dict[str, Any], dict[str, Any]]] = []
    unmatched: dict[str, list[int]] = defaultdict(list)
    if pending:
        lemma_index, form_index = build_indexes(load_corpus(corpus_root))
        already_unmatched = set(anki("findNotes", query=f'deck:"{UNMATCHED_DECK}" note:Basic'))
        for source in pending:
            candidates, reason = find_candidates(source["front"], source["language"], lemma_index, form_index)
            if candidates:
                create.append((source, replacement_note(source, candidates)))
            elif source["note_id"] not in already_unmatched:
                status = unmatched_status(source["front"], source["language"]) if reason == "unmatched" else reason
                unmatched[status].append(source["note_id"])

    for batch in chunks(create, 100):
        note_ids = anki("addNotes", notes=[payload for _, payload in batch])
        if any(note_id is None for note_id in note_ids):
            raise RuntimeError("Anki rejected one or more replacement notes")
        anki("suspend", cards=cards_for_notes(note_ids))
        for (source, _), note_id in zip(batch, note_ids, strict=True):
            replacements[source["note_id"]] = note_id

    if unmatched:
        anki("createDeck", deck=UNMATCHED_DECK)
    for status, ids in unmatched.items():
        anki("addTags", notes=ids, tags=f"corpus_cloze::unmatched corpus_cloze::{status}")
        for batch in chunks(ids, 100):
            anki("changeDeck", cards=cards_for_notes(batch), deck=UNMATCHED_DECK)

    audio = sync_audio(tempo=tempo)

    linked = [source for source in sources if source["note_id"] in replacements]
    texts = {
        note["noteId"]: note["fields"]["Text"]["value"]
        for note in batched_note_info([replacements[source["note_id"]] for source in linked])
    }
    ready = [source for source in linked if SOUND_RE.search(texts[replacements[source["note_id"]]])]
    activate = [replacements[s["note_id"]] for s in ready if s["original_state"] == "active"]
    for batch in chunks(activate, 100):
        anki("unsuspend", cards=cards_for_notes(batch))
    retired = [source["note_id"] for source in ready]
    if retired:
        archive_originals(archive_path, retired)
        anki("deleteNotes", notes=retired)
    return {
        "originals": len(sources), "created": len(create),
        "moved_to_unmatched": sum(map(len, unmatched.values())),
        "audio_updated": audio["updated"], "originals_retired": len(retired),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    run = subparsers.add_parser("migrate", help="match, create, add audio and retire originals; safe to re-run")
    run.add_argument("--corpora", type=Path, default=CORPUS_ROOT)
    run.add_argument("--archive", type=Path, default=ARCHIVE_PATH)
    subparsers.add_parser("fetch-translations", help="download canonical Perseus English editions")
    args = parser.parse_args()
    if args.command == "migrate":
        from flashcards.recipes import ClassicalMigration

        print(json.dumps(ClassicalMigration(args.corpora, args.archive).apply()))
    else:
        print(json.dumps({"downloaded": fetch_translations(TRANSLATION_ROOT), "directory": str(TRANSLATION_ROOT)}))


if __name__ == "__main__":
    main()
