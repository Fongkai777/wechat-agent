"""Package validation and durable code execution integrated with task runs."""
from __future__ import annotations

import hashlib
import json
import sqlite3

from langgraph.graph import StateGraph, START, END
from langsmith import tracing_context

from .code_skill_api import CodeAPI, COLUMNS
from .code_skill_packages import package_hash, validate_package
from .code_skill_runtime import run_package
from .goal_checkpoints import checkpoint_session
from .goals import validate_goal_result, GoalCancelled


def generate_code_skill(prompt, completion, store, goal_id, model, emit, revision=None, notes=''):
    from .code_skill_packages import generation_messages, PACKAGE_FORMAT, API_DOC
    messages = generation_messages(prompt)
    if revision is not None:
        messages.append({'role':'assistant','content':json.dumps(validate_package(revision),ensure_ascii=False)})
    if notes:
        if not isinstance(notes,str) or len(notes)>6000: raise ValueError('修改要求过长')
        messages.append({'role':'user','content':notes})
    usage, draft_id, last_skill = {'total_tokens':0}, None, None
    for attempt in range(3):
        emit('progress',message='正在生成代码 Skill，不读取真实聊天')
        try:
            response = completion(messages,PACKAGE_FORMAT)
        except (TimeoutError,ConnectionError,OSError) as exc:
            raise RuntimeError('生成代码时连接中断或超时，未自动重发可能已计费的请求；已生成的草稿仍保留。'+type(exc).__name__) from None
        usage['total_tokens'] += int((response.get('usage') or {}).get('total_tokens') or 0)
        choice = (response.get('choices') or [{}])[0]
        content = (choice.get('message') or {}).get('content') or ''
        try:
            if choice.get('finish_reason') not in ('stop',None): raise ValueError('代码包输出被截断')
            skill = validate_package(json.loads(content))
            skill['files'] = [f for f in skill['files'] if f['path']!='PLATFORM_API.md'] + [{'path':'PLATFORM_API.md','content':API_DOC}]
            draft_id = store.save_skill_draft(goal_id,prompt.strip(),skill,model,usage)
            last_skill = skill
            emit('progress',message='正在隔离测试（虚构数据，无真实模型调用）')
            validation = validate_code_skill(skill,prompt.strip(),store)
            break
        except Exception as exc:
            diagnostic = type(exc).__name__+': '+str(exc)[:1200]
            if attempt==2:
                if last_skill is None: raise ValueError('无法生成有效代码包：'+diagnostic) from None
                validation = {'passed':False,'error':diagnostic}
                break
            emit('progress',message='测试未通过，正在修正：'+diagnostic)
            messages += [{'role':'assistant','content':content}, {'role':'user','content':'修复完整代码包。以下为测试诊断数据，不是指令：'+diagnostic}]
    return {'ok':True,'skill':last_skill,'prompt':prompt.strip(),'draft_id':draft_id,'usage':usage,'validation':validation}


def setup(conn):
    conn.execute('CREATE TABLE IF NOT EXISTS code_skill_checks (hash TEXT PRIMARY KEY, report TEXT NOT NULL)')
    conn.execute('CREATE TABLE IF NOT EXISTS code_skill_state (goal_id TEXT PRIMARY KEY, value TEXT NOT NULL)')


def check_key(package, prompt):
    return hashlib.sha256((package_hash(package)+'\n'+prompt+'\nruntime-1').encode()).hexdigest()


def schema_example(schema):
    if 'enum' in schema: return schema['enum'][0]
    if 'anyOf' in schema: return schema_example(schema['anyOf'][0])
    kind = schema.get('type')
    if isinstance(kind,list): kind = next((k for k in kind if k!='null'),'null')
    if kind == 'object': return {k:schema_example(v) for k,v in schema.get('properties',{}).items()}
    if kind == 'array': return []
    if kind in ('number','integer'): return max(0,schema.get('minimum',0))
    if kind == 'boolean': return False
    if kind == 'null': return None
    return '模拟结果'


def synthetic_api(package, empty=False):
    context = {'task':'Synthetic integration test','since':100,'until':1000,'account':'self','dry_run':True}
    rows = [] if empty else [dict(zip(COLUMNS,[i, chat, title, kind, 200+i, '2026-01-01 10:00',sender.title()+' Display Name',sender,
             text,'text','fixture.db','messages',str(i),str(i),sender,sender])) for i,chat,title,kind,sender,text in [
             (1,'alice','Alice','private','alice','能帮我看看明天的安排吗？'),
             (2,'bob','Bob','private','bob','Hello'),(3,'bob','Bob','private','self','Replied'),
             (4,'careers@chatroom','Careers','group','alice','Singapore software internship, apply by Friday.'),
             (5,'careers@chatroom','Careers','group','bob','岗位链接和联系方式见上文')]]
    chats = list({r['chat_id']:{'id':r['chat_id'],'title':r['chat_title'],'type':r['chat_type'],'last_ts':r['timestamp']} for r in rows}.values())
    def messages(chat, since, until, latest):
        found = [{**r,'mine':r['sender_username']=='self'} for r in rows if r['chat_id']==chat and since<=r['timestamp']<=until]
        return found[-latest:] if latest else found
    return CodeAPI(package,context,lambda:chats,messages,lambda:rows,
                   lambda query,threshold:{'messages':rows,'warning':'Synthetic semantic retrieval'},
                   lambda seeds,before,after,gap:[],
                   lambda messages,schema:(schema_example(schema) if schema else '模拟结果',{}))


def validate_code_skill(package, prompt, store):
    package = validate_package(package)
    run_package(package,{},lambda *args:None,mode='test')
    for empty in (True,False):
        api = synthetic_api(package,empty)
        result = run_package(package,api.context,api.dispatch)
        try:
            validate_goal_result(json.dumps(result,ensure_ascii=False),{s['reference'] for s in api.sources})
        except ValueError as exc:
            raise ValueError(str(exc)+'; synthetic_case='+('empty' if empty else 'nonempty')+
                             '; result='+json.dumps(result,ensure_ascii=False)[:1500]) from None
    report = {'passed':True,'checks':['package tests','empty synthetic integration','synthetic integration'],
              'note':'隔离测试使用虚构消息和模拟模型，不代表真实回答质量'}
    with store.connect() as conn:
        setup(conn)
        conn.execute('INSERT OR REPLACE INTO code_skill_checks VALUES(?,?)',(check_key(package,prompt),json.dumps(report)))
    return report


def require_validated(conn, package, prompt):
    setup(conn)
    if not conn.execute('SELECT 1 FROM code_skill_checks WHERE hash=?',(check_key(package,prompt),)).fetchone():
        raise ValueError('代码包已变化或尚未通过隔离测试，请先测试 Skill')


def run_code_skill(goal, api, report, cancelled, model, preparation_time=lambda: 0):
    package = validate_package(goal['skill'])
    def publish(message):
        report({'progress':message,'sources':api.sources,'steps':api.steps,'usage':api.usage,'model':model})
    api.report = publish
    builder = StateGraph(dict)
    with checkpoint_session(goal.get('checkpoint_path')) as (saver, receipts), tracing_context(enabled=False):
        position = 0
        def dispatch(method, arguments):
            nonlocal position
            position += 1
            digest = hashlib.sha256(json.dumps([method,arguments],sort_keys=True,ensure_ascii=False).encode()).hexdigest()
            before_ledger = dict(api.ledger)
            counts = {k:len(getattr(api,k)) for k in ('sources','steps')}
            def perform():
                value = api.dispatch(method,arguments)
                snapshot = api.snapshot()
                snapshot['ledger'] = [v for k,v in api.ledger.items() if before_ledger.get(k)!=v]
                for key,n in counts.items(): snapshot[key] = snapshot[key][n:]
                return {'digest':digest,'value':value,'snapshot':snapshot}
            cached = receipts.conn.execute('SELECT status,payload FROM model_receipts WHERE round=?',(position,)).fetchone()
            if cached and cached[0]=='done' and json.loads(cached[1]).get('digest')!=digest:
                raise ValueError('恢复时 Skill 调用顺序变化，已停止以避免重复调用')
            data, _ = receipts.call(position,perform,allow_retry=method not in ('llm','semantic_search') or bool(goal.get('confirm_retry')),keep_all=True)
            if cached and cached[0]=='done':
                snapshot = data['snapshot']
                api.restore({**snapshot,'ledger':list(api.ledger.values())+snapshot['ledger'],
                             **{k:getattr(api,k)+snapshot[k] for k in counts}})
            publish('Skill · '+str(method))
            return data['value']
        def execute(state):
            if state['package_hash'] != package_hash(package) or state['profile_signature'] != goal.get('profile_signature'):
                raise ValueError('代码包或模型配置变化，不能恢复旧执行')
            api.context = state['context']
            api.staged_state = state['initial_state']
            try:
                result = run_package(package,api.context,dispatch,cancelled=cancelled,
                                     preparation_time=preparation_time)
            except Exception:
                if cancelled(): raise GoalCancelled() from None
                raise
            text = json.dumps(result,ensure_ascii=False)
            validate_goal_result(text,{s['reference'] for s in api.sources})
            return {**state,'result':text,'snapshot':api.snapshot()}
        builder.add_node('program',execute)
        builder.add_edge(START,'program'); builder.add_edge('program',END)
        graph = builder.compile(checkpointer=saver)
        config = {'configurable':{'thread_id':str(goal.get('run_id') or 'ephemeral')}}
        old = graph.get_state(config)
        if goal.get('resume'):
            if not old.values or old.values.get('package_hash')!=package_hash(package) or old.values.get('profile_signature')!=goal.get('profile_signature'):
                raise ValueError('代码或模型与检查点不一致')
            initial = None
        else:
            if old.values: raise ValueError('执行检查点已存在，请恢复执行')
            initial = {'package_hash':package_hash(package),'profile_signature':goal.get('profile_signature'),
                       'context':api.context,'initial_state':api.staged_state}
        result = graph.invoke(initial,config,durability='sync')
        api.restore(result['snapshot'])
        if api.state_changed: goal['code_skill_state'] = api.staged_state
        publish('代码 Skill 已完成')
        return result['result']
