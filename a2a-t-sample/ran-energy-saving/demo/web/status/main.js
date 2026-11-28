// Running-status page: poll the process-wide snapshot and render who is executing what.
// Every value is inserted with textContent; nothing from a batch or a run is treated as HTML.

import { applyDataI18n, batchStatusLabels, copyFor, t } from "./copy.js";

const REFRESH_MS = 2000;
const language = document.querySelector('meta[name="a2at-language"]')?.content || "zh-CN";
const copy = copyFor(language);
const BATCH_STATUS = batchStatusLabels(copy);

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
  const clock = when.toLocaleTimeString(language, { hour12: false });
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
  wrap.append(el("div", "mono", address || t(copy, "value.unknownIp")));
  const parts = [];
  const cookie = String(ownerId || "").slice(0, 8);
  parts.push(cookie ? `cookie ${cookie}` : t(copy, "value.noCookie"));
  const browser = browserLabel(data.userAgent);
  if (browser) parts.push(browser);
  wrap.append(el("div", "muted", parts.join(" · ")));
  return wrap;
}

function fillFacts(target, pairs) {
  target.replaceChildren();
  for (const [label, value] of pairs) {
    const item = document.createElement("div");
    item.append(el("b", "", `${label}${t(copy, "fact.separator")}`), document.createTextNode(value));
    target.append(item);
  }
}

function renderProcess(process) {
  const nms = process.nms ? t(copy, "value.realNms") : t(copy, "value.localRan");
  fillFacts($("process-facts"), [
    ["UI", location.host],
    [t(copy, "fact.process"), `PID ${process.pid}`],
    [
      t(copy, "fact.started"),
      t(copy, "process.started", {
        time: formatTime(process.startedAt),
        duration: formatDuration(process.uptimeSeconds),
      }),
    ],
    [
      t(copy, "fact.model"),
      t(copy, "process.model", {
        label: process.modelLabel || t(copy, "value.unconfigured"),
        mode: process.modelMode || "?",
      }),
    ],
    [t(copy, "fact.nms"), nms],
    [t(copy, "fact.ran"), process.serverUrl || "—"],
    [t(copy, "fact.dataDir"), process.runsDir || "—"],
  ]);
  $("process-mode").textContent = process.modelMode || "";
  const serial = process.sdkSerialBusy ? t(copy, "value.serialBusy") : t(copy, "value.serialIdle");
  fillFacts($("limits"), [
    [t(copy, "fact.sdkPool"), `${process.sdkInFlight}/${process.maxConcurrency} · ${serial}`],
    [t(copy, "fact.activeItems"), String(process.activeItems)],
    [
      t(copy, "fact.runningBatches"),
      t(copy, "process.runningBatches", {
        count: process.runningBatches,
        drafts: process.draftBatches,
      }),
    ],
    [
      t(copy, "fact.keptTasks"),
      t(copy, "process.keptTasks", {
        kept: process.keptTaskBatches,
        runs: process.runsHoldingTask,
      }),
    ],
    [
      t(copy, "fact.detachedRuns"),
      t(copy, "process.detachedRuns", {
        detached: process.detachedRuns,
        live: process.liveRuns,
      }),
    ],
  ]);
}

function activeSummary(batch) {
  const items = Array.isArray(batch.active) ? batch.active : [];
  if (!items.length) return el("span", "muted", "—");
  const wrap = document.createDocumentFragment();
  for (const item of items) {
    const round = batch.rounds > 1 ? t(copy, "batch.round", { round: item.round }) : "";
    const label = item.caseId ? `${item.caseId} ${item.name}` : item.name;
    wrap.append(
      el("div", "wrap", t(copy, "batch.item", { label: `${label}${round}`, protocol: item.protocol })),
    );
  }
  return wrap;
}

function renderBatches(batches) {
  const body = $("batches").querySelector("tbody");
  body.replaceChildren();
  for (const batch of batches) {
    const progress = batch.total
      ? t(copy, "batch.progress", {
          done: batch.done,
          total: batch.total,
          percent: Math.round((batch.done / batch.total) * 100),
        }) + (batch.queued ? t(copy, "batch.queued", { count: batch.queued }) : "")
      : "—";
    const mode = document.createDocumentFragment();
    if (batch.keepTasks) mode.append(badge(t(copy, "batch.keepTasks"), "warn"));
    if (batch.stopAfter) {
      mode.append(el("div", "muted", t(copy, "batch.stopAfter", { stage: batch.stopAfter })));
    }
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
  $("batch-count").textContent = t(copy, "batch.count", { count: batches.length });
}

function renderRuns(runs) {
  const body = $("runs").querySelector("tbody");
  body.replaceChildren();
  for (const run of runs) {
    const id = el("span", "mono", shortId(run.runId));
    id.title = run.runId;
    const source = document.createDocumentFragment();
    source.append(el("div", "", run.sourceKind || t(copy, "value.unknownSource")));
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
    started.append(el("div", "muted", t(copy, "process.uptime", { duration: formatDuration(run.elapsedSeconds) })));
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
  $("run-count").textContent = t(copy, "run.count", { count: runs.length });
}

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
  $("recent-count").textContent = t(copy, "recent.count", { count: recent.length });
}

function render(data) {
  renderProcess(data.process || {});
  renderBatches(Array.isArray(data.batches) ? data.batches : []);
  renderRuns(Array.isArray(data.runs) ? data.runs : []);
  renderRecent(Array.isArray(data.recent) ? data.recent : []);
  $("refreshed").textContent = t(copy, "status.refreshed", {
    time: formatTime(data.generatedAt),
    seconds: REFRESH_MS / 1000,
  });
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
    node.textContent = t(copy, "status.failed", {
      error: error && error.message ? error.message : error,
    });
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

applyDataI18n(document, copy);
document.documentElement.removeAttribute("data-locale-pending");
refresh();
start();
