import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from wechat_agent import web
from wechat_agent.qa_answers import QA_ANSWER_FORMAT, parse_qa_answer, validate_qa_answer


def answer(refs=None, text="Add demo and evaluation links to the resume.", kind="answer"):
    return {"paragraphs": [{"kind": kind, "text": text, "source_refs": [13] if refs is None else refs}]}


def completion(data, finish="stop"):
    return {"choices": [{"finish_reason": finish, "message": {"content": json.dumps(data)}}]}


def sources():
    return [{"chat_title": "Internships", "chat_type": "group", "sender": "Lin",
             "text": "A search internship is open."} for _ in range(12)] + [
        {"chat_title": "Lin", "chat_type": "private", "sender": "Lin",
         "text": "Add demo and evaluation links to the resume.", "time": "2026-09-22 10:00"}]


class AnswerContractTests(unittest.TestCase):
    def test_reference_above_twelve_is_valid_and_deduplicated(self):
        self.assertEqual(validate_qa_answer(answer([13, 13, 1]), 13), answer([13, 1]))

    def test_invalid_references_and_uncited_claims_are_rejected(self):
        for refs in ([0], [14], [-1], [True], [1.0], ["1"], [], "1"):
            with self.subTest(refs=refs), self.assertRaises(ValueError):
                validate_qa_answer(answer(refs), 13)

    def test_metadata_cannot_be_supplied_by_the_model(self):
        data = answer()
        data["paragraphs"][0]["chat_title"] = "Wrong group"
        with self.assertRaises(ValueError):
            validate_qa_answer(data, 13)

    def test_empty_and_malformed_answers_are_rejected(self):
        for data in ({}, [], {"paragraphs": []}, {"paragraphs": [None]}, answer(text=" ")):
            with self.subTest(data=data), self.assertRaises(ValueError):
                validate_qa_answer(data, 13)

    def test_missing_evidence_is_allowed_without_fake_citations(self):
        data = answer([], "No application deadline found.", "limitation")
        self.assertEqual(validate_qa_answer(data, 0), data)

    def test_refusal_and_truncation_are_not_valid_answers(self):
        for finish in ("length", "content_filter", None):
            with self.subTest(finish=finish), self.assertRaises(RuntimeError):
                parse_qa_answer(completion(answer(), finish), 13)
        data = completion(answer())
        data["choices"][0]["message"]["refusal"] = "Cannot answer"
        with self.assertRaises(RuntimeError):
            parse_qa_answer(data, 13)

    def test_bounded_repair_and_strict_response_format(self):
        with patch.object(web, "call_chat_payload", side_effect=[completion(answer([99])), completion(answer())]) as call:
            result = web.call_qa_answer({}, "fixture", "What did Lin suggest?", sources())
        self.assertEqual(result, answer())
        self.assertEqual(call.call_count, 2)
        self.assertEqual(call.call_args.kwargs["response_format"], QA_ANSWER_FORMAT)
        prompt = call.call_args.args[2][-2]["content"]
        self.assertIn("[13] 会话类型：私聊", prompt)
        self.assertIn("[1] 会话类型：群聊", prompt)

    def test_repeated_invalid_output_fails_instead_of_plain_text_fallback(self):
        with patch.object(web, "call_chat_payload", return_value=completion(answer([99]))) as call:
            with self.assertRaisesRegex(RuntimeError, "校验失败"):
                web.call_qa_answer({}, "fixture", "test", sources())
        self.assertEqual(call.call_count, 2)

    def test_network_failure_is_not_retried_as_a_format_error(self):
        with patch.object(web, "call_chat_payload", side_effect=RuntimeError("offline")) as call:
            with self.assertRaisesRegex(RuntimeError, "offline"):
                web.call_qa_answer({}, "fixture", "test", sources())
        self.assertEqual(call.call_count, 1)


class AnswerPersistenceTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        self.state = web.AppState(**{key: root / key for key in (
            "db_storage", "decrypted", "keys", "media_root", "voice_cache", "llm_config",
            "qa_store", "qa_index_cache", "qa_search_db")})
        with patch.object(web.threading.Thread, "start"):
            handler = web.make_handler(self.state)
        self.handler = handler.__new__(handler)
        for target, value in (
            ("load_llm_config", {"qa": {"api_key": "fixture"}}),
            ("load_or_build_qa_index", {"message_count": 13}),
            ("select_qa_context_with_diagnostics", (sources(), {})),
            ("build_person_summary", ""),
        ):
            mock = patch.object(web, target, return_value=value)
            mock.start()
            self.addCleanup(mock.stop)

    def run_answer(self, question="What did Lin suggest?", **payload):
        emit = Mock()
        with patch.object(web, "call_qa_answer", return_value=answer()) as call:
            self.handler.run_qa_stream({"question": question, "conversation_id": "test", **payload}, emit)
        done = [args.kwargs for args in emit.call_args_list if args.args[0] == "done"]
        self.assertEqual(len(done), 1)
        return done[0], call.call_args

    def test_all_sources_and_structured_answer_survive_reload_and_followup(self):
        event, _ = self.run_answer()
        self.assertEqual(len(event["sources"]), 13)
        stored = web.get_qa_conversation(self.state.qa_store, "test")
        original = copy.deepcopy(stored["messages"])
        self.assertEqual(original[-1]["answer_data"], answer())
        self.assertEqual(original[-1]["sources"][12]["chat_type"], "private")
        self.run_answer("And what next?", history=[{"role": "assistant", "content": "client lost sources"}])
        stored = web.get_qa_conversation(self.state.qa_store, "test")
        self.assertEqual(len(stored["messages"]), 4)
        self.assertEqual(stored["messages"][:2], original)

    def test_presaved_question_is_not_duplicated(self):
        web.save_qa_conversation(self.state.qa_store, "test", [{"role": "user", "content": "Already saved?"}])
        event, call = self.run_answer("Already saved?")
        self.assertEqual(len(event["conversation"]["messages"]), 2)
        self.assertEqual(call.args[4], [])

    def test_person_summary_is_an_explicit_separate_source(self):
        with patch.object(web, "build_person_summary", return_value="Lin sent 5 messages."):
            event, call = self.run_answer()
        self.assertEqual(event["sources"][-1]["chat_type"], "index_summary")
        self.assertEqual(len(call.args[3]), 14)

    def test_legacy_and_malformed_structured_records_remain_readable(self):
        for data in (None, answer([99])):
            message = {"role": "assistant", "content": "Legacy answer", "sources": sources(), "answer_data": data}
            stored = web.sanitize_qa_messages_for_store([message])[0]
            self.assertEqual(stored["content"], "Legacy answer")
            self.assertNotIn("answer_data", stored)

    def test_invalid_model_answer_is_saved_as_error_not_success(self):
        with patch.object(web, "call_chat_payload", return_value=completion(answer([99]))):
            emit = Mock()
            self.handler.run_qa_stream({"question": "test", "conversation_id": "test"}, emit)
        self.assertFalse(any(call.args[0] == "done" for call in emit.call_args_list))
        stored = web.get_qa_conversation(self.state.qa_store, "test")["messages"][-1]
        self.assertIn("校验失败", stored["error"])
        self.assertNotIn("answer_data", stored)
