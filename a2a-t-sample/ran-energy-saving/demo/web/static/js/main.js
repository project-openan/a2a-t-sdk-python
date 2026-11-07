/** Bootstrap of the RAN energy-saving Run Explorer.
 *
 * Wiring only: the store owns state, the views render it, and this module connects the DOM events,
 * the SSE stream, the manual decision form and the keyboard map.
 */

import { ApiClient } from "./api.js";
import { append, el, statusInfo } from "./dom.js";
import {
  EM_DASH,
  formatClock,
  formatCountdown,
  formatDuration,
  formatNumber,
  shortId,
  truncate,
} from "./format.js";
import { FilterState, FollowController, availableRounds, isAtBottom } from "./filters.js";
import {
  applyStatic,
  getLocale,
  getLocaleMode,
  initLocale,
  localeLabel,
  normalizeLocale,
  onLocaleChange,
  setLocaleMode,
  setProcessLanguage,
  t,
} from "./i18n/index.js";
import { Inspector, phaseDurationList, runElapsed } from "./inspector.js";
import { SHORTCUTS, installShortcuts } from "./shortcuts.js";
import { RunStore, itemKey } from "./store.js";
import { TimelineView } from "./timeline.js";

const $ = (id) => document.getElementById(id);

/** Copy ``?token=…`` into the UI cookie, then strip it from the address bar.
 *
 * The bearer middleware protects ``/api/*``; a same-origin cookie is carried by both fetch and
 * EventSource. The cookie name mirrors ``UI_TOKEN_COOKIE`` in shared/http_security.
 */
function installUiToken() {
  const params = new URLSearchParams(window.location.search);
  const token = params.get("token");
  if (!token) return;
  document.cookie = `a2at_ui_token=${encodeURIComponent(token)}; Path=/; SameSite=Strict`;
  params.delete("token");
  const query = params.toString();
  history.replaceState(
    null,
    "",
    `${location.pathname}${query ? `?${query}` : ""}${location.hash}`,
  );
}

/** Minimum delay before closing a finished run's stream: the server posts its last spans late. */
const DEFAULT_FINISHED_GRACE_MS = 1500;

const api = new ApiClient();
const store = new RunStore();
const filters = new FilterState();
const follow = new FollowController();
const app = {
  meta: null,
  runId: null,
  subscription: null,
  loading: true,
  startError: null,
  decisionSeq: null,
  decisionAt: null,
  decisionTimeout: null,
  decisionSubmitting: false,
  decisionTimedOut: false,
  inspectorOpen: true,
  theme: "auto",
  lastSnapshotAt: 0,
  disconnected: false,
  fatalStream: false,
  finalizing: false,
  finishTimer: null,
  pendingScrollKey: null,
  tasks: [],
  tasksEnabled: false,
  taskFetching: false,
  taskTimer: null,
  clientRuns: [],
  clientRunsEnabled: false,
  clientRunFetching: false,
  clientRunTimer: null,
  clientRunDetailId: null,
  conversations: [],
  conversationsEnabled: false,
  conversationFetching: false,
  conversationTimer: null,
  conversationDetailId: null,
  historyOpen: false,
  historyTab: "tasks",
};

const timeline = new TimelineView({
  timeline: $("timeline"),
  stats: $("timeline-stats"),
  onSelect: (key) => selectItem(key),
  states: {
    empty: $("timeline-empty"),
    loading: $("timeline-loading"),
    error: $("timeline-error"),
    errorText: $("timeline-error-text"),
    noTrace: $("timeline-no-trace"),
  },
});

const inspector = new Inspector({
  elements: {
    root: $("inspector"),
    stepLabel: $("inspector-step"),
    kindLabel: $("inspector-kind"),
    tabs: $("inspector-tabs"),
    body: $("inspector-body"),
    prevButton: $("inspector-prev"),
    nextButton: $("inspector-next"),
    closeButton: $("inspector-close"),
  },
  callbacks: {
    onStep: (offset) => stepInspector(offset),
    onClose: () => closeInspector(),
    onSelectKey: (key) => selectItem(key, { scroll: true }),
    fetchSpan: (spanId) => api.getSpan(app.runId, spanId),
    notify: (message) => notify(message),
  },
});

let renderQueued = false;

/** Queue one coalesced render on the next animation frame. */
function scheduleRender() {
  if (renderQueued) return;
  renderQueued = true;
  requestAnimationFrame(() => {
    renderQueued = false;
    renderAll();
  });
}

/** Render every view from the current store state. */
function renderAll() {
  renderHeader();
  renderContext();
  renderStepper();
  renderRail();
  renderTimeline();
  renderManual();
  renderFooter();
  renderInspector();
  syncFollow();
  updateHistoryCount();
}

/** Render the header identity/status row. */
function renderHeader() {
  const runId = store.runId;
  const runButton = $("run-id");
  runButton.disabled = !runId;
  runButton.textContent = runId ? t("header.run", { id: shortId(runId) }) : t("header.noRun");
  runButton.title = runId || t("header.noRunStarted");

  const waiting = store.pendingDecision;
  const status = waiting ? "waiting" : store.status;
  const info = statusInfo(
    status === "running"
      ? "running"
      : status === "finished"
        ? "completed"
        : status === "failed"
          ? "failed"
          : status === "waiting"
            ? "waiting"
            : "pending",
  );
  const label =
    status === "running"
      ? "running"
      : status === "finished"
        ? "completed"
        : status === "failed"
          ? "failed"
          : status === "waiting"
            ? "waiting"
            : "idle";
  const pill = $("status-pill");
  pill.dataset.status = status;
  pill.className = `status-pill status-${status}`;
  pill.textContent = `${info.glyph} ${t(`header.status.${label}`)}`;

  $("elapsed").textContent = formatClock(runElapsed(store));
  const token = store.tokenTotal();
  $("token-total").textContent = token
    ? token.total !== undefined && token.total > 0
      ? `${formatNumber(token.total)} tok`
      : token.scripted
        ? t("value.notAvailableScripted")
        : token.missing
          ? t("value.notAvailable")
          : EM_DASH
    : EM_DASH;
  $("start-button").disabled = !app.meta || store.running || store.pendingDecision;

  const themeButton = $("theme-toggle");
  if (themeButton) {
    themeButton.textContent = t("header.theme", { theme: t(`theme.${app.theme}`) });
  }
}

/** Render the context strip. */
function renderContext() {
  const meta = store.runMeta();
  const ids = store.ids();
  $("ctx-scenario").textContent = meta.scenario || EM_DASH;
  $("ctx-mode").textContent = meta.mode || EM_DASH;
  $("ctx-transport").textContent = meta.transport || EM_DASH;
  $("ctx-discovery").textContent = meta.discovery || EM_DASH;
  $("ctx-language").textContent = meta.language || app.meta?.language || EM_DASH;
  $("ctx-model").textContent = meta.modelLabel || meta.modelMode || EM_DASH;
  $("context-id").textContent = ids.contextId || EM_DASH;
  $("task-id").textContent = ids.taskId || EM_DASH;
  $("current-activity").textContent = store.derived().activity || t("context.idle");
  $("rail-language").textContent = app.meta?.language || EM_DASH;
  updateLocaleHint();
}

/** Show a quiet hint when the interface language differs from the process language. */
function updateLocaleHint() {
  const hint = $("locale-hint");
  const text = $("locale-hint-text");
  if (!hint || !text) return;
  const dataLanguage = store.runMeta().language || app.meta?.language || null;
  const dataLocale = normalizeLocale(dataLanguage);
  if (dataLocale && dataLocale !== getLocale()) {
    hint.hidden = false;
    text.textContent = t("context.localeMismatch", {
      ui: localeLabel(getLocale()),
      data: dataLanguage,
    });
  } else {
    hint.hidden = true;
    text.textContent = "";
  }
}

/** Sync the language select with the stored preference. */
function renderLocaleSelect() {
  const select = $("locale-select");
  if (select) select.value = getLocaleMode();
}

/** Render the eight-phase stepper. */
function renderStepper() {
  const derived = store.derived();
  for (const button of document.querySelectorAll(".phase-step-button")) {
    const key = button.dataset.phase;
    const state = derived.phaseStates[key] || "pending";
    const group = derived.groups.find((candidate) => candidate.key === key);
    const item = button.closest(".phase-step");
    item.className = `phase-step phase-${state}`;
    button.setAttribute("aria-current", state === "active" ? "step" : "false");
    const note = store.phaseNote(key);
    if (note) button.title = note;
    else button.removeAttribute("title");
    // A glyph plus a word keeps the state readable without relying on colour.
    const marker = { pending: "○", skipped: "–", failed: "✕", done: "✓", active: "●" }[state] || "•";
    const meta = button.querySelector(".phase-step-meta");
    meta.replaceChildren();
    if (state === "pending" || state === "skipped" || state === "failed") {
      meta.textContent = `${marker} ${t(`phaseState.${state}`)}`;
      continue;
    }
    const parts = [t(`phaseState.${state}`)];
    if (group?.stats.durationMs !== null && group?.stats.durationMs !== undefined) {
      parts.push(formatDuration(group.stats.durationMs));
    } else {
      parts.push(t("toolbar.step", { count: group?.stats.steps ?? 0 }));
    }
    if (key === "resolution") {
      const label = store.resolutionLabel();
      if (label) parts.push(label);
    }
    meta.textContent = `${marker} ${parts.join(" · ")}`;
    if (key === "negotiation" && group?.rounds?.length) {
      for (const round of group.rounds) {
        const chip = el(
          "span",
          `round-chip round-${round.terminal ? "done" : "active"}`,
          `R${round.round}${round.terminal ? " ✓" : " ◐"}`,
        );
        meta.appendChild(document.createTextNode(" "));
        meta.appendChild(chip);
      }
    }
  }
}

/** Render the analysis, decision log and export rail. */
function renderRail() {
  const derived = store.derived();
  const analysis = derived.analysis;
  $("analysis-wall").textContent = formatClock(runElapsed(store));
  $("analysis-llm").textContent = `${formatNumber(analysis.llmCalls)}${
    analysis.scriptedCalls
      ? t("analysis.scriptedSuffix", { count: analysis.scriptedCalls })
      : ""
  }`;
  $("analysis-tokens").textContent = tokenAnalysisText(analysis);
  $("analysis-tools").textContent = t("analysis.toolsAndErrors", {
    tools: formatNumber(analysis.tools),
    errors: analysis.toolErrors,
  });
  $("analysis-errors").textContent = `${analysis.errors} / ${analysis.retries}`;
  $("analysis-parse").textContent = formatNumber(analysis.parseFailures);
  $("analysis-decisions").textContent = formatNumber(analysis.decisions);

  const freshDurations = phaseDurationList(store);
  $("phase-durations").replaceChildren(...Array.from(freshDurations.children));

  const anomalies = $("anomalies-list");
  anomalies.replaceChildren();
  if (derived.anomalies.length === 0) {
    anomalies.appendChild(el("li", "placeholder", t("analysis.none")));
  } else {
    for (const anomaly of derived.anomalies) {
      const row = el("li", `anomaly anomaly-${anomaly.kind}`);
      if (anomaly.key) {
        const button = el("button", "anomaly-button", anomaly.message);
        button.type = "button";
        button.addEventListener("click", () => selectItem(anomaly.key, { scroll: true }));
        row.appendChild(button);
      } else {
        row.textContent = anomaly.message;
      }
      anomalies.appendChild(row);
    }
  }

  const decisionLog = $("decision-log");
  decisionLog.replaceChildren();
  if (derived.decisionLog.length === 0) {
    decisionLog.appendChild(el("li", "placeholder", t("analysis.noDecisions")));
  } else {
    for (const entry of derived.decisionLog) {
      const row = el("li", "decision-entry");
      const button = el("button", "decision-button");
      button.type = "button";
      append(
        button,
        el(
          "span",
          "decision-head",
          `${entry.round ? `R${entry.round} ` : ""}${String(entry.action).toUpperCase()} · ${entry.source}`,
        ),
        el("span", "decision-rationale", truncate(entry.rationale, 120)),
      );
      button.addEventListener("click", () => selectItem(entry.key, { scroll: true }));
      row.appendChild(button);
      decisionLog.appendChild(row);
    }
  }

  const enabled = Boolean(store.runId);
  const urls = store.runId
    ? api.exportUrls(store.runId)
    : { trace: "#", transcriptJson: "#", transcriptMarkdown: "#" };
  setLink("export-trace", enabled, urls.trace);
  setLink("export-json", enabled, urls.transcriptJson);
  setLink("export-md", enabled, urls.transcriptMarkdown);
  setLink("menu-trace", enabled, urls.trace);
  setLink("menu-transcript", enabled, urls.transcriptJson);
  renderLangfuseLink();

  renderRoundFilter();

  for (const chip of document.querySelectorAll("#filter-chips [data-filter]")) {
    const active = chip.dataset.filter === filters.filter;
    chip.classList.toggle("is-active", active);
    chip.setAttribute("aria-pressed", String(active));
  }
  for (const button of document.querySelectorAll("[data-view]")) {
    const active = button.dataset.view === filters.view;
    button.classList.toggle("is-active", active);
    button.setAttribute("aria-pressed", String(active));
  }
  if ($("actor-filter").value !== filters.actor) $("actor-filter").value = filters.actor;
  if ($("search-input").value !== filters.search) $("search-input").value = filters.search;
}

/** Enable or disable one download link. */
function setLink(id, enabled, href) {
  const link = $(id);
  if (!link) return;
  if (enabled) {
    link.href = href;
    link.setAttribute("download", "");
    link.removeAttribute("aria-disabled");
  } else {
    link.href = "#";
    link.setAttribute("aria-disabled", "true");
    link.removeAttribute("download");
  }
}

/** Show the Langfuse trace link when the run exported one. */
function renderLangfuseLink() {
  const link = $("export-langfuse");
  if (!link) return;
  const langfuse = store.finishedFrame?.langfuse || store.snapshot?.langfuse || null;
  const url = langfuse && langfuse.enabled && typeof langfuse.url === "string" ? langfuse.url : "";
  if (url) {
    link.href = url;
    link.hidden = false;
  } else {
    link.href = "#";
    link.hidden = true;
  }
}

/** Render the timeline, preserving scroll position when the reader is not following. */
function renderTimeline() {
  const scroll = $("timeline-scroll");
  const previousTop = scroll.scrollTop;
  timeline.render({
    store,
    filters,
    selectedKey: store.selectedKey,
    viewState: { loading: app.loading, error: app.startError },
    onClearFilters: () => resetFilters(),
  });
  if (follow.enabled && !follow.paused) {
    scroll.scrollTop = scroll.scrollHeight;
  } else {
    scroll.scrollTop = previousTop;
  }
  const pendingKey = app.pendingScrollKey;
  app.pendingScrollKey = null;
  if (pendingKey) timeline.scrollToItem(pendingKey);
}

/** Render the manual decision panels (manual mode only). */
function renderManual() {
  const meta = store.runMeta();
  const manual = store.pendingDecision && meta.mode === "manual";
  $("manual-panel-rail").hidden = !manual;
  $("manual-pinned").hidden = !manual;
  if (!manual) return;

  const request = store.decisionRequest;
  const proposal = request?.proposal || {};
  const round = request?.round ?? proposal.round ?? "?";
  const maxRounds = request?.maxRounds ?? proposal.max_rounds ?? "?";
  const kind = proposal.kind || "negotiation";
  const performative = request?.performative || proposal.performative || "PROPOSE";
  const content = proposal.content || "";
  const summary = `${kind} · R${round}/${maxRounds} · ${performative}${content ? `\n${content}` : ""}`;
  for (const node of document.querySelectorAll('[data-role="manual-context"]')) {
    node.textContent = summary;
  }
  const roundNode = document.querySelector("#manual-pinned [data-role='manual-round']");
  if (roundNode) roundNode.textContent = `R${round} · ${kind}`;
  updateCountdown();
  for (const button of document.querySelectorAll(".decision-submit")) {
    button.disabled = app.decisionSubmitting || app.decisionTimedOut;
  }
}

/** Render the footer connection state. */
function renderFooter() {
  const last = store.list()[store.list().length - 1];
  $("last-event").textContent = last
    ? t("connection.lastEvent", {
        number: String(last.number).padStart(2, "0"),
        title: truncate(last.title, 80),
      })
    : t("connection.noEvents");
  const banner = $("connection-banner");
  banner.hidden = !((app.disconnected || app.fatalStream) && store.running);
  $("connection-banner-text").textContent = app.fatalStream
    ? t("connection.closed")
    : t("connection.disconnected");
  syncConnection();
}

/** Render the inspector for the selection or the run summary. */
function renderInspector() {
  if (!app.inspectorOpen) {
    inspector.hide();
    return;
  }
  const keys = timeline.orderedKeys.length > 0 ? timeline.orderedKeys : store.list().map(itemKey);
  const key = store.selectedKey;
  if (!key) {
    inspector.showRun(store, keys.length);
    return;
  }
  const item = store.getItem(key);
  if (!item) {
    inspector.showRun(store, keys.length);
    return;
  }
  inspector.update(item, { store, index: keys.indexOf(key), total: keys.length });
}

/** Synchronize both follow toggles with the controller. */
function syncFollow() {
  const header = $("follow-toggle");
  const toolbar = $("timeline-follow");
  const label = follow.label();
  header.setAttribute("aria-pressed", String(!follow.paused));
  $("follow-label").textContent = follow.paused ? label : t("header.follow");
  header.classList.toggle("is-paused", follow.paused);
  toolbar.setAttribute("aria-pressed", String(!follow.paused));
  toolbar.textContent = follow.paused ? label : t("toolbar.follow");
  toolbar.classList.toggle("is-paused", follow.paused);
}

/** Sync the connection indicator and banner copy. */
function syncConnection() {
  const node = $("connection-state");
  let state = "idle";
  let label = t("connection.idle");
  if (app.fatalStream) {
    state = "closed";
    label = t("connection.streamClosed");
  } else if (app.disconnected) {
    state = "reconnecting";
    label = t("connection.reconnecting");
  } else if (store.pendingDecision) {
    state = "waiting";
    label = t("connection.waiting");
  } else if (store.status === "running") {
    state = "live";
    label = t("connection.live");
  } else if (store.status === "finished") {
    state = "finished";
    label = t("connection.streamClosed");
  } else if (store.status === "failed") {
    state = "failed";
    label = t("connection.failed");
  }
  node.dataset.state = state;
  $("connection-label").textContent = label;
}

/** Rebuild the round filter options while keeping the current selection when possible. */
function renderRoundFilter() {
  const select = $("round-filter");
  const rounds = availableRounds(store.list());
  const current = filters.round;
  const signature = rounds.join(",");
  if (select.dataset.signature !== signature) {
    select.replaceChildren();
    const all = el("option", "", t("toolbar.allRounds"));
    all.value = "all";
    select.appendChild(all);
    for (const round of rounds) {
      const option = el("option", "", `R${round}`);
      option.value = String(round);
      select.appendChild(option);
    }
    select.dataset.signature = signature;
  }
  if (current !== "all" && !rounds.includes(Number(current))) {
    filters.round = "all";
  }
  select.value = filters.round;
}

/** Open the inspector for one selection. */
function selectItem(key, { scroll = false } = {}) {
  store.select(key);
  const item = store.getItem(key);
  // An LLM row is first-class: opening it lands directly on its request payload.
  if (item?.kind === "llm") inspector.tab = "llm-request";
  if (scroll && key) app.pendingScrollKey = key;
  app.inspectorOpen = true;
  follow.pauseForReader();
  applyInspectorLayout();
  scheduleRender();
}

/** Close the inspector and clear the selection. */
function closeInspector() {
  store.select(null);
  app.inspectorOpen = false;
  applyInspectorLayout();
  scheduleRender();
}

/** Move the inspector selection by one row and bring it into view. */
function stepInspector(offset) {
  const keys = timeline.orderedKeys.length > 0 ? timeline.orderedKeys : store.list().map(itemKey);
  store.selectByOffset(offset, keys);
  if (store.selectedKey) {
    app.pendingScrollKey = store.selectedKey;
    app.inspectorOpen = true;
    follow.pauseForReader();
    applyInspectorLayout();
    scheduleRender();
  }
}

/** Apply the open/closed layout classes of the inspector. */
function applyInspectorLayout() {
  const overlay = window.matchMedia("(max-width: 1119px)").matches;
  document.body.classList.toggle("inspector-open", app.inspectorOpen);
  document.body.classList.toggle("inspector-closed", !app.inspectorOpen);
  $("inspector").setAttribute("aria-hidden", String(!app.inspectorOpen));
  $("inspector-backdrop").hidden = !(app.inspectorOpen && overlay);
}

/** Reset all filters and the search box. */
function resetFilters() {
  filters.filter = "all";
  filters.actor = "all";
  filters.round = "all";
  filters.search = "";
  scheduleRender();
}

/** Reset every per-run UI value (manual decision, stream and scroll state). */
function resetRunUiState() {
  if (app.finishTimer) {
    clearTimeout(app.finishTimer);
    app.finishTimer = null;
  }
  app.decisionSeq = null;
  app.decisionAt = null;
  app.decisionTimeout = null;
  app.decisionTimedOut = false;
  app.decisionSubmitting = false;
  app.disconnected = false;
  app.fatalStream = false;
  app.finalizing = false;
  app.pendingScrollKey = null;
  resetDecisionForms();
}

/** Start one run from the rail form. */
async function startRun(event) {
  event.preventDefault();
  if (!app.meta) {
    showStartError(t("notify.metadataUnavailable"));
    return;
  }
  app.startError = null;
  $("run-error").hidden = true;
  $("run-error").textContent = "";
  $("start-button").disabled = true;
  stopSubscription();
  api.clearSpanCache();
  try {
    const scenario = $("scenario-select").value;
    const entered = $("user-request-input").value.trim();
    // The input shows the concise presentation wording. While unchanged, keep the backend's
    // richer catalog prompt so the offline scripted extractor still routes the run.
    const defaultRequest = app.meta?.scenarioRequests?.[scenario] || "";
    const payload = await api.startRun({
      scenario,
      mode: $("mode-select").value,
      transport: $("transport-select").value,
      discovery: $("discovery-select").value,
      userRequest: entered && entered !== defaultRequest ? entered : null,
      source: "explorer",
    });
    app.runId = payload.runId;
    if (payload.deduplicated) {
      // The request repeated an idempotency key: the existing run is already terminal. Show its
      // persisted detail instead of opening an empty live stream.
      history.replaceState(null, "", `#run=${payload.runId}`);
      openHistory("runs");
      await refreshClientRuns();
      await showClientRunDetail(payload.runId);
      $("start-button").disabled = false;
      return;
    }
    resetRunUiState();
    store.beginRun(payload.runId, app.meta);
    history.replaceState(null, "", `#run=${payload.runId}`);
    connect(payload.runId);
    refreshTasks();
    refreshClientRuns();
    refreshConversations();
    scheduleRender();
  } catch (error) {
    showStartError(String(error?.message ?? error));
    $("start-button").disabled = false;
  }
}

/** Show one start failure in the rail, and in the timeline only when it is empty. */
function showStartError(message) {
  app.startError = store.list().length === 0 ? message : null;
  const node = $("run-error");
  node.hidden = false;
  node.textContent = message;
  $("start-button").disabled = false;
  scheduleRender();
}

/** Summarize LLM token usage for the rail (scripted runs never show a zero). */
function tokenAnalysisText(analysis) {
  if (analysis.llmCalls === 0) return EM_DASH;
  if (analysis.numericCalls === 0) {
    // "scripted" only labels the offline provider; a live call without usage is just missing.
    return analysis.scriptedCalls > 0 ? t("value.notAvailableScripted") : t("value.notAvailable");
  }
  const base = t("analysis.tokensInOut", {
    prompt: formatNumber(analysis.promptTokens),
    completion: formatNumber(analysis.completionTokens),
  });
  const extras = [];
  if (analysis.scriptedCalls > 0) {
    extras.push(t("analysis.scriptedExtra", { count: analysis.scriptedCalls }));
  }
  if (analysis.missingCalls > 0) {
    extras.push(t("analysis.noUsageExtra", { count: analysis.missingCalls }));
  }
  return extras.length > 0
    ? `${base}${t("analysis.extraSuffix", { extras: extras.join(", ") })}`
    : base;
}

/** Open the event stream of one run. */
function connect(runId) {
  stopSubscription();
  app.disconnected = false;
  app.subscription = api.subscribe(runId, {
    onOpen: () => {
      app.disconnected = false;
      app.fatalStream = false;
      scheduleRender();
    },
    onFrame: (frame) => handleFrame(frame),
    onError: (error, fatal) => {
      if (!store.running) {
        app.disconnected = false;
        app.fatalStream = false;
        scheduleRender();
        return;
      }
      // A temporary drop makes EventSource retry; a closed stream is terminal for this page.
      app.disconnected = !fatal;
      app.fatalStream = Boolean(fatal);
      scheduleRender();
    },
    onClose: () => {
      app.disconnected = false;
      app.fatalStream = false;
      finalizeRun();
    },
  });
  scheduleRender();
}

/** Handle one new SSE frame. */
function handleFrame(frame) {
  if (frame.type === "decision_requested") {
    if (app.decisionSeq !== frame.seq) {
      app.decisionSeq = frame.seq;
      app.decisionAt =
        typeof frame.requestedAt === "string" ? Date.parse(frame.requestedAt) : Date.now();
      app.decisionTimeout =
        typeof frame.timeoutSeconds === "number" ? frame.timeoutSeconds : null;
      app.decisionTimedOut = false;
      app.decisionSubmitting = false;
      resetDecisionForms();
    }
  }
  const changed = store.applyFrame(frame);
  if (!changed) return;
  app.startError = null;
  if (frame.type === "finished") {
    app.decisionAt = null;
    app.decisionTimedOut = false;
    app.disconnected = false;
    app.fatalStream = false;
    scheduleFinalize();
  } else if (follow.paused) {
    follow.noteNew();
  }
  announce(frame);
  scheduleRender();
}

/** Return the stream grace: the backend's window, never shorter than the local fallback. */
function finishedGraceMs() {
  const server = Number(app.meta?.finishedGraceMs);
  return Number.isFinite(server) && server > 0
    ? Math.max(DEFAULT_FINISHED_GRACE_MS, server)
    : DEFAULT_FINISHED_GRACE_MS;
}

/** Close a finished run after the grace window that admits late cross-process server spans. */
function scheduleFinalize() {
  if (app.finishTimer) return;
  app.finishTimer = window.setTimeout(() => {
    app.finishTimer = null;
    finalizeRun();
  }, finishedGraceMs());
}

/** Fetch the terminal snapshot/trace and close the stream bookkeeping (once per run). */
async function finalizeRun() {
  if (app.finalizing) return;
  app.finalizing = true;
  stopSubscription();
  if (!app.runId) return;
  try {
    const snapshot = await api.getRun(app.runId);
    store.applySnapshot(snapshot);
    clearStreamStateIfTerminal(snapshot);
  } catch {
    // The frames already carry the outcome; the snapshot only enriches it.
    app.disconnected = false;
    app.fatalStream = false;
  }
  try {
    // A server batch posted after the subscriber detached is still in the trace document; merge it
    // by seq/spanId so the final page never misses the run's last LLM/tool rows.
    const trace = await api.getTrace(app.runId);
    if (trace && Array.isArray(trace.spans)) store.mergeSpans(trace.spans);
  } catch {
    // The stream already carried every span it could; a missing trace document is not an error.
  }
  await refreshTasks();
  await refreshClientRuns();
  await refreshConversations();
  scheduleRender();
}

/** A terminal snapshot means any stream warning is stale. */
function clearStreamStateIfTerminal(snapshot) {
  if (snapshot && typeof snapshot.status === "string" && snapshot.status !== "running") {
    app.disconnected = false;
    app.fatalStream = false;
  }
}

/** Announce one new frame in the polite live region. */
function announce(frame) {
  let message = "";
  if (frame.type === "run_started") {
    message = t("announce.runStarted", { scenario: frame.scenario || "run" });
  } else if (frame.type === "finished") {
    message =
      frame.status === "failed"
        ? t("announce.runFailed", { error: frame.error || t("value.unknown") })
        : t("announce.runFinished");
  } else if (frame.type === "decision_requested") {
    message = t("announce.decisionRequested", { round: frame.round ?? "?" });
  } else if (frame.type === "event" || frame.type === "trace") {
    const last = store.list()[store.list().length - 1];
    message = last
      ? t("announce.newStep", { number: last.number, title: last.title })
      : t("announce.newStepGeneric");
  }
  if (message) $("live-region").textContent = message;
}

/** Stop the current SSE subscription. */
function stopSubscription() {
  if (app.subscription) {
    app.subscription.close();
    app.subscription = null;
  }
}

/** Show one transient notification in the live region. */
function notify(message) {
  $("live-region").textContent = message;
}

/** Resume a run from the URL hash after a reload. */
async function resumeRun(runId) {
  // Close the previous run's stream before resetting the store: otherwise its late frames could
  // still be applied to the new run (and keep the old manual-decision form state alive).
  stopSubscription();
  resetRunUiState();
  app.loading = false;
  app.runId = runId;
  store.beginRun(runId, app.meta);
  api.clearSpanCache();
  try {
    const snapshot = await api.getRun(runId);
    store.applySnapshot(snapshot);
    clearStreamStateIfTerminal(snapshot);
  } catch (error) {
    app.startError = t("notify.runLoadFailed", {
      id: shortId(runId),
      error: String(error?.message ?? error),
    });
    history.replaceState(null, "", location.pathname);
    app.runId = null;
    store.reset();
    store.setMeta(app.meta);
    scheduleRender();
    return;
  }
  connect(runId);
  scheduleRender();
}

/** Read the run id from the URL hash. */
function runIdFromHash() {
  const match = /^#run=([A-Za-z0-9_-]+)$/.exec(location.hash);
  return match ? match[1] : null;
}

/** Populate the run setup selects from the process metadata. */
function populateMeta(meta) {
  fillSelect($("scenario-select"), meta.scenarios || [], meta.defaultScenario);
  fillSelect($("mode-select"), meta.modes || [], meta.defaultMode);
  fillSelect($("transport-select"), meta.transports || [], meta.defaultTransport);
  fillSelect($("discovery-select"), meta.discoveries || [], meta.defaultDiscovery);
  fillUserRequest($("scenario-select").value);
  $("rail-language").textContent = meta.language || EM_DASH;
}

/** Fill the request editor with the selected scenario's default request. */
function fillUserRequest(scenario) {
  const input = $("user-request-input");
  if (!input) return;
  input.value = app.meta?.scenarioRequests?.[scenario] || "";
}

/** Fill one select with options, keeping one default selected. */
function fillSelect(select, values, selected) {
  select.replaceChildren();
  for (const value of values) {
    const option = el("option", "", value);
    option.value = value;
    select.appendChild(option);
  }
  if (selected && values.includes(selected)) select.value = selected;
}

/** True when a filter/search is active. */
function isFilterActive() {
  return (
    filters.filter !== "all" ||
    filters.actor !== "all" ||
    filters.round !== "all" ||
    Boolean(filters.search.trim())
  );
}

/** Scroll to one phase from the stepper, clearing filters when the phase is hidden. */
function goToPhase(phase) {
  if (isFilterActive()) {
    resetFilters();
    renderAll();
  }
  timeline.scrollToPhase(phase);
}

/** Toggle following from the header, toolbar or keyboard. */
function toggleFollow() {
  follow.toggle();
  if (!follow.paused) {
    const scroll = $("timeline-scroll");
    scroll.scrollTop = scroll.scrollHeight;
  }
  syncFollow();
}

/** Render or update the manual decision countdown. */
function updateCountdown() {
  if (!store.pendingDecision) return;
  let text = t("header.status.waiting");
  if (app.decisionAt && !Number.isNaN(app.decisionAt)) {
    const elapsed = Math.max(0, (Date.now() - app.decisionAt) / 1000);
    if (app.decisionTimeout !== null) {
      const remaining = app.decisionTimeout - elapsed;
      text = formatCountdown(remaining);
      if (remaining <= 0) {
        text = `0:00 · ${t("value.finished")}`;
        app.decisionTimedOut = true;
        for (const button of document.querySelectorAll(".decision-submit")) button.disabled = true;
      }
    } else {
      text = `${t("header.status.waiting")} ${Math.floor(elapsed)}s`;
    }
  }
  for (const node of document.querySelectorAll('[data-role="countdown"]')) node.textContent = text;
}

/** Reset both decision forms to their empty state. */
function resetDecisionForms() {
  for (const field of document.querySelectorAll("[data-field]")) {
    if (field.dataset.field === "action") field.value = "accept";
    else field.value = "";
  }
  for (const node of document.querySelectorAll(".decision-error")) {
    node.hidden = true;
    node.textContent = "";
  }
  for (const button of document.querySelectorAll(".decision-submit")) button.disabled = false;
}

/** Mirror a decision field value across both panels. */
function syncDecisionField(source) {
  for (const target of document.querySelectorAll(`[data-field="${source.dataset.field}"]`)) {
    if (target !== source) target.value = source.value;
  }
}

/** Submit the manual decision of one panel. */
async function submitDecision(button) {
  if (!store.pendingDecision || !app.runId || app.decisionTimedOut || app.decisionSubmitting) return;
  const panel = button.closest(".manual-panel, .manual-pinned") || document;
  const read = (name) => panel.querySelector(`[data-field="${name}"]`)?.value ?? "";
  const errorNode = panel.querySelector(".decision-error");
  let filled;
  let counterParams;
  try {
    filled = parseOptionalJson(read("filled"), t("rail.filled"));
    counterParams = parseOptionalJson(read("counterParams"), t("rail.counterParams"));
  } catch (error) {
    showDecisionError(errorNode, String(error.message));
    return;
  }
  const decision = {
    action: read("action") || "accept",
    rationale: read("rationale").trim(),
  };
  if (filled !== null) decision.filled_params = filled;
  const counter = read("counter").trim();
  if (counter) decision.counter_proposal = counter;
  if (counterParams !== null) decision.counter_params = counterParams;

  app.decisionSubmitting = true;
  for (const node of document.querySelectorAll(".decision-submit")) node.disabled = true;
  try {
    await api.submitDecision(app.runId, decision);
    showDecisionError(errorNode, null);
    notify(t("notify.decisionSubmitted"));
  } catch (error) {
    showDecisionError(errorNode, String(error?.message ?? error));
    app.decisionSubmitting = false;
    for (const node of document.querySelectorAll(".decision-submit")) node.disabled = false;
  }
}

/** Parse one optional JSON text field. */
function parseOptionalJson(text, label) {
  if (!text.trim()) return null;
  try {
    return JSON.parse(text);
  } catch (error) {
    throw new Error(t("notify.jsonInvalid", { label, error: error.message }));
  }
}

/** Show or clear one decision form error. */
function showDecisionError(node, message) {
  if (!node) return;
  node.hidden = !message;
  node.textContent = message || "";
}

/** Open or close the header menu, keeping ``aria-expanded`` in sync. */
function setMenuOpen(open) {
  $("menu-panel").hidden = !open;
  $("menu-button").setAttribute("aria-expanded", String(open));
}

/** Open the shortcuts dialog. */
function openShortcuts() {
  const dialog = $("shortcuts-dialog");
  if (typeof dialog.showModal === "function") dialog.showModal();
  else dialog.setAttribute("open", "");
}

/** Close the shortcuts dialog. */
function closeShortcuts() {
  const dialog = $("shortcuts-dialog");
  if (dialog.open) dialog.close();
  else dialog.removeAttribute("open");
}

/** Populate the shortcuts dialog from the shared map. */
function renderShortcuts() {
  const list = $("shortcuts-list");
  list.replaceChildren();
  for (const entry of SHORTCUTS) {
    const row = el("div", "shortcut-row");
    append(
      row,
      el("dt", "shortcut-keys", entry.keys),
      el("dd", "shortcut-description", t(entry.descriptionKey)),
    );
    list.appendChild(row);
  }
}

/** Apply one theme and persist the preference. */
function applyTheme(theme) {
  app.theme = theme;
  if (theme === "auto") delete document.documentElement.dataset.theme;
  else document.documentElement.dataset.theme = theme;
  try {
    localStorage.setItem("a2at-ran-theme", theme);
  } catch {
    // Private-browsing storage failures are irrelevant to the explorer.
  }
  $("theme-toggle").textContent = t("header.theme", { theme: t(`theme.${theme}`) });
}

/** Cycle auto → light → dark. */
function cycleTheme() {
  const order = ["auto", "light", "dark"];
  applyTheme(order[(order.indexOf(app.theme) + 1) % order.length]);
}

/** Open the history panel on one tab. */
function openHistory(tab) {
  if (tab) app.historyTab = tab;
  setHistoryOpen(true);
  selectHistoryTab(app.historyTab);
}

/** Expand or collapse the history panel. */
function setHistoryOpen(open) {
  app.historyOpen = Boolean(open);
  $("history-body").hidden = !app.historyOpen;
  $("history-toggle").setAttribute("aria-expanded", String(app.historyOpen));
  const chevron = $("history-toggle").querySelector(".history-chevron");
  if (chevron) chevron.textContent = app.historyOpen ? "▾" : "▸";
  updateHistoryCount();
}

/** Show one history tab and load its rows. */
function selectHistoryTab(tab) {
  app.historyTab = tab;
  for (const button of document.querySelectorAll("#history-tabs [data-history-tab]")) {
    button.setAttribute("aria-selected", String(button.dataset.historyTab === tab));
  }
  for (const panel of document.querySelectorAll("[data-history-panel]")) {
    panel.hidden = panel.dataset.historyPanel !== tab;
  }
  if (!app.historyOpen) setHistoryOpen(true);
  if (tab === "tasks") refreshTasks();
  else if (tab === "runs") refreshClientRuns();
  else refreshConversations();
  updateHistoryCount();
}

/** Show the active tab's row count in the collapsed header. */
function updateHistoryCount() {
  const node = $("history-count");
  if (!node) return;
  if (app.historyTab === "runs") {
    node.textContent = countOf(app.clientRuns.length, "history.runCountOne", "history.runCount");
  } else if (app.historyTab === "conversations") {
    node.textContent = countOf(
      app.conversations.length,
      "history.conversationCountOne",
      "history.conversationCount",
    );
  } else {
    node.textContent = countOf(app.tasks.length, "history.taskCountOne", "history.taskCount");
  }
}

/** Localize one count with its singular/plural key pair. */
function countOf(count, oneKey, manyKey) {
  return t(count === 1 ? oneKey : manyKey, { count });
}

/** Wire the static toolbar, form and dialog controls. */
function bindControls() {
  $("run-form").addEventListener("submit", startRun);
  $("scenario-select").addEventListener("change", () => fillUserRequest($("scenario-select").value));
  $("reset-request-button").addEventListener("click", () => fillUserRequest($("scenario-select").value));
  $("run-id").addEventListener("click", () => {
    if (!store.runId) return;
    navigator.clipboard?.writeText(store.runId).then(
      () => notify(t("notify.runIdCopied")),
      () => notify(store.runId),
    );
  });
  $("follow-toggle").addEventListener("click", toggleFollow);
  $("timeline-follow").addEventListener("click", toggleFollow);
  $("expand-all").addEventListener("click", () => timeline.expandAll());
  $("collapse-all").addEventListener("click", () => timeline.collapseAll());
  $("client-run-detail-close")?.addEventListener("click", closeClientRunDetail);
  $("conversation-detail-close")?.addEventListener("click", closeConversationDetail);

  $("locale-select")?.addEventListener("change", (event) => {
    setLocaleMode(event.target.value);
  });

  $("history-toggle")?.addEventListener("click", () => setHistoryOpen(!app.historyOpen));
  for (const button of document.querySelectorAll("#history-tabs [data-history-tab]")) {
    button.addEventListener("click", () => selectHistoryTab(button.dataset.historyTab));
  }

  for (const chip of document.querySelectorAll("#filter-chips [data-filter]")) {
    chip.addEventListener("click", () => {
      filters.filter = chip.dataset.filter;
      scheduleRender();
    });
  }
  for (const button of document.querySelectorAll("[data-view]")) {
    button.addEventListener("click", () => {
      filters.view = button.dataset.view;
      scheduleRender();
    });
  }
  $("actor-filter").addEventListener("change", (event) => {
    filters.actor = event.target.value;
    scheduleRender();
  });
  $("round-filter").addEventListener("change", (event) => {
    filters.round = event.target.value;
    scheduleRender();
  });
  $("search-input").addEventListener("input", (event) => {
    filters.search = event.target.value;
    scheduleRender();
  });
  $("connection-retry").addEventListener("click", () => {
    app.subscription?.reopen?.();
    app.disconnected = false;
    app.fatalStream = false;
    scheduleRender();
  });

  for (const button of document.querySelectorAll(".phase-step-button")) {
    button.addEventListener("click", () => goToPhase(button.dataset.phase));
  }

  for (const button of document.querySelectorAll(".decision-submit")) {
    button.addEventListener("click", () => submitDecision(button));
  }
  for (const button of document.querySelectorAll(".decision-focus")) {
    button.addEventListener("click", () => {
      $("manual-pinned").scrollIntoView({ behavior: "smooth", block: "start" });
      $("manual-pinned").querySelector("[data-field='rationale']")?.focus();
    });
  }
  for (const field of document.querySelectorAll("[data-field]")) {
    field.addEventListener("input", () => syncDecisionField(field));
    field.addEventListener("change", () => syncDecisionField(field));
  }

  $("inspector-backdrop").addEventListener("click", () => closeInspector());

  $("menu-button").addEventListener("click", (event) => {
    event.stopPropagation();
    setMenuOpen($("menu-panel").hidden);
  });
  document.addEventListener("click", (event) => {
    if ($("menu-panel").hidden) return;
    if (event.target.closest("#menu-panel") || event.target.closest("#menu-button")) return;
    setMenuOpen(false);
  });
  $("theme-toggle").addEventListener("click", cycleTheme);
  $("menu-shortcuts").addEventListener("click", () => {
    setMenuOpen(false);
    openShortcuts();
  });
  $("shortcuts-close").addEventListener("click", closeShortcuts);
  $("rail-toggle").addEventListener("click", () => {
    const expanded = document.body.classList.toggle("rail-expanded");
    $("rail-toggle").setAttribute("aria-expanded", String(expanded));
  });

  let scrollQueued = false;
  $("timeline-scroll").addEventListener("scroll", () => {
    if (scrollQueued) return;
    scrollQueued = true;
    requestAnimationFrame(() => {
      scrollQueued = false;
      const element = $("timeline-scroll");
      if (isAtBottom(element)) follow.resume();
      else follow.pauseForReader();
      syncFollow();
    });
  });

  installShortcuts({
    next: () => stepInspector(1),
    previous: () => stepInspector(-1),
    open: () => {
      if (store.selectedKey) {
        app.inspectorOpen = true;
        applyInspectorLayout();
        scheduleRender();
      } else if (timeline.orderedKeys.length > 0) {
        selectItem(timeline.orderedKeys[0], { scroll: true });
      }
    },
    escape: () => {
      if ($("shortcuts-dialog").open) {
        closeShortcuts();
        return;
      }
      if (!$("menu-panel").hidden) {
        setMenuOpen(false);
        return;
      }
      if (document.activeElement === $("search-input")) {
        $("search-input").blur();
        return;
      }
      if (app.inspectorOpen) closeInspector();
    },
    search: () => $("search-input").focus(),
    follow: toggleFollow,
    tab: (number) => {
      if (!app.inspectorOpen) {
        app.inspectorOpen = true;
        applyInspectorLayout();
        scheduleRender();
      }
      inspector.selectTabByNumber(number);
    },
    previousStep: () => stepInspector(-1),
    nextStep: () => stepInspector(1),
    help: openShortcuts,
    submitDecision: () => {
      const button = document.querySelector(".decision-submit:not([disabled])");
      if (button) submitDecision(button);
    },
  });

  window.addEventListener("hashchange", () => {
    const runId = runIdFromHash();
    if (runId && runId !== app.runId) resumeRun(runId);
  });
  window.matchMedia("(max-width: 1119px)").addEventListener("change", applyInspectorLayout);
}

/** Watch the run status in the background so ids/report enrich even if a frame is missed. */
function startPolling() {
  setInterval(async () => {
    if (!app.runId || !store.running) return;
    const now = Date.now();
    if (now - app.lastSnapshotAt < 2000) return;
    app.lastSnapshotAt = now;
    try {
      const snapshot = await api.getRun(app.runId);
      if (snapshot.runId === app.runId) {
        store.applySnapshot(snapshot);
        clearStreamStateIfTerminal(snapshot);
      }
      scheduleRender();
    } catch {
      // The SSE stream is authoritative for live state; a failed poll is not an error.
    }
  }, 1000);
}

/** Update clocks and the manual countdown without rebuilding the timeline. */
function startTicker() {
  setInterval(() => {
    $("elapsed").textContent = formatClock(runElapsed(store));
    $("analysis-wall").textContent = formatClock(runElapsed(store));
    updateCountdown();
  }, 250);
}

/** Human label of one A2A task state. */
function taskStateLabel(state) {
  const value = typeof state === "string" ? state.replace(/^TASK_STATE_/, "").toLowerCase() : "";
  if (!value) return t("taskState.unknown");
  const key = `taskState.${value}`;
  const text = t(key);
  return text === key ? value : text;
}

/** Format one ISO timestamp of the task table. */
function taskTime(value) {
  if (typeof value !== "string" || !value) return EM_DASH;
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? EM_DASH : date.toLocaleString();
}

/** Render the persisted NMS task table from ``app.tasks``. */
function renderTaskTable() {
  const body = $("task-table-body");
  if (!body) return;
  body.replaceChildren();
  const count = app.tasks.length;
  $("task-history-count").textContent = countOf(count, "history.taskCountOne", "history.taskCount");

  const note = $("task-table-note");
  const scroll = body.closest(".task-table-scroll");
  if (!app.tasksEnabled) {
    note.hidden = false;
    note.textContent = t("history.tasksDisabled");
    if (scroll) scroll.hidden = true;
    updateHistoryCount();
    return;
  }
  if (count === 0) {
    note.hidden = false;
    note.textContent = t("history.tasksEmpty");
    if (scroll) scroll.hidden = true;
    updateHistoryCount();
    return;
  }
  note.hidden = true;
  if (scroll) scroll.hidden = false;

  for (const task of app.tasks) {
    const row = el("tr");
    append(
      row,
      el("td", "task-id-cell", shortId(task.taskId)),
      el("td", "task-state-cell", taskStateLabel(task.state)),
      el("td", "task-context-cell", shortId(task.contextId)),
      el("td", "task-time-cell", taskTime(task.updatedAt)),
      el("td", "task-summary-cell", truncate(task.summary, 180) || EM_DASH),
      el("td", "task-run-cell", task.runId ? shortId(task.runId) : EM_DASH),
    );
    body.appendChild(row);
  }
  updateHistoryCount();
}

/** Fetch the persisted task summaries without overlapping requests. */
async function refreshTasks() {
  if (app.taskFetching) return;
  app.taskFetching = true;
  try {
    const payload = await api.getTasks(50);
    app.tasks = Array.isArray(payload?.tasks) ? payload.tasks : [];
    app.tasksEnabled = Boolean(payload?.enabled);
    renderTaskTable();
  } catch {
    // The task table is diagnostic; a transient API failure must not disturb the run UI.
  } finally {
    app.taskFetching = false;
  }
}

/** Poll the task table while a run is active so WORKING rows appear before the run ends. */
function startTaskPolling() {
  if (app.taskTimer) return;
  app.taskTimer = window.setInterval(() => {
    if (store.running) refreshTasks();
  }, 3000);
}

/** Return the display label of one client run status. */
function clientRunStatusLabel(status) {
  const value = typeof status === "string" ? status : "";
  if (!value) return t("value.unknown");
  const key = `runStatus.${value}`;
  const text = t(key);
  return text === key ? value : text;
}

/** Return the display label of one final state. */
function finalStateText(state) {
  if (typeof state !== "string" || !state) return EM_DASH;
  const key = `finalState.${state}`;
  const text = t(key);
  return text === key ? state : text;
}

/** Render the persisted client run table from ``app.clientRuns``. */
function renderClientRunTable() {
  const body = $("client-run-table-body");
  if (!body) return;
  body.replaceChildren();
  const count = app.clientRuns.length;
  $("client-history-count").textContent = countOf(
    count,
    "history.runCountOne",
    "history.runCount",
  );

  const note = $("client-history-note");
  const scroll = body.closest(".task-table-scroll");
  if (!app.clientRunsEnabled) {
    note.hidden = false;
    note.textContent = t("history.runsDisabled");
    if (scroll) scroll.hidden = true;
    updateHistoryCount();
    return;
  }
  if (count === 0) {
    note.hidden = false;
    note.textContent = t("history.runsEmpty");
    if (scroll) scroll.hidden = true;
    updateHistoryCount();
    return;
  }
  note.hidden = true;
  if (scroll) scroll.hidden = false;

  for (const run of app.clientRuns) {
    const row = el("tr");
    const detailButton = el("button", "secondary-button client-run-view", t("history.view"));
    detailButton.type = "button";
    detailButton.addEventListener("click", () => showClientRunDetail(run.runId));
    const detailCell = el("td", "client-run-detail-cell");
    detailCell.appendChild(detailButton);
    append(
      row,
      el("td", "task-id-cell", shortId(run.runId)),
      el("td", "task-context-cell", run.conversationId ? shortId(run.conversationId) : EM_DASH),
      el("td", "", run.scenario || EM_DASH),
      el("td", "task-state-cell", clientRunStatusLabel(run.status)),
      el("td", "", finalStateText(run.finalState)),
      el("td", "task-time-cell", taskTime(run.createdAt)),
      el("td", "task-summary-cell", truncate(run.userRequest, 160) || EM_DASH),
      detailCell,
    );
    body.appendChild(row);
  }
  updateHistoryCount();
}

/** Fetch the persisted client runs without overlapping requests. */
async function refreshClientRuns() {
  if (app.clientRunFetching) return;
  app.clientRunFetching = true;
  try {
    const payload = await api.getClientRuns(50);
    app.clientRuns = Array.isArray(payload?.runs) ? payload.runs : [];
    app.clientRunsEnabled = Boolean(payload?.enabled);
    renderClientRunTable();
    if (app.clientRunDetailId) await showClientRunDetail(app.clientRunDetailId, { reload: true });
  } catch {
    // The client history is diagnostic; a transient API failure must not disturb the run UI.
  } finally {
    app.clientRunFetching = false;
  }
}

/** Poll the client run table while a run is active. */
function startClientRunPolling() {
  if (app.clientRunTimer) return;
  app.clientRunTimer = window.setInterval(() => {
    if (store.running) refreshClientRuns();
  }, 3000);
}

/** Load and show one persisted client run detail. */
async function showClientRunDetail(runId, options = {}) {
  try {
    const detail = await api.getClientRun(runId);
    if (!detail) return;
    app.clientRunDetailId = runId;
    renderClientRunDetail(detail);
    $("client-run-detail").hidden = false;
    if (!options.reload) $("client-run-detail").scrollIntoView({ block: "nearest" });
  } catch {
    // A run removed between the list and the detail request simply keeps the old panel.
  }
}

/** Render one client run detail: metadata, turns, negotiation audit and transcript links. */
function renderClientRunDetail(detail) {
  const body = $("client-run-detail-body");
  if (!body) return;
  body.replaceChildren();
  $("client-run-detail-title").textContent = t("header.run", { id: shortId(detail.runId) });

  const facts = el("dl", "client-run-meta");
  append(
    facts,
    factRow(t("history.conversation"), detail.conversationId || EM_DASH),
    factRow(t("history.status"), clientRunStatusLabel(detail.status)),
    factRow(t("history.finalState"), finalStateText(detail.finalState)),
    factRow(t("history.scenario"), detail.scenario || EM_DASH),
    factRow(
      t("history.context"),
      detail.serverContextId ? shortId(detail.serverContextId) : EM_DASH,
    ),
    factRow(t("history.task"), detail.serverTaskId ? shortId(detail.serverTaskId) : EM_DASH),
    factRow(t("history.error"), detail.error || EM_DASH),
  );
  body.appendChild(facts);

  const turns = Array.isArray(detail.turns) ? detail.turns : [];
  body.appendChild(
    el("h4", "client-run-subtitle", t("history.turns", { count: turns.length })),
  );
  const turnList = el("ol", "client-turn-list");
  for (const turn of turns) {
    const item = el("li", "client-turn");
    append(
      item,
      el("span", `client-turn-role role-${turn.role || "system"}`, turn.role || "system"),
      el("span", "client-turn-content", turn.content || EM_DASH),
    );
    turnList.appendChild(item);
  }
  body.appendChild(turnList);

  const events = Array.isArray(detail.negotiationEvents) ? detail.negotiationEvents : [];
  body.appendChild(
    el("h4", "client-run-subtitle", t("history.negotiationEvents", { count: events.length })),
  );
  if (events.length === 0) {
    body.appendChild(el("p", "task-history-note", t("history.noNegotiation")));
  } else {
    const table = el("table", "client-event-table");
    const head = el("thead");
    const headRow = el("tr");
    for (const label of [
      t("history.eventIndex"),
      t("history.eventDirection"),
      t("history.eventRound"),
      t("history.eventPerformative"),
      t("history.eventDecision"),
      t("history.eventRationale"),
    ]) {
      headRow.appendChild(el("th", "", label));
    }
    append(head, headRow);
    const rows = el("tbody");
    for (const event of events) {
      const row = el("tr");
      append(
        row,
        el("td", "", event.seq ?? EM_DASH),
        el("td", "", event.direction || EM_DASH),
        el("td", "", event.round ?? EM_DASH),
        el("td", "", event.performative || EM_DASH),
        el("td", "", event.decisionAction || EM_DASH),
        el("td", "client-event-rationale", event.rationale || EM_DASH),
      );
      rows.appendChild(row);
    }
    append(table, head, rows);
    body.appendChild(table);
  }

  const links = el("p", "client-run-links");
  if (detail.transcriptJson) {
    const link = el("a", "", t("history.transcriptJson"));
    link.href = `/api/runs/${encodeURIComponent(detail.runId)}/transcript.json`;
    link.target = "_blank";
    link.rel = "noreferrer";
    links.appendChild(link);
  }
  if (detail.transcriptMarkdown) {
    const link = el("a", "", t("history.transcriptMarkdown"));
    link.href = `/api/runs/${encodeURIComponent(detail.runId)}/transcript.md`;
    link.target = "_blank";
    link.rel = "noreferrer";
    links.appendChild(link);
  }
  if (links.childNodes.length > 0) body.appendChild(links);
}

/** Build one detail fact row. */
function factRow(label, value) {
  const wrapper = el("div", "fact");
  append(wrapper, el("dt", "fact-label", label), el("dd", "fact-value", value));
  return wrapper;
}

/** Hide the client run detail panel. */
function closeClientRunDetail() {
  app.clientRunDetailId = null;
  $("client-run-detail").hidden = true;
  $("client-run-detail-body").replaceChildren();
}

/** Render the persisted conversation table from ``app.conversations``. */
function renderConversationTable() {
  const body = $("conversation-table-body");
  if (!body) return;
  body.replaceChildren();
  const count = app.conversations.length;
  $("conversation-history-count").textContent = countOf(
    count,
    "history.conversationCountOne",
    "history.conversationCount",
  );

  const note = $("conversation-history-note");
  const scroll = body.closest(".task-table-scroll");
  if (!app.conversationsEnabled) {
    note.hidden = false;
    note.textContent = t("history.conversationsDisabled");
    if (scroll) scroll.hidden = true;
    updateHistoryCount();
    return;
  }
  if (count === 0) {
    note.hidden = false;
    note.textContent = t("history.conversationsEmpty");
    if (scroll) scroll.hidden = true;
    updateHistoryCount();
    return;
  }
  note.hidden = true;
  if (scroll) scroll.hidden = false;

  for (const conversation of app.conversations) {
    const row = el("tr");
    const detailButton = el("button", "secondary-button client-run-view", t("history.view"));
    detailButton.type = "button";
    detailButton.addEventListener("click", () =>
      showConversationDetail(conversation.conversationId),
    );
    const detailCell = el("td", "client-run-detail-cell");
    detailCell.appendChild(detailButton);
    append(
      row,
      el("td", "task-id-cell", shortId(conversation.conversationId)),
      el("td", "", String(conversation.turnCount ?? 0)),
      el("td", "", String(conversation.runCount ?? 0)),
      el("td", "task-time-cell", taskTime(conversation.updatedAt)),
      detailCell,
    );
    body.appendChild(row);
  }
  updateHistoryCount();
}

/** Fetch the persisted conversations without overlapping requests. */
async function refreshConversations() {
  if (app.conversationFetching) return;
  app.conversationFetching = true;
  try {
    const payload = await api.getConversations(50);
    app.conversations = Array.isArray(payload?.conversations) ? payload.conversations : [];
    app.conversationsEnabled = Boolean(payload?.enabled);
    renderConversationTable();
    if (app.conversationDetailId) {
      await showConversationDetail(app.conversationDetailId, { reload: true });
    }
  } catch {
    // The conversation history is diagnostic; a transient API failure must not disturb the run UI.
  } finally {
    app.conversationFetching = false;
  }
}

/** Poll the conversation table while a run is active. */
function startConversationPolling() {
  if (app.conversationTimer) return;
  app.conversationTimer = window.setInterval(() => {
    if (store.running) refreshConversations();
  }, 3000);
}

/** Load and show one persisted conversation. */
async function showConversationDetail(conversationId, options = {}) {
  try {
    const detail = await api.getConversation(conversationId);
    if (!detail) return;
    app.conversationDetailId = conversationId;
    renderConversationDetail(detail);
    $("conversation-detail").hidden = false;
    if (!options.reload) $("conversation-detail").scrollIntoView({ block: "nearest" });
  } catch {
    // A conversation removed between the list and the detail request keeps the old panel.
  }
}

/** Render one conversation: metadata, turns and runs. */
function renderConversationDetail(detail) {
  const body = $("conversation-detail-body");
  if (!body) return;
  body.replaceChildren();
  $("conversation-detail-title").textContent = t("history.conversationTitleId", {
    id: shortId(detail.conversationId),
  });

  const facts = el("dl", "client-run-meta");
  append(
    facts,
    factRow(t("history.conversation"), detail.conversationId || EM_DASH),
    factRow(t("history.externalId"), detail.externalId || EM_DASH),
    factRow(t("history.userId"), detail.userId || EM_DASH),
    factRow(t("history.columnUpdated"), taskTime(detail.updatedAt)),
  );
  body.appendChild(facts);

  const turns = Array.isArray(detail.turns) ? detail.turns : [];
  body.appendChild(
    el("h4", "client-run-subtitle", t("history.turns", { count: turns.length })),
  );
  if (turns.length === 0) {
    body.appendChild(el("p", "task-history-note", t("history.noTurns")));
  } else {
    const turnList = el("ol", "client-turn-list");
    for (const turn of turns) {
      const item = el("li", "client-turn");
      append(
        item,
        el("span", `client-turn-role role-${turn.role || "system"}`, turn.role || "system"),
        el("span", "client-turn-content", turn.content || EM_DASH),
      );
      turnList.appendChild(item);
    }
    body.appendChild(turnList);
  }

  const runs = Array.isArray(detail.runs) ? detail.runs : [];
  body.appendChild(el("h4", "client-run-subtitle", t("history.runsTitle")));
  if (runs.length === 0) {
    body.appendChild(el("p", "task-history-note", t("history.runsEmpty")));
  } else {
    const table = el("table", "client-event-table");
    const head = el("thead");
    const headRow = el("tr");
    for (const label of [
      t("history.columnRun"),
      t("history.columnScenario"),
      t("history.columnStatus"),
      t("history.columnStarted"),
    ]) {
      headRow.appendChild(el("th", "", label));
    }
    append(head, headRow);
    const rows = el("tbody");
    for (const run of runs) {
      const row = el("tr");
      append(
        row,
        el("td", "", shortId(run.runId)),
        el("td", "", run.scenario || EM_DASH),
        el("td", "", clientRunStatusLabel(run.status)),
        el("td", "task-time-cell", taskTime(run.createdAt)),
      );
      rows.appendChild(row);
    }
    append(table, head, rows);
    body.appendChild(table);
  }
}

/** Hide the conversation detail panel. */
function closeConversationDetail() {
  app.conversationDetailId = null;
  $("conversation-detail").hidden = true;
  $("conversation-detail-body").replaceChildren();
}

/** Load the process metadata and restore a run from the URL hash. */
async function boot() {
  initLocale({ search: location.search });
  bindControls();
  renderShortcuts();
  renderLocaleSelect();
  setHistoryOpen(false);
  const savedTheme = (() => {
    try {
      return localStorage.getItem("a2at-ran-theme") || "auto";
    } catch {
      return "auto";
    }
  })();
  applyTheme(savedTheme);
  // The run summary is the default inspector on wide screens; narrow layouts start uncluttered.
  app.inspectorOpen = !window.matchMedia("(max-width: 1119px)").matches;
  applyInspectorLayout();
  startPolling();
  startTicker();
  startTaskPolling();
  startClientRunPolling();
  startConversationPolling();
  refreshTasks();
  refreshClientRuns();
  refreshConversations();
  scheduleRender();

  onLocaleChange(() => {
    renderShortcuts();
    renderLocaleSelect();
    updateLocaleHint();
    scheduleRender();
  });

  try {
    app.meta = await api.getMeta();
    setProcessLanguage(app.meta.language);
    store.setMeta(app.meta);
    populateMeta(app.meta);
    renderLocaleSelect();
    updateLocaleHint();
    applyStatic(document);
  } catch (error) {
    app.startError = t("notify.metadataUnavailableDetail", {
      error: String(error?.message ?? error),
    });
  }
  app.loading = false;
  const runId = runIdFromHash();
  if (runId) await resumeRun(runId);
  else scheduleRender();
}

installUiToken();
boot();
