"""Capability-checked message/model APIs; no task-specific decision rules."""
from __future__ import annotations

import copy
import json
import math
import sqlite3
import time

from .code_skill_packages import manifest
from .skill_retrieval import message_key

COLUMNS = 'id chat_id chat_title chat_type timestamp time sender sender_username text type source_db source_table local_id server_id person_id person_name'.split()
FUNCTIONS = set('abs coalesce ifnull nullif lower upper length substr substring instr like glob replace trim ltrim rtrim printf round min max sum avg count total group_concat row_number rank dense_rank lag lead first_value last_value nth_value ntile percent_rank cume_dist datetime date time strftime julianday unixepoch typeof cast'.split())


class CodeAPI:
    def __init__(self, package, context, chats, messages, indexed, semantic, surrounding, llm,
                 state=None, report=lambda message: None):
        self.context, self.limits = context, manifest(package)
        self.backends = dict(chats=chats, messages=messages, indexed=indexed, semantic=semantic,
                             surrounding=surrounding, llm=llm)
        self.staged_state, self.report = copy.deepcopy(state or {}), report
        self.state_changed = False
        self.ledger, self.sources, self.steps = {}, [], []
        self.model_calls = self.embedding_calls = 0
        self.usage = {'total_tokens':0, 'model_calls':[]}

    def remember(self, rows):
        if not isinstance(rows, list): raise ValueError('消息接口返回格式错误')
        output = []
        for row in rows:
            row = dict(row)
            for field in ('local_id','server_id'):
                row[field] = str(row.get(field) or '')
            timestamp = row.get('timestamp')
            if type(timestamp) not in (int,float) or not self.context['since'] <= timestamp <= self.context['until']:
                raise ValueError('消息超出任务时间范围')
            if not row.get('chat_id') or 'text' not in row: raise ValueError('消息缺少来源字段')
            self.ledger[message_key(row)] = copy.deepcopy(row)
            output.append(row)
        return output

    def verified(self, rows):
        if not isinstance(rows,list): raise ValueError('messages 必须为数组')
        output = []
        for row in rows:
            if not isinstance(row,dict): raise ValueError('消息格式错误')
            original = self.ledger.get(message_key(row))
            if not original or any(row.get(k) != original.get(k) for k in
                    ('chat_id','timestamp','text','sender_username','source_db','source_table','local_id','server_id')):
                raise ValueError('引用或上下文锚点不是实际检索到的原始消息')
            output.append(copy.deepcopy(original))
        return output

    def dispatch(self, method, arguments):
        permissions = {'chats':'messages.read','messages':'messages.read','query':'index.query',
                       'semantic_search':'embedding','surrounding':'messages.read','llm':'llm',
                       'state_get':'state.read','state_set':'state.write','cite':None,'log':None}
        if method not in permissions or not isinstance(arguments,dict): raise ValueError('未知 Skill 接口')
        required = permissions[method]
        if required and required not in self.limits['permissions']: raise ValueError('Skill 未声明权限：'+required)
        started = time.monotonic()
        value = getattr(self, 'api_'+method)(**arguments)
        self.steps.append({'tool':method,'elapsed_seconds':round(time.monotonic()-started,3),
                           'count':len(value) if isinstance(value,list) else None})
        self.report('Skill · '+method)
        return value

    def api_chats(self):
        return [{k:row.get(k) for k in ('id','title','type','last_ts')} for row in self.backends['chats']()]

    def api_messages(self, chat_id, since=None, until=None, latest=None):
        if not isinstance(chat_id,str) or len(chat_id)>200: raise ValueError('chat_id 无效')
        since = self.context['since'] if since is None else since
        until = self.context['until'] if until is None else until
        if any(type(v) not in (int,float) or not math.isfinite(v) for v in (since,until)):
            raise ValueError('时间范围无效')
        if not self.context['since'] <= since <= until <= self.context['until']:
            raise ValueError('不能扩大任务时间范围')
        if latest is not None and (type(latest) is not int or not 1 <= latest <= 100000): raise ValueError('latest 无效')
        return self.remember(self.backends['messages'](chat_id,since,until,latest))

    def api_query(self, sql, params=None):
        if not isinstance(sql,str) or len(sql)>30000 or not isinstance(params or [],list): raise ValueError('SQL 参数无效')
        rows = self.backends['indexed']()
        # The SQL engine sees only a task-window snapshot, never the source connection or host paths.
        if len(rows)>300000: raise ValueError('SQL 时间范围超过 300000 条安全预算，请缩小范围；未截断')
        conn = sqlite3.connect(':memory:')
        conn.row_factory = sqlite3.Row
        try:
            conn.execute('PRAGMA temp_store=MEMORY')
            conn.execute('CREATE TABLE messages ('+','.join(c+' '+('INTEGER' if c in ('id','timestamp') else 'TEXT') for c in COLUMNS)+')')
            for row in rows:
                if not self.context['since'] <= row['timestamp'] <= self.context['until']: raise ValueError('索引快照越界')
            conn.executemany('INSERT INTO messages VALUES('+','.join('?' for _ in COLUMNS)+')',
                             [[r.get(c) for c in COLUMNS] for r in rows])
            conn.execute('CREATE VIEW chats AS SELECT chat_id AS id,chat_title AS title,chat_type AS type,max(timestamp) AS last_ts FROM messages GROUP BY chat_id')
            deadline = time.monotonic()+8
            conn.set_progress_handler(lambda: int(time.monotonic()>deadline), 1000)
            def authorize(action, arg1, arg2, database, source):
                if action in (sqlite3.SQLITE_SELECT, sqlite3.SQLITE_RECURSIVE): return sqlite3.SQLITE_OK
                if action == sqlite3.SQLITE_READ and arg1 in ('messages','chats') and database in ('main',None): return sqlite3.SQLITE_OK
                if action == sqlite3.SQLITE_FUNCTION and str(arg2).lower() in FUNCTIONS: return sqlite3.SQLITE_OK
                return sqlite3.SQLITE_DENY
            conn.set_authorizer(authorize)
            result, size = [], 0
            originals = {message_key(r):r for r in rows}
            for row in conn.execute(sql, params or []):
                item = dict(row)
                size += len(json.dumps(item).encode())
                if size>24*1024*1024: raise ValueError('SQL 返回超过 24 MB，请在 Skill 中分页；未截断')
                result.append(item)
                original = originals.get(message_key(item))
                if original and all(item.get(k)==original.get(k) for k in COLUMNS if k != 'id'):
                    self.remember([item])
            return result
        finally:
            conn.close()

    def api_semantic_search(self, query, min_similarity=.25):
        if not isinstance(query,str) or not 1<=len(query)<=4000 or type(min_similarity) not in (int,float) or not 0<=min_similarity<=1:
            raise ValueError('语义查询参数无效')
        self.embedding_calls += 1
        if self.embedding_calls>self.limits['max_embedding_calls']: raise ValueError('Embedding 调用预算已用完')
        result = self.backends['semantic'](query,min_similarity)
        result['messages'] = self.remember(result['messages'])
        return result

    def api_surrounding(self, messages, before=3, after=3, max_gap_minutes=30):
        if any(type(n) is not int or not 0<=n<=50 for n in (before,after)) or type(max_gap_minutes) is not int or not 1<=max_gap_minutes<=1440:
            raise ValueError('上下文范围无效')
        return self.remember(self.backends['surrounding'](self.verified(messages),before,after,max_gap_minutes))

    def api_cite(self, messages):
        result = []
        for row in self.verified(messages):
            reference = next((s['reference'] for s in self.sources if message_key(s)==message_key(row)),None)
            if reference is None:
                reference = len(self.sources)+1
                self.sources.append({**row,'reference':reference})
            result.append({**row,'reference':reference})
        return result

    def api_llm(self, messages, schema=None):
        if not isinstance(messages,list) or not messages or any(not isinstance(m,dict) or set(m)!={'role','content'} or m['role'] not in ('system','user','assistant') or not isinstance(m['content'],str) for m in messages):
            raise ValueError('模型 messages 无效')
        if len(json.dumps(messages).encode())>1024*1024: raise ValueError('单次模型输入超过 1 MB 安全预算；请分批处理')
        if schema is not None and (not isinstance(schema,dict) or len(json.dumps(schema))>30000): raise ValueError('输出 schema 无效')
        self.model_calls += 1
        if self.model_calls>self.limits['max_llm_calls']: raise ValueError('模型调用预算已用完')
        value, usage = self.backends['llm'](messages,schema)
        self.add_usage(usage,'llm')
        return value

    def add_usage(self, usage, kind):
        self.usage['total_tokens'] += int(usage.get('total_tokens') or 0)
        self.usage['model_calls'].append({'kind':kind,**usage})

    def api_state_get(self): return copy.deepcopy(self.staged_state)

    def api_state_set(self, value):
        if not isinstance(value,dict) or len(json.dumps(value,allow_nan=False).encode())>1024*1024: raise ValueError('任务状态必须是小于 1 MB 的 JSON 对象')
        self.staged_state, self.state_changed = copy.deepcopy(value), True
        return {'staged':True}

    def api_log(self, message):
        if not isinstance(message,str) or len(message)>500: raise ValueError('日志最长 500 字')
        self.report(message)
        return None

    def snapshot(self):
        return {'ledger':list(self.ledger.values()), 'sources':self.sources,'steps':self.steps,
                'usage':self.usage,'model_calls':self.model_calls,'embedding_calls':self.embedding_calls,
                'staged_state':self.staged_state,'state_changed':self.state_changed}

    def restore(self, snapshot):
        self.ledger = {message_key(r):r for r in snapshot['ledger']}
        for name in ('sources','steps','usage','model_calls','embedding_calls','staged_state','state_changed'):
            setattr(self,name,snapshot[name])
