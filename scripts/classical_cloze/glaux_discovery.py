"""Sample unmatched Greek lemmas against the hosted GLAUx search index."""

from __future__ import annotations

import argparse
import json
import random
import time
import unicodedata
import urllib.parse
import urllib.request
import urllib.error
from collections import Counter
from pathlib import Path
from typing import Any


BASE_URL = "https://glaux.be"


def request_json(url: str, data: bytes | None = None) -> Any:
    request = urllib.request.Request(
        url,
        data=data,
        headers={
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "User-Agent": "classical-cloze-private-research/1",
            "X-Requested-With": "XMLHttpRequest",
        },
    )
    for attempt in range(5):
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                return json.load(response)
        except (urllib.error.URLError, TimeoutError):
            if attempt == 4:
                raise
            time.sleep(2 ** attempt * 5)


def search(value: str, field: str = "lemma") -> list[dict[str, Any]]:
    value = unicodedata.normalize("NFD", value)
    payload = {
        "searchdata": {
            "nodes": {
                "word_1": {
                    "properties": {field: ["exact", value]},
                    "has_effect_on": [],
                    "effectProperties": {},
                    "animacy": False,
                    "TM_name": {},
                }
            },
            "rootnodes": ["word_1"],
            "unitMetadata": {},
            "nodePositionOrder": [],
        }
    }
    return request_json(
        f"{BASE_URL}/runsearch.php?page=1",
        json.dumps(payload, ensure_ascii=False).encode(),
    )


def loose(value: str) -> str:
    decomposed = unicodedata.normalize("NFD", value).casefold()
    return "".join(character for character in decomposed if unicodedata.category(character) != "Mn").replace("ς", "σ")


def suggested_lemma(lemma: str) -> str | None:
    query = urllib.parse.urlencode({"string": lemma})
    suggestions = request_json(f"{BASE_URL}/AJAX/getLemmaSuggestion.php?{query}")
    matches = [item["lemma"] for item in suggestions if loose(item["lemma"]) == loose(lemma)]
    return unicodedata.normalize("NFC", matches[0]) if matches else None


def sentence_details(sentence_ids: list[int]) -> dict[str, Any]:
    merged = {
        "sentenceBuildInfo": {},
        "sentenceAuthorTitles": {},
        "sentenceTranslations": {},
    }
    for start in range(0, len(sentence_ids), 40):
        query = urllib.parse.urlencode(
            [("s[]", value) for value in sentence_ids[start:start + 40]]
        )
        payload = request_json(f"{BASE_URL}/AJAX/getsentencebyid.php?{query}")
        for key in merged:
            merged[key].update(payload.get(key, {}))
    return merged


def render_sentence(words: list[dict[str, Any]]) -> str:
    output = ""
    for word in words:
        form = word.get("form_original") or word["word"]
        if form in {",", ".", ";", "·", ":", "?", "!"}:
            output = output.rstrip() + form + " "
        else:
            output += form + " "
    return unicodedata.normalize("NFC", output.strip())


def sample_entries(
    manifest: dict[str, Any], size: int, seed: int, all_clean: bool,
) -> list[dict[str, Any]]:
    entries = [
        entry for entry in manifest["entries"]
        if entry.get("language") == "greek"
        and entry.get("status") == "unmatched"
        and len(entry.get("front", "").split()) == 1
    ]
    if all_clean:
        return entries
    random.Random(seed).shuffle(entries)
    return entries[:size]


def discover(
    manifest_path: Path, output_path: Path, size: int, seed: int, delay: float,
    retry_misses: bool, all_clean: bool,
) -> None:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if output_path.exists():
        output = json.loads(output_path.read_text(encoding="utf-8"))
    else:
        output = {"schema_version": 1, "manifest": str(manifest_path), "results": {}}
    sample = sample_entries(manifest, size, seed, all_clean)
    for entry in sample:
        lemma = entry["front"]
        previous = output["results"].get(lemma)
        if previous and (previous["examples"] or not retry_misses):
            continue
        searched_lemma = lemma
        search_field = "lemma"
        hits = search(searched_lemma, search_field)
        if not hits:
            searched_lemma = suggested_lemma(lemma) or lemma
            if searched_lemma != lemma:
                hits = search(searched_lemma, search_field)
        if not hits:
            searched_lemma = lemma
            search_field = "word"
            hits = search(searched_lemma, search_field)
        sentence_ids = list(dict.fromkeys(hit["sentenceid"] for hit in hits))
        details = sentence_details(sentence_ids)
        hits_by_sentence: dict[int, list[dict[str, Any]]] = {}
        for hit in hits:
            hits_by_sentence.setdefault(hit["sentenceid"], []).append(hit)
        examples = []
        for sentence_id in sentence_ids:
            key = str(sentence_id)
            metadata = details["sentenceAuthorTitles"].get(key, [])
            examples.append({
                "sentence_id": sentence_id,
                "author": metadata[0] if len(metadata) > 0 else "",
                "work": metadata[1] if len(metadata) > 1 else "",
                "genre": metadata[2] if len(metadata) > 2 else "",
                "text": render_sentence(details["sentenceBuildInfo"].get(key, [])),
                "translation": details["sentenceTranslations"].get(key),
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
                } for token in details["sentenceBuildInfo"].get(key, [])],
            })
        output["results"][lemma] = {
            "note_id": entry["note_id"],
            "searched_lemma": searched_lemma,
            "search_field": search_field,
            "hit_count_first_page": len(hits),
            "examples": examples,
        }
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        time.sleep(delay)

    works = Counter(
        (example["author"], example["work"])
        for result in output["results"].values()
        for example in result["examples"]
        if example["work"]
    )
    print(json.dumps({
        "sampled": len(output["results"]),
        "lemmas_with_hits": sum(bool(item["examples"]) for item in output["results"].values()),
        "top_works": [list(item) + [count] for item, count in works.most_common(20)],
        "output": str(output_path),
    }, ensure_ascii=False, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--output", type=Path, default=Path("data/classical_cloze/glaux-discovery.json"))
    parser.add_argument("--sample-size", type=int, default=20)
    parser.add_argument("--seed", type=int, default=20261010)
    parser.add_argument("--delay", type=float, default=3.0)
    parser.add_argument("--retry-misses", action="store_true")
    parser.add_argument("--all-clean", action="store_true")
    args = parser.parse_args()
    discover(
        args.manifest, args.output, args.sample_size, args.seed, args.delay,
        args.retry_misses,
        args.all_clean,
    )


if __name__ == "__main__":
    main()
