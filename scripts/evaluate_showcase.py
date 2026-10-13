"""Exercise the real Q&A agent against staged, entirely fictional conversations.

Requires explicit --live-config because embedding/reranking/answers cost money.
Only the synthetic corpus is indexed; no production history or settings are saved.
"""
from __future__ import annotations

import argparse
from datetime import datetime
import json
from pathlib import Path
import sys
import tempfile
import threading
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from wechat_agent import demo, web
from wechat_agent.qa_agent import run_conversation_agent
from wechat_agent.qa_recovery import AGENT_VERSION
from wechat_agent.platform_runtime import create_platform_runtime, PlatformToolSession, QA_LIMITS
from scripts.evaluate_retrieval import UsageRecorder, evidence_ids
from scripts.evaluate_answers import judge_answer
from scripts.evaluate_search_v2 import keyword, final_context, EVALUATION_CHAR_BUDGET
from wechat_agent.qa_tools import ConversationTools
from wechat_agent.evidence_context import select_evidence

CASES = Path(__file__).resolve().parents[1] / 'eval/showcase_cases.json'


def load_cases():
    return json.loads(CASES.read_text(encoding='utf-8'))


def conversation_history(case, completed):
    previous = case.get('history_from')
    if not previous:
        return []
    row = completed[previous]
    if not row.get('answer'):
        raise ValueError('Previous conversation turn failed')
    return row['history'] + [
        {'role':'user', 'content':row['question']},
        {'role':'assistant', 'content':web.qa_answer_text(row['answer'])}]


def evaluate_offline(output, top_k=60):
    """Latest-query keyword hits plus production context; no model or gold routing."""
    data = load_cases()
    now = datetime.fromisoformat(data['anchor'])
    results = []
    with tempfile.TemporaryDirectory(prefix='wechat-showcase-offline-') as directory:
        root = Path(directory)
        demo.initialize(root, anchor=data['anchor'])
        state = demo.demo_state(root)
        for phase in demo.workspace_manifest(root)['corpus']['phases']:
            if phase['name'] != 'initial':
                demo.append_phase(root,phase['name'])
                demo.refresh_workspace(state)
            web.update_qa_search_db_incremental(state)
            tools = ConversationTools(state,
                lambda query,people,since,until,size,scope=None:(keyword(state,query,size,until),{}),
                web.qa_item_from_search_row,lambda:None)
            for case in (c for c in data['cases'] if c['phase']==phase['name']):
                started = time.perf_counter()
                found = tools.search({'query':case['question'],'people':[],
                    'since_ts':0,'until_ts':int(now.timestamp())},size=top_k)
                sources, trimmed = final_context(found['items'],top_k)
                retrieved = {int(s['local_id']) for s in sources if s.get('local_id')}
                expected = set(case['evidence'])
                expanded_ids = {int(s['local_id']) for s in found['items'] if s.get('local_id')}
                uncapped, _ = select_evidence(found['items'],char_limit=EVALUATION_CHAR_BUDGET)
                uncapped_ids = {int(s['local_id']) for s in uncapped if s.get('local_id')}
                row = {'id':case['id'],'question':case['question'],
                    'expected_message_ids':sorted(expected),'retrieved_message_ids':sorted(retrieved),
                    'missing_message_ids':sorted(expected-retrieved),
                    'missing_before_budget':sorted(expected-expanded_ids),
                    'lost_to_budget':sorted((expected & expanded_ids)-retrieved),
                    'without_row_cap':{'final_context_count':len(uncapped),
                        'missing_message_ids':sorted(expected-uncapped_ids)},
                    'all_evidence_found':expected <= retrieved,
                    'evidence_recall':len(expected & retrieved)/len(expected),
                    'pre_budget_count':len(found['items']),'final_context_count':len(sources),
                    'context_chars':sum(len(s.get('text','')) for s in sources),
                    'context_trimmed':trimmed,'sources':sources,
                    'latency_s':round(time.perf_counter()-started,3)}
                results.append(row)
                print(f"{case['id']}: recall={row['evidence_recall']} missing={row['missing_message_ids']} rows={len(sources)}",flush=True)
    report = {'anchor':data['anchor'],'mode':'keyword_with_context','top_k_raw_messages':top_k,
        'all_evidence_hits':sum(r['all_evidence_found'] for r in results),
        'cases':len(results),'results':results,'api_calls':0,
        'limits':['Authored diagnostic set, not held-out answer accuracy.',
                  'Latest question only; no rewritten query, answer or judge model.',
                  'Post-expansion row cap; complete windows only. No gold evidence enters retrieval.',
                  'S04 labels evidence of unavailable salary, not a known salary answer.']}
    output.mkdir(parents=True,exist_ok=True)
    (output/'results.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    return report


def evaluate(config_path, output, judge=False, case_ids=None):
    data = load_cases()
    if case_ids:
        by_id = {c['id']:c for c in data['cases']}
        selected = set(case_ids)
        if selected - set(by_id):
            raise ValueError('Unknown case IDs')
        for case_id in list(selected):
            while by_id[case_id].get('history_from'):
                case_id = by_id[case_id]['history_from']
                selected.add(case_id)
        data['cases'] = [c for c in data['cases'] if c['id'] in selected]
    now = datetime.fromisoformat(data['anchor'])
    config_path = config_path.resolve()
    config = web.load_llm_config(config_path)
    profile = config['qa']
    key = web.resolve_llm_api_key(profile)
    if not key:
        raise ValueError('Missing Q&A credentials')
    output.mkdir(parents=True, exist_ok=True)
    report = {'anchor':data['anchor'], 'agent_version':AGENT_VERSION, 'results':[], 'index_updates':[],
              'models':{k:v.get('model') for k,v in config.items() if isinstance(v,dict)},
              'limits':[
                  'Small authored diagnostic set, not a held-out quality benchmark.',
                  'Evidence recall covers final answer context, not all tool observations.',
                  'Structural citation validation does not prove that a claim is supported.',
                  'Optional grades are same-model judgments, not independent human scores.',
                  'Question latency includes retrieval and answering, excluding index building and judging.']}
    completed = {}
    with tempfile.TemporaryDirectory(prefix='wechat-showcase-eval-') as directory, UsageRecorder(output) as usage:
        root = Path(directory)
        demo.initialize(root, anchor=data['anchor'])
        state = demo.demo_state(root)
        state.llm_config = config_path
        corpus = demo.workspace_manifest(root)['corpus']
        visible = []
        for phase in corpus['phases']:
            if not any(c['id'] not in completed for c in data['cases']):
                break
            if phase['name'] != 'initial':
                demo.append_phase(root, phase['name'])
                demo.refresh_workspace(state)
            visible.extend(phase['messages'])
            usage.stage = phase['name'] + ':index'
            indexed = web.update_qa_search_db_incremental(state)
            try:
                web.build_or_update_qa_semantic_index(state)
            except Exception as exc:
                report['setup_error'] = {'phase':phase['name'], 'type':type(exc).__name__,
                                         'cause':type(exc.__cause__ or exc).__name__}
                report['api_calls'] = usage.calls
                (output/'results.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
                print('Index preparation failed; see sanitized results.json and api_usage.json.',flush=True)
                return report
            report['index_updates'].append({'phase':phase['name'],
                'mode':indexed.get('update_mode'), 'inserted':indexed.get('inserted'),
                'messages':indexed['message_count']})
            index = web.load_or_build_qa_index(state)
            for case in (c for c in data['cases'] if c['phase'] == phase['name']):
                row = {**case, 'sources':[], 'history':[], 'manual_review':None}
                usage.stage = case['id'] + ':answer'
                started = time.perf_counter()
                runtime = None
                try:
                    row['history'] = conversation_history(case, completed)
                    runtime = create_platform_runtime(state,
                        {'task':case['question'],'since':0,'until':int(now.timestamp()),
                         'account':state.account,'dry_run':False},
                        QA_LIMITS, config, 'qa', lambda:None, threading.Lock())
                    def complete(messages,schema=None,**options):
                        name = (schema or {}).get('json_schema',{}).get('name')
                        usage.stage = case['id'] + (':check' if name=='qa_evidence_review' else ':answer')
                        return runtime.model.complete(messages,response_format=schema,**options)
                    def checkpoint(trace,sources):
                        row['trace'] = trace
                        (output/'progress.json').write_text(json.dumps({
                            'case':case['id'],'trace':trace,'source_count':len(sources)},
                            ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
                    answer, sources, trace = run_conversation_agent(
                        case['question'],row['history'],index.get('people_list') or [],
                        PlatformToolSession(state,runtime),
                        complete,
                        lambda q,s,h:web.call_qa_answer(profile,key,q,s,h,
                            complete=lambda messages,response_format=None,**opts:complete(messages,response_format,**opts)),
                        lambda *a,**k:None, checkpoint,
                        lambda:None, limit=60, now=now)
                    row.update(answer=answer,sources=sources,trace=trace)
                    row['citation_refs_valid'] = all(
                        isinstance(ref,int) and 1 <= ref <= len(sources)
                        for p in answer['paragraphs'] for ref in p['source_refs'])
                    row['retrieved_ids'] = sorted({mid for s in sources if s.get('local_id')
                                                  for mid in evidence_ids(state,s)})
                    expected = set(case['evidence'])
                    row['evidence_recall'] = len(expected & set(row['retrieved_ids'])) / len(expected)
                except Exception as exc:
                    # Provider exception bodies can contain credentials; record types only.
                    row['error_type'] = type(exc).__name__
                finally:
                    if runtime is not None:
                        runtime.api.close()
                row['latency_s'] = round(time.perf_counter()-started,3)
                if judge and row.get('answer'):
                    usage.stage = case['id'] + ':judge'
                    try:
                        row['review'] = judge_answer({**case,'history':row['history']},
                            row['answer'],row['sources'],profile,key,
                            corpus={'contacts':corpus['contacts'],'messages':visible,'increment':[]})
                    except Exception as exc:
                        row['judge_error_type'] = type(exc).__name__
                completed[case['id']] = row
                report['results'].append(row)
                report['api_calls'] = usage.calls
                (output/'results.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
                print(f"{case['id']} ({phase['name']}): answer={bool(row.get('answer'))} recall={row.get('evidence_recall')} {row['latency_s']}s",flush=True)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--live-config', type=Path)
    mode.add_argument('--offline', action='store_true')
    parser.add_argument('--top-k',type=int,default=60,help='Offline final raw-message cap')
    parser.add_argument('--output', type=Path, default=Path('artifacts/showcase'))
    parser.add_argument('--judge', action='store_true')
    parser.add_argument('--cases', help='Comma-separated IDs; preceding conversation turns are included')
    args = parser.parse_args()
    if args.offline:
        if args.judge or args.cases:
            parser.error('--offline runs all seven cases without a judge')
        if not 8 <= args.top_k <= 160:
            parser.error('--top-k must be 8..160')
        evaluate_offline(args.output,args.top_k)
        return 0
    result = evaluate(args.live_config,args.output,args.judge,args.cases.split(',') if args.cases else None)
    return int(bool(result.get('setup_error')) or any(
        r.get('error_type') or r.get('judge_error_type') or not r.get('citation_refs_valid')
        for r in result['results']))


if __name__ == '__main__':
    raise SystemExit(main())
