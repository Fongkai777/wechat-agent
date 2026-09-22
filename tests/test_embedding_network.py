import io
import json
import socket
import ssl
import threading
import unittest
import urllib.error
from unittest.mock import Mock, patch

from wechat_agent import web
from wechat_agent.jobs import JobRegistry
from test_background_jobs import finished


class EmbeddingNetworkTests(unittest.TestCase):
    profile = {"base_url": "https://api.example.test/v1", "model": "test-embedding"}

    def response(self):
        return io.BytesIO(json.dumps({"data": [{"index": 0, "embedding": [0.1, 0.2]}],
                                     "usage": {"total_tokens": 2}}).encode())

    def dns_error(self):
        return urllib.error.URLError(socket.gaierror(8, "nodename nor servname provided, or not known"))

    def test_dns_failure_retries_current_batch_with_backoff_and_progress(self):
        progress = Mock()
        with patch.object(web.urllib.request, "urlopen", side_effect=[self.dns_error(), self.dns_error(), self.response()]) as api, \
                patch.object(web, "wait_for_job_retry") as wait, patch.object(web.random, "uniform", return_value=0):
            vectors, usage = web.call_embeddings(self.profile, "test-only", ["fixture"], progress=progress)
        self.assertEqual(vectors, [[0.1, 0.2]])
        self.assertEqual(usage["total_tokens"], 2)
        self.assertEqual(api.call_count, 3)
        self.assertEqual([c.args[0] for c in wait.call_args_list], [2, 4])
        self.assertIs(api.call_args_list[0].args[0], api.call_args_list[2].args[0])
        self.assertIn("2/2", progress.call_args.kwargs["message"])

    def test_persistent_dns_error_is_bounded_and_identifies_host_not_credentials(self):
        with patch.object(web.urllib.request, "urlopen", side_effect=self.dns_error()) as api, \
                patch.object(web, "wait_for_job_retry"), self.assertRaises(RuntimeError) as caught:
            web.call_embeddings(self.profile, "test-only", ["private fixture text"])
        self.assertEqual(api.call_count, 3)
        self.assertIn("api.example.test", str(caught.exception))
        self.assertIn("已重试 2 次", str(caught.exception))
        self.assertNotIn("private fixture", str(caught.exception))
        self.assertNotIn("test-only", str(caught.exception))

    def test_auth_bad_request_quota_and_http_errors_do_not_trigger_dns_retries(self):
        for status in (400, 401, 403, 429, 500):
            with self.subTest(status=status):
                error = urllib.error.HTTPError("https://api.example.test", status, "fixture", {}, io.BytesIO(b'{}'))
                with patch.object(web.urllib.request, "urlopen", side_effect=error) as api, \
                        patch.object(web, "wait_for_job_retry") as wait, self.assertRaises(RuntimeError):
                    web.call_embeddings(self.profile, "test-only", ["fixture"])
                self.assertEqual(api.call_count, 1)
                wait.assert_not_called()

    def test_ambiguous_timeout_and_certificate_errors_do_not_replay_paid_requests(self):
        for error, message in ((TimeoutError(), "超时"), (urllib.error.URLError(socket.timeout()), "超时"),
                               (urllib.error.URLError(ssl.SSLCertVerificationError()), "证书验证失败"),
                               (urllib.error.URLError(ConnectionResetError()), "服务连接失败")):
            with self.subTest(error=error), patch.object(web.urllib.request, "urlopen", side_effect=error) as api, \
                    patch.object(web, "wait_for_job_retry") as wait, self.assertRaisesRegex(RuntimeError, message):
                web.call_embeddings(self.profile, "test-only", ["fixture"])
            self.assertEqual(api.call_count, 1)
            wait.assert_not_called()

    def test_invalid_urls_fail_before_network_and_do_not_leak_userinfo(self):
        for url in ("api.example.test/v1", "https://bad host/v1", "file:///tmp/key", "https://user:secret@example.test/v1", "https://example.test:bad/v1"):
            with self.subTest(url=url), patch.object(web.urllib.request, "urlopen") as api, \
                    self.assertRaisesRegex(RuntimeError, "Base URL 无效") as caught:
                web.call_embeddings({**self.profile, "base_url": url}, "test-only", ["fixture"])
            api.assert_not_called()
            self.assertNotIn("secret", str(caught.exception))

    def test_cancellation_interrupts_retry_delay_and_prevents_next_attempt(self):
        ready = threading.Event()
        registry = JobRegistry()
        def run(job):
            web.call_embeddings(self.profile, "test-only", ["fixture"], progress=lambda *a, **kw: ready.set())
        with patch.object(web.urllib.request, "urlopen", side_effect=self.dns_error()) as api:
            job = registry.start("/api/rag/prepare", {}, "maintenance", run)
            self.assertTrue(ready.wait(2))
            registry.stop(job.id)
            self.assertEqual(finished(job)["status"], "cancelled")
            self.assertEqual(api.call_count, 1)
