import io
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from wechat_agent import web
from wechat_agent.jobs import JobRegistry, JobConflict, JobCancelled, check_job_cancelled, run_preparation


def finished(job):
    for _ in range(300):
        if job.finished_at is not None:
            return job.snapshot()
        time.sleep(.005)
    raise AssertionError("Background job did not finish")


class BackgroundJobTests(unittest.TestCase):
    def test_no_observer_and_reconnect_do_not_cancel_or_repeat_work(self):
        registry = JobRegistry()
        ready, release = threading.Event(), threading.Event()
        calls = []

        def work(job):
            calls.append(1)
            job.emit("progress", message="working")
            ready.set()
            release.wait(2)
            job.emit("done", ok=True, answer="saved")

        job = registry.start("qa", {}, "qa:1", work, "same-request")
        self.assertTrue(ready.wait(2))
        self.assertIs(registry.start("qa", {}, "qa:1", work, "same-request"), job)
        cursor = job.snapshot()["cursor"]
        with self.assertRaises(JobConflict):
            registry.start("qa", {}, "qa:1", work)
        release.set()
        self.assertEqual(finished(job)["status"], "completed")
        self.assertEqual(job.snapshot(cursor)["events"][0]["answer"], "saved")
        self.assertEqual(calls, [1])
        self.assertIs(registry.start("qa", {}, "qa:1", work, "same-request"), job)
        with self.assertRaises(JobConflict):
            registry.start("qa", {"different": True}, "qa:1", work, "same-request")

    def test_explicit_stop_prevents_later_api_calls(self):
        registry = JobRegistry()
        ready, release = threading.Event(), threading.Event()

        def work(job):
            ready.set()
            release.wait(2)
            web.call_chat_payload({"model": "test", "base_url": "https://example.test"}, "fixture", [])

        with patch.object(web.urllib.request, "urlopen") as api:
            job = registry.start("qa", {}, "qa:1", work)
            self.assertTrue(ready.wait(2))
            registry.stop(job.id)
            release.set()
            self.assertEqual(finished(job)["status"], "cancelled")
            api.assert_not_called()

    def test_bounded_progress_keeps_final_result(self):
        def work(job):
            for i in range(300):
                job.emit("progress", current=i)
            job.emit("done", ok=True)
        job = JobRegistry().start("index", {}, "maintenance", work)
        result = finished(job)
        self.assertEqual(len(result["events"]), 160)
        self.assertEqual(result["cursor"], 301)
        self.assertTrue(result["result"]["ok"])

    def test_pipeline_runs_in_background_and_stops_after_failed_stage(self):
        for fail in (False, True):
            with self.subTest(fail=fail):
                called = []
                def stage(number):
                    def run(emit):
                        called.append(number)
                        if fail and number == 2:
                            emit("error", error="index failed")
                        else:
                            emit("done", ok=True, failed=2 if number == 1 else 0)
                    return run
                job = JobRegistry().start("prepare", {}, "maintenance", lambda j: run_preparation(
                    [(str(i), stage(i)) for i in (1, 2, 3)], j.emit))
                result = finished(job)
                self.assertEqual(called, [1, 2] if fail else [1, 2, 3])
                self.assertEqual(result["status"], "failed" if fail else "completed")
                if not fail:
                    self.assertEqual(result["result"]["voice_failures"], 2)

    def test_stop_pipeline_does_not_start_next_stage(self):
        registry = JobRegistry()
        ready, release = threading.Event(), threading.Event()
        later = Mock()
        def voice(emit):
            ready.set()
            release.wait(2)
            emit("done", ok=True)
        job = registry.start("prepare", {}, "maintenance", lambda j: run_preparation([
            ("voice", voice), ("text", later), ("semantic", later)], j.emit))
        self.assertTrue(ready.wait(2))
        registry.stop(job.id)
        release.set()
        self.assertEqual(finished(job)["status"], "cancelled")
        later.assert_not_called()


class BackgroundHTTPTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        self.state = web.AppState(**{key: root/key for key in (
            "db_storage", "decrypted", "keys", "media_root", "voice_cache", "llm_config", "qa_store", "qa_index_cache", "qa_search_db")})
        with patch.object(web.threading.Thread, "start"):
            self.cls = web.make_handler(self.state)
        self.handler = self.cls.__new__(self.cls)
        self.handler.json_response = Mock()

    def test_legacy_broken_pipe_detaches_only_observer(self):
        ready, release = threading.Event(), threading.Event()
        def run(payload, emit):
            emit("progress", message="working")
            ready.set()
            release.wait(2)
            emit("done", ok=True, answer="finished offline")
        self.handler.run_qa_stream = run
        self.handler.read_json_body = Mock(return_value={"question": "test"})
        self.handler.send_response = self.handler.send_header = self.handler.end_headers = Mock()
        self.handler.wfile = Mock()
        self.handler.wfile.write.side_effect = BrokenPipeError()
        self.handler.handle_job_request("/api/qa_stream")
        self.assertTrue(ready.wait(2))
        job = next(iter(self.cls.background_jobs.jobs.values()))
        self.assertFalse(job.cancel.is_set())
        release.set()
        self.assertEqual(finished(job)["result"]["answer"], "finished offline")

    def qa_patches(self):
        config = {"qa": {"model": "test", "base_url": "https://example.test", "api_key": "fixture"}}
        for target, value in (
            ("load_llm_config", config), ("load_or_build_qa_index", {"message_count": 1, "corpus": [{}]}),
            ("select_qa_context_with_diagnostics", ([{"text": "fixture"}], {})), ("build_person_summary", ""),
        ):
            mocked = patch.object(web, target, return_value=value)
            mocked.start()
            self.addCleanup(mocked.stop)

    def test_qa_is_saved_without_browser_and_stop_is_persisted(self):
        self.qa_patches()
        for cancel in (False, True):
            with self.subTest(cancel=cancel):
                ready, release = threading.Event(), threading.Event()
                def answer(*args, **kwargs):
                    ready.set()
                    release.wait(2)
                    return {"paragraphs": [{"kind": "answer", "text": "fixture answer", "source_refs": [1]}]}
                conversation = "cancelled" if cancel else "completed"
                self.handler.path = "/api/jobs/start"
                self.handler.read_json_body = Mock(return_value={"kind": "/api/qa_stream", "payload": {
                    "question": "fixture question", "conversation_id": conversation}})
                with patch.object(web, "call_qa_answer", side_effect=answer):
                    self.handler.do_POST()
                    job = self.cls.background_jobs.get(self.handler.json_response.call_args.args[0]["job"]["id"])
                    self.assertTrue(ready.wait(2))
                    stored = web.get_qa_conversation(self.state.qa_store, conversation)
                    self.assertTrue(stored["messages"][-1]["pending"])
                    if cancel:
                        self.cls.background_jobs.stop(job.id)
                    release.set()
                    self.assertEqual(finished(job)["status"], "cancelled" if cancel else "completed")
                stored = web.get_qa_conversation(self.state.qa_store, conversation)
                self.assertEqual(len(stored["messages"]), 2)
                self.assertNotIn("pending", stored["messages"][-1])
                self.assertEqual(stored["messages"][-1]["content"], "已停止回答" if cancel else "fixture answer [1]")

    def test_qa_failure_and_service_restart_remain_in_history(self):
        self.qa_patches()
        with patch.object(web, "call_qa_answer", side_effect=RuntimeError("fixture model failure")):
            self.handler.run_qa_stream({"question": "test", "conversation_id": "failed"}, Mock())
        stored = web.get_qa_conversation(self.state.qa_store, "failed")
        self.assertEqual(stored["messages"][-1]["error"], "fixture model failure")
        web.save_qa_conversation(self.state.qa_store, "interrupted", [{"role": "assistant", "content": "working", "pending": True}])
        web.interrupt_pending_qa(self.state.qa_store)
        stored = web.get_qa_conversation(self.state.qa_store, "interrupted")
        self.assertEqual(stored["messages"][-1]["error"], "服务已重启")
        self.assertNotIn("pending", stored["messages"][-1])

    def test_qa_completion_preserves_renamed_title(self):
        web.save_qa_conversation(self.state.qa_store, "one", [{"role": "user", "content": "question"}])
        web.rename_qa_conversation(self.state.qa_store, "one", "Custom title")
        result = web.save_qa_conversation(self.state.qa_store, "one", [{"role": "assistant", "content": "answer"}])
        self.assertEqual(result["title"], "Custom title")
