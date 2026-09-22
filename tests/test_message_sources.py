import json
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from wechat_agent import web
from wechat_agent.cli import find_message_dbs


class MessageSourceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.state = web.AppState(
            db_storage=root / "source", decrypted=root / "decrypted", keys=root / "keys.json",
            media_root=root / "msg", voice_cache=root / "voice.json", llm_config=root / "config.json",
            qa_store=root / "qa.json", qa_index_cache=root / "qa.pkl", qa_search_db=root / "search.db",
        )
        self.directory = self.state.decrypted / "message"
        self.directory.mkdir(parents=True)
        self.make_db("message_2.db", "Msg_test", [(1, 101, "repeat"), (2, 102, "repeat")])
        self.make_db("message_0.db", "Msg_other", [(1, 103, "unrelated")])
        self.copy = self.directory / "message_2 2.db"
        shutil.copy2(self.directory / "message_2.db", self.copy)
        self.state.chats = web.build_chat_index(self.state)

    def make_db(self, name, table, rows):
        with sqlite3.connect(self.directory / name) as conn:
            conn.execute("CREATE TABLE Name2Id(user_name TEXT)")
            conn.execute("INSERT INTO Name2Id VALUES ('friend')")
            conn.execute(f"CREATE TABLE {table}(local_id INTEGER, server_id INTEGER, local_type INTEGER, real_sender_id INTEGER, create_time INTEGER, message_content TEXT)")
            conn.executemany(f"INSERT INTO {table} VALUES (?, ?, 1, 1, 100, ?)", rows)

    def connect(self):
        conn = sqlite3.connect(self.state.qa_search_db)
        conn.row_factory = sqlite3.Row
        self.addCleanup(conn.close)
        return conn

    def seed_legacy_index(self):
        chat = next(chat for chat in self.state.chats if chat["table_hash"] == "test")
        chat["shards"].append({"db": "message/message_2 2.db", "table": "Msg_test", "count": 2})
        chat["total_messages"] += 2
        web.build_qa_search_db(self.state)
        with self.connect() as conn:
            web.ensure_semantic_schema(conn)
            rows = conn.execute("SELECT * FROM messages ORDER BY id").fetchall()
            for group in ([row for row in rows if row["source_db"] == "message/message_0.db"],
                          [row for row in rows if row["source_db"] != "message/message_0.db"]):
                chunk = web.semantic_chunk_from_message_rows(group)
                chunk_id = web.insert_semantic_chunk(conn, chunk, [1.0, 0.0], "test-embedding")
                conn.executemany("INSERT INTO semantic_message_map VALUES (?, ?)", [(row["id"], chunk_id) for row in group])
            self.original_ids = [row["id"] for row in rows if not web.is_excluded_message_source(row["source_db"])]
        self.state.chats = web.build_chat_index(self.state)

    def test_discovery_only_includes_numeric_message_shards(self):
        for name in ("message_fts.db", "message_resource.db", "message_2 copy.db", "message_2_backup.db",
                     "message_2.db-wal", "biz_message_1.db", "biz_message_1 2.db", "media_0.db", "message_12.db"):
            (self.directory / name).touch()
        (self.directory / "message_99.db").mkdir()
        self.assertEqual([path.name for path in find_message_dbs(self.state.decrypted)],
                         ["message_0.db", "message_12.db", "message_2.db"])
        self.assertIn("biz_message_1.db", [path.name for path in find_message_dbs(self.state.decrypted, include_biz=True)])
        self.assertNotIn("biz_message_1 2.db", [path.name for path in find_message_dbs(self.state.decrypted, include_biz=True)])

    def test_chat_counts_and_api_collection_preserve_real_repeated_messages(self):
        self.assertEqual(sum(chat["total_messages"] for chat in self.state.chats), 3)
        chat = next(chat for chat in self.state.chats if chat["table_hash"] == "test")
        messages = web.collect_messages_for_chat(self.state, chat)
        self.assertEqual([message["content"] for message in messages], ["repeat", "repeat"])
        self.assertEqual([message["server_id"] for message in messages], [101, 102])
        self.assertEqual(len(chat["shards"]), 1)

    def test_new_text_index_excludes_copy(self):
        status = web.build_qa_search_db(self.state)
        self.assertEqual(status["message_count"], 3)
        with self.connect() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM messages WHERE source_db LIKE '% 2.db'").fetchone()[0], 0)

    def test_legacy_cleanup_is_incremental_and_preserves_unrelated_vectors(self):
        self.seed_legacy_index()
        copy_bytes = self.copy.read_bytes()
        with patch.object(web, "build_qa_search_db", side_effect=AssertionError("unexpected full rebuild")), \
             patch.object(web, "call_embeddings", side_effect=AssertionError("unexpected API request")):
            result = web.update_qa_search_db_incremental(self.state)
            self.assertEqual((result["update_mode"], result["removed_messages"], result["invalidated_chunks"]), ("incremental", 2, 1))
            self.assertEqual((result["inserted"], result["message_count"]), (0, 3))
            self.assertTrue(result["ready"])
            with self.connect() as conn:
                self.assertEqual([row[0] for row in conn.execute("SELECT id FROM messages ORDER BY id")], self.original_ids)
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM messages_fts WHERE messages_fts MATCH 'repeat'").fetchone()[0], 2)
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM semantic_chunks").fetchone()[0], 1)
                self.assertEqual(len(web.semantic_pending_message_rows(conn)), 2)
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM semantic_message_map sm LEFT JOIN messages m ON sm.message_id=m.id WHERE m.id IS NULL").fetchone()[0], 0)
            self.assertEqual(web.update_qa_search_db_incremental(self.state)["removed_messages"], 0)
        self.assertEqual(self.copy.read_bytes(), copy_bytes)

    def test_cleanup_rolls_back_fts_and_vectors_on_failure(self):
        self.seed_legacy_index()
        with self.connect() as conn:
            original = web.invalidate_semantic_chunks_for_messages

            def fail_after_invalidating(connection, ids):
                original(connection, ids)
                raise RuntimeError("test interruption")

            with patch.object(web, "invalidate_semantic_chunks_for_messages", side_effect=fail_after_invalidating):
                with self.assertRaisesRegex(RuntimeError, "test interruption"):
                    web.remove_excluded_qa_sources(conn)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0], 5)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM messages_fts").fetchone()[0], 5)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM semantic_chunks").fetchone()[0], 2)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM semantic_message_map").fetchone()[0], 5)

    def test_missing_real_shard_still_requires_reconciliation(self):
        current = {"files": [{"path": "message/message_0.db", "size": 100}]}
        old = {"files": current["files"] + [{"path": "message/message_2.db", "size": 100}]}
        self.assertFalse(web.fingerprint_incremental_possible(json.dumps(old), current)[0])
        old["files"][-1]["path"] = "message/message_2 2.db"
        self.assertTrue(web.fingerprint_incremental_possible(json.dumps(old), current)[0])


if __name__ == "__main__":
    unittest.main()
