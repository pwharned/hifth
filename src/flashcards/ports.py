from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol

from .domain import CardDraft


class Anki(Protocol):
    def invoke(self, action: str, **params: Any) -> Any: ...


class Translator(Protocol):
    def translate(self, text: str, source: str, target: str) -> str: ...


class SpeechSynthesizer(Protocol):
    def synthesize(self, text: str, language: str, output: Path) -> None: ...


class Publisher(Protocol):
    def publish(self, card: CardDraft) -> int: ...
