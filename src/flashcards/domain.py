from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True, slots=True)
class Provenance:
    source: str
    location: str | None = None
    details: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class AudioAsset:
    filename: str
    path: Path


@dataclass(frozen=True, slots=True)
class CardDraft:
    """Provider-independent description of a card to be published."""

    id: str
    fields: dict[str, str]
    model: str
    deck: str
    tags: tuple[str, ...] = ()
    audio: tuple[AudioAsset, ...] = ()
    provenance: Provenance | None = None

    def __post_init__(self) -> None:
        if not self.id.strip():
            raise ValueError("card draft id must be nonempty")
        if not self.fields:
            raise ValueError("card draft fields must not be empty")
        if not self.model.strip() or not self.deck.strip():
            raise ValueError("card draft model and deck must be nonempty")


@dataclass(frozen=True, slots=True)
class DraftBatch:
    cards: tuple[CardDraft, ...]
    warnings: tuple[str, ...] = ()
    metadata: dict[str, str] = field(default_factory=dict)
