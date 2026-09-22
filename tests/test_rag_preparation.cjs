const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

function setup() {
  const elements = new Map();
  const element = () => ({
    textContent: '', innerHTML: '', disabled: false, style: {}, children: [],
    addEventListener() {}, setAttribute() {}, querySelectorAll() { return []; },
    querySelector() { return element(); }, appendChild(child) { this.children.push(child); },
    classList: { add() {}, remove() {}, toggle() {} },
  });
  const document = {
    querySelector(selector) {
      if (!elements.has(selector)) elements.set(selector, element());
      return elements.get(selector);
    },
    querySelectorAll() { return []; }, createElement: element, addEventListener() {},
  };
  const context = vm.createContext({
    document, AbortController, TextDecoder, Response,
    fetch: () => new Promise(() => {}),
    window: { setInterval() {}, setTimeout() {}, clearTimeout() {}, addEventListener() {} },
  });
  vm.runInContext(fs.readFileSync(path.join(__dirname, '../web/app.js'), 'utf8'), context);
  context.loadVoiceStatus = async () => {};
  context.loadRagStatus = async () => {};
  context.loadStatus = async () => {};
  return { context, elements, state: vm.runInContext('state', context) };
}

test('one click delegates all three stages to one server-owned execution', async () => {
  const { context, elements } = setup();
  const calls = [];
  context.window.WechatJobs = { run: async (kind, payload, options) => {
    calls.push([kind, payload]);
    options.onEvent({event:'stage', stage:2, message:'全文索引'});
    return {ok:true};
  }};
  await context.prepareRag();
  assert.equal(calls.length, 1);
  assert.equal(calls[0][0], '/api/rag/prepare');
  assert.equal(elements.get('#ragPrepareStatus').textContent, '检索准备完成');
  assert.equal(elements.get('#ragPrepareBtn').disabled, false);
});

test('failure is visible and does not start another paid execution automatically', async () => {
  const { context, elements } = setup();
  let calls=0;
  context.window.WechatJobs = { run: async () => { calls++; throw new Error('fixture failed'); } };
  await context.prepareRag();
  assert.equal(calls,1);
  assert.equal(elements.get('#ragPrepareStatus').textContent,'fixture failed');
});

test('explicit stop targets the preparation job and keeps controls locked until confirmed', async () => {
  const { context, elements, state } = setup();
  let finish, signal;
  context.window.WechatJobs = { run: (kind,payload,options) => {
    signal=options.signal;
    return new Promise(resolve=>{finish=resolve;});
  }};
  const running=context.prepareRag();
  context.stopRagPreparation();
  assert.equal(signal.aborted,true);
  assert.equal(state.preparationRunning,true);
  assert.equal(elements.get('#ragPrepareBtn').disabled,true);
  finish({ok:true});
  await running;
  assert.equal(state.preparationRunning,false);
});

test('double clicks and standalone index actions do not overlap a pipeline', async () => {
  const { context } = setup();
  let finish, calls=0;
  context.window.WechatJobs = { run: () => {calls++; return new Promise(resolve=>{finish=resolve;});}};
  const running=context.prepareRag();
  await context.prepareRag();
  assert.equal(await context.rebuildRagIndex(),null);
  assert.equal(calls,1);
  finish({ok:true});
  await running;
});

test('partial voice failures remain visible after successful index updates', async () => {
  const { context, elements } = setup();
  context.window.WechatJobs={run:async()=>({ok:true,voice_failures:2})};
  await context.prepareRag();
  assert.match(elements.get('#ragPrepareStatus').textContent,/2 条语音转写失败/);
});

test('voice action preserves cached transcripts and all index actions use background jobs', async () => {
  const { context } = setup();
  const requests=[];
  context.window.WechatJobs={run:async(kind,payload)=>{requests.push([kind,JSON.parse(JSON.stringify(payload))]);return {ok:true};}};
  assert.equal((await context.transcribeAllVoices()).ok,true);
  await context.rebuildRagIndex();
  await context.rebuildRagEmbeddingIndex();
  assert.deepEqual(requests,[
    ['/api/transcribe_all_voices_stream',{force:false}],
    ['/api/rag/rebuild_stream',{full:false}],
    ['/api/rag/embedding_rebuild_stream',{full:false}],
  ]);
});

test('switching application views never aborts running operations', async () => {
  const { context, state } = setup();
  context.document.dispatchEvent=()=>{};
  context.CustomEvent=class {constructor(type,options){this.type=type;this.detail=options.detail;}};
  const controllers=['qaStreamController','voiceBatchController','ragRebuildController','ragEmbeddingController','preparationController'];
  for (const slot of controllers) state[slot]=new AbortController();
  for (const view of ['raw','rag','goals','qa']) context.switchView(view);
  for (const slot of controllers) assert.equal(state[slot].signal.aborted,false);
});

test('RAG settings keeps logs first and removes redundant controls', () => {
  const {context,elements}=setup();
  const html=fs.readFileSync(path.join(__dirname,'../web/index.html'),'utf8');
  assert.match(html,/data-view="rag">RAG 配置/);
  assert.match(html,/<h1 class="page-heading">RAG 配置<\/h1>/);
  assert.ok(html.indexOf('class="rag-log-panel"')<html.indexOf('id="ragSearchForm"'));
  assert.doesNotMatch(html,/voiceSettingsBtn/);
  context.renderRagStatus();
  assert.doesNotMatch(html,/ragOverview|rag-overview/);
  assert.equal(elements.has('#ragOverview'),false);
  assert.doesNotMatch(elements.get('#ragMeta').textContent,/联系人索引/);
  assert.match(html,/调试片段数/);
});

test('reset person cache does not show a contradictory RAG readiness label', () => {
  const {context,elements,state}=setup();
  state.ragStatus={person_index:{ready:false,stats:{}},search_index:{ready:true,message_count:460724},
    semantic_index:{ready:true,configured:true,api_key_set:true,pending_message_count:0}};
  context.renderRagStatus();
  assert.match(elements.get('#ragMeta').textContent,/全文索引 已就绪.*语义索引 已就绪/);
  assert.doesNotMatch(elements.get('#ragMeta').textContent,/未就绪|联系人/);
  assert.doesNotMatch(elements.get('#ragStatusGrid').innerHTML,/联系人索引|检索策略/);
  assert.match(elements.get('#ragStatusGrid').innerHTML,/460,724/);
});

test('debug fragment limit only affects the debug request, not saved QA settings', async () => {
  const {context,elements,state}=setup(); const requests=[];
  elements.get('#ragQuestion').value='fixture retrieval question';
  elements.get('#ragLimit').value='24';
  elements.get('#qaContextLimit').value='80';
  state.llmConfig={qa:{max_context_messages:80}};
  context.postJSON=async(url,payload)=>{requests.push([url,JSON.parse(JSON.stringify(payload))]);return {ok:true};};
  context.renderRagSearchResult=()=>{};
  await context.runRagSearch({preventDefault(){}});
  assert.deepEqual(requests,[['/api/rag/search',{question:'fixture retrieval question',limit:24}]]);
  assert.equal(state.llmConfig.qa.max_context_messages,80);
  assert.equal(elements.get('#qaContextLimit').value,'80');
});

test('entering RAG settings refreshes index and voice status without a refresh button', () => {
  const {context}=setup(); let index=0,voice=0;
  context.document.dispatchEvent=()=>{};
  context.CustomEvent=function(){};
  context.loadRagStatus=async()=>index++;
  context.loadVoiceStatus=async()=>voice++;
  context.switchView('rag');
  assert.equal(index,1); assert.equal(voice,1);
});

test('voice card keeps live text counts without a progress element', () => {
  const {context,elements}=setup();
  const html=fs.readFileSync(path.join(__dirname,'../web/index.html'),'utf8');
  assert.match(html,/data-view="raw">聊天内容/);
  assert.doesNotMatch(html,/voiceBatchProgress|<progress/);
  context.renderVoiceStatus({transcribed:800,total:868,pending:68});
  assert.equal(elements.get('#voiceBatchCount').textContent,'已转写 800 / 868');
  assert.equal(elements.get('#voicePendingCount').textContent,'待转写 68 条');
  context.renderVoiceStatus({transcribed:868,total:868,pending:0});
  assert.equal(elements.get('#voiceBatchCount').textContent,'已转写 868 / 868');
  assert.equal(elements.get('#voicePendingCount').textContent,'待转写 0 条');
});

test('single voice and RAG debug retrieval also use the background executor', async () => {
  const { context } = setup();
  const calls=[];
  context.window.WechatJobs={run:async(kind)=>{calls.push(kind);return {ok:true};}};
  await context.postJSON('/api/transcribe_voice',{});
  await context.postJSON('/api/rag/search',{});
  assert.deepEqual(calls,['/api/transcribe_voice','/api/rag/search']);
});

function scheduleFixture() {
  return {enabled:false,interval_value:1,interval_unit:'days',next_run_at:null,last_status:'',last_finished_at:null};
}

test('schedule renders saved hours/days and keeps unsaved edits during polling', () => {
  const {context,elements,state}=setup();
  state.ragSchedule=scheduleFixture();
  context.renderRagSchedule();
  assert.equal(elements.get('#ragScheduleUnit').value,'days');
  assert.equal(elements.get('#ragScheduleEnabled').checked,false);
  assert.equal(elements.get('#ragScheduleSaveBtn').disabled,true);
  elements.get('#ragScheduleValue').value='6';
  elements.get('#ragScheduleUnit').value='hours';
  context.editRagSchedule();
  state.ragSchedule={...state.ragSchedule,enabled:true,next_run_at:1789426800};
  context.renderRagSchedule();
  assert.equal(elements.get('#ragScheduleValue').value,'6');
  assert.equal(elements.get('#ragScheduleValue').max,'8760');
  assert.equal(elements.get('#ragScheduleSaveBtn').disabled,false);
  assert.match(elements.get('#ragScheduleStatus').textContent,/下次更新/);
});

test('saving a schedule only saves settings and does not start a paid pipeline', async () => {
  const {context,elements,state}=setup(); const calls=[];
  state.ragSchedule=scheduleFixture(); context.renderRagSchedule();
  elements.get('#ragScheduleEnabled').checked=true;
  elements.get('#ragScheduleValue').value='2';
  elements.get('#ragScheduleUnit').value='hours';
  context.editRagSchedule();
  context.postJSON=async(url,payload)=>{calls.push([url,JSON.parse(JSON.stringify(payload))]);return {schedule:{...state.ragSchedule,...payload}};};
  await context.saveRagSchedule({preventDefault(){}});
  assert.deepEqual(calls,[['/api/rag/schedule',{enabled:true,interval_value:2,interval_unit:'hours'}]]);
  assert.equal(state.ragScheduleDirty,false);
  assert.equal(elements.get('#ragScheduleFeedback').textContent,'已保存');
  assert.equal(elements.get('#ragScheduleSaveBtn').disabled,true);
});

test('invalid or failed saves keep the old schedule and allow retry', async () => {
  const {context,elements,state}=setup(); let calls=0;
  state.ragSchedule=scheduleFixture(); context.renderRagSchedule();
  context.postJSON=async()=>{calls++;throw Error('fixture failure');};
  elements.get('#ragScheduleValue').value='0'; context.editRagSchedule();
  await context.saveRagSchedule({preventDefault(){}});
  assert.equal(calls,0);
  elements.get('#ragScheduleValue').value='2';
  await context.saveRagSchedule({preventDefault(){}});
  assert.equal(calls,1);
  assert.equal(state.ragSchedule.interval_value,1);
  assert.equal(state.ragScheduleDirty,true);
  assert.equal(elements.get('#ragScheduleValue').value,'2');
  assert.match(elements.get('#ragScheduleFeedback').textContent,/fixture failure/);
});

test('schedule polling observes a server-started job once and never submits work', async () => {
  const {context,state}=setup(); let observed=0;
  state.ragScheduleLoading=false;
  context.getJSON=async()=>({schedule:{...scheduleFixture(),enabled:true,last_status:'running',job:{id:'scheduled',kind:'/api/rag/prepare',status:'running'}}});
  context.resumeMaintenanceJob=async job=>{observed++;assert.equal(job.id,'scheduled');state.preparationController=new AbortController();};
  await context.loadRagSchedule();
  await context.loadRagSchedule();
  assert.equal(observed,1);
});

test('stale schedule read cannot overwrite a newer saved configuration', async () => {
  const {context,elements,state}=setup(); let resolve;
  state.ragScheduleLoading=false;
  state.ragSchedule=scheduleFixture(); context.renderRagSchedule();
  context.getJSON=()=>new Promise(r=>resolve=r);
  const read=context.loadRagSchedule();
  elements.get('#ragScheduleValue').value='3'; context.editRagSchedule();
  context.postJSON=async(url,payload)=>({schedule:{...state.ragSchedule,...payload}});
  await context.saveRagSchedule({preventDefault(){}});
  resolve({schedule:scheduleFixture()}); await read;
  assert.equal(state.ragSchedule.interval_value,3);
});

test('busy, paused-running and failed states remain distinguishable', () => {
  const {context,elements,state}=setup();
  state.ragSchedule={...scheduleFixture(),enabled:true,waiting:true};
  context.renderRagSchedule();
  assert.match(elements.get('#ragScheduleStatus').textContent,/等待当前操作完成/);
  state.ragSchedule={...scheduleFixture(),last_status:'running'};
  context.renderRagSchedule();
  assert.match(elements.get('#ragScheduleStatus').textContent,/定时已关闭.*执行中/);
  state.ragSchedule={...scheduleFixture(),last_status:'failed',last_finished_at:1789426800,last_error:'fixture error'};
  context.renderRagSchedule();
  assert.match(elements.get('#ragScheduleStatus').textContent,/上次失败.*fixture error/);
});

test('all five page headings match their tabs and use shared title styling', () => {
  const html=fs.readFileSync(path.join(__dirname,'../web/index.html'),'utf8');
  const headings=[...html.matchAll(/<h1 class="page-heading">([^<]+)<\/h1>/g)].map(m=>m[1]);
  const tabs=[...html.matchAll(/data-view="[^"]+">([^<]+)<\/button>/g)].map(m=>m[1]);
  assert.equal(headings.length,5);
  assert.deepEqual([...headings].sort(),[...tabs].sort());
  assert.match(html,/<h2 id="qaTitle" class="conversation-title">/);
  assert.doesNotMatch(html,/<h[12]>微信<|<h[12]>问答<|<h[12]>我的任务</);
  const css=fs.readFileSync(path.join(__dirname,'../web/styles.css'),'utf8');
  assert.match(css,/\.page-header \.page-heading\s*\{[^}]*font-size: 20px;[^}]*font-weight: 650;/);
});

test('previous DNS failures get readable labels without changing the stored record', () => {
  const {context,elements,state}=setup();
  const error='Embedding 请求失败：[Errno 8] nodename nor servname provided, or not known';
  state.ragSchedule={...scheduleFixture(),last_status:'failed',last_finished_at:1789426800,last_error:error};
  context.renderRagSchedule();
  assert.match(elements.get('#ragScheduleStatus').textContent,/上次失败.*域名解析失败/);
  assert.doesNotMatch(elements.get('#ragScheduleStatus').textContent,/Errno 8|nodename/);
  assert.equal(state.ragSchedule.last_error,error);
});

test('latest manual success replaces previous scheduled error without moving the next run', () => {
  const {context,elements,state}=setup();
  state.ragSchedule={...scheduleFixture(),enabled:true,next_run_at:1789430000,last_status:'failed',
    last_finished_at:1789426800,last_error:'old DNS error',
    latest_run:{id:'manual-1',trigger:'manual',status:'completed',finished_at:1789427000,error:''}};
  context.renderRagSchedule();
  assert.match(elements.get('#ragScheduleStatus').textContent,/下次更新.*上次手动已完成/);
  assert.doesNotMatch(elements.get('#ragScheduleStatus').textContent,/失败|old DNS/);
  assert.equal(state.ragSchedule.next_run_at,1789430000);
  state.ragSchedule.latest_run={...state.ragSchedule.latest_run,status:'failed',error:'new failure'};
  context.renderRagSchedule();
  assert.match(elements.get('#ragScheduleStatus').textContent,/new failure/);
});

test('failed history load stays distinct from empty history and retries without a model call', async () => {
  const {context,elements,state}=setup(); const timers=[];
  state.qaHistoryLoading=false;
  context.window.setTimeout=(callback,delay)=>{timers.push({callback,delay});return 1;};
  context.getJSON=async()=>{throw Error('temporary disconnect');};
  await context.loadQaConversations();
  assert.match(elements.get('#qaConversationList').innerHTML,/加载失败/);
  assert.doesNotMatch(elements.get('#qaConversationList').innerHTML,/暂无对话/);
  assert.equal(timers.at(-1).delay,3000);
  context.getJSON=async url=>url==='/api/qa/conversations'
    ? {conversations:[{id:'restored',title:'fixture',message_count:2}]}
    : {conversation:{id:'restored',title:'fixture',messages:[{role:'user',content:'one'},{role:'assistant',content:'two'}]}};
  await timers.at(-1).callback();
  assert.equal(state.qaConversations.length,1);
  assert.equal(state.qaMessages.length,2);
  assert.equal(state.qaHistoryError,'');
});

test('malformed history response preserves existing messages instead of rendering no conversations', async () => {
  const {context,state}=setup(); state.qaHistoryLoading=false;
  state.qaConversations=[{id:'kept',title:'fixture'}];
  state.activeQaConversation={id:'kept'};
  state.qaMessages=[{role:'user',content:'saved'}];
  context.getJSON=async()=>({ok:true});
  await context.loadQaConversations();
  assert.equal(state.qaConversations[0].id,'kept');
  assert.equal(state.qaMessages[0].content,'saved');
  assert.match(state.qaHistoryError,/有效/);
});

test('failed detail load retries the selected conversation and preserves the visible answer', async () => {
  const {context,state}=setup(); state.qaHistoryLoading=false;
  state.activeQaConversation={id:'old'};
  state.qaMessages=[{role:'user',content:'preserved'}];
  context.getJSON=async()=>{throw Error('detail interrupted');};
  await assert.rejects(context.openQaConversation('selected'));
  assert.equal(state.qaMessages[0].content,'preserved');
  context.getJSON=async url=>url==='/api/qa/conversations' ? {conversations:[{id:'selected'}]}
    : {conversation:{id:'selected',title:'fixture',messages:[{role:'user',content:'restored'}]}};
  await context.loadQaConversations();
  assert.equal(state.activeQaConversation.id,'selected');
  assert.equal(state.qaMessages[0].content,'restored');
});

test('late conversation response cannot replace the more recently selected conversation', async () => {
  const {context,state}=setup(); const reads={};
  context.getJSON=url=>new Promise(resolve=>{reads[url]=resolve;});
  const first=context.openQaConversation('first');
  const second=context.openQaConversation('second');
  reads['/api/qa/conversation?id=second']({conversation:{id:'second',messages:[]}}); await second;
  reads['/api/qa/conversation?id=first']({conversation:{id:'first',messages:[]}}); await first;
  assert.equal(state.activeQaConversation.id,'second');
});

test('reentering QA retries history loading without cancelling an answer', () => {
  const {context,state}=setup(); let reads=0;
  context.document.dispatchEvent=()=>{};context.CustomEvent=function(){};
  state.qaStreamController=new AbortController();
  context.loadQaConversations=async()=>reads++;
  context.switchView('qa');
  assert.equal(reads,1);
  assert.equal(state.qaStreamController.signal.aborted,false);
});
