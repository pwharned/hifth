from __future__ import annotations

import argparse
import hashlib
import tempfile
from pathlib import Path

from .anki import PreparedCard, export_apkg
from .media import materialize_audio_span
from .models import ProjectManifest


def load_manifest(path: str | Path) -> ProjectManifest:
    manifest_path = Path(path)
    if not manifest_path.is_file():
        raise FileNotFoundError(f"Manifest not found: {manifest_path}")
    return ProjectManifest.from_json(manifest_path.read_text(encoding="utf-8"))


def export_manifest_apkg(
    manifest: ProjectManifest,
    output_path: str | Path,
    *,
    media_base_dir: str | Path = ".",
    deck_name: str | None = None,
) -> Path:
    if not isinstance(manifest, ProjectManifest):
        raise ValueError("manifest must be a ProjectManifest")
    if not manifest.cards:
        raise ValueError("manifest contains no cards")
    base_dir = Path(media_base_dir)
    media_by_id = {source.id: source for source in manifest.media}

    with tempfile.TemporaryDirectory(prefix="flashcards-clips-") as temp_dir_name:
        temp_dir = Path(temp_dir_name)
        prepared: list[PreparedCard] = []
        for card in manifest.cards:
            audio_path = None
            if card.audio_span is not None:
                source = media_by_id[card.audio_span.media_id]
                source_path = Path(source.path)
                if not source_path.is_absolute():
                    source_path = base_dir / source_path
                safe_card_name = hashlib.sha256(card.id.encode("utf-8")).hexdigest()
                audio_path = materialize_audio_span(
                    source_path,
                    temp_dir / f"{safe_card_name}.mp3",
                    card.audio_span,
                )
            prepared.append(PreparedCard(card, audio_path))
        return export_apkg(
            tuple(prepared),
            output_path,
            deck_name=deck_name or manifest.title,
        )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Render a versioned flashcard project manifest as an Anki package."
    )
    parser.add_argument("manifest", type=Path, help="Path to the project manifest JSON")
    parser.add_argument("--out", type=Path, required=True, help="Output .apkg path")
    parser.add_argument(
        "--media-base-dir",
        type=Path,
        help="Base directory for relative media paths (defaults to the manifest directory)",
    )
    parser.add_argument("--deck-name", help="Override the manifest title as the Anki deck name")
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    manifest = load_manifest(args.manifest)
    base_dir = args.media_base_dir or args.manifest.resolve().parent
    output = export_manifest_apkg(
        manifest,
        args.out,
        media_base_dir=base_dir,
        deck_name=args.deck_name,
    )
    print(f"Generated {len(manifest.cards)} card(s) -> {output}")


if __name__ == "__main__":
    main()
