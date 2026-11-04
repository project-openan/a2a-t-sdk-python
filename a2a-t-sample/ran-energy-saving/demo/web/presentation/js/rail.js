/** Negotiation rail: the turns in business language and the message each turn carried. */

import { actionLabel, decisionLine, format, proposalLine } from "../copy.js";
import { el } from "./dom.js";
import { negotiationExpected, resolutionOf, roundInfo, stageOf } from "./state.js";

export function createRail(els, getCopy, getUi) {
  // The page renders on every frame; rebuilding the same list each time made the panel flash.
  // Only touch the DOM when the thing the panel shows actually changed.
  let dotsKey = null;
  let statusTextKey = null;
  let emptyTextKey = null;
  let turnsKey = null;

  function render(state) {
    const ui = getUi();
    const copy = getCopy();
    const { round, maxRounds } = roundInfo(state);
    const nextDotsKey = `${round}/${maxRounds}`;
    if (nextDotsKey !== dotsKey) {
      els.roundDots.replaceChildren();
      for (let index = 1; index <= maxRounds; index += 1) {
        const dot = el("span", "round-dot");
        dot.dataset.filled = String(index <= round);
        els.roundDots.append(dot);
      }
      dotsKey = nextDotsKey;
    }

    const status = statusText(state, copy);
    if (status !== statusTextKey) {
      els.status.textContent = status;
      statusTextKey = status;
    }
    const empty = emptyText(state, copy);
    if (empty !== emptyTextKey) {
      els.empty.textContent = empty;
      emptyTextKey = empty;
    }
    els.empty.hidden = state.negotiation.length > 0 || ui.tab === "wire";

    const business = ui.tab !== "wire";
    els.turns.hidden = !business;
    els.wire.hidden = business;
    if (!business) return;

    const nextTurnsKey = `${state.negotiation.length}|${ui.selectedTurn}|${copy.lang}`;
    if (nextTurnsKey === turnsKey) return;
    const scrollTop = els.turns.scrollTop;
    els.turns.replaceChildren();
    state.negotiation.forEach((turn, index) => {
      els.turns.append(turnNode(turn, index, ui, copy, state.scenario, select));
    });
    els.turns.scrollTop = scrollTop;
    turnsKey = nextTurnsKey;
  }

  function select(index) {
    const ui = getUi();
    ui.selectedTurn = ui.selectedTurn === index ? -1 : index;
    ui.wireEntry = null;
    render(ui.view);
    if (ui.selectedTurn >= 0 && ui.tab !== "wire") scrollTo(index);
  }

  function scrollTo(index) {
    const node = els.turns.children[index];
    if (!node) return;
    const top = node.offsetTop;
    const bottom = top + node.offsetHeight;
    if (top < els.turns.scrollTop || bottom > els.turns.scrollTop + els.turns.clientHeight) {
      els.turns.scrollTop = Math.max(bottom - els.turns.clientHeight, 0);
    }
  }

  function showEntry(entry) {
    const ui = getUi();
    ui.wireEntry = entry;
    ui.selectedTurn = -1;
    ui.tab = "wire";
    render(ui.view);
  }

  function renderWire(state, ui, copy) {
    const turn = selectedTurn(state, ui);
    const entry = turn || ui.wireEntry;
    if (!entry) {
      els.wireTitle.textContent = "";
      els.wireCode.textContent = copy.negotiation.wireEmpty;
      els.wireNote.hidden = true;
      return;
    }
    const kind = entry.negotiationType || entry.kind || "message";
    els.wireTitle.textContent = format(copy.negotiation.wireTitle, { kind });
    els.wireCode.textContent = wireText(entry, copy);
    els.wireNote.textContent = copy.negotiation.sent;
    els.wireNote.hidden = false;
  }

  return { render, select, showEntry, renderWire };
}

function selectedTurn(state, ui) {
  if (ui.selectedTurn >= 0 && state.negotiation[ui.selectedTurn]) {
    return state.negotiation[ui.selectedTurn];
  }
  return ui.wireEntry || null;
}

function turnNode(turn, index, ui, copy, scenario, select) {
  const node = el("li", "turn");
  node.dataset.side = turn.side;
  node.dataset.action = turn.action;
  node.dataset.expanded = String(index === ui.selectedTurn);

  const head = el("button", "turn-head");
  head.type = "button";
  head.setAttribute("aria-expanded", String(index === ui.selectedTurn));
  const meta = el("span", "turn-meta");
  meta.append(
    el("span", "turn-who", copy.who[turn.side] || ""),
    el("span", "turn-action", actionLabel(copy, turn.action)),
    el("span", "turn-round", turn.round ? copy.roundShort(turn.round) : ""),
  );
  head.append(meta, el("span", "turn-text", messageText(turn, copy, scenario)));
  head.addEventListener("click", () => select(index));

  const body = el("div", "turn-body");
  body.hidden = index !== ui.selectedTurn;
  for (const child of turnBody(turn, copy)) body.append(child);

  node.append(head, body);
  return node;
}

function turnBody(turn, copy) {
  const nodes = [];
  if (turn.confirmedBy === "engineer") {
    nodes.push(el("span", "badge-confirmed", copy.negotiation.confirmedBy));
  }
  if (turn.side === "nms") {
    if (turn.action === "end") {
      nodes.push(el("p", "content-line", copy.negotiation.endLine));
      return nodes;
    }
    if (turn.action === "abort") {
      nodes.push(el("p", "content-line", copy.negotiation.abortLine));
      return nodes;
    }
    const content = turn.content || turn.summary;
    if (content) {
      nodes.push(...renderContent(content));
      return nodes;
    }
    nodes.push(el("p", "content-line", messageText(turn, copy, "")));
    return nodes;
  }

  const detail = turn.detail || {};
  appendParams(nodes, copy.negotiation.filled, detail.filledParams);
  appendParams(nodes, copy.negotiation.counter, detail.counterParams);
  if (detail.counterProposal) {
    nodes.push(el("h5", "content-heading", copy.negotiation.counter));
    nodes.push(el("p", "content-line", detail.counterProposal));
  }
  if (nodes.length === 0) nodes.push(el("p", "content-line", copy.negotiation.sent));
  return nodes;
}

function appendParams(nodes, label, params) {
  if (params === null || typeof params !== "object" || Array.isArray(params)) return;
  const entries = Object.entries(params).filter(([, value]) => value !== null && value !== undefined);
  if (entries.length === 0) return;
  nodes.push(el("h5", "content-heading", label));
  const list = el("dl", "turn-params");
  for (const [key, value] of entries) {
    list.append(el("dt", "", key), el("dd", "", typeof value === "string" ? value : JSON.stringify(value)));
  }
  nodes.push(list);
}

function renderContent(text) {
  const nodes = [];
  for (const raw of String(text).split(/\r?\n/)) {
    const line = raw.replace(/\*\*/g, "").trim();
    if (!line) continue;
    if (line.startsWith("### ")) nodes.push(el("h5", "content-heading", line.slice(4)));
    else if (line.startsWith("## ")) nodes.push(el("h4", "content-heading", line.slice(3)));
    else if (line.startsWith("# ")) nodes.push(el("h3", "content-heading", line.slice(2)));
    else nodes.push(el("p", "content-line", line));
  }
  return nodes;
}

function messageText(turn, copy, scenario) {
  if (turn.side === "nms") {
    if (turn.action === "abort") return copy.negotiation.abortLine;
    if (turn.action === "end") return copy.negotiation.endLine;
    return proposalLine(copy, scenario, turn.round) || turn.summary || copy.negotiation.generic;
  }
  return decisionLine(copy, scenario, turn.round) || actionLabel(copy, turn.action);
}

function statusText(state, copy) {
  const stage = stageOf(state);
  const resolution = resolutionOf(state);
  const { round, maxRounds } = roundInfo(state);
  if (state.negotiation.length === 0 && !negotiationExpected(state) && stage !== "idle") {
    return copy.negotiation.notNeededShort;
  }
  if (
    state.negotiation.length === 0 &&
    (stage === "executing" || stage === "done")
  ) {
    return copy.negotiation.statusNotNeeded;
  }
  if (resolution === "rejected" || resolution === "aborted" || stage === "ended") {
    return copy.negotiation.statusEnded;
  }
  if (stage === "done" || stage === "executing" || resolution === "accepted") {
    return copy.negotiation.statusAgreed;
  }
  if (stage === "negotiating") {
    return maxRounds > 0
      ? format(copy.negotiation.statusNegotiating, {
          round: copy.round(Math.max(round, 1), maxRounds),
        })
      : copy.negotiation.statusWaiting;
  }
  if (stage === "canceled" || stage === "failed" || stage === "notApplied") {
    return copy.negotiation.statusEnded;
  }
  return copy.negotiation.statusIdle;
}

function emptyText(state, copy) {
  const stage = stageOf(state);
  if (state.negotiation.length === 0 && !negotiationExpected(state) && stage !== "idle") {
    return copy.negotiation.notNeededHint;
  }
  if (state.negotiation.length === 0 && (stage === "executing" || stage === "done")) {
    return copy.negotiation.notNeeded;
  }
  return stage === "idle" ? copy.negotiation.empty : copy.negotiation.waiting;
}

function wireText(entry, copy) {
  if (entry.side === "nms") {
    return entry.content || entry.summary || copy.negotiation.wireEmpty;
  }
  const detail = entry.detail || {};
  const payload = {
    action: entry.action,
    rationale: entry.rationale || entry.summary || "",
    filled_params: detail.filledParams ?? null,
    counter_proposal: detail.counterProposal ?? null,
    counter_params: detail.counterParams ?? null,
  };
  return JSON.stringify(payload, null, 2);
}
