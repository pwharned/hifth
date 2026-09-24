# Flashcard core

`flashcards` is the language-neutral part of the repository. It models timed
media transcripts, exact learning-unit spans, card drafts, and Anki output.
Quran-specific canonical-text mapping and progressive masking remain separate.

## Current workflow

Install the local package and Anki exporter:

```bash
python -m pip install -e ".[anki]"
```

Choose an installed Ollama model when starting the reviewer. The default is
`qwen3.5:9b`; installing it is an explicit user action, never something the
application does automatically. Structured Qwen output requires Ollama 0.32.0
or newer:

```bash
ollama pull qwen3.5:9b
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
token span, and automatically asks `qwen3.5:9b` for contextual translations
and possible containing learning units. Clicking a suggestion expands the
selection to its exact validated token range and fills its gloss, phrase type,
component explanation, and sentence translation. Suggestions remain editable;
the confirmed manual span is what is written to the project manifest. "Export
Anki deck" clips the original media for every card and writes
`projects/movie.apkg`.

Analysis can be disabled or configured without changing the project format:

```bash
flashcards-review projects/movie.json --no-analysis
flashcards-review projects/movie.json --model qwen3.5:4b
flashcards-review projects/movie.json --model qwen3.6:35b
```

Use `ollama list` to see models already present on the machine. If the selected
model is missing, the reviewer reports the exact pull command but remains fully
usable for manual span selection.

The reviewer talks only to loopback Ollama URLs and explicitly bypasses system
HTTP proxies. Valid analyses are cached in
`projects/movie.analysis-cache.json` using the model digest, prompt version,
language, sentence, and exact token spans. Invalid model ranges are rejected
and retried once before the reviewer falls back to manual selection.

A manifest can also be exported without opening the reviewer:

```bash
flashcards-export projects/movie.json --out projects/movie.apkg
```

All processing is local. The subtitle importer itself does not call a model;
the reviewer calls the Ollama API at `http://127.0.0.1:11434` and never sends
text or media to an external service.

## Domain rules

- Character spans and token ranges are half-open: `[start, end)`.
- Clozes are built from offsets, never unrestricted string replacement.
- Orthographic tokens are base units. A learning unit may span several base
  tokens, such as Vietnamese `học sinh`.
- Scripts that normally omit spaces, including Chinese, Japanese, Thai, Lao,
  Khmer, and Myanmar, use character-sized base units so the model can propose
  larger word and phrase ranges.
- Card and media identities are stable across regeneration.
- `schema_version` is validated before a manifest is loaded.
- Audio references retain source-media times and are materialized only during
  export.

Phrase discovery is language-neutral: the model receives the source language,
exact sentence, and immutable numbered token list. Language-specific tools can
later provide additional evidence, but manually confirmed spans remain the
source of truth.
