# Homeric residual analysis

This analysis compares the 337 currently remaining Greek originals with the
local GLAUx editions of the *Iliad* and *Odyssey*. It normalizes accents and
elision marks but does not assume that every remaining card is Homeric.

| Classification | Cards | Interpretation |
|---|---:|---|
| Single unattested form or lemma variant | 135 | Usually a spelling, dialectal form, inflection, or nearby dictionary lemma |
| Partially attested multiword record | 149 | Some components occur in Homer; many are annotations or non-Homeric quotations |
| Independently attested components | 21 | All components occur in Homer, but not together in the same sentence |
| Exact contiguous surface phrase | 4 | The phrase occurs exactly after accent/elision normalization |
| Contiguous lemma-equivalent phrase | 2 | The phrase occurs contiguously after inflectional normalization |
| Ordered phrase with at most three intervening words | 5 | The reconstruction follows the text closely but omits words |
| Ordered components elsewhere in one sentence | 2 | Same sentence, but not a compact phrase |
| Unattested phrase or annotation | 15 | No complete Homeric interpretation from the supplied terms |
| No Greek target | 4 | English-only records |

## Exact phrases

Four records are recoverable with a punctuation-aware surface search. For
example, `μελαινέων ἕρμ ὀδυνάων` occurs in the *Iliad* as
`μελαινέων ἕρμ’ ὀδυνάων`. The apostrophe attached to `ἕρμ’` caused the previous
exact matcher to miss it.

Two further records, including `κατα οσσε`, become contiguous matches when each
term may match either the token's surface form or its lemma.

These six records are safe candidates for automatic phrase migration because
the complete intended sequence is attested.

## Reconstructed phrases

Twenty-one records contain components independently attested in Homer but not
in the supplied sequence. These resemble personal reading notes rather than
quotations. Automatically presenting them as exact Homeric phrases would be
incorrect.

A useful migration can still be made by identifying the least frequent lexical
component, finding a short sentence containing that component, and using the
original dictionary gloss. This requires either user confirmation or a strong
signal that the gloss describes that component rather than the reconstruction
as a whole.

Five additional records preserve the source order with no more than three
intervening words. They can be shown in the manual campaign as suggested source
passages, but should not be silently converted into contiguous phrase clozes.

## Dictionary mismatches

The 135 singleton records are the strongest evidence for nearby-but-different
dictionary headwords. Examples include unaccented forms, inflected forms,
Homeric variants, and transcription errors. The campaign's exact-form,
accent-normalized, safe fuzzy, and user-confirmed shared-stem searches are the
appropriate workflow for these records.

The 149 partially attested records are mixed. They include principal-part
lists, a lemma followed by an example, Plato passages, morphology notes, and
Homeric phrases with one malformed component. They cannot safely share one
automatic rule.

## Recommended automation

1. Strip elision apostrophes for lookup while preserving them for display.
2. Add contiguous phrase matching where every requested term may match either
   a surface form or its lemma.
3. Automatically migrate only complete contiguous matches.
4. Offer ordered-near matches and independently attested reconstructions as
   campaign suggestions, preferring the longest attested content word. Reject
   components shorter than five normalized Greek letters and common function
   words before using corpus rarity as a tie-breaker.
5. Continue shared-stem/manual selection for singleton dictionary mismatches.
6. Leave long quotations, mixed annotations, and English records for manual
   review.

This conservative first batch can automatically recover six cards. The next
campaign-assisted pool contains 28 reconstructed or ordered phrases. The
remaining records need target selection or dictionary-form correction rather
than broader corpus downloads alone.
