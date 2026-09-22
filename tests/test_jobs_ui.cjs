const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const { webcrypto } = require('node:crypto');

function setup(fetch) {
  const values = new Map();
  const context = vm.createContext({
    window:{}, fetch, crypto:webcrypto, AbortController,
    localStorage:{getItem:k=>values.get(k),setItem:(k,v)=>values.set(k,v)},
    setTimeout:(callback,delay)=>delay===15000?0:setTimeout(callback,0),
    clearTimeout:id=>clearTimeout(id),
  });
  vm.runInContext(fs.readFileSync(require('node:path').join(__dirname,'../web/jobs.js'),'utf8'),context);
  return {jobs:context.window.WechatJobs,values};
}
const response=data=>({ok:true,status:200,json:async()=>data});
const done=id=>response({ok:true,job:{id,status:'completed',cursor:2,
  events:[{event:'progress',seq:1,message:'working'},{event:'done',seq:2,ok:true,answer:'answer'}],
  result:{event:'done',seq:2,ok:true,answer:'answer'}}});

test('lost start acknowledgement looks up the same execution ID without resubmission',async()=>{
  const ids=[];
  const {jobs}=setup(async(url,options)=>{
    if(url==='/api/jobs/start'){
      const data=JSON.parse(options.body);ids.push(data.request_id);
      throw new TypeError('network disconnected after acceptance');
    }
    return done(ids[0]);
  });
  const result=await jobs.run('/api/qa_stream',{question:'test'});
  assert.equal(result.answer,'answer');
  assert.equal(ids.length,1);
});

test('temporary disconnect resumes polling without a new model submission or stop',async()=>{
  const requests=[];let reads=0;
  const {jobs}=setup(async(url)=>{
    requests.push(url);
    if(url==='/api/jobs/start')return response({ok:true,job:{id:'one',kind:'qa'}});
    if(++reads===1)throw new TypeError('offline');
    return done('one');
  });
  assert.equal((await jobs.run('/api/qa_stream',{})).ok,true);
  assert.equal(requests.filter(x=>x==='/api/jobs/start').length,1);
  assert.equal(requests.some(x=>x.includes('/stop')),false);
});

test('explicit stop is sent to the server and is not reported complete until acknowledged',async()=>{
  const controller=new AbortController();let stopCount=0;
  const {jobs}=setup(async(url)=>{
    if(url==='/api/jobs/stop'){stopCount++;return response({ok:true});}
    return response({ok:true,job:{id:'one',status:'cancelled',cursor:0,events:[],result:null}});
  });
  controller.abort();
  await assert.rejects(jobs.watch({id:'one',kind:'qa'},{signal:controller.signal}),{name:'AbortError'});
  assert.equal(stopCount,1);
});

test('refresh recovery only observes remembered or active jobs, never reruns completed operations',async()=>{
  const requests=[];
  const {jobs,values}=setup(async(url)=>{
    requests.push(url);
    if(url==='/api/jobs')return response({ok:true,jobs:[{id:'finished',status:'completed'},{id:'active',status:'running'}]});
    return done('finished');
  });
  values.set('wechat.backgroundJobs.v1',JSON.stringify({finished:{id:'finished',kind:'/api/qa_stream'}}));
  assert.equal((await jobs.recoverable()).length,2);
  await jobs.watch({id:'finished',kind:'/api/qa_stream'});
  const recovered=await jobs.recoverable();
  assert.deepEqual(Array.from(recovered,x=>x.id),['active']);
  assert.equal(requests.some(x=>x.includes('/start')||x.includes('/stop')),false);
});

test('completion is emitted once and only after the server has finalized its state',async()=>{
  let reads=0;const events=[];
  const {jobs}=setup(async()=>++reads===1?response({ok:true,job:{id:'one',status:'running',cursor:1,
    events:[{event:'done',ok:true,seq:1}],result:{event:'done',ok:true,seq:1}}}):done('one'));
  await jobs.watch({id:'one'},{onEvent:event=>events.push(event.event)});
  assert.equal(events.filter(x=>x==='done').length,1);
  assert.equal(reads,2);
});

test('service restart is an explicit failure, not a paid automatic retry',async()=>{
  const requests=[];
  const {jobs}=setup(async(url)=>{requests.push(url);return {ok:false,status:404,json:async()=>({error:'服务已重启'})};});
  await assert.rejects(jobs.watch({id:'missing'}),/服务已重启/);
  assert.equal(requests.length,1);
});

test('stopping before submission never starts a paid job',async()=>{
  const controller=new AbortController();controller.abort();
  let calls=0;
  const {jobs}=setup(async()=>{calls++;return done('unused');});
  await assert.rejects(jobs.run('/api/qa_stream',{}, {signal:controller.signal}),{name:'AbortError'});
  assert.equal(calls,0);
});
