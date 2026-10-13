"""Both orchestrators use identical data execution and evidence boundaries."""
import copy
import json
import sqlite3
import tempfile
import threading
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from tests.test_code_skills import package
from tests.test_qa_agent import decision
from wechat_agent import web
from wechat_agent.code_skill_service import synthetic_api
from wechat_agent.platform_api import PlatformAPI
from wechat_agent.platform_data_tools import DATA_TOOLS
from wechat_agent.platform_tools import action_from_arguments, execute_action, FUNCTION_TOOLS
from wechat_agent.platform_runtime import create_platform_runtime, PlatformToolSession, PlatformModel
from wechat_agent.qa_agent import run_conversation_agent


def native_args(name, **changes):
    defaults = dict(offset=0, size=60, chat_id='alice', chat_ids=['alice','bob'],
                    since=None, until=None, latest=1, query='internship', min_similarity=.25,
                    sql='SELECT * FROM messages', params=[], source_ids=[], before=3, after=3,
                    max_gap_minutes=30)
    defaults.update(changes)
    return {key:defaults[key] for key in DATA_TOOLS[name][3]}


class UnifiedAPITests(unittest.TestCase):
    def setUp(self):
        self.api = synthetic_api(package())

    def test_shared_class_and_catalog(self):
        self.assertIsInstance(self.api, PlatformAPI)
        names = {t['function']['name'] for t in FUNCTION_TOOLS}
        self.assertTrue(set(DATA_TOOLS)<=names)
        self.assertEqual(self.api.api_tools(), FUNCTION_TOOLS)

    def test_raw_native_and_python_read_same_rows_and_citations(self):
        self.api.api_tool('read_chats', native_args('read_chats'))
        native = self.api.api_tool('read_messages', native_args('read_messages'))
        direct = self.api.dispatch('messages', dict(chat_id='alice', latest=1))
        self.assertEqual(native['items'][0]['text'], direct[0]['text'])
        self.assertEqual(native['items'][0]['reference'], self.api.api_cite(direct)[0]['reference'])
        self.assertFalse(native['complete'])
        self.assertEqual(self.api.model_calls, 0)
        self.assertEqual(self.api.embedding_calls, 0)

    def test_batch_read_identity_and_pagination(self):
        self.api.api_tool('read_chats', native_args('read_chats'))
        first = self.api.api_tool('read_messages_batch', native_args('read_messages_batch',size=1))
        second = self.api.api_tool('read_messages_batch', native_args('read_messages_batch',size=1,offset=first['next_offset']))
        self.assertEqual(first['matched'],2)
        self.assertEqual(second['items'][0]['sender_username'],'self')
        self.assertIsNone(second['next_offset'])

    def test_hidden_pages_are_not_available_as_model_identities(self):
        self.api.api_tool('read_chats',native_args('read_chats',size=1))
        with self.assertRaisesRegex(ValueError,'已返回的会话ID'):
            self.api.api_tool('read_messages',native_args('read_messages',chat_id='bob'))
        result=self.api.api_tool('query_messages',native_args('query_messages',size=1))
        self.assertEqual(len(self.api.ledger),1)
        self.assertEqual(len(self.api.available_sources()),1)
        self.assertEqual(result['next_offset'],1)

    def test_python_reads_can_feed_native_tools_without_rediscovery(self):
        self.api.dispatch('chats',{})
        result=self.api.api_tool('read_messages',native_args('read_messages'))
        self.assertEqual(result['items'][0]['chat_id'],'alice')
        rows=self.api.dispatch('messages',{'chat_id':'bob'})
        self.assertIn('evidence_id',rows[0])
        result=self.api.api_tool('message_context',native_args('message_context',source_ids=[rows[0]['evidence_id']]))
        self.assertEqual(result['items'],[])

    def test_sql_native_and_python_same_snapshot(self):
        sql = 'SELECT * FROM messages WHERE text LIKE ?'
        args = native_args('query_messages',sql=sql, params=['%internship%'])
        native = self.api.api_tool('query_messages', args)
        direct = self.api.dispatch('query',dict(sql=sql,params=['%internship%']))
        self.assertEqual([r['text'] for r in native['items']], [r['text'] for r in direct])
        self.assertEqual(native['items'][0]['index_row_id'], direct[0]['id'])
        self.assertEqual(native['items'][0]['evidence_role'],'primary')

    def test_aggregate_is_explicit_summary_not_fabricated_message(self):
        result = self.api.api_tool('query_messages',native_args('query_messages',sql='SELECT count(*) AS n FROM messages'))
        item = result['items'][0]
        self.assertEqual(item['evidence_role'],'statistics')
        self.assertEqual(item['chat_type'],'index_summary')
        self.assertEqual(json.loads(item['text'])['rows'],[{'n':5}])
        result = self.api.api_tool('query_messages',native_args('query_messages',
            sql="SELECT id,chat_id,timestamp,'invented' AS text FROM messages"))
        self.assertTrue(all(r['evidence_role']=='statistics' for r in result['items']))

    def test_sql_scope_before_query_and_no_mutation_of_boundary(self):
        original = copy.deepcopy(self.api.context)
        result = self.api.api_tool('query_messages',native_args('query_messages',since=203,until=204))
        self.assertEqual([r['timestamp'] for r in result['items']],[203,204])
        self.assertEqual(self.api.context, original)
        all_rows = self.api.api_tool('query_messages',native_args('query_messages'))
        self.assertEqual(all_rows['matched'],5)
        for since,until in ((0,1000),(100,1001),(500,400),(float('nan'),800)):
            with self.subTest(since=since,until=until),self.assertRaises(ValueError):
                self.api.api_tool('query_messages',native_args('query_messages',since=since,until=until))

    def test_sql_native_denies_writes_files_and_permissions(self):
        for sql in ('DELETE FROM messages','PRAGMA database_list',"ATTACH '/tmp/private' AS p",'SELECT * FROM sqlite_master'):
            with self.subTest(sql=sql),self.assertRaises((ValueError,sqlite3.Error)):
                self.api.api_tool('query_messages',native_args('query_messages',sql=sql))
        self.api.limits['permissions'] = ['messages.read']
        with self.assertRaises(ValueError): self.api.api_tool('query_messages',native_args('query_messages'))
        self.assertIn('read_messages',{t['function']['name'] for t in self.api.api_tools()})
        self.assertNotIn('query_messages',{t['function']['name'] for t in self.api.api_tools()})

    def test_native_rejects_unknown_ids_invalid_scalars_and_extra_fields(self):
        cases = [('read_messages',dict(chat_id='invented')),
                 ('read_messages',dict(latest=True)),('read_chats',dict(size=61)),
                 ('query_messages',dict(params=[{'evil':'x'}])),
                 ('semantic_search',dict(min_similarity=float('nan')))]
        for name,changes in cases:
            with self.subTest(name=name,changes=changes),self.assertRaises(ValueError):
                self.api.api_tool(name,native_args(name,**changes))
        with self.assertRaises(ValueError):
            self.api.api_tool('read_chats',dict(native_args('read_chats'),unexpected=True))

    def test_semantic_uses_same_backend_and_budget(self):
        backend = Mock(wraps=self.api.backends['semantic'])
        self.api.backends['semantic'] = backend
        result = self.api.api_tool('semantic_search',native_args('semantic_search',size=2))
        self.assertEqual(result['matched'],5)
        self.assertEqual(result['next_offset'],2)
        self.assertFalse(result['complete'])
        backend.assert_called_once_with('internship',.25)
        self.assertEqual(self.api.embedding_calls,1)
        self.assertEqual(self.api.model_calls,0)

    def test_context_uses_verified_seed_and_keeps_narrower_bounds(self):
        result = self.api.api_tool('query_messages',native_args('query_messages',since=202,until=202))
        seed = result['items'][0]
        neighbours = self.api.api_messages('alice')
        self.api.backends['surrounding'] = Mock(return_value=neighbours)
        result = self.api.api_tool('message_context',native_args('message_context',source_ids=[seed['evidence_id']]))
        self.assertEqual(result['items'],[])
        self.api.backends['surrounding'].assert_called_once()

    def test_session_does_not_mutate_plan_and_native_sources_match_skill(self):
        session = PlatformToolSession(SimpleNamespace(since_ts=100),SimpleNamespace(api=self.api))
        plan = self.api.tool_plan()
        original = copy.deepcopy(plan)
        action = action_from_arguments('query_messages',native_args('query_messages'),[],[],plan)
        result = execute_action(session,plan,action,[])
        self.assertEqual(result['matched'],5)
        self.assertEqual(plan,original)


class UnifiedAgentRecoveryTests(unittest.TestCase):
    def test_graph_uses_data_tools_and_restores_paid_receipt_and_ledger(self):
        class Interrupted(BaseException): pass
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'qa.db'
            api = synthetic_api(package())
            session = PlatformToolSession(SimpleNamespace(since_ts=100),SimpleNamespace(api=api))
            choose = Mock(side_effect=[decision('semantic_search',**native_args('semantic_search')),
                                       decision('finish')])
            def stop(diag,sources):
                if diag.get('trace'): raise Interrupted()
            args = ('internships',[],[],session,choose,Mock(return_value={'paragraphs':[]} ),Mock())
            with self.assertRaises(Interrupted):
                run_conversation_agent(*args,stop,lambda:None,now=datetime.fromtimestamp(1000,timezone.utc),checkpoint_path=path)
            fresh = synthetic_api(package())
            fresh.backends['semantic'] = Mock(side_effect=AssertionError('paid call repeated'))
            recovered = PlatformToolSession(SimpleNamespace(since_ts=100),SimpleNamespace(api=fresh))
            result,sources,_ = run_conversation_agent('internships',[],[],recovered,choose,
                Mock(return_value={'paragraphs':[]}),Mock(),Mock(),lambda:None,
                now=datetime.fromtimestamp(2000,timezone.utc),checkpoint_path=path,resume=True)
            self.assertEqual(len(sources),5)
            self.assertEqual(fresh.embedding_calls,1)
            self.assertEqual(len(fresh.ledger),5)
            fresh.backends['semantic'].assert_not_called()
            self.assertEqual(len(fresh.api_cite([sources[0]])),1)


class SharedRuntimeTests(unittest.TestCase):
    def test_qa_http_uses_actual_shared_runtime_for_sql_and_answer(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            state=web.AppState(**{k:root/k for k in ('db_storage','decrypted','keys','media_root','voice_cache','llm_config','qa_store','qa_index_cache','qa_search_db')})
            state.account='self'
            fixture=synthetic_api(package())
            from wechat_agent.platform_api import COLUMNS
            with sqlite3.connect(state.qa_search_db) as conn:
                conn.execute('CREATE TABLE messages ('+','.join(c+(' INTEGER' if c in ('id','timestamp') else ' TEXT') for c in COLUMNS)+')')
                conn.executemany('INSERT INTO messages VALUES('+','.join('?' for _ in COLUMNS)+')',
                    [[r.get(c) for c in COLUMNS] for r in fixture.backends['indexed']()])
            config={'qa':{'model':'fixture','base_url':'https://example.test','api_key':'fixture'},'embedding':{}}
            responses=[decision('query_messages',**native_args('query_messages',sql='SELECT * FROM messages WHERE chat_id=?',params=['alice'])),
                       decision('finish'),
                       {'choices':[{'finish_reason':'stop','message':{'content':json.dumps({'paragraphs':[
                           {'kind':'answer','text':'Alice asked about the schedule.','source_refs':[1]}]})}}]}]
            with patch.object(web,'load_llm_config',return_value=config), \
                 patch.object(web,'load_or_build_qa_index',return_value={'message_count':5}), \
                 patch.object(web,'qa_search_db_status',return_value={'ready':True}), \
                 patch.object(web,'effective_llm_profile',return_value={}), \
                 patch.object(web,'call_chat_payload',side_effect=responses) as paid, \
                 patch.object(web,'build_or_update_qa_semantic_index',side_effect=AssertionError('SQL must not embed')):
                with patch.object(web.threading.Thread,'start'):
                    handler_class=web.make_handler(state)
                handler=handler_class.__new__(handler_class)
                events=[]
                handler.run_qa_stream({'question':'What did Alice ask?','conversation_id':'fixture'},
                                     lambda event,**data:events.append((event,data)))
            self.assertEqual(events[-1][0],'done',events[-1])
            self.assertEqual(paid.call_count,3)
            stored=web.get_qa_conversation(state.qa_store,'fixture')['messages'][-1]
            self.assertEqual(stored['sources'][0]['chat_id'],'alice')
            self.assertEqual(stored['sources'][0]['evidence_role'],'primary')
            self.assertEqual(stored['retrieval']['trace'][0]['parameters']['tool'],'query_messages')

    def test_same_factory_handles_qa_and_task_raw_reads_without_models(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            state=web.AppState(**{k:root/k for k in ('db_storage','decrypted','keys','media_root','voice_cache','llm_config','qa_store','qa_index_cache','qa_search_db')})
            state.account='self'
            state.chats=[{'id':'alice','chat':'alice','title':'Alice','type':'private','last_ts':201,'shards':[{'db':'x','table':'messages'}]}]
            context={'task':'last message','since':100,'until':1000,'account':'self','dry_run':False}
            fixture=synthetic_api(package())
            config={'qa':{'model':'qa'},'task':{'model':'task'},'embedding':{}}
            with patch.object(web,'effective_llm_profile',return_value={}), \
                 patch.object(web,'collect_qa_items_for_chat',return_value=fixture.api_messages('alice')) as reader, \
                 patch.object(web,'call_chat_payload',side_effect=AssertionError('unexpected model')):
                for name in ('qa','task'):
                    runtime=create_platform_runtime(state,context,fixture.limits,config,name,lambda:None,threading.Lock())
                    self.assertIsInstance(runtime.api,PlatformAPI)
                    rows=runtime.api.dispatch('messages',{'chat_id':'alice','latest':1})
                    self.assertEqual(rows[0]['sender_username'],'alice')
                    self.assertTrue(reader.call_args.kwargs['include_empty'])
                    self.assertEqual(runtime.model.profile['model'],name)

    def test_model_boundary_uses_profile_without_exposing_key(self):
        payload={'choices':[{'finish_reason':'stop','message':{'content':'{"ok":true}'}}],'usage':{'total_tokens':4}}
        model=PlatformModel({'model':'fixture','api_key':'synthetic-key'},{},lambda:None)
        with patch.object(web,'call_chat_payload',return_value=payload) as send:
            value,usage=model.llm([{'role':'user','content':'test'}],schema={'type':'object'})
        self.assertEqual(value,{'ok':True})
        self.assertEqual(usage['total_tokens'],4)
        self.assertNotIn('synthetic-key',json.dumps(value))
        self.assertEqual(send.call_args.kwargs['response_format']['json_schema']['name'],'platform_result')


if __name__=='__main__': unittest.main()
