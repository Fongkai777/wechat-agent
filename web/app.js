const state = {
  chats: [],
  activeChat: null,
  currentLimit: 100,
  messageWindowLimit: 1000,
  currentMessages: [],
  beforeCursor: null,
  afterCursor: null,
  hasMoreBefore: false,
  hasMoreAfter: false,
  chatGeneration: 0,
  chatListGeneration: 0,
  chatViewReady: false,
  chatViewLoading: false,
  chatViewRetryTimer: null,
  chatLoading: false,
  olderLoading: false,
  newerLoading: false,
  query: "",
  statusSummary: "",
  llmConfig: null,
  qaIndexStatus: null,
  qaMessages: [],
  qaConversations: [],
  qaHistoryReady: false,
  qaHistoryLoading: false,
  qaHistoryError: "",
  qaHistoryTimer: null,
  qaHistoryGeneration: 0,
  qaOpenGeneration: 0,
  qaRequestedConversationId: null,
  activeQaConversation: null,
  voiceBatchController: null,
  voiceStatus: null,
  ragStatus: null,
  ragSearchResult: null,
  ragRebuildController: null,
  ragEmbeddingController: null,
  qaStreamController: null,
  qaTimer: null,
  activeQaMessage: null,
  syncRevision: null,
  syncPolling: false,
  manualSyncing: false,
  preparationRunning: false,
  preparationStopRequested: false,
  ragStatusTimer: null,
  ragSchedule: null,
  ragScheduleDirty: false,
  ragScheduleSaving: false,
  ragScheduleLoading: false,
  ragScheduleTimer: null,
  ragScheduleRevision: 0,
};

const mainTabs = document.querySelectorAll(".main-tab");
const views = document.querySelectorAll(".view");
const chatList = document.querySelector("#chatList");
const statusLine = document.querySelector("#statusLine");
const searchInput = document.querySelector("#searchInput");
const messagePane = document.querySelector("#messagePane");
const chatTitle = document.querySelector("#chatTitle");
const chatMeta = document.querySelector("#chatMeta");
const latestMessagesBtn = document.querySelector("#latestMessagesBtn");
const refreshBtn = document.querySelector("#refreshBtn");
const syncStatus = document.querySelector("#syncStatus");
const syncTime = document.querySelector("#syncTime");
const qaMeta = document.querySelector("#qaMeta");
const qaHistoryStatus = document.querySelector("#qaHistoryStatus");
const qaForm = document.querySelector("#qaForm");
const qaTitle = document.querySelector("#qaTitle");
const qaNewBtn = document.querySelector("#qaNewBtn");
const qaQuestion = document.querySelector("#qaQuestion");
const qaAskBtn = document.querySelector("#qaAskBtn");
const qaConversationList = document.querySelector("#qaConversationList");
const qaMessages = document.querySelector("#qaMessages");
const qaProgress = document.querySelector("#qaProgress");
const llmConfigForm = document.querySelector("#llmConfigForm");
const settingsStatus = document.querySelector("#settingsStatus");
const voiceBaseUrl = document.querySelector("#voiceBaseUrl");
const voiceModel = document.querySelector("#voiceModel");
const voiceApiKey = document.querySelector("#voiceApiKey");
const voiceApiKeyEnv = document.querySelector("#voiceApiKeyEnv");
const qaBaseUrl = document.querySelector("#qaBaseUrl");
const qaModel = document.querySelector("#qaModel");
const taskBaseUrl = document.querySelector("#taskBaseUrl");
const taskModel = document.querySelector("#taskModel");
const taskApiKey = document.querySelector("#taskApiKey");
const taskApiKeyEnv = document.querySelector("#taskApiKeyEnv");
const qaApiKey = document.querySelector("#qaApiKey");
const qaApiKeyEnv = document.querySelector("#qaApiKeyEnv");
const qaTemperature = document.querySelector("#qaTemperature");
const qaContextLimit = document.querySelector("#qaContextLimit");
const embeddingEnabled = document.querySelector("#embeddingEnabled");
const embeddingBaseUrl = document.querySelector("#embeddingBaseUrl");
const embeddingModel = document.querySelector("#embeddingModel");
const embeddingApiKey = document.querySelector("#embeddingApiKey");
const embeddingApiKeyEnv = document.querySelector("#embeddingApiKeyEnv");
const embeddingDimensions = document.querySelector("#embeddingDimensions");
const embeddingBatchSize = document.querySelector("#embeddingBatchSize");
const embeddingQueryCandidates = document.querySelector("#embeddingQueryCandidates");
const rerankEnabled = document.querySelector("#rerankEnabled");
const rerankBaseUrl = document.querySelector("#rerankBaseUrl");
const rerankModel = document.querySelector("#rerankModel");
const rerankApiKey = document.querySelector("#rerankApiKey");
const rerankApiKeyEnv = document.querySelector("#rerankApiKeyEnv");
const rerankCandidateLimit = document.querySelector("#rerankCandidateLimit");
const saveLlmConfigBtn = document.querySelector("#saveLlmConfigBtn");
const voiceTranscribeAllBtn = document.querySelector("#voiceTranscribeAllBtn");
const voiceStopBatchBtn = document.querySelector("#voiceStopBatchBtn");
const voiceBatchCount = document.querySelector("#voiceBatchCount");
const voiceBatchStatus = document.querySelector("#voiceBatchStatus");
const voiceBatchLog = document.querySelector("#voiceBatchLog");
const voiceBatchDetails = document.querySelector("#voiceBatchDetails");
const voicePendingCount = document.querySelector("#voicePendingCount");
const ragMeta = document.querySelector("#ragMeta");
const ragStatusGrid = document.querySelector("#ragStatusGrid");
const ragPrepareBtn = document.querySelector("#ragPrepareBtn");
const ragPrepareStopBtn = document.querySelector("#ragPrepareStopBtn");
const ragPrepareStatus = document.querySelector("#ragPrepareStatus");
const ragScheduleForm = document.querySelector("#ragScheduleForm");
const ragScheduleEnabled = document.querySelector("#ragScheduleEnabled");
const ragScheduleValue = document.querySelector("#ragScheduleValue");
const ragScheduleUnit = document.querySelector("#ragScheduleUnit");
const ragScheduleSaveBtn = document.querySelector("#ragScheduleSaveBtn");
const ragScheduleFeedback = document.querySelector("#ragScheduleFeedback");
const ragScheduleStatus = document.querySelector("#ragScheduleStatus");
const ragSearchForm = document.querySelector("#ragSearchForm");
const ragQuestion = document.querySelector("#ragQuestion");
const ragLimit = document.querySelector("#ragLimit");
const ragSearchBtn = document.querySelector("#ragSearchBtn");
const ragLog = document.querySelector("#ragLog");
const ragRoute = document.querySelector("#ragRoute");
const ragSources = document.querySelector("#ragSources");

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function compactTime(value) {
  if (!value || value === "-") return "";
  const parts = value.split(" ");
  return parts.length > 1 ? parts[1].slice(0, 5) : value;
}

function avatarText(title) {
  const clean = String(title || "?").trim();
  return Array.from(clean)[0] || "?";
}

function renderAvatar(title, url, className = "avatar") {
  const size = className === "mini-avatar" ? 34 : 44;
  const picture = url ? `<img class="avatar-image" src="${escapeHtml(url)}" alt="" width="${size}" height="${size}" loading="lazy" decoding="async" />` : "";
  return `<span class="${className}"><span aria-hidden="true">${escapeHtml(avatarText(title))}</span>${picture}</span>`;
}

function bindAvatarFallbacks(container) {
  for (const img of container.querySelectorAll(".avatar-image")) {
    if (img.dataset.bound) continue;
    img.dataset.bound = "true";
    img.addEventListener("error", () => img.remove(), { once: true });
    if (img.complete && !img.naturalWidth) img.remove();
  }
}

function setSyncStatus(message, kind = "ready") {
  syncStatus.textContent = message;
  syncStatus.dataset.state = kind;
}

async function getJSON(url) {
  const controller = new AbortController();
  const timer = window.setTimeout(() => controller.abort(), 15000);
  try {
    const response = await fetch(url, {signal: controller.signal, cache: "no-store"});
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || response.statusText);
    return data;
  } catch (error) {
    if (controller.signal.aborted) throw new Error("连接服务超时，请求已结束，可自动重试");
    throw error;
  } finally {
    window.clearTimeout(timer);
  }
}

async function postJSON(url, payload) {
  if (url === "/api/transcribe_voice" || url === "/api/rag/search") {
    return window.WechatJobs.run(url, payload);
  }
  const response = await fetch(url, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(payload),
  });
  const data = await response.json();
  if (!response.ok || data.ok === false) {
    const error = new Error(data.error || response.statusText);
    error.status = response.status;
    error.code = data.code;
    throw error;
  }
  return data;
}

async function loadStatus() {
  const data = await getJSON("/api/status");
  if (!state.manualSyncing) renderStatus(data);
  return data;
}

function renderStatus(data) {
  state.statusSummary = `${data.total_chats.toLocaleString()} 个聊天 · ${data.total_messages.toLocaleString()} 条消息`;
  if (data.demo_mode) state.statusSummary = `合成示例 · ${state.statusSummary}`;
  statusLine.textContent = state.statusSummary;
  const mode = data.sync_interval > 0 ? "自动同步已开启" : "手动同步";
  const syncError = data.sync_error || data.decrypt?.warning;
  setSyncStatus(syncError ? "同步异常" : mode, syncError ? "error" : "ready");
  syncStatus.title = syncError || (data.sync_interval > 0 ? `每 ${data.sync_interval} 秒检查最新消息` : "点击右侧按钮同步消息");
  syncTime.textContent = data.last_synced_at ? `${data.last_synced_at.split("T")[1]} 已更新` : "尚未同步";
  syncTime.title = data.last_synced_at ? `上次同步：${data.last_synced_at.replace("T", " ")}` : "";
  statusLine.title = `数据源：${data.db_storage}\n最新消息：${data.last_message_time || "未知"}`;
  renderQaMeta();
  return data;
}

async function pollSyncedMessages() {
  if (document.hidden || state.syncPolling || state.manualSyncing) return;
  if (!state.chatViewReady) return initializeChatView();
  state.syncPolling = true;
  try {
    const data = await loadStatus();
    if (state.manualSyncing) return;
    const revision = String(data.sync_revision);
    if (state.syncRevision !== revision) {
      const applied = await loadChats();
      if (state.manualSyncing || applied === false) return;
      await refreshChatMessages();
      state.syncRevision = revision;
    } else if (state.hasMoreAfter && nearMessageBottom()) {
      await loadNewer();
    }
  } catch (error) {
    if (!state.manualSyncing) {
      setSyncStatus("消息刷新失败，请重试", "error");
      syncStatus.title = error.message || String(error);
    }
  } finally {
    state.syncPolling = false;
  }
}

async function loadQaIndexStatus() {
  const data = await getJSON("/api/qa/index_status");
  state.qaIndexStatus = data;
  renderQaMeta();
  if (!data.ready && data.building) {
    window.setTimeout(() => loadQaIndexStatus().catch(() => {}), 1600);
  }
}

async function loadRagStatus() {
  window.clearTimeout(state.ragStatusTimer);
  const data = await getJSON("/api/rag/status");
  state.ragStatus = data;
  if (data.person_index) {
    state.qaIndexStatus = data.person_index;
    renderQaMeta();
  }
  renderRagStatus();
  if (data.maintenance?.running || data.search_index?.building || data.semantic_index?.building) {
    state.ragStatusTimer = window.setTimeout(() => loadRagStatus().catch(() => {}), 1600);
  }
}

function ragScheduleErrorText(error) {
  const text = String(error || "");
  if (text.includes("Embedding 请求失败") && /nodename nor servname|Name or service not known|Temporary failure in name resolution/.test(text)) {
    return "Embedding 服务域名解析失败，请检查网络、DNS 或代理连接";
  }
  return text;
}

function renderRagSchedule() {
  const schedule = state.ragSchedule;
  if (!schedule) return;
  if (!state.ragScheduleDirty && !state.ragScheduleSaving) {
    ragScheduleEnabled.checked = schedule.enabled;
    ragScheduleValue.value = schedule.interval_value;
    ragScheduleUnit.value = schedule.interval_unit;
  }
  for (const field of [ragScheduleEnabled, ragScheduleValue, ragScheduleUnit]) field.disabled = state.ragScheduleSaving;
  ragScheduleValue.max = ragScheduleUnit.value === "hours" ? "8760" : "365";
  ragScheduleSaveBtn.disabled = state.ragScheduleSaving || !state.ragScheduleDirty;
  ragScheduleSaveBtn.textContent = state.ragScheduleSaving ? "保存中" : "保存";
  const date = value => new Date(value * 1000).toLocaleString("zh-CN", {
    month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false,
  });
  const labels = {running: "执行中", completed: "已完成", failed: "失败", cancelled: "已停止", interrupted: "已中断"};
  const latest = schedule.latest_run || (schedule.last_finished_at ? {
    status: schedule.last_status, finished_at: schedule.last_finished_at, error: schedule.last_error,
  } : null);
  const bits = [];
  if (!schedule.enabled) bits.push("定时已关闭");
  if (schedule.last_status === "running") bits.push("定时更新执行中");
  else if (schedule.enabled && schedule.waiting) bits.push("已到更新时间，等待当前操作完成");
  else if (schedule.enabled && schedule.next_run_at) bits.push(`下次更新 ${date(schedule.next_run_at)}`);
  if (latest) bits.push(`上次${latest.trigger === "manual" ? "手动" : ""}${labels[latest.status] || "执行"} ${date(latest.finished_at)}`);
  const error = schedule.scheduler_error || latest?.error || (!latest ? schedule.last_error : "");
  if (error) bits.push(ragScheduleErrorText(error));
  ragScheduleStatus.textContent = bits.join(" · ");
  ragScheduleStatus.setAttribute("data-error", String(Boolean(error)));
}

function editRagSchedule() {
  state.ragScheduleDirty = true;
  ragScheduleFeedback.textContent = "未保存";
  ragScheduleFeedback.setAttribute("data-error", "false");
  renderRagSchedule();
}

async function saveRagSchedule(event) {
  event.preventDefault();
  if (state.ragScheduleSaving || !state.ragSchedule) return;
  const value = Number(ragScheduleValue.value);
  const unit = ragScheduleUnit.value;
  const max = unit === "hours" ? 8760 : 365;
  if (!["hours", "days"].includes(unit) || !Number.isInteger(value) || value < 1 || value > max) {
    ragScheduleFeedback.textContent = `周期须为 1 至 ${max} 的整数`;
    ragScheduleFeedback.setAttribute("data-error", "true");
    return;
  }
  state.ragScheduleSaving = true;
  ++state.ragScheduleRevision;
  renderRagSchedule();
  try {
    const data = await postJSON("/api/rag/schedule", {enabled: ragScheduleEnabled.checked,
      interval_value: value, interval_unit: unit});
    state.ragSchedule = data.schedule;
    state.ragScheduleDirty = false;
    ragScheduleFeedback.textContent = "已保存";
    ragScheduleFeedback.setAttribute("data-error", "false");
  } catch (error) {
    ragScheduleFeedback.textContent = error.message || "保存失败";
    ragScheduleFeedback.setAttribute("data-error", "true");
  } finally {
    state.ragScheduleSaving = false;
    renderRagSchedule();
  }
}

async function loadRagSchedule() {
  if (state.ragScheduleLoading) return;
  state.ragScheduleLoading = true;
  window.clearTimeout(state.ragScheduleTimer);
  const revision = state.ragScheduleRevision;
  try {
    const data = await getJSON("/api/rag/schedule");
    if (revision !== state.ragScheduleRevision || state.ragScheduleSaving) return;
    if (!data.schedule) throw new Error("未能读取定时设置");
    const previous = state.ragSchedule;
    state.ragSchedule = data.schedule;
    renderRagSchedule();
    const job = data.schedule.job;
    if (job?.status === "running" && !state.preparationController) {
      resumeMaintenanceJob(job).catch(showRagError);
    } else if (previous && ((previous.last_finished_at !== data.schedule.last_finished_at && data.schedule.last_finished_at) ||
      (previous.latest_run?.id !== data.schedule.latest_run?.id && data.schedule.latest_run?.finished_at))) {
      Promise.allSettled([loadRagStatus(), loadVoiceStatus()]);
    }
  } catch (error) {
    ragScheduleStatus.textContent = `定时状态暂不可用：${error.message || "读取失败"}`;
    ragScheduleStatus.setAttribute("data-error", "true");
  } finally {
    state.ragScheduleLoading = false;
    state.ragScheduleTimer = window.setTimeout(loadRagSchedule, 5000);
  }
}

function renderRagStatus() {
  if (!ragStatusGrid) return;
  const data = state.ragStatus || {};
  const search = data.search_index || {};
  const semantic = data.semantic_index || {};
  const retrieval = data.retrieval_config || {};
  const searchReady = search.ready
    ? (search.soft_stale ? "可用，有新变化" : "已就绪")
    : search.building
      ? "构建中"
      : search.exists && search.usable
        ? (search.can_incremental ? "可用，需增量更新" : "可用，建议全量重建")
        : search.exists && search.can_incremental
          ? "需要增量更新"
          : search.exists
            ? "需要全量重建"
            : "未建立";
  const semanticReady = semantic.enabled === false
    ? "未启用"
    : semantic.building
      ? "构建中"
      : !semantic.configured || !semantic.api_key_set
        ? "未配置"
      : semantic.ready
        ? (semantic.soft_stale ? "可用，有新变化" : "已就绪")
        : semantic.stale
          ? "需要重建"
          : semantic.usable
            ? "可用"
            : semantic.exists && semantic.schema_ok
              ? "未建立"
              : "未建立";
  const searchDetail = search.building
    ? `${search.build_stats?.message || "正在处理"} · ${formatNumber(search.build_stats?.inserted || 0)} 条`
    : search.error
      ? search.error
      : `${formatNumber(search.message_count || 0)} 条文本 · ${formatNumber(search.people_count || 0)} 人 · ${formatNumber(search.chat_count || 0)} 个聊天${search.incremental_reason ? ` · ${search.incremental_reason}` : ""}`;
  const semanticDetail = semantic.building
    ? `${semantic.build_stats?.message || "正在处理"}${semantic.build_stats?.usage_totals ? formatUsage(semantic.build_stats.usage_totals) : ""}`
    : semantic.error
      ? semantic.error
      : `${formatNumber(semantic.chunk_count || 0)} 个文本块 · ${formatNumber(semantic.mapped_message_count || 0)} 条消息 · 待更新 ${formatNumber(semantic.pending_message_count || 0)} 条 · ${semantic.model || retrieval.embedding_model || ""}`;
  ragMeta.textContent = [
    `全文索引 ${searchReady}`,
    `语义索引 ${semanticReady}`,
    search.updated_at ? `更新 ${search.updated_at}` : "",
  ].filter(Boolean).join(" · ");
  ragStatusGrid.innerHTML = `
    <div class="rag-status-tile">
      <div class="rag-stage-heading"><span class="rag-step">2</span><h2>全文索引</h2></div>
      <strong>${escapeHtml(searchReady)}</strong>
      <small>${escapeHtml(searchDetail)}</small>
      <div class="rag-card-actions">
        <button id="ragRebuildBtn" type="button" data-rag-action="rebuild" ${search.building ? "disabled" : ""}>${search.building ? "更新中" : "增量更新索引"}</button>
        <button id="ragFullRebuildBtn" class="subtle-btn" type="button" data-rag-action="full-rebuild" ${search.building ? "disabled" : ""}>全量重建</button>
      </div>
    </div>
    <div class="rag-status-tile">
      <div class="rag-stage-heading"><span class="rag-step">3</span><h2>语义索引</h2></div>
      <strong>${escapeHtml(semanticReady)}</strong>
      <small>${escapeHtml(semanticDetail)}</small>
      <div class="rag-card-actions">
        <button id="ragEmbeddingRebuildBtn" type="button" data-rag-action="embedding-rebuild" ${semantic.building ? "disabled" : ""}>${semantic.building ? "更新中" : "更新语义索引"}</button>
        <button id="ragEmbeddingFullRebuildBtn" class="subtle-btn" type="button" data-rag-action="embedding-full-rebuild" ${semantic.building ? "disabled" : ""}>重建语义</button>
      </div>
    </div>
  `;
  renderPreparationControls();
}

function preparationBusy() {
  return Boolean(state.preparationRunning || state.voiceBatchController || state.ragRebuildController ||
    state.ragEmbeddingController || state.ragStatus?.maintenance?.running ||
    state.ragStatus?.search_index?.building || state.ragStatus?.semantic_index?.building);
}

function renderPreparationControls() {
  const busy = preparationBusy();
  ragPrepareBtn.disabled = busy;
  ragPrepareBtn.textContent = state.preparationRunning ? "正在准备检索" : "一键准备检索";
  ragPrepareStopBtn.disabled = !state.preparationRunning || state.preparationStopRequested;
  voiceTranscribeAllBtn.disabled = busy;
  voiceStopBatchBtn.disabled = !state.voiceBatchController;
  for (const button of ragStatusGrid.querySelectorAll("button[data-rag-action]")) {
    button.disabled = busy;
  }
}

function renderQaMeta() {
  if (!qaMeta) return;
  const parts = [];
  if (state.statusSummary) parts.push(state.statusSummary);
  const index = state.qaIndexStatus;
  if (index?.ready) {
    const stats = index.stats || {};
    const cacheLabel = stats.cache_hit ? "缓存" : "新建";
    parts.push(`联系人索引已就绪：${stats.people || 0} 人 · ${cacheLabel}`);
  } else if (index?.building) {
    parts.push("联系人索引构建中");
  } else if (index?.error) {
    parts.push("联系人索引异常");
  }
  qaMeta.textContent = parts.join(" · ") || "使用全部已解析聊天内容";
}

function formatNumber(value) {
  return Number(value || 0).toLocaleString("zh-CN");
}

function formatMetricValue(value) {
  return typeof value === "number" ? formatNumber(value) : escapeHtml(value || "0");
}

async function loadChats() {
  const generation = ++state.chatListGeneration;
  const query = state.query;
  const params = new URLSearchParams({ limit: "1000" });
  if (query) params.set("q", query);
  try {
    const data = await getJSON(`/api/chats?${params}`);
    if (generation !== state.chatListGeneration || query !== state.query) return false;
    if (!Array.isArray(data.chats)) throw new Error("服务未返回有效的聊天列表");
    state.chats = data.chats;
    renderChats();
    return true;
  } catch (error) {
    if (generation !== state.chatListGeneration || query !== state.query) return false;
    throw error;
  }
}

async function loadLLMConfig() {
  const data = await getJSON("/api/llm/config");
  state.llmConfig = data.config;
  renderLLMConfig(data.config);
}

async function loadVoiceStatus() {
  const data = await getJSON("/api/voice/status");
  state.voiceStatus = data;
  renderVoiceStatus(data);
}

function renderLLMConfig(config) {
  if (!config) return;
  const voice = config.voice || {};
  const qa = config.qa || {};
  const task = config.task || qa;
  const embedding = config.embedding || {};
  const rerank = config.rerank || {};
  voiceBaseUrl.value = voice.base_url || "";
  voiceModel.value = voice.model || "";
  voiceApiKey.value = "";
  voiceApiKey.placeholder = voice.api_key_set
    ? `已保存：${voice.api_key_preview || "API Key"}`
    : "留空则使用环境变量";
  voiceApiKeyEnv.value = voice.api_key_env || "OPENAI_API_KEY";
  qaBaseUrl.value = qa.base_url || "";
  qaModel.value = qa.model || "";
  qaApiKey.value = "";
  qaApiKey.placeholder = qa.api_key_set
    ? `已保存：${qa.api_key_preview || "API Key"}`
    : "留空则使用环境变量";
  qaApiKeyEnv.value = qa.api_key_env || "OPENAI_API_KEY";
  qaTemperature.value = qa.temperature ?? 1;
  qaContextLimit.value = qa.max_context_messages ?? 40;
  taskBaseUrl.value = task.base_url || "";
  taskModel.value = task.model || "";
  taskApiKey.value = "";
  taskApiKey.placeholder = task.api_key_set
    ? `已保存：${task.api_key_preview || "API Key"}`
    : "留空则使用环境变量";
  taskApiKeyEnv.value = task.api_key_env || "OPENAI_API_KEY";
  embeddingEnabled.checked = embedding.enabled !== false;
  embeddingBaseUrl.value = embedding.base_url || "";
  embeddingModel.value = embedding.model || "text-embedding-3-small";
  embeddingApiKey.value = "";
  embeddingApiKey.placeholder = embedding.api_key_set
    ? `已保存：${embedding.api_key_preview || "API Key"}`
    : "留空继承问答 API Key";
  embeddingApiKeyEnv.value = embedding.api_key_env || "";
  embeddingDimensions.value = embedding.dimensions ?? 0;
  embeddingBatchSize.value = embedding.batch_size ?? 64;
  embeddingQueryCandidates.value = embedding.query_candidates ?? 80;
  rerankEnabled.checked = rerank.enabled !== false;
  rerankBaseUrl.value = rerank.base_url || "";
  rerankModel.value = rerank.model || "gpt-5-nano";
  rerankApiKey.value = "";
  rerankApiKey.placeholder = rerank.api_key_set
    ? `已保存：${rerank.api_key_preview || "API Key"}`
    : "留空继承问答 API Key";
  rerankApiKeyEnv.value = rerank.api_key_env || "";
  rerankCandidateLimit.value = rerank.candidate_limit ?? 48;
  saveLlmConfigBtn.disabled = !config.task;
  settingsStatus.textContent = config.task ? "配置就绪" : "请重启服务以启用任务助手独立配置";
}

function renderVoiceStatus(data) {
  if (!data) {
    voiceBatchCount.textContent = "已转写 - / -";
    return;
  }
  voiceBatchCount.textContent = `已转写 ${formatNumber(data.transcribed)} / ${formatNumber(data.total)}`;
  voicePendingCount.textContent = `待转写 ${formatNumber(data.pending)} 条`;
}

function resizeQaComposer() {
  qaQuestion.style.height = "auto";
  qaQuestion.style.height = `${Math.min(qaQuestion.scrollHeight, 160)}px`;
}

async function loadQaConversations() {
  if (state.qaHistoryLoading) return;
  state.qaHistoryLoading = true;
  window.clearTimeout(state.qaHistoryTimer);
  const generation = state.qaHistoryGeneration;
  renderQaConversations();
  try {
    const data = await getJSON("/api/qa/conversations");
    if (generation !== state.qaHistoryGeneration) return;
    if (!Array.isArray(data.conversations)) throw new Error("服务未返回有效的问答历史");
    state.qaConversations = data.conversations;
    state.qaHistoryReady = true;
    state.qaHistoryError = "";
    renderQaConversations();
    if (state.qaRequestedConversationId && !state.qaStreamController) {
      await openQaConversation(state.qaRequestedConversationId);
    } else if (!state.activeQaConversation && state.qaConversations[0]) {
      await openQaConversation(state.qaConversations[0].id);
    }
  } catch (error) {
    if (generation !== state.qaHistoryGeneration) return;
    state.qaHistoryError = `问答历史加载失败，正在重试：${error.message || "连接中断"}`;
  } finally {
    state.qaHistoryLoading = false;
    renderQaConversations();
    if (state.qaHistoryError || generation !== state.qaHistoryGeneration) {
      state.qaHistoryTimer = window.setTimeout(() => loadQaConversations(), 3000);
    }
  }
}

function renderQaConversations() {
  qaHistoryStatus.textContent = state.qaHistoryError || (!state.qaHistoryReady ? "正在加载问答历史" : "");
  qaHistoryStatus.hidden = !qaHistoryStatus.textContent;
  const notice = state.qaHistoryError
    ? `<div class="qa-history-status" role="status">${escapeHtml(state.qaHistoryError)}<button type="button" data-qa-history-retry>重试</button></div>` : "";
  if (!state.qaConversations.length) {
    qaConversationList.innerHTML = notice || `<div class="qa-conversation-empty">${state.qaHistoryReady ? "暂无对话" : "正在加载问答历史"}</div>`;
    qaConversationList.querySelector("[data-qa-history-retry]")?.addEventListener("click", () => loadQaConversations());
    return;
  }
  qaConversationList.innerHTML = notice + state.qaConversations
    .map((conv) => {
      const active = state.activeQaConversation && state.activeQaConversation.id === conv.id ? " active" : "";
      const title = conv.title || "新对话";
      const meta = `${compactTime(conv.updated_at || conv.created_at)} · ${conv.message_count || 0} 条`;
      return `
        <div class="qa-conversation-item${active}" data-conversation="${escapeHtml(conv.id)}">
          <button class="qa-conversation-open" data-conversation="${escapeHtml(conv.id)}" type="button">
            <span>${escapeHtml(title)}</span>
            <small>${escapeHtml(meta)}</small>
          </button>
          <div class="qa-conversation-actions">
            <button class="qa-conversation-rename" data-conversation="${escapeHtml(conv.id)}" data-title="${escapeHtml(title)}" type="button" aria-label="重命名" title="重命名"><img src="/static/icons/pencil.svg" width="14" height="14" alt="" /></button>
            <button class="qa-conversation-delete" data-conversation="${escapeHtml(conv.id)}" data-title="${escapeHtml(title)}" type="button" aria-label="删除" title="删除"><img src="/static/icons/trash-2.svg" width="14" height="14" alt="" /></button>
          </div>
        </div>
      `;
    })
    .join("");
  for (const item of qaConversationList.querySelectorAll(".qa-conversation-open")) {
    item.addEventListener("click", () => openQaConversation(item.dataset.conversation).catch(showQaHistoryError));
  }
  for (const item of qaConversationList.querySelectorAll(".qa-conversation-rename")) {
    item.addEventListener("click", () => renameQaConversation(item.dataset.conversation, item.dataset.title).catch(showQaHistoryError));
  }
  for (const item of qaConversationList.querySelectorAll(".qa-conversation-delete")) {
    item.addEventListener("click", () => deleteQaConversation(item.dataset.conversation, item.dataset.title).catch(showQaHistoryError));
  }
  qaConversationList.querySelector("[data-qa-history-retry]")?.addEventListener("click", () => loadQaConversations());
}

function showQaHistoryError(error) {
  state.qaHistoryError = error.message || "问答历史加载失败";
  renderQaConversations();
  setQaProgress(state.qaHistoryError);
}

async function newQaConversation() {
  if (guardActiveQaAnswer()) return;
  ++state.qaHistoryGeneration;
  ++state.qaOpenGeneration;
  state.qaRequestedConversationId = null;
  const data = await postJSON("/api/qa/conversation/new", {});
  state.qaConversations = data.conversations || [];
  state.activeQaConversation = data.conversation;
  state.qaMessages = [];
  qaTitle.textContent = "新对话";
  renderQaConversations();
  renderQaMessages();
  setQaProgress("");
}

async function openQaConversation(id) {
  if (state.activeQaConversation && state.activeQaConversation.id === id) return;
  if (guardActiveQaAnswer()) return;
  const generation = ++state.qaOpenGeneration;
  state.qaRequestedConversationId = id;
  const data = await getJSON(`/api/qa/conversation?id=${encodeURIComponent(id)}`);
  if (generation !== state.qaOpenGeneration || state.qaStreamController) return;
  if (!data.conversation || data.conversation.id !== id || !Array.isArray(data.conversation.messages)) {
    throw new Error("服务未返回有效的问答记录，请重试");
  }
  state.activeQaConversation = data.conversation;
  state.qaRequestedConversationId = null;
  state.qaHistoryError = "";
  state.qaMessages = data.conversation.messages || [];
  qaTitle.textContent = data.conversation.title || "新对话";
  renderQaConversations();
  renderQaMessages();
  setQaProgress("");
}

async function saveActiveQaConversation() {
  if (!state.activeQaConversation) return;
  const data = await postJSON("/api/qa/conversation", {
    id: state.activeQaConversation.id,
    title: state.activeQaConversation.title,
    messages: state.qaMessages.filter((item) => !item.pending && !item.error),
  });
  state.activeQaConversation = data.conversation;
  state.qaConversations = data.conversations || [];
  qaTitle.textContent = data.conversation.title || "新对话";
  renderQaConversations();
}

async function renameQaConversation(id, currentTitle = "") {
  const title = window.prompt("重命名对话", currentTitle || "新对话");
  if (title === null) return;
  const trimmed = title.trim();
  if (!trimmed) return;
  ++state.qaHistoryGeneration;
  const data = await postJSON("/api/qa/conversation/rename", { id, title: trimmed });
  state.qaConversations = data.conversations || [];
  if (state.activeQaConversation && state.activeQaConversation.id === id) {
    state.activeQaConversation = data.conversation;
    qaTitle.textContent = data.conversation.title || "新对话";
  }
  renderQaConversations();
}

async function deleteQaConversation(id, title = "") {
  if (guardActiveQaAnswer()) return;
  const label = title || "新对话";
  if (!window.confirm(`删除对话「${label}」？`)) return;
  ++state.qaHistoryGeneration;
  ++state.qaOpenGeneration;
  state.qaRequestedConversationId = null;
  const data = await postJSON("/api/qa/conversation/delete", { id });
  state.qaConversations = data.conversations || [];
  if (state.activeQaConversation && state.activeQaConversation.id === id) {
    state.activeQaConversation = null;
    state.qaMessages = [];
    qaTitle.textContent = "新对话";
    renderQaMessages();
    setQaProgress("");
    if (state.qaConversations[0]) {
      await openQaConversation(state.qaConversations[0].id);
      return;
    }
  }
  renderQaConversations();
}

function renderChats() {
  if (!state.chats.length) {
    chatList.innerHTML = `<div class="empty-state">没有匹配会话</div>`;
    return;
  }
  chatList.innerHTML = state.chats
    .map((chat) => {
      const active = state.activeChat && state.activeChat.id === chat.id ? " active" : "";
      const typeClass = chat.type === "group" ? " group" : "";
      const summary = String(chat.summary || "").trim() || "[暂无预览]";
      return `
        <button class="chat-item${active}" data-chat="${escapeHtml(chat.id)}">
          ${renderAvatar(chat.title, chat.avatar_url, `avatar${typeClass}`)}
          <span class="chat-main">
            <span class="chat-row">
              <span class="chat-title" title="${escapeHtml(chat.title)}">${escapeHtml(chat.title)}</span>
              <span class="chat-time">${escapeHtml(compactTime(chat.last_time))}</span>
            </span>
            <span class="chat-summary" title="${escapeHtml(summary)}">${escapeHtml(summary)}</span>
          </span>
        </button>
      `;
    })
    .join("");

  bindAvatarFallbacks(chatList);
  for (const item of chatList.querySelectorAll(".chat-item")) {
    item.addEventListener("click", () => openChat(item.dataset.chat).catch(showError));
  }
}

async function openChat(chatId) {
  const generation = ++state.chatGeneration;
  state.activeChat = state.chats.find(chat => chat.id === chatId) || null;
  chatTitle.textContent = state.activeChat?.title || "载入中";
  chatMeta.textContent = "";
  state.chatLoading = true;
  state.olderLoading = false;
  state.newerLoading = false;
  state.beforeCursor = state.afterCursor = null;
  state.hasMoreBefore = state.hasMoreAfter = false;
  state.currentMessages = [];
  latestMessagesBtn.hidden = true;
  const params = new URLSearchParams({ chat: chatId, limit: String(state.currentLimit) });
  messagePane.innerHTML = `<div class="empty-state">载入中</div>`;
  try {
    const data = await getJSON(`/api/messages?${params}`);
    if (generation !== state.chatGeneration) return;
    if (data.messages.some(msg => !msg.id)) throw new Error("请重启原来的 8787 服务以加载消息分页功能");
    state.activeChat = data.chat;
    state.currentMessages = data.messages;
    state.beforeCursor = data.before_cursor;
    state.afterCursor = data.after_cursor;
    state.hasMoreBefore = data.has_more_before;
    state.hasMoreAfter = data.has_more_after;
    updateChatHeading(data.chat);
    renderChats();
    renderMessages(data);
    messagePane.scrollTop = messagePane.scrollHeight;
  } catch (error) {
    if (generation === state.chatGeneration) {
      messagePane.innerHTML = `<div class="empty-state">${escapeHtml(error.message || "加载失败，请重新选择会话")}</div>`;
    }
  } finally {
    if (generation === state.chatGeneration) state.chatLoading = false;
  }
}

function updateChatHeading(chat) {
  chatTitle.textContent = chat.title;
  chatTitle.title = chat.title;
  chatMeta.textContent = `${chat.total_messages} 条消息 · ${chat.first_time} 至 ${chat.last_time}`;
  chatMeta.title = chatMeta.textContent;
}

function nearMessageBottom() {
  return messagePane.scrollHeight - messagePane.scrollTop - messagePane.clientHeight < 100;
}

function messageAnchor() {
  const top = messagePane.getBoundingClientRect().top;
  const entry = [...messagePane.querySelectorAll(".message-entry")].find(node => node.getBoundingClientRect().bottom > top);
  return entry ? {entry, top: entry.getBoundingClientRect().top} : null;
}

function restoreMessageAnchor(anchor) {
  if (anchor?.entry.isConnected) messagePane.scrollTop += anchor.entry.getBoundingClientRect().top - anchor.top;
}

function updateOlderButton() {
  const older = messagePane.querySelector("#loadOlderBtn");
  if (!older) return;
  older.hidden = !state.hasMoreBefore;
  older.disabled = state.olderLoading || state.newerLoading;
  older.textContent = state.olderLoading ? "加载中" : "更早消息";
}

function trimMessageWindow(edge, anchor = null) {
  if (state.currentMessages.length <= state.messageWindowLimit) return;
  const keep = edge === "start" ? state.currentMessages.slice(-state.messageWindowLimit) : state.currentMessages.slice(0, state.messageWindowLimit);
  const kept = new Set(keep.map(msg => msg.id));
  // Never evict the message the user is currently reading.
  if (anchor && !kept.has(anchor.entry.dataset.messageId)) return;
  for (const msg of state.currentMessages) {
    if (!kept.has(msg.id)) messagePane.querySelector(`.message-entry[data-message-id="${CSS.escape(msg.id)}"]`)?.remove();
  }
  state.currentMessages = keep;
  state.beforeCursor = keep[0].id;
  state.afterCursor = keep[keep.length - 1].id;
  if (edge === "start") state.hasMoreBefore = true;
  else {
    state.hasMoreAfter = true;
    latestMessagesBtn.hidden = false;
    latestMessagesBtn.textContent = "查看最新消息";
  }
  updateMessageDivider(keep[0].id, null);
  updateOlderButton();
}

async function loadOlder() {
  if (!state.activeChat || state.chatLoading || state.olderLoading || state.newerLoading || !state.hasMoreBefore || !state.beforeCursor) return;
  const generation = state.chatGeneration;
  state.olderLoading = true;
  updateOlderButton();
  const params = new URLSearchParams({
    chat: state.activeChat.id, before: state.beforeCursor, limit: String(state.currentLimit),
  });
  try {
    const data = await getJSON(`/api/messages?${params}`);
    if (generation !== state.chatGeneration) return;
    const anchor = messageAnchor();
    const ids = new Set(state.currentMessages.map(msg => msg.id));
    const added = data.messages.filter(msg => !ids.has(msg.id));
    const previousFirst = state.currentMessages[0];
    state.currentMessages = [...added, ...state.currentMessages];
    state.beforeCursor = data.before_cursor || state.beforeCursor;
    state.hasMoreBefore = data.has_more_before;
    messagePane.querySelector("#messageEntries").insertAdjacentHTML("afterbegin", messageEntriesHTML(added));
    if (previousFirst && added.length) updateMessageDivider(previousFirst.id, added[added.length - 1]);
    trimMessageWindow("end", anchor);
    state.olderLoading = false;
    updateOlderButton();
    bindMessageControls();
    restoreMessageAnchor(anchor);
  } catch (error) {
    if (generation === state.chatGeneration) {
      const older = messagePane.querySelector("#loadOlderBtn");
      if (older) older.title = error.message;
      setSyncStatus("历史消息加载失败，请重试", "error");
    }
  } finally {
    if (generation === state.chatGeneration) {
      state.olderLoading = false;
      updateOlderButton();
    }
  }
}

async function loadNewer() {
  if (!state.activeChat || state.chatLoading || state.newerLoading || state.olderLoading) return;
  if (!state.afterCursor) return openChat(state.activeChat.id);
  const generation = state.chatGeneration;
  state.newerLoading = true;
  updateOlderButton();
  try {
    const params = new URLSearchParams({chat: state.activeChat.id, after: state.afterCursor, limit: String(state.currentLimit)});
    const data = await getJSON(`/api/messages?${params}`);
    if (generation !== state.chatGeneration) return;
    const atBottom = nearMessageBottom();
    const anchor = atBottom ? null : messageAnchor();
    const ids = new Set(state.currentMessages.map(msg => msg.id));
    const added = data.messages.filter(msg => !ids.has(msg.id));
    const previous = state.currentMessages[state.currentMessages.length - 1];
    state.currentMessages.push(...added);
    state.afterCursor = data.after_cursor || state.afterCursor;
    state.hasMoreAfter = data.has_more_after;
    state.activeChat = data.chat;
    updateChatHeading(data.chat);
    messagePane.querySelector("#messageEntries").insertAdjacentHTML("beforeend", messageEntriesHTML(added, previous));
    trimMessageWindow("start", anchor);
    bindMessageControls();
    if (atBottom) messagePane.scrollTop = messagePane.scrollHeight;
    else restoreMessageAnchor(anchor);
    latestMessagesBtn.hidden = !state.hasMoreAfter && (atBottom || !added.length);
    latestMessagesBtn.textContent = state.hasMoreAfter ? "查看最新消息" : "新消息";
  } finally {
    if (generation === state.chatGeneration) {
      state.newerLoading = false;
      updateOlderButton();
    }
  }
}

async function refreshChatMessages() {
  if (!state.activeChat || state.chatLoading) return;
  const latest = state.chats.find(chat => chat.id === state.activeChat.id);
  if (!latest) return;
  updateChatHeading(latest);
  if (nearMessageBottom() && document.querySelector("#rawView").classList.contains("active")) {
    await loadNewer();
    await refreshVisibleMessages();
  } else if (latest.total_messages !== state.activeChat.total_messages || latest.last_ts !== state.activeChat.last_ts) {
    latestMessagesBtn.hidden = false;
    latestMessagesBtn.textContent = "查看最新消息";
  }
}

function messageEntriesHTML(messages, previous = null) {
  let lastDivider = previous ? dividerLabel(previous.time) : "";
  return messages.map(msg => {
    const divider = dividerLabel(msg.time);
    const heading = divider && divider !== lastDivider ? `<div class="day-divider">${escapeHtml(divider)}</div>` : "";
    lastDivider = divider;
    return `<div class="message-entry" data-message-id="${escapeHtml(msg.id)}">${heading}${renderMessage(msg, state.activeChat.type, msg.id)}</div>`;
  }).join("");
}

function updateMessageDivider(id, previous) {
  const entry = messagePane.querySelector(`.message-entry[data-message-id="${CSS.escape(id)}"]`);
  const message = state.currentMessages.find(msg => msg.id === id);
  if (!entry || !message) return;
  const heading = entry.querySelector(".day-divider");
  const label = dividerLabel(message.time);
  if (label === dividerLabel(previous?.time)) heading?.remove();
  else if (label && !heading) entry.insertAdjacentHTML("afterbegin", `<div class="day-divider">${escapeHtml(label)}</div>`);
}

function renderMessages(data) {
  messagePane.innerHTML = `<button id="loadOlderBtn" class="load-older">更早消息</button><div id="messageEntries">${messageEntriesHTML(data.messages)}</div>`;
  if (!data.messages.length) messagePane.querySelector("#messageEntries").innerHTML = `<div class="empty-state">没有消息</div>`;
  updateOlderButton();
  messagePane.querySelector("#loadOlderBtn").addEventListener("click", loadOlder);
  bindMessageControls();
}

function bindMessageControls() {
  bindVoiceTranscribeButtons();
  bindMediaFallbacks();
  bindAvatarFallbacks(messagePane);
}

async function refreshVisibleMessages() {
  if (!state.activeChat || state.chatLoading || !state.currentMessages.length) return;
  const generation = state.chatGeneration;
  const visible = messageAnchor();
  const index = Math.max(0, state.currentMessages.findIndex(msg => msg.id === visible?.entry.dataset.messageId) - 1);
  const params = new URLSearchParams({chat: state.activeChat.id, limit: String(state.currentLimit)});
  if (index > 0) params.set("after", state.currentMessages[index - 1].id);
  else if (state.currentMessages.length > state.currentLimit) params.set("before", state.currentMessages[state.currentLimit].id);
  const data = await getJSON(`/api/messages?${params}`);
  if (generation !== state.chatGeneration) return;
  const anchor = messageAnchor();
  for (const msg of data.messages) {
    const position = state.currentMessages.findIndex(item => item.id === msg.id);
    if (position < 0 || JSON.stringify(state.currentMessages[position]) === JSON.stringify(msg)) continue;
    state.currentMessages[position] = msg;
    const entry = messagePane.querySelector(`.message-entry[data-message-id="${CSS.escape(msg.id)}"]`);
    const bubble = entry?.querySelector(".message");
    if (bubble) bubble.outerHTML = renderMessage(msg, state.activeChat.type, msg.id);
  }
  bindMessageControls();
  restoreMessageAnchor(anchor);
}

function dividerLabel(time) {
  if (!time || time === "-") return "";
  return time.slice(0, 16);
}

function renderMessage(msg, chatType, index) {
  const isSystem = msg.type === "system";
  if (isSystem) {
    return `<div class="message system"><div class="bubble">${escapeHtml(msg.content)}</div></div>`;
  }
  const mine = msg.mine ? " mine" : "";
  const sender = chatType === "group" && !msg.mine ? `<div class="sender">${escapeHtml(msg.sender || msg.sender_username)}</div>` : "";
  const body = renderMessageBody(msg, index);
  const contentClass = msg.type === "text" ? "" : " type-chip";
  return `
    <div class="message${mine}">
      ${renderAvatar(msg.mine ? "我" : msg.sender || msg.sender_username, msg.avatar_url, "mini-avatar")}
      <div class="bubble-wrap">
        ${sender}
        <div class="bubble${contentClass}">${body}</div>
      </div>
    </div>
  `;
}

function renderMessageBody(msg, index) {
  const media = msg.media;
  if (msg.record) {
    return renderForwardedRecord(msg.record);
  }
  if (msg.app) {
    return renderAppMessage(msg.app);
  }
  if (media?.available && media.kind === "image" && media.url) {
    return `<img class="media-image" src="${escapeHtml(media.url)}" alt="${escapeHtml(msg.content || "图片")}" loading="lazy" data-fallback="${escapeHtml(media.note || "图片加载失败")}">`;
  }
  if (media?.available && media.kind === "video" && media.url) {
    const poster = media.thumb_url ? ` poster="${escapeHtml(media.thumb_url)}"` : "";
    return `<video class="media-video" src="${escapeHtml(media.url)}"${poster} controls preload="metadata"></video>`;
  }
  if (media?.available && media.kind === "sticker" && media.url) {
    return `<img class="sticker-image" src="${escapeHtml(media.url)}" alt="${escapeHtml(msg.content || "表情包")}" loading="lazy" data-fallback="${escapeHtml(media.note || "表情包加载失败")}">`;
  }
  if (media?.kind === "voice") {
    if (media.transcription) {
      return `
        <div class="voice-message transcribed">
          <span class="voice-note">${escapeHtml(msg.content)} · 转文字</span>
          <span class="voice-transcript">${escapeHtml(media.transcription)}</span>
        </div>
      `;
    }
    const transcribeButton = !media.transcription && media.can_transcribe
      ? `<button class="voice-transcribe" data-message-id="${escapeHtml(index)}">转文字</button>`
      : "";
    if (media.available && media.url) {
      return `
        <div class="voice-message">
          <audio src="${escapeHtml(media.url)}" controls preload="none"></audio>
          ${transcribeButton}
        </div>
      `;
    }
    return `
      <div class="voice-message">
        <span>${escapeHtml(msg.content)}</span>
        <span class="voice-note">${escapeHtml(media.note || "未转文字")}</span>
        ${transcribeButton}
      </div>
    `;
  }
  if (media?.encrypted) {
    return `<span class="media-placeholder">${escapeHtml(media.note || "图片需要额外 key")}</span>`;
  }
  if (media?.kind === "sticker") {
    return `<span class="media-placeholder">${escapeHtml(media.note || msg.content || "表情包")}</span>`;
  }
  return escapeHtml(msg.content);
}

function renderAppMessage(app) {
  const meta = [app.label, app.app_name, app.domain].filter(Boolean).join(" · ");
  const title = app.title || app.url || app.label || "链接";
  const desc = app.description ? `<div class="app-desc">${escapeHtml(app.description)}</div>` : "";
  const url = app.url
    ? `<a class="app-url" href="${escapeHtml(app.url)}" target="_blank" rel="noopener noreferrer">${escapeHtml(app.url)}</a>`
    : "";
  const inner = `
    <div class="app-meta">${escapeHtml(meta)}</div>
    <div class="app-title">${escapeHtml(title)}</div>
    ${desc}
    ${url}
  `;
  return `<div class="app-card">${inner}</div>`;
}

function renderForwardedRecord(record) {
  const items = record.items || [];
  const count = record.count || items.length;
  const rows = items
    .map((item) => {
      const meta = [item.time, item.sender].filter(Boolean).join(" · ");
      const label = item.type !== "1" ? `<span class="record-kind">${escapeHtml(item.label || "消息")}</span>` : "";
      const media = renderRecordItemMedia(item);
      const text = item.content ? escapeHtml(item.content) : "";
      return `
        <div class="record-item">
          <div class="record-meta">${escapeHtml(meta)}</div>
          <div class="record-text">${label}${text}</div>
          ${media}
        </div>
      `;
    })
    .join("");
  const empty = record.note
    ? `<div class="record-empty">${escapeHtml(record.note)}</div>`
    : `<div class="record-empty">没有可展开的记录</div>`;
  const more = record.truncated ? `<div class="record-empty">还有更多条目未展开</div>` : "";
  return `
    <details class="record-card">
      <summary>
        <span class="record-title">${escapeHtml(record.title || "聊天记录")}</span>
        <span class="record-count">${escapeHtml(count)} 条</span>
      </summary>
      <div class="record-list">
        ${rows || empty}
        ${more}
      </div>
    </details>
  `;
}

function renderRecordItemMedia(item) {
  const media = item.media;
  if (!media) return "";
  if (media.available && media.kind === "image" && media.url) {
    return `<img class="record-media-image" src="${escapeHtml(media.url)}" alt="${escapeHtml(item.content || "图片")}" loading="lazy" data-fallback="${escapeHtml(media.note || "转发记录图片加载失败")}">`;
  }
  if (media.available && media.kind === "video" && media.url) {
    const poster = media.thumb_url ? ` poster="${escapeHtml(media.thumb_url)}"` : "";
    return `<video class="record-media-video" src="${escapeHtml(media.url)}"${poster} controls preload="metadata"></video>`;
  }
  if (media.kind === "record-voice") {
    return `<span class="record-media-note">${escapeHtml(media.note || "转发记录语音暂不可播放")}</span>`;
  }
  if (media.missing || media.encrypted || media.available === false) {
    return `<span class="record-media-note">${escapeHtml(media.note || "媒体文件需要额外 key")}</span>`;
  }
  return "";
}

function bindMediaFallbacks() {
  for (const img of messagePane.querySelectorAll("img.media-image, img.sticker-image, img.record-media-image")) {
    if (img.dataset.bound) continue;
    img.dataset.bound = "true";
    img.addEventListener("error", () => {
      const note = document.createElement("span");
      note.className = img.classList.contains("record-media-image") ? "record-media-note" : "media-placeholder";
      note.textContent = img.dataset.fallback || img.alt || "媒体加载失败";
      img.replaceWith(note);
    }, { once: true });
  }
}

function bindVoiceTranscribeButtons() {
  for (const button of messagePane.querySelectorAll(".voice-transcribe")) {
    if (button.dataset.bound) continue;
    button.dataset.bound = "true";
    button.addEventListener("click", () => transcribeVoice(button));
  }
}

async function transcribeVoice(button) {
  const msg = state.currentMessages.find(item => item.id === button.dataset.messageId);
  const generation = state.chatGeneration;
  const media = msg?.media;
  if (!media) return;
  button.disabled = true;
  button.textContent = "识别中";
  try {
    const result = await postJSON("/api/transcribe_voice", {
      db: media.db,
      local_id: media.local_id,
      create_time: media.create_time,
    });
    if (generation === state.chatGeneration) {
      const current = state.currentMessages.find(item => item.id === msg.id);
      if (current?.media) {
        const anchor = messageAnchor();
        current.media.transcription = result.text || "（未识别到文字）";
        current.media.note = "";
        const entry = messagePane.querySelector(`.message-entry[data-message-id="${CSS.escape(msg.id)}"]`);
        const bubble = entry?.querySelector(".message");
        if (bubble) bubble.outerHTML = renderMessage(current, state.activeChat.type, current.id);
        bindMessageControls();
        restoreMessageAnchor(anchor);
      }
    }
  } catch (error) {
    button.textContent = error.message || "识别失败";
    button.classList.add("error");
  } finally {
    button.disabled = false;
  }
}

function switchView(name) {
  for (const tab of mainTabs) {
    tab.classList.toggle("active", tab.dataset.view === name);
  }
  for (const view of views) {
    view.classList.toggle("active", view.id === `${name}View`);
  }
  if (name === "raw") refreshChatMessages().catch(() => setSyncStatus("消息更新失败，请重试", "error"));
  if (name === "settings" && !state.llmConfig) {
    loadLLMConfig().catch(showPanelError);
  }
  if (name === "settings") {
    loadVoiceStatus().catch(showPanelError);
  }
  if (name === "rag") {
    loadRagStatus().catch(showRagError);
    loadVoiceStatus().catch(showRagError);
  }
  if (name === "qa") loadQaConversations();
  document.dispatchEvent(new CustomEvent("viewchange", { detail: name }));
}

document.addEventListener("goal-open-chat", (event) => {
  switchView("raw");
  openChat(event.detail).catch(showPanelError);
});

async function saveLLMConfig(event) {
  event.preventDefault();
  if (!state.llmConfig?.task) {
    settingsStatus.textContent = "请重启服务以启用任务助手独立配置";
    return;
  }
  saveLlmConfigBtn.disabled = true;
  saveLlmConfigBtn.textContent = "保存中";
  settingsStatus.textContent = "保存中";
  try {
    const payload = {
      voice: {
        base_url: voiceBaseUrl.value.trim(),
        model: voiceModel.value.trim(),
        api_key: voiceApiKey.value.trim(),
        api_key_env: voiceApiKeyEnv.value.trim(),
      },
      qa: {
        base_url: qaBaseUrl.value.trim(),
        model: qaModel.value.trim(),
        api_key: qaApiKey.value.trim(),
        api_key_env: qaApiKeyEnv.value.trim(),
        temperature: Number(qaTemperature.value || 1),
        max_context_messages: Number(qaContextLimit.value || 40),
      },
      task: {
        base_url: taskBaseUrl.value.trim(),
        model: taskModel.value.trim(),
        api_key: taskApiKey.value.trim(),
        api_key_env: taskApiKeyEnv.value.trim(),
      },
      embedding: {
        enabled: embeddingEnabled.checked,
        base_url: embeddingBaseUrl.value.trim(),
        model: embeddingModel.value.trim(),
        api_key: embeddingApiKey.value.trim(),
        api_key_env: embeddingApiKeyEnv.value.trim(),
        dimensions: Number(embeddingDimensions.value || 0),
        batch_size: Number(embeddingBatchSize.value || 64),
        query_candidates: Number(embeddingQueryCandidates.value || 80),
      },
      rerank: {
        enabled: rerankEnabled.checked,
        base_url: rerankBaseUrl.value.trim(),
        model: rerankModel.value.trim(),
        api_key: rerankApiKey.value.trim(),
        api_key_env: rerankApiKeyEnv.value.trim(),
        candidate_limit: Number(rerankCandidateLimit.value || 48),
      },
    };
    const result = await postJSON("/api/llm/config", payload);
    state.llmConfig = result.config;
    renderLLMConfig(result.config);
    settingsStatus.textContent = "已保存";
  } catch (error) {
    settingsStatus.textContent = error.message || "保存失败";
  } finally {
    saveLlmConfigBtn.disabled = false;
    saveLlmConfigBtn.textContent = "保存配置";
  }
}

async function prepareRag() {
  if (preparationBusy()) return;
  state.preparationRunning = true;
  state.preparationStopRequested = false;
  ragPrepareStatus.textContent = "1/3 · 语音转文字";
  renderPreparationControls();
  let voiceFailures = 0;
  try {
    const controller = new AbortController();
    state.preparationController = controller;
    const result = await window.WechatJobs.run("/api/rag/prepare", {}, {
      signal: controller.signal, onEvent: handlePreparationEvent,
    });
    voiceFailures = Number(result.voice_failures || 0);
    ragPrepareStatus.textContent = voiceFailures
      ? `索引已更新 · ${voiceFailures} 条语音转写失败，可重试`
      : "检索准备完成";
  } catch (error) {
    ragPrepareStatus.textContent = error.message || "准备失败";
  } finally {
    state.preparationRunning = false;
    state.preparationController = null;
    await Promise.allSettled([loadVoiceStatus(), loadRagStatus()]);
    renderPreparationControls();
  }
}

function stopRagPreparation() {
  state.preparationStopRequested = true;
  state.preparationController?.abort();
  state.voiceBatchController?.abort();
  state.ragRebuildController?.abort();
  state.ragEmbeddingController?.abort();
  ragPrepareStatus.textContent = "正在停止";
  renderPreparationControls();
}

function handlePreparationEvent(event) {
  if (event.event === "stage") {
    ragPrepareStatus.textContent = `${event.stage}/3 · ${event.message}`;
    return;
  }
  const forwarded = { ...event, event: event.event === "stage_done" ? "done" : event.event };
  if (event.stage === 1) handleVoiceBatchEvent(forwarded);
  if (event.stage === 2) handleRagRebuildEvent(forwarded);
  if (event.stage === 3) handleRagEmbeddingRebuildEvent(forwarded);
  if (!event.stage && event.message) ragPrepareStatus.textContent = event.message;
}

async function transcribeAllVoices({ pipeline = false } = {}) {
  if (!pipeline && preparationBusy()) return null;
  const controller = new AbortController();
  state.voiceBatchController = controller;
  renderPreparationControls();
  voiceBatchStatus.textContent = "准备中";
  voiceBatchLog.innerHTML = "";
  voiceBatchDetails.hidden = false;
  try {
    return await window.WechatJobs.run("/api/transcribe_all_voices_stream", { force: false }, {
      signal: controller.signal, onEvent: handleVoiceBatchEvent,
    });
  } catch (error) {
    if (error.name === "AbortError") {
      voiceBatchStatus.textContent = "已停止";
      appendVoiceBatchLog("已停止");
    } else {
      voiceBatchStatus.textContent = error.message || "转写失败";
      appendVoiceBatchLog(error.message || "转写失败", "error");
    }
    return { ok: false, error: error.message, aborted: error.name === "AbortError" };
  } finally {
    state.voiceBatchController = null;
    await Promise.allSettled([loadVoiceStatus(), loadRagStatus()]);
    renderPreparationControls();
  }
}

function handleVoiceBatchEvent(event) {
  if (event.event === "progress") {
    voiceBatchStatus.textContent = event.message || "处理中";
    return;
  }
  if (event.event === "summary") {
    state.voiceStatus = {
      total: event.total,
      transcribed: event.skipped,
      pending: event.pending,
    };
    renderVoiceStatus(state.voiceStatus);
    voiceBatchStatus.textContent = `待转写 ${event.pending} 条 · 已有 ${event.skipped} 条`;
    appendVoiceBatchLog(`共 ${event.total} 条本地语音，跳过已转写 ${event.skipped} 条`);
    return;
  }
  if (event.event === "item_done") {
    const item = event.item || {};
    if (state.voiceStatus) {
      state.voiceStatus.transcribed = Math.min(state.voiceStatus.total, state.voiceStatus.transcribed + 1);
      state.voiceStatus.pending = Math.max(0, state.voiceStatus.total - state.voiceStatus.transcribed);
      renderVoiceStatus(state.voiceStatus);
    }
    appendVoiceBatchLog(`${item.time || ""} · ${event.text || "（未识别到文字）"}${formatUsage(event.usage_totals)}`);
    return;
  }
  if (event.event === "item_error") {
    const item = event.item || {};
    appendVoiceBatchLog(`${item.time || ""} · ${event.error || "转写失败"}`, "error");
    return;
  }
  if (event.event === "error") {
    voiceBatchStatus.textContent = event.error || "转写失败";
    appendVoiceBatchLog(event.error || "转写失败", "error");
    return;
  }
  if (event.event === "done") {
    voiceBatchStatus.textContent = `完成：转写 ${event.transcribed} 条 · 跳过 ${event.skipped} 条 · 失败 ${event.failed} 条${formatUsage(event.usage_totals)}`;
    loadVoiceStatus().catch((error) => { voiceBatchStatus.textContent = error.message; });
    if (state.activeChat) {
      refreshVisibleMessages().catch(showError);
    }
    loadStatus().catch(showError);
  }
}

async function rebuildRagIndex(full = false, { pipeline = false } = {}) {
  if (!pipeline && preparationBusy()) return null;
  const controller = new AbortController();
  const rebuildBtn = document.querySelector("#ragRebuildBtn");
  const fullRebuildBtn = document.querySelector("#ragFullRebuildBtn");
  state.ragRebuildController = controller;
  renderPreparationControls();
  if (rebuildBtn) rebuildBtn.disabled = true;
  if (fullRebuildBtn) fullRebuildBtn.disabled = true;
  if (rebuildBtn) rebuildBtn.textContent = full ? "重建中" : "更新中";
  ragLog.innerHTML = "";
  appendRagLog(full ? "开始全量重建全文候选库" : "开始增量更新全文候选库");
  try {
    return await window.WechatJobs.run("/api/rag/rebuild_stream", { full }, {
      signal: controller.signal, onEvent: handleRagRebuildEvent,
    });
  } catch (error) {
    appendRagLog(error.name === "AbortError" ? "已停止" : error.message || "重建失败", "error");
    return { ok: false, error: error.message, aborted: error.name === "AbortError" };
  } finally {
    state.ragRebuildController = null;
    if (fullRebuildBtn) fullRebuildBtn.disabled = false;
    await loadRagStatus().catch(() => {});
    renderPreparationControls();
  }
}

async function rebuildRagEmbeddingIndex(full = false, { pipeline = false } = {}) {
  if (!pipeline && preparationBusy()) return null;
  const controller = new AbortController();
  const embeddingRebuildBtn = document.querySelector("#ragEmbeddingRebuildBtn");
  const embeddingFullRebuildBtn = document.querySelector("#ragEmbeddingFullRebuildBtn");
  state.ragEmbeddingController = controller;
  renderPreparationControls();
  if (embeddingRebuildBtn) embeddingRebuildBtn.disabled = true;
  if (embeddingFullRebuildBtn) embeddingFullRebuildBtn.disabled = true;
  if (embeddingRebuildBtn) embeddingRebuildBtn.textContent = full ? "重建中" : "更新中";
  ragLog.innerHTML = "";
  appendRagLog(full ? "开始重建语义索引" : "开始更新语义索引");
  try {
    return await window.WechatJobs.run("/api/rag/embedding_rebuild_stream", { full }, {
      signal: controller.signal, onEvent: handleRagEmbeddingRebuildEvent,
    });
  } catch (error) {
    appendRagLog(error.name === "AbortError" ? "已停止" : error.message || "语义索引失败", "error");
    return { ok: false, error: error.message, aborted: error.name === "AbortError" };
  } finally {
    state.ragEmbeddingController = null;
    if (embeddingFullRebuildBtn) embeddingFullRebuildBtn.disabled = false;
    await loadRagStatus().catch(() => {});
    renderPreparationControls();
  }
}

function handleRagRebuildEvent(event) {
  if (event.event === "progress") {
    appendRagLog(`${event.message || "处理中"}${event.inserted ? ` · 已写入 ${formatNumber(event.inserted)} 条` : ""}`);
    if (state.ragStatus?.search_index) {
      state.ragStatus.search_index.building = true;
      state.ragStatus.search_index.build_stats = event;
      renderRagStatus();
    }
    return;
  }
  if (event.event === "error") {
    appendRagLog(event.error || "重建失败", "error");
    if (event.status) {
      state.ragStatus = event.status;
      renderRagStatus();
    }
    return;
  }
  if (event.event === "done") {
    const search = event.status?.search_index || {};
    const stats = search.build_stats || {};
    const mode = stats.update_mode === "full" ? "全量重建" : "增量更新";
    appendRagLog(`${mode}完成${stats.inserted !== undefined ? ` · 新增 ${formatNumber(stats.inserted)} 条` : ""}${stats.removed_messages ? ` · 排除副本记录 ${formatNumber(stats.removed_messages)} 条` : ""}${stats.updated_voices ? ` · 更新语音 ${formatNumber(stats.updated_voices)} 条` : ""}${stats.invalidated_chunks ? ` · 待更新语义块 ${formatNumber(stats.invalidated_chunks)} 个` : ""}`);
    if (event.status) {
      state.ragStatus = event.status;
      renderRagStatus();
    }
  }
}

function handleRagEmbeddingRebuildEvent(event) {
  if (event.event === "progress") {
    appendRagLog(`${event.message || "处理中"}${event.inserted ? ` · 文本块 ${formatNumber(event.inserted)}` : ""}${formatUsage(event.usage_totals)}`);
    if (state.ragStatus?.semantic_index) {
      state.ragStatus.semantic_index.building = true;
      state.ragStatus.semantic_index.build_stats = event;
      renderRagStatus();
    }
    return;
  }
  if (event.event === "error") {
    appendRagLog(event.error || "语义索引失败", "error");
    if (event.status) {
      state.ragStatus = event.status;
      renderRagStatus();
    }
    return;
  }
  if (event.event === "done") {
    const semantic = event.status?.semantic_index || {};
    const stats = semantic.build_stats || {};
    const mode = stats.update_mode === "full" ? "语义重建" : "语义增量更新";
    appendRagLog(`${mode}完成 · 文本块 ${formatNumber(stats.inserted_chunks || 0)} · 消息 ${formatNumber(stats.mapped_messages || 0)}${formatUsage(stats.usage_totals)}`);
    if (event.status) {
      state.ragStatus = event.status;
      renderRagStatus();
    }
  }
}

async function runRagSearch(event) {
  event.preventDefault();
  const question = ragQuestion.value.trim();
  if (!question) return;
  ragSearchBtn.disabled = true;
  ragSearchBtn.textContent = "检索中";
  ragRoute.innerHTML = `<div class="rag-placeholder">正在检索</div>`;
  ragSources.innerHTML = "";
  try {
    const result = await postJSON("/api/rag/search", {
      question,
      limit: Number(ragLimit.value || 60),
    });
    state.ragSearchResult = result;
    state.ragStatus = result.status || state.ragStatus;
    renderRagStatus();
    renderRagSearchResult(result);
  } catch (error) {
    showRagError(error);
  } finally {
    ragSearchBtn.disabled = false;
    ragSearchBtn.textContent = "运行检索";
  }
}

function renderRagSearchResult(result) {
  const route = result.route || {};
  const retrieval = result.retrieval || {};
  const search = retrieval.search || {};
  const plan = route.plan || retrieval.query_plan || search.plan || {};
  const relatedPeople = route.related_people || plan.related_people || [];
  const terms = route.terms || plan.terms || search.terms || [];
  const branches = search.branches || [];
  const queries = plan.queries || [];
  const chips = [
    route.strategy || retrieval.mode,
    plan.intent_label,
    route.scope || retrieval.scope,
    plan.time_since_label || (route.time_scope ? `自 ${route.time_scope}` : ""),
    relatedPeople.length ? `相关联系人 ${relatedPeople.length}` : "",
    route.wants_person_summary ? "联系人摘要" : "",
  ].filter(Boolean);
  const diagnostics = [
    ["主路", search.primary === "embedding" ? "Embedding" : "本地"],
    ["计划 Query", queries.length || 1],
    ["分路", branches.length],
    ["唯一候选", retrieval.candidate_count],
    ["粗排", retrieval.scored_count],
    ["入模", retrieval.context_count],
    ["FTS", search.fts_count],
    ["LIKE", search.like_count],
    ["Embedding", search.embedding_count],
    ["Rerank", search.rerank_used ? search.rerank_count : 0],
  ];
  const semantic = search.semantic || {};
  const apiNotes = [
    semantic.error ? `Embedding：${semantic.error}` : "",
    search.rerank_error ? `Rerank：${search.rerank_error}` : "",
  ].filter(Boolean);
  const summary = result.person_summary
    ? `<pre class="rag-summary">${escapeHtml(result.person_summary)}</pre>`
    : "";
  const planHtml = `
    <div class="rag-plan-grid">
      <div class="rag-plan-card">
        <div class="rag-section-title">Query Planner</div>
        <div class="rag-plan-meta">
          <span>${escapeHtml(plan.intent_label || "普通聊天检索")}</span>
          <span>${escapeHtml(plan.scope_label || "全库")}</span>
          ${plan.time_since_label ? `<span>自 ${escapeHtml(plan.time_since_label)}</span>` : ""}
        </div>
        <div class="rag-query-list">
          ${queries.length ? queries.map((query, index) => `
            <div class="rag-query-item">
              <strong>${index + 1}</strong>
              <div>
                <span>${escapeHtml(query.text || "")}</span>
                <small>${escapeHtml(query.reason || "")}${query.weight ? ` · 权重 ${escapeHtml(String(query.weight))}` : ""}</small>
              </div>
            </div>
          `).join("") : `<div class="rag-muted">使用原始问题检索。</div>`}
        </div>
      </div>
      <div class="rag-plan-card">
        <div class="rag-section-title">召回分路</div>
        <div class="rag-branch-list">
          ${branches.length ? branches.map((branch) => `
            <div class="rag-branch-item">
              <strong>${escapeHtml(branch.label || retrievalSourceLabel(branch.source))}</strong>
              <span>${formatNumber(branch.count)} 次命中 · 最好排名 ${escapeHtml(String(branch.best_rank || "-"))}</span>
            </div>
          `).join("") : `<div class="rag-muted">还没有分路命中。</div>`}
        </div>
        ${search.fusion ? `
          <div class="rag-fusion-note">
            融合：${escapeHtml(search.fusion.method || "RRF")} · k=${escapeHtml(String(search.fusion.k || ""))}
          </div>
        ` : ""}
      </div>
    </div>
  `;
  ragRoute.innerHTML = `
    <div class="rag-section-title">路由</div>
    <div class="rag-chip-row">${chips.map((chip) => `<span>${escapeHtml(chip)}</span>`).join("") || "<span>全库</span>"}</div>
    <div class="rag-metric-row">
      ${diagnostics.map(([label, value]) => `
        <div>
          <span>${escapeHtml(label)}</span>
          <strong>${formatMetricValue(value)}</strong>
        </div>
      `).join("")}
    </div>
    ${planHtml}
    <div class="rag-section-title">相关联系人</div>
    ${renderRagPeople(relatedPeople)}
    <div class="rag-section-title">检索词</div>
    <div class="rag-token-row">${terms.slice(0, 40).map((term) => `<span>${escapeHtml(term)}</span>`).join("")}</div>
    ${apiNotes.length ? `<div class="rag-muted">${escapeHtml(apiNotes.join(" · "))}</div>` : ""}
    ${summary}
  `;
  const sources = result.sources || [];
  ragSources.innerHTML = sources.length
    ? `
      <div class="rag-section-title">入模片段</div>
      <div class="rag-source-list">
        ${sources.map((source, index) => {
          const meta = [source.chat_title, source.time, source.sender, source.type].filter(Boolean).join(" · ");
          const scores = [
            source.rank_score !== undefined ? `粗排 ${source.rank_score}` : "",
            source.fusion_score !== undefined ? `融合 ${formatFloat(source.fusion_score, 4)}` : "",
            source.semantic_score !== undefined ? `语义 ${formatFloat(source.semantic_score, 3)}` : "",
            source.rerank_score !== undefined ? `Rerank ${formatFloat(source.rerank_score, 2)}` : "",
          ].filter(Boolean);
          return `
            <article class="rag-source-card">
              <div class="rag-source-rank">${index + 1}</div>
              <div>
                <div class="rag-source-meta">${escapeHtml(meta)}</div>
                ${scores.length ? `<div class="rag-source-score">${scores.map(escapeHtml).join(" · ")}</div>` : ""}
                ${renderRagSignals(source.retrieval_signals || [])}
                <div class="rag-source-body">${escapeHtml(source.text || "")}</div>
              </div>
            </article>
          `;
        }).join("")}
      </div>
    `
    : `<div class="rag-placeholder">没有可展示片段</div>`;
}

function renderRagPeople(people) {
  if (!people.length) {
    return `<div class="rag-muted">没有触发联系人补充召回，按聊天内容全库检索。</div>`;
  }
  return `
    <div class="rag-people-list">
      ${people.map((person) => {
        const meta = [
          Number.isFinite(Number(person.message_count)) ? `${formatNumber(person.message_count)} 条` : "",
          Number.isFinite(Number(person.private_count)) ? `私聊 ${formatNumber(person.private_count)}` : "",
          Number.isFinite(Number(person.group_count)) ? `群聊 ${formatNumber(person.group_count)}` : "",
          person.last_time || "",
        ].filter(Boolean).join(" · ");
        return `
          <div class="rag-person">
            <strong>${escapeHtml(person.name || person.id || "-")}</strong>
            <span>${escapeHtml(meta || "联系人软信号")}</span>
          </div>
        `;
      }).join("")}
    </div>
  `;
}

function renderRagSignals(signals) {
  if (!signals.length) return "";
  return `
    <div class="rag-signal-row">
      ${signals.slice(0, 6).map((signal) => `
        <span title="${escapeHtml(signal.query || "")}">
          ${escapeHtml(retrievalSourceLabel(signal.source))} #${escapeHtml(String(signal.rank || "-"))}
        </span>
      `).join("")}
    </div>
  `;
}

function retrievalSourceLabel(source) {
  return {
    embedding: "Embedding",
    fts: "FTS",
    like: "LIKE",
    contact: "联系人",
  }[source] || source || "-";
}

function formatFloat(value, digits = 2) {
  const number = Number(value);
  if (!Number.isFinite(number)) return "-";
  return number.toFixed(digits);
}

function appendRagLog(text, kind = "") {
  const line = document.createElement("div");
  line.className = `rag-log-line${kind ? ` ${kind}` : ""}`;
  line.textContent = text;
  ragLog.appendChild(line);
  while (ragLog.children.length > 120) {
    ragLog.firstElementChild?.remove();
  }
  ragLog.scrollTop = ragLog.scrollHeight;
}

function showRagError(error) {
  ragRoute.innerHTML = `<div class="rag-placeholder error">${escapeHtml(error.message || error)}</div>`;
}

function appendVoiceBatchLog(text, kind = "") {
  const line = document.createElement("div");
  line.className = `voice-batch-line${kind ? ` ${kind}` : ""}`;
  line.textContent = text;
  voiceBatchLog.appendChild(line);
  while (voiceBatchLog.children.length > 160) {
    voiceBatchLog.firstElementChild?.remove();
  }
  voiceBatchLog.scrollTop = voiceBatchLog.scrollHeight;
}

function formatUsage(usage) {
  if (!usage) return "";
  const total = usage.total_tokens || usage.prompt_tokens || usage.input_tokens;
  if (!total) return "";
  return ` · tokens ${formatNumber(total)}`;
}

async function askQuestion(event) {
  event.preventDefault();
  const question = qaQuestion.value.trim();
  if (!question) return;
  if (state.qaStreamController || state.qaStarting) return;
  state.qaStarting = true;
  try {
    if (!state.activeQaConversation) await newQaConversation();
  } catch (error) {
    setQaProgress(error.message || "创建对话失败");
    return;
  } finally { state.qaStarting = false; }
  const conversationId = state.activeQaConversation?.id || "";
  const controller = new AbortController();
  state.qaStreamController = controller;
  qaQuestion.value = "";
  resizeQaComposer();
  state.qaMessages.push({ role: "user", content: question });
  const assistantMessage = {
    id: makeClientId("qa"),
    role: "assistant",
    content: "",
    pending: true,
    progress: "发送问题",
    started_at: Date.now(),
    elapsed_ms: 0,
    sources: [],
    context_count: 0,
    conversation_id: conversationId,
  };
  state.qaMessages.push(assistantMessage);
  renderQaMessages();
  qaAskBtn.disabled = true;
  qaAskBtn.textContent = "...";
  startQaTimer(assistantMessage);
  updateQaProgressFromMessage(assistantMessage);
  try {
    await saveActiveQaConversation().catch(() => {});
    await streamQuestion(question, assistantMessage, conversationId, controller);
  } catch (error) {
    assistantMessage.pending = false;
    assistantMessage.elapsed_ms = elapsedForMessage(assistantMessage);
    if (error.name === "AbortError") {
      assistantMessage.stopped = true;
      assistantMessage.content = assistantMessage.content || "已停止回答";
    } else {
      assistantMessage.error = error.message || "问答失败";
      assistantMessage.content = assistantMessage.error;
    }
    renderQaMessages();
    setQaProgress("");
  } finally {
    stopQaTimer(assistantMessage);
    state.qaStreamController = null;
    qaAskBtn.disabled = false;
    qaAskBtn.textContent = "↑";
  }
}

async function streamQuestion(question, assistantMessage, conversationId, controller = new AbortController()) {
  state.qaStreamController = controller;
  const history = state.qaMessages
    .filter((item) => !item.pending && !item.error)
    .slice(0, -1)
    .map((item) => ({ role: item.role, content: item.content }));
  await window.WechatJobs.run("/api/qa_stream", { question, history, conversation_id: conversationId }, {
    signal: controller.signal,
    onEvent: (event) => handleQaStreamEvent(event, assistantMessage),
  });
}

function handleQaStreamEvent(event, assistantMessage) {
  if (event.event === "progress") {
    assistantMessage.progress = event.message || "正在处理";
    updateQaProgressFromMessage(assistantMessage);
    updateQaElapsedNode(assistantMessage);
    return;
  }
  if (event.event === "error") {
    assistantMessage.pending = false;
    assistantMessage.elapsed_ms = elapsedForMessage(assistantMessage);
    assistantMessage.error = event.error || "问答失败";
    assistantMessage.content = assistantMessage.error;
    renderQaMessages();
    setQaProgress("");
    return;
  }
  if (event.event === "done") {
    assistantMessage.pending = false;
    assistantMessage.processing_ms = Number(event.processing_ms || 0) || elapsedForMessage(assistantMessage);
    assistantMessage.elapsed_ms = assistantMessage.processing_ms;
    assistantMessage.content = event.answer || "没有返回结果";
    assistantMessage.answer_data = event.answer_data || null;
    assistantMessage.sources = event.sources || [];
    assistantMessage.context_count = event.context_count || 0;
    assistantMessage.retrieval = event.retrieval || null;
    if (event.conversation && state.activeQaConversation?.id === event.conversation.id) {
      state.activeQaConversation = event.conversation;
      qaTitle.textContent = event.conversation.title || "新对话";
    }
    if (event.conversations) {
      state.qaConversations = event.conversations;
      renderQaConversations();
    }
    renderQaMessages();
    setQaProgress("");
  }
}

function renderQaMessages() {
  if (!state.qaMessages.length) {
    qaMessages.innerHTML = `<div class="qa-empty">问一个关于聊天记录的问题</div>`;
    return;
  }
  qaMessages.innerHTML = state.qaMessages.map(renderQaMessage).join("");
  bindQaMessageActions();
  qaMessages.scrollTop = qaMessages.scrollHeight;
}

function renderQaMessage(message) {
  const roleClass = message.role === "user" ? " user" : " assistant";
  const errorClass = message.error ? " error" : "";
  const sources = message.role === "assistant" ? renderQaSources(message) : "";
  const meta = message.role === "assistant" ? renderQaResponseMeta(message) : "";
  return `
    <article class="qa-message${roleClass}${errorClass}">
      <div class="qa-avatar">${message.role === "user" ? "我" : "AI"}</div>
      <div class="qa-bubble">
        ${renderQaAnswer(message)}
        ${meta}
        ${sources}
      </div>
    </article>
  `;
}

function qaSourceMeta(source, ref) {
  const kind = { private: "私聊", group: "群聊", index_summary: "索引汇总" }[source.chat_type] || "会话类型未知";
  return [`[${ref}]`, kind, source.chat_title, source.time, source.sender].filter(Boolean).join(" · ");
}

function renderQaAnswer(message) {
  const paragraphs = message.answer_data?.paragraphs;
  const sources = message.sources || [];
  const valid = message.role === "assistant" && !message.error && Array.isArray(paragraphs) && paragraphs.length
    && paragraphs.every(p => p && ["answer", "limitation"].includes(p.kind) && typeof p.text === "string"
      && Array.isArray(p.source_refs) && (p.kind === "limitation" || p.source_refs.length)
      && p.source_refs.every(ref => Number.isInteger(ref) && ref > 0 && Boolean(sources[ref - 1])));
  if (!valid) return `<div class="qa-answer-text">${escapeHtml(message.content || "")}</div>`;
  return paragraphs.map(p => {
    const citations = [...new Set(p.source_refs)].map(ref => {
      const source = sources[ref - 1];
      return `<details class="qa-citation"><summary>${escapeHtml(qaSourceMeta(source, ref))}</summary><div class="qa-source-text">${escapeHtml(source.text || "")}</div></details>`;
    }).join("");
    return `<div class="qa-answer-paragraph"><div class="qa-answer-text">${escapeHtml(p.text)}</div>${citations}</div>`;
  }).join("");
}

function renderQaResponseMeta(message) {
  const elapsed = message.elapsed_ms || message.processing_ms || 0;
  const status = message.pending ? (message.progress || "正在处理") : message.stopped ? "已停止" : elapsed ? "已完成" : "";
  if (!status && !elapsed) return "";
  const stop = message.pending
    ? `<button class="qa-stop-answer" type="button" data-qa-stop="1">停止</button>`
    : "";
  return `
    <div class="qa-response-meta">
      <span data-qa-status="${escapeHtml(message.id || "")}">${escapeHtml(status)}</span>
      ${elapsed ? `<span data-qa-elapsed="${escapeHtml(message.id || "")}">${escapeHtml(formatElapsed(elapsed))}</span>` : `<span data-qa-elapsed="${escapeHtml(message.id || "")}">0.0s</span>`}
      ${stop}
    </div>
  `;
}

function bindQaMessageActions() {
  for (const button of qaMessages.querySelectorAll(".qa-stop-answer")) {
    button.addEventListener("click", stopQaAnswer);
  }
}

function renderQaSources(message) {
  const retrieval = message.retrieval || {};
  const retrievalBits = [];
  if (retrieval.mode) retrievalBits.push(retrieval.mode);
  if (retrieval.scope) retrievalBits.push(retrieval.scope);
  if (retrieval.time_scope) retrievalBits.push(`自 ${retrieval.time_scope}`);
  if (Array.isArray(retrieval.related_people) && retrieval.related_people.length) {
    retrievalBits.push(`相关联系人 ${retrieval.related_people.slice(0, 4).join("、")}`);
  }
  if (Number.isFinite(Number(retrieval.candidate_count))) retrievalBits.push(`候选 ${Number(retrieval.candidate_count)} 条`);
  if (Number.isFinite(Number(retrieval.scored_count))) retrievalBits.push(`打分 ${Number(retrieval.scored_count)} 条`);
  if (Number.isFinite(Number(message.context_count))) retrievalBits.push(`入模 ${Number(message.context_count)} 条`);
  const summary = retrievalBits.length
    ? `检索片段 · ${retrievalBits.join(" · ")}`
    : `检索片段 · ${message.context_count || 0} 条`;
  const sources = (message.sources || [])
    .map((source, index) => {
      const meta = qaSourceMeta(source, index + 1);
      return `
        <div class="qa-source">
          <div class="qa-source-meta">${escapeHtml(meta)}</div>
          <div class="qa-source-text">${escapeHtml(source.text || "")}</div>
        </div>
      `;
    })
    .join("");
  return sources
    ? `<details class="qa-sources"><summary>${escapeHtml(summary)}</summary>${sources}</details>`
    : "";
}

function setQaProgress(text) {
  qaProgress.textContent = text;
  qaProgress.classList.toggle("active", Boolean(text));
}

function makeClientId(prefix) {
  const random = Math.random().toString(16).slice(2);
  return `${prefix}-${Date.now().toString(36)}-${random}`;
}

function startQaTimer(message) {
  stopQaTimer();
  state.activeQaMessage = message;
  const tick = () => {
    if (!message.pending) return;
    message.elapsed_ms = elapsedForMessage(message);
    updateQaElapsedNode(message);
    updateQaProgressFromMessage(message);
  };
  tick();
  state.qaTimer = window.setInterval(tick, 500);
}

function stopQaTimer(message = null) {
  if (state.qaTimer) {
    window.clearInterval(state.qaTimer);
    state.qaTimer = null;
  }
  if (message) {
    message.elapsed_ms = elapsedForMessage(message);
    updateQaElapsedNode(message);
  }
  state.activeQaMessage = null;
}

function elapsedForMessage(message) {
  const startedAt = Number(message?.started_at || 0);
  if (!startedAt) return Number(message?.elapsed_ms || message?.processing_ms || 0);
  return Math.max(0, Date.now() - startedAt);
}

function updateQaElapsedNode(message) {
  if (!message?.id) return;
  const node = qaMessages.querySelector(`[data-qa-elapsed="${CSS.escape(message.id)}"]`);
  if (node) node.textContent = formatElapsed(message.elapsed_ms || elapsedForMessage(message));
}

function updateQaStatusNode(message) {
  if (!message?.id) return;
  const node = qaMessages.querySelector(`[data-qa-status="${CSS.escape(message.id)}"]`);
  if (node) node.textContent = message.progress || "正在处理";
}

function updateQaProgressFromMessage(message) {
  if (!message?.pending) return;
  const label = message.progress || "正在处理";
  updateQaStatusNode(message);
  setQaProgress(`${label} · ${formatElapsed(message.elapsed_ms || elapsedForMessage(message))}`);
}

function formatElapsed(ms) {
  const totalMs = Math.max(0, Number(ms || 0));
  const seconds = totalMs / 1000;
  if (seconds < 60) return `${seconds.toFixed(1)}s`;
  const minutes = Math.floor(seconds / 60);
  const rest = Math.floor(seconds % 60).toString().padStart(2, "0");
  return `${minutes}:${rest}`;
}

function stopQaAnswer() {
  if (!state.qaStreamController) return;
  state.qaStreamController.abort();
  if (state.activeQaMessage) {
    state.activeQaMessage.progress = "正在停止";
    updateQaProgressFromMessage(state.activeQaMessage);
  }
}

function guardActiveQaAnswer() {
  if (!state.qaStreamController) return false;
  setQaProgress("正在回答中，先停止或等它完成再切换对话");
  return true;
}

function showPanelError(error) {
  settingsStatus.textContent = error.message || String(error);
}

let searchTimer = null;
let lastMessageScrollTop = 0;
messagePane.addEventListener("scroll", () => {
  const movingUp = messagePane.scrollTop < lastMessageScrollTop;
  lastMessageScrollTop = messagePane.scrollTop;
  if (movingUp && messagePane.scrollTop < 100) loadOlder();
  if (!movingUp && nearMessageBottom() && state.hasMoreAfter) loadNewer().catch(() => setSyncStatus("消息更新失败，请重试", "error"));
});
latestMessagesBtn.addEventListener("click", () => {
  if (state.activeChat && !state.chatLoading) openChat(state.activeChat.id).catch(showError);
});
searchInput.addEventListener("input", () => {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(() => {
    state.query = searchInput.value.trim();
    loadChats().catch(showError);
  }, 180);
});

for (const tab of mainTabs) {
  tab.addEventListener("click", () => {
    switchView(tab.dataset.view);
  });
}

function isSyncBusy(error) {
  // Older servers return the same 409 for busy readers and actual sync failures.
  return error.status === 409 && (error.code === "sync_busy" ||
    error.message === "正在处理其他请求，请稍后同步");
}

function waitForSyncRetry(milliseconds) {
  return new Promise(resolve => window.setTimeout(resolve, milliseconds));
}

async function syncLatestMessages() {
  if (state.manualSyncing) return;
  state.manualSyncing = true;
  refreshBtn.disabled = true;
  refreshBtn.setAttribute("aria-busy", "true");
  refreshBtn.querySelector("span").textContent = "正在同步";
  setSyncStatus("正在读取最新消息", "loading");
  syncStatus.title = "";
  let phase = "sync";
  try {
    let result;
    for (let attempt = 0; attempt < 5; attempt++) {
      try {
        result = await postJSON("/api/sync", {});
        break;
      } catch (error) {
        if (!isSyncBusy(error) || attempt === 4) throw error;
        setSyncStatus("等待当前读取完成", "loading");
        syncStatus.title = "其他请求正在使用数据快照，稍后自动重试；不会中断正在运行的任务";
        await waitForSyncRetry(500 * (attempt + 1));
      }
    }
    phase = "chats";
    renderStatus(result.status);
    setSyncStatus("正在更新聊天列表", "loading");
    const applied = await loadChats();
    phase = "messages";
    await refreshChatMessages();
    if (applied !== false) state.syncRevision = String(result.status.sync_revision);
    renderStatus(result.status);
    if (!result.status.sync_error && !result.status.decrypt?.warning) {
      setSyncStatus("已同步最新消息");
      syncStatus.title = "已检查本地微信数据库并刷新聊天列表";
    }
    // Index status is ancillary; it cannot turn a successful message sync into a failure.
    Promise.allSettled([loadRagStatus(), loadQaIndexStatus()]);
  } catch (error) {
    const detail = error.message || String(error);
    if (isSyncBusy(error)) {
      setSyncStatus("服务忙，请稍后同步", "warning");
      syncStatus.title = "其他读取或后台任务仍在运行，本次未执行同步；没有中断现有任务";
    } else {
      const label = phase === "sync" ? "同步失败" : phase === "chats" ? "已同步，聊天列表刷新失败" : "已同步，消息刷新失败";
      setSyncStatus(`${label}：${detail}`, "error");
      syncStatus.title = `${label}：${detail}`;
    }
  } finally {
    state.manualSyncing = false;
    refreshBtn.disabled = false;
    refreshBtn.setAttribute("aria-busy", "false");
    refreshBtn.querySelector("span").textContent = "同步最新消息";
  }
}

refreshBtn.addEventListener("click", syncLatestMessages);

qaForm.addEventListener("submit", askQuestion);
qaQuestion.addEventListener("input", resizeQaComposer);
qaQuestion.addEventListener("keydown", (event) => {
  if (event.key !== "Enter" || event.shiftKey || event.isComposing) return;
  event.preventDefault();
  if (!qaAskBtn.disabled) {
    qaForm.requestSubmit();
  }
});
qaNewBtn.addEventListener("click", () => {
  newQaConversation().catch(showQaHistoryError);
});
voiceTranscribeAllBtn.addEventListener("click", transcribeAllVoices);
ragPrepareBtn.addEventListener("click", prepareRag);
ragScheduleForm.addEventListener("submit", saveRagSchedule);
ragScheduleEnabled.addEventListener("change", editRagSchedule);
ragScheduleValue.addEventListener("input", editRagSchedule);
ragScheduleUnit.addEventListener("change", editRagSchedule);
ragPrepareStopBtn.addEventListener("click", stopRagPreparation);
voiceStopBatchBtn.addEventListener("click", () => {
  if (state.preparationRunning) stopRagPreparation();
  else state.voiceBatchController?.abort();
});
llmConfigForm.addEventListener("submit", saveLLMConfig);
ragStatusGrid.addEventListener("click", (event) => {
  const button = event.target.closest("button[data-rag-action]");
  if (!button || button.disabled) return;
  const action = button.dataset.ragAction;
  if (action === "rebuild") {
    rebuildRagIndex(false);
  } else if (action === "full-rebuild") {
    rebuildRagIndex(true);
  } else if (action === "embedding-rebuild") {
    rebuildRagEmbeddingIndex(false);
  } else if (action === "embedding-full-rebuild") {
    rebuildRagEmbeddingIndex(true);
  }
});
ragSearchForm.addEventListener("submit", runRagSearch);
ragQuestion.addEventListener("keydown", (event) => {
  if (event.key !== "Enter" || event.shiftKey || event.isComposing) return;
  event.preventDefault();
  if (!ragSearchBtn.disabled) {
    ragSearchForm.requestSubmit();
  }
});

function showError(error) {
  messagePane.innerHTML = `<div class="empty-state">${escapeHtml(error.message || error)}</div>`;
}

async function initializeChatView() {
  if (state.chatViewReady || state.chatViewLoading || state.manualSyncing) return;
  state.chatViewLoading = true;
  window.clearTimeout(state.chatViewRetryTimer);
  state.chatViewRetryTimer = null;
  try {
    // A failed status request must not prevent a successful chat list from rendering.
    const results = await Promise.allSettled([loadStatus(), loadChats()]);
    if (state.manualSyncing) return;
    const failures = results.filter(result => result.status === "rejected");
    state.chatViewReady = !failures.length;
    if (failures.length) {
      statusLine.textContent = state.statusSummary || "等待连接";
      setSyncStatus("连接暂时中断，正在重试", "warning");
      syncStatus.title = failures.map(result => result.reason.message || String(result.reason)).join("；");
    }
    if (!state.activeChat && !state.query && state.chats[0]) await openChat(state.chats[0].id);
  } finally {
    state.chatViewLoading = false;
    if (!state.chatViewReady) state.chatViewRetryTimer = window.setTimeout(initializeChatView, 3000);
  }
}

initializeChatView().catch(showError);
loadRagSchedule();

Promise.allSettled([loadLLMConfig(), loadVoiceStatus(), loadQaConversations(), loadQaIndexStatus(), loadRagStatus()])
  .then(() => recoverBackgroundJobs()).catch(showPanelError);
window.setInterval(pollSyncedMessages, 15000);
document.addEventListener("visibilitychange", () => {
  if (!document.hidden) {
    pollSyncedMessages();
    recoverBackgroundJobs().catch(showPanelError);
  }
});
window.addEventListener("online", () => {
  pollSyncedMessages();
  loadQaConversations();
});

async function recoverBackgroundJobs() {
  if (!window.WechatJobs || state.recoveringJobs) return;
  state.recoveringJobs = true;
  try {
    const jobs = await window.WechatJobs.recoverable();
    for (const job of jobs) {
      // Observation does not submit a new model request.
      if (job.kind === "/api/qa_stream" || job.kind === "/api/qa") {
        if (!state.qaStreamController) resumeQaJob(job).catch(showPanelError);
      } else {
        resumeMaintenanceJob(job).catch(showPanelError);
      }
    }
  } finally { state.recoveringJobs = false; }
}

async function resumeQaJob(job) {
  const controller = new AbortController();
  state.qaStreamController = controller;
  qaAskBtn.disabled = true;
  let message;
  try {
    const data = await getJSON(`/api/qa/conversation?id=${encodeURIComponent(job.conversation_id)}`);
    state.activeQaConversation = data.conversation;
    state.qaMessages = (data.conversation.messages || []).filter(item => !item.pending);
    message = { id: makeClientId("qa"), role: "assistant", pending: true, content: "",
      progress: "恢复后台进度", started_at: (job.started_at || Date.now()/1000) * 1000,
      conversation_id: job.conversation_id };
    // A completed answer may already be in history; do not append it twice.
    if (job.status && job.status !== "running") {
      await window.WechatJobs.watch(job);
      return;
    }
    state.qaMessages.push(message);
    qaTitle.textContent = data.conversation.title;
    renderQaMessages();
    startQaTimer(message);
    await window.WechatJobs.watch(job, { signal: controller.signal,
      onEvent: event => handleQaStreamEvent(event, message) });
  } catch (error) {
    if (message) {
      message.pending = false;
      message.content = error.message;
      message.stopped = error.name === "AbortError";
      if (!message.stopped) message.error = error.message;
    }
  } finally {
    stopQaTimer(message);
    state.qaStreamController = null;
    qaAskBtn.disabled = false;
    qaAskBtn.textContent = "↑";
    // The server is the source of truth for completed, failed and cancelled answers.
    const data = await getJSON(`/api/qa/conversation?id=${encodeURIComponent(job.conversation_id)}`).catch(() => null);
    if (data?.conversation && state.activeQaConversation?.id === job.conversation_id) {
      state.qaMessages = data.conversation.messages || [];
      qaTitle.textContent = data.conversation.title || "新对话";
    }
    renderQaMessages();
    setQaProgress("");
    await loadQaConversations();
  }
}

async function resumeMaintenanceJob(job) {
  const bindings = {
    "/api/rag/prepare": ["preparationController", handlePreparationEvent],
    "/api/transcribe_all_voices_stream": ["voiceBatchController", handleVoiceBatchEvent],
    "/api/transcribe_chat_voices_stream": ["voiceBatchController", handleVoiceBatchEvent],
    "/api/rag/rebuild_stream": ["ragRebuildController", handleRagRebuildEvent],
    "/api/rag/embedding_rebuild_stream": ["ragEmbeddingController", handleRagEmbeddingRebuildEvent],
  };
  const [slot, onEvent] = bindings[job.kind] || [];
  if (slot && state[slot]) return;
  const controller = new AbortController();
  if (slot) state[slot] = controller;
  if (slot === "preparationController") {
    state.preparationRunning = true;
    state.preparationStopRequested = false;
  }
  renderPreparationControls();
  try {
    const result = await window.WechatJobs.watch(job, { signal: controller.signal, onEvent });
    if (job.kind === "/api/rag/search") renderRagSearchResult(result);
    if (slot === "preparationController") ragPrepareStatus.textContent = result.voice_failures
      ? `索引已更新 · ${result.voice_failures} 条语音转写失败，可重试` : "检索准备完成";
    if (job.kind === "/api/transcribe_voice" && state.activeChat) await refreshVisibleMessages();
  } catch (error) {
    if (slot === "preparationController") ragPrepareStatus.textContent = error.message;
    else if (slot === "voiceBatchController") voiceBatchStatus.textContent = error.message;
    else appendRagLog(error.message, "error");
  } finally {
    if (slot) state[slot] = null;
    if (slot === "preparationController") state.preparationRunning = false;
    await Promise.allSettled([loadVoiceStatus(), loadRagStatus()]);
    renderPreparationControls();
  }
}
