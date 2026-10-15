from __future__ import annotations

from dataclasses import dataclass

from .domain import DraftBatch
from .ports import Publisher
from .recipes.base import Recipe


@dataclass(frozen=True, slots=True)
class PublishReport:
    created_or_updated: int
    failed: int
    errors: tuple[str, ...] = ()


class FlashcardApp:
    """Stable facade for generating and publishing batch flashcards."""

    def generate(self, recipe: Recipe) -> DraftBatch:
        return recipe.generate()

    def publish(self, batch: DraftBatch, publisher: Publisher) -> PublishReport:
        published = 0
        errors: list[str] = []
        for card in batch.cards:
            try:
                publisher.publish(card)
                published += 1
            except Exception as exc:
                errors.append(f"{card.id}: {exc}")
        return PublishReport(published, len(errors), tuple(errors))
