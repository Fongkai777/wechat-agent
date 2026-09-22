import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.evaluate_retrieval import evaluate
from scripts.privacy_check import findings


class PublicationTests(unittest.TestCase):
    def test_private_paths_and_identifiers_are_flagged(self):
        self.assertIn("private-data-path", findings("web_cache/config.json", b"{}"))
        self.assertIn("provider-key", findings("code.py", b"sk-" + b"x" * 40))
        self.assertIn("personal-chatroom-id", findings("test.py", b"9" * 11 + b"@chatroom"))
        self.assertEqual(findings("test.py", b"1234567890@chatroom"), [])
        self.assertEqual(findings("examples/chats.json", b"demo_group@chatroom"), [])

    def test_offline_evaluation_is_reproducible_without_network(self):
        with tempfile.TemporaryDirectory() as directory, contextlib.redirect_stdout(io.StringIO()):
            with patch("urllib.request.urlopen", side_effect=AssertionError("Offline benchmark made a network request")):
                report = evaluate(Path(directory))
            self.assertEqual(report["incremental_inserted"], 10)
            self.assertEqual(report["answerable_cases"], 15)
            self.assertEqual(len(report["results"]), 16)
            self.assertEqual(report["conversation_hits"], 15)
            self.assertEqual(report["all_evidence_hits"], 14)
            self.assertTrue(all(item["answer"] is None for item in report["results"]))
            self.assertTrue((Path(directory) / "REPORT.md").is_file())


if __name__ == "__main__":
    unittest.main()
