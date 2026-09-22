// Isolated browser fixture: API calls and storage never touch real chat/model data.
const fs = require('node:fs');
const path = require('node:path');
const root = path.join(__dirname, '..');
const fixture = `
<script>
(() => {
  const store = new Map();
  Object.defineProperty(window, 'localStorage', {value: {
    getItem: key => store.get(key) ?? null, setItem: (key,value) => store.set(key,String(value)),
    removeItem: key => store.delete(key),
  }});
  let schedule = JSON.parse(sessionStorage.getItem('rag-schedule-fixture') || 'null') || {
    enabled:false, interval_value:1, interval_unit:'days', next_run_at:null, last_status:'', last_finished_at:null,
  };
  if (new URLSearchParams(location.search).has('dns_error')) {
    schedule = {...schedule,enabled:true,next_run_at:Date.now()/1000+3600,last_status:'failed',
      last_finished_at:Date.now()/1000,last_error:'Embedding 请求失败：[Errno 8] nodename nor servname provided, or not known'};
  }
  const params = new URLSearchParams(location.search);
  if (params.has('manual_success')) {
    schedule = {...schedule,enabled:true,next_run_at:Date.now()/1000+3600,last_status:'failed',last_finished_at:Date.now()/1000-600,
      last_error:'old DNS failure',latest_run:{id:'fixture-manual',trigger:'manual',status:'completed',finished_at:Date.now()/1000,error:''}};
  }
  let historyReads = 0;
  const conversations = params.has('qa_retry') ? [{id:'history-fixture',title:'已保存的问答',message_count:2}] : [];
  const nativeFetch = window.fetch.bind(window);
  const rag = {
    ok:true, maintenance:{running:false}, person_index:{ready:true,stats:{people:4189,messages:458283}},
    search_index:{ready:true,message_count:458283,people_count:4189,chat_count:540},
    semantic_index:{ready:true,enabled:true,configured:true,api_key_set:true,chunk_count:26424,mapped_message_count:458283},
    retrieval_config:{embedding_enabled:true,embedding_model:'text-embedding-3-small',rerank_enabled:true},
  };
  window.fetch = async (url, options={}) => {
    const route = new URL(String(url),location.href).pathname;
    if (route.startsWith('/static/')) return nativeFetch(url,options);
    let body = {ok:true};
    if (route === '/api/rag/schedule') {
      if (options.method === 'POST') {
        const data = JSON.parse(options.body);
        schedule = {...schedule,...data,next_run_at:data.enabled ? Date.now()/1000 + data.interval_value*(data.interval_unit==='hours'?3600:86400) : null};
        sessionStorage.setItem('rag-schedule-fixture',JSON.stringify(schedule));
      }
      body = {ok:true,schedule};
    } else if (options.method === 'POST') throw Error('Fixture blocks all execution requests');
    else if (route === '/api/rag/status') body=rag;
    else if (route === '/api/chats') body={chats:[],total:0};
    else if (route === '/api/jobs') body={ok:true,jobs:[]};
    else if (route === '/api/goals') body={ok:true,goals:[]};
    else if (route === '/api/qa/conversations') {
      if (params.has('qa_retry') && ++historyReads <= 2) {
        console.warn('Fixture history request failed; automatic retry expected');
        return new Response(JSON.stringify({error:'测试连接中断'}),{status:503,headers:{'Content-Type':'application/json'}});
      }
      body={ok:true,conversations};
    }
    else if (route === '/api/qa/conversation') body={ok:true,conversation:{...conversations[0],messages:[
      {role:'user',content:'这是一条已保存的测试问题。'}, {role:'assistant',content:'这是对应的历史回答。'}]}};
    else if (route === '/api/qa/index_status') body=rag.person_index;
    else if (route === '/api/voice/status') body={ok:true,total:868,transcribed:868,pending:0};
    else if (route === '/api/llm/config') body={ok:true,config:{qa:{},voice:{},embedding:{},rerank:{}}};
    else if (route === '/api/status') body={ok:true,total_chats:0,total_messages:0,sync_interval:0};
    return new Response(JSON.stringify(body),{status:200,headers:{'Content-Type':'application/json'}});
  };
})();
</script>`;
const html = fs.readFileSync(path.join(root,'web/index.html'),'utf8').replace('<head>', '<head>'+fixture);
fs.writeFileSync(path.join(root,'web/.rag-schedule-preview.html'),html);
