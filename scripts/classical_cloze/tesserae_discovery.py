"""Client helpers for the hosted Tesserae corpus API."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from typing import Any


BASE_URL = "https://tesserae.caset.buffalo.edu/api"
ERA_PRIORITY = {"Republic": 0, "Augustan": 1, "Early Imperial": 2, "Late Imperial": 3}
ENGLISH_WORDS = {
    "a", "an", "and", "are", "as", "at", "before", "by", "for", "from",
    "in", "is", "it", "make", "of", "on", "or", "that", "the", "their",
    "this", "to", "was", "were", "when", "with", "you",
}


def post(endpoint: str, payload: dict[str, Any]) -> dict[str, Any]:
    request = urllib.request.Request(
        f"{BASE_URL}/{endpoint}",
        data=json.dumps(payload).encode(),
        headers={
            "Content-Type": "application/json",
            "User-Agent": "classical-cloze-private-research/1",
        },
    )
    for attempt in range(5):
        try:
            with urllib.request.urlopen(request, timeout=180) as response:
                return json.load(response)
        except (urllib.error.URLError, TimeoutError):
            if attempt == 4:
                raise
            time.sleep(2 ** attempt * 5)
    raise RuntimeError("unreachable")


def search(value: str) -> tuple[str, list[dict[str, Any]]]:
    lemma = post("corpus-search", {"lemmas": [value], "language": "la", "max_results": 100})
    if lemma.get("results"):
        return "lemma", lemma["results"]
    surface = post("line-search", {
        "query": value, "language": "la", "search_type": "exact", "max_results": 100,
    })
    return "word", surface.get("results", [])
