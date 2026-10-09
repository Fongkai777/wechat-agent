import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from wechat_agent import search_storage as storage, web


class SearchStorageTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        self.state = web.AppState(
            db_storage=root / "source", decrypted=root / "decrypted", keys=root / "keys.json",
            media_root=root / "msg", voice_cache=root / "voice.json", llm_config=root / "config.json",
            qa_store=root / "qa.json", qa_index_cache=root / "qa.pkl", qa_search_db=root / "search.db",
        )
        web.build_qa_search_db(self.state)
        with self.connect() as conn:
            web.insert_qa_search_rows(conn, [self.message(i) for i in range(1, 121)])
            web.ensure_semantic_schema(conn)
            rows = conn.execute("SELECT * FROM messages ORDER BY id").fetchall()
            chunk = web.semantic_chunk_from_message_rows(rows[:3])
            chunk_id = web.insert_semantic_chunk(conn, chunk, [0.1, 0.2, 0.3], "fixture")
            conn.executemany("INSERT INTO semantic_message_map VALUES (?, ?)", [(i, chunk_id) for i in (1, 2, 3)])

    def connect(self):
        conn = sqlite3.connect(self.state.qa_search_db)
        conn.row_factory = sqlite3.Row
        self.addCleanup(conn.close)
        return conn

    def message(self, i):
        return web.qa_search_row_from_item({
            "source_db": "message/message_0.db", "source_table": "Msg_test", "local_id": i,
            "server_id": i, "chat_id": "friend", "chat_title": "Friend", "chat_type": "private",
            "person_id": "friend", "timestamp": i, "type": "text", "text": "needle " + "alpha beta " * 100,
        })

    def legacy(self, conn):
        conn.execute("DROP TABLE messages_fts")
        conn.execute("CREATE VIRTUAL TABLE messages_fts USING fts5(search_text, tokenize='unicode61')")
        conn.execute("INSERT INTO messages_fts(rowid, search_text) SELECT id, search_text FROM messages")
        conn.execute("UPDATE semantic_chunks SET search_text='legacy duplicate embedding input'")
        conn.commit()

    def matches(self, conn):
        return [tuple(row) for row in conn.execute(
            "SELECT rowid, bm25(messages_fts) FROM messages_fts WHERE messages_fts MATCH 'needle' ORDER BY rowid"
        )]

    def test_new_indexes_do_not_duplicate_text(self):
        with self.connect() as conn:
            self.assertFalse(storage.has_private_fts_content(conn))
            self.assertEqual(len(self.matches(conn)), 120)
            self.assertEqual(conn.execute("SELECT search_text FROM semantic_chunks").fetchone()[0], "")
            self.assertTrue(web.qa_search_schema_ok(conn))
            self.assertTrue(web.semantic_schema_ok(conn))

    def test_migration_preserves_all_data_rankings_and_is_idempotent(self):
        with self.connect() as conn:
            self.legacy(conn)
            before, matches = storage.preserved_data(conn), self.matches(conn)
            storage.migrate_storage(conn)
            storage.migrate_storage(conn)
            self.assertEqual(storage.preserved_data(conn), before)
            self.assertEqual(self.matches(conn), matches)
            self.assertFalse(storage.has_private_fts_content(conn))
            self.assertEqual(conn.execute("SELECT search_text FROM semantic_chunks").fetchone()[0], "")

    def test_insert_ignore_update_delete_and_rollback_in_both_formats(self):
        for legacy in (False, True):
            with self.subTest(legacy=legacy), self.connect() as conn:
                if legacy:
                    self.legacy(conn)
                self.assertEqual(web.insert_qa_search_rows(conn, [self.message(1)]), 0)
                self.assertEqual(web.insert_qa_search_rows(conn, [self.message(121)]), 1)
                appended_id = conn.execute("SELECT id FROM messages WHERE local_id='121'").fetchone()[0]
                with self.assertRaises(RuntimeError), conn:
                    storage.delete_fts_rows(conn, [1])
                    conn.execute("UPDATE messages SET search_text='replacement' WHERE id=1")
                    conn.execute("INSERT INTO messages_fts(rowid,search_text) VALUES (1,'replacement')")
                    raise RuntimeError("rollback")
                self.assertEqual(len(self.matches(conn)), 121)
                with conn:
                    storage.delete_fts_rows(conn, [appended_id])
                    conn.execute("DELETE FROM messages WHERE id=?", (appended_id,))
                self.assertEqual(len(self.matches(conn)), 120)
                with conn:
                    storage.delete_fts_rows(conn, [1])
                    conn.execute("UPDATE messages SET search_text='replacement' WHERE id=1")
                    conn.execute("INSERT INTO messages_fts(rowid,search_text) VALUES (1,'replacement')")
                self.assertEqual(len(self.matches(conn)), 119)
                with conn:
                    storage.delete_fts_rows(conn, [1])
                    original = self.message(1)[-1]
                    conn.execute("UPDATE messages SET search_text=? WHERE id=1", (original,))
                    conn.execute("INSERT INTO messages_fts(rowid,search_text) VALUES (1,?)", (original,))

    def test_failed_migration_restores_legacy_fts(self):
        with self.connect() as conn:
            self.legacy(conn)
            before = storage.preserved_data(conn)
            with patch.object(storage, "FTS_SCHEMA", "INVALID SQL"), self.assertRaises(sqlite3.Error):
                storage.migrate_storage(conn)
            self.assertTrue(storage.has_private_fts_content(conn))
            self.assertEqual(storage.preserved_data(conn), before)
            self.assertEqual(len(self.matches(conn)), 120)

    def test_compaction_reclaims_space_without_api_and_preserves_values(self):
        with self.connect() as conn:
            self.legacy(conn)
            before = storage.preserved_data(conn)
        conn.close()
        with patch.object(web, "call_embeddings", side_effect=AssertionError("unexpected API")):
            result = storage.compact_offline(self.state.qa_search_db)
        self.assertGreater(result["freed_bytes"], 0)
        self.assertEqual(result["verified"], before)
        with self.connect() as conn:
            self.assertEqual(storage.preserved_data(conn), before)
            self.assertEqual(len(self.matches(conn)), 120)
            self.assertEqual(conn.execute("PRAGMA freelist_count").fetchone()[0], 0)

    def test_failed_verification_leaves_original_untouched(self):
        path = self.state.qa_search_db
        original = path.read_bytes()
        with patch.object(storage, "preserved_data", side_effect=[{"bad": True}, {}]), self.assertRaisesRegex(RuntimeError, "validation failed"):
            storage.compact_offline(path)
        self.assertEqual(path.read_bytes(), original)
        self.assertEqual(list(path.parent.glob("*.compact-*")), [])

    def test_sidecars_and_active_transaction_are_rejected(self):
        Path(str(self.state.qa_search_db) + "-wal").touch()
        with self.assertRaisesRegex(RuntimeError, "sidecars"):
            storage.compact_offline(self.state.qa_search_db)
        with self.connect() as conn:
            conn.execute("BEGIN")
            with self.assertRaisesRegex(RuntimeError, "separate transaction"):
                storage.migrate_storage(conn)


if __name__ == "__main__":
    unittest.main()
