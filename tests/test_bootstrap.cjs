const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const html=fs.readFileSync(path.join(__dirname,'../web/index.html'),'utf8');
const code=html.match(/<script id="appBootstrap">([\s\S]*?)<\/script>/)[1];

function setup(assets=[]) {
  const scripts=[],timers=new Map(),events={},elements=new Map();let nextTimer=0;
  const document={
    body:{appendChild:script=>scripts.push(script)},
    createElement:()=>({remove(){this.removed=true;}}),
    querySelector:selector=>{if(!elements.has(selector))elements.set(selector,{textContent:'',dataset:{}});return elements.get(selector);},
    querySelectorAll:()=>assets,
    addEventListener:(name,callback)=>events[name]=callback,
  };
  const context=vm.createContext({document,window:{addEventListener:(name,callback)=>events[name]=callback},
    setTimeout:(callback,ms)=>{timers.set(++nextTimer,{callback,ms});return nextTimer;},clearTimeout:id=>timers.delete(id)});
  vm.runInContext(code,context);
  const tick=()=>{const [id,item]=timers.entries().next().value;timers.delete(id);item.callback();return item.ms;};
  return {scripts,timers,events,elements,tick};
}

test('inline bootstrap retries a missing script and resumes the dependency chain once',()=>{
  const {scripts,elements,events,tick}=setup();
  assert.equal(scripts[0].src,'/static/sidebar.js');
  events.online();assert.equal(scripts.length,1);
  scripts[0].onerror();assert.equal(scripts[0].removed,true);assert.equal(scripts[0].onload,null);
  assert.equal(elements.get('#syncStatus').textContent,'页面资源加载失败，正在重试');
  assert.equal(tick(),1000);assert.equal(scripts[1].src,'/static/sidebar.js');
  scripts[1].onload();assert.equal(scripts[2].src,'/static/jobs.js');
  scripts[2].onload();assert.equal(scripts[3].src,'/static/app.js');
  scripts[3].onerror();tick();assert.equal(scripts[4].src,'/static/app.js');
  scripts[4].onload();assert.equal(scripts[5].src,'/static/goals.js');
  scripts[5].onload();events.online();assert.equal(scripts.length,6);
  assert.equal(scripts.filter(script=>script.src==='/static/jobs.js').length,1);
});

test('script retry backs off without reloading the page or retrying successful scripts',()=>{
  const {scripts,tick,events,timers}=setup();
  for (const delay of [1000,2000,4000,8000,15000,15000]) {
    scripts.at(-1).onerror();assert.equal(tick(),delay);
  }
  scripts.at(-1).onerror();events.online();assert.equal(timers.size,0);
  assert.equal(scripts.at(-1).src,'/static/sidebar.js');
  assert.doesNotMatch(code,/location\.reload|fetch\(|\/api\//);
});

test('failed local icons and stylesheets recover but private media is never retried by the loader',()=>{
  const asset=(tagName,url)=>({tagName,rel:'stylesheet',dataset:{},isConnected:true,complete:true,naturalWidth:0,sheet:null,
    getAttribute(){return url;},setAttribute(key,value){this.changed={key,value};}});
  const icon=asset('IMG','/static/icons/search.svg');
  const css=asset('LINK','/static/styles.css');
  const photo=asset('IMG','/media?file=private');
  const {tick,events,timers}=setup([icon,css]);
  events.error({target:photo});events.error({target:icon});assert.equal(timers.size,2);
  tick();tick();assert.equal(icon.changed.value,'/static/icons/search.svg?asset_retry=1');
  assert.equal(css.changed.value,'/static/styles.css?asset_retry=1');assert.equal(photo.changed,undefined);
  events.error({target:icon});icon.isConnected=false;const previous=icon.changed;tick();assert.equal(icon.changed,previous);
});
