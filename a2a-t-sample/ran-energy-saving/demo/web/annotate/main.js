// Expectation annotation console.
//
// The page mirrors the corpus: pick a case and a protocol on the left, then work the subtasks on
// the right. A subtask's candidates come from every stored tester batch, and the run-round
// selector narrows them to one observation (default: the newest real-machine live run). Only
// candidates that agree with the case may be adopted; the values are exported, never written to
// the case files, because the yellow zone hot-replaces them.
//
// Pure helpers are exported so the Node tests can drive them without a DOM.

const SLOT_DIMENSION = "slot_errors";
const MESSAGE_DIMENSION = "messages";

const STATUS_LABELS = {
  pending: "未采集",
  draft: "草稿",
  confirmed: "已确认",
  none: "无需标注",
};

const state = {
  meta: null,
  cases: [],
  selected: null, // {caseId, protocol}
  details: {},
  run: "",
  runTouched: false,
  snippet: null,
  scan: null,
};

export function statusLabel(status) {
  if (!status) return STATUS_LABELS.pending;
  return STATUS_LABELS[status] || String(status);
}

export function subtaskLabel(dimension) {
  return dimension === SLOT_DIMENSION ? "拦截 slot_errors" : "协商报文";
}

export function protocolLabel(protocol) {
  return protocol === "a2a-t" ? "A2A-T" : "A2A";
}

export function progressText(progress) {
  if (!progress || !progress.counts) return "";
  const counts = progress.counts;
  return `子任务 ${counts.total || 0} · 未采集 ${counts.pending || 0} · 草稿 ${counts.draft || 0}`
    + ` · 已确认 ${counts.confirmed || 0}`;
}

//: Sub-task rows of one protocol task.
export function subtasksOf(caseRow, protocol) {
  const task = (caseRow.protocols || []).find((item) => item.protocol === protocol);
  return task && Array.isArray(task.subtasks) ? task.subtasks : [];
}

//: Filter the corpus rows by case, protocol, sub-task, status and evidence presence.
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
      let subtasks = task.subtasks || [];
      if (filters.subtask) subtasks = subtasks.filter((row) => row.dimension === filters.subtask);
      if (filters.status) {
        subtasks = subtasks.filter((row) => (row.status || "pending") === filters.status);
      }
      if (filters.candidates === "with") {
        subtasks = subtasks.filter((row) => Number(row.observations || 0) > 0);
      } else if (filters.candidates === "without") {
        subtasks = subtasks.filter((row) => Number(row.observations || 0) === 0);
      }
      if (!subtasks.length) {
        if (filters.hideNone) continue;
        if (filters.subtask || filters.status || filters.candidates) continue;
      }
      protocols.push({ ...task, subtasks });
    }
    if (!protocols.length) continue;
    rows.push({ ...caseRow, protocols });
  }
  return rows;
}

export function runKey(observation) {
  return `${observation.sourceBatch || ""}#${observation.sourceRound || 1}`;
}

export function runLabel(observation) {
  return [
    observation.sourceBatch || "unknown",
    `r${observation.sourceRound || 1}`,
    String(observation.observedAt || "").replace("T", " ").slice(0, 16),
    observation.model || "—",
    observation.nms ? "真机" : "本地",
    observation.decisionMode || "—",
  ].join(" · ");
}

//: Unique run options of a detail payload, newest first.
export function runOptions(observations) {
  const byKey = new Map();
  for (const observation of observations || []) {
    const key = runKey(observation);
    const existing = byKey.get(key);
    if (existing) {
      existing.count += 1;
      continue;
    }
    byKey.set(key, { key, observation, count: 1 });
  }
  return [...byKey.values()]
    .map(({ key, observation, count }) => ({
      key,
      label: runLabel(observation),
      observedAt: String(observation.observedAt || ""),
      nms: Boolean(observation.nms),
      decisionMode: String(observation.decisionMode || ""),
      count,
    }))
    .sort((left, right) => right.observedAt.localeCompare(left.observedAt));
}

//: Default run: the newest real-machine live observation, else the newest one.
export function defaultRunKey(options) {
  if (!options || !options.length) return "";
  const live = options.find((option) => option.nms && option.decisionMode === "live");
  return (live || options[0]).key;
}

export function filterObservations(observations, filters) {
  return (observations || []).filter((observation) => {
    if (filters.run && runKey(observation) !== filters.run) return false;
    if (filters.nms && !observation.nms) return false;
    if (filters.live && observation.decisionMode !== "live") return false;
    if (filters.consistent && !observation.consistent) return false;
    return true;
  });
}

export function groupByRound(observations) {
  const groups = new Map();
  for (const observation of observations || []) {
    const round = Number(observation.round) || 0;
    if (!groups.has(round)) groups.set(round, []);
    groups.get(round).push(observation);
  }
  return [...groups.entries()].sort((left, right) => left[0] - right[0]);
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

function field(id) {
  const node = document.getElementById(id);
  return node ? node.value : "";
}

function checked(id) {
  const node = document.getElementById(id);
  return node ? node.checked : false;
}

function showNotice(message, isError = false) {
  const node = document.getElementById("scan-note");
  if (!node) return;
  if (!message) {
    node.hidden = true;
    return;
  }
  node.hidden = false;
  node.className = isError ? "notice error" : "notice";
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

function subtaskPath(caseId, protocol, dimension, suffix = "") {
  return `/api/annotate/case/${caseId}/${protocol}/${dimension}${suffix}`;
}

async function loadCases(progressPayload) {
  const payload = await getJson("/api/annotate/cases");
  state.cases = payload.cases || [];
  document.getElementById("progress").textContent = progressText(progressPayload || payload);
  renderCases();
}

function currentFilters() {
  return {
    query: field("filter-query"),
    protocol: field("filter-protocol"),
    subtask: field("filter-subtask"),
    status: field("filter-status"),
    candidates: field("filter-candidates"),
    hideNone: checked("hide-none"),
  };
}

function renderCases() {
  const list = document.getElementById("case-list");
  const rows = filterCases(state.cases, currentFilters());
  document.getElementById("case-count").textContent = `${rows.length} / ${state.cases.length}`;
  list.replaceChildren();
  if (!rows.length) {
    list.append(el("p", "empty-state", "没有匹配的用例。"));
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
      if (task.status === "none") {
        badges.append(el("span", "badge none", statusLabel("none")));
      } else {
        for (const subtask of task.subtasks) {
          const status = subtask.status || "pending";
          badges.append(
            el(
              "span",
              `badge ${status}`,
              `${subtask.dimension === SLOT_DIMENSION ? "拦截" : "协商"} · ${statusLabel(status)}`,
            ),
          );
        }
      }
      row.append(badges);
      row.addEventListener("click", () => selectProtocol(caseRow, task.protocol));
      block.append(row);
    }
    list.append(block);
  }
}

async function selectProtocol(caseRow, protocol) {
  state.selected = { caseId: caseRow.caseId, protocol, caseName: caseRow.caseName };
  state.snippet = null;
  state.run = "";
  state.runTouched = false;
  renderCases();
  await loadDetails();
}

async function loadDetails() {
  const container = document.getElementById("detail");
  container.replaceChildren(el("section", "card", "加载中…"));
  const { caseId, protocol } = state.selected;
  const caseRow = state.cases.find((row) => row.caseId === caseId);
  const subtasks = subtasksOf(caseRow || {}, protocol);
  const details = {};
  await Promise.all(
    subtasks.map(async (subtask) => {
      details[subtask.dimension] = await getJson(
        subtaskPath(caseId, protocol, subtask.dimension),
      );
    }),
  );
  state.details = details;
  if (!state.runTouched) {
    const observations = Object.values(details).flatMap((detail) => detail.observations || []);
    state.run = defaultRunKey(runOptions(observations));
  }
  renderDetail();
}

function currentDetailObservations() {
  const filters = {
    run: state.run,
    nms: checked("run-nms"),
    live: checked("run-live"),
    consistent: checked("run-consistent"),
  };
  const visible = {};
  for (const [dimension, detail] of Object.entries(state.details)) {
    visible[dimension] = filterObservations(detail.observations || [], filters);
  }
  return visible;
}

function renderDetail() {
  if (!state.selected) return;
  const container = document.getElementById("detail");
  container.replaceChildren();
  const { caseId, protocol } = state.selected;
  const caseRow = state.cases.find((row) => row.caseId === caseId) || {};
  const subtasks = subtasksOf(caseRow, protocol);
  container.append(renderHeader(caseRow, protocol));
  if (!subtasks.length) {
    container.append(
      el("section", "card empty", "该用例的这个协议没有需要标注的子任务（无需标注）。"),
    );
    return;
  }
  container.append(renderRunBar(subtasks));
  const visible = currentDetailObservations();
  const dimensionFilter = field("filter-subtask");
  for (const subtask of subtasks) {
    if (dimensionFilter && subtask.dimension !== dimensionFilter) continue;
    const detail = state.details[subtask.dimension];
    if (!detail) continue;
    container.append(
      subtask.dimension === SLOT_DIMENSION
        ? renderSlotPanel(detail, visible[subtask.dimension] || [])
        : renderMessagePanel(detail, visible[subtask.dimension] || []),
    );
  }
  container.append(renderCaseExport(caseRow));
}

function renderHeader(caseRow, protocol) {
  const card = el("section", "card");
  const title = el("div", "section-title");
  title.append(
    el("h2", "", `#${caseRow.caseId} ${caseRow.caseName || ""} · ${protocolLabel(protocol)}`),
  );
  const task = (caseRow.protocols || []).find((row) => row.protocol === protocol);
  title.append(el("span", `badge ${task ? task.status : "none"}`, statusLabel(task && task.status)));
  card.append(title);
  const facts = el("dl", "kv");
  const rows = [
    ["操作", caseRow.operation],
    ["维度", caseRow.dimension],
    ["预期校验", task && task.validate],
    ["预期轮次", task && task.expectedRounds],
    ["预期类型", task && (task.kinds || []).join(", ")],
  ];
  for (const [label, value] of rows) {
    facts.append(el("dt", "", label));
    facts.append(el("dd", "", textBlock(value)));
  }
  card.append(facts);
  return card;
}

function renderRunBar(subtasks) {
  const card = el("section", "card runbar");
  const observations = subtasks.flatMap((subtask) => {
    const detail = state.details[subtask.dimension];
    return detail ? detail.observations || [] : [];
  });
  const options = runOptions(observations);
  const label = el("label", "", "运行轮次");
  const select = document.createElement("select");
  const all = document.createElement("option");
  all.value = "";
  all.textContent = `全部轮次（${options.length}）`;
  select.append(all);
  for (const option of options) {
    const node = document.createElement("option");
    node.value = option.key;
    node.textContent = `${option.label} · ${option.count} 条`;
    select.append(node);
  }
  if (!state.runTouched) state.run = defaultRunKey(options);
  select.value = state.run;
  select.addEventListener("change", () => {
    state.run = select.value;
    state.runTouched = true;
    renderDetail();
  });
  label.append(select);
  card.append(label);
  card.append(check("run-nms", "只看真机", true));
  card.append(check("run-live", "只看 live", true));
  card.append(check("run-consistent", "只看一致候选", true));
  if (!options.length) {
    card.append(el("span", "summary", "该时间窗内没有该协议、该子任务的观测。"));
  }
  return card;
}

function check(id, label, defaultChecked) {
  const wrapper = el("label", "check");
  const input = document.createElement("input");
  input.type = "checkbox";
  input.id = id;
  input.checked = defaultChecked;
  input.addEventListener("change", () => renderDetail());
  wrapper.append(input, document.createTextNode(` ${label}`));
  return wrapper;
}

function renderSlotPanel(detail, observations) {
  const card = el("section", "card");
  const head = el("div", "section-title");
  head.append(el("h2", "", subtaskLabel(SLOT_DIMENSION)));
  const mode = document.createElement("select");
  for (const value of ["any", "cover"]) {
    const option = document.createElement("option");
    option.value = value;
    option.textContent = value === "any" ? "any（任一命中）" : "cover（全部覆盖）";
    mode.append(option);
  }
  mode.value = detail.target.mode || "any";
  mode.addEventListener("change", async () => {
    await postJson(
      subtaskPath(detail.target.caseId, detail.target.protocol, SLOT_DIMENSION, "/mode"),
      { mode: mode.value },
    );
    await refresh();
  });
  head.append(mode);
  card.append(head);
  card.append(el("p", "sub", "用例现有期望"));
  card.append(
    el(
      "pre",
      "mono",
      detail.current === null || detail.current === undefined
        ? "（用例当前没有 slot_errors 期望，只按校验结论判定）"
        : textBlock(detail.current),
    ),
  );

  const accepted = acceptedMap(detail);
  const table = document.createElement("table");
  table.className = "grid";
  const thead = document.createElement("thead");
  const headRow = document.createElement("tr");
  for (const title of ["采纳", "错误码", "槽位", "facts", "说明", "次数", "来源", "关键词（可选）"]) {
    headRow.append(el("th", "", title));
  }
  thead.append(headRow);
  table.append(thead);
  const tbody = document.createElement("tbody");
  if (!observations.length) {
    const row = document.createElement("tr");
    const cell = el("td", "empty-state", "当前轮次过滤后没有候选。");
    cell.colSpan = 8;
    row.append(cell);
    tbody.append(row);
  }
  for (const observation of observations) {
    const row = document.createElement("tr");
    if (!observation.consistent) row.className = "inconsistent";
    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.disabled = !observation.consistent;
    checkbox.checked = accepted.has(observation.id);
    const keywords = document.createElement("input");
    keywords.className = "keywords";
    keywords.placeholder = "gain, 增益";
    keywords.value = accepted.get(observation.id)?.editedText || "";
    const save = async () => {
      await postJson(
        subtaskPath(detail.target.caseId, detail.target.protocol, SLOT_DIMENSION, "/choice"),
        {
          observationId: observation.id,
          accepted: checkbox.checked,
          editedText: keywords.value || null,
        },
      );
      await refresh();
    };
    checkbox.addEventListener("change", save);
    keywords.addEventListener("change", () => {
      if (checkbox.checked) save();
    });
    const first = document.createElement("td");
    first.append(checkbox);
    const keywordCell = document.createElement("td");
    keywordCell.append(keywords);
    row.append(
      first,
      el("td", "mono-cell", observation.code || ""),
      el("td", "mono-cell", observation.slotName || ""),
      el("td", "mono-cell", JSON.stringify(observation.facts || {})),
      el("td", "", observation.message || ""),
      el("td", "mono-cell", String(observation.seenCount || 1)),
      el("td", "mono-cell", `${observation.sourceBatch || ""} r${observation.sourceRound || 1}`),
      keywordCell,
    );
    tbody.append(row);
  }
  table.append(tbody);
  card.append(table);
  card.append(renderPending(detail));
  card.append(renderConfirmPanel(detail));
  return card;
}

function renderMessagePanel(detail, observations) {
  const card = el("section", "card");
  card.append(
    el("h2", "", `${subtaskLabel(MESSAGE_DIMENSION)}（${detail.case.expectedRounds} 轮）`),
  );
  card.append(el("p", "sub", "用例现有期望"));
  card.append(
    el(
      "pre",
      "mono",
      detail.current === null || detail.current === undefined
        ? "（用例当前没有 messages 期望，只按轮数与类型判定）"
        : textBlock(detail.current),
    ),
  );
  const accepted = acceptedMap(detail);
  const groups = groupByRound(observations);
  if (!groups.length) {
    card.append(el("p", "empty-state", "当前轮次过滤后没有候选。"));
    card.append(renderPending(detail));
    card.append(renderConfirmPanel(detail));
    return card;
  }
  for (const [round, rows] of groups) {
    const group = el("div", "round-group");
    const head = el("div", "round-head");
    head.append(el("strong", "", `第 ${round} 轮`));
    head.append(
      el(
        "span",
        "summary",
        `${rows.length} 个变体 · 已采纳 ${rows.filter((row) => accepted.has(row.id)).length}`,
      ),
    );
    group.append(head);
    for (const observation of rows) {
      const candidate = el("div", "candidate");
      const candidateHead = el("div", "candidate-head");
      const checkbox = document.createElement("input");
      checkbox.type = "checkbox";
      checkbox.disabled = !observation.consistent;
      checkbox.checked = accepted.has(observation.id);
      const label = el("label", "", "采纳");
      label.prepend(checkbox);
      checkbox.addEventListener("change", async () => {
        await postJson(
          subtaskPath(detail.target.caseId, detail.target.protocol, MESSAGE_DIMENSION, "/choice"),
          { observationId: observation.id, accepted: checkbox.checked, editedText: null },
        );
        await refresh();
      });
      candidateHead.append(label);
      candidateHead.append(
        el(
          "span",
          "source",
          `${observation.kind || "unknown"} · ${observation.sourceBatch} r${observation.sourceRound}`,
        ),
      );
      candidateHead.append(el("span", "summary", `出现 ${observation.seenCount || 1} 次`));
      if (!observation.consistent) candidateHead.append(el("span", "badge", "与预期不一致"));
      const editButton = el("button", "secondary", "编辑备选");
      candidateHead.append(editButton);
      candidate.append(candidateHead);
      candidate.append(el("pre", "mono", observation.content || ""));

      const editor = document.createElement("textarea");
      editor.hidden = true;
      editor.value = accepted.get(observation.id)?.editedText || observation.content || "";
      editButton.addEventListener("click", () => {
        editor.hidden = !editor.hidden;
      });
      editor.addEventListener("change", async () => {
        await postJson(
          subtaskPath(detail.target.caseId, detail.target.protocol, MESSAGE_DIMENSION, "/choice"),
          { observationId: observation.id, accepted: true, editedText: editor.value },
        );
        await refresh();
      });
      candidate.append(editor);
      group.append(candidate);
    }
    card.append(group);
  }
  card.append(renderPending(detail));
  card.append(renderConfirmPanel(detail));
  return card;
}

function acceptedMap(detail) {
  return new Map(
    (detail.choices || [])
      .filter((choice) => choice.accepted)
      .map((choice) => [choice.observationId, choice]),
  );
}

function renderPending(detail) {
  const block = el("div", "");
  block.append(el("p", "sub", "采纳后的导出值（确认后进入全量导出）"));
  block.append(
    el(
      "pre",
      "mono",
      detail.pending === null || detail.pending === undefined
        ? "（尚未采纳任何候选）"
        : textBlock(detail.pending),
    ),
  );
  return block;
}

function renderConfirmPanel(detail) {
  const card = el("div", "confirm");
  const target = detail.target;
  card.append(
    el(
      "span",
      "summary",
      target.confirmedBy
        ? `已确认：${target.confirmedBy} · ${target.confirmedAt || ""}`
        : "尚未确认",
    ),
  );
  const note = document.createElement("input");
  note.className = "note";
  note.placeholder = "备注（例如：依据 11-15 三批 live 的多数渲染）";
  note.value = target.note || "";
  card.append(note);
  const saveNote = el("button", "secondary", "保存备注");
  saveNote.addEventListener("click", async () => {
    await postJson(
      subtaskPath(target.caseId, target.protocol, target.dimension, "/note"),
      { note: note.value },
    );
    await refresh();
  });
  const confirm = el("button", "primary", "确认");
  confirm.disabled = !(detail.choices || []).some((choice) => choice.accepted);
  confirm.addEventListener("click", async () => {
    await postJson(subtaskPath(target.caseId, target.protocol, target.dimension, "/confirm"), {});
    await refresh();
  });
  card.append(saveNote, confirm);
  return card;
}

function renderCaseExport(caseRow) {
  const card = el("section", "card");
  const head = el("div", "section-title");
  head.append(el("h2", "", "导出该用例"));
  const button = el("button", "secondary", "生成期望片段");
  button.addEventListener("click", async () => {
    const response = await fetch(`/api/annotate/export/case/${caseRow.caseId}`, {
      credentials: "same-origin",
    });
    if (!response.ok) {
      showNotice(`生成片段失败（HTTP ${response.status}）。`, true);
      return;
    }
    state.snippet = await response.text();
    renderDetail();
  });
  head.append(button);
  card.append(head);
  card.append(
    el(
      "p",
      "sub",
      "片段按 expect.protocols 的层级（6 空格）生成，供人工合并进 eval/osp/cases/<用例>.yaml。",
    ),
  );
  if (state.snippet) card.append(el("pre", "mono", state.snippet));
  return card;
}

async function refresh() {
  await loadCases();
  if (state.selected) await loadDetails();
}

async function scan() {
  const button = document.getElementById("scan-button");
  button.disabled = true;
  showNotice("");
  try {
    const days = Number(field("config-days")) || 1;
    const payload = await postJson(`/api/annotate/scan?days=${encodeURIComponent(days)}`, {});
    state.scan = payload.scan;
    await loadCases(payload);
    if (state.selected) await loadDetails();
    const info = payload.scan || {};
    const skipped = Object.entries(info.skipped || {})
      .map(([reason, count]) => `${reason}=${count}`)
      .join("，");
    showNotice(
      `扫描 ${info.batches || 0} 个批次，得到 ${info.observations || 0} 条候选`
        + `（新增 ${info.inserted || 0}）。窗口 ${info.windowStart} → ${info.windowEnd}`
        + (skipped ? `；跳过 ${skipped}` : "")
        + (info.truncated ? "；已达批次上限，较早的批次未纳入。" : ""),
      Boolean(info.truncated),
    );
  } catch (error) {
    showNotice(error.message, true);
  } finally {
    button.disabled = false;
  }
}

function wireEvents() {
  document.getElementById("scan-button").addEventListener("click", scan);
  const ids = [
    "filter-query",
    "filter-protocol",
    "filter-subtask",
    "filter-status",
    "filter-candidates",
    "hide-none",
  ];
  for (const id of ids) {
    document.getElementById(id).addEventListener("input", () => {
      renderCases();
      if (state.selected) renderDetail();
    });
  }
}

async function autoSelect() {
  const rows = filterCases(state.cases, currentFilters());
  for (const caseRow of rows) {
    for (const task of caseRow.protocols) {
      if (task.subtasks.length) {
        await selectProtocol(caseRow, task.protocol);
        return;
      }
    }
  }
}

async function init() {
  wireEvents();
  try {
    state.meta = await getJson("/api/annotate/meta");
    await loadCases();
    await autoSelect();
  } catch (error) {
    showNotice(error.message, true);
  }
}

if (typeof document !== "undefined") {
  init();
}
