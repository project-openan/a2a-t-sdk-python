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
  final_state: "终态",
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
  cases: [],
  selected: null, // {caseId, protocol}
  detail: null,
  round: 0, // index into detail.rounds, or -1 for the round matrix
  decision: { status: "pending", category: "", note: "", dirty: false },
  intentDiffOnly: true,
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

//: Human-readable label of one stored round.
export function roundLabel(round) {
  return [
    round.batchId || "unknown",
    `r${round.sourceRound || 1}`,
    String(round.observedAt || "").replace("T", " ").slice(0, 16),
    round.model || "—",
    round.nms ? "真机" : "本地",
    round.decisionMode || "—",
    statusLabel(round.judgement && round.judgement.status),
  ].join(" · ");
}

//: Default round: the newest real-machine live run, else the newest one.
export function defaultRoundIndex(rounds) {
  if (!rounds || !rounds.length) return 0;
  const live = rounds.findIndex((round) => round.nms && round.decisionMode === "live");
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
  document.getElementById("progress").textContent = progressText(progressPayload || payload);
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

async function selectProtocol(caseRow, protocol) {
  if (!confirmDiscardDecision()) return;
  state.selected = { caseId: caseRow.caseId, protocol, caseName: caseRow.caseName };
  state.snippet = null;
  await loadDetail();
}

async function loadDetail() {
  if (!state.selected) return;
  const container = document.getElementById("detail");
  container.replaceChildren(el("section", "card", "加载中…"));
  const { caseId, protocol } = state.selected;
  state.detail = await getJson(detailPath(caseId, protocol));
  state.round = defaultRoundIndex(state.detail.rounds || []);
  resetDecision();
  renderDetail();
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
  container.replaceChildren();
  container.append(renderCaseCard());
  container.append(renderRoundBar());
  if (state.round === -1) container.append(renderRoundMatrix());
  else {
    const round = (state.detail.rounds || [])[state.round];
    if (round) {
      container.append(renderDimensionStrip(round));
      container.append(renderIntentCard(round));
      container.append(renderTaskTCard(round));
      container.append(renderValidateCard(round));
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
    strip.append(
      el(
        "span",
        `badge ${dimension.status}`,
        `${dimensionLabel(dimension.dimension)} · ${statusLabel(dimension.status)}`,
      ),
    );
  }
  card.append(strip);
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
  return card;
}

function renderTaskTCard(round) {
  const card = el("section", "card");
  const detail = (round.judgement && round.judgement.detail) || {};
  const taskT = detail.taskT || {};
  card.append(el("h2", "", "SDK Task-T"));
  card.append(el("p", "sub", "Task-T 直接决定下游如何理解请求；分节判定，固定模板文字只记录不判。"));
  if (!taskT.rows || !taskT.rows.length) {
    card.append(el("p", "empty-state", "没有 Task-T 证据（拦截前即失败或该协议不适用）。"));
    return card;
  }
  const table = document.createElement("table");
  table.className = "grid";
  const headRow = el("tr");
  for (const title of ["小节", "预期", "实际", "结论"]) {
    headRow.append(el("th", "", title));
  }
  table.append(headRow);
  for (const row of taskT.rows) {
    const tr = el("tr", row.verdict === "same" ? "" : "fail");
    tr.append(
      el("td", "mono-cell", row.path.replace("task_t.", "")),
      el("td", "", shortText(row.expected)),
      el("td", "", shortText(row.actual)),
      el("td", `verdict ${row.verdict}`, row.verdict === "same" ? "一致" : row.reason || row.verdict),
    );
    table.append(tr);
  }
  card.append(table);
  const raw = document.createElement("details");
  raw.append(el("summary", "", "生成的 Task-T 原文"));
  raw.append(el("pre", "mono", taskT.actual || ""));
  card.append(raw);
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
    if (state.selected) await loadDetail();
    const info = payload.scan || {};
    const skipped = Object.entries(info.skipped || {})
      .map(([reason, count]) => `${reason}=${count}`)
      .join("，");
    showNotice(
      `采集 ${info.batches || 0} 个批次、${info.observations || 0} 条证据（新增 ${info.inserted || 0}）。`
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
    await loadCases();
    await autoSelect();
  } catch (caught) {
    showNotice(caught.message, "error");
  }
}

if (typeof document !== "undefined") {
  init();
}
