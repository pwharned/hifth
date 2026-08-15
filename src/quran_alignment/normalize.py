"""
normalize.py
------------
Converts raw MP3 audio files to the format required by WhisperX:
    - 16kHz sampling rate
    - Mono channel
    - 16-bit PCM WAV
"""
import logging
import argparse
from pathlib import Path
from pydub import AudioSegment
# ── Constants ────────────────────────────────────────────────────────────────
TARGET_SAMPLE_RATE = 16000
TARGET_CHANNELS    = 1
TARGET_SAMPLE_WIDTH = 2  # 16-bit = 2 bytes
# ── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(message)s",
    datefmt="%H:%M:%S"
)
log = logging.getLogger(__name__)
# ── Core Logic ───────────────────────────────────────────────────────────────
def normalize_audio(input_path: Path, output_path: Path) -> Path:
    """
    Converts a single audio file to WhisperX-ready WAV format.
    Args:
        input_path:  Path to the source audio file (MP3 or WAV).
        output_path: Path where the normalized WAV will be written.
    Returns:
        The output_path on success.
    Raises:
        FileNotFoundError: If input_path does not exist.
        RuntimeError:      If conversion fails.
    """
    if not input_path.exists():
        raise FileNotFoundError(f"Input file not found: {input_path}")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    log.info(f"Loading   : {input_path.name}")
    audio = AudioSegment.from_file(str(input_path))
    log.info(
        f"Source    : {audio.frame_rate}Hz | "
        f"{audio.channels}ch | "
        f"{audio.sample_width * 8}-bit | "
        f"{len(audio) / 1000:.1f}s"
    )
    # Apply normalization steps
    audio = audio.set_frame_rate(TARGET_SAMPLE_RATE)
    audio = audio.set_channels(TARGET_CHANNELS)
    audio = audio.set_sample_width(TARGET_SAMPLE_WIDTH)
    audio.export(str(output_path), format="wav")
    log.info(f"Exported  : {output_path.name}")
    log.info(
        f"Output    : {audio.frame_rate}Hz | "
        f"{audio.channels}ch | "
        f"{audio.sample_width * 8}-bit"
    )
    return output_path
def normalize_surah(surah_number: int, raw_audio_dir: Path, processed_audio_dir: Path) -> Path:
    """
    Locates a raw Surah MP3 by surah number and normalizes it.
    Expects the file to be named using zero-padded surah number,
    e.g. 001.mp3, 002.mp3 ... 114.mp3
    Args:
        surah_number:        Integer surah number (1-114).
        raw_audio_dir:       Directory containing raw MP3 files.
        processed_audio_dir: Directory where normalized WAVs are written.
    Returns:
        Path to the normalized WAV file.
    Raises:
        FileNotFoundError: If no matching source file is found.
    """
    if not 1 <= surah_number <= 114:
        raise ValueError(f"Surah number must be between 1 and 114, got: {surah_number}")
    padded = str(surah_number).zfill(3)
    # Accept .mp3 or .wav source files
    candidates = list(raw_audio_dir.glob(f"{padded}.*"))
    candidates = [f for f in candidates if f.suffix.lower() in {".mp3", ".wav"}]
    if not candidates:
        raise FileNotFoundError(
            f"No audio file found for Surah {surah_number} "
            f"(looked for {padded}.mp3 / {padded}.wav in {raw_audio_dir})"
        )
    input_path  = candidates[0]
    output_path = processed_audio_dir / f"{padded}.wav"
    return normalize_audio(input_path, output_path)
# ── CLI Entry Point ───────────────────────────────────────────────────────────
def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Normalize a Surah audio file for WhisperX alignment."
    )
    parser.add_argument(
        "--surah",
        type=int,
        required=True,
        help="Surah number to process (1-114)."
    )
    parser.add_argument(
        "--raw-dir",
        type=Path,
        default=Path("data/raw_audio"),
        help="Directory containing raw MP3 files. Default: data/raw_audio"
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=Path("data/processed_audio"),
        help="Output directory for normalized WAVs. Default: data/processed_audio"
    )
    return parser.parse_args()
if __name__ == "__main__":
    args = _parse_args()
    output = normalize_surah(
        surah_number=args.surah,
        raw_audio_dir=args.raw_dir,
        processed_audio_dir=args.out_dir
    )
    log.info(f"Done. Normalized file: {output}")
