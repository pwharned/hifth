"""Archive and correct clearly reversed unmatched source/gloss fields."""

from __future__ import annotations

import argparse
import datetime
import json
from pathlib import Path

from scripts.classical_cloze.pipeline import UNMATCHED_DECK, anki, is_greek, strip_html


REVERSED_BATCH_START = 1687284383720
EXPLICIT_REVERSED = {1677962155485, 1677962155697, 1677962156375}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--archive", type=Path,
        default=Path("data/classical_cloze/archive/reversed-originals-2026-10-11.json"),
    )
    args = parser.parse_args()
    ids = anki("findNotes", query=f'deck:"{UNMATCHED_DECK}" note:Basic')
    notes = []
    for start in range(0, len(ids), 500):
        notes.extend(anki("notesInfo", notes=ids[start:start + 500]))
    selected = [
        note for note in notes
        if note["noteId"] in EXPLICIT_REVERSED
        or (
            note["noteId"] >= REVERSED_BATCH_START
            and "Greek" not in note["tags"]
            and "Latin" not in note["tags"]
        )
    ]
    if len(selected) != 173:
        raise RuntimeError(f"refusing to reverse: expected 173 notes, found {len(selected)}")
    cards = []
    for start in range(0, len(selected), 100):
        note_ids = [note["noteId"] for note in selected[start:start + 100]]
        card_ids = [
            card for note_id in note_ids
            for card in anki("findCards", query=f"nid:{note_id}")
        ]
        cards.extend(anki("cardsInfo", cards=card_ids))
    archive = {
        "schema_version": 1,
        "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "deck": UNMATCHED_DECK,
        "notes": selected,
        "cards": cards,
    }
    args.archive.parent.mkdir(parents=True, exist_ok=True)
    args.archive.write_text(
        json.dumps(archive, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    if len(json.loads(args.archive.read_text(encoding="utf-8"))["notes"]) != 173:
        raise RuntimeError("archive verification failed")

    for note in selected:
        front = note["fields"]["Back"]["value"]
        back = note["fields"]["Front"]["value"]
        language = "Greek" if is_greek(strip_html(front)) else "Latin"
        anki("updateNoteFields", note={
            "id": note["noteId"],
            "fields": {"Front": front, "Back": back},
        })
        anki("addTags", notes=[note["noteId"]], tags=f"{language} import_correction::reversed")
    print(json.dumps({
        "reversed": len(selected),
        "greek": sum(is_greek(strip_html(note["fields"]["Back"]["value"])) for note in selected),
        "archive": str(args.archive),
    }))


if __name__ == "__main__":
    main()
