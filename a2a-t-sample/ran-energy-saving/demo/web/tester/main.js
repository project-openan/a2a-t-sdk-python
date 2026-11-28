/* Tester console: catalog-driven case selection, staged execution and diffs. */

import {
  applyDataI18n,
  compareLabels,
  copyFor,
  localizeValidateLabel,
  stageLabels,
  statusLabels,
  t,
} from "./copy.js";

const STAGE_ORDER = ["intent", "task_t", "validate", "send", "negotiate", "accepted", "working", "final"];
const language = document.querySelector('meta[name="a2at-language"]')?.content || "zh-CN";
const copy = copyFor(language);
// The validate stage asks whether the Task-T is intercepted before sending, not whether the
// task succeeded: the OSP column it judges is "拦截".
const STAGE_LABELS = stageLabels(copy);
const SDK_STAGES = new Set(["intent", "task_t", "validate"]);
const STATUS_LABELS = statusLabels(copy);

const state = {
  meta: null,
  catalog: [],
  selected: new Set(),
  filters: { query: "", dimension: "", operation: "", validate: "", tag: "" },
  batch: null,
  history: [],
  polling: null,
  roundFilter: "",
  exporting: false,
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
    warning: "waiting",
  }[status] || status;
  return el("span", `badge ${mapped || "pending"}`, text || statusLabel(status));
}

function showToast(message, kind = "", timeout = 5000) {
  const node = $("toast");
  node.textContent = message;
  node.className = `toast ${kind}`.trim();
  node.hidden = false;
  window.clearTimeout(showToast.timer);
  // ``timeout <= 0`` keeps the toast up until the next showToast call hides it.
  if (timeout > 0) {
    showToast.timer = window.setTimeout(() => {
      node.hidden = true;
    }, timeout);
  }
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
  $("catalog-count").textContent = meta ? t(copy, "meta.catalogCount", { count: meta.catalogCount }) : "";
  const stopAfter = $("config-stop-after");
  clear(stopAfter);
  for (const stage of meta?.stopAfter || STAGE_ORDER) {
    const option = el("option", "", t(copy, "meta.stageOption", { label: STAGE_LABELS[stage] || stage, key: stage }));
    option.value = stage;
    stopAfter.append(option);
  }
  stopAfter.value = meta?.defaults?.stopAfter || "accepted";
  $("config-timeout").value = meta?.defaults?.timeoutSeconds ?? "";
  $("config-working-timeout").value = meta?.defaults?.workingTimeoutSeconds ?? "";
  const concurrency = $("config-concurrency");
  concurrency.max = String(meta?.maxConcurrency || 1);
  concurrency.value = String(meta?.defaults?.concurrency || 1);
  const rounds = $("config-rounds");
  rounds.max = String(meta?.maxRounds || 1);
  rounds.value = String(meta?.defaults?.rounds || 1);
  syncRoundsKeepTasks();
}

function syncRoundsKeepTasks() {
  const multi = Number($("config-rounds").value || 1) > 1;
  const keep = $("config-keep-tasks");
  keep.disabled = multi;
  if (multi) keep.checked = false;
  keep.title = multi ? t(copy, "config.multiRoundKeepTitle") : "";
}

async function loadCatalog() {
  try {
    const payload = await getJson("/api/tester/catalog");
    state.catalog = Array.isArray(payload.cases) ? payload.cases : [];
  } catch (error) {
    state.catalog = [];
    showToast(t(copy, "toast.catalogLoadFailed", { error: error.message || error }), "error");
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
    list.append(el("p", "status-line", t(copy, "catalog.empty")));
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
      el(
        "span",
        "catalog-meta",
        `${item.operation} · ${localizeValidateLabel(copy, item.validateLabel || "") || t(copy, "validate.unfilled")}`,
      ),
    );
    const tags = el("span", "catalog-tags");
    for (const tag of item.tags || []) tags.append(el("span", "pill", tag));
    body.append(tags);
    const detail = el("button", "catalog-detail", t(copy, "action.detail"));
    detail.type = "button";
    detail.title = t(copy, "catalog.detailTitle");
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
  $("selection-summary").textContent = count
    ? t(copy, "toast.selected", { count })
    : t(copy, "summary.notSelected");
}

/* ------------------------------------------------------------------- batch */

async function startBatch() {
  const caseIds = [...state.selected];
  if (!caseIds.length) {
    showToast(t(copy, "toast.selectFirst"), "error");
    return;
  }
  const protocols = [];
  if ($("protocol-a2at").checked) protocols.push("a2a-t");
  if ($("protocol-a2a").checked) protocols.push("a2a");
  if (!protocols.length) {
    showToast(t(copy, "toast.selectProtocol"), "error");
    return;
  }
  const rounds = Number($("config-rounds").value || 1);
  const runs = caseIds.length * protocols.length * rounds;
  const runLabel =
    rounds > 1
      ? t(copy, "confirm.runItemsRounds", { runs, rounds })
      : t(copy, "confirm.runItems", { runs });
  if (state.meta?.nmsEnabled && !window.confirm(t(copy, "confirm.nmsBatch", { runLabel }))) {
    return;
  }
  $("start-button").disabled = true;
  try {
    const created = await postJson("/api/tester/batches", {
      caseIds,
      protocols,
      stopAfter: $("config-stop-after").value,
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
    showToast(t(copy, "toast.startFailed", { error: error.message || error }), "error");
  } finally {
    $("start-button").disabled = false;
  }
}

async function startScratch() {
  const userInput = $("scratch-input").value.trim();
  if (!userInput) {
    $("scratch-status").textContent = t(copy, "scratch.needInput");
    return;
  }
  if (state.meta?.nmsEnabled && !window.confirm(t(copy, "confirm.nmsScratch"))) {
    return;
  }
  $("scratch-status").textContent = t(copy, "scratch.starting");
  try {
    const created = await postJson("/api/tester/scratch", {
      userInput,
      protocol: $("scratch-protocol").value,
      timeoutSeconds: Number($("scratch-timeout").value) || 300,
      keepTasks: $("config-keep-tasks").checked,
    });
    state.batch = await postJson(`/api/tester/batches/${encodeURIComponent(created.batchId)}/start`, {});
    $("scratch-status").textContent = t(copy, "scratch.started");
    switchTab("catalog");
    renderResults();
    startPolling();
  } catch (error) {
    $("scratch-status").textContent = t(copy, "toast.startFailed", { error: error.message || error });
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
    showToast(t(copy, "toast.refreshFailed", { error: error.message || error }), "error");
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
    showToast(t(copy, "toast.loadBatchFailed", { error: error.message || error }), "error");
  }
}

async function cancelBatch() {
  if (!state.batch?.batchId) return;
  try {
    await postJson(`/api/tester/batches/${encodeURIComponent(state.batch.batchId)}/cancel`, {});
    await refreshBatch();
  } catch (error) {
    showToast(t(copy, "toast.cancelFailed", { error: error.message || error }), "error");
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
    showToast(t(copy, "toast.retryFailed", { error: error.message || error }), "error");
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
    showToast(t(copy, "toast.cancelFailed", { error: error.message || error }), "error");
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
    showToast(t(copy, "toast.retryFailed", { error: error.message || error }), "error");
  }
}

function elapsedSeconds(startedAt) {
  return Math.max(1, Math.round((Date.now() - startedAt) / 1000));
}

async function exportBatch(format) {
  if (!state.batch?.batchId || state.exporting) return;
  const batchId = state.batch.batchId;
  const button = $(`export-${format}`);
  const originalLabel = button.textContent;
  const startedAt = Date.now();
  // The XLSX report fetches Langfuse metrics per run, so a click can stay silent for seconds.
  // Keep both buttons disabled and a live toast on screen until the download is handed over.
  state.exporting = true;
  $("export-json").disabled = true;
  $("export-xlsx").disabled = true;
  button.textContent = t(copy, "export.running");
  button.setAttribute("aria-busy", "true");
  const describe = () =>
    t(copy, "export.generating", {
      format: format.toUpperCase(),
      seconds: elapsedSeconds(startedAt),
    });
  showToast(describe(), "waiting", 0);
  const ticker = window.setInterval(() => showToast(describe(), "waiting", 0), 1000);
  try {
    const response = await fetch(`/api/tester/batches/${encodeURIComponent(batchId)}/export.${format}`);
    if (!response.ok) {
      const payload = await response.json().catch(() => ({}));
      throw new Error(payload.error || response.statusText);
    }
    const blob = await response.blob();
    downloadBlob(blob, response.headers.get("content-disposition"), `osp-tester-${batchId}.${format}`);
    showToast(t(copy, "export.done", { seconds: elapsedSeconds(startedAt) }), "success", 2000);
  } catch (error) {
    showToast(t(copy, "export.failed", { error: error.message || error }), "error", 8000);
  } finally {
    window.clearInterval(ticker);
    state.exporting = false;
    button.textContent = originalLabel;
    button.removeAttribute("aria-busy");
    const enabled = Boolean(state.batch);
    $("export-json").disabled = !enabled;
    $("export-xlsx").disabled = !enabled;
  }
}

function downloadBlob(blob, disposition, fallbackName) {
  const match = /filename="?([^";]+)"?/i.exec(disposition || "");
  const objectUrl = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = objectUrl;
  anchor.download = match ? match[1] : fallbackName;
  document.body.append(anchor);
  anchor.click();
  anchor.remove();
  window.setTimeout(() => URL.revokeObjectURL(objectUrl), 1000);
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
    const transport = batch.transport ? ` · ${batch.transport}` : "";
    const keep = batch.keepTasks ? t(copy, "history.keepTasks") : "";
    const concurrency = batch.concurrency > 1 ? t(copy, "history.concurrency", { count: batch.concurrency }) : "";
    const rounds = batch.rounds > 1 ? t(copy, "history.rounds", { count: batch.rounds }) : "";
    const option = el("option", "", `${batch.stopAfter || "final"}${transport}${keep}${concurrency}${rounds} · ${statusLabel(batch.status)} · ${batch.createdAt || ""}`);
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
    const chip = el("span", `stage-chip ${status}`, t(copy, "stageChip.baseline"));
    chip.title = t(copy, "stageChip.baselineTitle", {
      status: baseline.status || "",
      state: baseline.state ? ` · ${baseline.state}` : "",
      error: baseline.error ? ` · ${baseline.error}` : "",
    });
    wrap.append(chip);
  }
  const interception = item.interception;
  if (interception?.stages) {
    const validate = interception.stages.validate;
    const status = validate?.status || "recorded";
    // "拦截预检" is the blocked original request, which is separate from the "拦截" stage of the
    // request the run dispatches.
    const chip = el("span", `stage-chip ${status}`, t(copy, "stageChip.interception"));
    const slotText = slotErrorText(slotErrorsOf(validate));
    chip.title = t(copy, "stageChip.interceptionTitle", {
      status: statusLabel(status),
      message: validate?.message ? ` · ${validate.message}` : "",
      slots: slotText ? ` · ${slotText}` : "",
    });
    wrap.append(chip);
  }
  const settled = !["pending", "running", "draft"].includes(String(item.status || ""));
  for (const stage of STAGE_ORDER) {
    if (pure && SDK_STAGES.has(stage)) continue;
    if (limit >= 0 && STAGE_ORDER.indexOf(stage) > limit) continue;
    const result = item.stages?.[stage];
    // Every A2A-T item generates a Task-T and records the local gate evidence (a slot.* block
    // or a passing read-back), so the chip is visible from the start and filled when the evidence
    // exists. A finished item without a stage really skipped it.
    const status = result?.status || (settled ? "skipped" : "pending");
    const chip = el("span", `stage-chip ${status}`, `${STAGE_LABELS[stage] || stage}`);
    chip.title = t(copy, "stageChip.stageTitle", {
      stage,
      status: statusLabel(status),
      message: result?.message ? ` · ${result.message}` : "",
    });
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
  if (!tokens || tokens.available !== true) return "—";
  const total = typeof tokens.total === "number" ? tokens.total.toLocaleString() : "—";
  return `${total}`;
}

function acceptedNote(item) {
  const accepted = item.stages?.accepted;
  if (!accepted || accepted.status !== "passed") return "";
  const actual = accepted.actual && typeof accepted.actual === "object" ? accepted.actual : {};
  const state = typeof actual.state === "string" && actual.state ? actual.state : "TASK_STATE_SUBMITTED";
  const recorded =
    item.serverIntent && typeof item.serverIntent === "object"
      ? t(copy, "result.serverIntentRecorded")
      : "";
  return t(copy, "result.received", { state, recorded });
}

//: Badge text of the structural comparison of the final structured intent.
const INTENT_STRUCTURE_LABELS = {
  same: "result.intentStructureSame",
  semanticOnly: "result.intentStructureSemanticOnly",
  different: "result.intentStructureDifferent",
};

function intentStructureBadge(document) {
  if (!document || document.semantic === "unavailable") return null;
  if (document.semantic !== "same") {
    return badge("failed", t(copy, INTENT_STRUCTURE_LABELS.different));
  }
  if (document.structure === "same") {
    return badge("passed", t(copy, INTENT_STRUCTURE_LABELS.same));
  }
  return badge("waiting", t(copy, INTENT_STRUCTURE_LABELS.semanticOnly));
}

function intentStructureCell(item) {
  const node = intentStructureBadge(item.intentStructure);
  if (!node) return el("td", "metric", "—");
  node.title = t(copy, "result.intentStructureHint");
  return el("td", "", node);
}

function appendIntentStructureBlock(body, item) {
  const document = item.intentStructure;
  const node = intentStructureBadge(document);
  if (!node) return;
  const block = el("section", "diff-block");
  const head = el("div", "diff-head");
  head.append(el("strong", "", t(copy, "result.intentStructure")), node);
  block.append(head);
  const diffs = Array.isArray(document.diffs) ? document.diffs : [];
  if (!diffs.length) {
    block.append(el("p", "summary", t(copy, "result.intentStructureSameSummary")));
    body.append(block);
    return;
  }
  const table = el("table", "grid diff-table");
  const thead = document.createElement("thead");
  const headRow = document.createElement("tr");
  for (const title of [
    t(copy, "table.field"),
    t(copy, "table.kind"),
    t(copy, "table.expected"),
    t(copy, "table.actual"),
  ]) {
    headRow.append(el("th", "", title));
  }
  thead.append(headRow);
  const tbody = document.createElement("tbody");
  for (const diff of diffs) {
    const row = document.createElement("tr");
    row.append(
      el("td", "mono", diff.path || ""),
      el("td", "", diff.kind || ""),
      el("td", "mono", String(diff.expected ?? "")),
      el("td", "mono", String(diff.actual ?? "")),
    );
    tbody.append(row);
  }
  table.append(thead, tbody);
  block.append(table);
  body.append(block);
}

function resultText(item) {
  if (item.error) return item.error;
  const blocked = Object.entries(item.interception?.stages || {}).map(([stage, value]) => [
    t(copy, "result.interceptionPrefix", { stage: STAGE_LABELS[stage] || stage }),
    value,
  ]);
  const all = [...Object.entries(item.stages || {}), ...blocked];
  const failed = all.filter(([, stage]) => stage.status === "failed");
  const separator = t(copy, "listSeparator");
  if (failed.length) {
    const text = failed
      .map(([stage, value]) =>
        t(copy, "result.stageMessage", {
          stage,
          message: value.message || t(copy, "result.mismatch"),
        }),
      )
      .join(separator);
    // A red row often still received the submission payload; show that next to the failure so the
    // row is not read as "nothing came back".
    const note = acceptedNote(item);
    return note ? `${text}${separator}${note}` : text;
  }
  const waiting = Object.entries(item.stages || {}).filter(([, stage]) => stage.status === "waiting");
  if (waiting.length) {
    return waiting
      .map(([stage, value]) =>
        t(copy, "result.stageMessage", {
          stage,
          message: value.message || t(copy, "status.waiting"),
        }),
      )
      .join(separator);
  }
  const recorded = Object.entries(item.stages || {}).filter(([, stage]) => stage.status === "recorded");
  if (recorded.length) return t(copy, "result.stagesRecorded", { count: recorded.length });
  return item.status === "passed" ? t(copy, "result.allPassed") : "";
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
  const all = el("option", "", t(copy, "round.all"));
  all.value = "";
  select.append(all);
  for (let index = 1; index <= rounds; index += 1) {
    const option = el("option", "", t(copy, "round.label", { round: index }));
    option.value = String(index);
    select.append(option);
  }
  select.value = state.roundFilter;
}

function roundSummaryNote(batch) {
  const parts = [];
  for (const entry of batch.roundSummaries || []) {
    const summary = entry.summary || {};
    parts.push(
      t(copy, "round.summary", {
        round: entry.round,
        passed: summary.passed || 0,
        total: summary.total || 0,
      }),
    );
  }
  const cross = batch.crossRound || {};
  if (cross.total) {
    parts.push(t(copy, "round.stable", { stable: cross.stable || 0, total: cross.total }));
  }
  return parts.length ? ` · ${parts.join(" · ")}` : "";
}

function renderResults() {
  const batch = state.batch;
  const body = $("results-body");
  clear(body);
  if (!batch) {
    $("empty-state").hidden = false;
    $("empty-state").textContent = t(copy, "empty.noBatch");
    $("result-summary").textContent = "";
    $("language-note").hidden = true;
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
  const batchLanguage = batch.language || "zh-CN";
  const languageNote = $("language-note");
  if (batchLanguage !== language) {
    languageNote.hidden = false;
    languageNote.textContent = t(copy, "batch.languageNote", {
      language: t(copy, batchLanguage === "zh-CN" ? "language.zh" : "language.en"),
    });
  } else {
    languageNote.hidden = true;
  }
  $("round-head").hidden = !multi;
  $("round-filter-wrap").hidden = !multi;
  if (multi) populateRoundFilter(rounds);
  else state.roundFilter = "";
  const visible = multi && state.roundFilter
    ? items
        .map((item, index) => [item, index])
        .filter(([item]) => String(item.round || 1) === state.roundFilter)
    : items.map((item, index) => [item, index]);
  // Reruns queued while the batch still runs; each row stays clickable unless it is in this list.
  const rerunQueue = Array.isArray(batch.rerunQueue) ? batch.rerunQueue : [];
  $("empty-state").hidden = visible.length > 0;
  if (!items.length) $("empty-state").textContent = t(copy, "empty.noItems");
  else if (!visible.length) $("empty-state").textContent = t(copy, "empty.noRoundItems");

  visible.forEach(([item, index]) => {
    const row = document.createElement("tr");
    const queued = rerunQueue.includes(index);
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
    if (queued) status.append(document.createTextNode(" "), badge("waiting", t(copy, "status.queued")));
    const structure = intentStructureCell(item);
    const duration = el("td", "metric", durationText(item.durationMs));
    const tokens = el("td", "metric", tokenText(item.tokens));
    const runs = document.createElement("td");
    runs.className = "run-cell";
    const baseline = item.baseline && typeof item.baseline === "object" ? item.baseline : null;
    if (baseline?.runId) runs.append(runLine(t(copy, "runTag.baseline"), baseline.runId));
    if (item.runId) {
      runs.append(runLine(baseline?.runId ? t(copy, "runTag.modify") : "", item.runId));
    }
    if (!runs.childNodes.length) runs.append(document.createTextNode("—"));
    const result = el("td", "result", resultText(item));
    if (item.langfuseWarning) {
      const warning = el("div", "context-line error", t(copy, "langfuse.exportFailed"));
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
    const detail = el("button", "link-button", t(copy, "action.detail"));
    detail.type = "button";
    detail.addEventListener("click", () => openDrawer(item, index));
    actions.append(document.createTextNode(" · "), detail);
    if (queued) {
      const drop = el("button", "link-button", t(copy, "action.dequeue"));
      drop.type = "button";
      drop.title = t(copy, "action.dequeueTitle");
      drop.addEventListener("click", () => cancelItem(index));
      actions.append(document.createTextNode(" · "), drop);
    } else if (
      batch.status === "running"
      && (item.status === "running" || item.status === "pending")
    ) {
      // One item at a time: the rest of the batch keeps running.
      const cancel = el("button", "link-button", t(copy, "action.cancelItem"));
      cancel.type = "button";
      cancel.title = t(copy, "action.cancelItemTitle");
      cancel.addEventListener("click", () => cancelItem(index));
      actions.append(document.createTextNode(" · "), cancel);
    }
    // A finished row may be rerun while the rest of the batch still runs; a pending or running
    // row would be executed twice, and a queued row is already waiting.
    const rerun = el("button", "link-button", t(copy, "action.rerun"));
    rerun.type = "button";
    rerun.disabled = queued
      || item.status === "running"
      || (item.status === "pending" && batch.status === "running");
    rerun.addEventListener("click", () => rerunItem(index));
    actions.append(document.createTextNode(" · "), rerun);
    row.append(
      number, id, name, protocol, ...(round ? [round] : []),
      stages, status, structure, duration, tokens, runs, result, actions,
    );
    body.append(row);
  });

  const summary = batch.summary || {};
  const concurrencyNote =
    batch.concurrency > 1 ? t(copy, "history.concurrency", { count: batch.concurrency }) : "";
  const queuedNote = rerunQueue.length
    ? t(copy, "summary.queuedRerun", { count: rerunQueue.length })
    : "";
  const roundNote = multi ? roundSummaryNote(batch) : "";
  $("result-summary").textContent = t(copy, "summary.results", {
    total: summary.total || 0,
    passed: summary.passed || 0,
    partial: summary.partial || 0,
    failed: summary.failed || 0,
    error: summary.error || 0,
    stageRecorded: summary.stageRecorded || 0,
    concurrency: concurrencyNote,
    queued: queuedNote,
    round: roundNote,
  });
  const running = batch.status === "running";
  const retryable = (summary.failed || 0) + (summary.error || 0) + (summary.cancelled || 0) > 0;
  $("start-button").hidden = running;
  $("start-button").disabled = running;
  $("cancel-button").hidden = !running;
  $("retry-button").hidden = running || !retryable;
  $("export-json").disabled = state.exporting;
  $("export-xlsx").disabled = state.exporting;
  $("progress").hidden = !running && batch.status !== "finished" && batch.status !== "cancelled";
  const total = summary.total || 0;
  const done =
    (summary.passed || 0) + (summary.partial || 0) + (summary.failed || 0) + (summary.error || 0) + (summary.cancelled || 0);
  $("progress-bar").style.width = `${total ? Math.min(100, Math.round((done / total) * 100)) : 0}%`;
  const roundPrefix = multi
    ? t(copy, "progress.roundPrefix", { round: batch.currentRound || 1, rounds })
    : "";
  $("progress-text").textContent = running
    ? t(copy, "progress.running", {
        prefix: roundPrefix,
        done,
        total,
        running: summary.running || 0,
      })
    : t(copy, "progress.done", { prefix: roundPrefix, done, total });
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
  block.append(el("strong", "", t(copy, "drawer.runContext")));
  block.append(
    contextTable([
      { label: "runId", value: context.runId, runId: context.runId },
      { label: "traceId", value: context.traceId },
      // What this run actually dispatched: for an intercepted case that is the follow-up text,
      // not the original request the interception recorded.
      { label: "userRequest", value: item.refs?.userInput || "" },
      {
        label: item.protocol === "a2a" ? t(copy, "drawer.taskIdOuter") : "taskId",
        value: context.taskId,
      },
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
  head.append(el("strong", "", t(copy, "drawer.serverReply")));
  if (Array.isArray(item.answer?.exchanges) && item.answer.exchanges.length) {
    head.append(el("span", "summary", t(copy, "drawer.fullRecordInExchanges")));
  }
  block.append(head, contextTable(rows));
  body.append(block);
}

function appendBaselineBlock(body, item) {
  const baseline = item.baseline;
  if (!baseline || typeof baseline !== "object") return;
  const status = String(baseline.status || "");
  const label =
    status === "running"
      ? t(copy, "baseline.creating")
      : status === "accepted"
        ? t(copy, "baseline.accepted")
        : status === "failed"
          ? t(copy, "baseline.failed")
          : t(copy, "baseline.recorded");
  const block = el("section", "diff-block");
  const head = el("div", "diff-head");
  head.append(
    el("strong", "", t(copy, "baseline.title")),
    badge(status, label),
    el("span", "summary", baseline.state || (status === "running" ? t(copy, "baseline.waitingAccepted") : "")),
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

//: Per-slot validation errors the SDK returned for a blocked Task-T. They are evidence, not a
//: comparison: the case only asks whether the request was blocked.
function slotErrorsOf(result) {
  const detail = result && typeof result.detail === "object" ? result.detail : null;
  return detail && Array.isArray(detail.slotErrors) ? detail.slotErrors : [];
}

function slotErrorText(errors) {
  return errors
    .map((error) => `${error.slotName || "?"}: ${error.code || ""}`.trim())
    .join(" · ");
}

function appendSlotErrorTable(block, errors) {
  const head = el("div", "diff-head");
  head.append(
    el("strong", "", "slot_error"),
    el("span", "summary", t(copy, "slotError.count", { count: errors.length })),
  );
  block.append(head);
  const table = el("table", "grid diff-table intent-diff-table");
  const thead = document.createElement("thead");
  const headRow = document.createElement("tr");
  for (const title of [
    t(copy, "slotError.slot"),
    t(copy, "slotError.code"),
    t(copy, "slotError.description"),
  ]) {
    headRow.append(el("th", "", title));
  }
  thead.append(headRow);
  const tbody = document.createElement("tbody");
  for (const error of errors) {
    const row = document.createElement("tr");
    row.append(
      el("td", "mono", error.slotName || ""),
      el("td", "mono", error.code || ""),
      el("td", "", error.message || ""),
    );
    tbody.append(row);
  }
  table.append(thead, tbody);
  block.append(table);
}

function appendStageBlock(body, label, result, skipDetails = false) {
  const block = el("section", "diff-block");
  const head = el("div", "diff-head");
  head.append(el("strong", "", label), badge(result.status), el("span", "summary", result.message || ""));
  block.append(head);
  if (!skipDetails && (result.expected !== undefined || result.actual !== undefined)) {
    const summary = el("div", "diff-summary");
    summary.append(
      el("div", "", t(copy, "stage.expected", { value: textBlock(result.expected) })),
      el("div", "", t(copy, "stage.actual", { value: textBlock(result.actual) })),
    );
    block.append(summary);
  }
  const diffs = Array.isArray(result.diffs) ? result.diffs : [];
  if (!skipDetails && diffs.length) {
    const table = el("table", "grid diff-table");
    const thead = document.createElement("thead");
    const headRow = document.createElement("tr");
    for (const title of [
      t(copy, "table.field"),
      t(copy, "table.expected"),
      t(copy, "table.actual"),
      t(copy, "table.description"),
    ]) {
      headRow.append(el("th", "", title));
    }
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
  const slotErrors = slotErrorsOf(result);
  if (slotErrors.length) {
    appendSlotErrorTable(block, slotErrors);
  } else if (result.detail?.verdict === "blocked") {
    // No per-slot detail came back; the verdict still has to read as an intentional block.
    const rawCode = result.detail.code;
    const code = rawCode ? `${copy.lang === "en-US" ? " (" : "（"}${rawCode}${copy.lang === "en-US" ? ")" : "）"}` : "";
    block.append(el("p", "summary", t(copy, "stage.blockedNoDetail", { code })));
  }
  body.append(block);
}

const INTENT_ROW_LABELS = compareLabels(copy);

//: Statuses that count as a difference in a comparison summary. ``recorded`` rows are fixed
//: template prose (or evidence only), so they are shown but never counted.
const COMPARE_ROW_DIFFERENCES = new Set(["changed", "missing", "added"]);

function appendCompareTable(body, title, unit, rows) {
  // Rows carrying a verdict are compared twice: the status is the verbatim reading (whitespace and
  // line breaks ignored) and the verdict is the semantic one the pass/fail decision uses. They get
  // their own columns so a normalized-away difference is still visible.
  const hasVerdict = rows.some((row) => row.verdict);
  const differs = (value) => COMPARE_ROW_DIFFERENCES.has(String(value || "same"));
  const literalDiff = rows.filter((row) => differs(row.status)).length;
  const semanticDiff = rows.filter((row) => differs(hasVerdict ? row.verdict : row.status)).length;
  const summary = hasVerdict
    ? t(copy, "compare.verdictSummary", { literal: literalDiff, semantic: semanticDiff })
    : literalDiff
      ? t(copy, "compare.diffCount", { count: literalDiff, unit })
      : t(copy, "compare.allSame", { unit });
  const block = el("section", "diff-block");
  const head = el("div", "diff-head");
  head.append(el("strong", "", title), el("span", "summary", summary));
  block.append(head);
  const table = el("table", "grid diff-table intent-diff-table");
  const thead = document.createElement("thead");
  const headRow = document.createElement("tr");
  const columns = [
    unit,
    t(copy, "table.expected"),
    t(copy, "table.actual"),
    hasVerdict ? t(copy, "compare.literal") : t(copy, "table.status"),
  ];
  if (hasVerdict) columns.push(t(copy, "compare.semantic"));
  columns.push(t(copy, "table.description"));
  for (const column of columns) headRow.append(el("th", "", column));
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
    );
    if (hasVerdict) {
      const verdict = String(row.verdict || status);
      tr.append(el("td", "", INTENT_ROW_LABELS[verdict] || verdict));
    }
    tr.append(el("td", "", row.reason || ""));
    tbody.append(tr);
  }
  table.append(thead, tbody);
  block.append(table);
  body.append(block);
}

//: Sections of a ``task_t`` stage, empty for any other stage: the server payload only carries
//: them when both the expected and the generated Task-T are present.
function stageSections(stage, result) {
  return stage === "task_t" && Array.isArray(result?.sections) ? result.sections : [];
}

//: Append one stage, replacing the whole-prompt expect/actual blobs with the per-section table
//: when the stage carries sections.
function appendStageWithComparison(body, label, stage, result, intentRows) {
  const hasIntentTable = stage === "intent" && Array.isArray(intentRows) && intentRows.length > 0;
  const sections = stageSections(stage, result);
  appendStageBlock(body, label, result, hasIntentTable || sections.length > 0);
  if (hasIntentTable) {
    appendCompareTable(body, t(copy, "compare.intentTitle"), t(copy, "unit.field"), intentRows);
  }
  if (sections.length) {
    appendCompareTable(body, t(copy, "compare.taskTTitle"), t(copy, "unit.section"), sections);
  }
  return hasIntentTable;
}

function renderResultTab(item, body) {
  if (item.langfuseWarning) {
    const block = el("section", "diff-block");
    block.append(el("strong", "", t(copy, "langfuse.exportFailed")));
    block.append(el("p", "status-line error", String(item.langfuseWarning)));
    body.append(block);
  }
  appendBaselineBlock(body, item);
  appendContextBlock(body, item);
  appendServerReplyBlock(body, item);
  appendIntentStructureBlock(body, item);
  const stages = item.stages || {};
  if (!Object.keys(stages).length) {
    body.append(el("p", "status-line", t(copy, "result.noStages")));
  }
  const interception = item.interception;
  if (interception?.stages) {
    const block = el("section", "diff-block");
    block.append(el("strong", "", t(copy, "result.interceptionTitle")));
    block.append(
      el("p", "summary", t(copy, "result.followup", { value: interception.followupInput || "—" })),
    );
    block.append(el("pre", "mono pre", interception.input || "—"));
    body.append(block);
    for (const stage of STAGE_ORDER) {
      // An intercepted request is judged only on whether the block happened: the raw intent of a
      // request that must never be sent is not meaningful, so its block is not rendered.
      if (stage === "intent") continue;
      const result = interception.stages[stage];
      if (!result) continue;
      const label = stage === "validate"
        ? t(copy, "result.interceptionVerdict")
        : t(copy, "result.interceptionPrefix", { stage: STAGE_LABELS[stage] || stage });
      appendStageWithComparison(body, label, stage, result, []);
    }
  }
  const intentRows = Array.isArray(item.intentCompare) ? item.intentCompare : [];
  let intentTableRendered = false;
  for (const stage of STAGE_ORDER) {
    const result = stages[stage];
    if (!result) continue;
    if (appendStageWithComparison(body, STAGE_LABELS[stage] || stage, stage, result, intentRows)) {
      intentTableRendered = true;
    }
  }
  if (!intentTableRendered && intentRows.length) {
    appendCompareTable(body, t(copy, "compare.intentTitle"), t(copy, "unit.field"), intentRows);
  }
  if (!intentRows.length) {
    // No comparable server payload (proxy hop, simulator, or a run that stopped early): keep the
    // raw evidence instead of inventing a comparison.
    if (item.serverIntent && typeof item.serverIntent === "object") {
      body.append(caseSection(t(copy, "result.serverIntent"), item.serverIntent));
    }
    const expectedIntent = item.refs?.expectedIntent;
    if (expectedIntent && typeof expectedIntent === "object") {
      body.append(caseSection(t(copy, "result.expectedIntent"), expectedIntent));
    }
  }
  if (item.intent && typeof item.intent === "object") {
    body.append(caseSection(t(copy, "result.sdkIntent"), item.intent));
  }
  const attempts = Array.isArray(item.attempts) ? item.attempts : [];
  if (attempts.length) {
    const block = el("section", "diff-block");
    block.append(el("strong", "", t(copy, "result.attempts")));
    for (const attempt of attempts) {
      const line = el("div", "attempt-line");
      line.append(el("span", "summary", `${statusLabel(attempt.status)} · ${attempt.finishedAt || ""}`));
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
    body.append(caseSection(t(copy, "scratch.title"), item.scratch || ""));
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
    body.append(el("p", "status-line", t(copy, "case.loading")));
    return;
  }
  if (drawer.error) {
    body.append(el("p", "status-line error", t(copy, "case.loadFailed", { error: drawer.error })));
    return;
  }
  const doc = drawer.caseDoc;
  if (!doc) {
    body.append(el("p", "status-line", t(copy, "case.empty")));
    return;
  }
  const summary = doc.summary || {};
  const baselineRequest = doc.case?.baseline?.request;
  const followup = doc.case?.blocked_followup;
  const meta = el("div", "case-meta");
  meta.append(
    el("span", "pill", summary.dimension || t(copy, "case.ungrouped")),
    el("span", "pill", summary.operation || ""),
    el("span", "pill", localizeValidateLabel(copy, summary.validateLabel || "")),
    ...(summary.tags || []).map((tag) => el("span", "pill", tag)),
  );
  if (baselineRequest) meta.append(el("span", "pill", t(copy, "case.baselineCreate")));
  if (followup?.input) meta.append(el("span", "pill", t(copy, "case.interceptionFollowup")));
  body.append(meta);
  if (doc.case?.known_issue) {
    body.append(caseSection(t(copy, "case.knownIssue"), doc.case.known_issue));
  }
  if (baselineRequest) {
    const block = el("section", "diff-block");
    block.append(el("strong", "", t(copy, "case.baselineTitle")));
    block.append(el("p", "summary", t(copy, "case.baselineHint")));
    block.append(el("pre", "mono case-pre", textBlock(baselineRequest)));
    const actual = drawer.item?.baseline;
    if (actual?.runId) {
      const line = el("div", "attempt-line");
      line.append(
        el(
          "span",
          "summary",
          t(copy, "case.actualBaseline", {
            state: actual.state || "—",
            error: actual.error ? ` · ${actual.error}` : "",
          }),
        ),
      );
      const wire = el("a", "run-link", `wire ${actual.runId}`);
      wire.href = `/api/runs/${encodeURIComponent(actual.runId)}/wire`;
      wire.target = "_blank";
      line.append(document.createTextNode(" "), wire);
      block.append(line);
    }
    body.append(block);
  }
  body.append(
    caseSection(
      followup?.input ? t(copy, "case.requestWithInterception") : t(copy, "case.request"),
      doc.case?.user_input || "",
    ),
  );
  if (followup?.input) {
    const block = el("section", "diff-block");
    block.append(el("strong", "", t(copy, "case.followupTitle")));
    block.append(el("p", "summary", t(copy, "case.followupHint")));
    block.append(el("pre", "mono case-pre", textBlock(followup.input)));
    body.append(block);
    if (followup.expect) {
      body.append(caseSection(t(copy, "case.followupExpect"), followup.expect));
    }
    if (followup.assert) {
      body.append(caseSection(t(copy, "case.followupAssert"), followup.assert));
    }
  }
  const intent = doc.case?.expect?.intent;
  const expectationScope = followup?.input ? t(copy, "case.followupScope") : "";
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
    block.append(el("strong", "", t(copy, "case.expectedIntentTitle", { scope: expectationScope })), table);
    body.append(block);
  }
  const protocolBlock = doc.case?.expect?.protocols?.[protocol] || {};
  body.append(
    caseSection(
      t(copy, "case.expectedTaskT", {
        protocol: protocol === "a2a-t" ? "A2A-T" : "A2A",
        scope: expectationScope,
      }),
      protocolBlock.task_t || t(copy, "case.noTaskT"),
    ),
  );
  const negotiation = protocolBlock.negotiation;
  if (negotiation) {
    const block = el("section", "diff-block");
    block.append(el("strong", "", t(copy, "case.negotiationExpected")));
    block.append(
      el(
        "p",
        "summary",
        `expected=${negotiation.expected} · rounds=${negotiation.expected_rounds} · kinds=${(negotiation.kinds || []).join(", ") || "—"}`,
      ),
    );
    (negotiation.rounds || []).forEach((round, index) => {
      block.append(el("div", "summary", t(copy, "round.label", { round: index + 1 })));
      block.append(el("pre", "mono case-pre", textBlock(round)));
    });
    body.append(block);
  }
  body.append(caseSection(t(copy, "case.assertions"), doc.case?.assert || {}));
  body.append(caseSection(t(copy, "case.runner"), doc.case?.runner || {}));
  body.append(caseSection(t(copy, "case.source"), doc.case?.source || {}));
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
  if (baseline?.runId) {
    runs.push({ key: "baseline", label: t(copy, "runTag.baseline"), runId: String(baseline.runId) });
  }
  if (item.runId) {
    runs.push({
      key: "modify",
      label: runs.length ? t(copy, "runTag.modify") : t(copy, "runTag.run"),
      runId: String(item.runId),
    });
  }
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
    body.append(el("p", "status-line", t(copy, "trace.noRuns")));
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
    body.append(
      el("p", "status-line", t(copy, "trace.loadingRun", { run: shortId(active.runId) })),
    );
    return;
  }
  if (drawer.traceError) {
    body.append(
      el("p", "status-line error", t(copy, "trace.loadFailed", { error: drawer.traceError })),
    );
    return;
  }
  if (!drawer.traceDoc || drawer.traceRunId !== active.runId) {
    body.append(el("p", "status-line", t(copy, "trace.loading")));
    return;
  }
  const doc = drawer.traceDoc;
  const transcript =
    doc && typeof doc.transcript === "object" && doc.transcript ? doc.transcript : {};
  const meta = el("div", "case-meta");
  meta.append(
    el("span", "pill", `${active.label} ${shortId(active.runId)}`),
    el("span", "pill", String(doc.status || transcript.finalState || t(copy, "trace.running"))),
  );
  if (transcript.traceId) meta.append(el("span", "pill", `trace ${shortId(transcript.traceId)}`));
  if (transcript.taskId) meta.append(el("span", "pill", `task ${shortId(transcript.taskId)}`));
  body.append(meta);
  const events = traceEvents(doc);
  if (!events.length) {
    body.append(el("p", "status-line", t(copy, "trace.empty")));
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
    body.append(el("p", "status-line", t(copy, "exchange.empty")));
    return;
  }
  body.append(el("p", "status-line", t(copy, "exchange.note")));
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
    details.append(el("summary", "", t(copy, "exchange.fullPayload")));
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
  const roundNote =
    item.round && state.batch?.rounds > 1 ? t(copy, "drawer.roundNote", { round: item.round }) : "";
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
  applyDataI18n(document, copy);
  document.documentElement.removeAttribute("data-locale-pending");
  wireEvents();
  await loadMeta();
  await loadCatalog();
  await loadHistory();
  if (state.history.length) await openBatch(state.history[0].batchId);
}

void init();
