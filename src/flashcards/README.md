# Media transcript artifacts

Python has one responsibility in the generic flashcard workflow: turn media
into an immutable, timed source-text artifact. It does not host the reviewer,
translate selections, store cards, or export a generic Anki package.

## Generate an artifact

```bash
python -m pip install -e ".[asr]"

# The only command allowed to download ASR weights.
flashcards-prepare-asr --model medium

# External SRT or WebVTT.
flashcards-import-media movie.mp4 \
  --subtitles movie.vi.srt \
  --language vi \
  --out projects/movie.json

# Matching embedded subtitles, then cache-only ASR fallback.
flashcards-import-media movie.mkv \
  --language vi \
  --asr-model medium \
  --out projects/movie.json

# Audio-only input follows the same fallback.
flashcards-import-media podcast.mp3 \
  --language vi \
  --asr-model medium \
  --out projects/podcast.json
```

Use `--subtitle-stream N` when matching embedded tracks are ambiguous, or
`--force-transcribe` to ignore embedded subtitles. Imports require exactly one
audio stream so the wrong language track is never selected silently. Model
weights are never downloaded by an import.

The output contract is `schema/media-artifact-v1.schema.json`. It contains only
media metadata, timed utterances, base tokens, and provenance. It deliberately
has no card drafts, enrichment results, learning units, progress, or review
state. Relative media paths are resolved from the artifact directory, and source
checksums are verified when the Scala reviewer opens it.

Stateful prototype manifests containing `cards` or `learning_units` are rejected.
Regenerate them with `flashcards-import-media --force`; an existing transcription
cache beside the output is reused, so this does not require retranscribing.

## Review into Anki

Install and run AnkiConnect, then start the reviewer:

```bash
sbt "mediaReviewerBackend/run projects/movie.json"
```

Open `http://127.0.0.1:8766`. Selecting any exact text span automatically sends
the sentence to Google for an English sentence translation, an exact contextual
selection translation, and whole-sentence source-language TTS. The source cue
and generated TTS have separate players. Review or edit both translations,
listen to the generated audio, and create the card. The exact previewed TTS
bytes are then sent to AnkiConnect; no reviewer state is written. The reviewer
loads Anki's current deck list, lets you choose the destination, and keeps a
visible success, duplicate, failure, or unknown-result message beside the Create
button. Successful results include the Anki note ID and destination deck.

Useful runtime options:

```bash
sbt "mediaReviewerBackend/run \
  --deck Default \
  --anki-model Cloze \
  projects/movie.json"
```

`--deck` sets the initial preference. If it exists, it is selected from the live
Anki deck list; otherwise the first available deck is selected.

The reviewer requires internet access for Google Translate and Google TTS.
Selected text and complete source sentences leave the machine; media files do
not. AnkiConnect remains restricted to loopback hosts. The configured Anki model
must expose `Text`, `Back Extra`, and `Translation`. The final fields are:

- `Text`: one exact-offset `{{c1::...}}` plus `[sound:...]`
- `Back Extra`: empty
- `Translation`: `<selection translation> : <sentence translation>`

Anki is the only system of record and handles duplicate rejection.
Generated audio is transient: one preparation is held in bounded memory per
WebSocket, expires after 30 minutes, and is discarded on reselection,
disconnect, successful creation, or duplicate rejection.

## Browser extension

```bash
sbt clausulaExtension/fullLinkJS
```

Load `extension/dist` as an unpacked Manifest V3 extension. It shares the Scala
card domain and preview UI but remains runtime-independent: page translation,
whole-sentence TTS, and AnkiConnect calls go through `extension/dist/background.js`.
The extension intentionally retains Clausula's `Default` deck and customized
`Cloze` model contract; the standalone reviewer exposes overrides as shown above.

## Architecture

All reviewer domain traffic uses the shared Scala `ClientMessage` and
`ServerMessage` ADTs over one WebSocket. HTTP serves only static files and
authenticated byte-range source/preview audio. Python/Scala compatibility is
guarded by the JSON schema and the shared `media-artifact-v1.json` golden fixture.
