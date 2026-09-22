"""Process-owned operations: HTTP connections only submit or observe work."""
from __future__ import annotations

import hashlib
import json
import threading
import time
import uuid
from collections import deque


class JobCancelled(BaseException):
    # Do not let model fallback/retry handlers swallow explicit cancellation.
    pass


class JobConflict(ValueError):
    pass


_current = threading.local()


def check_job_cancelled():
    job = getattr(_current, "job", None)
    if job is not None:
        job.check()


def wait_for_job_retry(seconds):
    job = getattr(_current, "job", None)
    if job is None:
        time.sleep(seconds)
    else:
        job.cancel.wait(seconds)
        job.check()


class Job:
    def __init__(self, job_id, kind, payload, resource):
        self.id, self.kind, self.resource = job_id, kind, resource
        self.fingerprint = hashlib.sha256(json.dumps([kind, payload], sort_keys=True).encode()).hexdigest()
        self.conversation_id = str(payload.get("conversation_id") or "")
        self.started_at = time.time()
        self.finished_at = None
        self.status = "running"
        self.cancel = threading.Event()
        self.lock = threading.RLock()
        self.events = deque(maxlen=160)
        self.sequence = 0
        self.result = None

    def check(self):
        if self.cancel.is_set():
            raise JobCancelled()

    def emit(self, event, **fields):
        with self.lock:
            self.check()
            self.sequence += 1
            value = {"event": event, **fields, "seq": self.sequence}
            self.events.append(value)
            if event in ("done", "error"):
                self.result = value

    def snapshot(self, after=0, include_events=True):
        with self.lock:
            value = {"id": self.id, "kind": self.kind, "conversation_id": self.conversation_id,
                     "status": self.status, "started_at": self.started_at, "finished_at": self.finished_at,
                     "cancel_requested": self.cancel.is_set(), "cursor": self.sequence}
            if include_events:
                value.update(events=[e for e in self.events if e["seq"] > after], result=self.result)
            return value


class JobRegistry:
    def __init__(self):
        self.lock = threading.RLock()
        self.jobs = {}

    def start(self, kind, payload, resource, work, request_id=None, on_finished=None):
        job_id = str(request_id or uuid.uuid4().hex)
        if not job_id or len(job_id) > 100:
            raise ValueError("无效的执行编号")
        job = Job(job_id, kind, payload, resource)
        with self.lock:
            old = self.jobs.get(job_id)
            if old:
                if old.fingerprint != job.fingerprint:
                    raise JobConflict("执行编号已被其他请求使用")
                return old
            if any(j.status == "running" and j.resource == resource for j in self.jobs.values()):
                raise JobConflict("该操作正在后台执行，请等待完成后再试")
            if sum(j.status == "running" for j in self.jobs.values()) >= 8:
                raise JobConflict("后台执行数量已达上限")
            # Answers and indexes have their own durable stores; retain bounded progress history.
            finished = [j for j in self.jobs.values() if j.status != "running"]
            for expired in sorted(finished, key=lambda j: j.finished_at or 0)[:-32]:
                self.jobs.pop(expired.id, None)
            self.jobs[job_id] = job
            threading.Thread(target=self._run, args=(job, work, on_finished), daemon=True, name="wechat-job").start()
        return job

    @staticmethod
    def _run(job, work, on_finished=None):
        _current.job = job
        try:
            work(job)
            with job.lock:
                job.check()
                if job.result is None:
                    job.emit("error", error="执行结束但没有返回结果")
                job.status = "completed" if job.result.get("event") == "done" and job.result.get("ok") else "failed"
        except JobCancelled:
            job.status = "cancelled"
        except Exception as exc:
            with job.lock:
                if not job.cancel.is_set():
                    job.emit("error", error=str(exc) if isinstance(exc, (RuntimeError, ValueError)) else "后台执行异常，请检查服务日志")
                job.status = "cancelled" if job.cancel.is_set() else "failed"
        finally:
            job.finished_at = time.time()
            _current.job = None
            if on_finished:
                try:
                    on_finished(job)
                except Exception as exc:
                    print(f"Background result persistence failed: {exc}")

    def get(self, job_id):
        with self.lock:
            job = self.jobs.get(job_id)
            if job is None:
                raise KeyError(job_id)
            return job

    def stop(self, job_id):
        job = self.get(job_id)
        with job.lock:
            if job.status == "running" and not (job.result and job.result.get("event") == "done"):
                job.cancel.set()
        return job

    def list(self):
        with self.lock:
            return [j.snapshot(include_events=False) for j in self.jobs.values()]


def run_preparation(stages, emit):
    """Keep stage sequencing on the server even when there is no browser observer."""
    voice_failures = 0
    for index, (label, run) in enumerate(stages, 1):
        check_job_cancelled()
        emit("stage", stage=index, message=label)
        result = None

        def forward(event, **fields):
            nonlocal result
            check_job_cancelled()
            if event in ("done", "error"):
                result = {"event": event, **fields}
            emit("stage_done" if event == "done" else event, stage=index, **fields)

        run(forward)
        if not result or result.get("event") != "done" or not result.get("ok"):
            raise RuntimeError((result or {}).get("error") or f"{label}未完成")
        if index == 1:
            voice_failures = int(result.get("failed") or 0)
    emit("done", ok=True, voice_failures=voice_failures)
