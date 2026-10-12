import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from scripts.evaluate_search_v2 import dataset, evaluate
from scripts.benchmark_skill_sql import benchmark


class SearchEvaluationTests(unittest.TestCase):
    def test_sql_benchmark_preserves_results_and_counts_setup(self):
        result=benchmark(200,2)
        self.assertTrue(result['results_equal'])
        self.assertEqual(len(result['current_query_seconds']),2)
        self.assertAlmostEqual(result['prototype_total_including_setup_s'],
                               result['prototype_setup_s']+sum(result['prototype_query_seconds']))

    def test_independent_questions_and_valid_fictional_evidence(self):
        corpus,cases,now=dataset()
        self.assertEqual(len(cases),50)
        self.assertEqual(len({c['question'] for c in cases}),50)
        self.assertEqual(len(corpus['messages'])+len(corpus['increment']),95)
        self.assertEqual(now.utcoffset().total_seconds(),8*3600)
        self.assertGreaterEqual(sum(bool(c.get('unanswerable')) for c in cases),5)
        self.assertTrue(all(c['facts'] for c in cases))

    def test_offline_run_never_calls_models_or_claims_answer_scores(self):
        with tempfile.TemporaryDirectory() as root, \
             patch('wechat_agent.web.call_chat_payload',side_effect=AssertionError('No paid calls')), \
             patch('wechat_agent.web.call_embeddings',side_effect=AssertionError('No paid calls')):
            args=SimpleNamespace(cases='E08,E17,E45',modes='keyword',live_config=None,
                judge_model='',top_k=20,output=Path(root)/'report')
            report=evaluate(args)
            summary=report['summaries']['keyword']
            self.assertEqual(summary['reviewed'],0)
            self.assertEqual(summary['generation_failures'],0)
            self.assertIsNone(summary['answer_correctness']['rate'])
            self.assertEqual(report['summaries']['agent']['status'],'not_run')
            self.assertEqual(json.loads((args.output/'api_usage.json').read_text()) if (args.output/'api_usage.json').exists() else [],[])

    def test_paid_modes_cannot_silently_be_replaced_with_offline_search(self):
        with self.assertRaisesRegex(ValueError,'require explicit'):
            evaluate(SimpleNamespace(cases='',modes='hybrid',live_config=None))


if __name__=='__main__': unittest.main()
