/** Run Explorer state: SSE frame ingestion, dedupe, phase grouping and selectors.
 *
 * The store is the single source of truth for the rendered page. It never mutates the frames the
 * backend sent: every view model is derived. Frames are deduplicated by their global ``seq``, so a
 * reconnect or a full replay after a reload never duplicates or reorders a row.
 */

import { numericTokens, shortId } from "./format.js";
import { t, translateTraceTitle } from "./i18n/index.js";

/** The base Run Explorer phases, in display order. Labels resolve through ``i18n.t``. */
export const PHASES = [
  { key: "dispatch" },
  { key: "task_prompt" },
  { key: "task_validation" },
  { key: "negotiation" },
  { key: "resolution" },
  { key: "execution" },
  { key: "report" },
  { key: "done" },
];

/** Optional phases: only shown when the run actually produced rows for them. */
export const OPTIONAL_PHASES = [{ key: "task_modify" }];

/** Phase key → one-based display index. The optional Modify phase sits before ``done``. */
export const PHASE_INDEX = (() => {
  const index = {};
  let next = 1;
  for (const phase of PHASES) {
    if (phase.key === "done") index.task_modify = next++;
    index[phase.key] = next++;
  }
  return index;
})();

/** Return the human label of one terminal outcome. */
export function finalStateLabel(state) {
  if (typeof state !== "string" || !state) return null;
  const key = `finalState.${state}`;
  const text = t(key);
  return text === key ? state : text;
}

const DECISION_ACTIONS = new Set(["accept", "reject", "abort", "counter"]);
const TERMINAL_ACTIONS = new Set(["accept", "reject", "abort"]);

/** Stable view-model key of one timeline item. */
export function itemKey(item) {
  return item.spanId ? `span:${item.spanId}` : `seq:${item.seq}`;
}

/** Whether one item is an LLM span. */
export function isLlm(item) {
  return item.kind === "llm";
}

/** Whether one item is a tool span. */
export function isTool(item) {
  return item.kind === "tool";
}

/** Whether one item is a provider/human decision row. */
export function isDecision(item) {
  return item.entryType === "decision" || DECISION_ACTIONS.has(item.kind) && item.source === "event";
}

/** Whether one item carries an error signal. */
export function hasError(item) {
  if (item.status === "error") return true;
  if (item.error) return true;
  const response = item.detail?.response;
  return Boolean(response?.parseError || response?.errorMessage || response?.errorCode);
}

/** Run Explorer state container. */
export class RunStore extends EventTarget {
  constructor() {
    super();
    this.reset();
  }

  /** Clear every run-specific value (a new run or an initial load). */
  reset() {
    this.frames = new Map();
    this.runId = null;
    this.meta = null;
    this.snapshot = null;
    this.items = [];
    this.itemsByKey = new Map();
    this.itemsBySpan = new Map();
    this.childrenByKey = new Map();
    this.selectedKey = null;
    this.status = "idle";
    this.error = null;
    this.decisionRequest = null;
    this.pendingDecision = false;
    this._derived = null;
  }

  /** Attach process metadata without rebuilding the timeline. */
  setMeta(meta) {
    this.meta = meta;
    this.emit("meta", meta);
  }

  /** Start a fresh run scope. */
  beginRun(runId, meta = this.meta) {
    this.reset();
    this.runId = runId;
    this.meta = meta;
    this.status = "running";
    this.emit("change", { reason: "begin" });
  }

  /** Merge the snapshot document of the current run (ids, status, report). */
  applySnapshot(snapshot) {
    if (!snapshot || typeof snapshot !== "object") return;
    this.snapshot = snapshot;
    this.runId = snapshot.runId || this.runId;
    if (snapshot.status) this.status = snapshot.status;
    this._rebuild();
    this.emit("change", { reason: "snapshot" });
  }

  /** Merge spans of one ``trace.json`` document (late server batch after the stream closed).
   *
   * When the UI's grace window ends before the server posts its final batch, those spans are still
   * in ``/trace.json``. Merging by ``seq`` *and* ``spanId`` keeps a replay or a second merge from
   * duplicating a row. Returns the number of newly ingested spans.
   */
  mergeSpans(spans) {
    if (!Array.isArray(spans) || spans.length === 0) return 0;
    const knownSeq = new Set(this.frames.keys());
    const knownSpans = new Set();
    for (const existing of this.frames.values()) {
      if (existing.type === "trace" && existing.event?.spanId) knownSpans.add(existing.event.spanId);
    }
    let merged = 0;
    for (const span of spans) {
      if (!span || typeof span !== "object") continue;
      const spanId = typeof span.spanId === "string" ? span.spanId : null;
      if (spanId !== null && knownSpans.has(spanId)) continue;
      let seq = Number.isInteger(span.seq) ? span.seq : null;
      if (seq !== null && knownSeq.has(seq)) continue;
      if (seq === null) {
        seq = this.frames.size === 0 ? 1 : Math.max(...this.frames.keys()) + 1;
      }
      this.frames.set(seq, { type: "trace", event: { ...span, seq }, seq });
      knownSeq.add(seq);
      if (spanId !== null) knownSpans.add(spanId);
      merged += 1;
    }
    if (merged > 0) {
      this._rebuild();
      this.emit("change", { reason: "merge", merged });
    }
    return merged;
  }

  /** Ingest one SSE frame. Returns ``true`` when the frame was new. */
  applyFrame(frame) {
    if (!frame || typeof frame !== "object" || typeof frame.type !== "string") return false;
    const seq = Number.isInteger(frame.seq) ? frame.seq : null;
    if (seq !== null) {
      if (this.frames.has(seq)) return false;
      this.frames.set(seq, frame);
    }
    this._ingestMeta(frame);
    this._rebuild();
    this.emit("change", { reason: "frame", frame, seq });
    return true;
  }

  /** Return whether one ``seq`` was already ingested. */
  hasSeq(seq) {
    return this.frames.has(seq);
  }

  /** Return the number of ingested frames. */
  get frameCount() {
    return this.frames.size;
  }

  /** Return the last ingested SSE frame, ``null`` before the first one. */
  get lastFrame() {
    if (this.frames.size === 0) return null;
    const max = Math.max(...this.frames.keys());
    return this.frames.get(max) || null;
  }

  /** Return the current run status (``idle`` / ``running`` / ``finished`` / ``failed``). */
  get running() {
    return this.status === "running";
  }

  /** Return one item by its view-model key. */
  getItem(key) {
    return this.itemsByKey.get(key) || null;
  }

  /** Return one item by span id. */
  getItemBySpan(spanId) {
    return this.itemsBySpan.get(spanId) || null;
  }

  /** Return the numbered items in ``seq`` order. */
  list() {
    return this.items;
  }

  /** Select one item; emits a ``selection`` event. */
  select(key) {
    if (key !== null && !this.itemsByKey.has(key)) return;
    if (this.selectedKey === key) return;
    this.selectedKey = key;
    this.emit("selection", { key, item: key ? this.getItem(key) : null });
  }

  /** Select the item ``offset`` positions away from the current selection. */
  selectByOffset(offset, keys = this.items.map(itemKey)) {
    if (keys.length === 0) return;
    const current = this.selectedKey ? keys.indexOf(this.selectedKey) : -1;
    const next = Math.min(keys.length - 1, Math.max(0, current + offset));
    this.select(keys[next]);
  }

  /** Return the index of the selected item inside one key list, ``-1`` when absent. */
  selectedIndex(keys = this.items.map(itemKey)) {
    return this.selectedKey ? keys.indexOf(this.selectedKey) : -1;
  }

  /** Return the merged detail payload of one item (remote span when fetched). */
  detailOf(item) {
    if (!item) return null;
    return item.remoteSpan?.detail ?? item.detail ?? item.remoteSpan ?? null;
  }

  /** Merge one lazily fetched span into its timeline item. */
  mergeSpan(spanId, span) {
    const item = this.itemsBySpan.get(spanId);
    if (!item || !span) return false;
    item.remoteSpan = span;
    item.detailLoaded = true;
    if (!item.detail && span.detail) item.detail = span.detail;
    if (!item.startedAt && span.startedAt) item.startedAt = span.startedAt;
    if (!item.endedAt && span.endedAt) item.endedAt = span.endedAt;
    if (item.durationMs === null && typeof span.durationMs === "number") item.durationMs = span.durationMs;
    if (!item.model && span.model) item.model = span.model;
    if (!item.tokens && span.tokens) item.tokens = span.tokens;
    if (item.taskId === null && span.taskId) item.taskId = span.taskId;
    if (item.contextId === null && span.contextId) item.contextId = span.contextId;
    this.emit("payload", { spanId, item });
    return true;
  }

  /** Return the current connection/run status used by the header pill. */
  get runStatus() {
    return this.status;
  }

  /** Return the run metadata gathered from ``run_started`` or the snapshot. */
  runMeta() {
    const start = this.runStarted;
    const meta = this.meta || {};
    return {
      runId: this.runId,
      scenario: start?.scenario ?? this.snapshot?.scenario ?? null,
      description: start?.description ?? null,
      userRequest: start?.userRequest ?? null,
      language: start?.language ?? meta.language ?? null,
      mode: start?.mode ?? this.snapshot?.mode ?? null,
      transport: start?.transport ?? this.snapshot?.transport ?? null,
      discovery: start?.discovery ?? this.snapshot?.discovery ?? null,
      modelMode: start?.modelMode ?? meta.modelMode ?? null,
      modelLabel: start?.modelLabel ?? meta.modelLabel ?? null,
      expectedSequence: Array.isArray(start?.expectedSequence) ? start.expectedSequence : [],
    };
  }

  /** Return the run's context and task ids, when one is known. */
  ids() {
    const fromItems = this.items.find((item) => item.contextId) || null;
    const taskFromItems = this.items.find((item) => item.taskId) || null;
    return {
      contextId: this.snapshot?.contextId ?? fromItems?.contextId ?? null,
      taskId: this.snapshot?.taskId ?? taskFromItems?.taskId ?? null,
    };
  }

  /** Return the run id of the start frame. */
  get runStarted() {
    for (const frame of this.frames.values()) {
      if (frame.type === "run_started") return frame;
    }
    return null;
  }

  /** Return the terminal frame, ``null`` while the run is active. */
  get finishedFrame() {
    for (const frame of this.frames.values()) {
      if (frame.type === "finished") return frame;
    }
    return null;
  }

  /** Return the derived view model (cached until the next frame). */
  derived() {
    if (this._derived) return this._derived;
    const runFinished = this.status === "finished" || this.status === "failed";
    const groupOf = (phase) => {
      const items = this.items.filter((item) => item.phase === phase.key);
      return { ...phase, items, stats: this._phaseStats(items), rounds: this._phaseRounds(items) };
    };
    const baseGroups = PHASES.map(groupOf);
    const optionalGroups = OPTIONAL_PHASES.map(groupOf);
    // The optional Modify phase sits before ``done`` and only appears when it has rows.
    const doneIndex = baseGroups.findIndex((group) => group.key === "done");
    const groups = [
      ...baseGroups.slice(0, doneIndex),
      ...optionalGroups.filter((group) => group.items.length > 0),
      ...baseGroups.slice(doneIndex),
    ];
    const lastPhaseWithItems = [...groups].reverse().find((group) => group.items.length > 0)?.key ?? null;
    const states = {};
    for (const group of [...baseGroups, ...optionalGroups]) {
      states[group.key] = this._phaseState(group, { runFinished, lastPhaseWithItems });
    }
    this._derived = {
      groups,
      phaseStates: states,
      rounds: this._allRounds(),
      analysis: this._analysis(),
      decisionLog: this._decisionLog(),
      anomalies: this._anomalies(),
      activity: this._activity(),
    };
    return this._derived;
  }

  /** Emit one custom event. */
  emit(type, detail) {
    this.dispatchEvent(new CustomEvent(type, { detail }));
  }

  /** Ingest the run-level metadata carried by one frame. */
  _ingestMeta(frame) {
    if (frame.type === "run_started") {
      this.runId = frame.runId || this.runId;
      this.status = "running";
    } else if (frame.type === "finished") {
      this.status = frame.status === "failed" ? "failed" : "finished";
      this.error = frame.error || null;
    } else if (frame.type === "decision_requested") {
      this.decisionRequest = frame;
    }
  }

  /** Rebuild the sorted, numbered timeline and every index. */
  _rebuild() {
    this._derived = null;
    const built = [];
    for (const frame of this.frames.values()) {
      const item = this._itemFromFrame(frame);
      if (item) built.push(item);
    }
    built.sort((left, right) => left.seq - right.seq);
    const seenKeys = new Set();
    this.items = [];
    for (const item of built) {
      let key = itemKey(item);
      if (seenKeys.has(key)) key = `seq:${item.seq}`;
      seenKeys.add(key);
      item.key = key;
      item.number = this.items.length + 1;
      this.items.push(item);
    }
    this.itemsByKey = new Map(this.items.map((item) => [item.key, item]));
    this.itemsBySpan = new Map();
    for (const item of this.items) {
      if (item.spanId && !this.itemsBySpan.has(item.spanId)) this.itemsBySpan.set(item.spanId, item);
    }
    this.childrenByKey = new Map();
    for (const item of this.items) {
      if (!item.parentSpanId) continue;
      const parent = this.itemsBySpan.get(item.parentSpanId);
      if (!parent) continue;
      const children = this.childrenByKey.get(parent.key) || [];
      children.push(item);
      this.childrenByKey.set(parent.key, children);
    }
    this._resolvePendingDecision();
    if (this.selectedKey && !this.itemsByKey.has(this.selectedKey)) this.selectedKey = null;
  }

  /** Decide whether the latest manual request is still awaiting an answer. */
  _resolvePendingDecision() {
    const request = this.decisionRequest;
    if (!request || this.status !== "running") {
      this.pendingDecision = false;
      return;
    }
    const answered = this.items.some(
      (item) => item.entryType === "decision" && item.seq > request.seq,
    );
    this.pendingDecision = !answered;
  }

  /** Build one normalized timeline item from one SSE frame. */
  _itemFromFrame(frame) {
    if (frame.type === "trace" && frame.event && typeof frame.event === "object") {
      return this._itemFromSpan(frame.event);
    }
    if (frame.type === "event" && frame.event && typeof frame.event === "object") {
      return this._itemFromEntry(frame.event);
    }
    if (frame.type === "run_started") {
      return {
        seq: frame.seq,
        spanId: frame.spanId || null,
        parentSpanId: null,
        source: "run_started",
        entryType: "run_started",
        phase: frame.phase || "dispatch",
        phaseStep: frame.phaseStep || 1,
        round: null,
        actor: "system",
        side: "client",
        kind: "run_started",
        stage: null,
        title: t("trace.runStarted"),
        summary: frame.description || "",
        purpose: frame.userRequest || null,
        rationale: null,
        status: "ok",
        durationMs: null,
        model: null,
        tokens: null,
        error: null,
        direction: "internal",
        contextId: null,
        taskId: null,
        templateUri: null,
        negotiationType: null,
        performative: null,
        startedAt: frame.startedAt || null,
        endedAt: null,
        detail: { runStarted: frame },
        payload: frame,
        detailLoaded: false,
        remoteSpan: null,
      };
    }
    if (frame.type === "finished") {
      const failed = frame.status === "failed";
      return {
        seq: frame.seq,
        spanId: frame.spanId || null,
        parentSpanId: null,
        source: "finished",
        entryType: "finished",
        phase: frame.phase || "done",
        phaseStep: frame.phaseStep || 1,
        round: null,
        actor: "system",
        side: "client",
        kind: "finished",
        stage: null,
        title: failed ? t("trace.runFailed") : t("trace.runFinished"),
        summary: frame.error || finalStateLabel(frame.finalState) || "",
        purpose: null,
        rationale: null,
        status: failed ? "error" : "ok",
        durationMs: typeof frame.durationMs === "number" ? frame.durationMs : null,
        model: null,
        tokens: null,
        error: frame.error || null,
        direction: "internal",
        contextId: null,
        taskId: null,
        templateUri: null,
        negotiationType: null,
        performative: null,
        // The backend stamps the terminal span start; dropping it left the Done phase without a
        // duration even though the frame carried one.
        startedAt: frame.startedAt || null,
        endedAt: null,
        detail: { finished: frame },
        payload: frame,
        detailLoaded: false,
        remoteSpan: null,
      };
    }
    return null;
  }

  /** Normalize one ``trace`` span frame. */
  _itemFromSpan(span) {
    return {
      seq: span.seq,
      spanId: span.spanId || null,
      parentSpanId: span.parentSpanId || null,
      source: "trace",
      entryType: "trace",
      phase: span.phase || "dispatch",
      phaseStep: span.phaseStep || 0,
      round: Number.isInteger(span.round) ? span.round : null,
      actor: span.actor || "system",
      side: span.side || null,
      kind: span.kind || "trace",
      stage: span.stage || null,
      title: translateTraceTitle(span.title || span.kind || "trace"),
      summary: span.purpose || "",
      purpose: span.purpose || null,
      rationale: null,
      status: span.status || "ok",
      durationMs: typeof span.durationMs === "number" ? span.durationMs : null,
      model: span.model || null,
      tokens: span.tokens || null,
      error: span.error || null,
      direction: span.direction || "internal",
      contextId: span.contextId || null,
      taskId: span.taskId || null,
      templateUri: null,
      negotiationType: null,
      performative: null,
      startedAt: span.startedAt || null,
      endedAt: span.endedAt || null,
      detail: span.detail || {},
      payload: span,
      detailLoaded: true,
      remoteSpan: null,
    };
  }

  /** Normalize one ``event`` transcript entry frame. */
  _itemFromEntry(entry) {
    const decision = entry.entryType === "decision";
    const mode = this.runMeta().mode;
    const actor = decision ? (mode === "manual" ? "human" : "client") : "server";
    const title = decision
      ? t("trace.decisionEntry", { action: entry.action || entry.kind || "decision" })
      : entry.summary || entry.kind || "event";
    return {
      seq: entry.seq,
      spanId: entry.spanId || null,
      parentSpanId: entry.parentSpanId || null,
      source: "event",
      entryType: entry.entryType || "event",
      phase: entry.phase || "negotiation",
      phaseStep: entry.phaseStep || 0,
      round: Number.isInteger(entry.round) ? entry.round : null,
      actor,
      side: decision ? "client" : "server",
      kind: entry.kind || entry.eventKind || "event",
      stage: null,
      title,
      summary: decision ? entry.rationale || "" : entry.summary || "",
      purpose: null,
      rationale: entry.rationale || null,
      status: entry.status || "ok",
      durationMs: typeof entry.durationMs === "number" ? entry.durationMs : null,
      model: null,
      tokens: null,
      error: null,
      direction: entry.direction || (decision ? "client->server" : "server->client"),
      contextId: entry.contextId || null,
      taskId: null,
      templateUri: entry.templateUri || null,
      negotiationType: entry.negotiationType || null,
      performative: entry.performative || null,
      action: entry.action || null,
      filledParams: entry.filledParams || null,
      counterProposal: entry.counterProposal || null,
      counterParams: entry.counterParams || null,
      confirmedIntent: entry.confirmedIntent || null,
      feasibilitySummary: entry.feasibilitySummary || null,
      maxRounds: entry.maxRounds || null,
      startedAt: entry.ts || null,
      endedAt: null,
      detail: entry.detail || null,
      payload: entry,
      detailLoaded: false,
      remoteSpan: null,
    };
  }

  /** Compute the stats of one phase. */
  _phaseStats(items) {
    const llm = items.filter(isLlm).length;
    const tools = items.filter(isTool).length;
    const errors = items.filter(hasError).length;
    return {
      steps: items.length,
      llm,
      tools,
      errors,
      durationMs: this._groupDuration(items),
      rounds: [...new Set(items.map((item) => item.round).filter((round) => Number.isInteger(round)))],
    };
  }

  /** Compute the wall-clock span of one phase (first start → last end/start). */
  _groupDuration(items) {
    const starts = [];
    const ends = [];
    for (const item of items) {
      const start = Date.parse(item.startedAt || "");
      if (!Number.isNaN(start)) starts.push(start);
      const end = Date.parse(item.endedAt || item.startedAt || "");
      if (!Number.isNaN(end)) ends.push(end);
    }
    if (starts.length >= 1 && ends.length >= 1) {
      const span = Math.max(...ends) - Math.min(...starts);
      if (span >= 0) return span;
    }
    const summed = items.reduce(
      (total, item) => total + (typeof item.durationMs === "number" ? item.durationMs : 0),
      0,
    );
    return summed > 0 ? summed : null;
  }

  /** Compute the round chips of the negotiation phase. */
  _phaseRounds(items) {
    const rounds = [...new Set(items.map((item) => item.round).filter((round) => Number.isInteger(round)))];
    return rounds.sort((left, right) => left - right).map((round) => ({
      round,
      hasDecision: items.some((item) => item.round === round && item.entryType === "decision"),
      terminal: items.some(
        (item) => item.round === round && item.entryType === "decision" && TERMINAL_ACTIONS.has(item.action),
      ),
    }));
  }

  /** Return every round seen anywhere in the run, with its chip state. */
  _allRounds() {
    const maxRound = this._maxRound();
    const byRound = new Map();
    for (const item of this.items) {
      if (!Number.isInteger(item.round)) continue;
      const entry = byRound.get(item.round) || { round: item.round, terminal: false, decision: false };
      if (item.entryType === "decision") {
        entry.decision = true;
        if (TERMINAL_ACTIONS.has(item.action)) entry.terminal = true;
      }
      byRound.set(item.round, entry);
    }
    return [...byRound.values()]
      .sort((left, right) => left.round - right.round)
      .map((entry) => ({
        ...entry,
        state: entry.terminal
          ? "done"
          : this.status === "running" && entry.round === maxRound
            ? "active"
            : "done",
      }));
  }

  /** Return the highest round of the run, ``null`` before the first negotiation row. */
  _maxRound() {
    const rounds = this.items
      .map((item) => item.round)
      .filter((round) => Number.isInteger(round));
    return rounds.length > 0 ? Math.max(...rounds) : null;
  }

  /** Return the state of one phase stepper entry. */
  _phaseState(group, { runFinished, lastPhaseWithItems }) {
    if (group.items.length === 0) {
      return runFinished ? "skipped" : "pending";
    }
    if (this.status === "failed" && group.items.some(hasError)) return "failed";
    if (!runFinished && group.key === lastPhaseWithItems) return "active";
    return "done";
  }

  /** Return why a phase was skipped, or ``null`` when the phase has rows or is still running. */
  phaseNote(key) {
    if (this.status !== "finished" && this.status !== "failed") return null;
    if (this.items.some((item) => item.phase === key)) return null;
    const finalState = this.finishedFrame?.finalState || this.snapshot?.finalState || null;
    if (key === "negotiation") {
      if (this.status === "failed") {
        return this.error
          ? t("phaseNote.negotiationFailedWithError", { error: this.error })
          : t("phaseNote.negotiationFailed");
      }
      if (finalState === "task_completed") {
        return t("phaseNote.negotiationNotNeeded");
      }
      if (finalState === "task_rejected") {
        return t("phaseNote.negotiationRejected");
      }
      return t("phaseNote.negotiationNone");
    }
    if (key === "resolution") {
      if (finalState === "no_task_negotiation_aborted") {
        return t("phaseNote.resolutionAborted");
      }
      if (finalState === "no_task_negotiation_ended") {
        return t("phaseNote.resolutionEnded");
      }
      return t("phaseNote.resolutionNone");
    }
    return t("phaseNote.noSteps");
  }

  /** Return the label shown next to the Resolution phase. */
  resolutionLabel() {
    const decisions = this.items.filter(
      (item) => item.phase === "resolution" && item.entryType === "decision",
    );
    const last = decisions[decisions.length - 1];
    if (last?.action) {
      const key = `resolution.${last.action}`;
      const text = t(key);
      return text === key ? last.action : text;
    }
    const state = this.finishedFrame?.finalState || this.snapshot?.finalState;
    if (state === "no_task_negotiation_aborted") return t("resolution.aborted");
    if (state === "no_task_negotiation_ended") return t("resolution.ended");
    return null;
  }

  /** Return the analysis model of the run. */
  _analysis() {
    const llmItems = this.items.filter(isLlm);
    const toolItems = this.items.filter(isTool);
    const offline = this.runMeta().modelMode === "offline";
    let prompt = 0;
    let completion = 0;
    let total = 0;
    let numeric = 0;
    let scripted = 0;
    let missing = 0;
    for (const item of llmItems) {
      const tokens = numericTokens(item.tokens);
      if (tokens === null) {
        // Only an offline (scripted) run may label absent usage as scripted; a live call without
        // usage metadata is simply missing, never fabricated.
        const labeled =
          offline || item.tokens?.scripted === true || typeof item.tokens?.label === "string";
        if (labeled) scripted += 1;
        else missing += 1;
        continue;
      }
      prompt += tokens.prompt;
      completion += tokens.completion;
      total += tokens.total;
      numeric += 1;
    }
    const decisions = this.items.filter((item) => item.entryType === "decision");
    const errors = this.items.filter(hasError);
    const parseFailures = this.items.filter((item) => item.detail?.response?.parseError);
    const retries = this.items.filter(
      (item) => item.detail?.retry === true || Number.isInteger(item.detail?.retryCount),
    );
    return {
      llmCalls: llmItems.length,
      scriptedCalls: scripted,
      missingCalls: missing,
      numericCalls: numeric,
      promptTokens: prompt,
      completionTokens: completion,
      totalTokens: total,
      tools: toolItems.length,
      toolErrors: toolItems.filter(hasError).length,
      errors: errors.length,
      parseFailures: parseFailures.length,
      retries: retries.length,
      decisions: decisions.length,
      steps: this.items.length,
    };
  }

  /** Return the decision log in causal order. */
  _decisionLog() {
    const modelMode = this.runMeta().modelMode;
    return this.items
      .filter((item) => item.entryType === "decision")
      .map((item) => ({
        key: item.key,
        seq: item.seq,
        round: item.round,
        action: item.action || item.kind,
        rationale: item.rationale || item.summary || "",
        source:
          item.actor === "human"
            ? t("decisionSource.human")
            : modelMode === "offline"
              ? t("decisionSource.scripted")
              : t("decisionSource.clientLlm"),
        actor: item.actor,
        ts: item.startedAt,
      }));
  }

  /** Return detected anomalies (never fabricated: each maps to a concrete row). */
  _anomalies() {
    const anomalies = [];
    for (const item of this.items) {
      if (item.status === "error" || item.error) {
        anomalies.push({ kind: "error", key: item.key, message: item.error || item.title });
      }
      const response = item.detail?.response;
      if (response?.parseError) {
        anomalies.push({
          kind: "parse",
          key: item.key,
          message: t("anomaly.parse", { error: response.parseError }),
        });
      }
      if (response?.errorMessage) {
        anomalies.push({ kind: "error", key: item.key, message: response.errorMessage });
      }
      if (isLlm(item) && !response?.parseError && !response?.errorMessage) {
        const text = typeof response?.text === "string" ? response.text : response?.rawContent;
        const calls = Array.isArray(response?.functionCalls) ? response.functionCalls.length : 0;
        if (typeof text === "string" && !text.trim() && calls === 0) {
          anomalies.push({ kind: "empty", key: item.key, message: t("anomaly.empty") });
        }
      }
      if (typeof item.durationMs === "number" && item.durationMs > 10000) {
        anomalies.push({
          kind: "slow",
          key: item.key,
          message: t("anomaly.slow", { ms: Math.round(item.durationMs) }),
        });
      }
      const tokens = numericTokens(item.tokens);
      if (tokens && tokens.prompt + tokens.completion !== tokens.total) {
        anomalies.push({
          kind: "tokens",
          key: item.key,
          message: t("anomaly.tokens"),
        });
      }
    }
    const maxRound = this._maxRound();
    const maxRounds = this._maxRounds();
    const accepted = this.items.some((item) => item.entryType === "decision" && item.action === "accept");
    if (maxRounds !== null && maxRound !== null && maxRound >= maxRounds && !accepted) {
      anomalies.push({
        kind: "max-rounds",
        key: null,
        message: t("anomaly.maxRounds", { max: maxRounds }),
      });
    }
    return anomalies;
  }

  /** Return the negotiation round budget, ``null`` when unknown. */
  _maxRounds() {
    const values = [
      this.decisionRequest?.maxRounds,
      ...this.items.map((item) => item.maxRounds),
    ].filter((value) => Number.isInteger(value));
    return values.length > 0 ? Math.max(...values) : null;
  }

  /** Return the one-line current activity of the run. */
  _activity() {
    const meta = this.runMeta();
    if (this.pendingDecision && this.decisionRequest) {
      return t("activity.waitingDecision", { round: this.decisionRequest.round ?? "?" });
    }
    if (this.status === "failed") return this.error || t("trace.runFailed");
    if (this.status === "finished") {
      const state = this.finishedFrame?.finalState || this.snapshot?.finalState;
      return finalStateLabel(state) || t("value.finished");
    }
    if (this.status === "running") {
      const last = this.items[this.items.length - 1];
      return last ? last.title : t("value.starting", { scenario: meta.scenario || "run" });
    }
    return t("value.idle");
  }

  /** Return the global token total of the header. */
  tokenTotal() {
    const analysis = this._analysis();
    if (analysis.llmCalls === 0) return null;
    if (analysis.numericCalls === 0) {
      if (analysis.scriptedCalls > 0) return { scripted: analysis.scriptedCalls };
      return { missing: analysis.missingCalls };
    }
    return {
      total: analysis.totalTokens,
      scripted: analysis.scriptedCalls,
      missing: analysis.missingCalls,
    };
  }

  /** Return a short run label for the header. */
  runLabel() {
    return this.runId ? shortId(this.runId) : null;
  }
}

/** Return the full label of one phase key. */
export function phaseLabel(key) {
  const text = t(`phase.${key}`);
  return text === `phase.${key}` ? key : text;
}

/** Return the compact label used by dense rows and duration bars. */
export function phaseShort(key) {
  const text = t(`phaseShort.${key}`);
  return text === `phaseShort.${key}` ? key : text;
}
