import json
import unittest
from unittest.mock import Mock

from wechat_agent.evidence_context import merge_evidence, select_evidence, context_windows, ordered_source_groups
from wechat_agent.qa_agent import run_conversation_agent
from wechat_agent import web
from wechat_agent.qa_tools import context_turn
from tests import test_qa_agent as fixtures
from tests.test_qa_agent import NOW, decision


def row(key, **fields):
    return {'evidence_id':key, 'evidence_role':'primary', 'text':key,
            'timestamp':1, 'index_row_id':int(key), 'chat_id':'chat', **fields}


class EvidenceSelectionTests(unittest.TestCase):
    def test_turn_extension_stops_at_sender_pause_or_cap(self):
        rows = [{'sender_username':'alice','timestamp':i*15} for i in range(20)]
        self.assertEqual(len(context_turn(rows)),12)
        rows[5]['sender_username']='bob'
        self.assertEqual(len(context_turn(rows)),5)
        rows[4]['timestamp']=1000
        self.assertEqual(len(context_turn(rows)),4)

    def test_selection_keeps_subject_when_short_reply_is_selected(self):
        sources = [row('1',context_ids=['2','3']), row('2',evidence_role='surrounding',context_for=['1']),
                   row('3',evidence_role='surrounding',context_for=['1']), row('4')]
        selected, trimmed = select_evidence(sources, ['2'])
        self.assertEqual({s['evidence_id'] for s in selected}, {'1','2','3'})
        self.assertFalse(trimmed)
        windows = context_windows(selected)
        self.assertEqual(windows[0]['ordered_refs'], [2,1,3])
        self.assertFalse(windows[0]['incomplete'])

    def test_budgets_drop_whole_windows_instead_of_isolating_a_phrase(self):
        sources = [row('1',context_ids=['2','3']), row('2',context_for=['1']), row('3',context_for=['1']), row('4')]
        for kwargs in ({'limit':2}, {'char_limit':2}):
            selected, trimmed = select_evidence(sources, **kwargs)
            self.assertTrue(trimmed)
            self.assertEqual([s['evidence_id'] for s in selected], ['4'])

    def test_neighbours_do_not_recursively_expand_across_topics(self):
        sources = [row('1',context_ids=['2']), row('2',context_ids=['3']), row('3',context_ids=['4']), row('4')]
        selected, _ = select_evidence(sources, ['1'])
        self.assertEqual([s['evidence_id'] for s in selected], ['1','2'])
        self.assertTrue(context_windows(selected)[1]['incomplete'])

    def test_repeated_hits_merge_links_and_preserve_primary_role(self):
        sources = merge_evidence([row('1',context_ids=['2'])],
                                 [row('1',evidence_role='surrounding',context_for=['3'],context_ids=['4'])])
        self.assertEqual(sources[0]['evidence_role'], 'primary')
        self.assertEqual(sources[0]['context_ids'], ['2','4'])
        selected, missing = select_evidence(sources)
        self.assertTrue(missing)
        self.assertEqual(selected, [])

    def test_prompt_lists_chronological_windows_without_relabeling_sources(self):
        sources = [row('2',context_ids=['1'],chat_type='private',sender='Alice'),
                   row('1',context_for=['2'],chat_type='private',sender='Me')]
        messages = web.build_qa_messages('Which role?', sources)
        self.assertIn('"ordered_refs": [2, 1]', messages[-1]['content'])
        self.assertIn('不证明同一话题或直接回复', messages[-1]['content'])
        self.assertIn('发送者：Alice', messages[-1]['content'])
        self.assertLess(messages[-1]['content'].index('[2] 会话类型'),messages[-1]['content'].index('[1] 会话类型'))

    def test_display_blocks_keep_refs_and_do_not_merge_chats_or_long_pauses(self):
        sources = [row('1',timestamp=4000),row('2',timestamp=100),
                   row('3',timestamp=101,chat_id='other'),row('4',timestamp=102)]
        groups = ordered_source_groups(sources)
        self.assertEqual([[ref for ref,_ in group] for group in groups],[[2,4],[1],[3]])


class SearchWindowTests(unittest.TestCase):
    setUp = fixtures.ToolTests.setUp
    insert = fixtures.ToolTests.insert

    def hit(self, key):
        source = self.tools.timeline(dict(self.plan, people=[]))['items']
        return next(s for s in source if s['local_id']==str(key))

    def test_automatic_context_respects_chat_time_and_same_second_order(self):
        self.insert(1); self.insert(2,person='bob'); self.insert(3,person='me')
        self.insert(4,hour=10); self.insert(5,chat='elsewhere'); self.insert(6,hour=15)
        self.hybrid.return_value = ([self.hit(2)], {})
        result = self.tools.search(dict(self.plan, people=[]), size=1)
        self.assertEqual([s['local_id'] for s in result['items']], ['2','1','3'])
        self.assertEqual(result['anchor_count'],1)
        self.assertEqual(result['context_count'],2)
        self.assertEqual(context_windows(result['items'])[0]['ordered_refs'], [2,1,3])
        # Even nearby messages before an explicit time floor stay excluded.
        result = self.tools.search(dict(self.plan,people=[],since_ts=NOW.replace(hour=13).timestamp()+1), size=1)
        self.assertEqual(result['items'], [])

    def test_finish_keeps_context_even_when_model_selects_only_fragment(self):
        self.insert(1); self.insert(2,person='bob'); self.insert(3,person='me')
        seed = self.hit(2)
        self.hybrid.return_value = ([seed], {})
        answer = Mock(return_value={'paragraphs':[]})
        complete = Mock(side_effect=[decision('search',query='deadline',size=1),
                                     decision('finish',source_ids=[seed['evidence_id']])])
        _, sources, diag = run_conversation_agent('Deadline?', [], [], self.tools, complete, answer,
            Mock(), Mock(), lambda:None, now=NOW)
        self.assertEqual({s['local_id'] for s in sources}, {'1','2','3'})
        self.assertEqual(diag['trace'][0]['anchor_count'],1)
        self.assertEqual(diag['trace'][0]['context_count'],2)
        request = json.loads(complete.call_args_list[1].args[0][-1]['content'])
        self.assertTrue(any(s.get('context_ids') for s in request['evidence']))

    def test_stale_index_anchor_is_rejected(self):
        self.insert(1)
        seed = self.hit(1)
        self.conn.execute("UPDATE messages SET local_id='new' WHERE id=1")
        self.conn.commit()
        with self.assertRaisesRegex(ValueError, '锚点'):
            self.tools.surrounding(self.plan, [seed])

    def test_small_hit_target_does_not_discard_context_windows(self):
        for group in range(8):
            for offset in range(7):
                self.insert(group*7+offset+1,chat=f'g{group}@chatroom')
        hits = [self.hit(group*7+4) for group in range(8)]
        self.hybrid.return_value = (hits, {})
        calls = []
        def choose(messages, schema, **options):
            calls.append(messages)
            if len(calls)==1:
                return decision('search',query='fragment',size=8)
            request = json.loads(messages[-1]['content'])
            tool = json.loads(next(m['content'] for m in messages if m['role']=='tool'))
            ids = {s['evidence_id'] for s in request['source_catalog']}
            self.assertEqual(len(ids),56)
            self.assertEqual({s['evidence_id'] for s in tool['items']}, ids)
            return decision('finish',source_ids=[s['evidence_id'] for s in hits])
        _, sources, _ = run_conversation_agent('fragment?',[],[],self.tools,choose,
            Mock(return_value={'paragraphs':[]}),Mock(),Mock(),lambda:None,now=NOW,limit=10)
        self.assertEqual({s['local_id'] for s in sources},{str(i) for i in range(1,57)})

    def test_evaluation_cap_is_explicit_and_keeps_whole_windows(self):
        from scripts.evaluate_search_v2 import final_context
        for group in range(4):
            for offset in range(7):
                self.insert(group*7+offset+1,chat=f'g{group}@chatroom')
        hits = [self.hit(group*7+4) for group in range(4)]
        self.hybrid.return_value = (hits,{})
        answer = Mock(return_value={'paragraphs':[]})
        choose = Mock(side_effect=[decision('search',query='deadline',size=4),
                                   decision('finish',source_ids=[s['evidence_id'] for s in hits])])
        _, sources, diag = run_conversation_agent('deadline?',[],[],self.tools,choose,answer,
            Mock(),Mock(),lambda:None,now=NOW,limit=20,final_message_limit=20)
        self.assertEqual(len(sources),14)
        self.assertTrue(diag['coverage']['context_incomplete'])
        raw = self.tools.search(dict(self.plan,people=[]),size=4)['items']
        baseline, trimmed = final_context(raw,20)
        self.assertTrue(trimmed)
        self.assertEqual([s['evidence_id'] for s in baseline],[s['evidence_id'] for s in sources])

    def test_configured_character_budget_keeps_or_omits_whole_long_message(self):
        self.insert(1)
        self.conn.execute('UPDATE messages SET text=? WHERE id=1',('x'*11000,))
        self.conn.commit()
        hit = self.hit(1)
        self.hybrid.return_value = ([hit],{})
        for budget, count in [(10000,0),(20000,1)]:
            answer = Mock(return_value={'paragraphs':[]})
            choose = Mock(side_effect=[decision('search',query='long',size=1),
                decision('finish',source_ids=[hit['evidence_id']])])
            _,sources,diag = run_conversation_agent('long?',[],[],self.tools,choose,answer,
                Mock(),Mock(),lambda:None,now=NOW,context_char_budget=budget)
            self.assertEqual(len(sources),count)
            self.assertEqual(answer.call_count,count)
            self.assertEqual(diag['coverage']['context_char_budget'],budget)
            if sources:
                self.assertEqual(len(sources[0]['text']),11000)


if __name__ == '__main__': unittest.main()
