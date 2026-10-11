# Documentation

Choose the workflow that matches the material you want to study.

| Goal | Start here |
|---|---|
| Make language-learning cards from audio or video | [Media flashcards](MEDIA_FLASHCARDS.md) |
| Align Quran recitation and generate Anki cards | [Root Quran guide](../README.md#quran-memorization-with-your-preferred-reciter) |
| Migrate Greek and Latin vocabulary cards | [Classical corpus cloze](../scripts/classical_cloze/README.md) |
| Develop or run the legacy Hifth web app | [Legacy Hifth](../hifth/README.md) |
| Restore the Greek/Latin migration after remote sync | [Anki sync recovery](ANKI_SYNC_RECOVERY.md) |

## Technical References

- [Media artifact contract](../src/flashcards/README.md)
- [Media reviewer architecture](../media-reviewer/README.md)
- [Quran Anki export details](../scripts/anki_export/README.md)
- [Classical corpus coverage](../scripts/classical_cloze/EXPANDED_REPORT.md)
- [Classical unmatched-card analysis](../scripts/classical_cloze/UNMATCHED_ANALYSIS.md)

The repository root is the SBT build for the media reviewer. The legacy Hifth
application under `hifth/` has a separate SBT build.
