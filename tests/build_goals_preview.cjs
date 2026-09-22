// Isolated visual fixture: real UI assets, simulated API, no model requests.
const fs = require('node:fs');
const path = require('node:path');
const { pathToFileURL } = require('node:url');
const root = path.resolve(__dirname, '..');
const assetRoot = pathToFileURL(path.join(root, 'web') + '/').href;
let html = fs.readFileSync(path.join(root, 'web/index.html'), 'utf8');
html = html.replace(/<script src="\/static\/[^"\n]+"><\/script>/g, '');
html = html.replace(/<script id="appBootstrap">[\s\S]*?<\/script>/, '');
html = html.replace('/static/styles.css', assetRoot + 'styles.css');
html = html.replaceAll('/static/', assetRoot);
const script = fs.readFileSync(path.join(root, 'web/goals.js'), 'utf8').replaceAll('/static/', assetRoot);
const now = Math.floor(Date.now() / 1000);
const run = {id:'run-one',started_at:now-90,finished_at:now-40,status:'completed',result:'发现 2 条待确认的私聊。\n\n林同学询问明天是否有时间讨论项目。建议回复草稿：\n“明天下午可以，我们三点聊？” [1]\n\n另一条对话已经自然结束，不需要额外回复。',model:'问答模型',usage:{total_tokens:1320},steps:[{tool:'检查私聊',count:2}],sources:[{reference:1,chat_id:'sample',chat_title:'林同学',time:'2026-09-13 10:30',sender:'林同学',text:'明天有时间讨论一下项目吗？'}]};
const goals = [
  {id:'sample-replies',title:'待回复私聊',prompt:'检查今天有无忘记回复的私聊，结合上下文判断是否需要回复，并给出自然的回复草稿。',cadence:'hourly',enabled:true,next_run_at:now+3600,latest_run:run},
  {id:'sample-internships',title:'新增实习信息',prompt:'关注新加坡本地新增的产品和 AI 实习机会，整理公司、岗位、地点、申请方式和截止时间，去掉重复消息。',cadence:'daily',enabled:true,next_run_at:now+86400,latest_run:null},
  {id:'sample-food',title:'美食推荐',prompt:'帮我查找聊天中有无新的餐厅和美食推荐，整理推荐菜、位置和推荐理由。',cadence:'daily',enabled:false,next_run_at:null,latest_run:null},
];
const bootstrap = `
let fixtureGoals = ${JSON.stringify(goals)};
const fixtureRun = ${JSON.stringify(run)};
window.fetch = async (url, options = {}) => {
  const input = options.body ? JSON.parse(options.body) : {};
  if (url === '/api/goals/save') fixtureGoals = [...fixtureGoals.filter(g=>g.id!==input.id), {...input,id:input.id||'fixture-new',next_run_at:Date.now()/1000+3600}];
  else if (url === '/api/goals/toggle') fixtureGoals=fixtureGoals.map(g=>g.id===input.id?{...g,enabled:input.enabled}:g);
  else if (url === '/api/goals/delete') fixtureGoals=fixtureGoals.filter(g=>g.id!==input.id);
  else if (!url.startsWith('/api/goals')) throw new Error('Fixture blocks external requests');
  return {ok:true,json:async()=>({ok:true,goals:fixtureGoals,runs:[fixtureRun],scheduler_running:true})};
};
for (const tab of document.querySelectorAll('.main-tab')) tab.addEventListener('click',()=>{
  for (const item of document.querySelectorAll('.main-tab')) item.classList.toggle('active',item===tab);
  for (const view of document.querySelectorAll('.view')) view.classList.toggle('active',view.id===tab.dataset.view+'View');
  document.dispatchEvent(new CustomEvent('viewchange',{detail:tab.dataset.view}));
});`;
html = html.replace('</body>', `<script>${bootstrap}</script><script>${script}</script><script>document.querySelector('[data-view="goals"]').click();</script></body>`);
const target = '/private/tmp/wechat-agent-goals-preview.html';
fs.writeFileSync(target, html);
console.log(target);
