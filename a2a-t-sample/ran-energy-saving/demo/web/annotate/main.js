// Expectation annotation console.
//
// The page annotates one case expectation at a time, but its unit of evidence is the whole corpus
// window: candidates come from every stored tester batch, not from the batch this browser started.
// Pure helpers are exported so the Node tests can drive them without a DOM.

const SLOT_DIMENSION = "slot_errors";
const MESSAGE_DIMENSION = "messages";

const STATUS_LABELS = {
  pending: "未采集",
  draft: "草稿",
  confirmed: "已确认",
  applied: "已应用",
};

const state = {
  meta: null,
  targets: [],
  selected: null,
  detail: null,
  scan: null,
  diff: null,
  applyResult: null,
  busy: false,
};

export function statusLabel(status) {
  if (!status) return STATUS_LABELS.pending;
  return STATUS_LABELS[status] || String(status);
}

export function progressText(progress) {
  if (!progress || !progress.counts) return "";
  const counts = progress.counts;
  return `目标 ${counts.total || 0} · 未采集 ${counts.pending || 0} · 草稿 ${counts.draft || 0} · `
    + `已确认 ${counts.confirmed || 0} · 已应用 ${counts.applied || 0}`;
}

export function filterTargets(targets, filters) {
  const query = String(filters.query || "").trim().toLowerCase();
  return targets.filter((target) => {
    if (filters.dimension && target.dimension !== filters.dimension) return false;
    if (filters.protocol && target.protocol !== filters.protocol) return false;
    if (filters.status && (target.status || "pending") !== filters.status) return false;
    if (!query) return true;
    return (
      String(target.caseId).includes(query)
      || String(target.caseName || "").toLowerCase().includes(query)
    );
  });
}

export function filterByRunRound(observations, runRound) {
  if (!runRound) return observations || [];
  const wanted = Number(runRound);
  return (observations || []).filter(
    (observation) => Number(observation.sourceRound || 1) === wanted,
  );
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
  if (!response.ok) {
    throw new Error(payload.error || `请求失败（HTTP ${response.status}）。`);
  }
  return payload;
}

function targetPath(target, suffix = "") {
  return `/api/annotate/target/${target.caseId}/${target.protocol}/${target.dimension}${suffix}`;
}

function onlyConsistent() {
  const node = document.getElementById("only-consistent");
  return node ? node.checked : true;
}

function visibleObservations(detail) {
  const rows = filterByRunRound(detail.observations || [], selectedRunRound());
  return onlyConsistent() ? rows.filter((observation) => observation.consistent) : rows;
}

function selectedRunRound() {
  const node = document.getElementById("filter-round");
  return node ? node.value : "";
}

function currentTarget() {
  return state.selected;
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

function setBusy(busy) {
  state.busy = busy;
  const button = document.getElementById("scan-button");
  if (button) button.disabled = busy;
}

async function loadTargets() {
  const payload = await getJson("/api/annotate/targets");
  state.targets = payload.targets || [];
  renderTargets(payload);
}

function renderTargets(progressPayload) {
  const list = document.getElementById("target-list");
  const count = document.getElementById("target-count");
  const filters = {
    dimension: document.getElementById("filter-dimension").value,
    protocol: document.getElementById("filter-protocol").value,
    status: document.getElementById("filter-status").value,
    query: document.getElementById("filter-query").value,
  };
  const rows = filterTargets(state.targets, filters);
  count.textContent = `${rows.length} / ${state.targets.length}`;
  if (progressPayload) {
    document.getElementById("progress").textContent = progressText(progressPayload);
  }
  list.replaceChildren();
  if (!rows.length) {
    list.append(el("p", "empty-state", "没有匹配的标注目标。"));
    return;
  }
  for (const target of rows) {
    const button = el("button", "target");
    if (
      state.selected
      && state.selected.caseId === target.caseId
      && state.selected.protocol === target.protocol
      && state.selected.dimension === target.dimension
    ) {
      button.classList.add("active");
    }
    button.type = "button";
    const left = el("span", "meta");
    left.append(el("span", "id", `#${target.caseId}`));
    left.append(
      el(
        "span",
        "name",
        `${target.protocol === "a2a-t" ? "A2A-T" : "A2A"} · `
          + (target.dimension === SLOT_DIMENSION ? "拦截" : `协商 ${target.expectedRounds} 轮`),
      ),
    );
    const right = el("span", "meta");
    right.append(el("span", "muted", `${target.accepted || 0}/${target.observations || 0}`));
    const badge = el("span", `badge ${target.status || "pending"}`, statusLabel(target.status));
    right.append(badge);
    button.append(left, right);
    button.addEventListener("click", () => openTarget(target));
    list.append(button);
  }
}

async function openTarget(target) {
  state.selected = target;
  state.detail = null;
  renderTargets();
  const container = document.getElementById("detail");
  container.replaceChildren(el("section", "card", "加载中…"));
  try {
    state.detail = await getJson(targetPath(target));
  } catch (error) {
    container.replaceChildren(el("section", "card notice error", error.message));
    return;
  }
  renderDetail();
}

function renderDetail() {
  const container = document.getElementById("detail");
  container.replaceChildren();
  const detail = state.detail;
  if (!detail) return;
  container.append(renderHeader(detail));
  container.append(renderCurrent(detail));
  if (detail.target && detail.target.status === "applied") {
    container.append(
      el("section", "card notice", "该目标已应用；再次确认会生成新的补丁并覆盖用例中的期望。"),
    );
  }
  container.append(
    detail.target && detail.target.dimension === SLOT_DIMENSION
      ? renderSlotPanel(detail)
      : renderMessagePanel(detail),
  );
  container.append(renderConfirmPanel(detail));
  container.append(renderApplyPanel());
}

function renderHeader(detail) {
  const header = el("section", "card");
  const title = el("div", "section-title");
  const name = el(
    "h2",
    "",
    `#${detail.case.id} ${detail.case.name} · `
      + (detail.target.dimension === SLOT_DIMENSION ? "拦截 slot_errors" : "协商报文"),
  );
  title.append(name);
  title.append(
    el(
      "span",
      `badge ${detail.target.status || "pending"}`,
      statusLabel(detail.target.status),
    ),
  );
  header.append(title);
  const facts = el("dl", "kv");
  const rows = [
    ["操作", detail.case.operation],
    ["维度", detail.case.dimension],
    ["协议", detail.target.protocol],
    ["预期校验", detail.case.validate],
    ["预期轮次", detail.case.expectedRounds],
    ["预期类型", (detail.case.kinds || []).join(", ") || "—"],
  ];
  for (const [label, value] of rows) {
    facts.append(el("dt", "", label));
    facts.append(el("dd", "", textBlock(value)));
  }
  header.append(facts);
  header.append(el("p", "sub", "原始请求"));
  header.append(el("pre", "mono", String(detail.case.userInput || "")));
  return header;
}

function renderCurrent(detail) {
  const card = el("section", "card");
  card.append(el("p", "sub", "用例现有期望"));
  card.append(
    el(
      "pre",
      "mono",
      detail.current === null || detail.current === undefined
        ? "（用例当前没有该期望，只按校验结论/轮次判定）"
        : textBlock(detail.current),
    ),
  );
  return card;
}

function renderSlotPanel(detail) {
  const card = el("section", "card");
  const head = el("div", "section-title");
  head.append(el("h2", "", "候选 slot_errors"));
  const mode = document.createElement("select");
  for (const value of ["any", "cover"]) {
    const option = document.createElement("option");
    option.value = value;
    option.textContent = value === "any" ? "any（任一命中）" : "cover（全部覆盖）";
    mode.append(option);
  }
  mode.value = detail.target.mode || "any";
  mode.addEventListener("change", async () => {
    await postJson(targetPath(detail.target, "/mode"), { mode: mode.value });
    await refresh();
  });
  head.append(mode);
  card.append(head);
  card.append(
    el(
      "p",
      "sub",
      "只有与用例预期一致（预期拦截且实测拦截）的候选可以采纳；关键词命中槽位名、说明或事实值中的任意一个即可。",
    ),
  );

  const accepted = new Map(
    (detail.choices || []).filter((choice) => choice.accepted).map((choice) => [
      choice.observationId,
      choice,
    ]),
  );
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

  const observations = visibleObservations(detail);
  if (!observations.length) {
    const row = document.createElement("tr");
    const cell = el("td", "empty-state", "该时间窗内没有观测到 slot 错误。");
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
      await postJson(targetPath(detail.target, "/choice"), {
        observationId: observation.id,
        accepted: checkbox.checked,
        editedText: keywords.value || null,
      });
      await refresh();
    };
    checkbox.addEventListener("change", save);
    keywords.addEventListener("change", () => {
      if (checkbox.checked) save();
    });
    const first = document.createElement("td");
    first.append(checkbox);
    row.append(
      first,
      el("td", "mono-cell", observation.code || ""),
      el("td", "mono-cell", observation.slotName || ""),
      el("td", "mono-cell", JSON.stringify(observation.facts || {})),
      el("td", "", observation.message || ""),
      el("td", "mono-cell", String(observation.seenCount || 1)),
      el("td", "mono-cell", `${observation.sourceBatch || ""} r${observation.sourceRound || 1}`),
      (() => {
        const cell = document.createElement("td");
        cell.append(keywords);
        return cell;
      })(),
    );
    tbody.append(row);
  }
  table.append(tbody);
  card.append(table);
  card.append(renderPending(detail));
  return card;
}

function renderMessagePanel(detail) {
  const card = el("section", "card");
  card.append(el("h2", "", "候选协商报文"));
  card.append(
    el(
      "p",
      "sub",
      "按协商轮次分组；同一轮可采纳多个备选（任意一个命中即通过）。未观测到该轮的用例不会出现在这里。",
    ),
  );
  const accepted = new Map(
    (detail.choices || []).filter((choice) => choice.accepted).map((choice) => [
      choice.observationId,
      choice,
    ]),
  );
  const groups = groupByRound(visibleObservations(detail));
  if (!groups.length) {
    card.append(el("p", "empty-state", "该时间窗内没有观测到服务端协商报文。"));
    card.append(renderPending(detail));
    return card;
  }
  for (const [round, observations] of groups) {
    const group = el("div", "round-group");
    const head = el("div", "round-head");
    head.append(el("strong", "", `第 ${round} 轮`));
    head.append(
      el(
        "span",
        "summary",
        `${observations.length} 个变体 · 采纳 ${observations.filter((item) => accepted.has(item.id)).length}`,
      ),
    );
    group.append(head);
    for (const observation of observations) {
      const candidate = el("div", "candidate");
      const candidateHead = el("div", "candidate-head");
      const checkbox = document.createElement("input");
      checkbox.type = "checkbox";
      checkbox.disabled = !observation.consistent;
      checkbox.checked = accepted.has(observation.id);
      const label = el("label", "", "采纳");
      label.prepend(checkbox);
      checkbox.addEventListener("change", async () => {
        await postJson(targetPath(detail.target, "/choice"), {
          observationId: observation.id,
          accepted: checkbox.checked,
          editedText: null,
        });
        await refresh();
      });
      candidateHead.append(label);
      candidateHead.append(
        el("span", "source", `${observation.kind || "unknown"} · ${observation.sourceBatch} r${observation.sourceRound}`),
      );
      candidateHead.append(el("span", "summary", `出现 ${observation.seenCount || 1} 次`));
      if (!observation.consistent) {
        candidateHead.append(el("span", "badge", "与预期不一致"));
      }
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
        await postJson(targetPath(detail.target, "/choice"), {
          observationId: observation.id,
          accepted: true,
          editedText: editor.value,
        });
        await refresh();
      });
      candidate.append(editor);
      group.append(candidate);
    }
    card.append(group);
  }
  card.append(renderPending(detail));
  return card;
}

function renderPending(detail) {
  const block = el("div", "");
  block.append(el("p", "sub", "采纳后的期望值（确认后才会写入用例）"));
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
  const card = el("section", "card");
  const head = el("div", "section-title");
  head.append(el("h2", "", "确认"));
  head.append(
    el(
      "span",
      "summary",
      detail.target.confirmedBy
        ? `${detail.target.confirmedBy} · ${detail.target.confirmedAt || ""}`
        : "尚未确认",
    ),
  );
  card.append(head);
  const note = document.createElement("textarea");
  note.placeholder = "备注（例如：依据 11-15 三批 live 的多数渲染）";
  note.value = detail.target.note || "";
  note.style.width = "100%";
  note.style.minHeight = "60px";
  card.append(note);
  const actions = el("div", "actions");
  const saveNote = el("button", "secondary", "保存备注");
  saveNote.addEventListener("click", async () => {
    await postJson(targetPath(detail.target, "/note"), { note: note.value });
    await refresh();
  });
  const confirm = el("button", "primary", "确认该目标");
  confirm.disabled = !(detail.choices || []).some((choice) => choice.accepted);
  confirm.addEventListener("click", async () => {
    await postJson(targetPath(detail.target, "/confirm"), {});
    await refresh();
  });
  actions.append(saveNote, confirm);
  card.append(actions);
  return card;
}

function renderApplyPanel() {
  const card = el("section", "card");
  const head = el("div", "section-title");
  head.append(el("h2", "", "生成用例期望"));
  head.append(
    el(
      "span",
      "summary",
      state.meta && state.meta.casesAllPresent ? "写入 docs-local/cases-all.yaml 并重生成" : "缺少 cases-all.yaml，只能导出标注数据",
    ),
  );
  card.append(head);
  card.append(
    el(
      "p",
      "sub",
      "只写入已确认的目标；应用前会先给出补丁预览，应用时先备份、重生成用例文件，再刷新用例目录。",
    ),
  );
  const actions = el("div", "actions");
  const previewButton = el("button", "secondary", "预览补丁");
  previewButton.addEventListener("click", async () => {
    const payload = await getJson("/api/annotate/draft");
    state.diff = payload.diff || "";
    state.applyResult = payload;
    renderDetail();
  });
  const applyButton = el("button", "primary", "应用");
  applyButton.addEventListener("click", async () => {
    if (!window.confirm("将写入 docs-local/cases-all.yaml 并重生成 eval/osp/cases，继续？")) return;
    const payload = await postJson("/api/annotate/apply", {});
    state.applyResult = payload;
    state.diff = payload.diff || "";
    await loadTargets();
    renderDetail();
  });
  const exportYaml = el("a", "", "导出标注数据（YAML）");
  exportYaml.href = "/api/annotate/export.yaml";
  const exportJson = el("a", "", "导出 JSON");
  exportJson.href = "/api/annotate/export.json";
  actions.append(previewButton, applyButton, exportYaml, exportJson);
  card.append(actions);
  if (state.applyResult) {
    const result = state.applyResult;
    const message = el(
      "p",
      result.ok ? "sub" : "notice error",
      [result.message, ...(result.errors || [])].filter(Boolean).join(" · "),
    );
    card.append(message);
  }
  if (state.diff) card.append(renderDiff(state.diff));
  return card;
}

function renderDiff(text) {
  const pre = el("pre", "diff");
  for (const line of String(text).split("\n")) {
    const span = document.createElement("span");
    span.className = line.startsWith("+") && !line.startsWith("+++")
      ? "add"
      : line.startsWith("-") && !line.startsWith("---")
        ? "del"
        : line.startsWith("@@")
          ? "hunk"
          : "";
    span.textContent = `${line}\n`;
    pre.append(span);
  }
  return pre;
}

async function postJson(path, body) {
  return getJson(path, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body || {}),
  });
}

async function refresh() {
  await loadTargets();
  if (!state.selected) return;
  state.detail = await getJson(targetPath(state.selected));
  renderDetail();
}

async function scan() {
  setBusy(true);
  showNotice("");
  try {
    const days = Number(document.getElementById("config-days").value) || 1;
    const payload = await postJson(`/api/annotate/scan?days=${encodeURIComponent(days)}`, {});
    state.scan = payload.scan;
    await loadTargets(payload);
    const scanInfo = payload.scan || {};
    const skipped = scanInfo.skipped || {};
    const skippedText = Object.entries(skipped)
      .map(([reason, count]) => `${reason}=${count}`)
      .join("，");
    showNotice(
      `扫描 ${scanInfo.batches || 0} 个批次，得到 ${scanInfo.observations || 0} 条候选`
        + `（新增 ${scanInfo.inserted || 0}）。窗口 ${scanInfo.windowStart} → ${scanInfo.windowEnd}`
        + (skippedText ? `；跳过 ${skippedText}` : "")
        + (scanInfo.truncated ? "；已达批次上限，较早的批次未纳入。" : ""),
      Boolean(scanInfo.truncated),
    );
  } catch (error) {
    showNotice(error.message, true);
  } finally {
    setBusy(false);
  }
}

function wireEvents() {
  document.getElementById("scan-button").addEventListener("click", scan);
  for (const id of ["filter-dimension", "filter-protocol", "filter-status", "filter-query"]) {
    document.getElementById(id).addEventListener("input", () => renderTargets());
  }
  document.getElementById("filter-round").addEventListener("input", () => {
    if (state.detail) renderDetail();
  });
  document.getElementById("only-consistent").addEventListener("input", () => {
    if (state.detail) renderDetail();
  });
}

async function init() {
  wireEvents();
  try {
    state.meta = await getJson("/api/annotate/meta");
    if (!state.meta.casesAllPresent) {
      showNotice(
        "未找到 docs-local/cases-all.yaml：可以采集与导出标注数据，但无法应用。",
        true,
      );
    }
    await loadTargets();
  } catch (error) {
    showNotice(error.message, true);
  }
}

if (typeof document !== "undefined") {
  init();
}
