# Hifth

A Quran memorization toolkit built on word-level audio/text alignment.

Raw recitation audio is force-aligned to the Uthmani text with WhisperX,
producing millisecond-accurate timestamps for every word. That alignment
data powers two independent outputs:

1. **A full-stack web app** ("Archipelago") — a Scala 3 / ScalaJS study
   player with an SRS-driven queue (SM-2), an audio-synced player with a
   progressive cloze mask (0→95%), tap-to-score recall, and streaks —
   organized around the Quran's 240 Quarter-Hizb (QH) sections.
2. **An Anki export pipeline** — a standalone Python script that turns the
   same alignment data into a deck of interactive flashcards (one per QH),
   without needing any of the app's backend/frontend/SRS machinery.

Both consume the same `data/output/verified/<surah>_aligned.json` files;
neither depends on the other.

## General-purpose media flashcards

The repository also contains a stateless language-learning card bridge. Python
extracts an immutable timed transcript artifact from downloaded audio or video;
a Scala JVM/Scala.js reviewer consumes that artifact, translates exact text
selections and sentences with Google, prepares whole-sentence Google TTS, and
sends audio Cloze notes directly to AnkiConnect. Anki remains the only card
and review store.

Media can use external subtitles, an embedded source-language text track, or
local speech recognition when neither is available:

```bash
python -m pip install -e ".[asr]"

# Explicit one-time ASR model download. Imports never download model weights.
flashcards-prepare-asr --model medium

# External subtitles
flashcards-import-media movie.mp4 --subtitles movie.vi.srt \
  --language vi --out projects/movie.json

# Embedded Vietnamese subtitles when available, otherwise local ASR
flashcards-import-media movie.mkv \
  --language vi --asr-model medium --out projects/movie.json

# Audio-only input follows the same ASR fallback
flashcards-import-media podcast.mp3 \
  --language vi --asr-model medium --out projects/podcast.json

sbt "backend/run projects/movie.json"
```

Selecting text in a cue immediately requests a contextual target gloss and a
full-sentence English translation, then prepares whole-sentence source-language
TTS. Both translations are editable and the generated audio is playable before
**Create card** sends the exact previewed bytes to AnkiConnect. The reviewer does
not save cards, progress, transcript edits, or duplicate state; Anki rejects
duplicate notes naturally. Source text is sent to Google for translation and
speech generation. The destination deck is selected from Anki's live deck list,
and a persistent result reports the created note ID or the failure. See
`src/flashcards/README.md` for the artifact schema and complete workflow.

The reviewer intentionally mirrors Clausula's undocumented `MkEWBc` translation
RPC and `translate_tts` request rather than using a supported Google API.

## Project structure

```
src/flashcards/       - Python media/subtitle/ASR artifact generator
src/quran_alignment/ - Python alignment pipeline (normalize → align → validate)
scripts/             - pipeline runner + Anki deck generator
tests/               - Python artifact/alignment tests
data/
  raw_audio/         - source MP3s per Surah (001.mp3 ... 114.mp3)
  text/              - cached Uthmani text per Surah
  processed_audio/   - normalized 16kHz mono WAVs (pipeline output)
  output/
    aligned/         - raw WhisperX alignment output (pre-QA)
    verified/        - QA-passed alignment JSON (promoted from aligned/)
    anki/            - generated .apkg deck output

hifth/                    - legacy Quran memorization app with its own SBT build
  shared/                 - Quran domain and typed WebSocket messages
  backend/                - legacy SRS backend and static asset server
  frontend/               - legacy Laminar memorization player
  static/                 - generated legacy Scala.js output
media-reviewer/           - root SBT application's Archipelago-style modules
  shared/                 - card/artifact domain and typed WebSocket messages
  backend/                - stateless Google TTS/translation and AnkiConnect bridge
  frontend/               - standalone Laminar media reviewer and shared UI
  schema/                 - Python/Scala media artifact contract
```

## Generating alignment data for the entire Quran

The alignment pipeline (`src/quran_alignment/`) is what everything else is
built on: it turns raw recitation MP3s + the Uthmani text into per-word
millisecond timestamps, one JSON file per Surah.

### 1. Environment

```bash
conda env create -f environment.yml
conda activate quran
python -m pip install -e .
```

Requires `ffmpeg` on PATH (used both by the pipeline and the Anki audio
export) and a CUDA GPU for reasonable `align` speed (`--device cpu` works
but is much slower for `medium`-sized Whisper).

### 2. Inputs

Place the recitation audio before running the pipeline:

- `data/raw_audio/<NNN>.mp3` — one recitation file per Surah (`001.mp3` … `114.mp3`)

For example, download audio from YouTube with `yt-dlp` and name it for Surah 3:

```bash
yt-dlp \
  --remote-components ejs:github \
  --extractor-args "youtube:player_client=default,-android_vr,-android_sdkless" \
  -x --audio-format mp3 \
  -o "data/raw_audio/003.%(ext)s" \
  "https://www.youtube.com/watch?v=0UITOdeCk9w"
```

Change `003` and the URL for another Surah. The output basename must be the
zero-padded Surah number so the pipeline can locate it. Only download audio
that you are authorized to use.

The aligner downloads canonical Uthmani text from Quran.com when it is missing
and caches it at `data/text/<NNN>_uthmani.json`; it does not need to be
predownloaded. Existing cache files are reused.

### 3. Run the pipeline

Each Surah goes through three steps — `normalize` (→ 16kHz mono WAV) →
`align` (two-pass WhisperX transcription + forced alignment) → `validate`
(QA checks; promotes `data/output/aligned/` → `data/output/verified/` on
success, and copies the verified JSON into the backend's static
resources for the web app).

For the **entire Quran** (all 114 Surahs):

```bash
python scripts/run_pipeline.py --all --device cuda
```

For a subset (e.g. resuming after a failure, or just a few Surahs):

```bash
python scripts/run_pipeline.py --surahs 1 2 3 --device cpu
```

The runner prints a pass/fail summary at the end and exits non-zero if any
Surah failed. Negative or zero word durations and word-count mismatches are
hard validation failures and prevent promotion. Long silence gaps and low
alignment-confidence scores are warnings: they are logged for inspection but
do not prevent promotion or make validation exit unsuccessfully. Confidence
is checked only when WhisperX supplies a numeric score; unavailable confidence
is stored as `null` rather than treated as zero. Re-run the specific alignment
step after fixing a hard failure:

```bash
python -m src.quran_alignment.align --surah N --device cuda
python -m src.quran_alignment.validate --surah N
```

Only Surahs present in `data/output/verified/` are usable by either
downstream output (web app or Anki export) — partial Quran coverage is
fine, both consumers simply skip whatever isn't verified yet.

## Generating a full Anki deck

Once some (or all) Surahs are verified, generate the interactive
Quarter-Hizb flashcard deck:

```bash
python scripts/generate_anki_cards.py
```

This produces `data/output/anki/quran_quarter_hizb.apkg`, ready to import
into Anki. With no range flags, the generator attempts all QH IDs 1 through
240 and skips those whose required data is unavailable. Useful flags:

```bash
python scripts/generate_anki_cards.py --qh-start 1 --qh-end 20   # subset, e.g. while verifying incrementally
python scripts/generate_anki_cards.py --no-audio                  # skip ffmpeg trimming (faster, smaller deck)
python scripts/generate_anki_cards.py --out my_deck.apkg --deck-name "Quran::My Deck"
```

Each card embeds an adjustable cloze slider (0/10/25/50/75/90/95%, same
masking algorithm and seeding as the web app's player) over the full
Quarter-Hizb plus the ayah immediately before and after it. At the beginning
and end of the Quran, only the available adjacent ayah is included. All of
this text participates in masking and the corresponding audio is included.
A QH crossing a Surah boundary is supported: its per-Surah audio clips are
trimmed and concatenated. Every Surah used by either the QH or its adjacent
context must have `data/output/verified/<NNN>_aligned.json`; audio generation
also requires `data/processed_audio/<NNN>.wav`. Therefore context can require
an adjacent verified Surah even when the QH itself does not cross a boundary.
See `scripts/anki_export/README.md` for implementation details and caveats.

Re-running the generator after verifying more Surahs is safe and
idempotent — note GUIDs are stable per QH id, so re-importing the
updated deck into Anki updates existing cards rather than duplicating
them and preserves their scheduling. Enable updating existing notes in the
Anki import options. Changed audio may leave the previous clips unused;
Anki's **Tools > Check Media** can remove them after import.

## Legacy Hifth web app architecture

The original Quran memorization app is retained under `hifth/` with its own SBT
build. Its server serves plain HTML pages, while small Scala.js islands mount
into the page and communicate through one typed WebSocket.

No REST endpoints. No SPA framework. No shared mutable state between islands except through the server.

## Stack

- **Backend**: http4s + cats-effect + fs2
- **Frontend**: ScalaJS + Laminar
- **Shared**: Scala 3 cross-compiled domain models and WebSocket protocol
- **Serialization**: jsoniter-scala

## How it works

The shared module defines the entire client-server contract:

```scala
enum ClientMessage:
  case Increment
  case Decrement
  case Reset

enum ServerMessage:
  case CountUpdated(value: Int)
  case Error(msg: String)
```

Both sides are bound to this ADT. Adding a new message type is a compile error until both sides handle it.

On the frontend, islands communicate exclusively through `AppBus`:

```scala
// send
AppBus.outgoing.emit(ClientMessage.Increment)

// receive
AppBus.incoming.events.collect:
  case ServerMessage.CountUpdated(v) => v
```

Islands know nothing about WebSockets, serialization or connection state. `WsClient` owns the connection and handles reconnection with exponential backoff. Disconnection is surfaced via CSS on `#app-container` - no connection logic leaks into domain islands.

## Running in development

```bash
cd hifth

# terminal 1 - recompile frontend on change
sbt '~frontend/fastLinkJS'

# terminal 2 - run backend with filesystem asset serving
MODE=DEV sbt '~backend/reStart'
```

Open `http://localhost:8080`.

The browser auto-reloads when the backend restarts via a lightweight SSE endpoint that only exists in dev mode.

## Production build

```bash
cd hifth
sbt backend/package
```

This compiles the frontend with full optimisation, copies the JS output into the backend jar alongside the HTML files, and produces a self-contained artifact. Set `MODE=PROD` (or omit `MODE` entirely) at runtime.

## Adding an island

1. Create a new file in `hifth/frontend/src/main/scala/.../islands/`
2. Export a mount function:

```scala
@JSExportTopLevel("mountMyIsland", moduleID = "myisland")
def mount(el: dom.Element): Unit = ...
```

3. Add a mount point in your HTML:

```html
<div id="my-island"></div>
<script type="module">
  import { mountMyIsland } from "/js/myisland.js";
  mountMyIsland(document.getElementById("my-island"));
</script>
```

4. Add message types to the shared protocol if needed.

No routing configuration. No new endpoints. The WS handler picks up new message types automatically once they are added to the ADT.

## Adding a backend handler

Implement `MessageHandler[IO, ClientMessage]` and wire it into `WsHandler.make` in `Main.scala`. The handler receives a `publish` function for sending messages to all connected clients and is otherwise free to depend on whatever it needs (database connections, HTTP clients, etc.).
