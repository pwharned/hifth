# Flashcards Python API

This is a batch-first flashcard creation library. `FlashcardApp` is the public
facade, recipes describe source-specific workflows, and provider ports isolate
Anki, translation, speech, media, and transcription integrations.

```python
from flashcards.recipes import ClassicalMigration

recipe = ClassicalMigration()
recipe.apply()
recipe.repair_audio()
```

Provider-independent cards are immutable `CardDraft` values. Stable draft IDs
make publication idempotent.

## Timed media artifacts

`MediaArtifact` represents media metadata, timed utterances, tokens, and
provenance. Its JSON contract is
[`schema/media-artifact-v1.schema.json`](schema/media-artifact-v1.schema.json).
It deliberately excludes cards and review state.

## Boundaries

- `domain.py`: provider-independent values
- `ports.py`: interfaces for side effects
- `adapters/`: external service implementations
- `recipes/`: user-facing workflow composition
- `app.py`: stable generation/publication facade
- `cli.py`: the unified `flashcards` command

Domain and recipe code should not implement HTTP protocols directly. New
sources should produce card drafts and reuse the ports.
