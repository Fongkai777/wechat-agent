"""Isolate context expansion on previously selected synthetic evidence.

This is an answer/context diagnostic, not a full Agent retrieval evaluation.
No gold facts are sent to the model; earlier generated history is preserved.
"""
import argparse
from datetime import datetime
import json
from pathlib import Path
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from wechat_agent import demo, web
from wechat_agent.qa_tools import ConversationTools
from wechat_agent.evidence_context import merge_evidence, select_evidence
from wechat_agent.qa_review import review_answer
from scripts.evaluate_showcase import load_cases
from scripts.evaluate_retrieval import UsageRecorder


def replay(config_path, input_path, output, case_id):
    cases = load_cases()
    case = next(c for c in cases['cases'] if c['id']==case_id)
    previous = json.loads(input_path.read_text(encoding='utf-8'))
    if previous['anchor'] != cases['anchor']:
        raise ValueError('Replay clock differs from the synthetic corpus')
    original = next(r for r in previous['results'] if r['id']==case_id)
    profile = web.load_llm_config(config_path)['qa']
    key = web.resolve_llm_api_key(profile)
    if not key:
        raise ValueError('Missing Q&A credentials')
    output.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='wechat-context-replay-') as directory, UsageRecorder(output) as usage:
        root = Path(directory)
        demo.initialize(root,anchor=cases['anchor'])
        for phase in ('initial','updates','replies'):
            if phase!='initial': demo.append_phase(root,phase)
            if phase==case['phase']: break
        state = demo.demo_state(root)
        web.update_qa_search_db_incremental(state)
        tools = ConversationTools(state,None,web.qa_item_from_search_row,lambda:None)
        now = datetime.fromisoformat(cases['anchor'])
        plan = {'since_ts':0,'until_ts':int(now.timestamp())}
        seeds = []
        with tools.connection() as conn:
            for source in original['sources']:
                if source.get('evidence_role') != 'primary': continue
                row = conn.execute('SELECT * FROM messages WHERE chat_id=? AND local_id=?',
                                   (source['chat_id'],source['local_id'])).fetchone()
                if row is None or tools.item(row)['evidence_id'] != source['evidence_id']:
                    raise ValueError('Replay source does not belong to the synthetic corpus')
                seeds.append(tools.item(row,plan=plan))
        surrounding = tools.surrounding(plan,seeds,max(1,len(seeds)*24))
        for seed in seeds:
            seed['context_ids'] = surrounding['context_links'].get(seed['evidence_id'],[])
        sources, dropped = select_evidence(merge_evidence(seeds,surrounding['items']),limit=60,char_limit=60000)
        coverage = {'exhaustive':False,'included':len(sources),'context_incomplete':dropped}
        usage.stage = case_id+':replay_answer'
        started = time.perf_counter()
        answer = web.call_qa_answer(profile,key,case['question'],sources,original['history'])
        answer_seconds = time.perf_counter()-started
        usage.stage = case_id+':replay_review'
        review = review_answer(lambda messages,schema:web.call_chat_payload(profile,key,messages,response_format=schema),
            case['question'],original['history'],answer,sources,[],coverage,now)
        result = {'mode':'fixed_selected_evidence_context_replay','case':case_id,'model':profile['model'],
                  'sources':sources,'answer':answer,'review':review,'answer_seconds':round(answer_seconds,3),
                  'elapsed_seconds':round(time.perf_counter()-started,3),'coverage':coverage,
                  'limitations':['Not a full Agent run: selected anchors and conversation history come from the prior run.',
                                 'Review is model-generated, not an independent correctness verdict.']}
        (output/'results.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        print(f'{case_id}: sources={len(sources)}, review={review["decision"]}, elapsed={result["elapsed_seconds"]}s')


if __name__=='__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live-config',type=Path,required=True)
    parser.add_argument('--input',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--case',required=True)
    args = parser.parse_args()
    try:
        replay(args.live_config,args.input,args.output,args.case)
    except Exception as exc:
        print('Replay failed: '+type(exc).__name__,file=sys.stderr)
        sys.exit(1)
