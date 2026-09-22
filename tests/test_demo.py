import tempfile
import unittest
from pathlib import Path
from wechat_agent import demo, web


class DemoTests(unittest.TestCase):
    def test_isolated_idempotent_import_and_incremental_index(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.assertEqual(demo.initialize(root), 30)
            self.assertEqual(demo.initialize(root), 0)
            state = demo.demo_state(root)
            self.assertEqual(len(state.chats), 6)
            self.assertEqual(sum(c["total_messages"] for c in state.chats), 30)
            web.update_qa_search_db_incremental(state)
            self.assertEqual(demo.import_messages(root, demo.fixture()["increment"]), 10)
            state = demo.demo_state(root)
            result = web.update_qa_search_db_incremental(state)
            self.assertEqual(result["update_mode"], "incremental")
            self.assertEqual(result["inserted"], 10)
            self.assertEqual(web.update_qa_search_db_incremental(state)["inserted"], 0)
            self.assertEqual(demo.import_messages(root, demo.fixture()["increment"]), 0)

    def test_rejects_non_demo_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "private.txt").write_text("do not touch")
            with self.assertRaises(ValueError):
                demo.initialize(root)
            with self.assertRaises(ValueError):
                demo.demo_state(root)
            self.assertEqual((root / "private.txt").read_text(), "do not touch")


if __name__ == "__main__":
    unittest.main()
