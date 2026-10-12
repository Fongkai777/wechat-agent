"""Bind the portable Skill API to the application's existing data services."""
from __future__ import annotations

import hashlib
import json
import sqlite3
import time

from .code_skill_api import CodeAPI, COLUMNS
from .code_skill_packages import validate_package
from .code_skill_service import require_validated, run_code_skill, setup
from .goals import observation_window, GoalCancelled
from .skill_retrieval import SkillRetrieval


def prepare_code_skill_indexes(state, semantic, progress, add_usage):
    """Prepare platform-owned dependencies without changing the Skill package."""
    # 索引按需准备：先保证全文索引可用，语义检索再补齐向量索引并复查状态。
    from . import web as app
    status = app.qa_search_db_status(state)
    if not status.get('ready') or status.get('soft_stale'):
        progress('progress', message='任务准备：自动更新全文索引')
        app.update_qa_search_db_incremental(state, progress)
        status = app.qa_search_db_status(state)
        if not status.get('ready') or status.get('soft_stale'):
            raise RuntimeError('全文索引自动更新后仍不可用：'+str(status.get('error') or '请检查同步数据和索引状态'))
    if not semantic:
        return
    status = app.qa_embedding_index_status(state)
    if status.get('ready') and not status.get('soft_stale') and not status.get('pending_message_count'):
        return
    progress('progress', message='任务准备：自动更新语义索引')
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


def execute_code_goal(state, store, goal, report, cancelled, maintenance_lock):
    # 应用执行入口：把当前账户的数据、模型配置及任务状态绑定为受控 API 后交给 service。
    from . import web as app
    # Database claims and persisted run snapshots contain serialized packages.
    package = goal['skill']
    goal['skill'] = validate_package(json.loads(package) if isinstance(package, str) else package)
    config = app.load_llm_config(state.llm_config)
    profile = config['task']
    embedding_profile = app.effective_llm_profile(config,'embedding')
    # 恢复签名绑定模型配置和数据来源，但不包含密钥，换密钥本身不会使检查点失效。
    goal['profile_signature'] = hashlib.sha256(json.dumps(
        {name:{k:v for k,v in value.items() if k!='api_key'} for name,value in
         [('task',profile),('embedding',embedding_profile),('source',{'account':state.account,'storage':str(state.db_storage)})]},sort_keys=True).encode()).hexdigest()
    since, until = observation_window(goal)
    # 拒绝超出已导入历史的任务，避免把数据缺失误判为没有匹配消息。
    if state.since_ts and since < state.since_ts:
        raise ValueError('任务范围早于已导入数据的起始日期，请调整范围或导入历史数据')
    if not state.account: raise ValueError('无法识别当前账户，请先同步聊天')
    with store.connect() as conn:
        # 真实执行前检查测试凭据，并按任务 ID 读取跨轮状态，避免任务之间共享状态。
        require_validated(conn,goal['skill'],goal['prompt'])
        row = conn.execute('SELECT value FROM code_skill_state WHERE goal_id=?',(goal['id'],)).fetchone()
        local_state = json.loads(row[0]) if row else {}
    voice = app.load_voice_cache(state.voice_cache)
    def check():
        if cancelled(): raise GoalCancelled()
    preparation_seconds = 0.0
    # 数据读取和索引维护共用锁；等锁与准备索引的耗时单独统计，供运行器扣除。
    def locked(operation):
        nonlocal preparation_seconds
        check()
        if not maintenance_lock.acquire(blocking=False):
            start = time.monotonic()
            try:
                api.report('任务准备：等待正在进行的数据或索引更新')
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
                api.report('任务准备 · '+str(data['message']))
        try:
            check()
            prepare_code_skill_indexes(state, semantic, progress, api.add_usage)
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
    indexed_cache = None
    # 首次 SQL 查询准备并缓存任务时间窗快照，后续查询复用，超量时明确失败而不截断。
    def indexed():
        nonlocal indexed_cache
        if indexed_cache is not None: return indexed_cache
        def read():
            prepare_indexes()
            conn = sqlite3.connect(state.qa_search_db.resolve().as_uri()+'?mode=ro',uri=True,timeout=10)
            conn.row_factory = sqlite3.Row
            try:
                rows = []
                for row in conn.execute('SELECT '+','.join(COLUMNS)+' FROM messages WHERE timestamp>=? AND timestamp<=? ORDER BY timestamp,id',(since,until)):
                    check()
                    rows.append(dict(row))
                    if len(rows)>300000: raise ValueError('索引时间范围超过安全预算，请缩小范围；未截断')
                return rows
            finally: conn.close()
        indexed_cache = locked(read)
        return indexed_cache
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
        embed,app.qa_item_from_search_row,app.collect_qa_items_for_chat,check,voice)
    def semantic(query,threshold):
        # 索引准备和检索需持锁，远程查询向量请求放在锁外，减少对其他数据操作的阻塞。
        def prepare():
            prepare_indexes(semantic=True)
            retrieval.prepare()
        locked(prepare)
        embedding = embed(query)
        return locked(lambda:retrieval.search({'query':query,'keywords':[], 'source':'all_messages',
                                               'min_similarity':threshold},embedding,since,until))
    def surrounding(seeds,before,after,gap):
        def read():
            if any(s.get('index_row_id') is not None for s in seeds):
                prepare_indexes(semantic=True)
            return retrieval.context(seeds,{'before':before,'after':after,'max_gap_minutes':gap},since,until)['messages']
        return locked(read)
    def shared_tool(plan,action,sources):
        from .qa_tools import ConversationTools
        from .platform_tools import execute_action
        def hybrid(query,people,start,end,count):
            result,diagnostics=app.search_qa_search_db(state,query,people,start,count,
                {'verified_people':True,'time_until':end,'queries':[{'text':query}]})
            semantic=diagnostics.get('semantic') or {}
            if semantic.get('error') or diagnostics.get('rerank_error'):
                raise ValueError('共享检索未完整执行：'+str(semantic.get('error') or diagnostics.get('rerank_error')))
            api.add_usage(semantic.get('usage') or {},'shared_search_embedding')
            return result,diagnostics
        def run():
            prepare_indexes(semantic=action['tool']=='search')
            return execute_action(ConversationTools(state,hybrid,app.qa_item_from_search_row,check),plan,action,sources)
        return locked(run)

    def llm(messages,schema,**options):
        # 模型配置和密钥留在宿主侧；响应必须完整，声明 schema 时才按 JSON 解析。
        check()
        key = app.resolve_llm_api_key(profile)
        if not key: raise ValueError('请先配置任务模型 API Key')
        response_format = {'type':'json_schema','json_schema':{'name':'skill_result','strict':True,'schema':schema}} if schema else None
        payload = app.call_chat_payload(profile,key,messages,timeout=app.GOAL_MODEL_TIMEOUT_SECONDS,response_format=response_format,**options)
        choice = (payload.get('choices') or [{}])[0]
        if (choice.get('message') or {}).get('refusal'): raise ValueError('模型拒绝本次请求')
        if options.get('tools') is not None:
            if choice.get('finish_reason') not in ('tool_calls','stop'): raise ValueError('模型工具调用被截断或未完成')
            return {'finish_reason':choice['finish_reason'],'message':choice.get('message') or {}},payload.get('usage') or {}
        if choice.get('finish_reason') not in ('stop',None): raise ValueError('Skill 模型输出未完成，未保存成功结果')
        content = (choice.get('message') or {}).get('content')
        if not isinstance(content,str) or not content.strip(): raise ValueError('模型未返回内容')
        return json.loads(content) if schema else content, payload.get('usage') or {}
    api = CodeAPI(goal['skill'],{'task':goal['prompt'],'since':since,'until':until,'account':state.account,
                  'dry_run':False,'synced_at':state.last_synced_at,'sync_error':state.sync_error or ''},
                  chats,messages,indexed,semantic,surrounding,llm,state=local_state,tools=shared_tool)
    return run_code_skill(goal,api,report,cancelled,str(profile.get('model') or '代码 Skill'),
                          preparation_time=lambda:preparation_seconds)
