"""One bounded, read-only SQL snapshot per execution, shared by both API entries."""
from __future__ import annotations

import copy
import sqlite3
import threading
import time

from .skill_retrieval import message_key


class SQLSnapshot:
    def __init__(self):
        self.lock = threading.RLock()
        self.conn = None
        self.bounds = None
        self.originals = {}
        self.builds = 0

    def close(self):
        with self.lock:
            if self.conn is not None:
                self.conn.close()
            self.conn, self.bounds, self.originals = None, None, {}

    def prepare(self, bounds, reader, columns, functions):
        if self.conn is not None and self.bounds == bounds:
            return self.conn
        self.close()
        rows = reader()
        if len(rows) > 300000:
            raise ValueError('SQL 时间范围超过 300000 条安全预算，请缩小范围；未截断')
        if any(not bounds[0] <= row['timestamp'] <= bounds[1] for row in rows):
            raise ValueError('索引快照越界')
        # Graph workers may change threads; the owner serializes every query/close.
        conn = sqlite3.connect(':memory:', check_same_thread=False)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute('PRAGMA temp_store=MEMORY')
            conn.execute('CREATE TABLE messages (' + ','.join(
                c+' '+('INTEGER' if c in ('id', 'timestamp') else 'TEXT') for c in columns)+')')
            conn.executemany('INSERT INTO messages VALUES('+','.join('?' for _ in columns)+')',
                             ([row.get(c) for c in columns] for row in rows))
            conn.execute('CREATE VIEW chats AS SELECT chat_id AS id,chat_title AS title,chat_type AS type,'
                         'max(timestamp) AS last_ts FROM messages GROUP BY chat_id')
            conn.commit()
            conn.execute('PRAGMA query_only=ON')

            def authorize(action, arg1, arg2, database, source):
                if action in (sqlite3.SQLITE_SELECT, sqlite3.SQLITE_RECURSIVE):
                    return sqlite3.SQLITE_OK
                if action == sqlite3.SQLITE_READ and arg1 in ('messages', 'chats') and database in ('main', None):
                    return sqlite3.SQLITE_OK
                if action == sqlite3.SQLITE_FUNCTION and str(arg2).lower() in functions:
                    return sqlite3.SQLITE_OK
                return sqlite3.SQLITE_DENY

            conn.set_authorizer(authorize)
            self.originals = {message_key(row): copy.deepcopy(row) for row in rows}
        except BaseException:
            conn.close()
            raise
        self.conn, self.bounds = conn, bounds
        self.builds += 1
        return conn

    def deadline(self):
        # Reset per query, including queries after an interruption or SQL error.
        end = time.monotonic() + 8
        self.conn.set_progress_handler(lambda: int(time.monotonic() > end), 1000)

    def __del__(self):
        self.close()
