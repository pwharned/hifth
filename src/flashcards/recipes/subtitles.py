from __future__ import annotations

from dataclasses import dataclass
from functools import partial
from pathlib import Path


@dataclass(frozen=True, slots=True)
class SubtitleBatch:
    """Batch translation, speech generation, and Anki publication recipe."""

    source: Path
    deck: str
    language: str
    target_language: str = "en"
    cache: Path | None = None
    delay: float = 0.25
    format: str | None = None
    styles: tuple[str, ...] = ()

    def apply(self) -> dict[str, int]:
        from ..batch_srt import generate
        from ..subtitles import parse_subtitles

        format_name = (self.format or self.source.suffix.lstrip(".")).casefold()
        parser = parse_subtitles
        if format_name == "ass":
            from ..batch_ass import DEFAULT_STYLE, parse_ass

            styles = self.styles or ((DEFAULT_STYLE,) if self.language == "ja" else ())
            if not styles:
                raise ValueError("ASS styles are required for non-Japanese subtitles")
            parser = partial(parse_ass, styles=frozenset(styles))
        elif format_name not in {"srt", "vtt"}:
            raise ValueError(f"unsupported subtitle format: {format_name}")
        cache = self.cache or Path("data/subtitle-batch") / f"{self.source.stem}.json"
        return generate(
            self.source,
            self.deck,
            cache,
            self.delay,
            parser,
            lang=self.language,
            translate_to=self.target_language,
        )
