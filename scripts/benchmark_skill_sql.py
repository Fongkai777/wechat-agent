"""Measure repeated Skill SQL materialization vs an experimental read-only snapshot.

Synthetic only. The reused connection is a benchmark prototype, not a replacement
for the production capability boundary. No model, private data or network is used.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sqlite3
import statistics
import sys
import time

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from wechat_agent.code_skill_api import CodeAPI, COLUMNS


def benchmark(count, repetitions):
    rows=[dict(id=i,chat_id='chat-'+str(i%100),chat_title='Synthetic',chat_type='private',
               timestamp=i,time=str(i),sender='Synthetic',sender_username='sender',text='internship '+str(i),
               type='text',source_db='synthetic',source_table='messages',local_id=str(i),server_id=str(i),
               person_id='sender',person_name='Synthetic') for i in range(count)]
    package={'files':[{'path':'manifest.json','content':json.dumps({
        'api_version':'1','entrypoint':'workflow.py:run','permissions':['index.query'],
        'timeout_seconds':10,'max_api_calls':100,'max_llm_calls':0,'max_embedding_calls':0})}]}
    api=CodeAPI(package,{'since':0,'until':count},lambda:[],lambda *a:[],lambda:rows,
                lambda *a:None,lambda *a:[],lambda *a:None)
    sql='SELECT chat_id,count(*) AS n,max(timestamp) AS last_ts FROM messages WHERE timestamp >= ? GROUP BY chat_id'
    cold=[]
    for _ in range(repetitions):
        start=time.perf_counter()
        expected=api.api_query(sql,[count//2])
        cold.append(time.perf_counter()-start)
    conn=sqlite3.connect(':memory:')
    conn.row_factory=sqlite3.Row
    try:
        start=time.perf_counter()
        conn.execute('CREATE TABLE messages ('+','.join(c+' '+('INTEGER' if c in ('id','timestamp') else 'TEXT') for c in COLUMNS)+')')
        conn.executemany('INSERT INTO messages VALUES('+','.join('?' for _ in COLUMNS)+')',
                         [[r.get(c) for c in COLUMNS] for r in rows])
        conn.commit()
        conn.execute('PRAGMA query_only=ON')
        setup=time.perf_counter()-start
        warm=[]
        for _ in range(repetitions):
            start=time.perf_counter()
            result=[dict(r) for r in conn.execute(sql,[count//2])]
            warm.append(time.perf_counter()-start)
            if result!=expected: raise AssertionError('Snapshot changed SQL results')
    finally:
        conn.close()
    return {'messages':count,'queries':repetitions,'current_query_seconds':cold,
            'current_total_s':sum(cold),'current_median_s':statistics.median(cold),
            'prototype_setup_s':setup,'prototype_query_seconds':warm,
            'prototype_total_including_setup_s':setup+sum(warm),
            'prototype_speedup':sum(cold)/(setup+sum(warm)),
            'results_equal':True}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sizes',default='10000,50000,100000')
    parser.add_argument('--queries',type=int,default=5)
    parser.add_argument('--output',type=Path,default=Path('artifacts/skill-sql-benchmark.json'))
    args=parser.parse_args()
    sizes=[int(v) for v in args.sizes.split(',')]
    if not 1<=args.queries<=100 or any(not 1<=n<=300000 for n in sizes): parser.error('Invalid benchmark size')
    report={'method':'Single process; 100 chats; identical aggregate query repeated; no SQL indexes; current CodeAPI vs prototype reuse.',
            'limits':['Prototype excludes production authorization, provenance bookkeeping and restart lifecycle; speedup is not a production claim.',
                      'One local run, no cold-disk or concurrent workload coverage; memory retained by a reused snapshot is not measured.'],
            'results':[benchmark(n,args.queries) for n in sizes]}
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report,indent=2))


if __name__=='__main__': main()
