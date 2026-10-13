"""A bounded tool-first LangGraph agent with server-owned evidence.

SQLite checkpoints and call receipts allow explicit recovery without replaying
completed stages. Restarting a process never automatically retries model calls.
"""
from __future__ import annotations

import json
import time
from copy import deepcopy
from datetime import datetime
from typing import TypedDict

from .goal_checkpoints import checkpoint_session
from .qa_recovery import AGENT_VERSION, CallReceipts, frozen_input, recorded_call
from .qa_review import review_answer
from .evidence_context import merge_evidence, select_evidence, context_windows
from .platform_tools import (INSTRUCTIONS, FUNCTION_TOOLS, execute_action,
                             parse_tool_call, tool_result_message, call_may_bill)


# 图内共享计划、证据和覆盖率；原生调用消息与调用 ID 一并保存，供恢复后续接。
class AgentState(TypedDict, total=False):
    plan: dict
    sources: list
    trace: list
    coverage: dict
    answer_data: dict
    usage: dict
    action: dict
    rounds: int
    observations: list
    stop_reason: str
    tool_messages: list
    tool_call_id: str
    platform_state: dict
    evidence_pool: list
    reviews: list
    review_rounds: int
    review_route: str
    review_feedback: dict
    quality_status: str


def limitation(text):
    return {"paragraphs": [{"kind": "limitation", "text": text, "source_refs": []}]}


def run_conversation_agent(question, history, people, tools, complete, answer, emit, checkpoint,
                           check, limit=60, now=None, checkpoint_path=None, resume=False,
                           confirm_retry=False, identity=None, context_char_budget=120000,
                           final_message_limit=None):
    now = now or datetime.now().astimezone()
    with checkpoint_session(checkpoint_path) as (saver, receipts):
        # 入口冻结问题、历史、时钟和版本；恢复读取原快照，并核对模型与数据来源标识。
        inputs = frozen_input(receipts.conn, {
            'question': question, 'history': history, 'people': people, 'limit': limit,
            'now': now.isoformat(), 'identity': identity,
            'agent_version': AGENT_VERSION,
            'context_char_budget': context_char_budget,
            'final_message_limit': final_message_limit,
        }, resume)
        if inputs.get("agent_version") != AGENT_VERSION:
            raise ValueError("旧版检索执行记录已停用，请重新提问；已保存的历史回答不受影响")
        return _run_agent(inputs, tools, complete, answer, emit, checkpoint, check,
                          saver, CallReceipts(receipts.conn, confirm_retry), resume)


def _run_agent(inputs, tools, complete, answer, emit, checkpoint, check, saver, receipts, resume):
    from langgraph.graph import StateGraph, START, END
    from langsmith import tracing_context

    question, history, people = inputs['question'], inputs['history'], inputs['people']
    now = datetime.fromisoformat(inputs['now'])
    limit = max(10, min(int(inputs['limit']), 200))
    # Search targets count hits, not the neighbours needed to interpret them.
    # A row cap is opt-in for controlled evaluations, not production answers.
    final_message_limit = inputs['final_message_limit']
    char_budget = max(10000, min(int(inputs['context_char_budget']), 1000000))
    retrieval_limit = 800
    started = time.monotonic()
    usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "calls": 0,
             "scope": "tool_decisions_and_reviews"}

    def model(messages, schema=None, tool_definitions=None):
        check()
        if time.monotonic() - started > 900:
            raise TimeoutError("本轮检索达到 900 秒预算，已保存执行过程，请重试")
        # 回执复用已完成请求；结果未知的计费请求由恢复层要求明确确认后重试。
        result = recorded_call('complete', lambda: complete(messages, schema,tools=tool_definitions,tool_choice='required')
                               if tool_definitions is not None else complete(messages,schema), True)
        usage["calls"] += 1
        for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
            usage[key] += int((result.get("usage") or {}).get(key) or 0)
        check()
        return result

    def diagnostics(s):
        plan = s.get("plan") or {}
        since, until = plan.get("since_ts"), plan.get("until_ts")
        time_scope = ""
        if since is not None:
            time_scope = f"{datetime.fromtimestamp(since).isoformat(sep=' ', timespec='seconds')} 至 {datetime.fromtimestamp(until).isoformat(sep=' ', timespec='seconds')}"
        scope = '按各步工具条件检索'
        chat_scope=plan.get('chat_scope') or {}
        if chat_scope.get('type') in ('group','private'):
            scope += ' · '+('群聊' if chat_scope['type']=='group' else '私聊')
        if chat_scope.get('keyword'):
            scope += ' · 会话名包含 '+chat_scope['keyword']
        return {"mode": "原生工具调用检索", "scope": scope,
                "time_scope": time_scope, "query_plan": plan, "trace": s.get("trace", []),
                "coverage": s.get("coverage", {}), "usage": dict(s.get('usage') or usage), "context_count": len(s.get("sources", [])),
                'answer_reviews':s.get('reviews',[]),'quality_status':s.get('quality_status','pending'),
                "conversation_state": {"people": plan.get("people", []), "query": plan.get("query", ""),
                                       'chat_scope':plan.get('chat_scope',{}),
                                       "requested_at": plan.get("requested_at", ""), "time_expression": plan.get("time_expression", "")}}

    def save(s, stage):
        # 对外发布诊断与证据快照；图节点的持久化另由 LangGraph 检查点负责。
        check()
        emit("progress", message=stage)
        checkpoint(diagnostics(s), s.get("sources", []))
        return s

    def run_tool(s, name, call, parameters=None):
        check()
        if len(s.get("trace", [])) >= 8 or time.monotonic() - started > 900:
            raise TimeoutError("检索预算已用完，未继续扩大查询")
        tick = time.monotonic()
        snapshot = getattr(type(tools), 'snapshot', None)
        def invoke():
            result = call()
            return {**result, '_platform_state':tools.snapshot()} if snapshot else result
        result = dict(recorded_call(name, invoke, call_may_bill('tool', {'name':name})))
        # A completed tool receipt must also restore its evidence ledger and budgets.
        platform_state = result.pop('_platform_state', None)
        if platform_state is not None:
            tools.restore(platform_state)
            s['platform_state'] = platform_state
        check()
        # 按稳定证据 ID 排除已收录消息，再应用总预算；工具分页信息保留在轨迹中。
        seen = {item["evidence_id"] for item in s.get("sources", [])}
        merged = merge_evidence(s.get('sources', []), result['items'])
        for source in merged:
            linked = result.get('context_links', {}).get(source['evidence_id'])
            if linked is not None:
                source['context_ids'] = list(dict.fromkeys([*source.get('context_ids', []), *linked]))
        s['sources'], dropped = select_evidence(merged, limit=retrieval_limit)
        retained = {item['evidence_id'] for item in s['sources']}
        included = len(retained-seen)
        event = {"tool": name, "parameters": parameters or {}, "returned": len(result.get('people',result.get('chats',result["items"]))),
                 'unit':'个人物' if 'people' in result else '个会话' if 'chats' in result else '条',
                 "included": included, "matched": result.get("matched"),
                 "complete": bool(result.get("complete")) and not dropped,
                 "next_offset": result.get("next_offset"), "note": result.get("note", ""),
                 "elapsed_ms": round((time.monotonic() - tick) * 1000)}
        event['tool_call_id'] = s['tool_call_id']
        for field in ('anchor_count', 'context_count'):
            if field in result:
                event[field] = result[field]
        s["trace"] = [*s.get("trace", []), event]
        # 任一工具未完整返回或证据被预算截断，均不能标为穷尽覆盖。
        s["coverage"] = {"exhaustive": all(t["complete"] for t in s["trace"]),
                         "context_limit": final_message_limit, "context_char_budget": char_budget,
                         "included": len(s["sources"]),
                         "note": "仅覆盖本地索引；相关性检索和上下文预算不代表遍历全部消息"}
        save(s, f"{name}：返回 {event['returned']} 条，纳入 {event['included']} 条")
        # Tool previews must not advertise IDs removed from the working set;
        # otherwise a legitimate finish selection could fail ID validation.
        if dropped:
            result = {**result, 'items':[item for item in result['items'] if item['evidence_id'] in retained],
                      'complete':False, 'note':result.get('note','')+'；工作集预算省略了部分完整上下文窗口'}
        return result

    def plan_node(s):
        boundary = {'tool_first': True, 'people': [], 'query': question,
                    'since_ts': tools.state.since_ts, 'until_ts': int(now.timestamp()),
                    'chat_scope': {'type': 'all', 'keyword': ''}, 'requested_at': now.isoformat()}
        return save(dict(s, plan=boundary, sources=[], trace=[]), '开始按需检索')

    def decide_node(s):
        s=dict(s)
        # Bound exploration without pretending to have exhaustive coverage.
        if s.get('rounds',0)>=8 or len(s.get('sources',[]))>=retrieval_limit:
            reason='达到工具调用或上下文预算，未声称完整检索'
            s['coverage']=dict(s.get('coverage',{}),exhaustive=False,note=reason)
            s['evidence_pool'] = list(s.get('sources',[]))
            return dict(s,action={'tool':'finish'},stop_reason=reason)
        previews=[]
        remaining=30000
        # 优先展示最近取得的原文预览，同时另传全量证据目录供模型选择引用对象。
        for source in reversed(s.get('sources',[])):
            if remaining<=0: break
            text=str(source.get('text',''))
            excerpt=text[:min(1500,remaining)]
            remaining-=len(excerpt)
            previews.append({k:source.get(k) for k in ('evidence_id','chat_id','chat_title','chat_type','sender','time','evidence_role','context_ids','context_for')}
                            | {'text':excerpt,'preview_truncated':len(excerpt)<len(text)})
        request = {
            'question':question,'observations':s.get('observations',[]),
            'answer_review_feedback':s.get('review_feedback'),
            'trace':s.get('trace',[]),'evidence':previews,'calls_remaining':8-s.get('rounds',0),
            'evidence_slots_remaining':retrieval_limit-len(s.get('sources',[])),
            'search_hit_target':limit,
            'answer_evidence_limit':final_message_limit,
            'answer_context_char_budget':char_budget,
            'source_catalog':[{'evidence_id':x['evidence_id'],'chat_title':x.get('chat_title'),
                               'sender':x.get('sender'),'time':x.get('time')} for x in s.get('sources',[])]}
        request.update(current_time=now.isoformat(), platform_boundary={
            'since_ts':s['plan']['since_ts'],'until_ts':s['plan']['until_ts']},
            conversation_history=[{'role':m.get('role'),'content':str(m.get('content') or '')[:8000]}
                for m in history[-20:] if m.get('role') in ('user','assistant') and not any(m.get(k) for k in ('error','pending','stopped'))])
        messages=[{'role':'system','content':INSTRUCTIONS},
                  {'role':'user','content':json.dumps(request,ensure_ascii=False)}]
        emit('progress',message=f"模型决定第 {s.get('rounds',0)+1} 步检索")
        messages[0]['content'] += '每轮调用一个原生函数工具，结束时调用finish。'
        messages = [messages[0], {'role':'user','content':question}, *s.get('tool_messages',[]), messages[1]]
        action, assistant = parse_tool_call(model(messages,tool_definitions=FUNCTION_TOOLS),
            s.get('sources',[]),s.get('observations',[]),s['plan'],s.get('tool_messages',[]))
        s['tool_messages'] = [*s.get('tool_messages',[]),assistant]
        s['tool_call_id'] = assistant['tool_calls'][0]['id']
        if action['tool']=='finish':
            s['evidence_pool'] = list(s.get('sources',[]))
            s['tool_messages'].append(tool_result_message(s['tool_call_id'],{'tool':'finish','note':'已结束检索，进入引用回答阶段。'}))
        if action['tool']=='finish' and action['source_ids']:
            # 选择原文时连带保留关联上下文，避免丢失支持接话判断的其他发送者消息。
            s['sources'], incomplete = select_evidence(s['sources'], action['source_ids'])
            s['coverage']=dict(s.get('coverage',{}),exhaustive=False,included=len(s['sources']))
            if incomplete:
                s['coverage']['context_incomplete'] = True
        # 相同动作不再次执行，防止模型在相同查询上循环消耗预算。
        if any(t.get('parameters')==action for t in s.get('trace',[])):
            s['coverage']=dict(s.get('coverage',{}),exhaustive=False)
            s['tool_messages'].append(tool_result_message(s['tool_call_id'],{'tool':action['tool'],'note':'重复请求未执行，已停止检索。'}))
            return dict(s,action={'tool':'finish'},stop_reason='模型重复相同工具调用，已停止重复检索')
        return dict(s,action=action)

    def execute_node(s):
        s=dict(s)
        action=s['action']
        # 执行结果分别进入证据工作集、结构化观察和原生工具回执，再回到决策节点。
        result=run_tool(s,action['tool'],lambda:execute_action(tools,s['plan'],action,s['sources']),action)
        observation={k:result[k] for k in ('tool','matched','next_offset','complete','note','chats','people','windows','window_seconds') if k in result}
        s['observations']=[*s.get('observations',[]),observation]
        s['tool_messages']=[*s['tool_messages'],tool_result_message(s['tool_call_id'],result)]
        s['rounds']=s.get('rounds',0)+1
        return s

    def answer_node(s):
        s = dict(s)
        plan = s["plan"]
        # 有歧义或没有可引用原文时只返回限制说明，不调用回答模型补造结论。
        if not s["sources"]:
            s["answer_data"] = limitation("本轮未取得可引用的消息原文，尚不能回答这个问题；这不代表相关对话不存在。")
        else:
            save(s, "根据已核实的消息生成回答")
            # Retain whole local windows under both budgets. Never turn a
            # contextualised hit back into a dangling phrase by tail-slicing.
            context, trimmed = select_evidence(s['sources'], limit=final_message_limit, char_limit=char_budget)
            s["sources"] = context
            if trimmed:
                s["coverage"] = dict(s.get("coverage", {}), exhaustive=False, context_incomplete=True, included=len(context))
            if not context:
                s['answer_data'] = limitation('完整上下文超过本轮证据预算，未截取孤立短句作答。请缩小问题范围或提高证据文本预算。')
                return s
            grounded_question = question + "\n\n本轮工具执行记录与覆盖范围（查询条件不代表用户意图已经核实）：\n" + json.dumps({
                "platform_boundary": plan, "coverage": s.get("coverage"), "trace": s["trace"],
                'context_windows':context_windows(context),
                'stop_reason':s.get('stop_reason',''),
                'review_feedback_to_verify_against_sources':s.get('review_feedback')}, ensure_ascii=False)
            s["answer_data"] = recorded_call('answer', lambda: answer(grounded_question, context, history), True)
            # 回答引用对应最终 context 顺序；非穷尽结果再附服务端限制说明。
            if not s.get("coverage", {}).get("exhaustive", False):
                s["answer_data"]["paragraphs"].append({"kind": "limitation", "text": f"本轮纳入 {len(context)} 条证据，属于选取的相关片段，不代表完整检查了范围内全部消息。", "source_refs": []})
        check()
        return s

    def review_node(s):
        s = dict(s)
        if not s.get('sources'):
            return dict(s,review_route='end',quality_status='no_evidence')
        save(s,'检查回答与证据是否一致')
        tick = time.monotonic()
        result = review_answer(model,question,history,s['answer_data'],s['sources'],
                               s['trace'],s.get('coverage',{}),now)
        attempt = s.get('review_rounds',0)+1
        event = {**result,'attempt':attempt,'elapsed_ms':round((time.monotonic()-tick)*1000),
                 'source_count':len(s['sources']),
                 'source_ids':[source['evidence_id'] for source in s['sources']]}
        s['reviews'] = [*s.get('reviews',[]),event]
        s['review_rounds'] = attempt
        if result['decision']=='accept':
            s.update(review_route='end',quality_status='reviewed')
            return save(s,'回答检查完成（模型复核）')
        # Two correction rounds, with a final review. Never publish a draft
        # already flagged as wrong just because correction exhausted its budget.
        exhausted = (attempt >= 3 or (result['decision']=='retrieve' and
                     (s.get('rounds',0)>=8 or len(s.get('evidence_pool',s['sources']))>=retrieval_limit)))
        if exhausted:
            s.update(review_route='end',quality_status='unresolved')
            s['answer_data'] = limitation('回答检查仍发现未解决的问题，本轮未发布草稿结论。请查看查询过程中的检查记录，或缩小问题后重试。')
            return save(s,'回答检查未通过，已保留证据与检查记录')
        s['review_feedback'] = result
        s['quality_status'] = 'correcting'
        s['review_route'] = 'answer' if result['decision']=='revise' else 'decide'
        if result['decision']=='retrieve':
            # Restore evidence discarded by finish selection before resuming
            # tools; the next answer gets a fresh, consistently numbered list.
            s['sources'] = s.get('evidence_pool') or s['sources']
        return save(s,'修正回答中的证据使用' if result['decision']=='revise' else '补查回答缺少的证据')

    graph = StateGraph(AgentState)
    def durable_node(name, node):
        def run(s):
            nonlocal usage
            check()
            if s.get('platform_state') is not None:
                tools.restore(s['platform_state'])
            usage = dict(s.get('usage') or {"prompt_tokens": 0, "completion_tokens": 0,
                "total_tokens": 0, "calls": 0,
                "scope": "tool_decisions_and_reviews"})
            # 回执按节点和轮次隔离；深拷贝后执行，节点成功返回才交付新的图状态。
            scope = f"{name}:{s.get('rounds',0)}" if name in ('decide','execute') else name
            scope += f":review-{s.get('review_rounds',0)}"
            with receipts.scope(scope):
                result = node(deepcopy(s))
            result['usage'] = dict(usage)
            return result
        return run
    for name, node in (("plan", plan_node), ("decide", decide_node),
                       ("execute", execute_node), ("answer", answer_node), ('review',review_node)):
        graph.add_node(name, durable_node(name, node))
    for a, b in ((START, 'plan'), ('plan', 'decide'), ('execute', 'decide'), ('answer','review')):
        graph.add_edge(a, b)
    graph.add_conditional_edges('decide', lambda s: 'answer' if s['action']['tool']=='finish' else 'execute')
    graph.add_conditional_edges('review',lambda s:s['review_route'],{'end':END,'answer':'answer','decide':'decide'})
    # Never upload local conversations via environment-enabled LangSmith tracing.
    # 显式关闭外部追踪，避免本地聊天内容随环境配置上传。
    with tracing_context(enabled=False):
        compiled = graph.compile(checkpointer=saver)
        config = {'configurable': {'thread_id': 'qa'}, 'recursion_limit': 40}
        if resume and not compiled.get_state(config).values:
            # The first input checkpoint can be empty if planning was interrupted.
            if not saver.get_tuple(config):
                raise ValueError('没有可恢复的问答检查点')
        # 恢复用 None 续跑已存图状态；同步落盘配合调用回执，避免重放已完成请求。
        result = compiled.invoke(None if resume else {}, config, durability='sync')
    return result["answer_data"], result["sources"], diagnostics(result)
