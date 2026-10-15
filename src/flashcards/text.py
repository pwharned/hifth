from __future__ import annotations

import argparse
import base64
import hashlib
import html
import json
import re
import time
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

from .adapters import AnkiConnect, GoogleTTS, GoogleTranslate, MicrosoftTTS


MODEL_NAME = "Ordered Sentence Audio"
SENTENCE_END_RE = re.compile(r"(?<=[.!?。！？؛;··])\s+")


@dataclass(frozen=True, slots=True)
class TextSentence:
    position: int
    text: str


def split_sentences(payload: str) -> tuple[TextSentence, ...]:
    """Split Unicode text without dropping or rewriting non-whitespace content."""
    if not isinstance(payload, str):
        raise TypeError("text payload must be a string")
    normalized = unicodedata.normalize("NFC", payload).replace("\r\n", "\n").replace("\r", "\n")
    output: list[TextSentence] = []
    for paragraph in re.split(r"\n\s*\n", normalized):
        paragraph = re.sub(r"\s+", " ", paragraph).strip()
        if not paragraph:
            continue
        for sentence in SENTENCE_END_RE.split(paragraph):
            sentence = sentence.strip()
            if sentence:
                output.append(TextSentence(len(output) + 1, sentence))
    return tuple(output)


def _safe_model_name(language: str) -> str:
    return f"{MODEL_NAME} ({language})"


def _ensure_model(anki: AnkiConnect, language: str) -> None:
    name = _safe_model_name(language)
    if name in anki.invoke("modelNames"):
        return
    anki.invoke(
        "createModel",
        modelName=name,
        inOrderFields=["Source", "Translation", "Audio", "SentenceID", "Position", "SourceFile"],
        css=(
            ".card { font-family: sans-serif; font-size: 24px; text-align: center; line-height: 1.5; }"
            ".translation { margin-top: 1rem; } .position { color: #888; font-size: 12px; }"
        ),
        isCloze=False,
        cardTemplates=[{
            "Name": "Sentence",
            "Front": f'<div lang="{html.escape(language)}">{{{{Source}}}}</div><div>{{{{Audio}}}}</div>',
            "Back": "{{FrontSide}}<hr><div class=translation>{{Translation}}</div>"
                    "<div class=position>{{SourceFile}} · {{Position}}</div>",
        }],
    )


class MmsTTS:
    def __init__(self) -> None:
        from scripts.classical_cloze.mms_audio import Synthesizer

        self._synthesizer = Synthesizer()

    def synthesize(self, text: str, language: str, output: Path) -> None:
        classical = {
            "grc": "greek", "greek": "greek",
            "la": "latin", "lat": "latin", "latin": "latin",
        }
        selected = classical.get(language.casefold())
        if selected is None:
            raise ValueError("MMS audio supports Greek (grc/greek) and Latin (la/lat/latin)")
        self._synthesizer.render(selected, text, 0.8, output)

    def close(self) -> None:
        self._synthesizer.close()


def generate_text_cards(
    source: Path,
    *,
    language: str,
    target_language: str,
    deck: str,
    audio_provider: str = "google",
    microsoft_voice: str | None = None,
    cache_path: Path | None = None,
    delay: float = 0.25,
    anki: AnkiConnect | None = None,
    translator: Any | None = None,
    synthesizer: Any | None = None,
) -> dict[str, int]:
    source_bytes = source.read_bytes()
    source_hash = hashlib.sha256(source_bytes).hexdigest()
    sentences = split_sentences(source_bytes.decode("utf-8-sig"))
    if not sentences:
        raise ValueError("text contains no sentences")
    cache_path = cache_path or Path("data/text-batch") / f"{source.stem}.json"
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache = json.loads(cache_path.read_text(encoding="utf-8")) if cache_path.exists() else {
        "schema_version": 1,
        "source_sha256": source_hash,
        "language": language,
        "target_language": target_language,
        "audio_provider": audio_provider,
        "microsoft_voice": microsoft_voice,
        "sentences": {},
    }
    expected = (source_hash, language, target_language, audio_provider, microsoft_voice)
    actual = (
        cache.get("source_sha256"), cache.get("language"),
        cache.get("target_language"), cache.get("audio_provider"), cache.get("microsoft_voice"),
    )
    if actual != expected:
        raise ValueError("text or provider settings changed; use a new cache file")

    anki = anki or AnkiConnect()
    translator = translator or GoogleTranslate()
    if audio_provider not in {"google", "microsoft", "mms"}:
        raise ValueError(f"unsupported audio provider: {audio_provider}")
    if synthesizer is None:
        synthesizer = (
            MicrosoftTTS(microsoft_voice) if audio_provider == "microsoft"
            else MmsTTS() if audio_provider == "mms"
            else GoogleTTS()
        )
    _ensure_model(anki, language)
    anki.invoke("createDeck", deck=deck)
    media_dir = cache_path.parent / "text-audio"
    media_dir.mkdir(parents=True, exist_ok=True)
    created = updated = 0
    width = len(str(len(sentences)))
    for sentence in sentences:
        print(f"\rProcessing sentence {sentence.position}/{len(sentences)}", end="", flush=True)
        identity = hashlib.sha256(
            f"{source_hash}\0{sentence.position}\0{sentence.text}".encode()
        ).hexdigest()[:24]
        sentence_id = f"text-{identity}"
        saved = cache["sentences"].setdefault(sentence_id, {})
        translation = saved.get("translation")
        if not translation:
            translation = translator.translate(sentence.text, language, target_language)
            saved["translation"] = translation
            cache_path.write_text(json.dumps(cache, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            time.sleep(delay)
        extension = "wav" if audio_provider == "mms" else "mp3"
        filename = f"text_{language}_{identity}.{extension}"
        audio_path = media_dir / filename
        if not audio_path.exists():
            synthesizer.synthesize(sentence.text, language, audio_path)
            time.sleep(delay)
        stored = anki.invoke(
            "storeMediaFile", filename=filename,
            data=base64.b64encode(audio_path.read_bytes()).decode(),
        )
        fields = {
            "Source": html.escape(sentence.text),
            "Translation": html.escape(translation),
            "Audio": f"[sound:{stored}]",
            "SentenceID": sentence_id,
            "Position": str(sentence.position).zfill(width),
            "SourceFile": source.name,
        }
        existing = anki.invoke("findNotes", query=f'deck:"{deck}" SentenceID:{sentence_id}')
        if existing:
            anki.invoke("updateNoteFields", note={"id": existing[0], "fields": fields})
            updated += 1
        else:
            anki.invoke("addNote", note={
                "deckName": deck, "modelName": _safe_model_name(language), "fields": fields,
                "options": {"allowDuplicate": False},
                "tags": ["generated::text", f"language::{language}"],
            })
            created += 1
    if hasattr(synthesizer, "close"):
        synthesizer.close()
    print()
    return {"sentences": len(sentences), "created": created, "updated": updated}


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Create ordered sentence cards from UTF-8 text.")
    parser.add_argument("source", type=Path)
    parser.add_argument("--language", required=True)
    parser.add_argument("--translate-to", default="en")
    parser.add_argument("--deck", required=True)
    parser.add_argument("--audio", choices=("google", "microsoft", "mms"), default="google")
    parser.add_argument("--voice", help="explicit Microsoft voice name")
    parser.add_argument("--cache", type=Path)
    parser.add_argument("--delay", type=float, default=0.25)
    args = parser.parse_args(argv)
    print(json.dumps(generate_text_cards(
        args.source, language=args.language, target_language=args.translate_to,
        deck=args.deck, audio_provider=args.audio, microsoft_voice=args.voice,
        cache_path=args.cache, delay=args.delay,
    )))
