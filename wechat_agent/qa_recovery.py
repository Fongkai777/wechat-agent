"""Private Q&A snapshots and nested call receipts, independent of browser state."""
from __future__ import annotations

import functools
import hashlib
import inspect
import json
import re
import sqlite3
from contextlib import closing, contextmanager
from contextvars import ContextVar
from pathlib import Path

from .goal_checkpoints import GoalResumeRequired, checkpoint_info, remove_checkpoint


_scope = ContextVar('qa_receipts', default=None)


def checkpoint_path(store, turn_id):
    if not re.fullmatch(r'[0-9a-f]{32}', str(turn_id)):
        raise ValueError('无效的回答标识')
    return Path(store).parent / 'qa_checkpoints' / (turn_id + '.sqlite3')


def recovery_info(store, message):
    result = {'resumable': False, 'retry_confirmation_required': False}
    if not message.get('error') or message.get('pending') or message.get('stopped'):
        return result
    try:
        path = checkpoint_path(store, message.get('turn_id'))
        result = checkpoint_info(path)
        if result['resumable']:
            with closing(sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True)) as conn:
                result['retry_confirmation_required'] = bool(conn.execute(
                    "SELECT 1 FROM qa_calls WHERE status='pending' AND billable=1 LIMIT 1").fetchone())
    except (ValueError, sqlite3.Error):
        return {'resumable': False, 'retry_confirmation_required': False}
    return result


def discard_checkpoint(store, turn_id):
    try:
        remove_checkpoint(checkpoint_path(store, turn_id))
    except (ValueError, OSError):
        pass


def read_snapshot(path):
    with closing(sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True)) as conn:
        row = conn.execute('SELECT payload FROM qa_input WHERE id=1').fetchone()
        if not row:
            raise ValueError('原始问答快照不存在')
        return json.loads(row[0])


def frozen_input(conn, value, resume):
    with conn:
        conn.execute('CREATE TABLE IF NOT EXISTS qa_input(id INTEGER PRIMARY KEY, payload TEXT NOT NULL)')
        if not resume:
            conn.execute('INSERT INTO qa_input VALUES(1, ?)', (json.dumps(value, ensure_ascii=False),))
    row = conn.execute('SELECT payload FROM qa_input WHERE id=1').fetchone()
    if not row:
        raise ValueError('原始问答快照不存在')
    saved = json.loads(row[0])
    if saved.get('identity') != value.get('identity'):
        raise ValueError('模型配置或数据来源已改变，请恢复原配置后继续，或重新提问')
    return saved


class CallReceipts:
    def __init__(self, conn, allow_retry=False):
        self.conn = conn
        with conn:
            conn.execute('''CREATE TABLE IF NOT EXISTS qa_calls(
                id TEXT PRIMARY KEY, status TEXT NOT NULL, payload TEXT NOT NULL DEFAULT '',
                billable INTEGER NOT NULL, uncertain_attempts INTEGER NOT NULL DEFAULT 0)''')
        self.approved = {row[0] for row in conn.execute("SELECT id FROM qa_calls WHERE status='pending'")} if allow_retry else set()

    @contextmanager
    def scope(self, name):
        token = _scope.set([self, name, 0])
        try:
            yield
        finally:
            _scope.reset(token)

    def call(self, key, invoke, billable):
        row = self.conn.execute('SELECT status,payload,uncertain_attempts FROM qa_calls WHERE id=?', (key,)).fetchone()
        if row and row[0] == 'done':
            return json.loads(row[1])
        if row and billable and key not in self.approved:
            raise GoalResumeRequired('上次模型请求结果未知，可能已计费；请确认后重试该请求')
        unknown = int(row[2]) + int(billable) if row else 0
        self.approved.discard(key)
        with self.conn:
            self.conn.execute("INSERT OR REPLACE INTO qa_calls VALUES(?, 'pending', '', ?, ?)", (key, int(billable), unknown))
        with self.scope(key):
            result = invoke()
        with self.conn:
            self.conn.execute("UPDATE qa_calls SET status='done',payload=? WHERE id=?", (json.dumps(result, ensure_ascii=False), key))
        return result


def recorded_call(name, invoke, billable=False, fingerprint=''):
    scope = _scope.get()
    if scope is None:
        return invoke()
    receipts, parent, counter = scope
    scope[2] += 1
    return receipts.call(f'{parent}/{counter}:{name}:{fingerprint}', invoke, billable)


def durable_model_call(function):
    """Cache each API response, including repair calls inside one graph node."""
    signature = inspect.signature(function)

    @functools.wraps(function)
    def wrapped(*args, **kwargs):
        if _scope.get() is None:
            return function(*args, **kwargs)
        values = signature.bind(*args, **kwargs).arguments
        values = {k: v for k, v in values.items() if k not in ('api_key', 'progress')}
        if 'profile' in values:
            values['profile'] = {k: v for k, v in values['profile'].items() if k != 'api_key'}
        digest = hashlib.sha256(json.dumps(values, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        return recorded_call(function.__name__, lambda: function(*args, **kwargs), True, digest)
    return wrapped
