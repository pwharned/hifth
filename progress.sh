uv run --extra classical python - <<'PY'
from scripts.classical_cloze import mms_audio as m

ids = m.anki("findNotes", query=f'deck:"{m.DESTINATION_DECK}" note:"{m.MODEL_NAME}"')
notes = m.batched_note_info(ids)

done = sum(
    m.SOUND_RE.findall(n["fields"]["Text"]["value"])
    == [m.audio_filename(m.language_of(n), 0.8, m.spoken_text(n["fields"]["Text"]["value"]))]
    for n in notes
)

print(f"{done}/{len(notes)} complete ({done / len(notes):.1%})")
PY
