import copy
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from wechat_agent.task_skills import validate_skill, run_skill_graph, generation_messages, parse_generated_skill
from wechat_agent.goals import GoalStore, GoalConflict
from wechat_agent.goal_tools import GoalChatTools
from wechat_agent import web


def skill(**changes):
    return {**{'schema_version': 1, 'name': '待回检查', 'description': '列出末条来自对方的私聊',
               'source': 'private_latest', 'filters': {'sender': 'other', 'keywords': [], 'keyword_match': 'any'},
               'output': {'mode': 'list', 'instruction': ''}}, **changes}


class SkillTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.store = GoalStore(self.root / 'goals.sqlite3')
        self.data = {'title': '待回', 'prompt': '看看忘回的私聊', 'enabled': False, 'range_type': 'recent_days',
                     'range_value': 1, 'range_unit': 'days', 'interval_value': 1, 'interval_unit': 'hours',
                     'skill': skill(), 'skill_prompt': '看看忘回的私聊', 'expected_skill_version': 0}
        self.id = self.store.save(self.data, now=100)

    def run_skill(self, spec=None, items=None, goal=None, completion=None):
        goal = goal or {'prompt': '看看忘回的私聊', 'skill': spec or skill(), 'skill_version': 1, 'started_at': 100000,
                        'range_type': 'recent_days', 'range_value': 1, 'range_unit': 'days'}
        self.invoke = Mock(return_value={'messages': items or [], 'complete': True})
        self.report = Mock()
        self.complete = completion or Mock(side_effect=AssertionError('No model allowed'))
        result = run_skill_graph(goal, self.complete, self.invoke, self.report, lambda: False, 'fixture')
        return json.loads(result)

    def test_untrusted_fields_and_arbitrary_executables_rejected(self):
        for bad in (skill(command='rm -rf /'), skill(source='sql'), skill(schema_version=True), skill(schema_version=2),
                    skill(filters={'sender':'other','keywords':[''],'keyword_match':'any'}),
                    skill(output={'mode':'list','instruction':'do something'})):
            with self.subTest(bad=bad), self.assertRaises(ValueError): validate_skill(bad)
        self.assertEqual(validate_skill(json.dumps(skill())), skill())

    def test_generated_skill_validates_completion_and_reads_no_chat(self):
        messages = generation_messages('看看忘回的私聊')
        self.assertEqual(json.loads(messages[-1]['content']), {'task':'看看忘回的私聊'})
        self.assertEqual(parse_generated_skill({'choices':[{'finish_reason':'stop','message':{'content':json.dumps(skill())}}]}), skill())
        with self.assertRaises(ValueError): parse_generated_skill({'choices':[{'finish_reason':'length'}]})

    def test_local_filter_lists_all_incoming_without_model_or_type_exclusions(self):
        items = [{'chat_id':str(i), 'chat_title':str(i), 'mine':False, 'sender_known':True,
                  'text':'[表情包]' if i % 2 else '[通话]', 'timestamp':i} for i in range(70)]
        items += [{'mine':True,'sender_known':True}, {'mine':False,'sender_known':False}]
        result = self.run_skill(items=items)
        self.assertEqual(len(result['items']), 70)
        self.complete.assert_not_called()
        self.assertIn('1 条', result['notice'])
        self.assertEqual(self.report.call_args.args[0]['usage']['total_tokens'], 0)

    def test_keywords_and_sender_are_editable_and_validated_before_execution(self):
        spec = skill(source='all_messages', filters={'sender':'any','keywords':['Intern','Singapore'],'keyword_match':'all'})
        result = self.run_skill(spec=spec, items=[{'text':'Singapore INTERN','timestamp':1},{'text':'intern','timestamp':2}])
        self.assertEqual(len(result['items']), 1)
        self.assertIn('字面匹配', result['notice'])

    def test_only_filtered_candidates_are_sent_to_model(self):
        spec = skill(output={'mode':'model','instruction':'推荐回复草稿'})
        complete = Mock(return_value={'choices':[{'finish_reason':'stop','message':{'content':json.dumps({
            'summary':'一条待回', 'items':[{'title':'Alice','detail':'消息','suggestion':'草稿：好的','source_refs':[1]}], 'notice':None})}}], 'usage':{'total_tokens':12}})
        self.run_skill(spec, [{'text':'CANDIDATE','mine':False,'sender_known':True}, {'text':'REPLIED_ALREADY','mine':True,'sender_known':True}], completion=complete)
        complete.assert_called_once()
        self.assertIn('CANDIDATE', str(complete.call_args))
        self.assertNotIn('REPLIED_ALREADY', str(complete.call_args))
        self.assertIsNone(complete.call_args.args[1])

    def test_save_keeps_versions_and_drafts_do_not_change_saved_task(self):
        self.store.save_skill_draft(self.id, 'new', skill(name='新草稿'), 'fixture', {'total_tokens':1})
        self.assertEqual(self.store.list()[0]['skill_version'], 1)
        self.assertEqual(len(self.store.skill_drafts()), 1)
        updated = {**self.data,'id':self.id,'skill':skill(name='修改版'),'expected_skill_version':1}
        self.store.save(updated)
        self.assertEqual([v['version'] for v in self.store.skill_versions(self.id)], [2,1])
        with self.assertRaises(GoalConflict): self.store.save(updated)
        with self.assertRaises(ValueError): self.store.save({**updated,'prompt':'changed'})

    def test_run_keeps_immutable_skill_version_and_can_resume_without_model(self):
        goal = self.store.claim_manual(self.id, now=100000)
        goal['checkpoint_path'] = str(self.store.checkpoint_path(goal['run_id']))
        result = self.run_skill(goal=goal)
        self.store.finish(goal, 'failed', error='history write interrupted')
        self.store.save({**self.data,'id':self.id,'skill':skill(name='第二版'),'expected_skill_version':1})
        resumed = self.store.claim_resume(self.id, goal['run_id'])
        self.assertEqual(resumed['skill_version'],1)
        self.assertEqual(self.run_skill(goal=resumed),result)
        self.invoke.assert_not_called()
        self.assertEqual(self.store.history(self.id)[0]['skill']['name'], '待回检查')


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

    def test_local_skill_execution_requires_neither_model_configuration_nor_key(self):
        self.state.chats = [{'id': 'alice', 'type': 'private', 'last_ts': 100000}]
        goal = {'started_at': 100000, 'prompt': '待回', 'skill': skill(), 'skill_version': 1,
                'range_type': 'recent_days', 'range_value': 1, 'range_unit': 'days'}
        with patch.object(web, 'load_llm_config', side_effect=AssertionError('No model config')), \
             patch.object(web, 'resolve_llm_api_key', side_effect=AssertionError('No key')), \
             patch.object(web, 'collect_skill_chat_tail', return_value=[{
                 'chat_id': 'alice', 'chat_title': 'Alice', 'sender_username': 'alice',
                 'mine': False, 'text': 'hello', 'timestamp': 100000}]), \
             patch.object(web, 'call_chat_payload', side_effect=AssertionError('No API')):
            result = self.handler_class.goal_scheduler.execute(goal, Mock(), lambda: False)
        self.assertEqual(len(json.loads(result)['items']), 1)

    def test_generation_persists_only_draft_and_uses_task_profile(self):
        from test_code_skills import package
        generated = package()
        payload = {'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps(generated)}}],
                   'usage': {'total_tokens': 10}}
        emit = Mock()
        with patch.object(web, 'load_llm_config', return_value={'task': {'model': 'task-model'}}), \
             patch.object(web, 'resolve_llm_api_key', return_value='fixture'), \
             patch.object(web, 'call_chat_payload', return_value=payload) as call, \
             patch('wechat_agent.code_skill_service.validate_code_skill',return_value={'passed':True}):
            self.handler.run_skill_generation({'prompt': '待回'}, emit)
        store = self.handler_class.goal_scheduler.store
        self.assertEqual(store.list(), [])
        stored = store.skill_drafts()[0]['skill']
        self.assertEqual(stored['schema_version'],3)
        self.assertTrue(any(f['path']=='workflow.py' for f in stored['files']))
        self.assertEqual(call.call_args.args[0]['model'], 'task-model')
        self.assertEqual(json.loads(call.call_args.args[2][-1]['content']), {'task': '待回'})
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
        return web.collect_skill_chat_tail(self.state,self.rec,0,until,lambda:None)
    def test_last_message_selected_before_sender_filter_even_same_second(self):
        self.add(1,10); self.add(2,10)
        self.assertTrue(self.tail()[0]['mine'])
        self.add(1,20)
        self.assertFalse(self.tail()[0]['mine'])
        self.assertTrue(self.tail(until=15)[0]['mine'])
    def test_time_window_missing_shard_and_unknown_sender(self):
        self.add(8,10)
        self.assertFalse(self.tail()[0]['sender_known'])
        self.rec['shards'].append({'db':'missing','table':'Msg'})
        with self.assertRaises(ValueError): self.tail()
    def test_skill_latest_uses_tail_reader_not_whole_chat_reader(self):
        full=Mock(side_effect=AssertionError('full scan'))
        tools=GoalChatTools(self.state,full,Mock(),lambda:False,latest_reader=web.collect_skill_chat_tail)
        self.add(1,10)
        data=tools('skill_read',{'source':'private_latest'},0,100)
        self.assertEqual(len(data['messages']),1)
        full.assert_not_called()
        self.state.account=''
        with self.assertRaises(ValueError): tools('skill_read',{'source':'private_latest'},0,100)

    def test_generic_latest_keeps_empty_physical_message_and_same_second_order(self):
        self.add(1,10); self.add(2,10)
        with patch.object(web,'qa_text_from_raw_message',return_value=''):
            items=web.collect_qa_items_for_chat(self.state,self.rec,since_ts=0,until_ts=100,
                max_items=1,strict=True,include_empty=True,max_text_chars=None)
        self.assertEqual(len(items),1)
        self.assertEqual(items[0]['sender_username'],'me')
        self.assertEqual(items[0]['text'],'[无文本消息]')


if __name__ == '__main__': unittest.main()
