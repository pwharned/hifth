"""Generate MMS and Google TTS samples from the corpus manifest."""

from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import unicodedata
import urllib.parse
import urllib.request
from pathlib import Path


def google_audio(text: str, language: str) -> bytes:
    query = urllib.parse.urlencode({
        "ie": "UTF-8", "q": text,
        "tl": "el" if language == "greek" else "la", "client": "tw-ob",
    })
    request = urllib.request.Request(
        f"https://translate.googleapis.com/translate_tts?{query}",
        headers={
            "Referer": "https://translate.google.com/",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; WOW64) AppleWebKit/537.36",
        },
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        return response.read()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--output", type=Path, default=Path("data/classical_cloze/audio-comparison"))
    parser.add_argument("--per-language", type=int, default=2)
    args = parser.parse_args()

    wavfile = importlib.import_module("scipy.io.wavfile")
    torch = importlib.import_module("torch")
    transformers = importlib.import_module("transformers")

    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    args.output.mkdir(parents=True, exist_ok=True)
    selected = {}
    for language in ("greek", "latin"):
        selected[language] = [
            entry for entry in manifest["entries"]
            if entry["status"] == "matched" and entry["language"] == language
        ][:args.per_language]

    index = []
    for language, entries in selected.items():
        model_id = "facebook/mms-tts-grc" if language == "greek" else "facebook/mms-tts-lat"
        tokenizer = transformers.AutoTokenizer.from_pretrained(model_id)
        model = transformers.VitsModel.from_pretrained(model_id).eval()
        if torch.cuda.is_available():
            model = model.to("cuda")
        for number, entry in enumerate(entries, 1):
            text = unicodedata.normalize("NFKC", entry["primary"]["text"])
            stem = f"{language}-{number}-{hashlib.sha256(text.encode()).hexdigest()[:8]}"
            inputs = tokenizer(text, return_tensors="pt")
            if torch.cuda.is_available():
                inputs = {key: value.to("cuda") for key, value in inputs.items()}
            transformers.set_seed(555)
            with torch.inference_mode():
                waveform = model(**inputs).waveform[0].cpu().numpy()
            wavfile.write(args.output / f"{stem}-mms.wav", model.config.sampling_rate, waveform)
            (args.output / f"{stem}-google.mp3").write_bytes(google_audio(text, language))
            index.append({
                "language": language, "text": text,
                "mms": f"{stem}-mms.wav", "google": f"{stem}-google.mp3",
            })
        del model
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    (args.output / "index.json").write_text(
        json.dumps(index, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()
