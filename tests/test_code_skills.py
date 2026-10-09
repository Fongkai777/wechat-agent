import copy
import json
import os
import sqlite3
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

from wechat_agent.code_skill_packages import validate_package
from wechat_agent.code_skill_runtime import run_package
from wechat_agent.code_skill_service import synthetic_api, validate_code_skill, run_code_skill
from wechat_agent.goals import GoalStore
from wechat_agent.code_skill_web import prepare_code_skill_indexes


def package(code="def run(api):\n return {'summary':'Done','items':[],'notice':None}"):
    return {'schema_version':3,'name':'Test','description':'Synthetic test', 'files':[
        {'path':'SKILL.md','content':'Synthetic test'},
        {'path':'manifest.json','content':json.dumps({'api_version':'1','entrypoint':'workflow.py:run',
          'permissions':['messages.read','index.query','embedding','llm','state.read','state.write'],
          'timeout_seconds':10,'max_api_calls':30,'max_llm_calls':2,'max_embedding_calls':2})},
        {'path':'workflow.py','content':code},
        {'path':'test_skill.py','content':'def test():\n assert True'}]}


class CodeAPITests(unittest.TestCase):
    def setUp(self): self.api=synthetic_api(package())

    def test_sql_window_and_custom_rule(self):
        rows=self.api.dispatch('query',{'sql':'''WITH ranked AS (SELECT *,row_number() OVER
          (PARTITION BY chat_id ORDER BY timestamp DESC,id DESC) AS position FROM messages WHERE chat_type='private')
          SELECT * FROM ranked WHERE position=1 AND sender_username!=?''','params':['self']})
        self.assertEqual([r['chat_id'] for r in rows],['alice'])
        self.assertEqual(self.api.dispatch('cite',{'messages':rows})[0]['reference'],1)

    def test_sql_denies_writes_and_host_access(self):
        for sql in ['DELETE FROM messages','PRAGMA database_list',"ATTACH '/tmp/leak' AS x",
                    'SELECT * FROM sqlite_master',"SELECT load_extension('/tmp/a')",'CREATE TABLE bad(x)']:
            with self.subTest(sql=sql), self.assertRaises(Exception): self.api.dispatch('query',{'sql':sql})

    def test_sql_like_is_available(self):
        rows=self.api.dispatch('query',{'sql':"SELECT * FROM messages WHERE lower(text) LIKE ?",'params':['%internship%']})
        self.assertEqual(len(rows),1)

    def test_cannot_fabricate_or_modify_evidence(self):
        row=self.api.dispatch('messages',{'chat_id':'alice'})[0]
        row['text']='fabricated'
        with self.assertRaises(ValueError): self.api.dispatch('cite',{'messages':[row]})
        with self.assertRaises(ValueError): self.api.dispatch('surrounding',{'messages':[row]})

    def test_cannot_expand_scope(self):
        with self.assertRaises(ValueError): self.api.dispatch('messages',{'chat_id':'alice','since':0})

    def test_permissions_and_budgets(self):
        self.api.limits['permissions']=[]
        with self.assertRaises(ValueError): self.api.dispatch('query',{'sql':'SELECT * FROM messages'})
        self.api.limits['permissions']=['llm']; self.api.limits['max_llm_calls']=0
        with self.assertRaises(ValueError): self.api.dispatch('llm',{'messages':[{'role':'user','content':'Hi'}]})

    def test_path_and_entrypoint_validation(self):
        for name in ('../leak.py','/tmp/leak.py','_platform_worker.py'):
            p=package(); p['files'].append({'path':name,'content':'pass'})
            with self.assertRaises(ValueError): validate_package(p)


class CodeIndexPreparationTests(unittest.TestCase):
    def setUp(self):
        from wechat_agent import web
        self.state=object()
        self.progress=Mock()
        self.usage=Mock()
        self.text=self.enterContext(patch.object(web,'qa_search_db_status',return_value={'ready':True}))
        self.semantic=self.enterContext(patch.object(web,'qa_embedding_index_status',return_value={'ready':True}))
        self.update_text=self.enterContext(patch.object(web,'update_qa_search_db_incremental'))
        self.update_semantic=self.enterContext(patch.object(web,'build_or_update_qa_semantic_index',return_value={}))

    # Python 3.9 unittest does not provide enterContext.
    def enterContext(self, manager):
        value=manager.__enter__()
        self.addCleanup(manager.__exit__,None,None,None)
        return value

    def prepare(self, semantic=False):
        prepare_code_skill_indexes(self.state,semantic,self.progress,self.usage)

    def test_ready_indexes_are_reused(self):
        self.prepare(True)
        self.update_text.assert_not_called()
        self.update_semantic.assert_not_called()

    def test_sql_repairs_missing_or_stale_text_without_embedding(self):
        for initial in ({'ready':False},{'ready':True,'soft_stale':True}):
            with self.subTest(initial=initial):
                self.text.side_effect=[initial,{'ready':True}]
                self.prepare()
        self.assertEqual(self.update_text.call_count,2)
        self.semantic.assert_not_called()
        self.update_semantic.assert_not_called()

    def test_semantic_repairs_dependencies_in_order_and_counts_usage_once(self):
        order=[]
        self.text.side_effect=[{'ready':False},{'ready':True}]
        self.semantic.side_effect=[{'ready':True,'pending_message_count':2},{'ready':True}]
        self.update_text.side_effect=lambda *args:order.append('text')
        def update(state,full,progress):
            self.assertFalse(full)
            order.append('semantic')
            progress('progress',message='batch 1',usage_totals={'total_tokens':10})
            progress('progress',message='batch 2',usage_totals={'total_tokens':10})
            return {'usage_totals':{'total_tokens':25}}
        self.update_semantic.side_effect=update
        self.prepare(True)
        self.assertEqual(order,['text','semantic'])
        self.assertEqual(sum(c.args[0]['total_tokens'] for c in self.usage.call_args_list),25)
        self.assertTrue(self.progress.called)

    def test_update_failure_does_not_continue_to_semantic(self):
        self.text.return_value={'ready':False}
        self.update_text.side_effect=OSError('disk I/O error')
        with self.assertRaisesRegex(OSError,'disk I/O'):
            self.prepare(True)
        self.update_semantic.assert_not_called()

    def test_failed_readiness_recheck_is_not_reported_as_success(self):
        self.text.return_value={'ready':False}
        with self.assertRaisesRegex(RuntimeError,'全文索引自动更新后仍不可用'):
            self.prepare()
        self.text.return_value={'ready':True}
        self.semantic.return_value={'ready':True,'pending_message_count':1}
        with self.assertRaisesRegex(RuntimeError,'语义索引自动更新后仍不可用'):
            self.prepare(True)

    def test_cancellation_stops_before_update(self):
        from wechat_agent.goals import GoalCancelled
        self.text.return_value={'ready':False}
        self.progress.side_effect=GoalCancelled()
        with self.assertRaises(GoalCancelled): self.prepare()
        self.update_text.assert_not_called()


@unittest.skipUnless(os.environ.get('TEST_CODE_SKILL_SANDBOX')=='1','Explicit sandbox integration test')
class CodeSandboxTests(unittest.TestCase):
    def test_query_waits_for_maintenance_builds_missing_index_and_continues(self):
        from wechat_agent import web
        from wechat_agent.code_skill_api import COLUMNS
        from wechat_agent.code_skill_web import execute_code_goal
        with tempfile.TemporaryDirectory() as root:
            root=Path(root)
            state=web.AppState(**{key:root/key for key in ('db_storage','decrypted','keys','media_root','voice_cache','llm_config','qa_store','qa_index_cache','qa_search_db')})
            state.account='self'
            p=package('''def run(api):
    first=api.query('SELECT count(*) AS n FROM messages')
    second=api.query('SELECT count(*) AS n FROM messages')
    return {'summary':str(first[0]['n'])+':'+str(second[0]['n']),'items':[],'notice':None}
''')
            store=GoalStore(root/'goals.db')
            validate_code_skill(p,'Test',store)
            goal={'id':'fixture','prompt':'Test','skill':json.dumps(p),'started_at':1000,
                  'range_type':'recent_days','range_value':1,'range_unit':'days'}
            status={'ready':False}
            def update(state,progress):
                progress('progress',message='build fixture index')
                with sqlite3.connect(state.qa_search_db) as conn:
                    conn.execute('CREATE TABLE messages ('+','.join(c+' '+('INTEGER' if c in ('id','timestamp') else 'TEXT') for c in COLUMNS)+')')
                    conn.execute("INSERT INTO messages(id,chat_id,timestamp,text) VALUES(1,'alice',500,'hello')")
                status['ready']=True
            maintenance=threading.Lock()
            maintenance.acquire()
            events=[]
            def report(data):
                events.append(data.get('progress',''))
                if '等待正在进行' in data.get('progress',''):
                    maintenance.release()
            with patch.object(web,'load_llm_config',return_value={'task':{},'embedding':{}}), \
                 patch.object(web,'effective_llm_profile',return_value={}), \
                 patch.object(web,'qa_search_db_status',side_effect=lambda state:dict(status)), \
                 patch.object(web,'update_qa_search_db_incremental',side_effect=update) as build, \
                 patch.object(web,'build_or_update_qa_semantic_index',side_effect=AssertionError('SQL must not embed')), \
                 patch.object(web,'call_chat_payload',side_effect=AssertionError('Unexpected model request')):
                result=execute_code_goal(state,store,goal,report,lambda:False,maintenance)
            self.assertEqual(json.loads(result)['summary'],'1:1')
            build.assert_called_once()
            self.assertFalse(maintenance.locked())
            self.assertTrue(any('build fixture index' in event for event in events))

    def test_host_preparation_does_not_exhaust_code_timeout(self):
        from wechat_agent import code_skill_runtime
        monotonic=time.monotonic
        elapsed=[0]
        def dispatch(method,args):
            elapsed[0]+=20
            return 'ready'
        with patch.object(code_skill_runtime.time,'monotonic',side_effect=lambda:monotonic()+elapsed[0]):
            result=run_package(package('def run(api):\n return api.log("prepare")'),{},dispatch,
                               preparation_time=lambda:elapsed[0])
        self.assertEqual(result,'ready')

    def test_application_adapter_reads_latest_without_model_or_index(self):
        from wechat_agent import web
        from wechat_agent.code_skill_web import execute_code_goal
        with tempfile.TemporaryDirectory() as root:
            root=Path(root)
            state=web.AppState(**{key:root/key for key in ('db_storage','decrypted','keys','media_root','voice_cache','llm_config','qa_store','qa_index_cache','qa_search_db')})
            state.account='self'
            state.chats=[{'id':'alice','chat':'alice','title':'Alice','type':'private','last_ts':1000,'shards':[{'db':'fixture','table':'Msg'}]}]
            p=package('''def run(api):
    rows=[]
    for chat in api.chats():
        rows.extend(api.messages(chat_id=chat['id'],latest=1))
    refs=api.cite(messages=rows)
    return {'summary':'Found','items':[{'title':r['chat_title'],'detail':r['text'],'suggestion':None,'source_refs':[r['reference']]} for r in refs],'notice':None}
''')
            store=GoalStore(root/'goals.db')
            validate_code_skill(p,'Test',store)
            goal_id=store.save({'title':'Test','prompt':'Test','skill':p,'skill_prompt':'Test',
                'interval_value':1,'interval_unit':'hours','range_type':'recent_days','range_value':1,'range_unit':'days'})
            goal=store.claim_manual(goal_id,now=1000)
            self.assertIsInstance(goal['skill'],str)
            row={'chat_id':'alice','chat_title':'Alice','chat_type':'private','timestamp':1000,'text':'hello',
                 'sender':'Alice Display','sender_username':'alice','local_id':1,'server_id':2,'source_db':'fixture','source_table':'Msg'}
            with patch.object(web,'load_llm_config',return_value={'task':{},'embedding':{}}), \
                 patch.object(web,'effective_llm_profile',return_value={}), \
                 patch.object(web,'collect_qa_items_for_chat',return_value=[row]) as reader, \
                 patch.object(web,'call_chat_payload',side_effect=AssertionError('Unexpected model request')), \
                 patch.object(web,'qa_search_db_status',side_effect=AssertionError('Unexpected index access')):
                for stored_package in (goal['skill'],p):
                    with self.subTest(serialized=isinstance(stored_package,str)):
                        execution_goal={**goal,'skill':stored_package}
                        result=execute_code_goal(state,store,execution_goal,lambda data:None,lambda:False,threading.Lock())
                        self.assertEqual(execution_goal['skill'],p)
                        self.assertEqual(json.loads(result)['items'][0]['detail'],'hello')
                edited=copy.deepcopy(p)
                edited['description']='changed after testing'
                for field,value in [('skill',json.dumps(edited)),('prompt','Changed task')]:
                    with self.subTest(changed=field), self.assertRaisesRegex(ValueError,'请先测试 Skill'):
                        execute_code_goal(state,store,{**goal,field:value},lambda data:None,lambda:False,threading.Lock())
            self.assertEqual(json.loads(result)['items'][0]['detail'],'hello')
            self.assertEqual(reader.call_args.kwargs['max_items'],1)
            self.assertTrue(reader.call_args.kwargs['include_empty'])

    def test_real_sandbox_denies_files_network_writes_and_fork(self):
        with tempfile.TemporaryDirectory() as root:
            secret=Path(root)/'secret'; secret.write_text('private')
            code='''import os, socket
def run(api):
    results=[]
    for operation in [lambda: open(%r).read(), lambda: open('write.txt','w'),
                      lambda: socket.create_connection(('127.0.0.1',8787),timeout=1), os.fork]:
        try:
            operation()
            results.append('ALLOWED')
        except (OSError, PermissionError): results.append('DENIED')
    return results
''' % str(secret)
            self.assertEqual(run_package(package(code),{},lambda *args:None),['DENIED']*4)

    def test_rpc_and_timeout(self):
        self.assertEqual(run_package(package('def run(api):\n return api.log(message="hello")'),{},lambda m,a:a['message']),'hello')
        with self.assertRaises(TimeoutError): run_package(package('def run(api):\n while True: pass'),{},lambda *a:None)

    def test_validate_save_and_require_retest_after_edit(self):
        with tempfile.TemporaryDirectory() as root:
            store=GoalStore(Path(root)/'goals.db'); p=package()
            data={'title':'Test','prompt':'Test','skill':p,'skill_prompt':'Test','interval_value':1,'interval_unit':'hours','range_type':'recent_days','range_value':1,'range_unit':'days'}
            with self.assertRaises(ValueError): store.save(data)
            self.assertTrue(validate_code_skill(p,'Test',store)['passed'])
            data['id']=store.save(data)
            data['skill']['description']='edited'
            with self.assertRaises(ValueError): store.save(data)

    def test_durable_replay_no_duplicate_model_call(self):
        p=package('''def run(api):
    api.llm(messages=[{'role':'user','content':'hello'}])
    api.log(message='after model')
    api.state_set(value={'done':True})
    return {'summary':'Done','items':[],'notice':None}
''')
        with tempfile.TemporaryDirectory() as root:
            goal={'skill':p,'run_id':'test','checkpoint_path':str(Path(root)/'checkpoint.db'),'profile_signature':'model'}
            api=synthetic_api(p)
            def interrupted(message):
                if message=='after model': raise RuntimeError('interrupt')
            with self.assertRaises(RuntimeError): run_code_skill(goal,api,lambda data:interrupted(data['progress']),lambda:False,'test')
            api=synthetic_api(p)
            api.backends['llm']=lambda *args: self.fail('Model called again on recovery')
            goal['resume']=True
            result=run_code_skill(goal,api,lambda data:None,lambda:False,'test')
            self.assertEqual(json.loads(result)['summary'],'Done')
            self.assertEqual(goal['code_skill_state'],{'done':True})


if __name__=='__main__': unittest.main()
