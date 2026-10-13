import copy
import json
import io
import sqlite3
import tempfile
import threading
import unittest
import urllib.error
from pathlib import Path
from datetime import datetime
from unittest.mock import Mock, patch

from wechat_agent.goals import GoalStore, GoalScheduler, GoalConflict, GoalCancelled, GoalReferenceError, GoalCoverageError, observation_window, GOAL_RESULT_FORMAT, validate_goal_result
from wechat_agent import web


class GoalStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'goals.sqlite3'
        self.store = GoalStore(self.path)
        self.data = {'title': 'Follow up', 'prompt': 'Check messages', 'cadence': 'hourly', 'enabled': True}

    def test_create_persist_and_no_early_execution(self):
        goal_id = self.store.save(self.data, now=100)
        goal = GoalStore(self.path).list()[0]
        self.assertEqual(goal['id'], goal_id)
        self.assertEqual(goal['next_run_at'], 3700)
        self.assertIsNone(self.store.claim_due(3699))
        self.assertIsNotNone(self.store.claim_due(3700))

    def test_only_supported_cadences_and_valid_inputs(self):
        for delta in ({'cadence': 'manual'}, {'cadence': 'minutely'}, {'title': ''}, {'prompt': ''}, {'prompt': 'a'*3001}, {'enabled': 'yes'}):
            with self.assertRaises(ValueError):
                self.store.save({**self.data, **delta})

    def test_range_persists_reschedules_and_is_snapshotted_in_history(self):
        goal_id = self.store.save({**self.data, 'range_type': 'recent_days', 'range_days': 7}, now=100)
        saved = GoalStore(self.path).list()[0]
        self.assertEqual((saved['range_type'], saved['range_value'], saved['range_unit']), ('recent_days', 7, 'days'))
        run = self.store.claim_due(3700)
        self.store.finish(run, 'completed', 'report', now=3710)
        self.store.save({**self.data, 'id': goal_id, 'range_type': 'today'}, now=3800)
        saved = self.store.list()[0]
        self.assertEqual(saved['next_run_at'], 7400)
        self.assertIsNone(saved['last_success_at'])
        history = self.store.history(goal_id)[0]
        self.assertEqual((history['range_type'], history['range_value']), ('recent_days', 7))
        self.assertEqual(history['window_start'], 3700 - 7 * 86400)
        self.assertEqual(history['window_end'], 3700)

    def test_omitted_range_fields_preserve_existing_settings(self):
        goal_id = self.store.save({**self.data, 'range_type': 'recent_days', 'range_days': 3}, now=100)
        self.store.save({**self.data, 'id': goal_id, 'title': 'Renamed'}, now=200)
        goal = self.store.list()[0]
        self.assertEqual((goal['range_type'], goal['range_value'], goal['next_run_at']), ('recent_days', 3, 3700))

    def test_units_persist_and_invalid_quantities_are_rejected(self):
        goal_id = self.store.save({**self.data, 'range_type': 'recent_days', 'range_value': 2, 'range_unit': 'weeks'}, now=100)
        goal = GoalStore(self.path).list()[0]
        self.assertEqual((goal['range_value'], goal['range_unit']), (2, 'weeks'))
        self.store.save({**self.data, 'id': goal_id, 'range_unit': 'months'}, now=200)
        goal = self.store.list()[0]
        self.assertEqual((goal['range_value'], goal['range_unit'], goal['next_run_at']), (2, 'months', 3800))
        for unit, value in [('years', 1), ('weeks', 13), ('months', 4), ('months', 1.5)]:
            with self.subTest(unit=unit, value=value), self.assertRaises(ValueError):
                self.store.save({**self.data, 'range_type': 'recent_days', 'range_value': value, 'range_unit': unit})

    def test_invalid_ranges_are_rejected_without_creating_goals(self):
        for days in (0, -1, 91, 1.5, True, '7', None):
            with self.subTest(days=days), self.assertRaises(ValueError):
                self.store.save({**self.data, 'range_type': 'recent_days', 'range_days': days})
        with self.assertRaises(ValueError):
            self.store.save({**self.data, 'range_type': 'all'})
        self.assertEqual(self.store.list(), [])

    def test_old_database_migrates_without_losing_goals_or_history(self):
        legacy = Path(self.temp.name) / 'legacy.sqlite3'
        with sqlite3.connect(legacy) as conn:
            conn.executescript("""
                CREATE TABLE goals (id TEXT PRIMARY KEY, title TEXT, prompt TEXT, cadence TEXT,
                    enabled INTEGER, created_at REAL, updated_at REAL, next_run_at REAL, last_success_at REAL);
                CREATE TABLE goal_runs (id TEXT PRIMARY KEY, goal_id TEXT, started_at REAL, finished_at REAL,
                    status TEXT, title TEXT, prompt TEXT, result TEXT DEFAULT '', progress TEXT DEFAULT '',
                    error TEXT DEFAULT '', model TEXT DEFAULT '', usage TEXT DEFAULT '{}',
                    sources TEXT DEFAULT '[]', steps TEXT DEFAULT '[]');
                INSERT INTO goals VALUES('old','Title','Prompt','hourly',1,100,100,3700,200);
                INSERT INTO goal_runs(id,goal_id,started_at,status,result) VALUES('run','old',200,'completed','Existing report');
            """)
        store = GoalStore(legacy)
        self.assertEqual(GoalStore(legacy).list()[0]['range_type'], 'today')
        self.assertEqual(store.list()[0]['last_success_at'], 200)
        history = store.history('old')[0]
        self.assertEqual(history['result'], 'Existing report')
        self.assertEqual(history['range_type'], '')
        self.assertIsNone(history['window_start'])

    def test_edit_reschedules_only_when_needed(self):
        goal_id = self.store.save(self.data, now=100)
        self.store.save({**self.data, 'id': goal_id, 'title': 'Renamed'}, now=200)
        self.assertEqual(self.store.list()[0]['next_run_at'], 3700)
        self.store.save({**self.data, 'id': goal_id, 'cadence': 'daily'}, now=200)
        self.assertEqual(self.store.list()[0]['next_run_at'], 86600)

    def test_custom_periods_drive_due_checks_resume_and_repeat(self):
        for value, unit, interval in [(12, 'hours', 43200), (7, 'days', 604800)]:
            with self.subTest(value=value, unit=unit):
                store = GoalStore(Path(self.temp.name) / (unit + '.sqlite3'))
                goal_id = store.save({**self.data, 'interval_value':value, 'interval_unit':unit}, now=100)
                self.assertIsNone(store.claim_due(100 + interval - 1))
                run = store.claim_due(100 + interval)
                self.assertIsNotNone(run)
                self.assertEqual(store.list()[0]['next_run_at'], 100 + 2 * interval)
                store.finish(run, 'completed', 'Report', now=101 + interval)
                store.toggle(goal_id, False, now=102 + interval)
                store.toggle(goal_id, True, now=200 + interval)
                self.assertEqual(store.list()[0]['next_run_at'], 200 + 2 * interval)
                store.save({'id':goal_id, 'title':'Renamed', 'prompt':self.data['prompt']}, now=300 + interval)
                saved = GoalStore(store.path).list()[0]
                self.assertEqual((saved['interval_value'], saved['interval_unit']), (value, unit))
                self.assertEqual(saved['next_run_at'], 200 + 2 * interval)
                store.save({'id':goal_id, 'title':'Renamed', 'prompt':self.data['prompt'], 'interval_value':2, 'interval_unit':'hours'}, now=400 + interval)
                self.assertEqual(store.list()[0]['next_run_at'], 400 + interval + 7200)

    def test_invalid_custom_periods_are_rejected(self):
        for value in (None, True, 0, -1, 1.5, '2', 366):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.store.save({**self.data, 'interval_value':value, 'interval_unit':'hours'})
        with self.assertRaises(ValueError):
            self.store.save({**self.data, 'interval_value':1, 'interval_unit':'manual'})
        self.assertEqual(self.store.list(), [])

    def test_paused_goal_does_not_run_and_resume_waits_full_interval(self):
        goal_id = self.store.save(self.data, now=100)
        self.store.toggle(goal_id, False, now=200)
        self.assertIsNone(self.store.claim_due(50000))
        self.store.toggle(goal_id, True, now=50000)
        self.assertEqual(self.store.list()[0]['next_run_at'], 53600)

    def test_missed_intervals_coalesce_and_duplicate_claims_block(self):
        self.store.save(self.data, now=100)
        run = self.store.claim_due(100000)
        self.assertIsNone(GoalStore(self.path).claim_due(100001))
        self.store.finish(run, 'completed', 'done', now=100020)
        self.assertIsNone(self.store.claim_due(100021))
        self.assertEqual(self.store.list()[0]['next_run_at'], 103600)

    def test_manual_run_restarts_timer_and_records_trigger(self):
        goal_id = self.store.save(self.data, now=100)
        run = self.store.claim_manual(goal_id, now=200)
        self.assertEqual(run['trigger'], 'manual')
        self.assertEqual(run['next_run_at'], 3800)
        with self.assertRaises(GoalConflict):
            self.store.claim_manual(goal_id, now=201)
        self.assertIsNone(self.store.claim_due(3700))
        self.store.finish(run, 'completed', 'Manual report', now=300)
        self.assertEqual(self.store.history(goal_id)[0]['trigger'], 'manual')
        self.assertEqual(self.store.list()[0]['latest_run']['trigger'], 'manual')
        self.assertIsNone(self.store.claim_due(3799))
        self.assertEqual(self.store.claim_due(3800)['trigger'], 'scheduled')

    def test_manual_range_uses_click_time_not_last_success(self):
        goal_id = self.store.save({**self.data, 'range_type':'recent_days', 'range_value':3, 'range_unit':'days'}, now=100)
        previous = self.store.claim_manual(goal_id, now=1000000)
        self.store.finish(previous, 'completed', 'Previous report', now=1000010)
        run = self.store.claim_manual(goal_id, now=1000100)
        history = self.store.history(goal_id)[0]
        self.assertEqual(run['last_success_at'],1000000)
        self.assertEqual(history['window_start'],1000100 - 3 * 86400)
        self.assertEqual(history['window_end'],1000100)

    def test_paused_goal_can_run_once_and_stop_without_enabling_schedule(self):
        goal_id = self.store.save({**self.data, 'enabled':False}, now=100)
        run = self.store.claim_manual(goal_id, now=200)
        self.assertFalse(self.store.run_cancelled(run))
        self.assertIsNone(self.store.list()[0]['next_run_at'])
        self.store.cancel_run(goal_id, run['run_id'])
        self.assertTrue(self.store.run_cancelled(run))
        self.store.progress(run['run_id'], {'progress':'Should not overwrite stop status', 'usage':{'total_tokens':10}})
        self.assertIn('停止中', self.store.history(goal_id)[0]['progress'])
        self.store.finish(run, 'completed', 'late answer', now=300)
        self.assertEqual(self.store.history(goal_id)[0]['status'], 'cancelled')
        self.assertFalse(self.store.list()[0]['enabled'])
        self.assertIsNone(self.store.claim_due(100000))
        next_run = self.store.claim_manual(goal_id, now=400)
        with self.assertRaises(GoalConflict):
            self.store.cancel_run(goal_id, run['run_id'])
        self.assertFalse(self.store.run_cancelled(next_run))

    def test_manual_and_timer_racing_claim_only_one_run(self):
        goal_id = self.store.save(self.data, now=100)
        output = []
        def claim(manual):
            try:
                output.append(self.store.claim_manual(goal_id, now=3700) if manual else self.store.claim_due(3700))
            except GoalConflict:
                output.append(None)
        threads = [threading.Thread(target=claim, args=(manual,)) for manual in (True, False)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(sum(run is not None for run in output), 1)
        self.assertEqual(len(self.store.history(goal_id)), 1)

    def test_invalid_manual_goal_does_not_create_a_run(self):
        for goal_id in ('', 'missing'):
            with self.assertRaises(ValueError):
                self.store.claim_manual(goal_id, now=200)

    def test_atomic_claim_across_connections(self):
        self.store.save(self.data, now=100)
        output = []
        threads = [threading.Thread(target=lambda: output.append(GoalStore(self.path).claim_due(3700))) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(sum(row is not None for row in output), 1)

    def test_running_edit_delete_blocked_and_pause_cannot_be_undone_early(self):
        goal_id = self.store.save(self.data, now=100)
        self.store.claim_due(3700)
        with self.assertRaises(GoalConflict):
            self.store.save({**self.data, 'id': goal_id})
        with self.assertRaises(GoalConflict):
            self.store.delete(goal_id)
        self.store.toggle(goal_id, False)
        self.assertFalse(self.store.enabled(goal_id))
        with self.assertRaises(GoalConflict):
            self.store.toggle(goal_id, True)

    def test_recovery_failure_and_history_retention(self):
        goal_id = self.store.save(self.data, now=100)
        self.store.claim_due(3700)
        GoalStore(self.path).recover(3800)
        self.assertEqual(self.store.history(goal_id)[0]['status'], 'interrupted')
        self.assertIsNone(self.store.list()[0]['last_success_at'])
        for i in range(25):
            run = self.store.claim_due(100000 + i*3600)
            self.store.finish(run, 'completed', result=f'result {i}')
        self.assertEqual(len(self.store.history(goal_id)), 20)
        next_run = self.store.claim_due(200000)
        self.assertEqual(next_run['previous_result'], 'result 24')
        self.store.finish(next_run, 'failed', error='network')
        self.store.delete(goal_id)
        self.assertEqual(self.store.history(goal_id), [])

    def test_scheduler_execution_is_independent_of_browser(self):
        goal_id = self.store.save(self.data, now=100)
        execute = Mock(return_value='report')
        scheduler = GoalScheduler(self.store, execute)
        self.assertFalse(scheduler.tick(300))
        self.assertTrue(scheduler.tick(3700))
        self.assertEqual(self.store.history(goal_id)[0]['result'], 'report')
        self.assertEqual(self.store.list()[0]['last_success_at'], 3700)

    def test_manual_scheduler_returns_before_model_finishes_and_saves_result(self):
        goal_id = self.store.save({**self.data, 'enabled':False}, now=100)
        release, entered = threading.Event(), threading.Event()
        def execute(goal, report, cancelled):
            self.assertFalse(cancelled())
            entered.set()
            release.wait(3)
            return 'Manual report'
        scheduler = GoalScheduler(self.store, execute)
        scheduler.thread = Mock(is_alive=Mock(return_value=True))
        try:
            run = scheduler.run_now(goal_id)
            self.assertTrue(entered.wait(1))
            self.assertEqual(self.store.history(goal_id)[0]['status'], 'running')
            self.assertFalse(scheduler.tick())
        finally:
            release.set()
            if scheduler.manual_thread:
                scheduler.manual_thread.join(3)
        self.assertEqual(self.store.history(goal_id)[0]['result'], 'Manual report')
        self.assertEqual(self.store.history(goal_id)[0]['id'], run['run_id'])
        self.assertFalse(self.store.list()[0]['enabled'])

    def test_manual_requires_running_service_and_worker_start_failure_is_saved(self):
        goal_id = self.store.save(self.data, now=100)
        scheduler = GoalScheduler(self.store, Mock())
        with self.assertRaises(GoalConflict):
            scheduler.run_now(goal_id)
        self.assertEqual(self.store.history(goal_id), [])
        scheduler.thread = Mock(is_alive=Mock(return_value=True))
        with patch.object(threading.Thread, 'start', side_effect=RuntimeError('thread unavailable')):
            with self.assertRaises(GoalConflict):
                scheduler.run_now(goal_id)
        self.assertEqual(self.store.history(goal_id)[0]['status'], 'failed')
        scheduler.execute.assert_not_called()

    def test_observation_watermark_uses_confirmed_sync_not_future_wall_clock(self):
        self.store.save(self.data, now=100)
        goal=self.store.claim_due(3700)
        goal['data_until']=3600
        self.store.finish(goal,'completed','result',now=3750)
        self.assertEqual(self.store.list()[0]['last_success_at'],3600)
        goal=self.store.claim_due(7300)
        goal['data_until']=None
        self.store.finish(goal,'completed','old snapshot only')
        self.assertEqual(self.store.list()[0]['last_success_at'],3600)

    def test_scheduler_failure_waits_for_next_tick_and_cancellation_keeps_watermark(self):
        goal_id = self.store.save(self.data, now=100)
        scheduler = GoalScheduler(self.store, Mock(side_effect=RuntimeError('failed')))
        scheduler.tick(3700)
        self.assertEqual(self.store.history(goal_id)[0]['status'], 'failed')
        self.assertIsNone(self.store.claim_due(3701))
        scheduler.execute = Mock(side_effect=GoalCancelled())
        scheduler.tick(7300)
        self.assertEqual(self.store.history(goal_id)[0]['status'], 'cancelled')
        self.assertIsNone(self.store.list()[0]['last_success_at'])


def result_fixture():
    return {'summary': 'Found one item', 'items': [{'title': 'Friend', 'detail': 'An open question needs a reply', 'suggestion': None, 'source_refs': [1]}], 'notice': None}


def response(name=None, arguments=None, text=None, usage=10):
    if text is None:
        text = json.dumps(result_fixture())
    message = {'content': text, 'role': 'assistant'}
    if name:
        message['tool_calls'] = [{'id': 'call_1', 'type': 'function', 'function': {'name': name, 'arguments': json.dumps(arguments or {})}}]
    return {'choices': [{'message': message, 'finish_reason': 'tool_calls' if name else 'stop'}], 'usage': {'prompt_tokens': usage, 'completion_tokens': 2, 'total_tokens': usage+2}}


class GoalResultTests(unittest.TestCase):
    def test_nullable_fields_and_empty_results_are_valid(self):
        data = {'summary': 'Nothing actionable in inspected messages', 'items': [], 'notice': None}
        self.assertEqual(validate_goal_result(json.dumps(data), set()), data)
        self.assertEqual(validate_goal_result(json.dumps(result_fixture()), {1}), result_fixture())

    def test_shape_types_lengths_and_references_are_validated_locally(self):
        invalid = [None, [], {}, {**result_fixture(), 'extra': 'ignored?'}, {**result_fixture(), 'notice': ''},
                   {**result_fixture(), 'summary': 'a' * 81}]
        for field, value in [('title', ''), ('detail', 42), ('suggestion', False), ('source_refs', []),
                             ('source_refs', [True]), ('source_refs', [1.0]), ('source_refs', [-1]),
                             ('extra', 'unexpected')]:
            invalid.append({**result_fixture(), 'items': [{**result_fixture()['items'][0], field: value}]})
        for data in invalid:
            with self.subTest(data=data), self.assertRaises(ValueError):
                validate_goal_result(json.dumps(data), {1})

    def test_all_items_and_references_are_kept(self):
        data = {**result_fixture(), 'items':[{**result_fixture()['items'][0], 'source_refs':list(range(1,61))} for _ in range(60)]}
        self.assertEqual(validate_goal_result(json.dumps(data), set(range(1,61))), data)
        self.assertNotIn('maxItems', GOAL_RESULT_FORMAT['json_schema']['schema']['properties']['items'])

    def test_structured_storage_survives_restart_without_truncating_or_rewriting_legacy_text(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'goals.sqlite3'
            store = GoalStore(path)
            goal_id = store.save({'title': 'Test', 'prompt': 'Test', 'cadence': 'hourly'}, now=100)
            legacy = store.claim_due(3700)
            store.finish(legacy, 'completed', result='Legacy plain report', now=3710)
            structured = store.claim_due(7300)
            data = result_fixture()
            data['items'] = [{**data['items'][0], 'detail': 'Long result ' * 12} for _ in range(15)]
            raw = json.dumps(data)
            self.assertGreater(len(raw), 1500)
            store.finish(structured, 'completed', result=raw, now=7310)
            reopened = GoalStore(path)
            summary = reopened.list()[0]['latest_run']
            self.assertEqual(summary['result'], data['summary'])
            self.assertEqual(summary['result_data'], data)
            newest, oldest = reopened.history(goal_id)
            self.assertEqual(newest['result'], raw)
            self.assertEqual(newest['result_data'], data)
            self.assertEqual(oldest['result'], 'Legacy plain report')
            self.assertIsNone(oldest['result_data'])

    def test_invalid_new_output_is_persisted_as_failure_not_a_successful_empty_result(self):
        with tempfile.TemporaryDirectory() as directory:
            store = GoalStore(Path(directory) / 'goals.sqlite3')
            goal_id = store.save({'title': 'Test', 'prompt': 'Test', 'cadence': 'hourly'}, now=100)
            scheduler = GoalScheduler(store, lambda goal, report, cancelled: validate_goal_result('Not JSON', set()))
            scheduler.tick(3700)
            run = store.history(goal_id)[0]
            self.assertEqual(run['status'], 'failed')
            self.assertEqual(run['result'], '')
            self.assertIn('格式无效', run['error'])
            self.assertIsNone(store.list()[0]['last_success_at'])


class GoalAPITests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root=Path(self.temp.name)
        state=web.AppState(**{key:root/key for key in ('db_storage','decrypted','keys','media_root','voice_cache','llm_config','qa_store','qa_index_cache','qa_search_db')})
        with patch.object(web.threading.Thread,'start'):
            self.handler_class=web.make_handler(state)
        self.handler=self.handler_class.__new__(self.handler_class)
        self.handler.json_response=Mock()

    def test_http_create_toggle_and_invalid_manual(self):
        self.handler.read_json_body=Mock(return_value={'title':'Goal','prompt':'Check messages','cadence':'manual'})
        self.handler.handle_goal_action('/api/goals/save')
        self.assertEqual(self.handler.json_response.call_args.kwargs['status'],400)
        self.handler.read_json_body.return_value['cadence']='hourly'
        self.handler.handle_goal_action('/api/goals/save')
        self.assertTrue(self.handler.json_response.call_args.args[0]['ok'])
        self.assertIsNone(self.handler_class.goal_scheduler.store.claim_due())

    def test_manual_http_action_is_async_and_stop_targets_the_specific_run(self):
        goal = {'id':'goal', 'run_id':'run', 'started_at':100, 'next_run_at':3700}
        scheduler = self.handler_class.goal_scheduler
        self.handler.read_json_body = Mock(return_value={'id':'goal'})
        with patch.object(scheduler, 'run_now', return_value=goal) as run:
            self.handler.handle_goal_action('/api/goals/run')
        run.assert_called_once_with('goal')
        self.assertEqual(self.handler.json_response.call_args.kwargs['status'], 202)
        self.assertEqual(self.handler.json_response.call_args.args[0]['run_id'], 'run')
        self.handler.read_json_body.return_value = {'id':'goal', 'run_id':'run'}
        with patch.object(scheduler.store, 'cancel_run') as stop:
            self.handler.handle_goal_action('/api/goals/stop')
        stop.assert_called_once_with('goal', 'run')

    def test_manual_conflict_returns_409(self):
        self.handler.read_json_body = Mock(return_value={'id':'goal'})
        with patch.object(self.handler_class.goal_scheduler, 'run_now', side_effect=GoalConflict('busy')):
            self.handler.handle_goal_action('/api/goals/run')
        self.assertEqual(self.handler.json_response.call_args.kwargs['status'], 409)

    def test_resume_http_action_preserves_run_id_and_confirmation(self):
        goal = {'id':'goal', 'run_id':'existing', 'started_at':100, 'next_run_at':3700}
        self.handler.read_json_body = Mock(return_value={'id':'goal', 'run_id':'existing', 'confirm_retry':True})
        with patch.object(self.handler_class.goal_scheduler, 'resume', return_value=goal) as resume:
            self.handler.handle_goal_action('/api/goals/resume')
        resume.assert_called_once_with('goal', 'existing', True)
        self.assertEqual(self.handler.json_response.call_args.kwargs['status'], 202)
        self.assertEqual(self.handler.json_response.call_args.args[0]['run_id'], 'existing')

    def test_task_entrypoint_executes_code_skill_only(self):
        from tests.test_code_skills import package
        goal = {'started_at':1, 'skill':package(), 'skill_version':1}
        with patch('wechat_agent.code_skill_web.execute_code_goal', return_value='done') as run:
            self.assertEqual(self.handler_class.goal_scheduler.execute(goal, Mock(), lambda:False), 'done')
        self.assertIs(run.call_args.args[2], goal)


class GoalSnapshotTests(unittest.TestCase):
    def test_bounded_reader_filters_dates_and_merges_shards_without_changing_default(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            state=web.AppState(**{key:root/key for key in ('db_storage','decrypted','keys','media_root','voice_cache','llm_config','qa_store','qa_index_cache','qa_search_db')})
            state.decrypted.mkdir()
            state.account='me'
            shards=[]
            for index in range(2):
                name=f'message_{index}.db'
                shards.append({'db':name,'table':'Msg_test'})
                with sqlite3.connect(state.decrypted/name) as conn:
                    conn.execute('CREATE TABLE Name2Id(user_name TEXT)')
                    conn.execute("INSERT INTO Name2Id VALUES('me')")
                    conn.execute('CREATE TABLE Msg_test(local_id INTEGER, server_id INTEGER, local_type INTEGER, real_sender_id INTEGER, create_time INTEGER, message_content TEXT)')
                    conn.executemany('INSERT INTO Msg_test VALUES(?,?,1,1,?,?)',[(i,i,i,f'text {i}') for i in range(100+index*10,110+index*10)])
            rec={'id':'friend','chat':'friend','type':'private','title':'Friend','shards':shards}
            all_items=web.collect_qa_items_for_chat(state,rec)
            bounded=web.collect_qa_items_for_chat(state,rec,since_ts=105,until_ts=114,max_items=3)
            self.assertEqual(len(all_items),20)
            self.assertEqual([m['timestamp'] for m in bounded],[112,113,114])
            self.assertTrue(all(m['mine'] for m in bounded))

            with sqlite3.connect(state.decrypted/shards[0]['db']) as conn:
                conn.execute("UPDATE Msg_test SET message_content=? WHERE local_id=105", ('x'*2000 + ' intern-tail',))
            full_text = web.collect_qa_items_for_chat(state, rec, since_ts=105, until_ts=105, max_text_chars=None)
            self.assertTrue(full_text[0]['text'].endswith('intern-tail'))
            regular = web.collect_qa_items_for_chat(state, rec, since_ts=105, until_ts=105)
            self.assertNotIn('intern-tail', regular[0]['text'])
            with self.assertRaises(GoalCancelled):
                web.collect_qa_items_for_chat(state, rec, check_cancelled=Mock(side_effect=GoalCancelled()))

    def test_full_task_forwarded_record_retains_items_after_eighty(self):
        record = {'title':'Forwarded', 'items':[{'content':f'unique-message-{i}'} for i in range(100)]}
        with patch.object(web, 'parse_forwarded_record', return_value=record):
            content = web.qa_text_from_raw_message(None, {}, 1, 'xml', 'app', None, full_text=True)
        self.assertIn('unique-message-99', content)


class GoalModelRequestTests(unittest.TestCase):
    def test_direct_and_wrapped_timeouts_are_readable_and_not_retried(self):
        for error in (TimeoutError('The read operation timed out'), urllib.error.URLError(TimeoutError('timeout'))):
            with self.subTest(error=type(error).__name__), patch.object(web.urllib.request,'urlopen',side_effect=error) as send:
                with self.assertRaisesRegex(TimeoutError, '300 秒'):
                    web.call_chat_payload({'base_url':'https://api.example.test','model':'fixture'},'fixture-only-key',[],timeout=300,tools=[])
                self.assertEqual(send.call_count, 1)

    def test_timeout_while_reading_response_is_also_reported(self):
        response_stream = Mock()
        response_stream.read.side_effect = TimeoutError('read stalled')
        response_context = Mock(__enter__=Mock(return_value=response_stream), __exit__=Mock(return_value=False))
        with patch.object(web.urllib.request, 'urlopen', return_value=response_context) as send:
            with self.assertRaisesRegex(TimeoutError, '300 秒'):
                web.call_chat_payload({'base_url':'https://api.example.test','model':'fixture'},'fixture-only-key',[],timeout=300,tools=[])
            self.assertEqual(send.call_count, 1)

    def test_tool_calls_use_existing_profile_without_unsupported_temperature(self):
        payload=response('search_messages',{'query':'intern'})
        profile={'base_url':'https://api.example.test/v1','model':'configured-model','temperature':0.2}
        with patch.object(web.urllib.request,'urlopen',return_value=io.BytesIO(json.dumps(payload).encode())) as send:
            result=web.call_chat_payload(profile,'fixture-only-key',[{'role':'user','content':'goal'}],tools=[{'type':'function'}],tool_choice='required',response_format=GOAL_RESULT_FORMAT)
        body=json.loads(send.call_args.args[0].data)
        self.assertEqual(body['model'],'configured-model')
        self.assertEqual(body['tool_choice'],'required')
        self.assertNotIn('temperature',body)
        self.assertEqual(result['usage']['total_tokens'],12)
        self.assertFalse(body['parallel_tool_calls'])
        self.assertNotIn('max_completion_tokens',body)
        self.assertEqual(body['response_format'], GOAL_RESULT_FORMAT)
        self.assertTrue(body['response_format']['json_schema']['strict'])

    def test_existing_text_completion_remains_compatible(self):
        with patch.object(web.urllib.request,'urlopen',return_value=io.BytesIO(json.dumps(response(text='answer')).encode())) as send:
            result=web.call_chat_completion({'base_url':'https://api.example.test/v1','model':'existing'},'fixture-only-key',[])
        self.assertEqual(result,'answer')
        self.assertNotIn('response_format', json.loads(send.call_args.args[0].data))

    def test_goal_api_error_does_not_persist_remote_error_body(self):
        error=urllib.error.HTTPError('https://api.example.test',400,'bad request',{},io.BytesIO(b'sensitive remote body'))
        with patch.object(web.urllib.request,'urlopen',side_effect=error):
            with self.assertRaises(RuntimeError) as raised:
                web.call_chat_payload({'base_url':'https://api.example.test','model':'existing'},'fixture-only-key',[],tools=[],response_format=GOAL_RESULT_FORMAT)
        self.assertIn('400',str(raised.exception))
        self.assertIn('Structured Outputs',str(raised.exception))
        self.assertNotIn('sensitive',str(raised.exception))


if __name__ == '__main__':
    unittest.main()
