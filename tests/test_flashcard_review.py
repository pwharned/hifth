from __future__ import annotations

import sys
import json
import stat
import threading
import tempfile
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from flashcards import BaseToken, MediaSource, ProjectManifest, TextSpan, Utterance  # noqa: E402
from flashcards.review import ReviewProject, _handler  # noqa: E402


class ReviewProjectTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        text = "fish and fish"
        manifest = ProjectManifest(
            "project",
            "Review test",
            "en",
            (MediaSource("media", "movie.mp4", "en", duration_ms=2_000),),
            (
                Utterance(
                    "utterance",
                    "media",
                    text,
                    100,
                    1_000,
                    (
                        BaseToken("fish", TextSpan(0, 4)),
                        BaseToken("and", TextSpan(5, 8)),
                        BaseToken("fish", TextSpan(9, 13)),
                    ),
                ),
            ),
            (),
            (),
        )
        self.manifest_path = self.root / "project.json"
        self.manifest_path.write_text(manifest.to_json(indent=2), encoding="utf-8")
        self.project = ReviewProject(self.manifest_path)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_upsert_uses_exact_token_occurrence_and_persists(self) -> None:
        card = self.project.upsert_card(
            {
                "utterance_id": "utterance",
                "token_start": 2,
                "token_end": 3,
                "kind": "word",
                "target_gloss": "a fish",
                "sentence_translation": "fish and fish",
                "analysis": "Second occurrence",
                "tags": ["example"],
            }
        )
        self.assertEqual(card.target_text(), "fish")
        self.assertEqual(card.target_span, TextSpan(9, 13))
        self.assertEqual(card.cloze_text(), "fish and {{c1::fish}}")
        reloaded = ReviewProject(self.manifest_path)
        self.assertEqual(reloaded.manifest.cards, (card,))
        self.assertEqual(reloaded.manifest.learning_units[0].token_start, 2)

        updated = self.project.upsert_card(
            {
                "utterance_id": "utterance",
                "token_start": 2,
                "token_end": 3,
                "kind": "word",
                "target_gloss": "fish (updated)",
                "tags": [],
            }
        )
        self.assertEqual(updated.id, card.id)
        self.assertEqual(len(self.project.manifest.cards), 1)
        self.assertEqual(self.project.manifest.cards[0].target_gloss, "fish (updated)")

    def test_delete_removes_card_and_review_learning_unit(self) -> None:
        card = self.project.upsert_card(
            {
                "utterance_id": "utterance",
                "token_start": 0,
                "token_end": 1,
                "tags": [],
            }
        )
        self.project.delete_card(card.id)
        self.assertEqual(self.project.manifest.cards, ())
        self.assertEqual(self.project.manifest.learning_units, ())

    def test_invalid_token_range_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            self.project.upsert_card(
                {
                    "utterance_id": "utterance",
                    "token_start": 0,
                    "token_end": 9,
                    "tags": [],
                }
            )

    def test_local_http_api_and_media_ranges(self) -> None:
        media_path = self.root / "movie.mp4"
        media_path.write_bytes(b"0123456789")
        handler_type = _handler(
            self.project,
            Path(__file__).resolve().parents[1] / "src" / "flashcards" / "reviewer_static",
            "test-token",
        )
        handler_type.log_message = lambda *_args: None
        server = ThreadingHTTPServer(
            ("127.0.0.1", 0),
            handler_type,
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base_url = f"http://127.0.0.1:{server.server_port}"
        try:
            project_request = urllib.request.Request(
                f"{base_url}/api/project",
                headers={"X-Flashcards-Token": "test-token"},
            )
            with urllib.request.urlopen(project_request) as response:
                payload = json.load(response)
                self.assertEqual(payload["project"]["id"], "project")

            media_request = urllib.request.Request(
                f"{base_url}/media/media?token=test-token", headers={"Range": "bytes=2-5"}
            )
            with urllib.request.urlopen(media_request) as response:
                self.assertEqual(response.status, 206)
                self.assertEqual(response.read(), b"2345")

            body = json.dumps(
                {
                    "utterance_id": "utterance",
                    "token_start": 0,
                    "token_end": 1,
                    "kind": "word",
                    "tags": [],
                }
            ).encode()
            request = urllib.request.Request(
                f"{base_url}/api/cards",
                data=body,
                headers={
                    "Content-Type": "application/json",
                    "Origin": base_url,
                    "X-Flashcards-Token": "test-token",
                },
                method="POST",
            )
            with urllib.request.urlopen(request) as response:
                self.assertEqual(json.load(response)["project"]["cards"][0]["target_span"], {
                    "start_char": 0,
                    "end_char": 4,
                })

            bad_origin_request = urllib.request.Request(
                f"{base_url}/api/cards",
                data=body,
                headers={
                    "Content-Type": "application/json",
                    "Origin": "https://example.test",
                    "X-Flashcards-Token": "test-token",
                },
                method="POST",
            )
            with self.assertRaises(urllib.error.HTTPError) as error:
                urllib.request.urlopen(bad_origin_request)
            self.assertEqual(error.exception.code, 403)
            error.exception.close()

            unauthorized = urllib.request.Request(f"{base_url}/api/project")
            with self.assertRaises(urllib.error.HTTPError) as error:
                urllib.request.urlopen(unauthorized)
            self.assertEqual(error.exception.code, 403)
            error.exception.close()
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_save_preserves_manifest_permissions(self) -> None:
        self.manifest_path.chmod(0o600)
        self.project.upsert_card(
            {
                "utterance_id": "utterance",
                "token_start": 0,
                "token_end": 1,
                "tags": [],
            }
        )
        self.assertEqual(stat.S_IMODE(self.manifest_path.stat().st_mode), 0o600)


if __name__ == "__main__":
    unittest.main()
