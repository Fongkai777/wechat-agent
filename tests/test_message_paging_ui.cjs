const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

function setup() {
  const elements = new Map();
  const element = () => ({
    textContent:'', _html:'', rewrites:0, inserts:[], disabled:false, hidden:false, style:{}, dataset:{},
    scrollHeight:1000, scrollTop:800, clientHeight:200, isConnected:true,
    get innerHTML() {return this._html;}, set innerHTML(value) {this._html=value; this.rewrites++;},
    insertAdjacentHTML(where, html) {this.inserts.push([where, html]);},
    addEventListener() {}, setAttribute() {}, remove() {}, querySelectorAll() {return [];},
    querySelector(selector) {return get(selector);}, getBoundingClientRect() {return {top:0,bottom:100};},
    classList:{add(){},remove(){},toggle(){},contains(){return true;}},
  });
  const get = selector => {if (!elements.has(selector)) elements.set(selector,element()); return elements.get(selector);};
  const context = vm.createContext({
    document:{querySelector:get,querySelectorAll(){return [];},createElement:element,addEventListener(){}},
    AbortController,TextDecoder,Response,URLSearchParams,CSS:{escape:value=>value},
    fetch:()=>new Promise(()=>{}),window:{setInterval(){},setTimeout(){},clearTimeout(){},addEventListener(){}},
  });
  vm.runInContext(fs.readFileSync(path.join(__dirname,'../web/app.js'),'utf8'),context);
  const state = vm.runInContext('state',context);
  state.chatViewReady=true;state.chatViewLoading=false;
  return {context,state,elements,get};
}

function page(chat='a', ids=[1,2], more=true) {
  const messages=ids.map(id=>({id:`${chat}-${id}`,type:'text',content:'repeat',time:'2026-09-13 12:00:00'}));
  return {chat:{id:chat,title:chat,type:'private',total_messages:200},total:200,messages,
    before_cursor:messages[0]?.id,after_cursor:messages.at(-1)?.id,has_more_before:more,has_more_after:false};
}

function syncResult(updated=0) {
  return {ok:true,status:{total_chats:1,total_messages:201,sync_interval:60,sync_revision:5,
    last_synced_at:'2026-09-13T21:50:00',decrypt:{updated}}};
}

function busyError() {
  return Object.assign(new Error('正在处理其他请求，请稍后同步'),{status:409,code:'sync_busy'});
}

test('manual sync refreshes messages even if automatic sync already imported the change',async()=>{
  const {context,state,get}=setup();const calls=[];
  context.postJSON=async url=>{calls.push(url);return syncResult();};
  context.loadChats=async()=>{calls.push('chats');return true;};
  context.refreshChatMessages=async()=>calls.push('messages');
  context.refreshVisibleMessages=async()=>{throw new Error('duplicate refresh');};
  context.loadStatus=async()=>{throw new Error('redundant status request');};
  context.loadRagStatus=async()=>{throw new Error('RAG status unavailable');};
  context.loadQaIndexStatus=async()=>{throw new Error('QA status unavailable');};
  await context.syncLatestMessages();
  assert.deepEqual(calls,['/api/sync','chats','messages']);
  assert.equal(state.syncRevision,'5');assert.equal(state.manualSyncing,false);
  assert.equal(get('#refreshBtn').disabled,false);assert.equal(get('#syncStatus').textContent,'已同步最新消息');
  assert.equal(get('#syncTime').textContent,'21:50:00 已更新');
});

test('busy readers are retried without cancelling tasks, including responses from older servers',async()=>{
  const {context,get}=setup();let attempts=0;const waits=[];
  context.postJSON=async()=>{if (++attempts<3) {const error=busyError();delete error.code;throw error;}return syncResult();};
  context.waitForSyncRetry=async ms=>{waits.push(ms);assert.equal(get('#syncStatus').textContent,'等待当前读取完成');};
  context.loadChats=async()=>true;context.refreshChatMessages=async()=>{};
  context.loadRagStatus=async()=>{};context.loadQaIndexStatus=async()=>{};
  await context.syncLatestMessages();
  assert.equal(attempts,3);assert.deepEqual(waits,[500,1000]);
  assert.equal(get('#syncStatus').textContent,'已同步最新消息');
});

test('long-running readers leave an actionable busy state after bounded retries',async()=>{
  const {context,state,get}=setup();let attempts=0;const waits=[];
  state.chats=[{id:'keep'}];state.currentMessages=[{id:'keep-1'}];
  context.postJSON=async()=>{attempts++;throw busyError();};
  context.waitForSyncRetry=async ms=>waits.push(ms);
  context.loadChats=async()=>assert.fail('a busy sync must not refresh the sidebar');
  await context.syncLatestMessages();
  assert.equal(attempts,5);assert.deepEqual(waits,[500,1000,1500,2000]);
  assert.equal(get('#syncStatus').textContent,'服务忙，请稍后同步');
  assert.equal(get('#syncStatus').dataset.state,'warning');
  assert.equal(state.chats[0].id,'keep');assert.equal(state.currentMessages[0].id,'keep-1');
  assert.equal(state.manualSyncing,false);assert.equal(get('#refreshBtn').disabled,false);
});

test('real sync failures are not retried as busy and retain the concrete reason',async()=>{
  for (const status of [409,500]) {
    const {context,get}=setup();let attempts=0;
    get('#syncTime').textContent='previous successful sync';
    context.postJSON=async()=>{attempts++;throw Object.assign(new Error('部分数据库同步失败'),{status});};
    context.waitForSyncRetry=async()=>assert.fail('not retryable');
    await context.syncLatestMessages();
    assert.equal(attempts,1);assert.equal(get('#syncStatus').textContent,'同步失败：部分数据库同步失败');
    assert.equal(get('#syncTime').textContent,'previous successful sync');
    assert.equal(get('#refreshBtn').disabled,false);
  }
});

test('sidebar and message refresh failures are distinguished from a successful import',async()=>{
  for (const phase of ['chats','messages']) {
    const {context,state,get}=setup();state.syncRevision='4';
    context.postJSON=async()=>syncResult(1);
    context.loadChats=async()=>{if(phase==='chats')throw new Error('offline');return true;};
    context.refreshChatMessages=async()=>{throw new Error('offline');};
    await context.syncLatestMessages();
    assert.match(get('#syncStatus').textContent,phase==='chats'?/已同步，聊天列表刷新失败：offline/:/已同步，消息刷新失败：offline/);
    assert.equal(get('#syncTime').textContent,'21:50:00 已更新');
    assert.equal(state.syncRevision,'4');assert.equal(get('#refreshBtn').disabled,false);
  }
});

test('double sync clicks share one execution and in-flight polling does not overwrite its state',async()=>{
  const {context,state,get}=setup();let finish,resolveStatus,attempts=0;
  context.getJSON=()=>new Promise(resolve=>resolveStatus=resolve);
  const polling=context.pollSyncedMessages();
  context.postJSON=()=>{attempts++;return new Promise(resolve=>finish=resolve);};
  context.loadChats=async()=>true;context.refreshChatMessages=async()=>{};
  context.loadRagStatus=async()=>{};context.loadQaIndexStatus=async()=>{};
  const syncing=context.syncLatestMessages();await context.syncLatestMessages();
  resolveStatus(syncResult().status);await polling;
  assert.equal(get('#syncStatus').textContent,'正在读取最新消息');
  assert.equal(attempts,1);finish(syncResult());await syncing;
  assert.equal(state.manualSyncing,false);
});

test('HTTP error metadata survives postJSON for narrow sync retries',async()=>{
  const {context}=setup();
  context.fetch=async()=>new Response(JSON.stringify({ok:false,code:'sync_busy',error:'busy'}),{status:409});
  await assert.rejects(context.postJSON('/api/sync',{}),error=>error.status===409&&error.code==='sync_busy');
});

test('older sidebar requests and errors cannot overwrite a newer search result',async()=>{
  for (const failOld of [false,true]) {
    const {context,state}=setup();const pending=[];
    context.getJSON=url=>new Promise((resolve,reject)=>pending.push({url,resolve,reject}));
    const old=context.loadChats();state.query='friend';const newer=context.loadChats();
    assert.match(pending[1].url,/q=friend/);
    pending[1].resolve({chats:[{id:'new',title:'Friend'}]});assert.equal(await newer,true);
    if (failOld) pending[0].reject(new Error('old request failed'));
    else pending[0].resolve({chats:[{id:'old',title:'Old'}]});
    assert.equal(await old,false);assert.equal(state.chats[0].id,'new');
  }
});

test('invalid sidebar data does not erase the last good list',async()=>{
  const {context,state}=setup();state.chats=[{id:'keep'}];context.getJSON=async()=>({});
  await assert.rejects(context.loadChats(),/有效的聊天列表/);assert.equal(state.chats[0].id,'keep');
});

test('a failed initial status request retries in place and opens the successful chat list only once',async()=>{
  const {context,state,get}=setup();state.chatViewReady=false;
  const retries=[];let statuses=0,opened=0;
  context.window.setTimeout=(callback,ms)=>{retries.push({callback,ms});return retries.length;};
  context.loadStatus=async()=>{if(++statuses===1)throw new Error('offline');context.renderStatus(syncResult().status);};
  context.loadChats=async()=>{state.chats=[{id:'a'}];return true;};
  context.openChat=async id=>{opened++;state.activeChat={id};};
  await context.initializeChatView();
  assert.equal(state.chatViewReady,false);assert.equal(state.chatViewLoading,false);
  assert.equal(get('#syncStatus').textContent,'连接暂时中断，正在重试');
  assert.match(get('#syncStatus').title,/offline/);assert.equal(retries[0].ms,3000);assert.equal(opened,1);
  await retries[0].callback();
  assert.equal(state.chatViewReady,true);assert.equal(get('#syncStatus').textContent,'自动同步已开启');
  assert.equal(opened,1);
});

test('initialization does not duplicate an in-flight attempt',async()=>{
  const {context,state}=setup();state.chatViewReady=false;let complete,calls=0;
  context.loadStatus=async()=>syncResult().status;
  context.loadChats=()=>{calls++;return new Promise(resolve=>complete=resolve);};
  const initializing=context.initializeChatView();await context.initializeChatView();await context.pollSyncedMessages();
  assert.equal(calls,1);complete(true);await initializing;assert.equal(state.chatViewReady,true);
});

test('a failed initial chat list is fetched again after the service recovers',async()=>{
  const {context,state}=setup();state.chatViewReady=false;let retry,attempts=0;
  context.window.setTimeout=callback=>{retry=callback;return 1;};
  context.loadStatus=async()=>context.renderStatus(syncResult().status);
  context.loadChats=async()=>{if(++attempts===1)throw new Error('service restarting');state.chats=[{id:'a'}];return true;};
  context.openChat=async id=>{state.activeChat={id};};
  await context.initializeChatView();assert.equal(state.chatViewReady,false);assert.equal(state.activeChat,null);
  await retry();assert.equal(attempts,2);assert.equal(state.chatViewReady,true);assert.equal(state.activeChat.id,'a');
});

test('read requests time out during response-body loading and always clear their timer',async()=>{
  const {context}=setup();let timeout,cleared=0,options,bodyStarted;
  const readingBody=new Promise(resolve=>bodyStarted=resolve);
  context.window.setTimeout=(callback,ms)=>{timeout=callback;assert.equal(ms,15000);return 7;};
  context.window.clearTimeout=id=>{assert.equal(id,7);cleared++;};
  context.fetch=async(url,input)=>{options=input;return {ok:true,json:()=>new Promise((resolve,reject)=>{
    input.signal.addEventListener('abort',()=>reject(new Error('abort')));
    bodyStarted();
  })};};
  const request=context.getJSON('/api/status');await readingBody;timeout();
  await assert.rejects(request,/连接服务超时/);assert.equal(options.signal.aborted,true);assert.equal(cleared,1);
  assert.equal(options.cache,'no-store');
});

test('first load is a 100-message cursor request and a stale chat cannot overwrite the selection', async()=>{
  const {context,state,get}=setup(); const pending=[];
  context.getJSON=url=>new Promise(resolve=>pending.push({url,resolve}));
  const a=context.openChat('a'); const b=context.openChat('b');
  assert.match(pending[0].url,/limit=100/); assert.doesNotMatch(pending[0].url,/offset/);
  pending[1].resolve(page('b')); await b;
  pending[0].resolve(page('a')); await a;
  assert.equal(state.activeChat.id,'b'); assert.equal(get('#chatTitle').textContent,'b');
});

test('double older clicks only issue one request and preserve existing message DOM', async()=>{
  const {context,state,get}=setup(); context.getJSON=async()=>page('a',[101,102]); await context.openChat('a');
  const writes=get('#messagePane').rewrites; let finish, calls=0, url;
  context.getJSON=value=>{calls++;url=value;return new Promise(resolve=>finish=resolve);};
  const first=context.loadOlder(); await context.loadOlder();
  assert.equal(calls,1); assert.match(url,/before=a-101/);
  finish(page('a',[99,100])); await first;
  assert.equal(state.currentMessages.length,4); assert.equal(get('#messagePane').rewrites,writes);
  assert.equal(get('#messageEntries').inserts.at(-1)[0],'afterbegin');
});

test('new messages append without rewriting existing DOM or duplicating IDs',async()=>{
  const {context,state,get}=setup(); context.getJSON=async()=>page(); await context.openChat('a');
  const writes=get('#messagePane').rewrites; let url;
  context.getJSON=async value=>{url=value;return page('a',[2,3]);};
  await context.loadNewer();
  assert.match(url,/after=a-2/); assert.equal(state.currentMessages.length,3);
  assert.equal(get('#messagePane').rewrites,writes);
  assert.equal(get('#messageEntries').inserts.at(-1)[0],'beforeend');
  assert.equal(state.afterCursor,'a-3');
});

test('older request completed after switching chats is ignored',async()=>{
  const {context,state}=setup(); context.getJSON=async()=>page('a',[101]); await context.openChat('a');
  let finish;context.getJSON=()=>new Promise(resolve=>finish=resolve);const older=context.loadOlder();
  context.getJSON=async()=>page('b',[20]);await context.openChat('b');
  finish(page('a',[100])); await older;
  assert.equal(state.currentMessages[0].id,'b-20');assert.equal(state.olderLoading,false);
});

test('an empty delta keeps the cursor and retries failures without clearing the conversation',async()=>{
  const {context,state,get}=setup();context.getJSON=async()=>page();await context.openChat('a');
  const writes=get('#messagePane').rewrites;
  context.getJSON=async()=>page('a',[]);await context.loadNewer();assert.equal(state.afterCursor,'a-2');
  context.getJSON=async()=>{throw new Error('offline');};await context.loadOlder();
  assert.equal(state.olderLoading,false);assert.equal(get('#loadOlderBtn').disabled,false);
  assert.equal(get('#messagePane').rewrites,writes);assert.equal(state.currentMessages.length,2);
});

test('synchronization does not jump away when reading older messages',async()=>{
  const {context,state,get}=setup();context.getJSON=async()=>page();await context.openChat('a');
  state.chats=[{...state.activeChat,total_messages:201,last_ts:200}];get('#messagePane').scrollTop=100;
  context.getJSON=()=>{throw new Error('should not fetch while reading history');};
  await context.refreshChatMessages();
  assert.equal(get('#latestMessagesBtn').hidden,false);assert.equal(get('#messagePane').scrollTop,100);
});

test('status timestamps alone do not reload chats',async()=>{
  const {context,state}=setup();state.syncRevision='4';let chats=0;
  context.loadStatus=async()=>({last_synced_at:'new successful check',sync_revision:4});
  context.loadChats=async()=>chats++;await context.pollSyncedMessages();assert.equal(chats,0);
});

test('voice actions use stable IDs after history is prepended',()=>{
  const {context}=setup();
  const html=context.renderMessageBody({media:{kind:'voice',can_transcribe:true}},'a-102');
  assert.match(html,/data-message-id="a-102"/);assert.doesNotMatch(html,/data-message-index/);
});

test('sidebar shows preview labels and never substitutes a message count',()=>{
  const {context,state,get}=setup();
  state.chats=[{id:'a',title:'Friend',summary:'[表情包]',total_messages:10823},
               {id:'b',title:'Other',summary:' ',total_messages:130}];
  context.renderChats();
  assert.match(get('#chatList').innerHTML,/\[表情包\]/);
  assert.match(get('#chatList').innerHTML,/\[暂无预览\]/);
  assert.doesNotMatch(get('#chatList').innerHTML,/10823 条消息|130 条消息/);
});

test('forward and backward requests cannot race while evicting opposite window edges',async()=>{
  const {context,state}=setup();context.getJSON=async()=>page();await context.openChat('a');
  let finish,calls=0;context.getJSON=()=>{calls++;return new Promise(resolve=>finish=resolve);};
  const older=context.loadOlder();await context.loadNewer();assert.equal(calls,1);
  finish(page('a',[0]));await older;
  const newer=context.loadNewer();await context.loadOlder();assert.equal(calls,2);
  finish(page('a',[3]));await newer;assert.equal(state.newerLoading,false);
});

test('the moving window keeps 1000 messages and both directions remain reachable',async()=>{
  const {context,state}=setup();
  context.getJSON=async()=>page('a',Array.from({length:1000},(_,i)=>i+1));await context.openChat('a');
  context.getJSON=async()=>page('a',Array.from({length:100},(_,i)=>i+1001));await context.loadNewer();
  assert.equal(state.currentMessages.length,1000);assert.equal(state.beforeCursor,'a-101');
  assert.equal(state.afterCursor,'a-1100');assert.equal(state.hasMoreBefore,true);
  context.getJSON=async()=>page('a',Array.from({length:100},(_,i)=>i+1));await context.loadOlder();
  assert.equal(state.currentMessages.length,1000);assert.equal(state.beforeCursor,'a-1');
  assert.equal(state.afterCursor,'a-1000');assert.equal(state.hasMoreAfter,true);
});

test('window eviction is deferred if it would remove the reading anchor',async()=>{
  const {context,state}=setup();
  context.getJSON=async()=>page('a',Array.from({length:1100},(_,i)=>i+1));await context.openChat('a');
  context.trimMessageWindow('start',{entry:{dataset:{messageId:'a-1'}}});
  assert.equal(state.currentMessages.length,1100);
  context.trimMessageWindow('start',{entry:{dataset:{messageId:'a-1100'}}});
  assert.equal(state.currentMessages.length,1000);
});
