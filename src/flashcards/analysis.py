from __future__ import annotations

import hashlib
import json
import math
import os
import re
import stat
import tempfile
import threading
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Self
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit, urlunsplit
from urllib.request import ProxyHandler, Request, build_opener

from .models import LearningUnitKind, Utterance


DEFAULT_OLLAMA_MODEL = "qwen3.5:9b"
DEFAULT_OLLAMA_URL = "http://127.0.0.1:11434"
ANALYSIS_PROMPT_VERSION = "1"
ANALYSIS_CACHE_VERSION = 1
MINIMUM_OLLAMA_VERSION = (0, 32, 0)
MODEL_INFO_TTL_SECONDS = 30.0


class AnalysisError(Exception):
    """Base error for analysis and Ollama failures."""


class ModelUnavailableError(AnalysisError):
    """Raised when the configured Ollama model is not installed."""


class AnalysisValidationError(AnalysisError, ValueError):
    """Raised when analysis data does not match its required shape or source."""


def _require_string(value: object, context: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise AnalysisValidationError(f"{context} must be a nonempty string")
    return value


def _require_int(value: object, context: str, *, minimum: int = 0) -> int:
    if type(value) is not int or value < minimum:
        raise AnalysisValidationError(
            f"{context} must be an integer greater than or equal to {minimum}"
        )
    return value


def _require_bool(value: object, context: str) -> bool:
    if type(value) is not bool:
        raise AnalysisValidationError(f"{context} must be a boolean")
    return value


def _require_mapping(value: object, context: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise AnalysisValidationError(f"{context} must be an object")
    return value


def _require_sequence(value: object, context: str) -> Sequence[Any]:
    if isinstance(value, (str, bytes, bytearray)) or not isinstance(value, Sequence):
        raise AnalysisValidationError(f"{context} must be an array")
    return value


def _require_keys(
    data: Mapping[str, Any],
    required: set[str],
    context: str,
    *,
    optional: set[str] | None = None,
) -> None:
    optional = optional or set()
    keys = set(data)
    missing = sorted(required - keys)
    if missing:
        raise AnalysisValidationError(f"missing {context}.{missing[0]}")
    unexpected = sorted(keys - required - optional)
    if unexpected:
        raise AnalysisValidationError(f"unexpected {context}.{unexpected[0]}")


@dataclass(frozen=True, slots=True)
class AnalysisComponent:
    surface: str
    gloss: str

    def __post_init__(self) -> None:
        _require_string(self.surface, "AnalysisComponent.surface")
        _require_string(self.gloss, "AnalysisComponent.gloss")

    def to_dict(self) -> dict[str, str]:
        return {"surface": self.surface, "gloss": self.gloss}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> Self:
        data = _require_mapping(value, "analysis component")
        _require_keys(data, {"surface", "gloss"}, "analysis component")
        return cls(surface=data["surface"], gloss=data["gloss"])


@dataclass(frozen=True, slots=True)
class LearningUnitSuggestion:
    token_start: int
    token_end: int
    surface: str
    contextual_gloss: str
    kind: LearningUnitKind
    recommended_as_unit: bool
    reason: str
    components: tuple[AnalysisComponent, ...]

    def __post_init__(self) -> None:
        _require_int(self.token_start, "LearningUnitSuggestion.token_start")
        _require_int(self.token_end, "LearningUnitSuggestion.token_end")
        if self.token_end <= self.token_start:
            raise AnalysisValidationError(
                "LearningUnitSuggestion.token_end must be greater than token_start"
            )
        _require_string(self.surface, "LearningUnitSuggestion.surface")
        _require_string(self.contextual_gloss, "LearningUnitSuggestion.contextual_gloss")
        try:
            kind = LearningUnitKind(self.kind)
        except (TypeError, ValueError) as exc:
            raise AnalysisValidationError(
                f"unsupported learning unit kind: {self.kind!r}"
            ) from exc
        object.__setattr__(self, "kind", kind)
        _require_bool(
            self.recommended_as_unit,
            "LearningUnitSuggestion.recommended_as_unit",
        )
        _require_string(self.reason, "LearningUnitSuggestion.reason")
        components = _require_sequence(
            self.components,
            "LearningUnitSuggestion.components",
        )
        for index, component in enumerate(components):
            if not isinstance(component, AnalysisComponent):
                raise AnalysisValidationError(
                    "LearningUnitSuggestion.components"
                    f"[{index}] must be an AnalysisComponent"
                )
        object.__setattr__(self, "components", tuple(components))

    def to_dict(self) -> dict[str, Any]:
        return {
            "token_start": self.token_start,
            "token_end": self.token_end,
            "surface": self.surface,
            "contextual_gloss": self.contextual_gloss,
            "kind": self.kind.value,
            "recommended_as_unit": self.recommended_as_unit,
            "reason": self.reason,
            "components": [component.to_dict() for component in self.components],
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> Self:
        data = _require_mapping(value, "learning unit suggestion")
        fields = {
            "token_start",
            "token_end",
            "surface",
            "contextual_gloss",
            "kind",
            "recommended_as_unit",
            "reason",
            "components",
        }
        _require_keys(data, fields, "learning unit suggestion")
        component_values = _require_sequence(
            data["components"],
            "learning unit suggestion.components",
        )
        return cls(
            token_start=data["token_start"],
            token_end=data["token_end"],
            surface=data["surface"],
            contextual_gloss=data["contextual_gloss"],
            kind=data["kind"],
            recommended_as_unit=data["recommended_as_unit"],
            reason=data["reason"],
            components=tuple(
                AnalysisComponent.from_dict(component) for component in component_values
            ),
        )


@dataclass(frozen=True, slots=True)
class SentenceAnalysis:
    sentence_translation: str
    candidates: tuple[LearningUnitSuggestion, ...]
    model: str
    model_digest: str
    prompt_version: str

    def __post_init__(self) -> None:
        _require_string(self.sentence_translation, "SentenceAnalysis.sentence_translation")
        candidates = _require_sequence(self.candidates, "SentenceAnalysis.candidates")
        for index, candidate in enumerate(candidates):
            if not isinstance(candidate, LearningUnitSuggestion):
                raise AnalysisValidationError(
                    f"SentenceAnalysis.candidates[{index}] must be a LearningUnitSuggestion"
                )
        object.__setattr__(self, "candidates", tuple(candidates))
        _require_string(self.model, "SentenceAnalysis.model")
        _require_string(self.model_digest, "SentenceAnalysis.model_digest")
        _require_string(self.prompt_version, "SentenceAnalysis.prompt_version")

    def to_dict(self) -> dict[str, Any]:
        return {
            "sentence_translation": self.sentence_translation,
            "candidates": [candidate.to_dict() for candidate in self.candidates],
            "model": self.model,
            "model_digest": self.model_digest,
            "prompt_version": self.prompt_version,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> Self:
        data = _require_mapping(value, "sentence analysis")
        fields = {
            "sentence_translation",
            "candidates",
            "model",
            "model_digest",
            "prompt_version",
        }
        _require_keys(data, fields, "sentence analysis")
        candidate_values = _require_sequence(
            data["candidates"],
            "sentence analysis.candidates",
        )
        return cls(
            sentence_translation=data["sentence_translation"],
            candidates=tuple(
                LearningUnitSuggestion.from_dict(candidate)
                for candidate in candidate_values
            ),
            model=data["model"],
            model_digest=data["model_digest"],
            prompt_version=data["prompt_version"],
        )


@dataclass(frozen=True, slots=True)
class OllamaModelInfo:
    name: str
    digest: str
    size: int | None = None

    def __post_init__(self) -> None:
        _require_string(self.name, "OllamaModelInfo.name")
        _require_string(self.digest, "OllamaModelInfo.digest")
        if self.size is not None:
            _require_int(self.size, "OllamaModelInfo.size")

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "digest": self.digest, "size": self.size}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> Self:
        data = _require_mapping(value, "Ollama model info")
        _require_keys(
            data,
            {"name", "digest"},
            "Ollama model info",
            optional={"size"},
        )
        return cls(name=data["name"], digest=data["digest"], size=data.get("size"))


_MODEL_PART = r"[A-Za-z0-9][A-Za-z0-9._-]*"
_MODEL_NAME_RE = re.compile(rf"{_MODEL_PART}(?:/{_MODEL_PART})*(?::{_MODEL_PART})?\Z")
_LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}


def _validate_model_name(model: object) -> str:
    if (
        not isinstance(model, str)
        or len(model) > 256
        or _MODEL_NAME_RE.fullmatch(model) is None
    ):
        raise ValueError(
            "model must be a valid Ollama model name such as "
            f"{DEFAULT_OLLAMA_MODEL!r}"
        )
    return model


def _validate_base_url(base_url: object) -> str:
    if not isinstance(base_url, str) or not base_url or base_url != base_url.strip():
        raise ValueError("base_url must be a loopback HTTP Ollama URL")
    try:
        parsed = urlsplit(base_url)
        port = parsed.port
    except ValueError as exc:
        raise ValueError("base_url contains an invalid port or host") from exc
    if parsed.scheme.lower() != "http":
        raise ValueError("base_url must use HTTP")
    if parsed.hostname is None or parsed.hostname.lower() not in _LOOPBACK_HOSTS:
        raise ValueError(
            "base_url must use a loopback host: 127.0.0.1, localhost, or ::1"
        )
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("base_url must not contain credentials")
    if port is not None and port == 0:
        raise ValueError("base_url must use a valid nonzero port")
    if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
        raise ValueError("base_url must contain only the Ollama server origin")
    return urlunsplit(("http", parsed.netloc, "", "", ""))


def _model_names_match(requested: str, installed: object) -> bool:
    if not isinstance(installed, str):
        return False
    return installed == requested or (
        ":" not in requested and installed == f"{requested}:latest"
    )


def _analysis_schema(token_count: int) -> dict[str, Any]:
    component = {
        "type": "object",
        "additionalProperties": False,
        "required": ["surface", "gloss"],
        "properties": {
            "surface": {"type": "string", "minLength": 1},
            "gloss": {"type": "string", "minLength": 1},
        },
    }
    candidate = {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "token_start",
            "token_end",
            "surface",
            "contextual_gloss",
            "kind",
            "recommended_as_unit",
            "reason",
            "components",
        ],
        "properties": {
            "token_start": {
                "type": "integer",
                "minimum": 0,
                "maximum": max(0, token_count - 1),
            },
            "token_end": {
                "type": "integer",
                "minimum": 1,
                "maximum": token_count,
            },
            "surface": {"type": "string", "minLength": 1},
            "contextual_gloss": {"type": "string", "minLength": 1},
            "kind": {
                "type": "string",
                "enum": [kind.value for kind in LearningUnitKind],
            },
            "recommended_as_unit": {"type": "boolean"},
            "reason": {"type": "string", "minLength": 1},
            "components": {"type": "array", "items": component},
        },
    }
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["sentence_translation", "candidates"],
        "properties": {
            "sentence_translation": {"type": "string", "minLength": 1},
            "candidates": {"type": "array", "items": candidate},
        },
    }


def _analysis_prompt(
    utterance: Utterance,
    language: str,
    translation_language: str,
) -> str:
    token_lines = "\n".join(
        f"{index}. "
        + json.dumps(
            {
                "text": token.text,
                "start_char": token.span.start_char,
                "end_char": token.span.end_char,
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
        for index, token in enumerate(utterance.tokens)
    )
    return f"""Analyze the sentence for language learning without assuming any particular script, segmentation convention, grammar, or language family.

Source language: {json.dumps(language, ensure_ascii=False)}
Desired translation language: {json.dumps(translation_language, ensure_ascii=False)}
Exact sentence: {json.dumps(utterance.text, ensure_ascii=False)}

The following zero-based token list is immutable. Do not split, merge, reorder, edit, or renumber its entries. Every candidate must use a half-open range [token_start, token_end), and surface must exactly equal the source characters from the first selected token through the last selected token, including intervening source characters.
{token_lines}

Return only the JSON object required by the schema. Translate the complete sentence into the desired translation language. Include only meaningful language-learning candidates: lexical compounds, fixed expressions, idioms, collocations, and useful independent words. Do not create arbitrary grammar chunks or spans that are meaningful only because adjacent tokens form part of the sentence. Overlapping candidates are allowed when each is independently useful, but do not repeat a token range.

For each candidate use exactly these fields: token_start, token_end, surface, contextual_gloss, kind, recommended_as_unit, reason, and components. A component uses exactly surface and gloss; use an empty components array when decomposition is not useful. The glosses, translation, and reasons must be in the desired translation language."""


def _validate_analysis_for_utterance(
    analysis: SentenceAnalysis,
    utterance: Utterance,
) -> None:
    if not isinstance(analysis, SentenceAnalysis):
        raise AnalysisValidationError("analysis must be a SentenceAnalysis")
    if not isinstance(utterance, Utterance):
        raise AnalysisValidationError("utterance must be an Utterance")
    seen_ranges: set[tuple[int, int]] = set()
    for index, candidate in enumerate(analysis.candidates):
        start = candidate.token_start
        end = candidate.token_end
        if not 0 <= start < end <= len(utterance.tokens):
            raise AnalysisValidationError(
                f"candidates[{index}] token range [{start}, {end}) is outside "
                f"the {len(utterance.tokens)} source tokens"
            )
        token_range = (start, end)
        if token_range in seen_ranges:
            raise AnalysisValidationError(
                f"candidates[{index}] duplicates token range [{start}, {end})"
            )
        seen_ranges.add(token_range)
        source_surface = utterance.span_for_tokens(start, end).extract(utterance.text)
        if candidate.surface != source_surface:
            raise AnalysisValidationError(
                f"candidates[{index}].surface must be exactly {source_surface!r} "
                f"for token range [{start}, {end})"
            )
        if not any(character.isalnum() for character in source_surface):
            raise AnalysisValidationError(
                f"candidates[{index}] must contain at least one alphanumeric character"
            )


class OllamaAnalyzer:
    def __init__(
        self,
        model: str = DEFAULT_OLLAMA_MODEL,
        base_url: str = DEFAULT_OLLAMA_URL,
        timeout_seconds: float = 180.0,
    ) -> None:
        self.model = _validate_model_name(model)
        self.base_url = _validate_base_url(base_url)
        if (
            isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, (int, float))
            or not math.isfinite(timeout_seconds)
            or timeout_seconds <= 0
        ):
            raise ValueError("timeout_seconds must be a positive finite number")
        self.timeout_seconds = float(timeout_seconds)
        self._proxy_handler = ProxyHandler({})
        self._opener = build_opener(self._proxy_handler)
        self._server_version_cache: str | None = None
        self._model_info_cache: OllamaModelInfo | None = None
        self._model_info_cached_at = 0.0
        self._model_info_lock = threading.RLock()

    def _request_json(
        self,
        method: str,
        path: str,
        payload: Mapping[str, Any] | None = None,
    ) -> Any:
        """Make one Ollama request; subclasses may override this network boundary."""
        method = method.upper()
        if method not in {"GET", "POST"} or not path.startswith("/"):
            raise ValueError("invalid Ollama request method or path")
        body = None
        headers = {"Accept": "application/json"}
        if payload is not None:
            body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode(
                "utf-8"
            )
            headers["Content-Type"] = "application/json"
        request = Request(
            self.base_url + path,
            data=body,
            headers=headers,
            method=method,
        )
        try:
            with self._opener.open(request, timeout=self.timeout_seconds) as response:
                response_body = response.read()
        except HTTPError as exc:
            try:
                detail = exc.read().decode("utf-8", errors="replace").strip()
            except OSError:
                detail = ""
            suffix = f": {detail}" if detail else ""
            raise AnalysisError(
                f"Ollama {method} {path} failed with HTTP {exc.code}{suffix}"
            ) from exc
        except (URLError, TimeoutError, OSError) as exc:
            raise AnalysisError(
                f"Could not reach Ollama at {self.base_url}; ensure Ollama is running "
                f"with `ollama serve`: {exc}"
            ) from exc
        try:
            result = json.loads(response_body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise AnalysisError(
                f"Ollama {method} {path} returned a malformed JSON response"
            ) from exc
        if isinstance(result, Mapping) and "error" in result:
            detail = result["error"]
            if not isinstance(detail, str) or not detail.strip():
                detail = repr(detail)
            raise AnalysisError(f"Ollama {method} {path} failed: {detail}")
        return result

    def server_version(self) -> str:
        with self._model_info_lock:
            if self._server_version_cache is not None:
                return self._server_version_cache
            response = self._request_json("GET", "/api/version")
            if not isinstance(response, Mapping):
                raise AnalysisError("Ollama /api/version response must be a JSON object")
            version = response.get("version")
            if not isinstance(version, str):
                raise AnalysisError("Ollama /api/version response has no valid version")
            match = re.match(r"^(\d+)\.(\d+)\.(\d+)", version)
            if match is None:
                raise AnalysisError(f"Ollama returned an unrecognized version {version!r}")
            parsed = tuple(int(part) for part in match.groups())
            if parsed < MINIMUM_OLLAMA_VERSION:
                minimum = ".".join(str(part) for part in MINIMUM_OLLAMA_VERSION)
                raise AnalysisError(
                    f"Ollama {version} is too old for structured qwen3.5 output; "
                    f"upgrade to Ollama >= {minimum}"
                )
            self._server_version_cache = version
            return version

    def model_info(self, *, refresh: bool = False) -> OllamaModelInfo:
        with self._model_info_lock:
            now = time.monotonic()
            if (
                not refresh
                and self._model_info_cache is not None
                and now - self._model_info_cached_at < MODEL_INFO_TTL_SECONDS
            ):
                return self._model_info_cache
            self._model_info_cache = None
            self._model_info_cached_at = 0.0
            self.server_version()
            response = self._request_json("GET", "/api/tags")
            if not isinstance(response, Mapping):
                raise AnalysisError("Ollama /api/tags response must be a JSON object")
            models = response.get("models")
            if isinstance(models, (str, bytes)) or not isinstance(models, Sequence):
                raise AnalysisError("Ollama /api/tags response has no valid models array")
            match: Mapping[str, Any] | None = None
            for entry in models:
                if isinstance(entry, Mapping) and _model_names_match(
                    self.model,
                    entry.get("name"),
                ):
                    match = entry
                    break
            if match is None:
                raise ModelUnavailableError(
                    f"Ollama model {self.model!r} is not installed; run "
                    f"`ollama pull {self.model}`"
                )
            if match.get("remote_model") or match.get("remote_host"):
                raise AnalysisError(
                    f"Ollama model {self.model!r} is cloud-backed; choose a model "
                    "whose weights are installed locally"
                )
            try:
                info = OllamaModelInfo(
                    name=match.get("name"),
                    digest=match.get("digest"),
                    size=match.get("size"),
                )
            except AnalysisValidationError as exc:
                raise AnalysisError(
                    f"Ollama returned invalid metadata for model {self.model!r}: {exc}"
                ) from exc
            self._model_info_cache = info
            self._model_info_cached_at = now
            return info

    def analyze(
        self,
        utterance: Utterance,
        language: str,
        translation_language: str = "English",
    ) -> SentenceAnalysis:
        if not isinstance(utterance, Utterance):
            raise TypeError("utterance must be an Utterance")
        language = _require_string(language, "language")
        translation_language = _require_string(
            translation_language,
            "translation_language",
        )
        model_info = self.model_info(refresh=True)
        messages: list[dict[str, str]] = [
            {
                "role": "system",
                "content": (
                    "You are a precise, language-neutral linguistic analyst. "
                    "Obey the supplied immutable source text and token boundaries, "
                    "and output only schema-conforming JSON."
                ),
            },
            {
                "role": "user",
                "content": _analysis_prompt(
                    utterance,
                    language,
                    translation_language,
                ),
            },
        ]
        schema = _analysis_schema(len(utterance.tokens))

        for attempt in range(2):
            response = self._request_json(
                "POST",
                "/api/chat",
                {
                    "model": self.model,
                    "messages": messages,
                    "stream": False,
                    "think": False,
                    "format": schema,
                    "keep_alive": "10m",
                    "options": {
                        "temperature": 0,
                        "num_ctx": 4096,
                        "num_predict": 768,
                    },
                },
            )
            prior_output = _render_prior_output(response)
            try:
                content = _message_content(response)
                prior_output = content
                analysis = _parse_model_analysis(content, model_info)
                _validate_analysis_for_utterance(analysis, utterance)
                return analysis
            except AnalysisValidationError as exc:
                concise_error = _concise_error(exc)
                if attempt == 1:
                    raise AnalysisValidationError(
                        "Ollama returned invalid analysis after one corrective retry: "
                        f"{concise_error}"
                    ) from exc
                messages = [
                    *messages,
                    {
                        "role": "user",
                        "content": (
                            "Correct the previous output and return only the corrected "
                            f"JSON object. Validation error: {concise_error}. "
                            "Previous output: "
                            + json.dumps(prior_output, ensure_ascii=False)
                        ),
                    },
                ]
        raise AssertionError("unreachable")


def _message_content(response: object) -> str:
    if not isinstance(response, Mapping):
        raise AnalysisValidationError("Ollama chat response must be an object")
    message = response.get("message")
    if not isinstance(message, Mapping):
        raise AnalysisValidationError("Ollama chat response.message must be an object")
    content = message.get("content")
    if not isinstance(content, str) or not content.strip():
        raise AnalysisValidationError(
            "Ollama chat response.message.content must be a nonempty JSON string"
        )
    return content


def _parse_model_analysis(content: str, model_info: OllamaModelInfo) -> SentenceAnalysis:
    try:
        value = json.loads(content)
    except json.JSONDecodeError as exc:
        raise AnalysisValidationError(
            f"message.content is not valid JSON at line {exc.lineno}, column {exc.colno}"
        ) from exc
    data = _require_mapping(value, "model analysis")
    _require_keys(
        data,
        {"sentence_translation", "candidates"},
        "model analysis",
    )
    candidate_values = _require_sequence(data["candidates"], "model analysis.candidates")
    return SentenceAnalysis(
        sentence_translation=data["sentence_translation"],
        candidates=tuple(
            LearningUnitSuggestion.from_dict(candidate) for candidate in candidate_values
        ),
        model=model_info.name,
        model_digest=model_info.digest,
        prompt_version=ANALYSIS_PROMPT_VERSION,
    )


def _render_prior_output(response: object) -> str:
    try:
        return json.dumps(response, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError):
        return repr(response)


def _concise_error(error: BaseException) -> str:
    message = " ".join(str(error).split())
    return message[:300] if message else error.__class__.__name__


class AnalysisCache:
    def __init__(self, path: str | os.PathLike[str]) -> None:
        self.path = Path(path)
        self._lock = threading.RLock()
        self._loaded = False
        self._entries: dict[str, dict[str, Any]] = {}

    def _load_locked(self) -> None:
        if self._loaded:
            return
        entries: dict[str, dict[str, Any]] = {}
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, UnicodeDecodeError, json.JSONDecodeError):
            self._entries = entries
            self._loaded = True
            return
        if (
            not isinstance(value, Mapping)
            or set(value) != {"schema_version", "entries"}
            or type(value.get("schema_version")) is not int
            or value.get("schema_version") != ANALYSIS_CACHE_VERSION
            or not isinstance(value.get("entries"), Mapping)
        ):
            self._entries = entries
            self._loaded = True
            return
        for key, entry in value["entries"].items():
            if isinstance(key, str) and key and isinstance(entry, Mapping):
                try:
                    entries[key] = SentenceAnalysis.from_dict(entry).to_dict()
                except AnalysisValidationError:
                    continue
        self._entries = entries
        self._loaded = True

    def get(self, key: str) -> SentenceAnalysis | None:
        if not isinstance(key, str) or not key:
            raise ValueError("cache key must be a nonempty string")
        with self._lock:
            self._load_locked()
            value = self._entries.get(key)
            if value is None:
                return None
            try:
                return SentenceAnalysis.from_dict(value)
            except (AnalysisValidationError, TypeError, ValueError):
                return None

    def put(self, key: str, analysis: SentenceAnalysis) -> None:
        if not isinstance(key, str) or not key:
            raise ValueError("cache key must be a nonempty string")
        if not isinstance(analysis, SentenceAnalysis):
            raise TypeError("analysis must be a SentenceAnalysis")
        serialized = analysis.to_dict()
        with self._lock:
            self._load_locked()
            entries = dict(self._entries)
            entries[key] = serialized
            payload = {
                "schema_version": ANALYSIS_CACHE_VERSION,
                "entries": entries,
            }
            self._write_locked(payload)
            self._entries = entries

    def _write_locked(self, payload: Mapping[str, Any]) -> None:
        encoded = (
            json.dumps(
                payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        )
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            try:
                mode = stat.S_IMODE(self.path.stat().st_mode)
            except FileNotFoundError:
                mode = 0o600
            descriptor, temporary_name = tempfile.mkstemp(
                prefix=f".{self.path.name}.",
                suffix=".tmp",
                dir=self.path.parent,
            )
        except OSError as exc:
            raise AnalysisError(f"Could not prepare analysis cache {self.path}: {exc}") from exc

        temporary_path = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
                if hasattr(os, "fchmod"):
                    os.fchmod(handle.fileno(), mode)
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            if not hasattr(os, "fchmod"):
                os.chmod(temporary_path, mode)
            os.replace(temporary_path, self.path)
        except OSError as exc:
            try:
                temporary_path.unlink()
            except FileNotFoundError:
                pass
            raise AnalysisError(f"Could not write analysis cache {self.path}: {exc}") from exc


class AnalysisService:
    def __init__(self, analyzer: OllamaAnalyzer, cache: AnalysisCache) -> None:
        if not isinstance(analyzer, OllamaAnalyzer):
            raise TypeError("analyzer must be an OllamaAnalyzer")
        if not isinstance(cache, AnalysisCache):
            raise TypeError("cache must be an AnalysisCache")
        self.analyzer = analyzer
        self.cache = cache
        self._inference_lock = threading.Lock()

    def analyze(
        self,
        utterance: Utterance,
        language: str,
        translation_language: str = "English",
        refresh: bool = False,
    ) -> SentenceAnalysis:
        if not isinstance(utterance, Utterance):
            raise TypeError("utterance must be an Utterance")
        language = _require_string(language, "language")
        translation_language = _require_string(
            translation_language,
            "translation_language",
        )
        if type(refresh) is not bool:
            raise TypeError("refresh must be a boolean")

        with self._inference_lock:
            model_info = self.analyzer.model_info(refresh=True)
            cache_key = self._cache_key(
                model_info,
                utterance,
                language,
                translation_language,
            )
            if not refresh:
                cached = self.cache.get(cache_key)
                if cached is not None:
                    try:
                        self._validate_metadata(cached, model_info)
                        _validate_analysis_for_utterance(cached, utterance)
                    except AnalysisValidationError:
                        pass
                    else:
                        return cached

            analysis = self.analyzer.analyze(
                utterance,
                language,
                translation_language,
            )
            result_model_info = OllamaModelInfo(
                analysis.model,
                analysis.model_digest,
            )
            self._validate_metadata(analysis, result_model_info)
            _validate_analysis_for_utterance(analysis, utterance)
            result_cache_key = self._cache_key(
                result_model_info,
                utterance,
                language,
                translation_language,
            )
            self.cache.put(result_cache_key, analysis)
            return analysis

    def status(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "enabled": True,
            "available": False,
            "model": self.analyzer.model,
        }
        try:
            info = self.analyzer.model_info()
        except ModelUnavailableError as exc:
            result["error"] = str(exc)
            result["pull_command"] = f"ollama pull {self.analyzer.model}"
        except AnalysisError as exc:
            result["error"] = str(exc)
        else:
            result["available"] = True
            result["digest"] = info.digest
        return result

    @staticmethod
    def _validate_metadata(
        analysis: SentenceAnalysis,
        model_info: OllamaModelInfo,
    ) -> None:
        if analysis.model != model_info.name:
            raise AnalysisValidationError("analysis model does not match the configured model")
        if analysis.model_digest != model_info.digest:
            raise AnalysisValidationError("analysis model digest does not match the installed model")
        if analysis.prompt_version != ANALYSIS_PROMPT_VERSION:
            raise AnalysisValidationError("analysis prompt version is not current")

    @staticmethod
    def _cache_key(
        model_info: OllamaModelInfo,
        utterance: Utterance,
        language: str,
        translation_language: str,
    ) -> str:
        identity = {
            "model_digest": model_info.digest,
            "prompt_version": ANALYSIS_PROMPT_VERSION,
            "language": language,
            "translation_language": translation_language,
            "text": utterance.text,
            "tokens": [
                {
                    "text": token.text,
                    "start_char": token.span.start_char,
                    "end_char": token.span.end_char,
                }
                for token in utterance.tokens
            ],
        }
        canonical = json.dumps(
            identity,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
