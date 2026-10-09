"""Generate 90%-tempo MMS audio and attach it to manifest-backed Anki notes."""

from __future__ import annotations

import argparse
import base64
import hashlib
import importlib
import json
import re
import subprocess
import tempfile
import unicodedata
import urllib.request
from pathlib import Path
from typing import Any


ANKI_URL = "http://127.0.0.1:8765"
MODEL_IDS = {
    "greek": "facebook/mms-tts-grc",
    "latin": "facebook/mms-tts-lat",
}


def anki(action: str, **params: Any) -> Any:
    payload = json.dumps({"action": action, "version": 6, "params": params}).encode()
    request = urllib.request.Request(
        ANKI_URL, data=payload, headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(request, timeout=300) as response:
        result = json.load(response)
    if result.get("error"):
        raise RuntimeError(f"AnkiConnect {action}: {result['error']}")
    return result["result"]


def chunks(items: list[dict[str, Any]], size: int):
    for start in range(0, len(items), size):
        yield items[start:start + size]


def generate(manifest_path: Path, tempo: float) -> None:
    torch = importlib.import_module("torch")
    transformers = importlib.import_module("transformers")
    wavfile = importlib.import_module("scipy.io.wavfile")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    entries = [entry for entry in manifest["entries"] if entry.get("generated_note_id")]

    cache = manifest_path.parent / "mms-cache"
    cache.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as temporary:
        temporary_directory = Path(temporary)
        for language, model_id in MODEL_IDS.items():
            language_entries = [entry for entry in entries if entry["language"] == language]
            if not language_entries:
                continue
            tokenizer = transformers.AutoTokenizer.from_pretrained(model_id)
            model = transformers.VitsModel.from_pretrained(model_id).eval()
            if torch.cuda.is_available():
                model = model.to("cuda")
            generated = []
            for entry in language_entries:
                if entry.get("audio_provider") == model_id and entry.get("audio_tempo") == tempo:
                    continue
                text = unicodedata.normalize("NFKC", entry["primary"]["text"])
                digest = hashlib.sha256(f"{model_id}\0{tempo}\0{text}".encode()).hexdigest()[:20]
                filename = f"classical_cloze_mms_{language}_{digest}.wav"
                raw_path = temporary_directory / "raw.wav"
                output_path = cache / filename
                if not output_path.exists():
                    inputs = tokenizer(text, return_tensors="pt")
                    if torch.cuda.is_available():
                        inputs = {key: value.to("cuda") for key, value in inputs.items()}
                    transformers.set_seed(555)
                    with torch.inference_mode():
                        waveform = model(**inputs).waveform[0].cpu().numpy()
                    wavfile.write(raw_path, model.config.sampling_rate, waveform)
                    subprocess.run([
                        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                        "-i", str(raw_path), "-filter:a", f"atempo={tempo}", str(output_path),
                    ], check=True)
                generated.append((entry, filename, output_path))
                if len(generated) >= 10:
                    attach_batch(generated, model_id, tempo, manifest, manifest_path)
                    generated = []
            if generated:
                attach_batch(generated, model_id, tempo, manifest, manifest_path)
            del model
            if torch.cuda.is_available():
                torch.cuda.empty_cache()


def attach_batch(generated, model_id, tempo, manifest, manifest_path):
    store_actions = [{
        "action": "storeMediaFile", "version": 6,
        "params": {"filename": filename, "data": base64.b64encode(path.read_bytes()).decode()},
    } for _, filename, path in generated]
    stored_results = anki("multi", actions=store_actions)
    filenames = [result["result"] for result in stored_results]
    update_actions = []
    for (entry, _, _), filename in zip(generated, filenames, strict=True):
        text_field = re.sub(r"\[sound:[^]]+\]", "", entry["text"])
        update_actions.append({
            "action": "updateNoteFields", "version": 6,
            "params": {"note": {
                "id": entry["generated_note_id"],
                "fields": {"Text": f"{text_field}[sound:{filename}]"},
            }},
        })
        entry["audio_filename"] = filename
        entry["audio_provider"] = model_id
        entry["audio_tempo"] = tempo
    anki("multi", actions=update_actions)
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--tempo", type=float, default=0.9)
    args = parser.parse_args()
    if not 0.5 <= args.tempo <= 1.5:
        parser.error("--tempo must be between 0.5 and 1.5")
    generate(args.manifest, args.tempo)


if __name__ == "__main__":
    main()
