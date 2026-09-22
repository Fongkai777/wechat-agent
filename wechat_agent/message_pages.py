"""Bounded, read-only message paging across WeChat's database shards."""

from __future__ import annotations

import base64
import json
import sqlite3
from pathlib import Path
from typing import Any

from .cli import open_snapshot


MESSAGE_COLUMNS = (
    "local_id", "server_id", "local_type", "real_sender_id", "create_time",
    "message_content", "compress_content",
)


def quote_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def row_key(row: dict[str, Any]) -> tuple:
    return (row["create_time"], row["source_db"], row["source_table"], row["source_rowid"])


def encode_cursor(chat_id: str, row: dict[str, Any]) -> str:
    data = json.dumps([1, chat_id, *row_key(row)], separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def decode_cursor(value: str, chat_id: str) -> tuple:
    try:
        if len(value) > 4096:
            raise ValueError()
        data = json.loads(base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True))
        if (not isinstance(data, list) or len(data) != 6 or data[:2] != [1, chat_id]
                or type(data[2]) is not int or type(data[5]) is not int
                or not isinstance(data[3], str) or not isinstance(data[4], str)):
            raise ValueError()
        return tuple(data[2:])
    except (ValueError, TypeError, UnicodeError) as exc:
        raise ValueError("invalid message cursor; reopen the conversation") from exc


def select_message_page(root: Path, rec: dict[str, Any], since_ts: int | None,
                        limit: int = 100, before: str = "", after: str = "") -> dict[str, Any]:
    if before and after:
        raise ValueError("before and after are mutually exclusive")
    if not 1 <= limit <= 200:
        raise ValueError("message page limit must be between 1 and 200")
    chat_id = rec["id"]
    cursor = decode_cursor(before or after, chat_id) if before or after else None
    ascending = bool(after)
    candidates = []
    for shard in rec.get("shards", []):
        path = root / shard["db"]
        if not path.is_file():
            continue
        table = quote_identifier(shard["table"])
        conn = open_snapshot(path)
        conn.row_factory = sqlite3.Row
        try:
            columns = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
            if "create_time" not in columns:
                continue
            selected = [quote_identifier(c) for c in MESSAGE_COLUMNS if c in columns]
            clauses, params = ["create_time IS NOT NULL"], []
            if since_ts is not None:
                clauses.append("create_time >= ?")
                params.append(since_ts)
            # Shard identity breaks timestamp ties; rowid breaks ties within a shard.
            if cursor:
                timestamp, db, source_table, rowid = cursor
                source = (shard["db"], shard["table"])
                operator = ">" if ascending else "<"
                if source == (db, source_table):
                    clauses.append(f"(create_time, rowid) {operator} (?, ?)")
                    params.extend([timestamp, rowid])
                else:
                    inclusive = source > (db, source_table) if ascending else source < (db, source_table)
                    clauses.append(f"create_time {operator}{'=' if inclusive else ''} ?")
                    params.append(timestamp)
            order = "ASC" if ascending else "DESC"
            rows = conn.execute(
                f"SELECT rowid AS source_rowid, {', '.join(selected)} FROM {table} "
                f"WHERE {' AND '.join(clauses)} ORDER BY create_time {order}, rowid {order} LIMIT ?",
                [*params, limit + 1],
            ).fetchall()
            for row in rows:
                item = dict(row)
                item.update(source_db=shard["db"], source_table=shard["table"])
                candidates.append(item)
        finally:
            conn.close()
    candidates.sort(key=row_key, reverse=not ascending)
    more = len(candidates) > limit
    rows = sorted(candidates[:limit], key=row_key)
    return {
        "rows": rows,
        "before_cursor": encode_cursor(chat_id, rows[0]) if rows else None,
        "after_cursor": encode_cursor(chat_id, rows[-1]) if rows else None,
        "has_more_before": bool(after) or more,
        "has_more_after": more if ascending else bool(before),
    }


def target_filter(targets: list[dict[str, Any]] | None, columns: tuple[str, str, str]) -> tuple[str, list]:
    """Limit media lookups to this page, including legacy local/time fallbacks."""
    if targets is None:
        return "", []
    if len(targets) > 200:
        raise ValueError("media targets must fit in one message page")
    clauses, params = [], []
    for source, column in zip(("create_time", "local_id", "server_id"), columns):
        values = sorted({row.get(source) for row in targets if row.get(source) is not None
                         and (source != "server_id" or row.get(source))})
        if values:
            clauses.append(f"{column} IN ({','.join('?' for _ in values)})")
            params.extend(values)
    return " AND (" + (" OR ".join(clauses) or "0") + ")", params
