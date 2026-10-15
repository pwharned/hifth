from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class QuranAlignment:
    surahs: tuple[int, ...]
    device: str = "cuda"
    backend_dir: Path = Path("hifth/backend/src/main/resources/static/data/surah")

    def apply(self) -> dict[str, tuple[int, ...]]:
        from scripts.run_pipeline import run_surah

        invalid = tuple(value for value in self.surahs if not 1 <= value <= 114)
        if invalid:
            raise ValueError(f"invalid Surah numbers: {invalid}")
        passed, failed = [], []
        for surah in self.surahs:
            (passed if run_surah(surah, self.device, self.backend_dir) else failed).append(surah)
        return {"passed": tuple(passed), "failed": tuple(failed)}


@dataclass(frozen=True, slots=True)
class QuranDeck:
    output: Path | None = None
    qh_start: int = 1
    qh_end: int = 240
    deck_name: str = "Quran::Quarter-Hizb Memorization"
    audio: bool = True

    def apply(self) -> None:
        from scripts.generate_anki_cards import main

        arguments = [
            "--qh-start", str(self.qh_start), "--qh-end", str(self.qh_end),
            "--deck-name", self.deck_name,
        ]
        if self.output is not None:
            arguments.extend(("--out", str(self.output)))
        if not self.audio:
            arguments.append("--no-audio")
        main(arguments)
