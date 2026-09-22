import base64
import io
import sqlite3
import tempfile
import unittest
import urllib.parse
from pathlib import Path
from unittest.mock import Mock, patch

from wechat_agent import web


PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=")


class AvatarTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.state = web.AppState(
            db_storage=root / "source", decrypted=root / "decrypted",
            keys=root / "keys.json", media_root=root / "msg",
            voice_cache=root / "voice.json", llm_config=root / "config.json",
            qa_store=root / "qa.json", qa_index_cache=root / "qa.pkl",
            qa_search_db=root / "qa.db",
        )
        self.path = self.state.decrypted / "head_image" / "head_image.db"
        self.path.parent.mkdir(parents=True)
        with sqlite3.connect(self.path) as conn:
            conn.execute("CREATE TABLE head_image(username TEXT PRIMARY KEY, md5 TEXT, image_buffer BLOB, update_time INTEGER)")
            conn.executemany("INSERT INTO head_image VALUES (?, ?, ?, ?)", [
                ("friend", "version1", PNG, 1), ("empty", "", b"", 1),
                ("bad", "version2", b"<svg onload='bad()'/>", 1),
            ])
        self.state.avatar_versions = web.load_avatar_versions(self.state)

    def test_local_image_and_versioned_url(self):
        self.assertEqual(web.read_avatar(self.state, "friend"), (PNG, "image/png"))
        url = urllib.parse.urlparse(web.avatar_url(self.state, "friend"))
        self.assertEqual(url.path, "/avatar")
        self.assertEqual(urllib.parse.parse_qs(url.query), {"username": ["friend"], "v": ["version1"]})

    def test_empty_unknown_and_unsafe_content_fall_back(self):
        for name in ["empty", "unknown", "../../keys.json", "' OR 1=1 --", "bad"]:
            self.assertIsNone(web.read_avatar(self.state, name))
        self.assertEqual(web.avatar_url(self.state, "empty"), "")
        self.assertEqual(web.avatar_url(self.state, "unknown"), "")

    def test_changed_avatar_invalidates_url(self):
        before = web.avatar_url(self.state, "friend")
        with sqlite3.connect(self.path) as conn:
            conn.execute("UPDATE head_image SET md5='changed' WHERE username='friend'")
        self.state.avatar_versions = web.load_avatar_versions(self.state)
        self.assertNotEqual(before, web.avatar_url(self.state, "friend"))

    def test_missing_database_does_not_break_chat_loading(self):
        self.path.unlink()
        self.assertEqual(web.load_avatar_versions(self.state), {})
        self.assertIsNone(web.read_avatar(self.state, "friend"))

    def test_endpoint_returns_image_and_supports_conditional_cache(self):
        with patch.object(web.threading.Thread, "start"):
            handler_class = web.make_handler(self.state)
        handler = handler_class.__new__(handler_class)
        handler.headers = {}
        handler.wfile = io.BytesIO()
        for name in ("send_response", "send_header", "end_headers", "send_error"):
            setattr(handler, name, Mock())
        handler.handle_avatar({"username": ["friend"]})
        handler.send_response.assert_called_once_with(200)
        self.assertEqual(handler.wfile.getvalue(), PNG)
        headers = dict(call.args for call in handler.send_header.call_args_list)
        self.assertEqual(headers["Content-Type"], "image/png")
        self.assertIn("private", headers["Cache-Control"])
        handler.headers = {"If-None-Match": headers["ETag"]}
        handler.wfile = io.BytesIO()
        handler.handle_avatar({"username": ["friend"]})
        handler.send_response.assert_called_with(304)
        self.assertEqual(handler.wfile.getvalue(), b"")
        handler.handle_avatar({"username": ["missing"]})
        handler.send_error.assert_called_with(404, "No local avatar")


if __name__ == "__main__":
    unittest.main()
