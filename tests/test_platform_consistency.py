"""Cross-entry regressions: billing, raw context, and scoped SQL reuse."""
import json
import sqlite3
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from tests.test_code_skills import package
from tests.test_platform_unification import native_args
from tests.test_qa_agent import decision, accept_reviews
from wechat_agent import demo, web
from wechat_agent.code_skill_service import run_code_skill, synthetic_api
from wechat_agent.goal_checkpoints import GoalResumeRequired
from wechat_agent.platform_runtime import create_platform_runtime, QA_LIMITS, PlatformToolSession
from wechat_agent.platform_tools import TOOL_EFFECTS, call_may_bill
from wechat_agent.qa_agent import run_conversation_agent


class SQLReuseTests(unittest.TestCase):
    def setUp(self):
        self.api = synthetic_api(package())
        self.addCleanup(self.api.close)
        self.reader = Mock(wraps=self.api.backends['indexed'])
        self.api.backends['indexed'] = self.reader

    def test_python_and_native_share_one_table_and_provenance(self):
        direct = self.api.dispatch('query', {'sql':'SELECT * FROM messages'})
        native = self.api.api_tool('query_messages', native_args('query_messages'))
        self.assertEqual([r['text'] for r in direct], [r['text'] for r in native['items']])
        self.assertEqual(self.reader.call_count, 1)
        self.assertEqual(self.api.sql_snapshot.builds, 1)
        self.assertEqual(self.api.api_cite(direct)[0]['reference'], native['items'][0]['reference'])

    def test_scope_change_replaces_snapshot_and_cannot_escape_with_subquery(self):
        self.api.api_query('SELECT * FROM messages')
        old = self.api.sql_snapshot.conn
        rows = self.api.api_query('WITH all_rows AS (SELECT * FROM messages) SELECT * FROM all_rows', since=202, until=202)
        self.assertEqual([r['timestamp'] for r in rows], [202])
        with self.assertRaises(sqlite3.ProgrammingError): old.execute('SELECT 1')
        self.assertEqual(self.api.sql_snapshot.builds, 2)
        with self.assertRaises(ValueError): self.api.api_query('SELECT * FROM messages', since=0)

    def test_read_only_boundary_survives_warm_queries_and_errors(self):
        self.api.api_query('SELECT count(*) FROM messages')
        for sql in ('DELETE FROM messages', 'PRAGMA query_only=OFF', 'SELECT * FROM sqlite_master',
                    "ATTACH ':memory:' AS other", "SELECT load_extension('bad')", 'SELECT * FROM unknown'):
            with self.subTest(sql=sql), self.assertRaises(sqlite3.DatabaseError): self.api.api_query(sql)
        self.assertEqual(self.api.api_query('SELECT count(*) AS n FROM messages'), [{'n':5}])
        self.assertEqual(self.reader.call_count, 1)

    def test_reuse_across_graph_threads_and_close(self):
        with ThreadPoolExecutor(max_workers=1) as pool:
            pool.submit(self.api.api_query, 'SELECT count(*) FROM messages').result()
        self.assertEqual(self.api.api_query('SELECT count(*) AS n FROM messages'), [{'n':5}])
        conn = self.api.sql_snapshot.conn
        self.api.close()
        with self.assertRaises(sqlite3.ProgrammingError): conn.execute('SELECT 1')
        self.assertEqual(self.api.sql_snapshot.originals, {})

    def test_source_mutation_does_not_change_active_snapshot(self):
        rows = self.reader()
        self.api.backends['indexed'] = lambda: rows
        self.api.api_query('SELECT count(*) FROM messages')
        rows[0]['text'] = 'changed after snapshot'
        result = self.api.api_query('SELECT * FROM messages WHERE id=1')
        self.assertNotEqual(result[0]['text'], rows[0]['text'])
        self.assertEqual(len(self.api.api_cite(result)), 1)


class PaidEntryTests(unittest.TestCase):
    def setUp(self):
        accept_reviews(self)

    def test_paid_native_metadata_matches_permissions(self):
        for name in ('search', 'semantic_search'):
            self.assertTrue(TOOL_EFFECTS[name]['may_bill'])
        self.assertFalse(call_may_bill('tool', {'name':'query_messages'}))

    def test_qa_unknown_semantic_tool_does_not_repeat_without_confirmation(self):
        with tempfile.TemporaryDirectory() as directory:
            backend = Mock(side_effect=TimeoutError('unknown outcome'))
            choose = Mock(side_effect=[decision('semantic_search', **native_args('semantic_search')),
                                       decision('finish')])
            path = Path(directory)/'qa.db'
            def run(resume=False, confirm=False):
                api = synthetic_api(package())
                api.backends['semantic'] = backend
                session = PlatformToolSession(SimpleNamespace(since_ts=100), SimpleNamespace(api=api))
                try:
                    return run_conversation_agent('internship', [], [], session, choose,
                        Mock(return_value={'paragraphs':[]}), Mock(), Mock(), lambda:None,
                        now=datetime.fromtimestamp(1000, timezone.utc), checkpoint_path=path,
                        resume=resume, confirm_retry=confirm)
                finally: api.close()
            with self.assertRaises(TimeoutError): run()
            with self.assertRaises(GoalResumeRequired): run(resume=True)
            self.assertEqual(backend.call_count, 1)
            backend.side_effect = None
            backend.return_value = {'messages':[], 'warning':''}
            run(resume=True, confirm=True)
            self.assertEqual(backend.call_count, 2)

    def test_uncertain_semantic_calls_require_confirmation_through_both_entries(self):
        entries = [('semantic_search', {'query':'internship'}),
                   ('tool', {'name':'semantic_search', 'arguments':native_args('semantic_search')})]
        for method, arguments in entries:
            with self.subTest(method=method), tempfile.TemporaryDirectory() as directory:
                p = package()
                goal = {'skill':p, 'run_id':'one', 'checkpoint_path':str(Path(directory)/'run.db'),
                        'profile_signature':'fixture'}
                backend = Mock(side_effect=TimeoutError('unknown remote outcome'))
                def workflow(package, context, dispatch, **kwargs):
                    dispatch(method, arguments)
                    return {'summary':'Done', 'items':[], 'notice':None}
                def run():
                    api = synthetic_api(p)
                    api.backends['semantic'] = backend
                    try:
                        return run_code_skill(goal, api, lambda data:None, lambda:False, 'fixture')
                    finally: api.close()
                with patch('wechat_agent.code_skill_service.run_package', side_effect=workflow):
                    with self.assertRaises(TimeoutError): run()
                    goal.update(resume=True, confirm_retry=False)
                    with self.assertRaises(GoalResumeRequired): run()
                    self.assertEqual(backend.call_count, 1)
                    backend.side_effect = None
                    backend.return_value = {'messages':[], 'warning':''}
                    goal['confirm_retry'] = True
                    self.assertEqual(json.loads(run())['summary'], 'Done')
                    self.assertEqual(backend.call_count, 2)
                    run()
                    self.assertEqual(backend.call_count, 2)


class RawContextTests(unittest.TestCase):
    def test_raw_reader_and_both_context_entries_preserve_empty_anchor_and_row_order(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            demo.initialize(root)
            state = demo.demo_state(root)
            with sqlite3.connect(state.decrypted/'fixture.db') as conn:
                conn.execute('CREATE TABLE Name2Id(user_name TEXT)')
                conn.execute("INSERT INTO Name2Id VALUES ('alice')")
                conn.execute('CREATE TABLE Msg_raw(local_id INTEGER, server_id INTEGER, local_type INTEGER, '
                             'real_sender_id INTEGER, create_time INTEGER, message_content TEXT)')
                conn.executemany('INSERT INTO Msg_raw VALUES(?,?,1,1,15,?)',
                                 [(30,101,'before'), (10,102,''), (20,103,'after')])
            state.chats = [{'id':'alice', 'chat':'alice', 'type':'private', 'title':'Alice', 'last_ts':15,
                            'shards':[{'db':'fixture.db', 'table':'Msg_raw'}]}]
            runtime = create_platform_runtime(state, {'since':10,'until':20,'task':'Test','account':'self'},
                QA_LIMITS, web.load_llm_config(state.llm_config), 'qa', lambda:None, threading.Lock(),
                prepare=Mock(side_effect=AssertionError('raw reads must not prepare an index')))
            self.addCleanup(runtime.api.close)
            api = runtime.api
            rows = api.dispatch('messages', {'chat_id':'alice'})
            self.assertEqual([r['local_id'] for r in rows], ['30', '10', '20'])
            self.assertEqual(rows[1]['text'], '[无文本消息]')
            direct = api.dispatch('surrounding', {'messages':[rows[1]], 'before':1, 'after':1})
            native = api.api_tool('message_context', native_args('message_context',
                source_ids=[rows[1]['evidence_id']], before=1, after=1))
            self.assertEqual([r['text'] for r in direct], ['before', 'after'])
            self.assertEqual([r['text'] for r in native['items']], ['before', 'after'])


class RequiredRetrievalTests(unittest.TestCase):
    def test_legacy_disabled_profiles_search_with_required_pipeline(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            demo.initialize(root)
            state = demo.demo_state(root)
            web.save_llm_config(state.llm_config, {name:{'enabled':False, 'api_key':'fixture'}
                                                 for name in ('embedding','rerank')})
            config = web.load_llm_config(state.llm_config)
            web.update_qa_search_db_incremental(state)
            with sqlite3.connect(state.qa_search_db) as conn:
                conn.row_factory = sqlite3.Row
                row = web.qa_item_from_search_row(conn.execute('SELECT * FROM messages LIMIT 1').fetchone())
            prepare = Mock()
            with patch.object(web, 'search_qa_search_db', return_value=([row], {'semantic':{}, 'rerank_used':True})) as search:
                runtime = create_platform_runtime(state, {'since':0,'until':2000000000,'task':'Test'},
                    QA_LIMITS, config, 'qa', lambda:None, threading.Lock(), prepare=prepare)
                self.addCleanup(runtime.api.close)
                result = runtime.api.api_tool('search', {'query':'test','chat_ids':[], 'person_ids':[], 'size':20})
                self.assertTrue(result['items'])
                self.assertIs(prepare.call_args.args[1], True)
                search.assert_called_once()

    def test_search_preflights_both_services_before_index_or_network_work(self):
        with tempfile.TemporaryDirectory() as directory:
            demo.initialize(Path(directory))
            state = demo.demo_state(Path(directory))
            config = web.load_llm_config(state.llm_config)
            prepare = Mock()
            with patch.object(web, 'resolve_llm_api_key', return_value=''), \
                 patch.object(web, 'call_embeddings') as embed, patch.object(web, 'call_chat_payload') as model:
                runtime = create_platform_runtime(state, {'since':0,'until':2000000000,'task':'Test'},
                    QA_LIMITS, config, 'qa', lambda:None, threading.Lock(), prepare=prepare)
                self.addCleanup(runtime.api.close)
                with self.assertRaisesRegex(ValueError, '模型配置'):
                    runtime.api.api_tool('search', {'query':'test','chat_ids':[], 'person_ids':[], 'size':20})
                prepare.assert_not_called()
                embed.assert_not_called()
                model.assert_not_called()


if __name__ == '__main__': unittest.main()
