"""Capability-checked message/model APIs; no task-specific decision rules."""
from __future__ import annotations

import copy
import hashlib
import json
import math
import time

from .skill_retrieval import message_key
from .platform_tools import (SKILL_TOOL_NAMES, TOOL_PERMISSIONS, tool_definitions,
                             action_from_arguments, parse_tool_call)
from .skill_tool_calling import validate_messages
from .platform_data_tools import DATA_TOOLS
from .qa_tools import evidence_id
from .evidence_context import merge_evidence
from .platform_sql import SQLSnapshot

COLUMNS = 'id chat_id chat_title chat_type timestamp time sender sender_username text type source_db source_table local_id server_id person_id person_name'.split()
FUNCTIONS = set('abs coalesce ifnull nullif lower upper length substr substring instr like glob replace trim ltrim rtrim printf round min max sum avg count total group_concat row_number rank dense_rank lag lead first_value last_value nth_value ntile percent_rank cume_dist datetime date time strftime julianday unixepoch typeof cast'.split())


class PlatformAPI:
    # 宿主侧能力边界：统一检查权限、消息来源和预算，业务判断由包内工作流实现。
    def __init__(self, limits, context, chats, messages, indexed, semantic, surrounding, llm,
                 state=None, report=lambda message: None, tools=None):
        self.context, self.limits = context, copy.deepcopy(limits)
        self.backends = dict(chats=chats, messages=messages, indexed=indexed, semantic=semantic,
                             surrounding=surrounding, llm=llm)
        self.staged_state, self.report = copy.deepcopy(state or {}), report
        self.state_changed = False
        self.ledger, self.sources, self.steps = {}, [], []
        self.model_calls = self.embedding_calls = 0
        self.usage = {'total_tokens':0, 'model_calls':[]}
        self.tool_backend = tools
        self.tool_sources, self.tool_observations = [], []
        self.sql_snapshot = SQLSnapshot()

    def close(self):
        self.sql_snapshot.close()

    def remember(self, rows):
        # 所有返回消息都需处于任务时间窗内；留存独立副本作为后续引用核验的依据。
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
            row.setdefault('evidence_id', evidence_id(row))
            row.setdefault('evidence_role', 'primary')
            previous = self.ledger.get(message_key(row))
            row = merge_evidence([previous] if previous else [], [row])[0]
            self.ledger[message_key(row)] = copy.deepcopy(row)
            output.append(row)
        return output

    def verified(self, rows):
        # 引用和上下文锚点都必须匹配检索账本中的原始字段，不能由工作流编造证据。
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
        # 子进程调用统一从这里进入；白名单同时限制可调用方法和所需声明权限。
        permissions = {'chats':'messages.read','messages':'messages.read','messages_batch':'messages.read','query':'index.query',
                       'semantic_search':'embedding','surrounding':'messages.read','llm':'llm',
                       'state_get':'state.read','state_set':'state.write','cite':None,'log':None}
        permissions.update(tools=None,tool=None)
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
        rows = [{k:row.get(k) for k in ('id','title','type','last_ts')} for row in self.backends['chats']()]
        self.tool_observations.append({'chats':[{'chat_id':r['id'],'chat_title':r['title'],'chat_type':r['type']} for r in rows]})
        return rows

    def api_messages(self, chat_id, since=None, until=None, latest=None):
        # 单次读取只能缩小任务时间窗，不能通过传参扩大数据范围。
        if not isinstance(chat_id,str) or len(chat_id)>200: raise ValueError('chat_id 无效')
        since = self.context['since'] if since is None else since
        until = self.context['until'] if until is None else until
        if any(type(v) not in (int,float) or not math.isfinite(v) for v in (since,until)):
            raise ValueError('时间范围无效')
        if not self.context['since'] <= since <= until <= self.context['until']:
            raise ValueError('不能扩大任务时间范围')
        if latest is not None and (type(latest) is not int or not 1 <= latest <= 100000): raise ValueError('latest 无效')
        return self.remember(self.backends['messages'](chat_id,since,until,latest))

    def api_messages_batch(self, chat_ids, since=None, until=None, latest=1):
        # Batch transport only: preserve raw-message ordering, scope and evidence checks.
        # Keep per-call work bounded; packages can iterate without a per-conversation RPC.
        if (not isinstance(chat_ids, list) or len(chat_ids) > 100 or
                any(not isinstance(chat, str) or not chat or len(chat) > 200 for chat in chat_ids) or
                len(set(chat_ids)) != len(chat_ids)):
            raise ValueError('chat_ids 必须为最多 100 个不重复的会话 ID')
        if type(latest) is not int or not 1 <= latest <= 100:
            raise ValueError('批量 latest 必须为 1 到 100')
        return {chat: self.api_messages(chat, since, until, latest) for chat in chat_ids}

    def api_query(self, sql, params=None, since=None, until=None):
        if not isinstance(sql,str) or len(sql)>30000 or not isinstance(params or [],list): raise ValueError('SQL 参数无效')
        since = self.context['since'] if since is None else since
        until = self.context['until'] if until is None else until
        if any(type(v) not in (int,float) or not math.isfinite(v) for v in (since,until)) or not self.context['since']<=since<=until<=self.context['until']:
            raise ValueError('不能扩大平台时间范围')
        def read():
            reader = self.backends.get('indexed_window')
            return reader(since, until) if reader else [r for r in self.backends['indexed']() if since<=r['timestamp']<=until]
        # Reuse only an identical scope. A narrower call gets its own physically
        # bounded table, so user SQL cannot escape via CTEs, joins or subqueries.
        with self.sql_snapshot.lock:
            conn = self.sql_snapshot.prepare((since, until), read, COLUMNS, FUNCTIONS)
            self.sql_snapshot.deadline()
            result, size = [], 0
            originals = self.sql_snapshot.originals
            for row in conn.execute(sql, params or []):
                item = dict(row)
                size += len(json.dumps(item).encode())
                if size>24*1024*1024: raise ValueError('SQL 返回超过 24 MB，请在 Skill 中分页；未截断')
                result.append(item)
                original = originals.get(message_key(item))
                # 聚合或改写结果可供计算；只有保留原始消息字段的结果才能登记为引用证据。
                if original and all(item.get(k)==original.get(k) for k in COLUMNS if k != 'id'):
                    self.remember([item])
            return result

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
        # 核验后按消息身份复用引用编号，使多次引用同一消息保持一致。
        result = []
        for row in self.verified(messages):
            reference = next((s['reference'] for s in self.sources if message_key(s)==message_key(row)),None)
            if reference is None:
                reference = len(self.sources)+1
                self.sources.append({**row,'reference':reference})
            else:
                self.sources[reference-1] = {**row, 'reference':reference}
            result.append({**row,'reference':reference})
        return result

    def api_tools(self, names=None):
        # 与问答使用同一目录；只展示本包权限允许的子集，绝不允许模型自定义宿主工具。
        allowed=[n for n in SKILL_TOOL_NAMES if TOOL_PERMISSIONS[n]<=set(self.limits['permissions'])]
        names=allowed if names is None else names
        definitions=tool_definitions(names)
        if any(n not in allowed for n in names): raise ValueError('工具未开放或 Skill 未声明所需权限')
        return copy.deepcopy(definitions)

    def tool_plan(self):
        # Only the user's task window is permanent; tool observations carry identities.
        return {'tool_first':True,'people':[],'query':self.context['task'],
                'since_ts':self.context['since'],'until_ts':self.context['until'],
                'chat_scope':{'type':'all','keyword':''}}

    def api_tool(self, name, arguments):
        self.api_tools([name])
        plan=self.tool_plan()
        sources = self.available_sources()
        action=action_from_arguments(name,arguments,sources,self.tool_observations,plan)
        return self.execute_registered(plan, action, sources)

    def available_sources(self):
        # Python reads and native tools share identity discovery and citation provenance.
        rows = {r['evidence_id']:r for r in self.ledger.values()}
        rows.update({r['evidence_id']:r for r in self.tool_sources})
        return list(rows.values())

    def execute_registered(self, plan, action, sources):
        """Both orchestrators enter here after validating native arguments."""
        plan = copy.deepcopy(plan)
        name = action['tool']
        self.api_tools([name])
        # Per-call filters do not mutate the task's immutable time boundary.
        chats={c['chat_id']:c.get('chat_type') for o in self.tool_observations for c in o.get('chats',[])}
        chats.update({s.get('chat_id'):s.get('chat_type') for s in self.tool_sources if s.get('chat_id')})
        kinds={chats.get(c) for c in action.get('chat_ids', [])}
        if len(kinds)==1 and next(iter(kinds)) in ('group','private'):
            plan['chat_scope']['type']=next(iter(kinds))
        if name=='finish': return {'tool':'finish','items':[],'complete':True}
        if name not in DATA_TOOLS and self.tool_backend is None: raise ValueError('共享检索工具未连接，请检查平台配置')
        if name=='search':
            self.embedding_calls+=1
            self.model_calls+=1
            if self.embedding_calls>self.limits['max_embedding_calls'] or self.model_calls>self.limits['max_llm_calls']:
                raise ValueError('共享检索的 Embedding/重排调用预算已用完')
        result = self.execute_data(name, action['arguments'], sources) if name in DATA_TOOLS else self.tool_backend(plan,action,sources)
        items=[]
        for item in result.get('items',[]):
            if item.get('evidence_role')=='statistics':
                existing=next((s for s in self.sources if s.get('evidence_id')==item['evidence_id']),None)
                cited=existing or {**item,'reference':len(self.sources)+1}
                if existing is None: self.sources.append(cited)
            else:
                cited=self.api_cite(self.remember([item]))[0]
            items.append(cited)
        self.tool_sources = merge_evidence(self.tool_sources, items)
        for source in self.tool_sources:
            linked = result.get('context_links', {}).get(source['evidence_id'])
            if linked is not None:
                source['context_ids'] = list(dict.fromkeys([*source.get('context_ids', []), *linked]))
        self.tool_observations.append({k:v for k,v in result.items() if k not in ('items','diagnostics')})
        return {**result,'items':items}

    def execute_data(self, name, arguments, sources):
        """Native tools are paged views of the Python API, not another data engine."""
        args = dict(arguments)
        offset, size = args.pop('offset'), args.pop('size')
        method = DATA_TOOLS[name][0]
        previous_keys = set(self.ledger)
        previous_observations = len(self.tool_observations)
        context_for = {}
        if name == 'message_context':
            ids = args.pop('source_ids')
            seeds = [s for s in sources if s['evidence_id'] in ids]
            rows = []
            # Preserve each seed's narrower retrieval window when expanding context.
            for seed in seeds:
                neighbours = self.dispatch(method, dict(args, messages=[seed]))
                bounds = seed.get('retrieval_bounds') or {}
                low = max(self.context['since'], bounds.get('since_ts') or self.context['since'])
                high = min(self.context['until'], bounds.get('until_ts') or self.context['until'])
                for row in neighbours:
                    if low<=row['timestamp']<=high:
                        key = message_key(row)
                        if key not in context_for: rows.append(row)
                        context_for.setdefault(key, []).append(seed['evidence_id'])
        else:
            result = self.dispatch(method, args)
            rows = (result['messages'] if name=='semantic_search' else
                    [r for batch in result.values() for r in batch] if name=='read_messages_batch' else result)
        selected = rows[offset:offset+size]
        # The native caller only received this page; do not expose hidden pages as
        # known identities/evidence merely because the Python backend read them.
        selected_keys = {message_key(row) for row in selected}
        self.ledger = {key:row for key,row in self.ledger.items() if key in previous_keys or key in selected_keys}
        del self.tool_observations[previous_observations:]
        more = offset+len(selected)<len(rows)
        output = {'tool':name, 'items':[], 'matched':len(rows), 'next_offset':offset+len(selected) if more else None,
                  'complete':not more, 'note':'返回页仅覆盖本次查询；不代表所有历史或全部主题信息。'}
        if name=='read_chats':
            output['chats'] = [{'chat_id':r['id'],'chat_title':r['title'],'chat_type':r['type'],'last_ts':r['last_ts']} for r in selected]
            return output
        if name=='semantic_search' or name.startswith('read_messages') and args.get('latest') is not None:
            output['complete'] = False
        if name=='semantic_search': output['note'] = result.get('warning') or output['note']
        derived = []
        for row in selected:
            original = self.ledger.get(message_key(row))
            if name=='query_messages' and (not original or any(row.get(k)!=original.get(k) for k in COLUMNS if k!='id')):
                derived.append(row)
                continue
            item = dict(row, evidence_id=evidence_id(row), evidence_role='surrounding' if name=='message_context' else 'primary')
            if name=='query_messages' and row.get('id') is not None:
                item['index_row_id'] = row['id']
            item['retrieval_bounds'] = {'since_ts':args.get('since') if args.get('since') is not None else self.context['since'],
                                        'until_ts':args.get('until') if args.get('until') is not None else self.context['until']}
            if context_for: item['context_for'] = context_for[message_key(row)]
            output['items'].append(item)
        if derived:
            text = json.dumps({'sql':args['sql'],'params':args.get('params'), 'since':args.get('since'),
                               'until':args.get('until'),'rows':derived}, ensure_ascii=False)
            if len(text)>24000: raise ValueError('SQL 计算摘要过大，请缩小返回页；未截断')
            output['items'].append({'evidence_id':'sql:'+hashlib.sha256(text.encode()).hexdigest()[:24],
                'evidence_role':'statistics','chat_type':'index_summary','chat_title':'只读 SQL 计算结果',
                'type':'索引摘要','text':text})
            output['note'] += ' SQL计算摘要不是消息原文，结论需核对查询表达式。'
        return output

    def api_llm(self, messages, schema=None, tools=None, tool_choice='auto'):
        validate_messages(messages,native=tools is not None)
        if schema is not None and (not isinstance(schema,dict) or len(json.dumps(schema))>30000): raise ValueError('输出 schema 无效')
        if tool_choice not in ('auto','required','none'): raise ValueError('tool_choice 无效')
        definitions=self.api_tools(tools) if tools is not None else None
        self.model_calls += 1
        if self.model_calls>self.limits['max_llm_calls']: raise ValueError('模型调用预算已用完')
        if definitions is None:
            value, usage = self.backends['llm'](messages,schema)
        else:
            value, usage = self.backends['llm'](messages,schema,tools=definitions,tool_choice=tool_choice)
            self.add_usage(usage,'llm')
            if not isinstance(value,dict): raise ValueError('模型工具响应格式无效')
            if value.get('finish_reason')=='tool_calls':
                if tool_choice=='none': raise ValueError('本轮禁止工具调用，但模型仍返回了工具指令')
                _,assistant=parse_tool_call({'choices':[value]},self.available_sources(),self.tool_observations,self.tool_plan(),messages)
                if assistant['tool_calls'][0]['function']['name'] not in tools: raise ValueError('模型调用了未提供的工具')
                value={**value,'message':assistant}
            elif value.get('finish_reason')!='stop' or tool_choice=='required':
                raise ValueError('模型未完成有效的工具调用或回答')
            else:
                message=value.get('message')
                if not isinstance(message,dict) or message.get('refusal') or message.get('tool_calls') or not isinstance(message.get('content'),str) or not message['content'].strip():
                    raise ValueError('模型未返回有效回答')
                value={**value,'message':{'role':'assistant','content':message['content']}}
        if definitions is None: self.add_usage(usage,'llm')
        return value

    def add_usage(self, usage, kind):
        self.usage['total_tokens'] += int(usage.get('total_tokens') or 0)
        self.usage['model_calls'].append({'kind':kind,**usage})

    def api_state_get(self): return copy.deepcopy(self.staged_state)

    def api_state_set(self, value):
        # 状态更新只暂存于本次执行，避免失败或验证运行提前覆盖跨轮状态。
        if not isinstance(value,dict) or len(json.dumps(value,allow_nan=False).encode())>1024*1024: raise ValueError('任务状态必须是小于 1 MB 的 JSON 对象')
        self.staged_state, self.state_changed = copy.deepcopy(value), True
        return {'staged':True}

    def api_log(self, message):
        if not isinstance(message,str) or len(message)>500: raise ValueError('日志最长 500 字')
        self.report(message)
        return None

    def snapshot(self):
        # 回放除返回值外还要还原证据、预算计数和暂存状态，才能保持后续行为一致。
        return {'tool_sources':self.tool_sources,'tool_observations':self.tool_observations,
                'ledger':list(self.ledger.values()), 'sources':self.sources,'steps':self.steps,
                'usage':self.usage,'model_calls':self.model_calls,'embedding_calls':self.embedding_calls,
                'staged_state':self.staged_state,'state_changed':self.state_changed}

    def restore(self, snapshot):
        self.ledger = {message_key(r):{**r,'evidence_id':r.get('evidence_id') or evidence_id(r),
                                      'evidence_role':r.get('evidence_role') or 'primary'} for r in snapshot['ledger']}
        self.tool_sources=snapshot.get('tool_sources',[])
        self.tool_observations=snapshot.get('tool_observations',[])
        for name in ('sources','steps','usage','model_calls','embedding_calls','staged_state','state_changed'):
            setattr(self,name,snapshot[name])
