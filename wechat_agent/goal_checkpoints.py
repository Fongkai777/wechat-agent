"""Local task checkpoints and receipts for potentially billable model requests."""
from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from langgraph.checkpoint.sqlite import SqliteSaver


class GoalResumeRequired(RuntimeError):
    pass


class RecentSqliteSaver(SqliteSaver):
    """Retain the current checkpoint and parent, not an unbounded time-travel log."""
    def put(self, config, checkpoint, metadata, new_versions):
        result = super().put(config, checkpoint, metadata, new_versions)
        thread = str(config['configurable']['thread_id'])
        with self.lock, self.conn:
            self.conn.execute("""DELETE FROM checkpoints WHERE thread_id=? AND checkpoint_id NOT IN
                (SELECT checkpoint_id FROM checkpoints WHERE thread_id=? ORDER BY checkpoint_id DESC LIMIT 2)""", (thread, thread))
            self.conn.execute("""DELETE FROM writes WHERE thread_id=? AND checkpoint_id NOT IN
                (SELECT checkpoint_id FROM checkpoints WHERE thread_id=?)""", (thread, thread))
        return result


class ModelReceipts:
    def __init__(self, conn):
        self.conn = conn
        with conn:
            conn.execute("""CREATE TABLE IF NOT EXISTS model_receipts(
                round INTEGER PRIMARY KEY, status TEXT NOT NULL, payload TEXT NOT NULL DEFAULT '',
                uncertain_attempts INTEGER NOT NULL DEFAULT 0)""")

    def call(self, turn, invoke, allow_retry=False, keep_all=False):
        row = self.conn.execute("SELECT status,payload,uncertain_attempts FROM model_receipts WHERE round=?", (turn,)).fetchone()
        if row and row[0] == 'done':
            return json.loads(row[1]), row[2]
        if row and not allow_retry:
            raise GoalResumeRequired('上次模型请求结果未知，可能已计费；请确认后重试该请求')
        unknown = (row[2] + 1) if row else 0
        with self.conn:
            if not keep_all:
                self.conn.execute("DELETE FROM model_receipts WHERE round<? AND status='done'", (turn,))
            self.conn.execute("INSERT OR REPLACE INTO model_receipts VALUES (?, 'pending', '', ?)", (turn, unknown))
        payload = invoke()
        # Persist the response before returning to LangGraph. A process may die
        # after the network call but before the next graph checkpoint is written.
        with self.conn:
            self.conn.execute("UPDATE model_receipts SET status='done',payload=? WHERE round=?", (json.dumps(payload, ensure_ascii=False), turn))
        return payload, unknown


@contextmanager
def checkpoint_session(path=None):
    if path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
        os.close(fd)
    conn = sqlite3.connect(str(path) if path else ':memory:', check_same_thread=False, timeout=30)
    receipts_conn = sqlite3.connect(str(path), timeout=30) if path else conn
    try:
        saver = RecentSqliteSaver(conn)
        saver.setup()
        yield saver, ModelReceipts(receipts_conn)
    finally:
        if receipts_conn is not conn:
            receipts_conn.close()
        conn.close()


def checkpoint_info(path):
    path = Path(path)
    if not path.exists():
        return {'resumable': False, 'retry_confirmation_required': False}
    try:
        conn = sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True, timeout=5)
        try:
            present = bool(conn.execute('SELECT 1 FROM checkpoints LIMIT 1').fetchone())
            pending = bool(conn.execute("SELECT 1 FROM model_receipts WHERE status='pending' LIMIT 1").fetchone())
            return {'resumable': present, 'retry_confirmation_required': pending}
        finally:
            conn.close()
    except sqlite3.Error:
        return {'resumable': False, 'retry_confirmation_required': False}


def remove_checkpoint(path):
    for suffix in ('', '-wal', '-shm', '-journal'):
        Path(str(path) + suffix).unlink(missing_ok=True)
