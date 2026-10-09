"""Read-only, bounded tools over the canonical message index.

The executor owns time/person constraints. Model-generated keywords cannot relax them.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import contextmanager


def evidence_id(item):
    keys = ("chat_id", "source_db", "source_table", "local_id", "server_id", "timestamp", "sender_username")
    return hashlib.sha256(json.dumps([item.get(k) for k in keys], ensure_ascii=False).encode()).hexdigest()[:24]


class ConversationTools:
    def __init__(self, state, hybrid, row_to_item, check):
        self.state, self.hybrid, self.row_to_item, self.check = state, hybrid, row_to_item, check

    @contextmanager
    def connection(self):
        self.check()
        if not self.state.qa_search_db.exists():
            raise ValueError("请先建立全文索引，再进行聊天问答")
        conn = sqlite3.connect(self.state.qa_search_db.resolve().as_uri() + "?mode=ro", uri=True, timeout=10)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    def filters(self, plan, background=False):
        filters, params = ["timestamp <= ?"], [plan["until_ts"]]
        since = plan.get("since_ts")
        if background and since is not None:
            filters, params = ["timestamp < ?"], [since]
            since = None
        floor = self.state.since_ts
        if floor is not None:
            since = max(since, floor) if since is not None else floor
        if since is not None:
            filters.append("timestamp >= ?"); params.append(since)
        ids = [p["id"] for p in plan.get("people", [])]
        if ids:
            filters.append("person_id IN (" + ",".join("?" for _ in ids) + ")"); params.extend(ids)
        return filters, params

    def item(self, row, role="primary"):
        item = self.row_to_item(row)
        item.update(evidence_id=evidence_id(item), evidence_role=role, index_row_id=row["id"])
        return item

    def timeline(self, plan, offset=0, size=60):
        filters, params = self.filters(plan)
        where = " AND ".join(filters)
        with self.connection() as conn:
            total = conn.execute("SELECT COUNT(*) FROM messages WHERE " + where, params).fetchone()[0]
            rows = conn.execute("SELECT * FROM messages WHERE " + where + " ORDER BY timestamp DESC, id DESC LIMIT ? OFFSET ?",
                                [*params, size, offset]).fetchall()
        return {"items": [self.item(r) for r in rows], "matched": total,
                "next_offset": offset + len(rows) if offset + len(rows) < total else None,
                "complete": offset + len(rows) >= total, "tool": "timeline", "offset": offset}

    def search(self, plan, query=None, size=60, background=False):
        self.check()
        retrieval_plan = dict(plan, query=query or plan["query"])
        filters, params = self.filters(plan, background)
        since = None if background else plan.get("since_ts")
        until = plan["since_ts"] - 1 if background and plan.get("since_ts") is not None else plan["until_ts"]
        hits, diagnostics = self.hybrid(retrieval_plan["query"], plan.get("people", []), since, until, size)
        ids = []
        with self.connection() as conn:
            for hit in hits:
                self.check()
                if hit.get("semantic_chunk_id"):
                    rows = conn.execute("SELECT message_id FROM semantic_message_map WHERE chunk_id=?", (hit["semantic_chunk_id"],)).fetchall()
                    ids.extend(row[0] for row in rows)
                else:
                    rows = conn.execute("SELECT id FROM messages WHERE source_db=? AND source_table=? AND local_id=? AND timestamp=?",
                                        [hit.get("source_db", ""), hit.get("source_table", ""), str(hit.get("local_id", "")), hit.get("timestamp", 0)]).fetchall()
                    ids.extend(row[0] for row in rows)
            unique = list(dict.fromkeys(ids))
            items = []
            # Hydrate raw messages and enforce constraints again, including chunks
            # which overlap the time boundary or contain several different speakers.
            for start in range(0, len(unique), 400):
                batch = unique[start:start + 400]
                rows = conn.execute("SELECT * FROM messages WHERE " + " AND ".join(filters)
                                    + " AND id IN (" + ",".join("?" for _ in batch) + ")", [*params, *batch]).fetchall()
                by_id = {r["id"]: r for r in rows}
                items.extend(self.item(by_id[i], "background" if background else "primary") for i in batch if i in by_id)
        return {"items": items[:size], "matched": len(items), "complete": False, "tool": "hybrid_search",
                "note": "相关性检索，不代表穷尽时间范围内全部消息", "diagnostics": diagnostics}

    def surrounding(self, plan, seeds, size=60):
        items, seen = [], {s["evidence_id"] for s in seeds}
        with self.connection() as conn:
            for seed in seeds:
                self.check()
                if seed.get("evidence_role") != "primary":
                    continue
                for direction, order in (("<", "DESC"), (">", "ASC")):
                    rows = conn.execute(
                        f"SELECT * FROM messages WHERE chat_id=? AND timestamp >= ? AND timestamp <= ? "
                        f"AND (timestamp, id) {direction} (?, ?) ORDER BY timestamp {order}, id {order} LIMIT 3",
                        (seed["chat_id"], max(plan.get("since_ts") or 0, seed["timestamp"] - 1800),
                         min(plan["until_ts"], seed["timestamp"] + 1800), seed["timestamp"], seed["index_row_id"]),
                    ).fetchall()
                    for row in rows:
                        item = self.item(row, "surrounding")
                        if item["evidence_id"] not in seen:
                            seen.add(item["evidence_id"]); items.append(item)
                            if len(items) >= size:
                                return {"items": items, "complete": False, "tool": "surrounding"}
        return {"items": items, "complete": True, "tool": "surrounding"}

    def statistics(self, plan):
        filters, params = self.filters(plan)
        with self.connection() as conn:
            rows = conn.execute("SELECT chat_id, chat_title, chat_type, COUNT(*) AS count FROM messages WHERE "
                                + " AND ".join(filters) + " GROUP BY chat_id ORDER BY count DESC, chat_id", params).fetchall()
        data = [{"chat_id": r["chat_id"], "chat_title": r["chat_title"], "chat_type": r["chat_type"], "count": r["count"]} for r in rows]
        return {"items": [{"chat_type": "index_summary", "chat_title": "指定范围会话统计", "type": "索引摘要",
                            "text": json.dumps({"messages": sum(r["count"] for r in data), "chats": len(data), "top_chats": data[:100]}, ensure_ascii=False),
                            "evidence_role": "statistics", "evidence_id": "statistics"}],
                "complete": True, "matched": sum(r["count"] for r in data), "tool": "statistics"}

    def unanswered(self, plan, size=60):
        if not self.state.account:
            raise ValueError("尚未识别自己的微信账号，不能可靠判断是否回复")
        filters, params = self.filters(plan)
        with self.connection() as conn:
            rows = conn.execute(
                "SELECT * FROM (SELECT m.*, ROW_NUMBER() OVER (PARTITION BY chat_id ORDER BY timestamp DESC, id DESC) AS pos "
                "FROM messages m WHERE chat_type='private' AND " + " AND ".join(filters) + ") WHERE pos=1 ORDER BY timestamp DESC", params).fetchall()
        pending = [r for r in rows if r["sender_username"] != self.state.account and r["chat_id"] not in ("qqmail", "filehelper")]
        return {"items": [self.item(r) for r in pending[:size]], "matched": len(pending), "complete": len(pending) <= size,
                "tool": "unanswered", "note": "窗口内最后消息来自对方仅是待核查线索，不等于必须回复；需结合前后文。"}
