const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

const app = fs.readFileSync(path.join(__dirname, '../web/app.js'), 'utf8');
function context() {
  const ctx = { escapeHtml: value => String(value).replace(/[&<>"']/g, c => ({'&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;'}[c])) };
  vm.createContext(ctx);
  vm.runInContext(app.slice(app.indexOf('function qaSourceMeta('), app.indexOf('function renderQaResponseMeta(')), ctx);
  vm.runInContext(app.slice(app.indexOf('function renderQaSources('), app.indexOf('function setQaProgress(')), ctx);
  return ctx;
}
function message() {
  return { role: 'assistant', content: 'Legacy text', answer_data: {paragraphs: [
    {kind: 'answer', text: 'Add demo links.', source_refs: [13]},
  ]}, sources: Array.from({length: 13}, (_, i) => ({chat_type: i === 12 ? 'private' : 'group',
    chat_title: i === 12 ? 'Lin' : 'Internships', sender: 'Lin', time: '10:00', text: 'Original message'})) };
}

test('citation 13 uses private source metadata and can expand the original message', () => {
  const html = context().renderQaAnswer(message());
  assert.match(html, /\[13\] · 私聊 · Lin · 10:00 · Lin/);
  assert.match(html, /<details class="qa-citation">/);
  assert.match(html, /Original message/);
  assert.doesNotMatch(html, /群聊|Internships/);
});

test('source metadata comes from retrieved data, not extra model fields', () => {
  const msg = message();
  msg.answer_data.paragraphs[0].chat_title = 'Wrong group';
  const html = context().renderQaAnswer(msg);
  assert.doesNotMatch(html, /Wrong group/);
  assert.match(html, /私聊 · Lin/);
});

test('legacy and invalid structured answers fall back to escaped plain text', () => {
  const ctx = context();
  const msg = message();
  msg.answer_data.paragraphs[0].source_refs = [14];
  assert.match(ctx.renderQaAnswer(msg), /Legacy text/);
  assert.doesNotMatch(ctx.renderQaAnswer(msg), /qa-citation/);
  assert.match(ctx.renderQaAnswer({role: 'assistant', content: '<img>'}), /&lt;img&gt;/);
});

test('answer, source title and original message cannot inject HTML', () => {
  const msg = message();
  msg.answer_data.paragraphs[0].text = '<script>alert(1)</script>';
  msg.sources[12].chat_title = '<img src=x onerror=alert(1)>';
  msg.sources[12].text = '<iframe>';
  const html = context().renderQaAnswer(msg);
  assert.doesNotMatch(html, /<script|<img|<iframe/);
  assert.match(html, /&lt;script&gt;/);
  assert.match(html, /&lt;iframe&gt;/);
});

test('source list numbers match paragraph references without truncation', () => {
  const html = context().renderQaSources(message());
  assert.match(html, /\[1\] · 群聊/);
  assert.match(html, /\[13\] · 私聊/);
  assert.equal((html.match(/class="qa-source"/g) || []).length, 13);
});

test('missing evidence renders without invented citations', () => {
  const msg = message();
  msg.answer_data.paragraphs = [{kind: 'limitation', text: 'No deadline found.', source_refs: []}];
  assert.doesNotMatch(context().renderQaAnswer(msg), /qa-citation/);
});

test('done event retains structured content before any history reload', () => {
  const ctx = context();
  Object.assign(ctx, {state: {}, renderQaMessages() {}, setQaProgress() {}});
  vm.runInContext(app.slice(app.indexOf('function handleQaStreamEvent('), app.indexOf('function renderQaMessages(')), ctx);
  const result = {};
  const data = message();
  ctx.handleQaStreamEvent({event: 'done', processing_ms: 10, answer: data.content,
    answer_data: data.answer_data, sources: data.sources}, result);
  assert.deepEqual(result.answer_data, data.answer_data);
  assert.match(ctx.renderQaAnswer({...result, role: 'assistant'}), /\[13\] · 私聊/);
});
