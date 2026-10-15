# Classical corpus cloze

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

Anki Desktop and AnkiConnect must be running.

## Model

Anki is the only source of truth. There are two groups of cards:

- **Matched:** `Classical Corpus Cloze` notes in `Greek and Latin::Corpus Cloze`.
- **Unmatched:** the original `Basic` notes in `Greek and Latin::Unmatched Originals`,
  waiting for the manual reviewer (below).

No manifest or cache file exists. A card's audio is derived only from the text
displayed on the card.

## Migration

```bash
python -m scripts.classical_cloze.pipeline fetch-translations   # once
.venv/bin/python -m scripts.classical_cloze.pipeline migrate
```

`migrate` is safe to run at any time and any number of times; a second run
changes nothing. Each original `Basic` note under `Greek and Latin` moves as far
as it can:

1. No replacement yet: it is matched against the corpora. A match creates a
   suspended cloze note; no match moves the original to the unmatched deck.
2. Replacement without audio: audio is generated.
3. Replacement with audio: it is activated (if the original was active), the
   original is archived to `data/classical_cloze/archive/migrated-originals.json`
   and then deleted.

Existing replacements are never rewritten, so manual edits survive. Pass
`--corpora` or `--archive` to change the default locations.

## Audio

Audio is MMS Ancient Greek/Latin speech at 80% tempo. Each note's clip filename
embeds a hash of the model, tempo and the card text, so a clip is current exactly
when it matches the text on the card. Run it on its own with:

```bash
uv run --extra classical flashcards classical repair-audio       # 80% tempo
uv run --extra classical flashcards classical repair-audio --tempo 0.9
```

`uv` creates or repairs `.venv` and installs the declared dependencies. No
manual activation or pip bootstrap is required. Anki with AnkiConnect and
`ffmpeg` must be available outside the Python environment.

To retry unmatched single-word and recognized paradigm entries using the same
GLAUx/Tesserae search strategies as the manual campaign, run:

```bash
uv run --extra classical flashcards classical migrate --online-single-words
```

This takes the first form of recognized paradigms. Phrases, correlatives,
quotations, and grammar/reference notes remain unmatched for manual handling.

Only notes whose clip is missing or no longer matches their text are regenerated.
To fix a card by hand, edit its text in Anki and run the command again. To force
every clip to regenerate, change `AUDIO_VERSION` in `mms_audio.py`. Superseded
clips stay in Anki's media folder until you run **Tools > Check Media**.

The base adapter covers eligible sentences in the downloaded UD treebanks. `migrate` aligns translations by canonical section or source-anchored verse
range. UD Iliad sentence IDs are resolved through the original AGDT treebank's
canonical `subdoc` references. Cards without a defensible canonical match remain
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

## Manual migration campaign

Cards that remain unmatched can be reviewed one at a time in a local browser.
Anki Desktop and AnkiConnect must be running:

```bash
.venv/bin/python -m scripts.classical_cloze.manual_reviewer
```

Open <http://127.0.0.1:8767>. Select or enter the intended headword, search
GLAUx for Greek or Tesserae for Latin, and choose an attested example. Approval
creates the replacement suspended, generates MMS audio at 80% tempo, archives
the original, activates the replacement, and only then deletes the original.
Failures leave the original untouched and any incomplete replacement suspended.

Campaign state and archived originals are stored under
`data/classical_cloze/`. **Skip for now** hides a card from the campaign and
tags it `manual_migration::skipped`; **Mark unusable** records the same durable
skip with an `unusable` reason.

Greek search tries exact lemmas and forms first. If those fail, it accepts an
accent-normalized surface suggestion or up to five GLAUx lemmas differing only
near a shared stem ending. Inferred results are labeled in the candidate source
line so they can be rejected before creation.

If no safe automatic match exists, the reviewer also offers up to ten broader
shared-stem lemma suggestions. These are never selected automatically: choose a
related lemma only when its meaning agrees with the original dictionary gloss.
