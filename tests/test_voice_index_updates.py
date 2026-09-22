import copy
import json
import sqlite3
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from wechat_agent import web
from wechat_agent.voice_transcribe import save_voice_cache, voice_cache_key


class VoiceIndexUpdateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.state = web.AppState(
            db_storage=root / "source", decrypted=root / "decrypted", keys=root / "keys.json",
            media_root=root / "msg", voice_cache=root / "voice_transcriptions.json",
            llm_config=root / "config.json", qa_store=root / "qa.json",
            qa_index_cache=root / "qa.pkl", qa_search_db=root / "search.db",
        )
        directory = self.state.decrypted / "message"
        directory.mkdir(parents=True)
        with sqlite3.connect(directory / "message_0.db") as conn:
            conn.execute("CREATE TABLE Name2Id(user_name TEXT)")
            conn.execute("INSERT INTO Name2Id VALUES ('friend')")
            conn.execute("CREATE TABLE Msg_test(local_id INTEGER, server_id INTEGER, local_type INTEGER, real_sender_id INTEGER, create_time INTEGER, message_content TEXT)")
            conn.executemany("INSERT INTO Msg_test VALUES (?, ?, ?, 1, ?, ?)", [
                (1, 11, 34, 100, '<msg><voicemsg voicelength="7900" /></msg>'),
                (2, 12, 1, 110, "neighbor"), (3, 13, 1, 120, "unrelated"),
            ])
        with sqlite3.connect(directory / "media_0.db") as conn:
            conn.execute("CREATE TABLE Name2Id(user_name TEXT)")
            conn.execute("INSERT INTO Name2Id VALUES ('friend')")
            conn.execute("CREATE TABLE VoiceInfo(chat_name_id INTEGER, create_time INTEGER, local_id INTEGER, svr_id INTEGER, voice_data BLOB, data_index BLOB)")
            conn.execute("INSERT INTO VoiceInfo VALUES (1, 100, 901, 11, X'01', NULL)")
        self.state.chats = [{"id": "friend", "chat": "friend", "title": "Friend", "type": "private", "total_messages": 3,
                             "shards": [{"db": "message/message_0.db", "table": "Msg_test", "count": 3}]}]
        self.state.contacts = {"friend": "Friend"}
        self.key = voice_cache_key("message/media_0.db", 901, 100)
        web.build_qa_search_db(self.state)
        with self.connect() as conn:
            web.ensure_semantic_schema(conn)
            rows = conn.execute("SELECT * FROM messages ORDER BY id").fetchall()
            for group in (rows[:2], rows[2:]):
                chunk = web.semantic_chunk_from_message_rows(group)
                chunk_id = web.insert_semantic_chunk(conn, chunk, [1.0, 0.0], "test-embedding")
                conn.executemany("INSERT INTO semantic_message_map VALUES (?, ?)", [(row["id"], chunk_id) for row in group])
            self.original = {row["id"]: dict(row) for row in rows}
        self.addCleanup(patch.stopall)
        self.no_full_rebuild = patch.object(web, "build_qa_search_db", side_effect=AssertionError("unexpected full rebuild")).start()

    def connect(self):
        conn = sqlite3.connect(self.state.qa_search_db)
        conn.row_factory = sqlite3.Row
        self.addCleanup(conn.close)
        return conn

    def save_text(self, text, **extra):
        save_voice_cache({self.key: {"text": text, **extra}}, self.state.voice_cache)

    def test_old_voice_updates_fts_preserves_ids_and_unrelated_vectors(self):
        self.save_text("newtranscription")
        status = web.update_qa_search_db_incremental(self.state)
        self.assertEqual((status["update_mode"], status["inserted"], status["updated_voices"], status["invalidated_chunks"]), ("incremental", 0, 1, 1))
        with self.connect() as conn:
            self.assertEqual([r[0] for r in conn.execute("SELECT id FROM messages ORDER BY id")], [1, 2, 3])
            self.assertIn("newtranscription", conn.execute("SELECT text FROM messages WHERE id=1").fetchone()[0])
            self.assertEqual(conn.execute("SELECT rowid FROM messages_fts WHERE messages_fts MATCH 'newtranscription'").fetchone()[0], 1)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM semantic_chunks").fetchone()[0], 1)
            self.assertEqual(conn.execute("SELECT message_id FROM semantic_message_map").fetchone()[0], 3)
            self.assertEqual([r["id"] for r in web.semantic_pending_message_rows(conn)], [1, 2])
            self.assertEqual(dict(conn.execute("SELECT * FROM messages WHERE id=3").fetchone()), self.original[3])
        self.assertTrue(status["ready"])

    def test_correction_and_deletion_remove_stale_search_terms(self):
        self.save_text("oldtranscription")
        web.update_qa_search_db_incremental(self.state)
        self.save_text("correctedtranscription")
        self.assertEqual(web.update_qa_search_db_incremental(self.state)["updated_voices"], 1)
        with self.connect() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM messages_fts WHERE messages_fts MATCH 'oldtranscription'").fetchone()[0], 0)
        self.state.voice_cache.unlink()
        self.assertEqual(web.update_qa_search_db_incremental(self.state)["updated_voices"], 1)
        with self.connect() as conn:
            self.assertEqual(conn.execute("SELECT text FROM messages WHERE id=1").fetchone()[0], self.original[1]["text"])
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM messages_fts WHERE messages_fts MATCH 'correctedtranscription'").fetchone()[0], 0)

    def test_repeating_update_and_metadata_only_changes_do_not_reembed(self):
        self.save_text("savedtext", model="one")
        web.update_qa_search_db_incremental(self.state)
        with patch.object(web, "refresh_qa_voice_rows", side_effect=AssertionError("unchanged cache scanned")):
            self.assertEqual(web.update_qa_search_db_incremental(self.state)["updated_voices"], 0)
        self.save_text("savedtext", model="two")
        status = web.update_qa_search_db_incremental(self.state)
        self.assertEqual((status["updated_voices"], status["invalidated_chunks"]), (0, 0))

    def test_voice_only_scan_excludes_text_messages(self):
        self.assertEqual([r["local_id"] for r in web.collect_qa_items_for_chat(self.state, self.state.chats[0], voice_only=True)], [1])

    def test_failed_refresh_rolls_back_text_fts_vectors_and_fingerprint(self):
        with self.connect() as conn:
            fingerprint = web.read_qa_search_meta(conn, "fingerprint")
        self.save_text("failuretext")
        with patch.object(web, "invalidate_semantic_chunks_for_messages", side_effect=RuntimeError("test failure")):
            with self.assertRaises(RuntimeError):
                web.update_qa_search_db_incremental(self.state)
        with self.connect() as conn:
            self.assertEqual(web.read_qa_search_meta(conn, "fingerprint"), fingerprint)
            self.assertEqual(conn.execute("SELECT text FROM messages WHERE id=1").fetchone()[0], self.original[1]["text"])
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM messages_fts WHERE messages_fts MATCH 'failuretext'").fetchone()[0], 0)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM semantic_chunks").fetchone()[0], 2)

    def test_invalid_cache_preserves_index(self):
        self.state.voice_cache.write_text("invalid JSON")
        with self.assertRaisesRegex(RuntimeError, "保留已有索引"):
            web.update_qa_search_db_incremental(self.state)
        with self.connect() as conn:
            self.assertEqual(conn.execute("SELECT text FROM messages WHERE id=1").fetchone()[0], self.original[1]["text"])

    def test_legacy_and_custom_cache_fingerprints_allow_incremental(self):
        old = {"account": "a", "since_ts": None, "files": [{"path": "voice_transcriptions.json", "size": 10}]}
        current = {**old, "files": [{"path": "voice_transcriptions.json", "size": 12}]}
        self.assertTrue(web.fingerprint_incremental_possible(json.dumps(old), current)[0])
        current.update(voice_cache_file="custom.json", files=[{"path": "custom.json", "size": 12}])
        self.assertTrue(web.fingerprint_incremental_possible(json.dumps(old), current)[0])
        self.assertFalse(web.fingerprint_incremental_possible(json.dumps(old), {**current, "account": "other"})[0])

    def test_semantic_update_embeds_only_invalidated_chunk(self):
        self.save_text("newwords")
        config = copy.deepcopy(web.DEFAULT_LLM_CONFIG)
        config["embedding"].update(enabled=True, api_key="test-only", model="test-embedding", dimensions=2)
        with patch.object(web, "load_llm_config", return_value=config), \
                patch.object(web, "call_embeddings", return_value=([[0.0, 1.0]], {})) as api:
            result = web.build_or_update_qa_semantic_index(self.state)
        self.assertEqual((result["inserted_chunks"], result["mapped_messages"]), (1, 2))
        self.assertEqual(len(api.call_args.args[2]), 1)
        self.assertIn("newwords", api.call_args.args[2][0])
        with self.connect() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM semantic_message_map").fetchone()[0], 3)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM semantic_chunks").fetchone()[0], 2)

    def test_new_messages_can_append_while_old_voice_is_updated(self):
        with sqlite3.connect(self.state.decrypted / "message/message_0.db") as conn:
            conn.execute("INSERT INTO Msg_test VALUES (4, 14, 1, 1, 130, 'appended')")
        self.state.chats[0]["total_messages"] = 4
        self.save_text("newwords")
        status = web.update_qa_search_db_incremental(self.state)
        self.assertEqual((status["inserted"], status["updated_voices"]), (1, 1))

    def test_maintenance_routes_reject_overlap_and_release_after_error(self):
        with patch.object(web.threading.Thread, "start"):
            cls = web.make_handler(self.state)
        first = cls.__new__(cls)
        other = cls.__new__(cls)
        first.path = other.path = "/api/jobs/start"
        first.read_json_body = Mock(return_value={"kind": "/api/transcribe_all_voices_stream", "payload": {}})
        other.read_json_body = Mock(return_value={"kind": "/api/rag/rebuild_stream", "payload": {}})
        first.json_response = Mock()
        other.json_response = Mock()
        entered, release = threading.Event(), threading.Event()

        def transcribe(payload, emit):
            entered.set()
            release.wait(2)
            raise RuntimeError("test")

        other.run_rag_rebuild_stream = Mock(side_effect=lambda payload, emit: emit("done", ok=True))
        first.run_transcribe_all_voices_stream = transcribe
        first.do_POST()
        self.assertTrue(entered.wait(2))
        other.do_POST()
        self.assertEqual(other.json_response.call_args.kwargs["status"], 409)
        other.run_rag_rebuild_stream.assert_not_called()
        release.set()
        job = cls.background_jobs.get(first.json_response.call_args.args[0]["job"]["id"])
        for _ in range(200):
            if job.status != "running":
                break
            time.sleep(.01)
        self.assertEqual(job.status, "failed")
        other.do_POST()
        job = cls.background_jobs.get(other.json_response.call_args.args[0]["job"]["id"])
        for _ in range(200):
            if job.status != "running":
                break
            time.sleep(.01)
        self.assertEqual(job.status, "completed")
        other.run_rag_rebuild_stream.assert_called_once()


if __name__ == "__main__":
    unittest.main()
