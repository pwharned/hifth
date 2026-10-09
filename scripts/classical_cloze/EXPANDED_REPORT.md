# Expanded corpus coverage report

The report was generated from all 10,317 `Basic` notes in the `Greek and Latin`
deck. No Anki notes were created or modified while producing it. The complete
machine-readable artifact is `data/classical_cloze/expanded-full-report.json`.

## Corpus expansion

- GLAUx v2.1: Plato's Euthyphro, Apology, Crito, and Republic; Aristotle's
  Nicomachean Ethics.
- Canonical Perseus plus LatinCy 3.9.8: Livy books 1-2, Aeneid books 1-12, and
  Gallic War books 1-8.
- Existing eligible Greek and Latin UD treebanks.

The index contains 50,560 sentences. GLAUx annotations are corpus-supplied;
LatinCy annotations are automatic and retain that provenance.

## Results

| Result | Greek | Latin | Total |
|---|---:|---:|---:|
| Matched | 4,615 | 3,797 | 8,412 |
| Residual | 951 | 954 | 1,905 |

Overall coverage is 81.5%. Greek coverage is 82.9%; Latin coverage is 79.9%.
The expansion recovered 2,234 notes from the previous residual inventory:
1,201 through GLAUx, 1,015 through the expanded Latin corpus, and 18 through
parser/index changes.

Primary selected-author counts include 3,271 Virgil, 2,474 Homer, 1,816 Plato,
283 Caesar, 163 Aristotle, and 97 Livy cards. These counts reflect ranking, not
the number of corpus sentences.

## Residuals

| Status | Greek | Latin | Total |
|---|---:|---:|---:|
| Clean unmatched headword | 706 | 471 | 1,177 |
| Unmatched phrase | 193 | 381 | 574 |
| Unknown annotation | 28 | 60 | 88 |
| Unmatched long text | 16 | 39 | 55 |
| Explicit paradigm | 4 | 3 | 7 |
| Nonlexical/wrong language | 4 | 0 | 4 |

The remaining clean headwords include genuine rare vocabulary, proper names,
probable source misspellings, and words absent from the requested authors.
Probable spelling corrections are not applied automatically. Phrase residuals
include genuine unattested constructions as well as unrecognized dictionary
records. English instructions, long quotations, and malformed records should be
quarantined rather than converted into cards.
