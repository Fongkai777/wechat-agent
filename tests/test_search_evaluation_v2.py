import json
from pathlib import Path
import tempfile
import subprocess
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from scripts.evaluate_search_v2 import dataset, evaluate
from scripts.benchmark_skill_sql import benchmark


class SearchEvaluationTests(unittest.TestCase):
    def test_sql_benchmark_preserves_results_and_counts_setup(self):
        result=benchmark(200,2)
        self.assertTrue(result['results_equal'])
        self.assertEqual(len(result['rebuild_query_seconds']),2)
        self.assertEqual(len(result['reuse_query_seconds']),2)
        self.assertEqual(result['reuse_snapshot_builds'],1)
        self.assertAlmostEqual(result['reuse_total_s'], sum(result['reuse_query_seconds']))
        self.assertEqual(result['reuse_first_query_s'],result['reuse_query_seconds'][0])

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
            self.assertEqual(summary['retrieval_failures'],0)
            self.assertTrue(all(not r.get('error_type') for r in report['results']))
            self.assertTrue(all(r['all_evidence_found'] for r in report['results'] if r['expected_message_ids']))
            for row in report['results']:
                self.assertLessEqual(len(row['sources']),args.top_k)
                self.assertTrue(all(s.get('evidence_id') for s in row['sources']))
            self.assertIsNone(summary['answer_correctness']['rate'])
            self.assertEqual(report['summaries']['agent']['status'],'not_run')
            self.assertEqual(json.loads((args.output/'api_usage.json').read_text()) if (args.output/'api_usage.json').exists() else [],[])

    def test_offline_cli_exits_successfully_with_nonempty_keyword_results(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(__file__).resolve().parents[1]
            output = Path(directory)/'report'
            result = subprocess.run([sys.executable,'-B',str(root/'scripts/evaluate_search_v2.py'),
                '--cases','E01,E08','--output',str(output)],cwd=root,
                capture_output=True,text=True,timeout=60)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
            report = json.loads((output/'results.json').read_text())
            self.assertEqual(len(report['results']),2)
            self.assertTrue(all(r['all_evidence_found'] for r in report['results']))

    def test_paid_modes_cannot_silently_be_replaced_with_offline_search(self):
        with self.assertRaisesRegex(ValueError,'require explicit'):
            evaluate(SimpleNamespace(cases='',modes='hybrid',live_config=None))


if __name__=='__main__': unittest.main()
