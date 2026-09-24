from __future__ import annotations

import hashlib
import html
import json
import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from typing import Any, Self, TypeVar


CURRENT_SCHEMA_VERSION = 1


def _require_string(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a nonempty string")
    return value


def _optional_string(value: object, name: str) -> str | None:
    if value is None:
        return None
    return _require_string(value, name)


def _require_int(value: object, name: str, *, minimum: int = 0) -> int:
    if type(value) is not int or value < minimum:
        raise ValueError(f"{name} must be an integer greater than or equal to {minimum}")
    return value


def _require_number(value: object, name: str, *, minimum: float = 0) -> int | float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a number greater than or equal to {minimum}")
    if not math.isfinite(value) or value < minimum:
        raise ValueError(f"{name} must be a number greater than or equal to {minimum}")
    return value


def _validate_interval(start: object, end: object, prefix: str) -> None:
    start_value = _require_number(start, f"{prefix}.start_ms")
    end_value = _require_number(end, f"{prefix}.end_ms")
    if end_value <= start_value:
        raise ValueError(f"{prefix}.end_ms must be greater than {prefix}.start_ms")


def _validate_confidence(value: object, name: str = "confidence") -> None:
    if value is None:
        return
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a number between 0 and 1")
    if not math.isfinite(value) or not 0 <= value <= 1:
        raise ValueError(f"{name} must be between 0 and 1")


T = TypeVar("T")


def _as_tuple(value: object, name: str, *, none_as_empty: bool = False) -> tuple[Any, ...]:
    if value is None and none_as_empty:
        return ()
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise ValueError(f"{name} must be a sequence")
    return tuple(value)


def _string_tuple(value: object, name: str) -> tuple[str, ...]:
    values = _as_tuple(value, name, none_as_empty=True)
    for index, item in enumerate(values):
        _require_string(item, f"{name}[{index}]")
    return values


@dataclass(frozen=True, slots=True)
class TextSpan:
    start_char: int
    end_char: int

    def __post_init__(self) -> None:
        _require_int(self.start_char, "start_char")
        _require_int(self.end_char, "end_char")
        if self.end_char <= self.start_char:
            raise ValueError("end_char must be greater than start_char")

    def extract(self, text: str) -> str:
        if not isinstance(text, str):
            raise ValueError("text must be a string")
        if self.end_char > len(text):
            raise ValueError(
                f"span [{self.start_char}, {self.end_char}) exceeds text length {len(text)}"
            )
        return text[self.start_char : self.end_char]


@dataclass(frozen=True, slots=True)
class MediaSource:
    id: str
    path: str
    language: str
    title: str | None = None
    source_url: str | None = None
    checksum_sha256: str | None = None
    duration_ms: float | None = None

    def __post_init__(self) -> None:
        _require_string(self.id, "MediaSource.id")
        _require_string(self.path, "MediaSource.path")
        _require_string(self.language, "MediaSource.language")
        _optional_string(self.title, "MediaSource.title")
        _optional_string(self.source_url, "MediaSource.source_url")
        if self.checksum_sha256 is not None:
            if not isinstance(self.checksum_sha256, str) or not re.fullmatch(
                r"[0-9a-fA-F]{64}", self.checksum_sha256
            ):
                raise ValueError("MediaSource.checksum_sha256 must contain 64 hexadecimal characters")
        if self.duration_ms is not None:
            _require_number(self.duration_ms, "MediaSource.duration_ms")
            if self.duration_ms == 0:
                raise ValueError("MediaSource.duration_ms must be greater than 0")


@dataclass(frozen=True, slots=True)
class BaseToken:
    text: str
    span: TextSpan
    start_ms: float | None = None
    end_ms: float | None = None
    confidence: float | None = None

    def __post_init__(self) -> None:
        _require_string(self.text, "BaseToken.text")
        if not isinstance(self.span, TextSpan):
            raise ValueError("BaseToken.span must be a TextSpan")
        if (self.start_ms is None) != (self.end_ms is None):
            raise ValueError("BaseToken.start_ms and BaseToken.end_ms must be provided together")
        if self.start_ms is not None:
            _validate_interval(self.start_ms, self.end_ms, "BaseToken")
        _validate_confidence(self.confidence, "BaseToken.confidence")


@dataclass(frozen=True, slots=True)
class Utterance:
    id: str
    media_id: str
    text: str
    start_ms: float
    end_ms: float
    tokens: tuple[BaseToken, ...]
    translation: str | None = None
    confidence: float | None = None
    provenance: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _require_string(self.id, "Utterance.id")
        _require_string(self.media_id, "Utterance.media_id")
        _require_string(self.text, "Utterance.text")
        _validate_interval(self.start_ms, self.end_ms, "Utterance")
        _optional_string(self.translation, "Utterance.translation")
        _validate_confidence(self.confidence, "Utterance.confidence")

        tokens = _as_tuple(self.tokens, "Utterance.tokens")
        provenance = _string_tuple(self.provenance, "Utterance.provenance")
        object.__setattr__(self, "tokens", tokens)
        object.__setattr__(self, "provenance", provenance)

        previous_span: TextSpan | None = None
        previous_timed_token: BaseToken | None = None
        for index, token in enumerate(tokens):
            if not isinstance(token, BaseToken):
                raise ValueError(f"Utterance.tokens[{index}] must be a BaseToken")
            if token.span.extract(self.text) != token.text:
                raise ValueError(
                    f"Utterance.tokens[{index}].text does not match its source span"
                )
            if previous_span is not None and token.span.start_char < previous_span.end_char:
                raise ValueError("Utterance token character spans must be ordered and non-overlapping")
            previous_span = token.span

            if token.start_ms is not None:
                if token.start_ms < self.start_ms or token.end_ms > self.end_ms:
                    raise ValueError(f"Utterance.tokens[{index}] falls outside utterance audio times")
                if previous_timed_token is not None and (
                    token.start_ms < previous_timed_token.start_ms
                    or token.end_ms < previous_timed_token.end_ms
                ):
                    raise ValueError("Utterance token audio times must be ordered")
                previous_timed_token = token

    def _validate_token_range(self, start: int, end: int) -> None:
        _require_int(start, "start token index")
        _require_int(end, "end token index")
        if start >= end:
            raise ValueError("end token index must be greater than start token index")
        if end > len(self.tokens):
            raise ValueError(f"token range [{start}, {end}) exceeds {len(self.tokens)} tokens")

    def span_for_tokens(self, start: int, end: int) -> TextSpan:
        self._validate_token_range(start, end)
        return TextSpan(self.tokens[start].span.start_char, self.tokens[end - 1].span.end_char)

    def audio_times_for_tokens(self, start: int, end: int) -> tuple[float, float]:
        self._validate_token_range(start, end)
        start_ms = self.tokens[start].start_ms
        end_ms = self.tokens[end - 1].end_ms
        return (
            self.start_ms if start_ms is None else start_ms,
            self.end_ms if end_ms is None else end_ms,
        )


class LearningUnitKind(str, Enum):
    UNKNOWN = "unknown"
    WORD = "word"
    COMPOUND = "compound"
    FIXED_EXPRESSION = "fixed_expression"
    COLLOCATION = "collocation"
    NAMED_ENTITY = "named_entity"
    FREE_PHRASE = "free_phrase"


@dataclass(frozen=True, slots=True)
class LearningComponent:
    text: str
    gloss: str | None = None
    span: TextSpan | None = None

    def __post_init__(self) -> None:
        _require_string(self.text, "LearningComponent.text")
        _optional_string(self.gloss, "LearningComponent.gloss")
        if self.span is not None and not isinstance(self.span, TextSpan):
            raise ValueError("LearningComponent.span must be a TextSpan")


@dataclass(frozen=True, slots=True)
class LearningUnit:
    id: str
    utterance_id: str
    span: TextSpan
    kind: LearningUnitKind
    token_start: int | None = None
    token_end: int | None = None
    contextual_gloss: str | None = None
    components: tuple[LearningComponent, ...] = ()
    confidence: float | None = None
    evidence: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _require_string(self.id, "LearningUnit.id")
        _require_string(self.utterance_id, "LearningUnit.utterance_id")
        if not isinstance(self.span, TextSpan):
            raise ValueError("LearningUnit.span must be a TextSpan")
        try:
            kind = LearningUnitKind(self.kind)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"unsupported learning unit kind: {self.kind!r}") from exc
        object.__setattr__(self, "kind", kind)

        if (self.token_start is None) != (self.token_end is None):
            raise ValueError("LearningUnit.token_start and token_end must be provided together")
        if self.token_start is not None:
            _require_int(self.token_start, "LearningUnit.token_start")
            _require_int(self.token_end, "LearningUnit.token_end")
            if self.token_end <= self.token_start:
                raise ValueError("LearningUnit.token_end must be greater than token_start")

        _optional_string(self.contextual_gloss, "LearningUnit.contextual_gloss")
        _validate_confidence(self.confidence, "LearningUnit.confidence")
        components = _as_tuple(self.components, "LearningUnit.components", none_as_empty=True)
        for index, component in enumerate(components):
            if not isinstance(component, LearningComponent):
                raise ValueError(f"LearningUnit.components[{index}] must be a LearningComponent")
        object.__setattr__(self, "components", components)
        object.__setattr__(self, "evidence", _string_tuple(self.evidence, "LearningUnit.evidence"))


@dataclass(frozen=True, slots=True)
class AudioSpan:
    media_id: str
    start_ms: float
    end_ms: float
    padding_before_ms: float = 250
    padding_after_ms: float = 250

    def __post_init__(self) -> None:
        _require_string(self.media_id, "AudioSpan.media_id")
        _validate_interval(self.start_ms, self.end_ms, "AudioSpan")
        _require_number(self.padding_before_ms, "AudioSpan.padding_before_ms")
        _require_number(self.padding_after_ms, "AudioSpan.padding_after_ms")


class ClozePolicy(str, Enum):
    EXPLICIT_SPAN = "explicit_span"
    PROGRESSIVE_MASK = "progressive_mask"


@dataclass(frozen=True, slots=True)
class CardDraft:
    id: str
    media_id: str
    utterance_id: str
    language: str
    text: str
    policy: ClozePolicy
    target_span: TextSpan | None = None
    target_gloss: str | None = None
    sentence_translation: str | None = None
    analysis: str | None = None
    source_title: str | None = None
    source_url: str | None = None
    audio_span: AudioSpan | None = None
    tags: tuple[str, ...] = ()
    provenance: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _require_string(self.id, "CardDraft.id")
        _require_string(self.media_id, "CardDraft.media_id")
        _require_string(self.utterance_id, "CardDraft.utterance_id")
        _require_string(self.language, "CardDraft.language")
        _require_string(self.text, "CardDraft.text")
        try:
            policy = ClozePolicy(self.policy)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"unsupported cloze policy: {self.policy!r}") from exc
        object.__setattr__(self, "policy", policy)

        if self.target_span is not None:
            if not isinstance(self.target_span, TextSpan):
                raise ValueError("CardDraft.target_span must be a TextSpan")
            self.target_span.extract(self.text)
        if policy is ClozePolicy.EXPLICIT_SPAN and self.target_span is None:
            raise ValueError("explicit_span cards require target_span")

        _optional_string(self.target_gloss, "CardDraft.target_gloss")
        _optional_string(self.sentence_translation, "CardDraft.sentence_translation")
        _optional_string(self.analysis, "CardDraft.analysis")
        _optional_string(self.source_title, "CardDraft.source_title")
        _optional_string(self.source_url, "CardDraft.source_url")
        if self.audio_span is not None:
            if not isinstance(self.audio_span, AudioSpan):
                raise ValueError("CardDraft.audio_span must be an AudioSpan")
            if self.audio_span.media_id != self.media_id:
                raise ValueError("CardDraft.audio_span must reference CardDraft.media_id")
        object.__setattr__(self, "tags", _string_tuple(self.tags, "CardDraft.tags"))
        object.__setattr__(
            self, "provenance", _string_tuple(self.provenance, "CardDraft.provenance")
        )

    def target_text(self) -> str:
        if self.target_span is None:
            return self.text
        return self.target_span.extract(self.text)

    def cloze_text(
        self,
        cloze_number: int = 1,
        hint: str | None = None,
        escape_html: bool = False,
    ) -> str:
        if self.policy is ClozePolicy.PROGRESSIVE_MASK:
            return html.escape(self.text) if escape_html else self.text
        from .cloze import build_cloze

        return build_cloze(
            self.text,
            self.target_span,
            cloze_number=cloze_number,
            hint=hint,
            escape_html=escape_html,
        )


def stable_card_id(
    media_id: str,
    utterance_id: str,
    target_span: TextSpan | None,
    policy: ClozePolicy,
) -> str:
    _require_string(media_id, "media_id")
    _require_string(utterance_id, "utterance_id")
    if target_span is not None and not isinstance(target_span, TextSpan):
        raise ValueError("target_span must be a TextSpan or None")
    try:
        policy_value = ClozePolicy(policy).value
    except (TypeError, ValueError) as exc:
        raise ValueError(f"unsupported cloze policy: {policy!r}") from exc
    identity = {
        "media_id": media_id,
        "policy": policy_value,
        "target_span": None
        if target_span is None
        else [target_span.start_char, target_span.end_char],
        "utterance_id": utterance_id,
    }
    canonical = json.dumps(identity, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class ProjectManifest:
    id: str
    title: str
    language: str
    media: tuple[MediaSource, ...]
    utterances: tuple[Utterance, ...]
    learning_units: tuple[LearningUnit, ...]
    cards: tuple[CardDraft, ...]
    schema_version: int = CURRENT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != CURRENT_SCHEMA_VERSION or type(self.schema_version) is not int:
            raise ValueError(
                f"unsupported schema version {self.schema_version!r}; "
                f"expected {CURRENT_SCHEMA_VERSION}"
            )
        _require_string(self.id, "ProjectManifest.id")
        _require_string(self.title, "ProjectManifest.title")
        _require_string(self.language, "ProjectManifest.language")

        media = _as_tuple(self.media, "ProjectManifest.media")
        utterances = _as_tuple(self.utterances, "ProjectManifest.utterances")
        learning_units = _as_tuple(self.learning_units, "ProjectManifest.learning_units")
        cards = _as_tuple(self.cards, "ProjectManifest.cards")
        object.__setattr__(self, "media", media)
        object.__setattr__(self, "utterances", utterances)
        object.__setattr__(self, "learning_units", learning_units)
        object.__setattr__(self, "cards", cards)

        self._validate_item_types(media, MediaSource, "media")
        self._validate_item_types(utterances, Utterance, "utterances")
        self._validate_item_types(learning_units, LearningUnit, "learning_units")
        self._validate_item_types(cards, CardDraft, "cards")

        media_by_id = self._index_by_id(media, "media")
        utterance_by_id = self._index_by_id(utterances, "utterance")
        self._index_by_id(learning_units, "learning unit")
        self._index_by_id(cards, "card")

        for utterance in utterances:
            source = media_by_id.get(utterance.media_id)
            if source is None:
                raise ValueError(
                    f"utterance {utterance.id!r} references unknown media {utterance.media_id!r}"
                )
            if source.duration_ms is not None and utterance.end_ms > source.duration_ms:
                raise ValueError(f"utterance {utterance.id!r} exceeds its media duration")

        for unit in learning_units:
            utterance = utterance_by_id.get(unit.utterance_id)
            if utterance is None:
                raise ValueError(
                    f"learning unit {unit.id!r} references unknown utterance "
                    f"{unit.utterance_id!r}"
                )
            unit.span.extract(utterance.text)
            if unit.token_start is not None:
                token_span = utterance.span_for_tokens(unit.token_start, unit.token_end)
                if token_span != unit.span:
                    raise ValueError(
                        f"learning unit {unit.id!r} span does not match its token range"
                    )
            for component in unit.components:
                if component.span is None:
                    continue
                if not (
                    unit.span.start_char <= component.span.start_char
                    and component.span.end_char <= unit.span.end_char
                ):
                    raise ValueError(
                        f"component span in learning unit {unit.id!r} falls outside the unit span"
                    )
                if component.span.extract(utterance.text) != component.text:
                    raise ValueError(
                        f"component text in learning unit {unit.id!r} does not match its span"
                    )

        for card in cards:
            source = media_by_id.get(card.media_id)
            if source is None:
                raise ValueError(f"card {card.id!r} references unknown media {card.media_id!r}")
            utterance = utterance_by_id.get(card.utterance_id)
            if utterance is None:
                raise ValueError(
                    f"card {card.id!r} references unknown utterance {card.utterance_id!r}"
                )
            if utterance.media_id != card.media_id:
                raise ValueError(f"card {card.id!r} media does not match its utterance")
            if card.text != utterance.text:
                raise ValueError(f"card {card.id!r} text does not match its utterance")
            if card.audio_span is not None:
                if card.audio_span.media_id not in media_by_id:
                    raise ValueError(
                        f"card {card.id!r} audio references unknown media "
                        f"{card.audio_span.media_id!r}"
                    )
                if (
                    source.duration_ms is not None
                    and card.audio_span.end_ms > source.duration_ms
                ):
                    raise ValueError(f"card {card.id!r} audio exceeds its media duration")

    @staticmethod
    def _validate_item_types(items: tuple[Any, ...], item_type: type[T], name: str) -> None:
        for index, item in enumerate(items):
            if not isinstance(item, item_type):
                raise ValueError(
                    f"ProjectManifest.{name}[{index}] must be a {item_type.__name__}"
                )

    @staticmethod
    def _index_by_id(items: tuple[T, ...], name: str) -> dict[str, T]:
        indexed: dict[str, T] = {}
        for item in items:
            item_id = getattr(item, "id")
            if item_id in indexed:
                raise ValueError(f"duplicate {name} id: {item_id!r}")
            indexed[item_id] = item
        return indexed

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "id": self.id,
            "title": self.title,
            "language": self.language,
            "media": [
                {
                    "id": source.id,
                    "path": source.path,
                    "language": source.language,
                    "title": source.title,
                    "source_url": source.source_url,
                    "checksum_sha256": source.checksum_sha256,
                    "duration_ms": source.duration_ms,
                }
                for source in self.media
            ],
            "utterances": [
                {
                    "id": utterance.id,
                    "media_id": utterance.media_id,
                    "text": utterance.text,
                    "start_ms": utterance.start_ms,
                    "end_ms": utterance.end_ms,
                    "tokens": [
                        {
                            "text": token.text,
                            "span": _span_to_dict(token.span),
                            "start_ms": token.start_ms,
                            "end_ms": token.end_ms,
                            "confidence": token.confidence,
                        }
                        for token in utterance.tokens
                    ],
                    "translation": utterance.translation,
                    "confidence": utterance.confidence,
                    "provenance": list(utterance.provenance),
                }
                for utterance in self.utterances
            ],
            "learning_units": [
                {
                    "id": unit.id,
                    "utterance_id": unit.utterance_id,
                    "span": _span_to_dict(unit.span),
                    "kind": unit.kind.value,
                    "token_start": unit.token_start,
                    "token_end": unit.token_end,
                    "contextual_gloss": unit.contextual_gloss,
                    "components": [
                        {
                            "text": component.text,
                            "gloss": component.gloss,
                            "span": None
                            if component.span is None
                            else _span_to_dict(component.span),
                        }
                        for component in unit.components
                    ],
                    "confidence": unit.confidence,
                    "evidence": list(unit.evidence),
                }
                for unit in self.learning_units
            ],
            "cards": [
                {
                    "id": card.id,
                    "media_id": card.media_id,
                    "utterance_id": card.utterance_id,
                    "language": card.language,
                    "text": card.text,
                    "policy": card.policy.value,
                    "target_span": None
                    if card.target_span is None
                    else _span_to_dict(card.target_span),
                    "target_gloss": card.target_gloss,
                    "sentence_translation": card.sentence_translation,
                    "analysis": card.analysis,
                    "source_title": card.source_title,
                    "source_url": card.source_url,
                    "audio_span": None
                    if card.audio_span is None
                    else {
                        "media_id": card.audio_span.media_id,
                        "start_ms": card.audio_span.start_ms,
                        "end_ms": card.audio_span.end_ms,
                        "padding_before_ms": card.audio_span.padding_before_ms,
                        "padding_after_ms": card.audio_span.padding_after_ms,
                    },
                    "tags": list(card.tags),
                    "provenance": list(card.provenance),
                }
                for card in self.cards
            ],
        }

    def to_json(self, *, indent: int | None = None) -> str:
        separators = (",", ":") if indent is None else None
        return json.dumps(
            self.to_dict(),
            ensure_ascii=False,
            indent=indent,
            separators=separators,
            sort_keys=True,
        )

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Self:
        root = _require_mapping(data, "manifest")
        version = root.get("schema_version")
        if version != CURRENT_SCHEMA_VERSION or type(version) is not int:
            raise ValueError(
                f"unsupported schema version {version!r}; expected {CURRENT_SCHEMA_VERSION}"
            )

        media = tuple(_media_from_dict(item) for item in _require_sequence(root, "media"))
        utterances = tuple(
            _utterance_from_dict(item) for item in _require_sequence(root, "utterances")
        )
        learning_units = tuple(
            _learning_unit_from_dict(item)
            for item in _require_sequence(root, "learning_units")
        )
        cards = tuple(_card_from_dict(item) for item in _require_sequence(root, "cards"))
        return cls(
            id=_required(root, "id", "manifest"),
            title=_required(root, "title", "manifest"),
            language=_required(root, "language", "manifest"),
            media=media,
            utterances=utterances,
            learning_units=learning_units,
            cards=cards,
            schema_version=version,
        )

    @classmethod
    def from_json(cls, payload: str) -> Self:
        if not isinstance(payload, str):
            raise ValueError("manifest JSON payload must be a string")
        return cls.from_dict(json.loads(payload))


def _span_to_dict(span: TextSpan) -> dict[str, int]:
    return {"start_char": span.start_char, "end_char": span.end_char}


def _required(data: Mapping[str, Any], key: str, context: str) -> Any:
    if key not in data:
        raise ValueError(f"missing {context}.{key}")
    return data[key]


def _require_mapping(value: object, context: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{context} must be an object")
    return value


def _require_sequence(data: Mapping[str, Any], key: str) -> Sequence[Any]:
    value = _required(data, key, "manifest")
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise ValueError(f"manifest.{key} must be an array")
    return value


def _parse_span(value: object, context: str) -> TextSpan:
    data = _require_mapping(value, context)
    return TextSpan(
        start_char=_required(data, "start_char", context),
        end_char=_required(data, "end_char", context),
    )


def _media_from_dict(value: object) -> MediaSource:
    data = _require_mapping(value, "media item")
    return MediaSource(
        id=_required(data, "id", "media item"),
        path=_required(data, "path", "media item"),
        language=_required(data, "language", "media item"),
        title=data.get("title"),
        source_url=data.get("source_url"),
        checksum_sha256=data.get("checksum_sha256"),
        duration_ms=data.get("duration_ms"),
    )


def _token_from_dict(value: object) -> BaseToken:
    data = _require_mapping(value, "token")
    return BaseToken(
        text=_required(data, "text", "token"),
        span=_parse_span(_required(data, "span", "token"), "token.span"),
        start_ms=data.get("start_ms"),
        end_ms=data.get("end_ms"),
        confidence=data.get("confidence"),
    )


def _utterance_from_dict(value: object) -> Utterance:
    data = _require_mapping(value, "utterance")
    tokens_value = _required(data, "tokens", "utterance")
    if isinstance(tokens_value, (str, bytes)) or not isinstance(tokens_value, Sequence):
        raise ValueError("utterance.tokens must be an array")
    return Utterance(
        id=_required(data, "id", "utterance"),
        media_id=_required(data, "media_id", "utterance"),
        text=_required(data, "text", "utterance"),
        start_ms=_required(data, "start_ms", "utterance"),
        end_ms=_required(data, "end_ms", "utterance"),
        tokens=tuple(_token_from_dict(item) for item in tokens_value),
        translation=data.get("translation"),
        confidence=data.get("confidence"),
        provenance=data.get("provenance", ()),
    )


def _component_from_dict(value: object) -> LearningComponent:
    data = _require_mapping(value, "learning component")
    span_value = data.get("span")
    return LearningComponent(
        text=_required(data, "text", "learning component"),
        gloss=data.get("gloss"),
        span=None if span_value is None else _parse_span(span_value, "learning component.span"),
    )


def _learning_unit_from_dict(value: object) -> LearningUnit:
    data = _require_mapping(value, "learning unit")
    components_value = data.get("components", ())
    if components_value is None:
        components_value = ()
    if isinstance(components_value, (str, bytes)) or not isinstance(components_value, Sequence):
        raise ValueError("learning unit.components must be an array")
    return LearningUnit(
        id=_required(data, "id", "learning unit"),
        utterance_id=_required(data, "utterance_id", "learning unit"),
        span=_parse_span(_required(data, "span", "learning unit"), "learning unit.span"),
        kind=_required(data, "kind", "learning unit"),
        token_start=data.get("token_start"),
        token_end=data.get("token_end"),
        contextual_gloss=data.get("contextual_gloss"),
        components=tuple(_component_from_dict(item) for item in components_value),
        confidence=data.get("confidence"),
        evidence=data.get("evidence", ()),
    )


def _audio_span_from_dict(value: object) -> AudioSpan:
    data = _require_mapping(value, "audio span")
    return AudioSpan(
        media_id=_required(data, "media_id", "audio span"),
        start_ms=_required(data, "start_ms", "audio span"),
        end_ms=_required(data, "end_ms", "audio span"),
        padding_before_ms=data.get("padding_before_ms", 250),
        padding_after_ms=data.get("padding_after_ms", 250),
    )


def _card_from_dict(value: object) -> CardDraft:
    data = _require_mapping(value, "card")
    span_value = data.get("target_span")
    audio_value = data.get("audio_span")
    return CardDraft(
        id=_required(data, "id", "card"),
        media_id=_required(data, "media_id", "card"),
        utterance_id=_required(data, "utterance_id", "card"),
        language=_required(data, "language", "card"),
        text=_required(data, "text", "card"),
        policy=_required(data, "policy", "card"),
        target_span=None if span_value is None else _parse_span(span_value, "card.target_span"),
        target_gloss=data.get("target_gloss"),
        sentence_translation=data.get("sentence_translation"),
        analysis=data.get("analysis"),
        source_title=data.get("source_title"),
        source_url=data.get("source_url"),
        audio_span=None if audio_value is None else _audio_span_from_dict(audio_value),
        tags=data.get("tags", ()),
        provenance=data.get("provenance", ()),
    )
