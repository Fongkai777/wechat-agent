const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const app = fs.readFileSync(require('node:path').join(__dirname, '../web/app.js'), 'utf8');

function context(confirmed = true) {
  const message = {role:'assistant', turn_id:'a'.repeat(32), content:'interrupted', error:'timeout',
    resumable:true, retry_confirmation_required:true, processing_ms:1200};
  const calls = [];
  const ctx = {state:{qaMessages:[message], activeQaConversation:{id:'one'}}, AbortController,
    qaAskBtn:{}, qaTitle:{}, window:{confirm:()=>confirmed, WechatJobs:{run:async(...args)=>calls.push(args)}},
    escapeHtml:x=>String(x).replace(/"/g, '&quot;'), formatElapsed:x=>String(x),
    renderQaMessages(){}, setQaProgress(){}, startQaTimer(){}, stopQaTimer(){}, makeClientId:()=> 'fixture',
    getJSON:async()=>({conversation:{id:'one', messages:[message]}})};
  vm.createContext(ctx);
  vm.runInContext(app.slice(app.indexOf('function renderQaResponseMeta('), app.indexOf('function renderQaSources(')), ctx);
  return {ctx, message, calls};
}

test('only failed resumable answers show continue, not legacy, complete or stopped answers', () => {
  const {ctx, message} = context();
  assert.match(ctx.renderQaResponseMeta(message), /继续回答/);
  assert.match(ctx.renderQaResponseMeta(message), /已中断/);
  assert.doesNotMatch(ctx.renderQaResponseMeta({...message, stopped:true}), /继续回答/);
  assert.doesNotMatch(ctx.renderQaResponseMeta({...message, resumable:false}), /继续回答/);
});

test('declining unknown-charge confirmation sends no request', async () => {
  const {ctx, message, calls} = context(false);
  await ctx.resumeQaAnswer(message.turn_id);
  assert.equal(calls.length, 0);
  assert.equal(ctx.state.qaMessages.length, 1);
});

test('confirmation resumes the same turn without adding messages or resending history', async () => {
  const {ctx, message, calls} = context();
  await ctx.resumeQaAnswer(message.turn_id);
  assert.equal(calls.length, 1);
  assert.equal(calls[0][0], '/api/qa/resume');
  assert.deepEqual(JSON.parse(JSON.stringify(calls[0][1])), {
    conversation_id:'one', turn_id:message.turn_id, confirm_retry:true,
  });
  assert.equal(ctx.state.qaMessages.length, 1);
  assert.equal(ctx.state.qaStreamController, null);
});

test('active execution prevents a second resume request', async () => {
  const {ctx, message, calls} = context();
  ctx.state.qaStreamController = new AbortController();
  await ctx.resumeQaAnswer(message.turn_id);
  assert.equal(calls.length, 0);
});
