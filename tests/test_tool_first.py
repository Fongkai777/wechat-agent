"""No mandatory planner; independent tool queries under platform boundaries."""
import json
import unittest
from unittest.mock import Mock, patch

from tests import test_qa_agent as fixtures
from tests.test_code_skills import package
from wechat_agent.code_skill_service import synthetic_api
from wechat_agent.qa_agent import run_conversation_agent

NOW, PEOPLE, decision = fixtures.NOW, fixtures.PEOPLE, fixtures.decision


def filters(**values):
    return dict(chat_type='all', chat_keyword='', since=None, until=None, **values)


class ToolFirstTests(unittest.TestCase):
    setUp = fixtures.ToolTests.setUp
    insert = fixtures.ToolTests.insert

    def run_agent(self, complete, question='周一营业吗', history=None):
        with patch('wechat_agent.qa_agent.make_plan', side_effect=AssertionError('planner must not run')):
            return run_conversation_agent(question, history or [], PEOPLE, self.tools, complete,
                Mock(return_value={'paragraphs':[]}), Mock(), Mock(), lambda:None, now=NOW)

    def test_first_call_is_native_search_with_history_not_slot_schema(self):
        self.insert(1, days=400)
        self.hybrid.return_value = ([{'source_db':'message/message_0.db','source_table':'Msg',
            'local_id':'1','timestamp':int((NOW.replace(hour=13)).timestamp())-400*86400}], {})
        complete = Mock(side_effect=[decision('search',query='餐厅 周一'), decision('finish')])
        _, sources, diagnostics = self.run_agent(complete, history=[{'role':'user','content':'青叶食堂如何？'}])
        self.assertEqual(len(sources),1)
        messages, schema = complete.call_args_list[0].args
        self.assertIsNone(schema)
        self.assertIn('tools',complete.call_args_list[0].kwargs)
        request = json.loads(messages[-1]['content'])
        self.assertNotIn('plan',request)
        self.assertEqual(request['conversation_history'][0]['content'],'青叶食堂如何？')
        self.assertEqual(request['current_time'],NOW.isoformat())
        self.assertIsNone(diagnostics['query_plan']['since_ts'])
        self.assertEqual(complete.call_count,2)

    def test_date_search_then_keyword_search_does_not_inherit_date(self):
        self.insert(1); self.insert(2,days=400)
        temporal=filters(); temporal['since']=NOW.replace(hour=12,minute=0).isoformat()
        complete=Mock(side_effect=[decision('timeline',filters=temporal),decision('timeline'),decision('finish')])
        _, sources, diagnostics=self.run_agent(complete)
        self.assertEqual([t['returned'] for t in diagnostics['trace']],[1,2])
        self.assertEqual({s['local_id'] for s in sources},{'1','2'})
        self.assertIsNone(diagnostics['query_plan']['since_ts'])

    def test_identity_discovery_does_not_lock_people_or_autoselect_namesake(self):
        self.insert(1,person='alice'); self.insert(2,person='bob')
        self.conn.execute("UPDATE messages SET sender='小林',person_name='小林'")
        self.conn.commit()
        complete=Mock(side_effect=[decision('find_people',query='小林'),
            decision('timeline',person_ids=['alice']), decision('timeline',person_ids=['bob']),decision('finish')])
        _, sources, diagnostics=self.run_agent(complete,question='看看两个小林说了什么')
        self.assertEqual({s['sender_username'] for s in sources},{'alice','bob'})
        observations=json.loads(complete.call_args_list[1].args[0][-1]['content'])['observations']
        self.assertEqual({p['id'] for p in observations[0]['people']},{'alice','bob'})
        self.assertEqual(diagnostics['query_plan']['people'],[])

    def test_per_call_group_filter_does_not_persist(self):
        self.insert(1,chat='NTU@chatroom'); self.insert(2,chat='other@chatroom')
        scope=filters(); scope.update(chat_type='group',chat_keyword='NTU')
        complete=Mock(side_effect=[decision('list_chats',filters=scope),decision('timeline'),decision('finish')])
        _,sources,_=self.run_agent(complete)
        self.assertEqual({s['chat_id'] for s in sources},{'NTU@chatroom','other@chatroom'})

    def test_context_expansion_preserves_seed_time_filter(self):
        self.insert(1); self.insert(2,person='bob')
        stamp=int(NOW.replace(hour=13).timestamp())
        self.conn.execute('UPDATE messages SET timestamp=? WHERE id=2',(stamp-60,))
        self.conn.commit()
        scope=filters(); scope['since']=NOW.replace(hour=13).isoformat()
        def choose(messages,schema,**kwargs):
            request=json.loads(messages[-1]['content'])
            if not request['trace']: return decision('timeline',filters=scope)
            if len(request['trace'])==1:
                return decision('surrounding',source_ids=[request['source_catalog'][0]['evidence_id']])
            return decision('finish')
        _,sources,_=self.run_agent(choose)
        self.assertEqual([s['local_id'] for s in sources],['1'])


class SkillToolFirstTests(unittest.TestCase):
    def setUp(self):
        self.api=synthetic_api(package())

    def test_skill_queries_are_independent_and_old_arguments_work(self):
        start=filters(); start['since']='1970-01-01T00:08:20+00:00'
        args=dict(chat_ids=[],person_ids=[],offset=0,size=60)
        limited=self.api.api_tool('timeline',dict(args,filters=start))
        all_rows=self.api.api_tool('timeline',args)
        self.assertTrue(all(i['timestamp']>=500 for i in limited['items']))
        self.assertGreater(len(all_rows['items']),len(limited['items']))
        self.assertEqual(self.api.tool_plan()['since_ts'],100)
        self.assertEqual(self.api.tool_plan()['people'],[])

    def test_skill_cannot_expand_task_window_or_invent_identity(self):
        for field, value in [('since','1970-01-01T00:00:00+00:00'),('until','1970-01-02T00:00:00+00:00'),
                             ('since','2026-10-12T00:00:00')]:
            scope=filters(); scope[field]=value
            with self.subTest(field=field,value=value),self.assertRaises(ValueError):
                self.api.api_tool('timeline',dict(chat_ids=[],person_ids=[],offset=0,size=10,filters=scope))
        with self.assertRaisesRegex(ValueError,'未验证的人物'):
            self.api.api_tool('timeline',dict(chat_ids=[],person_ids=['invented'],offset=0,size=10))

    def test_discovered_people_can_be_used_after_receipt_restore(self):
        result=self.api.api_tool('find_people',dict(query='alice',offset=0,size=20))
        self.assertTrue(result['people'])
        self.assertFalse(self.api.sources)
        restored=synthetic_api(package()); restored.restore(self.api.snapshot())
        result=restored.api_tool('timeline',dict(chat_ids=[],person_ids=['alice'],offset=0,size=20))
        self.assertTrue(result['items'])


if __name__=='__main__': unittest.main()
