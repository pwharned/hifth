# Latin residual analysis

This report describes the live unmatched deck after the Tesserae and Homeric
salvage migrations. Counts are a snapshot and may decrease while the manual
campaign is in use.

## Inventory

The deck contained 819 originals:

| Group | Cards |
|---|---:|
| Recognizable Greek fronts | 180 |
| Tagged Latin records | 441 |
| Untagged Latin-like fronts | 33 |
| English fronts with a Latin back | 23 |
| English fronts with a Greek back | 142 |

The Latin migration population is therefore approximately 497 cards. The 142
reversed Greek imports must not be sent to a Latin corpus search.

## Latin structural patterns

| Pattern | Cards | Examples |
|---|---:|---|
| Principal parts | 154 | `tinguo tinguere tinxi tinctum`; `maneō manēre mānsī mānsum` |
| Annotated dictionary records | 75 | `ob (prep + acc)`; `comes comitis M/F` |
| Clean or nearly clean singletons | 104 | `vagio`; `tabularium`; `succipio` |
| Genuine phrases and constructions | 48 | `salutem dicere`; `fructui esse`; `ne ... quidem` |
| Conjugation or declension paradigms | 47 | `impleo imples implet...`; `acer acris acre` |
| Apparent spelling errors | 24 | `existmo`; `quattor`; `irsascor` |
| English/reversed Latin records | 42 | English definition on the front, Latin headword or quotation on the back |
| Irreducibly malformed | 3 | Empty gloss or no identifiable target |

## Principal parts

Most records currently tagged `unmatched_phrase` are not phrases. They are
dictionary principal parts such as:

```text
credo credere credidi creditus
obliviscor oblivisci oblitus sum
perfero perferre pertuli perlatus
accipio ere cepi ceptus
```

The first token is usually the lemma. The following infinitive, perfect, and
supine or participle should be retained as morphology, not searched as a
contiguous sentence. Approximately 145 of 154 records have a safe initial
headword.

## Paradigms

Forty-seven records contain paradigms rather than corpus phrases:

```text
incipio incipis incipit incipimus incipitis incipiunt
velim velis velit velimus velitis velint
hunc hanc hoc
acer acris acre
```

These can produce a useful card by selecting a representative lemma or form and
retaining the complete original sequence as morphology. They should never be
searched as contiguous prose.

## Dictionary annotations

Seventy-five records have a usable initial headword followed by gender, case
government, part of speech, or an alternate spelling:

```text
pro (prep + abl)
parum (adv. and indeclinable adj.)
vis (pl. vires, virium)
chlamys chlamydis f.
cottidie/cotidie
```

Recognized grammatical annotations can be removed for lookup and preserved in
the card's morphology or original-note field. About 70 are deterministic.

## Phrases

Forty-eight records are genuine constructions or examples. Exact contiguous
phrases can be searched directly. Discontinuous constructions such as
`non solum ... sed etiam` should be preserved as construction cards or reduced
to their longest useful component under the same information-loss policy used
for Greek.

## Spelling errors

Twenty-four records appear misspelled:

```text
existmo -> existimo
Quattor -> quattuor
acccedo -> accedo
irsascor -> irascor
nonullus -> nonnullus
```

These should use a curated alias or a unique Tesserae lemma/surface suggestion.
Unrestricted nearest-neighbor matching remains unsafe.

## Reversed imports

At least 165 records have English definitions on the front and a Greek or Latin
headword on the back. They should first be swapped or have a target extracted
from the back. Searching the first English word caused false Latin matches in an
earlier experiment.

## Recommended migration order

1. Extract and search initial lemmas from principal-parts records.
2. Strip known dictionary grammar annotations and search the headword.
3. Convert paradigms using a representative lemma while preserving all forms.
4. Swap clearly reversed English/Latin records.
5. Search clean singletons through Tesserae lemma then surface lookup.
6. Salvage the longest meaningful term from phrases only when exact phrase
   search fails.
7. Apply only unique, high-confidence spelling corrections.

Approximately 85% of the 441 tagged Latin records contain a deterministically
useful target. The dominant problem is record structure, not corpus coverage.
