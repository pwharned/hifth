from __future__ import annotations

import argparse
import hmac
import json
import mimetypes
import os
import re
import secrets
import stat
import tempfile
import threading
import webbrowser
from dataclasses import replace
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote, unquote, urlsplit

from .analysis import (
    DEFAULT_OLLAMA_MODEL,
    DEFAULT_OLLAMA_URL,
    AnalysisCache,
    AnalysisError,
    AnalysisService,
    AnalysisValidationError,
    LearningUnitSuggestion,
    ModelUnavailableError,
    OllamaAnalyzer,
    SentenceAnalysis,
)
from .export import export_manifest_apkg, load_manifest
from .models import (
    AudioSpan,
    CardDraft,
    ClozePolicy,
    LearningComponent,
    LearningUnit,
    LearningUnitKind,
    ProjectManifest,
    Utterance,
    stable_card_id,
)


MAX_REQUEST_BYTES = 1024 * 1024


def _optional_text(value: object, name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a string")
    value = value.strip()
    return value or None


def _token_index(value: object, name: str) -> int:
    if type(value) is not int or value < 0:
        raise ValueError(f"{name} must be a nonnegative integer")
    return value


class ReviewProject:
    def __init__(self, manifest_path: str | Path, output_path: str | Path | None = None) -> None:
        self.manifest_path = Path(manifest_path).resolve()
        self.output_path = (
            Path(output_path).resolve()
            if output_path is not None
            else self.manifest_path.with_suffix(".apkg")
        )
        self._lock = threading.RLock()
        self.manifest = load_manifest(self.manifest_path)

    def api_payload(self) -> dict[str, Any]:
        with self._lock:
            return {
                "project": self.manifest.to_dict(),
                "export_path": str(self.output_path),
                "media_urls": {
                    source.id: f"/media/{source.id}" for source in self.manifest.media
                },
            }

    def resolve_media(self, media_id: str) -> Path:
        with self._lock:
            source = next((item for item in self.manifest.media if item.id == media_id), None)
            if source is None:
                raise FileNotFoundError(f"Unknown media id: {media_id}")
            path = Path(source.path)
            if not path.is_absolute():
                path = self.manifest_path.parent / path
            if not path.is_file():
                raise FileNotFoundError(f"Media file not found: {path}")
            return path

    def get_utterance(self, utterance_id: str) -> Utterance:
        with self._lock:
            utterance = next(
                (item for item in self.manifest.utterances if item.id == utterance_id),
                None,
            )
            if utterance is None:
                raise ValueError(f"unknown utterance: {utterance_id}")
            return utterance

    def upsert_card(
        self,
        payload: dict[str, Any],
        suggestion: tuple[SentenceAnalysis, LearningUnitSuggestion] | None = None,
    ) -> CardDraft:
        if not isinstance(payload, dict):
            raise ValueError("request body must be an object")
        reserved_evidence_fields = {
            "components",
            "analysis_model",
            "analysis_digest",
            "prompt_version",
        }
        if reserved_evidence_fields.intersection(payload):
            raise ValueError("model evidence must be verified by the reviewer service")
        utterance_id = payload.get("utterance_id")
        if not isinstance(utterance_id, str) or not utterance_id.strip():
            raise ValueError("utterance_id must be a nonempty string")
        token_start = _token_index(payload.get("token_start"), "token_start")
        token_end = _token_index(payload.get("token_end"), "token_end")

        with self._lock:
            utterance = self.get_utterance(utterance_id)
            target_span = utterance.span_for_tokens(token_start, token_end)
            card_id = stable_card_id(
                utterance.media_id,
                utterance.id,
                target_span,
                ClozePolicy.EXPLICIT_SPAN,
            )
            source = next(item for item in self.manifest.media if item.id == utterance.media_id)
            tags_value = payload.get("tags", ())
            if isinstance(tags_value, str) or not isinstance(tags_value, list | tuple):
                raise ValueError("tags must be an array of strings")
            tags = tuple(tag.strip() for tag in tags_value if isinstance(tag, str) and tag.strip())
            if len(tags) != len(tags_value):
                raise ValueError("tags must contain only nonempty strings")
            try:
                kind = LearningUnitKind(payload.get("kind", LearningUnitKind.UNKNOWN.value))
            except (TypeError, ValueError) as exc:
                raise ValueError("unsupported learning-unit kind") from exc

            target_gloss = _optional_text(payload.get("target_gloss"), "target_gloss")
            sentence_translation = _optional_text(
                payload.get("sentence_translation"), "sentence_translation"
            )
            analysis = _optional_text(payload.get("analysis"), "analysis")
            existing_card = next(
                (item for item in self.manifest.cards if item.id == card_id),
                None,
            )
            existing_unit = next(
                (
                    item
                    for item in self.manifest.learning_units
                    if item.id == f"unit-{card_id[:24]}"
                ),
                None,
            )
            if suggestion is not None:
                sentence_analysis, candidate = suggestion
                if not isinstance(sentence_analysis, SentenceAnalysis) or not isinstance(
                    candidate, LearningUnitSuggestion
                ):
                    raise ValueError("suggestion must contain validated analysis objects")
                if candidate not in sentence_analysis.candidates:
                    raise ValueError("suggestion candidate is not part of its sentence analysis")
                if (candidate.token_start, candidate.token_end) != (token_start, token_end):
                    raise ValueError("suggestion does not match the selected token range")
                components = tuple(
                    LearningComponent(component.surface, component.gloss)
                    for component in candidate.components
                )
                provenance = (
                    "manual-review",
                    f"model:{sentence_analysis.model}",
                    f"model-digest:{sentence_analysis.model_digest}",
                    f"prompt:{sentence_analysis.prompt_version}",
                )
            else:
                components = existing_unit.components if existing_unit is not None else ()
                provenance = (
                    existing_card.provenance
                    if existing_card is not None
                    else ("manual-review",)
                )
            card = CardDraft(
                id=card_id,
                media_id=utterance.media_id,
                utterance_id=utterance.id,
                language=self.manifest.language,
                text=utterance.text,
                policy=ClozePolicy.EXPLICIT_SPAN,
                target_span=target_span,
                target_gloss=target_gloss,
                sentence_translation=sentence_translation,
                analysis=analysis,
                source_title=source.title,
                source_url=source.source_url,
                audio_span=AudioSpan(
                    utterance.media_id,
                    utterance.start_ms,
                    utterance.end_ms,
                ),
                tags=tags,
                provenance=provenance,
            )
            unit = LearningUnit(
                id=f"unit-{card_id[:24]}",
                utterance_id=utterance.id,
                span=target_span,
                kind=kind,
                token_start=token_start,
                token_end=token_end,
                contextual_gloss=target_gloss,
                components=components,
                evidence=provenance,
            )
            cards = self._upsert_by_id(self.manifest.cards, card)
            units = self._upsert_by_id(self.manifest.learning_units, unit)
            self.manifest = replace(self.manifest, cards=cards, learning_units=units)
            self._save()
            return card

    def delete_card(self, card_id: str) -> None:
        with self._lock:
            if not any(card.id == card_id for card in self.manifest.cards):
                raise ValueError(f"unknown card: {card_id}")
            cards = tuple(card for card in self.manifest.cards if card.id != card_id)
            unit_id = f"unit-{card_id[:24]}"
            units = tuple(unit for unit in self.manifest.learning_units if unit.id != unit_id)
            self.manifest = replace(self.manifest, cards=cards, learning_units=units)
            self._save()

    def export(self) -> Path:
        with self._lock:
            manifest = self.manifest
        return export_manifest_apkg(
            manifest,
            self.output_path,
            media_base_dir=self.manifest_path.parent,
        )

    @staticmethod
    def _upsert_by_id(items: tuple[Any, ...], replacement: Any) -> tuple[Any, ...]:
        result = []
        found = False
        for item in items:
            if item.id == replacement.id:
                result.append(replacement)
                found = True
            else:
                result.append(item)
        if not found:
            result.append(replacement)
        return tuple(result)

    def _save(self) -> None:
        permissions = stat.S_IMODE(self.manifest_path.stat().st_mode)
        descriptor, temp_name = tempfile.mkstemp(
            dir=self.manifest_path.parent,
            prefix=f".{self.manifest_path.name}.",
            suffix=".tmp",
        )
        temp_path = Path(temp_name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as manifest_file:
                manifest_file.write(self.manifest.to_json(indent=2) + "\n")
                manifest_file.flush()
                os.fsync(manifest_file.fileno())
            temp_path.chmod(permissions)
            temp_path.replace(self.manifest_path)
        finally:
            temp_path.unlink(missing_ok=True)


def _handler(
    project: ReviewProject,
    static_dir: Path,
    access_token: str,
    analysis_service: AnalysisService | None = None,
    translation_language: str = "English",
) -> type[BaseHTTPRequestHandler]:
    def api_payload() -> dict[str, Any]:
        payload = project.api_payload()
        payload["analysis"] = (
            analysis_service.status()
            if analysis_service is not None
            else {
                "enabled": False,
                "available": False,
                "error": "Local model analysis is disabled",
            }
        )
        payload["analysis"]["translation_language"] = translation_language
        return payload

    def verified_suggestion(
        request: dict[str, Any],
    ) -> tuple[SentenceAnalysis, LearningUnitSuggestion] | None:
        reference = request.pop("analysis_ref", None)
        if reference is None:
            return None
        if analysis_service is None:
            raise ValueError("model analysis is disabled")
        if not isinstance(reference, dict) or set(reference) != {
            "model",
            "model_digest",
            "prompt_version",
        }:
            raise ValueError(
                "analysis_ref must contain exactly model, model_digest, and prompt_version"
            )
        utterance_id = request.get("utterance_id")
        if not isinstance(utterance_id, str):
            raise ValueError("utterance_id must be a nonempty string")
        token_start = _token_index(request.get("token_start"), "token_start")
        token_end = _token_index(request.get("token_end"), "token_end")
        result = analysis_service.analyze(
            project.get_utterance(utterance_id),
            project.manifest.language,
            translation_language,
        )
        expected_reference = {
            "model": result.model,
            "model_digest": result.model_digest,
            "prompt_version": result.prompt_version,
        }
        if reference != expected_reference:
            raise ValueError("analysis_ref does not match the current validated analysis")
        candidate = next(
            (
                item
                for item in result.candidates
                if (item.token_start, item.token_end) == (token_start, token_end)
            ),
            None,
        )
        if candidate is None:
            raise ValueError("selected token range is not a validated model suggestion")
        return result, candidate

    class ReviewHandler(BaseHTTPRequestHandler):
        server_version = "FlashcardReviewer/1"

        def do_GET(self) -> None:
            request_url = urlsplit(self.path)
            path = unquote(request_url.path)
            if path.startswith(("/api/", "/media/")) and not self._authorized(request_url.query):
                self._error(HTTPStatus.FORBIDDEN, "Invalid reviewer session token")
                return
            try:
                if path == "/api/project":
                    self._json(HTTPStatus.OK, api_payload())
                elif path.startswith("/media/"):
                    self._media(project.resolve_media(path.removeprefix("/media/")))
                else:
                    self._static(path)
            except FileNotFoundError as exc:
                self._error(HTTPStatus.NOT_FOUND, str(exc))
            except (ValueError, OSError) as exc:
                self._error(HTTPStatus.BAD_REQUEST, str(exc))

        def do_POST(self) -> None:
            path = unquote(urlsplit(self.path).path)
            if not self._authorized(""):
                self._error(HTTPStatus.FORBIDDEN, "Invalid reviewer session token")
                return
            if not self._same_origin():
                self._error(HTTPStatus.FORBIDDEN, "Cross-origin requests are not allowed")
                return
            try:
                if path == "/api/cards":
                    request = self._request_json()
                    card = project.upsert_card(request, verified_suggestion(request))
                    self._json(HTTPStatus.OK, {"card_id": card.id, **api_payload()})
                elif path == "/api/analyze":
                    if analysis_service is None:
                        self._error(HTTPStatus.SERVICE_UNAVAILABLE, "Local model analysis is disabled")
                        return
                    request = self._request_json()
                    utterance_id = request.get("utterance_id")
                    if not isinstance(utterance_id, str) or not utterance_id.strip():
                        raise ValueError("utterance_id must be a nonempty string")
                    refresh = request.get("refresh", False)
                    if type(refresh) is not bool:
                        raise ValueError("refresh must be a boolean")
                    result = analysis_service.analyze(
                        project.get_utterance(utterance_id),
                        project.manifest.language,
                        translation_language,
                        refresh,
                    )
                    self._json(HTTPStatus.OK, {"analysis": result.to_dict()})
                elif path == "/api/export":
                    output = project.export()
                    self._json(
                        HTTPStatus.OK,
                        {"output_path": str(output), "card_count": len(project.manifest.cards)},
                    )
                else:
                    self._error(HTTPStatus.NOT_FOUND, "Unknown endpoint")
            except ModelUnavailableError as exc:
                self._error(HTTPStatus.SERVICE_UNAVAILABLE, str(exc))
            except AnalysisValidationError as exc:
                self._error(HTTPStatus.BAD_GATEWAY, str(exc))
            except AnalysisError as exc:
                self._error(HTTPStatus.BAD_GATEWAY, str(exc))
            except (ValueError, FileNotFoundError) as exc:
                self._error(HTTPStatus.BAD_REQUEST, str(exc))
            except Exception as exc:
                self._error(HTTPStatus.INTERNAL_SERVER_ERROR, str(exc))

        def do_DELETE(self) -> None:
            path = unquote(urlsplit(self.path).path)
            if not self._authorized(""):
                self._error(HTTPStatus.FORBIDDEN, "Invalid reviewer session token")
                return
            if not self._same_origin():
                self._error(HTTPStatus.FORBIDDEN, "Cross-origin requests are not allowed")
                return
            match = re.fullmatch(r"/api/cards/([0-9a-f]{64})", path)
            if match is None:
                self._error(HTTPStatus.NOT_FOUND, "Unknown endpoint")
                return
            try:
                project.delete_card(match.group(1))
                self._json(HTTPStatus.OK, api_payload())
            except ValueError as exc:
                self._error(HTTPStatus.BAD_REQUEST, str(exc))

        def _request_json(self) -> dict[str, Any]:
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError as exc:
                raise ValueError("invalid Content-Length") from exc
            if length <= 0 or length > MAX_REQUEST_BYTES:
                raise ValueError("request body is empty or too large")
            try:
                payload = json.loads(self.rfile.read(length))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ValueError("request body is not valid JSON") from exc
            if not isinstance(payload, dict):
                raise ValueError("request body must be an object")
            return payload

        def _same_origin(self) -> bool:
            origin = self.headers.get("Origin")
            if origin is None:
                return True
            parsed = urlsplit(origin)
            return parsed.scheme in {"http", "https"} and parsed.netloc == self.headers.get("Host")

        def _authorized(self, query: str) -> bool:
            candidate = self.headers.get("X-Flashcards-Token")
            if candidate is None and query:
                candidate = parse_qs(query).get("token", [None])[0]
            return isinstance(candidate, str) and hmac.compare_digest(candidate, access_token)

        def _static(self, request_path: str) -> None:
            relative = "index.html" if request_path == "/" else request_path.lstrip("/")
            candidate = (static_dir / relative).resolve()
            if static_dir not in candidate.parents or not candidate.is_file():
                self._error(HTTPStatus.NOT_FOUND, "Not found")
                return
            content_type = mimetypes.guess_type(candidate.name)[0] or "application/octet-stream"
            body = candidate.read_bytes()
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", f"{content_type}; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header(
                "Content-Security-Policy",
                "default-src 'self'; script-src 'self'; style-src 'self'; "
                "connect-src 'self'; media-src 'self'; object-src 'none'",
            )
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)

        def _media(self, media_path: Path) -> None:
            size = media_path.stat().st_size
            start = 0
            end = size - 1
            status = HTTPStatus.OK
            range_header = self.headers.get("Range")
            if range_header:
                if "," in range_header:
                    range_header = None
            if range_header:
                match = re.fullmatch(r"bytes=(\d*)-(\d*)", range_header.strip())
                if match is None or (not match.group(1) and not match.group(2)):
                    self._invalid_range(size)
                    return
                if match.group(1):
                    start = int(match.group(1))
                    end = int(match.group(2)) if match.group(2) else end
                else:
                    suffix_length = int(match.group(2))
                    start = max(0, size - suffix_length)
                if start >= size or end < start:
                    self._invalid_range(size)
                    return
                end = min(end, size - 1)
                status = HTTPStatus.PARTIAL_CONTENT

            content_type = mimetypes.guess_type(media_path.name)[0] or "application/octet-stream"
            content_length = end - start + 1
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Length", str(content_length))
            if status is HTTPStatus.PARTIAL_CONTENT:
                self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            self.end_headers()
            try:
                with media_path.open("rb") as media_file:
                    media_file.seek(start)
                    remaining = content_length
                    while remaining:
                        chunk = media_file.read(min(64 * 1024, remaining))
                        if not chunk:
                            break
                        self.wfile.write(chunk)
                        remaining -= len(chunk)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def _invalid_range(self, size: int) -> None:
            self.send_response(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
            self.send_header("Content-Range", f"bytes */{size}")
            self.send_header("Content-Length", "0")
            self.end_headers()

        def _json(self, status: HTTPStatus, payload: object) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)

        def _error(self, status: HTTPStatus, message: str) -> None:
            self._json(status, {"error": message})

    return ReviewHandler


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Review timed transcripts and create cloze cards.")
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--out", type=Path, help="Output .apkg path")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--model", default=DEFAULT_OLLAMA_MODEL)
    parser.add_argument("--ollama-url", default=DEFAULT_OLLAMA_URL)
    parser.add_argument("--translation-language", default="English")
    parser.add_argument("--no-analysis", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    if not 1 <= args.port <= 65_535:
        raise SystemExit("--port must be between 1 and 65535")
    project = ReviewProject(args.manifest, args.out)
    analysis_service = None
    if not args.no_analysis:
        analysis_service = AnalysisService(
            OllamaAnalyzer(model=args.model, base_url=args.ollama_url),
            AnalysisCache(project.manifest_path.with_suffix(".analysis-cache.json")),
        )
    static_dir = Path(__file__).resolve().parent / "reviewer_static"
    if not static_dir.is_dir():
        raise SystemExit(f"Reviewer assets not found: {static_dir}")
    access_token = secrets.token_urlsafe(32)
    server = ThreadingHTTPServer(
        (args.host, args.port),
        _handler(
            project,
            static_dir,
            access_token,
            analysis_service,
            args.translation_language,
        ),
    )
    url = f"http://{args.host}:{args.port}/?token={quote(access_token)}"
    print(f"Reviewing {project.manifest.title!r} at {url}")
    print(f"Cards are saved to {project.manifest_path}")
    if analysis_service is not None:
        model_status = analysis_service.status()
        if model_status["available"]:
            print(f"Learning-unit analysis: {model_status['model']}")
        else:
            print(f"Learning-unit analysis unavailable: {model_status['error']}")
    if not args.no_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping reviewer")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
