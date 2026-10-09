import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from wechat_agent.skill_retrieval import SkillRetrieval, deduplicate
from wechat_agent.task_skills import validate_skill, skill_plan, run_skill_graph


def plan(semantic=True, context=True, mode='list'):
    source = ({'op': 'semantic_search', 'source': 'all_messages', 'query': '实习机会',
               'keywords': ['intern'], 'min_similarity': .25} if semantic else
              {'op': 'read', 'source': 'private_latest'})
    return {'schema_version': 2, 'name': '测试任务', 'description': '可编辑的检索步骤', 'steps': [source,
        {'op': 'filter', 'sender': 'any' if semantic else 'other', 'keywords': [], 'keyword_match': 'any'},
        *([{'op': 'context', 'before': 1, 'after': 1, 'max_gap_minutes': 30}] if context else []),
        {'op': 'deduplicate'}, {'op': 'output', 'mode': mode, 'instruction': '整理有证据的实习信息' if mode == 'model' else ''}]}


class PlanTests(unittest.TestCase):
    def test_v2_validates_order_parameters_and_no_arbitrary_operations(self):
        self.assertEqual(skill_plan(validate_skill(plan()))['search']['query'], '实习机会')
        for mutate in [lambda p: p['steps'].reverse(),
                       lambda p: p['steps'][0].update(top_k=30),
                       lambda p: p['steps'][0].update(min_similarity=float('nan')),
                       lambda p: p['steps'][2].update(before=True),
                       lambda p: p['steps'][2].update(after=51),
                       lambda p: p['steps'][0].update(op='shell'),
                       lambda p: p['steps'][0].update(query='')]:
            data = plan(); mutate(data)
            with self.subTest(data=data), self.assertRaises(ValueError): validate_skill(data)

    def test_local_plan_makes_no_embedding_call(self):
        invoke = Mock(return_value={'complete': True, 'messages': [
            {'chat_id': 'alice', 'local_id': 1, 'timestamp': 20, 'sender_known': True, 'mine': False, 'text': 'hello'},
            {'chat_id': 'bob', 'local_id': 1, 'timestamp': 20, 'sender_known': True, 'mine': True, 'text': 'hi'}]})
        output = run_skill_graph(self.goal(plan(False, False)), Mock(side_effect=AssertionError('no LLM')), invoke, Mock(), lambda: False, 'local')
        self.assertEqual(len(json.loads(output)['items']), 1)
        self.assertEqual([c.args[0] for c in invoke.call_args_list], ['skill_read'])

    @staticmethod
    def goal(skill):
        return {'prompt': '实习信息', 'skill': skill, 'skill_version': 1, 'started_at': 100000,
                'range_type': 'recent_days', 'range_value': 1, 'range_unit': 'days'}

    def test_pipeline_separates_context_and_preserves_embedding_receipt_on_resume(self):
        with tempfile.TemporaryDirectory() as directory:
            goal = {**self.goal(plan()), 'checkpoint_path': str(Path(directory)/'run.sqlite'), 'run_id': 'fixture'}
            seed = {'chat_id': 'a', 'server_id': '1', 'text': 'intern', 'timestamp': 20}
            context = {'chat_id': 'a', 'server_id': '2', 'text': 'apply before Friday', 'timestamp': 21, 'evidence_role': 'context'}
            fail = [True]
            def tool(name, args, since, until):
                if name == 'skill_search_prepare': return {'ready': True}
                if name == 'skill_embed': return {'vectors': [[1, 0]], 'usage': {'total_tokens': 5}}
                if name == 'skill_search':
                    if fail[0]: raise ValueError('local database temporarily unavailable')
                    return {'messages': [seed, seed], 'complete': True}
                if name == 'skill_context': return {'messages': [context, context], 'complete': True}
                raise AssertionError(name)
            invoke = Mock(side_effect=tool)
            with self.assertRaises(ValueError):
                run_skill_graph(goal, Mock(), invoke, Mock(), lambda: False, 'fixture')
            fail[0] = False
            report = Mock()
            output = run_skill_graph({**goal, 'resume': True}, Mock(), invoke, report, lambda: False, 'fixture')
            self.assertEqual(sum(c.args[0] == 'skill_embed' for c in invoke.call_args_list), 1)
            self.assertEqual(len(json.loads(output)['items']), 1)
            saved = report.call_args.args[0]
            self.assertEqual(len(saved['sources']), 2)
            self.assertEqual(saved['usage']['total_tokens'], 5)
            self.assertEqual([s['reference'] for s in saved['sources']], [1, 2])

    def test_model_receives_context_and_combined_usage(self):
        def invoke(name, args, since, until):
            if name == 'skill_search_prepare': return {'ready': True}
            if name == 'skill_embed': return {'vectors': [[1, 0]], 'usage': {'total_tokens': 5}}
            return {'complete': True, 'messages': [{'chat_id': 'a', 'server_id': '1' if name == 'skill_search' else '2',
                    'text': 'match' if name == 'skill_search' else 'context', 'evidence_role': 'match' if name == 'skill_search' else 'context'}]}
        completion = Mock(return_value={'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps({
            'summary': '实习', 'items': [{'title': '岗位', 'detail': '详情', 'suggestion': None, 'source_refs': [1, 2]}], 'notice': None})}}],
            'usage': {'total_tokens': 10}})
        report = Mock()
        run_skill_graph(self.goal(plan(mode='model')), completion, invoke, report, lambda: False, 'fixture')
        evidence = json.loads(completion.call_args.args[0][-1]['content'])['evidence']
        self.assertEqual(len(evidence), 2)
        self.assertEqual(report.call_args.args[0]['usage']['total_tokens'], 15)

    def test_uncertain_embedding_does_not_retry_on_resume_without_confirmation(self):
        from wechat_agent.goal_checkpoints import GoalResumeRequired
        with tempfile.TemporaryDirectory() as directory:
            goal = {**self.goal(plan(context=False)), 'checkpoint_path': str(Path(directory)/'run.sqlite'), 'run_id': 'fixture'}
            def tool(name, args, since, until):
                if name == 'skill_search_prepare': return {'ready': True}
                if name == 'skill_embed': raise TimeoutError('unknown outcome')
                raise AssertionError(name)
            invoke = Mock(side_effect=tool)
            with self.assertRaises(TimeoutError):
                run_skill_graph(goal, Mock(), invoke, Mock(), lambda: False, 'fixture')
            with self.assertRaises(GoalResumeRequired):
                run_skill_graph({**goal, 'resume': True}, Mock(), invoke, Mock(), lambda: False, 'fixture')
            self.assertEqual(sum(c.args[0] == 'skill_embed' for c in invoke.call_args_list), 1)


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
        self.tools = SkillRetrieval(self.state, self.status, self.semantic, Mock(), dict, self.reader, lambda: None, {})

    def add(self, id, ts, text, chat='a', chunk=None):
        self.conn.execute('INSERT INTO messages VALUES(?,?,?,?,?,?,?)', (id, chat, 'private', ts, text, str(id), 'alice'))
        if chunk: self.conn.execute('INSERT INTO semantic_message_map VALUES(?,?)', (id, chunk))
        self.conn.commit()

    def test_recall_hydrates_chunks_clips_boundaries_unions_keywords_no_top_n(self):
        self.add(1, 9, 'outside', chunk=1)
        self.add(2, 10, 'semantic evidence', chunk=1)
        self.add(3, 21, 'outside', chunk=1)
        for i in range(4, 74): self.add(i, 15, 'INTERN')
        result = self.tools.search(plan()['steps'][0], {'vectors': [[1, 0]]}, 10, 20)
        self.assertEqual(len(result['messages']), 71)
        self.assertEqual(result['semantic_messages'], 1)
        self.assertEqual(result['keyword_messages'], 70)
        self.assertTrue(all(10 <= x['timestamp'] <= 20 for x in result['messages']))
        self.assertTrue(all(x['sender_known'] for x in result['messages']))

    def test_keyword_metacharacters_are_literals_not_sql(self):
        self.semantic.return_value = ([], {'ready': True})
        self.add(1, 10, '100%_done'); self.add(2, 10, 'anything')
        step = {**plan()['steps'][0], 'keywords': ['%_']}
        self.assertEqual(len(self.tools.search(step, {}, 0, 20)['messages']), 1)

    def test_stale_index_stops_before_paid_request_and_no_keyword_fallback(self):
        self.status.return_value = ({'ready': True, 'soft_stale': True}, {'ready': True})
        with self.assertRaises(ValueError): self.tools.prepare()
        self.tools.embed.assert_not_called()
        self.status.return_value = ({'ready': True}, {'ready': True})
        self.semantic.return_value = ([], {'error': 'bad embedding', 'ready': True})
        with self.assertRaises(RuntimeError): self.tools.search(plan()['steps'][0], {}, 0, 20)

    def test_context_same_chat_same_second_and_boundaries(self):
        self.add(1, 9, 'out'); self.add(2, 10, 'before'); self.add(3, 10, 'seed')
        self.add(4, 10, 'after'); self.add(5, 11, 'other chat', chat='b'); self.add(6, 21, 'out')
        seed = {
            'index_row_id': 3, 'chat_id': 'a', 'server_id': '3', 'timestamp': 10}
        result = self.tools.context([seed], plan()['steps'][2], 10, 20)
        self.assertEqual([x['text'] for x in result['messages']], ['before', 'after'])
        self.assertTrue(all(x['evidence_role'] == 'context' for x in result['messages']))

    def test_raw_context_needs_no_index_and_keeps_consecutive_messages(self):
        self.state.chats = [{'id': 'a'}]
        rows = [{'chat_id': 'a', 'server_id': str(i), 'timestamp': i, 'local_id': i, 'text': 'same'} for i in range(1, 6)]
        self.reader.return_value = rows
        result = self.tools.context([rows[2]], plan()['steps'][2], 1, 5)
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
