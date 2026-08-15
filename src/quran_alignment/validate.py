"""
validate.py
-----------
Automated QA checks on raw WhisperX alignment output.
Checks:
    1. No negative durations (end_ms <= start_ms)
    2. No silence gaps exceeding 4 seconds between consecutive words
    3. Word token count matches Uthmani text source count
Usage:
    python -m src.quran_alignment.validate --surah 1
On success, promotes the file from data/output/aligned/ to data/output/verified/.
On failure, writes a detailed report and exits without promoting.
"""
import json
import logging
import argparse
import shutil
from dataclasses import dataclass, field
from pathlib import Path
# ── Constants ────────────────────────────────────────────────────────────────
MAX_SILENCE_GAP_MS  = 4000.0   # Flag gaps larger than this between words
MIN_WORD_SCORE      = 0.5      # Flag words with alignment confidence below this
# ── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(message)s",
    datefmt="%H:%M:%S"
)
log = logging.getLogger(__name__)
# ── Data Structures ──────────────────────────────────────────────────────────
@dataclass
class ValidationIssue:
    """Represents a single failed check on a word token."""
    check:   str        # Name of the check that failed
    index:   int        # Global word index
    surah:   int
    ayah:    int
    text:    str        # Arabic word text
    detail:  str        # Human-readable description of the problem
@dataclass
class ValidationReport:
    """Aggregates all issues found during validation of a single Surah."""
    surah_number:  int
    total_words:   int
    issues:        list[ValidationIssue] = field(default_factory=list)
    @property
    def passed(self) -> bool:
        return len(self.issues) == 0
    def summary(self) -> str:
        if self.passed:
            return (
                f"Surah {self.surah_number} | PASSED | "
                f"{self.total_words} words | 0 issues"
            )
        return (
            f"Surah {self.surah_number} | FAILED | "
            f"{self.total_words} words | {len(self.issues)} issue(s) found"
        )
# ── Individual Checks ────────────────────────────────────────────────────────
def check_negative_durations(words: list[dict]) -> list[ValidationIssue]:
    """
    Flags any word where end_ms <= start_ms.
    Indicates a malformed timestamp from the aligner.
    """
    issues = []
    for w in words:
        if w["end_ms"] <= w["start_ms"]:
            issues.append(ValidationIssue(
                check  = "negative_duration",
                index  = w["index"],
                surah  = w["surah"],
                ayah   = w["ayah"],
                text   = w["text"],
                detail = (
                    f"end_ms ({w['end_ms']}) <= start_ms ({w['start_ms']}). "
                    f"Duration: {w['end_ms'] - w['start_ms']:.1f}ms"
                )
            ))
    return issues
def check_silence_gaps(words: list[dict]) -> list[ValidationIssue]:
    """
    Flags any gap between consecutive words exceeding MAX_SILENCE_GAP_MS.
    Large gaps suggest a text skip or a section where the aligner lost sync.
    Note: Gaps at Ayah boundaries are expected to be longer due to natural
    pausing in recitation. These are still flagged if they exceed the limit,
    but the detail message notes the Ayah boundary context.
    """
    issues = []
    for i in range(1, len(words)):
        prev = words[i - 1]
        curr = words[i]
        gap  = curr["start_ms"] - prev["end_ms"]
        if gap > MAX_SILENCE_GAP_MS:
            at_ayah_boundary = curr["ayah"] != prev["ayah"]
            context = "at Ayah boundary" if at_ayah_boundary else "mid-Ayah"
            issues.append(ValidationIssue(
                check  = "silence_gap",
                index  = curr["index"],
                surah  = curr["surah"],
                ayah   = curr["ayah"],
                text   = curr["text"],
                detail = (
                    f"Gap of {gap:.1f}ms before this word ({context}). "
                    f"Previous word: '{prev['text']}' ended at {prev['end_ms']}ms. "
                    f"This word starts at {curr['start_ms']}ms."
                )
            ))
    return issues
def check_low_confidence_scores(words: list[dict]) -> list[ValidationIssue]:
    """
    Flags any word with an alignment confidence score below MIN_WORD_SCORE.
    Low scores indicate the aligner struggled to match the phonemes.
    These do not fail validation but are reported as warnings.
    """
    issues = []
    for w in words:
        if w["score"] < MIN_WORD_SCORE:
            issues.append(ValidationIssue(
                check  = "low_confidence",
                index  = w["index"],
                surah  = w["surah"],
                ayah   = w["ayah"],
                text   = w["text"],
                detail = (
                    f"Alignment score {w['score']:.4f} is below "
                    f"threshold {MIN_WORD_SCORE}. "
                    f"Timestamp may be imprecise."
                )
            ))
    return issues
def check_word_count(words: list[dict], text_dir: Path, surah_number: int) -> list[ValidationIssue]:
    """
    Confirms the number of aligned word tokens matches the cached
    Uthmani text token count exactly.
    Relies on the text cache written by align.py. If the cache is missing,
    this check is skipped with a warning.
    """
    cache_path = text_dir / f"{str(surah_number).zfill(3)}_uthmani.json"
    if not cache_path.exists():
        log.warning(
            f"Word count check skipped: "
            f"Uthmani text cache not found at {cache_path}. "
            f"Run align.py first to populate the cache."
        )
        return []
    with open(cache_path, encoding="utf-8") as f:
        uthmani_words = json.load(f)
    expected = len(uthmani_words)
    actual   = len(words)
    if actual != expected:
        return [ValidationIssue(
            check  = "word_count_mismatch",
            index  = -1,
            surah  = surah_number,
            ayah   = -1,
            text   = "",
            detail = (
                f"Aligned output has {actual} tokens but "
                f"Uthmani text has {expected} tokens. "
                f"Delta: {actual - expected:+d}. "
                f"Inspect aligned JSON manually before promoting."
            )
        )]
    return []
# ── Orchestrator ─────────────────────────────────────────────────────────────
def validate_surah(
    surah_number:  int,
    aligned_dir:   Path,
    verified_dir:  Path,
    text_dir:      Path
) -> ValidationReport:
    """
    Runs all validation checks against a raw aligned JSON file.
    Promotes the file to verified_dir if all hard checks pass.
    Hard checks (block promotion on failure):
        - negative_duration
        - word_count_mismatch
    Soft checks (logged as warnings, do not block promotion):
        - silence_gap        (may be legitimate recitation pauses)
        - low_confidence     (informational only)
    Args:
        surah_number: Surah to validate (1-114).
        aligned_dir:  Directory containing raw aligned JSON files.
        verified_dir: Directory to promote passing files into.
        text_dir:     Directory containing Uthmani text cache.
    Returns:
        A ValidationReport with all findings.
    Raises:
        FileNotFoundError: If the aligned JSON does not exist.
    """
    padded       = str(surah_number).zfill(3)
    aligned_path = aligned_dir / f"{padded}_aligned.json"
    if not aligned_path.exists():
        raise FileNotFoundError(
            f"Aligned JSON not found: {aligned_path}. "
            f"Run align.py first."
        )
    log.info(f"Validating : {aligned_path.name}")
    with open(aligned_path, encoding="utf-8") as f:
        data = json.load(f)
    words  = data["words"]
    report = ValidationReport(
        surah_number = surah_number,
        total_words  = len(words)
    )
    # ── Run checks ────────────────────────────────────────────────────────────
    log.info("Check 1/4  : Negative durations")
    report.issues += check_negative_durations(words)
    log.info("Check 2/4  : Silence gaps")
    report.issues += check_silence_gaps(words)
    log.info("Check 3/4  : Alignment confidence scores")
    report.issues += check_low_confidence_scores(words)
    log.info("Check 4/4  : Word count vs Uthmani text")
    report.issues += check_word_count(words, text_dir, surah_number)
    # ── Categorise results ────────────────────────────────────────────────────
    hard_failures = [
        i for i in report.issues
        if i.check in {"negative_duration", "word_count_mismatch"}
    ]
    soft_warnings = [
        i for i in report.issues
        if i.check in {"silence_gap", "low_confidence"}
    ]
    # ── Log all issues ────────────────────────────────────────────────────────
    if soft_warnings:
        log.warning(f"Warnings   : {len(soft_warnings)} soft issue(s) found")
        for issue in soft_warnings:
            log.warning(
                f"  [{issue.check}] "
                f"Word {issue.index} | Ayah {issue.ayah} | '{issue.text}' | "
                f"{issue.detail}"
            )
    if hard_failures:
        log.error(f"Failures   : {len(hard_failures)} hard failure(s) found")
        for issue in hard_failures:
            log.error(
                f"  [{issue.check}] "
                f"Word {issue.index} | Ayah {issue.ayah} | '{issue.text}' | "
                f"{issue.detail}"
            )
    # ── Promotion decision ────────────────────────────────────────────────────
    if hard_failures:
        log.error(f"Result     : FAILED - file not promoted")
        log.error(f"           : Fix the {len(hard_failures)} hard failure(s) before re-running")
    else:
        verified_dir.mkdir(parents=True, exist_ok=True)
        verified_path = verified_dir / f"{padded}_aligned.json"
        shutil.copy2(aligned_path, verified_path)
        log.info(f"Result     : PASSED - promoted to {verified_path}")
    log.info(report.summary())
    return report
# ── CLI Entry Point ───────────────────────────────────────────────────────────
def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate a WhisperX alignment output JSON for a single Surah."
    )
    parser.add_argument(
        "--surah",
        type=int,
        required=True,
        help="Surah number to validate (1-114)."
    )
    parser.add_argument(
        "--aligned-dir",
        type=Path,
        default=Path("data/output/aligned"),
        help="Directory containing raw aligned JSON. Default: data/output/aligned"
    )
    parser.add_argument(
        "--verified-dir",
        type=Path,
        default=Path("data/output/verified"),
        help="Directory for promoted verified JSON. Default: data/output/verified"
    )
    parser.add_argument(
        "--text-dir",
        type=Path,
        default=Path("data/text"),
        help="Directory containing Uthmani text cache. Default: data/text"
    )
    return parser.parse_args()
if __name__ == "__main__":
    args   = _parse_args()
    report = validate_surah(
        surah_number = args.surah,
        aligned_dir  = args.aligned_dir,
        verified_dir = args.verified_dir,
        text_dir     = args.text_dir
    )
    if not report.passed:
        raise SystemExit(1)
