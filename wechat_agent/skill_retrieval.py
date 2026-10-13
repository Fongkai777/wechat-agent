"""Read-only task retrieval: indexed recall, raw evidence and bounded neighbours."""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager


def message_key(item):
    server = str(item.get('server_id') or '')
    if server not in ('', '0'):
        return (item.get('chat_id'), 'server', server)
    return tuple(str(item.get(k) or '') for k in (
        'chat_id', 'source_db', 'source_table', 'local_id', 'timestamp', 'sender_username'))


def deduplicate(items):
    unique = {}
    for item in items:
        key = message_key(item)
        if key not in unique or unique[key].get('evidence_role') == 'context' and item.get('evidence_role') != 'context':
            unique[key] = item
    return list(unique.values())


def raw_message_order(item):
    # Physical row order, not local_id or sender, breaks ties within a shard.
    return (item['timestamp'], str(item.get('source_db') or ''),
            str(item.get('source_table') or ''), int(item.get('source_rowid') or 0))


class SkillRetrieval:
    # Generic semantic/context APIs for code packages; task logic lives in the package.
    def __init__(self, state, status, semantic, row_to_item, reader, check, voice_cache):
        self.state, self.status, self.semantic = state, status, semantic
        self.row_to_item, self.reader, self.check, self.voice_cache = row_to_item, reader, check, voice_cache

    @contextmanager
    def connection(self):
        self.check()
        conn = sqlite3.connect(self.state.qa_search_db.resolve().as_uri() + '?mode=ro', uri=True, timeout=10)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    def prepare(self):
        # The platform prepares dependencies before entering this read-only adapter.
        self.check()
        text, semantic = self.status()
        if not text.get('ready') or text.get('soft_stale'):
            raise ValueError('Skill 需要最新全文索引，请先在 RAG 配置更新索引，再执行任务')
        if not semantic.get('ready') or semantic.get('soft_stale') or semantic.get('pending_message_count'):
            raise ValueError('Skill 需要最新语义索引及可用的 Embedding 配置，请先更新语义索引：' + str(semantic.get('error') or '索引未就绪或有待索引消息'))
        return {'ready': True, 'index_updated_at': text.get('updated_at'), 'semantic_updated_at': semantic.get('updated_at')}

    def item(self, row):
        item = self.row_to_item(row)
        sender = str(item.get('sender_username') or '')
        return {**item, 'index_row_id': row['id'], 'evidence_role': 'match',
                'mine': bool(self.state.account and sender == self.state.account),
                'sender_known': bool(self.state.account and sender and not sender.isdigit())}

    def search(self, query, embedding, since, until, min_similarity):
        # This low-level API applies the threshold requested by the Skill itself.
        self.prepare()
        since = max(since, self.state.since_ts or 0)
        hits, diagnostics = self.semantic(query, since, until, embedding)
        if diagnostics.get('error') or not diagnostics.get('ready'):
            raise RuntimeError(diagnostics.get('error') or '语义检索不可用，未以关键词检索冒充成功')
        chunks = [h for h in hits if h['semantic_score'] >= min_similarity]
        filters, params = ['timestamp>=?', 'timestamp<=?'], [since, until]
        where = ' AND '.join(filters)
        by_id = {}
        with self.connection() as conn:
            for start in range(0, len(chunks), 300):
                self.check()
                ids = [h['semantic_chunk_id'] for h in chunks[start:start+300]]
                marks = ','.join('?' for _ in ids)
                rows = conn.execute('SELECT * FROM messages WHERE ' + where
                                    + f' AND id IN (SELECT message_id FROM semantic_message_map WHERE chunk_id IN ({marks}))',
                                    [*params, *ids]).fetchall()
                for row in rows:
                    by_id[row['id']] = {**self.item(row), 'matched_by': ['semantic']}
        return {'messages': list(by_id.values()), 'complete': True,
                'scanned': diagnostics.get('candidate_count', 0), 'semantic_messages': len(by_id),
                'min_similarity': min_similarity,
                'query': query, 'warning': '语义召回不保证穷尽主题相关消息；相似度门槛为 ' + str(min_similarity)}

    def context(self, seeds, step, since, until):
        # 上下文只在同一会话、任务窗口和最大时间间隔内扩展，并标记 context，不能当独立命中计数。
        since = max(since, self.state.since_ts or 0)
        gap = step['max_gap_minutes'] * 60
        contexts = []
        indexed = [s for s in seeds if s.get('index_row_id') is not None]
        if indexed:
            text, _ = self.status()
            if not text.get('ready') or text.get('soft_stale'):
                raise ValueError('上下文查询需要可用的全文索引')
            with self.connection() as conn:
                for seed in indexed:
                    self.check()
                    anchor = conn.execute('SELECT * FROM messages WHERE id=?', (seed['index_row_id'],)).fetchone()
                    if anchor is None or message_key(self.item(anchor)) != message_key(seed):
                        raise ValueError('检索后索引发生变化，无法确认上下文锚点，请重新执行任务')
                    for direction, order, count in (('<', 'DESC', step['before']), ('>', 'ASC', step['after'])):
                        rows = conn.execute(
                            f'SELECT * FROM messages WHERE chat_id=? AND timestamp>=? AND timestamp<=? '
                            f'AND (timestamp,id) {direction} (?,?) ORDER BY timestamp {order},id {order} LIMIT ?',
                            (seed['chat_id'], max(since, seed['timestamp']-gap), min(until, seed['timestamp']+gap),
                             seed['timestamp'], seed['index_row_id'], count))
                        contexts.extend({**self.item(row), 'evidence_role': 'context'} for row in rows)
        # Private-tail rules do not depend on an index. Read neighbouring raw messages.
        raw = {}
        for seed in seeds:
            if seed.get('index_row_id') is None:
                raw.setdefault(seed['chat_id'], []).append(seed)
        chats = {c['id']: c for c in self.state.chats}
        for chat_id, anchors in raw.items():
            self.check()
            chat = chats.get(chat_id)
            if chat is None:
                raise ValueError('上下文所属会话已不可用，请重新同步')
            rows = self.reader(self.state, chat, since_ts=max(since, min(s['timestamp'] for s in anchors)-gap),
                               until_ts=min(until, max(s['timestamp'] for s in anchors)+gap), max_items=None,
                               max_text_chars=None, voice_cache=self.voice_cache, check_cancelled=self.check,
                               strict=True, include_empty=True)
            rows = deduplicate(rows)
            rows.sort(key=raw_message_order)
            positions = {message_key(r): i for i, r in enumerate(rows)}
            for seed in anchors:
                pos = positions.get(message_key(seed))
                if pos is None:
                    raise ValueError('无法找到原消息的上下文锚点，未返回不完整结果')
                neighbours = rows[max(0, pos-step['before']):pos] + rows[pos+1:pos+1+step['after']]
                contexts.extend({**r, 'evidence_role': 'context'} for r in neighbours if abs(r['timestamp']-seed['timestamp']) <= gap)
        matches = {message_key(s) for s in seeds}
        contexts = [c for c in deduplicate(contexts) if message_key(c) not in matches]
        return {'messages': contexts, 'complete': True, 'warning': '', 'count': len(contexts)}
