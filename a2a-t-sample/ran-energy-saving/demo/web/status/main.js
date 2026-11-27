// Running-status page: poll the process-wide snapshot and render who is executing what.
// Every value is inserted with textContent; nothing from a batch or a run is treated as HTML.

const REFRESH_MS = 2000;

/** @type {number | null} */
let timer = null;

function $(id) {
  return document.getElementById(id);
}

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined && text !== null) node.textContent = String(text);
  return node;
}

function badge(text, kind) {
  return el("span", `badge ${kind || ""}`.trim(), text);
}

function cell(content) {
  const td = document.createElement("td");
  if (content instanceof Node) td.append(content);
  else td.textContent = content === undefined || content === null ? "" : String(content);
  return td;
}

function row(cells) {
  const tr = document.createElement("tr");
  for (const content of cells) tr.append(cell(content));
  return tr;
}

function shortId(value) {
  const text = String(value || "");
  const bare = text.startsWith("batch-") ? text.slice("batch-".length) : text;
  return bare.slice(0, 8);
}

function formatDuration(seconds) {
  const total = Math.max(0, Math.floor(Number(seconds) || 0));
  if (total < 60) return `${total}s`;
  const minutes = Math.floor(total / 60);
  if (minutes < 60) return `${minutes}m${total % 60 ? `${total % 60}s` : ""}`;
  const hours = Math.floor(minutes / 60);
  return `${hours}h${minutes % 60 ? `${minutes % 60}m` : ""}`;
}

function formatTime(value) {
  if (!value) return "—";
  const when = new Date(value);
  if (Number.isNaN(when.getTime())) return String(value);
  const now = new Date();
  const clock = when.toLocaleTimeString("zh-CN", { hour12: false });
  if (when.toDateString() === now.toDateString()) return clock;
  return `${String(when.getMonth() + 1).padStart(2, "0")}-${String(when.getDate()).padStart(2, "0")} ${clock}`;
}

function clientAddress(source) {
  const forwarded = String(source.forwardedFor || "").split(",")[0].trim();
  return forwarded || source.realIp || source.remoteAddress || "";
}

function browserLabel(userAgent) {
  const text = String(userAgent || "");
  if (!text) return "";
  const match = text.match(/(Edg|Edge|Chrome|Firefox|Safari)\/([\d.]+)/);
  if (!match) return text.slice(0, 28);
  return `${match[1]} ${match[2].split(".")[0]}`;
}

function ownerCell(source, ownerId) {
  const data = source || {};
  const wrap = document.createDocumentFragment();
  const address = clientAddress(data);
  wrap.append(el("div", "mono", address || "IP 未知"));
  const parts = [];
  const cookie = String(ownerId || "").slice(0, 8);
  parts.push(cookie ? `cookie ${cookie}` : "无 tester cookie");
  const browser = browserLabel(data.userAgent);
  if (browser) parts.push(browser);
  wrap.append(el("div", "muted", parts.join(" · ")));
  return wrap;
}

function fillFacts(target, pairs) {
  target.replaceChildren();
  for (const [label, value] of pairs) {
    const item = document.createElement("div");
    item.append(el("b", "", `${label}：`), document.createTextNode(value));
    target.append(item);
  }
}

function renderProcess(process) {
  const nms = process.nms
    ? (process.nmsSim ? "模拟器（nms-sim）" : "真实网管")
    : "本地 RAN Agent";
  fillFacts($("process-facts"), [
    ["UI", location.host],
    ["进程", `PID ${process.pid}`],
    ["启动", `${formatTime(process.startedAt)}（已运行 ${formatDuration(process.uptimeSeconds)}）`],
    ["模型", `${process.modelLabel || "未配置"}（${process.modelMode || "?"}）`],
    ["NMS", nms],
    ["RAN 服务", process.serverUrl || "—"],
    ["数据目录", process.runsDir || "—"],
  ]);
  $("process-mode").textContent = process.modelMode || "";
  const serial = process.sdkSerialBusy ? "串行锁忙" : "串行锁空闲";
  fillFacts($("limits"), [
    ["SDK 池", `${process.sdkInFlight}/${process.maxConcurrency} · ${serial}`],
    ["在跑用例", String(process.activeItems)],
    ["执行中批次", `${process.runningBatches}（草稿 ${process.draftBatches}）`],
    ["保留 Task", `批次 ${process.keptTaskBatches} · run ${process.runsHoldingTask}`],
    ["后台 run", `detached ${process.detachedRuns} · 全部 ${process.liveRuns}`],
  ]);
}

function activeSummary(batch) {
  const items = Array.isArray(batch.active) ? batch.active : [];
  if (!items.length) return el("span", "muted", "—");
  const wrap = document.createDocumentFragment();
  for (const item of items) {
    const round = batch.rounds > 1 ? ` · 第${item.round}轮` : "";
    const label = item.caseId ? `${item.caseId} ${item.name}` : item.name;
    wrap.append(el("div", "wrap", `${label}${round}（${item.protocol}）`));
  }
  return wrap;
}

function renderBatches(batches) {
  const body = $("batches").querySelector("tbody");
  body.replaceChildren();
  for (const batch of batches) {
    const progress = batch.total
      ? `${batch.done}/${batch.total} · ${Math.round((batch.done / batch.total) * 100)}%`
        + (batch.queued ? ` · 排队重跑 ${batch.queued}` : "")
      : "—";
    const mode = document.createDocumentFragment();
    mode.append(el("div", "", `${batch.decisionMode || "?"} · ${batch.negotiationSource || "?"}`));
    if (batch.keepTasks) mode.append(badge("保留 Task", "warn"));
    if (batch.stopAfter) mode.append(el("div", "muted", `执行到 ${batch.stopAfter}`));
    const endpoint = `${batch.transport || "?"} / ${batch.discovery || "?"}`;
    const id = el("span", "mono", shortId(batch.batchId));
    id.title = batch.batchId;
    body.append(
      row([
        id,
        ownerCell(batch.ownerSource, batch.ownerId),
        (batch.protocols || []).join(" / "),
        progress,
        activeSummary(batch),
        formatDuration(batch.elapsedSeconds),
        String(batch.concurrency || 1),
        mode,
        endpoint,
      ])
    );
  }
  $("batches-empty").hidden = batches.length > 0;
  $("batch-count").textContent = `${batches.length} 个`;
}

function renderRuns(runs) {
  const body = $("runs").querySelector("tbody");
  body.replaceChildren();
  for (const run of runs) {
    const id = el("span", "mono", shortId(run.runId));
    id.title = run.runId;
    const source = document.createDocumentFragment();
    source.append(el("div", "", run.sourceKind || "未知来源"));
    const address = clientAddress(run);
    if (address) source.append(el("div", "mono muted", address));
    const status = document.createDocumentFragment();
    status.append(badge(run.status || "?", run.status === "running" ? "action" : ""));
    if (run.detached) status.append(document.createTextNode(" "), badge("detached", "warn"));
    const task = document.createDocumentFragment();
    if (run.remoteTaskId) {
      const taskId = el("div", "mono", run.remoteTaskId.slice(0, 8));
      taskId.title = run.remoteTaskId;
      task.append(taskId);
    } else {
      task.append(el("div", "muted", "—"));
    }
    const started = document.createDocumentFragment();
    started.append(el("div", "", formatTime(run.startedAt)));
    started.append(el("div", "muted", `已 ${formatDuration(run.elapsedSeconds)}`));
    body.append(
      row([
        id,
        source,
        `${run.protocol || "?"} / ${run.transport || "?"}`,
        status,
        task,
        started,
        run.latestTaskState || "—",
      ])
    );
  }
  $("runs-empty").hidden = runs.length > 0;
  $("run-count").textContent = `${runs.length} 个`;
}

const BATCH_STATUS = {
  finished: ["完成", "ok"],
  cancelled: ["取消", ""],
  running: ["执行中", "action"],
  draft: ["草稿", ""],
};

function renderRecent(recent) {
  const body = $("recent").querySelector("tbody");
  body.replaceChildren();
  for (const batch of recent) {
    const [label, kind] = BATCH_STATUS[batch.status] || [batch.status || "?", ""];
    const id = el("span", "mono", shortId(batch.batchId));
    id.title = batch.batchId;
    const progress = batch.total ? `${batch.done}/${batch.total}` : "—";
    body.append(
      row([
        id,
        ownerCell(batch.ownerSource, batch.ownerId),
        (batch.protocols || []).join(" / "),
        badge(label, kind),
        progress,
        formatTime(batch.startedAt),
        formatTime(batch.finishedAt),
      ])
    );
  }
  $("recent-empty").hidden = recent.length > 0;
  $("recent-count").textContent = `${recent.length} 个`;
}

function render(data) {
  renderProcess(data.process || {});
  renderBatches(Array.isArray(data.batches) ? data.batches : []);
  renderRuns(Array.isArray(data.runs) ? data.runs : []);
  renderRecent(Array.isArray(data.recent) ? data.recent : []);
  $("refreshed").textContent = `更新于 ${formatTime(data.generatedAt)} · 每 ${REFRESH_MS / 1000} 秒刷新`;
}

async function refresh() {
  try {
    const response = await fetch("/api/status", { cache: "no-store" });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    render(await response.json());
    $("error").hidden = true;
  } catch (error) {
    const node = $("error");
    node.hidden = false;
    node.textContent = `读取运行状态失败：${error && error.message ? error.message : error}`;
  }
}

function start() {
  if (timer !== null) window.clearInterval(timer);
  timer = window.setInterval(refresh, REFRESH_MS);
}

function stop() {
  if (timer !== null) window.clearInterval(timer);
  timer = null;
}

document.addEventListener("visibilitychange", () => {
  $("paused").hidden = !document.hidden;
  if (document.hidden) {
    stop();
  } else {
    refresh();
    start();
  }
});

refresh();
start();
