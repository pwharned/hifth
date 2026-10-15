# Restoring the Greek and Latin migration after a remote sync

The local Anki collection was exported before accepting remote changes:

```text
data/classical_cloze/archive/greek-and-latin-before-remote-sync-2026-10-11.apkg
```

The package includes the complete `Greek and Latin` deck hierarchy, scheduling,
and media. It was validated after export. Keep this local file until the remote
sync and restoration have both been verified.

```text
Size:   2,005,556,268 bytes
SHA256: 2fd84f93af82ae8dbc7f97fccf9fa74be7e05c94b13d2db9639fa3944b01f8cb
```

## Recovery sequence

1. Close the manual migration reviewer so it cannot modify Anki during recovery.
2. Sync Anki and choose **Download from AnkiWeb** when prompted for a one-way
   decision. This intentionally replaces the local collection with the remote
   state.
3. Import `greek-and-latin-before-remote-sync-2026-10-11.apkg` through Anki's
   **File > Import** command.
4. Confirm that `Greek and Latin::Corpus Cloze` and
   `Greek and Latin::Unmatched Originals` are present.
5. Run the migration with AnkiConnect active:

```bash
.venv/bin/python -m scripts.classical_cloze.pipeline migrate
```

6. Verify the deck in Anki, then sync normally to upload the merged and
   reconciled collection.

## Why re-running the migration repairs this

An APKG contains notes, cards, scheduling, and media, but absence from a package
does not delete a note already present in the collection. If AnkiWeb still has
the pre-migration originals, those notes survive package import. `migrate` is
idempotent and reads only Anki: it uses each replacement's `OriginalNoteID` to
find originals that already have corpus-cloze replacements, archives them to
`data/classical_cloze/archive/migrated-originals.json`, and deletes only those.
Originals without replacements are matched again or moved to the unmatched deck.
