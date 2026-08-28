#!/usr/bin/env python3
"""
generate_anki_cards.py
-----------------------
Generates an Anki deck (.apkg) with one interactive flashcard per
Quarter-Hizb (QH). Each card embeds the app's visual cloze mechanism
(a live, JS-driven slider over the whole QH's Arabic text) instead of
native Anki cloze deletion -- the whole card is the memorization target,
the slider only adjusts how much of it is masked while reading.

Reuses:
  - QuranData QH -> Surah/Ayah segment math          (quran_data.py)
  - MaskEngine's seeded-shuffle masking algorithm     (mask_engine.py)
  - data/output/verified/<surah>_aligned.json          (word text + timing)
  - data/processed_audio/<surah>.wav                   (trimmed per QH)

Only Surahs with a verified aligned JSON are processed; QH spans needing
un-verified Surahs are skipped and reported.

Usage:
    python scripts/generate_anki_cards.py
    python scripts/generate_anki_cards.py --out my_deck.apkg --no-audio
    python scripts/generate_anki_cards.py --qh-start 1 --qh-end 20
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

import genanki

sys.path.insert(0, str(Path(__file__).parent))
from anki_export import quran_data as qd
from anki_export import mask_engine
from anki_export.templates import CSS, FRONT_TEMPLATE, BACK_TEMPLATE

REPO_ROOT = Path(__file__).parent.parent
VERIFIED_DIR = REPO_ROOT / "data" / "output" / "verified"
AUDIO_DIR = REPO_ROOT / "data" / "processed_audio"
DEFAULT_OUT = REPO_ROOT / "data" / "output" / "anki" / "quran_quarter_hizb.apkg"

MODEL_ID = 1607392837  # fixed, arbitrary - keeps note type stable across re-generation
DECK_ID = 1607392900

AUDIO_PAD_MS = 250  # small pre/post-roll so words aren't clipped


def load_surah_words(surah_number: int) -> list[dict] | None:
    path = VERIFIED_DIR / f"{surah_number:03d}_aligned.json"
    if not path.exists():
        return None
    with path.open(encoding="utf-8") as f:
        return json.load(f)["words"]


def words_in_ayah_range(words: list[dict], start_ayah: int, end_ayah: int) -> list[dict]:
    return [w for w in words if start_ayah <= w["ayah"] <= end_ayah]


def build_full_text_html(segment_words: list[list[dict]]) -> str:
    """Plain (unmasked) rendering for the Back of the card."""
    parts = []
    for seg in segment_words:
        last_ayah = None
        for w in seg:
            if last_ayah is not None and w["ayah"] != last_ayah:
                parts.append(f'<span class="ayah-marker">{last_ayah}</span>')
            parts.append(f'<span class="word">{w["text"]}</span>')
            last_ayah = w["ayah"]
        if last_ayah is not None:
            parts.append(f'<span class="ayah-marker">{last_ayah}</span>')
    return "".join(parts)


def build_words_json(segment_words: list[list[dict]], seed: int) -> str:
    entries = []
    for seg_idx, seg in enumerate(segment_words):
        ranks = mask_engine.ranked_indices(len(seg), seed)
        for w, rank in zip(seg, ranks):
            entries.append({"t": w["text"], "a": w["ayah"], "s": seg_idx, "r": rank})
    return json.dumps(entries, ensure_ascii=False)


def trim_audio(surah_number: int, start_ms: float, end_ms: float, out_path: Path) -> bool:
    src = AUDIO_DIR / f"{surah_number:03d}.wav"
    if not src.exists():
        return False
    start_s = max(0.0, (start_ms - AUDIO_PAD_MS) / 1000.0)
    duration_s = (end_ms - start_ms + 2 * AUDIO_PAD_MS) / 1000.0
    cmd = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-ss", f"{start_s:.3f}",
        "-i", str(src),
        "-t", f"{duration_s:.3f}",
        "-ac", "1", "-ar", "44100", "-b:a", "64k",
        str(out_path),
    ]
    return subprocess.run(cmd).returncode == 0


def concat_audio(clip_paths: list[Path], out_path: Path) -> bool:
    if len(clip_paths) == 1:
        clip_paths[0].rename(out_path)
        return True
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as f:
        for p in clip_paths:
            f.write(f"file '{p.resolve()}'\n")
        list_file = f.name
    cmd = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-f", "concat", "-safe", "0", "-i", list_file,
        "-c", "copy", str(out_path),
    ]
    ok = subprocess.run(cmd).returncode == 0
    Path(list_file).unlink(missing_ok=True)
    for p in clip_paths:
        p.unlink(missing_ok=True)
    return ok


def build_model() -> genanki.Model:
    return genanki.Model(
        MODEL_ID,
        "Quran Quarter-Hizb (Adjustable Cloze)",
        fields=[
            {"name": "QHId"},
            {"name": "SurahLabel"},
            {"name": "WordsJson"},
            {"name": "FullTextHtml"},
            {"name": "AudioTag"},
        ],
        templates=[
            {
                "name": "QH Card",
                "qfmt": FRONT_TEMPLATE,
                "afmt": BACK_TEMPLATE,
            }
        ],
        css=CSS,
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--qh-start", type=int, default=1)
    parser.add_argument("--qh-end", type=int, default=240)
    parser.add_argument("--no-audio", action="store_true")
    parser.add_argument("--deck-name", default="Quran::Quarter-Hizb Memorization")
    args = parser.parse_args()

    args.out.parent.mkdir(parents=True, exist_ok=True)
    media_dir = args.out.parent / "media"
    media_dir.mkdir(parents=True, exist_ok=True)

    surah_word_cache: dict[int, list[dict] | None] = {}

    def get_surah_words(surah_number: int):
        if surah_number not in surah_word_cache:
            surah_word_cache[surah_number] = load_surah_words(surah_number)
        return surah_word_cache[surah_number]

    model = build_model()
    deck = genanki.Deck(DECK_ID, args.deck_name)
    package_media: list[str] = []

    generated, skipped = [], []

    for qh_id in range(args.qh_start, args.qh_end + 1):
        segments = qd.segments_for_qh(qh_id)
        surahs_needed = {seg.surah_number for seg in segments}
        missing = [s for s in surahs_needed if get_surah_words(s) is None]
        if missing:
            skipped.append((qh_id, missing))
            continue

        segment_words: list[list[dict]] = []
        seg_audio_clips: list[tuple[int, float, float]] = []
        for seg in segments:
            all_words = get_surah_words(seg.surah_number)
            seg_words = words_in_ayah_range(all_words, seg.start_ayah, seg.end_ayah)
            if not seg_words:
                continue
            segment_words.append(seg_words)
            seg_audio_clips.append(
                (seg.surah_number, seg_words[0]["start_ms"], seg_words[-1]["end_ms"])
            )

        if not segment_words:
            skipped.append((qh_id, ["no words in range"]))
            continue

        words_json = build_words_json(segment_words, seed=qh_id)
        full_text_html = build_full_text_html(segment_words)
        surah_label = qd.surah_name(qd.primary_surah_for_qh(qh_id))

        audio_tag = ""
        if not args.no_audio:
            audio_filename = f"qh_{qh_id:03d}.mp3"
            audio_out_path = media_dir / audio_filename
            clip_paths = []
            ok = True
            for i, (surah_num, start_ms, end_ms) in enumerate(seg_audio_clips):
                clip_path = media_dir / f"_tmp_qh_{qh_id:03d}_{i}.mp3"
                if trim_audio(surah_num, start_ms, end_ms, clip_path):
                    clip_paths.append(clip_path)
                else:
                    ok = False
                    break
            if ok and clip_paths and concat_audio(clip_paths, audio_out_path):
                audio_tag = f"[sound:{audio_filename}]"
                package_media.append(str(audio_out_path))
            else:
                for p in clip_paths:
                    p.unlink(missing_ok=True)

        note = genanki.Note(
            model=model,
            fields=[str(qh_id), surah_label, words_json, full_text_html, audio_tag],
            guid=genanki.guid_for("quran-qh", qh_id),
        )
        deck.add_note(note)
        generated.append(qh_id)

    if not generated:
        print("No Quarter-Hizb cards could be generated (no verified data in range).")
        sys.exit(1)

    package = genanki.Package(deck)
    package.media_files = package_media
    package.write_to_file(str(args.out))

    print(f"Generated {len(generated)} card(s) -> {args.out}")
    if skipped:
        print(f"Skipped {len(skipped)} QH id(s) (missing verified Surah data):")
        for qh_id, missing in skipped[:10]:
            print(f"  QH {qh_id}: missing {missing}")
        if len(skipped) > 10:
            print(f"  ... and {len(skipped) - 10} more")


if __name__ == "__main__":
    main()
