(() => {
  'use strict';
  const key = 'wechat.backgroundJobs.v1';
  const observed = new Set();
  const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
  const stored = () => {
    try { return JSON.parse(localStorage.getItem(key) || '{}'); } catch { return {}; }
  };
  function remember(job, remove = false) {
    try {
      const data = stored();
      if (remove) delete data[job.id];
      else data[job.id] = { id: job.id, kind: job.kind, conversation_id: job.conversation_id };
      localStorage.setItem(key, JSON.stringify(data));
    } catch { /* Progress recovery also uses the server's active execution list. */ }
  }
  async function request(url, payload) {
    const timeout = new AbortController();
    const timer = setTimeout(() => timeout.abort(), 15000);
    try {
      const response = await fetch(url, {
        ...(payload === undefined ? {} : { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(payload) }),
        signal: timeout.signal,
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok || data.ok === false) {
        const error = new Error(data.error || (response.status === 404 ? '请重启原来的 8787 服务以加载后台执行功能' : '后台请求失败'));
        error.status = response.status;
        throw error;
      }
      return data;
    } finally { clearTimeout(timer); }
  }
  async function retry(operation, onEvent) {
    let delay = 500;
    for (;;) {
      try { return await operation(); } catch (error) {
        if (error.status) throw error;
        onEvent?.({ event: 'progress', message: '连接暂时中断，正在恢复后台进度' });
        await sleep(delay);
        delay = Math.min(delay * 2, 5000);
      }
    }
  }
  async function watch(job, { signal, onEvent = () => {} } = {}) {
    observed.add(job.id);
    remember(job);
    let cursor = 0;
    let stopSent = false;
    try {
      for (;;) {
        if (signal?.aborted && !stopSent) {
          await retry(() => request('/api/jobs/stop', { id: job.id }), onEvent);
          stopSent = true;
          onEvent({ event: 'progress', message: '停止中，等待当前请求结束' });
        }
        const data = await retry(() => request(`/api/jobs/status?id=${encodeURIComponent(job.id)}&after=${cursor}`), onEvent);
        for (const event of data.job.events || []) {
          if (event.event !== 'done' && event.event !== 'error') onEvent(event);
        }
        cursor = data.job.cursor;
        if (data.job.status !== 'running') {
          remember(job, true);
          if (data.job.status === 'cancelled') {
            const error = new Error('已停止，已完成的结果已保留');
            error.name = 'AbortError';
            throw error;
          }
          const result = data.job.result;
          if (data.job.status !== 'completed' || !result?.ok) throw new Error(result?.error || '执行未完成');
          onEvent(result);
          return result;
        }
        await sleep(800);
      }
    } catch (error) {
      if (error.status === 404) remember(job, true);
      throw error;
    } finally { observed.delete(job.id); }
  }
  async function run(kind, payload, options = {}) {
    if (options.signal?.aborted) {
      const error = new Error('已停止，尚未提交模型请求');
      error.name = 'AbortError';
      throw error;
    }
    const requestId = crypto.randomUUID();
    const pending = { id: requestId, kind, conversation_id: payload.conversation_id || '' };
    observed.add(requestId);
    remember(pending);
    try {
      let data;
      try {
        data = await request('/api/jobs/start', { kind, payload, request_id: requestId });
      } catch (error) {
        if (error.status) throw error;
        // A lost acknowledgement is ambiguous, especially across server restarts.
        // Only look up the original ID; never resubmit a potentially paid operation.
        options.onEvent?.({event:'progress',message:'提交连接中断，正在确认后台执行状态'});
        data = await retry(() => request(`/api/jobs/status?id=${encodeURIComponent(requestId)}`), options.onEvent);
      }
      return await watch(data.job, options);
    } catch (error) {
      if (error.status) remember(pending, true);
      throw error;
    } finally { observed.delete(requestId); }
  }
  async function recoverable() {
    const data = await request('/api/jobs');
    const pending = stored();
    const jobs = new Map(Object.values(pending).map(job => [job.id, job]));
    for (const job of data.jobs || []) {
      if (job.status === 'running' || pending[job.id]) jobs.set(job.id, job);
    }
    return [...jobs.values()].filter(job => !observed.has(job.id));
  }
  window.WechatJobs = { run, watch, recoverable };
})();
