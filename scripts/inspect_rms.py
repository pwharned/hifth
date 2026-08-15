"""
scripts/inspect_rms.py
----------------------
Prints a histogram of RMS values across a WAV file to help
calibrate SILENCE_THRESHOLD_RMS for a specific recording.
Usage:
    python scripts/inspect_rms.py --surah 2
"""
import argparse
import numpy as np
import whisperx
from pathlib import Path
def inspect(audio_path: Path) -> None:
    print(f"Loading {audio_path.name}...")
    audio = whisperx.load_audio(str(audio_path))
    window_samples = int(0.01 * 16000)   # 10ms windows
    rms_values = []
    for i in range(0, len(audio) - window_samples, window_samples):
        window = audio[i : i + window_samples]
        rms    = float(np.sqrt(np.mean(window ** 2)))
        rms_values.append(rms)
    rms_array = np.array(rms_values)
    print(f"\nRMS distribution across {len(rms_values)} windows:")
    print(f"  Min    : {rms_array.min():.5f}")
    print(f"  Max    : {rms_array.max():.5f}")
    print(f"  Mean   : {rms_array.mean():.5f}")
    print(f"  Median : {np.median(rms_array):.5f}")
    thresholds = [0.001, 0.005, 0.01, 0.02, 0.05]
    print(f"\nWindows below each threshold (i.e. counted as silence):")
    for t in thresholds:
        count = int((rms_array < t).sum())
        pct   = count / len(rms_array) * 100
        print(f"  < {t:.3f} : {count:6d} windows ({pct:.1f}%)")
parser = argparse.ArgumentParser()
parser.add_argument("--surah", type=int, required=True)
parser.add_argument(
    "--processed-dir",
    type=Path,
    default=Path("data/processed_audio")
)
args = parser.parse_args()
padded = str(args.surah).zfill(3)
inspect(args.processed_dir / f"{padded}.wav")
