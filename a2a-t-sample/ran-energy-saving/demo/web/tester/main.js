/* Tester console: upload a workbook, run the batch, read the verdicts. */

const COPY = {
  zh: {
    kicker: "RAN 节能 · A2A / A2A-T",
    title: "协议测试控制台",
    presentation: "演示页面",
    comparison: "协议对比",
    importTitle: "1. 导入测试用例",
    template: "下载 Excel 模板",
    nmsWarningTitle: "当前连接真实网管",
    nmsWarningText: "执行用例会向真实网络下发任务，请确认用例内容后再开始。",
    nmsConfirm: "当前连接真实网管，将执行 {n} 条用例并可能下发真实任务。确认继续？",
    historyLabel: "最近批次",
    retry: "重跑失败",
    retrying: "正在创建重跑批次…",
    retryFailed: "重跑失败：{message}",
    historyLoadFailed: "加载历史批次失败。",
    dropTitle: "拖拽 Excel 到这里，或点击选择文件",
    dropHint: "仅支持 .xlsx，一行一条用例；场景列可留空。",
    helpSummary: "填写说明",
    help: [
      "用户请求是业务输入；场景列可留空，留空时只按用户请求执行，不校验协商类型。",
      "填写场景时，未填写的预期值取该场景的默认值。",
      "审批结果：approve（默认）自动通过，reject 自动拒绝；auto 不自动应答，等待人工或超时。",
      "传输与发现方式由后端按协议决定，不需要在 Excel 中填写。",
      "批次结果保存在当前 UI 进程内；刷新页面后需要重新导入。",
    ],
    casesTitle: "2. 用例预览",
    start: "开始执行",
    cancel: "取消执行",
    export: "导出结果",
    thId: "用例编号",
    thName: "名称",
    thProtocol: "协议",
    thScenario: "场景",
    thRequest: "用户请求",
    thExpect: "预期",
    thStatus: "状态",
    thDuration: "耗时",
    thTokens: "Token",
    thResult: "结果 / 错误",
    thRun: "Run",
    tokenOffline: "离线脚本",
    tokenMissing: "—",
    defaultScenario: "（默认）",
    scenarioRequest: "使用场景自带请求",
    reading: "正在解析 Excel…",
    imported: "已导入 {n} 条用例，其中 {r} 条可执行。",
    importFailed: "导入失败：{message}",
    onlyXlsx: "请选择 .xlsx 文件。",
    tooLarge: "文件超过 10 MB 上限。",
    starting: "正在启动…",
    startFailed: "启动失败：{message}",
    cancelling: "正在取消…",
    cancelFailed: "取消失败：{message}",
    summary: "共 {n} 条 · 可执行 {r} 条 · 通过 {p} · 失败 {f} · 错误 {e}",
    progress: "{done}/{total} 已完成",
    status: {
      pending: "待执行",
      running: "执行中",
      passed: "通过",
      failed: "失败",
      error: "错误",
      skipped: "跳过",
      invalid: "无效",
      cancelled: "已取消",
      draft: "已导入",
      finished: "已完成",
    },
    wire: "wire",
    trace: "trace",
    passedDetail: "{final} · {kind}/{rounds} 轮",
    empty: "—",
    reloadHint: "批次结果保存在当前进程内；刷新页面后需要重新导入。",
  },
  en: {
    kicker: "RAN energy saving · A2A / A2A-T",
    title: "Protocol test console",
    presentation: "Demo page",
    comparison: "Comparison",
    importTitle: "1. Import test cases",
    template: "Download Excel template",
    nmsWarningTitle: "Connected to the real NMS",
    nmsWarningText: "Running cases sends real tasks to the network. Check the sheet before starting.",
    nmsConfirm: "You are connected to the real NMS; {n} cases may change the live network. Continue?",
    historyLabel: "Recent batches",
    retry: "Retry failed",
    retrying: "Creating a retry batch…",
    retryFailed: "Retry failed: {message}",
    historyLoadFailed: "Could not load the batch history.",
    dropTitle: "Drop an Excel file here, or click to choose one",
    dropHint: "Only .xlsx; one row per case; the scenario column may stay empty.",
    helpSummary: "How to fill the sheet",
    help: [
      "The user request is the business input. The scenario column is optional; when it is empty the run is checked only against the request, not against a negotiation kind.",
      "When a scenario is named, blank expectation cells inherit that scenario's defaults.",
      "Approval: approve (default) answers a paused gate with approval, reject with rejection; auto leaves it to a human or the timeout.",
      "Transport and discovery are chosen by the backend per protocol; do not put them in the sheet.",
      "Batch results live in this UI process; reloading the page requires a new import.",
    ],
    casesTitle: "2. Case preview",
    start: "Run cases",
    cancel: "Cancel run",
    export: "Export results",
    thId: "Case ID",
    thName: "Name",
    thProtocol: "Protocol",
    thScenario: "Scenario",
    thRequest: "User request",
    thExpect: "Expected",
    thStatus: "Status",
    thDuration: "Duration",
    thTokens: "Tokens",
    thResult: "Result / error",
    thRun: "Run",
    tokenOffline: "Offline scripted",
    tokenMissing: "—",
    defaultScenario: " (default)",
    scenarioRequest: "Uses the scenario's request",
    reading: "Reading the workbook…",
    imported: "Imported {n} cases; {r} can run.",
    importFailed: "Import failed: {message}",
    onlyXlsx: "Choose an .xlsx file.",
    tooLarge: "The file exceeds the 10 MB limit.",
    starting: "Starting…",
    startFailed: "Start failed: {message}",
    cancelling: "Cancelling…",
    cancelFailed: "Cancel failed: {message}",
    summary: "{n} cases · {r} runnable · {p} passed · {f} failed · {e} errors",
    progress: "{done}/{total} done",
    status: {
      pending: "Pending",
      running: "Running",
      passed: "Passed",
      failed: "Failed",
      error: "Error",
      skipped: "Skipped",
      invalid: "Invalid",
      cancelled: "Cancelled",
      draft: "Imported",
      finished: "Finished",
    },
    wire: "wire",
    trace: "trace",
    passedDetail: "{final} · {kind}/{rounds} rounds",
    empty: "—",
    reloadHint: "Batch results live in this process; reload the page and import again.",
  },
};

const state = {
  language: "zh",
  meta: null,
  batch: null,
  history: [],
  polling: null,
  uploading: false,
};

const dom = {
  kicker: document.getElementById("kicker"),
  title: document.getElementById("title"),
  presentationLink: document.getElementById("presentation-link"),
  comparisonLink: document.getElementById("comparison-link"),
  importTitle: document.getElementById("import-title"),
  templateLink: document.getElementById("template-link"),
  dropzone: document.getElementById("dropzone"),
  dropTitle: document.getElementById("dropzone-title"),
  dropHint: document.getElementById("dropzone-hint"),
  fileInput: document.getElementById("file-input"),
  importStatus: document.getElementById("import-status"),
  helpSummary: document.getElementById("help-summary"),
  helpList: document.getElementById("help-list"),
  nmsWarning: document.getElementById("nms-warning"),
  nmsWarningTitle: document.getElementById("nms-warning-title"),
  nmsWarningText: document.getElementById("nms-warning-text"),
  historyWrap: document.getElementById("history-wrap"),
  historyLabel: document.getElementById("history-label"),
  historySelect: document.getElementById("history-select"),
  casesCard: document.getElementById("cases-card"),
  casesTitle: document.getElementById("cases-title"),
  caseSummary: document.getElementById("case-summary"),
  casesBody: document.getElementById("cases-body"),
  startButton: document.getElementById("start-button"),
  cancelButton: document.getElementById("cancel-button"),
  retryButton: document.getElementById("retry-button"),
  exportButton: document.getElementById("export-button"),
  progress: document.getElementById("progress"),
  progressBar: document.getElementById("progress-bar"),
  progressText: document.getElementById("progress-text"),
  toast: document.getElementById("toast"),
  headers: {
    id: document.getElementById("th-id"),
    name: document.getElementById("th-name"),
    protocol: document.getElementById("th-protocol"),
    scenario: document.getElementById("th-scenario"),
    request: document.getElementById("th-request"),
    expect: document.getElementById("th-expect"),
    status: document.getElementById("th-status"),
    duration: document.getElementById("th-duration"),
    tokens: document.getElementById("th-tokens"),
    result: document.getElementById("th-result"),
    run: document.getElementById("th-run"),
  },
};

function t(key) {
  return COPY[state.language][key];
}

function format(template, values) {
  return template.replace(/\{(\w+)\}/g, (_, name) =>
    values[name] === undefined ? "" : String(values[name]),
  );
}

function applyCopy() {
  document.documentElement.lang = state.language === "zh" ? "zh-CN" : "en";
  dom.kicker.textContent = t("kicker");
  dom.title.textContent = t("title");
  dom.presentationLink.textContent = t("presentation");
  dom.comparisonLink.textContent = t("comparison");
  dom.importTitle.textContent = t("importTitle");
  dom.templateLink.textContent = t("template");
  dom.dropTitle.textContent = t("dropTitle");
  dom.dropHint.textContent = t("dropHint");
  dom.helpSummary.textContent = t("helpSummary");
  dom.helpList.replaceChildren(
    ...t("help").map((line) => {
      const item = document.createElement("li");
      item.textContent = line;
      return item;
    }),
  );
  dom.nmsWarningTitle.textContent = t("nmsWarningTitle");
  dom.nmsWarningText.textContent = t("nmsWarningText");
  dom.nmsWarning.hidden = state.meta?.nmsEnabled !== true;
  dom.historyLabel.textContent = t("historyLabel");
  dom.casesTitle.textContent = t("casesTitle");
  dom.startButton.textContent = t("start");
  dom.cancelButton.textContent = t("cancel");
  dom.retryButton.textContent = t("retry");
  dom.exportButton.textContent = t("export");
  dom.headers.id.textContent = t("thId");
  dom.headers.name.textContent = t("thName");
  dom.headers.protocol.textContent = t("thProtocol");
  dom.headers.scenario.textContent = t("thScenario");
  dom.headers.request.textContent = t("thRequest");
  dom.headers.expect.textContent = t("thExpect");
  dom.headers.status.textContent = t("thStatus");
  dom.headers.duration.textContent = t("thDuration");
  dom.headers.tokens.textContent = t("thTokens");
  dom.headers.result.textContent = t("thResult");
  dom.headers.run.textContent = t("thRun");
}

async function loadMeta() {
  try {
    const response = await fetch("/api/tester/meta");
    if (!response.ok) return;
    state.meta = await response.json();
    state.language = state.meta.defaultLanguage === "en" ? "en" : "zh";
  } catch {
    state.language = "zh";
  }
  applyCopy();
}

async function loadHistory() {
  try {
    const response = await fetch("/api/tester/batches?limit=20");
    if (!response.ok) return;
    const payload = await response.json();
    state.history = Array.isArray(payload.batches) ? payload.batches : [];
  } catch {
    state.history = [];
  }
  renderHistory();
}

function renderHistory() {
  const batches = state.history || [];
  dom.historyWrap.hidden = batches.length === 0;
  dom.historySelect.replaceChildren();
  for (const batch of batches) {
    const option = document.createElement("option");
    option.value = batch.batchId;
    const source = batch.sourceName || batch.batchId;
    option.textContent = `${source} · ${statusLabel(batch.status)} · ${batch.createdAt || ""}`;
    dom.historySelect.append(option);
  }
  if (state.batch?.batchId) dom.historySelect.value = state.batch.batchId;
}

async function openBatch(batchId) {
  if (!batchId) return;
  stopPolling();
  try {
    const response = await fetch(`/api/tester/batches/${encodeURIComponent(batchId)}`);
    if (!response.ok) return;
    state.batch = await response.json();
    renderBatch();
  } catch {
    showToast(t("historyLoadFailed"), "error");
  }
}

function setImportStatus(message, kind = "") {
  dom.importStatus.textContent = message;
  dom.importStatus.className = `status-line ${kind}`.trim();
}

function showToast(message, kind = "") {
  dom.toast.textContent = message;
  dom.toast.className = `toast ${kind}`.trim();
  dom.toast.hidden = false;
  window.clearTimeout(showToast.timer);
  showToast.timer = window.setTimeout(() => {
    dom.toast.hidden = true;
  }, 5000);
}

function protocolLabel(protocol) {
  return protocol === "a2a-t" ? "A2A-T" : "A2A";
}

function statusLabel(status) {
  return t("status")[status] || status || t("empty");
}

function badge(status) {
  const span = document.createElement("span");
  span.className = `badge ${status}`;
  span.textContent = statusLabel(status);
  return span;
}

function formatDuration(ms) {
  if (typeof ms !== "number" || !Number.isFinite(ms)) return t("empty");
  if (ms < 1000) return `${Math.round(ms)} ms`;
  const seconds = ms / 1000;
  if (seconds < 60) return `${seconds.toFixed(seconds < 10 ? 2 : 1)} s`;
  const minutes = Math.floor(seconds / 60);
  return `${minutes}m ${Math.round(seconds % 60)}s`;
}

function durationCell(result) {
  const cell = document.createElement("td");
  cell.className = "metric";
  cell.textContent = formatDuration(result.durationMs);
  if (result.startedAt || result.finishedAt) {
    cell.title = `${result.startedAt || t("empty")} → ${result.finishedAt || t("empty")}`;
  }
  return cell;
}

function tokenCell(result) {
  const cell = document.createElement("td");
  cell.className = "metric";
  const tokens = result.metrics?.tokens;
  if (!tokens) {
    cell.textContent = t("tokenMissing");
    return cell;
  }
  if (tokens.available === true) {
    const total = typeof tokens.total === "number" ? tokens.total : 0;
    cell.textContent = total.toLocaleString();
    const parts = [
      `prompt ${tokens.prompt ?? t("empty")}`,
      `completion ${tokens.completion ?? t("empty")}`,
      `LLM ${tokens.llmCalls ?? 0}`,
    ];
    const latency = result.metrics?.latency || {};
    if (typeof latency.modelMs === "number") parts.push(`model ${formatDuration(latency.modelMs)}`);
    cell.title = parts.join(" · ");
    return cell;
  }
  if (tokens.scripted === true) {
    cell.textContent = t("tokenOffline");
    cell.title = `${tokens.reportedCalls ?? 0}/${tokens.llmCalls ?? 0} scripted LLM calls`;
    return cell;
  }
  cell.textContent = t("tokenMissing");
  cell.title = "The provider did not report token usage.";
  return cell;
}

function expectedText(testCase) {
  const parts = [testCase.expectedFinalState];
  if (testCase.expectedKind) parts.push(testCase.expectedKind);
  if (testCase.expectedRounds !== null && testCase.expectedRounds !== undefined) {
    parts.push(`R${testCase.expectedRounds}`);
  }
  return parts.filter(Boolean).join(" · ");
}

function resultText(testCase, result) {
  if (result.error) return result.error;
  if (result.status === "passed") {
    return format(t("passedDetail"), {
      final: result.finalState || t("empty"),
      kind: result.actualKind || t("empty"),
      rounds: result.actualRounds ?? 0,
    });
  }
  if (result.status === "pending") return "";
  if (result.status === "skipped") return testCase.enabled ? "" : t("status").skipped;
  return "";
}

function runCell(result) {
  const cell = document.createElement("td");
  if (!result.runId) {
    cell.textContent = t("empty");
    return cell;
  }
  const wire = document.createElement("a");
  wire.className = "run-link";
  wire.href = `/api/runs/${encodeURIComponent(result.runId)}/wire`;
  wire.target = "_blank";
  wire.rel = "noreferrer";
  wire.textContent = t("wire");
  const separator = document.createTextNode(" · ");
  const trace = document.createElement("a");
  trace.className = "run-link";
  trace.href = `/api/runs/${encodeURIComponent(result.runId)}/trace.json`;
  trace.target = "_blank";
  trace.rel = "noreferrer";
  trace.textContent = t("trace");
  cell.append(wire, separator, trace);
  return cell;
}

function renderBatch() {
  const batch = state.batch;
  if (!batch) return;
  dom.casesCard.hidden = false;
  dom.casesBody.replaceChildren();

  const cases = Array.isArray(batch.cases) ? batch.cases : [];
  const results = Array.isArray(batch.results) ? batch.results : [];
  cases.forEach((testCase, index) => {
    const result = results[index] || {};
    const row = document.createElement("tr");
    if (result.status === "running") row.className = "running";
    if (result.status === "invalid") row.className = "invalid";

    const number = document.createElement("td");
    number.className = "num";
    number.textContent = String(index + 1);

    const id = document.createElement("td");
    id.textContent = testCase.caseId;

    const name = document.createElement("td");
    name.textContent = testCase.name || t("empty");

    const protocol = document.createElement("td");
    protocol.textContent = protocolLabel(testCase.protocol);

    const scenario = document.createElement("td");
    scenario.textContent = testCase.scenarioExplicit
      ? testCase.scenario
      : `${testCase.scenario}${t("defaultScenario")}`;

    const request = document.createElement("td");
    request.className = "request";
    request.textContent = testCase.userRequest || t("scenarioRequest");
    if (testCase.userRequest) request.title = testCase.userRequest;

    const expect = document.createElement("td");
    expect.textContent = expectedText(testCase);

    const status = document.createElement("td");
    status.append(badge(result.status));

    const outcome = document.createElement("td");
    outcome.className = `result ${result.status === "passed" ? "pass" : result.status === "failed" || result.status === "error" || result.status === "invalid" ? "fail" : ""}`;
    outcome.textContent = resultText(testCase, result);

    row.append(
      number,
      id,
      name,
      protocol,
      scenario,
      request,
      expect,
      status,
      durationCell(result),
      tokenCell(result),
      outcome,
      runCell(result),
    );
    dom.casesBody.append(row);
  });

  const summary = batch.summary || {};
  let summaryText = format(t("summary"), {
    n: summary.total || cases.length,
    r: summary.runnable || 0,
    p: summary.passed || 0,
    f: summary.failed || 0,
    e: summary.error || 0,
  });
  if (summary.durationMs) summaryText += ` · ${formatDuration(summary.durationMs)}`;
  if (summary.tokensAvailable) {
    summaryText += ` · ${Number(summary.tokens || 0).toLocaleString()} tokens`;
  }
  dom.caseSummary.textContent = summaryText;

  const running = batch.status === "running";
  const draft = batch.status === "draft";
  const retryable =
    (summary.failed || 0) + (summary.error || 0) + (summary.cancelled || 0) > 0;
  dom.startButton.disabled = !draft;
  dom.startButton.hidden = !draft;
  dom.cancelButton.hidden = !running;
  dom.retryButton.hidden = running || !retryable;
  dom.exportButton.disabled = draft;
  if (state.batch.batchId) dom.historySelect.value = state.batch.batchId;
  dom.progress.hidden = !running && batch.status !== "finished" && batch.status !== "cancelled";

  const total = summary.runnable || 0;
  const done = (summary.passed || 0) + (summary.failed || 0) + (summary.error || 0) + (summary.cancelled || 0);
  const percent = total > 0 ? Math.min(100, Math.round((done / total) * 100)) : 0;
  dom.progressBar.style.width = `${percent}%`;
  dom.progressText.textContent = format(t("progress"), { done, total });

  if (running) startPolling();
}

async function upload(file) {
  if (state.uploading) return;
  if (!file) return;
  if (!file.name.toLowerCase().endsWith(".xlsx")) {
    setImportStatus(t("onlyXlsx"), "error");
    return;
  }
  const limit = state.meta?.maxUploadBytes || 10 * 1024 * 1024;
  if (file.size > limit) {
    setImportStatus(t("tooLarge"), "error");
    return;
  }
  state.uploading = true;
  stopPolling();
  setImportStatus(t("reading"));
  try {
    const response = await fetch("/api/tester/import", {
      method: "POST",
      headers: {
        "Content-Type": "application/octet-stream",
        "X-File-Name": encodeURIComponent(file.name),
      },
      body: file,
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      setImportStatus(format(t("importFailed"), { message: payload.error || response.statusText }), "error");
      return;
    }
    state.batch = payload;
    const summary = payload.summary || {};
    setImportStatus(
      format(t("imported"), { n: summary.total || 0, r: summary.runnable || 0 }),
      "ok",
    );
    await loadHistory();
    renderBatch();
  } catch (error) {
    setImportStatus(format(t("importFailed"), { message: error.message || error }), "error");
  } finally {
    state.uploading = false;
    dom.fileInput.value = "";
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
    const response = await fetch(`/api/tester/batches/${encodeURIComponent(state.batch.batchId)}`);
    if (!response.ok) return;
    state.batch = await response.json();
    renderBatch();
    if (state.batch.status !== "running") {
      stopPolling();
      void loadHistory();
    }
  } catch {
    // A transient fetch failure keeps the last rendered snapshot; the next tick retries.
  }
}

async function startBatch() {
  if (!state.batch || state.batch.status !== "draft") return;
  if (state.meta?.nmsEnabled === true) {
    const count = state.batch.summary?.runnable || 0;
    if (!window.confirm(format(t("nmsConfirm"), { n: count }))) return;
  }
  dom.startButton.disabled = true;
  setImportStatus(t("starting"));
  try {
    const response = await fetch(
      `/api/tester/batches/${encodeURIComponent(state.batch.batchId)}/start`,
      { method: "POST" },
    );
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      showToast(format(t("startFailed"), { message: payload.error || response.statusText }), "error");
      dom.startButton.disabled = false;
      return;
    }
    state.batch = payload;
    void loadHistory();
    renderBatch();
  } catch (error) {
    showToast(format(t("startFailed"), { message: error.message || error }), "error");
    dom.startButton.disabled = false;
  }
}

async function cancelBatch() {
  if (!state.batch || state.batch.status !== "running") return;
  dom.cancelButton.disabled = true;
  showToast(t("cancelling"));
  try {
    const response = await fetch(
      `/api/tester/batches/${encodeURIComponent(state.batch.batchId)}/cancel`,
      { method: "POST" },
    );
    if (!response.ok) {
      const payload = await response.json().catch(() => ({}));
      showToast(format(t("cancelFailed"), { message: payload.error || response.statusText }), "error");
    }
    await refreshBatch();
    await loadHistory();
  } catch (error) {
    showToast(format(t("cancelFailed"), { message: error.message || error }), "error");
  } finally {
    dom.cancelButton.disabled = false;
  }
}

async function retryFailed() {
  if (!state.batch?.batchId) return;
  dom.retryButton.disabled = true;
  showToast(t("retrying"));
  try {
    const response = await fetch(
      `/api/tester/batches/${encodeURIComponent(state.batch.batchId)}/retry`,
      { method: "POST" },
    );
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      showToast(format(t("retryFailed"), { message: payload.error || response.statusText }), "error");
      return;
    }
    state.batch = payload;
    await loadHistory();
    renderBatch();
  } catch (error) {
    showToast(format(t("retryFailed"), { message: error.message || error }), "error");
  } finally {
    dom.retryButton.disabled = false;
  }
}

function exportResults() {
  if (!state.batch?.batchId) return;
  window.location.href = `/api/tester/batches/${encodeURIComponent(state.batch.batchId)}/export.xlsx`;
}

function wireEvents() {
  dom.dropzone.addEventListener("click", () => dom.fileInput.click());
  dom.dropzone.addEventListener("keydown", (event) => {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      dom.fileInput.click();
    }
  });
  dom.fileInput.addEventListener("change", () => upload(dom.fileInput.files?.[0]));
  for (const name of ["dragenter", "dragover"]) {
    dom.dropzone.addEventListener(name, (event) => {
      event.preventDefault();
      dom.dropzone.classList.add("dragging");
    });
  }
  for (const name of ["dragleave", "drop"]) {
    dom.dropzone.addEventListener(name, (event) => {
      event.preventDefault();
      dom.dropzone.classList.remove("dragging");
    });
  }
  dom.dropzone.addEventListener("drop", (event) => {
    upload(event.dataTransfer?.files?.[0]);
  });
  dom.startButton.addEventListener("click", startBatch);
  dom.cancelButton.addEventListener("click", cancelBatch);
  dom.retryButton.addEventListener("click", retryFailed);
  dom.exportButton.addEventListener("click", exportResults);
  dom.historySelect.addEventListener("change", () => openBatch(dom.historySelect.value));
  window.addEventListener("beforeunload", stopPolling);
}

async function init() {
  await loadMeta();
  wireEvents();
  await loadHistory();
  if (state.history.length > 0) {
    await openBatch(state.history[0].batchId);
  }
}

init();
