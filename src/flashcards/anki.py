from __future__ import annotations

import hashlib
import html
import re
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from .media import sha256_file
from .models import CardDraft, ClozePolicy


MODEL_NAME = "Media Span Cloze"
MODEL_NAMESPACE = "hifth-flashcards/media-span-cloze/v1"

FIELDS = (
    "CardId",
    "Text",
    "Target",
    "TargetGloss",
    "SentenceTranslation",
    "Analysis",
    "Audio",
    "Source",
)

FRONT_TEMPLATE = """
<div class="media-card">
  {{#Audio}}<div class="audio">{{Audio}}</div>{{/Audio}}
  <div class="sentence">{{cloze:Text}}</div>
</div>
"""

BACK_TEMPLATE = """
{{FrontSide}}
<hr class="answer-separator">
<div class="answer">
  {{#TargetGloss}}<div class="target"><strong>{{Target}}</strong>: {{TargetGloss}}</div>{{/TargetGloss}}
  {{#SentenceTranslation}}<div class="translation">{{SentenceTranslation}}</div>{{/SentenceTranslation}}
  {{#Analysis}}<div class="analysis">{{Analysis}}</div>{{/Analysis}}
  {{#Source}}<div class="source">{{Source}}</div>{{/Source}}
</div>
"""

CSS = """
.card {
  margin: 0;
  padding: 24px;
  background: #15171d;
  color: #f2f0e9;
  font-family: system-ui, sans-serif;
  font-size: 22px;
  text-align: left;
}
.media-card, .answer { max-width: 760px; margin: 0 auto; }
.audio { margin-bottom: 20px; text-align: center; }
.sentence { line-height: 1.7; white-space: pre-wrap; }
.cloze { color: #e6a84b; font-weight: 650; }
.answer-separator { border: 0; border-top: 1px solid #3b3e48; margin: 24px auto; max-width: 760px; }
.target { font-size: 21px; margin-bottom: 14px; }
.translation { color: #d7d4cc; line-height: 1.55; margin-bottom: 14px; white-space: pre-wrap; }
.analysis { color: #aaa69d; font-size: 16px; line-height: 1.5; margin-bottom: 14px; white-space: pre-wrap; }
.source { color: #777b86; font-size: 13px; margin-top: 20px; }
.source a { color: #8ba8d9; }
"""


@dataclass(frozen=True, slots=True)
class PreparedCard:
    draft: CardDraft
    audio_path: Path | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.draft, CardDraft):
            raise ValueError("draft must be a CardDraft")
        if self.draft.policy is not ClozePolicy.EXPLICIT_SPAN:
            raise ValueError("Media Span Cloze only supports explicit_span cards")
        if self.audio_path is not None:
            path = Path(self.audio_path)
            if not path.is_file():
                raise FileNotFoundError(f"Card audio file not found: {path}")
            object.__setattr__(self, "audio_path", path)


def stable_anki_id(namespace: str) -> int:
    if not isinstance(namespace, str) or not namespace.strip():
        raise ValueError("namespace must be a nonempty string")
    value = int.from_bytes(hashlib.sha256(namespace.encode("utf-8")).digest()[:4], "big")
    return 1_000_000_000 + value % 1_000_000_000


def _plain_text_html(value: str | None) -> str:
    if value is None:
        return ""
    return html.escape(value).replace("\n", "<br>")


def _source_html(card: CardDraft) -> str:
    label = card.source_title or card.source_url
    if not label:
        return ""
    escaped_label = html.escape(label)
    if not card.source_url:
        return escaped_label
    parsed = urlsplit(card.source_url)
    if parsed.scheme not in {"http", "https"}:
        return escaped_label
    escaped_url = html.escape(card.source_url, quote=True)
    return f'<a href="{escaped_url}">{escaped_label}</a>'


def render_fields(card: CardDraft, *, audio_filename: str | None = None) -> dict[str, str]:
    if not isinstance(card, CardDraft):
        raise ValueError("card must be a CardDraft")
    if card.policy is not ClozePolicy.EXPLICIT_SPAN:
        raise ValueError("Media Span Cloze only supports explicit_span cards")
    if audio_filename is not None:
        if Path(audio_filename).name != audio_filename or any(c in audio_filename for c in "[]\r\n"):
            raise ValueError("audio_filename must be a safe filename without a path")
    return {
        "CardId": card.id,
        "Text": card.cloze_text(escape_html=True),
        "Target": _plain_text_html(card.target_text()),
        "TargetGloss": _plain_text_html(card.target_gloss),
        "SentenceTranslation": _plain_text_html(card.sentence_translation),
        "Analysis": _plain_text_html(card.analysis),
        "Audio": "" if audio_filename is None else f"[sound:{audio_filename}]",
        "Source": _source_html(card),
    }


def _tag(value: str) -> str:
    normalized = re.sub(r"\s+", "_", value.strip())
    return re.sub(r"[^\w:.-]", "_", normalized, flags=re.UNICODE)


def export_apkg(
    cards: list[PreparedCard] | tuple[PreparedCard, ...],
    output_path: str | Path,
    *,
    deck_name: str = "Language::Media Cloze",
    deck_id: int | None = None,
    model_id: int | None = None,
) -> Path:
    if not cards:
        raise ValueError("at least one prepared card is required")
    if not isinstance(deck_name, str) or not deck_name.strip():
        raise ValueError("deck_name must be a nonempty string")
    prepared_cards = tuple(cards)
    for index, card in enumerate(prepared_cards):
        if not isinstance(card, PreparedCard):
            raise ValueError(f"cards[{index}] must be a PreparedCard")
    card_ids = [card.draft.id for card in prepared_cards]
    if len(card_ids) != len(set(card_ids)):
        raise ValueError("prepared cards must have unique card ids")

    try:
        import genanki
    except ImportError as exc:
        raise RuntimeError("genanki is required for .apkg export") from exc

    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    model = genanki.Model(
        model_id or stable_anki_id(MODEL_NAMESPACE),
        MODEL_NAME,
        fields=[{"name": field} for field in FIELDS],
        templates=[{"name": "Media Cloze", "qfmt": FRONT_TEMPLATE, "afmt": BACK_TEMPLATE}],
        css=CSS,
        model_type=genanki.Model.CLOZE,
    )
    deck = genanki.Deck(deck_id or stable_anki_id(f"{MODEL_NAMESPACE}/deck/{deck_name}"), deck_name)

    with tempfile.TemporaryDirectory(prefix="flashcards-anki-") as temp_dir_name:
        temp_dir = Path(temp_dir_name)
        media_files: list[str] = []
        packaged_media: dict[tuple[str, str], str] = {}
        for prepared in prepared_cards:
            audio_filename = None
            if prepared.audio_path is not None:
                suffix = prepared.audio_path.suffix.lower() or ".mp3"
                digest = sha256_file(prepared.audio_path)
                media_key = (digest, suffix)
                audio_filename = packaged_media.get(media_key)
                if audio_filename is None:
                    audio_filename = f"flashcard_audio_{digest[:24]}{suffix}"
                    packaged_audio = temp_dir / audio_filename
                    shutil.copy2(prepared.audio_path, packaged_audio)
                    media_files.append(str(packaged_audio))
                    packaged_media[media_key] = audio_filename
            fields = render_fields(prepared.draft, audio_filename=audio_filename)
            tags = tuple(dict.fromkeys(("flashcards", prepared.draft.language, *prepared.draft.tags)))
            note = genanki.Note(
                model=model,
                fields=[fields[field] for field in FIELDS],
                tags=[_tag(tag) for tag in tags],
                guid=genanki.guid_for(MODEL_NAMESPACE, prepared.draft.id),
            )
            deck.add_note(note)
        package = genanki.Package(deck)
        package.media_files = media_files
        package.write_to_file(str(output))
    return output
