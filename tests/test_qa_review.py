"""Review is a fallible model decision, but repair and recovery are deterministic."""
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import Mock

from tests.test_qa_agent import NOW, decision, completion
from wechat_agent.qa_agent import run_conversation_agent
from wechat_agent.qa_review import parse_review
from wechat_agent.goal_checkpoints import GoalResumeRequired


def verdict(kind='accept', reason='Existing source contradicts draft', refs=None):
    return completion({'checks':[{'paragraph_index':1,
        'status':{'accept':'supported','revise':'contradicted','retrieve':'needs_context'}[kind],
        'reason':reason,'source_refs':refs or []}],
        'coverage':{'status':'covered','reason':'Question covered'}})


def draft(text):
    return {'paragraphs':[{'kind':'answer','text':text,'source_refs':[1]}]}


class AnswerReviewTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name)/'qa.db'
        self.tools = SimpleNamespace(state=SimpleNamespace(since_ts=None),
            timeline=Mock(return_value={'items':[
                {'evidence_id':'one','text':'这次 SG','sender':'Lin','chat_type':'group'},
                {'evidence_id':'two','text':'至少三个月','sender':'Lin','chat_type':'group'}], 'complete':True}),
            surrounding=Mock(return_value={'items':[
                {'evidence_id':'three','text':'最好能讲一个答错的例子','sender':'Lin','chat_type':'private'}], 'complete':True}))
        self.choose = Mock(side_effect=[decision('timeline'),decision('finish')])
        self.review = Mock(return_value=verdict())
        self.answer = Mock(return_value=draft('In Singapore'))
        self.events = Mock()
        self.checkpoint = Mock()

    def complete(self, messages, schema=None, **options):
        if not schema:
            return self.choose(messages,schema,**options)
        result = self.review(messages,schema)
        data = json.loads(result['choices'][0]['message']['content'])
        count = len(json.loads(messages[-1]['content'])['draft']['paragraphs'])
        data['checks'].extend({'paragraph_index':i,'status':'supported','reason':'Server coverage notice','source_refs':[]}
                              for i in range(2,count+1))
        return completion(data)

    def run_agent(self, **options):
        return run_conversation_agent('Where and how to prepare?',[],[],self.tools,self.complete,
            self.answer,self.events,self.checkpoint,lambda:None,now=NOW,checkpoint_path=self.path,**options)

    def test_correct_answer_needs_one_review_and_no_extra_retrieval(self):
        result,sources,diag = self.run_agent()
        self.assertEqual(result['paragraphs'][0]['text'],'In Singapore')
        self.assertEqual(diag['quality_status'],'reviewed')
        self.assertEqual(self.review.call_count,1)
        self.assertEqual(self.choose.call_count,2)
        self.assertEqual(self.answer.call_count,1)
        material = json.loads(self.review.call_args.args[0][-1]['content'])
        self.assertEqual(material['sources'][0]['text'],'这次 SG')
        self.assertNotIn('expected_facts',material)
        self.assertEqual(self.review.call_args.args[1]['json_schema']['name'],'qa_evidence_review')

    def test_missed_existing_evidence_rewrites_answer_without_search(self):
        self.answer.side_effect = [draft('Location unknown'),draft('Singapore')]
        self.review.side_effect = [verdict('revise','Source 1 says SG',[1]),verdict()]
        result,sources,diag = self.run_agent()
        self.assertEqual(result['paragraphs'][0]['text'],'Singapore')
        self.assertIn('Source 1 says SG',self.answer.call_args.args[0])
        self.assertEqual(self.tools.timeline.call_count,1)
        self.assertEqual(self.choose.call_count,2)
        self.assertEqual([r['decision'] for r in diag['answer_reviews']],['revise','accept'])

    def test_missing_evidence_reopens_tools_restores_pool_and_updates_citations(self):
        self.choose.side_effect = [decision('timeline'),decision('finish',source_ids=['one']),
            decision('surrounding',source_ids=['one']),decision('finish')]
        self.review.side_effect = [verdict('retrieve','Preparation advice needs more context'),verdict()]
        result,sources,diag = self.run_agent()
        self.assertEqual({s['evidence_id'] for s in sources},{'one','two','three'})
        resumed = json.loads(self.choose.call_args_list[2].args[0][-1]['content'])
        self.assertEqual(len(resumed['source_catalog']),2)
        self.assertEqual(resumed['answer_review_feedback']['decision'],'retrieve')
        self.assertEqual([t['tool'] for t in diag['trace']],['timeline','surrounding'])
        self.assertEqual(self.tools.timeline.call_count,1)
        self.assertEqual(self.answer.call_count,2)

    def test_failed_repairs_are_bounded_and_do_not_publish_rejected_draft(self):
        self.review.return_value = verdict('revise')
        self.answer.return_value = draft('Known contradictory conclusion')
        result,sources,diag = self.run_agent()
        self.assertEqual(self.review.call_count,3)
        self.assertEqual(self.answer.call_count,3)
        self.assertEqual(diag['quality_status'],'unresolved')
        self.assertNotIn('Known contradictory',str(result))
        self.assertEqual(result['paragraphs'][0]['kind'],'limitation')

    def test_budget_exhaustion_does_not_restart_search_indefinitely(self):
        self.choose.side_effect = [decision('timeline',offset=i) for i in range(8)]
        self.review.return_value = verdict('retrieve')
        result,_,diag = self.run_agent()
        self.assertEqual(self.choose.call_count,8)
        self.assertEqual(self.review.call_count,1)
        self.assertEqual(diag['quality_status'],'unresolved')

    def test_unknown_review_charge_requires_confirmation_and_preserves_draft_receipt(self):
        self.review.side_effect = TimeoutError('unknown paid outcome')
        with self.assertRaises(TimeoutError): self.run_agent()
        with self.assertRaises(GoalResumeRequired): self.run_agent(resume=True)
        self.assertEqual(self.review.call_count,1)
        self.review.side_effect = None
        result,_,diag = self.run_agent(resume=True,confirm_retry=True)
        self.assertEqual(self.review.call_count,2)
        self.assertEqual(self.answer.call_count,1)
        self.assertEqual(self.tools.timeline.call_count,1)
        self.assertEqual(diag['quality_status'],'reviewed')
        self.run_agent(resume=True)
        self.assertEqual(self.review.call_count,2)

    def test_completed_review_receipt_survives_interruption_before_checkpoint(self):
        class ProcessExit(BaseException): pass
        def stop(diag,sources):
            if diag.get('answer_reviews'): raise ProcessExit()
        self.checkpoint.side_effect = stop
        with self.assertRaises(ProcessExit): self.run_agent()
        self.checkpoint.side_effect = None
        _,_,diag = self.run_agent(resume=True)
        self.assertEqual(self.review.call_count,1)
        self.assertEqual(self.answer.call_count,1)
        self.assertEqual(diag['quality_status'],'reviewed')

    def test_no_sources_returns_server_limitation_without_paid_review(self):
        self.tools.timeline.return_value = {'items':[],'complete':True}
        _,_,diag = self.run_agent()
        self.review.assert_not_called()
        self.answer.assert_not_called()
        self.assertEqual(diag['quality_status'],'no_evidence')


class ReviewValidationTests(unittest.TestCase):
    def test_decision_is_derived_from_every_paragraph_and_question_coverage(self):
        self.assertEqual(parse_review(verdict(),1)['decision'],'accept')
        self.assertEqual(parse_review(verdict('revise'),1)['decision'],'revise')
        self.assertEqual(parse_review(verdict('retrieve'),1)['decision'],'retrieve')
        data=json.loads(verdict()['choices'][0]['message']['content'])
        data['coverage']['status']='needs_evidence'
        self.assertEqual(parse_review(completion(data),1)['decision'],'retrieve')
        with self.assertRaises(ValueError): parse_review(verdict(),1,2)

    def test_invalid_or_truncated_reviews_cannot_approve_an_answer(self):
        payloads = [completion({'decision':'accept','issues':[{'reason':'wrong','source_refs':[]}]}),
            completion({'decision':'retrieve','issues':[]}),verdict('revise',refs=[2]),
            verdict('revise',refs=[True]),completion({'decision':'accept','issues':[],'extra':True})]
        truncated = verdict(); truncated['choices'][0]['finish_reason']='length'; payloads.append(truncated)
        refused = verdict(); refused['choices'][0]['message']['refusal']='no'; payloads.append(refused)
        for payload in payloads:
            with self.subTest(payload=payload),self.assertRaises(ValueError): parse_review(payload,1)


if __name__ == '__main__': unittest.main()
