from __future__ import annotations

from typing import Protocol

from ..domain import DraftBatch


class Recipe(Protocol):
    def generate(self) -> DraftBatch: ...
