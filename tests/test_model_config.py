import json
from pathlib import Path
import tempfile
import unittest

from wechat_agent import web


class ModelConfigTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "config.json"

    def test_legacy_task_copies_qa_without_sharing_mutable_profile(self):
        self.path.write_text(json.dumps({"qa": {
            "base_url": "https://example.test/v1", "model": "custom-chat",
            "api_key": "fixture-old-secret", "temperature": 0.3,
        }}))
        config = web.load_llm_config(self.path)
        self.assertEqual(config["task"]["model"], "custom-chat")
        self.assertEqual(config["task"]["api_key"], "fixture-old-secret")
        self.assertNotIn("temperature", config["task"])
        self.assertNotIn("max_context_messages", config["task"])
        config["qa"]["model"] = "changed"
        self.assertEqual(config["task"]["model"], "custom-chat")

    def test_saving_qa_materializes_legacy_task_before_qa_changes(self):
        self.path.write_text(json.dumps({"qa": {"model": "old-model", "api_key": "fixture-secret"}}))
        web.save_llm_config(self.path, {"qa": {"model": "new-model", "api_key": "fixture-new"}})
        config = web.load_llm_config(self.path)
        self.assertEqual(config["qa"]["model"], "new-model")
        self.assertEqual(config["task"]["model"], "old-model")
        self.assertEqual(config["task"]["api_key"], "fixture-secret")
        self.assertIn("task", json.loads(self.path.read_text()))

    def test_task_round_trip_secret_preservation_and_redaction(self):
        web.save_llm_config(self.path, {"task": {
            "base_url": "https://task.test/v1/", "model": "task-model",
            "api_key": "fixture-task-secret", "api_key_env": "TASK_API_KEY", "temperature": 0.2,
        }})
        config = web.save_llm_config(self.path, {"task": {"api_key": "", "model": "updated-task"}})
        self.assertEqual(config["task"]["api_key"], "fixture-task-secret")
        self.assertEqual(config["task"]["base_url"], "https://task.test/v1")
        self.assertEqual(config["task"]["api_key_env"], "TASK_API_KEY")
        self.assertNotEqual(config["qa"]["model"], "updated-task")
        safe = web.safe_llm_config(config)
        self.assertNotIn("fixture-task-secret", json.dumps(safe))
        self.assertTrue(safe["task"]["effective_api_key_set"])
        self.assertFalse(safe["task"]["inherits_qa"])
        cleared = web.save_llm_config(self.path, {"task": {"clear_api_key": True}})
        self.assertEqual(cleared["task"]["api_key"], "")

    def test_fresh_config_has_five_profiles(self):
        self.assertEqual(set(web.load_llm_config(self.path)), {"voice", "qa", "task", "embedding", "rerank"})


if __name__ == "__main__":
    unittest.main()
