# Classical corpus cloze pilot

This standalone tool converts `Basic` notes in the `Greek and Latin` Anki deck
into suspended, sentence-level cloze notes. It uses only attested, annotated
sentences from eligible Universal Dependencies corpora. It never generates or
rewrites Greek or Latin text.

## Data

Place these repositories under `data/classical_cloze/corpora/`:

```bash
git clone --depth 1 https://github.com/UniversalDependencies/UD_Ancient_Greek-Perseus.git data/classical_cloze/corpora/UD_Ancient_Greek-Perseus
git clone --depth 1 https://github.com/UniversalDependencies/UD_Ancient_Greek-PROIEL.git data/classical_cloze/corpora/UD_Ancient_Greek-PROIEL
git clone --depth 1 https://github.com/UniversalDependencies/UD_Latin-Perseus.git data/classical_cloze/corpora/UD_Latin-Perseus
git clone --depth 1 https://github.com/UniversalDependencies/UD_Latin-PROIEL.git data/classical_cloze/corpora/UD_Latin-PROIEL
```

Anki Desktop and AnkiConnect must be running. Building a manifest is read-only.

## Pilot

```bash
python -m scripts.classical_cloze.pipeline build-manifest
python -m scripts.classical_cloze.pipeline write-pilot data/classical_cloze/pilot.json
.venv/bin/python -m scripts.classical_cloze.mms_audio data/classical_cloze/pilot.json
```

`build-manifest` reads all source notes but retains only the first 50 ranked
matches per language plus the complete unmatched/ambiguous inventory.
Inspect `pilot.json` before running `write-pilot`. The writer is resumable and
records each generated note ID immediately. Every generated card is suspended.
The second command replaces temporary pilot audio with deterministic MMS
Ancient Greek/Latin audio slowed to 90% tempo without changing pitch.

Source-note tagging is a separate, explicit mutation:

```bash
python -m scripts.classical_cloze.pipeline tag-unmatched data/classical_cloze/pilot.json
```

The base adapter covers eligible sentences in the downloaded UD treebanks.
Download the configured public-domain English Perseus editions before building
the manifest:

```bash
python -m scripts.classical_cloze.pipeline fetch-translations
```

The pipeline aligns translations by canonical section or line range. When a
source citation also identifies a sentence within that passage, it selects the
same numbered English sentence; otherwise the card is explicitly tagged as a
passage-level alignment. Cards without a defensible canonical match remain
tagged `corpus_cloze::translation_missing`. Every supported citation includes
a link to its CTS passage in the Perseus/Scaife reader, plus translator and
alignment provenance.

## Expanded corpora

The pipeline also reads selected GLAUx v2.1 XML files from `GLAUx/xml/` and a
LatinCy annotation artifact at `canonical-latinLit/annotated.json`. Generate the
Latin artifact from downloaded canonical Perseus editions with:

```bash
uv venv --python 3.12
uv pip install --python .venv/bin/python numpy==1.26.4 spacy \
  https://huggingface.co/latincy/la_core_web_lg/resolve/main/la_core_web_lg-3.9.8-py3-none-any.whl
.venv/bin/python -m scripts.classical_cloze.annotate_latin
```

The configured material is Plato's Euthyphro, Apology, Crito, and Republic;
Aristotle's Nicomachean Ethics; Livy books 1-2; the complete Aeneid; and the
complete Gallic War. GLAUx supplies Greek annotations. LatinCy annotations are
automatic and are labeled as such in corpus provenance.
