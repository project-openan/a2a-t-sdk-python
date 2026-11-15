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
  stale: "需重跑",
  not_run: "未跑",
};

const DECISION_LABELS = {
  pending: "未审核",
  accepted: "通过",
  defect: "缺陷",
  known_issue: "已知问题",
  recheck: "待复核",
};

const CATEGORY_LABELS = {
  sdk: "SDK",
  vendor: "网管",
  case: "用例",
  env: "环境",
  other: "其他",
  "": "—",
};

const state = {
  meta: null,
  view: "round", // "round": batches of evidence first; "case": the corpus first
  cases: [],
  batches: [],
  batch: null, // payload of the selected batch/round
  selectedBatch: null, // {batchId, round}
  selected: null, // {caseId, protocol}
  detail: null,
  round: 0, // index into detail.rounds, or -1 for the round matrix
  decision: { status: "pending", category: "", note: "", dirty: false },
  intentDiffOnly: true,
  taskTDiffOnly: true,
  snippet: null,
  scan: null,
};

export function statusLabel(status) {
  return STATUS_LABELS[status] || String(status || "未跑");
}

export function decisionLabel(status) {
  return DECISION_LABELS[status] || String(status || "未审核");
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
    + ` · 部分 ${statuses.partial || 0} · 需重跑 ${statuses.stale || 0} · 未跑 ${statuses.not_run || 0}`
    + ` ｜ 已审核 ${decisions.accepted || 0} · 缺陷 ${decisions.defect || 0}`
    + ` · 已知问题 ${decisions.known_issue || 0} · 待复核 ${decisions.recheck || 0}`;
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
    const protocols = [];
    for (const task of caseRow.protocols || []) {
      if (filters.protocol && task.protocol !== filters.protocol) continue;
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
      if (filters.onlyFailures && !["fail", "partial", "stale"].includes(task.status)) continue;
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

//: Default round: the newest real-machine live run (never the simulator), else the newest one.
export function defaultRoundIndex(rounds) {
  if (!rounds || !rounds.length) return 0;
  const live = rounds.findIndex(
    (round) => round.nms && !round.nmsSim && round.decisionMode === "live",
  );
  return live >= 0 ? live : 0;
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

function shortText(value, limit = 180) {
  const text = typeof value === "string" ? value : textBlock(value);
  return text.length > limit ? `${text.slice(0, limit)}…` : text;
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
          decision.stale || decision.newEvidence
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
      badges.append(el("span", "badge", `${batch.observations || 0} 证据`));
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
  const container = document.getElementById("detail");
  const scrollTop = container.scrollTop;
  container.replaceChildren();
  container.append(renderBatchSummary());
  container.append(renderBatchTable());
  refreshBatchTable();
  container.scrollTop = scrollTop;
  const header = document.getElementById("progress");
  if (header) header.textContent = progressText(state.batch);
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
  return card;
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
  for (const label of ["#", "用例", "操作", "A2A-T", "A2A", "标记"]) {
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
    dimension: field("batch-filter-dimension"),
    decision: field("batch-filter-decision"),
    category: field("batch-filter-category"),
    onlyFailures: checked("batch-only-failures"),
    onlyPending: checked("batch-only-pending"),
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
    ["batch-filter-dimension", dimensionOptions],
    [
      "batch-filter-status",
      [
        ["", "全部自动判定"],
        ["pass", "通过"],
        ["fail", "失败"],
        ["partial", "部分可判"],
        ["stale", "需重跑"],
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
    ["batch-only-failures", "只看失败/部分/需重跑"],
    ["batch-only-pending", "只看未审核"],
  ]) {
    const wrapper = el("label", "check");
    const input = document.createElement("input");
    input.type = "checkbox";
    input.id = id;
    input.addEventListener("change", refreshBatchTable);
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
    cell.colSpan = 6;
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
    if (task.inputChanged) markers.add("输入已变更");
    const decision = task.decision || {};
    const decisionStatus = decision.status || "pending";
    if (decision.stale) markers.add("结论待复核");
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

//: Tint the detail area so A2A and A2A-T are distinguishable at a glance.
function applyProtocolTint() {
  const container = document.getElementById("detail");
  const protocol = (state.detail && state.detail.protocol) || "";
  container.classList.toggle("proto-a2a-t", protocol === "a2a-t");
  container.classList.toggle("proto-a2a", protocol === "a2a");
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
  state.view = view === "case" ? "case" : "round";
  for (const [id, active] of [
    ["view-round", state.view === "round"],
    ["view-case", state.view === "case"],
  ]) {
    const tab = document.getElementById(id);
    tab.classList.toggle("active", active);
    tab.setAttribute("aria-selected", String(active));
  }
  const layout = document.getElementById("layout");
  if (layout) layout.classList.toggle("view-round", state.view === "round");
  document.getElementById("sidebar-round").hidden = state.view !== "round";
  document.getElementById("sidebar-case").hidden = state.view !== "case";
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
    if (state.detail && state.selected) renderDetail();
    else {
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
  state.snippet = null;
  await loadDetail();
}

async function loadDetail() {
  if (!state.selected) return;
  const container = document.getElementById("detail");
  container.replaceChildren(el("section", "card", "加载中…"));
  const { caseId, protocol } = state.selected;
  state.detail = await getJson(detailPath(caseId, protocol));
  let index = defaultRoundIndex(state.detail.rounds || []);
  const scope = state.detailScope;
  if (scope) {
    const match = (state.detail.rounds || []).findIndex(
      (row) => row.batchId === scope.batchId && Number(row.sourceRound || 1) === Number(scope.round || 1),
    );
    if (match >= 0) index = match;
  }
  state.round = index;
  resetDecision();
  renderDetail();
}

async function fetchTrace(button) {
  if (!state.selected) return;
  const { caseId, protocol } = state.selected;
  button.disabled = true;
  button.textContent = "抓取中…";
  try {
    await postJson(detailPath(caseId, protocol, "/trace"), {});
    await loadDetail();
  } catch (caught) {
    showNotice(caught.message, "error");
    button.disabled = false;
    button.textContent = "抓取轨迹";
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
  const container = document.getElementById("detail");
  const scrollTop = container.scrollTop;
  applyProtocolTint();
  container.replaceChildren();
  if (state.view === "round" && state.batch) {
    const bar = el("div", "backbar");
    const back = el("button", "ghost", `← 返回批次 ${state.selectedBatch?.batchId || ""}`);
    back.type = "button";
    back.addEventListener("click", () => {
      state.detail = null;
      state.selected = null;
      state.detailScope = null;
      renderBatch();
    });
    bar.append(back);
    container.append(bar);
  }
  container.append(renderCaseCard());
  container.append(renderRoundBar());
  if (state.round === -1) container.append(renderRoundMatrix());
  else {
    const round = (state.detail.rounds || [])[state.round];
    if (round) {
      container.append(renderDimensionStrip(round));
      container.append(renderIntentCard(round));
      // Pure A2A has no SDK Task-T and no interception, and both dimensions are judged
      // not_applicable there; showing the empty cards only adds noise.
      if (state.detail.protocol === "a2a-t") {
        container.append(renderTaskTCard(round));
        container.append(renderValidateCard(round));
      }
      container.append(renderNegotiationCard(round));
      container.append(renderRuntimeCard(round));
    }
  }
  container.append(renderDecisionCard());
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
  head.append(copy);
  item.append(head);
  if (hint) item.append(el("p", "request-hint", hint));
  item.append(el("pre", "request-text", text));
  return item;
}

function protocolStatus() {
  const rounds = state.detail && state.detail.rounds;
  if (!rounds || !rounds.length) return "not_run";
  const round = rounds[state.round >= 0 ? state.round : 0];
  return round && round.inputChanged ? "stale" : round && round.judgement
    ? round.judgement.status
    : "not_run";
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
    option.textContent = roundLabel(round);
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
  const trace = (round.judgement && round.judgement.detail && round.judgement.detail.trace) || {};
  if (trace.present) {
    const nms = (trace.frames && trace.frames.nms) || {};
    const attribution = trace.attribution || {};
    const findings = attribution.findings || [];
    const facts = [
      `轨迹 ${trace.nmsKind || "—"}`,
      trace.spanLevel ? `层级 ${trace.spanLevel}` : "",
      trace.captureContent === false ? "内容未捕获" : "",
      `NMS 帧 ${nms.observed ?? "—"}/${nms.expected ?? "—"}`,
      attribution.status
        ? `归因 ${attribution.status}${findings.length ? `（${findings.length} 点）` : ""}`
        : "",
    ].filter(Boolean);
    card.append(el("p", "sub", facts.join(" · ")));
  }
  const failures = (round.judgement && round.judgement.failures) || [];
  const notExported = failures.some(
    (row) =>
      row.dimension === "trace"
      && (row.diffs || []).some((diff) => diff.reason === "trace_not_exported"),
  );
  if (notExported && round.runId) {
    const button = el("button", "ghost small", "抓取轨迹");
    button.type = "button";
    button.addEventListener("click", () => fetchTrace(button));
    card.append(button);
  }
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
  if (round.inputChanged) {
    card.append(
      el("p", "hint", "该轮证据来自另一个输入（用例输入已变更），不能作为当前用例的判定，需要重跑。"),
    );
  }
  return card;
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
      el("td", "", shortText(row.expected)),
      el("td", "", shortText(row.actual)),
      el("td", "", shortText(sdkByPath.get(row.path)?.actual)),
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
        el("td", "", shortText(row.expected)),
        el("td", "", shortText(row.actual)),
        el("td", "", exported === undefined ? "—" : shortText(exported)),
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
  const points = (detail.trace && detail.trace.points) || [];
  if (points.length) {
    const box = document.createElement("details");
    const passed = points.filter((point) => point.ok).length;
    box.append(
      el("summary", "", `过程点（期望 vs Langfuse，${passed}/${points.length} 满足）`),
    );
    const table = document.createElement("table");
    table.className = "grid";
    const headRow = el("tr");
    for (const title of ["过程点", "期望", "实际", "结论"]) {
      headRow.append(el("th", "", title));
    }
    table.append(headRow);
    for (const point of points) {
      const tr = el("tr", point.ok ? "" : "fail");
      tr.append(
        el("td", "", point.label),
        el("td", "", point.expected),
        el("td", "", point.observed),
        el("td", `verdict ${point.ok ? "same" : "missing"}`, point.ok ? "满足" : "缺失"),
      );
      table.append(tr);
    }
    box.append(table);
    card.append(box);
  }
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
  for (const message of messages) {
    const block = el("div", "candidate");
    block.append(
      el(
        "div",
        "candidate-head",
        `第 ${message.round} 轮 · ${message.kind || "unknown"}`,
      ),
    );
    block.append(el("pre", "mono", message.content || ""));
    const expected = alternatives[(message.round || 1) - 1];
    if (expected && expected.length) {
      const alternativesBox = document.createElement("details");
      alternativesBox.append(el("summary", "", `该轮 ${expected.length} 个可接受备选`));
      for (const text of expected) alternativesBox.append(el("pre", "mono", text));
      block.append(alternativesBox);
    }
    card.append(block);
  }
  const match = negotiation.messageMatch;
  if (match) {
    card.append(
      el(
        "p",
        `verdict ${match.matched ? "pass" : "fail"}`,
        match.matched ? "报文匹配通过" : "报文与所有备选都不匹配",
      ),
    );
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
    tr.append(
      el("td", "", round.inputChanged ? "需重跑" : statusLabel(round.judgement && round.judgement.status)),
    );
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
  if (decision.newEvidence) card.append(el("p", "hint", "采集到了结论之后的新轮次，需要复核。"));
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
    const days = Number(field("config-days")) || 7;
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
  window.addEventListener("beforeunload", (event) => {
    if (!state.decision.dirty) return;
    event.preventDefault();
    event.returnValue = "";
  });
  for (const id of [
    "filter-query",
    "filter-protocol",
    "filter-status",
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
    await setView("round");
  } catch (caught) {
    showNotice(caught.message, "error");
  }
}

if (typeof document !== "undefined") {
  init();
}
