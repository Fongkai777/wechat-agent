"""Shared host bindings for Q&A and Skill data/model capabilities."""
from __future__ import annotations

import json
import sqlite3
import time
from types import SimpleNamespace

from .platform_api import PlatformAPI, COLUMNS
from .skill_retrieval import SkillRetrieval

QA_LIMITS = {'permissions':['messages.read','index.query','embedding','llm'],
             'max_llm_calls':256, 'max_embedding_calls':32}


def prepare_platform_indexes(state, semantic, progress, add_usage):
    """Prepare platform-owned dependencies without changing the Skill package."""
    # 索引按需准备：先保证全文索引可用，语义检索再补齐向量索引并复查状态。
    from . import web as app
    status = app.qa_search_db_status(state)
    if not status.get('ready') or status.get('soft_stale'):
        progress('progress', message='准备：自动更新全文索引')
        app.update_qa_search_db_incremental(state, progress)
        status = app.qa_search_db_status(state)
        if not status.get('ready') or status.get('soft_stale'):
            raise RuntimeError('全文索引自动更新后仍不可用：'+str(status.get('error') or '请检查同步数据和索引状态'))
    if not semantic:
        return
    status = app.qa_embedding_index_status(state)
    if status.get('ready') and not status.get('soft_stale') and not status.get('pending_message_count'):
        return
    progress('progress', message='准备：自动更新语义索引')
    accounted = {}
    # 进度和最终结果都可能携带累计用量，只记新增部分以免重复计费统计。
    def account(totals):
        delta = {k:max(0,int(v)-accounted.get(k,0)) for k,v in (totals or {}).items()
                 if k in ('prompt_tokens','input_tokens','output_tokens','total_tokens')}
        if any(delta.values()):
            add_usage(delta, 'index_embedding')
        accounted.update({k:accounted.get(k,0)+v for k,v in delta.items()})
    def semantic_progress(event, **data):
        account(data.get('usage_totals'))
        progress(event, **data)
    result = app.build_or_update_qa_semantic_index(state, full=False, progress=semantic_progress)
    account(result.get('usage_totals'))
    status = app.qa_embedding_index_status(state)
    if not status.get('ready') or status.get('soft_stale') or status.get('pending_message_count'):
        raise RuntimeError('语义索引自动更新后仍不可用：'+str(status.get('error') or '请检查 Embedding 配置和索引状态'))



class PlatformModel:
    """Keep provider configuration and credentials in the host, not the caller."""
    def __init__(self, profile, config, check, timeout=90):
        self.profile, self.config, self.check, self.timeout = profile, config, check, timeout

    def complete(self, messages, response_format=None, **options):
        from . import web as app
        self.check()
        key = app.resolve_llm_api_key(self.profile)
        if not key:
            raise ValueError('请先配置模型 API Key')
        payload = app.call_chat_payload(self.profile, key, messages, timeout=self.timeout,
                                       response_format=response_format, **options)
        self.check()
        return payload

    def llm(self, messages, schema=None, **options):
        response_format = {'type':'json_schema','json_schema':{'name':'platform_result','strict':True,'schema':schema}} if schema else None
        payload = self.complete(messages, response_format, **options)
        choice = (payload.get('choices') or [{}])[0]
        if (choice.get('message') or {}).get('refusal'):
            raise ValueError('模型拒绝本次请求')
        if options.get('tools') is not None:
            if choice.get('finish_reason') not in ('tool_calls','stop'):
                raise ValueError('模型工具调用被截断或未完成')
            return {'finish_reason':choice['finish_reason'],'message':choice.get('message') or {}}, payload.get('usage') or {}
        if choice.get('finish_reason') not in ('stop', None):
            raise ValueError('模型输出未完成，未保存成功结果')
        content = (choice.get('message') or {}).get('content')
        if not isinstance(content, str) or not content.strip():
            raise ValueError('模型未返回内容')
        return json.loads(content) if schema else content, payload.get('usage') or {}


def create_platform_runtime(state, context, limits, config, profile_name, check, maintenance_lock,
                            local_state=None, report=lambda message: None, prepare=prepare_platform_indexes):
    """Bind one execution window to the same backends for either orchestrator."""
    from . import web as app
    since, until = context['since'], context['until']
    profile = config[profile_name]
    embedding_profile = app.effective_llm_profile(config, 'embedding')
    voice = app.load_voice_cache(state.voice_cache)
    preparation_seconds = 0.0
    # 数据读取和索引维护共用锁；等锁与准备索引的耗时单独统计，供运行器扣除。
    def locked(operation):
        nonlocal preparation_seconds
        check()
        if not maintenance_lock.acquire(blocking=False):
            start = time.monotonic()
            try:
                api.report('准备：等待正在进行的数据或索引更新')
                while not maintenance_lock.acquire(timeout=.2):
                    check()
                    if time.monotonic()-start > 1800:
                        raise TimeoutError('等待数据或索引更新超过 30 分钟')
            finally:
                preparation_seconds += time.monotonic()-start
        try:
            check()
            return operation()
        finally: maintenance_lock.release()
    def prepare_indexes(semantic=False):
        nonlocal preparation_seconds
        start = time.monotonic()
        def progress(event, **data):
            check()
            if time.monotonic()-start > 1800:
                raise TimeoutError('自动准备索引超过 30 分钟；已完成的增量更新会保留')
            if data.get('message'):
                api.report('准备 · '+str(data['message']))
        try:
            check()
            prepare(state, semantic, progress, api.add_usage)
            check()
        finally:
            preparation_seconds += time.monotonic()-start
    def chats():
        return locked(lambda:[dict(c) for c in state.chats if (c.get('last_ts') or 0)>=since])
    def messages(chat_id,start,end,latest):
        def read():
            chat = state.chat_by_id(chat_id)
            if chat is None: raise ValueError('未找到会话')
            if not chat.get('shards'): raise ValueError('会话没有可读取的数据分片')
            # 严格读取并保留无正文消息，避免遗漏分片错误或误判实际消息先后关系。
            return app.collect_qa_items_for_chat(state,chat,since_ts=start,until_ts=end,max_items=latest,
                voice_cache=voice,max_text_chars=None,check_cancelled=check,strict=True,include_empty=True)
        return locked(read)
    indexed_cache = {}
    # 首次 SQL 查询准备并缓存任务时间窗快照，后续查询复用，超量时明确失败而不截断。
    def indexed(start=since, end=until):
        key = (start, end)
        if key in indexed_cache: return indexed_cache[key]
        def read():
            prepare_indexes()
            conn = sqlite3.connect(state.qa_search_db.resolve().as_uri()+'?mode=ro',uri=True,timeout=10)
            conn.row_factory = sqlite3.Row
            try:
                rows = []
                for row in conn.execute('SELECT '+','.join(COLUMNS)+' FROM messages WHERE timestamp>=? AND timestamp<=? ORDER BY timestamp,id',(start,end)):
                    check()
                    rows.append(dict(row))
                    if len(rows)>300000: raise ValueError('索引时间范围超过安全预算，请缩小范围；未截断')
                return rows
            finally: conn.close()
        # Retain only the latest window to avoid accumulating multiple full snapshots.
        rows = locked(read)
        indexed_cache.clear()
        indexed_cache[key] = rows
        return rows
    def embed(query):
        key = app.resolve_llm_api_key(config['embedding'],config.get('qa'))
        if not key: raise ValueError('请先配置 Embedding API Key')
        vectors, usage = app.call_embeddings(embedding_profile,key,[query])
        api.add_usage(usage,'embedding')
        return {'vectors':vectors,'usage':usage}
    retrieval = SkillRetrieval(state,
        lambda:(app.qa_search_db_status(state),app.qa_embedding_index_status(state)),
        lambda query,start,end,embedding:app.query_qa_semantic_index(state,query,[],start,None,
            {'queries':[{'text':query}],'time_until':end},query_embedding=embedding),
        app.qa_item_from_search_row,app.collect_qa_items_for_chat,check,voice)
    def semantic(query,threshold):
        # 索引准备和检索需持锁，远程查询向量请求放在锁外，减少对其他数据操作的阻塞。
        def prepare():
            prepare_indexes(semantic=True)
            retrieval.prepare()
        locked(prepare)
        embedding = embed(query)
        return locked(lambda:retrieval.search(query,embedding,since,until,threshold))
    def surrounding(seeds,before,after,gap):
        def read():
            if any(s.get('index_row_id') is not None for s in seeds):
                prepare_indexes()
            return retrieval.context(seeds,{'before':before,'after':after,'max_gap_minutes':gap},since,until)['messages']
        return locked(read)
    def shared_tool(plan,action,sources):
        from .qa_tools import ConversationTools
        from .platform_tools import execute_action
        def hybrid(query,people,start,end,count,scope=None):
            calls = 0
            def rerank_call(profile, key, messages, **options):
                nonlocal calls
                # api.tool reserves the first rerank call; charge every extra batch/repair.
                if calls:
                    api.model_calls += 1
                    if api.model_calls > api.limits['max_llm_calls']:
                        raise ValueError('共享检索的重排调用预算已用完')
                calls += 1
                payload = app.call_chat_payload(profile, key, messages, **options)
                api.add_usage(payload.get('usage') or {}, 'shared_search_rerank')
                choice = (payload.get('choices') or [{}])[0]
                if choice.get('finish_reason') != 'stop':
                    raise RuntimeError('重排输出未完成')
                return (choice.get('message') or {}).get('content') or ''
            result,diagnostics=app.search_qa_search_db(state,query,people,start,count,
                {**(scope or {}),'verified_people':True,'time_until':end,'queries':[{'text':query}]},
                rerank_call=rerank_call)
            semantic=diagnostics.get('semantic') or {}
            api.add_usage(semantic.get('usage') or {},'shared_search_embedding')
            if semantic.get('error') or diagnostics.get('rerank_error'):
                raise ValueError('共享检索未完整执行：'+str(semantic.get('error') or diagnostics.get('rerank_error')))
            return result,diagnostics
        def run():
            prepare_indexes(semantic=action['tool']=='search')
            return execute_action(ConversationTools(state,hybrid,app.qa_item_from_search_row,check),plan,action,sources)
        return locked(run)

    model = PlatformModel(profile, config, check, app.GOAL_MODEL_TIMEOUT_SECONDS if profile_name == 'task' else 90)
    api = PlatformAPI(limits, context, chats, messages, indexed, semantic, surrounding, model.llm,
                      state=local_state, report=report, tools=shared_tool)
    api.backends['indexed_window'] = indexed
    return SimpleNamespace(api=api, model=model, preparation_time=lambda: preparation_seconds)


class PlatformToolSession:
    """Q&A adapter; its graph owns decisions, this session owns capabilities."""
    def __init__(self, state, runtime):
        self.state, self.runtime = state, runtime

    def execute_platform_action(self, plan, action, sources):
        return self.runtime.api.execute_registered(plan, action, sources)

    def snapshot(self):
        return self.runtime.api.snapshot()

    def restore(self, snapshot):
        self.runtime.api.restore(snapshot)
