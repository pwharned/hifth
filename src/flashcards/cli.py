from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Sequence


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="flashcards", description="Batch-create flashcards from media, subtitles, and specialist sources."
    )
    commands = parser.add_subparsers(dest="command", required=True)

    media = commands.add_parser("media", help="extract a timed artifact from media")
    media.add_argument("args", nargs=argparse.REMAINDER)

    subtitles = commands.add_parser("subtitles", help="translate and publish subtitle cards")
    subtitles.add_argument("path", type=Path)
    subtitles.add_argument("--format", choices=("srt", "ass"))
    subtitles.add_argument("--deck", required=True)
    subtitles.add_argument("--language", default="ja")
    subtitles.add_argument("--translate-to", default="en")
    subtitles.add_argument("--style", action="append")
    subtitles.add_argument("--cache", type=Path)
    subtitles.add_argument("--delay", type=float, default=0.25)

    classical = commands.add_parser("classical", help="classical corpus card workflows")
    classical_commands = classical.add_subparsers(dest="classical_command", required=True)
    migrate = classical_commands.add_parser("migrate")
    migrate.add_argument("--corpora", type=Path, default=Path("data/classical_cloze/corpora"))
    migrate.add_argument(
        "--archive", type=Path,
        default=Path("data/classical_cloze/archive/migrated-originals.json"),
    )
    migrate.add_argument("--tempo", type=float, default=0.8)
    audio = classical_commands.add_parser("repair-audio")
    audio.add_argument("--tempo", type=float, default=0.8)

    return parser


def main(argv: Sequence[str] | None = None) -> None:
    args = _parser().parse_args(argv)
    if args.command == "media":
        from .import_media import main as import_media

        import_media(args.args)
        return
    if args.command == "classical":
        from .recipes.classical import ClassicalMigration

        recipe = ClassicalMigration(
            corpus_root=getattr(args, "corpora", Path("data/classical_cloze/corpora")),
            archive=getattr(
                args, "archive", Path("data/classical_cloze/archive/migrated-originals.json")
            ),
            tempo=args.tempo,
        )
        result = recipe.apply() if args.classical_command == "migrate" else recipe.repair_audio()
        print(json.dumps(result))
        return

    from .recipes import SubtitleBatch

    recipe = SubtitleBatch(
        source=args.path,
        deck=args.deck,
        language=args.language,
        target_language=args.translate_to,
        cache=args.cache,
        delay=args.delay,
        format=args.format,
        styles=tuple(args.style or ()),
    )
    print(json.dumps(recipe.apply()))


if __name__ == "__main__":
    main()
