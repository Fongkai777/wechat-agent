import copy
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from wechat_agent.goals import GoalStore, GoalConflict
from wechat_agent import web


class SkillIntegrationTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        self.state = web.AppState(**{key: root/key for key in (
            'db_storage', 'decrypted', 'keys', 'media_root', 'voice_cache',
            'llm_config', 'qa_store', 'qa_index_cache', 'qa_search_db')})
        self.state.account = 'me'
        with patch.object(web.threading.Thread, 'start'):
            self.handler_class = web.make_handler(self.state)
        self.handler = self.handler_class.__new__(self.handler_class)

    def test_missing_or_retired_skill_never_executes_legacy_runtime(self):
        for skill in (None, {'schema_version': 1}, {'schema_version': 2}):
            goal = {'started_at': 100000, 'prompt': '待回', 'skill': skill,
                    'skill_version': 1 if skill else 0}
            with patch.object(web, 'call_chat_payload', side_effect=AssertionError('No API')):
                with self.assertRaises(ValueError):
                    self.handler_class.goal_scheduler.execute(goal, Mock(), lambda: False)

    def test_generation_persists_only_draft_and_uses_task_profile(self):
        from tests.test_code_skills import package
        generated = package()
        payload = {'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps(generated)}}],
                   'usage': {'total_tokens': 10}}
        assessment={'choices':[{'finish_reason':'stop','message':{'content':json.dumps({'summary':'可实现','requirements':[
            {'need':'检查待回','status':'supported','capabilities':['messages.read','python.compute'],'reason':'本地判断'}]})}}]}
        emit = Mock()
        with patch.object(web, 'load_llm_config', return_value={'task': {'model': 'task-model'}}), \
             patch.object(web, 'resolve_llm_api_key', return_value='fixture'), \
             patch.object(web, 'call_chat_payload', side_effect=[assessment,payload]) as call, \
             patch('wechat_agent.code_skill_service.validate_code_skill',return_value={'passed':True}):
            self.handler.run_skill_generation({'prompt': '待回'}, emit)
        store = self.handler_class.goal_scheduler.store
        self.assertEqual(store.list(), [])
        stored = store.skill_drafts()[0]['skill']
        self.assertEqual(stored['schema_version'],3)
        self.assertTrue(any(f['path']=='workflow.py' for f in stored['files']))
        self.assertEqual(call.call_args.args[0]['model'], 'task-model')
        self.assertIn({'task': '待回'},[json.loads(m['content']) for m in call.call_args.args[2] if m['content'].startswith('{')])
        self.assertEqual(call.call_count,2)
        self.assertEqual(emit.call_args.args[0], 'done')


class TailTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.root=Path(temp.name)
        self.rec={'id':'alice','title':'Alice','type':'private','last_ts':100,'shards':[{'db':'message.db','table':'Msg'}]}
        self.state=SimpleNamespace(decrypted=self.root, account='me', contacts={}, since_ts=None, chats=[self.rec], sync_error='',last_synced_at='2026-10-08')
        self.conn=sqlite3.connect(self.root/'message.db'); self.addCleanup(self.conn.close)
        self.conn.execute('CREATE TABLE Msg(local_id INTEGER,server_id INTEGER,local_type INTEGER,real_sender_id INTEGER,create_time INTEGER,message_content TEXT)')
        for target, value in [('load_name2id',{1:'alice',2:'me'}),('qa_text_from_raw_message','preview')]:
            p=patch.object(web,target,return_value=value);p.start();self.addCleanup(p.stop)
    def add(self, sender, ts):
        self.conn.execute('INSERT INTO Msg VALUES(1,1,1,?,?,?)',(sender,ts,'hello'));self.conn.commit()
    def tail(self, until=100):
        return web.collect_qa_items_for_chat(self.state,self.rec,since_ts=0,until_ts=until,
            max_items=1,strict=True,include_empty=True,max_text_chars=None)
    def test_last_message_selected_before_sender_filter_even_same_second(self):
        self.add(1,10); self.add(2,10)
        self.assertTrue(self.tail()[0]['mine'])
        self.add(1,20)
        self.assertFalse(self.tail()[0]['mine'])
        self.assertTrue(self.tail(until=15)[0]['mine'])
    def test_time_window_missing_shard_and_unknown_sender(self):
        self.add(8,10)
        self.assertEqual(self.tail()[0]['sender_username'], '8')
        self.rec['shards'].append({'db':'missing','table':'Msg'})
        with self.assertRaises(ValueError): self.tail()
    def test_generic_latest_keeps_empty_physical_message_and_same_second_order(self):
        self.add(1,10); self.add(2,10)
        with patch.object(web,'qa_text_from_raw_message',return_value=''):
            items=web.collect_qa_items_for_chat(self.state,self.rec,since_ts=0,until_ts=100,
                max_items=1,strict=True,include_empty=True,max_text_chars=None)
        self.assertEqual(len(items),1)
        self.assertEqual(items[0]['sender_username'],'me')
        self.assertEqual(items[0]['text'],'[无文本消息]')


if __name__ == '__main__': unittest.main()
