"""
mask_engine.py
--------------
Python port of PlayerEngine.scala's MaskEngine. Produces a seeded shuffle
rank for each word in a segment. A word is "masked" at cloze level L when
rank < round(total * L / 100) -- this makes mask sets additive: everything
masked at 25% is still masked at 50%, etc. Same property as the app.

We don't need bit-for-bit parity with Scala's util.Random algorithm - this
is a standalone export pipeline, not shared runtime state - just the same
determinism (seed = quarterHizbId) and additive-threshold behavior.
"""
import random


def ranked_indices(total_words: int, seed: int) -> list[int]:
    """Returns a list of length total_words where result[i] = shuffle rank
    of word i (0 = masked first / at lowest cloze levels)."""
    order = list(range(total_words))
    random.Random(seed).shuffle(order)
    ranks = [0] * total_words
    for rank, word_idx in enumerate(order):
        ranks[word_idx] = rank
    return ranks


def masked_count(total_words: int, percent: int) -> int:
    return round(total_words * percent / 100.0)
