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
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .adapters import AnkiConnect
from .subtitles import SubtitleCue, parse_subtitles


ANKI_URL = "http://127.0.0.1:8765"
_ANKI = AnkiConnect(ANKI_URL)
MODEL_NAME = "SRT Sentence Audio"
LANGUAGE_RE = re.compile(r"^[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})?$")
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
    return _ANKI.invoke(action, **params)


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


def language_code(value: str) -> str:
    if not LANGUAGE_RE.fullmatch(value):
        raise ValueError(f"invalid language code {value!r}; expected e.g. 'ja', 'vi', 'ar', 'zh-CN'")
    return value


def add_language_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--lang", type=language_code, default="ja",
                        help="language of the subtitles, used for translation and audio (default: ja)")
    parser.add_argument("--translate-to", type=language_code, default="en",
                        help="language of the translation on the back of the card (default: en)")


def translate(text: str, source: str = "ja", target: str = "en") -> str:
    query = urllib.parse.urlencode({
        "client": "gtx", "sl": source, "tl": target, "dt": "t", "q": text,
    })
    payload = json.loads(fetch(f"https://translate.googleapis.com/translate_a/single?{query}"))
    return "".join(part[0] for part in payload[0] if part and part[0]).strip()


def synthesize(text: str, lang: str = "ja") -> bytes:
    query = urllib.parse.urlencode({
        "ie": "UTF-8", "client": "tw-ob", "tl": lang, "q": text,
    })
    return fetch(f"https://translate.googleapis.com/translate_tts?{query}")


def model_name(lang: str) -> str:
    return MODEL_NAME if lang == "ja" else f"{MODEL_NAME} ({lang})"


def ensure_model(lang: str = "ja") -> None:
    front = f"""<div class="source" lang="{lang}">{{{{Source}}}}</div>
<div class="audio">{{{{Audio}}}}</div>"""
    back = """{{FrontSide}}
<hr id="answer">
<div class="translation">{{Translation}}</div>
<div class="metadata">Cue {{CueID}} · {{Timestamp}}</div>"""
    css = """.card { font-family: sans-serif; font-size: 22px; text-align: center; line-height: 1.55; }
.source { font-size: 28px; }
.audio { margin: 1rem 0; }
.translation { margin-top: 1rem; }
.metadata { margin-top: 1rem; color: #888; font-size: 12px; }
hr { border: 0; border-top: 1px solid #bbb; }"""
    if lang == "ja":
        css += "\n.source { font-family: 'Noto Sans JP', sans-serif; }"
    name = model_name(lang)
    if name in anki("modelNames"):
        return
    anki(
        "createModel", modelName=name,
        inOrderFields=["Source", "Translation", "Audio", "CueID", "Timestamp", "SourceFile"],
        css=css,
        isCloze=False,
        cardTemplates=[{"Name": "Sentence", "Front": front, "Back": back}],
    )


def timestamp(milliseconds: float) -> str:
    total = int(milliseconds // 1000)
    return f"{total // 3600:02d}:{total % 3600 // 60:02d}:{total % 60:02d}"


def generate(
    subtitle_path: Path,
    deck: str,
    cache_path: Path,
    delay: float,
    parse: Callable[[str], tuple[SubtitleCue, ...]] = parse_subtitles,
    lang: str = "ja",
    translate_to: str = "en",
) -> dict[str, int]:
    language_code(lang)
    language_code(translate_to)
    subtitle_bytes = subtitle_path.read_bytes()
    source_hash = hashlib.sha256(subtitle_bytes).hexdigest()
    parsed = parse(subtitle_bytes.decode("utf-8-sig"))
    cues = prepare_cues(parsed)
    cache = (
        json.loads(cache_path.read_text(encoding="utf-8"))
        if cache_path.exists() else {
            "schema_version": 1, "source_sha256": source_hash,
            "lang": lang, "translate_to": translate_to, "cues": {},
        }
    )
    if cache["source_sha256"] != source_hash:
        raise RuntimeError("SRT changed; use a new cache or remove the old one")
    if (cache.get("lang", "ja"), cache.get("translate_to", "en")) != (lang, translate_to):
        raise RuntimeError(
            f"cache was built for {cache.get('lang', 'ja')} -> {cache.get('translate_to', 'en')}; "
            "use a new cache for a different language"
        )
    media_dir = cache_path.parent / "srt-tts-media"
    media_dir.mkdir(parents=True, exist_ok=True)
    ensure_model(lang)
    anki("createDeck", deck=deck)
    created = 0
    existing = 0
    for cue in cues:
        stable_id = f"{source_hash[:16]}-{cue.index:04d}"
        stored = cache["cues"].setdefault(stable_id, {})
        translation = stored.get("translation")
        if not translation:
            translation = translate(cue.text, lang, translate_to)
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
        digest = hashlib.sha256(f"{lang}\0{cue.text}".encode()).hexdigest()[:24]
        filename = f"srt_{lang}_{digest}.mp3"
        audio_path = media_dir / filename
        if not audio_path.exists():
            audio_path.write_bytes(synthesize(cue.text, lang))
            time.sleep(delay)
        media = anki(
            "storeMediaFile", filename=filename,
            data=base64.b64encode(audio_path.read_bytes()).decode(),
        )
        note_id = anki("addNote", note={
            "deckName": deck,
            "modelName": model_name(lang),
            "fields": {
                "Source": html.escape(cue.text),
                "Translation": html.escape(translation),
                "Audio": f"[sound:{media}]",
                "CueID": stable_id,
                "Timestamp": f"{timestamp(cue.start_ms)}–{timestamp(cue.end_ms)}",
                "SourceFile": subtitle_path.name,
            },
            "options": {"allowDuplicate": True},
            "tags": ["generated::srt_batch", "source::" + re.sub(r"\s+", "_", subtitle_path.stem)],
        })
        card = anki("findCards", query=f"nid:{note_id}")[0]
        stored["note_id"] = note_id
        stored["card_id"] = card
        stored["order"] = cue.index
        cache_path.write_text(json.dumps(cache, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        created += 1
    return {"input_cues": len(parsed), "cards": len(cues), "created": created, "existing": existing}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("subtitles", type=Path)
    parser.add_argument("--deck", required=True)
    add_language_arguments(parser)
    parser.add_argument("--cache", type=Path)
    parser.add_argument("--delay", type=float, default=0.25)
    args = parser.parse_args()
    cache = args.cache or Path("data/srt-batch") / f"{args.subtitles.stem}.json"
    result = generate(
        args.subtitles, args.deck, cache, args.delay,
        lang=args.lang, translate_to=args.translate_to,
    )
    print(json.dumps(result))


if __name__ == "__main__":
    main()
