const state = {
  chats: [],
  activeChat: null,
  currentOffset: 0,
  currentLimit: 120,
  currentMessages: [],
  query: "",
  statusSummary: "",
  llmConfig: null,
  qaIndexStatus: null,
  qaMessages: [],
  qaConversations: [],
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
};

const mainTabs = document.querySelectorAll(".main-tab");
const views = document.querySelectorAll(".view");
const chatList = document.querySelector("#chatList");
const statusLine = document.querySelector("#statusLine");
const searchInput = document.querySelector("#searchInput");
const messagePane = document.querySelector("#messagePane");
const chatTitle = document.querySelector("#chatTitle");
const chatMeta = document.querySelector("#chatMeta");
const refreshBtn = document.querySelector("#refreshBtn");
const qaMeta = document.querySelector("#qaMeta");
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
const ragMeta = document.querySelector("#ragMeta");
const ragStatusGrid = document.querySelector("#ragStatusGrid");
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

async function getJSON(url) {
  const response = await fetch(url);
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || response.statusText);
  return data;
}

async function postJSON(url, payload) {
  const response = await fetch(url, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(payload),
  });
  const data = await response.json();
  if (!response.ok || data.ok === false) throw new Error(data.error || response.statusText);
  return data;
}

async function loadStatus() {
  const data = await getJSON("/api/status");
  state.statusSummary = `${data.total_chats} 个聊天 · ${data.total_messages} 条消息`;
  statusLine.textContent = state.statusSummary;
  renderQaMeta();
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
  const data = await getJSON("/api/rag/status");
  state.ragStatus = data;
  if (data.person_index) {
    state.qaIndexStatus = data.person_index;
    renderQaMeta();
  }
  renderRagStatus();
  if (data.search_index?.building) {
    window.setTimeout(() => loadRagStatus().catch(() => {}), 1600);
  }
}

function renderRagStatus() {
  if (!ragStatusGrid) return;
  const data = state.ragStatus || {};
  const person = data.person_index || {};
  const personStats = person.stats || {};
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
  const personReady = person.ready ? "已就绪" : person.building ? "构建中" : person.error ? "异常" : "未就绪";
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
  const strategyBits = [
    "联系人路由",
    semantic.ready ? "OpenAI Embedding 主召回" : "Embedding 待建",
    semantic.ready ? "SQLite 精确补漏" : "SQLite FTS/LIKE",
    "RRF 融合",
    retrieval.rerank_enabled ? "OpenAI Rerank" : "本地重排",
  ];
  ragMeta.textContent = [
    `联系人索引 ${personReady}`,
    `全文索引 ${searchReady}`,
    `语义索引 ${semanticReady}`,
    search.updated_at ? `更新 ${search.updated_at}` : "",
  ].filter(Boolean).join(" · ");
  ragStatusGrid.innerHTML = `
    <div class="rag-status-tile">
      <span>联系人路由</span>
      <strong>${escapeHtml(personReady)}</strong>
      <small>${formatNumber(personStats.people || 0)} 人 · ${formatNumber(personStats.messages || 0)} 条消息 · ${escapeHtml(personStats.cache_hit ? "缓存" : "新建")}</small>
    </div>
    <div class="rag-status-tile">
      <span>全文候选库</span>
      <strong>${escapeHtml(searchReady)}</strong>
      <small>${escapeHtml(searchDetail)}</small>
      <div class="rag-card-actions">
        <button id="ragRebuildBtn" type="button" data-rag-action="rebuild" ${search.building ? "disabled" : ""}>${search.building ? "更新中" : "增量更新索引"}</button>
        <button id="ragFullRebuildBtn" class="subtle-btn" type="button" data-rag-action="full-rebuild" ${search.building ? "disabled" : ""}>全量重建</button>
      </div>
    </div>
    <div class="rag-status-tile">
      <span>语义索引</span>
      <strong>${escapeHtml(semanticReady)}</strong>
      <small>${escapeHtml(semanticDetail)}</small>
      <div class="rag-card-actions">
        <button id="ragEmbeddingRebuildBtn" type="button" data-rag-action="embedding-rebuild" ${semantic.building ? "disabled" : ""}>${semantic.building ? "更新中" : "更新语义索引"}</button>
        <button id="ragEmbeddingFullRebuildBtn" class="subtle-btn" type="button" data-rag-action="embedding-full-rebuild" ${semantic.building ? "disabled" : ""}>重建语义</button>
      </div>
    </div>
    <div class="rag-status-tile">
      <span>检索策略</span>
      <strong>${escapeHtml(semantic.ready || retrieval.rerank_enabled ? "Hybrid + API" : "Hybrid")}</strong>
      <small>${escapeHtml(strategyBits.join(" · "))}</small>
      <div class="rag-card-actions">
        <button class="subtle-btn" type="button" data-rag-action="refresh">刷新状态</button>
      </div>
    </div>
  `;
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
  const params = new URLSearchParams({ limit: "1000" });
  if (state.query) params.set("q", state.query);
  const data = await getJSON(`/api/chats?${params}`);
  state.chats = data.chats;
  renderChats();
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
  settingsStatus.textContent = "配置就绪";
}

function renderVoiceStatus(data) {
  if (!data) {
    voiceBatchCount.textContent = "已转写 - / -";
    return;
  }
  voiceBatchCount.textContent = `已转写 ${data.transcribed} / ${data.total}`;
}

function resizeQaComposer() {
  qaQuestion.style.height = "auto";
  qaQuestion.style.height = `${Math.min(qaQuestion.scrollHeight, 160)}px`;
}

async function loadQaConversations() {
  const data = await getJSON("/api/qa/conversations");
  state.qaConversations = data.conversations || [];
  renderQaConversations();
  if (!state.activeQaConversation && state.qaConversations[0]) {
    await openQaConversation(state.qaConversations[0].id);
  }
}

function renderQaConversations() {
  if (!state.qaConversations.length) {
    qaConversationList.innerHTML = `<div class="qa-conversation-empty">暂无对话</div>`;
    return;
  }
  qaConversationList.innerHTML = state.qaConversations
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
            <button class="qa-conversation-rename" data-conversation="${escapeHtml(conv.id)}" data-title="${escapeHtml(title)}" type="button" aria-label="重命名" title="重命名">✎</button>
            <button class="qa-conversation-delete" data-conversation="${escapeHtml(conv.id)}" data-title="${escapeHtml(title)}" type="button" aria-label="删除" title="删除">⌫</button>
          </div>
        </div>
      `;
    })
    .join("");
  for (const item of qaConversationList.querySelectorAll(".qa-conversation-open")) {
    item.addEventListener("click", () => openQaConversation(item.dataset.conversation).catch(showPanelError));
  }
  for (const item of qaConversationList.querySelectorAll(".qa-conversation-rename")) {
    item.addEventListener("click", () => renameQaConversation(item.dataset.conversation, item.dataset.title).catch(showPanelError));
  }
  for (const item of qaConversationList.querySelectorAll(".qa-conversation-delete")) {
    item.addEventListener("click", () => deleteQaConversation(item.dataset.conversation, item.dataset.title).catch(showPanelError));
  }
}

async function newQaConversation() {
  if (guardActiveQaAnswer()) return;
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
  const data = await getJSON(`/api/qa/conversation?id=${encodeURIComponent(id)}`);
  state.activeQaConversation = data.conversation;
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
      const summary = chat.summary || `${chat.total_messages} 条消息`;
      return `
        <button class="chat-item${active}" data-chat="${escapeHtml(chat.id)}">
          <span class="avatar${typeClass}">${escapeHtml(avatarText(chat.title))}</span>
          <span class="chat-main">
            <span class="chat-row">
              <span class="chat-title">${escapeHtml(chat.title)}</span>
              <span class="chat-time">${escapeHtml(compactTime(chat.last_time))}</span>
            </span>
            <span class="chat-summary">${escapeHtml(summary)}</span>
          </span>
        </button>
      `;
    })
    .join("");

  for (const item of chatList.querySelectorAll(".chat-item")) {
    item.addEventListener("click", () => openChat(item.dataset.chat));
  }
}

async function openChat(chatId, offset = "") {
  const params = new URLSearchParams({ chat: chatId, limit: String(state.currentLimit) });
  if (offset !== "") params.set("offset", String(offset));
  messagePane.innerHTML = `<div class="empty-state">载入中</div>`;
  const data = await getJSON(`/api/messages?${params}`);
  state.activeChat = data.chat;
  state.currentOffset = data.offset;
  state.currentMessages = data.messages;
  chatTitle.textContent = data.chat.title;
  chatMeta.textContent = `${data.total} 条消息 · ${data.chat.first_time} 至 ${data.chat.last_time}`;
  renderChats();
  renderMessages(data);
  messagePane.scrollTop = messagePane.scrollHeight;
}

async function loadOlder() {
  if (!state.activeChat) return;
  const nextOffset = Math.max(0, state.currentOffset - state.currentLimit);
  const previousHeight = messagePane.scrollHeight;
  const params = new URLSearchParams({
    chat: state.activeChat.id,
    offset: String(nextOffset),
    limit: String(state.currentOffset - nextOffset),
  });
  const data = await getJSON(`/api/messages?${params}`);
  state.currentOffset = data.offset;
  state.currentMessages = [...data.messages, ...state.currentMessages];
  renderMessages({
    ...data,
    messages: state.currentMessages,
    has_more_before: data.offset > 0,
  });
  messagePane.scrollTop = messagePane.scrollHeight - previousHeight;
}

function renderMessages(data) {
  const blocks = [];
  if (data.has_more_before) {
    blocks.push(`<button id="loadOlderBtn" class="load-older">更早消息</button>`);
  }

  let lastDivider = "";
  data.messages.forEach((msg, index) => {
    const divider = dividerLabel(msg.time);
    if (divider && divider !== lastDivider) {
      blocks.push(`<div class="day-divider">${escapeHtml(divider)}</div>`);
      lastDivider = divider;
    }
    blocks.push(renderMessage(msg, data.chat.type, index));
  });

  messagePane.innerHTML = blocks.join("") || `<div class="empty-state">没有消息</div>`;
  const older = document.querySelector("#loadOlderBtn");
  if (older) older.addEventListener("click", loadOlder);
  bindVoiceTranscribeButtons();
  bindMediaFallbacks();
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
      <div class="mini-avatar">${escapeHtml(avatarText(msg.mine ? "我" : msg.sender || msg.sender_username))}</div>
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
      ? `<button class="voice-transcribe" data-message-index="${index}">转文字</button>`
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
    button.addEventListener("click", () => transcribeVoice(button));
  }
}

async function transcribeVoice(button) {
  const index = Number(button.dataset.messageIndex);
  const msg = state.currentMessages[index];
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
    media.transcription = result.text || "（未识别到文字）";
    media.note = "";
    renderMessages({
      chat: state.activeChat,
      messages: state.currentMessages,
      has_more_before: state.currentOffset > 0,
    });
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
  if (name === "settings" && !state.llmConfig) {
    loadLLMConfig().catch(showPanelError);
  }
  if (name === "settings") {
    loadVoiceStatus().catch(showPanelError);
  }
  if (name === "rag") {
    loadRagStatus().catch(showRagError);
  }
}

async function saveLLMConfig(event) {
  event.preventDefault();
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

async function transcribeAllVoices() {
  const controller = new AbortController();
  state.voiceBatchController = controller;
  voiceTranscribeAllBtn.disabled = true;
  voiceStopBatchBtn.disabled = false;
  voiceBatchStatus.textContent = "准备中";
  voiceBatchLog.innerHTML = "";
  try {
    const response = await fetch("/api/transcribe_all_voices_stream", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ force: false }),
      signal: controller.signal,
    });
    if (!response.ok || !response.body) {
      const data = await response.json().catch(() => ({}));
      throw new Error(data.error || response.statusText);
    }
    await consumeNDJSON(response, handleVoiceBatchEvent);
  } catch (error) {
    if (error.name === "AbortError") {
      voiceBatchStatus.textContent = "已停止";
      appendVoiceBatchLog("已停止");
    } else {
      voiceBatchStatus.textContent = error.message || "转写失败";
      appendVoiceBatchLog(error.message || "转写失败", "error");
    }
  } finally {
    state.voiceBatchController = null;
    voiceTranscribeAllBtn.disabled = false;
    voiceStopBatchBtn.disabled = true;
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
    loadVoiceStatus().catch(showPanelError);
    if (state.activeChat) {
      openChat(state.activeChat.id, state.currentOffset).catch(showError);
    }
    loadStatus().catch(showError);
  }
}

async function rebuildRagIndex(full = false) {
  const controller = new AbortController();
  const rebuildBtn = document.querySelector("#ragRebuildBtn");
  const fullRebuildBtn = document.querySelector("#ragFullRebuildBtn");
  state.ragRebuildController = controller;
  if (rebuildBtn) rebuildBtn.disabled = true;
  if (fullRebuildBtn) fullRebuildBtn.disabled = true;
  if (rebuildBtn) rebuildBtn.textContent = full ? "重建中" : "更新中";
  ragLog.innerHTML = "";
  appendRagLog(full ? "开始全量重建全文候选库" : "开始增量更新全文候选库");
  try {
    const response = await fetch("/api/rag/rebuild_stream", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ full }),
      signal: controller.signal,
    });
    if (!response.ok || !response.body) {
      const data = await response.json().catch(() => ({}));
      throw new Error(data.error || response.statusText);
    }
    await consumeNDJSON(response, handleRagRebuildEvent);
  } catch (error) {
    appendRagLog(error.name === "AbortError" ? "已停止" : error.message || "重建失败", "error");
  } finally {
    state.ragRebuildController = null;
    if (fullRebuildBtn) fullRebuildBtn.disabled = false;
    await loadRagStatus().catch(() => {});
  }
}

async function rebuildRagEmbeddingIndex(full = false) {
  const controller = new AbortController();
  const embeddingRebuildBtn = document.querySelector("#ragEmbeddingRebuildBtn");
  const embeddingFullRebuildBtn = document.querySelector("#ragEmbeddingFullRebuildBtn");
  state.ragEmbeddingController = controller;
  if (embeddingRebuildBtn) embeddingRebuildBtn.disabled = true;
  if (embeddingFullRebuildBtn) embeddingFullRebuildBtn.disabled = true;
  if (embeddingRebuildBtn) embeddingRebuildBtn.textContent = full ? "重建中" : "更新中";
  ragLog.innerHTML = "";
  appendRagLog(full ? "开始重建语义索引" : "开始更新语义索引");
  try {
    const response = await fetch("/api/rag/embedding_rebuild_stream", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ full }),
      signal: controller.signal,
    });
    if (!response.ok || !response.body) {
      const data = await response.json().catch(() => ({}));
      throw new Error(data.error || response.statusText);
    }
    await consumeNDJSON(response, handleRagEmbeddingRebuildEvent);
  } catch (error) {
    appendRagLog(error.name === "AbortError" ? "已停止" : error.message || "语义索引失败", "error");
  } finally {
    state.ragEmbeddingController = null;
    if (embeddingFullRebuildBtn) embeddingFullRebuildBtn.disabled = false;
    await loadRagStatus().catch(() => {});
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
    appendRagLog(`${mode}完成${stats.inserted !== undefined ? ` · 写入 ${formatNumber(stats.inserted)} 条` : ""}`);
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

async function consumeNDJSON(response, onEvent) {
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split("\n");
    buffer = lines.pop() || "";
    for (const line of lines) {
      if (line.trim()) onEvent(JSON.parse(line));
    }
  }
  if (buffer.trim()) onEvent(JSON.parse(buffer));
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
  if (state.qaStreamController) return;
  if (!state.activeQaConversation) {
    await newQaConversation();
  }
  const conversationId = state.activeQaConversation?.id || "";
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
    await streamQuestion(question, assistantMessage, conversationId);
  } catch (error) {
    assistantMessage.pending = false;
    assistantMessage.elapsed_ms = elapsedForMessage(assistantMessage);
    if (error.name === "AbortError") {
      assistantMessage.stopped = true;
      assistantMessage.content = assistantMessage.content || "已停止回答";
      saveActiveQaConversation().catch(() => {});
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

async function streamQuestion(question, assistantMessage, conversationId) {
  const controller = new AbortController();
  state.qaStreamController = controller;
  const history = state.qaMessages
    .filter((item) => !item.pending && !item.error)
    .slice(0, -1)
    .map((item) => ({ role: item.role, content: item.content }));
  const response = await fetch("/api/qa_stream", {
    method: "POST",
    headers: { "content-type": "application/json" },
    signal: controller.signal,
    body: JSON.stringify({ question, history, conversation_id: conversationId }),
  });
  if (!response.ok || !response.body) {
    const data = await response.json().catch(() => ({}));
    throw new Error(data.error || response.statusText);
  }
  await consumeNDJSON(response, (event) => handleQaStreamEvent(event, assistantMessage));
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
    assistantMessage.sources = event.sources || [];
    assistantMessage.context_count = event.context_count || 0;
    assistantMessage.retrieval = event.retrieval || null;
    if (event.conversation) {
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
        <div class="qa-answer-text">${escapeHtml(message.content || "")}</div>
        ${meta}
        ${sources}
      </div>
    </article>
  `;
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
    ? `引用片段 · ${retrievalBits.join(" · ")}`
    : `引用片段 · ${escapeHtml(message.context_count || 0)} 条`;
  const sources = (message.sources || [])
    .map((source) => {
      const meta = [source.chat_title, source.time, source.sender].filter(Boolean).join(" · ");
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

refreshBtn.addEventListener("click", () => {
  Promise.all([loadStatus(), loadChats()]).catch(showError);
});

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
  newQaConversation().catch(showPanelError);
});
voiceTranscribeAllBtn.addEventListener("click", transcribeAllVoices);
voiceStopBatchBtn.addEventListener("click", () => {
  state.voiceBatchController?.abort();
});
llmConfigForm.addEventListener("submit", saveLLMConfig);
ragStatusGrid.addEventListener("click", (event) => {
  const button = event.target.closest("button[data-rag-action]");
  if (!button || button.disabled) return;
  const action = button.dataset.ragAction;
  if (action === "refresh") {
    loadRagStatus().catch(showRagError);
  } else if (action === "rebuild") {
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

Promise.all([loadStatus(), loadChats(), loadLLMConfig(), loadVoiceStatus(), loadQaConversations(), loadQaIndexStatus(), loadRagStatus()])
  .then(() => {
    if (state.chats[0]) return openChat(state.chats[0].id);
  })
  .catch(showError);
