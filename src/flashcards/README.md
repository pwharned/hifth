# Flashcard core

`flashcards` is the language-neutral part of the repository. It models timed
media transcripts, exact learning-unit spans, card drafts, and Anki output.
Quran-specific canonical-text mapping and progressive masking remain separate.

## Current workflow

Install the local package and Anki exporter:

```bash
python -m pip install -e ".[anki]"
```

Create a project from local media and source-language SRT or WebVTT subtitles:

```bash
flashcards-import-subtitles movie.mp4 movie.vi.srt \
  --language vi \
  --title "Movie title" \
  --out projects/movie.json
```

Open the local review application:

```bash
flashcards-review projects/movie.json
```

The reviewer plays each subtitle cue, lets you select an exact contiguous
token span, stores a contextual gloss and phrase classification, and writes
cards back to the project manifest. "Export Anki deck" clips the original
media for every card and writes `projects/movie.apkg`.

A manifest can also be exported without opening the reviewer:

```bash
flashcards-export projects/movie.json --out projects/movie.apkg
```

All processing is local. The subtitle importer does not call translation,
speech, or language-model APIs.

## Domain rules

- Character spans and token ranges are half-open: `[start, end)`.
- Clozes are built from offsets, never unrestricted string replacement.
- Orthographic tokens are base units. A learning unit may span several base
  tokens, such as Vietnamese `học sinh`.
- Card and media identities are stable across regeneration.
- `schema_version` is validated before a manifest is loaded.
- Audio references retain source-media times and are materialized only during
  export.

The first schema version intentionally leaves phrase discovery to language
adapters. Vietnamese segmentation, offline glosses, and local model-assisted
candidate ranking are the next layer; manually confirmed spans remain the
source of truth.
