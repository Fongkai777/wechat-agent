import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from wechat_agent import web
from wechat_agent.jobs import Job, JobConflict
from wechat_agent.rag_schedule import RagScheduler
from test_background_jobs import finished


class RagScheduleTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.path = Path(temp.name) / "schedule.sqlite3"
        self.now = 1000
        self.jobs = {}
        self.submissions = []

        def submit(job_id):
            self.submissions.append(job_id)
            self.jobs[job_id] = Job(job_id, "/api/rag/prepare", {}, "maintenance")

        self.submit = submit
        self.scheduler = self.reopen()

    def reopen(self):
        return RagScheduler(self.path, self.submit, self.jobs.__getitem__, lambda: self.now)

    def enable(self, value=1, unit="hours"):
        return self.scheduler.save(dict(enabled=True, interval_value=value, interval_unit=unit))

    def complete(self, status="completed", result=None):
        job = self.jobs[self.submissions[-1]]
        job.status, job.finished_at = status, self.now
        job.result = result or {"event": "done", "ok": True}

    def test_disabled_default_and_hours_days_are_durable(self):
        self.assertFalse(self.scheduler.snapshot()["enabled"])
        self.scheduler.tick()
        self.assertEqual(self.submissions, [])
        saved = self.enable(2, "days")
        self.assertEqual(saved["next_run_at"], 1000 + 2 * 86400)
        self.assertEqual(self.reopen().snapshot()["next_run_at"], saved["next_run_at"])
        self.assertEqual(self.enable(3)["next_run_at"], 1000 + 3 * 3600)

    def test_invalid_settings_do_not_mutate_schedule(self):
        for values in (dict(enabled="true"), dict(interval_value=True), dict(interval_value=0),
                       dict(interval_value=1.5), dict(interval_value="2"), dict(interval_value=8761),
                       dict(interval_unit="weeks"), dict(interval_unit="days", interval_value=366)):
            with self.subTest(values=values), self.assertRaises(ValueError):
                self.scheduler.save({"enabled": True, "interval_value": 1, "interval_unit": "hours", **values})
        self.assertFalse(self.scheduler.snapshot()["enabled"])

    def test_unchanged_save_preserves_deadline_change_resets_it(self):
        saved = self.enable()
        self.now += 200
        self.assertEqual(self.enable()["next_run_at"], saved["next_run_at"])
        self.assertEqual(self.enable(2)["next_run_at"], self.now + 7200)

    def test_due_once_no_overlap_and_completion_starts_next_interval(self):
        self.enable()
        self.now = 4599
        self.scheduler.tick()
        self.assertEqual(self.submissions, [])
        self.now = 4600
        self.scheduler.tick()
        self.now += 8000
        self.scheduler.tick()
        self.assertEqual(len(self.submissions), 1)
        self.complete()
        self.scheduler.tick()
        self.assertEqual(self.scheduler.snapshot()["next_run_at"], self.now + 3600)
        self.scheduler.tick()
        self.assertEqual(len(self.submissions), 1)

    def test_busy_maintenance_waits_without_consuming_the_due_run(self):
        due = self.enable()["next_run_at"]
        self.now = due
        with patch.object(self.scheduler, "submit", side_effect=JobConflict("busy")):
            self.scheduler.tick()
            self.scheduler.tick()
        row = self.scheduler.snapshot()
        self.assertTrue(row["waiting"])
        self.assertEqual(row["next_run_at"], due)
        self.assertIsNone(row["last_started_at"])
        self.scheduler.tick()
        self.assertFalse(self.scheduler.snapshot()["waiting"])
        self.assertEqual(len(self.submissions), 1)

    def test_pausing_does_not_cancel_active_run_or_schedule_another(self):
        self.enable()
        self.now += 3600
        self.scheduler.tick()
        self.scheduler.save(dict(enabled=False, interval_value=1, interval_unit="hours"))
        job = self.jobs[self.submissions[0]]
        self.assertFalse(job.cancel.is_set())
        self.complete()
        self.scheduler.tick()
        self.now += 100000
        self.scheduler.tick()
        self.assertIsNone(self.scheduler.snapshot()["next_run_at"])
        self.assertEqual(len(self.submissions), 1)

    def test_failure_and_cancel_wait_for_next_period(self):
        for status in ("failed", "cancelled"):
            self.enable()
            self.now = self.scheduler.snapshot()["next_run_at"]
            self.scheduler.tick()
            count = len(self.submissions)
            self.complete(status, {"error": "fixture error"})
            self.scheduler.tick()
            self.assertEqual(self.scheduler.snapshot()["last_status"], status)
            self.assertEqual(self.scheduler.snapshot()["last_error"], "fixture error")
            self.scheduler.tick()
            self.assertEqual(len(self.submissions), count)

    def test_restart_catches_up_once_not_every_missed_period(self):
        self.enable()
        self.now += 3600 * 200
        self.scheduler = self.reopen()
        self.scheduler.tick()
        self.scheduler.tick()
        self.assertEqual(len(self.submissions), 1)

    def test_restart_interrupted_claim_does_not_resubmit_immediately(self):
        self.enable()
        self.now += 3600
        self.scheduler.tick()
        self.jobs.clear()
        self.scheduler = self.reopen()
        self.scheduler.tick()
        self.scheduler.tick()
        self.assertEqual(self.scheduler.snapshot()["last_status"], "interrupted")
        self.assertEqual(len(self.submissions), 1)

    def test_claim_is_persisted_before_dispatch_and_submission_failure_backoff(self):
        self.enable()
        self.now += 3600
        def fail(job_id):
            self.assertEqual(self.reopen().snapshot()["job_id"], job_id)
            raise RuntimeError("fixture start error")
        with patch.object(self.scheduler, "submit", side_effect=fail), self.assertRaises(RuntimeError):
            self.scheduler.tick()
        self.assertEqual(self.scheduler.snapshot()["last_status"], "failed")
        self.scheduler.tick()
        self.assertEqual(self.submissions, [])

    def test_partial_voice_failure_is_visible(self):
        self.enable()
        self.now += 3600
        self.scheduler.tick()
        self.complete(result={"ok": True, "voice_failures": 2})
        self.scheduler.tick()
        self.assertIn("2 条语音", self.scheduler.snapshot()["last_error"])

    def test_timer_runs_without_http_observers_and_stops(self):
        self.enable()
        self.now += 3600
        started = threading.Event()
        with patch.object(self.scheduler, "submit", side_effect=lambda _: started.set()):
            self.scheduler.start()
            self.assertTrue(started.wait(2))
            self.scheduler.stop()
        self.assertFalse(self.scheduler.thread.is_alive())

    def manual_result(self, status="completed", error="", kind="/api/rag/prepare"):
        job = Job("manual-" + str(self.now), kind, {}, "maintenance")
        job.status = status
        job.finished_at = self.now
        job.result = {"event": "done" if status == "completed" else "error", "ok": status == "completed", "error": error}
        return job

    def test_manual_success_replaces_old_error_durably_without_resetting_timer(self):
        self.enable()
        self.now += 3600
        self.scheduler.tick()
        self.complete("failed", {"error": "old DNS error"})
        self.scheduler.tick()
        due = self.scheduler.snapshot()["next_run_at"]
        self.now += 20
        self.scheduler.record_result(self.manual_result())
        current = self.reopen().snapshot()
        self.assertEqual(current["latest_run"]["status"], "completed")
        self.assertEqual(current["latest_run"]["trigger"], "manual")
        self.assertEqual(current["latest_run"]["error"], "")
        self.assertEqual(current["next_run_at"], due)
        self.assertEqual(current["last_error"], "old DNS error")

    def test_later_failure_replaces_success_but_stale_callback_cannot_replace_it(self):
        self.scheduler.record_result(self.manual_result())
        old = self.manual_result()
        self.now += 20
        self.scheduler.record_result(self.manual_result("failed", "new failure"))
        self.scheduler.record_result(old)
        self.assertEqual(self.scheduler.snapshot()["latest_run"]["error"], "new failure")

    def test_unrelated_success_does_not_hide_a_failed_preparation(self):
        self.scheduler.record_result(self.manual_result("failed", "preparation failed"))
        self.now += 20
        self.scheduler.record_result(self.manual_result(kind="/api/rag/search"))
        self.assertEqual(self.scheduler.snapshot()["latest_run"]["error"], "preparation failed")

    def test_later_scheduled_completion_supersedes_saved_manual_result(self):
        self.scheduler.record_result(self.manual_result())
        self.enable()
        self.now += 3600
        self.scheduler.tick()
        self.complete("failed", {"error": "scheduled failure"})
        self.scheduler.tick()
        latest = self.scheduler.snapshot()["latest_run"]
        self.assertEqual(latest["trigger"], "scheduled")
        self.assertEqual(latest["error"], "scheduled failure")


class RagScheduleHTTPTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        self.state = web.AppState(**{key: root / key for key in (
            "db_storage", "decrypted", "keys", "media_root", "voice_cache", "llm_config",
            "qa_store", "qa_index_cache", "qa_search_db")})
        with patch.object(web.threading.Thread, "start"):
            self.cls = web.make_handler(self.state)
        self.handler = self.cls.__new__(self.cls)
        self.handler.json_response = Mock()
        self.scheduler = self.cls.rag_scheduler
        self.now = 1000
        self.scheduler.clock = lambda: self.now

    def test_settings_get_post_validation_and_no_immediate_execution(self):
        self.handler.path = "/api/rag/schedule"
        self.handler.do_GET()
        self.assertFalse(self.handler.json_response.call_args.args[0]["schedule"]["enabled"])
        self.handler.read_json_body = Mock(return_value={"enabled": True, "interval_value": 2, "interval_unit": "hours"})
        self.handler.do_POST()
        self.assertEqual(self.handler.json_response.call_args.args[0]["schedule"]["next_run_at"], 8200)
        self.assertEqual(self.cls.background_jobs.list(), [])
        self.handler.read_json_body.return_value["interval_value"] = -1
        self.handler.do_POST()
        self.assertEqual(self.handler.json_response.call_args.kwargs["status"], 400)

    def test_scheduled_pipeline_reuses_incremental_stages_and_manual_job_lock(self):
        self.scheduler.save(dict(enabled=True, interval_value=1, interval_unit="hours"))
        self.now += 3600
        stages = []
        ready, release = threading.Event(), threading.Event()
        def voice(payload, emit):
            stages.append(("voice", payload))
            ready.set()
            release.wait(2)
            emit("done", ok=True)
        def stage(name):
            return lambda payload, emit: (stages.append((name, payload)), emit("done", ok=True))
        with patch.object(self.cls, "run_transcribe_all_voices_stream", side_effect=voice), \
             patch.object(self.cls, "run_rag_rebuild_stream", side_effect=stage("text")), \
             patch.object(self.cls, "run_rag_embedding_rebuild_stream", side_effect=stage("semantic")):
            self.scheduler.tick()
            self.assertTrue(ready.wait(2))
            job = self.cls.background_jobs.get(self.scheduler.snapshot()["job_id"])
            self.handler.path = "/api/jobs/start"
            self.handler.read_json_body = Mock(return_value={"kind": "/api/rag/prepare", "payload": {}})
            self.handler.do_POST()
            self.assertEqual(self.handler.json_response.call_args.kwargs["status"], 409)
            release.set()
            self.assertEqual(finished(job)["status"], "completed")
        self.scheduler.tick()
        self.assertEqual(stages, [("voice", {"force": False}), ("text", {"full": False}), ("semantic", {"full": False})])
        self.assertEqual(self.scheduler.snapshot()["last_status"], "completed")

    def test_existing_manual_job_defers_scheduled_run(self):
        self.scheduler.save(dict(enabled=True, interval_value=1, interval_unit="hours"))
        ready, release = threading.Event(), threading.Event()
        def work(job):
            ready.set()
            release.wait(2)
            job.emit("done", ok=True)
        job = self.cls.background_jobs.start("/api/rag/rebuild_stream", {}, "maintenance", work)
        self.assertTrue(ready.wait(2))
        try:
            self.now += 3600
            self.scheduler.tick()
            self.assertTrue(self.scheduler.snapshot()["waiting"])
            self.assertEqual(len(self.cls.background_jobs.list()), 1)
        finally:
            release.set()
            finished(job)

    def test_manual_http_job_persists_latest_result_without_a_browser_observer(self):
        recorded = threading.Event()
        record = self.scheduler.record_result
        def on_finished(job):
            record(job)
            recorded.set()
        self.handler.path = "/api/jobs/start"
        self.handler.read_json_body = Mock(return_value={"kind": "/api/rag/prepare", "payload": {}})
        with patch.object(self.scheduler, "record_result", side_effect=on_finished), \
                patch.object(self.handler, "run_rag_preparation", side_effect=lambda payload, emit: emit("done", ok=True)):
            self.handler.do_POST()
            self.assertTrue(recorded.wait(2))
        latest = self.scheduler.snapshot()["latest_run"]
        self.assertEqual(latest["status"], "completed")
        self.assertEqual(latest["trigger"], "manual")
        self.assertIsNone(self.scheduler.snapshot()["next_run_at"])
