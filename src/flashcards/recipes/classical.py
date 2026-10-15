from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class ClassicalMigration:
    """Public recipe for the specialized, idempotent classical migration."""

    corpus_root: Path = Path("data/classical_cloze/corpora")
    archive: Path = Path("data/classical_cloze/archive/migrated-originals.json")
    tempo: float = 0.8
    online_single_words: bool = False

    def apply(self) -> dict[str, int]:
        # Imported lazily so the core library does not require classical ML dependencies.
        from scripts.classical_cloze.pipeline import migrate

        return migrate(
            self.corpus_root,
            self.archive,
            tempo=self.tempo,
            online_single_words=self.online_single_words,
        )

    def repair_audio(self) -> dict[str, int]:
        from scripts.classical_cloze.mms_audio import sync_audio

        return sync_audio(tempo=self.tempo)
