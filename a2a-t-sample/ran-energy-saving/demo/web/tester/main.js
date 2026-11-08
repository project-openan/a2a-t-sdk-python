/* Tester console: catalog-driven case selection, staged execution and diffs. */

const STAGE_ORDER = ["intent", "task_t", "validate", "send", "negotiate", "final"];
const STAGE_LABELS = {
  intent: "意图",
  task_t: "Task-T",
  validate: "校验",
  send: "发送",
  negotiate: "协商",
  final: "终态",
};
const STATUS_LABELS = {
  pending: "待执行",
  running: "执行中",
  passed: "通过",
  failed: "失败",
  error: "错误",
  recorded: "仅记录",
  skipped: "跳过",
  cancelled: "已取消",
  draft: "已创建",
  finished: "已完成",
  blocked: "拦截",
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
  tab: "catalog",
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
  stopAfter.value = meta?.defaults?.stopAfter || "final";
  $("config-approval").value = meta?.defaults?.approval || "approve";
  $("config-timeout").value = meta?.defaults?.timeoutSeconds || 300;
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
    row.append(check, body);
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
  const runs = caseIds.length * protocols.length;
  if (state.meta?.nmsEnabled && !window.confirm(`当前连接真实网管，将执行 ${runs} 个运行项。确认继续？`)) {
    return;
  }
  $("start-button").disabled = true;
  try {
    const created = await postJson("/api/tester/batches", {
      caseIds,
      protocols,
      stopAfter: $("config-stop-after").value,
      approval: $("config-approval").value,
      timeoutSeconds: Number($("config-timeout").value) || 300,
    });
    state.batch = created;
    await fetch(`/api/tester/batches/${encodeURIComponent(created.batchId)}/start`, { method: "POST" });
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
      timeoutSeconds: Number($("scratch-timeout").value) || 300,
    });
    state.batch = created;
    await fetch(`/api/tester/batches/${encodeURIComponent(created.batchId)}/start`, { method: "POST" });
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
    if (payload.status !== "running") {
      stopPolling();
      void loadHistory();
    }
  } catch {
    /* keep the last snapshot; the next tick retries */
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
    state.batch = created;
    await fetch(`/api/tester/batches/${encodeURIComponent(created.batchId)}/start`, { method: "POST" });
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
    const option = el("option", "", `${batch.stopAfter || "final"} · ${statusLabel(batch.status)} · ${batch.createdAt || ""}`);
    option.value = batch.batchId;
    select.append(option);
  }
  if (state.batch?.batchId) select.value = state.batch.batchId;
}

/* ----------------------------------------------------------------- results */

function stageChips(item, stopAfter) {
  const wrap = el("span", "stage-chips");
  const limit = STAGE_ORDER.indexOf(stopAfter || "final");
  for (const stage of STAGE_ORDER) {
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

function resultText(item) {
  if (item.error) return item.error;
  const failed = Object.entries(item.stages || {}).filter(([, stage]) => stage.status === "failed");
  if (failed.length) {
    return failed.map(([stage, value]) => `${stage}: ${value.message || "不匹配"}`).join("；");
  }
  const recorded = Object.entries(item.stages || {}).filter(([, stage]) => stage.status === "recorded");
  if (recorded.length) return `${recorded.length} 个阶段仅记录`;
  return item.status === "passed" ? "全部阶段通过" : "";
}

function renderResults() {
  const batch = state.batch;
  const body = $("results-body");
  clear(body);
  if (!batch) {
    $("empty-state").hidden = false;
    $("empty-state").textContent = "选择用例并开始执行。";
    $("result-summary").textContent = "";
    $("start-button").hidden = false;
    $("cancel-button").hidden = true;
    $("retry-button").hidden = true;
    $("export-json").disabled = true;
    $("export-xlsx").disabled = true;
    $("progress").hidden = true;
    return;
  }
  const items = Array.isArray(batch.items) ? batch.items : [];
  $("empty-state").hidden = items.length > 0;
  if (!items.length) $("empty-state").textContent = "该批次没有运行项。";

  items.forEach((item, index) => {
    const row = document.createElement("tr");
    if (item.status === "running") row.className = "running";
    const number = el("td", "num", index + 1);
    const id = el("td", "", item.caseId === 0 ? "scratch" : String(item.caseId).padStart(2, "0"));
    const name = el("td", "", item.name || "—");
    const protocol = el("td", "", item.protocol === "a2a-t" ? "A2A-T" : "A2A");
    const stages = document.createElement("td");
    stages.append(stageChips(item, batch.stopAfter));
    const status = document.createElement("td");
    status.append(badge(item.status));
    const duration = el("td", "metric", durationText(item.durationMs));
    const tokens = el("td", "metric", tokenText(item.tokens));
    const result = el("td", "result", resultText(item));
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
    if (Object.keys(item.stages || {}).length) {
      const diff = el("button", "link-button", "差异");
      diff.type = "button";
      diff.addEventListener("click", () => openDrawer(item));
      actions.append(document.createTextNode(" · "), diff);
    }
    row.append(number, id, name, protocol, stages, status, duration, tokens, result, actions);
    body.append(row);
  });

  const summary = batch.summary || {};
  $("result-summary").textContent = `${summary.total || 0} 项 · 通过 ${summary.passed || 0} · 失败 ${summary.failed || 0} · 错误 ${summary.error || 0} · 仅记录 ${summary.stageRecorded || 0}`;
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
  const done = (summary.passed || 0) + (summary.failed || 0) + (summary.error || 0) + (summary.cancelled || 0);
  $("progress-bar").style.width = `${total ? Math.min(100, Math.round((done / total) * 100)) : 0}%`;
  $("progress-text").textContent = `${done}/${total}`;
  if (running) startPolling();
}

/* ------------------------------------------------------------------ drawer */

function textBlock(value) {
  if (value === null || value === undefined || value === "") return "—";
  if (typeof value === "string") return value;
  return JSON.stringify(value, null, 2);
}

function renderDrawer(item) {
  $("drawer-title").textContent = `#${String(item.caseId).padStart(2, "0")} ${item.name} · ${item.protocol === "a2a-t" ? "A2A-T" : "A2A"}`;
  const body = $("drawer-body");
  clear(body);
  const stages = item.stages || {};
  if (!Object.keys(stages).length) {
    body.append(el("p", "status-line", "该运行项还没有阶段结果。"));
    return;
  }
  for (const stage of STAGE_ORDER) {
    const result = stages[stage];
    if (!result) continue;
    const block = el("section", "diff-block");
    const head = el("div", "diff-head");
    head.append(el("strong", "", STAGE_LABELS[stage] || stage), badge(result.status), el("span", "summary", result.message || ""));
    block.append(head);
    if (result.expected !== undefined || result.actual !== undefined) {
      const summary = el("div", "diff-summary");
      summary.append(
        el("div", "", `预期：${textBlock(result.expected)}`),
        el("div", "", `实际：${textBlock(result.actual)}`),
      );
      block.append(summary);
    }
    const diffs = Array.isArray(result.diffs) ? result.diffs : [];
    if (diffs.length) {
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
}

function openDrawer(item) {
  renderDrawer(item);
  $("drawer").hidden = false;
  $("drawer-backdrop").hidden = false;
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
  $("drawer-close").addEventListener("click", closeDrawer);
  $("drawer-backdrop").addEventListener("click", closeDrawer);
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
