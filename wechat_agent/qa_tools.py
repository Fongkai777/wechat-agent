"""Read-only, bounded tools over the canonical message index.

The executor owns time/person constraints. Model-generated keywords cannot relax them.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import contextmanager


def evidence_id(item):
    # 以原始消息定位字段生成稳定 ID，便于跨工具去重及绑定上下文。
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
        # 工具只读规范索引，连接结束即关闭；查询不修改聊天数据。
        conn = sqlite3.connect(self.state.qa_search_db.resolve().as_uri() + "?mode=ro", uri=True, timeout=10)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    def filters(self, plan, background=False):
        # 统一生成参数化范围条件；历史模式转到起始时间之前，仍受索引下界限制。
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
        scope = plan.get('chat_scope') or {}
        kind = scope.get('type','all')
        if kind in ('group','private'):
            filters.append('chat_type = ?'); params.append(kind)
        if scope.get('keyword'):
            filters.append('instr(lower(chat_title),lower(?)) > 0'); params.append(scope['keyword'])
        if plan.get('chat_ids'):
            filters.append('chat_id IN ('+','.join('?' for _ in plan['chat_ids'])+')'); params.extend(plan['chat_ids'])
        ids = [p["id"] for p in plan.get("people", [])]
        if ids:
            # 明确群聊范围时按发送者筛选；其余范围沿用索引中的联系人归属。
            column = 'sender_username' if kind == 'group' else 'person_id'
            filters.append(column+" IN (" + ",".join("?" for _ in ids) + ")"); params.extend(ids)
        return filters, params

    def item(self, row, role="primary", plan=None):
        item = self.row_to_item(row)
        item.update(evidence_id=evidence_id(item), evidence_role=role, index_row_id=row["id"])
        if plan is not None:
            item['retrieval_bounds'] = {k:plan.get(k) for k in ('since_ts','until_ts')}
        return item

    def find_people(self, plan, query, offset=0, size=20):
        # Identity discovery returns candidates only; it never sets a search scope.
        filters, params = self.filters(dict(plan,people=[]))
        where = ' AND '.join(filters)
        sql = ('WITH scoped AS (SELECT * FROM messages WHERE '+where+'''), names AS (
            SELECT sender_username AS id, sender AS name FROM scoped
            UNION SELECT person_id AS id, person_name AS name FROM scoped
        ) SELECT DISTINCT id,name FROM names WHERE COALESCE(id,'') != ''
            AND (instr(lower(id),lower(?)) > 0 OR instr(lower(name),lower(?)) > 0)''')
        args = [*params, query.strip(), query.strip()]
        with self.connection() as conn:
            total = conn.execute('SELECT COUNT(*) FROM ('+sql+')', args).fetchone()[0]
            rows = conn.execute(sql+' ORDER BY name,id LIMIT ? OFFSET ?', [*args,size,offset]).fetchall()
        return {'tool':'find_people','items':[], 'people':[dict(r) for r in rows],
                'matched':total, 'next_offset':offset+len(rows) if offset+len(rows)<total else None,
                'complete':offset+len(rows)>=total}

    def timeline(self, plan, offset=0, size=60):
        # 时间与行 ID 双重排序稳定分页，总数用于计算下一偏移和是否到末页。
        filters, params = self.filters(plan)
        where = " AND ".join(filters)
        with self.connection() as conn:
            total = conn.execute("SELECT COUNT(*) FROM messages WHERE " + where, params).fetchone()[0]
            rows = conn.execute("SELECT * FROM messages WHERE " + where + " ORDER BY timestamp DESC, id DESC LIMIT ? OFFSET ?",
                                [*params, size, offset]).fetchall()
        return {"items": [self.item(r, plan=plan) for r in rows], "matched": total,
                "next_offset": offset + len(rows) if offset + len(rows) < total else None,
                "complete": offset + len(rows) >= total, "tool": "timeline", "offset": offset}

    def shared_windows(self, plan, offset=0, size=60, window_seconds=1800):
        if len({p['id'] for p in plan.get('people',[])}) < 2:
            raise ValueError('共同发言时间窗至少需要两位已确认的人物')
        if (plan.get('chat_scope') or {}).get('type','all') == 'all':
            plan = dict(plan,chat_scope={**(plan.get('chat_scope') or {}),'type':'group'})
        filters, params = self.filters(plan)
        filters.append("chat_type='group'")
        # Adjacent target speakers are candidate anchors, not inferred replies.
        # 先筛选目标人物，再用 LAG 找同群相邻的不同发送者；中间可能有其他人发言。
        query = '''WITH scoped AS (
            SELECT id, chat_id, timestamp, sender_username FROM messages WHERE '''+' AND '.join(filters)+'''
        ), ordered AS (
            SELECT *, LAG(id) OVER w AS previous_id,
                LAG(timestamp) OVER w AS previous_time,
                LAG(sender_username) OVER w AS previous_sender
            FROM scoped WINDOW w AS (PARTITION BY chat_id ORDER BY timestamp,id)
        ), candidates AS (
            SELECT * FROM ordered WHERE sender_username != previous_sender
                AND timestamp - previous_time <= ?
        ) '''
        params = [*params,window_seconds]
        # Reserve room for both anchors and their neighbors as one evidence unit.
        # 按候选窗口分页，每页最多四组，并为两端原文与周边消息共同预留额度。
        page_size = max(1,min(size//4,4))
        with self.connection() as conn:
            total = conn.execute(query+'SELECT COUNT(*) FROM candidates',params).fetchone()[0]
            pairs = conn.execute(query+'SELECT * FROM candidates ORDER BY timestamp DESC,id DESC LIMIT ? OFFSET ?',
                                 [*params,page_size,offset]).fetchall()
            ids = list(dict.fromkeys(i for p in pairs for i in (p['previous_id'],p['id'])))
            rows = conn.execute('SELECT * FROM messages WHERE id IN ('+','.join('?' for _ in ids)+')',ids).fetchall() if ids else []
        by_id = {r['id']:self.item(r, plan=plan) for r in rows}
        windows = [{'source_ids':[by_id[p['previous_id']]['evidence_id'],by_id[p['id']]['evidence_id']],
                    'gap_seconds':p['timestamp']-p['previous_time']} for p in pairs]
        anchors = [dict(by_id[i],candidate_relation=True) for i in ids]
        neighbors = self.surrounding(plan,anchors,size-len(anchors)) if size>len(anchors) else {'items':[]}
        return {'items':[*anchors,*neighbors['items']], 'windows':windows, 'matched':total,
                'next_offset':offset+len(pairs) if offset+len(pairs)<total else None,
                'complete':offset+len(pairs)>=total, 'tool':'shared_windows', 'window_seconds':window_seconds,
                'note':f'匹配数为候选时间窗数，不是互动次数；双方发言间隔不超过{window_seconds}秒，可能夹有其他人发言，需展开前后文核实。'}

    def search(self, plan, query=None, size=60, background=False):
        # 混合检索负责召回；语义块先映射回消息，再由规范索引过滤并返回可引用原文。
        self.check()
        retrieval_plan = dict(plan, query=query or plan["query"])
        filters, params = self.filters(plan, background)
        since = None if background else plan.get("since_ts")
        if self.state.since_ts is not None:
            since = max(since, self.state.since_ts) if since is not None else self.state.since_ts
        until = plan["since_ts"] - 1 if background and plan.get("since_ts") is not None else plan["until_ts"]
        hits, diagnostics = self.hybrid(retrieval_plan["query"], plan.get("people", []), since, until, size,
                                        scope=plan)
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
            # 每批回查都重施人物、日期与会话条件，防止语义块跨边界带入范围外原文。
            for start in range(0, len(unique), 400):
                batch = unique[start:start + 400]
                rows = conn.execute("SELECT * FROM messages WHERE " + " AND ".join(filters)
                                    + " AND id IN (" + ",".join("?" for _ in batch) + ")", [*params, *batch]).fetchall()
                by_id = {r["id"]: r for r in rows}
                bounds=dict(plan,since_ts=since,until_ts=until)
                items.extend(self.item(by_id[i], "background" if background else "primary", bounds) for i in batch if i in by_id)
        note = "相关性检索，不代表穷尽时间范围内全部消息"
        if diagnostics.get('rerank_error'):
            note += '；重排未完成，使用已有排序：'+diagnostics['rerank_error']
        return {"items": items[:size], "matched": len(items), "complete": False, "tool": "hybrid_search",
                "note": note, "diagnostics": diagnostics}

    def surrounding(self, plan, seeds, size=60):
        # 仅展开主证据所在会话的前后半小时，各方向最多三条，并遵守计划日期边界。
        # 上下文允许其他发送者出现以核实接话，保留真实身份并标为 surrounding。
        items, seen = [], {s["evidence_id"] for s in seeds}
        by_id = {}
        with self.connection() as conn:
            for seed in seeds:
                self.check()
                if seed.get("evidence_role") != "primary":
                    continue
                bounds=seed.get('retrieval_bounds') or {}
                lower=max(self.state.since_ts or 0, plan.get('since_ts') or 0, bounds.get('since_ts') or 0)
                upper=min(plan['until_ts'],bounds.get('until_ts') or plan['until_ts'])
                for direction, order in (("<", "DESC"), (">", "ASC")):
                    rows = conn.execute(
                        f"SELECT * FROM messages WHERE chat_id=? AND timestamp >= ? AND timestamp <= ? "
                        f"AND (timestamp, id) {direction} (?, ?) ORDER BY timestamp {order}, id {order} LIMIT 3",
                        (seed["chat_id"], max(lower, seed["timestamp"] - 1800),
                         min(upper, seed["timestamp"] + 1800), seed["timestamp"], seed["index_row_id"]),
                    ).fetchall()
                    for row in rows:
                        item = self.item(row, "surrounding")
                        # 同一邻接消息只收录一次，但记录它支持的多个种子证据。
                        if item['evidence_id'] in by_id:
                            by_id[item['evidence_id']]['context_for'].append(seed['evidence_id'])
                        if item["evidence_id"] not in seen:
                            item['context_for'] = [seed['evidence_id']]
                            by_id[item['evidence_id']] = item
                            seen.add(item["evidence_id"]); items.append(item)
                            if len(items) >= size:
                                return {"items": items, "complete": False, "tool": "surrounding"}
        return {"items": items, "complete": True, "tool": "surrounding"}

    def statistics(self, plan):
        # SQL 聚合整个匹配范围，输出可引用索引摘要；消息总数不代表相互回复次数。
        filters, params = self.filters(plan)
        with self.connection() as conn:
            rows = conn.execute("SELECT chat_id, chat_title, chat_type, COUNT(*) AS count FROM messages WHERE "
                                + " AND ".join(filters) + " GROUP BY chat_id ORDER BY count DESC, chat_id", params).fetchall()
        data = [{"chat_id": r["chat_id"], "chat_title": r["chat_title"], "chat_type": r["chat_type"], "count": r["count"]} for r in rows]
        return {"items": [{"chat_type": "index_summary", "chat_title": "指定范围会话统计", "type": "索引摘要",
                            "text": json.dumps({"messages": sum(r["count"] for r in data), "chats": len(data), "top_chats": data[:100]}, ensure_ascii=False),
                            "evidence_role": "statistics", "evidence_id": 'statistics:'+hashlib.sha256(json.dumps(plan,sort_keys=True).encode()).hexdigest()[:16]}],
                "complete": len(data)<=100, "matched": sum(r["count"] for r in data), "tool": "statistics",
                "note":"发言计数不是两人互相回复次数；判断互动需要读取原文和上下文。"}

    def list_chats(self, plan, offset=0, size=30):
        # 返回会话目录和发送者计数供后续定位，items 为空，不直接占用原文证据预算。
        filters, params = self.filters(plan)
        with self.connection() as conn:
            rows = conn.execute('SELECT chat_id,chat_title,chat_type,COUNT(*) AS messages,'
                'COUNT(DISTINCT sender_username) AS speakers FROM messages WHERE '+ ' AND '.join(filters)+
                ' GROUP BY chat_id ORDER BY messages DESC,chat_id',params).fetchall()
            selected = rows[offset:offset+size]
            chats = []
            for row in selected:
                self.check()
                counts = conn.execute('SELECT sender_username,COUNT(*) AS count FROM messages WHERE '+
                    ' AND '.join(filters)+' AND chat_id=? GROUP BY sender_username ORDER BY count DESC',
                    [*params,row['chat_id']]).fetchall()
                chats.append({**dict(row),'senders':[dict(r) for r in counts[:20]],
                              'senders_truncated':len(counts)>20})
        return {'items':[], 'chats':chats, 'matched':len(rows), 'complete':offset+len(selected)>=len(rows),
                'next_offset':offset+len(selected) if offset+len(selected)<len(rows) else None,
                'tool':'list_chats','note':'匹配会话及发言数，仅用于定位；共同发言不等于直接互动。'}

    def unanswered(self, plan, size=60):
        # 按私聊取查询窗口内最后一条消息，与本人账号比较，仅产出待核查线索。
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
