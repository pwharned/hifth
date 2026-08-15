"""
align.py
--------
Runs a two-pass alignment pipeline to produce word-level millisecond
timestamps that correctly handle within-Ayah repetitions.
Pass 1: Free Whisper transcription
    Captures what the reciter actually spoke, including repetitions,
    with approximate word-level timestamps.
Pass 2: Forced alignment of the transcript
    Runs WhisperX forced alignment against the transcript (not the
    canonical text) to produce precise phoneme-level word boundaries.
Mapping:
    Maps transcript words back to canonical Uthmani word indices using
    Ayah-constrained sequence alignment. Repetitions are detected as
    backward jumps in canonical position within an Ayah. The last
    timestamp for each canonical word is used.
Key constraints exploited:
    - Repetitions never cross Ayah boundaries
    - Every word the reciter speaks exists in the canonical text
    - Ayahs are completed in strict forward order
"""
import gc
import json
import logging
import argparse
import time
import unicodedata
import re
from pathlib import Path
import numpy as np
import torch
import whisperx
import requests
# ── Constants ────────────────────────────────────────────────────────────────
WHISPER_MODEL    = "medium"
LANGUAGE         = "ar"
BATCH_SIZE       = 8
CHUNK_DURATION_S = 600.0
SAMPLE_RATE      = 16000
DEFAULT_DEVICE = "cuda" if torch.cuda.is_available() else "cpu"
COMPUTE_TYPE_MAP = {
    "cuda": "float16",
    "cpu":  "int8"
}
# Silence detection
SILENCE_THRESHOLD_RMS   = 0.020
MIN_SILENCE_DURATION_S  = 0.2
SILENCE_SEARCH_WINDOW_S = 60.0
QURAN_API_BASE  = "https://api.quran.com/api/v4"
VERSES_PER_PAGE = 286
# ── DP Alignment Constants ────────────────────────────────────────────────────
SKIP_CANONICAL_COST  = 0.5   # Cost for leaving a canonical word unmatched
SKIP_TRANSCRIPT_COST = 0.3   # Cost for skipping a transcript word (hallucination)
POSITION_WEIGHT      = 0.4   # Weight applied to positional deviation penalty
ALIGNMENT_BAND_WIDTH = 300   # Search window around expected position in DP
# Bismillah
BISMILLAH_WORDS = [
    {"surah": None, "ayah": 0, "position": 1, "text": "بِسْمِ",         "is_basmala": True},
    {"surah": None, "ayah": 0, "position": 2, "text": "ٱللَّهِ",        "is_basmala": True},
    {"surah": None, "ayah": 0, "position": 3, "text": "ٱلرَّحْمَـٰنِ", "is_basmala": True},
    {"surah": None, "ayah": 0, "position": 4, "text": "ٱلرَّحِيمِ",    "is_basmala": True},
]
SURAHS_WITHOUT_PREPENDED_BISMILLAH = {1, 9}
# ── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(message)s",
    datefmt="%H:%M:%S"
)
log = logging.getLogger(__name__)
# ── Text Utilities ────────────────────────────────────────────────────────────
# Arabic diacritic Unicode ranges:
#   064B-065F: Arabic combining marks (fatha, damma, kasra, sukun, shadda etc.)
#   0610-061A: Extended Arabic marks
#   06D6-06DC: Quranic annotation signs
#   06DF-06E4: Quranic annotation signs continued
#   06E7-06E8: Quranic annotation signs continued
#   06EA-06ED: Quranic annotation signs continued
_DIACRITIC_PATTERN = re.compile(
    r'[\u064B-\u065F\u0610-\u061A\u06D6-\u06DC\u06DF-\u06E4\u06E7\u06E8\u06EA-\u06ED]'
)
# Tatweel (elongation character)
_TATWEEL_PATTERN = re.compile(r'\u0640')
# Alef variants → bare alef for matching only
_ALEF_PATTERN = re.compile(r'[\u0622\u0623\u0625\u0671]')
def strip_diacritics(text: str) -> str:
    """
    Removes diacritical marks from Arabic text for fuzzy matching.
    Strips vowel marks and Quranic annotation signs.
    Does NOT normalize hamza or alef variants - these carry meaning.
    Does NOT normalize for display - only for matching.
    """
    text = _DIACRITIC_PATTERN.sub('', text)
    text = _TATWEEL_PATTERN.sub('', text)
    return text.strip()
def edit_distance(a: str, b: str) -> int:
    """
    Standard Levenshtein edit distance between two strings.
    Operates on the stripped forms passed in.
    """
    if not a: return len(b)
    if not b: return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a):
        curr = [i + 1]
        for j, cb in enumerate(b):
            curr.append(min(
                prev[j + 1] + 1,    # deletion
                curr[j]     + 1,    # insertion
                prev[j] + (0 if ca == cb else 1)  # substitution
            ))
        prev = curr
    return prev[-1]
def similarity_score(transcript_word: str, canonical_word: str) -> float:
    """
    Returns a similarity score in [0, 1] between a transcript word
    and a canonical word. Both are stripped of diacritics before
    comparison. 1.0 = identical, 0.0 = completely different.
    """
    a = strip_diacritics(transcript_word)
    b = strip_diacritics(canonical_word)
    if not a and not b: return 1.0
    if not a or not b:  return 0.0
    max_len = max(len(a), len(b))
    dist    = edit_distance(a, b)
    return 1.0 - dist / max_len
# ── Text Fetching ─────────────────────────────────────────────────────────────
def _prepend_bismillah(surah_number: int, word_list: list[dict]) -> list[dict]:
    if surah_number in SURAHS_WITHOUT_PREPENDED_BISMILLAH:
        return word_list
    basmala = [{**w, "surah": surah_number} for w in BISMILLAH_WORDS]
    return basmala + word_list
def fetch_uthmani_text(surah_number: int, text_dir: Path) -> list[dict]:
    """
    Returns the Uthmani word list for a Surah including Bismillah where
    appropriate. Fetches from Quran.com API v4 on first call, then
    serves from local cache.
    """
    text_dir.mkdir(parents=True, exist_ok=True)
    cache_path = text_dir / f"{str(surah_number).zfill(3)}_uthmani.json"
    if cache_path.exists():
        log.info(f"Text cache : Loading from {cache_path.name}")
        with open(cache_path, encoding="utf-8") as f:
            return json.load(f)
    log.info(f"Text fetch : Requesting Surah {surah_number} from Quran.com API v4")
    url = (
        f"{QURAN_API_BASE}/verses/by_chapter/{surah_number}"
        f"?words=true"
        f"&word_fields=text_uthmani"
        f"&per_page={VERSES_PER_PAGE}"
        f"&page=1"
    )
    response = requests.get(url, timeout=15)
    if response.status_code != 200:
        raise RuntimeError(
            f"Quran.com API returned status {response.status_code} "
            f"for Surah {surah_number}. URL: {url}"
        )
    data = response.json()
    if "verses" not in data: 
        raise ValueError(
            f"Unexpected API response. Expected 'verses', got: {list(data.keys())}"
        )
    word_list = []
    for verse in data["verses"]:
        ayah_number = verse["verse_number"]
        for word in verse["words"]:
            if word.get("char_type_name") == "end":
                continue
            word_list.append({
                "surah":    surah_number,
                "ayah":     ayah_number,
                "position": word["position"],
                "text":     word["text_uthmani"]
            })
    if not word_list:
        raise ValueError(f"No word tokens extracted for Surah {surah_number}.")
    word_list = _prepend_bismillah(surah_number, word_list)
    log.info(f"Text fetch : {len(word_list)} word tokens extracted")
    with open(cache_path, "w", encoding="utf-8") as f:
        json.dump(word_list, f, ensure_ascii=False, indent=2)
    log.info(f"Text cache : Saved to {cache_path.name}")
    return word_list
# ── Silence Detection ─────────────────────────────────────────────────────────
def _interpolate_timestamps(
    resolved:        list[dict],
    canonical_words: list[dict]
) -> list[dict]:
    """
    Fills in timestamps for canonical words that had no transcript match
    at all (timestamp_source == "interpolated").
    Words with timestamp_source == "nearby" already have real timestamps
    from the transcript and are left unchanged.
    """
    n = len(resolved)
    i = 0
    while i < n:
        if resolved[i]["timestamp_source"] != "interpolated":
            i += 1
            continue
        # Found start of a true-gap run
        run_start = i
        while i < n and resolved[i]["timestamp_source"] == "interpolated":
            i += 1
        run_end = i  # exclusive
        # Find anchor timestamps from nearest non-interpolated neighbours
        prev_end_ms = 0.0
        for k in range(run_start - 1, -1, -1):
            if resolved[k]["timestamp_source"] != "interpolated":
                prev_end_ms = resolved[k]["end_ms"]
                break
        next_start_ms = prev_end_ms
        for k in range(run_end, n):
            if resolved[k]["timestamp_source"] != "interpolated":
                next_start_ms = resolved[k]["start_ms"]
                break
        run_length    = run_end - run_start
        slot_duration = (next_start_ms - prev_end_ms) / run_length
        for k, idx in enumerate(range(run_start, run_end)):
            slot_start = prev_end_ms + k * slot_duration
            slot_end   = slot_start + slot_duration
            resolved[idx]["start_ms"] = round(slot_start, 1)
            resolved[idx]["end_ms"]   = round(slot_end,   1)
            log.debug(
                f"Interpolated: '{canonical_words[idx]['text']}' "
                f"→ {slot_start:.0f}ms – {slot_end:.0f}ms"
            )
    return resolved
def _dp_align(
    transcript_words:  list[dict],
    canonical_words:   list[dict],
    audio_duration_s:  float
) -> list[tuple[int, int | None, str]]:
    """
    Aligns transcript words to canonical words using banded dynamic programming.
    Returns a list of (canonical_idx, transcript_idx, match_type) tuples where
    match_type is one of:
        "match"   — canonical word directly matched to a transcript word
        "nearby"  — canonical word was skipped by DP but a transcript word
                    was present at that position (hallucination case —
                    use transcript timestamp, substitute canonical text)
        "gap"     — canonical word has no nearby transcript word at all
                    (true absence — requires timestamp interpolation)
    """
    N = len(canonical_words)
    M = len(transcript_words)
    if N == 0 or M == 0:
        return [(i, None, "gap") for i in range(N)]
    INF      = float("inf")
    half_band = ALIGNMENT_BAND_WIDTH // 2
    dp        = [[INF] * (M + 1) for _ in range(N + 1)]
    traceback = [[""] * (M + 1) for _ in range(N + 1)]
    dp[0][0] = 0.0
    for i in range(1, N + 1):
        dp[i][0]        = dp[i - 1][0] + SKIP_CANONICAL_COST
        traceback[i][0] = "skip_canonical"
    for j in range(1, M + 1):
        dp[0][j]        = dp[0][j - 1] + SKIP_TRANSCRIPT_COST
        traceback[0][j] = "skip_transcript"
    for i in range(1, N + 1):
        expected_j = int((i - 1) * M / N)
        j_lo = max(1, expected_j - half_band)
        j_hi = min(M, expected_j + half_band)
        for j in range(j_lo, j_hi + 1):
            cost_match = dp[i - 1][j - 1] + _match_cost(
                transcript_words[j - 1],
                canonical_words[i - 1],
                i - 1,
                N,
                audio_duration_s
            )
            cost_skip_canon = dp[i - 1][j] + SKIP_CANONICAL_COST
            cost_skip_trans = dp[i][j - 1] + SKIP_TRANSCRIPT_COST
            best = min(cost_match, cost_skip_canon, cost_skip_trans)
            dp[i][j] = best
            if best == cost_match:
                traceback[i][j] = "match"
            elif best == cost_skip_canon:
                traceback[i][j] = "skip_canonical"
            else:
                traceback[i][j] = "skip_transcript"
    # ── Traceback ─────────────────────────────────────────────────────────────
    best_end_j    = M
    best_end_cost = dp[N][M]
    for j in range(M + 1):
        if dp[N][j] < best_end_cost:
            best_end_cost = dp[N][j]
            best_end_j    = j
    alignment: list[tuple[int, int | None, str]] = []
    i, j = N, best_end_j
    while i > 0 or j > 0:
        move = traceback[i][j]
        if move == "match":
            alignment.append((i - 1, j - 1, "match"))
            i -= 1
            j -= 1
        elif move == "skip_canonical":
            # A transcript word was present at this position but the DP
            # chose not to match it — this is Case 2 (hallucination).
            # Record the nearest transcript word index so its timestamp
            # can be reused even though we substitute the canonical text.
            nearby_j = j - 1 if j > 0 else None
            match_type = "nearby" if nearby_j is not None else "gap"
            alignment.append((i - 1, nearby_j, match_type))
            i -= 1
        elif move == "skip_transcript":
            j -= 1
        else:
            # Outside band — true gap
            alignment.append((i - 1, None, "gap"))
            i -= 1
    alignment.reverse()
    return alignment

def map_transcript_to_canonical(
    transcript_words: list[dict],
    canonical_words:  list[dict],
    audio_duration_s: float = 0.0
) -> list[dict]:
    """
    Maps transcript words to canonical Uthmani word indices using
    banded dynamic programming sequence alignment.
    Three timestamp sources are distinguished:
        "aligned"      — timestamp from forced alignment, direct match
        "nearby"       — timestamp from forced alignment, DP skipped the
                         canonical word but a transcript word was present
                         (Whisper hallucinated a different word — we keep
                         the timestamp and substitute the canonical text)
        "interpolated" — no transcript word nearby at all, timestamp is
                         linearly interpolated between neighbours
                         (true gap — word genuinely absent from transcript)
    """
    N = len(canonical_words)
    M = len(transcript_words)
    log.info(f"DP align   : {N} canonical words, {M} transcript words")
    # ── Step 1: Global DP alignment ───────────────────────────────────────────
    alignment = _dp_align(transcript_words, canonical_words, audio_duration_s)
    matched_count  = sum(1 for _, _, t in alignment if t == "match")
    nearby_count   = sum(1 for _, _, t in alignment if t == "nearby")
    gap_count      = sum(1 for _, _, t in alignment if t == "gap")
    log.info(
        f"DP align   : {matched_count} matched | "
        f"{nearby_count} nearby (hallucination) | "
        f"{gap_count} true gaps"
    )
    # ── Step 2: Repetition resolution ────────────────────────────────────────
    alignment = _resolve_repetitions(alignment, transcript_words, canonical_words)
    # ── Step 3: Accumulate occurrences per canonical word ─────────────────────
    # occurrences[canonical_idx] = list of (start_ms, end_ms, match_type)
    occurrences: dict[int, list[tuple[float, float, str]]] = {
        i: [] for i in range(N)
    }
    for canon_idx, trans_idx, match_type in alignment:
        if trans_idx is not None:
            t_word   = transcript_words[trans_idx]
            start_ms = round(t_word["start"] * 1000, 1)
            end_ms   = round(t_word["end"]   * 1000, 1)
            occurrences[canon_idx].append((start_ms, end_ms, match_type))
    # ── Step 4: Resolve to last occurrence, flag source ──────────────────────
    resolved = []
    for idx, word in enumerate(canonical_words):
        occ = occurrences[idx]
        if occ:
            start_ms, end_ms, match_type = occ[-1]
            resolved.append({
                "meta":             word,
                "start_ms":         start_ms,
                "end_ms":           end_ms,
                "repetitions":      len(occ),
                "timestamp_source": match_type   # "match" or "nearby"
            })
        else:
            # True gap — will be filled by interpolation
            resolved.append({
                "meta":             word,
                "start_ms":         0.0,
                "end_ms":           0.0,
                "repetitions":      0,
                "timestamp_source": "interpolated"
            })
    # ── Step 5: Interpolate timestamps for true gaps ──────────────────────────
    resolved = _interpolate_timestamps(resolved, canonical_words)
    total_reps = sum(r["repetitions"] - 1 for r in resolved if r["repetitions"] > 1)
    log.info(
        f"Repetitions: {total_reps} repeated word occurrences resolved"
    )
    return resolved

def _find_silence_boundaries(
    audio: np.ndarray,
    min_silence_duration_s: float = MIN_SILENCE_DURATION_S,
    threshold_rms: float = SILENCE_THRESHOLD_RMS
) -> list[float]:
    window_samples  = int(0.01 * SAMPLE_RATE)
    min_sil_samples = int(min_silence_duration_s * SAMPLE_RATE)
    silence_boundaries = []
    in_silence         = False
    silence_start      = 0
    for i in range(0, len(audio) - window_samples, window_samples):
        window = audio[i : i + window_samples]
        rms    = float(np.sqrt(np.mean(window ** 2)))
        if rms < threshold_rms:
            if not in_silence:
                in_silence    = True
                silence_start = i
        else:
            if in_silence:
                in_silence     = False
                silence_length = i - silence_start
                if silence_length >= min_sil_samples:
                    midpoint_s = (silence_start + silence_length / 2) / SAMPLE_RATE
                    silence_boundaries.append(midpoint_s)
    if in_silence:
        silence_length = len(audio) - silence_start
        if silence_length >= min_sil_samples:
            midpoint_s = (silence_start + silence_length / 2) / SAMPLE_RATE
            silence_boundaries.append(midpoint_s)
    return silence_boundaries
def _find_nearest_silence(
    target_s: float,
    silence_boundaries: list[float],
    search_window_s: float = SILENCE_SEARCH_WINDOW_S
) -> float | None:
    candidates = [s for s in silence_boundaries if abs(s - target_s) <= search_window_s]
    if not candidates:
        return None
    return min(candidates, key=lambda s: abs(s - target_s))
# ── Chunking ──────────────────────────────────────────────────────────────────
def _split_audio_at_silences(
    audio: np.ndarray,
    chunk_duration_s: float,
    silence_boundaries: list[float]
) -> list[tuple[np.ndarray, float]]:
    audio_duration_s = len(audio) / SAMPLE_RATE
    split_points_s: list[float] = [0.0]
    target_s = chunk_duration_s
    while target_s < audio_duration_s:
        nearest = _find_nearest_silence(target_s, silence_boundaries)
        if nearest is not None:
            split_points_s.append(nearest)
            log.info(f"Split point: target={target_s:.1f}s → silence at {nearest:.1f}s")
        else:
            log.warning(f"No silence near {target_s:.1f}s - forcing split at target")
            split_points_s.append(target_s)
        target_s += chunk_duration_s
    split_points_s.append(audio_duration_s)
    split_points_s = sorted(set(split_points_s))
    chunks = []
    for i in range(len(split_points_s) - 1):
        start_s      = split_points_s[i]
        end_s        = split_points_s[i + 1]
        start_sample = int(start_s * SAMPLE_RATE)
        end_sample   = int(end_s   * SAMPLE_RATE)
        chunks.append((audio[start_sample:end_sample], start_s))
        log.info(f"Chunk {i+1}: {start_s:.1f}s → {end_s:.1f}s ({end_s - start_s:.1f}s)")
    return chunks
# ── Ayah-Constrained Sequence Mapper ─────────────────────────────────────────
def _group_canonical_by_ayah(
    word_list: list[dict]
) -> dict[int, list[dict]]:
    """
    Groups canonical word list by Ayah number.
    Bismillah words (ayah=0) are treated as a single pseudo-Ayah.
    Returns dict mapping ayah_number → list of word dicts in order.
    """
    groups: dict[int, list[dict]] = {}
    for word in word_list:
        ayah = word["ayah"]
        groups.setdefault(ayah, []).append(word)
    return groups
def _match_word_to_ayah(
    transcript_word: str,
    ayah_words:      list[dict],
    current_pos:     int
) -> int:
    """
    Finds the best matching canonical position within an Ayah for a
    transcript word. Searches forward from current_pos first, then
    wraps to the beginning to handle repetitions.
    Args:
        transcript_word: The word as Whisper transcribed it.
        ayah_words:      Canonical words for this Ayah.
        current_pos:     Current position in the Ayah (0-indexed).
    Returns:
        The 0-indexed canonical position that best matches.
    """
    n = len(ayah_words)
    # Build search order: from current_pos forward, then from 0
    search_order = list(range(current_pos, n)) + list(range(0, current_pos))
    best_pos   = current_pos % n
    best_score = -1.0
    for pos in search_order:
        score = similarity_score(transcript_word, ayah_words[pos]["text"])
        if score > best_score:
            best_score = score
            best_pos   = pos
        # Early exit on very high confidence match
        if score >= 0.9:
            break
    return best_pos


# ── Transcription ─────────────────────────────────────────────────────────────
def _transcribe_chunk(
    chunk_audio: np.ndarray,
    model,
    batch_size: int = BATCH_SIZE
) -> list[dict]:
    """
    Runs Whisper free transcription on a chunk.
    Returns segment-level dicts with text.
    Word-level timestamps are produced in the forced alignment pass.
    """
    result = model.transcribe(chunk_audio, batch_size=batch_size)
    return result.get("segments", [])
def _forced_align_chunk(
    chunk_audio:      np.ndarray,
    transcript_segs:  list[dict],
    chunk_duration_s: float,
    model_a,
    metadata,
    device:           str
) -> list[dict]:
    """
    Runs WhisperX forced alignment against transcript segments
    to produce word-level timestamps.
    Args:
        chunk_audio:     Mono float32 audio at 16kHz.
        transcript_segs: Segment dicts from _transcribe_chunk.
        chunk_duration_s: Duration of this chunk in seconds.
        model_a:         WhisperX alignment model.
        meta        Alignment model metadata.
        device:          "cuda" or "cpu".
    Returns:
        List of word dicts: {"word": str, "start": float, "end": float}
    """
    if not transcript_segs:
        return []
    # Ensure segments have required start/end fields
    # whisperx.align expects these to be present
    for seg in transcript_segs:
        if "start" not in seg:
            seg["start"] = 0.0
        if "end" not in seg:
            seg["end"] = chunk_duration_s
    aligned = whisperx.align(
        transcript_segs,
        model_a,
        metadata,
        chunk_audio,
        device,
        return_char_alignments=False
    )
    words = []
    for word in aligned.get("word_segments", []):
        text = word.get("word", "").strip()
        if not text:
            continue
        words.append({
            "word":  text,
            "start": word.get("start", 0.0),
            "end":   word.get("end",   0.0)
        })
    return words

# ── DP Sequence Aligner ───────────────────────────────────────────────────────
def _match_cost(
    transcript_word: dict,
    canonical_word:  dict,
    canonical_idx:   int,
    total_canonical: int,
    audio_duration_s: float
) -> float:
    """
    Cost of matching a single transcript word to a single canonical word.
    Combines text similarity with a positional deviation penalty.
    A cost of 0.0 means a perfect match at the expected position.
    A cost of 1.0+ means a very poor text match or large positional deviation.
    Args:
        transcript_word:  Word dict from Pass 2, has "word", "start", "end".
        canonical_word:   Word dict from fetch_uthmani_text, has "text".
        canonical_idx:    0-based index of this canonical word in the full list.
        total_canonical:  Total number of canonical words in this Surah.
        audio_duration_s: Total audio duration in seconds.
    Returns:
        Float cost value. Lower is better.
    """
    # Text similarity component — 0.0 perfect, 1.0 completely different
    text_cost = 1.0 - similarity_score(
        transcript_word["word"],
        canonical_word["text"]
    )
    # Positional deviation component
    # Expected: canonical word i should appear at roughly (i / N) through the audio
    if audio_duration_s > 0 and total_canonical > 0:
        expected_norm = canonical_idx / total_canonical
        actual_norm   = transcript_word["start"] / audio_duration_s
        position_cost = abs(actual_norm - expected_norm)
    else:
        position_cost = 0.0
    return text_cost + POSITION_WEIGHT * position_cost
def _resolve_repetitions(
    alignment:        list[tuple[int, int | None, str]],
    transcript_words: list[dict],
    canonical_words:  list[dict]
) -> list[tuple[int, int | None, str]]:
    """
    Detects and resolves repetitions within Ayah boundaries.
    After the primary DP alignment, transcript words that were skipped
    as "extras" may be repetitions of canonical words within the same Ayah.
    For each such unmatched transcript word, if a high-similarity canonical
    word exists at a nearby position within the same Ayah, we record the
    match as a repetition. The last occurrence timestamp wins.
    Args:
        alignment:        Output of _dp_align — (canonical_idx, transcript_idx, match_type).
        transcript_words: Pass 2 word list.
        canonical_words:  Canonical Uthmani word list.
    Returns:
        Updated alignment with repetition matches appended and sorted by
        canonical index. Each canonical word may appear multiple times —
        the caller resolves to the last occurrence.
    """
    # Build set of already-matched transcript indices
    matched_transcript = {
        t_idx
        for _, t_idx, _ in alignment
        if t_idx is not None
    }
    # Find unmatched transcript words
    unmatched_transcript = [
        j for j in range(len(transcript_words))
        if j not in matched_transcript
    ]
    if not unmatched_transcript:
        return alignment
    # Build a map: canonical_idx → ayah number for boundary checks
    canon_ayah = {i: w["ayah"] for i, w in enumerate(canonical_words)}
    extra_matches: list[tuple[int, int, str]] = []
    for j in unmatched_transcript:
        t_word    = transcript_words[j]
        t_start_s = t_word["start"]
        # Find canonical words whose timestamps bracket this transcript word
        nearby_canon = [
            c_idx
            for c_idx, t_idx, _ in alignment
            if t_idx is not None
            and abs(transcript_words[t_idx]["start"] - t_start_s) < 60.0
        ]
        if not nearby_canon:
            continue
        # Find the nearest matched canonical word by timestamp
        ref_canon_idx = min(
            nearby_canon,
            key=lambda c: abs(
                transcript_words[
                    next(t for cc, t, _ in alignment if cc == c and t is not None)
                ]["start"] - t_start_s
            )
        )
        target_ayah = canon_ayah[ref_canon_idx]
        # Search canonical words in this Ayah for the best text match
        best_score     = 0.0
        best_canon_idx = None
        for c_idx, word in enumerate(canonical_words):
            if word["ayah"] != target_ayah:
                continue
            score = similarity_score(t_word["word"], word["text"])
            if score > best_score and score >= 0.7:
                best_score     = score
                best_canon_idx = c_idx
        if best_canon_idx is not None:
            extra_matches.append((best_canon_idx, j, "match"))
            log.debug(
                f"Repetition : transcript word '{t_word['word']}' "
                f"→ canonical '{canonical_words[best_canon_idx]['text']}' "
                f"(Ayah {target_ayah}, score={best_score:.2f})"
            )
    combined = alignment + extra_matches
    combined.sort(key=lambda x: (x[0], x[1] if x[1] is not None else -1))
    return combined


# ── Memory ────────────────────────────────────────────────────────────────────
def _release_memory() -> None:
    gc.collect()
    if torch.cuda.is_available():
        torch.cuda.empty_cache()
# ── Main Pipeline ─────────────────────────────────────────────────────────────

def run_alignment(
    surah_number:        int,
    processed_audio_dir: Path,
    text_dir:            Path,
    output_dir:          Path,
    device:              str   = DEFAULT_DEVICE,
    chunk_duration_s:    float = CHUNK_DURATION_S
) -> Path:
    """
    Runs the two-pass alignment pipeline for a single Surah.
    Pass 1: Free Whisper transcription (captures actual spoken sequence
            including repetitions)
    Pass 2: WhisperX forced alignment of transcript (refines boundaries)
    Mapping: Ayah-constrained sequence alignment maps transcript words
             back to canonical indices, resolving repetitions by taking
             the last timestamp per canonical word.
    Args:
        surah_number:        Surah to process (1-114).
        processed_audio_dir: Directory containing normalized WAV files.
        text_dir:            Directory containing cached Uthmani text JSON.
        output_dir:          Directory to write raw alignment JSON output.
        device:              "cuda" or "cpu".
        chunk_duration_s:    Target chunk size for memory management.
    """
    compute_type = COMPUTE_TYPE_MAP[device]
    log.info(f"Device     : {device} ({compute_type})")
    log.info(f"Chunk size : {chunk_duration_s:.0f}s target")
    if device == "cuda" and not torch.cuda.is_available():
        raise RuntimeError("CUDA requested but not available. Use --device cpu.")
    padded     = str(surah_number).zfill(3)
    audio_path = processed_audio_dir / f"{padded}.wav"
    if not audio_path.exists():
        raise FileNotFoundError(
            f"Normalized WAV not found: {audio_path}. Run normalize.py first."
        )
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"{padded}_aligned.json"
    intermediate_dir = output_dir.parent / "intermediate"
    intermediate_dir.mkdir(parents=True, exist_ok=True)
    # ── Step 1: Fetch canonical text ────────────────────────────────────────
    canonical_words = fetch_uthmani_text(surah_number, text_dir)
    log.info(f"Canonical  : {len(canonical_words)} words")
    # ── Step 2: Load audio ──────────────────────────────────────────────────
    log.info(f"Audio      : Loading {audio_path.name}")
    audio            = whisperx.load_audio(str(audio_path))
    audio_duration_s = len(audio) / SAMPLE_RATE
    log.info(f"Audio      : {audio_duration_s:.1f}s ({audio_duration_s/60:.1f} min)")
    # ── Step 3: Detect silence + split ──────────────────────────────────────
    log.info(f"Silence    : Scanning...")
    silence_boundaries = _find_silence_boundaries(audio)
    log.info(f"Silence    : {len(silence_boundaries)} boundaries found")
    chunks = _split_audio_at_silences(audio, chunk_duration_s, silence_boundaries)
    log.info(f"Chunks     : {len(chunks)}")
    del audio
    _release_memory()
    # ── Step 4: Load Whisper model for transcription ─────────────────────────
    log.info(f"Model      : Loading Whisper {WHISPER_MODEL} for transcription")
    whisper_model = whisperx.load_model(
        WHISPER_MODEL, device, compute_type=compute_type,
        language=LANGUAGE
    )
    # ── Step 5: Pass 1 - Free transcription per chunk ───────────────────────
    log.info(f"Pass 1     : Free transcription")
    all_transcript_words: list[dict] = []
    t_start = time.time()
    for i, (chunk_audio, chunk_offset_s) in enumerate(chunks):
        log.info(f"           Chunk {i+1}/{len(chunks)} | offset={chunk_offset_s:.1f}s")
        t_words = _transcribe_chunk(chunk_audio, whisper_model)
        for w in t_words:
            w["start"] += chunk_offset_s
            w["end"]   += chunk_offset_s
        log.info(f"           → {len(t_words)} transcript words")
        all_transcript_words.extend(t_words)
    log.info(
        f"Pass 1     : Complete in {time.time() - t_start:.1f}s | "
        f"{len(all_transcript_words)} total transcript words"
    )
    pass1_path = intermediate_dir / f"{padded}_pass1_transcript.json"
    with open(pass1_path, "w", encoding="utf-8") as f:
        json.dump(all_transcript_words, f, ensure_ascii=False, indent=2)
    log.info(f"Saved      : Pass 1 transcript → {pass1_path.name}")
    # Free Whisper model before loading alignment model
    del whisper_model
    _release_memory()
    # ── Step 6: Load alignment model ────────────────────────────────────────
    log.info(f"Model      : Loading WhisperX alignment model (Arabic)")
    model_a, metadata = whisperx.load_align_model(
        language_code=LANGUAGE, device=device
    )
    # ── Step 7: Pass 2 - Forced alignment of transcript per chunk ───────────
    log.info(f"Pass 2     : Forced alignment of transcript")
    refined_transcript: list[dict] = []
    t_start = time.time()
    for i, (chunk_audio, chunk_offset_s) in enumerate(chunks):
        chunk_duration  = len(chunk_audio) / SAMPLE_RATE
        chunk_end_s     = chunk_offset_s + chunk_duration
        chunk_segs = [
            {
                **seg,
                "start": seg["start"] - chunk_offset_s,
                "end":   seg["end"]   - chunk_offset_s
            }
            for seg in all_transcript_words
            if chunk_offset_s <= seg.get("start", 0.0) < chunk_end_s
        ]
        if not chunk_segs:
            log.warning(f"Chunk {i+1} has no transcript segments - skipping")
            continue
        log.info(
            f"           Chunk {i+1}/{len(chunks)} | "
            f"{len(chunk_segs)} segments | offset={chunk_offset_s:.1f}s"
        )
        aligned_words = _forced_align_chunk(
            chunk_audio      = chunk_audio,
            transcript_segs  = chunk_segs,
            chunk_duration_s = chunk_duration,
            model_a          = model_a,
            metadata         = metadata,
            device           = device
        )
        for w in aligned_words:
            w["start"] += chunk_offset_s
            w["end"]   += chunk_offset_s
        log.info(f"           → {len(aligned_words)} word-level timestamps")
        refined_transcript.extend(aligned_words)
    log.info(
        f"Pass 2     : Complete in {time.time() - t_start:.1f}s | "
        f"{len(refined_transcript)} aligned words"
    )
    pass2_path = intermediate_dir / f"{padded}_pass2_aligned.json"
    with open(pass2_path, "w", encoding="utf-8") as f:
        json.dump(refined_transcript, f, ensure_ascii=False, indent=2)
    log.info(f"Saved      : Pass 2 aligned → {pass2_path.name}")
    del model_a
    _release_memory()
    # ── Step 8: Map transcript → canonical ──────────────────────────────────
    log.info(f"Mapping    : Transcript → canonical Uthmani")
    t_start = time.time()
    t_start  = time.time()
    resolved = map_transcript_to_canonical(
        transcript_words = refined_transcript,
        canonical_words  = canonical_words,
        audio_duration_s = audio_duration_s
    )
    total_reps = sum(r["repetitions"] - 1 for r in resolved if r["repetitions"] > 1)
    log.info(
        f"Mapping    : Complete in {time.time() - t_start:.1f}s | "
        f"{total_reps} repeated word occurrences resolved"
    )
    # ── Step 9: Build output ─────────────────────────────────────────────────
    output_words = []
    for i, item in enumerate(resolved):
        meta   = item["meta"]
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
            "timestamp_source":       source,
            "timestamp_interpolated": source == "interpolated"
        })
    # ── Step 10: Write output ────────────────────────────────────────────────
    result = {
        "surah_number":      surah_number,
        "total_words":       len(output_words),
        "audio_duration_ms": round(audio_duration_s * 1000, 1),
        "words":             output_words
    }
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    log.info(f"Output     : Written to {output_path}")
    _release_memory()
    return output_path

# ── CLI ───────────────────────────────────────────────────────────────────────
def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Two-pass Quran alignment pipeline with repetition handling."
    )
    parser.add_argument("--surah",          type=int,   required=True)
    parser.add_argument("--device",         type=str,   choices=["cuda","cpu"], default=DEFAULT_DEVICE)
    parser.add_argument("--chunk-duration", type=float, default=CHUNK_DURATION_S)
    parser.add_argument("--processed-dir",  type=Path,  default=Path("data/processed_audio"))
    parser.add_argument("--text-dir",       type=Path,  default=Path("data/text"))
    parser.add_argument("--output-dir",     type=Path,  default=Path("data/output/aligned"))
    return parser.parse_args()
if __name__ == "__main__":
    args = _parse_args()
    output = run_alignment(
        surah_number        = args.surah,
        processed_audio_dir = args.processed_dir,
        text_dir            = args.text_dir,
        output_dir          = args.output_dir,
        device              = args.device,
        chunk_duration_s    = args.chunk_duration
    )
    log.info(f"Done: {output}")
