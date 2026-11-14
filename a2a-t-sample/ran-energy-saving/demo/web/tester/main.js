/* Tester console: catalog-driven case selection, staged execution and diffs. */

const STAGE_ORDER = ["intent", "task_t", "validate", "send", "negotiate", "accepted", "working", "final"];
const STAGE_LABELS = {
  intent: "意图",
  task_t: "Task-T",
  validate: "校验",
  send: "发送",
  negotiate: "协商",
  accepted: "受理",
  working: "运行",
  final: "终态",
};
const SDK_STAGES = new Set(["intent", "task_t", "validate"]);
const STATUS_LABELS = {
  pending: "待执行",
  running: "执行中",
  passed: "通过",
  failed: "失败",
  error: "错误",
  partial: "部分确认",
  waiting: "待 WORKING",
  recorded: "仅记录",
  skipped: "跳过",
  cancelled: "已取消",
  draft: "已创建",
  finished: "已完成",
  blocked: "拦截",
  accepted: "已受理",
  pass: "不拦截",
  pass_with_terminology: "不拦截+术语",
  unfilled: "未填写",
};

const state = {
  meta: null,
  catalog: [],
  selected: new Set(),
  filters: { query: "", dimension: "", operation: "", validate: "", tag: "" },
  batch: null,
  history: [],
  polling: null,
  roundFilter: "",
  tab: "catalog",
  drawer: { item: null, tab: "result", caseDoc: null, loading: false },
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

function statusLabel(status) {
  return STATUS_LABELS[status] || status || "—";
}

function badge(status, text) {
  const mapped = {
    blocked: "failed",
    accepted: "passed",
    pass: "passed",
    pass_with_terminology: "passed",
    unfilled: "skipped",
    recorded: "skipped",
  }[status] || status;
  return el("span", `badge ${mapped || "pending"}`, text || statusLabel(status));
}

function showToast(message, kind = "") {
  const node = $("toast");
  node.textContent = message;
  node.className = `toast ${kind}`.trim();
  node.hidden = false;
  window.clearTimeout(showToast.timer);
  showToast.timer = window.setTimeout(() => {
    node.hidden = true;
  }, 5000);
}

async function getJson(url) {
  const response = await fetch(url);
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(payload.error || response.statusText);
  return payload;
}

async function postJson(url, body) {
  const response = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body ?? {}),
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(payload.error || response.statusText);
  return payload;
}

/* ------------------------------------------------------------------ catalog */

async function loadMeta() {
  try {
    state.meta = await getJson("/api/tester/meta");
  } catch {
    state.meta = null;
  }
  const meta = state.meta;
  $("nms-warning").hidden = meta?.nmsEnabled !== true;
  $("catalog-count").textContent = meta ? `${meta.catalogCount} 条用例` : "";
  const stopAfter = $("config-stop-after");
  clear(stopAfter);
  for (const stage of meta?.stopAfter || STAGE_ORDER) {
    const option = el("option", "", `${STAGE_LABELS[stage] || stage}（${stage}）`);
    option.value = stage;
    stopAfter.append(option);
  }
  stopAfter.value = meta?.defaults?.stopAfter || "working";
  $("config-approval").value = meta?.defaults?.approval || "";
  $("config-timeout").value = meta?.defaults?.timeoutSeconds ?? "";
  $("config-working-timeout").value = meta?.defaults?.workingTimeoutSeconds ?? "";
  const concurrency = $("config-concurrency");
  concurrency.max = String(meta?.maxConcurrency || 1);
  concurrency.value = String(meta?.defaults?.concurrency || 1);
  const rounds = $("config-rounds");
  rounds.max = String(meta?.maxRounds || 1);
  rounds.value = String(meta?.defaults?.rounds || 1);
  syncRoundsKeepTasks();
  const liveEnabled = (meta?.modelMode || "") !== "offline";
  const decision = $("config-decision");
  decision.value = meta?.defaults?.decisionMode || "scripted";
  decision.querySelector('option[value="live"]').disabled = !liveEnabled;
  if (!liveEnabled) decision.title = "离线模式只能使用 scripted";
  $("scratch-decision").querySelector('option[value="live"]').disabled = !liveEnabled;
}

function syncRoundsKeepTasks() {
  const multi = Number($("config-rounds").value || 1) > 1;
  const keep = $("config-keep-tasks");
  keep.disabled = multi;
  if (multi) keep.checked = false;
  keep.title = multi ? "多轮批次不支持保留任务" : "";
}

async function loadCatalog() {
  try {
    const payload = await getJson("/api/tester/catalog");
    state.catalog = Array.isArray(payload.cases) ? payload.cases : [];
  } catch (error) {
    state.catalog = [];
    showToast(`加载用例目录失败：${error.message || error}`, "error");
  }
  populateFilters();
  renderCatalog();
}

function populateFilters() {
  const dimensions = new Set();
  const tags = new Set();
  for (const item of state.catalog) {
    if (item.dimension) dimensions.add(item.dimension);
    for (const tag of item.tags || []) tags.add(tag);
  }
  const dimensionSelect = $("filter-dimension");
  for (const value of [...dimensions].sort()) {
    const option = el("option", "", value);
    option.value = value;
    dimensionSelect.append(option);
  }
  const tagSelect = $("filter-tag");
  for (const value of [...tags].sort()) {
    const option = el("option", "", value);
    option.value = value;
    tagSelect.append(option);
  }
}

function filteredCatalog() {
  const filters = state.filters;
  const query = filters.query.trim().toLowerCase();
  return state.catalog.filter((item) => {
    if (query && !String(item.id).includes(query) && !item.name.toLowerCase().includes(query)) return false;
    if (filters.dimension && item.dimension !== filters.dimension) return false;
    if (filters.operation && item.operation !== filters.operation) return false;
    if (filters.validate) {
      const marker = item.validate || "unfilled";
      if (marker !== filters.validate) return false;
    }
    if (filters.tag && !(item.tags || []).includes(filters.tag)) return false;
    return true;
  });
}

function renderCatalog() {
  const rows = filteredCatalog();
  $("filter-count").textContent = `${rows.length}/${state.catalog.length}`;
  const list = $("catalog-list");
  clear(list);
  if (!rows.length) {
    list.append(el("p", "status-line", "没有匹配的用例。"));
    updateSelectionSummary();
    return;
  }
  for (const item of rows) {
    const row = el("label", `catalog-item${state.selected.has(item.id) ? " selected" : ""}`);
    const check = document.createElement("input");
    check.type = "checkbox";
    check.checked = state.selected.has(item.id);
    check.addEventListener("change", () => {
      if (check.checked) state.selected.add(item.id);
      else state.selected.delete(item.id);
      renderCatalog();
    });
    const body = el("span", "catalog-body");
    body.append(
      el("span", "catalog-title", `${String(item.id).padStart(2, "0")} ${item.name}`),
      el("span", "catalog-meta", `${item.operation} · ${item.validateLabel || "未填写"}`),
    );
    const tags = el("span", "catalog-tags");
    for (const tag of item.tags || []) tags.append(el("span", "pill", tag));
    body.append(tags);
    const detail = el("button", "catalog-detail", "详情");
    detail.type = "button";
    detail.title = "查看用例详情（不运行）";
    detail.addEventListener("click", (event) => {
      // The row is a <label>; prevent the click from toggling the selection checkbox.
      event.preventDefault();
      event.stopPropagation();
      openCasePreview(item);
    });
    row.append(check, body, detail);
    list.append(row);
  }
  updateSelectionSummary();
}

function updateSelectionSummary() {
  const count = state.selected.size;
  $("selection-summary").textContent = count ? `已选择 ${count} 条用例` : "未选择用例";
}

/* ------------------------------------------------------------------- batch */

async function startBatch() {
  const caseIds = [...state.selected];
  if (!caseIds.length) {
    showToast("请先在左侧选择用例。", "error");
    return;
  }
  const protocols = [];
  if ($("protocol-a2at").checked) protocols.push("a2a-t");
  if ($("protocol-a2a").checked) protocols.push("a2a");
  if (!protocols.length) {
    showToast("至少选择一个协议。", "error");
    return;
  }
  const rounds = Number($("config-rounds").value || 1);
  const runs = caseIds.length * protocols.length * rounds;
  const runLabel = rounds > 1 ? `${runs} 个运行项（${rounds} 轮）` : `${runs} 个运行项`;
  if (state.meta?.nmsEnabled && !window.confirm(`当前连接真实网管，将执行 ${runLabel}。确认继续？`)) {
    return;
  }
  $("start-button").disabled = true;
  try {
    const created = await postJson("/api/tester/batches", {
      caseIds,
      protocols,
      stopAfter: $("config-stop-after").value,
      approval: $("config-approval").value || null,
      decisionMode: $("config-decision").value,
      timeoutSeconds: $("config-timeout").value === "" ? null : Number($("config-timeout").value),
      workingTimeoutSeconds:
        $("config-working-timeout").value === "" ? null : Number($("config-working-timeout").value),
      keepTasks: $("config-keep-tasks").checked,
      concurrency: Number($("config-concurrency").value || 1),
      rounds,
    });
    state.batch = await postJson(`/api/tester/batches/${encodeURIComponent(created.batchId)}/start`, {});
    renderResults();
    startPolling();
  } catch (error) {
    showToast(`启动失败：${error.message || error}`, "error");
  } finally {
    $("start-button").disabled = false;
  }
}

async function startScratch() {
  const userInput = $("scratch-input").value.trim();
  if (!userInput) {
    $("scratch-status").textContent = "请输入请求文本。";
    return;
  }
  if (state.meta?.nmsEnabled && !window.confirm("当前连接真实网管，可能下发真实任务。确认继续？")) {
    return;
  }
  $("scratch-status").textContent = "正在启动…";
  try {
    const created = await postJson("/api/tester/scratch", {
      userInput,
      protocol: $("scratch-protocol").value,
      approval: $("scratch-approval").value,
      decisionMode: $("scratch-decision").value,
      timeoutSeconds: Number($("scratch-timeout").value) || 300,
      keepTasks: $("config-keep-tasks").checked,
    });
    state.batch = await postJson(`/api/tester/batches/${encodeURIComponent(created.batchId)}/start`, {});
    $("scratch-status").textContent = "已启动；结果见“评测用例”页。";
    switchTab("catalog");
    renderResults();
    startPolling();
  } catch (error) {
    $("scratch-status").textContent = `启动失败：${error.message || error}`;
  }
}

function startPolling() {
  if (state.polling !== null) return;
  state.polling = window.setInterval(refreshBatch, 1000);
}

function stopPolling() {
  if (state.polling === null) return;
  window.clearInterval(state.polling);
  state.polling = null;
}

async function refreshBatch() {
  if (!state.batch?.batchId) return;
  try {
    const payload = await getJson(`/api/tester/batches/${encodeURIComponent(state.batch.batchId)}`);
    state.batch = payload;
    renderResults();
    refreshOpenDrawer();
    if (payload.status !== "running") {
      stopPolling();
      void loadHistory();
    }
  } catch (error) {
    stopPolling();
    showToast(`批次状态刷新失败：${error.message || error}`, "error");
  }
}

async function openBatch(batchId) {
  if (!batchId) return;
  stopPolling();
  try {
    state.batch = await getJson(`/api/tester/batches/${encodeURIComponent(batchId)}`);
    renderResults();
    if (state.batch.status === "running") startPolling();
  } catch (error) {
    showToast(`加载批次失败：${error.message || error}`, "error");
  }
}

async function cancelBatch() {
  if (!state.batch?.batchId) return;
  try {
    await postJson(`/api/tester/batches/${encodeURIComponent(state.batch.batchId)}/cancel`, {});
    await refreshBatch();
  } catch (error) {
    showToast(`取消失败：${error.message || error}`, "error");
  }
}

async function retryBatch() {
  if (!state.batch?.batchId) return;
  try {
    const created = await postJson(`/api/tester/batches/${encodeURIComponent(state.batch.batchId)}/retry`, {});
    state.batch = await postJson(`/api/tester/batches/${encodeURIComponent(created.batchId)}/start`, {});
    renderResults();
    startPolling();
  } catch (error) {
    showToast(`重跑失败：${error.message || error}`, "error");
  }
}

async function cancelItem(index) {
  if (!state.batch?.batchId) return;
  try {
    await postJson(
      `/api/tester/batches/${encodeURIComponent(state.batch.batchId)}/items/${index}/cancel`,
      {},
    );
    await refreshBatch();
  } catch (error) {
    showToast(`取消失败：${error.message || error}`, "error");
  }
}

async function rerunItem(index) {
  if (!state.batch?.batchId) return;
  try {
    state.batch = await postJson(
      `/api/tester/batches/${encodeURIComponent(state.batch.batchId)}/items/${index}/rerun`,
      {},
    );
    renderResults();
    startPolling();
  } catch (error) {
    showToast(`重跑失败：${error.message || error}`, "error");
  }
}

function exportBatch(format) {
  if (!state.batch?.batchId) return;
  window.location.href = `/api/tester/batches/${encodeURIComponent(state.batch.batchId)}/export.${format}`;
}

async function loadHistory() {
  try {
    const payload = await getJson("/api/tester/batches?limit=20");
    state.history = Array.isArray(payload.batches) ? payload.batches : [];
  } catch {
    state.history = [];
  }
  const wrap = $("history-wrap");
  const select = $("history-select");
  clear(select);
  wrap.hidden = state.history.length === 0;
  for (const batch of state.history) {
    const mode = batch.decisionMode ? ` · ${batch.decisionMode}` : "";
    const transport = batch.transport ? ` · ${batch.transport}` : "";
    const keep = batch.keepTasks ? " · 保留任务" : "";
    const concurrency = batch.concurrency > 1 ? ` · 并发 ${batch.concurrency}` : "";
    const rounds = batch.rounds > 1 ? ` · ${batch.rounds} 轮` : "";
    const option = el("option", "", `${batch.stopAfter || "final"}${mode}${transport}${keep}${concurrency}${rounds} · ${statusLabel(batch.status)} · ${batch.createdAt || ""}`);
    option.value = batch.batchId;
    select.append(option);
  }
  if (state.batch?.batchId) select.value = state.batch.batchId;
}

/* ----------------------------------------------------------------- results */

function stageChips(item, stopAfter) {
  const wrap = el("span", "stage-chips");
  const limit = STAGE_ORDER.indexOf(stopAfter || "final");
  const pure = item.protocol === "a2a";
  const baseline = item.baseline && typeof item.baseline === "object" ? item.baseline : null;
  if (baseline) {
    const status =
      baseline.status === "accepted"
        ? "passed"
        : baseline.status === "running"
          ? "waiting"
          : baseline.status === "failed"
            ? "failed"
            : baseline.status === "cancelled"
              ? "skipped"
              : "pending";
    const chip = el("span", `stage-chip ${status}`, "基线");
    chip.title = `基线 Create：${baseline.status || ""}${baseline.state ? ` · ${baseline.state}` : ""}${
      baseline.error ? ` · ${baseline.error}` : ""
    }`;
    wrap.append(chip);
  }
  const interception = item.interception;
  if (interception?.stages) {
    const validate = interception.stages.validate;
    const status = validate?.status || "recorded";
    const chip = el("span", `stage-chip ${status}`, "拦截");
    chip.title = `A2A-T 拦截：${statusLabel(status)}${validate?.message ? ` · ${validate.message}` : ""}`;
    wrap.append(chip);
  }
  for (const stage of STAGE_ORDER) {
    if (pure && SDK_STAGES.has(stage)) continue;
    if (limit >= 0 && STAGE_ORDER.indexOf(stage) > limit) continue;
    const result = item.stages?.[stage];
    const status = result?.status || (item.status === "pending" ? "pending" : "skipped");
    const chip = el("span", `stage-chip ${status}`, `${STAGE_LABELS[stage] || stage}`);
    chip.title = `${stage}: ${statusLabel(status)}${result?.message ? ` · ${result.message}` : ""}`;
    wrap.append(chip);
  }
  return wrap;
}

function durationText(ms) {
  if (typeof ms !== "number" || !Number.isFinite(ms)) return "—";
  if (ms < 1000) return `${Math.round(ms)} ms`;
  return `${(ms / 1000).toFixed(1)} s`;
}

function tokenText(tokens) {
  if (!tokens || tokens.available !== true) {
    return tokens?.scripted === true ? "离线脚本" : "—";
  }
  const total = typeof tokens.total === "number" ? tokens.total.toLocaleString() : "—";
  return `${total}`;
}

function acceptedNote(item) {
  const accepted = item.stages?.accepted;
  if (!accepted || accepted.status !== "passed") return "";
  const actual = accepted.actual && typeof accepted.actual === "object" ? accepted.actual : {};
  const state = typeof actual.state === "string" && actual.state ? actual.state : "TASK_STATE_SUBMITTED";
  const recorded = item.serverIntent && typeof item.serverIntent === "object" ? "（服务端意图已记录）" : "";
  return `已收到 ${state}${recorded}`;
}

function resultText(item) {
  if (item.error) return item.error;
  const blocked = Object.entries(item.interception?.stages || {}).map(([stage, value]) => [`拦截/${stage}`, value]);
  const all = [...Object.entries(item.stages || {}), ...blocked];
  const failed = all.filter(([, stage]) => stage.status === "failed");
  if (failed.length) {
    const text = failed.map(([stage, value]) => `${stage}: ${value.message || "不匹配"}`).join("；");
    // A red row often still received the submission payload; show that next to the failure so the
    // row is not read as "nothing came back".
    const note = acceptedNote(item);
    return note ? `${text}；${note}` : text;
  }
  const waiting = Object.entries(item.stages || {}).filter(([, stage]) => stage.status === "waiting");
  if (waiting.length) {
    return waiting.map(([stage, value]) => `${stage}: ${value.message || "待 WORKING"}`).join("；");
  }
  const recorded = Object.entries(item.stages || {}).filter(([, stage]) => stage.status === "recorded");
  if (recorded.length) return `${recorded.length} 个阶段仅记录`;
  return item.status === "passed" ? "全部阶段通过" : "";
}

function populateRoundFilter(rounds) {
  const select = $("round-filter");
  const signature = `${state.batch?.batchId || ""}:${rounds}`;
  if (select.dataset.signature === signature) {
    select.value = state.roundFilter;
    return;
  }
  select.dataset.signature = signature;
  // A different batch starts on "all rounds"; the previous selection would hide rows.
  state.roundFilter = "";
  clear(select);
  const all = el("option", "", "全部轮次");
  all.value = "";
  select.append(all);
  for (let index = 1; index <= rounds; index += 1) {
    const option = el("option", "", `第 ${index} 轮`);
    option.value = String(index);
    select.append(option);
  }
  select.value = state.roundFilter;
}

function roundSummaryNote(batch) {
  const parts = [];
  for (const entry of batch.roundSummaries || []) {
    const summary = entry.summary || {};
    parts.push(`第 ${entry.round} 轮 ${summary.passed || 0}/${summary.total || 0}`);
  }
  const cross = batch.crossRound || {};
  if (cross.total) parts.push(`稳定通过 ${cross.stable || 0}/${cross.total}`);
  return parts.length ? ` · ${parts.join(" · ")}` : "";
}

function renderResults() {
  const batch = state.batch;
  const body = $("results-body");
  clear(body);
  if (!batch) {
    $("empty-state").hidden = false;
    $("empty-state").textContent = "选择用例并开始执行。";
    $("result-summary").textContent = "";
    $("round-head").hidden = true;
    $("round-filter-wrap").hidden = true;
    state.roundFilter = "";
    $("start-button").hidden = false;
    $("cancel-button").hidden = true;
    $("retry-button").hidden = true;
    $("export-json").disabled = true;
    $("export-xlsx").disabled = true;
    $("progress").hidden = true;
    return;
  }
  const items = Array.isArray(batch.items) ? batch.items : [];
  const rounds = Number(batch.rounds || 1);
  const multi = rounds > 1;
  $("round-head").hidden = !multi;
  $("round-filter-wrap").hidden = !multi;
  if (multi) populateRoundFilter(rounds);
  else state.roundFilter = "";
  const visible = multi && state.roundFilter
    ? items
        .map((item, index) => [item, index])
        .filter(([item]) => String(item.round || 1) === state.roundFilter)
    : items.map((item, index) => [item, index]);
  $("empty-state").hidden = visible.length > 0;
  if (!items.length) $("empty-state").textContent = "该批次没有运行项。";
  else if (!visible.length) $("empty-state").textContent = "该轮次没有运行项。";

  visible.forEach(([item, index]) => {
    const row = document.createElement("tr");
    if (item.status === "running") row.className = "running";
    const number = el("td", "num", index + 1);
    const id = el("td", "", item.caseId === 0 ? "scratch" : String(item.caseId).padStart(2, "0"));
    const name = el("td", "", item.name || "—");
    const protocol = el("td", "", item.protocol === "a2a-t" ? "A2A-T" : "A2A");
    const round = multi ? el("td", "metric", String(item.round || 1)) : null;
    const stages = document.createElement("td");
    stages.append(stageChips(item, batch.stopAfter));
    const status = document.createElement("td");
    status.append(badge(item.status));
    const duration = el("td", "metric", durationText(item.durationMs));
    const tokens = el("td", "metric", tokenText(item.tokens));
    const runs = document.createElement("td");
    runs.className = "run-cell";
    const baseline = item.baseline && typeof item.baseline === "object" ? item.baseline : null;
    if (baseline?.runId) runs.append(runLine("基线", baseline.runId));
    if (item.runId) runs.append(runLine(baseline?.runId ? "修改" : "", item.runId));
    if (!runs.childNodes.length) runs.append(document.createTextNode("—"));
    const result = el("td", "result", resultText(item));
    if (item.langfuseWarning) {
      const warning = el("div", "context-line error", "Langfuse 导出失败");
      warning.title = String(item.langfuseWarning);
      result.append(warning);
    }
    const context = itemContext(item);
    if (context.taskId) {
      const line = el("div", "context-line", `task ${shortId(context.taskId)}`);
      line.title = [
        ["runId", context.runId],
        ["traceId", context.traceId],
        ["taskId", context.taskId],
        ["contextId", context.contextId],
      ]
        .filter(([, value]) => value)
        .map(([key, value]) => `${key}: ${value}`)
        .join("\n");
      result.append(line);
    }
    const actions = document.createElement("td");
    if (item.runId) {
      const wire = el("a", "run-link", "wire");
      wire.href = `/api/runs/${encodeURIComponent(item.runId)}/wire`;
      wire.target = "_blank";
      const trace = el("a", "run-link", "trace");
      trace.href = `/api/runs/${encodeURIComponent(item.runId)}/trace.json`;
      trace.target = "_blank";
      actions.append(wire, document.createTextNode(" · "), trace);
    }
    const detail = el("button", "link-button", "详情");
    detail.type = "button";
    detail.addEventListener("click", () => openDrawer(item, index));
    actions.append(document.createTextNode(" · "), detail);
    if (batch.status === "running" && (item.status === "running" || item.status === "pending")) {
      // One item at a time: the rest of the batch keeps running.
      const cancel = el("button", "link-button", "取消");
      cancel.type = "button";
      cancel.title = "只取消这一条用例，其余继续";
      cancel.addEventListener("click", () => cancelItem(index));
      actions.append(document.createTextNode(" · "), cancel);
    }
    const rerun = el("button", "link-button", "重跑");
    rerun.type = "button";
    rerun.disabled = batch.status === "running";
    rerun.addEventListener("click", () => rerunItem(index));
    actions.append(document.createTextNode(" · "), rerun);
    row.append(
      number, id, name, protocol, ...(round ? [round] : []),
      stages, status, duration, tokens, runs, result, actions,
    );
    body.append(row);
  });

  const summary = batch.summary || {};
  const concurrencyNote = batch.concurrency > 1 ? ` · 并发 ${batch.concurrency}` : "";
  const roundNote = multi ? roundSummaryNote(batch) : "";
  $("result-summary").textContent = `${summary.total || 0} 项 · 通过 ${summary.passed || 0} · 待确认 ${summary.partial || 0} · 失败 ${summary.failed || 0} · 错误 ${summary.error || 0} · 仅记录 ${summary.stageRecorded || 0}${concurrencyNote}${roundNote}`;
  const running = batch.status === "running";
  const retryable = (summary.failed || 0) + (summary.error || 0) + (summary.cancelled || 0) > 0;
  $("start-button").hidden = running;
  $("start-button").disabled = running;
  $("cancel-button").hidden = !running;
  $("retry-button").hidden = running || !retryable;
  $("export-json").disabled = false;
  $("export-xlsx").disabled = false;
  $("progress").hidden = !running && batch.status !== "finished" && batch.status !== "cancelled";
  const total = summary.total || 0;
  const done =
    (summary.passed || 0) + (summary.partial || 0) + (summary.failed || 0) + (summary.error || 0) + (summary.cancelled || 0);
  $("progress-bar").style.width = `${total ? Math.min(100, Math.round((done / total) * 100)) : 0}%`;
  const roundPrefix = multi ? `第 ${batch.currentRound || 1}/${rounds} 轮 · ` : "";
  $("progress-text").textContent = running
    ? `${roundPrefix}${done}/${total} · 运行中 ${summary.running || 0}`
    : `${roundPrefix}${done}/${total}`;
  if (running) startPolling();
}

/* ------------------------------------------------------------------ drawer */

function textBlock(value) {
  if (value === null || value === undefined || value === "") return "—";
  if (typeof value === "string") return value;
  return JSON.stringify(value, null, 2);
}

function itemContext(item) {
  const stages = item.stages || {};
  const observations = [stages.working?.actual, stages.accepted?.actual, stages.send?.actual].filter(
    (value) => value && typeof value === "object",
  );
  const observation = observations.find((value) => value.taskId) || observations[0] || {};
  return {
    runId: item.runId || "",
    traceId: item.traceId || "",
    taskId: observation.taskId || "",
    contextId: observation.contextId || "",
    baseline: item.baseline && typeof item.baseline === "object" ? item.baseline : null,
  };
}

function shortId(value) {
  const text = String(value || "");
  return text.length > 12 ? `${text.slice(0, 12)}…` : text;
}

function runLink(runId, label) {
  const link = el("a", "run-link", label);
  link.href = `/api/runs/${encodeURIComponent(runId)}/wire`;
  link.target = "_blank";
  link.title = `wire ${runId}`;
  return link;
}

function runLine(tag, runId) {
  const line = el("div", "run-line");
  if (tag) line.append(el("span", "run-tag", tag));
  line.append(runLink(runId, shortId(runId)));
  return line;
}

function contextTable(rows) {
  const table = el("table", "grid kv-table");
  const tbody = document.createElement("tbody");
  for (const row of rows) {
    const cell = el("td", "mono pre", textBlock(row.value === undefined ? "" : row.value));
    if (row.runId) {
      const wire = el("a", "run-link", "wire");
      wire.href = `/api/runs/${encodeURIComponent(row.runId)}/wire`;
      wire.target = "_blank";
      const trace = el("a", "run-link", "trace");
      trace.href = `/api/runs/${encodeURIComponent(row.runId)}/trace.json`;
      trace.target = "_blank";
      cell.append(document.createTextNode(" "), wire, document.createTextNode(" · "), trace);
    }
    const tr = document.createElement("tr");
    tr.append(el("td", "mono", row.label), cell);
    tbody.append(tr);
  }
  table.append(tbody);
  return table;
}

function appendContextBlock(body, item) {
  const context = itemContext(item);
  const block = el("section", "diff-block");
  block.append(el("strong", "", "运行上下文"));
  block.append(
    contextTable([
      { label: "runId", value: context.runId, runId: context.runId },
      { label: "traceId", value: context.traceId },
      { label: item.protocol === "a2a" ? "taskId（外层 Task）" : "taskId", value: context.taskId },
      { label: "contextId", value: context.contextId },
    ]),
  );
  body.append(block);
}

function appendServerReplyBlock(body, item) {
  const accepted = item.stages?.accepted;
  const actual = accepted?.actual && typeof accepted.actual === "object" ? accepted.actual : {};
  const serverIntent = item.serverIntent && typeof item.serverIntent === "object" ? item.serverIntent : null;
  const rows = [];
  if (actual.state) rows.push({ label: "state", value: actual.state });
  if (serverIntent) {
    for (const key of ["Operation type", "Energy saving region"]) {
      if (serverIntent[key] !== undefined && serverIntent[key] !== "") {
        rows.push({ label: key, value: serverIntent[key] });
      }
    }
  }
  if (actual.taskId) rows.push({ label: "taskId", value: actual.taskId });
  if (actual.contextId) rows.push({ label: "contextId", value: actual.contextId });
  if (!rows.length) return;
  const block = el("section", "diff-block");
  const head = el("div", "diff-head");
  head.append(el("strong", "", "任务受理回包"));
  if (Array.isArray(item.answer?.exchanges) && item.answer.exchanges.length) {
    head.append(el("span", "summary", "完整记录见「通信记录」"));
  }
  block.append(head, contextTable(rows));
  body.append(block);
}

function appendBaselineBlock(body, item) {
  const baseline = item.baseline;
  if (!baseline || typeof baseline !== "object") return;
  const status = String(baseline.status || "");
  const label =
    status === "running" ? "创建中" : status === "accepted" ? "已受理" : status === "failed" ? "失败" : "已记录";
  const block = el("section", "diff-block");
  const head = el("div", "diff-head");
  head.append(
    el("strong", "", "基线 Create（Modify 前置）"),
    badge(status, label),
    el("span", "summary", baseline.state || (status === "running" ? "等待受理…" : "")),
  );
  block.append(head);
  if (baseline.error) block.append(el("p", "summary error", String(baseline.error)));
  block.append(
    contextTable([
      { label: "runId", value: baseline.runId, runId: baseline.runId },
      { label: "taskId", value: baseline.taskId },
      { label: "contextId", value: baseline.contextId },
    ]),
  );
  if (baseline.request) block.append(el("pre", "mono case-pre", textBlock(baseline.request)));
  body.append(block);
}

function appendStageBlock(body, label, result, skipDetails = false) {
  const block = el("section", "diff-block");
  const head = el("div", "diff-head");
  head.append(el("strong", "", label), badge(result.status), el("span", "summary", result.message || ""));
  block.append(head);
  if (!skipDetails && (result.expected !== undefined || result.actual !== undefined)) {
    const summary = el("div", "diff-summary");
    summary.append(
      el("div", "", `预期：${textBlock(result.expected)}`),
      el("div", "", `实际：${textBlock(result.actual)}`),
    );
    block.append(summary);
  }
  const diffs = Array.isArray(result.diffs) ? result.diffs : [];
  if (!skipDetails && diffs.length) {
    const table = el("table", "grid diff-table");
    const thead = document.createElement("thead");
    const headRow = document.createElement("tr");
    for (const title of ["字段", "预期", "实际", "说明"]) headRow.append(el("th", "", title));
    thead.append(headRow);
    const tbody = document.createElement("tbody");
    for (const diff of diffs) {
      const row = document.createElement("tr");
      row.append(
        el("td", "mono", diff.path || ""),
        el("td", "mono pre", textBlock(diff.expected)),
        el("td", "mono pre", textBlock(diff.actual)),
        el("td", "", diff.reason || ""),
      );
      tbody.append(row);
    }
    table.append(thead, tbody);
    block.append(table);
  }
  body.append(block);
}

const INTENT_ROW_LABELS = {
  same: "一致",
  changed: "不同",
  missing: "缺失",
  added: "新增",
};

function appendIntentCompareTable(body, rows) {
  const block = el("section", "diff-block");
  const head = el("div", "diff-head");
  const differing = rows.filter((row) => row.status && row.status !== "same").length;
  head.append(
    el("strong", "", "意图逐字段对比"),
    el("span", "summary", differing ? `${differing} 个字段有差异` : "全部字段一致"),
  );
  block.append(head);
  const table = el("table", "grid diff-table intent-diff-table");
  const thead = document.createElement("thead");
  const headRow = document.createElement("tr");
  for (const title of ["字段", "期望", "实际", "状态", "说明"]) headRow.append(el("th", "", title));
  thead.append(headRow);
  const tbody = document.createElement("tbody");
  for (const row of rows) {
    const status = String(row.status || "same");
    const tr = el("tr", `intent-${status}`);
    tr.append(
      el("td", "mono", row.path || ""),
      el("td", "mono pre", textBlock(row.expected)),
      el("td", "mono pre", textBlock(row.actual)),
      el("td", "", INTENT_ROW_LABELS[status] || status),
      el("td", "", row.reason || ""),
    );
    tbody.append(tr);
  }
  table.append(thead, tbody);
  block.append(table);
  body.append(block);
}

function renderResultTab(item, body) {
  if (item.langfuseWarning) {
    const block = el("section", "diff-block");
    block.append(el("strong", "", "Langfuse 导出失败"));
    block.append(el("p", "status-line error", String(item.langfuseWarning)));
    body.append(block);
  }
  appendBaselineBlock(body, item);
  appendContextBlock(body, item);
  appendServerReplyBlock(body, item);
  const stages = item.stages || {};
  if (!Object.keys(stages).length) {
    body.append(el("p", "status-line", "该运行项还没有阶段结果。"));
  }
  const interception = item.interception;
  if (interception?.stages) {
    const block = el("section", "diff-block");
    block.append(el("strong", "", "A2A-T 拦截（原请求）"));
    block.append(el("p", "summary", `补充请求：${interception.followupInput || "—"}`));
    block.append(el("pre", "mono pre", interception.input || "—"));
    body.append(block);
    for (const stage of STAGE_ORDER) {
      const result = interception.stages[stage];
      if (result) appendStageBlock(body, `拦截/${STAGE_LABELS[stage] || stage}`, result);
    }
  }
  const intentRows = Array.isArray(item.intentCompare) ? item.intentCompare : [];
  let intentTableRendered = false;
  for (const stage of STAGE_ORDER) {
    const result = stages[stage];
    if (!result) continue;
    const hasIntentTable = stage === "intent" && intentRows.length > 0;
    appendStageBlock(body, STAGE_LABELS[stage] || stage, result, hasIntentTable);
    if (hasIntentTable) {
      appendIntentCompareTable(body, intentRows);
      intentTableRendered = true;
    }
  }
  if (!intentTableRendered && intentRows.length) {
    appendIntentCompareTable(body, intentRows);
  }
  if (!intentRows.length) {
    // No comparable server payload (proxy hop, simulator, or a run that stopped early): keep the
    // raw evidence instead of inventing a comparison.
    if (item.serverIntent && typeof item.serverIntent === "object") {
      body.append(caseSection("意图理解结果（服务端）", item.serverIntent));
    }
    const expectedIntent = item.refs?.expectedIntent;
    if (expectedIntent && typeof expectedIntent === "object") {
      body.append(caseSection("期望意图", expectedIntent));
    }
  }
  if (item.intent && typeof item.intent === "object") {
    body.append(caseSection("SDK 抽取意图（辅助证据）", item.intent));
  }
  const attempts = Array.isArray(item.attempts) ? item.attempts : [];
  if (attempts.length) {
    const block = el("section", "diff-block");
    block.append(el("strong", "", "历史尝试"));
    for (const attempt of attempts) {
      const line = el("div", "attempt-line");
      line.append(el("span", "summary", `${attempt.decisionMode || ""} · ${statusLabel(attempt.status)} · ${attempt.finishedAt || ""}`));
      if (attempt.runId) {
        const wire = el("a", "run-link", `wire ${attempt.runId}`);
        wire.href = `/api/runs/${encodeURIComponent(attempt.runId)}/wire`;
        wire.target = "_blank";
        line.append(document.createTextNode(" "), wire);
      }
      block.append(line);
    }
    body.append(block);
  }
}

function caseSection(title, value) {
  const block = el("section", "diff-block");
  block.append(el("strong", "", title));
  block.append(el("pre", "mono case-pre", textBlock(value)));
  return block;
}

function renderCaseTab(item, body) {
  if (item.caseId === 0) {
    body.append(caseSection("临时请求", item.scratch || ""));
    return;
  }
  const drawer = state.drawer;
  const protocol = drawer.protocol || item.protocol;
  const protocolSwitch = el("div", "protocol-switch");
  for (const value of ["a2a-t", "a2a"]) {
    const button = el(
      "button",
      `tab small${value === protocol ? " active" : ""}`,
      value === "a2a-t" ? "A2A-T" : "A2A",
    );
    button.type = "button";
    button.addEventListener("click", () => {
      state.drawer.protocol = value;
      renderDrawer();
    });
    protocolSwitch.append(button);
  }
  body.append(protocolSwitch);
  if (drawer.loading) {
    body.append(el("p", "status-line", "正在加载用例详情…"));
    return;
  }
  if (drawer.error) {
    body.append(el("p", "status-line error", `加载用例详情失败：${drawer.error}`));
    return;
  }
  const doc = drawer.caseDoc;
  if (!doc) {
    body.append(el("p", "status-line", "没有用例详情。"));
    return;
  }
  const summary = doc.summary || {};
  const baselineRequest = doc.case?.baseline?.request;
  const followup = doc.case?.blocked_followup;
  const meta = el("div", "case-meta");
  meta.append(
    el("span", "pill", summary.dimension || "未分组"),
    el("span", "pill", summary.operation || ""),
    el("span", "pill", summary.validateLabel || ""),
    ...(summary.tags || []).map((tag) => el("span", "pill", tag)),
  );
  if (baselineRequest) meta.append(el("span", "pill", "基线 Create"));
  if (followup?.input) meta.append(el("span", "pill", "拦截+补充"));
  body.append(meta);
  if (doc.case?.known_issue) body.append(caseSection("已知问题", doc.case.known_issue));
  if (baselineRequest) {
    const block = el("section", "diff-block");
    block.append(el("strong", "", "前置基线 Create（Modify 续传用）"));
    block.append(el("p", "summary", "运行期 fixture：先建出被修改的 Task；不参与 OSP 期望判定。"));
    block.append(el("pre", "mono case-pre", textBlock(baselineRequest)));
    const actual = drawer.item?.baseline;
    if (actual?.runId) {
      const line = el("div", "attempt-line");
      line.append(el("span", "summary", `本次实际基线：${actual.state || "—"}${actual.error ? ` · ${actual.error}` : ""}`));
      const wire = el("a", "run-link", `wire ${actual.runId}`);
      wire.href = `/api/runs/${encodeURIComponent(actual.runId)}/wire`;
      wire.target = "_blank";
      line.append(document.createTextNode(" "), wire);
      block.append(line);
    }
    body.append(block);
  }
  body.append(caseSection(followup?.input ? "请求原文（拦截段）" : "请求原文", doc.case?.user_input || ""));
  if (followup?.input) {
    const block = el("section", "diff-block");
    block.append(el("strong", "", "拦截后补充请求（A2A-T）"));
    block.append(el("p", "summary", "拦截段只记录；补充段用该请求重跑完整期望。"));
    block.append(el("pre", "mono case-pre", textBlock(followup.input)));
    body.append(block);
    if (followup.expect) body.append(caseSection("补充段 expect 覆盖", followup.expect));
    if (followup.assert) body.append(caseSection("补充段 assert 覆盖", followup.assert));
  }
  const intent = doc.case?.expect?.intent;
  const expectationScope = followup?.input ? "（补充请求）" : "";
  if (intent && typeof intent === "object") {
    const table = el("table", "grid kv-table");
    const tbody = document.createElement("tbody");
    for (const [key, value] of Object.entries(intent)) {
      const row = document.createElement("tr");
      row.append(el("td", "mono", key), el("td", "mono pre", textBlock(value)));
      tbody.append(row);
    }
    table.append(tbody);
    const block = el("section", "diff-block");
    block.append(el("strong", "", `期望意图${expectationScope}`), table);
    body.append(block);
  }
  const protocolBlock = doc.case?.expect?.protocols?.[protocol] || {};
  body.append(caseSection(`期望 Task-T（${protocol === "a2a-t" ? "A2A-T" : "A2A"}）${expectationScope}`, protocolBlock.task_t || "该协议没有 Task-T 期望"));
  const negotiation = protocolBlock.negotiation;
  if (negotiation) {
    const block = el("section", "diff-block");
    block.append(el("strong", "", "协商预期"));
    block.append(
      el(
        "p",
        "summary",
        `expected=${negotiation.expected} · rounds=${negotiation.expected_rounds} · kinds=${(negotiation.kinds || []).join(", ") || "—"}`,
      ),
    );
    (negotiation.rounds || []).forEach((round, index) => {
      block.append(el("div", "summary", `第 ${index + 1} 轮`));
      block.append(el("pre", "mono case-pre", textBlock(round)));
    });
    body.append(block);
  }
  body.append(caseSection("断言开关", doc.case?.assert || {}));
  body.append(caseSection("runner", doc.case?.runner || {}));
  body.append(caseSection("来源", doc.case?.source || {}));
}

async function ensureCaseDoc(item) {
  if (item.caseId === 0 || state.drawer.caseDoc || state.drawer.loading) return;
  state.drawer.loading = true;
  renderDrawer();
  try {
    state.drawer.caseDoc = await getJson(`/api/tester/catalog/${item.caseId}`);
  } catch (error) {
    state.drawer.error = error.message || String(error);
  } finally {
    state.drawer.loading = false;
    renderDrawer();
  }
}

function drawerRuns(item) {
  const runs = [];
  const baseline = item.baseline && typeof item.baseline === "object" ? item.baseline : null;
  if (baseline?.runId) runs.push({ key: "baseline", label: "基线", runId: String(baseline.runId) });
  if (item.runId) runs.push({ key: "modify", label: runs.length ? "修改" : "运行", runId: String(item.runId) });
  return runs;
}

function activeDrawerRun(item) {
  const runs = drawerRuns(item);
  if (!runs.length) return null;
  return runs.find((run) => run.key === state.drawer.run) || runs[runs.length - 1];
}

function traceEvents(doc) {
  const transcript =
    doc && typeof doc.transcript === "object" && doc.transcript ? doc.transcript : {};
  if (Array.isArray(transcript.timeline) && transcript.timeline.length) return transcript.timeline;
  if (Array.isArray(transcript.events)) return transcript.events;
  return Array.isArray(doc?.spans) ? doc.spans : [];
}

async function ensureTraceDoc(item) {
  const run = activeDrawerRun(item);
  if (!run) return;
  const drawer = state.drawer;
  if (drawer.traceDoc && drawer.traceRunId === run.runId) return;
  if (drawer.traceLoading) return;
  drawer.traceLoading = true;
  drawer.traceRunId = run.runId;
  drawer.traceError = "";
  renderDrawer();
  try {
    const doc = await getJson(`/api/runs/${encodeURIComponent(run.runId)}/trace.json`);
    drawer.traceDoc = doc;
    drawer.traceStatus = doc.status || "";
  } catch (error) {
    drawer.traceDoc = null;
    drawer.traceError = error.message || String(error);
  } finally {
    drawer.traceLoading = false;
    renderDrawer();
  }
}

async function refreshTraceDoc(item) {
  const drawer = state.drawer;
  if (drawer.traceLoading) return;
  const run = activeDrawerRun(item);
  if (!run) return;
  try {
    const doc = await getJson(`/api/runs/${encodeURIComponent(run.runId)}/trace.json`);
    const grew = traceEvents(doc).length !== traceEvents(drawer.traceDoc).length;
    const statusChanged = (doc.status || "") !== drawer.traceStatus;
    drawer.traceDoc = doc;
    drawer.traceRunId = run.runId;
    drawer.traceStatus = doc.status || "";
    drawer.traceError = "";
    if (grew || statusChanged) renderDrawer();
  } catch (error) {
    drawer.traceError = error.message || String(error);
    renderDrawer();
  }
}

function drawerSignature(item) {
  const stages = item.stages && typeof item.stages === "object" ? item.stages : {};
  const baseline =
    item.baseline && typeof item.baseline === "object"
      ? `${item.baseline.status || ""}:${item.baseline.state || ""}`
      : "";
  return [
    item.status || "",
    item.runId || "",
    baseline,
    item.error || "",
    `${Array.isArray(item.answer?.exchanges) ? item.answer.exchanges.length : 0}`,
    Object.entries(stages)
      .map(([name, value]) => `${name}:${value?.status || ""}:${value?.message || ""}`)
      .join("|"),
  ].join("§");
}

function refreshOpenDrawer() {
  const drawer = state.drawer;
  if (!drawer.item || drawer.preview === true || drawer.index === null) return;
  const items = Array.isArray(state.batch?.items) ? state.batch.items : [];
  const fresh = items[drawer.index];
  if (!fresh) return;
  const signature = drawerSignature(fresh);
  const changed = signature !== drawer.signature;
  drawer.item = fresh;
  drawer.signature = signature;
  if (drawer.tab === "trace") {
    void refreshTraceDoc(fresh);
    if (changed) renderDrawer();
    return;
  }
  if (changed) renderDrawer();
}

function traceEventNode(event, index) {
  const details = document.createElement("details");
  details.className = "trace-event";
  const summary = document.createElement("summary");
  const seq = event.seq !== undefined ? `#${event.seq}` : `#${index + 1}`;
  summary.append(
    el("span", "trace-seq", seq),
    el("span", "trace-kind", String(event.kind || event.performative || "event")),
    el("span", "trace-title", String(event.title || event.summary || event.content || "")),
  );
  if (event.status) summary.append(badge(String(event.status)));
  if (typeof event.durationMs === "number") {
    summary.append(el("span", "muted", durationText(event.durationMs)));
  }
  details.append(summary);
  const text = textBlock(event);
  details.append(el("pre", "mono case-pre", text.length > 6000 ? `${text.slice(0, 6000)}…` : text));
  return details;
}

function renderTraceTab(item, body) {
  const drawer = state.drawer;
  const runs = drawerRuns(item);
  if (!runs.length) {
    body.append(
      el("p", "status-line", "尚无运行：基线 Create 或请求发送后，轨迹会实时显示在这里。"),
    );
    return;
  }
  const active = activeDrawerRun(item);
  if (runs.length > 1) {
    const switcher = el("div", "protocol-switch");
    for (const run of runs) {
      const button = el("button", `tab small${run.key === active.key ? " active" : ""}`, run.label);
      button.type = "button";
      button.addEventListener("click", () => {
        state.drawer.run = run.key;
        state.drawer.traceDoc = null;
        state.drawer.traceRunId = "";
        state.drawer.traceStatus = "";
        renderDrawer();
        void ensureTraceDoc(state.drawer.item);
      });
      switcher.append(button);
    }
    body.append(switcher);
  }
  if (drawer.traceLoading && drawer.traceRunId === active.runId) {
    body.append(el("p", "status-line", `正在加载轨迹 ${shortId(active.runId)}…`));
    return;
  }
  if (drawer.traceError) {
    body.append(el("p", "status-line error", `加载轨迹失败：${drawer.traceError}`));
    return;
  }
  if (!drawer.traceDoc || drawer.traceRunId !== active.runId) {
    body.append(el("p", "status-line", "正在加载轨迹…"));
    return;
  }
  const doc = drawer.traceDoc;
  const transcript =
    doc && typeof doc.transcript === "object" && doc.transcript ? doc.transcript : {};
  const meta = el("div", "case-meta");
  meta.append(
    el("span", "pill", `${active.label} ${shortId(active.runId)}`),
    el("span", "pill", String(doc.status || transcript.finalState || "运行中")),
  );
  if (transcript.traceId) meta.append(el("span", "pill", `trace ${shortId(transcript.traceId)}`));
  if (transcript.taskId) meta.append(el("span", "pill", `task ${shortId(transcript.taskId)}`));
  body.append(meta);
  const events = traceEvents(doc);
  if (!events.length) {
    body.append(el("p", "status-line", "暂无轨迹事件；运行开始后会逐步出现。"));
    return;
  }
  const list = el("div", "trace-list");
  events.forEach((event, index) => {
    if (event && typeof event === "object") list.append(traceEventNode(event, index));
  });
  body.append(list);
}

function exchangeText(payload) {
  const message =
    payload && typeof payload === "object" && payload.message && typeof payload.message === "object"
      ? payload.message
      : payload;
  const parts = message && typeof message === "object" && Array.isArray(message.parts) ? message.parts : [];
  const text = parts
    .map((part) => (part && typeof part.text === "string" ? part.text : textBlock(part)))
    .join("\n");
  return text || textBlock(payload);
}

function exchangeMeta(exchange) {
  const bits = [];
  if (typeof exchange.round === "number") bits.push(`Round ${exchange.round}`);
  if (exchange.templateUri) bits.push(String(exchange.templateUri));
  if (typeof exchange.durationMs === "number" && exchange.durationMs > 0) {
    bits.push(durationText(exchange.durationMs));
  }
  if (exchange.timestamp) bits.push(String(exchange.timestamp));
  return bits.join(" · ");
}

function renderExchangesTab(item, body) {
  const exchanges = Array.isArray(item.answer?.exchanges) ? item.answer.exchanges : [];
  if (!exchanges.length) {
    body.append(
      el("p", "status-line", "该运行项没有通信记录：未生成 answer、运行项提前失败，或来自旧版本批次。"),
    );
    return;
  }
  body.append(
    el(
      "p",
      "status-line",
      "规范化记录（来自 session transcript 与 trace span）；逐字原始帧请用表格行的 wire 链接。",
    ),
  );
  for (const exchange of exchanges) {
    const block = el("section", "diff-block");
    const head = el("div", "diff-head");
    const direction = String(exchange.direction || "");
    head.append(
      el("strong", "", `${direction || "?"} ${String(exchange.messageType || "")}`),
      el("span", "summary", exchangeMeta(exchange)),
    );
    block.append(head);
    block.append(el("pre", "mono case-pre", exchangeText(exchange.payload)));
    const details = document.createElement("details");
    details.className = "trace-event";
    details.append(el("summary", "", "完整 payload"));
    details.append(el("pre", "mono case-pre", textBlock(exchange.payload)));
    block.append(details);
    body.append(block);
  }
}

function renderDrawer() {
  const drawer = state.drawer;
  const item = drawer.item;
  if (!item) return;
  const protocol = item.protocol === "a2a-t" ? "A2A-T" : "A2A";
  const roundNote = item.round && state.batch?.rounds > 1 ? ` · 第 ${item.round} 轮` : "";
  $("drawer-title").textContent = `#${String(item.caseId).padStart(2, "0")} ${item.name} · ${protocol}${roundNote}`;
  const langfuseLink = $("drawer-langfuse");
  const langfuseUrl = typeof item.langfuseUrl === "string" ? item.langfuseUrl : "";
  if (langfuseUrl) {
    langfuseLink.href = langfuseUrl;
    langfuseLink.hidden = false;
  } else {
    langfuseLink.removeAttribute("href");
    langfuseLink.hidden = true;
  }
  $("drawer-tab-result").hidden = drawer.preview === true;
  $("drawer-tab-result").classList.toggle("active", drawer.tab === "result");
  $("drawer-tab-trace").hidden = drawer.preview === true;
  $("drawer-tab-trace").classList.toggle("active", drawer.tab === "trace");
  $("drawer-tab-exchanges").hidden = drawer.preview === true;
  $("drawer-tab-exchanges").classList.toggle("active", drawer.tab === "exchanges");
  $("drawer-tab-case").classList.toggle("active", drawer.tab === "case");
  const body = $("drawer-body");
  const scroll = body.scrollTop;
  clear(body);
  if (drawer.tab === "result") renderResultTab(item, body);
  else if (drawer.tab === "trace") renderTraceTab(item, body);
  else if (drawer.tab === "exchanges") renderExchangesTab(item, body);
  else renderCaseTab(item, body);
  body.scrollTop = scroll;
}

function openDrawer(item, index) {
  state.drawer = {
    item,
    index,
    tab: "result",
    protocol: item.protocol,
    preview: false,
    caseDoc: null,
    loading: false,
    error: "",
    run: null,
    traceDoc: null,
    traceRunId: "",
    traceStatus: "",
    traceLoading: false,
    traceError: "",
    signature: "",
  };
  state.drawer.signature = drawerSignature(item);
  renderDrawer();
  $("drawer").hidden = false;
  $("drawer-backdrop").hidden = false;
}

function openCasePreview(summary) {
  const item = {
    caseId: summary.id,
    name: summary.name,
    protocol: "a2a-t",
    stages: {},
  };
  state.drawer = {
    item,
    index: null,
    tab: "case",
    protocol: "a2a-t",
    preview: true,
    caseDoc: null,
    loading: false,
    error: "",
    run: null,
    traceDoc: null,
    traceRunId: "",
    traceStatus: "",
    traceLoading: false,
    traceError: "",
    signature: drawerSignature(item),
  };
  renderDrawer();
  $("drawer").hidden = false;
  $("drawer-backdrop").hidden = false;
  void ensureCaseDoc(item);
}

function switchDrawerTab(tab) {
  state.drawer.tab = tab;
  renderDrawer();
  if (tab === "case") void ensureCaseDoc(state.drawer.item);
  if (tab === "trace") void ensureTraceDoc(state.drawer.item);
}

function closeDrawer() {
  $("drawer").hidden = true;
  $("drawer-backdrop").hidden = true;
}

/* -------------------------------------------------------------------- tabs */

function switchTab(name) {
  state.tab = name;
  $("catalog-panel").hidden = name !== "catalog";
  $("scratch-panel").hidden = name !== "scratch";
  $("tab-catalog").classList.toggle("active", name === "catalog");
  $("tab-scratch").classList.toggle("active", name === "scratch");
}

/* ------------------------------------------------------------------- init */

function wireEvents() {
  $("tab-catalog").addEventListener("click", () => switchTab("catalog"));
  $("tab-scratch").addEventListener("click", () => switchTab("scratch"));
  $("filter-query").addEventListener("input", () => {
    state.filters.query = $("filter-query").value;
    renderCatalog();
  });
  for (const [id, key] of [
    ["filter-dimension", "dimension"],
    ["filter-operation", "operation"],
    ["filter-validate", "validate"],
    ["filter-tag", "tag"],
  ]) {
    $(id).addEventListener("change", () => {
      state.filters[key] = $(id).value;
      renderCatalog();
    });
  }
  $("select-all").addEventListener("click", () => {
    for (const item of filteredCatalog()) state.selected.add(item.id);
    renderCatalog();
  });
  $("select-none").addEventListener("click", () => {
    state.selected.clear();
    renderCatalog();
  });
  $("start-button").addEventListener("click", startBatch);
  $("cancel-button").addEventListener("click", cancelBatch);
  $("retry-button").addEventListener("click", retryBatch);
  $("export-json").addEventListener("click", () => exportBatch("json"));
  $("export-xlsx").addEventListener("click", () => exportBatch("xlsx"));
  $("scratch-run").addEventListener("click", startScratch);
  $("history-select").addEventListener("change", () => openBatch($("history-select").value));
  $("round-filter").addEventListener("change", () => {
    state.roundFilter = $("round-filter").value;
    renderResults();
  });
  $("config-rounds").addEventListener("input", syncRoundsKeepTasks);
  $("drawer-close").addEventListener("click", closeDrawer);
  $("drawer-backdrop").addEventListener("click", closeDrawer);
  $("drawer-tab-result").addEventListener("click", () => switchDrawerTab("result"));
  $("drawer-tab-trace").addEventListener("click", () => switchDrawerTab("trace"));
  $("drawer-tab-exchanges").addEventListener("click", () => switchDrawerTab("exchanges"));
  $("drawer-tab-case").addEventListener("click", () => switchDrawerTab("case"));
  window.addEventListener("keydown", (event) => {
    if (event.key === "Escape") closeDrawer();
  });
  window.addEventListener("beforeunload", stopPolling);
}

async function init() {
  wireEvents();
  await loadMeta();
  await loadCatalog();
  await loadHistory();
  if (state.history.length) await openBatch(state.history[0].batchId);
}

void init();
