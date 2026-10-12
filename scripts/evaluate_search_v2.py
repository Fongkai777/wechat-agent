"""Paired keyword / hybrid / native Agent benchmark on 50 fictional questions.

Offline runs measure keyword recall only. --live-config explicitly enables paid
embedding, reranking, generation and judging; no private corpus is ever loaded.
"""
from __future__ import annotations

import argparse
import copy
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import statistics
import sys
import tempfile
import time
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from wechat_agent import demo, web
from wechat_agent.qa_agent import run_conversation_agent
from wechat_agent.qa_tools import ConversationTools
from scripts.evaluate_retrieval import UsageRecorder
from scripts.evaluate_answers import judge_answer, summarize, estimate_cost

ROOT = Path(__file__).resolve().parents[1]


def dataset():
    corpus = demo.fixture()
    extension = json.loads((ROOT / 'eval/retrieval_v2.json').read_text())
    corpus['contacts'].update(extension['contacts'])
    corpus['messages'].extend(extension['messages'])
    noise = extension['distractors']
    for i in range(noise['count']):
        corpus['messages'].append([noise['start_id'] + i, noise['chat_id'], 'demo_yu',
                                  '2026-09-22 17:%02d:00' % i, noise['text'].format(i=i+1)])
    cases = json.loads((ROOT / 'eval/cases.json').read_text()) + extension['cases']
    ids = [row[0] for row in corpus['messages'] + corpus['increment']]
    if len(ids) != len(set(ids)) or len({c['id'] for c in cases}) != len(cases):
        raise ValueError('Duplicate fixture IDs')
    for case in cases:
        if not set(case['evidence']) <= set(ids):
            raise ValueError('Unknown gold message: ' + case['id'])
    return corpus, cases, datetime.fromisoformat(extension['now'])


def keyword(state, query, size, until):
    with sqlite3.connect(state.qa_search_db) as conn:
        conn.row_factory = sqlite3.Row
        # Both branches use the same raw query. No gold labels or generated synonyms.
        branches = [web.query_qa_search_fts(conn, query, [], None, size * 4, until),
                    web.query_qa_search_like(conn, query, [], None, size * 4, until)]
        pool, scores = {}, {}
        for rows in branches:
            for rank, row in enumerate(rows, 1):
                pool[row['id']] = row
                scores[row['id']] = scores.get(row['id'], 0) + 1 / (60 + rank)
        order = sorted(pool, key=lambda mid: (scores[mid], pool[mid]['timestamp']), reverse=True)
        return [web.qa_item_from_search_row(pool[mid]) for mid in order[:size]]


def write_report(output, cases, results, calls, config, corpus, live, top_k):
    summaries = {}
    for mode in ('keyword', 'hybrid', 'agent'):
        rows = [r for r in results if r['mode'] == mode]
        if not rows:
            summaries[mode] = {'status': 'not_run'}
            continue
        positive = [r for r in rows if r['expected_message_ids']]
        owned = [c for c in calls if c['stage'].startswith(mode + ':')]
        summaries[mode] = {**summarize(rows, owned), 'status': 'measured',
            'recall_mean': sum(r['evidence_recall'] for r in positive) / len(positive) if positive else None,
            'all_evidence_hits': sum(r['all_evidence_found'] for r in positive),
            'answerable_cases': len(positive),
            'retrieval_failures': sum(bool(r.get('error_type')) for r in rows),
            'retrieval_only': not live,
            'answer_cost': estimate_cost([c for c in owned if not c['stage'].endswith(':judge')]),
            'judge_cost': estimate_cost([c for c in owned if c['stage'].endswith(':judge')])}
        summaries[mode]['latency_s']['definition'] = 'Retrieval plus answer generation, excluding judging; offline runs measure retrieval only.'
        durations = sorted(r['latency_s'] for r in rows)
        summaries[mode]['all_attempt_latency_s'] = {'p50':statistics.median(durations),'max':max(durations)}
        if not live:
            summaries[mode]['latency_s'].update(p50=statistics.median(durations),max=max(durations))
        if not live:
            # No generation was attempted; absence must not be counted as failure.
            summaries[mode].update(generation_failures=0, answer_status='not_run')
    report = {'generated_at': datetime.now(timezone.utc).isoformat(), 'selected_cases': len(cases),
              'fixture_messages': len(corpus['messages']) + len(corpus['increment']),
              'top_k_raw_messages': top_k, 'summaries': summaries, 'results': results,
              'models': {k: v.get('model') for k,v in config.items()} if live else {},
              'index_cost': estimate_cost([c for c in calls if c['stage'] == 'index']),
              'limits': [
                  'Authored synthetic diagnostic set, not a representative or held-out production benchmark.',
                  'Keyword: FTS/LIKE reciprocal-rank fusion, latest query only; hybrid: single query with production query expansion; Agent: production tool-first multi-step retrieval. No gold scopes injected.',
                  'Same final raw-message limit, but Agent can make more tool calls; tokens and cost expose this tradeoff. Not an equal-token-budget experiment.',
                  'Recall counts final answer context, not all intermediate observations. No-answer cases are excluded from recall.',
                  'Quality grades are model judgments, not human validation; same-model judge bias is possible. Manual review fields remain empty.',
                  'Cost uses the explicitly dated price catalog from evaluate_answers; unknown models or missing usage yield null totals, never zero.',
                  'Offline mode does not measure answer quality. Failed cases are retained with zero recall rather than dropped.']}
    (output/'results.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    lines = ['# Search Evaluation v2', '', f"{report['fixture_messages']} fictional messages; {len(cases)} selected questions; final context cap {top_k} raw messages.", '',
             '| Mode | Cases | Mean recall | All evidence | Reviewed answers |', '|---|---:|---:|---:|---:|']
    for mode, summary in summaries.items():
        if summary['status'] == 'not_run':
            lines.append(f'| {mode} | not run | - | - | - |')
        else:
            recall = summary['recall_mean']
            lines.append(f"| {mode} | {summary['attempted']} | {recall:.1%} | {summary['all_evidence_hits']}/{summary['answerable_cases']} | {summary['reviewed']} |")
    lines += ['', '## Misses', '']
    for row in results:
        missing = sorted(set(row['expected_message_ids']) - set(row['retrieved_message_ids']))
        if missing or row.get('error_type'):
            lines.append(f"- {row['mode']} {row['id']}: missing {missing}; error={row.get('error_type','none')}.")
    lines += ['', '## Limits', ''] + ['- '+v for v in report['limits']]
    (output/'REPORT.md').write_text('\n'.join(lines)+'\n')
    return report


def evaluate(args):
    corpus, cases, now = dataset()
    if args.cases:
        selected = set(args.cases.split(','))
        if selected - {c['id'] for c in cases}: raise ValueError('Unknown case IDs')
        cases = [c for c in cases if c['id'] in selected]
    modes = args.modes.split(',')
    if set(modes) - {'keyword','hybrid','agent'}: raise ValueError('Unknown mode')
    if not args.live_config and modes != ['keyword']:
        raise ValueError('Hybrid and Agent require explicit --live-config; offline substitutes are not equivalent')
    live = bool(args.live_config)
    config = web.load_llm_config(args.live_config) if live else {}
    if live and (not config['embedding'].get('enabled') or not config['rerank'].get('enabled')):
        raise ValueError('Live comparison requires enabled embedding and rerank profiles')
    profile = config.get('qa', {})
    key = web.resolve_llm_api_key(profile) if live else None
    if live and not key: raise ValueError('Missing QA credentials')
    judge = dict(profile, model=args.judge_model or profile.get('model'))
    args.output.mkdir(parents=True, exist_ok=True)
    results = []
    with tempfile.TemporaryDirectory(prefix='wechat-search-v2-') as directory, UsageRecorder(args.output) as usage:
        root = Path(directory)
        with patch.object(demo, 'fixture', return_value=copy.deepcopy(corpus)):
            demo.initialize(root)
        demo.import_messages(root, corpus['increment'])
        state = demo.demo_state(root)
        if live: state.llm_config = args.live_config.resolve()
        web.update_qa_search_db_incremental(state)
        if live: web.build_or_update_qa_semantic_index(state)
        index = web.load_or_build_qa_index(state)
        def hybrid(query, people, since, until, size, scope=None):
            plan = ({'queries':[{'text':query}]} if (scope or {}).get('tool_first')
                    else web.build_qa_search_plan(index, query, people, since))
            plan.update(scope or {})
            plan.update(verified_people=True, time_until=until)
            return web.search_qa_search_db(state, query, people, since, size, plan)
        tools = ConversationTools(state, hybrid, web.qa_item_from_search_row, lambda: None)
        for case in cases:
            for mode in modes:
                usage.stage = mode + ':' + case['id'] + ':answer'
                start = time.perf_counter()
                row = {'id':case['id'], 'mode':mode, 'question':case['question'], 'category':case['category'],
                       'expected_message_ids':case['evidence'], 'retrieved_message_ids':[],
                       'manual_review': {'correctness':None,'citation_support':None,'notes':''}}
                try:
                    if mode == 'keyword':
                        sources = keyword(state, case['question'], args.top_k, int(now.timestamp()))
                    elif mode == 'hybrid':
                        plan = {'query':case['question'], 'people':[], 'since_ts':None,
                                'until_ts':int(now.timestamp()), 'chat_scope':{'type':'all','keyword':''}}
                        found = tools.search(plan,size=args.top_k)
                        sources = found['items']
                        row['retrieval'] = found['diagnostics']
                        if (found['diagnostics'].get('semantic') or {}).get('error') or found['diagnostics'].get('rerank_error'):
                            raise RuntimeError('Hybrid retrieval degraded; not counted as a full hybrid run')
                    else:
                        answer, sources, trace = run_conversation_agent(
                            case['question'], case.get('history',[]), index.get('people_list') or [], tools,
                            lambda messages,schema=None,**opts:web.call_chat_payload(profile,key,messages,response_format=schema,**opts),
                            lambda q,s,h:web.call_qa_answer(profile,key,q,s,h), lambda *a,**k:None,
                            lambda trace,sources:row.update(retrieval=trace), lambda:None, limit=args.top_k, now=now)
                        row.update(answer=answer,retrieval=trace)
                    row['sources'] = sources
                    row['retrieved_message_ids'] = list(dict.fromkeys(int(s['local_id']) for s in sources if s.get('local_id')))
                    row['context_chars'] = sum(len(s.get('text','')) for s in sources)
                    if live and mode != 'agent':
                        row['answer'] = web.call_qa_answer(profile,key,case['question'],sources,case.get('history'))
                except Exception as exc:
                    # Never serialize credential-bearing request/config exceptions.
                    row['error_type'] = type(exc).__name__
                row['latency_s'] = round(time.perf_counter()-start,3)
                if row.get('answer'):
                    usage.stage = mode + ':' + case['id'] + ':judge'
                    try:
                        row['review'] = judge_answer(case,row['answer'],row['sources'],judge,key,corpus=corpus)
                    except Exception as exc:
                        row['judge_error_type'] = type(exc).__name__
                expected = set(case['evidence'])
                row['evidence_recall'] = len(expected & set(row['retrieved_message_ids']))/len(expected) if expected else None
                row['all_evidence_found'] = expected <= set(row['retrieved_message_ids']) if expected else None
                results.append(row)
                report = write_report(args.output,cases,results,usage.calls,config,corpus,live,args.top_k)
                print(f"{mode} {case['id']}: recall={row['evidence_recall']} answer={bool(row.get('answer'))} latency={row['latency_s']}s",flush=True)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live-config',type=Path)
    parser.add_argument('--cases',default='')
    parser.add_argument('--modes',default='keyword')
    parser.add_argument('--top-k',type=int,default=20)
    parser.add_argument('--judge-model',default='')
    parser.add_argument('--output',type=Path,default=Path('artifacts/search-v2'))
    args = parser.parse_args()
    if not 8 <= args.top_k <= 160: parser.error('top-k must be 8..160')
    report = evaluate(args)
    return int(any(r.get('error_type') or r.get('judge_error_type') for r in report['results']))


if __name__ == '__main__':
    raise SystemExit(main())
