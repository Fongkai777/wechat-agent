"""Synthetic story progression stays repeatable, isolated and calendar-consistent."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch, Mock

from wechat_agent import demo, web
from wechat_agent.demo_scenario import build_showcase
from wechat_agent.platform_runtime import create_platform_runtime, QA_LIMITS
from tests.test_platform_unification import native_args
from scripts.evaluate_showcase import load_cases, conversation_history
from wechat_agent.qa_tools import ConversationTools
from wechat_agent.evidence_context import select_evidence, context_windows
from wechat_agent.platform_tools import execute_action


ANCHOR = '2026-10-13T18:00:00+08:00'


class ShowcaseTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)

    def runtime(self, since=0):
        state = demo.demo_state(self.root)
        runtime = create_platform_runtime(state,
            {'since':since,'until':int(datetime.fromisoformat(ANCHOR).timestamp()),
             'task':'Showcase diagnostic','account':state.account},
            QA_LIMITS,web.load_llm_config(state.llm_config),'qa',lambda:None,threading.Lock())
        self.addCleanup(runtime.api.close)
        return runtime.api

    def test_authored_corpus_has_twelve_chats_and_stable_unique_ids(self):
        data = build_showcase(ANCHOR)
        rows = data['messages']+data['increment']
        self.assertEqual([len(p['messages']) for p in data['phases']], [314,21,8])
        self.assertEqual(len({r[1] for r in rows}), 12)
        self.assertEqual(len({r[0] for r in rows}), len(rows))
        self.assertTrue(all(r[1] in data['contacts'] and r[2] in data['contacts'] for r in rows))
        self.assertTrue(all(datetime.fromisoformat(r[3]) <= datetime.fromisoformat(ANCHOR) for r in rows))
        self.assertFalse(any('{{' in r[4] for r in rows))

    def test_clock_shifts_both_message_timestamps_and_deadlines(self):
        original = build_showcase(ANCHOR)
        later = build_showcase('2026-10-20T18:00:00+08:00')
        for a,b in zip(original['messages']+original['increment'], later['messages']+later['increment']):
            self.assertEqual(a[:3], b[:3])
            self.assertEqual(datetime.fromisoformat(b[3])-datetime.fromisoformat(a[3]), timedelta(days=7))
        texts = [r[4] for r in later['increment']]
        self.assertIn('你上次问的那个，截止延到 2026-10-24 晚上了', texts)
        self.assertIn('截止 2026-10-26', texts)
        with self.assertRaises(ValueError): build_showcase('2026-10-13T18:00:00')

    def test_calendar_rolls_over_year_and_leap_day(self):
        data = build_showcase('2028-02-28T01:00:00+08:00')
        self.assertTrue(any('2028-02-29' in r[4] for r in data['messages']))
        data = build_showcase('2026-12-31T01:00:00+08:00')
        self.assertTrue(any('2027-01-01' in r[4] for r in data['messages']))

    def test_restart_reuses_rendered_data_and_never_rebases_existing_root(self):
        self.assertEqual(demo.initialize(self.root, anchor=ANCHOR),314)
        with patch('wechat_agent.demo_scenario.build_showcase', side_effect=AssertionError('must use saved corpus')):
            self.assertEqual(demo.initialize(self.root),0)
            self.assertEqual(demo.append_phase(self.root)['inserted'],21)
        with self.assertRaises(ValueError): demo.initialize(self.root, anchor='2026-10-14T18:00:00+08:00')
        with self.assertRaises(ValueError): demo.initialize(self.root, dataset='evaluation')
        self.assertEqual(demo.workspace_manifest(self.root)['corpus']['anchor'],ANCHOR)

    def test_phases_incrementally_index_and_reply_clears_raw_pending_candidate(self):
        demo.initialize(self.root, anchor=ANCHOR)
        state = demo.demo_state(self.root)
        initial = web.update_qa_search_db_incremental(state)
        self.assertEqual(initial['message_count'],314)
        anchor = int(datetime.fromisoformat(ANCHOR).timestamp())
        def pending_yu():
            state = demo.demo_state(self.root)
            rows = web.collect_qa_items_for_chat(state, state.chat_by_id('demo_yu'),
                since_ts=anchor-86400, until_ts=anchor, max_items=1, strict=True, include_empty=True)
            return rows[-1]['sender_username'] != state.account
        self.assertTrue(pending_yu())
        with self.assertRaisesRegex(ValueError,'updates'): demo.append_phase(self.root,'replies')
        for name, count in [('updates',21),('replies',8)]:
            self.assertEqual(demo.append_phase(self.root,name)['inserted'],count)
            self.assertTrue(demo.refresh_workspace(state))
            result = web.update_qa_search_db_incremental(state)
            self.assertEqual((result['update_mode'], result['inserted']),('incremental',count))
            self.assertEqual(demo.append_phase(self.root,name)['inserted'],0)
        self.assertFalse(pending_yu())
        self.assertEqual(demo.append_phase(self.root),{'phase':None,'inserted':0,'complete':True})
        self.assertEqual(web.update_qa_search_db_incremental(state)['inserted'],0)
        self.assertEqual(sum(c['total_messages'] for c in state.chats),343)

    def test_concurrent_phase_requests_insert_once(self):
        demo.initialize(self.root, anchor=ANCHOR)
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _:demo.append_phase(self.root,'updates'), range(2)))
        self.assertEqual(sorted(r['inserted'] for r in results),[0,21])
        self.assertEqual(demo.append_phase(self.root)['phase'],'replies')

    def test_failed_import_rolls_back_messages_and_phase_receipt(self):
        demo.initialize(self.root, anchor=ANCHOR)
        insert = demo._insert_messages
        def fail(conn, messages):
            insert(conn, messages)
            raise RuntimeError('synthetic interruption')
        with patch.object(demo,'_insert_messages',side_effect=fail), self.assertRaises(RuntimeError):
            demo.append_phase(self.root)
        self.assertEqual(sum(c['total_messages'] for c in demo.demo_state(self.root).chats),314)
        self.assertEqual(demo.append_phase(self.root)['inserted'],21)

    def test_legacy_directory_keeps_fixed_evaluation_data(self):
        demo.initialize(self.root,dataset='evaluation')
        (self.root/demo.MANIFEST).unlink()
        self.assertEqual(demo.initialize(self.root),0)
        self.assertEqual(demo.append_phase(self.root)['inserted'],10)
        self.assertEqual(demo.fixture()['messages'][0][3],'2026-09-10 10:00:00')

    def test_deadline_fragment_expands_to_private_subject_and_citable_context(self):
        demo.initialize(self.root, anchor=ANCHOR)
        demo.append_phase(self.root, 'updates')
        api = self.runtime()
        with patch('urllib.request.urlopen', side_effect=AssertionError('Local tool must not call a model')):
            found = api.api_tool('query_messages', native_args('query_messages',
                sql='SELECT * FROM messages WHERE text LIKE ?', params=['%截止延到%']))
            self.assertEqual(len(found['items']),1)
            seed = found['items'][0]
            self.assertEqual(seed['local_id'],'315')
            nearby = api.api_tool('message_context', native_args('message_context',
                source_ids=[seed['evidence_id']],before=0,after=3))['items']
            self.assertEqual([r['local_id'] for r in nearby],['316','317','318'])
            self.assertTrue(all(r['chat_id']=='demo_lin' and r['chat_type']=='private' for r in nearby))
            self.assertIn('星桥',nearby[1]['text'])
            self.assertEqual(len(api.api_cite(nearby)),3)
            self.assertEqual(api.model_calls,0)

    def test_scoped_sql_keeps_cross_chat_forwards_and_separates_same_name_senders(self):
        demo.initialize(self.root, anchor=ANCHOR)
        demo.append_phase(self.root, 'updates')
        api = self.runtime()
        forwarded = api.dispatch('query', {'sql':'SELECT * FROM messages WHERE text LIKE ?',
                                          'params':['%https://careers.example.com/farsail-rag%']})
        self.assertEqual({r['chat_id'] for r in forwarded},{'demo_career@chatroom','demo_class@chatroom'})
        same_name = api.dispatch('query',{'sql':'SELECT * FROM messages WHERE sender_username=?','params':['demo_lin2']})
        self.assertTrue(same_name)
        self.assertTrue(all(r['chat_id']=='demo_outdoor@chatroom' for r in same_name))
        self.assertEqual(api.sql_snapshot.builds,1)

    def test_search_fragment_automatically_keeps_job_subject_and_correction(self):
        demo.initialize(self.root, anchor=ANCHOR)
        demo.append_phase(self.root,'updates')
        state = demo.demo_state(self.root)
        web.update_qa_search_db_incremental(state)
        plan = {'since_ts':0,'until_ts':int(datetime.fromisoformat(ANCHOR).timestamp()),
                'query':'deadline','people':[]}
        tools = ConversationTools(state,Mock(),web.qa_item_from_search_row,lambda:None)
        with tools.connection() as conn:
            seed = tools.item(conn.execute("SELECT * FROM messages WHERE local_id='315'").fetchone())
        tools.hybrid.return_value = ([seed],{})
        result = tools.search(plan,size=1)
        selected, _ = select_evidence(result['items'],[seed['evidence_id']],limit=60)
        self.assertEqual({s['local_id'] for s in selected},{'315','316','317','318'})
        self.assertTrue(all(s['chat_id']=='demo_lin' for s in selected))
        self.assertEqual(context_windows(selected)[0]['ordered_refs'],[1,2,3,4])
        self.assertIn('星桥',selected[2]['text'])
        # The same search result crosses the Skill/native facade with its
        # metadata and citation numbers intact, including checkpoint restore.
        api = self.runtime()
        api.tool_backend = lambda p,a,s:execute_action(tools,p,a,s)
        args = dict(query='deadline',chat_ids=[],person_ids=[],size=1)
        native = api.api_tool('search',args)
        self.assertEqual({s['local_id'] for s in native['items']},{'315','316','317','318'})
        saved = api.snapshot()
        api.restore(saved)
        again = api.api_tool('search',args)
        self.assertEqual([s['reference'] for s in native['items']], [s['reference'] for s in again['items']])
        self.assertEqual(context_windows(native['items']),context_windows(again['items']))

    def test_deadline_keeps_fourth_preceding_message_with_different_job_name(self):
        demo.initialize(self.root, anchor=ANCHOR)
        demo.append_phase(self.root,'updates')
        state = demo.demo_state(self.root)
        web.update_qa_search_db_incremental(state)
        tools = ConversationTools(state,Mock(),web.qa_item_from_search_row,lambda:None)
        with tools.connection() as conn:
            seed = tools.item(conn.execute("SELECT * FROM messages WHERE local_id='324'").fetchone())
        tools.hybrid.return_value = ([seed],{})
        result = tools.search({'since_ts':0,'until_ts':int(datetime.fromisoformat(ANCHOR).timestamp()),
                              'query':'deadline','people':[]},size=1)
        selected, _ = select_evidence(result['items'],[seed['evidence_id']],limit=60)
        self.assertEqual({s['local_id'] for s in selected},{'320','321','322','323','324','325'})
        subject = next(s for s in selected if s['local_id']=='320')
        self.assertIn('远帆',subject['text'])

    def test_pending_reply_query_changes_between_executions_without_model_calls(self):
        demo.initialize(self.root, anchor=ANCHOR)
        since = int(datetime.fromisoformat(ANCHOR).timestamp())-86400
        sql = ("SELECT m.* FROM messages m WHERE m.chat_type='private' AND "
               "NOT EXISTS (SELECT 1 FROM messages n WHERE n.chat_id=m.chat_id AND "
               "(n.timestamp>m.timestamp OR (n.timestamp=m.timestamp AND n.id>m.id))) "
               "AND m.sender_username<>?")
        with patch('urllib.request.urlopen', side_effect=AssertionError('Local task must not call a model')):
            api = self.runtime(since)
            initial = api.dispatch('query',{'sql':sql,'params':['demo_me']})
            self.assertEqual({r['chat_id'] for r in initial},{'demo_yu'})
            demo.append_phase(self.root,'updates')
            demo.append_phase(self.root,'replies')
            after = self.runtime(since)
            self.assertEqual(after.dispatch('query',{'sql':sql,'params':['demo_me']}),[])
            self.assertEqual(api.model_calls+after.model_calls,0)

    def test_case_evidence_exists_at_each_phase_and_followup_uses_actual_answer(self):
        cases = load_cases()
        data = build_showcase(cases['anchor'])
        visible, completed = set(), set()
        for phase in data['phases']:
            visible.update(r[0] for r in phase['messages'])
            for case in (c for c in cases['cases'] if c['phase']==phase['name']):
                self.assertTrue(set(case['evidence']) <= visible)
                if case.get('history_from'):
                    self.assertIn(case['history_from'],completed)
                self.assertNotIn(case['id'],completed)
                completed.add(case['id'])
        answer = {'paragraphs':[{'kind':'answer','text':'Actual generated answer','source_refs':[]}]}
        history = conversation_history({'history_from':'S01'},
            {'S01':{'question':'Original question','history':[],'answer':answer,'facts':['Never leak gold']}})
        self.assertEqual(history,[{'role':'user','content':'Original question'},
                                  {'role':'assistant','content':'Actual generated answer'}])
        with self.assertRaises(ValueError):
            conversation_history({'history_from':'S01'},{'S01':{'error_type':'TimeoutError'}})


if __name__ == '__main__': unittest.main()
