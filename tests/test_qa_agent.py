import json
import itertools
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from wechat_agent.qa_agent import run_conversation_agent
from wechat_agent.qa_tools import ConversationTools
from wechat_agent import web
from wechat_agent.platform_tools import FUNCTION_FIELDS


NOW = datetime(2026, 9, 30, 16, 30, tzinfo=timezone(timedelta(hours=8)))
PEOPLE = [{"id": "alice", "name": "小林", "aliases": ["小林"]}, {"id": "bob", "name": "小王", "aliases": ["小王"]}]


def completion(data):
    return {"choices": [{"finish_reason": "stop", "message": {"content": json.dumps(data, ensure_ascii=False)}}]}


CALL_IDS = itertools.count()


def action(tool, **changes):
    return {**dict(tool=tool,query='',chat_ids=[],source_ids=[],person_ids=[],offset=0,size=20,window_seconds=1800),**changes}


def decision(tool, **changes):
    values = action(tool,**changes)
    args = {k:values[k] for k in FUNCTION_FIELDS[tool]}
    if 'filters' in changes:
        args['filters'] = changes['filters']
    return {'choices':[{'finish_reason':'tool_calls','message':{'content':None,'tool_calls':[
        {'id':f'call_{next(CALL_IDS)}','type':'function','function':{'name':tool,'arguments':json.dumps(args)}}]}}]}


class ToolTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        path = Path(temp.name) / "messages.db"
        self.state = SimpleNamespace(qa_search_db=path, since_ts=None, account="me")
        self.conn = sqlite3.connect(path); self.addCleanup(self.conn.close)
        self.conn.executescript("""CREATE TABLE messages(id INTEGER PRIMARY KEY, source_db TEXT, source_table TEXT,
          local_id TEXT, server_id TEXT, chat_id TEXT, chat_title TEXT, chat_type TEXT, person_id TEXT,
          person_name TEXT, sender TEXT, sender_username TEXT, timestamp INTEGER, time TEXT, type TEXT, text TEXT, search_text TEXT);
          CREATE TABLE semantic_message_map(chunk_id INTEGER, message_id INTEGER);""")
        self.plan = {"tool_first": True, "people": [PEOPLE[0]], "query": "实习",
                     "since_ts": int(NOW.replace(hour=12, minute=0).timestamp()),
                     "until_ts": int(NOW.timestamp()), "chat_scope": {"type": "all", "keyword": ""}}
        self.hybrid = Mock(return_value=([], {}))
        self.tools = ConversationTools(self.state, self.hybrid, web.qa_item_from_search_row, lambda: None)

    def insert(self, id, person="alice", hour=13, days=0, chat="group@chatroom"):
        dt = NOW.replace(hour=hour) - timedelta(days=days)
        self.conn.execute("INSERT INTO messages VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
            id, "message/message_0.db", "Msg", str(id), str(id), chat, chat, "group" if "@" in chat else "private",
            person, person, person, person, int(dt.timestamp()), dt.isoformat(), "text", "internship", "internship"))
        self.conn.commit()

    def test_time_and_person_filters_and_pagination(self):
        self.insert(1); self.insert(2, person="bob"); self.insert(3, days=400); self.insert(4, hour=10); self.insert(5, hour=15)
        first = self.tools.timeline(self.plan, size=1)
        self.assertEqual(first["matched"], 2)
        self.assertFalse(first["complete"])
        second = self.tools.timeline(self.plan, first["next_offset"], 1)
        self.assertNotEqual(first["items"][0]["evidence_id"], second["items"][0]["evidence_id"])
        self.assertTrue(second["complete"])

    def test_group_scope_never_returns_private_or_other_groups(self):
        self.insert(1,chat='NTU@chatroom'); self.insert(2,person='bob',chat='NTU@chatroom')
        self.insert(3,chat='alice'); self.insert(4,chat='OTHER@chatroom')
        scoped=dict(self.plan,people=PEOPLE,chat_scope={'type':'group','keyword':'ntu','user_turn':0})
        chats=self.tools.list_chats(scoped)
        self.assertEqual([c['chat_id'] for c in chats['chats']],['NTU@chatroom'])
        self.assertEqual({r['sender_username'] for r in chats['chats'][0]['senders']},{'alice','bob'})
        # person_id can differ from sender identity; group filters must use the latter.
        self.conn.execute("UPDATE messages SET person_id='wrong' WHERE id=1"); self.conn.commit()
        self.assertEqual(len(self.tools.timeline(scoped)['items']),2)
        self.assertEqual(json.loads(self.tools.statistics(scoped)['items'][0]['text'])['messages'],2)
        self.assertEqual(self.tools.timeline(dict(scoped,chat_ids=['OTHER@chatroom']))['items'],[])

    def test_statistics_can_find_groups_then_read_messages(self):
        self.insert(1,chat='NTU@chatroom'); self.insert(2,person='bob',chat='NTU@chatroom')
        self.insert(3,chat='alice'); self.insert(4,chat='OTHER@chatroom')
        question='小林和小王在NTU群互动多不多'
        calls=[]
        def choose(messages,schema,**options):
            calls.append(json.loads(messages[-1]['content']))
            turn=len(calls)
            filters={'chat_type':'group','chat_keyword':'NTU','since':None,'until':None}
            if turn==1: return decision('statistics',filters=filters)
            if turn==2: return decision('list_chats',filters=filters)
            if turn==3:
                self.assertEqual(calls[-1]['observations'][-1]['chats'][0]['chat_id'],'NTU@chatroom')
                return decision('timeline',chat_ids=['NTU@chatroom'])
            return decision('finish')
        answer=Mock(return_value={'paragraphs':[{'kind':'answer','text':'只能确认共同发言','source_refs':[1]}]})
        result,sources,diag=run_conversation_agent(question,[],PEOPLE,self.tools,choose,answer,Mock(),Mock(),lambda:None,now=NOW)
        self.assertEqual([t['tool'] for t in diag['trace']],['statistics','list_chats','timeline'])
        self.assertEqual({s['chat_id'] for s in sources if s.get('chat_id')},{'NTU@chatroom'})
        self.assertEqual(len(calls),4)

    def test_empty_search_can_be_followed_by_timeline(self):
        self.insert(1)
        answer=Mock(return_value={'paragraphs':[]})
        complete=Mock(side_effect=[decision('search',query='no match'),
                                   decision('timeline'),decision('finish')])
        _,sources,diag=run_conversation_agent('小林今天下午说什么',[],PEOPLE,self.tools,complete,answer,
                                              Mock(),Mock(),lambda:None,now=NOW)
        self.assertTrue(sources)
        self.assertEqual([t['tool'] for t in diag['trace']],['search','timeline'])

    def test_invented_chat_id_rejected_before_tool_execution(self):
        from wechat_agent.platform_tools import validate_action
        value=action('timeline',chat_ids=['invented'])
        with self.assertRaisesRegex(ValueError,'已返回的会话ID'):
            validate_action(value,[],[])

    def test_person_subset_can_narrow_but_not_expand_verified_people(self):
        from wechat_agent.platform_tools import validate_action, execute_action
        self.insert(1); self.insert(2,person='bob')
        plan=dict(self.plan,people=PEOPLE,chat_scope={'type':'group','keyword':'','user_turn':0})
        value=action('timeline',person_ids=['bob'])
        validated=validate_action(value,[],[{'people':PEOPLE}],plan)
        self.assertEqual([x['sender_username'] for x in execute_action(self.tools,plan,validated,[])['items']],['bob'])
        with self.assertRaisesRegex(ValueError,'未验证的人物'):
            validate_action({**value,'person_ids':['stranger']},[],[],plan)

    def test_answer_context_limit_does_not_end_search_after_first_page(self):
        for i in range(12): self.insert(i)
        answer=Mock(return_value={'paragraphs':[]})
        complete=Mock(side_effect=[decision('timeline',size=10),
                                   decision('timeline',size=10,offset=10),decision('finish')])
        _,sources,diag=run_conversation_agent('小林今天下午说什么',[],PEOPLE,self.tools,complete,answer,
                                              Mock(),Mock(),lambda:None,now=NOW,limit=10)
        self.assertEqual(len(diag['trace']),2)
        self.assertEqual(len(sources),10)
        self.assertFalse(diag['coverage']['exhaustive'])

    def test_repeated_call_stops_without_infinite_retrieval(self):
        self.insert(1)
        complete=Mock(side_effect=[decision('timeline'),decision('timeline')])
        answer=Mock(return_value={'paragraphs':[]})
        _,_,diag=run_conversation_agent('小林今天下午说什么',[],PEOPLE,self.tools,complete,answer,
                                      Mock(),Mock(),lambda:None,now=NOW)
        self.assertEqual(len(diag['trace']),1)
        self.assertFalse(diag['coverage']['exhaustive'])

    def test_context_preserves_other_senders_without_relabeling(self):
        self.insert(1); self.insert(2, person="bob"); self.insert(3, hour=10)
        seeds = self.tools.timeline(self.plan)["items"]
        result = self.tools.surrounding(self.plan, seeds)
        self.assertEqual([i["sender"] for i in result["items"]], ["bob"])
        self.assertEqual(result["items"][0]["evidence_role"], "surrounding")

    def test_shared_windows_preserve_scope_and_page_candidate_pairs(self):
        self.insert(1,chat='NTU@chatroom'); self.insert(2,person='bob',chat='NTU@chatroom')
        self.insert(3,person='alice',chat='NTU@chatroom')
        self.insert(4,person='bob',hour=15,chat='NTU@chatroom')
        self.insert(5,chat='OTHER@chatroom'); self.insert(6,person='bob',chat='OTHER@chatroom')
        self.insert(7,chat='alice'); self.insert(8,person='bob',chat='alice')
        self.insert(9,days=2,chat='NTU@chatroom'); self.insert(10,person='bob',days=2,chat='NTU@chatroom')
        plan=dict(self.plan,people=PEOPLE,chat_scope={'type':'group','keyword':'NTU'})
        result=self.tools.shared_windows(plan,size=2,window_seconds=300)
        self.assertEqual(result['matched'],2)
        self.assertEqual(result['next_offset'],1)
        self.assertEqual({x['local_id'] for x in result['items']},{'2','3'})
        second=self.tools.shared_windows(plan,offset=1,size=2,window_seconds=300)
        self.assertTrue(second['complete'])
        self.assertEqual({x['local_id'] for x in second['items']},{'1','2'})
        self.assertEqual(second['windows'][0]['gap_seconds'],0)
        self.assertIn('不是互动次数',result['note'])
        self.assertEqual(self.tools.shared_windows(dict(plan,chat_scope={'type':'private'}))['items'],[])
        with self.assertRaisesRegex(ValueError,'两位'):
            self.tools.shared_windows(self.plan)

    def test_shared_windows_can_be_followed_by_surrounding_in_model_loop(self):
        self.insert(1); self.insert(2,person='bob'); self.insert(3,person='third')
        inputs=[]
        def choose(messages,schema,**options):
            inputs.append(json.loads(messages[-1]['content']))
            if len(inputs)==1: return decision('list_chats')
            if len(inputs)==2: return decision('shared_windows',person_ids=['alice','bob'])
            if len(inputs)==3:
                window=inputs[-1]['observations'][-1]['windows'][0]
                return decision('surrounding',source_ids=window['source_ids'])
            return decision('finish',source_ids=inputs[-1]['observations'][1]['windows'][0]['source_ids'])
        _,sources,diag=run_conversation_agent('小林和小王今天下午的对话',[],PEOPLE,self.tools,choose,
            Mock(return_value={'paragraphs':[]}),Mock(),Mock(),lambda:None,now=NOW)
        self.assertEqual([t['tool'] for t in diag['trace']],['list_chats','shared_windows','surrounding'])
        self.assertEqual({s['sender_username'] for s in sources},{'alice','bob','third'})

    def test_semantic_chunk_is_rehydrated_and_clipped(self):
        self.insert(1); self.insert(2, person="bob"); self.insert(3, days=400)
        self.conn.executemany("INSERT INTO semantic_message_map VALUES(1, ?)", [(1,), (2,), (3,)]); self.conn.commit()
        self.hybrid.return_value = ([{"semantic_chunk_id": 1}], {})
        result = self.tools.search(self.plan)
        self.assertEqual([i["local_id"] for i in result["items"]], ["1"])

    def test_empty_window_does_not_fall_back_to_history(self):
        self.insert(1, days=400)
        self.assertEqual(self.tools.timeline(self.plan)["items"], [])

    def test_search_exposes_optional_rerank_degradation(self):
        self.hybrid.return_value = ([], {'rerank_error':'timeout'})
        result = self.tools.search(self.plan)
        self.assertIn('重排未完成',result['note'])
        self.assertFalse(result['complete'])

    def test_statistics_counts_all_rows_not_top_k(self):
        for i in range(8): self.insert(i)
        result = self.tools.statistics(self.plan)
        self.assertEqual(json.loads(result["items"][0]["text"])["messages"], 8)

    def test_unanswered_excludes_conversations_already_replied(self):
        self.insert(1, chat="alice")
        self.insert(2, person="me", hour=14, chat="alice")
        self.insert(3, person="bob", chat="bob")
        result = self.tools.unanswered(dict(self.plan, people=[]))
        self.assertEqual([x["chat_id"] for x in result["items"]], ["bob"])

    def test_graph_saves_plan_trace_and_only_current_evidence(self):
        self.insert(1); self.insert(2, person="bob"); self.insert(3, days=400)
        seed=self.tools.timeline(self.plan)['items'][0]['evidence_id']
        complete = Mock(side_effect=[decision('timeline',filters={'chat_type':'all','chat_keyword':'',
            'since':NOW.replace(hour=12,minute=0).isoformat(),'until':None}),
            decision('surrounding',source_ids=[seed]),decision('finish')])
        answer = Mock(return_value={"paragraphs": [{"kind": "answer", "text": "在讨论实习", "source_refs": [1]}]})
        checkpoint = Mock()
        result, sources, diag = run_conversation_agent("她今天下午什么态度", [{"role": "user", "content": "小林想找实习吗"}],
            PEOPLE, self.tools, complete, answer, Mock(), checkpoint, lambda: None, now=NOW)
        self.assertEqual(diag["conversation_state"]["people"], [])
        self.assertIn('小林想找实习吗', str(complete.call_args_list[0].args))
        self.assertGreaterEqual(checkpoint.call_count, 3)
        self.assertEqual({s["local_id"] for s in sources}, {"1", "2"})
        self.assertNotIn("2025", answer.call_args.args[0])
        self.assertTrue(diag["trace"])

    def test_graph_no_evidence_never_calls_answer_or_hybrid(self):
        answer = Mock()
        result, sources, diag = run_conversation_agent("她今天下午什么态度", [{"role": "user", "content": "小林想找实习吗"}],
            PEOPLE, self.tools, Mock(side_effect=[decision('timeline',filters={'chat_type':'all','chat_keyword':'',
                'since':NOW.replace(hour=12,minute=0).isoformat(),'until':None}),decision('finish')]), answer, Mock(), Mock(), lambda: None, now=NOW)
        self.assertFalse(sources); answer.assert_not_called(); self.hybrid.assert_not_called()
        self.assertEqual(result["paragraphs"][0]["kind"], "limitation")


if __name__ == "__main__":
    unittest.main()
