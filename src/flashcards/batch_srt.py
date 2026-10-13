from __future__ import annotations

import argparse
import base64
import hashlib
import html
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .subtitles import SubtitleCue, parse_subtitles


ANKI_URL = "http://127.0.0.1:8765"
MODEL_NAME = "SRT Sentence Audio"
SPEAKER_RE = re.compile(r"^(?:\[ﾏｲｸ\])?\s*[（(][^）)]{1,30}[）)]\s*")
ONLY_DIRECTION_RE = re.compile(r"^[（(][^）)]+[）)]$")
MUSIC_RE = re.compile(r"^[♪♫♬～〜\s]+$")


@dataclass(frozen=True)
class BatchCue:
    index: int
    source_ids: tuple[str, ...]
    start_ms: float
    end_ms: float
    text: str


def anki(action: str, **params: Any) -> Any:
    request = urllib.request.Request(
        ANKI_URL,
        data=json.dumps({"action": action, "version": 6, "params": params}).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=120) as response:
        payload = json.load(response)
    if payload.get("error"):
        raise RuntimeError(f"AnkiConnect {action}: {payload['error']}")
    return payload["result"]


def clean_text(value: str) -> str:
    value = value.strip()
    if ONLY_DIRECTION_RE.fullmatch(value) or MUSIC_RE.fullmatch(value):
        return ""
    value = SPEAKER_RE.sub("", value)
    value = value.replace("→", "")
    value = value.strip("《》＜＞ \t")
    return re.sub(r"[ \t\u3000]+", " ", value).strip()


def prepare_cues(cues: tuple[SubtitleCue, ...]) -> list[BatchCue]:
    output = []
    pending_ids: list[str] = []
    pending_text: list[str] = []
    pending_start = 0.0
    pending_end = 0.0

    def flush() -> None:
        if not pending_text:
            return
        text = clean_text(" ".join(pending_text))
        if text:
            output.append(BatchCue(
                index=len(output) + 1,
                source_ids=tuple(pending_ids),
                start_ms=pending_start,
                end_ms=pending_end,
                text=text,
            ))
        pending_ids.clear()
        pending_text.clear()

    for cue in cues:
        raw = cue.text.strip()
        cleaned = clean_text(raw)
        if not cleaned:
            flush()
            continue
        if not pending_text:
            pending_start = cue.start_ms
        pending_ids.append(cue.id)
        pending_text.append(cleaned)
        pending_end = cue.end_ms
        if not raw.rstrip().endswith("→"):
            flush()
    flush()
    return output


def fetch(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "hifth-flashcards/1"})
    for attempt in range(5):
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                return response.read()
        except (urllib.error.URLError, TimeoutError):
            if attempt == 4:
                raise
            time.sleep(2 ** attempt * 3)
    raise RuntimeError("unreachable")


def translate(text: str) -> str:
    query = urllib.parse.urlencode({
        "client": "gtx", "sl": "ja", "tl": "en", "dt": "t", "q": text,
    })
    payload = json.loads(fetch(f"https://translate.googleapis.com/translate_a/single?{query}"))
    return "".join(part[0] for part in payload[0] if part and part[0]).strip()


def synthesize(text: str) -> bytes:
    query = urllib.parse.urlencode({
        "ie": "UTF-8", "client": "tw-ob", "tl": "ja", "q": text,
    })
    return fetch(f"https://translate.googleapis.com/translate_tts?{query}")


def ensure_model() -> None:
    front = """<div class="source" lang="ja">{{Source}}</div>
<div class="audio">{{Audio}}</div>"""
    back = """{{FrontSide}}
<hr id="answer">
<div class="translation">{{Translation}}</div>
<div class="metadata">Cue {{CueID}} · {{Timestamp}}</div>"""
    css = """.card { font-family: sans-serif; font-size: 22px; text-align: center; line-height: 1.55; }
.source { font-family: 'Noto Sans JP', sans-serif; font-size: 28px; }
.audio { margin: 1rem 0; }
.translation { margin-top: 1rem; }
.metadata { margin-top: 1rem; color: #888; font-size: 12px; }
hr { border: 0; border-top: 1px solid #bbb; }"""
    if MODEL_NAME in anki("modelNames"):
        return
    anki(
        "createModel", modelName=MODEL_NAME,
        inOrderFields=["Source", "Translation", "Audio", "CueID", "Timestamp", "SourceFile"],
        css=css,
        isCloze=False,
        cardTemplates=[{"Name": "Sentence", "Front": front, "Back": back}],
    )


def timestamp(milliseconds: float) -> str:
    total = int(milliseconds // 1000)
    return f"{total // 3600:02d}:{total % 3600 // 60:02d}:{total % 60:02d}"


def generate(subtitle_path: Path, deck: str, cache_path: Path, delay: float) -> dict[str, int]:
    subtitle_bytes = subtitle_path.read_bytes()
    source_hash = hashlib.sha256(subtitle_bytes).hexdigest()
    cues = prepare_cues(parse_subtitles(subtitle_bytes.decode("utf-8-sig")))
    cache = (
        json.loads(cache_path.read_text(encoding="utf-8"))
        if cache_path.exists() else {"schema_version": 1, "source_sha256": source_hash, "cues": {}}
    )
    if cache["source_sha256"] != source_hash:
        raise RuntimeError("SRT changed; use a new cache or remove the old one")
    media_dir = cache_path.parent / "srt-tts-media"
    media_dir.mkdir(parents=True, exist_ok=True)
    ensure_model()
    anki("createDeck", deck=deck)
    created = 0
    existing = 0
    for cue in cues:
        stable_id = f"{source_hash[:16]}-{cue.index:04d}"
        stored = cache["cues"].setdefault(stable_id, {})
        translation = stored.get("translation")
        if not translation:
            translation = translate(cue.text)
            stored["translation"] = translation
            cache_path.write_text(json.dumps(cache, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            time.sleep(delay)
        found = anki("findNotes", query=f'deck:"{deck}" CueID:{stable_id}')
        if found:
            anki("updateNoteFields", note={
                "id": found[0],
                "fields": {
                    "Source": html.escape(cue.text),
                    "Translation": html.escape(translation),
                    "Timestamp": f"{timestamp(cue.start_ms)}–{timestamp(cue.end_ms)}",
                    "SourceFile": subtitle_path.name,
                },
            })
            existing += 1
            continue
        digest = hashlib.sha256(f"ja\0{cue.text}".encode()).hexdigest()[:24]
        filename = f"srt_ja_{digest}.mp3"
        audio_path = media_dir / filename
        if not audio_path.exists():
            audio_path.write_bytes(synthesize(cue.text))
            time.sleep(delay)
        media = anki(
            "storeMediaFile", filename=filename,
            data=base64.b64encode(audio_path.read_bytes()).decode(),
        )
        note_id = anki("addNote", note={
            "deckName": deck,
            "modelName": MODEL_NAME,
            "fields": {
                "Source": html.escape(cue.text),
                "Translation": html.escape(translation),
                "Audio": f"[sound:{media}]",
                "CueID": stable_id,
                "Timestamp": f"{timestamp(cue.start_ms)}–{timestamp(cue.end_ms)}",
                "SourceFile": subtitle_path.name,
            },
            "options": {"allowDuplicate": True},
            "tags": ["generated::srt_batch", f"source::{subtitle_path.stem}"],
        })
        card = anki("findCards", query=f"nid:{note_id}")[0]
        stored["note_id"] = note_id
        stored["card_id"] = card
        stored["order"] = cue.index
        cache_path.write_text(json.dumps(cache, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        created += 1
    return {"input_cues": len(parse_subtitles(subtitle_bytes.decode("utf-8-sig"))), "cards": len(cues), "created": created, "existing": existing}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("subtitles", type=Path)
    parser.add_argument("--deck", required=True)
    parser.add_argument("--cache", type=Path)
    parser.add_argument("--delay", type=float, default=0.25)
    args = parser.parse_args()
    cache = args.cache or Path("data/srt-batch") / f"{args.subtitles.stem}.json"
    result = generate(args.subtitles, args.deck, cache, args.delay)
    print(json.dumps(result))


if __name__ == "__main__":
    main()
