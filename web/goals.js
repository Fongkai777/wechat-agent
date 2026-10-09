(() => {
  const $ = (id) => document.getElementById(id);
  const escape = (text) => String(text ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const date = (ts) => ts ? new Date(ts * 1000).toLocaleString('zh-CN', {month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'}) : '尚未执行';
  const statusText = {running:'执行中',completed:'已完成',failed:'执行失败',cancelled:'已停止',interrupted:'执行中断'};
  const rangeUnits = {days:'天',weeks:'周',months:'月'};
  const rangeLimits = {days:90,weeks:12,months:3};
  const intervalText = goal => `每 ${Number(goal.interval_value ?? 1)} ${(goal.interval_unit || (goal.cadence === 'daily' ? 'days' : 'hours')) === 'days' ? '天' : '小时'}`;
  const rangeText = goal => goal.range_type === 'recent_days' ? `最近 ${Number(goal.range_value ?? goal.range_days)} ${rangeUnits[goal.range_unit || 'days'] || '天'}` : '当日';
  let goals = [], runs = [], editing = null, historyId = null, selectedRunId = null, loading = false, lastSignature = '';
  let editorOpen = false, formBusy = false;
  let renderedRunSignature = '';
  let timingSupported = false;
  let manualSupported = false, pendingGoalId = null;
  const resultCache = new Map();
  let skillsSupported = false, skillText = '', skillPrompt = '', skillBusy = false;
  let skillFile = 'workflow.py';

  async function request(path, data) {
    const response = await fetch(path, data ? {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)} : {});
    const payload = await response.json();
    if (!response.ok || !payload.ok) throw new Error(payload.error || '请求失败');
    return payload;
  }

  function error(element, message) {
    element.textContent = message || '';
    element.hidden = !message;
  }

  function render() {
    const anyRunning = goals.some(goal => goal.latest_run?.status === 'running');
    const editIndex = editing ? goals.findIndex(goal => goal.id === editing.id) : -1;
    $('goalEditorRow').style.order = String(editIndex + 1);
    $('goalEditorResult').hidden = !editorOpen || !skillsSupported;
    $('goalNewBtn').disabled = editorOpen || formBusy || !!pendingGoalId;
    $('goalCards').innerHTML = goals.length ? goals.map((goal, index) => {
      if (editorOpen && goal.id === editing?.id) return '';
      const run = goal.latest_run;
      const running = run?.status === 'running';
      const cancelling = running && !!run.cancel_requested;
      const pending = pendingGoalId === goal.id;
      const runDisabled = !manualSupported || !!pendingGoalId || cancelling || (!running && (anyRunning || editorOpen));
      const runTitle = !manualSupported ? '请重启原来的 8787 服务以加载手动执行功能' : running ? '停止这次执行' : anyRunning ? '请等待当前任务执行完成' : '立即执行一次';
      const state = running ? 'running' : !goal.enabled ? 'paused' : run?.status === 'failed' ? 'failed' : 'active';
      const label = {running:'检查中',paused:'已暂停',failed:'执行失败',active:'定时开启'}[state];
      return `<div class="goal-row" data-goal-row="${escape(goal.id)}" style="order:${index + 1}"><article class="goal-card" data-goal="${escape(goal.id)}">
        <div class="goal-card-top"><span class="goal-state" data-state="${state}">${label}</span><div class="goal-card-controls">
          <button class="goal-icon goal-delete-icon" data-delete="${escape(goal.id)}" title="删除任务" aria-label="删除 ${escape(goal.title)}" ${running || editorOpen || pendingGoalId?'disabled':''}><img src="/static/icons/trash-2.svg" alt="" /></button>
          <button class="goal-icon" data-edit="${escape(goal.id)}" title="编辑任务" aria-label="编辑 ${escape(goal.title)}" ${running || editorOpen || pendingGoalId?'disabled':''}><img src="/static/icons/pencil.svg" alt="" /></button>
          <input class="goal-toggle" type="checkbox" role="switch" data-toggle="${escape(goal.id)}" ${goal.enabled?'checked':''} title="${goal.enabled?'暂停':'启用'}任务" aria-label="启用 ${escape(goal.title)}" />
        </div></div>
        <h2 class="goal-prompt" title="${escape(goal.prompt)}">${escape(goal.prompt)}</h2>
        <div class="goal-schedule"><img src="/static/icons/clock.svg" alt="" /><span>${intervalText(goal)}</span><span>· 检索${rangeText(goal)}</span><span>下次：${goal.enabled ? date(goal.next_run_at) : '已暂停'}</span></div>
        <footer><span>${run ? `${statusText[run.status] || ''} · ${date(run.started_at)}` : '尚无执行记录'}</span><div class="goal-run-actions">
          ${resumeMarkup(goal.id,run,runDisabled)}
          ${skillsSupported?`<button class="${goal.skill_version?'goal-secondary':'goal-primary'} goal-skill-action" data-skill="${escape(goal.id)}" title="${goal.skill_version?'查看或编辑 Skill':'生成任务 Skill'}" ${running || editorOpen || pendingGoalId?'disabled':''}><img src="/static/icons/${goal.skill_version?'pencil':'plus'}.svg" alt="" />${goal.skill_version?'更新 Skill':'生成 Skill'}</button>`:''}
          <button class="goal-secondary goal-run-button" data-run="${escape(goal.id)}" title="${runTitle}" ${runDisabled?'disabled':''}><img src="/static/icons/${running?'x':'refresh-cw'}.svg" alt="" />${pending?'提交中':cancelling?'停止中':running?'停止执行':'立即执行'}</button>
          </div></footer>
      </article><section class="goal-output" aria-label="${escape(goal.title)}的执行结果">${resultMarkup(goal)}</section></div>`;
    }).join('') : editorOpen ? '' : '<div class="goals-empty"><img src="/static/icons/clock.svg" alt="" /><h2>暂无任务</h2></div>';
  }

  function resultKey(run) {
    return `${run.id}:${run.status}:${run.finished_at || ''}`;
  }

  function resumeMarkup(goalId, run, disabled=false) {
    return run?.resumable ? `<button class="goal-secondary goal-run-button" data-resume="${escape(goalId)}" data-resume-run="${escape(run.id)}" title="沿用这次执行的任务和时间范围" ${disabled?'disabled':''}><img src="/static/icons/refresh-cw.svg" alt="" />恢复执行</button>` : '';
  }

  async function resumeRun(button) {
    if (button.disabled || pendingGoalId || editorOpen || goals.some(g=>g.latest_run?.status==='running')) return;
    const goalId=button.dataset.resume, runId=button.dataset.resumeRun;
    const run=runs.find(r=>r.id===runId) || goals.find(g=>g.id===goalId)?.latest_run;
    if (!run?.resumable) return;
    const message=run.retry_confirmation_required
      ? '上次模型请求结果未知，可能已计费。恢复将重试该请求，可能重复产生费用；已保存的检索结果会保留。继续吗？'
      : '从保存的检查点继续，沿用这次执行原来的任务内容和时间范围；后续模型请求仍可能产生费用。继续吗？';
    if (!confirm(message)) return;
    pendingGoalId=goalId;
    render();
    try {
      await request('/api/goals/resume',{id:goalId,run_id:runId,confirm_retry:!!run.retry_confirmation_required});
      resultCache.delete(goalId);
      await load();
      if ($('goalHistory').open) await loadHistory(goalId);
    } catch(e) { error($('goalsError'),e.message); }
    finally { pendingGoalId=null; render(); }
  }

  function conciseResult(run) {
    const text = String(run.result || '').trim();
    const bullet = /^(?:[-*•]\s+|\d+[.)、]\s+)/;
    const blocks = text.split(/\n\s*\n/);
    const groupIndex = blocks.findIndex(block => block.split('\n').some(line => bullet.test(line.trim())));
    let group = blocks[groupIndex];
    for (let index=groupIndex+1; group && index<blocks.length && bullet.test(blocks[index].trim()); index++) group += '\n' + blocks[index];
    const references = new Set((run.sources || []).map(source=>String(source.reference)));
    const clean = value => value.replace(/[（(]\s*chat_id\s*[:：]\s*[^)）]+[)）]/gi, '')
      .replace(/\[(\d+)\]/g,(match, id)=>references.has(id)?'':match).replace(/ {2,}/g,' ').trim();
    // Older reports contain an overview and several sections; excerpt their first list without rewriting saved text.
    if (!group) return {items:[clean(text)],note:''};
    const items = [];
    let intro = [];
    for (const line of group.split('\n').map(line=>line.trim()).filter(Boolean)) {
      if (bullet.test(line)) items.push(line.replace(bullet,''));
      else if (items.length) items[items.length-1] += '\n' + line;
      else intro.push(line);
    }
    const note = blocks.filter(block => /^(?:注意|覆盖限制|检索限制|范围限制|限制|提醒|说明|提示)[：:(（\s]/.test(block.trim())).join('\n');
    const heading = intro.join(' ');
    return {items:items.map(clean),note:clean(note),intro:/^(?:发现|结果|待处理事项|待回复|需关注|本次发现)[：:\s]*$/.test(heading)?'':clean(heading)};
  }

  function structuredResult(run) {
    const data=run?.result_data;
    if (!data || typeof data.summary!=='string' || !Array.isArray(data.items) || !(data.notice===null || typeof data.notice==='string')) return null;
    if (!data.items.every(item=>item && typeof item.title==='string' && typeof item.detail==='string' &&
      (item.suggestion===null || typeof item.suggestion==='string') && Array.isArray(item.source_refs) && item.source_refs.every(ref=>Number.isInteger(ref) && ref>0))) return null;
    return data;
  }

  function structuredMarkup(data, goalId='', runId='') {
    return `<div class="goal-structured-result"><p class="goal-result-summary">${escape(data.summary)}</p>${data.items.length?`<ul class="goal-result-list">${data.items.map(item=>`<li><div class="goal-result-item-heading"><h3>${escape(item.title)}</h3><span class="goal-result-references">${item.source_refs.map(ref=>`<button class="goal-source-ref" ${goalId?`data-history="${escape(goalId)}" data-history-run="${escape(runId)}"`:''} data-goal-reference="${ref}" title="查看来源 ${ref}" aria-label="查看来源 ${ref}">[${ref}]</button>`).join('')}</span></div><p class="goal-result-description">${escape(item.detail)}</p>${item.suggestion?`<p class="goal-result-suggestion"><span>建议</span>${escape(item.suggestion)}</p>`:''}</li>`).join('')}</ul>`:''}${data.notice?`<p class="goal-result-note">${escape(data.notice)}</p>`:''}</div>`;
  }

  function resultMarkup(goal) {
    const summary = goal.latest_run;
    const entry = resultCache.get(goal.id);
    const cached = summary && entry?.key === resultKey(summary) ? entry : null;
    const run = cached?.data || summary;
    const running = run?.status === 'running';
    const text = running ? run.progress || '准备检索' : run?.result || run?.error || (run ? '本次执行没有返回结果' : '等待首次检查');
    const structured = run?.status==='completed' ? structuredResult(run) : null;
    const concise = !structured && run?.status==='completed' && run.result ? conciseResult(run) : null;
    return `<header class="goal-output-header"><div><h2>最新结果</h2>${run?`<time>${escape(date(run.started_at))}</time>`:''}</div><div class="goal-output-actions"><span class="goal-output-status" data-state="${escape(run?.status || 'idle')}">${statusText[run?.status] || '尚未执行'}</span><button class="goal-history-button" data-history="${escape(goal.id)}" ${run?'':'disabled'}><img src="/static/icons/clock.svg" alt="" />历史记录</button></div></header>
      <div class="goal-output-body ${!run?'goal-output-placeholder':''} ${run?.error?'is-error':''}" tabindex="0" role="region" aria-label="最新结果内容">${structured ? structuredMarkup(structured,goal.id,run.id) : concise ? `${concise.intro?`<p class="goal-result-intro">${escape(concise.intro)}</p>`:''}<ul class="goal-result-list">${concise.items.map(item=>`<li>${escape(item)}</li>`).join('')}</ul>${concise.note?`<p class="goal-result-note">${escape(concise.note)}</p>`:''}` : escape(text)}</div>
      ${summary?.id && !running && (!cached || cached.status==='loading')?'<p class="goal-output-loading" role="status">正在读取完整结果</p>':''}
      ${cached?.status==='error'?`<div class="goal-output-error" role="alert"><span>完整结果读取失败</span><button class="goal-text-button" data-result-retry="${escape(goal.id)}">重试</button></div>`:''}`;
  }

  async function loadLatestResults() {
    const ids = new Set(goals.map(goal=>goal.id));
    for (const id of resultCache.keys()) if (!ids.has(id)) resultCache.delete(id);
    for (const goal of goals) {
      const run = goal.latest_run;
      if (!run?.id || run.status==='running') continue;
      const key = resultKey(run);
      if (resultCache.get(goal.id)?.key === key) continue;
      const entry = {key,status:'loading'};
      resultCache.set(goal.id,entry);
      render();
      try {
        const data = await request(`/api/goals/history?id=${encodeURIComponent(goal.id)}`);
        const full = data.runs.find(item=>item.id===run.id);
        if (!full) throw new Error('执行记录不存在');
        entry.data=full;
        entry.status='ready';
      } catch(e) { entry.status='error'; }
      const current = goals.find(item=>item.id===goal.id)?.latest_run;
      if (!current || resultKey(current)!==key) {
        if (resultCache.get(goal.id)===entry) resultCache.delete(goal.id);
      } else if (resultCache.get(goal.id)===entry) render();
    }
  }

  async function load() {
    if (loading) return;
    loading = true;
    try {
      const data = await request('/api/goals');
      timingSupported = Array.isArray(data.range_units) && ['days','weeks','months'].every(unit=>data.range_units.includes(unit)) && Array.isArray(data.interval_units) && ['hours','days'].every(unit=>data.interval_units.includes(unit));
      manualSupported = data.manual_run_supported === true;
      skillsSupported = data.skills_supported === true;
      goals = data.goals;
      $('goalsMeta').textContent = `${goals.length} 个任务 · ${goals.filter(g=>g.enabled).length} 个已启用 · ${data.scheduler_running?'定时服务运行中':'定时服务未运行'}`;
      error($('goalsError'), data.scheduler_error);
      const signature = JSON.stringify({goals,manualSupported,skillsSupported});
      if (signature !== lastSignature) {
        lastSignature = signature;
        render();
        if ($('goalHistory').open) await loadHistory(historyId);
      }
      await loadLatestResults();
    } catch (e) { error($('goalsError'), e.message); }
    finally { loading = false; }
  }

  function titleFromPrompt(prompt) {
    const chars = Array.from(prompt.replace(/\s+/g, ' '));
    return chars.length > 40 ? chars.slice(0, 40).join('') + '…' : chars.join('');
  }

  function edit(goal = null) {
    if (formBusy || pendingGoalId) return;
    if (editorOpen) { $('goalPrompt').focus(); return; }
    editing = goal;
    editorOpen = true;
    $('goalEditorTitle').textContent = goal ? '编辑任务' : '新增任务';
    $('goalPrompt').value = goal?.prompt || '';
    skillText = goal?.skill_version ? JSON.stringify(goal.skill, null, 2) : '';
    skillPrompt = goal?.prompt || '';
    renderSkillEditor();
    $('goalIntervalValue').value = goal?.interval_value ?? 1;
    $('goalIntervalUnit').value = goal?.interval_unit || (goal?.cadence === 'daily' ? 'days' : 'hours');
    $('goalRangeValue').value = goal?.range_type === 'recent_days' ? goal.range_value ?? goal.range_days ?? 1 : 1;
    $('goalRangeUnit').value = goal?.range_type === 'recent_days' ? goal.range_unit || 'days' : 'days';
    updateRange();
    error($('goalFormError'), '');
    $('goalEditor').hidden = false;
    $('goalEditorRow').hidden = false;
    render();
    $('goalPrompt').focus({preventScroll:true});
    $('goalEditor').scrollIntoView({block:'nearest'});
  }

  function closeEditor() {
    editorOpen = false;
    editing = null;
    $('goalEditor').hidden = true;
    $('goalEditorRow').hidden = true;
    render();
    $('goalNewBtn').focus({preventScroll:true});
  }

  function busy(value) {
    formBusy = value;
    $('goalNewBtn').disabled = editorOpen || formBusy || !!pendingGoalId;
    for (const id of ['goalPrompt','goalIntervalValue','goalIntervalUnit','goalSaveBtn','goalCancelBtn','goalCancelIcon','goalSkillGenerate','goalSkillText','goalSkillFile','goalSkillImport','goalSkillExport','goalSkillValidate']) if ($(id)) $(id).disabled = value;
    updateRange();
  }

  function updateRange() {
    $('goalRangeValue').disabled = formBusy;
    $('goalRangeUnit').disabled = formBusy;
    $('goalRangeValue').max = rangeLimits[$('goalRangeUnit').value];
  }

  function cancelEdit() {
    if (!formBusy) closeEditor();
  }

  function renderSkillEditor() {
    $('goalSkillText').value = skillText;
    $('goalSkillGenerate').innerHTML = `<img src="/static/icons/${skillText?'refresh-cw':'plus'}.svg" alt="" />${skillText ? '重新生成 Skill' : '生成 Skill'}`;
    $('goalSkillStatus').textContent = skillBusy ? '正在生成 Skill' : skillText ? '待确认保存' : '尚未生成';
    $('goalSaveBtn').textContent = skillsSupported ? (skillText && skillPrompt===$('goalPrompt').value.trim() ? '确认保存' : '生成 Skill') : '保存';
    let skill;
    try { skill = JSON.parse(skillText); } catch {}
    const fileSelect = $('goalSkillFile');
    if (fileSelect) {
      fileSelect.hidden = skill?.schema_version !== 3;
      if (skill?.schema_version === 3) {
        if (!skill.files.some(f=>f.path===skillFile)) skillFile=skill.files[0].path;
        fileSelect.replaceChildren(...skill.files.map(f=>{
          const option=document.createElement('option'); option.value=f.path; option.textContent=f.path; return option;
        }));
        fileSelect.value=skillFile;
        $('goalSkillText').value=skill.files.find(f=>f.path===skillFile).content;
      }
    }
    const steps = Array.isArray(skill?.steps) ? skill.steps : [];
    const output = steps.find(s=>s.op==='output') || skill?.output;
    const source = {private_latest:'私聊末条消息',private_messages:'私聊消息',all_messages:'全部聊天消息'}[steps[0]?.source || skill?.source];
    const labels = {read:source,semantic_search:'语义 + 关键词召回',filter:'本地筛选',context:'补齐上下文',deduplicate:'去重',output:output?.mode==='list'?'直接列出':'模型整理'};
    $('goalSkillSummary').textContent = skill ? `${skill.name || ''} · ${steps.length ? steps.map(s=>labels[s.op] || s.op).join(' → ') : `${source || ''} · ${output?.mode==='list'?'本地执行，不调用模型':'筛选后由模型整理'}`}` : '';
    if (skill?.schema_version===3) {
      let permissions=[];
      try { permissions=JSON.parse(skill.files.find(f=>f.path==='manifest.json').content).permissions; } catch {}
      $('goalSkillSummary').textContent=`${skill.name} · ${skill.files.length} 个文件 · ${permissions.join(' / ')}`;
    }
  }

  async function generateSkill() {
    if (formBusy || skillBusy) return;
    const prompt=$('goalPrompt').value.trim();
    if (!prompt) { error($('goalFormError'),'请填写任务'); return; }
    if (skillText && !confirm('重新生成只替换当前编辑草稿，已保存的 Skill 保持不变。继续吗？')) return;
    skillBusy=true; busy(true); error($('goalFormError'),'');
    $('goalSkillStatus').textContent='正在生成 Skill';
    try {
      const data=await window.WechatJobs.run('/api/goals/skill/generate',{id:editing?.id || '',prompt}, {
        onEvent:event=>{if(event.event==='progress') $('goalSkillStatus').textContent=event.message || '正在生成 Skill';}
      });
      skillText=JSON.stringify(data.skill,null,2); skillPrompt=data.prompt;
      renderSkillEditor();
      $('goalSkillStatus').textContent=`草稿已生成${data.validation?.passed?' · 隔离测试通过':data.validation?' · 测试未通过，需修改':''} · ${Number(data.usage?.total_tokens || 0)} tokens`;
      if(data.validation?.error) error($('goalFormError'),data.validation.error);
    } catch(e) { error($('goalFormError'),e.message); $('goalSkillStatus').textContent='生成未完成，原 Skill 未改变'; }
    finally { skillBusy=false; busy(false); }
  }

  $('goalSkillGenerate').addEventListener('click',generateSkill);
  $('goalSkillText').addEventListener('input',()=>{
    let skill; try { skill=JSON.parse(skillText); } catch {}
    if (skill?.schema_version===3) {
      skill.files.find(f=>f.path===skillFile).content=$('goalSkillText').value;
      skillText=JSON.stringify(skill,null,2);
    } else skillText=$('goalSkillText').value;
    $('goalSkillStatus').textContent='已修改 · 保存前需重新测试';
  });
  $('goalSkillFile')?.addEventListener('change',()=>{skillFile=$('goalSkillFile').value; renderSkillEditor();});
  $('goalSkillExport')?.addEventListener('click',()=>{
    if (!skillText) return;
    try {
      const skill=JSON.parse(skillText);
      const url=URL.createObjectURL(new Blob([JSON.stringify(skill,null,2)],{type:'application/json'}));
      const link=document.createElement('a'); link.href=url; link.download='wechat-task-skill.json'; link.click();
      setTimeout(()=>URL.revokeObjectURL(url),1000);
    } catch { error($('goalFormError'),'代码包格式无效'); }
  });
  $('goalSkillImport')?.addEventListener('click',()=>{if (!formBusy) $('goalSkillUpload').click();});
  $('goalSkillUpload')?.addEventListener('change',async()=>{
    const file=$('goalSkillUpload').files[0];
    if (!file || formBusy) return;
    try {
      if (file.size>1024*1024) throw new Error('代码包不能超过 1 MB');
      const skill=JSON.parse(await file.text());
      if (skill.schema_version!==3 || !Array.isArray(skill.files) || !skill.files.length || skill.files.some(f=>typeof f.path!=='string' || typeof f.content!=='string')) throw new Error('请选择有效的完整代码 Skill 包');
      skillText=JSON.stringify(skill,null,2); skillPrompt=$('goalPrompt').value.trim(); renderSkillEditor();
      $('goalSkillStatus').textContent='已导入草稿 · 尚未执行或保存';
    } catch(e) {error($('goalFormError'),e.message);}
    finally {$('goalSkillUpload').value='';}
  });
  async function validateCodeSkill(skill) {
    $('goalSkillStatus').textContent='隔离测试中（虚构数据，模拟模型）';
    const data=await window.WechatJobs.run('/api/goals/skill/validate',{skill,prompt:$('goalPrompt').value.trim()});
    if (!data.validation?.passed) throw new Error('Skill 测试未通过');
    $('goalSkillStatus').textContent='隔离测试通过 · 虚构数据与模拟模型';
  }
  $('goalSkillValidate')?.addEventListener('click',async()=>{
    if (formBusy) return;
    busy(true); error($('goalFormError'),'');
    try { await validateCodeSkill(JSON.parse(skillText)); }
    catch(e) {error($('goalFormError'),e.message); $('goalSkillStatus').textContent='测试未通过';}
    finally {busy(false);}
  });
  $('goalPrompt').addEventListener('input',()=>{ if(skillsSupported) $('goalSaveBtn').textContent=skillText && skillPrompt===$('goalPrompt').value.trim()?'确认保存':'生成 Skill'; });

  async function loadHistory(id) {
    const data = await request(`/api/goals/history?id=${encodeURIComponent(id)}`);
    if (historyId !== id) return;
    runs = data.runs;
    selectedRunId = runs.some(run=>run.id===selectedRunId) ? selectedRunId : runs[0]?.id || null;
    renderRun();
  }

  function renderRun() {
    $('goalRunList').innerHTML = runs.map(run=>`<button class="goal-history-item" data-run-select="${escape(run.id)}" aria-pressed="${run.id===selectedRunId}"><time>${escape(date(run.started_at))}</time><span><span class="goal-output-status" data-state="${escape(run.status)}">${escape(statusText[run.status] || run.status)}</span><small>${run.trigger==='manual'?'手动执行':'定时执行'}</small></span></button>`).join('');
    const run = runs.find(row=>row.id===selectedRunId);
    if (!run) { renderedRunSignature=''; $('goalRunContent').textContent='暂无执行记录'; return; }
    const signature = JSON.stringify(run);
    if (signature===renderedRunSignature) return;
    renderedRunSignature=signature;
    const elapsed = Math.max(0,(run.finished_at || Date.now()/1000)-run.started_at);
    const structured = run.status==='completed' ? structuredResult(run) : null;
    $('goalRunContent').innerHTML = `<h3 class="goal-history-result-title">${escape(date(run.started_at))} · 执行结果</h3><p class="goal-note">${escape(statusText[run.status])} · ${elapsed.toFixed(1)}s · ${escape(run.model || '等待模型')} · ${run.usage?.total_tokens == null ? 'Token 用量未返回' : `${Number(run.usage.total_tokens).toLocaleString()} tokens`}</p>
      <p class="goal-note">${run.trigger === 'manual' ? '手动执行' : '定时执行'}</p>
      ${run.range_type ? `<p class="goal-note">检索范围：${rangeText(run)} · ${escape(date(run.window_start))} 至 ${escape(date(run.window_end))}</p>` : ''}
      ${run.usage?.model_calls?.some(call=>call.usage_unknown)?'<p class="goal-note">部分请求用量未知，以上 Token 仅为已确认用量，实际费用可能更高。</p>':''}
      ${resumeMarkup(historyId,run,!!pendingGoalId || goals.some(g=>g.latest_run?.status==='running'))}
      <div class="goal-full-result ${run.error?'is-error':''}">${structured ? structuredMarkup(structured) : escape(run.result || run.error || run.progress)}</div>
      ${run.usage?.model_calls?.length?`<details class="goal-detail"><summary>模型调用 · ${run.usage.model_calls.length} 轮</summary><ol>${run.usage.model_calls.map(call=>call.operation?`<li>${call.operation==='embedding'?'查询 Embedding':'回答整理'} · ${Number((call.operation==='embedding'?run.usage.embedding_usage:run.usage.answer_usage)?.total_tokens || 0)} tokens${call.usage_unknown?' · 存在用量未知的尝试':''}</li>`:`<li>第 ${escape(call.round)} 轮 · ${escape(call.finish_reason || '未知结束原因')} · 输出 ${escape(call.completion_tokens ?? '未知')} tokens · 推理 ${escape(call.reasoning_tokens ?? '未知')} tokens · ${escape(call.tool_calls)} 次工具请求</li>`).join('')}</ol></details>`:''}
      ${run.steps?.length?`<details class="goal-detail"><summary>执行过程 · ${run.steps.length} 步</summary><ol>${run.steps.map(step=>`<li>${escape(step.tool)}${step.query?' · '+escape(step.query):''} · ${step.count ?? 0} 条${step.semantic_messages!=null?` · 语义 ${Number(step.semantic_messages)} / 关键词 ${Number(step.keyword_messages)} 条`:''}${step.warning?`<p>${escape(step.warning)}</p>`:''}${step.error?`<p class="is-error">${escape(step.error)}</p>`:''}</li>`).join('')}</ol></details>`:''}
      ${run.sources?.length?`<details id="goalSources" class="goal-detail"><summary>检索来源 · ${run.sources.length} 条</summary>${run.sources.map(source=>`<div id="goalSource-${escape(source.reference)}" class="goal-source" tabindex="-1"><div><strong>[${escape(source.reference)}] ${escape(source.chat_title)}</strong><button class="goal-text-button" data-chat="${escape(source.chat_id)}">打开聊天</button></div><small>${escape(source.time)} · ${escape(source.sender)}</small><p>${escape(source.text)}</p></div>`).join('')}</details>`:''}`;
  }

  function showReference(reference) {
    const ref=Number(reference);
    const run=runs.find(row=>row.id===selectedRunId);
    if (!Number.isInteger(ref) || !run?.sources?.some(source=>source.reference===ref)) return;
    $('goalSources').open=true;
    const source=$(`goalSource-${ref}`);
    source?.scrollIntoView({block:'nearest'});
    source?.focus({preventScroll:true});
  }

  $('goalNewBtn').addEventListener('click', () => edit());
  $('goalRangeUnit').addEventListener('change', updateRange);
  $('goalCancelBtn').addEventListener('click', cancelEdit);
  $('goalCancelIcon').addEventListener('click', cancelEdit);
  $('goalForm').addEventListener('keydown',event=>{
    if (event.key === 'Escape' && !formBusy) { event.preventDefault(); cancelEdit(); }
  });
  for (const button of document.querySelectorAll('[data-close-goal]')) button.addEventListener('click',()=>$(button.dataset.closeGoal).close());
  $('goalForm').addEventListener('submit',async event=>{
    event.preventDefault();
    if (formBusy) return;
    busy(true);
    try {
      if (!timingSupported) throw new Error('后台尚未加载自定义周期和范围功能，请重启原来的 8787 服务后重试');
      const prompt = $('goalPrompt').value.trim();
      const title = editing?.prompt === prompt ? editing.title : titleFromPrompt(prompt);
      const range_type = 'recent_days';
      const range_value = Number($('goalRangeValue').value);
      const range_unit = $('goalRangeUnit').value;
      const interval_value = Number($('goalIntervalValue').value);
      const interval_unit = $('goalIntervalUnit').value;
      if (!Number.isInteger(interval_value) || interval_value < 1 || interval_value > 365) throw new Error('周期数量须为 1 到 365 的整数');
      if (!['hours','days'].includes(interval_unit)) throw new Error('执行周期单位仅支持小时或天');
      const max = rangeLimits[range_unit];
      if (!max || !Number.isInteger(range_value) || range_value < 1 || range_value > max) throw new Error(`检索数量须为 1 到 ${max || 90} 的整数`);
      if (skillsSupported && (!skillText || skillPrompt!==prompt)) {
        busy(false); await generateSkill(); return;
      }
      let skillFields={};
      if (skillsSupported) {
        let skill;
        try { skill=JSON.parse(skillText); } catch { throw new Error('Skill JSON 格式无效，请检查后保存'); }
        if (skill.schema_version===3) await validateCodeSkill(skill);
        skillFields={skill,skill_prompt:skillPrompt,expected_skill_version:editing?.skill_version || 0};
      }
      await request('/api/goals/save',{id:editing?.id,title,prompt,interval_value,interval_unit,range_type,range_value,range_unit,enabled:editing?.enabled ?? true,...skillFields});
      closeEditor(); await load();
    } catch(e) { error($('goalFormError'),e.message); }
    finally { busy(false); }
  });
  $('goalsGrid').addEventListener('click',async event=>{
    const skillButton=event.target.closest('[data-skill]');
    if(skillButton) { if(!skillButton.disabled) edit(goals.find(g=>g.id===skillButton.dataset.skill)); return; }
    const resumeButton=event.target.closest('[data-resume]');
    if (resumeButton) { await resumeRun(resumeButton); return; }
    const retryButton=event.target.closest('[data-result-retry]');
    if (retryButton) {
      resultCache.delete(retryButton.dataset.resultRetry);
      await loadLatestResults();
      return;
    }
    const deleteButton=event.target.closest('[data-delete]');
    if (deleteButton) {
      if (deleteButton.disabled || pendingGoalId || formBusy || editorOpen) return;
      const goal=goals.find(item=>item.id===deleteButton.dataset.delete);
      if (!goal || goal.latest_run?.status==='running' || !confirm(`删除任务“${goal.title}”及其执行记录？`)) return;
      pendingGoalId=goal.id;
      error($('goalsError'),'');
      render();
      try {
        await request('/api/goals/delete',{id:goal.id});
        goals=goals.filter(item=>item.id!==goal.id);
        render();
        await load();
      } catch(e) { error($('goalsError'),e.message); }
      finally { pendingGoalId=null; render(); }
      return;
    }
    const runButton=event.target.closest('[data-run]');
    if (runButton) {
      if (pendingGoalId || runButton.disabled || !manualSupported) return;
      const goal = goals.find(item=>item.id===runButton.dataset.run);
      if (!goal) return;
      const stopping = goal.latest_run?.status === 'running';
      if (!stopping && (editorOpen || goals.some(item=>item.latest_run?.status==='running'))) return;
      pendingGoalId = goal.id;
      error($('goalsError'),'');
      render();
      try {
        const data = await request(stopping?'/api/goals/stop':'/api/goals/run',{id:goal.id,...(stopping?{run_id:goal.latest_run.id}:{})});
        const current = goals.find(item=>item.id===goal.id);
        if (current) {
          if (stopping) {
            if (current.latest_run?.id===goal.latest_run.id) current.latest_run={...current.latest_run,cancel_requested:true,progress:'停止中，等待当前请求结束'};
          } else {
            current.latest_run={id:data.run_id,status:'running',trigger:'manual',started_at:data.started_at,progress:'准备检索'};
            current.next_run_at=data.next_run_at;
          }
        }
        render();
        await load();
      } catch(e) { error($('goalsError'),e.message); }
      finally { pendingGoalId=null; render(); }
      return;
    }
    const editButton=event.target.closest('[data-edit]');
    const historyButton=event.target.closest('[data-history]');
    if (editButton) edit(goals.find(goal=>goal.id===editButton.dataset.edit));
    if (historyButton) {
      const id=historyButton.dataset.history;
      historyId=id;
      selectedRunId=historyButton.dataset.historyRun || null;
      renderedRunSignature='';
      $('goalHistoryTitle').textContent=goals.find(goal=>goal.id===historyId)?.title || '执行记录';
      $('goalRunContent').textContent='正在读取记录'; $('goalRunList').innerHTML='';
      $('goalHistory').showModal();
      try {
        await loadHistory(id);
        if (historyId===id && selectedRunId===historyButton.dataset.historyRun) showReference(historyButton.dataset.goalReference);
      } catch(e) { if(historyId===id) $('goalRunContent').textContent=e.message; }
    }
  });
  $('goalsGrid').addEventListener('change',async event=>{
    const checkbox=event.target.closest('[data-toggle]');
    if (!checkbox) return;
    checkbox.disabled=true;
    try { await request('/api/goals/toggle',{id:checkbox.dataset.toggle,enabled:checkbox.checked}); await load(); }
    catch(e) { checkbox.checked=!checkbox.checked; error($('goalsError'),e.message); }
    finally { checkbox.disabled=false; }
  });
  $('goalRunList').addEventListener('click',event=>{
    const button=event.target.closest('[data-run-select]');
    if (!button) return;
    selectedRunId=button.dataset.runSelect;
    renderRun();
    $('goalRunContent').scrollTop=0;
    Array.from(document.querySelectorAll('[data-run-select]')).find(item=>item.dataset.runSelect===selectedRunId)?.focus();
  });
  $('goalRunContent').addEventListener('click',event=>{
    const resumeButton=event.target.closest('[data-resume]');
    if (resumeButton) { resumeRun(resumeButton); return; }
    const reference=event.target.closest('[data-goal-reference]');
    if (reference) { showReference(reference.dataset.goalReference); return; }
    const button=event.target.closest('[data-chat]');
    if (button) { $('goalHistory').close(); document.dispatchEvent(new CustomEvent('goal-open-chat',{detail:button.dataset.chat})); }
  });
  document.addEventListener('viewchange',event=>{ if(event.detail==='goals') load(); });
  document.addEventListener('visibilitychange',()=>{ if(!document.hidden && $('goalsView').classList.contains('active')) load(); });
  setInterval(()=>{ if(!document.hidden && $('goalsView').classList.contains('active')) load(); },5000);
})();
