from __future__ import annotations

import json
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from flashcards.analysis import (  # noqa: E402
    ANALYSIS_CACHE_VERSION,
    ANALYSIS_PROMPT_VERSION,
    DEFAULT_OLLAMA_MODEL,
    AnalysisCache,
    AnalysisComponent,
    AnalysisError,
    AnalysisService,
    AnalysisValidationError,
    LearningUnitSuggestion,
    OllamaAnalyzer,
    SentenceAnalysis,
)
from flashcards.models import LearningUnitKind, Utterance  # noqa: E402
from flashcards.subtitles import tokenize_base_units  # noqa: E402


MODEL_DIGEST = "sha256:" + "a" * 64


def make_utterance() -> Utterance:
    text = "Tôi là học sinh."
    return Utterance(
        id="utterance-1",
        media_id="media-1",
        text=text,
        start_ms=0,
        end_ms=1_000,
        tokens=tokenize_base_units(text),
    )


def valid_model_output() -> dict[str, Any]:
    return {
        "sentence_translation": "I am a student.",
        "candidates": [
            {
                "token_start": 2,
                "token_end": 4,
                "surface": "học sinh",
                "contextual_gloss": "student",
                "kind": "compound",
                "recommended_as_unit": True,
                "reason": "A useful lexical compound.",
                "components": [
                    {"surface": "học", "gloss": "study"},
                    {"surface": "sinh", "gloss": "person"},
                ],
            }
        ],
    }


class FakeOllamaAnalyzer(OllamaAnalyzer):
    def __init__(
        self,
        outputs: list[dict[str, Any] | str] | None = None,
        *,
        models: list[dict[str, Any]] | None = None,
        version: str = "0.32.5",
        model: str = DEFAULT_OLLAMA_MODEL,
    ) -> None:
        super().__init__(model=model)
        self.outputs = list(outputs or [])
        self.version = version
        self.models = (
            [{"name": self.model, "digest": MODEL_DIGEST, "size": 5_000}]
            if models is None
            else models
        )
        self.tag_calls = 0
        self.version_calls = 0
        self.chat_payloads: list[dict[str, Any]] = []

    def _request_json(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
    ) -> Any:
        if (method, path) == ("GET", "/api/version"):
            self.version_calls += 1
            return {"version": self.version}
        if (method, path) == ("GET", "/api/tags"):
            self.tag_calls += 1
            return {"models": self.models}
        if (method, path) == ("POST", "/api/chat"):
            assert payload is not None
            self.chat_payloads.append(payload)
            output = self.outputs.pop(0)
            content = output if isinstance(output, str) else json.dumps(output, ensure_ascii=False)
            return {"message": {"role": "assistant", "content": content}}
        raise AssertionError(f"unexpected request: {method} {path}")


class FlashcardAnalysisTests(unittest.TestCase):
    def test_success_uses_exact_compound_range_and_required_ollama_options(self) -> None:
        analyzer = FakeOllamaAnalyzer([valid_model_output()])
        analysis = analyzer.analyze(make_utterance(), "Vietnamese")

        self.assertEqual(analysis.sentence_translation, "I am a student.")
        self.assertEqual(
            (analysis.candidates[0].token_start, analysis.candidates[0].token_end),
            (2, 4),
        )
        self.assertEqual(analysis.candidates[0].surface, "học sinh")
        self.assertIs(analysis.candidates[0].kind, LearningUnitKind.COMPOUND)
        payload = analyzer.chat_payloads[0]
        self.assertFalse(payload["stream"])
        self.assertFalse(payload["think"])
        self.assertIsInstance(payload["format"], dict)
        self.assertEqual(payload["keep_alive"], "10m")
        self.assertEqual(
            payload["options"],
            {"temperature": 0, "num_ctx": 4096, "num_predict": 768},
        )
        prompt = payload["messages"][1]["content"]
        self.assertIn('Source language: "Vietnamese"', prompt)
        self.assertIn('Desired translation language: "English"', prompt)
        self.assertIn('Exact sentence: "Tôi là học sinh."', prompt)
        self.assertIn('2. {"text":"học","start_char":7,"end_char":10}', prompt)

    def test_cache_hit_avoids_a_second_chat_request(self) -> None:
        analyzer = FakeOllamaAnalyzer([valid_model_output()])
        with tempfile.TemporaryDirectory() as temporary_directory:
            service = AnalysisService(
                analyzer,
                AnalysisCache(Path(temporary_directory) / "analysis.json"),
            )
            first = service.analyze(make_utterance(), "Vietnamese")
            second = service.analyze(make_utterance(), "Vietnamese")

        self.assertEqual(second, first)
        self.assertEqual(len(analyzer.chat_payloads), 1)
        self.assertEqual(analyzer.tag_calls, 3)

    def test_invalid_surface_gets_exactly_one_corrective_retry(self) -> None:
        invalid = valid_model_output()
        invalid["candidates"][0]["token_end"] = 3
        analyzer = FakeOllamaAnalyzer([invalid, valid_model_output()])

        analysis = analyzer.analyze(make_utterance(), "Vietnamese")

        self.assertEqual(analysis.candidates[0].surface, "học sinh")
        self.assertEqual(len(analyzer.chat_payloads), 2)
        correction = analyzer.chat_payloads[1]["messages"][-1]["content"]
        self.assertIn("Validation error:", correction)
        self.assertIn("Previous output:", correction)
        self.assertIn("token_end", correction)

    def test_second_invalid_response_raises_validation_error(self) -> None:
        invalid = valid_model_output()
        invalid["candidates"][0]["token_end"] = 3
        analyzer = FakeOllamaAnalyzer([invalid, invalid])

        with self.assertRaisesRegex(AnalysisValidationError, "corrective retry"):
            analyzer.analyze(make_utterance(), "Vietnamese")

        self.assertEqual(len(analyzer.chat_payloads), 2)

    def test_missing_model_status_has_pull_command_and_is_rechecked(self) -> None:
        analyzer = FakeOllamaAnalyzer(models=[])
        with tempfile.TemporaryDirectory() as temporary_directory:
            service = AnalysisService(
                analyzer,
                AnalysisCache(Path(temporary_directory) / "analysis.json"),
            )
            missing = service.status()
            analyzer.models.append(
                {"name": DEFAULT_OLLAMA_MODEL, "digest": MODEL_DIGEST, "size": 5_000}
            )
            available = service.status()

        self.assertEqual(
            missing,
            {
                "enabled": True,
                "available": False,
                "model": DEFAULT_OLLAMA_MODEL,
                "error": (
                    f"Ollama model {DEFAULT_OLLAMA_MODEL!r} is not installed; "
                    f"run `ollama pull {DEFAULT_OLLAMA_MODEL}`"
                ),
                "pull_command": f"ollama pull {DEFAULT_OLLAMA_MODEL}",
            },
        )
        self.assertTrue(available["available"])
        self.assertEqual(available["digest"], MODEL_DIGEST)
        self.assertEqual(analyzer.tag_calls, 2)

    def test_corrupt_cache_is_unchanged_until_successful_put_then_recovers(self) -> None:
        analyzer = FakeOllamaAnalyzer([valid_model_output()])
        with tempfile.TemporaryDirectory() as temporary_directory:
            cache_path = Path(temporary_directory) / "analysis.json"
            cache_path.write_text("not JSON\n", encoding="utf-8")
            cache = AnalysisCache(cache_path)
            self.assertIsNone(cache.get("missing"))
            self.assertEqual(cache_path.read_text(encoding="utf-8"), "not JSON\n")

            AnalysisService(analyzer, cache).analyze(make_utterance(), "Vietnamese")
            recovered = json.loads(cache_path.read_text(encoding="utf-8"))

        self.assertEqual(recovered["schema_version"], ANALYSIS_CACHE_VERSION)
        self.assertEqual(len(recovered["entries"]), 1)

    def test_cache_permissions_are_private_for_new_file_and_preserved(self) -> None:
        analysis = self._sample_analysis()
        with tempfile.TemporaryDirectory() as temporary_directory:
            cache_path = Path(temporary_directory) / "analysis.json"
            cache = AnalysisCache(cache_path)
            cache.put("first", analysis)
            self.assertEqual(stat.S_IMODE(cache_path.stat().st_mode), 0o600)

            os.chmod(cache_path, 0o640)
            cache.put("second", analysis)
            self.assertEqual(stat.S_IMODE(cache_path.stat().st_mode), 0o640)

    def test_non_loopback_ollama_url_is_rejected(self) -> None:
        for url in (
            "http://example.com:11434",
            "https://127.0.0.1:11434",
            "http://127.0.0.1:11434/api",
        ):
            with self.subTest(url=url), self.assertRaises(ValueError):
                OllamaAnalyzer(base_url=url)

    def test_ollama_version_must_support_structured_qwen_output(self) -> None:
        analyzer = FakeOllamaAnalyzer(version="0.31.1")
        with self.assertRaisesRegex(AnalysisError, "upgrade to Ollama >= 0.32.0"):
            analyzer.model_info()

    def test_untagged_model_name_matches_installed_latest_alias(self) -> None:
        analyzer = FakeOllamaAnalyzer(
            model="custom-model",
            models=[{"name": "custom-model:latest", "digest": MODEL_DIGEST, "size": 5_000}],
        )
        self.assertEqual(analyzer.model_info().name, "custom-model:latest")

    def test_failed_refresh_does_not_reuse_stale_model_metadata(self) -> None:
        analyzer = FakeOllamaAnalyzer()
        analyzer.model_info()
        analyzer.models.clear()
        with self.assertRaisesRegex(AnalysisError, "not installed"):
            analyzer.model_info(refresh=True)
        with self.assertRaisesRegex(AnalysisError, "not installed"):
            analyzer.model_info()

    def test_cloud_backed_ollama_model_is_rejected(self) -> None:
        analyzer = FakeOllamaAnalyzer(
            models=[
                {
                    "name": DEFAULT_OLLAMA_MODEL,
                    "digest": MODEL_DIGEST,
                    "size": 0,
                    "remote_model": "qwen3.5:9b",
                    "remote_host": "https://ollama.com",
                }
            ]
        )
        with self.assertRaisesRegex(AnalysisError, "cloud-backed"):
            analyzer.model_info()

    def test_loopback_requests_use_an_opener_with_proxies_disabled(self) -> None:
        analyzer = OllamaAnalyzer()
        self.assertEqual(analyzer._proxy_handler.proxies, {})

    def test_analysis_round_trip_is_deterministic(self) -> None:
        analysis = self._sample_analysis()
        serialized = analysis.to_dict()

        self.assertEqual(SentenceAnalysis.from_dict(serialized), analysis)
        self.assertEqual(SentenceAnalysis.from_dict(serialized).to_dict(), serialized)
        self.assertEqual(
            json.dumps(serialized, ensure_ascii=False, separators=(",", ":")),
            json.dumps(analysis.to_dict(), ensure_ascii=False, separators=(",", ":")),
        )

    @staticmethod
    def _sample_analysis() -> SentenceAnalysis:
        return SentenceAnalysis(
            sentence_translation="I am a student.",
            candidates=(
                LearningUnitSuggestion(
                    token_start=2,
                    token_end=4,
                    surface="học sinh",
                    contextual_gloss="student",
                    kind=LearningUnitKind.COMPOUND,
                    recommended_as_unit=True,
                    reason="A useful lexical compound.",
                    components=(
                        AnalysisComponent("học", "study"),
                        AnalysisComponent("sinh", "person"),
                    ),
                ),
            ),
            model=DEFAULT_OLLAMA_MODEL,
            model_digest=MODEL_DIGEST,
            prompt_version=ANALYSIS_PROMPT_VERSION,
        )


if __name__ == "__main__":
    unittest.main()
