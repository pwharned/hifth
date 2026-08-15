"""
run_pipeline.py
---------------
Runs the full alignment pipeline for one or more Surahs.
Usage:
    python scripts/run_pipeline.py --surahs 1 2 3
    python scripts/run_pipeline.py --surahs 2 --device cpu
    python scripts/run_pipeline.py --all --device cpu
"""
import argparse
import shutil
import subprocess
import sys
from pathlib import Path
# ── Defaults ──────────────────────────────────────────────────────────────────
DEFAULT_BACKEND_DIR = Path(
    "../quran/backend/src/main/resources/static/data/surah"
)
# ── Pipeline ──────────────────────────────────────────────────────────────────
def run_surah(surah_number: int, device: str, backend_dir: Path) -> bool:
    """
    Runs normalize → align → validate for a single Surah.
    On success copies the verified JSON to the backend resources directory.
    Returns True on success, False on any failure.
    """
    steps = [
        [
            "python", "-m", "src.quran_alignment.normalize",
            "--surah", str(surah_number)
        ],
        [
            "python", "-m", "src.quran_alignment.align",
            "--surah", str(surah_number),
            "--device", device
        ],
        [
            "python", "-m", "src.quran_alignment.validate",
            "--surah", str(surah_number)
        ],
    ]
    for step in steps:
        print(f"\n── {' '.join(step)}")
        result = subprocess.run(step)
        if result.returncode != 0:
            print(f"\n❌ Pipeline failed at: {' '.join(step)}")
            return False
    # Copy verified output to backend static resources
    padded = str(surah_number).zfill(3)
    src    = Path("data/output/verified") / f"{padded}_aligned.json"
    dst    = backend_dir / f"{padded}_aligned.json"
    if not src.exists():
        print(f"\n⚠️  Verified file not found at {src} - skipping copy")
        return False
    backend_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)
    print(f"\n✅ Surah {surah_number} complete → {dst}")
    return True
# ── CLI ───────────────────────────────────────────────────────────────────────
def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the full alignment pipeline for one or more Surahs."
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--surahs",
        nargs="+",
        type=int,
        metavar="N",
        help="One or more Surah numbers to process (e.g. --surahs 1 2 3)"
    )
    group.add_argument(
        "--all",
        action="store_true",
        help="Process all 114 Surahs sequentially"
    )
    parser.add_argument(
        "--device",
        type=str,
        choices=["cuda", "cpu"],
        default="cuda",
        help="Device to run alignment on. Default: cuda"
    )
    parser.add_argument(
        "--backend-dir",
        type=Path,
        default=DEFAULT_BACKEND_DIR,
        help=f"Backend surah resource directory. Default: {DEFAULT_BACKEND_DIR}"
    )
    return parser.parse_args()
def main() -> None:
    args   = _parse_args()
    surahs = list(range(1, 115)) if args.all else args.surahs
    invalid = [s for s in surahs if not 1 <= s <= 114]
    if invalid:
        print(f"❌ Invalid Surah numbers: {invalid}. Must be between 1 and 114.")
        sys.exit(1)
    print(f"Pipeline  : {len(surahs)} Surah(s) | device={args.device}")
    print(f"Backend   : {args.backend_dir}")
    passed = []
    failed = []
    for surah in surahs:
        print(f"\n{'─' * 50}")
        print(f"Surah {surah}")
        print(f"{'─' * 50}")
        if run_surah(surah, args.device, args.backend_dir):
            passed.append(surah)
        else:
            failed.append(surah)
    print(f"\n{'═' * 50}")
    print(f"Summary   : {len(passed)} passed, {len(failed)} failed")
    if passed:
        print(f"Passed    : {passed}")
    if failed:
        print(f"Failed    : {failed}")
        sys.exit(1)
if __name__ == "__main__":
    main()
