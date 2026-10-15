"""Client helpers for the hosted GLAUx search index."""

from __future__ import annotations

import json
import time
import unicodedata
import urllib.parse
import urllib.request
import urllib.error
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
