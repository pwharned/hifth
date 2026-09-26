from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from flashcards import MediaArtifact, MediaSource  # noqa: E402


class MediaArtifactContractTests(unittest.TestCase):
    def test_shared_golden_fixture_decodes_and_is_stateless(self) -> None:
        fixture = (
            ROOT
            / "media-reviewer"
            / "shared"
            / "src"
            / "test"
            / "resources"
            / "media-artifact-v1.json"
        )
        payload = fixture.read_text(encoding="utf-8")
        artifact = MediaArtifact.from_json(payload)

        self.assertEqual(artifact.utterances[0].text, "Tôi là học sinh.")
        self.assertNotIn("cards", json.loads(payload))
        self.assertNotIn("learning_units", json.loads(payload))
        self.assertEqual(MediaArtifact.from_json(artifact.to_json()), artifact)

    def test_stateful_manifest_fields_are_rejected(self) -> None:
        artifact = MediaArtifact(
            id="artifact",
            title="Title",
            language="en",
            media=(MediaSource("media", "lesson.mp3", "en"),),
            utterances=(),
        )
        payload = artifact.to_dict()
        payload["cards"] = []
        with self.assertRaisesRegex(ValueError, "unsupported field.*cards"):
            MediaArtifact.from_dict(payload)

    def test_nullable_fields_may_be_omitted(self) -> None:
        fixture = (
            ROOT
            / "media-reviewer"
            / "shared"
            / "src"
            / "test"
            / "resources"
            / "media-artifact-v1.json"
        )
        payload = json.loads(fixture.read_text(encoding="utf-8"))
        for key in ("title", "source_url", "checksum_sha256", "duration_ms"):
            payload["media"][0].pop(key)
        for key in ("start_ms", "end_ms", "confidence"):
            payload["utterances"][0]["tokens"][0].pop(key)
        payload["utterances"][0].pop("translation")
        payload["utterances"][0].pop("confidence")

        artifact = MediaArtifact.from_dict(payload)
        self.assertIsNone(artifact.media[0].title)
        self.assertIsNone(artifact.utterances[0].tokens[0].start_ms)


if __name__ == "__main__":
    unittest.main()
