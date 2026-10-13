import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from wechat_agent.skill_retrieval import SkillRetrieval, deduplicate


class RetrievalTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.state = SimpleNamespace(qa_search_db=Path(temp.name)/'index.sqlite', since_ts=None, account='me', chats=[])
        self.conn = sqlite3.connect(self.state.qa_search_db); self.addCleanup(self.conn.close)
        self.conn.executescript('''CREATE TABLE messages(id INTEGER PRIMARY KEY, chat_id TEXT, chat_type TEXT,
            timestamp INTEGER, text TEXT, server_id TEXT, sender_username TEXT);
            CREATE TABLE semantic_message_map(message_id INTEGER PRIMARY KEY, chunk_id INTEGER);''')
        self.status = Mock(return_value=({'ready': True}, {'ready': True}))
        self.semantic = Mock(return_value=([{'semantic_chunk_id': 1, 'semantic_score': .8}], {'ready': True, 'candidate_count': 2}))
        self.reader = Mock()
        self.tools = SkillRetrieval(self.state, self.status, self.semantic, dict, self.reader, lambda: None, {})

    def add(self, id, ts, text, chat='a', chunk=None):
        self.conn.execute('INSERT INTO messages VALUES(?,?,?,?,?,?,?)', (id, chat, 'private', ts, text, str(id), 'alice'))
        if chunk: self.conn.execute('INSERT INTO semantic_message_map VALUES(?,?)', (id, chunk))
        self.conn.commit()

    def test_recall_hydrates_chunks_clips_boundaries_without_top_n(self):
        self.add(1, 9, 'outside', chunk=1)
        self.add(2, 10, 'semantic evidence', chunk=1)
        self.add(3, 21, 'outside', chunk=1)
        for i in range(4, 74): self.add(i, 15, 'INTERN', chunk=1)
        result = self.tools.search('实习机会', {'vectors': [[1, 0]]}, 10, 20, 0.25)
        self.assertEqual(len(result['messages']), 71)
        self.assertEqual(result['semantic_messages'], 71)
        self.assertTrue(all(10 <= x['timestamp'] <= 20 for x in result['messages']))
        self.assertTrue(all(x['sender_known'] for x in result['messages']))

    def test_only_explicit_skill_similarity_threshold_is_applied(self):
        self.add(1, 10, '实习机会', chunk=1)
        self.assertEqual(self.tools.search('实习机会', {}, 0, 20, 0.9)['messages'], [])
        self.assertEqual(len(self.tools.search('实习机会', {}, 0, 20, 0.7)['messages']), 1)

    def test_stale_index_stops_before_paid_request_and_no_keyword_fallback(self):
        self.status.return_value = ({'ready': True, 'soft_stale': True}, {'ready': True})
        with self.assertRaises(ValueError): self.tools.prepare()
        self.semantic.assert_not_called()
        self.status.return_value = ({'ready': True}, {'ready': True})
        self.semantic.return_value = ([], {'error': 'bad embedding', 'ready': True})
        with self.assertRaises(RuntimeError): self.tools.search('实习机会', {}, 0, 20, 0.25)

    def test_context_same_chat_same_second_and_boundaries(self):
        self.add(1, 9, 'out'); self.add(2, 10, 'before'); self.add(3, 10, 'seed')
        self.add(4, 10, 'after'); self.add(5, 11, 'other chat', chat='b'); self.add(6, 21, 'out')
        seed = {
            'index_row_id': 3, 'chat_id': 'a', 'server_id': '3', 'timestamp': 10}
        result = self.tools.context([seed], {'before':3,'after':3,'max_gap_minutes':30}, 10, 20)
        self.assertEqual([x['text'] for x in result['messages']], ['before', 'after'])
        self.assertTrue(all(x['evidence_role'] == 'context' for x in result['messages']))

    def test_raw_context_needs_no_index_and_keeps_consecutive_messages(self):
        self.state.chats = [{'id': 'a'}]
        rows = [{'chat_id': 'a', 'server_id': str(i), 'timestamp': i, 'local_id': i, 'text': 'same'} for i in range(1, 6)]
        self.reader.return_value = rows
        result = self.tools.context([rows[2]], {'before':1,'after':1,'max_gap_minutes':30}, 1, 5)
        self.assertEqual([x['server_id'] for x in result['messages']], ['2', '4'])
        self.status.assert_not_called()
        self.assertTrue(self.reader.call_args.kwargs['strict'])
        self.assertEqual(len(deduplicate(rows)), 5)

    def test_existing_vector_engine_accepts_recorded_query_without_second_api_call(self):
        from wechat_agent import web
        web.ensure_semantic_schema(self.conn)
        for i in range(65):
            self.add(i+1, 15, 'intern', chunk=i+1)
            chunk = dict.fromkeys(('chat_title', 'person_ids', 'person_names', 'start_time', 'end_time', 'content_hash'), '')
            chunk.update(chunk_key=str(i), chat_id='a', chat_type='private', start_ts=10, end_ts=20,
                         message_ids=[i+1], text='intern')
            web.insert_semantic_chunk(self.conn, chunk, [1, 0], 'fixture')
        self.conn.commit()
        self.state.llm_config = Path('unused')
        profile = {'enabled': True, 'model': 'fixture', 'base_url': 'https://example.test', 'dimensions': 2}
        with patch.object(web, 'load_llm_config', return_value={'embedding': profile, 'qa': {}}), \
             patch.object(web, 'qa_embedding_index_status', return_value={'ready': True}), \
             patch.object(web, 'resolve_llm_api_key', return_value='fixture'), \
             patch.object(web, 'call_embeddings', side_effect=AssertionError('must reuse query vector')):
            hits, diagnostics = web.query_qa_semantic_index(self.state, 'intern', [], 10, None,
                {'time_until': 20}, query_embedding={'vectors': [[1, 0]], 'usage': {'total_tokens': 3}})
        self.assertEqual(len(hits), 65)
        self.assertEqual(diagnostics['usage']['total_tokens'], 3)


if __name__ == '__main__': unittest.main()
