import copy
import unittest

from scripts.evaluate_answers import estimate_cost, summarize, validate_grade


class AnswerEvaluationTests(unittest.TestCase):
    def setUp(self):
        self.grade = {
            "facts": [{"fact_index": 1, "satisfied": True}],
            "paragraphs": [{"paragraph_index": 1, "factually_correct": True,
                            "citation_supported": False}],
        }

    def test_accepts_complete_review(self):
        self.assertEqual(validate_grade(self.grade, 1, 1), self.grade)

    def test_rejects_missing_duplicate_and_non_boolean_grades(self):
        for changes in ([], [{"fact_index": 2, "satisfied": True}],
                        [{"fact_index": 1, "satisfied": "true"}],
                        [{"fact_index": True, "satisfied": True}]):
            grade = copy.deepcopy(self.grade)
            grade["facts"] = changes
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                validate_grade(grade, 1, 1)
        grade = copy.deepcopy(self.grade)
        grade["facts"] *= 2
        with self.assertRaises(ValueError):
            validate_grade(grade, 2, 1)

    def test_cost_accounts_for_cached_input(self):
        cost = estimate_cost([{"model": "gpt-5-mini", "status": "completed", "usage": {
            "prompt_tokens": 1000, "completion_tokens": 100,
            "prompt_tokens_details": {"cached_tokens": 500},
        }}])
        self.assertAlmostEqual(cost["total_usd"], 0.000338)
        self.assertEqual(cost["unknown_calls"], 0)

    def test_unknown_usage_is_not_free(self):
        for call in ({"model": "unknown"}, {"model": "gpt-5-mini", "status": "failed"},
                     {"model": "gpt-5-mini", "status": "completed", "usage": {}}):
            with self.subTest(call=call):
                cost = estimate_cost([call])
                self.assertIsNone(cost["total_usd"])
                self.assertEqual(cost["unknown_calls"], 1)

    def test_failures_and_unreviewed_answers_remain_visible(self):
        answer = {"paragraphs": [{"text": "No evidence", "source_refs": []}]}
        result = summarize([
            {"answer": answer, "review": self.grade, "latency_s": 2},
            {"answer": answer, "latency_s": 4},
            {"generation_error": "Failed", "latency_s": 6},
        ], [])
        self.assertEqual(result["attempted"], 3)
        self.assertEqual(result["generation_failures"], 1)
        self.assertEqual(result["unreviewed"], 1)
        self.assertEqual(result["answer_correctness"]["total"], 1)
        self.assertIsNone(result["citation_faithfulness"]["rate"])
        self.assertEqual(result["latency_s"]["p50"], 3)

    def test_cited_but_unsupported_paragraph_fails(self):
        result = summarize([{
            "answer": {"paragraphs": [{"text": "Claim", "source_refs": [1]}]},
            "review": self.grade, "latency_s": 1,
        }], [])
        self.assertEqual(result["citation_faithfulness"], {"passed": 0, "total": 1, "rate": 0})

    def test_empty_results_are_not_perfect_scores(self):
        result = summarize([], [])
        self.assertIsNone(result["answer_correctness"]["rate"])
        self.assertIsNone(result["answer_completeness"]["rate"])
        self.assertIsNone(result["latency_s"]["p50"])


if __name__ == "__main__":
    unittest.main()
