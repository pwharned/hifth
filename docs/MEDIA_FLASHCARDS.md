# Batch media and subtitle flashcards

The `flashcards` command batch-generates cards and sends them to AnkiConnect.
Anki is the review and editing interface: generate the complete batch, then
delete or edit cards in Anki as needed.

## Installation

```bash
python -m pip install -e ".[asr]"
```

Anki Desktop, AnkiConnect, and ffmpeg must be installed. Translation and TTS
use Google consumer endpoints and therefore require internet access.

## Subtitle batches

```bash
flashcards subtitles lesson.srt \
  --deck "Japanese::Lesson 1" \
  --language ja --translate-to en

flashcards subtitles lesson.ass \
  --deck "Japanese::Lesson 1" \
  --language ja --style JP-ja
```

Each cue is translated, given sentence TTS, and published with a deterministic
cue identity. A cache prevents repeated translation and synthesis. Re-running
updates existing cards rather than duplicating them.

## Media extraction and ASR

```bash
flashcards-prepare-asr --model medium
flashcards media movie.mkv --language vi --asr-model medium --out movie.json
flashcards media movie.mp4 --subtitles movie.vi.srt --language vi --out movie.json
```

Media extraction creates an immutable timed transcript artifact. The artifact
is a reusable source boundary for batch recipes; no separate web app is needed.

## Specialist workflows

```bash
flashcards classical migrate
flashcards classical repair-audio
flashcards classical repair-audio --tempo 0.9

flashcards quran align --surahs 1 2 3 --device cuda
flashcards quran export --qh-start 1 --qh-end 20
```

Audio repair is independent and idempotent. It derives each clip from the
current displayed card text, so it can be rerun after manual edits or fixes.
