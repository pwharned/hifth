from __future__ import annotations

import json
import urllib.parse
import urllib.request
from pathlib import Path


class GoogleTranslate:
    def translate(self, text: str, source: str, target: str) -> str:
        aliases = {"greek": "el", "grc": "el", "latin": "la", "lat": "la"}
        source = aliases.get(source.casefold(), source)
        target = aliases.get(target.casefold(), target)
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


class MicrosoftTTS:
    def __init__(self, voice: str | None = None) -> None:
        self.voice = voice

    def synthesize(self, text: str, language: str, output: Path) -> None:
        import asyncio
        import edge_tts

        async def render() -> None:
            voice = self.voice
            if voice is None:
                manager = await edge_tts.VoicesManager.create()
                matches = manager.find(Language=language)
                if not matches:
                    matches = manager.find(Locale=language)
                if not matches:
                    raise ValueError(f"Microsoft has no voice matching {language!r}")
                voice = matches[0]["Name"]
            await edge_tts.Communicate(text, voice).save(str(output))

        asyncio.run(render())
