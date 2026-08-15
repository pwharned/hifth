"""
remap.py
--------
Runs only the mapping step of the alignment pipeline using
previously saved intermediate files.
Useful for iterating on the mapping algorithm without re-running
the expensive Whisper transcription and forced alignment passes.
Requires:
    data/output/intermediate/{surah}_pass2_aligned.json
    data/text/{surah}_uthmani.json
Usage:
    python -m src.quran_alignment.remap --surah 2
"""
import json
import logging
import argparse
from pathlib import Path
from src.quran_alignment.align import (
    map_transcript_to_canonical,
    fetch_uthmani_text,
    SAMPLE_RATE
)
# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(message)s",
    datefmt="%H:%M:%S"
)
log = logging.getLogger(__name__)
# ── Core ──────────────────────────────────────────────────────────────────────
def run_remap(
    surah_number:     int,
    intermediate_dir: Path,
    text_dir:         Path,
    output_dir:       Path
) -> Path:
    """
    Loads saved intermediate files and runs the mapping step only.
    Args:
        surah_number:     Surah to remap (1-114).
        intermediate_dir: Directory containing pass2 aligned JSON.
        text_dir:         Directory containing Uthmani text cache.
        output_dir:       Directory to write the new aligned JSON.
    Returns:
        Path to the written aligned JSON.
    Raises:
        FileNotFoundError: If either intermediate file is missing.
    """
    padded    = str(surah_number).zfill(3)
    pass2_path = intermediate_dir / f"{padded}_pass2_aligned.json"
    if not pass2_path.exists():
        raise FileNotFoundError(
            f"Pass 2 intermediate file not found: {pass2_path}. "
            f"Run align.py first to generate intermediate outputs."
        )
    # ── Load intermediate files ───────────────────────────────────────────────
    log.info(f"Loading    : {pass2_path.name}")
    with open(pass2_path, encoding="utf-8") as f:
        transcript_words = json.load(f)
    log.info(f"Transcript : {len(transcript_words)} aligned words")
    canonical_words = fetch_uthmani_text(surah_number, text_dir)
    log.info(f"Canonical  : {len(canonical_words)} words")
    # ── Infer audio duration from last transcript word ────────────────────────
    if transcript_words:
        audio_duration_s = transcript_words[-1]["end"]
        log.info(f"Duration   : {audio_duration_s:.1f}s (inferred from last word)")
    else:
        audio_duration_s = 0.0
        log.warning("No transcript words found — positional anchoring disabled")
    # ── Run mapping ───────────────────────────────────────────────────────────
    log.info(f"Mapping    : Transcript → canonical Uthmani")
    resolved = map_transcript_to_canonical(
        transcript_words = transcript_words,
        canonical_words  = canonical_words,
        audio_duration_s = audio_duration_s
    )
    # ── Build output ──────────────────────────────────────────────────────────
    output_words = []
    for i, item in enumerate(resolved):
        meta = item["meta"]
        source = item["timestamp_source"]
        output_words.append({
            "index":                  i,
            "surah":                  meta["surah"],
            "ayah":                   meta["ayah"],
            "position":               meta["position"],
            "text":                   meta["text"],
            "start_ms":               item["start_ms"],
            "end_ms":                 item["end_ms"],
            "score":                  0.0,
            "is_basmala":             meta.get("is_basmala", False),
            "repetitions":            item["repetitions"],
            "timestamp_interpolated": source == "interpolated"
        })
    result = {
        "surah_number":      surah_number,
        "total_words":       len(output_words),
        "audio_duration_ms": round(audio_duration_s * 1000, 1),
        "words":             output_words
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"{padded}_aligned.json"
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    log.info(f"Output     : Written to {output_path}")
    return output_path
# ── CLI ───────────────────────────────────────────────────────────────────────
def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Re-run only the mapping step using saved intermediate files."
    )
    parser.add_argument(
        "--surah",
        type=int,
        required=True,
        help="Surah number to remap (1-114)."
    )
    parser.add_argument(
        "--intermediate-dir",
        type=Path,
        default=Path("data/output/intermediate"),
        help="Directory containing intermediate files. Default: data/output/intermediate"
    )
    parser.add_argument(
        "--text-dir",
        type=Path,
        default=Path("data/text"),
        help="Directory containing Uthmani text cache. Default: data/text"
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("data/output/aligned"),
        help="Output directory for aligned JSON. Default: data/output/aligned"
    )
    return parser.parse_args()
if __name__ == "__main__":
    args = _parse_args()
    output = run_remap(
        surah_number     = args.surah,
        intermediate_dir = args.intermediate_dir,
        text_dir         = args.text_dir,
        output_dir       = args.output_dir
    )
    log.info(f"Done: {output}")
