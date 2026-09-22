const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const web = path.join(__dirname, '../web');
const html = fs.readFileSync(path.join(web, 'index.html'), 'utf8');
const app = fs.readFileSync(path.join(web, 'app.js'), 'utf8');

test('workbench navigation preserves all five view targets', () => {
  const buttons = [...html.matchAll(/<button class="main-tab[^"]*" data-view="([^"]+)">([^<]+)<\/button>/g)];
  assert.deepEqual(buttons.map(match => match[1]), ['raw', 'qa', 'goals', 'rag', 'settings']);
  assert.equal(buttons[4][2], '模型配置');
  for (const [, view] of buttons) assert.match(html, new RegExp(`id="${view}View"`));
  const ids = [...html.matchAll(/\bid="([^"]+)"/g)].map(match => match[1]);
  assert.equal(new Set(ids).size, ids.length);
});

test('sticky settings action remains a submit button inside the model form', () => {
  const form = html.match(/<form id="llmConfigForm"[\s\S]*?<\/form>/)[0];
  assert.match(form, /<footer class="settings-actions"><button id="saveLlmConfigBtn" type="submit">/);
  assert.equal((form.match(/class="config-section"/g) || []).length, 5);
  for (const profile of ['voice', 'qa', 'task', 'embedding', 'rerank']) {
    assert.match(form, new RegExp(`id="${profile}ApiKey" type="password"`));
    assert.match(form, new RegExp(`aria-labelledby="${profile}ConfigHeading"`));
    assert.match(form, new RegExp(`id="${profile}ConfigHeading"`));
  }
  assert.equal((form.match(/class="config-fields"/g) || []).length, 5);
});

test('conversation action icons use local assets and keep accessible labels', () => {
  for (const [label, icon] of [['重命名', 'pencil'], ['删除', 'trash-2']]) {
    assert.match(app, new RegExp(`aria-label="${label}" title="${label}"><img src="/static/icons/${icon}.svg"`));
    assert.ok(fs.existsSync(path.join(web, 'icons', `${icon}.svg`)));
  }
});

test('model form loads and saves task settings independently without exposing saved keys', async () => {
  const context = {Number, settingsStatus: {}, saveLlmConfigBtn: {}, state: {}};
  const ids = [...html.matchAll(/<input id="([^"]+)"/g)].map(match => match[1]);
  for (const id of ids) context[id] = {value: '', placeholder: '', checked: false};
  let saved;
  context.postJSON = async (url, payload) => {
    assert.equal(url, '/api/llm/config');
    saved = payload;
    return {config: payload};
  };
  vm.createContext(context);
  vm.runInContext(app.slice(app.indexOf('function renderLLMConfig('), app.indexOf('function renderVoiceStatus(')), context);
  vm.runInContext(app.slice(app.indexOf('async function saveLLMConfig('), app.indexOf('async function prepareRag(')), context);
  context.state.llmConfig = {qa: {model: 'qa-only'}, task: {
    model: 'task-only', base_url: 'https://task.test/v1', api_key_set: true,
    api_key_preview: 'fix...test', api_key_env: 'TASK_KEY',
  }};
  context.renderLLMConfig(context.state.llmConfig);
  assert.equal(context.taskModel.value, 'task-only');
  assert.equal(context.qaModel.value, 'qa-only');
  assert.equal(context.taskApiKey.value, '');
  assert.match(context.taskApiKey.placeholder, /fix\.\.\.test/);
  await context.saveLLMConfig({preventDefault() {}});
  assert.equal(saved.task.model, 'task-only');
  assert.equal(saved.qa.model, 'qa-only');
  assert.equal(saved.task.api_key, '');
  assert.equal(saved.task.api_key_env, 'TASK_KEY');
  assert.equal(context.settingsStatus.textContent, '已保存');
  context.state.llmConfig = {qa: {model: 'legacy'}};
  context.renderLLMConfig(context.state.llmConfig);
  assert.equal(context.saveLlmConfigBtn.disabled, true);
  saved = null;
  await context.saveLLMConfig({preventDefault() {}});
  assert.equal(saved, null);
  assert.match(context.settingsStatus.textContent, /重启服务/);
});
