import json
import os
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from wechat_agent import web


class SourceSyncTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.state = web.AppState(
            db_storage=root / "source",
            decrypted=root / "decrypted",
            keys=root / "keys.json",
            media_root=root / "msg",
            voice_cache=root / "voice.json",
            llm_config=root / "config.json",
            qa_store=root / "qa.json",
            qa_index_cache=root / "qa.pkl",
            qa_search_db=root / "qa.db",
        )
        self.src = self.state.db_storage / "message" / "message_0.db"
        self.src.parent.mkdir(parents=True)
        self.src.write_bytes(b"encrypted database fixture")
        self.wal = self.src.with_name(self.src.name + "-wal")
        self.wal.write_bytes(b"first WAL fixture")
        self.dst = self.state.decrypted / "message" / "message_0.db"
        self.addCleanup(patch.stopall)
        patch.object(web, "load_keys", return_value={}).start()
        patch.object(web, "key_for_rel", return_value="00" * 32).start()
        self.decode = patch.object(web, "decrypt_db", side_effect=self.fake_decode).start()

    def fake_decode(self, src, dst, key):
        with sqlite3.connect(dst) as conn:
            conn.execute("CREATE TABLE messages(content TEXT)")
            conn.execute("INSERT INTO messages VALUES (?)", (src.with_name(src.name + "-wal").read_text(),))
        return True

    def test_unchanged_sources_are_not_decrypted_again(self):
        first = web.ensure_decrypted(self.state)
        second = web.ensure_decrypted(self.state)
        self.assertEqual((first["updated"], second["updated"]), (1, 0))
        self.assertEqual(self.decode.call_count, 1)

    def test_wal_only_update_is_imported_even_when_main_file_is_older(self):
        web.ensure_decrypted(self.state)
        main_mtime = self.src.stat().st_mtime_ns
        wal_mtime = self.wal.stat().st_mtime_ns
        self.wal.write_bytes(b"newer WAL fixture")
        os.utime(self.wal, ns=(wal_mtime + 1000000, wal_mtime + 1000000))
        result = web.ensure_decrypted(self.state)
        self.assertEqual(result["updated"], 1)
        self.assertEqual(self.src.stat().st_mtime_ns, main_mtime)
        with sqlite3.connect(self.dst) as conn:
            self.assertEqual(conn.execute("SELECT content FROM messages").fetchone()[0], "newer WAL fixture")

    def test_inconsistent_copy_keeps_last_good_database(self):
        web.ensure_decrypted(self.state)
        original = self.dst.read_bytes()
        with patch.object(web, "source_db_fingerprint", side_effect=[{"revision": 1}, {"revision": 2}]):
            result = web.ensure_decrypted(self.state)
        self.assertEqual(result["failed"], 1)
        self.assertEqual(self.dst.read_bytes(), original)
        self.assertEqual(self.decode.call_count, 1)

    def test_invalid_decryption_keeps_last_good_database(self):
        web.ensure_decrypted(self.state)
        original = self.dst.read_bytes()
        self.wal.write_bytes(b"updated WAL")

        def invalid_decode(src, dst, key):
            dst.write_bytes(b"not a sqlite database")
            return True

        self.decode.side_effect = invalid_decode
        result = web.ensure_decrypted(self.state)
        self.assertEqual(result["failed"], 1)
        self.assertEqual(self.dst.read_bytes(), original)

    def test_missing_source_is_reported(self):
        self.src.unlink()
        result = web.ensure_decrypted(self.state)
        self.assertEqual(result["source_dbs"], 0)
        self.assertIn("warning", result)

    def test_custom_wechat_tokenizer_does_not_prevent_snapshot_validation(self):
        path = Path(self.temp.name) / "fts.db"
        with sqlite3.connect(path) as conn:
            conn.execute("CREATE VIRTUAL TABLE search USING fts5(content, tokenize='unicode61     ')")
            conn.execute("INSERT INTO search VALUES ('test message')")
        conn.close()
        path.write_bytes(path.read_bytes().replace(b"unicode61     ", b"MMFtsTokenizer"))
        web.validate_sqlite_snapshot(path)

    def test_search_fingerprint_tracks_imported_snapshot(self):
        web.ensure_decrypted(self.state)
        before = web.qa_db_file_fingerprint(self.state, "message/message_0.db")
        self.wal.write_bytes(b"not imported yet")
        self.assertEqual(before, web.qa_db_file_fingerprint(self.state, "message/message_0.db"))
        web.ensure_decrypted(self.state)
        after = web.qa_db_file_fingerprint(self.state, "message/message_0.db")
        self.assertNotEqual(before, after)

    def test_equal_size_updates_still_mark_search_index_stale(self):
        old = {"files": [{"path": "message/message_0.db", "size": 8192, "mtime_ns": 1}]}
        new = {"files": [{"path": "message/message_0.db", "size": 8192, "mtime_ns": 2}]}
        self.assertFalse(web.fingerprint_soft_match(json.dumps(old), new))
        self.assertTrue(web.fingerprint_incremental_possible(json.dumps(old), new)[0])

    def test_refresh_reads_a_fresh_message_page(self):
        self.state.chats = [{"id": "friend", "chat": "friend", "total_messages": 1}]
        with patch.object(web.threading.Thread, "start"), \
                patch.object(web, "collect_message_page", side_effect=[{"messages": [{"text": "old"}]}, {"messages": [{"text": "new"}]}]), \
                patch.object(web, "build_chat_index", return_value=self.state.chats), \
                patch.object(web, "status_payload", return_value={}):
            handler_class = web.make_handler(self.state)
            handler = handler_class.__new__(handler_class)
            handler.json_response = Mock()
            handler.handle_messages({"chat": ["friend"]})
            self.assertEqual(handler.json_response.call_args.args[0]["messages"][0]["text"], "old")
            handler.handle_sync()
            self.assertTrue(handler.json_response.call_args.args[0]["ok"])
            handler.handle_messages({"chat": ["friend"]})
            self.assertEqual(handler.json_response.call_args.args[0]["messages"][0]["text"], "new")

    def test_local_source_config_survives_restart_and_cli_can_override(self):
        with patch.object(web, "source_settings", return_value={"db_storage": str(self.state.db_storage), "account": "demo_me"}), \
                patch.dict(os.environ, {"WECHAT_AGENT_DB_STORAGE": ""}):
            self.assertEqual(web.build_parser().parse_args([]).db_storage, self.state.db_storage)
            self.assertEqual(web.build_parser().parse_args(["--db-storage", "/another/source"]).db_storage, Path("/another/source"))
            self.assertEqual(web.build_parser().parse_args([]).account, "demo_me")
            self.assertEqual(web.build_parser().parse_args(["--account", "demo_other"]).account, "demo_other")

    def test_copied_source_keeps_explicit_sender_identity(self):
        with patch.object(web, "source_settings", return_value={"db_storage": str(self.state.db_storage), "account": "demo_me"}), \
                patch.dict(os.environ, {"WECHAT_AGENT_DB_STORAGE": ""}), \
                patch.object(web, "ensure_decrypted", return_value={}), \
                patch.object(web, "load_avatar_versions", return_value={}), \
                patch.object(web, "build_chat_index", return_value=[]), \
                patch.object(web, "load_image_key_settings", return_value=(None, 0)):
            state = web.load_state(web.build_parser().parse_args([]))
        self.assertEqual(web.infer_account(state.db_storage), "")
        self.assertEqual(state.account, "demo_me")

    def test_manual_sync_reports_busy_readers_without_replacing_their_snapshot(self):
        with patch.object(web.threading.Thread, "start"):
            handler_class = web.make_handler(self.state)
        reader = handler_class.__new__(handler_class)
        sync = handler_class.__new__(handler_class)
        sync.json_response = Mock()

        def overlapping_request():
            with patch.object(web.BaseHTTPRequestHandler, "handle_one_request", side_effect=sync.handle_sync):
                sync.handle_one_request()

        with patch.object(web.BaseHTTPRequestHandler, "handle_one_request", side_effect=overlapping_request):
            reader.handle_one_request()
        self.assertEqual(sync.json_response.call_args.kwargs["status"], 409)
        self.assertEqual(sync.json_response.call_args.args[0]["code"], "sync_busy")
        self.decode.assert_not_called()
        self.assertEqual(self.state.sync_error, "")
        with patch.object(web, "build_chat_index", return_value=[]), \
                patch.object(web, "status_payload", return_value={}):
            sync.handle_sync()
        self.assertTrue(sync.json_response.call_args.args[0]["ok"])
        self.assertEqual(self.decode.call_count, 1)

    def test_manual_sync_failure_is_not_classified_as_reader_contention(self):
        with patch.object(web.threading.Thread, "start"):
            handler_class = web.make_handler(self.state)
        handler = handler_class.__new__(handler_class)
        handler.json_response = Mock()
        with patch.object(web, "ensure_decrypted", side_effect=RuntimeError("source unavailable")), \
                patch.object(web, "status_payload", return_value={}):
            handler.handle_sync()
        result = handler.json_response.call_args.args[0]
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "source unavailable")
        self.assertNotEqual(result.get("code"), "sync_busy")
        self.assertEqual(self.state.sync_error, "source unavailable")

    def test_background_worker_syncs_without_a_browser_request(self):
        self.state.sync_stop = Mock()
        self.state.sync_stop.wait.side_effect = [False, True]
        with patch.object(web.threading, "Thread") as thread, \
                patch.object(web, "build_chat_index", return_value=[]):
            web.make_handler(self.state)
            callback = next(call.kwargs["target"] for call in thread.call_args_list if call.kwargs.get("name") == "wechat-sync")
            callback()
        self.assertEqual(self.state.last_sync_trigger, "auto")
        self.assertEqual(self.state.decrypt_summary["updated"], 1)
        self.assertTrue(self.state.last_synced_at)
        self.assertEqual(self.state.sync_revision, 1)
        self.assertEqual(self.state.sync_stop.wait.call_args_list[0].args, (60,))

    def test_background_worker_defers_during_an_active_request(self):
        self.state.sync_stop = Mock()
        self.state.sync_stop.wait.side_effect = [False, True]
        with patch.object(web.threading, "Thread") as thread:
            handler_class = web.make_handler(self.state)
            callback = next(call.kwargs["target"] for call in thread.call_args_list if call.kwargs.get("name") == "wechat-sync")
            handler = handler_class.__new__(handler_class)
            with patch.object(web.BaseHTTPRequestHandler, "handle_one_request", side_effect=callback):
                handler.handle_one_request()
        self.decode.assert_not_called()
        self.assertEqual(self.state.sync_stop.wait.call_args_list[-1].args, (5,))

    def test_index_prewarm_allows_page_requests_but_still_protects_the_snapshot(self):
        entered, release, served = threading.Event(), threading.Event(), threading.Event()
        with patch.object(web.threading, "Thread") as factory:
            handler_class = web.make_handler(self.state)
            prewarm = next(call.kwargs["target"] for call in factory.call_args_list
                           if call.kwargs.get("target").__name__ == "prewarm_qa_index")
        sync = handler_class.__new__(handler_class)
        sync.json_response = Mock()
        reader = handler_class.__new__(handler_class)

        def slow_index(state):
            entered.set()
            release.wait(3)
            return {"corpus": [], "people_list": []}

        def serve_request():
            sync.handle_sync()
            served.set()

        with patch.object(web, "load_or_build_qa_index", side_effect=slow_index), \
                patch.object(web.BaseHTTPRequestHandler, "handle_one_request", side_effect=serve_request):
            worker = threading.Thread(target=prewarm)
            request = threading.Thread(target=reader.handle_one_request)
            try:
                worker.start()
                self.assertTrue(entered.wait(1))
                request.start()
                self.assertTrue(served.wait(1), "index prewarming must not block page requests")
                self.assertEqual(sync.json_response.call_args.args[0]["code"], "sync_busy")
                self.decode.assert_not_called()
            finally:
                release.set()
                worker.join(4)
                if request.ident is not None:
                    request.join(4)
        self.assertFalse(worker.is_alive())
        with patch.object(web, "build_chat_index", return_value=[]), \
                patch.object(web, "status_payload", return_value={}):
            sync.handle_sync()
        self.assertTrue(sync.json_response.call_args.args[0]["ok"])


if __name__ == "__main__":
    unittest.main()
