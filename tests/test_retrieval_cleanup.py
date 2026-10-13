"""Topic-neutral recall, complete batched reranking and one-way index cleanup."""
import json
import sqlite3
import unittest
from unittest.mock import Mock, patch

from tests import test_qa_agent as fixtures
from wechat_agent import web


class NeutralRetrievalTests(unittest.TestCase):
    def setUp(self):
        fixtures.ToolTests.setUp(self)
        self.state.llm_config = self.state.qa_search_db.parent / "models.json"

    insert = fixtures.ToolTests.insert

    def test_query_and_index_terms_cannot_inject_topics_or_locations(self):
        for question in ("菜鸟实习招聘", "怎么吃透这篇论文", "推荐一本机器学习教材", "最近周一的消息"):
            for token in web.qa_search_terms(question):
                self.assertIn(token, question.casefold())
        tokens = web.qa_index_tokens({"chat_title": "菜鸟招聘交流群", "text": "后端实习面试"})
        self.assertFalse(tokens.intersection({"火锅", "鸡饭", "sg", "新加坡"}))

    def test_debug_does_not_infer_people_dates_or_business_intent(self):
        with patch.object(web, "search_qa_search_db", return_value=([], {"candidate_count": 0})) as search:
            for question in ("最近推荐什么", "去年实习", "新加坡餐厅", "谁吃透论文了"):
                _, diagnostics = web.select_qa_context_with_diagnostics(self.state, {}, question, 20)
                self.assertEqual(search.call_args.args[2:4], ([], None))
                self.assertEqual(diagnostics["query_plan"]["queries"], [{"text": question}])
                self.assertNotIn("intent", diagnostics["query_plan"])

    def test_fusion_has_no_hand_tuned_channel_preferences(self):
        pool = {}
        for source in ("embedding", "fts", "like"):
            web.add_retrieval_candidate(pool, {"local_id": source}, source, 1, "query")
        self.assertEqual(len({v["fusion_score"] for v in pool.values()}), 1)
        self.assertNotIn("weight", web.qa_plan_queries({"queries": [
            {"text": "query", "weight": 0.2}]}, "query")[0])

    def test_all_candidates_reach_api_including_late_best_hit(self):
        items = [{"local_id": str(i), "text": "irrelevant" if i < 24 else "correct",
                  "fusion_score": 100-i} for i in range(25)]
        seen = []
        def complete(profile, key, messages, **kwargs):
            candidates = json.loads(messages[1]["content"])["candidates"]
            seen.extend(c["text"] for c in candidates)
            return json.dumps({"results": [
                {"id": c["id"], "score": 1 if c["text"] == "correct" else 0.1} for c in candidates]})
        config = {"qa": {}, "rerank": {"enabled": True, "batch_size": 8}}
        with patch.object(web, "load_llm_config", return_value=config), patch.object(
                web, "effective_llm_profile", return_value={}), patch.object(
                web, "resolve_llm_api_key", return_value="synthetic"), patch.object(
                web, "call_chat_completion", side_effect=complete):
            result, diag = web.rerank_qa_items_with_config(self.state, "question", items, 1)
        self.assertEqual(len(seen), 25)
        self.assertEqual(result[0]["text"], "correct")
        self.assertEqual(diag["rerank_count"], 25)
        self.assertEqual(diag["rerank_batches"], 4)
        self.assertTrue(diag["rerank_complete"])

    def test_invalid_or_missing_scores_are_not_successful_reranking(self):
        items = [{"local_id": "1"}, {"local_id": "2"}]
        for rows in ([], [{"id":"c1","score":0.8}], [{"id":"alien","score":1}],
                     [{"id":"c1","score":float("nan")}, {"id":"c2","score":0}],
                     [{"id":"c1","score":1}, {"id":"c1","score":0}]):
            with self.subTest(rows=rows), self.assertRaises(RuntimeError):
                web.apply_rerank_response(json.dumps({"results":rows}), items, items, 1)

    def test_batch_failure_falls_back_as_a_whole_not_mixed_scores(self):
        items = [{"local_id":str(i)} for i in range(9)]
        response = json.dumps({"results":[{"id":f"c{i+1}","score":0.5} for i in range(8)]})
        with patch.object(web, "load_llm_config", return_value={"qa":{}, "rerank":{"enabled":True,"batch_size":8}}), \
             patch.object(web, "effective_llm_profile", return_value={}), \
             patch.object(web, "resolve_llm_api_key", return_value="synthetic"), \
             patch.object(web, "call_chat_completion", side_effect=[response, TimeoutError("timeout")]):
            result, diag = web.rerank_qa_items_with_config(self.state, "question", items, 2)
        self.assertEqual(result, items[:2])
        self.assertFalse(diag["rerank_used"])
        self.assertFalse(diag["rerank_complete"])
        self.assertEqual(diag["rerank_count"], 8)

    def test_semantic_and_lexical_hits_merge_by_message_before_reranking(self):
        self.insert(1); self.insert(2, person="bob")
        self.conn.execute("CREATE VIRTUAL TABLE messages_fts USING fts5(search_text)")
        self.conn.execute("INSERT INTO messages_fts(rowid,search_text) SELECT id,search_text FROM messages")
        self.conn.executemany("INSERT INTO semantic_message_map VALUES(7,?)", [(1,), (2,)])
        self.conn.commit()
        self.state.llm_config = self.state.qa_search_db.parent / "models.json"
        seen = []
        def rerank(state, question, items, limit):
            seen.extend(items)
            return items[:limit], {"rerank_count":len(items)}
        with patch.object(web, "qa_search_db_status", return_value={"usable":True}), \
             patch.object(web, "query_qa_semantic_index", return_value=([{
                 "semantic_chunk_id":7, "text":"same-chat context", "semantic_score":0.8}], {})), \
             patch.object(web, "rerank_qa_items_with_config", side_effect=rerank):
            web.search_qa_search_db(self.state, "internship", [], None, 10, {"queries":[{"text":"internship"}]})
        self.assertEqual(len(seen), 2)
        self.assertEqual({str(x["local_id"]) for x in seen}, {"1","2"})
        self.assertTrue(all("embedding" in x["retrieval_sources"] and "fts" in x["retrieval_sources"] for x in seen))
        self.assertTrue(all(x["retrieval_context"] == "same-chat context" for x in seen))

    def migration_fixture(self):
        self.insert(1)
        self.conn.execute("CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT)")
        self.conn.execute("INSERT INTO meta VALUES ('version','2')")
        self.conn.execute("UPDATE messages SET search_text='internship\n火锅 新加坡 internship'")
        self.conn.execute(web.FTS_SCHEMA)
        self.conn.execute("INSERT INTO messages_fts(messages_fts) VALUES ('rebuild')")
        self.conn.commit()

    def test_migration_cleans_fts_preserves_ids_and_is_idempotent(self):
        self.migration_fixture()
        before = self.conn.execute("SELECT id,text,server_id FROM messages").fetchall()
        web.migrate_search_text(self.state)
        self.assertEqual(self.conn.execute("SELECT id,text,server_id FROM messages").fetchall(), before)
        self.assertEqual(self.conn.execute("SELECT rowid FROM messages_fts WHERE messages_fts MATCH '火锅'").fetchall(), [])
        self.assertEqual(self.conn.execute("SELECT rowid FROM messages_fts WHERE messages_fts MATCH 'internship'").fetchall(), [(1,)])
        self.assertEqual(self.conn.execute("SELECT value FROM meta WHERE key='version'").fetchone()[0], str(web.QA_SEARCH_DB_VERSION))
        with patch.object(web, "qa_index_tokens", side_effect=AssertionError("already migrated")):
            web.migrate_search_text(self.state)

    def test_migration_failure_rolls_back_tokens_and_version(self):
        self.migration_fixture()
        with patch.object(web, "semantic_schema_ok", side_effect=RuntimeError("interrupted")):
            with self.assertRaises(RuntimeError):
                web.migrate_search_text(self.state)
        self.assertEqual(self.conn.execute("SELECT value FROM meta WHERE key='version'").fetchone()[0], "2")
        self.assertEqual(self.conn.execute("SELECT rowid FROM messages_fts WHERE messages_fts MATCH '火锅'").fetchall(), [(1,)])

    def test_compacted_vectors_are_invalidated_without_deleting_messages(self):
        self.migration_fixture()
        self.conn.row_factory = sqlite3.Row
        web.ensure_semantic_schema(self.conn)
        chunk = web.semantic_chunk_from_message_rows(self.conn.execute('SELECT * FROM messages').fetchall())
        chunk_id = web.insert_semantic_chunk(self.conn, chunk, [0.1,0.2], 'synthetic')
        self.conn.execute('INSERT INTO semantic_message_map(chunk_id,message_id) VALUES(?,1)', (chunk_id,))
        self.conn.commit()
        web.migrate_search_text(self.state)
        self.assertEqual(self.conn.execute('SELECT count(*) FROM messages').fetchone()[0], 1)
        self.assertEqual(self.conn.execute('SELECT count(*) FROM semantic_chunks').fetchone()[0], 0)
        self.assertEqual(self.conn.execute('SELECT count(*) FROM semantic_message_map').fetchone()[0], 0)
        self.assertIn('idx_semantic_map_chunk', [r['name'] for r in self.conn.execute('PRAGMA index_list(semantic_message_map)')])

    def test_clipped_index_recovers_original_not_short_display_text(self):
        self.insert(1)
        self.conn.row_factory = sqlite3.Row
        row = dict(self.conn.execute("SELECT * FROM messages").fetchone())
        original_text = "a" * 1700 + " internship deadline " + "b" * 1600
        row.update(text=original_text[:1597] + "...", search_text=original_text[:2597] + "...")
        original = {**web.qa_item_from_search_row(row), "text": original_text}
        self.state.chat_by_id = Mock(return_value={"chat": row["chat_id"]})
        with patch.object(web, "collect_qa_items_for_chat", return_value=[original]) as read:
            cleaned = web.neutral_search_text_for_row(self.state, row)
        self.assertIn("internship deadline", cleaned)
        self.assertIsNone(read.call_args.kwargs["max_text_chars"])
        self.assertTrue(read.call_args.kwargs["strict"])


if __name__ == "__main__":
    unittest.main()
