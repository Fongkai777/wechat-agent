"""A durable, single-server schedule for the existing RAG preparation job."""
from __future__ import annotations

import sqlite3
import json
import threading
import time
import uuid

from .jobs import JobConflict


class RagScheduler:
    def __init__(self, path, submit, get_job, clock=time.time):
        self.path, self.submit, self.get_job, self.clock = path, submit, get_job, clock
        self.lock = threading.RLock()
        self.stopped = threading.Event()
        self.thread = None
        self.waiting = False
        self.error = ""
        path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(path) as conn:
            conn.execute("""CREATE TABLE IF NOT EXISTS schedule (
                id INTEGER PRIMARY KEY CHECK (id = 1), enabled INTEGER NOT NULL DEFAULT 0,
                interval_value INTEGER NOT NULL DEFAULT 1, interval_unit TEXT NOT NULL DEFAULT 'days',
                next_run_at REAL, last_started_at REAL, last_finished_at REAL,
                last_status TEXT NOT NULL DEFAULT '', last_error TEXT NOT NULL DEFAULT '',
                job_id TEXT NOT NULL DEFAULT '')""")
            conn.execute("INSERT OR IGNORE INTO schedule (id) VALUES (1)")
            conn.execute("CREATE TABLE IF NOT EXISTS preparation_result (id INTEGER PRIMARY KEY CHECK (id=1), data TEXT NOT NULL)")

    def _read(self):
        with sqlite3.connect(self.path) as conn:
            conn.row_factory = sqlite3.Row
            row = dict(conn.execute("SELECT * FROM schedule WHERE id = 1").fetchone())
        row.pop("id")
        row["enabled"] = bool(row["enabled"])
        return row

    def _write(self, row):
        with sqlite3.connect(self.path) as conn:
            conn.execute("""UPDATE schedule SET enabled=:enabled, interval_value=:interval_value,
                interval_unit=:interval_unit, next_run_at=:next_run_at, last_started_at=:last_started_at,
                last_finished_at=:last_finished_at, last_status=:last_status, last_error=:last_error,
                job_id=:job_id WHERE id=1""", row)

    @staticmethod
    def seconds(row):
        return row["interval_value"] * (3600 if row["interval_unit"] == "hours" else 86400)

    def snapshot(self):
        with self.lock:
            row = self._read()
            job = None
            if row["last_status"] == "running" and row["job_id"]:
                try:
                    job = self.get_job(row["job_id"]).snapshot(include_events=False)
                except KeyError:
                    pass
            with sqlite3.connect(self.path) as conn:
                saved = conn.execute("SELECT data FROM preparation_result WHERE id=1").fetchone()
            latest = json.loads(saved[0]) if saved else None
            if row["last_finished_at"] and (not latest or row["last_finished_at"] > latest["finished_at"]):
                latest = {"id": row["job_id"], "status": row["last_status"], "error": row["last_error"],
                          "finished_at": row["last_finished_at"], "trigger": "scheduled"}
            return {**row, "latest_run": latest, "waiting": self.waiting, "scheduler_error": self.error,
                    "scheduler_running": bool(self.thread and self.thread.is_alive()), "job": job}

    def record_result(self, job):
        result = job.snapshot()
        if result["kind"] != "/api/rag/prepare" or result["status"] == "running" or not result["finished_at"]:
            return
        outcome = result.get("result") or {}
        error = str(outcome.get("error") or "")[:1000]
        if result["status"] == "completed" and outcome.get("voice_failures"):
            error = f"{outcome['voice_failures']} 条语音转写失败，索引已更新"
        with self.lock:
            row = self._read()
            latest = {"id": result["id"], "status": result["status"], "error": error,
                      "finished_at": result["finished_at"],
                      "trigger": "scheduled" if result["id"] == row["job_id"] else "manual"}
            # Completion callbacks may arrive out of order; never replace a newer result.
            with sqlite3.connect(self.path) as conn:
                old = conn.execute("SELECT data FROM preparation_result WHERE id=1").fetchone()
                if old and json.loads(old[0])["finished_at"] > latest["finished_at"]:
                    return
                conn.execute("INSERT OR REPLACE INTO preparation_result VALUES (1, ?)", (json.dumps(latest, ensure_ascii=False),))

    def save(self, data):
        if not isinstance(data, dict) or type(data.get("enabled")) is not bool:
            raise ValueError("请选择是否开启定时更新")
        value, unit = data.get("interval_value"), data.get("interval_unit")
        if unit not in ("hours", "days"):
            raise ValueError("更新周期单位须为小时或天")
        maximum = 8760 if unit == "hours" else 365
        if type(value) is not int or not 1 <= value <= maximum:
            raise ValueError(f"更新周期须为 1 至 {maximum} 的整数")
        with self.lock:
            row = self._read()
            changed = any(row[key] != data[key] for key in ("enabled", "interval_value", "interval_unit"))
            row.update(enabled=data["enabled"], interval_value=value, interval_unit=unit)
            if changed:
                row["next_run_at"] = self.clock() + self.seconds(row) if row["enabled"] else None
                self.waiting = False
            self._write(row)
            return self.snapshot()

    def tick(self):
        with self.lock:
            row = self._read()
            now = self.clock()
            if row["last_status"] == "running":
                try:
                    job = self.get_job(row["job_id"]).snapshot()
                except KeyError:
                    job = {"status": "interrupted", "finished_at": now,
                           "result": {"error": "服务重启，上次执行已中断；将在下一周期增量更新"}}
                if job["status"] == "running":
                    return
                result = job.get("result") or {}
                finished = job.get("finished_at") or now
                row.update(last_status=job["status"], last_finished_at=finished,
                           last_error=str(result.get("error") or "")[:1000],
                           next_run_at=finished + self.seconds(row) if row["enabled"] else None)
                if job["status"] == "completed" and result.get("voice_failures"):
                    row["last_error"] = f"{result['voice_failures']} 条语音转写失败，索引已更新"
                self._write(row)
                # Never replay a backlog or immediately restart a failed/long-running job.
                return
            if not row["enabled"] or not row["next_run_at"] or row["next_run_at"] > now:
                self.waiting = False
                return
            previous = dict(row)
            row.update(job_id=uuid.uuid4().hex, last_status="running", last_error="",
                       last_started_at=now, last_finished_at=None, next_run_at=now + self.seconds(row))
            # Persist the claim before starting work, including across a server crash.
            self._write(row)
            try:
                self.submit(row["job_id"])
                self.waiting = False
            except JobConflict:
                self._write(previous)
                self.waiting = True
            except Exception:
                row.update(last_status="failed", last_finished_at=now, last_error="无法启动定时更新，请检查服务日志")
                self._write(row)
                raise

    def start(self):
        if self.thread and self.thread.is_alive():
            return
        self.stopped.clear()

        def loop():
            while not self.stopped.is_set():
                try:
                    self.tick()
                    self.error = ""
                except Exception as exc:
                    self.error = "定时调度异常，请检查服务日志"
                    print(f"RAG scheduler failed: {exc}")
                self.stopped.wait(5)

        self.thread = threading.Thread(target=loop, name="wechat-rag-schedule", daemon=True)
        self.thread.start()

    def stop(self):
        self.stopped.set()
        if self.thread:
            self.thread.join(timeout=6)
