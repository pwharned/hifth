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

    text = commands.add_parser("text", help="create ordered sentence cards from UTF-8 text")
    text.add_argument("path", type=Path)
    text.add_argument("--language", required=True)
    text.add_argument("--translate-to", default="en")
    text.add_argument("--deck", required=True)
    text.add_argument("--audio", choices=("google", "microsoft", "mms"), default="google")
    text.add_argument("--voice")
    text.add_argument("--cache", type=Path)
    text.add_argument("--delay", type=float, default=0.25)

    classical = commands.add_parser("classical", help="classical corpus card workflows")
    classical_commands = classical.add_subparsers(dest="classical_command", required=True)
    migrate = classical_commands.add_parser("migrate")
    migrate.add_argument("--corpora", type=Path, default=Path("data/classical_cloze/corpora"))
    migrate.add_argument(
        "--archive", type=Path,
        default=Path("data/classical_cloze/archive/migrated-originals.json"),
    )
    migrate.add_argument("--tempo", type=float, default=0.8)
    migrate.add_argument("--online-single-words", action="store_true")
    audio = classical_commands.add_parser("repair-audio")
    audio.add_argument("--tempo", type=float, default=0.8)

    quran = commands.add_parser("quran", help="Quran alignment and memorization cards")
    quran_commands = quran.add_subparsers(dest="quran_command", required=True)
    align = quran_commands.add_parser("align")
    align_group = align.add_mutually_exclusive_group(required=True)
    align_group.add_argument("--surahs", nargs="+", type=int)
    align_group.add_argument("--all", action="store_true")
    align.add_argument("--device", choices=("cuda", "cpu"), default="cuda")
    export = quran_commands.add_parser("export")
    export.add_argument("--out", type=Path)
    export.add_argument("--qh-start", type=int, default=1)
    export.add_argument("--qh-end", type=int, default=240)
    export.add_argument("--deck-name", default="Quran::Quarter-Hizb Memorization")
    export.add_argument("--no-audio", action="store_true")

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
            online_single_words=getattr(args, "online_single_words", False),
        )
        result = recipe.apply() if args.classical_command == "migrate" else recipe.repair_audio()
        print(json.dumps(result))
        return
    if args.command == "quran":
        from .recipes import QuranAlignment, QuranDeck

        if args.quran_command == "align":
            surahs = tuple(range(1, 115)) if args.all else tuple(args.surahs)
            result = QuranAlignment(surahs, args.device).apply()
            print(json.dumps(result))
            if result["failed"]:
                raise SystemExit(1)
        else:
            QuranDeck(
                output=args.out,
                qh_start=args.qh_start,
                qh_end=args.qh_end,
                deck_name=args.deck_name,
                audio=not args.no_audio,
            ).apply()
        return
    if args.command == "text":
        from .recipes import TextBook

        result = TextBook(
            source=args.path,
            language=args.language,
            deck=args.deck,
            target_language=args.translate_to,
            audio=args.audio,
            voice=args.voice,
            cache=args.cache,
            delay=args.delay,
        ).apply()
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
