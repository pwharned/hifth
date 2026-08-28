# Anki export: adjustable-cloze Quarter-Hizb cards

Generates one Anki flashcard per Quarter-Hizb (QH), each embedding a live
JS cloze-slider over the app's masking algorithm (`MaskEngine`, seeded by
QH id, additive across levels 0/10/25/50/75/90/95). Unlike native Anki
cloze, the *whole card* is the memorization target — the slider only
controls how much of the QH's text is blanked while reading; the Back
reveals the full unmasked text plus the trimmed audio for that QH.

Only Surahs with a verified aligned JSON (`data/output/verified/*.json`)
are included; QH spans needing un-verified Surahs are skipped and
reported in the summary.

## Run

```bash
python scripts/generate_anki_cards.py
python scripts/generate_anki_cards.py --qh-start 1 --qh-end 20
python scripts/generate_anki_cards.py --no-audio   # skip ffmpeg trimming, smaller/faster
```

Output: `data/output/anki/quran_quarter_hizb.apkg` (import directly into Anki).

## Notes / caveats

- The slider's JS runs inline in the Anki card template (works in Anki
  Desktop, AnkiDroid, AnkiMobile — all support inline `<script>` in
  templates). It is *not* native cloze deletion.
- Masking is computed independently per Surah-segment within a QH (same
  as the app), using a single seed = QH id.
- Audio is trimmed per QH from `data/processed_audio/<surah>.wav` using
  first/last word timestamps (+250ms pad) and concatenated across Surah
  boundaries when a QH spans two Surahs.
- `quran_data.py` / `mask_engine.py` are intentionally standalone ports
  of the Scala `QuranData`/`MaskEngine` (not a shared module) — update
  both sides if the underlying tables/algorithm change.
