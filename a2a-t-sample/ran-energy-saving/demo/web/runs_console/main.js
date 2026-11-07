/**
 * Internal run console.
 *
 * Lists persisted client runs and renders one run's metrics, timeline, negotiation events, wire
 * payloads and request source. Everything is built with textContent: run data is untrusted and
 * must never become markup.
 */

const state = {
  meta: null,
  runs: [],
  total: 0,
  selectedRunId: null,
  detail: null,
  tab: "overview",
  refreshTimer: null,
  loading: false,
  detailSeq: 0,
};

const PROTOCOL_LABELS = { "a2a-t": "A2A-T", pure: "A2A", plain: "plain" };
const STATUS_LABELS = {
  running: "运行中",
  finished: "已完成",
  failed: "失败",
  interrupted: "中断",
};
const FINAL_STATE_LABELS = {
  task_completed: "任务完成",
  task_failed: "任务失败",
  input_invalid: "输入校验未通过",
  negotiation_aborted: "协商中止",
  negotiation_rejected: "协商拒绝",
  input_required: "等待输入",
};
const SOURCE_LABELS = {
  presentation: "演示页",
  explorer: "Run Explorer",
  tester: "测试页",
  gateway: "A2A 网关",
  api: "API",
  cli: "CLI",
};
const SOURCE_FIELDS = [
  ["kind", "来源类型"],
  ["channel", "入口通道"],
  ["remoteAddress", "客户端地址"],
  ["realIp", "X-Real-IP"],
  ["forwardedFor", "X-Forwarded-For"],
  ["host", "Host"],
  ["origin", "Origin"],
  ["referer", "Referer"],
  ["userAgent", "User-Agent"],
  ["acceptLanguage", "Accept-Language"],
  ["requestId", "请求 ID"],
  ["entryPath", "入口路径"],
  ["receivedAt", "接收时间"],
  ["batchId", "测试批次"],
  ["batchName", "批次文件"],
  ["caseId", "用例编号"],
  ["caseName", "用例名称"],
  ["taskId", "网关 Task"],
  ["contextId", "网关 Context"],
  ["messageId", "网关消息"],
];

const $ = (id) => document.getElementById(id);

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined && text !== null) node.textContent = String(text);
  return node;
}

function append(parent, ...children) {
  for (const child of children) {
    if (child !== null && child !== undefined) parent.appendChild(child);
  }
  return parent;
}

function clear(node) {
  while (node.firstChild) node.removeChild(node.firstChild);
}

function tableHead(table, headRow) {
  const thead = el("thead");
  thead.appendChild(headRow);
  table.appendChild(thead);
}

/** Short form of one id for a dense cell; the full value stays in the row title. */
function shortId(value) {
  const text = String(value || "");
  return text.length > 13 ? `${text.slice(0, 8)}…` : text;
}

function valueText(value) {
  if (value === null || value === undefined || value === "") return "—";
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

function pretty(value) {
  try {
    return JSON.stringify(value, null, 2);
  } catch (error) {
    return String(value);
  }
}

function cap(text, limit = 400000) {
  if (text.length <= limit) return text;
  return `${text.slice(0, limit)}\n\n… 已截断（${text.length} 字符）`;
}

function formatTime(value) {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  const pad = (n) => String(n).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} `
    + `${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`;
}

function formatDuration(ms) {
  if (typeof ms !== "number" || Number.isNaN(ms)) return "—";
  if (ms < 1000) return `${Math.round(ms)} ms`;
  if (ms < 60000) return `${(ms / 1000).toFixed(1)} s`;
  const minutes = Math.floor(ms / 60000);
  const seconds = Math.round((ms % 60000) / 1000);
  return `${minutes}m ${seconds}s`;
}

function formatNumber(value) {
  return typeof value === "number" && Number.isFinite(value) ? value.toLocaleString() : "—";
}

function percent(value) {
  return typeof value === "number" && Number.isFinite(value)
    ? `${(value * 100).toFixed(1)}%`
    : "—";
}

function protocolLabel(value) {
  return PROTOCOL_LABELS[value] || value || "—";
}

function statusLabel(value) {
  return STATUS_LABELS[value] || value || "—";
}

function finalStateLabel(value) {
  return FINAL_STATE_LABELS[value] || value || "—";
}

function sourceLabel(value) {
  return SOURCE_LABELS[value] || (value ? value : "未知");
}

async function fetchJson(url) {
  const response = await fetch(url, { headers: { accept: "application/json" } });
  if (response.status === 401 || response.status === 403) {
    throw new Error("未认证：请用带 ?token=… 的链接打开，或关闭 UI_TOKEN。");
  }
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(payload.error || `HTTP ${response.status}`);
  }
  return payload;
}

function showNotice(message) {
  const notice = $("notice");
  if (!message) {
    notice.hidden = true;
    notice.textContent = "";
    return;
  }
  notice.textContent = message;
  notice.hidden = false;
}

function filters() {
  const params = new URLSearchParams();
  const pairs = [
    ["protocol", $("filter-protocol").value],
    ["status", $("filter-status").value],
    ["source", $("filter-source").value],
    ["scenario", $("filter-scenario").value],
    ["query", $("filter-query").value.trim()],
    ["limit", $("filter-limit").value],
  ];
  for (const [key, value] of pairs) {
    if (value) params.set(key, value);
  }
  return params;
}

function sourceBadge(kind, channel) {
  const badge = el("span", "source-badge", sourceLabel(kind));
  if (channel) badge.title = channel;
  return badge;
}

/** Human name of the far side this run's server forwarded the task to. */
function targetName(run) {
  const hop = run.downstreamHop || (run.discovery === "nms" ? "nms" : "ran");
  return hop === "ran" ? "RAN Agent" : "NMS";
}

/** Label the contexts by the hop each one belongs to.
 *
 * A2A-T and the gateway talk to the RAN/NMS directly, so their server context is the target's.
 * A pure client run talks to the OSS Agent first; its server context belongs to that hop and the
 * downstream fields carry the NMS/RAN Task the OSS continued on its own side.
 */
function contextLabels(run) {
  const target = targetName(run);
  if (run.protocol === "a2a-t" || run.sourceKind === "gateway") {
    return { server: `${target} Context`, serverTask: `${target} Task`, downstream: null };
  }
  return {
    server: "OSS Context",
    serverTask: "OSS Task",
    downstream: `${target} Context`,
    downstreamTask: `${target} Task`,
  };
}

function statusNode(value) {
  const status = el("span", `status ${value || ""}`, statusLabel(value));
  status.title = value || "";
  return status;
}

function renderRows() {
  const body = $("runs-body");
  clear(body);
  if (!state.runs.length) {
    const row = el("tr");
    const cell = el("td", "empty", "没有匹配的运行。");
    cell.colSpan = 9;
    append(row, cell);
    body.appendChild(row);
    return;
  }
  for (const run of state.runs) {
    const row = el("tr");
    if (run.runId === state.selectedRunId) row.classList.add("selected");
    row.addEventListener("click", () => selectRun(run.runId));

    append(row, el("td", "mono", formatTime(run.createdAt)));
    const statusCell = el("td");
    append(statusCell, statusNode(run.status));
    if (run.finalState) statusCell.appendChild(el("div", "muted", finalStateLabel(run.finalState)));
    append(row, statusCell);
    append(row, el("td", "", protocolLabel(run.protocol)));
    append(row, el("td", "", run.scenario));
    const sourceCell = el("td");
    append(sourceCell, sourceBadge(run.sourceKind, run.sourceChannel));
    append(row, sourceCell);
    append(row, el("td", "num", formatDuration(run.durationMs)));
    append(
      row,
      el("td", "num", run.tokenAvailable ? formatNumber(run.totalTokens) : "—"),
    );
    const downstreamCell = el(
      "td",
      "mono",
      run.downstreamContextId ? shortId(run.downstreamContextId) : "—",
    );
    if (run.downstreamContextId) {
      downstreamCell.title = `${contextLabels(run).downstream} ${run.downstreamContextId}`;
    }
    append(row, downstreamCell);
    const requestCell = el("td", "request");
    const request = el("div", "request-text", run.userRequest || "—");
    request.title = run.userRequest || "";
    append(requestCell, request);
    append(row, requestCell);
    body.appendChild(row);
  }
}

async function loadRuns() {
  if (state.loading) return;
  state.loading = true;
  try {
    const payload = await fetchJson(`/api/run-console/runs?${filters().toString()}`);
    state.runs = payload.runs || [];
    state.total = payload.total || 0;
    $("run-count").textContent = `显示 ${state.runs.length} / 共 ${state.total} 条`;
    renderRows();
    showNotice("");
  } catch (error) {
    showNotice(error instanceof Error ? error.message : String(error));
  } finally {
    state.loading = false;
  }
}

async function loadMeta() {
  const meta = await fetchJson("/api/run-console/meta");
  state.meta = meta;
  const dbState = $("db-state");
  dbState.textContent = meta.enabled ? "数据库：已连接" : "数据库：不可用";
  dbState.classList.add(meta.enabled ? "ok" : "bad");
  $("meta-path").textContent = meta.enabled ? meta.database || "" : "客户端 SQLite 未启用";

  const protocol = $("filter-protocol");
  for (const value of meta.protocols || []) {
    const option = el("option", "", protocolLabel(value));
    option.value = value;
    protocol.appendChild(option);
  }
  const status = $("filter-status");
  for (const value of meta.statuses || []) {
    const option = el("option", "", statusLabel(value));
    option.value = value;
    status.appendChild(option);
  }
  const source = $("filter-source");
  for (const value of meta.sourceKinds || []) {
    const option = el("option", "", sourceLabel(value));
    option.value = value;
    source.appendChild(option);
  }
}

function currentScenarios() {
  const meta = state.meta || {};
  const protocol = $("filter-protocol").value;
  const groups = meta.scenariosByProtocol || {};
  if (protocol && Array.isArray(groups[protocol])) return groups[protocol];
  const merged = new Set();
  for (const values of Object.values(groups)) {
    for (const value of values || []) merged.add(value);
  }
  return [...merged].sort();
}

function refreshScenarios() {
  const select = $("filter-scenario");
  const selected = select.value;
  clear(select);
  const any = el("option", "", "全部");
  any.value = "";
  select.appendChild(any);
  for (const value of currentScenarios()) {
    const option = el("option", "", value);
    option.value = value;
    select.appendChild(option);
  }
  if ([...select.options].some((option) => option.value === selected)) {
    select.value = selected;
  }
}

function kvCard(title, pairs) {
  const card = el("section", "card");
  append(card, el("h3", "", title));
  const list = el("dl", "kv");
  for (const [label, value, mono] of pairs) {
    append(list, el("dt", "", label), el("dd", mono ? "mono" : "", valueText(value)));
  }
  append(card, list);
  return card;
}

function metricCard(label, value, hint) {
  const card = el("div", "metric");
  append(card, el("div", "label", label), el("div", "value", value));
  if (hint) append(card, el("div", "muted", hint));
  return card;
}

function detailStatus(detail) {
  return (detail.live && detail.live.status) || detail.status || "";
}

function renderOverview(detail) {
  const panel = $("tab-overview");
  clear(panel);
  const source = detail.source && typeof detail.source === "object" ? detail.source : {};
  const live = detail.live || {};
  const task = detail.taskSnapshot || {};
  const sourcePairs = SOURCE_FIELDS
    .filter(([key]) => source[key] !== undefined && source[key] !== null && source[key] !== "")
    .map(([key, label]) => [label, source[key], ["remoteAddress", "receivedAt"].includes(key)]);
  if (!sourcePairs.length) sourcePairs.push(["来源", "旧运行未记录来源", false]);

  append(
    panel,
    kvCard("基本", [
      ["Run ID", detail.runId, true],
      ["Conversation", detail.conversationId, true],
      ["Turn", detail.turnId, true],
      ["外部会话", detail.externalConversationId, true],
      ["调用方", detail.userId, false],
      ["协议", protocolLabel(detail.protocol), false],
      ["场景", detail.scenario, false],
      ["模式", detail.mode, false],
      ["传输", detail.transport, false],
      ["发现", detail.discovery, false],
      ["意图模式", detail.intentMode, false],
      ["状态", statusLabel(live.status || detail.status), false],
      ["最终状态", finalStateLabel(detail.finalState), false],
      ["错误", detail.error, false],
      ["创建", formatTime(detail.createdAt), false],
      ["更新", formatTime(detail.updatedAt), false],
      ["完成", formatTime(detail.finishedAt), false],
      ["幂等键", detail.idempotencyKey, true],
    ]),
    kvCard("请求", [
      ["用户请求", detail.userRequest, false],
      ...contextRows(detail),
      ["Task 快照状态", task.state, false],
      ["Task 快照更新", formatTime(task.updatedAt), false],
    ]),
    kvCard("请求来源", sourcePairs),
  );
}

/** Context/Task rows of one run, labelled by hop so two A2A contexts are never conflated. */
function contextRows(detail) {
  const labels = contextLabels(detail);
  const rows = [
    [labels.server, detail.serverContextId, true],
    [labels.serverTask, detail.serverTaskId, true],
  ];
  if (labels.downstream) {
    rows.push([labels.downstream, detail.downstreamContextId, true]);
    rows.push([labels.downstreamTask, detail.downstreamTaskId, true]);
  }
  return rows;
}

function renderTokens(tokens) {
  const card = el("section", "card");
  append(card, el("h3", "", "Token 用量"));
  if (!tokens || tokens.available !== true) {
    append(
      card,
      el("p", "muted", tokens && tokens.scripted ? "离线脚本，无真实用量。" : "供应商未上报用量。"),
    );
    return card;
  }
  const grid = el("div", "metrics-grid");
  append(
    grid,
    metricCard("总量", formatNumber(tokens.total)),
    metricCard("Prompt", formatNumber(tokens.prompt)),
    metricCard("Completion", formatNumber(tokens.completion)),
    metricCard("Reasoning", formatNumber(tokens.reasoning)),
    metricCard("缓存命中", formatNumber(tokens.cached)),
    metricCard("缓存命中率", percent(tokens.cacheHitRate), `${formatNumber(tokens.cachedCalls)} 次调用上报`),
    metricCard("LLM 调用", formatNumber(tokens.llmCalls)),
    metricCard("缺失用量", formatNumber(tokens.missingCalls)),
  );
  append(card, grid);

  const bySide = tokens.bySide || {};
  const entries = Object.entries(bySide);
  if (entries.length) {
    const table = el("table", "grid");
    const head = el("tr");
    for (const [label, align] of [
      ["侧", ""], ["Prompt", "num"], ["Completion", "num"], ["Total", "num"],
      ["缓存", "num"], ["命中率", "num"], ["调用", "num"],
    ]) {
      append(head, el("th", align, label));
    }
    tableHead(table, head);
    const body = el("tbody");
    for (const [side, bucket] of entries) {
      const row = el("tr");
      append(
        row,
        el("td", "", side === "client" ? "客户端" : side === "server" ? "服务端" : side),
        el("td", "num", formatNumber(bucket.prompt)),
        el("td", "num", formatNumber(bucket.completion)),
        el("td", "num", formatNumber(bucket.total)),
        el("td", "num", formatNumber(bucket.cached)),
        el("td", "num", percent(bucket.cacheHitRate)),
        el("td", "num", formatNumber(bucket.calls)),
      );
      body.appendChild(row);
    }
    append(table, body);
    append(card, el("h4", "", "按侧拆分"), table);
  }
  return card;
}

function renderLatency(latency) {
  const card = el("section", "card");
  append(card, el("h3", "", "耗时"));
  const grid = el("div", "metrics-grid");
  append(
    grid,
    metricCard("总耗时", formatDuration(latency.totalMs)),
    metricCard("模型", formatDuration(latency.modelMs)),
    metricCard("工具", formatDuration(latency.toolMs)),
    metricCard("审批等待", formatDuration(latency.approvalWaitMs)),
    metricCard("其他编排", formatDuration(latency.otherMs)),
    metricCard("首个审批", formatDuration(latency.timeToApprovalMs)),
    metricCard("LLM 调用", formatNumber(latency.llmCalls)),
    metricCard("工具调用", formatNumber(latency.toolCalls)),
  );
  append(card, grid);
  return card;
}

function renderAccuracy(accuracy) {
  const card = el("section", "card");
  append(card, el("h3", "", "准确率"));
  append(
    card,
    el(
      "p",
      "",
      `得分 ${percent(accuracy.score)} · ${accuracy.passed === true ? "通过" : accuracy.passed === false ? "未通过" : "未评估"}`,
    ),
  );
  const checks = accuracy.checks && typeof accuracy.checks === "object" ? accuracy.checks : {};
  const entries = Object.entries(checks);
  if (!entries.length) return card;
  const table = el("table", "grid");
  const head = el("tr");
  for (const label of ["检查", "结果", "期望", "实际"]) append(head, el("th", "", label));
  tableHead(table, head);
  const body = el("tbody");
  for (const [name, check] of entries) {
    const row = el("tr");
    if (check === null || typeof check !== "object") {
      append(row, el("td", "", name), el("td", "", "不适用"), el("td", "", "—"), el("td", "", "—"));
    } else {
      append(
        row,
        el("td", "", name),
        el("td", check.passed === true ? "status finished" : "status failed", check.passed === true ? "通过" : "未通过"),
        el("td", "wrap-code", pretty(check.expected)),
        el("td", "wrap-code", pretty(check.actual)),
      );
    }
    body.appendChild(row);
  }
  append(table, body);
  append(card, table);
  return card;
}

function renderMetrics(detail) {
  const panel = $("tab-metrics");
  clear(panel);
  const metrics = detail.metrics;
  if (!metrics) {
    append(panel, el("p", "muted", "这次运行还没有持久化指标（运行中或数据库写入失败）。"));
    return;
  }
  append(
    panel,
    renderTokens(metrics.tokens || {}),
    renderLatency(metrics.latency || {}),
    renderAccuracy(metrics.accuracy || {}),
    kvCard("协商与模型", [
      ["协商轮次", metrics.negotiation ? metrics.negotiation.rounds : null, false],
      ["协商消息", metrics.negotiation ? metrics.negotiation.messages : null, false],
      ["结局", metrics.negotiation ? metrics.negotiation.outcome : null, false],
      ["模型模式", metrics.modelMode, false],
      ["模型", metrics.modelLabel, false],
      ["开始", formatTime(metrics.startedAt), false],
      ["结束", formatTime(metrics.finishedAt), false],
    ]),
  );
}

function transcriptOf(detail) {
  return detail.transcript || (detail.live && detail.live.transcript) || {};
}

function eventNode(event, index) {
  const details = el("details", "event");
  const summary = el("summary");
  const seq = event.seq !== undefined ? `#${event.seq}` : `#${index + 1}`;
  append(
    summary,
    el("span", "seq", seq),
    el("span", "kind", event.kind || event.performative || "event"),
    el("span", "", event.phase || ""),
    el("span", "", event.title || event.summary || ""),
    event.status ? el("span", `status ${event.status}`, event.status) : null,
    event.durationMs !== undefined ? el("span", "muted", formatDuration(event.durationMs)) : null,
    event.model ? el("span", "muted", event.model) : null,
    event.tokens && typeof event.tokens.total === "number"
      ? el("span", "muted", `${event.tokens.total} tok`)
      : null,
  );
  append(details, summary, el("pre", "", cap(pretty(event), 20000)));
  return details;
}

function renderTimeline(detail) {
  const panel = $("tab-timeline");
  clear(panel);
  const events = transcriptOf(detail).events || [];
  if (!events.length) {
    append(panel, el("p", "muted", "没有可展示的时间线事件。"));
    return;
  }
  for (const [index, event] of events.entries()) {
    append(panel, eventNode(event, index));
  }
}

function jsonTable(title, rows, columns) {
  const card = el("section", "card");
  append(card, el("h3", "", title));
  if (!rows || !rows.length) {
    append(card, el("p", "muted", "无记录。"));
    return card;
  }
  const table = el("table", "grid");
  const head = el("tr");
  for (const [label, align] of columns) append(head, el("th", align, label));
  tableHead(table, head);
  const body = el("tbody");
  for (const row of rows) {
    const tr = el("tr");
    for (const [, , getter] of columns) {
      const value = getter(row);
      const cell = el("td", "", typeof value === "object" && value !== null ? pretty(value) : valueText(value));
      cell.classList.add("wrap-code");
      tr.appendChild(cell);
    }
    body.appendChild(tr);
  }
  append(table, body);
  append(card, table);
  return card;
}

function renderNegotiation(detail) {
  const panel = $("tab-negotiation");
  clear(panel);
  const transcript = transcriptOf(detail);
  append(
    panel,
    jsonTable("协议事件", detail.negotiationEvents || [], [
      ["序号", "num", (row) => row.seq],
      ["方向", "", (row) => row.direction],
      ["角色", "", (row) => row.actor],
      ["动作", "", (row) => row.performative || row.kind],
      ["轮次", "num", (row) => row.round],
      ["协商 ID", "", (row) => row.negotiationId],
      ["摘要", "", (row) => row.content],
    ]),
    jsonTable("客户端决策", transcript.decisions || [], [
      ["序号", "num", (row) => row.seq],
      ["动作", "", (row) => row.action],
      ["轮次", "num", (row) => row.round],
      ["动作类型", "", (row) => row.performative],
      ["理由", "", (row) => row.rationale],
      ["补全参数", "", (row) => row.filledParams],
    ]),
    jsonTable("会话轮次", detail.turns || [], [
      ["序号", "num", (row) => row.seq],
      ["角色", "", (row) => row.role],
      ["时间", "", (row) => formatTime(row.createdAt)],
      ["内容", "", (row) => row.content],
    ]),
  );
}

function renderWire(detail) {
  const panel = $("tab-wire");
  clear(panel);
  const records = detail.wire || [];
  if (!records.length) {
    append(panel, el("p", "muted", "这次运行没有捕获到报文（wire.jsonl 为空）。"));
    return;
  }
  for (const record of records) {
    const details = el("details", "wire");
    const summary = el("summary");
    append(
      summary,
      el("span", "muted", valueText(record.ts)),
      el("span", "method", valueText(record.method || record.kind)),
      el("span", "", valueText(record.path || record.url || record.summary || "")),
    );
    append(details, summary, el("pre", "", cap(pretty(record), 50000)));
    append(panel, details);
  }
}

function renderRaw(detail) {
  const panel = $("tab-raw");
  clear(panel);
  append(panel, el("pre", "", cap(pretty(detail))));
}

function renderDetail() {
  const detail = state.detail;
  if (!detail) return;
  $("detail-empty").hidden = true;
  $("detail").hidden = false;
  $("detail-run-id").textContent = detail.runId || "";
  $("detail-subtitle").textContent = [
    protocolLabel(detail.protocol),
    detail.scenario,
    statusLabel(detailStatus(detail)),
    finalStateLabel(detail.finalState),
    sourceLabel(detail.source ? detail.source.kind : ""),
    `创建 ${formatTime(detail.createdAt)}`,
  ].filter(Boolean).join(" · ");

  const links = $("detail-links");
  clear(links);
  const liveBase = `/api/runs/${encodeURIComponent(detail.runId)}`;
  const consoleBase = `/api/run-console/runs/${encodeURIComponent(detail.runId)}`;
  const entries = [
    ["transcript.json", `${consoleBase}/transcript.json`],
    ["transcript.md", `${consoleBase}/transcript.md`],
    ["wire", `${liveBase}/wire`],
  ];
  if (detail.live) entries.push(["trace.json", `${liveBase}/trace.json`]);
  for (const [label, href] of entries) {
    const link = el("a", "", label);
    link.href = href;
    link.target = "_blank";
    link.rel = "noopener";
    links.appendChild(link);
  }

  renderOverview(detail);
  renderMetrics(detail);
  renderTimeline(detail);
  renderNegotiation(detail);
  renderWire(detail);
  renderRaw(detail);
  selectTab(state.tab);
}

function selectTab(tab) {
  state.tab = tab;
  for (const button of document.querySelectorAll("#tabs .tab")) {
    button.classList.toggle("active", button.dataset.tab === tab);
  }
  for (const name of ["overview", "metrics", "timeline", "negotiation", "wire", "raw"]) {
    $(`tab-${name}`).hidden = name !== tab;
  }
}

async function loadDetail(runId) {
  const seq = ++state.detailSeq;
  try {
    const detail = await fetchJson(`/api/run-console/runs/${encodeURIComponent(runId)}`);
    if (seq !== state.detailSeq) return;
    state.detail = detail;
    renderDetail();
    showNotice("");
  } catch (error) {
    if (seq !== state.detailSeq) return;
    showNotice(error instanceof Error ? error.message : String(error));
  }
}

async function selectRun(runId) {
  state.selectedRunId = runId;
  renderRows();
  window.history.replaceState(null, "", `#run=${encodeURIComponent(runId)}`);
  await loadDetail(runId);
}

function selectedRunIsLive() {
  const detail = state.detail;
  if (!detail) return false;
  const status = detailStatus(detail);
  return status === "running" || Boolean(detail.live);
}

function scheduleRefresh() {
  if (state.refreshTimer) window.clearInterval(state.refreshTimer);
  state.refreshTimer = null;
  if (!$("auto-refresh").checked) return;
  state.refreshTimer = window.setInterval(async () => {
    await loadRuns();
    if (state.selectedRunId && selectedRunIsLive()) {
      await loadDetail(state.selectedRunId);
    }
  }, 5000);
}

function bindEvents() {
  for (const id of ["filter-protocol", "filter-status", "filter-source", "filter-scenario", "filter-limit"]) {
    $(id).addEventListener("change", () => {
      if (id === "filter-protocol") refreshScenarios();
      loadRuns();
    });
  }
  let queryTimer = null;
  $("filter-query").addEventListener("input", () => {
    window.clearTimeout(queryTimer);
    queryTimer = window.setTimeout(loadRuns, 250);
  });
  $("refresh").addEventListener("click", () => loadRuns());
  $("auto-refresh").addEventListener("change", scheduleRefresh);
  $("copy-run-id").addEventListener("click", async () => {
    if (!state.selectedRunId) return;
    try {
      await navigator.clipboard.writeText(state.selectedRunId);
      $("copy-run-id").textContent = "已复制";
      window.setTimeout(() => { $("copy-run-id").textContent = "复制"; }, 1200);
    } catch (error) {
      showNotice("复制失败：浏览器不允许访问剪贴板。");
    }
  });
  for (const button of document.querySelectorAll("#tabs .tab")) {
    button.addEventListener("click", () => selectTab(button.dataset.tab));
  }
}

async function main() {
  bindEvents();
  try {
    await loadMeta();
  } catch (error) {
    showNotice(error instanceof Error ? error.message : String(error));
  }
  refreshScenarios();
  await loadRuns();
  const hash = new URLSearchParams(window.location.hash.slice(1));
  const initial = hash.get("run");
  if (initial) await selectRun(initial);
  scheduleRefresh();
}

main();
