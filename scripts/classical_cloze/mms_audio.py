"""Make every Corpus Cloze note's audio match its displayed text.

Anki is the only source of truth. A note's clip is a pure function of its own
`Text` field: the filename embeds a hash of (model, tempo, text), so a note is
current exactly when its `[sound:]` tag names the file for its present text.
Re-running changes nothing; editing a note's text and re-running regenerates
only that note's clip.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import importlib
import re
import subprocess
import tempfile
import unicodedata
from pathlib import Path
from typing import Any

from scripts.classical_cloze.pipeline import DESTINATION_DECK, MODEL_NAME, anki, batched_note_info

MODEL_IDS = {
    "greek": "facebook/mms-tts-grc",
    "latin": "facebook/mms-tts-lat",
}
DEFAULT_TEMPO = 0.8
# Bump to invalidate every existing clip (for example after a synthesis change).
AUDIO_VERSION = "v2"
BATCH_SIZE = 10
SOUND_RE = re.compile(r"\[sound:([^]]+)\]")
CLOZE_RE = re.compile(r"\{\{c\d+::(.*?)(?:::[^}]*)?\}\}")


def spoken_text(field: str) -> str:
    return unicodedata.normalize("NFKC", CLOZE_RE.sub(r"\1", SOUND_RE.sub("", field)))


def audio_filename(language: str, tempo: float, text: str) -> str:
    key = f"{AUDIO_VERSION}\0{MODEL_IDS[language]}\0{tempo}\0{text}"
    digest = hashlib.sha256(key.encode()).hexdigest()[:20]
    return f"classical_cloze_mms_{language}_t{round(tempo * 100)}_{digest}.wav"


def language_of(note: dict[str, Any]) -> str:
    languages = [tag.split("::", 1)[1] for tag in note["tags"] if tag.startswith("language::")]
    if len(languages) != 1 or languages[0] not in MODEL_IDS:
        raise RuntimeError(f"note {note['noteId']} needs exactly one language::greek|latin tag")
    return languages[0]


class Synthesizer:
    """Loads each MMS model on first use and renders text to a tempo-adjusted WAV."""

    def __init__(self) -> None:
        self.torch = importlib.import_module("torch")
        self.transformers = importlib.import_module("transformers")
        self.wavfile = importlib.import_module("scipy.io.wavfile")
        self.models: dict[str, tuple[Any, Any]] = {}

    def render(self, language: str, text: str, tempo: float, output: Path) -> None:
        if language not in self.models:
            model_id = MODEL_IDS[language]
            tokenizer = self.transformers.AutoTokenizer.from_pretrained(model_id)
            model = self.transformers.VitsModel.from_pretrained(model_id).eval()
            if self.torch.cuda.is_available():
                model = model.to("cuda")
            self.models[language] = (tokenizer, model)
        tokenizer, model = self.models[language]
        inputs = tokenizer(text, return_tensors="pt")
        if self.torch.cuda.is_available():
            inputs = {key: value.to("cuda") for key, value in inputs.items()}
        self.transformers.set_seed(555)
        with self.torch.inference_mode():
            waveform = model(**inputs).waveform[0].cpu().numpy()
        raw = output.with_suffix(".raw.wav")
        self.wavfile.write(raw, model.config.sampling_rate, waveform)
        subprocess.run([
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-i", str(raw), "-filter:a", f"atempo={tempo}", str(output),
        ], check=True)

    def close(self) -> None:
        self.models.clear()
        if self.torch.cuda.is_available():
            self.torch.cuda.empty_cache()


def sync_audio(tempo: float = DEFAULT_TEMPO, note_ids: list[int] | None = None) -> dict[str, int]:
    """Attach the correct clip to each stale note; return counts of what was done."""
    if note_ids is None:
        note_ids = anki("findNotes", query=f'deck:"{DESTINATION_DECK}" note:"{MODEL_NAME}"')
    in_media = set(anki("getMediaFilesNames", pattern="classical_cloze_mms_*"))
    stale = []
    for note in batched_note_info(note_ids):
        field = note["fields"]["Text"]["value"]
        text = spoken_text(field)
        language = language_of(note)
        filename = audio_filename(language, tempo, text)
        if SOUND_RE.findall(field) != [filename]:
            stale.append((note["noteId"], SOUND_RE.sub("", field), language, text, filename))
    synthesized = 0
    if stale:
        synthesizer = None
        try:
            with tempfile.TemporaryDirectory() as temporary:
                for start in range(0, len(stale), BATCH_SIZE):
                    updates = []
                    for note_id, field, language, text, filename in stale[start:start + BATCH_SIZE]:
                        if filename not in in_media:
                            synthesizer = synthesizer or Synthesizer()
                            path = Path(temporary) / filename
                            synthesizer.render(language, text, tempo, path)
                            stored = anki(
                                "storeMediaFile", filename=filename,
                                data=base64.b64encode(path.read_bytes()).decode(),
                            )
                            if stored != filename:
                                raise RuntimeError(f"Anki stored {filename} as {stored}")
                            in_media.add(filename)
                            synthesized += 1
                        updates.append({
                            "action": "updateNoteFields", "version": 6,
                            "params": {"note": {"id": note_id, "fields": {"Text": f"{field}[sound:{filename}]"}}},
                        })
                    anki("multi", actions=updates)
        finally:
            if synthesizer is not None:
                synthesizer.close()
    return {"notes": len(note_ids), "updated": len(stale), "synthesized": synthesized}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tempo", type=float, default=DEFAULT_TEMPO)
    args = parser.parse_args()
    if not 0.5 <= args.tempo <= 1.5:
        parser.error("--tempo must be between 0.5 and 1.5")
    print(sync_audio(args.tempo))


if __name__ == "__main__":
    main()
