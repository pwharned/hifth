# Media flashcards

This workflow turns local audio or video into reviewed Anki cloze cards. It is
appropriate for Japanese and other languages supported by faster-whisper and
Google Translate/TTS.

## Prerequisites

- Python 3.11 or newer
- `ffmpeg` and `ffprobe` on `PATH`
- Java and SBT
- Anki Desktop with AnkiConnect running
- Internet access while reviewing for translation and TTS

Install the Python importer from the repository root:

```bash
python -m pip install -e ".[asr]"
```

Download an ASR model once. Imports never download model weights implicitly:

```bash
flashcards-prepare-asr --model medium
```

## Choose an input

External SRT or WebVTT subtitles give the most reliable transcript and timing:

```bash
flashcards-import-media episode.mp4 \
  --subtitles episode.ja.srt \
  --language ja \
  --out projects/episode.json
```

For media with embedded Japanese subtitles, the importer uses a matching track
and falls back to the already-downloaded ASR model:

```bash
flashcards-import-media episode.mkv \
  --language ja \
  --asr-model medium \
  --out projects/episode.json
```

Audio without timed subtitles uses ASR:

```bash
flashcards-import-media lesson.mp3 \
  --language ja \
  --asr-model medium \
  --out projects/lesson.json
```

Use `--subtitle-stream N` if multiple embedded Japanese tracks match. Use
`--force-transcribe` to ignore subtitles. Each input must contain exactly one
audio stream so the importer cannot silently choose the wrong language track.

## Import a Japanese lesson ZIP

For an archive such as `ZZ_audio_L01.zip`:

```bash
unzip "ZZ_audio_L01.zip"
mkdir -p japanese-projects

for audio in ZZ_audio_L01/*.mp3; do
  name=$(basename "$audio" .mp3)
  flashcards-import-media "$audio" \
    --language ja \
    --asr-model medium \
    --out "japanese-projects/$name.json"
done
```

This creates one immutable transcript artifact per recording. If an official
Japanese script is available only as a PDF, ASR still supplies timing; inspect
its transcript carefully because a PDF is not a timed subtitle file.

## Create cards

Start the reviewer with one artifact from the repository root:

```bash
sbt 'backend/run japanese-projects/ZZ_[01-01]_kiku1.json'
```

Open <http://127.0.0.1:8766>, then:

1. Select the exact Japanese word or phrase to hide.
2. Review or edit the selection gloss and sentence translation.
3. Listen to the source cue and generated sentence audio.
4. Choose an Anki deck.
5. Select **Create card**.

The resulting note contains one exact cloze, whole-sentence audio, and both
translations. Anki is the only card and review store; the reviewer does not
save drafts or progress.

Stop the backend and run it again with the next JSON artifact when moving to a
different recording.

## Privacy and limitations

Selected source text is sent to Google for translation and TTS. Media files do
not leave the machine. The integration uses undocumented consumer endpoints,
so Google may change them without notice.

Use only media you are authorized to download and keep proprietary course
material and generated cards private unless its license permits redistribution.

For artifact schema, cache, and implementation details, see the
[media artifact reference](../src/flashcards/README.md). For the Scala reviewer
layout, see the [media reviewer reference](../media-reviewer/README.md).
