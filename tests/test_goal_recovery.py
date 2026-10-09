import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from wechat_agent.goals import GoalStore, GoalScheduler, GoalConflict, run_goal_agent
from wechat_agent.goal_checkpoints import checkpoint_info, GoalResumeRequired


class ProcessExit(BaseException):
    pass


def response(calls=None):
    if calls is None:
        message = {'content': json.dumps({'summary': 'Found', 'items': [
            {'title': 'Item', 'detail': 'Supported finding', 'suggestion': None, 'source_refs': [1]}
        ], 'notice': None})}
    else:
        message = {'content': None, 'tool_calls': [
            {'id': 'call_' + str(i), 'type': 'function', 'function': {'name': 'read_chat', 'arguments': json.dumps({'chat_id': chat})}}
            for i, chat in enumerate(calls)
        ]}
    return {'choices': [{'message': message, 'finish_reason': 'stop' if calls is None else 'tool_calls'}],
            'usage': {'prompt_tokens': 10, 'completion_tokens': 2, 'total_tokens': 12}}


class GoalRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = GoalStore(Path(self.temp.name) / 'goals.sqlite3')
        self.data = {'title': 'Original task', 'prompt': 'Find opportunities', 'enabled': False,
                     'interval_value': 6, 'interval_unit': 'hours', 'range_type': 'recent_days', 'range_value': 3, 'range_unit': 'days'}
        self.goal_id = self.store.save(self.data, now=100)
        self.goal = self.store.claim_manual(self.goal_id, now=1000000)
        self.goal['checkpoint_path'] = str(self.store.checkpoint_path(self.goal['run_id']))
        self.reports = []
        self.tools = Mock(side_effect=lambda name, args, since, until: {'messages': [
            {'chat_id': args['chat_id'], 'local_id': 1, 'text': 'Evidence', 'timestamp': until - 1}
        ]})

    def run_agent(self, complete, goal=None, report=None, cancelled=lambda: False, model='fixture'):
        def save(data):
            self.reports.append(data)
            self.store.progress(self.goal['run_id'], data)
        return run_goal_agent(goal or self.goal, complete, self.tools, report or save, cancelled, model)

    def recover(self, confirmed=False):
        self.store.recover(now=1000050)
        return self.store.claim_resume(self.goal_id, self.goal['run_id'], confirmed)

    def test_process_restart_resumes_second_tool_without_replaying_first(self):
        normal = self.tools.side_effect
        def crash(name, args, since, until):
            if args['chat_id'] == 'two':
                raise ProcessExit()
            return normal(name, args, since, until)
        self.tools.side_effect = crash
        with self.assertRaises(ProcessExit):
            self.run_agent(Mock(return_value=response(['one', 'two'])))
        self.store = GoalStore(self.store.path)
        goal = self.recover()
        self.tools.reset_mock()
        self.tools.side_effect = normal
        complete = Mock(return_value=response())
        result = self.run_agent(complete, goal)
        self.assertEqual([c.args[1]['chat_id'] for c in self.tools.call_args_list], ['two'])
        complete.assert_called_once()
        self.assertEqual([s['reference'] for s in self.reports[-1]['sources']], [1, 2])
        self.assertEqual(self.reports[-1]['usage']['total_tokens'], 24)
        self.assertEqual(self.tools.call_args.args[2:], (1000000 - 3 * 86400, 1000000))
        self.store.finish(goal, 'completed', result)
        self.assertEqual(len(self.store.history(self.goal_id)), 1)
        self.assertFalse(Path(self.goal['checkpoint_path']).exists())

    def test_response_receipt_survives_crash_before_graph_checkpoint(self):
        def report(data):
            if data['progress'] == '模型已返回，处理检索结果':
                raise ProcessExit()
        first = Mock(return_value=response(['one']))
        with self.assertRaises(ProcessExit):
            self.run_agent(first, report=report)
        info = checkpoint_info(self.goal['checkpoint_path'])
        self.assertTrue(info['resumable'])
        self.assertFalse(info['retry_confirmation_required'])
        final = Mock(return_value=response())
        self.run_agent(final, self.recover())
        first.assert_called_once()
        final.assert_called_once()
        self.assertEqual(self.reports[-1]['usage']['total_tokens'], 24)

    def test_unknown_call_requires_explicit_confirmation_and_keeps_evidence(self):
        first = Mock(side_effect=[response(['one']), TimeoutError('timed out')])
        with self.assertRaisesRegex(RuntimeError, '未自动重试'):
            self.run_agent(first)
        first.assert_called()
        self.assertEqual(first.call_count, 2)
        self.store.recover()
        self.assertTrue(self.store.history(self.goal_id)[0]['retry_confirmation_required'])
        with self.assertRaisesRegex(GoalConflict, '可能已计费'):
            self.store.claim_resume(self.goal_id, self.goal['run_id'])
        resume = self.store.claim_resume(self.goal_id, self.goal['run_id'], True)
        self.tools.reset_mock()
        complete = Mock(return_value=response())
        self.run_agent(complete, resume)
        self.tools.assert_not_called()
        complete.assert_called_once()
        self.assertEqual(self.reports[-1]['usage']['model_calls'][-1]['uncertain_attempts'], 1)
        self.assertEqual(self.reports[-1]['sources'][0]['reference'], 1)

    def test_uncertain_receipt_blocks_direct_graph_resume_without_confirmation(self):
        with self.assertRaisesRegex(RuntimeError, '未自动重试'):
            self.run_agent(Mock(side_effect=TimeoutError('timeout')))
        complete = Mock()
        with self.assertRaises(GoalResumeRequired):
            self.run_agent(complete, {**self.goal, 'resume': True})
        complete.assert_not_called()

    def test_completed_graph_recovers_without_new_model_calls_and_finish_is_idempotent(self):
        result = self.run_agent(Mock(side_effect=[response(['one']), response()]))
        goal = self.recover()
        complete = Mock(side_effect=AssertionError('must reuse result'))
        recovered = self.run_agent(complete, goal)
        self.assertEqual(result, recovered)
        self.store.finish(goal, 'completed', recovered, now=1000100)
        self.store.finish(goal, 'failed', error='late duplicate', now=1000200)
        run = self.store.history(self.goal_id)[0]
        self.assertEqual((run['status'], run['finished_at']), ('completed', 1000100))

    def test_original_task_window_and_schedule_survive_edits_and_resume(self):
        with self.assertRaises(ProcessExit):
            self.tools.side_effect = ProcessExit()
            self.run_agent(Mock(return_value=response(['one'])))
        self.store.recover()
        self.store.save({**self.data, 'id': self.goal_id, 'prompt': 'New goal', 'range_value': 1}, now=1000100)
        goal = self.store.claim_resume(self.goal_id, self.goal['run_id'])
        self.assertEqual(goal['prompt'], self.data['prompt'])
        self.assertEqual(goal['range_value'], 3)
        self.assertEqual(goal['started_at'], 1000000)
        self.assertFalse(self.store.run_cancelled(goal))
        with self.assertRaises(GoalConflict):
            self.store.claim_resume(self.goal_id, self.goal['run_id'])
        with self.assertRaises(GoalConflict):
            self.store.claim_manual(self.goal_id)

    def test_model_change_rejected_without_paid_calls(self):
        with self.assertRaises(ProcessExit):
            self.tools.side_effect = ProcessExit()
            self.run_agent(Mock(return_value=response(['one'])))
        complete = Mock()
        with self.assertRaisesRegex(RuntimeError, '模型已变更'):
            self.run_agent(complete, self.recover(), model='different')
        complete.assert_not_called()

    def test_checkpoints_are_bounded_private_and_deleted_with_goal(self):
        self.run_agent(Mock(side_effect=[response(['one'])] * 12 + [response()]))
        path = Path(self.goal['checkpoint_path'])
        with sqlite3.connect(path) as conn:
            self.assertLessEqual(conn.execute('SELECT COUNT(*) FROM checkpoints').fetchone()[0], 2)
            self.assertLessEqual(conn.execute('SELECT COUNT(*) FROM model_receipts').fetchone()[0], 1)
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.store.finish(self.goal, 'failed', error='fixture')
        self.assertTrue(path.exists())
        self.store.delete(self.goal_id)
        self.assertFalse(path.exists())

    def test_old_history_has_no_fake_resume_button_and_no_snapshot_leak(self):
        self.store.finish(self.goal, 'failed', error='before graph started')
        run = self.store.history(self.goal_id)[0]
        self.assertFalse(run['resumable'])
        self.assertNotIn('goal_snapshot', run)
        with self.assertRaises(GoalConflict):
            self.store.claim_resume(self.goal_id, self.goal['run_id'])

    def test_resuming_older_run_is_visible_and_does_not_rewind_watermark(self):
        self.run_agent(Mock(side_effect=[response(['one']), response()]))
        self.store.finish(self.goal, 'failed', error='history save interrupted')
        newer = self.store.claim_manual(self.goal_id, now=2000000)
        self.store.finish(newer, 'completed', 'newer result')
        resumed = self.store.claim_resume(self.goal_id, self.goal['run_id'])
        self.assertEqual(self.store.list()[0]['latest_run']['id'], self.goal['run_id'])
        self.assertEqual(self.store.list()[0]['latest_run']['status'], 'running')
        self.store.finish(resumed, 'completed', 'older result')
        self.assertEqual(self.store.list()[0]['last_success_at'], 2000000)


if __name__ == '__main__':
    unittest.main()
