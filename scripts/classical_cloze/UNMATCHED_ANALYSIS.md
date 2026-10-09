# Unmatched card analysis

The full migration report contains 10,317 source notes. Of these, 8,412 have
an attested corpus example and 1,905 remain unmatched, for 81.5% coverage.

## Existing statuses

| Status | Greek | Latin | Total |
|---|---:|---:|---:|
| Clean unmatched headword | 706 | 471 | 1,177 |
| Unmatched phrase | 193 | 381 | 574 |
| Unknown annotation | 28 | 60 | 88 |
| Unmatched long text | 16 | 39 | 55 |
| Explicit paradigm | 4 | 3 | 7 |
| Nonlexical or wrong language | 4 | 0 | 4 |

## Root-cause analysis

| Root cause | Greek | Latin | Total |
|---|---:|---:|---:|
| Rare or absent lexical target | 409 | 182 | 591 |
| Edit-distance-one corpus neighbor | 296 | 208 | 504 |
| Phrase or construction | 175 | 134 | 309 |
| Principal parts or paradigm | 12 | 178 | 190 |
| English or non-target text | 4 | 147 | 151 |
| Dictionary annotation | 25 | 40 | 65 |
| Capitalized or proper-name candidate | 0 | 39 | 39 |
| Malformed dictionary dump or gloss | 10 | 26 | 36 |
| Quotation without an explicit target | 20 | 0 | 20 |

Near-spelling and proper-name categories are review queues, not safe automatic
corrections. The largest safe recovery opportunity is structured headword
extraction. Up to 242 residual records contain an initial headword that occurs
exactly in the corpus: 175 paradigms, 56 annotations, and 11 dictionary dumps.

Latin principal parts are commonly misclassified as phrases, for example
`tinguo tinguere tinxi tinctum`. Parenthetical grammar such as `ob (prep + acc)`
and `parum (adv. and indeclinable adj.)` is not covered by the current narrow
annotation stripper. Approximately 151 records are English explanations or
translations and should be quarantined rather than matched.

The 504 near-spelling records include credible corrections such as `existmo`
to `existimo` and `κατακλίνωω` to `κατακλίνω`, but unrestricted edit distance
also proposes false pairs such as `rapax` to `rapa`. Generic fuzzy matching is
therefore unsafe. The remaining 591 lexical targets are predominantly rare,
technical, late, or absent from the selected authors and require corpus
expansion.

## Recommended order

1. Parse clearly delimited headwords from principal parts and known dictionary
   annotations, requiring an exact lemma hit.
2. Quarantine English explanations, unsafe dictionary dumps, and quotations
   without a target.
3. Add lemma-aware contiguous phrase matching.
4. Review curated spelling aliases; never apply generic nearest-neighbor
   matching automatically.
5. Expand corpora for genuine rare and technical vocabulary.
