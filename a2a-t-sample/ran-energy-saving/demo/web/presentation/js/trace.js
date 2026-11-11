/** Session trace: the merged protocol events and spans of one run.
 *
 * Every row is clickable: it opens the message it carried in the middle panel and stays
 * highlighted. The list follows the newest row until the audience picks a row; after that it keeps
 * the selected row in view instead of jumping to the bottom on every frame.
 */

import { el } from "./dom.js";

const FOLLOW_SLACK_PX = 12;

/** Demo day bands; the trace row stage selects the active one. */
const STAGE_BAND = {
  request: 0,
  negotiation: 0,
  strategy: 0,
  collection: 1,
  mml: 2,
  report: 3,
};

export function createTrace(els, getCopy, onSelect, getShowCalls = () => true) {
  let selectedKey = null;
  let follow = true;
  let lastState = null;
  // Programmatic scrolls also fire ``scroll``; ignore them so a click near the bottom does not
  // silently re-enable following and yank the list away on the next frame.
  let quietUntil = 0;
  // Rows are inserted in event-time order: a late server span can belong before rows already on
  // screen. Track the full key list so an insertion rebuilds the small window, while a plain
  // append still updates the list incrementally.
  let renderedKeys = [];
  let selectedApplied = undefined;

  els.body.addEventListener("scroll", () => {
    if (Date.now() < quietUntil) return;
    follow = atBottom(els.body);
  });

  function scrollTo(value) {
    quietUntil = Date.now() + 150;
    els.body.scrollTop = value;
  }

  function render(state) {
    lastState = state;
    const copy = getCopy();
    // The session trace is evidence: never drop the first rows. An early request is exactly what
    // an operator opens the trace to inspect after a long negotiation or task poll.
    const source = state.trace;
    const rows = getShowCalls()
      ? source
      : source.filter((row) => row.tag !== "llm" && row.tag !== "nms");
    const keys = rows.map((row, index) => rowKey(row, index));
    renderDays(els, rows, copy);
    const appendOnly =
      renderedKeys.length <= keys.length &&
      renderedKeys.every((key, index) => key === keys[index]);
    if (!appendOnly) {
      // A rewind, a new run or a late span inserted before the visible rows. Rebuild the window,
      // but keep the reader's scroll position; following still goes to the newest row below.
      const previousScroll = els.body.scrollTop;
      els.rows.replaceChildren();
      renderedKeys = [];
      selectedApplied = undefined;
      els.body.scrollTop = Math.min(previousScroll, els.body.scrollHeight);
    }
    els.empty.hidden = rows.length > 0;
    let appended = 0;
    for (let index = renderedKeys.length; index < rows.length; index += 1) {
      const row = rows[index];
      const key = keys[index];
      els.rows.append(rowNode(row, copy, key, () => select(key, row)));
      appended += 1;
    }
    renderedKeys = keys;

    if (selectedApplied !== selectedKey) {
      for (const node of els.rows.children) {
        node.classList.toggle("sel", node.dataset.key === selectedKey);
      }
      selectedApplied = selectedKey;
    }
    if (follow && appended > 0) {
      scrollTo(els.body.scrollHeight);
    }
    const selected = selectNode(els.rows, selectedKey);
    if (selected !== null && appended > 0) {
      quietUntil = Date.now() + 150;
      keepVisible(els.body, selected);
    }
  }

  function select(key, row) {
    selectedKey = key;
    follow = false;
    onSelect(row);
    if (lastState !== null) render(lastState);
  }

  function reset() {
    selectedKey = null;
    follow = true;
    renderedKeys = [];
    selectedApplied = undefined;
    els.rows.replaceChildren();
  }

  return { render, reset };
}

/** Render the four demo day bands; the furthest stage reached is highlighted. */
function renderDays(els, rows, copy) {
  if (!els.days) return;
  const bands = Array.isArray(copy.trace.days) ? copy.trace.days : [];
  let active = -1;
  for (const row of rows) {
    const band = STAGE_BAND[row.stage];
    if (band !== undefined) active = Math.max(active, band);
  }
  const nodes = bands.map((band, index) => {
    const node = el("div", index === active ? "trace-day on" : "trace-day");
    node.append(el("strong", "", band.label || ""), el("small", "", band.sub || ""));
    return node;
  });
  els.days.replaceChildren(...nodes);
}

function rowNode(row, copy, key, open) {
  const node = el("li", "trace-row click");
  node.dataset.key = key;
  node.tabIndex = 0;
  node.setAttribute("role", "button");
  node.addEventListener("click", open);
  node.addEventListener("keydown", (event) => {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      open();
    }
  });

  const stageKey = typeof row.stage === "string" ? row.stage : "";
  const stageText = copy.trace.stages?.[stageKey] || "";
  const time = el("span", "w", shortTime(row.ts || row.entry?.ts || row.entry?.startedAt));
  const tag = el("span", `tag ${row.tag || "a2at"}`, copy.trace.tags[row.tag] || row.tag || "");
  const text = el("span", "t");
  const title = el("span", "title", (row.titleKey && copy.trace[row.titleKey]) || row.title || "");
  text.append(title);
  const dir = typeof row.dir === "string" ? row.dir : "";
  const answerSource = typeof row.answerSource === "string" ? row.answerSource : "";
  const sourceLabel = answerSource
    ? copy.trace.answerSources?.[answerSource] || answerSource
    : "";
  if (stageText || dir || sourceLabel || row.sub) {
    const small = el("small", "");
    if (stageText) small.append(el("span", "stage-meta", stageText));
    if (dir) {
      if (stageText) small.append(document.createTextNode(" · "));
      small.append(el("span", "dir", dir));
    }
    if (sourceLabel) {
      if (stageText || dir) small.append(document.createTextNode(" · "));
      small.append(el("span", "source", sourceLabel));
    }
    if (row.sub) {
      if (stageText || dir || sourceLabel) small.append(document.createTextNode(" · "));
      small.append(document.createTextNode(row.sub));
    }
    text.append(small);
  }
  const chip = el("span", row.chipKind ? `chip ${row.chipKind}` : "chip", row.chip || "");
  node.append(time, tag, text, chip);
  return node;
}

/** Stable identity of one row: its span/sequence when it has one, else its position. */
export function rowKey(row, index) {
  const entry = row.entry;
  if (entry) {
    if (entry.spanId) return `span:${entry.spanId}`;
    if (entry.seq !== undefined && entry.seq !== null) return `seq:${entry.seq}`;
  }
  if (row.seq !== undefined && row.seq !== null) return `seq:${row.seq}`;
  return `row:${index}`;
}

function selectNode(container, key) {
  if (key === null) return null;
  for (const node of container.children) {
    if (node.dataset.key === key) return node;
  }
  return null;
}

function atBottom(node) {
  return node.scrollTop + node.clientHeight >= node.scrollHeight - FOLLOW_SLACK_PX;
}

/** Keep one row inside the visible part of the trace body without moving the page. */
function keepVisible(view, node) {
  const rect = node.getBoundingClientRect();
  const frame = view.getBoundingClientRect();
  if (rect.top < frame.top) view.scrollTop += rect.top - frame.top;
  else if (rect.bottom > frame.bottom) view.scrollTop += rect.bottom - frame.bottom;
}

function shortTime(value) {
  if (typeof value !== "string" || value === "") return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value.slice(11, 19);
  return date.toLocaleTimeString(undefined, { hour12: false });
}

/** The entry the middle panel shows for one row: the real one, or a readable stand-in. */
export function entryOfRow(row) {
  if (row.entry) return row.entry;
  return {
    entryType: "trace",
    kind: row.tag || "trace",
    title: row.title || "",
    summary: row.sub || "",
    detail: {},
  };
}
