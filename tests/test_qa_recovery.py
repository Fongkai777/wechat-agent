import json
import sqlite3
import tempfile
import threading
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import Mock, patch

from wechat_agent import web
from wechat_agent.qa_agent import run_conversation_agent
from wechat_agent.qa_recovery import (
    CallReceipts, checkpoint_path, durable_model_call, recorded_call, read_snapshot,
)
from wechat_agent.goal_checkpoints import checkpoint_session, GoalResumeRequired
from tests.test_qa_agent import NOW, PEOPLE, raw, completion
from tests.test_background_jobs import finished


class ProcessExit(BaseException):
    pass


class QaRecoveryTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.store = Path(temp.name) / 'qa.json'
        self.turn = 'a' * 32
        self.path = checkpoint_path(self.store, self.turn)
        self.identity = {'conversation_id': 'one', 'profile_signature': 'fixture'}
        self.tools = Mock()
        self.tools.timeline.return_value = {'items': [{'evidence_id': 'one', 'text': 'original', 'sender': 'alice'}], 'complete': True}
        self.complete = Mock(side_effect=[completion(raw(need_context=False, time_user_turn=0)), completion({'action': 'finish', 'query': ''})])
        self.answer = Mock(return_value={'paragraphs': [{'kind': 'answer', 'text': 'Supported', 'source_refs': [1]}]})
        web.save_qa_conversation(self.store, 'one', [
            {'role': 'user', 'content': '小林今天下午说什么'},
            {'role': 'assistant', 'turn_id': self.turn, 'content': 'working', 'pending': True}])

    def run_agent(self, **kwargs):
        return run_conversation_agent('小林今天下午说什么', [], PEOPLE, self.tools,
            kwargs.pop('complete', self.complete), kwargs.pop('answer', self.answer), Mock(),
            kwargs.pop('checkpoint', Mock()), kwargs.pop('check', lambda: None),
            now=kwargs.pop('now', NOW), checkpoint_path=self.path,
            identity=kwargs.pop('identity', self.identity), **kwargs)

    def test_restart_resumes_answer_with_original_people_time_and_evidence(self):
        self.answer.side_effect = TimeoutError('unknown')
        with self.assertRaises(TimeoutError):
            self.run_agent()
        web.interrupt_pending_qa(self.store)
        old = web.get_qa_conversation(self.store, 'one')['messages'][-1]
        self.assertTrue(old['resumable'])
        self.assertTrue(old['retry_confirmation_required'])
        with self.assertRaisesRegex(ValueError, '可能已计费'):
            web.validate_qa_resume(self.store, {'conversation_id': 'one', 'turn_id': self.turn})
        self.tools.reset_mock()
        self.complete.reset_mock()
        self.answer.side_effect = None
        result, sources, diagnostics = self.run_agent(resume=True, confirm_retry=True, now=NOW + timedelta(days=5))
        self.tools.timeline.assert_not_called()
        self.complete.assert_not_called()
        self.assertEqual(sources[0]['text'], 'original')
        self.assertEqual(diagnostics['query_plan']['until_ts'], int(NOW.timestamp()))
        self.assertEqual(read_snapshot(self.path)['now'], NOW.isoformat())

    def test_raw_answer_receipt_survives_before_node_checkpoint(self):
        def check():
            if self.answer.called:
                raise ProcessExit()
        with self.assertRaises(ProcessExit):
            self.run_agent(check=check)
        self.answer.reset_mock()
        self.complete.reset_mock()
        result, _, _ = self.run_agent(resume=True)
        self.answer.assert_not_called()
        self.complete.assert_not_called()
        self.assertEqual(result['paragraphs'][0]['text'], 'Supported')

    def test_complete_graph_can_finalize_without_another_call(self):
        first = self.run_agent()
        self.answer.reset_mock()
        self.complete.reset_mock()
        self.assertEqual(first, self.run_agent(resume=True))
        self.answer.assert_not_called()
        self.complete.assert_not_called()

    def test_planning_timeout_needs_confirmation_and_no_automatic_call(self):
        api = Mock(side_effect=TimeoutError('unknown'))
        with self.assertRaises(TimeoutError):
            self.run_agent(complete=api)
        api.reset_mock()
        with self.assertRaises(GoalResumeRequired):
            self.run_agent(complete=api, resume=True)
        api.assert_not_called()
        self.run_agent(resume=True, confirm_retry=True)

    def test_tool_results_reused_when_later_tool_in_same_node_is_interrupted(self):
        self.complete.side_effect = [completion(raw(need_context=True, time_user_turn=0)), completion({'action': 'finish', 'query': ''})]
        self.tools.surrounding.side_effect = ProcessExit()
        with self.assertRaises(ProcessExit):
            self.run_agent()
        self.tools.timeline.reset_mock()
        self.tools.surrounding.side_effect = None
        self.tools.surrounding.return_value = {'items': [], 'complete': True}
        self.run_agent(resume=True)
        self.tools.timeline.assert_not_called()

    def test_config_change_rejected_and_checkpoint_files_private_bounded(self):
        self.run_agent()
        api = Mock()
        with self.assertRaisesRegex(ValueError, '配置'):
            self.run_agent(resume=True, identity={'conversation_id': 'two'}, complete=api)
        api.assert_not_called()
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)
        with sqlite3.connect(self.path) as conn:
            self.assertLessEqual(conn.execute('SELECT COUNT(*) FROM checkpoints').fetchone()[0], 2)

    def test_resume_updates_one_turn_preserving_newer_messages_and_title(self):
        messages = web.get_qa_conversation(self.store, 'one')['messages']
        messages.extend([{'role': 'user', 'content': 'newer'}, {'role': 'assistant', 'content': 'new answer'}])
        web.save_qa_conversation(self.store, 'one', messages)
        web.rename_qa_conversation(self.store, 'one', 'Renamed')
        result = web.update_qa_turn(self.store, 'one', self.turn,
            {'role': 'assistant', 'turn_id': self.turn, 'content': 'resumed answer'})
        self.assertEqual(len(result['messages']), 4)
        self.assertEqual(result['messages'][1]['content'], 'resumed answer')
        self.assertEqual(result['messages'][-1]['content'], 'new answer')
        self.assertEqual(result['title'], 'Renamed')
        with self.assertRaises(ValueError):
            web.update_qa_turn(self.store, 'another', self.turn, {})

    def test_delete_removes_checkpoint_and_legacy_stopped_rows_not_recoverable(self):
        self.run_agent()
        web.interrupt_pending_qa(self.store)
        with self.assertRaises(ValueError):
            web.validate_qa_resume(self.store, {'conversation_id': 'wrong', 'turn_id': self.turn})
        web.update_qa_turn(self.store, 'one', self.turn, {'role': 'assistant', 'turn_id': self.turn,
                                                     'content': 'stopped', 'error': 'old', 'stopped': True})
        self.assertFalse(web.get_qa_conversation(self.store, 'one')['messages'][-1]['resumable'])
        web.delete_qa_conversation(self.store, 'one')
        self.assertFalse(self.path.exists())
        for bad in ('../../keys', '', None):
            with self.assertRaises(ValueError):
                checkpoint_path(self.store, bad)

    def test_nested_paid_repair_calls_cache_first_response_and_never_store_key(self):
        calls = []
        @durable_model_call
        def api(profile, api_key, messages):
            calls.append(messages)
            if messages == 'repair' and calls.count('repair') == 1:
                raise TimeoutError('unknown')
            return {'result': messages}
        def answer():
            api({'model': 'fixture', 'api_key': 'SECRET_FIXTURE'}, 'SECRET_FIXTURE', 'first')
            return api({'model': 'fixture'}, 'SECRET_FIXTURE', 'repair')
        with checkpoint_session(self.path) as (_, r):
            receipts = CallReceipts(r.conn)
            with receipts.scope('answer'), self.assertRaises(TimeoutError):
                recorded_call('generate', answer, True)
        with checkpoint_session(self.path) as (_, r):
            receipts = CallReceipts(r.conn, True)
            with receipts.scope('answer'):
                self.assertEqual(recorded_call('generate', answer, True), {'result': 'repair'})
        self.assertEqual(calls, ['first', 'repair', 'repair'])
        self.assertNotIn(b'SECRET_FIXTURE', self.path.read_bytes())


class QaRecoveryHTTPTests(unittest.TestCase):
    def setUp(self):
        # datetime.now() is naive local time; keep this fixture in the local
        # afternoon even when the handler calls astimezone() on a UTC runner.
        clock = patch.object(web, 'datetime', wraps=datetime)
        clock.start().now.return_value = NOW.replace(tzinfo=None)
        self.addCleanup(clock.stop)
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        self.state = web.AppState(**{key: root/key for key in (
            'db_storage', 'decrypted', 'keys', 'media_root', 'voice_cache', 'llm_config',
            'qa_store', 'qa_index_cache', 'qa_search_db')})
        with patch.object(web.threading.Thread, 'start'):
            self.cls = web.make_handler(self.state)
        self.handler = self.cls.__new__(self.cls)
        self.handler.json_response = Mock()
        tools = Mock()
        tools.timeline.return_value = {'items': [{'evidence_id': 'one', 'text': 'fixture'}], 'complete': True}
        self.api = Mock(side_effect=[completion(raw(need_context=False, time_user_turn=0)), completion({'action': 'finish', 'query': ''})])
        self.answer = Mock(side_effect=TimeoutError('unknown'))
        for target, value in (
            ('load_llm_config', Mock(return_value={'qa': {'model': 'fixture', 'base_url': 'https://example.test', 'api_key': 'fixture'}})),
            ('load_or_build_qa_index', Mock(return_value={'message_count': 1, 'people_list': PEOPLE})),
            ('ConversationTools', Mock(return_value=tools)), ('call_chat_payload', self.api), ('call_qa_answer', self.answer),
        ):
            p = patch.object(web, target, value)
            p.start(); self.addCleanup(p.stop)
        self.handler.run_qa_stream({'conversation_id': 'one', 'question': '小林今天下午说什么'}, Mock())
        self.message = web.get_qa_conversation(self.state.qa_store, 'one')['messages'][-1]
        self.payload = {'conversation_id': 'one', 'turn_id': self.message['turn_id'], 'confirm_retry': True}

    def test_http_recovery_finishes_original_turn_and_deletes_checkpoint(self):
        self.assertTrue(self.message['resumable'])
        self.assertTrue(self.message['retry_confirmation_required'])
        self.assertEqual(self.message['error'], 'unknown')
        prior = web.get_qa_conversation(self.state.qa_store, 'one')['messages']
        web.save_qa_conversation(self.state.qa_store, 'one', prior + [
            {'role': 'user', 'content': 'new question'}, {'role': 'assistant', 'content': 'new answer'}])
        self.answer.side_effect = None
        self.answer.return_value = {'paragraphs': [{'kind': 'answer', 'text': 'recovered', 'source_refs': [1]}]}
        self.api.reset_mock()
        self.handler.run_qa_resume(self.payload, Mock())
        messages = web.get_qa_conversation(self.state.qa_store, 'one')['messages']
        self.assertEqual([m['content'] for m in messages], ['小林今天下午说什么', 'recovered [1]', 'new question', 'new answer'])
        self.assertFalse(checkpoint_path(self.state.qa_store, self.message['turn_id']).exists())
        self.api.assert_not_called()
        with self.assertRaises(ValueError):
            web.validate_qa_resume(self.state.qa_store, self.payload)

    def test_api_rejects_unconfirmed_retry_and_new_question_shares_resume_lock(self):
        self.handler.read_json_body = Mock(return_value={'kind': '/api/qa/resume', 'payload': {**self.payload, 'confirm_retry': False}})
        self.handler.handle_job_request('/api/jobs/start')
        self.assertEqual(self.handler.json_response.call_args.kwargs['status'], 400)
        ready, release = threading.Event(), threading.Event()
        def run(payload, emit):
            ready.set(); release.wait(2); emit('done', ok=True)
        self.handler.run_qa_resume = run
        self.handler.read_json_body.return_value = {'kind': '/api/qa/resume', 'payload': self.payload}
        self.handler.handle_job_request('/api/jobs/start')
        job = self.cls.background_jobs.get(self.handler.json_response.call_args.args[0]['job']['id'])
        try:
            self.assertTrue(ready.wait(2))
            self.handler.read_json_body.return_value = {'kind': '/api/qa_stream', 'payload': {'conversation_id': 'one', 'question': 'new'}}
            self.handler.handle_job_request('/api/jobs/start')
            self.assertEqual(self.handler.json_response.call_args.kwargs['status'], 409)
        finally:
            release.set()
            finished(job)

    def test_stale_browser_save_does_not_remove_interrupted_turn(self):
        self.handler.read_json_body = Mock(return_value={'id': 'one', 'messages': [{'role': 'user', 'content': 'stale'}]})
        self.handler.handle_save_qa_conversation()
        stored = web.get_qa_conversation(self.state.qa_store, 'one')['messages']
        self.assertEqual(stored[-1]['turn_id'], self.message['turn_id'])
        self.assertTrue(stored[-1]['resumable'])


if __name__ == '__main__':
    unittest.main()
