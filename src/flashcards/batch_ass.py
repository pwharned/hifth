"""Create Anki sentence-audio cards from the dialogue lines of an ASS subtitle file."""
from __future__ import annotations

import argparse
import json
import re
import unicodedata
from functools import partial
from pathlib import Path

from .batch_srt import add_language_arguments, generate
from .subtitles import SubtitleCue, _timestamp_ms

DEFAULT_STYLE = "JP-ja"  # default only for --lang ja; other languages must name their style
_OVERRIDE_RE = re.compile(r"\{[^}]*}")
_BREAK_RE = re.compile(r"\\[Nnh]")


def parse_ass(payload: str, styles: frozenset[str] = frozenset({DEFAULT_STYLE})) -> tuple[SubtitleCue, ...]:
    """Return cues from `Dialogue` events whose style is in `styles`, ordered by start time."""
    payload = payload.lstrip("\ufeff").replace("\r\n", "\n").replace("\r", "\n")
    columns: list[str] | None = None
    in_events = False
    found: list[tuple[float, float, str]] = []
    seen_styles: set[str] = set()
    for line in payload.split("\n"):
        stripped = line.strip()
        if stripped.startswith("["):
            in_events = stripped.lower() == "[events]"
            continue
        if not in_events:
            continue
        key, _, value = stripped.partition(":")
        if key == "Format":
            columns = [name.strip().lower() for name in value.split(",")]
        elif key == "Dialogue":
            if columns is None:
                raise ValueError("ASS Dialogue line appears before the Events Format line")
            fields = value.lstrip().split(",", len(columns) - 1)
            if len(fields) != len(columns):
                raise ValueError(f"malformed ASS dialogue line: {line!r}")
            row = dict(zip(columns, fields))
            seen_styles.add(row["style"])
            if row["style"] not in styles:
                continue
            text = _BREAK_RE.sub(" ", _OVERRIDE_RE.sub("", row["text"]))
            text = unicodedata.normalize("NFC", re.sub(r"\s+", " ", text).strip())
            start_ms, end_ms = _timestamp_ms(row["start"]), _timestamp_ms(row["end"])
            if text and end_ms > start_ms:
                found.append((start_ms, end_ms, text))
    if not found:
        raise ValueError(
            f"ASS file has no dialogue for styles: {', '.join(sorted(styles))}; "
            f"styles in file: {', '.join(sorted(seen_styles))}"
        )
    found.sort(key=lambda item: item[:2])
    return tuple(SubtitleCue(str(index), start, end, text) for index, (start, end, text) in enumerate(found, 1))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("subtitles", type=Path)
    parser.add_argument("--deck", required=True)
    add_language_arguments(parser)
    parser.add_argument(
        "--style", action="append",
        help=f"ASS style holding the dialogue to import; repeatable "
             f"(default for --lang ja: {DEFAULT_STYLE}; required for other languages)",
    )
    parser.add_argument("--cache", type=Path)
    parser.add_argument("--delay", type=float, default=0.25)
    args = parser.parse_args()
    styles = args.style or ([DEFAULT_STYLE] if args.lang == "ja" else None)
    if styles is None:
        parser.error(f"--style is required when --lang is {args.lang}")
    cache = args.cache or Path("data/srt-batch") / f"{args.subtitles.stem}.json"
    parse = partial(parse_ass, styles=frozenset(styles))
    print(json.dumps(generate(
        args.subtitles, args.deck, cache, args.delay, parse,
        lang=args.lang, translate_to=args.translate_to,
    )))


if __name__ == "__main__":
    main()
