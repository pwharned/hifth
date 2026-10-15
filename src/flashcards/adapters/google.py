from __future__ import annotations

import json
import urllib.parse
import urllib.request
from pathlib import Path


class GoogleTranslate:
    def translate(self, text: str, source: str, target: str) -> str:
        query = urllib.parse.urlencode(
            {"client": "gtx", "sl": source, "tl": target, "dt": "t", "q": text}
        )
        request = urllib.request.Request(
            f"https://translate.googleapis.com/translate_a/single?{query}",
            headers={"User-Agent": "hifth-flashcards/1"},
        )
        with urllib.request.urlopen(request, timeout=120) as response:
            payload = json.load(response)
        return "".join(part[0] for part in payload[0] if part and part[0]).strip()


class GoogleTTS:
    def synthesize(self, text: str, language: str, output: Path) -> None:
        query = urllib.parse.urlencode(
            {"ie": "UTF-8", "client": "tw-ob", "tl": language, "q": text}
        )
        request = urllib.request.Request(
            f"https://translate.googleapis.com/translate_tts?{query}",
            headers={"User-Agent": "hifth-flashcards/1"},
        )
        with urllib.request.urlopen(request, timeout=120) as response:
            output.write_bytes(response.read())
