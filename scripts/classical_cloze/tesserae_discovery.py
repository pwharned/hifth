"""Sample unmatched Latin terms against the hosted Tesserae corpus API."""

from __future__ import annotations

import argparse
import json
import random
import re
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from scripts.classical_cloze.pipeline import source_terms


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


def candidates(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    output = []
    for entry in manifest["entries"]:
        if entry.get("language") != "latin" or entry.get("status") != "unmatched":
            continue
        terms = source_terms(entry["front"], "latin")
        words = re.findall(r"[A-Za-z]+", entry["front"].casefold())
        english_score = sum(word in ENGLISH_WORDS for word in words)
        if (
            len(terms) == 1
            and re.fullmatch(r"[A-Za-zĀ-ž]+", terms[0])
            and terms[0].casefold() not in ENGLISH_WORDS
            and len(entry["front"]) <= 160
            and english_score < 2
        ):
            output.append({**entry, "query": terms[0]})
    return output


def discover(
    manifest_path: Path, output_path: Path, sample_size: int, seed: int,
    delay: float, all_clean: bool,
) -> None:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    entries = candidates(manifest)
    if not all_clean:
        random.Random(seed).shuffle(entries)
        entries = entries[:sample_size]
    output = (
        json.loads(output_path.read_text(encoding="utf-8"))
        if output_path.exists()
        else {"schema_version": 1, "manifest": str(manifest_path), "results": {}}
    )
    candidate_ids = {str(entry["note_id"]) for entry in entries}
    output["results"] = {
        key: value for key, value in output["results"].items()
        if key in candidate_ids
    }
    for result in output["results"].values():
        result["examples"] = [
            example for example in result["examples"]
            if len(example.get("text", "").split()) <= 50
        ]
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    for entry in entries:
        key = str(entry["note_id"])
        if key in output["results"]:
            continue
        field, results = search(entry["query"])
        results.sort(key=lambda item: (
            ERA_PRIORITY.get(item.get("era", ""), 9),
            len(item.get("text", "")), item.get("author", ""), item.get("locus", ""),
        ))
        results = [item for item in results if len(item.get("text", "").split()) <= 50]
        output["results"][key] = {
            "front": entry["front"],
            "query": entry["query"],
            "search_field": field,
            "total": len(results),
            "examples": results[:10],
        }
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        time.sleep(delay)
    print(json.dumps({
        "queried": len(output["results"]),
        "with_hits": sum(bool(item["examples"]) for item in output["results"].values()),
        "lemma_hits": sum(item["search_field"] == "lemma" and bool(item["examples"]) for item in output["results"].values()),
        "surface_hits": sum(item["search_field"] == "word" and bool(item["examples"]) for item in output["results"].values()),
        "output": str(output_path),
    }, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--output", type=Path, default=Path("data/classical_cloze/corpora/tesserae-online.json"))
    parser.add_argument("--sample-size", type=int, default=20)
    parser.add_argument("--seed", type=int, default=20261011)
    parser.add_argument("--delay", type=float, default=1.0)
    parser.add_argument("--all-clean", action="store_true")
    args = parser.parse_args()
    discover(args.manifest, args.output, args.sample_size, args.seed, args.delay, args.all_clean)


if __name__ == "__main__":
    main()
