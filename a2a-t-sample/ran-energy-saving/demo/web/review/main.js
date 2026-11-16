// Acceptance review console.
//
// The page mirrors one case × protocol task: every stored round is re-judged against the current
// corpus on the server, and the reviewer records one decision for the task. Checking and typing
// stay local until 「保存结论」; saving is the only write and refreshes the whole panel once.
//
// Pure helpers are exported so the Node tests can drive them without a DOM.

const DIMENSION_LABELS = {
  intent: "意图",
  task_t: "Task-T",
  validate: "拦截",
  negotiation: "协商",
  final_state: "结果",
  trace: "轨迹",
};

const STATUS_LABELS = {
  pass: "通过",
  fail: "失败",
  partial: "部分可判",
  recorded: "仅记录",
  missing: "缺证据",
  not_expected: "不适用",
  not_applicable: "不适用",
  not_run: "未跑",
};

const DECISION_LABELS = {
  pending: "未审核",
  accepted: "通过",
  defect: "缺陷",
  known_issue: "已知问题",
  recheck: "待复核",
};

const NMS_KIND_LABELS = {
  real: "真机",
  sim: "模拟器",
  local: "本机",
  unknown: "下游未声明",
};

const CATEGORY_LABELS = {
  sdk: "SDK",
  vendor: "网管",
  case: "用例",
  env: "环境",
  other: "其他",
  "": "—",
};

const TRACE_RESOLUTION_LABELS = {
  store: "审核库",
  tester: "tester 批次",
  client: "仅运行",
  trace: "仅轨迹",
  case_missing: "用例缺失",
  "": "—",
};

const TRACE_UNMATCHED_LABELS = {
  not_found: "未找到",
  tester_identity_only: "仅基线/重试",
};

const state = {
  meta: null,
  view: "round", // "round": batches of evidence first; "case": the corpus first
  cases: [],
  batches: [],
  batch: null, // payload of the selected batch/round
  batchView: "status", // "status": dimension table; "data": key-data table
  dataProtocol: "", // protocol filter of the key-data table, "" = both
  onlyRun: true, // hide protocol tasks this round never ran; on by default
  selectedBatch: null, // {batchId, round}
  selected: null, // {caseId, protocol}
  detail: null,
  round: 0, // index into detail.rounds, or -1 for the round matrix
  decision: { status: "pending", category: "", note: "", dirty: false },
  intentDiffOnly: true,
  taskTDiffOnly: true,
  snippet: null,
  scan: null,
  //: Result of the last trace / run lookup, plus the id the sidebar narrowed to and the run the
  //: detail pane currently shows. A trace id is not one run, so the scope keeps both.
  trace: null,
  traceQuery: "",
  traceExpanded: "",
  traceScope: null,
};

export function statusLabel(status) {
  return STATUS_LABELS[status] || String(status || "未跑");
}

export function decisionLabel(status) {
  return DECISION_LABELS[status] || String(status || "未审核");
}

//: Describe how a newer round's automatic verdict differs from the one a conclusion recorded.
export function verdictChangeText(change) {
  if (!change) return "";
  const rows = [];
  const status = change.status;
  if (status && status.from && status.to && status.from !== status.to) {
    rows.push(`整体 ${statusLabel(status.from)} → ${statusLabel(status.to)}`);
  }
  for (const row of change.dimensions || []) {
    if (!row || !row.dimension) continue;
    rows.push(`${dimensionLabel(row.dimension)} ${statusLabel(row.from)} → ${statusLabel(row.to)}`);
  }
  return rows.length ? `本轮自动判定与保存结论时不同：${rows.join("；")}。需要复核。` : "";
}

export function categoryLabel(category) {
  return CATEGORY_LABELS[category] || String(category || "—");
}

export function protocolLabel(protocol) {
  return protocol === "a2a-t" ? "A2A-T" : "A2A";
}

export function dimensionLabel(dimension) {
  return DIMENSION_LABELS[dimension] || String(dimension);
}

//: The judged dimensions a reviewer can filter by, in display order.
export const ATTENTION_DIMENSIONS = [
  "intent",
  "task_t",
  "validate",
  "negotiation",
  "final_state",
  "trace",
];

const DIMENSION_OK = new Set(["pass", "not_expected", "not_applicable"]);

const DIMENSION_MARKS = {
  pass: "✓",
  fail: "✗",
  partial: "…",
  missing: "?",
  recorded: "·",
};

//: A dimension needs attention when it did not pass and is not inapplicable; partial, missing and
//: recorded all mean the reviewer still has to look at that point.
export function dimensionNeedsAttention(status) {
  return !DIMENSION_OK.has(status);
}

//: The judged dimensions of one protocol task that need attention.
export function attentionDimensions(task) {
  const dimensions = (task && task.dimensions) || [];
  return dimensions.filter((row) => dimensionNeedsAttention(row.status));
}

export function progressText(progress) {
  if (!progress || !progress.statuses) return "";
  const statuses = progress.statuses;
  const decisions = progress.decisions || {};
  return `协议任务 ${progress.targets || 0} · 通过 ${statuses.pass || 0} · 失败 ${statuses.fail || 0}`
    + ` · 部分 ${statuses.partial || 0} · 未跑 ${statuses.not_run || 0}`
    + ` ｜ 已审核 ${decisions.accepted || 0} · 缺陷 ${decisions.defect || 0}`
    + ` · 已知问题 ${decisions.known_issue || 0} · 待复核 ${decisions.recheck || 0}`;
}

//: The static expectations a reviewer filters by: interception and per-protocol negotiation.
export function caseExpectations(caseRow) {
  const negotiation = caseRow.negotiation || {};
  return {
    blocked: Boolean(caseRow.blocked),
    a2aT: Boolean(negotiation["a2a-t"]),
    a2a: Boolean(negotiation.a2a),
  };
}

//: Human-readable expectation markers of one case, as ``[className, label]`` pairs.
export function expectationMarkers(caseRow) {
  const expected = caseExpectations(caseRow);
  const markers = [];
  if (expected.blocked) markers.push(["expect-blocked", "拦截"]);
  if (expected.a2aT) markers.push(["expect-negotiation", "A2A-T 协商"]);
  if (expected.a2a) markers.push(["expect-negotiation", "A2A 协商"]);
  return markers;
}

export function traceResolutionLabel(resolution) {
  return TRACE_RESOLUTION_LABELS[resolution] || String(resolution || "—");
}

export function traceUnmatchedLabel(reason) {
  return TRACE_UNMATCHED_LABELS[reason] || String(reason || "未找到");
}

//: One trace row needs attention when any judged point is not clean.
export function traceRowNeedsAttention(row) {
  if (!row) return false;
  return (row.dimensions || []).some((dimension) => dimensionNeedsAttention(dimension.status));
}

//: Filter the trace / run rows by identity, protocol, source, status and judged dimension.
export function filterTraceRows(rows, filters) {
  const query = String(filters.query || "").trim().toLowerCase();
  const selected = [];
  for (const row of rows || []) {
    if (filters.identity && row.input !== filters.identity) continue;
    if (filters.protocol && row.protocol !== filters.protocol) continue;
    if (filters.resolution && row.resolution !== filters.resolution) continue;
    if (filters.dimension) {
      const hit = (row.dimensions || []).some(
        (dimension) =>
          dimension.dimension === filters.dimension
          && dimensionNeedsAttention(dimension.status),
      );
      if (!hit) continue;
    }
    const trace = row.trace || {};
    if (filters.status === "attention" && !traceRowNeedsAttention(row)) continue;
    if (filters.status === "trace-missing" && trace.present) continue;
    if (
      filters.status === "unjudged"
      && !["client", "trace", "case_missing"].includes(row.resolution)
    ) {
      continue;
    }
    if (query) {
      const haystack = [
        row.input,
        row.traceId,
        row.runId,
        row.caseId,
        row.caseName,
        row.batchId,
        traceResolutionLabel(row.resolution),
      ]
        .map((value) => String(value || "").toLowerCase())
        .join(" ");
      if (!haystack.includes(query)) continue;
    }
    selected.push(row);
  }
  return selected;
}

//: The trace lookup counters, with the six attention dimensions.
export function traceSummaryText(summary) {
  if (!summary) return "";
  const dimensions = summary.dimensions || {};
  const attention = ATTENTION_DIMENSIONS.map(
    (dimension) => `${dimensionLabel(dimension)} ${dimensions[dimension] || 0}`,
  ).join(" · ");
  return `输入 ${summary.inputs || 0} · run ${summary.runs || 0} · 可判定 ${summary.judged || 0}`
    + ` · 未命中 ${summary.unmatched || 0} · 缺轨迹 ${summary.traceMissing || 0}`
    + ` · 写入 ${summary.ingested || 0} ｜ ${attention}`;
}

//: Whether this round actually produced evidence for one protocol task.
export function taskHasRun(task) {
  if (!task) return false;
  if (Number(task.observations) > 0) return true;
  return Boolean(task.status) && task.status !== "not_run";
}

//: Filter the corpus rows by case, protocol, verdict, decision and attention flags.
export function filterCases(cases, filters) {
  const query = String(filters.query || "").trim().toLowerCase();
  const rows = [];
  for (const caseRow of cases) {
    if (
      query
      && !String(caseRow.caseId).includes(query)
      && !String(caseRow.caseName || "").toLowerCase().includes(query)
    ) {
      continue;
    }
    const expected = caseExpectations(caseRow);
    if (filters.expect === "blocked" && !expected.blocked) continue;
    if (filters.expect === "a2a-t" && !expected.a2aT) continue;
    if (filters.expect === "a2a" && !expected.a2a) continue;
    const protocols = [];
    for (const task of caseRow.protocols || []) {
      if (filters.protocol && task.protocol !== filters.protocol) continue;
      if (filters.onlyRun && !taskHasRun(task)) continue;
      if (filters.status && task.status !== filters.status) continue;
      if (
        filters.dimension
        && !(task.dimensions || []).some(
          (row) => row.dimension === filters.dimension && dimensionNeedsAttention(row.status),
        )
      ) {
        continue;
      }
      const decision = task.decision || {};
      const decisionStatus = decision.status || "pending";
      if (filters.decision && decisionStatus !== filters.decision) continue;
      if (filters.category && decision.category !== filters.category) continue;
      if (filters.onlyFailures && !["fail", "partial"].includes(task.status)) continue;
      if (filters.onlyPending && decisionStatus !== "pending") continue;
      protocols.push(task);
    }
    if (!protocols.length) continue;
    rows.push({ ...caseRow, protocols });
  }
  return rows;
}

//: Keep only the Task-T sections a reviewer must explain; the fixed template prose is recorded,
//: never a difference, so "diff only" hides it as well.
export function filterTaskTRows(rows, diffOnly) {
  const all = rows || [];
  if (!diffOnly) return all.slice();
  return all.filter((row) => row.verdict !== "same" && row.verdict !== "recorded");
}

//: How one observed negotiation message participated in the verdict. The page shows every inbound
//: frame; only one frame per round is compared, so the extras say so instead of looking judged.
export function messageJudgement(match, index) {
  if (!match) return { judged: false, ok: null, label: "未参与判定" };
  const row = (match.rounds || []).find(
    (item) => item && Number(item.sourceIndex) === Number(index),
  );
  if (!row) {
    return { judged: false, ok: null, label: "未参与判定（同轮多余报文）" };
  }
  if (!row.matched) {
    return { judged: true, ok: false, label: row.reason || "报文与所有备选都不匹配" };
  }
  return { judged: true, ok: true, label: `命中备选 ${Number(row.alternative) + 1}` };
}

//: Rounds the annotated dialogue expected but never observed, ready for the page.
export function missingRoundNotes(match) {
  return ((match && match.rounds) || [])
    .filter((row) => row && row.sourceIndex === null && !row.matched)
    .map((row) => `第 ${row.round} 轮：${row.reason || "未观测到该轮协商"}`);
}

//: Filter the batch list by identifier, model, time, downstream and decision mode.
export function filterBatches(batches, filters) {
  const query = String(filters.query || "").trim().toLowerCase();
  const rows = [];
  for (const batch of batches || []) {
    if (filters.nms === "nms" && !(batch.nms && !batch.nmsSim)) continue;
    if (filters.nms === "local" && (batch.nms || batch.nmsSim)) continue;
    if (filters.nms === "sim" && !batch.nmsSim) continue;
    if (filters.mode && batch.decisionMode !== filters.mode) continue;
    if (query) {
      const haystack = [
        batch.batchId,
        batch.model,
        batch.decisionMode,
        batch.discovery,
        batch.transport,
        batch.observedAt,
      ]
        .map((value) => String(value || "").toLowerCase())
        .join(" ");
      if (!haystack.includes(query)) continue;
    }
    rows.push(batch);
  }
  return rows;
}

//: Human-readable label of one ingested batch.
export function batchLabel(batch) {
  if (!batch) return "";
  const rounds = Number(batch.rounds || 1);
  return [
    batch.batchId || "unknown",
    String(batch.observedAt || "").replace("T", " ").slice(0, 16),
    batch.model || "—",
    batch.nmsSim ? "模拟器" : batch.nms ? "真机" : "本地",
    batch.decisionMode || "—",
    `${rounds} 轮`,
  ].join(" · ");
}

//: The evidence counts of one stored round; a round the store did not summarise reads as empty.
export function roundEvidence(batch, round) {
  const index = Number(round);
  const rows = (batch && batch.roundsDetail) || [];
  return (
    rows.find((row) => Number(row.round) === index) || {
      round: index,
      observations: 0,
      cases: 0,
    }
  );
}

//: Human-readable label of one stored round.
export function roundLabel(round) {
  return [
    round.batchId || "unknown",
    `r${round.sourceRound || 1}`,
    String(round.observedAt || "").replace("T", " ").slice(0, 16),
    round.model || "—",
    round.nmsSim ? "模拟器" : round.nms ? "真机" : "本地",
    round.decisionMode || "—",
    statusLabel(round.judgement && round.judgement.status),
  ].join(" · ");
}

//: Label of one round in the selector; the run id keeps two runs of one batch apart.
export function roundOptionLabel(round) {
  const runId = String(round.runId || "");
  return runId ? `${roundLabel(round)} · ${shortId(runId)}` : roundLabel(round);
}

//: Default round: the newest real-machine live run (never the simulator), else the newest run.
//: A round without a run id has no evidence to show, so it is never preferred.
export function defaultRoundIndex(rounds) {
  if (!rounds || !rounds.length) return 0;
  const live = rounds.findIndex(
    (round) => round.runId && round.nms && !round.nmsSim && round.decisionMode === "live",
  );
  if (live >= 0) return live;
  const withRun = rounds.findIndex((round) => round.runId);
  return withRun >= 0 ? withRun : 0;
}

//: Per-dimension pass counts across rounds, for the "all rounds" view.
export function stabilityOf(rounds) {
  const rows = new Map();
  for (const round of rounds || []) {
    const judgement = round.judgement || {};
    for (const dimension of judgement.dimensions || []) {
      const entry = rows.get(dimension.dimension) || { pass: 0, total: 0 };
      entry.total += 1;
      if (dimension.status === "pass") entry.pass += 1;
      rows.set(dimension.dimension, entry);
    }
  }
  return [...rows.entries()].map(([dimension, counts]) => ({
    dimension,
    ...counts,
  }));
}

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined && text !== null) node.textContent = String(text);
  return node;
}

function textBlock(value) {
  if (value === null || value === undefined) return "—";
  if (typeof value === "string") return value;
  return JSON.stringify(value, null, 2);
}

//: Split a cell value into its collapsed preview and the full text. A truncated line can hide the
//: exact leaf that differs, so the full text must stay reachable from the cell itself.
export function textExcerpt(value, limit = 180) {
  const full = typeof value === "string" ? value : textBlock(value);
  return {
    preview: full.length > limit ? `${full.slice(0, limit)}…` : full,
    full,
    truncated: full.length > limit,
  };
}

function textCell(value, limit = 180) {
  const cell = el("td");
  const excerpt = textExcerpt(value, limit);
  if (!excerpt.truncated) {
    cell.textContent = excerpt.preview;
    return cell;
  }
  const details = el("details", "cell-more");
  details.append(el("summary", "", excerpt.preview));
  details.append(el("pre", "mono", excerpt.full));
  cell.append(details);
  return cell;
}

function field(id) {
  const node = document.getElementById(id);
  return node ? node.value : "";
}

function checked(id) {
  const node = document.getElementById(id);
  return node ? node.checked : false;
}

function showNotice(message, kind = "") {
  const node = document.getElementById("scan-note");
  if (!node) return;
  if (!message) {
    node.hidden = true;
    return;
  }
  node.hidden = false;
  node.className = kind ? `notice ${kind}` : "notice";
  node.textContent = message;
}

async function getJson(path, options) {
  const response = await fetch(path, { credentials: "same-origin", ...options });
  if (response.status === 401) {
    throw new Error("未认证：请用带 ?token=… 的链接打开，或关闭 UI_TOKEN。");
  }
  const text = await response.text();
  let payload = {};
  if (text) {
    try {
      payload = JSON.parse(text);
    } catch {
      throw new Error(`响应不是 JSON（HTTP ${response.status}）。`);
    }
  }
  if (!response.ok) throw new Error(payload.error || `请求失败（HTTP ${response.status}）。`);
  return payload;
}

async function postJson(path, body) {
  return getJson(path, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body || {}),
  });
}

function detailPath(caseId, protocol, suffix = "") {
  return `/api/review/case/${caseId}/${protocol}${suffix}`;
}

async function loadCases(progressPayload) {
  const payload = await getJson("/api/review/cases");
  state.cases = payload.cases || [];
  if (state.view === "case") {
    document.getElementById("progress").textContent = progressText(progressPayload || payload);
  }
  renderCases();
}

function currentFilters() {
  return {
    query: field("filter-query"),
    protocol: field("filter-protocol"),
    status: field("filter-status"),
    expect: field("filter-expect"),
    decision: field("filter-decision"),
    category: field("filter-category"),
    onlyFailures: checked("only-failures"),
    onlyPending: checked("only-pending"),
  };
}

function renderCases() {
  const list = document.getElementById("case-list");
  const scrollTop = list.scrollTop;
  const rows = filterCases(state.cases, currentFilters());
  document.getElementById("case-count").textContent = `${rows.length} / ${state.cases.length}`;
  list.replaceChildren();
  if (!rows.length) {
    list.append(el("p", "empty-state", "没有匹配的用例。"));
    list.scrollTop = scrollTop;
    return;
  }
  for (const caseRow of rows) {
    const block = el("div", "case");
    const head = el("div", "case-head");
    head.append(el("span", "id", `#${caseRow.caseId}`));
    head.append(el("span", "name", caseRow.caseName || ""));
    const markers = expectationMarkers(caseRow);
    if (markers.length) {
      const list = el("span", "expect-markers");
      for (const [kind, label] of markers) list.append(el("span", `badge ${kind}`, label));
      head.append(list);
    }
    block.append(head);
    for (const task of caseRow.protocols) {
      const row = el("button", "proto");
      row.type = "button";
      if (
        state.selected
        && state.selected.caseId === caseRow.caseId
        && state.selected.protocol === task.protocol
      ) {
        row.classList.add("active");
      }
      row.append(el("span", "proto-name", protocolLabel(task.protocol)));
      const badges = el("span", "badges");
      const failing = (task.dimensions || []).filter(
        (dimension) => !["pass", "not_expected", "not_applicable"].includes(dimension.status),
      );
      if (failing.length) {
        badges.append(
          el(
            "span",
            `badge ${failing.some((row2) => row2.status === "fail") ? "fail" : "partial"}`,
            failing.map((row2) => dimensionLabel(row2.dimension)).join("/"),
          ),
        );
      }
      badges.append(el("span", `badge ${task.status}`, statusLabel(task.status)));
      const decision = task.decision || {};
      const decisionStatus = decision.status || "pending";
      badges.append(
        el(
          "span",
          `badge ${decisionStatus}`,
          decision.stale || decision.verdictChanged
            ? `${decisionLabel(decisionStatus)}·待复核`
            : decisionLabel(decisionStatus),
        ),
      );
      row.append(badges);
      row.addEventListener("click", () => selectProtocol(caseRow, task.protocol));
      block.append(row);
    }
    list.append(block);
  }
  list.scrollTop = scrollTop;
}

// ---------------------------------------------------------------------------
// Round (batch) view
// ---------------------------------------------------------------------------

async function loadBatches() {
  const payload = await getJson("/api/review/batches");
  state.batches = payload.batches || [];
  renderBatches();
}

function batchFilters() {
  return {
    query: field("batch-query"),
    nms: field("batch-nms"),
    mode: field("batch-mode"),
  };
}

function renderBatches() {
  const list = document.getElementById("batch-list");
  if (!list) return;
  const scrollTop = list.scrollTop;
  const rows = filterBatches(state.batches, batchFilters());
  const count = document.getElementById("batch-count");
  if (count) count.textContent = `${rows.length} / ${state.batches.length}`;
  list.replaceChildren();
  if (!rows.length) {
    list.append(el("p", "empty-state", "没有匹配的批次；先在右上角采集证据。"));
    list.scrollTop = scrollTop;
    return;
  }
  for (const batch of rows) {
    const block = el("div", "case");
    const head = el("div", "case-head");
    head.append(el("span", "id", batch.batchId || "batch"));
    head.append(
      el(
        "span",
        "name",
        `${String(batch.observedAt || "").replace("T", " ").slice(5, 16)} · ${batch.cases || 0} 用例`,
      ),
    );
    block.append(head);
    const total = Math.max(1, Number(batch.rounds || 1));
    for (let index = 1; index <= total; index += 1) {
      const row = el("button", "proto");
      row.type = "button";
      if (
        state.selectedBatch
        && state.selectedBatch.batchId === batch.batchId
        && Number(state.selectedBatch.round) === index
      ) {
        row.classList.add("active");
      }
      row.append(el("span", "proto-name", total > 1 ? `第 ${index} 轮` : "本轮"));
      const badges = el("span", "badges");
      badges.append(
        el(
          "span",
          `badge ${batch.nmsSim ? "partial" : batch.nms ? "pass" : "not_run"}`,
          batch.nmsSim ? "模拟器" : batch.nms ? "真机" : "本地",
        ),
      );
      badges.append(el("span", "badge", batch.decisionMode || "—"));
      const evidence = roundEvidence(batch, index);
      const evidenceBadge = el(
        "span",
        "badge",
        `${evidence ? evidence.observations : 0} 条证据`,
      );
      evidenceBadge.title = evidence
        ? `本轮 ${evidence.cases} 个用例、${evidence.observations} 条证据：`
          + "一条用例×协议算一条；Modify 的基线 Create 与重试会各多一条。"
        : "本轮没有证据。";
      badges.append(evidenceBadge);
      row.append(badges);
      row.title = batchLabel(batch);
      row.addEventListener("click", () => selectBatch(batch.batchId, index));
      block.append(row);
    }
    list.append(block);
  }
  list.scrollTop = scrollTop;
}

async function selectBatch(batchId, round) {
  if (!confirmDiscardDecision()) return;
  closeDrawer();
  state.selectedBatch = { batchId, round };
  state.selected = null;
  state.detail = null;
  state.detailScope = null;
  renderBatches();
  await loadBatch();
}

async function refreshBatch() {
  if (!state.selectedBatch) return;
  const { batchId, round } = state.selectedBatch;
  const query = round ? `?round=${encodeURIComponent(round)}` : "";
  state.batch = await getJson(`/api/review/batch/${encodeURIComponent(batchId)}${query}`);
}

async function loadBatch() {
  if (!state.selectedBatch) return;
  const container = document.getElementById("detail");
  container.replaceChildren(el("section", "card", "加载中…"));
  await refreshBatch();
  renderBatch();
}

function renderBatch() {
  if (!state.batch) return;
  // The batch view owns the content pane; an open drawer would otherwise show a stale case.
  closeDrawer();
  const container = document.getElementById("detail");
  const scrollTop = container.scrollTop;
  container.replaceChildren();
  container.append(renderBatchSummary());
  container.append(renderBatchTabs());
  if (state.batchView === "data") {
    // The status table is not mounted in this tab; drop its stale references.
    state.batchTable = null;
    container.append(renderDataTable());
  } else {
    container.append(renderBatchTable());
    refreshBatchTable();
  }
  container.scrollTop = scrollTop;
  const header = document.getElementById("progress");
  if (header) header.textContent = progressText(state.batch);
}

function renderBatchTabs() {
  const bar = el("div", "view-switch");
  for (const [view, label] of [
    ["status", "本轮用例状态"],
    ["data", "关键数据"],
  ]) {
    const button = el("button", `tab ${state.batchView === view ? "active" : ""}`, label);
    button.type = "button";
    button.addEventListener("click", () => {
      state.batchView = view;
      renderBatch();
    });
    bar.append(button);
  }
  return bar;
}

const DATA_POINT_LABELS = [
  ["request.original", "原始请求"],
  ["request.dispatched", "实际下发"],
  ["slot_extraction", "槽位抽取"],
  ["task_t", "Task-T"],
  ["negotiation.message", "协商报文"],
  ["negotiation.reply", "协商回复"],
  ["task.succeeded", "成功报文"],
];

function dataRowsOf(task) {
  const rows = Array.isArray(task.data) ? task.data : [];
  return new Map(rows.map((row) => [row.id, row]));
}

//: One row per case × protocol, narrowed to one protocol when the reviewer asks for it.
export function filterProtocolTasks(caseRows, protocol) {
  const rows = [];
  for (const caseRow of Array.isArray(caseRows) ? caseRows : []) {
    for (const task of caseRow.protocols || []) {
      if (!protocol || task.protocol === protocol) rows.push({ caseRow, task });
    }
  }
  return rows;
}

export function langfuseLinkText(task) {
  return task && task.langfuseUrl ? "在 Langfuse 查看 ↗" : "—";
}

//: Provenance of one Langfuse copy. The blocked original and the dispatched request look alike
//: once rendered, so the copy's origin is part of the evidence, not decoration.
const DATA_SOURCE_LABELS = {
  task_validation: "拦截探针",
  task_prompt_rendered: "渲染提示词",
  trace_root: "轨迹根 metadata",
  message_sent: "发送报文",
  llm: "LLM 输出",
  propose: "协商报文",
  decision_requested: "协商请求",
  send_message: "服务端响应",
  a2a_request: "Task-T 扩展 metadata",
  decision: "协商决策",
  task_created: "任务创建",
  finished: "运行结束",
  status: "状态报文",
};

export function dataCopyLabel(copy) {
  if (!copy) return "";
  const label = DATA_SOURCE_LABELS[copy.source] || copy.source || "副本";
  const name = copy.span ? `${label} · ${copy.span}` : label;
  if (copy.source !== "task_validation" || !copy.verdict) return name;
  // The probe records the expected-blocked original even when the block did not fire; say what
  // was expected and what actually happened so "pass" is not read as missing evidence.
  const expected = copy.expected || "blocked";
  return `${name}（expected ${expected} / observed ${copy.verdict}）`;
}

export function dataCellText(row) {
  if (!row || row.applicable === false) return "—";
  if (row.status !== "present") return "缺";
  const copies = Array.isArray(row.copies) ? row.copies : [];
  const rounds = new Set(
    copies.map((copy) => copy.round).filter((round) => Number.isInteger(round)),
  );
  if (row.id && row.id.startsWith("negotiation.") && rounds.size) return `✓ ${rounds.size} 轮`;
  return `✓ ${copies.length || 1}`;
}

export function dataCellClass(row) {
  if (!row || row.applicable === false) return "";
  if (row.status !== "present") return row.required ? "fail" : "partial";
  return "";
}

function renderDataTable() {
  const card = el("section", "card");
  const head = el("div", "section-title");
  head.append(el("h2", "", "关键数据（1 行 = 1 用例 × 1 协议）"));
  card.append(head);
  card.append(
    el(
      "p",
      "sub",
      "只判导出的 Langfuse 轨迹里数据有没有；✓ n 表示 n 份副本中任一命中。点单元格查看该用例该协议的原文。",
    ),
  );
  const filters = el("div", "filters inline data-filters");
  const protocolSelect = el("select");
  for (const [value, label] of [
    ["", "全部协议"],
    ["a2a-t", "A2A-T"],
    ["a2a", "A2A"],
  ]) {
    const option = el("option", "", label);
    option.value = value;
    option.selected = state.dataProtocol === value;
    protocolSelect.append(option);
  }
  protocolSelect.addEventListener("change", () => {
    state.dataProtocol = protocolSelect.value;
    renderBatch();
  });
  filters.append(protocolSelect);
  const runOnly = el("label", "check");
  const runOnlyInput = document.createElement("input");
  runOnlyInput.type = "checkbox";
  runOnlyInput.checked = state.onlyRun;
  runOnlyInput.addEventListener("change", () => {
    state.onlyRun = runOnlyInput.checked;
    renderBatch();
  });
  runOnly.append(runOnlyInput, document.createTextNode(" 只看本轮已运行"));
  filters.append(runOnly);
  card.append(filters);

  const wrap = el("div", "table-wrap");
  const table = el("table", "batch-table data-table");
  const headRow = el("tr");
  for (const label of ["#", "用例", "协议", ...DATA_POINT_LABELS.map((entry) => entry[1]), "Langfuse"]) {
    headRow.append(el("th", "", label));
  }
  const thead = document.createElement("thead");
  thead.append(headRow);
  table.append(thead);
  const tbody = document.createElement("tbody");
  table.append(tbody);
  wrap.append(table);
  card.append(wrap);
  const caseRows = (state.batch && state.batch.cases) || [];
  for (const { caseRow, task } of filterProtocolTasks(caseRows, state.dataProtocol)) {
    if (state.onlyRun && !taskHasRun(task)) continue;
    const tr = el("tr");
    tr.append(el("td", "", String(caseRow.caseId)));
    tr.append(el("td", "", caseRow.caseName || ""));
    tr.append(el("td", "", protocolLabel(task.protocol)));
    const rows = dataRowsOf(task);
    for (const [pointId] of DATA_POINT_LABELS) {
      const row = rows.get(pointId);
      const cell = el("td", `data-cell ${dataCellClass(row)}`, dataCellText(row));
      if (row && row.copies && row.copies.length) {
        cell.title = row.copies
          .map((copy) => `${dataCopyLabel(copy)}${copy.round ? ` · R${copy.round}` : ""}`)
          .join("\n");
      }
      cell.addEventListener("click", () => {
        selectProtocol(caseRow, task.protocol, {
          batchId: (state.batch.batch || {}).batchId,
          round: (state.batch.batch || {}).round,
        }).catch((caught) => showNotice(caught.message, "error"));
      });
      tr.append(cell);
    }
    const link = el("td", "langfuse-cell");
    if (task.langfuseUrl) {
      const anchor = el("a", "langfuse-link", langfuseLinkText(task));
      anchor.href = task.langfuseUrl;
      anchor.target = "_blank";
      anchor.rel = "noopener noreferrer";
      anchor.title = task.traceId ? `traceId ${task.traceId}` : "在 Langfuse 中打开该次运行的轨迹";
      link.append(anchor);
    } else {
      link.append(el("span", "muted", task.traceId || "—"));
      if (task.traceId) link.title = `traceId ${task.traceId}（没有记录 Langfuse 链接）`;
    }
    tr.append(link);
    tbody.append(tr);
  }
  return card;
}

function renderBatchSummary() {
  const card = el("section", "card");
  const batch = state.batch.batch || {};
  const coverage = state.batch.coverage || {};
  const head = el("div", "section-title");
  head.append(
    el("h2", "", `批次 ${batch.batchId || "—"}${batch.round ? ` · 第 ${batch.round} 轮` : ""}`),
  );
  head.append(el("span", "summary", batchLabel(batch)));
  card.append(head);
  const facts = el("dl", "kv");
  const rows = [
    ["时间", String(batch.observedAt || "").replace("T", " ").slice(0, 19)],
    ["模型", batch.model || "—"],
    ["下游", batch.nmsSim ? "模拟器" : batch.nms ? "真机" : "本地"],
    ["发现 / 传输", [batch.discovery, batch.transport].filter(Boolean).join(" / ") || "—"],
    ["决策模式", batch.decisionMode || "—"],
    ["本批轮次", `${batch.rounds || 1}`],
    [
      "证据覆盖",
      `${coverage.observed || 0} / ${coverage.targets || 0} 协议任务（${coverage.cases || 0} 个用例）`,
    ],
  ];
  for (const [label, value] of rows) {
    facts.append(el("dt", "", label));
    facts.append(el("dd", "", textBlock(value)));
  }
  card.append(facts);
  card.append(el("p", "summary", progressText(state.batch)));
  const actions = el("div", "trace-actions");
  const fetchRound = el("button", "secondary", "抓取本轮轨迹");
  fetchRound.type = "button";
  fetchRound.disabled = !(state.meta && state.meta.traceFetchEnabled);
  fetchRound.title =
    `按 ${(state.meta && state.meta.traceConcurrency) || 2} 并发抓取本轮缺失的轨迹快照；`
    + "已有缓存不会重复请求，遇到 Langfuse 429 会停止。";
  fetchRound.addEventListener("click", () => {
    fetchRoundTraces(fetchRound).catch((caught) => showNotice(caught.message, "error"));
  });
  actions.append(fetchRound);
  card.append(actions);
  if (!(state.meta && state.meta.traceFetchEnabled)) {
    card.append(
      el("p", "hint", "轨迹抓取未启用：启动 review 时加 --fetch-traces，并配置 Langfuse 凭据。"),
    );
  }
  return card;
}

async function fetchRoundTraces(button) {
  const batch = (state.batch && state.batch.batch) || {};
  const batchId = batch.batchId || (state.selectedBatch && state.selectedBatch.batchId);
  if (!batchId) return;
  const round = Number(batch.round || (state.selectedBatch && state.selectedBatch.round) || 0);
  const observed = Number(((state.batch && state.batch.coverage) || {}).observed || 0);
  if (
    observed > 20
    && !window.confirm(
      `本轮 ${observed} 条证据，将按 2 并发抓取缺失的轨迹快照；遇到 Langfuse 429 会停止。继续？`,
    )
  ) {
    return;
  }
  const original = button.textContent;
  button.disabled = true;
  button.textContent = "抓取中…";
  try {
    const payload = await postJson(
      `/api/review/batch/${encodeURIComponent(batchId)}/traces`,
      { round: round || undefined },
    );
    const summary = payload.summary || {};
    const parts = [
      `抓取 ${summary.fetched || 0}`,
      `已有缓存 ${summary.cached || 0}`,
      `跳过 ${summary.skipped || 0}`,
    ];
    if (summary.failed) parts.push(`失败 ${summary.failed}`);
    if (summary.stopped) parts.push("触发限流，已停止");
    const failed = Number(summary.failed || 0) > 0 || summary.stopped === true;
    showNotice(`本轮轨迹：${parts.join(" · ")}`, failed ? "error" : "ok");
    await refreshBatch();
    renderBatch();
  } catch (caught) {
    showNotice(caught.message, "error");
    button.disabled = false;
    button.textContent = original;
  }
}

function renderBatchTable() {
  const card = el("section", "card");
  const head = el("div", "section-title");
  head.append(el("h2", "", "本轮用例状态"));
  const count = el("span", "summary");
  head.append(count);
  card.append(head);
  card.append(renderBatchFilters());

  const wrap = el("div", "table-wrap");
  const table = document.createElement("table");
  table.className = "batch-table";
  const headRow = document.createElement("tr");
  for (const label of ["#", "用例", "操作", "期望", "A2A-T", "A2A", "标记"]) {
    const th = document.createElement("th");
    th.textContent = label;
    headRow.append(th);
  }
  const thead = document.createElement("thead");
  thead.append(headRow);
  table.append(thead);
  const tbody = document.createElement("tbody");
  table.append(tbody);
  wrap.append(table);
  card.append(wrap);

  state.batchTable = { tbody, count };
  return card;
}

//: How many protocol tasks in the batch fail one dimension; shown in the filter options so the
//: reviewer sees where this round needs attention before choosing a filter.
function batchAttentionCount(dimension) {
  let count = 0;
  for (const caseRow of (state.batch && state.batch.cases) || []) {
    for (const task of caseRow.protocols || []) {
      if (
        (task.dimensions || []).some(
          (row) => row.dimension === dimension && dimensionNeedsAttention(row.status),
        )
      ) {
        count += 1;
      }
    }
  }
  return count;
}

function batchCaseFilters() {
  return {
    query: field("batch-filter-query"),
    protocol: field("batch-filter-protocol"),
    status: field("batch-filter-status"),
    expect: field("batch-filter-expect"),
    dimension: field("batch-filter-dimension"),
    decision: field("batch-filter-decision"),
    category: field("batch-filter-category"),
    onlyFailures: checked("batch-only-failures"),
    onlyPending: checked("batch-only-pending"),
    // Shared with the key-data tab, so switching tabs keeps the reviewer's choice.
    onlyRun: state.onlyRun,
  };
}

function renderBatchFilters() {
  const bar = el("div", "filters inline");
  const query = document.createElement("input");
  query.type = "search";
  query.id = "batch-filter-query";
  query.placeholder = "搜索编号或名称";
  query.addEventListener("input", refreshBatchTable);
  bar.append(query);
  const dimensionOptions = [["", "全部关注点"]];
  for (const dimension of ATTENTION_DIMENSIONS) {
    const count = batchAttentionCount(dimension);
    dimensionOptions.push([
      dimension,
      `${dimensionLabel(dimension)}${count ? `（${count}）` : ""}`,
    ]);
  }
  for (const [id, options] of [
    [
      "batch-filter-protocol",
      [
        ["", "全部协议"],
        ["a2a-t", "A2A-T"],
        ["a2a", "A2A"],
      ],
    ],
    [
      "batch-filter-expect",
      [
        ["", "全部期望"],
        ["blocked", "需拦截"],
        ["a2a-t", "A2A-T 协商"],
        ["a2a", "A2A 协商"],
      ],
    ],
    ["batch-filter-dimension", dimensionOptions],
    [
      "batch-filter-status",
      [
        ["", "全部自动判定"],
        ["pass", "通过"],
        ["fail", "失败"],
        ["partial", "部分可判"],
        ["not_run", "未跑"],
        ["not_applicable", "不适用"],
      ],
    ],
    [
      "batch-filter-decision",
      [
        ["", "全部审核结论"],
        ["pending", "未审核"],
        ["accepted", "通过"],
        ["defect", "缺陷"],
        ["known_issue", "已知问题"],
        ["recheck", "待复核"],
      ],
    ],
    [
      "batch-filter-category",
      [
        ["", "全部缺陷归属"],
        ["sdk", "SDK"],
        ["vendor", "网管"],
        ["case", "用例"],
        ["env", "环境"],
        ["other", "其他"],
      ],
    ],
  ]) {
    const select = document.createElement("select");
    select.id = id;
    for (const [value, label] of options) {
      const option = document.createElement("option");
      option.value = value;
      option.textContent = label;
      select.append(option);
    }
    select.addEventListener("change", refreshBatchTable);
    bar.append(select);
  }
  for (const [id, label] of [
    ["batch-only-run", "只看本轮已运行"],
    ["batch-only-failures", "只看失败/部分"],
    ["batch-only-pending", "只看未审核"],
  ]) {
    const wrapper = el("label", "check");
    const input = document.createElement("input");
    input.type = "checkbox";
    input.id = id;
    if (id === "batch-only-run") {
      input.checked = state.onlyRun;
      input.addEventListener("change", () => {
        state.onlyRun = input.checked;
        refreshBatchTable();
      });
    } else {
      input.addEventListener("change", refreshBatchTable);
    }
    wrapper.append(input, document.createTextNode(` ${label}`));
    bar.append(wrapper);
  }
  return bar;
}

function refreshBatchTable() {
  const table = state.batchTable;
  if (!table || !state.batch) return;
  const all = state.batch.cases || [];
  const rows = filterCases(all, batchCaseFilters());
  table.count.textContent = `${rows.length} / ${all.length}`;
  table.tbody.replaceChildren();
  if (!rows.length) {
    const tr = document.createElement("tr");
    const cell = document.createElement("td");
    cell.colSpan = 7;
    cell.className = "empty-state";
    cell.textContent = "没有匹配的用例。";
    tr.append(cell);
    table.tbody.append(tr);
    return;
  }
  for (const caseRow of rows) {
    const tr = document.createElement("tr");
    tr.append(cellText(`#${caseRow.caseId}`));
    tr.append(cellText(caseRow.caseName || ""));
    tr.append(cellText(caseRow.operation || ""));
    tr.append(expectationCell(caseRow));
    tr.append(protocolCell(caseRow, "a2a-t"));
    tr.append(protocolCell(caseRow, "a2a"));
    tr.append(cellText(batchMarkers(caseRow)));
    table.tbody.append(tr);
  }
}

function cellText(value) {
  const cell = document.createElement("td");
  cell.append(document.createTextNode(value === null || value === undefined ? "" : String(value)));
  return cell;
}

function expectationCell(caseRow) {
  const cell = document.createElement("td");
  const markers = expectationMarkers(caseRow);
  if (!markers.length) {
    cell.append(el("span", "muted", "—"));
    return cell;
  }
  const list = el("span", "expect-markers");
  for (const [kind, label] of markers) list.append(el("span", `badge ${kind}`, label));
  cell.append(list);
  return cell;
}

function protocolCell(caseRow, protocol) {
  const cell = document.createElement("td");
  const task = (caseRow.protocols || []).find((row) => row.protocol === protocol);
  if (!task) {
    cell.append(el("span", "muted", "—"));
    return cell;
  }
  const button = el("button", "cell-button");
  button.type = "button";
  button.append(el("span", `badge ${task.status}`, statusLabel(task.status)));
  // Every judged point inline, so one row answers "which check is not OK" without opening the case.
  const chips = el("span", "dim-chips");
  for (const row of task.dimensions || []) {
    if (row.status === "not_expected" || row.status === "not_applicable") continue;
    const mark = DIMENSION_MARKS[row.status] || "·";
    const chip = el(
      "span",
      `dim-chip ${row.status}`,
      `${mark} ${dimensionLabel(row.dimension)}`,
    );
    chip.title = `${dimensionLabel(row.dimension)} · ${statusLabel(row.status)}`
      + (row.reason ? `：${row.reason}` : "");
    chips.append(chip);
  }
  if (chips.childNodes.length) button.append(chips);
  const decision = task.decision || {};
  const decisionStatus = decision.status || "pending";
  button.append(el("span", `badge ${decisionStatus}`, decisionLabel(decisionStatus)));
  button.addEventListener("click", () => openFromBatch(caseRow, protocol));
  cell.append(button);
  return cell;
}

function batchMarkers(caseRow) {
  const markers = new Set();
  for (const task of caseRow.protocols || []) {
    const decision = task.decision || {};
    const decisionStatus = decision.status || "pending";
    if (decision.stale || decision.verdictChanged) markers.add("结论待复核");
    if (decision.onThisRound === false && decisionStatus !== "pending") markers.add("结论来自其它轮次");
  }
  return [...markers].join("；");
}

async function openFromBatch(caseRow, protocol) {
  await selectProtocol(caseRow, protocol, state.selectedBatch);
}

async function autoSelectBatch() {
  const rows = filterBatches(state.batches, batchFilters());
  if (!rows.length) {
    renderEmptyDetail(
      "还没有证据。",
      "先点右上角「采集证据」，或到评测控制台跑一轮用例。",
    );
    return;
  }
  const newest = rows[0];
  await selectBatch(newest.batchId, Math.max(1, Number(newest.rounds || 1)));
}

// ---------------------------------------------------------------------------
// Trace / run view
// ---------------------------------------------------------------------------

function traceFilters() {
  return {
    identity: state.traceExpanded,
    status: field("trace-filter-status"),
    protocol: field("trace-filter-protocol"),
    dimension: field("trace-filter-dimension"),
    resolution: field("trace-filter-resolution"),
  };
}

//: Copy-to-clipboard button used by the request blocks and the round identity line.
function copyButton(text) {
  const copy = el("button", "ghost small", "复制");
  copy.type = "button";
  copy.addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(text);
      copy.textContent = "已复制";
      window.setTimeout(() => {
        copy.textContent = "复制";
      }, 1500);
    } catch {
      copy.textContent = "复制失败";
    }
  });
  return copy;
}

function shortId(value, head = 10, tail = 6) {
  const text = String(value || "");
  if (text.length <= head + tail + 1) return text;
  return `${text.slice(0, head)}…${text.slice(-tail)}`;
}

function traceRowsForInput(id) {
  return ((state.trace && state.trace.rows) || []).filter((row) => row.input === id);
}

function traceUnmatchedForInput(id) {
  return ((state.trace && state.trace.unmatched) || []).filter((row) => row.input === id);
}

//: One trace per id: a batch can name the same workflow trace in several runs.
function missingTraceRows() {
  const seen = new Set();
  return ((state.trace && state.trace.rows) || []).filter((row) => {
    const trace = row.trace || {};
    if (trace.present || !trace.canFetch || !row.traceId) return false;
    if (seen.has(row.traceId)) return false;
    seen.add(row.traceId);
    return true;
  });
}

function renderTraceSidebar() {
  const list = document.getElementById("trace-list");
  if (!list) return;
  const count = document.getElementById("trace-count");
  const payload = state.trace;
  const ids = (payload && payload.query && payload.query.ids) || [];
  if (count) count.textContent = payload ? `${ids.length} 输入` : "";
  const scrollTop = list.scrollTop;
  list.replaceChildren();
  if (!payload) {
    list.append(el("p", "empty-state", "先粘贴 traceId / runId 并解析。"));
    return;
  }
  if (!ids.length) {
    list.append(el("p", "empty-state", "没有解析到有效 id。"));
    return;
  }
  const resolved = (payload && payload.query && payload.query.resolved) || [];
  for (const id of ids) {
    const rows = traceRowsForInput(id);
    const unmatched = traceUnmatchedForInput(id);
    const alias = !rows.length && resolved.includes(id);
    const block = el("div", "case");
    if (state.traceExpanded === id) block.classList.add("active");
    const head = el("div", "case-head");
    head.append(el("span", "id mono", shortId(id)));
    head.append(
      el(
        "span",
        "name",
        rows.length ? `${rows.length} run` : alias ? "同一运行" : "未命中",
      ),
    );
    block.append(head);
    const badges = el("span", "badges");
    if (unmatched.length) {
      badges.append(
        el("span", "badge missing", traceUnmatchedLabel(unmatched[0].reason)),
      );
    }
    if (alias) badges.append(el("span", "badge", "已并入其它 id"));
    for (const resolution of [...new Set(rows.map((row) => row.resolution))]) {
      const kind = ["store", "tester"].includes(resolution) ? "pass" : "partial";
      badges.append(el("span", `badge ${kind}`, traceResolutionLabel(resolution)));
    }
    const missing = rows.filter(
      (row) => !(row.trace || {}).present && (row.trace || {}).canFetch,
    ).length;
    if (missing) badges.append(el("span", "badge missing", `缺轨迹 ${missing}`));
    const attention = rows.filter(traceRowNeedsAttention).length;
    if (attention) badges.append(el("span", "badge fail", `关注 ${attention}`));
    block.append(badges);
    block.title = id;
    block.addEventListener("click", () => {
      // A trace id pasted next to its run id resolves to the same evidence row; narrow to the
      // input the row is grouped under instead of showing an empty table.
      const target = alias
        ? ((state.trace.rows || []).find(
          (row) => row.traceId === id || row.runId === id,
        ) || {}).input || id
        : id;
      state.traceExpanded = state.traceExpanded === target ? "" : target;
      renderTraceSidebar();
      renderTraceMain();
    });
    list.append(block);
  }
  list.scrollTop = scrollTop;
}

async function resolveTraces(options = {}) {
  const text = String(
    options.input !== undefined ? options.input : field("trace-input"),
  ).trim();
  if (!text) {
    showNotice("请先粘贴 traceId / runId。", "error");
    return;
  }
  const button = document.getElementById("trace-resolve");
  if (button) button.disabled = true;
  try {
    const payload = await postJson("/api/review/traces", { ids: text });
    state.trace = payload;
    state.traceQuery = text;
    state.traceExpanded = "";
    renderTraceSidebar();
    if (!options.silent || !state.traceScope) renderTraceMain();
    if (!options.silent) {
      const summary = payload.summary || {};
      showNotice(
        `解析 ${summary.inputs || 0} 个 id：命中 ${summary.runs || 0} 个 run`
          + `，未命中 ${summary.unmatched || 0}，写入审核库 ${summary.ingested || 0}。`,
        "ok",
      );
    }
  } catch (caught) {
    showNotice(caught.message, "error");
  } finally {
    if (button) button.disabled = false;
  }
}

function clearTraceInput() {
  const node = document.getElementById("trace-input");
  if (node) node.value = "";
  state.trace = null;
  state.traceQuery = "";
  state.traceExpanded = "";
  state.traceScope = null;
  state.detail = null;
  state.selected = null;
  renderTraceSidebar();
  renderEmptyDetail(
    "粘贴一个或一批 traceId / runId，直接查看这些运行的判定与轨迹。",
    "以运行 id 为入口，不用先知道用例和批次；支持整段日志和 Langfuse URL。",
  );
}

function renderTraceMain() {
  if (!state.trace) return;
  const container = document.getElementById("detail");
  const scrollTop = container.scrollTop;
  container.replaceChildren();
  container.append(renderTraceSummaryCard());
  const rows = filterTraceRows(state.trace.rows || [], traceFilters());
  container.append(renderTraceTable(rows));
  const unmatched = (state.trace.unmatched || []).filter(
    (row) => !state.traceExpanded || row.input === state.traceExpanded,
  );
  if (unmatched.length) container.append(renderTraceUnmatchedCard(unmatched));
  container.scrollTop = scrollTop;
}

function renderTraceSummaryCard() {
  const card = el("section", "card");
  const head = el("div", "section-title");
  head.append(el("h2", "", "轨迹 / 运行复核"));
  head.append(el("span", "summary", traceSummaryText(state.trace && state.trace.summary)));
  card.append(head);
  const dropped = Number((state.trace && state.trace.query && state.trace.query.dropped) || 0);
  if (dropped) {
    card.append(el("p", "hint", `超过单次上限，已丢弃 ${dropped} 个 id。`));
  }
  const actions = el("div", "trace-actions");
  const missing = missingTraceRows();
  const fetchAll = el("button", "secondary", `抓取缺失轨迹（${missing.length}）`);
  fetchAll.type = "button";
  fetchAll.disabled = !missing.length || !(state.meta && state.meta.traceFetchEnabled);
  fetchAll.addEventListener("click", () => {
    fetchMissingTraces(fetchAll).catch((caught) => showNotice(caught.message, "error"));
  });
  actions.append(fetchAll);
  for (const [format, label] of [["yaml", "导出本批 YAML"], ["json", "导出本批 JSON"]]) {
    const button = el("button", "ghost", label);
    button.type = "button";
    button.addEventListener("click", () => {
      downloadTraceExport(format).catch((caught) => showNotice(caught.message, "error"));
    });
    actions.append(button);
  }
  card.append(actions);
  return card;
}

function renderTraceTable(rows) {
  const card = el("section", "card");
  if (!rows.length) {
    card.append(el("p", "empty-state", "没有匹配的运行。"));
    return card;
  }
  const wrap = el("div", "table-wrap");
  const table = el("table", "batch-table");
  const headRow = el("tr");
  for (const title of [
    "traceId",
    "runId",
    "用例",
    "协议",
    "批次 / 轮次",
    "判定",
    "关注点",
    "轨迹",
    "结论",
  ]) {
    headRow.append(el("th", "", title));
  }
  const thead = el("thead");
  thead.append(headRow);
  table.append(thead);
  const body = el("tbody");
  for (const row of rows) {
    const tr = el("tr");
    if (row.status === "fail") tr.className = "fail";
    tr.title = `打开 ${row.traceId || row.input}${row.runId ? ` · ${row.runId}` : ""}`;
    tr.addEventListener("click", () => {
      openTraceRow(row).catch((caught) => showNotice(caught.message, "error"));
    });
    tr.append(
      el("td", "mono", shortId(row.traceId || row.input)),
      el("td", "mono", row.runId ? shortId(row.runId) : "—"),
      el("td", "", row.caseId ? `#${row.caseId} ${row.caseName || ""}` : "—"),
      el("td", "", row.protocol ? protocolLabel(row.protocol) : "—"),
      el(
        "td",
        "",
        row.batchId
          ? `${row.batchId}${row.sourceRound ? ` r${row.sourceRound}` : ""}`
          : "—",
      ),
      traceVerdictCell(row),
      traceAttentionCell(row),
      traceStateCell(row),
      traceDecisionCell(row),
    );
    body.append(tr);
  }
  table.append(body);
  wrap.append(table);
  card.append(wrap);
  return card;
}

function traceVerdictCell(row) {
  const td = el("td");
  if (["store", "tester"].includes(row.resolution) && row.caseId) {
    td.append(el("span", `badge ${row.status}`, statusLabel(row.status)));
    return td;
  }
  td.append(el("span", "badge partial", traceResolutionLabel(row.resolution)));
  if (row.runStatus) td.append(el("span", "sub", row.runStatus));
  if (row.finalState) td.append(el("span", "sub", String(row.finalState)));
  return td;
}

function traceAttentionCell(row) {
  const td = el("td");
  for (const dimension of row.dimensions || []) {
    if (!dimensionNeedsAttention(dimension.status)) continue;
    const mark = DIMENSION_MARKS[dimension.status] || "";
    const chip = el(
      "span",
      `dim-chip ${dimension.status}`,
      `${dimensionLabel(dimension.dimension)} ${mark}`.trim(),
    );
    chip.title = dimension.reason || "";
    td.append(chip);
  }
  if (!td.childNodes.length) td.append(el("span", "muted", "—"));
  return td;
}

function traceStateCell(row) {
  const td = el("td");
  const trace = row.trace || {};
  let label = "不可抓取";
  let kind = "not_run";
  if (trace.present) {
    label = trace.exportState === "partial" ? "快照部分" : "已缓存";
    kind = trace.exportState === "partial" ? "partial" : "pass";
  } else if (trace.exportState === "failed") {
    label = "抓取失败";
    kind = "fail";
  } else if (trace.canFetch) {
    label = "缺轨迹";
    kind = "partial";
  }
  td.append(el("span", `badge ${kind}`, label));
  if (trace.capturedAt) {
    td.append(
      el("span", "sub", String(trace.capturedAt).replace("T", " ").slice(0, 16)),
    );
  }
  return td;
}

function traceDecisionCell(row) {
  const td = el("td");
  const decision = row.decision || {};
  const status = decision.status || "pending";
  td.append(el("span", "badge", decisionLabel(status)));
  if (decision.stale) td.append(el("span", "badge stale", "待复核"));
  return td;
}

function renderTraceUnmatchedCard(unmatched) {
  const card = el("section", "card");
  card.append(el("h2", "", `未命中（${unmatched.length}）`));
  for (const row of unmatched) {
    const line = el("p", "sub");
    line.append(el("span", "mono", row.input));
    line.append(
      document.createTextNode(
        ` · ${traceUnmatchedLabel(row.reason)} · ${row.hint || ""}`,
      ),
    );
    card.append(line);
  }
  return card;
}

async function openTraceRow(row) {
  const traceId = row.traceId || row.input;
  if (!traceId) return;
  state.traceScope = { traceId, runId: row.runId || "", input: row.input };
  const container = document.getElementById("detail");
  container.replaceChildren(el("section", "card", "加载中…"));
  const query = row.runId ? `?runId=${encodeURIComponent(row.runId)}` : "";
  const payload = await getJson(
    `/api/review/trace/${encodeURIComponent(traceId)}${query}`,
  );
  applyTraceDetail(payload, row.runId || "");
}

async function reloadTraceDetail() {
  const scope = state.traceScope;
  if (!scope) return;
  const query = scope.runId ? `?runId=${encodeURIComponent(scope.runId)}` : "";
  const payload = await getJson(
    `/api/review/trace/${encodeURIComponent(scope.traceId)}${query}`,
  );
  applyTraceDetail(payload, scope.runId || "");
}

function applyTraceDetail(payload, runId) {
  if (payload.mode === "evidence") {
    const caseRow = payload.case || {};
    state.selected = {
      caseId: caseRow.id,
      protocol: payload.protocol,
      caseName: caseRow.name,
    };
    state.detail = payload;
    state.round = 0;
    state.detailScope = runId ? { runId } : null;
    renderDetail();
    return;
  }
  state.selected = null;
  state.detail = null;
  renderTraceOnlyDetail(payload);
}

function renderTraceOnlyDetail(payload) {
  const container = document.getElementById("detail");
  container.replaceChildren();
  const bar = el("div", "backbar");
  const back = el("button", "ghost", "← 返回轨迹清单");
  back.type = "button";
  back.addEventListener("click", backToTraceList);
  bar.append(back);
  container.append(bar);

  const head = el("section", "card");
  const title = el("div", "section-title");
  title.append(el("h2", "", `轨迹 / 运行 ${shortId(payload.traceId)}`));
  title.append(
    el("span", "badge partial", payload.mode === "run" ? "仅运行" : "仅轨迹"),
  );
  head.append(title);
  const facts = el("dl", "kv");
  for (const [label, value] of [
    ["traceId", payload.traceId],
    ["runId", payload.runId || "—"],
    ["相关 run", (payload.relatedRunIds || []).join(", ") || "—"],
  ]) {
    facts.append(el("dt", "", label));
    facts.append(el("dd", "mono", textBlock(value)));
  }
  head.append(facts);
  for (const hint of payload.hints || []) head.append(el("p", "hint", hint));
  container.append(head);

  for (const run of (payload.runs || []).slice(0, 20)) {
    container.append(renderRunCard(run));
  }
  if ((payload.runs || []).length > 20) {
    container.append(
      el("p", "sub", `仅显示前 20 个运行，共 ${payload.runs.length} 个。`),
    );
  }
  container.append(renderRawTraceCard(payload.tracePayload || {}, payload));
}

function renderRunCard(run) {
  const card = el("section", "card");
  const title = el("div", "section-title");
  title.append(el("h2", "", `运行 ${shortId(run.runId)}`));
  if (run.protocol) title.append(el("span", "badge", protocolLabel(run.protocol)));
  if (run.status) title.append(el("span", "badge", String(run.status)));
  card.append(title);
  const source = run.source && typeof run.source === "object" ? run.source : {};
  const facts = el("dl", "kv");
  for (const [label, value] of [
    ["traceId", run.traceId],
    ["终态", run.finalState],
    ["来源", [run.sourceKind, source.batchId ? `批次 ${source.batchId}` : "", source.caseId ? `用例 ${source.caseId}` : ""].filter(Boolean).join(" · ")],
    ["时间", [run.createdAt, run.finishedAt].filter(Boolean).join(" → ")],
    ["服务端 Task", run.serverTaskId],
    ["耗时 / Token", run.durationMs == null ? "" : `${run.durationMs} ms`],
  ]) {
    facts.append(el("dt", "", label));
    facts.append(el("dd", "", textBlock(value === null || value === undefined || value === "" ? "—" : value)));
  }
  card.append(facts);
  if (run.userRequest) card.append(el("pre", "request-text", String(run.userRequest)));
  const events = run.negotiationEvents || [];
  if (events.length) {
    const box = document.createElement("details");
    box.open = true;
    box.append(el("summary", "", `协商事件（${events.length} 条）`));
    const table = el("table", "grid");
    const headRow = el("tr");
    for (const titleText of ["轮次", "类型", "执行语", "方向", "协议"]) {
      headRow.append(el("th", "", titleText));
    }
    const thead = el("thead");
    thead.append(headRow);
    table.append(thead);
    const body = el("tbody");
    for (const event of events) {
      const tr = el("tr");
      tr.append(
        el("td", "", event.round == null ? "—" : String(event.round)),
        el("td", "", event.kind || ""),
        el("td", "", event.performative || ""),
        el("td", "", event.direction || ""),
        el("td", "", event.protocol || ""),
      );
      body.append(tr);
    }
    table.append(body);
    box.append(table);
    card.append(box);
  }
  return card;
}

function renderRawTraceCard(trace, payload) {
  const card = el("section", "card");
  card.append(el("h2", "", "轨迹快照"));
  const frames = trace.frames || {};
  const nms = frames.nms || {};
  const facts = [
    trace.traceId ? `traceId ${trace.traceId}` : "",
    trace.exportState ? `状态 ${trace.exportState}` : "",
    trace.capturedAt ? `抓取 ${trace.capturedAt}` : "",
    trace.spanLevel ? `层级 ${trace.spanLevel}` : "",
    nms.observed != null ? `NMS 帧 ${nms.observed}/${nms.expected ?? "—"}` : "",
    (trace.observations || []).length
      ? `观测 ${trace.observations.length} 条`
      : "",
  ].filter(Boolean);
  if (facts.length) card.append(el("p", "sub", facts.join(" · ")));
  for (const warning of trace.warnings || []) {
    card.append(el("p", "hint", warning));
  }
  const canFetch = Boolean(
    state.meta && state.meta.traceFetchEnabled && trace.canFetch !== false,
  );
  if (canFetch) {
    const button = el(
      "button",
      "ghost small",
      trace.exportState && trace.exportState !== "not_exported"
        ? "重新抓取"
        : "抓取轨迹",
    );
    button.type = "button";
    button.addEventListener("click", () => {
      fetchTraceInTraceView(
        button,
        payload.traceId,
        payload.runId,
        Boolean(trace.exportState && trace.exportState !== "not_exported"),
      ).catch((caught) => showNotice(caught.message, "error"));
    });
    card.append(button);
  } else if (state.meta && state.meta.traceConfigured === false) {
    card.append(
      el(
        "p",
        "hint",
        "未配置 Langfuse 凭据（LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY），抓取会失败。",
      ),
    );
  } else if (!(state.meta && state.meta.traceFetchEnabled)) {
    card.append(
      el("p", "hint", "轨迹抓取未启用：启动 review 时加 --fetch-traces。"),
    );
  }
  const observations = trace.observations || [];
  if (observations.length) {
    card.append(
      traceObservationTable(observations, Boolean(trace.observationsTruncated)),
    );
  }
  return card;
}

function traceObservationTable(observations, truncated) {
  const box = document.createElement("details");
  box.append(el("summary", "", `轨迹观测（${observations.length} 条）`));
  const table = el("table", "grid");
  const headRow = el("tr");
  for (const title of ["观测", "类型", "轮次", "侧", "runId", "状态"]) {
    headRow.append(el("th", "", title));
  }
  const thead = el("thead");
  thead.append(headRow);
  table.append(thead);
  const body = el("tbody");
  for (const row of observations) {
    const tr = el("tr");
    tr.append(
      el("td", "", [row.name, row.kind || row.stage].filter(Boolean).join(" · ")),
      el("td", "", row.type || ""),
      el("td", "", row.round == null ? "—" : String(row.round)),
      el("td", "", row.side || ""),
      el("td", "mono", row.runId || "—"),
      el("td", "", row.status || ""),
    );
    body.append(tr);
  }
  table.append(body);
  box.append(table);
  if (truncated) box.append(el("p", "hint", "快照超过上限，观测列表已截断。"));
  return box;
}

async function fetchTraceInTraceView(button, traceId, runId, force) {
  button.disabled = true;
  const original = button.textContent;
  button.textContent = "抓取中…";
  try {
    await postJson(`/api/review/trace/${encodeURIComponent(traceId)}/fetch`, {
      runId: runId || undefined,
      force,
    });
    showNotice(`已抓取轨迹 ${shortId(traceId)}。`, "ok");
    await resolveTraces({ silent: true });
    if (state.traceScope && state.traceScope.traceId === traceId) {
      await reloadTraceDetail();
    }
  } catch (caught) {
    showNotice(caught.message, "error");
    button.disabled = false;
    button.textContent = original;
  }
}

async function fetchMissingTraces(button) {
  const rows = missingTraceRows();
  if (!rows.length) return;
  if (
    rows.length > 20
    && !window.confirm(
      `将依次抓取 ${rows.length} 条轨迹；遇到 Langfuse 429 会停止。继续？`,
    )
  ) {
    return;
  }
  button.disabled = true;
  let done = 0;
  let failure = "";
  for (const row of rows) {
    button.textContent = `抓取中 ${done + 1}/${rows.length}…`;
    try {
      await postJson(`/api/review/trace/${encodeURIComponent(row.traceId)}/fetch`, {
        runId: row.runId || undefined,
      });
      done += 1;
    } catch (caught) {
      failure = caught.message;
      break;
    }
  }
  showNotice(
    `抓取完成 ${done}/${rows.length}${failure ? `；停止原因：${failure}` : ""}`,
    failure ? "error" : "ok",
  );
  await resolveTraces({ silent: true });
  if (!state.traceScope) renderTraceMain();
  else button.disabled = false;
}

async function downloadTraceExport(format) {
  if (!state.traceQuery) return;
  const response = await fetch("/api/review/traces/export", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ ids: state.traceQuery, format }),
  });
  if (!response.ok) {
    let message = `导出失败（${response.status}）`;
    try {
      const payload = await response.json();
      if (payload && payload.error) message = payload.error;
    } catch {
      // A non-JSON error body keeps the status message.
    }
    throw new Error(message);
  }
  const blob = await response.blob();
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = `osp-review-traces.${format === "yaml" ? "yaml" : "json"}`;
  document.body.append(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}

function backToTraceList() {
  state.traceScope = null;
  state.detail = null;
  state.selected = null;
  if (state.trace) renderTraceMain();
}

//: The trace view is read-only for decisions: the stored decision is one per case × protocol.
function renderTraceDecisionCard() {
  const card = el("section", "card");
  card.append(el("h2", "", "审核结论"));
  const decision = (state.detail && state.detail.decision) || {};
  card.append(
    el(
      "p",
      "sub",
      `当前：${decisionLabel(decision.status)}${decision.stale ? "（需复核）" : ""}`,
    ),
  );
  if (decision.category) {
    card.append(el("p", "sub", `缺陷归属：${categoryLabel(decision.category)}`));
  }
  if (decision.note) card.append(el("pre", "request-text", decision.note));
  if (decision.reviewer || decision.confirmedAt) {
    card.append(
      el(
        "p",
        "sub",
        `上次保存：${decision.reviewer || "—"} · ${decision.confirmedAt || "—"}`,
      ),
    );
  }
  const evidence = decision.evidence || {};
  const runIds = Array.isArray(evidence.runIds) ? evidence.runIds : [];
  if (runIds.length) {
    card.append(el("p", "sub", `依据 run：${runIds.join(", ")}`));
  }
  card.append(
    el(
      "p",
      "hint",
      "轨迹视图只读：结论按用例×协议保存，避免把一次运行当成整个用例的结论。",
    ),
  );
  const button = el("button", "secondary", "到用例视图给结论");
  button.type = "button";
  button.addEventListener("click", () => {
    openCaseFromTrace().catch((caught) => showNotice(caught.message, "error"));
  });
  card.append(button);
  return card;
}

async function openCaseFromTrace() {
  const scope = state.traceScope;
  if (!scope) return;
  state.detailScope = scope.runId ? { runId: scope.runId } : null;
  // ``setView`` clears the trace scope and reloads every round of the selected case.
  await setView("case");
}

//: A trace id / run id can be handed over as ``/review?trace=<id>``; repeat the parameter for a list.
function traceLinkInput() {
  if (typeof window === "undefined" || !window.location) return "";
  const params = new URLSearchParams(window.location.search);
  return [...params.getAll("trace"), ...params.getAll("run")]
    .filter(Boolean)
    .join("\n");
}

//: Tint the detail area so A2A and A2A-T are distinguishable at a glance. Only the container that
//: currently renders the detail is tinted; the batch table under a drawer keeps its neutral tint.
function applyProtocolTint() {
  const protocol = (state.detail && state.detail.protocol) || "";
  const pane = document.getElementById("detail");
  const drawer = document.getElementById("drawer");
  const target = drawerActive() ? drawer : pane;
  for (const container of [pane, drawer]) {
    if (!container) continue;
    const active = container === target;
    container.classList.toggle("proto-a2a-t", active && protocol === "a2a-t");
    container.classList.toggle("proto-a2a", active && protocol === "a2a");
  }
}

// ---------------------------------------------------------------------------
// Detail drawer
// ---------------------------------------------------------------------------

//: A detail opened from one batch round overlays the table instead of replacing it, so closing
//: returns to the same filters, scroll position and row.
function drawerActive() {
  return state.view === "round" && Boolean(state.detailScope && state.detailScope.batchId);
}

function detailContainer() {
  const body = document.getElementById("drawer-body");
  return drawerActive() && body ? body : document.getElementById("detail");
}

function openDrawer() {
  const drawer = document.getElementById("drawer");
  const backdrop = document.getElementById("drawer-backdrop");
  if (drawer) drawer.hidden = false;
  if (backdrop) backdrop.hidden = false;
}

function closeDrawer() {
  const drawer = document.getElementById("drawer");
  const backdrop = document.getElementById("drawer-backdrop");
  if (drawer) drawer.hidden = true;
  if (backdrop) backdrop.hidden = true;
}

function setDrawerTitle() {
  const title = document.getElementById("drawer-title");
  if (!title) return;
  const caseRow = (state.detail && state.detail.case) || {};
  const selected = state.selected || {};
  const batch = state.selectedBatch || {};
  const caseName = caseRow.name || selected.caseName || "";
  const caseId = caseRow.id || selected.caseId || "";
  const protocol = state.detail ? protocolLabel(state.detail.protocol) : "";
  const scope = [batch.batchId, batch.round ? `第 ${batch.round} 轮` : ""].filter(Boolean).join(" · ");
  title.textContent = [`#${caseId} ${caseName}`.trim(), protocol, scope]
    .filter(Boolean)
    .join(" · ");
}

//: Refresh the batch rows in place, keeping the filter controls and the reviewer's scroll.
function refreshBatchInPlace() {
  if (state.view !== "round" || !state.batch || !state.batchTable) return;
  const container = document.getElementById("detail");
  const scrollTop = container ? container.scrollTop : 0;
  refreshBatchTable();
  if (container) container.scrollTop = scrollTop;
}

function closeDrawerDetail() {
  if (!confirmDiscardDecision()) return;
  state.detail = null;
  state.selected = null;
  state.detailScope = null;
  state.traceScope = null;
  closeDrawer();
  refreshBatchInPlace();
}

function renderEmptyDetail(message, hint) {
  const container = document.getElementById("detail");
  applyProtocolTint();
  container.replaceChildren();
  const card = el("section", "card empty");
  card.append(el("p", "", message));
  if (hint) card.append(el("p", "muted", hint));
  container.append(card);
}

async function setView(view) {
  closeDrawer();
  const wasTrace = state.view === "trace";
  state.view = view === "case" ? "case" : view === "trace" ? "trace" : "round";
  if (wasTrace && state.view !== "trace") state.traceScope = null;
  for (const [id, active] of [
    ["view-round", state.view === "round"],
    ["view-case", state.view === "case"],
    ["view-trace", state.view === "trace"],
  ]) {
    const tab = document.getElementById(id);
    tab.classList.toggle("active", active);
    tab.setAttribute("aria-selected", String(active));
  }
  const layout = document.getElementById("layout");
  if (layout) {
    layout.classList.toggle("view-round", state.view === "round");
    layout.classList.toggle("view-trace", state.view === "trace");
  }
  document.getElementById("sidebar-round").hidden = state.view !== "round";
  document.getElementById("sidebar-case").hidden = state.view !== "case";
  document.getElementById("sidebar-trace").hidden = state.view !== "trace";
  if (state.view === "trace") {
    renderTraceSidebar();
    if (state.trace) renderTraceMain();
    else {
      renderEmptyDetail(
        "粘贴一个或一批 traceId / runId，按运行身份复核。",
        "支持整段日志和 Langfuse URL；命中 tester 批次的 run 会写入审核库。",
      );
    }
    return;
  }
  if (state.view === "round") {
    renderBatches();
    if (state.batch) renderBatch();
    else if (state.batches.length) await autoSelectBatch();
    else {
      renderEmptyDetail(
        "还没有证据。",
        "先点右上角「采集证据」，或到评测控制台跑一轮用例。",
      );
    }
  } else {
    if (!state.cases.length) await loadCases();
    else renderCases();
    if (state.detail && state.selected) {
      // Leaving a trace-scoped detail must reload every round of the case, not just the one run.
      if (wasTrace) await loadDetail();
      else renderDetail();
    } else {
      renderEmptyDetail(
        "从左侧选择用例与协议，右侧会给出跨轮次的自动判定与审核结论。",
        "判定使用当前用例语料重算已存证据；证据是否仍然有效、是否出现新轮次会单独标出。",
      );
    }
  }
}

// ---------------------------------------------------------------------------
// Case detail
// ---------------------------------------------------------------------------

async function selectProtocol(caseRow, protocol, scope = null) {
  if (!confirmDiscardDecision()) return;
  state.selected = { caseId: caseRow.caseId, protocol, caseName: caseRow.caseName };
  state.detailScope = scope;
  state.detail = null;
  state.snippet = null;
  await loadDetail();
}

async function loadDetail() {
  if (!state.selected) return;
  const container = detailContainer();
  container.replaceChildren(el("section", "card", "加载中…"));
  if (drawerActive()) {
    setDrawerTitle();
    openDrawer();
  }
  const { caseId, protocol } = state.selected;
  state.detail = await getJson(detailPath(caseId, protocol));
  let index = defaultRoundIndex(state.detail.rounds || []);
  const scope = state.detailScope;
  if (scope && scope.runId) {
    const match = (state.detail.rounds || []).findIndex(
      (row) => row.runId === scope.runId,
    );
    if (match >= 0) index = match;
  } else if (scope) {
    const match = (state.detail.rounds || []).findIndex(
      (row) => row.batchId === scope.batchId && Number(row.sourceRound || 1) === Number(scope.round || 1),
    );
    if (match >= 0) index = match;
  }
  state.round = index;
  resetDecision();
  renderDetail();
}

async function fetchTrace(button, round, options = {}) {
  if (!state.selected) return;
  const { caseId, protocol } = state.selected;
  const original = button ? button.textContent : "";
  if (button) {
    button.disabled = true;
    button.textContent = "抓取中…";
  }
  try {
    await postJson(detailPath(caseId, protocol, "/trace"), {
      traceId: round && round.traceId ? round.traceId : undefined,
      runId: round && round.runId ? round.runId : undefined,
      batchId: round && round.batchId ? round.batchId : undefined,
      round: round ? Number(round.sourceRound || 1) : undefined,
      force: options.force === true,
    });
    await loadDetail();
  } catch (caught) {
    showNotice(caught.message, "error");
    if (button) {
      button.disabled = false;
      button.textContent = original;
    }
  }
}

function resetDecision() {
  const decision = (state.detail && state.detail.decision) || {};
  state.decision = {
    status: decision.status || "pending",
    category: decision.category || "",
    note: decision.note || "",
    dirty: false,
  };
}

function confirmDiscardDecision() {
  if (!state.decision.dirty) return true;
  return window.confirm("当前审核结论还没有保存，切换后会丢失。继续？");
}

function markDecisionDirty() {
  state.decision.dirty = true;
  updateDecisionState();
}

function updateDecisionState() {
  const hint = document.getElementById("decision-state");
  if (hint) hint.textContent = state.decision.dirty ? "有未保存的修改" : "与已保存一致";
  const save = document.getElementById("decision-save");
  if (save) save.disabled = !state.decision.dirty;
  for (const button of document.querySelectorAll("[data-decision-status]")) {
    button.classList.toggle("chosen", button.dataset.decisionStatus === state.decision.status);
  }
  const category = document.getElementById("decision-category");
  if (category) category.disabled = state.decision.status !== "defect";
}

function renderDetail() {
  if (!state.selected || !state.detail) return;
  const container = detailContainer();
  const scrollTop = container.scrollTop;
  applyProtocolTint();
  container.replaceChildren();
  const scopedToBatch = state.view === "round" && Boolean(state.batch);
  const scopedToTrace = Boolean(state.traceScope);
  if (drawerActive()) {
    setDrawerTitle();
    openDrawer();
  }
  const allRoundsButton = document.getElementById("drawer-all-rounds");
  if (allRoundsButton) allRoundsButton.hidden = !drawerActive();
  if (scopedToTrace) {
    const bar = el("div", "backbar");
    const back = el("button", "ghost", "← 返回轨迹清单");
    back.type = "button";
    back.addEventListener("click", backToTraceList);
    bar.append(back);
    const allRounds = el("button", "ghost", "查看该用例全部轮次");
    allRounds.type = "button";
    allRounds.addEventListener("click", () => {
      openCaseFromTrace().catch((caught) => showNotice(caught.message, "error"));
    });
    bar.append(allRounds);
    container.append(bar);
  }
  container.append(renderCaseCard());
  if (!scopedToBatch && !scopedToTrace) container.append(renderRoundBar());
  if (state.round === -1) container.append(renderRoundMatrix());
  else {
    const round = (state.detail.rounds || [])[state.round];
    if (round) {
      container.append(renderDimensionStrip(round));
      container.append(renderDataCard(round));
      container.append(renderIntentCard(round));
      // Pure A2A has no SDK Task-T and no interception, and both dimensions are judged
      // not_applicable there; showing the empty cards only adds noise.
      if (state.detail.protocol === "a2a-t") {
        container.append(renderTaskTCard(round));
        container.append(renderValidateCard(round));
      }
      const tracePoints = renderTracePointsCard(round);
      if (tracePoints) container.append(tracePoints);
      container.append(renderNegotiationCard(round));
      container.append(renderRuntimeCard(round));
    }
  }
  container.append(scopedToTrace ? renderTraceDecisionCard() : renderDecisionCard());
  container.append(renderEvidenceCard());
  container.scrollTop = scrollTop;
  updateDecisionState();
}

function renderCaseCard() {
  const card = el("section", "card");
  const caseRow = state.detail.case || {};
  const board = el("div", "section-title");
  board.append(
    el("h2", "", `#${caseRow.id} ${caseRow.name || ""} · ${protocolLabel(state.detail.protocol)}`),
  );
  const badge = el("span", `badge ${protocolStatus()}`, statusLabel(protocolStatus()));
  board.append(badge);
  card.append(board);
  card.append(renderCaseRequests(caseRow));
  const facts = el("dl", "kv");
  const rows = [
    ["操作", caseRow.operation],
    ["维度", caseRow.dimension],
    ["拦截预期", caseRow.validate],
    ["A2A-T 协商轮次", caseRow.a2aTRounds],
    ["A2A 协商轮次", caseRow.a2aRounds],
    ["预期终态", caseRow.finalState],
    ["已标注期望", [caseRow.hasSlotErrors ? "slot_errors" : "", caseRow.hasMessages ? "messages" : ""].filter(Boolean).join(", ") || "—"],
    ["语料版本", state.detail.corpusVersion],
  ];
  for (const [label, value] of rows) {
    facts.append(el("dt", "", label));
    facts.append(el("dd", "", textBlock(value)));
  }
  card.append(facts);
  card.append(el("p", "sub", `用例指纹 ${caseRow.caseSignature || "—"}；判定使用当前语料重算已存证据。`));
  return card;
}

//: The two requests the corpus configures, always visible above the round cards.
function renderCaseRequests(caseRow) {
  const block = el("div", "request-pair");
  block.append(
    requestField("操作员原始输入（用例语料）", caseRow.userInput),
  );
  if (caseRow.followupInput) {
    block.append(
      requestField(
        "拦截后重新下发（blocked_followup.input）",
        caseRow.followupInput,
        "该用例先期望被拦截，拦截后再下发这段请求参与验收。",
      ),
    );
  } else if (caseRow.blocked) {
    block.append(
      el("p", "hint", "该用例期望被拦截，但没有配置 blocked_followup，因此没有重新下发的请求。"),
    );
  }
  return block;
}

function requestField(label, value, hint) {
  const item = el("div", "request-item");
  const head = el("div", "request-head");
  head.append(el("span", "request-label", label));
  const text = value === null || value === undefined || value === "" ? "—" : textBlock(value);
  head.append(copyButton(text));
  item.append(head);
  if (hint) item.append(el("p", "request-hint", hint));
  item.append(el("pre", "request-text", text));
  return item;
}

function protocolStatus() {
  const rounds = state.detail && state.detail.rounds;
  if (!rounds || !rounds.length) return "not_run";
  const round = rounds[state.round >= 0 ? state.round : 0];
  return round && round.judgement ? round.judgement.status : "not_run";
}

function renderRoundBar() {
  const card = el("section", "card roundbar");
  const select = document.createElement("select");
  const all = document.createElement("option");
  all.value = "-1";
  all.textContent = `全部轮次（${(state.detail.rounds || []).length}）`;
  select.append(all);
  (state.detail.rounds || []).forEach((round, index) => {
    const option = document.createElement("option");
    option.value = String(index);
    option.textContent = roundOptionLabel(round);
    option.title = `runId ${round.runId || "—"} · traceId ${round.traceId || "—"}`;
    select.append(option);
  });
  select.value = String(state.round);
  select.addEventListener("change", () => {
    state.round = Number(select.value);
    renderDetail();
  });
  const label = el("label", "", "运行轮次");
  label.append(select);
  card.append(label);
  if (!(state.detail.rounds || []).length) {
    card.append(el("span", "summary", "该窗口内没有这个用例、这个协议的证据；先采集证据或去评测控制台重跑。"));
  }
  return card;
}

function renderDimensionStrip(round) {
  const card = el("section", "card");
  const head = el("div", "section-title");
  head.append(el("h2", "", "判定汇总"));
  head.append(el("span", "summary", roundLabel(round)));
  card.append(head);
  // The verdict belongs to one run; show its ids verbatim so a reviewer can look it up by hand.
  const identity = el("dl", "kv");
  for (const [label, value] of [
    ["runId", round.runId],
    ["traceId", round.traceId],
  ]) {
    if (!value) continue;
    identity.append(el("dt", "", label));
    const cell = el("dd", "mono-cell");
    cell.append(document.createTextNode(String(value)));
    cell.append(document.createTextNode(" "));
    cell.append(copyButton(String(value)));
    identity.append(cell);
  }
  if (identity.childNodes.length) card.append(identity);
  const strip = el("div", "dimension-status");
  for (const dimension of (round.judgement && round.judgement.dimensions) || []) {
    const badge = el(
      "span",
      `badge ${dimension.status}`,
      `${dimensionLabel(dimension.dimension)} · ${statusLabel(dimension.status)}`,
    );
    if (dimension.reason) badge.title = String(dimension.reason);
    strip.append(badge);
  }
  card.append(strip);
  renderTraceBlock(card, round);
  const requests = round.requests || {};
  const rows = [
    [
      requests.originalSource === "corpus"
        ? "本次运行原文（当前语料，未采到运行时原文）"
        : "本次运行原文",
      requests.original,
    ],
    ["被拦截的请求", requests.intercepted],
    ["实际下发", requests.dispatched],
  ].filter(([, value]) => value);
  if (rows.length) {
    card.append(el("h3", "sub-title", "本次运行的请求（冻结证据）"));
    const facts = el("dl", "kv");
    for (const [label, value] of rows) {
      facts.append(el("dt", "", label));
      facts.append(el("dd", "", textBlock(value)));
    }
    card.append(facts);
  }
  return card;
}

export function traceButtonLabel(trace) {
  if (!trace || trace.canFetch !== true) return "";
  if (String(trace.exportState || "") === "failed") return "重试抓取";
  if (!trace.present) return "抓取轨迹";
  return "重新抓取";
}

function renderTraceBlock(card, round) {
  const trace = (round.judgement && round.judgement.detail && round.judgement.detail.trace) || {};
  const applicability = trace.applicability || {};
  const nms = (trace.frames && trace.frames.nms) || {};
  const attribution = trace.attribution || {};
  const findings = attribution.findings || [];
  const facts = [
    applicability.applicable === "yes"
      ? `轨迹 ${NMS_KIND_LABELS[trace.nmsKind] || "下游未声明"}`
      : `轨迹 ${applicability.text || "不参与验收"}`,
    trace.present ? `traceId ${trace.traceId || round.traceId || "—"}` : "",
    trace.fetchedAt ? `快照 ${trace.fetchedAt}` : "",
    trace.spanLevel ? `层级 ${trace.spanLevel}` : "",
    trace.captureContent === false ? "内容未捕获" : "",
    trace.present ? `NMS 帧 ${nms.observed ?? "—"}/${nms.expected ?? "—"}` : "",
    attribution.status
      ? `归因 ${attribution.status}${findings.length ? `（${findings.length} 点）` : ""}`
      : "",
  ].filter(Boolean);
  if (facts.length) card.append(el("p", "sub", facts.join(" · ")));
  const blockers = trace.blockers || [];
  if (blockers.length) {
    const list = el("ul", "trace-blockers");
    for (const blocker of blockers) {
      list.append(el("li", "", blocker.text || blocker.code || ""));
    }
    card.append(list);
  }
  // The button follows the judged state, not one failure code: an applicable round can be
  // fetched even when the dimension is not_applicable yet (e.g. no snapshot at all).
  const label = traceButtonLabel(trace);
  if (label) {
    const button = el("button", "ghost small", label);
    button.type = "button";
    button.addEventListener("click", () =>
      fetchTrace(button, round, { force: Boolean(trace.present) }),
    );
    card.append(button);
    if (state.meta && state.meta.traceConfigured === false) {
      card.append(
        el(
          "p",
          "hint",
          "未配置 Langfuse 凭据（LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY），抓取会失败。",
        ),
      );
    }
  } else if (applicability.applicable === "yes" && trace.fetchEnabled === false) {
    // Say exactly what is missing; "只读模式" alone left the reviewer with no next step.
    card.append(
      el(
        "p",
        "hint",
        "轨迹抓取未启用：启动 review 时加 --fetch-traces，并配置 LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY。",
      ),
    );
  }
  const observations = trace.observations || [];
  if (observations.length) {
    card.append(
      traceObservationTable(observations, Boolean(trace.observationsTruncated)),
    );
  }
  if (trace.url) {
    const link = el("a", "sub", "在 Langfuse 打开");
    link.href = trace.url;
    link.target = "_blank";
    link.rel = "noopener noreferrer";
    card.append(link);
  }
}

function renderIntentCard(round) {
  const card = el("section", "card");
  const detail = (round.judgement && round.judgement.detail) || {};
  const intent = detail.intent || {};
  const head = el("div", "section-title");
  head.append(el("h2", "", "最终意图（硬门槛）"));
  const label = el("label", "check");
  const toggle = document.createElement("input");
  toggle.type = "checkbox";
  toggle.checked = state.intentDiffOnly;
  toggle.addEventListener("change", () => {
    state.intentDiffOnly = toggle.checked;
    renderDetail();
  });
  label.append(toggle, document.createTextNode(" 只看差异"));
  head.append(label);
  card.append(head);
  card.append(
    el(
      "p",
      "sub",
      "用例预期 → 服务端最终（下游 SUBMITTED 结构化读数）是最重要的判定；SDK 抽取列用于区分“SDK 抽错”与“Task-T 传递损耗”。",
    ),
  );
  if (!intent.rows || !intent.rows.length) {
    card.append(el("p", "empty-state", "没有可比较的服务端意图（未采集、模拟器或该运行不可判定）。"));
    return card;
  }
  const sdkByPath = new Map((intent.sdkRows || []).map((row) => [row.path, row]));
  const rows = intent.rows.filter(
    (row) => !state.intentDiffOnly || row.verdict !== "same",
  );
  const table = document.createElement("table");
  table.className = "grid";
  const headRow = el("tr");
  for (const title of ["字段", "用例预期", "服务端最终", "SDK 抽取", "结论"]) {
    headRow.append(el("th", "", title));
  }
  table.append(headRow);
  for (const row of rows) {
    const tr = el("tr", row.verdict === "same" ? "" : "fail");
    tr.append(
      el("td", "mono-cell", row.path),
      textCell(row.expected),
      textCell(row.actual),
      textCell(sdkByPath.get(row.path)?.actual),
      el("td", `verdict ${row.verdict}`, row.verdict === "same" ? "一致" : row.reason || row.verdict),
    );
    table.append(tr);
  }
  card.append(table);
  const diffCount = intent.rows.filter((row) => row.verdict !== "same").length;
  card.append(el("p", "sub", `共 ${intent.rows.length} 个字段，${diffCount} 个与预期不一致。`));
  const corroboration = intent.corroboration || {};
  if (corroboration.comparable) {
    if (corroboration.equal) {
      card.append(el("p", "sub", "Langfuse task_created 的结构化读数与 oracle 一致。"));
    } else {
      const drift = (corroboration.rows || []).filter(
        (row) => ["changed", "missing", "added"].includes(row.status),
      );
      card.append(
        el(
          "p",
          "hint",
          `Langfuse task_created 读数与 oracle 有 ${drift.length} 处漂移`
            + `（${drift.map((row) => row.path).join("、")}）；oracle 仍以服务端 SUBMITTED payload 为准，不替换。`,
        ),
      );
    }
  }
  return card;
}

function renderTaskTCard(round) {
  const card = el("section", "card");
  const detail = (round.judgement && round.judgement.detail) || {};
  const taskT = detail.taskT || {};
  const head = el("div", "section-title");
  head.append(el("h2", "", "SDK Task-T"));
  const label = el("label", "check");
  const toggle = document.createElement("input");
  toggle.type = "checkbox";
  toggle.checked = state.taskTDiffOnly;
  toggle.addEventListener("change", () => {
    state.taskTDiffOnly = toggle.checked;
    renderDetail();
  });
  label.append(toggle, document.createTextNode(" 只看差异"));
  head.append(label);
  card.append(head);
  card.append(el("p", "sub", "Task-T 直接决定下游如何理解请求；分节判定，固定模板文字只记录不判。"));
  if (!taskT.rows || !taskT.rows.length) {
    card.append(el("p", "empty-state", "没有 Task-T 证据（拦截前即失败或该协议不适用）。"));
    return card;
  }
  const fidelity = taskT.fidelity || {};
  if (!fidelity.comparable) {
    card.append(
      el("p", "sub", "未导出 Task-T 原文，无法核对“判定所用”与 Langfuse 实际下发是否一致。"),
    );
  } else if (fidelity.equal) {
    card.append(el("p", "sub", "判定所用 Task-T 与 Langfuse 实际下发一致。"));
  } else {
    const changed = (fidelity.rows || []).filter(
      (row) => ["changed", "missing"].includes(row.verdict),
    );
    card.append(
      el(
        "p",
        "hint",
        `判定所用 Task-T 与 Langfuse 实际下发不一致：${changed.length} 处`
          + `（${changed.map((row) => row.path.replace("task_t.", "")).join("、")}）`
          + (fidelity.truncated ? "；导出的原文已截断，只比较前 16 KiB。" : "。"),
      ),
    );
  }
  const exportedByPath = new Map(
    (fidelity.rows || []).map((row) => [row.path, row.dispatched]),
  );
  const rows = filterTaskTRows(taskT.rows, state.taskTDiffOnly);
  if (!rows.length) {
    card.append(
      el(
        "p",
        "empty-state",
        `没有差异（共 ${taskT.rows.length} 个小节；固定模板文字只记录不判）。`,
      ),
    );
  } else {
    const table = document.createElement("table");
    table.className = "grid";
    const headRow = el("tr");
    for (const title of ["小节", "预期", "判定使用", "实际下发", "结论"]) {
      headRow.append(el("th", "", title));
    }
    table.append(headRow);
    for (const row of rows) {
      const exported = fidelity.comparable
        ? exportedByPath.get(row.path)
        : undefined;
      const tr = el("tr", row.verdict === "same" ? "" : "fail");
      tr.append(
        el("td", "mono-cell", row.path.replace("task_t.", "")),
        textCell(row.expected),
        textCell(row.actual),
        textCell(exported === undefined ? "—" : exported),
        el("td", `verdict ${row.verdict}`, row.verdict === "same" ? "一致" : row.reason || row.verdict),
      );
      table.append(tr);
    }
    card.append(table);
    if (state.taskTDiffOnly && rows.length < taskT.rows.length) {
      card.append(
        el("p", "sub", `只显示 ${rows.length} 个差异小节；共 ${taskT.rows.length} 个小节。`),
      );
    }
  }
  const raw = document.createElement("details");
  raw.append(el("summary", "", "判定使用的 Task-T 原文"));
  raw.append(el("pre", "mono", taskT.judged || taskT.actual || ""));
  card.append(raw);
  if (taskT.dispatched) {
    const exported = document.createElement("details");
    exported.append(
      el(
        "summary",
        "",
        `Langfuse 实际下发的 Task-T${taskT.dispatchedTruncated ? "（已截断）" : ""}`,
      ),
    );
    exported.append(el("pre", "mono", taskT.dispatched));
    card.append(exported);
  }
  return card;
}

function renderDataCard(round) {
  const detail = (round.judgement && round.judgement.detail) || {};
  const trace = detail.trace || {};
  const rows = Array.isArray(trace.data) ? trace.data : [];
  if (!rows.length) return null;
  const card = el("section", "card");
  const applicable = rows.filter((row) => row.applicable !== false);
  const present = applicable.filter((row) => row.present);
  card.append(el("h2", "", "关键数据"));
  card.append(
    el(
      "p",
      "sub",
      `导出的 Langfuse 轨迹中，${present.length}/${applicable.length} 项关键数据有副本。只判有无，不比对副本内容。`,
    ),
  );
  const table = document.createElement("table");
  table.className = "grid";
  const headRow = el("tr");
  for (const title of ["数据项", "状态", "Langfuse 副本", "原文"]) {
    headRow.append(el("th", "", title));
  }
  table.append(headRow);
  for (const row of rows) {
    const applies = row.applicable !== false;
    const missing = applies && !row.present;
    const tr = el("tr", missing && row.required ? "fail" : missing ? "partial" : "");
    tr.append(el("td", "", row.label || row.id));
    const status = !applies ? "不适用" : row.present ? "有" : row.required ? "缺（必选）" : "缺（可选）";
    tr.append(el("td", `verdict ${!applies ? "na" : row.present ? "pass" : "missing"}`, status));
    const copies = Array.isArray(row.copies) ? row.copies : [];
    const copyCell = el("td", "");
    copyCell.append(
      el(
        "span",
        "sub",
        copies.length
          ? copies.map((copy) => `${dataCopyLabel(copy)}${copy.round ? ` · R${copy.round}` : ""}`).join("\n")
          : "—",
      ),
    );
    tr.append(copyCell);
    const valueCell = el("td", "");
    const values = copies.filter((copy) => copy.value);
    if (values.length) {
      const box = document.createElement("details");
      box.append(el("summary", "", `${values.length} 份原文`));
      for (const copy of values) {
        box.append(el("p", "sub", dataCopyLabel(copy)));
        box.append(el("pre", "mono", copy.value));
      }
      valueCell.append(box);
    } else if (copies.some((copy) => copy.excerpt)) {
      valueCell.append(el("span", "sub", copies.map((copy) => copy.excerpt || "").filter(Boolean).join("\n")));
    } else {
      valueCell.append(el("span", "sub", "—"));
    }
    tr.append(valueCell);
    table.append(tr);
  }
  card.append(table);
  return card;
}

function renderTracePointsCard(round) {
  const detail = (round.judgement && round.judgement.detail) || {};
  const trace = detail.trace || {};
  const points = trace.points || [];
  if (!points.length) return null;
  // Reference only: the point table explains an ingest gap, it no longer gates acceptance. The
  // key-data card above is the judged view; this one stays folded so it does not compete with it.
  const card = el("section", "card");
  const passed = points.filter((point) => point.ok).length;
  const facts = [`期望 vs Langfuse，${passed}/${points.length} 满足`];
  if (trace.traceId) facts.push(`traceId ${trace.traceId}`);
  if (trace.nmsKind) facts.push(`下游 ${trace.nmsKind}`);
  card.append(el("h2", "", "轨迹过程点（参考）"));
  card.append(el("p", "sub", facts.join(" · ")));
  const box = document.createElement("details");
  box.append(el("summary", "", "展开参考表（不影响验收结论）"));
  const table = document.createElement("table");
  table.className = "grid";
  const headRow = el("tr");
  for (const title of ["过程点", "期望", "实际", "结论"]) {
    headRow.append(el("th", "", title));
  }
  table.append(headRow);
  for (const point of points) {
    const tr = el("tr", point.ok ? "" : "partial");
    // The original Langfuse observation name rides next to the process point so the table can be
    // matched against the observation list below without knowing the point-to-span mapping.
    const spans = (point.spans || []).filter(Boolean);
    const labelCell = el("td", "");
    labelCell.append(point.label);
    labelCell.append(el("span", "sub", ` (${spans.length ? spans.join(" / ") : point.point})`));
    tr.append(
      labelCell,
      el("td", "", point.expected),
      el("td", "", point.observed),
      el("td", `verdict ${point.ok ? "same" : "partial"}`, point.ok ? "满足" : "缺失（参考）"),
    );
    table.append(tr);
  }
  box.append(table);
  card.append(box);
  return card;
}

function renderValidateCard(round) {
  const card = el("section", "card");
  const detail = (round.judgement && round.judgement.detail) || {};
  const validate = detail.validate || {};
  card.append(el("h2", "", "拦截与 slot_errors"));
  card.append(
    el("p", "sub", `预期 ${textBlock(validate.expected)} · 实测 ${textBlock(validate.actual)}`),
  );
  const errors = validate.errors || [];
  if (!errors.length) {
    card.append(el("p", "empty-state", "该轮没有观测到 slot 错误。"));
    return card;
  }
  const match = (validate.detail || {}).slotErrorMatch || {};
  const unmatched = new Set(match.unmatchedObserved || []);
  const table = document.createElement("table");
  table.className = "grid";
  const headRow = el("tr");
  for (const title of ["结论", "错误码", "槽位", "facts", "说明"]) {
    headRow.append(el("th", "", title));
  }
  table.append(headRow);
  errors.forEach((error, index) => {
    const tr = el("tr", unmatched.has(index) ? "fail" : "");
    tr.append(
      el("td", `verdict ${unmatched.has(index) ? "fail" : "pass"}`, unmatched.has(index) ? "未命中" : "命中"),
      el("td", "mono-cell", error.code || ""),
      el("td", "mono-cell", error.slotName || ""),
      el("td", "mono-cell", JSON.stringify(error.facts || {})),
      el("td", "", error.message || ""),
    );
    table.append(tr);
  });
  card.append(table);
  if (match.reason) card.append(el("p", "sub", match.reason));
  return card;
}

function renderNegotiationCard(round) {
  const card = el("section", "card");
  const detail = (round.judgement && round.judgement.detail) || {};
  const negotiation = detail.negotiation || {};
  card.append(el("h2", "", "协商轮次与报文"));
  if (!negotiation.expectedRounds && !(negotiation.expectedMessages || []).length) {
    card.append(el("p", "empty-state", "该用例这个协议不预期协商。"));
    return card;
  }
  card.append(
    el(
      "p",
      "sub",
      `预期 ${negotiation.expectedRounds || 0} 轮；实测 ${textBlock((negotiation.actual || {}).rounds)} 轮；`
        + `类型 ${textBlock((negotiation.actual || {}).kinds)}`,
    ),
  );
  const messages = negotiation.messages || [];
  if (!messages.length) {
    card.append(el("p", "empty-state", "没有保存协商报文证据（旧批次或缺少 run 存储），该轮会被标为部分可判。"));
    return card;
  }
  const alternatives = negotiation.expectedMessages || [];
  const match = negotiation.messageMatch;
  messages.forEach((message, index) => {
    const block = el("div", "candidate");
    const head = el(
      "div",
      "candidate-head",
      `第 ${message.round} 轮 · ${message.kind || "unknown"}`,
    );
    if (match) {
      const judgement = messageJudgement(match, index);
      const kind = judgement.ok === true ? "pass" : judgement.ok === false ? "fail" : "recorded";
      head.append(el("span", `badge ${kind}`, judgement.label));
    }
    block.append(head);
    block.append(el("pre", "mono", message.content || ""));
    const expected = alternatives[(message.round || 1) - 1];
    if (expected && expected.length) {
      const alternativesBox = document.createElement("details");
      alternativesBox.append(el("summary", "", `该轮 ${expected.length} 个可接受备选`));
      for (const text of expected) alternativesBox.append(el("pre", "mono", text));
      block.append(alternativesBox);
    }
    card.append(block);
  });
  if (match) {
    card.append(
      el(
        "p",
        `verdict ${match.matched ? "pass" : "fail"}`,
        match.matched ? "报文匹配通过" : "报文与所有备选都不匹配",
      ),
    );
    const missing = missingRoundNotes(match);
    if (missing.length) card.append(el("p", "hint", missing.join("；")));
  }
  return card;
}

function renderRuntimeCard(round) {
  const card = el("section", "card");
  const detail = (round.judgement && round.judgement.detail) || {};
  const runtime = detail.runtime || {};
  card.append(el("h2", "", "运行与终态"));
  const facts = el("dl", "kv");
  for (const [label, value] of [
    ["发送", runtime.send],
    ["受理", runtime.accepted],
    ["运行", runtime.working],
    ["终态", runtime.final],
  ]) {
    facts.append(el("dt", "", label));
    facts.append(el("dd", "", textBlock(value)));
  }
  card.append(facts);
  return card;
}

function renderRoundMatrix() {
  const card = el("section", "card");
  card.append(el("h2", "", "全部轮次"));
  const stability = stabilityOf(state.detail.rounds || []);
  const line = el("div", "stability");
  for (const row of stability) {
    line.append(
      el("span", "summary", `${dimensionLabel(row.dimension)} ${row.pass}/${row.total} 通过`),
    );
  }
  card.append(line);
  const table = document.createElement("table");
  table.className = "grid";
  const headRow = el("tr");
  for (const title of ["轮次", "整体", ...Object.values(DIMENSION_LABELS)]) {
    headRow.append(el("th", "", title));
  }
  table.append(headRow);
  for (const round of state.detail.rounds || []) {
    const tr = el("tr", round.judgement && round.judgement.status === "fail" ? "fail" : "");
    tr.append(el("td", "mono-cell", roundLabel(round)));
    tr.append(el("td", "", statusLabel(round.judgement && round.judgement.status)));
    for (const dimension of Object.keys(DIMENSION_LABELS)) {
      const row = ((round.judgement && round.judgement.dimensions) || []).find(
        (item) => item.dimension === dimension,
      );
      tr.append(el("td", `verdict ${row ? row.status : "not_applicable"}`, row ? statusLabel(row.status) : "—"));
    }
    table.append(tr);
  }
  card.append(table);
  return card;
}

function renderDecisionCard() {
  const card = el("section", "card");
  const head = el("div", "section-title");
  head.append(el("h2", "", "审核结论"));
  const stateText = el("span", "summary", "");
  stateText.id = "decision-state";
  head.append(stateText);
  card.append(head);
  const decision = (state.detail && state.detail.decision) || {};
  if (decision.staleReason) card.append(el("p", "hint", decision.staleReason));
  if (decision.verdictChanged) {
    card.append(
      el(
        "p",
        "hint",
        verdictChangeText(decision.verdictChange) || "本轮自动判定与保存结论时不同，需要复核。",
      ),
    );
  } else if (decision.newEvidence) {
    // A newer round with the same verdict keeps the conclusion; say so instead of asking again.
    card.append(el("p", "sub", "结论之后有新的轮次，自动判定与保存时一致，沿用结论。"));
  }
  const auto = protocolStatus();
  if (auto !== "pass") {
    card.append(el("p", "hint", `自动判定为「${statusLabel(auto)}」；确认通过时必须填写理由。`));
  }
  const buttons = el("div", "decision-buttons");
  for (const status of ["pending", "accepted", "defect", "known_issue", "recheck"]) {
    const button = el("button", "secondary", decisionLabel(status));
    button.type = "button";
    button.dataset.decisionStatus = status;
    button.addEventListener("click", () => {
      state.decision.status = status;
      if (status !== "defect") state.decision.category = "";
      markDecisionDirty();
    });
    buttons.append(button);
  }
  card.append(buttons);
  const category = document.createElement("select");
  category.id = "decision-category";
  category.className = "decision-category";
  for (const value of ["", "sdk", "vendor", "case", "env", "other"]) {
    const option = document.createElement("option");
    option.value = value;
    option.textContent = value ? categoryLabel(value) : "缺陷归属（仅缺陷）";
    category.append(option);
  }
  category.value = state.decision.category || "";
  category.addEventListener("change", () => {
    state.decision.category = category.value;
    markDecisionDirty();
  });
  card.append(category);
  const note = document.createElement("textarea");
  note.className = "decision-note";
  note.placeholder = "审核备注（缺陷原因、认可偏差的理由、依据哪几轮）";
  note.value = state.decision.note || "";
  note.addEventListener("input", () => {
    state.decision.note = note.value;
    markDecisionDirty();
  });
  card.append(note);
  const row = el("div", "save-row");
  const save = el("button", "primary", "保存结论");
  save.id = "decision-save";
  save.type = "button";
  save.addEventListener("click", saveDecision);
  row.append(save);
  row.append(el("span", "summary", "一个协议任务一条结论；保存后写入审核库并刷新左侧徽标。"));
  card.append(row);
  if (decision.confirmedAt) {
    card.append(
      el(
        "p",
        "sub",
        `上次保存：${decisionLabel(decision.status)} · ${decision.reviewer || "—"} · ${decision.confirmedAt}`,
      ),
    );
  }
  const error = el("p", "empty-state");
  error.id = "decision-error";
  error.hidden = true;
  card.append(error);
  return card;
}

function renderEvidenceCard() {
  const card = el("section", "card");
  card.append(el("h2", "", "证据"));
  const rounds = state.detail.rounds || [];
  if (!rounds.length) {
    card.append(el("p", "empty-state", "没有证据。"));
    return card;
  }
  const list = el("div", "evidence-list");
  for (const round of rounds.slice(0, 20)) {
    list.append(
      el(
        "span",
        "",
        `${round.batchId} r${round.sourceRound} · run=${round.runId || "—"} · trace=${round.traceId || "—"}`
          + ` · ${round.model || "—"} · ${round.nms ? "真机" : "本地"} · ${round.decisionMode || "—"}`,
      ),
    );
  }
  card.append(list);
  if (rounds.length > 20) card.append(el("p", "sub", `仅显示最新 20 条，共 ${rounds.length} 条。`));
  return card;
}

async function saveDecision() {
  if (!state.selected) return;
  const { caseId, protocol } = state.selected;
  const save = document.getElementById("decision-save");
  const error = document.getElementById("decision-error");
  if (error) {
    error.hidden = true;
    error.textContent = "";
  }
  if (save) save.disabled = true;
  try {
    const payload = await postJson(detailPath(caseId, protocol, "/decision"), {
      status: state.decision.status,
      category: state.decision.status === "defect" ? state.decision.category : "",
      note: state.decision.note,
    });
    state.detail = payload.detail;
    resetDecision();
    await loadCases();
    if (state.view === "round" && state.selectedBatch) await refreshBatch();
    renderDetail();
    // The list under the drawer must show the new conclusion as soon as it is saved.
    refreshBatchInPlace();
  } catch (caught) {
    if (error) {
      error.hidden = false;
      error.textContent = caught.message;
    }
    updateDecisionState();
  }
}

async function scan() {
  const button = document.getElementById("scan-button");
  button.disabled = true;
  showNotice("");
  try {
    const days = Number(field("config-days")) || 2;
    const payload = await postJson(`/api/review/scan?days=${encodeURIComponent(days)}`, {});
    state.scan = payload.scan;
    await loadCases(payload);
    await loadBatches();
    if (state.view === "round") {
      const kept = state.selectedBatch
        && state.batches.some((batch) => batch.batchId === state.selectedBatch.batchId);
      if (kept) await loadBatch();
      else {
        state.selectedBatch = null;
        state.batch = null;
        await autoSelectBatch();
      }
    } else if (state.selected) {
      await loadDetail();
    }
    const info = payload.scan || {};
    const skipped = Object.entries(info.skipped || {})
      .map(([reason, count]) => `${reason}=${count}`)
      .join("，");
    showNotice(
      `采集 ${info.batches || 0} 个批次、${info.observations || 0} 条证据`
        + `（新增 ${info.inserted || 0}，轨迹快照 ${info.traces || 0}）。`
        + `窗口 ${info.windowStart} → ${info.windowEnd}`
        + (skipped ? `；跳过 ${skipped}` : "")
        + (info.truncated ? "；已达批次上限，较早批次未纳入。" : ""),
      info.truncated ? "error" : "ok",
    );
  } catch (caught) {
    showNotice(caught.message, "error");
  } finally {
    button.disabled = false;
  }
}

function wireEvents() {
  document.getElementById("scan-button").addEventListener("click", scan);
  document
    .getElementById("view-round")
    .addEventListener("click", () => setView("round").catch((caught) => showNotice(caught.message, "error")));
  document
    .getElementById("view-case")
    .addEventListener("click", () => setView("case").catch((caught) => showNotice(caught.message, "error")));
  document
    .getElementById("view-trace")
    .addEventListener("click", () => setView("trace").catch((caught) => showNotice(caught.message, "error")));
  document
    .getElementById("trace-resolve")
    .addEventListener("click", () => resolveTraces());
  document
    .getElementById("trace-clear")
    .addEventListener("click", clearTraceInput);
  const traceInput = document.getElementById("trace-input");
  traceInput.addEventListener("keydown", (event) => {
    if ((event.ctrlKey || event.metaKey) && event.key === "Enter") {
      event.preventDefault();
      resolveTraces();
    }
  });
  for (const id of [
    "trace-filter-status",
    "trace-filter-protocol",
    "trace-filter-dimension",
    "trace-filter-resolution",
  ]) {
    document.getElementById(id).addEventListener("input", renderTraceMain);
  }
  window.addEventListener("beforeunload", (event) => {
    if (!state.decision.dirty) return;
    event.preventDefault();
    event.returnValue = "";
  });
  for (const id of [
    "filter-query",
    "filter-protocol",
    "filter-status",
    "filter-expect",
    "filter-decision",
    "filter-category",
    "only-failures",
    "only-pending",
  ]) {
    document.getElementById(id).addEventListener("input", renderCases);
  }
  for (const id of ["batch-query", "batch-nms", "batch-mode"]) {
    document.getElementById(id).addEventListener("input", renderBatches);
  }
  document.getElementById("drawer-close").addEventListener("click", closeDrawerDetail);
  document.getElementById("drawer-backdrop").addEventListener("click", closeDrawerDetail);
  document.getElementById("drawer-all-rounds").addEventListener("click", () => {
    if (!confirmDiscardDecision()) return;
    state.detailScope = null;
    closeDrawer();
    setView("case").catch((caught) => showNotice(caught.message, "error"));
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && !document.getElementById("drawer").hidden) {
      closeDrawerDetail();
    }
  });
}

async function autoSelect() {
  const rows = filterCases(state.cases, currentFilters());
  for (const caseRow of rows) {
    for (const task of caseRow.protocols) {
      if (task.status !== "not_applicable") {
        await selectProtocol(caseRow, task.protocol);
        return;
      }
    }
  }
}

async function init() {
  wireEvents();
  try {
    state.meta = await getJson("/api/review/meta");
    await loadBatches();
    const linked = traceLinkInput();
    if (linked) {
      const node = document.getElementById("trace-input");
      if (node) node.value = linked;
      await setView("trace");
      await resolveTraces();
    } else {
      await setView("round");
    }
  } catch (caught) {
    showNotice(caught.message, "error");
  }
}

if (typeof document !== "undefined") {
  init();
}
