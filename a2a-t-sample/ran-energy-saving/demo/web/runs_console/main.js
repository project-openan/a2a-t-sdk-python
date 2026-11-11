/**
 * Internal run console.
 *
 * Lists persisted client runs and renders one run as a compact summary plus one merged, filtered
 * log stream. Payloads and raw files are fetched on demand so a long run never ships as one dump.
 * Everything is built with textContent: run data is untrusted and must never become markup.
 */

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
  decision_ungrounded: "决策值无确定性来源",
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
const LOG_SOURCE_LABELS = {
  wire: "wire",
  client: "client",
  server: "server",
  process: "进程",
};
const PHASE_LABELS = {
  dispatch: "预检",
  task_prompt: "Task-T",
  task_validation: "校验",
  task_modify: "Modify",
  negotiation: "协商",
  resolution: "决策",
  execution: "执行",
  report: "回调",
  done: "结束",
  unknown: "未知",
};
const PHASE_ORDER = [
  "dispatch",
  "task_prompt",
  "task_validation",
  "task_modify",
  "negotiation",
  "resolution",
  "execution",
  "report",
  "done",
  "unknown",
];
const PAGE_SIZE = 200;
const PAYLOAD_CACHE_LIMIT = 50;

const state = {
  meta: null,
  runs: [],
  total: 0,
  selectedRunId: null,
  summary: null,
  tab: "logs",
  loading: false,
  log: {
    entries: [],
    total: 0,
    nextOffset: null,
    phase: "",
    query: "",
    loading: false,
    requestSeq: 0,
  },
  payloadCache: new Map(),
  rawFiles: [],
  rawName: null,
  rawSeq: 0,
};

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

function formatClock(value) {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  const pad = (n) => String(n).padStart(2, "0");
  return `${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`;
}

export function formatDuration(ms) {
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

export function phaseLabel(value) {
  return PHASE_LABELS[value] || value || "未知";
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

/* ---------------------------------------------------------------- run list */

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
    if (run.downstreamContextId) downstreamCell.title = run.downstreamContextId;
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

/* ----------------------------------------------------------------- summary */

function idItem(label, value, { mono = true } = {}) {
  const wrap = el("span", "id");
  wrap.title = value || "";
  append(wrap, el("span", "muted", `${label} `), el("span", mono ? "mono" : "", shortId(value)));
  return wrap;
}

function renderSummary() {
  const payload = state.summary;
  if (!payload) return;
  const run = payload.run || {};
  $("detail-run-id").textContent = run.runId || "";

  const status = $("summary-status");
  clear(status);
  const statusClass = run.status === "finished" ? "finished" : run.status || "";
  append(
    status,
    el("span", statusClass, statusLabel(run.status)),
    el("span", "muted", " · "),
    el("span", "", finalStateLabel(run.finalState)),
  );
  if (run.error) append(status, el("span", " failed", ` · ${run.error}`));

  const metrics = payload.metrics || {};
  const latency = metrics.latency || {};
  const negotiation = metrics.negotiation || {};
  const metricParts = [
    protocolLabel(run.protocol),
    run.scenario,
    sourceLabel(run.sourceKind),
    formatDuration(run.durationMs ?? latency.totalMs),
    negotiation.rounds !== undefined ? `协商 ${negotiation.rounds} 轮` : "",
  ].filter(Boolean);
  $("summary-metrics").textContent = metricParts.join(" · ");

  const identity = $("summary-identity");
  clear(identity);
  append(
    identity,
    idItem("Run", run.runId),
    idItem("Conv", run.conversationId),
    idItem("trace", run.traceId),
  );
  if (run.serverTaskId) append(identity, idItem("Task", run.serverTaskId));
  if (run.serverContextId) append(identity, idItem("Context", run.serverContextId));
  if (run.downstreamTaskId) append(identity, idItem("下游 Task", run.downstreamTaskId));
  if (run.downstreamContextId) append(identity, idItem("下游 Context", run.downstreamContextId));

  const all = $("summary-all-body");
  clear(all);
  const list = el("dl", "kv");
  for (const [key, value] of Object.entries(run)) {
    if (value === null || value === undefined || value === "") continue;
    append(list, el("dt", "", key), el("dd", key === "runId" ? "mono" : "", valueText(value)));
  }
  all.appendChild(list);

  const links = $("detail-links");
  clear(links);
  const consoleBase = `/api/run-console/runs/${encodeURIComponent(run.runId)}`;
  for (const [label, href] of [
    ["transcript.json", `${consoleBase}/transcript.json`],
    ["transcript.md", `${consoleBase}/transcript.md`],
    ["Langfuse 导出", "/observe"],
  ]) {
    const link = el("a", "", label);
    link.href = href;
    link.target = "_blank";
    link.rel = "noopener";
    links.appendChild(link);
  }
}

async function loadSummary() {
  if (!state.selectedRunId) return;
  const payload = await fetchJson(
    `/api/run-console/runs/${encodeURIComponent(state.selectedRunId)}/summary`,
  );
  state.summary = payload;
  renderSummary();
  renderPhaseOptions();
}

/* ------------------------------------------------------------------ metrics */

function metricCard(label, value, hint) {
  const card = el("div", "metric");
  append(card, el("div", "label", label), el("div", "value", value));
  if (hint) append(card, el("div", "muted", hint));
  return card;
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
      const expected = el("td", "wrap-code");
      expected.appendChild(objectCell(check.expected));
      const actual = el("td", "wrap-code");
      actual.appendChild(objectCell(check.actual));
      append(
        row,
        el("td", "", name),
        el("td", check.passed === true ? "status finished" : "status failed", check.passed === true ? "通过" : "未通过"),
        expected,
        actual,
      );
    }
    body.appendChild(row);
  }
  append(table, body);
  append(card, table);
  return card;
}

function renderMetrics() {
  const panel = $("tab-metrics");
  clear(panel);
  const metrics = state.summary ? state.summary.metrics : null;
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

/* -------------------------------------------------------------- negotiation */

export function compactObject(value) {
  if (value === null || value === undefined) return "—";
  if (Array.isArray(value)) return `数组 · ${value.length} 项`;
  if (typeof value === "object") return `对象 · ${Object.keys(value).length} 个字段`;
  return valueText(value);
}

function objectCell(value) {
  if (value === null || value === undefined) return el("span", "", "—");
  if (typeof value !== "object") return el("span", "", valueText(value));
  const details = el("details", "cell-json");
  append(details, el("summary", "", compactObject(value)), el("pre", "", cap(pretty(value), 20000)));
  return details;
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
      const cell = el("td", "wrap-code");
      cell.appendChild(objectCell(getter(row)));
      tr.appendChild(cell);
    }
    body.appendChild(tr);
  }
  append(table, body);
  append(card, table);
  return card;
}

function renderNegotiation() {
  const panel = $("tab-negotiation");
  clear(panel);
  const payload = state.negotiation || {};
  append(
    panel,
    jsonTable("协议事件", payload.negotiationEvents || [], [
      ["序号", "num", (row) => row.seq],
      ["方向", "", (row) => row.direction],
      ["角色", "", (row) => row.actor],
      ["动作", "", (row) => row.performative || row.kind],
      ["轮次", "num", (row) => row.round],
      ["协商 ID", "", (row) => row.negotiationId],
      ["摘要", "", (row) => row.content],
    ]),
    jsonTable("客户端决策", payload.decisions || [], [
      ["序号", "num", (row) => row.seq],
      ["动作", "", (row) => row.action],
      ["轮次", "num", (row) => row.round],
      ["理由", "", (row) => row.rationale],
      ["补全参数", "", (row) => row.filledParams],
    ]),
    jsonTable("会话轮次", payload.turns || [], [
      ["序号", "num", (row) => row.seq],
      ["角色", "", (row) => row.role],
      ["时间", "", (row) => formatTime(row.createdAt)],
      ["内容", "", (row) => row.content],
    ]),
  );
}

async function loadNegotiation() {
  if (!state.selectedRunId) return;
  state.negotiation = await fetchJson(
    `/api/run-console/runs/${encodeURIComponent(state.selectedRunId)}/negotiation`,
  );
  renderNegotiation();
}

/* -------------------------------------------------------------------- logs */

function logSources() {
  return [...document.querySelectorAll(".source-toggles input:checked")].map(
    (input) => input.dataset.source,
  );
}

function renderPhaseOptions() {
  const select = $("log-phase");
  const selected = state.log.phase;
  clear(select);
  const any = el("option", "", "全部");
  any.value = "";
  select.appendChild(any);
  const counts = (state.summary && state.summary.counts && state.summary.counts.byPhase) || {};
  const phases = Object.keys(counts).sort((left, right) => {
    const leftIndex = PHASE_ORDER.indexOf(left);
    const rightIndex = PHASE_ORDER.indexOf(right);
    return (leftIndex < 0 ? 99 : leftIndex) - (rightIndex < 0 ? 99 : rightIndex);
  });
  for (const phase of phases) {
    const option = el("option", "", `${phaseLabel(phase)} (${counts[phase]})`);
    option.value = phase;
    select.appendChild(option);
  }
  if ([...select.options].some((option) => option.value === selected)) {
    select.value = selected;
  } else {
    state.log.phase = "";
  }
}

function logEntryNode(entry) {
  const details = el("details", "log-entry");
  const summary = el("summary");
  const phase = el(
    "span",
    `log-phase${entry.phaseInferred ? " inferred" : ""}`,
    phaseLabel(entry.phase),
  );
  phase.title = entry.phaseInferred ? `${entry.phase}（推断）` : entry.phase || "";
  append(
    summary,
    el("span", "log-time", formatClock(entry.ts)),
    phase,
    el("span", "log-source", LOG_SOURCE_LABELS[entry.source] || entry.source),
    (() => {
      const title = el("span", "log-title", entry.title || "—");
      if (entry.summary) title.appendChild(el("span", "muted", entry.summary));
      return title;
    })(),
  );
  const pre = el("pre", "", "");
  append(details, summary, pre);
  details.addEventListener("toggle", () => {
    if (details.open && entry.payloadAvailable) void fillPayload(entry, pre);
  });
  return details;
}

async function fillPayload(entry, pre) {
  if (pre.dataset.loaded === "1") return;
  const key = `${state.selectedRunId}:${entry.id}`;
  let payload = state.payloadCache.get(key);
  if (payload === undefined) {
    try {
      const response = await fetchJson(
        `/api/run-console/runs/${encodeURIComponent(state.selectedRunId)}/timeline/${encodeURIComponent(entry.id)}`,
      );
      payload = response;
      state.payloadCache.set(key, payload);
      if (state.payloadCache.size > PAYLOAD_CACHE_LIMIT) {
        const oldest = state.payloadCache.keys().next().value;
        state.payloadCache.delete(oldest);
      }
    } catch (error) {
      pre.textContent = `读取失败：${error instanceof Error ? error.message : String(error)}`;
      pre.dataset.loaded = "1";
      return;
    }
  }
  const text = payload.payload || "（无 payload）";
  pre.textContent = payload.payloadTruncated ? `${text}\n\n… 已截断（${payload.payloadBytes} 字节）` : text;
  pre.dataset.loaded = "1";
}

function renderLogs() {
  const list = $("log-list");
  clear(list);
  if (!state.log.entries.length) {
    list.appendChild(el("p", "log-empty", "没有匹配的日志。"));
  } else {
    for (const entry of state.log.entries) list.appendChild(logEntryNode(entry));
  }
  $("log-count").textContent = `显示 ${state.log.entries.length} / 共 ${state.log.total} 条`;
  $("log-total").textContent = state.log.nextOffset === null ? "已到末尾" : "还有更多";
  $("log-more").hidden = state.log.nextOffset === null;
}

function renderLogNotice(payload) {
  const notice = $("log-notice");
  const warnings = Array.isArray(payload.warnings) ? payload.warnings : [];
  if (!warnings.length) {
    notice.hidden = true;
    notice.textContent = "";
    return;
  }
  notice.hidden = false;
  notice.textContent = warnings.join("；");
}

async function loadLogs({ reset = false } = {}) {
  if (!state.selectedRunId) return;
  if (state.log.loading && !reset) return;
  const offset = reset ? 0 : state.log.nextOffset;
  if (offset === null && !reset) return;
  state.log.loading = true;
  const seq = ++state.log.requestSeq;
  const params = new URLSearchParams();
  const sources = logSources();
  if (sources.length) params.set("source", sources.join(","));
  if (state.log.phase) params.set("phase", state.log.phase);
  if (state.log.query) params.set("q", state.log.query);
  params.set("offset", String(offset || 0));
  params.set("limit", String(PAGE_SIZE));
  try {
    const payload = await fetchJson(
      `/api/run-console/runs/${encodeURIComponent(state.selectedRunId)}/timeline?${params}`,
    );
    if (seq !== state.log.requestSeq) return;
    if (reset) state.log.entries = [];
    state.log.entries = state.log.entries.concat(payload.entries || []);
    state.log.total = payload.total || 0;
    state.log.nextOffset = payload.nextOffset ?? null;
    renderLogNotice(payload);
    renderLogs();
    showNotice("");
  } catch (error) {
    showNotice(error instanceof Error ? error.message : String(error));
  } finally {
    state.log.loading = false;
  }
}

/* --------------------------------------------------------------- raw files */

function renderRawFiles() {
  const list = $("raw-files");
  clear(list);
  if (!state.rawFiles.length) {
    list.appendChild(el("p", "muted", "没有可用的原始文件。"));
    return;
  }
  for (const file of state.rawFiles) {
    const button = el("button", "raw-file", file.name);
    button.type = "button";
    if (file.name === state.rawName) button.classList.add("active");
    if (!file.available) {
      button.classList.add("unavailable");
      button.disabled = true;
      button.title = file.reason || "不可用";
    } else {
      button.title = `${file.name} · ${formatNumber(file.size)} 字节`;
      button.addEventListener("click", () => void selectRawFile(file.name));
    }
    list.appendChild(button);
  }
}

async function loadFiles() {
  if (!state.selectedRunId) return;
  const seq = ++state.rawSeq;
  try {
    const payload = await fetchJson(
      `/api/run-console/runs/${encodeURIComponent(state.selectedRunId)}/files`,
    );
    if (seq !== state.rawSeq) return;
    state.rawFiles = payload.files || [];
    renderRawFiles();
  } catch (error) {
    showNotice(error instanceof Error ? error.message : String(error));
  }
}

async function selectRawFile(name) {
  if (!state.selectedRunId) return;
  state.rawName = name;
  renderRawFiles();
  $("raw-title").textContent = name;
  $("raw-content").textContent = "读取中…";
  try {
    const response = await fetch(
      `/api/run-console/runs/${encodeURIComponent(state.selectedRunId)}/files/${encodeURIComponent(name)}`,
    );
    const text = await response.text();
    if (!response.ok) throw new Error(text || `HTTP ${response.status}`);
    const truncated = response.headers.get("X-Truncated") === "true";
    $("raw-title").textContent = truncated ? `${name}（已截断）` : name;
    $("raw-content").textContent = text;
  } catch (error) {
    $("raw-content").textContent = `读取失败：${error instanceof Error ? error.message : String(error)}`;
  }
}

/* -------------------------------------------------------------------- tabs */

function selectTab(tab) {
  state.tab = tab;
  for (const button of document.querySelectorAll("#tabs .tab")) {
    button.classList.toggle("active", button.dataset.tab === tab);
  }
  for (const name of ["logs", "metrics", "negotiation", "raw"]) {
    $(`tab-${name}`).hidden = name !== tab;
  }
  if (tab === "metrics") renderMetrics();
  if (tab === "negotiation") void loadNegotiation();
  if (tab === "raw" && !state.rawFiles.length) void loadFiles();
}

/* --------------------------------------------------------------- selection */

function resetDetailState() {
  state.summary = null;
  state.negotiation = null;
  state.log.entries = [];
  state.log.total = 0;
  state.log.nextOffset = null;
  state.log.phase = "";
  state.log.query = "";
  state.log.requestSeq += 1;
  state.log.loading = false;
  state.payloadCache.clear();
  state.rawFiles = [];
  state.rawName = null;
  $("log-query").value = "";
  $("log-phase").value = "";
  $("log-list").replaceChildren();
  $("log-notice").hidden = true;
  $("raw-files").replaceChildren();
  $("raw-title").textContent = "选择一个文件";
  $("raw-content").textContent = "";
  $("tab-metrics").replaceChildren();
  $("tab-negotiation").replaceChildren();
}

async function selectRun(runId) {
  state.selectedRunId = runId;
  renderRows();
  window.history.replaceState(null, "", `#run=${encodeURIComponent(runId)}`);
  $("detail-empty").hidden = true;
  $("detail").hidden = false;
  resetDetailState();
  try {
    await loadSummary();
    await loadLogs({ reset: true });
    if (state.tab === "metrics") renderMetrics();
    if (state.tab === "negotiation") await loadNegotiation();
    if (state.tab === "raw") await loadFiles();
  } catch (error) {
    showNotice(error instanceof Error ? error.message : String(error));
  }
}

async function refreshAll() {
  await loadRuns();
  if (!state.selectedRunId) return;
  try {
    await loadSummary();
    await loadLogs({ reset: true });
    if (state.tab === "metrics") renderMetrics();
    if (state.tab === "negotiation") await loadNegotiation();
    if (state.tab === "raw") await loadFiles();
  } catch (error) {
    showNotice(error instanceof Error ? error.message : String(error));
  }
}

/* --------------------------------------------------------------------- boot */

function bindEvents() {
  for (const id of ["filter-protocol", "filter-status", "filter-source", "filter-scenario", "filter-limit"]) {
    $(id).addEventListener("change", () => {
      if (id === "filter-protocol") refreshScenarios();
      void loadRuns();
    });
  }
  let queryTimer = null;
  $("filter-query").addEventListener("input", () => {
    window.clearTimeout(queryTimer);
    queryTimer = window.setTimeout(loadRuns, 250);
  });
  $("refresh").addEventListener("click", () => void refreshAll());
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
  for (const input of document.querySelectorAll(".source-toggles input")) {
    input.addEventListener("change", () => void loadLogs({ reset: true }));
  }
  $("log-phase").addEventListener("change", () => {
    state.log.phase = $("log-phase").value;
    void loadLogs({ reset: true });
  });
  let logTimer = null;
  $("log-query").addEventListener("input", () => {
    window.clearTimeout(logTimer);
    logTimer = window.setTimeout(() => {
      state.log.query = $("log-query").value.trim();
      void loadLogs({ reset: true });
    }, 250);
  });
  $("log-more").addEventListener("click", () => void loadLogs());
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
}

if (typeof document !== "undefined") {
  main();
}
