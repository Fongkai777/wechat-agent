const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

function setup(initialGoals = [], rangeUnits = ['days','weeks','months'], manualSupported = true) {
  const nodes = new Map(), documentEvents = new Map(), requests = [], intervals = [];
  let goals = initialGoals;
  let failure = '';
  let confirmed = true;
  const histories = new Map();
  let readHistory = id => histories.get(id) || [];
  const element = id => {
    if (!nodes.has(id)) nodes.set(id, {value:'',checked:false,disabled:false,hidden:false,open:false,innerHTML:'',textContent:'',events:new Map(),style:{},
      addEventListener(name,fn){this.events.set(name,fn);}, focus(){}, scrollIntoView(){}, showModal(){this.open=true;}, close(){this.open=false;},
      classList:{contains:()=>true}});
    return nodes.get(id);
  };
  const document = {hidden:false,getElementById:element,querySelectorAll:()=>[],
    addEventListener:(name,fn)=>documentEvents.set(name,fn),dispatchEvent:event=>documentEvents.get(event.type)?.(event)};
  const fetch = async (url, options={}) => {
    const data=options.body ? JSON.parse(options.body) : undefined;
    requests.push({url,data});
    if (failure) return {ok:false,json:async()=>({ok:false,error:failure})};
    if (url.startsWith('/api/goals/history?')) {
      const runs = await readHistory(new URL(url,'http://localhost').searchParams.get('id'));
      return {ok:true,json:async()=>({ok:true,runs})};
    }
    if (url==='/api/goals/save') {
      goals=[...goals.filter(g=>g.id!==data.id),{...data,id:data.id||'new',next_run_at:Date.now()/1000+3600}];
    }
    if (url==='/api/goals/toggle') goals=goals.map(g=>g.id===data.id?{...g,enabled:data.enabled}:g);
    if (url==='/api/goals/delete') goals=goals.filter(g=>g.id!==data.id);
    let runData={};
    if (url==='/api/goals/run') {
      runData={run_id:'manual-run',started_at:200,next_run_at:3800};
      goals=goals.map(g=>g.id===data.id?{...g,next_run_at:g.enabled?3800:null,latest_run:{id:'manual-run',status:'running',trigger:'manual',started_at:200,progress:'准备检索'}}:g);
    }
    if (url==='/api/goals/stop') goals=goals.map(g=>g.id===data.id?{...g,latest_run:{...g.latest_run,cancel_requested:true,progress:'停止中，等待当前请求结束'}}:g);
    return {ok:true,json:async()=>({ok:true,goals,scheduler_running:true,runs:[],range_units:rangeUnits,interval_units:['hours','days'],manual_run_supported:manualSupported,...runData})};
  };
  vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../web/goals.js'),'utf8'),{
    document,fetch,setInterval:fn=>intervals.push(fn),Date,console,confirm:()=>confirmed,CustomEvent:class {constructor(type,init){this.type=type;this.detail=init.detail;}}
  });
  return {element,requests,document,intervals,histories,get goals(){return goals;},fail(message){failure=message;},confirm(value){confirmed=value;},onHistory(fn){readHistory=fn;},
    async load(){documentEvents.get('viewchange')({detail:'goals'});await new Promise(resolve=>setImmediate(resolve));},
    async emit(id,name,event={}) {await element(id).events.get(name)({preventDefault(){},...event});},
  };
}

test('opening and polling goal tab only reads, never triggers execution',async()=>{
  const app=setup();await app.load();
  app.intervals[0]();await new Promise(resolve=>setImmediate(resolve));
  assert.ok(app.requests.every(r=>r.url==='/api/goals'&&!r.data));
  assert.match(app.element('goalCards').innerHTML,/暂无任务/);
});

test('saving a periodic goal includes range and has no manual run endpoint',async()=>{
  const app=setup();await app.load();
  await app.emit('goalNewBtn','click');
  assert.equal(app.element('goalEditor').hidden,false);
  assert.equal(app.element('goalEditor').open,false);
  app.element('goalPrompt').value='  帮我查看有无新增的实习信息  ';
  app.element('goalIntervalUnit').value='days';
  await app.emit('goalForm','submit');
  assert.equal(app.goals[0].interval_value,1);
  assert.equal(app.goals[0].interval_unit,'days');
  assert.equal(app.goals[0].enabled,true);
  assert.equal(app.goals[0].range_type,'recent_days');
  assert.equal(app.goals[0].range_value,1);
  assert.equal(app.goals[0].range_unit,'days');
  assert.equal(app.goals[0].title,'帮我查看有无新增的实习信息');
  assert.equal(app.goals[0].prompt,'帮我查看有无新增的实习信息');
  assert.equal(app.element('goalEditor').hidden,true);
  assert.ok(!app.requests.some(r=>/run|trigger/.test(r.url)));
});

test('cards escape goal content and model output',async()=>{
  const app=setup([{id:'one',title:'<script>alert(1)</script>',prompt:'<img src=x onerror=x>',cadence:'hourly',enabled:true,latest_run:{status:'completed',result:'<svg onload=x>',started_at:100}}]);
  await app.load();
  const html=app.element('goalCards').innerHTML;
  assert.ok(!html.includes('<script>'));
  assert.ok(!html.includes('<svg onload'));
  assert.match(html,/&lt;svg onload=x&gt;/);
});

test('save failure leaves draft intact and enables retry',async()=>{
  const app=setup();await app.load();await app.emit('goalNewBtn','click');
  app.element('goalPrompt').value='Draft';
  app.fail('network failed');await app.emit('goalForm','submit');
  assert.equal(app.element('goalEditor').hidden,false);
  assert.equal(app.element('goalPrompt').value,'Draft');
  assert.equal(app.element('goalSaveBtn').disabled,false);
  assert.equal(app.element('goalFormError').textContent,'network failed');
});

test('old running backend cannot silently discard the selected range',async()=>{
  const legacy=setup([],null);await legacy.load();await legacy.emit('goalNewBtn','click');
  legacy.element('goalPrompt').value='Goal';
  await legacy.emit('goalForm','submit');
  assert.match(legacy.element('goalFormError').textContent,/重启/);
  assert.ok(!legacy.requests.some(r=>r.data));
  assert.equal(legacy.element('goalPrompt').value,'Goal');
});

test('editing a paused goal does not silently enable it',async()=>{
  const app=setup([{id:'one',title:'Old title',prompt:'Old goal',cadence:'hourly',enabled:false}]);await app.load();
  await app.emit('goalsGrid','click',{target:{closest:selector=>selector==='[data-edit]'?{dataset:{edit:'one'}}:null}});
  app.element('goalPrompt').value='Updated goal';
  await app.emit('goalForm','submit');
  assert.equal(app.goals[0].enabled,false);
  assert.equal(app.goals[0].title,'Updated goal');
});

test('long and multiline goals produce a bounded title without losing full content',async()=>{
  const app=setup();await app.load();await app.emit('goalNewBtn','click');
  const prompt='检查消息\n'+'任务'.repeat(100);
  app.element('goalPrompt').value=prompt;
  await app.emit('goalForm','submit');
  assert.equal(app.goals[0].prompt,prompt);
  assert.equal(Array.from(app.goals[0].title).length,41);
  assert.ok(!app.goals[0].title.includes('\n'));
});

test('editor has matching numeric period and range rows without today option',()=>{
  const html=fs.readFileSync(path.join(__dirname,'../web/index.html'),'utf8');
  const form=html.match(/<form id="goalForm">([\s\S]*?)<\/form>/)[1];
  assert.equal((form.match(/<(?:input|textarea|select)\b/g)||[]).length,5);
  assert.match(form,/>任务<textarea/);
  assert.match(form,/for="goalIntervalValue">检索周期/);
  assert.match(form,/for="goalRangeValue">检索范围/);
  assert.match(form,/id="goalRangeValue"[^>]*min="1" max="90"/);
  assert.match(form,/<span>最近<\/span>/);
  assert.ok(!form.includes('当日'));
  assert.ok(!form.includes('goalRangeType'));
  assert.match(form,/<select id="goalRangeUnit"/);
  assert.ok(!form.includes('goalTemplate'));
  assert.ok(!form.includes('goalName'));
  assert.ok(!form.includes('goalEnabled'));
  assert.ok(!form.includes('goalDeleteBtn'));
  assert.match(html,/<article id="goalEditor"/);
  assert.ok(!html.includes('<dialog id="goalEditor"'));
});

test('recent range is always editable, persists, and restores while editing',async()=>{
  const app=setup();await app.load();await app.emit('goalNewBtn','click');
  assert.equal(app.element('goalRangeValue').disabled,false);
  app.element('goalPrompt').value='Check internships';
  app.element('goalRangeValue').value='3';
  await app.emit('goalForm','submit');
  assert.equal(app.goals[0].range_type,'recent_days');
  assert.equal(app.goals[0].range_value,3);
  assert.match(app.element('goalCards').innerHTML,/最近 3 天/);
  await app.emit('goalsGrid','click',{target:{closest:s=>s==='[data-edit]'?{dataset:{edit:'new'}}:null}});
  assert.equal(Number(app.element('goalRangeValue').value),3);
  await app.load();
  assert.equal(Number(app.element('goalRangeValue').value),3);
});

test('unit selector persists weeks and changes the numeric limit',async()=>{
  const app=setup();await app.load();await app.emit('goalNewBtn','click');
  app.element('goalPrompt').value='Goal';
  app.element('goalRangeValue').value='2';
  app.element('goalRangeUnit').value='weeks';await app.emit('goalRangeUnit','change');
  assert.equal(app.element('goalRangeValue').max,12);
  await app.emit('goalForm','submit');
  assert.equal(app.goals[0].range_value,2);
  assert.equal(app.goals[0].range_unit,'weeks');
  assert.match(app.element('goalCards').innerHTML,/最近 2 周/);
  await app.emit('goalsGrid','click',{target:{closest:s=>s==='[data-edit]'?{dataset:{edit:'new'}}:null}});
  assert.equal(app.element('goalRangeUnit').value,'weeks');
  app.element('goalRangeUnit').value='months';await app.emit('goalRangeUnit','change');
  assert.equal(app.element('goalRangeValue').max,3);
});

test('invalid days cannot save and valid input enables retry',async()=>{
  const app=setup();await app.load();await app.emit('goalNewBtn','click');
  app.element('goalPrompt').value='Goal';
  for(const value of ['', '0', '-2', '91', '1.5']) {
    app.element('goalRangeValue').value=value;
    await app.emit('goalForm','submit');
    assert.equal(app.element('goalEditor').hidden,false);
    assert.equal(app.element('goalRangeValue').disabled,false);
    assert.match(app.element('goalFormError').textContent,/1 到 90/);
  }
  assert.ok(!app.requests.some(r=>r.data));
  app.element('goalRangeValue').value='1';
  await app.emit('goalForm','submit');
  assert.equal(app.goals[0].range_type,'recent_days');
  assert.equal(app.goals[0].range_value,1);
  assert.equal(app.element('goalRangeUnit').disabled,false);
});

test('custom periods save half-day and weekly intervals and restore on edit',async()=>{
  for (const [value,unit,label] of [[12,'hours','每 12 小时'],[7,'days','每 7 天']]) {
    const app=setup();await app.load();await app.emit('goalNewBtn','click');
    app.element('goalPrompt').value='Goal';
    app.element('goalIntervalValue').value=String(value);
    app.element('goalIntervalUnit').value=unit;
    await app.emit('goalForm','submit');
    assert.equal(app.goals[0].interval_value,value);
    assert.equal(app.goals[0].interval_unit,unit);
    assert.ok(app.element('goalCards').innerHTML.includes(label));
    await app.emit('goalsGrid','click',{target:{closest:s=>s==='[data-edit]'?{dataset:{edit:'new'}}:null}});
    assert.equal(Number(app.element('goalIntervalValue').value),value);
    assert.equal(app.element('goalIntervalUnit').value,unit);
  }
});

test('invalid intervals never reach API and legacy today goals change only on save',async()=>{
  const app=setup([{id:'old',title:'Old',prompt:'Goal',cadence:'daily',range_type:'today',enabled:false}]);await app.load();
  await app.emit('goalsGrid','click',{target:{closest:s=>s==='[data-edit]'?{dataset:{edit:'old'}}:null}});
  assert.equal(app.element('goalIntervalUnit').value,'days');
  assert.equal(Number(app.element('goalRangeValue').value),1);
  assert.equal(app.goals[0].range_type,'today');
  for(const value of ['', '0', '-1', '1.5', '366']) {
    app.element('goalIntervalValue').value=value;
    await app.emit('goalForm','submit');
    assert.match(app.element('goalFormError').textContent,/周期数量/);
  }
  assert.ok(!app.requests.some(r=>r.data));
  app.element('goalIntervalValue').value='1';
  await app.emit('goalForm','submit');
  assert.equal(app.goals[0].range_type,'recent_days');
  assert.equal(app.goals[0].enabled,false);
});

test('inline draft survives polling and repeated new clicks',async()=>{
  const app=setup();await app.load();await app.emit('goalNewBtn','click');
  app.element('goalPrompt').value='Unsaved draft';
  await app.load();await app.emit('goalNewBtn','click');
  assert.equal(app.element('goalPrompt').value,'Unsaved draft');
  assert.equal(app.element('goalEditor').hidden,false);
  assert.equal(app.element('goalCards').innerHTML,'');
  assert.equal(app.element('goalNewBtn').disabled,true);
});

test('editing replaces the card in place and cancellation restores it without saving',async()=>{
  const app=setup([{id:'one',title:'One',prompt:'First',enabled:true,cadence:'hourly'},{id:'two',title:'Two',prompt:'Second',enabled:false,cadence:'daily'}]);await app.load();
  await app.emit('goalsGrid','click',{target:{closest:selector=>selector==='[data-edit]'?{dataset:{edit:'two'}}:null}});
  assert.equal(app.element('goalEditorRow').style.order,'2');
  assert.equal(app.element('goalEditorRow').hidden,false);
  assert.ok(!app.element('goalCards').innerHTML.includes('data-goal="two"'));
  app.element('goalPrompt').value='Draft';
  await app.emit('goalCancelBtn','click');
  assert.equal(app.element('goalEditor').hidden,true);
  assert.equal(app.element('goalEditorRow').hidden,true);
  assert.match(app.element('goalCards').innerHTML,/data-goal="two"/);
  assert.equal(app.goals[1].prompt,'Second');
  assert.ok(app.requests.every(r=>!r.data));
});

test('pause toggle persists enabled state, failure restores checkbox',async()=>{
  const app=setup([{id:'one',title:'One',prompt:'Check',cadence:'hourly',enabled:true}]);await app.load();
  const checkbox={dataset:{toggle:'one'},checked:false,disabled:false};
  await app.emit('goalsGrid','change',{target:{closest:()=>checkbox}});
  assert.equal(app.goals[0].enabled,false);
  app.fail('unavailable');checkbox.checked=true;
  await app.emit('goalsGrid','change',{target:{closest:()=>checkbox}});
  assert.equal(checkbox.checked,false);
});

test('manual run is explicit, duplicate clicks are blocked, and stop targets its run id',async()=>{
  const app=setup([{id:'one',title:'One',prompt:'Goal',enabled:false,cadence:'hourly',range_type:'recent_days',range_value:3,range_unit:'days'}]);
  await app.load();
  const event={target:{closest:s=>s==='[data-run]'?{dataset:{run:'one'}}:null}};
  const first=app.emit('goalsGrid','click',event);
  await app.emit('goalsGrid','click',event);
  await first;
  assert.equal(app.requests.filter(r=>r.url==='/api/goals/run').length,1);
  assert.equal(app.goals[0].enabled,false);
  assert.equal(app.goals[0].range_value,3);
  assert.match(app.element('goalCards').innerHTML,/停止执行/);
  await app.emit('goalsGrid','click',event);
  const stop=app.requests.find(r=>r.url==='/api/goals/stop');
  assert.deepEqual(stop.data,{id:'one',run_id:'manual-run'});
  assert.match(app.element('goalCards').innerHTML,/停止中/);
});

test('manual failure stays retryable and old servers do not receive run requests',async()=>{
  const goal={id:'one',title:'One',prompt:'Goal',enabled:true,cadence:'hourly'};
  const event={target:{closest:s=>s==='[data-run]'?{dataset:{run:'one'}}:null}};
  const old=setup([goal],['days','weeks','months'],false);await old.load();
  await old.emit('goalsGrid','click',event);
  assert.ok(old.requests.every(r=>!r.data));
  const app=setup([goal]);await app.load();app.fail('busy');
  await app.emit('goalsGrid','click',event);
  assert.equal(app.element('goalsError').textContent,'busy');
  assert.match(app.element('goalCards').innerHTML,/立即执行/);
  assert.ok(!app.element('goalCards').innerHTML.includes('提交中'));
});

test('other running goals block manual starts while completed cancellations can run again',async()=>{
  const app=setup([{id:'one',title:'One',prompt:'Goal',enabled:true,latest_run:{id:'r',status:'running',started_at:100}},
    {id:'two',title:'Two',prompt:'Goal',enabled:true,latest_run:{id:'old',status:'cancelled',cancel_requested:true,started_at:90}}]);await app.load();
  const event={target:{closest:s=>s==='[data-run]'?{dataset:{run:'two'}}:null}};
  await app.emit('goalsGrid','click',event);
  assert.ok(app.requests.every(r=>!r.data));
  app.goals[0].latest_run.status='completed';await app.load();
  await app.emit('goalsGrid','click',event);
  assert.equal(app.requests.filter(r=>r.url==='/api/goals/run').length,1);
});

test('delete is left of edit, confirms, and does not open the editor',async()=>{
  const app=setup([{id:'one',title:'One',prompt:'Goal',enabled:true,cadence:'hourly'}]);await app.load();
  const html=app.element('goalCards').innerHTML;
  assert.ok(html.indexOf('data-delete="one"')<html.indexOf('data-edit="one"'));
  const event={target:{closest:s=>s==='[data-delete]'?{dataset:{delete:'one'}}:null}};
  app.confirm(false);await app.emit('goalsGrid','click',event);
  assert.ok(app.requests.every(r=>!r.data));
  assert.equal(app.goals.length,1);
  app.confirm(true);
  const first=app.emit('goalsGrid','click',event);
  await app.emit('goalsGrid','click',event);await first;
  assert.equal(app.requests.filter(r=>r.url==='/api/goals/delete').length,1);
  assert.equal(app.goals.length,0);
  assert.match(app.element('goalCards').innerHTML,/暂无任务/);
});

test('running goals cannot be deleted and delete failure keeps the card',async()=>{
  const app=setup([{id:'one',title:'One',prompt:'Goal',enabled:true,latest_run:{status:'running',started_at:100}}]);await app.load();
  const event={target:{closest:s=>s==='[data-delete]'?{dataset:{delete:'one'}}:null}};
  await app.emit('goalsGrid','click',event);
  assert.ok(app.requests.every(r=>!r.data));
  app.goals[0].latest_run.status='completed';await app.load();app.fail('Delete failed');
  await app.emit('goalsGrid','click',event);
  assert.equal(app.goals.length,1);
  assert.equal(app.element('goalsError').textContent,'Delete failed');
  assert.match(app.element('goalCards').innerHTML,/data-delete="one"/);
});

test('each goal has its own row with the complete escaped result outside the card',async()=>{
  const result='Complete result\n'+'Evidence '.repeat(400)+'<img src=x onerror=x>END';
  const run={id:'run-one',status:'completed',started_at:100,finished_at:145,result,trigger:'manual',model:'test-model',usage:{total_tokens:1200}};
  const app=setup([{id:'one',title:'One',prompt:'First goal',enabled:true,latest_run:{...run,result:result.slice(0,1500)}},
    {id:'two',title:'Two',prompt:'Second goal',enabled:false}]);
  app.histories.set('one',[run]);
  await app.load();
  const html=app.element('goalCards').innerHTML;
  assert.equal((html.match(/class="goal-row"/g)||[]).length,2);
  assert.equal((html.match(/<section class="goal-output"/g)||[]).length,2);
  assert.ok(html.indexOf('data-goal-row="one"')<html.indexOf('data-goal-row="two"'));
  assert.ok(!html.match(/<article[\s\S]*?<\/article>/)[0].includes('Complete result'));
  assert.match(html,/&lt;img src=x onerror=x&gt;END/);
  assert.ok(!html.includes('<img src=x'));
  assert.ok(!html.includes('45.0s'));
  assert.ok(!html.includes('1,200 tokens'));
  assert.ok(!html.includes('正在读取完整结果'));
  await app.load();await app.load();
  assert.equal(app.requests.filter(r=>r.url.startsWith('/api/goals/history?')).length,1);
  assert.ok(app.requests.every(r=>!r.data));
  await app.emit('goalsGrid','click',{target:{closest:s=>s==='[data-history]'?{dataset:{history:'one'}}:null}});
  assert.match(app.element('goalRunContent').innerHTML,/45\.0s/);
  assert.match(app.element('goalRunContent').innerHTML,/1,200 tokens/);
  assert.match(app.element('goalRunContent').innerHTML,/&lt;img src=x onerror=x&gt;END/);
});

test('history failure retains the preview and explicitly retries without executing the goal',async()=>{
  const run={id:'run-one',status:'completed',started_at:100,finished_at:145,result:'Preview'};
  const app=setup([{id:'one',title:'One',prompt:'Goal',latest_run:run}]);
  app.onHistory(()=>{throw new Error('Offline');});
  await app.load();
  assert.match(app.element('goalCards').innerHTML,/Preview/);
  assert.match(app.element('goalCards').innerHTML,/完整结果读取失败/);
  await app.load();
  assert.equal(app.requests.filter(r=>r.url.startsWith('/api/goals/history?')).length,1);
  app.onHistory(()=>[{...run,result:'Full recovered result'}]);
  await app.emit('goalsGrid','click',{target:{closest:s=>s==='[data-result-retry]'?{dataset:{resultRetry:'one'}}:null}});
  assert.match(app.element('goalCards').innerHTML,/Full recovered result/);
  assert.ok(!app.element('goalCards').innerHTML.includes('完整结果读取失败'));
  assert.ok(app.requests.every(r=>!r.data));
});

test('editing retains the result on the right and result hydration preserves the draft',async()=>{
  const run={id:'run-one',status:'completed',started_at:100,finished_at:145,result:'Preview'};
  const app=setup([{id:'one',title:'One',prompt:'Goal',latest_run:run}]);
  let finishHistory;
  app.onHistory(()=>new Promise(resolve=>{finishHistory=resolve;}));
  await app.load();
  await app.emit('goalsGrid','click',{target:{closest:s=>s==='[data-edit]'?{dataset:{edit:'one'}}:null}});
  app.element('goalPrompt').value='Unsaved changes';
  finishHistory([{...run,result:'Full result'}]);
  await new Promise(resolve=>setImmediate(resolve));
  assert.equal(app.element('goalEditorResult').hidden,false);
  assert.match(app.element('goalEditorResult').innerHTML,/Full result/);
  assert.equal(app.element('goalPrompt').value,'Unsaved changes');
  assert.ok(!app.element('goalCards').innerHTML.includes('data-goal-row="one"'));
  await app.emit('goalCancelBtn','click');
  assert.match(app.element('goalCards').innerHTML,/Full result/);
  assert.equal(app.element('goalEditorResult').hidden,true);
  assert.ok(app.requests.every(r=>!r.data));
});

test('a stale history response never replaces a newer run and new completion refreshes the result',async()=>{
  const old={id:'old',status:'completed',started_at:100,finished_at:145,result:'Old preview'};
  const app=setup([{id:'one',title:'One',prompt:'Goal',latest_run:old}]);
  let finishHistory;
  app.onHistory(()=>new Promise(resolve=>{finishHistory=resolve;}));
  await app.load();
  app.goals[0].latest_run={id:'new',status:'running',started_at:200,progress:'New progress'};
  finishHistory([{...old,result:'Stale full result'}]);
  await new Promise(resolve=>setImmediate(resolve));
  await app.load();
  assert.match(app.element('goalCards').innerHTML,/New progress/);
  assert.ok(!app.element('goalCards').innerHTML.includes('Stale full result'));
  const completed={...app.goals[0].latest_run,status:'completed',finished_at:250,result:'New complete result'};
  app.goals[0].latest_run={...completed,result:'New preview'};
  app.onHistory(()=>[completed,old]);
  await app.load();
  assert.match(app.element('goalCards').innerHTML,/New complete result/);
  assert.ok(app.requests.every(r=>!r.data));
});

test('legacy result excerpts only its findings but retains qualifications, warnings, and full history',async()=>{
  const result='已核查；synced_at=2026-09-13，调用工具：list_private_chats\n\n发现（可能需回复，仍需确认）\n- 小明（chat_id: wxid_test）：询问明天时间 [1]\n- 小红：面试时间为 2026-09-15 10:00 [2]\n\n未列为待回\n- 小李：已回复\n\n注意：仅检查了部分私聊，不能确认没有遗漏。';
  const run={id:'run',status:'completed',started_at:100,result,sources:[{reference:1},{reference:2}]};
  const app=setup([{id:'one',title:'Goal',prompt:'Goal',latest_run:run}]);
  app.histories.set('one',[run]);await app.load();
  const html=app.element('goalCards').innerHTML;
  assert.match(html,/goal-result-list/);
  assert.match(html,/可能需回复，仍需确认/);
  assert.match(html,/小明：询问明天时间/);
  assert.match(html,/2026-09-15 10:00/);
  assert.match(html,/不能确认没有遗漏/);
  assert.ok(!html.includes('synced_at'));
  assert.ok(!html.includes('wxid_test'));
  assert.ok(!html.includes('[1]'));
  assert.ok(!html.includes('小李'));
  assert.equal(run.result,result);
  await app.emit('goalsGrid','click',{target:{closest:s=>s==='[data-history]'?{dataset:{history:'one'}}:null}});
  assert.match(app.element('goalRunContent').innerHTML,/小李/);
  assert.match(app.element('goalRunContent').innerHTML,/wxid_test/);
});

test('blank-separated bullets and their continuation text stay in the list',async()=>{
  const run={id:'run',status:'completed',started_at:100,result:'- 第一条\n补充内容\n\n- 第二条\n\n注意：数据可能不完整。'};
  const app=setup([{id:'one',title:'Goal',prompt:'Goal',latest_run:run}]);
  app.histories.set('one',[run]);await app.load();
  const html=app.element('goalCards').innerHTML;
  assert.match(html,/<li>第一条\n补充内容<\/li>/);
  assert.match(html,/<li>第二条<\/li>/);
  assert.match(html,/数据可能不完整/);
});

test('history opens the latest result, switches runs, and preserves selection on polling',async()=>{
  const latest={id:'latest',status:'completed',started_at:200,finished_at:205,result:'Latest result',usage:{total_tokens:50}};
  const older={id:'older',status:'failed',started_at:100,finished_at:105,error:'Historical failure'};
  const app=setup([{id:'one',title:'Goal',prompt:'Goal',latest_run:latest}]);
  app.histories.set('one',[latest,older]);await app.load();
  await app.emit('goalsGrid','click',{target:{closest:s=>s==='[data-history]'?{dataset:{history:'one'}}:null}});
  assert.equal(app.element('goalHistory').open,true);
  assert.match(app.element('goalRunList').innerHTML,/data-run-select="latest" aria-pressed="true"/);
  assert.match(app.element('goalRunContent').innerHTML,/Latest result/);
  await app.emit('goalRunList','click',{target:{closest:()=>({dataset:{runSelect:'older'}})}});
  assert.match(app.element('goalRunContent').innerHTML,/Historical failure/);
  app.goals[0].next_run_at=300;await app.load();
  assert.match(app.element('goalRunList').innerHTML,/data-run-select="older" aria-pressed="true"/);
  assert.match(app.element('goalRunContent').innerHTML,/Historical failure/);
  await app.emit('goalsGrid','click',{target:{closest:s=>s==='[data-history]'?{dataset:{history:'one',historyRun:'latest'}}:null}});
  assert.match(app.element('goalRunContent').innerHTML,/Latest result/);
  assert.ok(app.requests.every(r=>!r.data));
});

test('history responses from a previously opened goal cannot overwrite the selected goal',async()=>{
  const a={id:'a-run',status:'completed',result:'Result A',started_at:100};
  const b={id:'b-run',status:'completed',result:'Result B',started_at:200};
  const app=setup([{id:'a',title:'A',prompt:'A',latest_run:a},{id:'b',title:'B',prompt:'B',latest_run:b}]);
  app.histories.set('a',[a]);app.histories.set('b',[b]);await app.load();
  let release;
  app.onHistory(id=>id==='a'?new Promise(resolve=>{release=resolve;}):[b]);
  const open=id=>app.emit('goalsGrid','click',{target:{closest:s=>s==='[data-history]'?{dataset:{history:id}}:null}});
  const pending=open('a');await new Promise(resolve=>setImmediate(resolve));
  await open('b');release([a]);await pending;
  assert.match(app.element('goalRunContent').innerHTML,/Result B/);
  assert.ok(!app.element('goalRunContent').innerHTML.includes('Result A'));
});

test('structured results render fields instead of JSON and references open the original evidence',async()=>{
  const data={summary:'Found one open question',items:[{title:'<script>Friend</script>',detail:'Can you confirm tomorrow?',suggestion:'草稿：明天可以。',source_refs:[1]}],notice:'Only synced messages were checked.'};
  const run={id:'run',status:'completed',started_at:100,result:JSON.stringify(data),result_data:data,
    sources:[{reference:1,chat_id:'friend',chat_title:'Friend',text:'Can you confirm tomorrow?',sender:'Friend',time:'09/13 18:00'}]};
  const app=setup([{id:'one',title:'Goal',prompt:'Goal',latest_run:{...run,result:data.summary}}]);
  app.histories.set('one',[run]);await app.load();
  const html=app.element('goalCards').innerHTML;
  assert.match(html,/goal-structured-result/);
  assert.match(html,/goal-result-summary">Found one open question/);
  assert.match(html,/<h3>&lt;script&gt;Friend&lt;\/script&gt;<\/h3>/);
  assert.match(html,/goal-result-description">Can you confirm tomorrow/);
  assert.match(html,/草稿：明天可以/);
  assert.match(html,/Only synced messages were checked/);
  assert.ok(!html.includes('source_refs'));
  assert.ok(!html.includes('<script>'));
  await app.emit('goalsGrid','click',{target:{closest:s=>s==='[data-history]'?{dataset:{history:'one',historyRun:'run',goalReference:'1'}}:null}});
  assert.equal(app.element('goalSources').open,true);
  assert.match(app.element('goalRunContent').innerHTML,/goal-result-summary">Found one open question/);
  assert.ok(!app.element('goalRunContent').innerHTML.includes('source_refs'));
  app.element('goalSources').open=false;
  await app.emit('goalRunContent','click',{target:{closest:s=>s==='[data-goal-reference]'?{dataset:{goalReference:'1'}}:null}});
  assert.equal(app.element('goalSources').open,true);
  assert.ok(app.requests.every(r=>!r.data));
});

test('empty structured findings do not fabricate items and notices stay visible',async()=>{
  const data={summary:'No clear reply needed in inspected messages',items:[],notice:'Some messages are not synced.'};
  const run={id:'run',status:'completed',started_at:100,result:JSON.stringify(data),result_data:data};
  const app=setup([{id:'one',title:'Goal',prompt:'Goal',latest_run:run}]);
  app.histories.set('one',[run]);await app.load();
  const html=app.element('goalCards').innerHTML;
  assert.match(html,/No clear reply needed in inspected messages/);
  assert.match(html,/Some messages are not synced/);
  assert.ok(!html.includes('goal-result-list'));
  assert.ok(!html.includes('没有返回结果'));
  assert.ok(!html.includes('等待首次检查'));
});

test('a long structured result is not replaced by the legacy 1500 character preview',async()=>{
  const data={summary:'Multiple results',items:Array.from({length:15},(_,i)=>({title:`Item ${i}`,detail:'Long evidence '.repeat(12),suggestion:null,source_refs:[1]})),notice:null};
  const raw=JSON.stringify(data);
  const run={id:'run',status:'completed',started_at:100,result:raw,result_data:data};
  const app=setup([{id:'one',title:'Goal',prompt:'Goal',latest_run:{...run,result:raw.slice(0,1500)}}]);
  app.histories.set('one',[run]);await app.load();
  const html=app.element('goalCards').innerHTML;
  assert.match(html,/<h3>Item 14<\/h3>/);
  assert.ok(!html.includes('source_refs'));
  assert.match(html,/tabindex="0" role="region" aria-label="最新结果内容"/);
});
