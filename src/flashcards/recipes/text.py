from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class TextBook:
    source: Path
    language: str
    deck: str
    target_language: str = "en"
    audio: str = "google"
    voice: str | None = None
    cache: Path | None = None
    delay: float = 0.25

    def apply(self) -> dict[str, int]:
        from ..text import generate_text_cards

        return generate_text_cards(
            self.source,
            language=self.language,
            target_language=self.target_language,
            deck=self.deck,
            audio_provider=self.audio,
            microsoft_voice=self.voice,
            cache_path=self.cache,
            delay=self.delay,
        )
