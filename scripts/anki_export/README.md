# Anki export: adjustable-cloze Quarter-Hizb cards

Generates one Anki flashcard per Quarter-Hizb (QH), each embedding a live
JS cloze-slider over the app's masking algorithm (`MaskEngine`, seeded by
QH id, additive across levels 0/10/25/50/75/90/95). Unlike native Anki
cloze, the *whole card* is the memorization target: the full QH plus one ayah
immediately before and after it when available. The slider controls how much
of that text is blanked while reading, and the card includes audio for the
same expanded range.

Only Surahs with a verified aligned JSON (`data/output/verified/*.json`)
are included; QH spans or their adjacent context needing un-verified Surahs
are skipped and reported in the summary.

## Run

```bash
python scripts/generate_anki_cards.py                       # attempt all QH IDs 1-240
python scripts/generate_anki_cards.py --qh-start 1 --qh-end 20
python scripts/generate_anki_cards.py --no-audio   # skip ffmpeg trimming, smaller/faster
```

Output: `data/output/anki/quran_quarter_hizb.apkg` (import directly into Anki).
Re-importing a regenerated package with updates enabled replaces existing QH
notes by stable GUID while preserving their review scheduling. After updating
audio, **Tools > Check Media** can remove old clips that Anki no longer uses.

## Notes / caveats

- The slider's JS runs inline in the Anki card template (works in Anki
  Desktop, AnkiDroid, AnkiMobile — all support inline `<script>` in
  templates). It is *not* native cloze deletion.
- Masking is computed independently per Surah-segment within a QH (same
  as the app), using a single seed = QH id. Adjacent context participates in
  masking.
- Audio is trimmed per QH from `data/processed_audio/<surah>.wav` using
  first/last word timestamps (+250ms pad) and concatenated across Surah
  boundaries. Missing processed audio or an ffmpeg failure skips the card
  unless `--no-audio` is used.
- Every Surah used by the QH or its adjacent context must be verified. This
  can require the previous or next Surah even when the QH itself is contained
  within one Surah. Cross-Surah QHs are supported when all required verified
  JSON and processed WAV files exist.
- `quran_data.py` / `mask_engine.py` are maintained independently from the
  Scala `QuranData`/`MaskEngine`. The adjacent-ayah expansion is specific to
  Anki generation and does not change the web player.
