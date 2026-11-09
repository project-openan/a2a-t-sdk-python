/* Observe page: paste Langfuse trace ids, run the server-side export job, read the metrics rows.
   Single ES module, no build step. All DOM writes go through textContent (values come from the
   Langfuse API / local runs, never trusted markup). */

const STATUS_LABELS = {
  ok: "成功",
  not_found: "未找到",
  auth: "认证失败",
  network: "网络失败",
  error: "错误",
  timeout: "超时",
  running: "进行中",
  done: "已完成",
  pending: "等待中",
};

const ERROR_LABELS = {
  invalid_trace_id: "traceId 格式不正确",
  too_many_traces: "trace 数量超过上限",
  langfuse_not_configured: "Langfuse 未配置",
  observe_busy: "已有导出任务在运行",
};

const IDENTITY_COLUMNS = new Set(["runId", "caseId", "batchId", "chainId"]);
const RUN_ATTRIBUTE_COLUMNS = new Set([
  "protocol",
  "scenario",
  "runMode",
  "mode",
  "modelMode",
  "sourceKind",
]);
const TIMELINE_COLUMNS = new Set(["receivedAt", "sdkCalledAt", "requestSentAt", "taskCreatedAt"]);
const COUNT_COLUMNS = new Set([
  "observations",
  "runObservations",
  "clientObservations",
  "serverObservations",
  "generations",
  "toolCalls",
]);
const LOCAL_RUN_COLUMNS = new Set(["status", "finalState", "startedAt", "finishedAt"]);

export function statusLabel(status) {
  return STATUS_LABELS[status] || status || "—";
}

export function pollIntervalMs(count) {
  const total = Number.isFinite(Number(count)) ? Number(count) : 0;
  if (total >= 100) return 5000;
  if (total >= 20) return 2000;
  return 1000;
}

export function mergeResults(existing, incoming) {
  const bySeq = new Map();
  for (const item of [...(Array.isArray(existing) ? existing : []), ...(Array.isArray(incoming) ? incoming : [])]) {
    if (item === null || item === undefined) continue;
    const seq = Number(item.seq);
    if (!Number.isFinite(seq)) continue;
    bySeq.set(seq, item);
  }
  return [...bySeq.values()].sort((left, right) => Number(left.seq) - Number(right.seq));
}

const state = {
  meta: null,
  fields: null,
  job: null,
  results: [],
  nextSeq: 0,
  expanded: new Set(),
  pollTimer: null,
  busy: false,
};

const $ = (id) => document.getElementById(id);

function clear(node) {
  node.replaceChildren();
}

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined && text !== null) node.textContent = String(text);
  return node;
}

function showBanner(message) {
  const node = $("banner");
  node.textContent = message;
  node.hidden = false;
}

function hideBanner() {
  const node = $("banner");
  node.hidden = true;
  node.textContent = "";
}

function errorMessage(error) {
  const raw = String(error?.message || error || "未知错误");
  const label = ERROR_LABELS[raw];
  return label ? `${label}（${raw}）` : raw;
}

async function getJson(url) {
  const response = await fetch(url, { headers: { Accept: "application/json" } });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(payload.error || payload.detail || `${response.status} ${response.statusText}`);
  }
  return payload;
}

async function postJson(url, body) {
  const response = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "application/json" },
    body: JSON.stringify(body),
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(payload.error || payload.detail || `${response.status} ${response.statusText}`);
  }
  return payload;
}

/* ------------------------------------------------------------------ helpers */

function asObject(value) {
  return value !== null && typeof value === "object" && !Array.isArray(value) ? value : {};
}

function textOf(value) {
  if (value === null || value === undefined) return "";
  if (typeof value === "boolean") return value ? "true" : "false";
  return String(value);
}

function intText(value) {
  if (value === null || value === undefined || typeof value === "boolean") return "";
  if (typeof value === "number" && Number.isFinite(value)) return String(Math.round(value));
  if (typeof value === "string" && value.trim() !== "") {
    const parsed = Number(value);
    if (Number.isFinite(parsed)) return String(Math.round(parsed));
  }
  return "";
}

function stageMs(start, end) {
  const startMs = Date.parse(start);
  const endMs = Date.parse(end);
  if (!Number.isFinite(startMs) || !Number.isFinite(endMs)) return "";
  return String(Math.round(endMs - startMs));
}

function displayValue(value) {
  const text = value === null || value === undefined ? "" : String(value);
  return text === "" ? "—" : text;
}

function rowValue(result, column) {
  const metrics = asObject(result.metrics);
  const localRun = asObject(result.localRun);

  if (column === "traceId") return result.traceId || "";
  if (column === "exportStatus") return result.status || "";
  if (column === "warnings") {
    return (Array.isArray(result.warnings) ? result.warnings : []).join("; ");
  }
  if (result.status !== "ok") {
    // A failed trace keeps its identity columns only; everything else stays empty (design §5.1).
    if (IDENTITY_COLUMNS.has(column)) return textOf(localRun[column]);
    return "";
  }

  const timeline = asObject(metrics.timeline);
  const tokens = asObject(metrics.tokens);
  const negotiation = asObject(metrics.negotiation);
  const counts = asObject(metrics.counts);
  const latency = asObject(metrics.latency);

  if (column === "prepareMs") return stageMs(timeline.receivedAt, timeline.sdkCalledAt);
  if (column === "sdkMs") return stageMs(timeline.sdkCalledAt, timeline.requestSentAt);
  if (column === "dispatchMs") return stageMs(timeline.requestSentAt, timeline.taskCreatedAt);
  if (column === "endToEndMs") return intText(latency.endToEndMs);

  if (column === "input" || column === "cachedInput" || column === "output" || column === "total") {
    return intText(tokens[column]) || "0";
  }
  if (column.startsWith("client.") || column.startsWith("server.")) {
    const [side, key] = column.split(".");
    const sideTokens = asObject(asObject(tokens.bySide)[side]);
    if (Object.keys(sideTokens).length === 0) return "";
    return intText(sideTokens[key]) || "0";
  }
  if (column === "cacheHitRate") {
    const input = Number(tokens.input) || 0;
    const cached = Number(tokens.cachedInput) || 0;
    return input === 0 ? "" : (cached / input).toFixed(4);
  }
  if (column === "inputExcludingCached") {
    const input = Number(tokens.input) || 0;
    const cached = Number(tokens.cachedInput) || 0;
    return String(Math.max(0, input - cached));
  }

  if (column === "negotiationTriggered") return textOf(negotiation.triggered);
  if (column === "rounds") return intText(negotiation.rounds);
  if (column === "negotiationLatencyMs") return intText(negotiation.latencyMs);
  if (column === "serverDurationMs") return intText(negotiation.serverDurationMs);

  if (COUNT_COLUMNS.has(column)) return intText(counts[column]);

  if (column === "passed") return textOf(localRun.passed);
  if (column === "accuracy") {
    const accuracy = localRun.accuracy;
    return typeof accuracy === "number" ? accuracy.toFixed(4) : textOf(accuracy);
  }
  if (LOCAL_RUN_COLUMNS.has(column)) return textOf(localRun[column]);

  if (IDENTITY_COLUMNS.has(column)) return textOf(metrics[column] ?? localRun[column]);
  if (RUN_ATTRIBUTE_COLUMNS.has(column)) return textOf(metrics[column]);
  if (TIMELINE_COLUMNS.has(column)) return textOf(timeline[column]);

  return textOf(metrics[column] ?? localRun[column]);
}

function parseTraceIds(text) {
  return String(text || "")
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter((line) => line.length > 0);
}

function countTraceIds(text) {
  return parseTraceIds(text).length;
}

function autoGrowInput() {
  const area = $("trace-input");
  area.style.height = "auto";
  area.style.height = `${Math.max(64, Math.min(area.scrollHeight, 220))}px`;
}

function updateTraceCount() {
  const count = countTraceIds($("trace-input").value);
  const hint = count === 0 ? "" : count === 1 ? " · 可查询" : " · 可批量导出";
  $("trace-count").textContent = `${count} 条${hint}`;
}

function updateStartState() {
  const configured = Boolean(state.meta?.configured);
  const count = countTraceIds($("trace-input").value);
  const ready = configured && !state.busy;
  $("query-button").disabled = !ready || count !== 1;
  $("start-button").disabled = !ready || count < 2;
}

/* ------------------------------------------------------------- configuration */

function renderMeta(meta) {
  state.meta = meta;
  const configured = Boolean(meta?.configured);
  const pill = $("config-state");
  pill.textContent = configured ? "已配置" : "未配置";
  pill.className = `pill ${configured ? "ok" : "bad"}`;

  $("config-host").textContent = meta?.hostLabel ? `host=${meta.hostLabel}` : "";
  $("tool-version").textContent = meta?.toolVersion ? `tool ${meta.toolVersion}` : "";
  $("config-detail").textContent = configured
    ? `Langfuse 指标读取已就绪；单次最多 ${meta?.maxTraces ?? "?"} 条 trace。`
    : "请设置 LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY 后重启服务。";
  const uiBase = String(meta?.uiBaseUrl || "");
  $("tls-hint").textContent = uiBase === ""
    ? "TLS：未知（未配置 Langfuse UI 地址）"
    : uiBase.startsWith("https://")
      ? "TLS：已启用"
      : "TLS：未启用（HTTP）";

  updateStartState();
  if (meta?.runningJobId && !state.job) {
    void openJob(meta.runningJobId);
  }
}

async function loadMeta() {
  try {
    renderMeta(await getJson("/api/observe/meta"));
  } catch (error) {
    showBanner(`无法读取观测配置：${errorMessage(error)}`);
    renderMeta(null);
  }
}

/* ---------------------------------------------------------------- trace runs */

function protocolLabel(protocol) {
  if (protocol === "a2a-t") return "A2A-T";
  if (protocol === "a2a") return "A2A";
  return protocol || "—";
}

function describeRun(run) {
  const parts = [];
  if (run.caseId !== null && run.caseId !== undefined) parts.push(`case ${run.caseId}`);
  if (run.batchId) parts.push(`batch ${run.batchId}`);
  if (run.scenario) parts.push(run.scenario);
  parts.push(`status ${run.status || "—"}`);
  if (run.createdAt) parts.push(run.createdAt);
  return parts.join(" · ");
}

function appendTrace(traceId) {
  const area = $("trace-input");
  const current = area.value.trim();
  area.value = current === "" ? traceId : `${current}\n${traceId}`;
  autoGrowInput();
  updateTraceCount();
  updateStartState();
}

function renderRuns(runs) {
  const list = $("run-list");
  clear(list);
  $("runs-summary").textContent = `${runs.length} 条`;
  if (runs.length === 0) {
    list.append(el("p", "empty", "没有可用的本地 run。"));
    return;
  }
  for (const run of runs) {
    const traceId = run.traceId ? String(run.traceId) : "";
    const item = el("button", "run-item");
    item.type = "button";
    item.disabled = traceId === "";
    item.title = traceId === "" ? "该 run 没有可用的 traceId" : `追加 ${traceId}`;
    item.append(el("span", "run-line run-id", run.runId || "(no run id)"));
    item.append(el("span", "run-line run-sub", `${protocolLabel(run.protocol)} · ${describeRun(run)}`));
    if (traceId !== "") item.append(el("span", "run-line run-trace", traceId));
    item.addEventListener("click", () => appendTrace(traceId));
    list.append(item);
  }
}

async function loadRuns() {
  try {
    const payload = await getJson("/api/observe/runs?limit=50");
    renderRuns(Array.isArray(payload.runs) ? payload.runs : []);
  } catch (error) {
    $("runs-summary").textContent = "读取失败";
    showBanner(`无法读取最近 run：${errorMessage(error)}`);
  }
}

/* ------------------------------------------------------------------- fields */

function renderFields(payload) {
  state.fields = payload;
  const body = $("legend-body");
  clear(body);
  const fields = Array.isArray(payload?.fields) ? payload.fields : [];
  if (fields.length === 0) {
    const cell = el("td", "empty", "没有字段说明。");
    cell.colSpan = 5;
    const row = el("tr");
    row.append(cell);
    body.append(row);
    return;
  }
  for (const field of fields) {
    const row = el("tr");
    row.append(el("td", "mono", field.column || "—"));
    row.append(el("td", "", field.group || "—"));
    row.append(el("td", "wrap", field.meaningZh || "—"));
    row.append(el("td", "wrap", field.source || "—"));
    row.append(el("td", "wrap", field.caveat || "—"));
    body.append(row);
  }
  renderResults();
}

async function loadFields() {
  try {
    renderFields(await getJson("/api/observe/fields"));
  } catch (error) {
    const body = $("legend-body");
    clear(body);
    const cell = el("td", "empty", "字段说明读取失败。");
    cell.colSpan = 5;
    const row = el("tr");
    row.append(cell);
    body.append(row);
    showBanner(`无法读取字段说明：${errorMessage(error)}`);
  }
}

/* --------------------------------------------------------------------- jobs */

function nextSeqAfter(results) {
  let seq = 0;
  for (const result of results) {
    const value = Number(result?.seq);
    if (Number.isFinite(value) && value >= seq) seq = value + 1;
  }
  return seq;
}

function adoptJob(job, results) {
  state.job = job;
  state.results = mergeResults([], results);
  const next = Number(job?.nextSeq);
  state.nextSeq = Number.isFinite(next) ? next : nextSeqAfter(state.results);
  state.expanded = new Set();
  renderJob();
}

function jobBase(jobId) {
  return `/api/observe/jobs/${encodeURIComponent(jobId)}`;
}

function stopPolling() {
  if (state.pollTimer !== null) {
    window.clearTimeout(state.pollTimer);
    state.pollTimer = null;
  }
}

function startPolling() {
  stopPolling();
  if (!state.job || state.job.status === "done") return;
  const total = Number(state.job.total);
  const count = Number.isFinite(total) ? total : state.results.length;
  state.pollTimer = window.setTimeout(() => {
    void pollJob();
  }, pollIntervalMs(count));
}

async function pollJob() {
  state.pollTimer = null;
  const jobId = state.job?.jobId;
  if (!jobId) return;
  try {
    const payload = await getJson(`${jobBase(jobId)}?since=${state.nextSeq}`);
    if (state.job?.jobId !== jobId) return;
    state.job = { ...state.job, ...payload };
    state.results = mergeResults(state.results, payload.results);
    const next = Number(payload.nextSeq);
    state.nextSeq = Number.isFinite(next) ? next : nextSeqAfter(state.results);
    renderJob();
    if (payload.status === "done") {
      stopPolling();
      void loadJobs();
    } else {
      startPolling();
    }
  } catch (error) {
    showBanner(`任务进度刷新失败：${errorMessage(error)}`);
    state.pollTimer = window.setTimeout(() => {
      void pollJob();
    }, 5000);
  }
}

async function openJob(jobId) {
  if (!jobId) return;
  stopPolling();
  hideBanner();
  state.job = { jobId };
  state.results = [];
  state.nextSeq = 0;
  state.expanded = new Set();
  try {
    const payload = await getJson(`${jobBase(jobId)}?since=0`);
    if (state.job?.jobId !== jobId) return;
    adoptJob(payload, payload.results);
    if (payload.status !== "done") startPolling();
  } catch (error) {
    showBanner(`无法读取 job ${jobId}：${errorMessage(error)}`);
  }
}

async function startExport(action = "batch") {
  if (state.busy) return;
  const traceIds = parseTraceIds($("trace-input").value);
  if (traceIds.length === 0) {
    showBanner("请输入 traceId。");
    return;
  }
  if (action === "query" && traceIds.length !== 1) {
    showBanner("查询只接受一个 traceId；多个请用“批量导出”。");
    return;
  }
  if (action === "batch" && traceIds.length < 2) {
    showBanner("批量导出至少需要两个 traceId；单个请用“查询”。");
    return;
  }
  const maxTraces = Number(state.meta?.maxTraces);
  if (Number.isFinite(maxTraces) && maxTraces > 0 && traceIds.length > maxTraces) {
    showBanner(`一次最多处理 ${maxTraces} 条 trace，当前输入 ${traceIds.length} 条。`);
    return;
  }
  state.busy = true;
  hideBanner();
  updateStartState();
  try {
    const job = await postJson("/api/observe/jobs", {
      traceIds,
      full: Boolean($("full-export").checked),
    });
    adoptJob(job, job?.results);
    if (job?.status !== "done") startPolling();
  } catch (error) {
    const noun = action === "query" ? "查询" : "导出任务";
    showBanner(`创建${noun}失败：${errorMessage(error)}`);
  } finally {
    state.busy = false;
    updateStartState();
    void loadJobs();
  }
}

function formatCount(value) {
  const number = Number(value);
  return Number.isFinite(number) ? String(number) : "—";
}

function renderJob() {
  const job = state.job;
  const progress = $("progress");
  progress.hidden = !job;
  if (job) {
    const total = Number(job.total);
    const done = Number(job.done ?? state.results.length);
    const failed = Number(job.failed);
    const percent = Number.isFinite(total) && total > 0
      ? Math.min(100, Math.round(((Number.isFinite(done) ? done : 0) / total) * 100))
      : 0;
    $("progress-bar").style.width = `${percent}%`;
    $("progress-text").textContent = `${formatCount(done)}/${formatCount(total)}${Number.isFinite(failed) && failed > 0 ? ` · 失败 ${failed}` : ""}`;
    $("job-summary").textContent = `${statusLabel(job.status)} · job ${job.jobId}${job.full ? " · 完整导出" : ""}`;
  } else {
    $("job-summary").textContent = "尚未开始";
  }
  renderDownloads();
  renderResults();
}

function renderDownloads() {
  const done = state.job?.status === "done";
  $("download-metrics").disabled = !done;
  $("download-rounds").disabled = !done;
  $("download-fields").disabled = !done;
  $("download-table").disabled = !done;
}

function downloadCsv(kind) {
  const jobId = state.job?.jobId;
  if (!jobId || state.job?.status !== "done") return;
  const base = jobBase(jobId);
  if (kind === "metrics") window.location.href = `${base}/metrics.csv`;
  else if (kind === "rounds") window.location.href = `${base}/rounds.csv`;
  else if (kind === "fields") window.location.href = `${base}/fields.csv`;
  else if (kind === "table") window.location.href = `${base}/table.xlsx`;
}

function archiveUrl(jobId, traceId) {
  return `${jobBase(jobId)}/archives/${encodeURIComponent(traceId)}.zip`;
}

/* ------------------------------------------------------------------ results */

function toggleDetail(seq) {
  const key = String(seq);
  if (state.expanded.has(key)) state.expanded.delete(key);
  else state.expanded.add(key);
  renderResults();
}

function detailRow(result, columnCount) {
  const row = el("tr", "detail-row");
  const cell = el("td");
  cell.colSpan = Math.max(1, columnCount);
  const box = el("div", "detail-box");
  const warnings = Array.isArray(result.warnings) ? result.warnings : [];
  box.append(el("span", warnings.length > 0 ? "warn" : "dim", `警告：${warnings.join("; ") || "无"}`));
  const duration = Number(result.durationMs);
  box.append(el("span", "dim", `耗时：${Number.isFinite(duration) ? `${Math.round(duration)} ms` : "—"}`));
  if (result.archive) {
    const link = el("button", "link-button", `下载完整导出 zip（${result.traceId}）`);
    link.type = "button";
    link.addEventListener("click", () => {
      if (state.job?.jobId) window.location.href = archiveUrl(state.job.jobId, result.traceId);
    });
    box.append(link);
  }
  cell.append(box);
  row.append(cell);
  return row;
}

function renderResults() {
  const head = $("results-head");
  const body = $("results-body");
  clear(head);
  clear(body);
  const columns = Array.isArray(state.fields?.mainColumns) ? state.fields.mainColumns : [];
  if (columns.length === 0) {
    const cell = el("td", "empty", "字段定义读取中或读取失败。");
    const row = el("tr");
    row.append(cell);
    body.append(row);
    return;
  }
  for (const column of columns) head.append(el("th", "", column));
  if (state.results.length === 0) {
    const cell = el("td", "empty", "这个 job 还没有结果。");
    cell.colSpan = columns.length;
    const row = el("tr");
    row.append(cell);
    body.append(row);
    return;
  }
  for (const result of state.results) {
    const row = el("tr", result.status === "ok" ? "" : "row-error");
    for (const column of columns) {
      const cell = el("td", column === "traceId" ? "mono" : "", displayValue(rowValue(result, column)));
      row.append(cell);
    }
    row.addEventListener("click", () => toggleDetail(result.seq));
    body.append(row);
    if (state.expanded.has(String(result.seq))) body.append(detailRow(result, columns.length));
  }
}

/* ------------------------------------------------------------------ history */

function renderJobs(jobs) {
  const body = $("jobs-body");
  clear(body);
  $("jobs-summary").textContent = `${jobs.length} 条`;
  if (jobs.length === 0) {
    const cell = el("td", "empty", "还没有导出 job。");
    cell.colSpan = 7;
    const row = el("tr");
    row.append(cell);
    body.append(row);
    return;
  }
  for (const job of jobs) {
    const row = el("tr");
    row.append(el("td", "mono", job.jobId || "—"));
    row.append(el("td", "", job.createdAt || "—"));
    row.append(el("td", "", job.full ? "是" : "否"));
    row.append(el("td", "", formatCount(job.total)));
    row.append(el("td", "", formatCount(job.done)));
    row.append(el("td", "", formatCount(job.failed)));
    row.append(el("td", "", statusLabel(job.status)));
    row.addEventListener("click", () => {
      void openJob(job.jobId);
    });
    body.append(row);
  }
}

async function loadJobs() {
  try {
    const payload = await getJson("/api/observe/jobs?limit=20");
    renderJobs(Array.isArray(payload.jobs) ? payload.jobs : []);
  } catch (error) {
    $("jobs-summary").textContent = "读取失败";
    showBanner(`无法读取历史 job：${errorMessage(error)}`);
  }
}

/* --------------------------------------------------------------------- boot */

function boot() {
  $("trace-input").addEventListener("input", () => {
    autoGrowInput();
    updateTraceCount();
    updateStartState();
  });
  $("clear-button").addEventListener("click", () => {
    $("trace-input").value = "";
    autoGrowInput();
    updateTraceCount();
    updateStartState();
  });
  $("query-button").addEventListener("click", () => {
    void startExport("query");
  });
  $("start-button").addEventListener("click", () => {
    void startExport("batch");
  });
  $("download-metrics").addEventListener("click", () => downloadCsv("metrics"));
  $("download-rounds").addEventListener("click", () => downloadCsv("rounds"));
  $("download-fields").addEventListener("click", () => downloadCsv("fields"));
  $("download-table").addEventListener("click", () => downloadCsv("table"));

  updateTraceCount();
  updateStartState();
  renderJob();
  void loadMeta();
  void loadFields();
  void loadRuns();
  void loadJobs();
}

if (typeof document !== "undefined") {
  boot();
}
