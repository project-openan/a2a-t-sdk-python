/** Presentation page controller.
 *
 * The page is a stage plus an evidence column. Every stage action is driven by the same SSE
 * frames as the Run Explorer; the player only changes their pacing. Two frames can stop the show
 * for a human: a negotiation decision and a change approval.
 */

import {
  copyFor,
  format,
  scenarioKind,
  scenarioLabel,
  scenarioStory,
  scenarioTag,
} from "../copy.js";
import {
  applyFrame,
  continuityModel,
  createState,
  endingOf,
  negotiationExpected,
  pendingHuman,
  stageOf,
} from "./state.js";
import { STEP_KEYS, sceneFor } from "./scenes.js";
import { createPlayer } from "./player.js";
import { createStage, setStageCopy } from "./stage.js";
import { createRail } from "./rail.js";
import { createTrace, entryOfRow } from "./trace.js";
import { createWire } from "./wire.js";
import { renderIntent } from "./intent.js";
import {
  approvalCard,
  clock,
  decisionCard,
  decisionPayload,
  intentCard,
  remainingSeconds,
} from "./decisions.js";
import { clear, el } from "./dom.js";

const byId = (id) => document.getElementById(id);

//: Evidence rows are applied one at a time so a batch of server spans does not flash the panels.
const EVIDENCE_MS = 60;

//: Story beats that should animate once; a repeat is evidence only.
const REPEATED_SILENT = new Set([
  "request",
  "understand",
  "intent-review",
  "send",
  "strategy",
  "collect-start",
  "collect-progress",
  "collect-done",
  "approval-ready",
  "approval",
  "approval-pure",
  "apply",
  "report",
  "end",
  "abort",
  "approval-rejected",
]);

const els = {
  banner: byId("connection-banner"),
  bannerText: byId("connection-text"),
  brandKicker: byId("brand-kicker"),
  title: byId("title"),
  badge: byId("badge"),
  runPill: byId("run-pill"),
  scenariosLabel: byId("scenarios-label"),
  protocolLabel: byId("protocol-label"),
  themeToggle: byId("theme-toggle"),
  protocolRow: byId("protocol-row"),
  protocolSwitch: byId("protocol-switch"),
  protocolOptions: Array.from(document.querySelectorAll("#protocol-switch .pbtn")),
  protocolHint: byId("protocol-hint"),
  wireFormat: byId("wire-format"),
  scenarioSelect: byId("scenario-select"),
  scenarioDesc: byId("scenario-desc"),
  continuity: byId("continuity"),
  continuityBadge: byId("continuity-badge"),
  continuityChange: byId("continuity-change"),
  requestCard: byId("request-card"),
  requestCardLabel: byId("request-card-label"),
  requestCardText: byId("request-card-text"),
  editRequest: byId("edit-request"),
  editLabel: byId("edit-label"),
  cancelRun: byId("cancel-run"),
  cancelLabel: byId("cancel-label"),
  drawer: byId("drawer"),
  requestTitle: byId("request-title"),
  requestText: byId("request-text"),
  requestHint: byId("request-hint"),
  requestError: byId("request-error"),
  start: byId("start"),
  startQuick: byId("start-quick"),
  reset: byId("reset-request"),
  steps: byId("steps"),
  stage: byId("stage"),
  bubbles: {
    eng: byId("b-eng"),
    oss: byId("b-oss"),
    nms: byId("b-nms"),
    cells: byId("b-cells"),
  },
  actors: {
    eng: byId("a-eng"),
    oss: byId("a-oss"),
    nms: byId("a-nms"),
    cells: byId("a-cells"),
  },
  states: {
    eng: byId("s-eng"),
    oss: byId("s-oss"),
    nms: byId("s-nms"),
    cells: byId("s-cells"),
  },
  nmsBar: byId("nms-bar"),
  approval: byId("a-approval"),
  approvalState: byId("approval-state"),
  cellsClockMain: byId("cells-clock-main"),
  cellsClockSub: byId("cells-clock-sub"),
  cellsFigure: byId("cells-figure"),
  say: byId("say"),
  sayKicker: byId("say-kicker"),
  sayText: byId("say-text-content"),
  sayDetail: byId("say-detail"),
  choices: byId("choices"),
  pbAuto: byId("pb-auto"),
  pbStep: byId("pb-step"),
  speed: byId("speed"),
  loop: byId("loop"),
  prev: byId("prev"),
  pause: byId("pause"),
  next: byId("next"),
  pausedTag: byId("paused-tag"),
  intentBody: byId("intent-body"),
  intentTitle: byId("intent-title"),
  intentSub: byId("intent-sub"),
  negoTitle: byId("nego-title"),
  negoStatus: byId("nego-status"),
  roundDots: byId("round-dots"),
  tabBusiness: byId("tab-business"),
  tabWire: byId("tab-wire"),
  turns: byId("turns"),
  wire: byId("wire"),
  wireFormat: byId("wire-format"),
  wireT: byId("wire-t"),
  wireX: byId("wire-x"),
  wireA: byId("wire-a"),
  wireTitle: byId("wire-title"),
  wireCode: byId("wire-code"),
  wireNote: byId("wire-note"),
  negoEmpty: byId("nego-empty"),
  traceTitle: byId("trace-title"),
  traceSub: byId("trace-sub"),
  traceCalls: byId("trace-calls"),
  traceCallsLabel: byId("trace-calls-label"),
  traceToggle: byId("trace-toggle"),
  traceBody: byId("trace-body"),
  traceDays: byId("trace-days"),
  traceRows: byId("trace-rows"),
  traceEmpty: byId("trace-empty"),
  tech: byId("tech"),
  techSummary: byId("tech-summary"),
  techLog: byId("tech-log"),
  belowFlow: byId("below-flow"),
  belowTitle: byId("below-title"),
  belowNote: byId("below-note"),
  showsTitle: byId("shows-title"),
  showsList: byId("shows-list"),
  boothTitle: byId("booth-title"),
  boothList: byId("booth-list"),
  notShownTitle: byId("not-shown-title"),
  notShownList: byId("not-shown-list"),
  footer: byId("footer"),
  legendChat: byId("legend-chat"),
  legendTask: byId("legend-task"),
  legendStatus: byId("legend-status"),
  legendNeg: byId("legend-neg"),
  legendCfg: byId("legend-cfg"),
  legendData: byId("legend-data"),
  splitter: byId("splitter"),
  traceSplitter: byId("trace-splitter"),
};

const ui = {
  meta: null,
  copy: copyFor("zh-CN"),
  brand: document.documentElement.dataset.brand || "orange",
  selected: null,
  protocol: "a2a-t",
  compareSaved: null,
  resultRevealed: null,
  resultCard: null,
  resultCardRun: null,
  view: createState(),
  shadow: createState(),
  frameLog: [],
  seenFrames: new Set(),
  lastScene: null,
  lastQueuedScene: null,
  //: Highest story step reached in the current playhead; prevents a late phase from rewinding it.
  maxStep: 0,
  terminalApplied: null,
  source: null,
  mieSource: null,
  mieEnabled: false,
  busy: false,
  tab: "business",
  selectedTurn: -1,
  wireEntry: null,
  wireFormat: "t",
  wirePinned: false,
  showCalls: false,
  submitting: false,
  counterOpen: false,
  counterText: "",
  selectedRegion: "",
  cardKey: null,
  cardError: "",
  deadline: null,
  countdownTimer: null,
  rendering: false,
  looping: false,
  renderingIntent: false,
};

const stage = createStage(els);
const rail = createRail(
  {
    roundDots: els.roundDots,
    status: els.negoStatus,
    empty: els.negoEmpty,
    turns: els.turns,
    wire: els.wire,
    wireTitle: els.wireTitle,
    wireCode: els.wireCode,
    wireNote: els.wireNote,
  },
  () => ui.copy,
  () => ui,
);
const wire = createWire(
  {
    formatT: els.wireT,
    formatX: els.wireX,
    formatA: els.wireA,
    title: els.wireTitle,
    code: els.wireCode,
    note: els.wireNote,
  },
  () => ui.copy,
  () => ui,
);
const trace = createTrace(
  { body: els.traceBody, days: els.traceDays, rows: els.traceRows, empty: els.traceEmpty },
  () => ui.copy,
  (row) => {
    const entry = entryOfRow(row);
    if (!row.entry && row.titleKey && ui.copy.trace[row.titleKey]) {
      entry.title = ui.copy.trace[row.titleKey];
    }
    ui.wirePinned = true;
    wire.showEntry(entry);
    setTab("wire");
  },
  () => ui.showCalls,
);

const player = createPlayer({
  onApply: (frame) => {
    applyFrame(ui.view, frame);
    render();
  },
  onRewind: (index) => {
    rebuild(index);
  },
  onChange: () => renderControls(),
  // A gate scene is queued with ``hold`` when the approval frame arrives. If the engineer answers
  // while that scene is still waiting behind earlier beats, the decision is already known when it
  // is finally applied; do not hold or repaint the stale "waiting" card.
  holds: (scene) => scene.key !== "approval-pure" || ui.view.mieResolved === null,
  onScene: (scene, { hold } = {}) => {
    if (scene.key === "approval-pure" && hold === false) return;
    // Evidence frames (an unmapped trace span, a plan event) update the panels; they are not
    // story beats and must not rewind the step bar to the step that was current when they
    // arrived in the queue.
    if (scene.key !== "trace" && scene.key !== "event") ui.lastScene = scene;
    renderSteps();
    if (!scene.silent) stage.scene(scene, ui.view, ui.copy);
    renderNarrator();
    handleLoop(scene);
  },
});

boot();

async function boot() {
  installTokenFromUrl();
  bindStaticEvents();
  try {
    const response = await fetch("/api/meta");
    if (response.status === 401) {
      showError(ui.copy.authRequired);
      return;
    }
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    ui.meta = await response.json();
  } catch {
    showError(ui.copy.startFailed);
    return;
  }
  ui.copy = copyFor(ui.meta.language);
  ui.brand = currentBrand();
  ui.selected = ui.meta.defaultScenario || ui.meta.scenarios?.[0] || null;
  ui.protocol = ui.meta.protocol || ui.meta.defaultProtocol || "a2a-t";
  const bootScenarios = scenariosForProtocol(ui.protocol);
  if (!bootScenarios.includes(ui.selected)) ui.selected = bootScenarios[0] || null;
  ui.mieEnabled = Boolean(ui.meta.mieEnabled);
  els.requestText.value = ui.meta.scenarioRequests?.[ui.selected] || "";
  applyStaticCopy();
  renderProtocol();
  renderPresets();
  renderBottomCopy();
  subscribeMie();
  render();
}

function bindStaticEvents() {
  for (const button of els.protocolOptions || []) {
    button.addEventListener("click", () => selectProtocol(button.dataset.protocol));
  }
  els.start.addEventListener("click", () => startRun());
  if (els.startQuick) els.startQuick.addEventListener("click", () => startRun());
  if (els.themeToggle) els.themeToggle.addEventListener("click", toggleBrand);
  els.reset.addEventListener("click", resetRequest);
  els.editRequest.addEventListener("click", () => {
    const open = els.drawer.hidden;
    els.drawer.hidden = !open;
    els.editRequest.setAttribute("aria-expanded", String(open));
    if (open) els.requestText.focus();
  });
  if (els.cancelRun) els.cancelRun.addEventListener("click", () => cancelRun());
  els.requestText.addEventListener("input", () => {
    hideError();
    if (!ui.busy && ui.view.status === "idle") renderIntentPanel();
    renderRequest();
  });
  els.requestText.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) {
      event.preventDefault();
      startRun();
    }
  });
  els.pbAuto.addEventListener("change", () => player.setMode("auto"));
  els.pbStep.addEventListener("change", () => player.setMode("step"));
  els.speed.addEventListener("change", () => player.setSpeed(els.speed.value));
  els.loop.addEventListener("change", () => {
    if (els.loop.checked && !ui.busy && ui.view.status === "idle") startRun();
  });
  els.next.addEventListener("click", onNext);
  els.prev.addEventListener("click", () => player.previous());
  els.pause.addEventListener("click", () => {
    const status = player.status();
    if (status.paused) player.resume();
    else player.pause();
  });
  els.tabBusiness.addEventListener("click", () => setTab("business"));
  els.tabWire.addEventListener("click", () => setTab("wire"));
  els.requestText.addEventListener("keydown", (event) => {
    if ((event.ctrlKey || event.metaKey) && event.key === "Enter") {
      event.preventDefault();
      startRun();
    }
  });
  els.wireT.addEventListener("click", () => {
    ui.wireFormat = "t";
    wire.render(ui.view);
  });
  els.wireX.addEventListener("click", () => {
    if (els.wireX.disabled) return;
    ui.wireFormat = "x";
    wire.render(ui.view);
  });
  els.wireA.addEventListener("click", () => {
    if (els.wireA.disabled) return;
    ui.wireFormat = "a";
    wire.render(ui.view);
  });
  els.traceCalls.addEventListener("change", () => {
    ui.showCalls = els.traceCalls.checked;
    trace.render(ui.view);
  });
  els.traceToggle.addEventListener("click", () => {
    const open = els.traceToggle.getAttribute("aria-expanded") === "true";
    els.traceToggle.setAttribute("aria-expanded", String(!open));
    els.traceBody.hidden = open;
    els.rowsHidden = open;
  });
  bindSplitter();
  bindTraceSplitter();
  document.addEventListener("keydown", (event) => {
    if (event.target.closest && event.target.closest("textarea,select,input")) return;
    if (event.key === "ArrowRight" && !els.next.disabled && !els.next.hidden) {
      event.preventDefault();
      els.next.click();
    }
    if (event.key === "ArrowLeft" && !els.prev.disabled) {
      event.preventDefault();
      els.prev.click();
    }
    if (event.key === " " && !(event.target.closest && event.target.closest("button,label"))) {
      event.preventDefault();
      els.pause.click();
    }
  });
}

function onNext() {
  const status = player.status();
  if (status.waitingNext) {
    player.next();
    return;
  }
  if (!ui.busy && ui.view.status === "idle") startRun();
}

function setTab(tab) {
  ui.tab = tab;
  els.tabBusiness.setAttribute("aria-pressed", String(tab === "business"));
  els.tabWire.setAttribute("aria-pressed", String(tab === "wire"));
  render();
}

function currentBrand() {
  return document.documentElement.dataset.brand === "huawei" ? "huawei" : "orange";
}

function brandLabel(copy, brand) {
  return brand === "huawei" ? copy.themeHuawei : copy.themeOrange;
}

/** Apply one brand palette, optionally remembering the engineer's choice. */
function applyBrand(brand, { persist = false } = {}) {
  ui.brand = brand === "huawei" ? "huawei" : "orange";
  document.documentElement.dataset.brand = ui.brand;
  if (persist) {
    try {
      localStorage.setItem("a2at-ran-brand", ui.brand);
    } catch {
      // Private-browsing storage failures must not block the visual switch.
    }
  }
  renderThemeToggle();
}

function toggleBrand() {
  applyBrand(currentBrand() === "orange" ? "huawei" : "orange", { persist: true });
}

function renderThemeToggle() {
  if (!els.themeToggle) return;
  els.themeToggle.textContent = `${ui.copy.themeLabel}: ${brandLabel(ui.copy, currentBrand())}`;
  els.themeToggle.setAttribute("aria-label", ui.copy.themeAria);
}

function applyStaticCopy() {
  const copy = ui.copy;
  document.documentElement.lang = copy.lang;
  document.title = copy.title;
  renderThemeToggle();
  els.brandKicker.textContent = copy.kicker;
  els.title.textContent = copy.title;
  els.badge.textContent = copy.badge;
  els.protocolLabel.textContent = copy.protocol.label;
  els.runPill.textContent =
    ui.meta?.modelMode === "offline" ? copy.runPillOffline : copy.runPillLive;
  els.scenariosLabel.textContent = copy.scenarioLabel;
  els.editLabel.textContent = copy.editRequest;
  if (els.cancelLabel) {
    els.cancelLabel.textContent = ui.cancelling ? copy.cancelling : copy.cancelRequest;
  }
  if (els.cancelRun) els.cancelRun.setAttribute("aria-label", copy.cancelRequest);
  els.requestCardLabel.textContent = copy.requestCardLabel;
  els.requestTitle.textContent = copy.requestTitle;
  els.requestHint.textContent = copy.requestHint;
  els.reset.textContent = copy.reset;
  els.start.textContent = copy.start;
  els.bannerText.textContent = copy.connectionLost;
  els.intentTitle.textContent = copy.intent.title;
  els.intentSub.textContent = copy.intent.sub;
  els.negoTitle.textContent = copy.negotiation.title;
  els.tabBusiness.textContent = copy.negotiation.business;
  els.tabWire.textContent = copy.negotiation.wire;
  els.traceTitle.textContent = copy.trace.title;
  els.traceSub.textContent = copy.trace.sub;
  if (els.traceCallsLabel) els.traceCallsLabel.textContent = copy.trace.callsFilter;
  els.techSummary.textContent = copy.tech;
  els.legendChat.textContent = copy.legend.chat;
  els.legendTask.textContent = copy.legend.task;
  els.legendStatus.textContent = copy.legend.status;
  els.legendNeg.textContent = copy.legend.neg;
  els.legendCfg.textContent = copy.legend.cfg;
  els.legendData.textContent = copy.legend.data;
  setStageCopy(copy);
  const skills = byId("oss-skills");
  if (skills) {
    skills.replaceChildren();
    for (const skill of copy.roles.ossSkills) skills.append(el("span", "", skill));
  }
}

function renderBottomCopy() {
  const copy = ui.copy;
  els.belowTitle.textContent = copy.below.flowTitle;
  els.belowFlow.replaceChildren();
  for (const card of copy.below.flow) {
    const node = el("article", "below-card");
    node.append(
      el("span", "when", card.when),
      el("h3", "", card.title),
      el("p", "", card.body),
    );
    if (card.hitl) node.append(el("span", "hitl", card.hitl));
    els.belowFlow.append(node);
  }
  els.showsTitle.textContent = copy.below.showsTitle;
  els.boothTitle.textContent = copy.below.boothTitle;
  els.notShownTitle.textContent = copy.below.notShownTitle;
  clear(els.showsList);
  clear(els.boothList);
  clear(els.notShownList);
  for (const item of copy.below.shows) els.showsList.append(el("li", "", item));
  for (const item of copy.below.booth) els.boothList.append(el("li", "", item));
  for (const item of copy.below.notShown) els.notShownList.append(el("li", "", item));
  els.belowNote.textContent = copy.below.note;
  if (els.footer) els.footer.textContent = copy.footer;
}

/** Return the scenarios the selected request form supports. */
function scenariosForProtocol(protocol) {
  return ui.meta?.scenariosByProtocol?.[protocol] || ui.meta?.scenarios || [];
}

/** Switch between the A2A-T extension and standard A2A for the next run. */
function selectProtocol(protocol) {
  if (ui.busy || protocol === ui.protocol) return;
  if (!(ui.meta?.protocols || []).includes(protocol)) return;
  ui.protocol = protocol;
  const scenarios = scenariosForProtocol(protocol);
  if (!scenarios.includes(ui.selected)) {
    ui.selected = scenarios[0] || ui.selected;
  }
  renderPresets();
  // Reuse the scenario reset path so no frame from the previous request form stays on stage.
  selectScenario(ui.selected);
  renderProtocol();
}

function renderPresets() {
  const select = els.scenarioSelect;
  if (!select) return;
  select.replaceChildren();
  for (const name of scenariosForProtocol(ui.protocol)) {
    const option = el("option", "", `${scenarioLabel(ui.copy, name)} · ${scenarioTag(ui.copy, name)}`);
    option.value = name;
    option.title = scenarioStory(ui.copy, name);
    select.append(option);
  }
  select.value = ui.selected;
  select.disabled = Boolean(ui.busy);
  if (!select.dataset.bound) {
    select.addEventListener("change", () => {
      if (ui.busy) {
        select.value = ui.selected;
        return;
      }
      selectScenario(select.value);
    });
    select.dataset.bound = "1";
  }
  renderScenarioDescription();
}

function renderScenarioDescription() {
  if (!els.scenarioDesc) return;
  els.scenarioDesc.textContent = scenarioStory(ui.copy, ui.selected);
}

function selectScenario(name, { start = false } = {}) {
  closeStream();
  player.reset();
  ui.selected = name;
  renderProtocol();
  ui.view = createState();
  ui.shadow = createState();
  ui.frameLog = [];
  ui.seenFrames = new Set();
  ui.lastScene = null;
  ui.lastQueuedScene = null;
  ui.maxStep = 0;
  ui.terminalApplied = null;
  ui.tab = "business";
  ui.selectedTurn = -1;
  ui.wireEntry = null;
  ui.wireFormat = "t";
  ui.wirePinned = false;
  ui.counterOpen = false;
  ui.selectedRegion = "";
  ui.looping = false;
  els.requestText.value = ui.meta?.scenarioRequests?.[name] || "";
  stage.reset();
  trace.reset();
  hideError();
  if (els.scenarioSelect) els.scenarioSelect.value = name;
  renderScenarioDescription();
  render();
  if (start) startRun();
}

function resetRequest() {
  if (ui.busy) return;
  els.requestText.value = ui.meta?.scenarioRequests?.[ui.selected] || "";
  hideError();
  renderIntentPanel();
  renderRequest();
}

async function startRun() {
  if (ui.busy) {
    showError(ui.copy.running);
    return;
  }
  const request = els.requestText.value.trim();
  hideError();
  closeStream();
  player.reset();
  ui.view = createState();
  ui.shadow = createState();
  ui.frameLog = [];
  ui.seenFrames = new Set();
  ui.lastScene = null;
  ui.lastQueuedScene = null;
  ui.maxStep = 0;
  ui.terminalApplied = null;
  ui.tab = "business";
  ui.selectedTurn = -1;
  ui.wireEntry = null;
  ui.wireFormat = "t";
  ui.wirePinned = false;
  ui.counterOpen = false;
  ui.counterText = "";
  ui.selectedRegion = "";
  ui.submitting = false;
  ui.cancelling = false;
  ui.deadline = null;
  ui.looping = false;
  stage.reset();
  trace.reset();
  clear(els.techLog);
  setBusy(true);
  // Show the engineer writing immediately. Starting a run can take a few seconds (session claim
  // in live mode); waiting for the first SSE frame made the page look dead.
  const defaultRequest = ui.meta?.scenarioRequests?.[ui.selected] || "";
  ui.view.userRequest = request;
  ui.view.intentText = request;
  ui.view.started = true;
  ui.view.status = "running";
  ui.shadow.userRequest = request;
  ui.shadow.intentText = request;
  stage.showRequest(ui.copy, request);
  render();

  try {
    const response = await fetch("/api/runs", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        scenario: ui.selected,
        // The textarea shows the concise presentation wording. While it is unchanged, let the
        // backend use the richer catalog prompt that the live model extracts Task-T from.
        userRequest: request && request !== defaultRequest ? request : undefined,
        protocol: ui.protocol,
      }),
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(payload.error || `HTTP ${response.status}`);
    ui.view.runId = payload.runId;
    subscribe(payload.runId);
    render();
  } catch (error) {
    setBusy(false);
    ui.view = createState();
    ui.view.scenario = ui.selected;
    render();
    const detail = error instanceof Error && error.message ? ` (${error.message})` : "";
    showError(`${ui.copy.startFailed}${detail}`);
  }
}

/** Stop the running task. The backend cancels the remote Task best effort and the local run. */
async function cancelRun() {
  if (!ui.busy || ui.cancelling || !ui.view.runId) return;
  ui.cancelling = true;
  if (els.cancelLabel) els.cancelLabel.textContent = ui.copy.cancelling;
  if (els.cancelRun) els.cancelRun.disabled = true;
  try {
    const response = await fetch(`/api/runs/${encodeURIComponent(ui.view.runId)}/cancel`, {
      method: "POST",
    });
    // A run that just reached its terminal frame is already stopped; the SSE finished frame will
    // release the page, so 409 is not an error here.
    if (!response.ok && response.status !== 409) {
      const payload = await response.json().catch(() => ({}));
      throw new Error(payload.error || `HTTP ${response.status}`);
    }
  } catch (error) {
    ui.cancelling = false;
    if (els.cancelLabel) els.cancelLabel.textContent = ui.copy.cancelRequest;
    if (els.cancelRun) els.cancelRun.disabled = false;
    const detail = error instanceof Error && error.message ? ` (${error.message})` : "";
    showError(`${ui.copy.cancelFailed}${detail}`);
  }
}

function subscribe(runId) {
  closeStream();
  const source = new EventSource(`/api/runs/${runId}/events`);
  ui.source = source;
  source.onopen = hideBanner;
  source.onerror = () => {
    if (ui.source === source && !ui.view.finished) showBanner();
  };
  source.onmessage = (event) => {
    let frame;
    try {
      frame = JSON.parse(event.data);
    } catch {
      return;
    }
    logFrame(frame);
    // The request is on the wire by the time its span arrives, so the captured exchange can be
    // read back here and rendered as-is instead of being reconstructed from the span.
    if (frame.type === "trace" && frame.event?.kind === "message_sent") {
      loadWire(runId);
    } else if (frame.type === "finished" || (frame.type === "event" && ui.view.wire.length === 0)) {
      // The send span is emitted just before the HTTP call, so the first read can land before the
      // request hook has written the record. Retry on the next server frame and at the end.
      loadWire(runId);
    }
    // An approval trace span carries the plan details and may arrive after the status frame that
    // stopped the show. Apply it immediately so the card is never stuck on a generic message.
    if (frame.type === "trace" && isApprovalTrace(frame.event) && player.status().hold) {
      applyFrame(ui.view, frame);
      applyFrame(ui.shadow, frame);
      render();
    }
    // A gate the server has already closed (timeout or rejection) must not deadlock the player:
    // its REJECTED/COMPLETED/finished frames are otherwise queued behind the held card and never
    // applied, leaving the page stuck on "review needed".
    if (isTerminalFrame(frame) && player.status().hold) {
      applyFrame(ui.view, frame);
      applyFrame(ui.shadow, frame);
      render();
      if (pendingHuman(ui.view) === null) player.release();
    }
    enqueueFrame(frame);
    if (frame.type === "finished") {
      closeStream();
      setBusy(false);
    }
  };
}

/** Read the captured NMS exchanges of one run; the wire inspector renders the request from them. */
async function loadWire(runId) {
  try {
    const response = await fetch(`/api/runs/${encodeURIComponent(runId)}/wire`);
    if (!response.ok) return;
    const payload = await response.json();
    if (ui.view.runId !== runId) return; // a newer run replaced this one
    ui.view.wire = Array.isArray(payload.records) ? payload.records : [];
    render();
  } catch {
    // A missing capture leaves the inspector on its "no captured exchange" wording.
  }
}

/** Subscribe to the MIE Agent approval stream of the pure A2A three-hop mode. */
function subscribeMie() {
  if (!ui.mieEnabled) return;
  if (ui.mieSource) ui.mieSource.close();
  const source = new EventSource("/api/mie/events");
  ui.mieSource = source;
  source.onmessage = (event) => {
    let payload;
    try {
      payload = JSON.parse(event.data);
    } catch {
      return;
    }
    if (payload !== null && typeof payload === "object") handleMieEvent(payload);
  };
}

/** Apply one MIE approval event: fill or clear the card; release the held scene on a decision. */
function handleMieEvent(payload) {
  if (payload.type === "approval_requested") {
    // Store the card now, but let the run SSE's approval scene decide when to show it. The MIE
    // event usually arrives before the OSS relays the wait, and showing it at step 1 would let
    // the audience approve before the story reaches the approval step.
    ui.view.mieApproval = { ...payload, requestedAt: new Date().toISOString() };
    ui.shadow.mieApproval = ui.view.mieApproval;
    render();
    return;
  }
  if (payload.type === "approval_resolved") {
    const resolved = {
      approved: payload.approved === true,
      reason: payload.reason ?? null,
    };
    ui.view.mieResolved = resolved;
    ui.shadow.mieResolved = resolved;
    ui.view.mieApproval = null;
    ui.shadow.mieApproval = null;
    ui.view.mieWaiting = false;
    ui.shadow.mieWaiting = false;
    if (resolved.approved) stage.approvalResolved(ui.copy, ui.view);
    player.release();
    render();
  }
}

function enqueueFrame(frame) {
  const key = frameKey(frame);
  if (key !== null) {
    if (ui.seenFrames.has(key)) return;
    ui.seenFrames.add(key);
  }
  const scene = sceneFor(frame, ui.shadow) || {
    key: "trace",
    step: ui.lastScene ? ui.lastScene.step : 0,
    duration: 0,
    hold: false,
    frame,
  };
  // Unmapped trace/event frames are evidence rows, not stage steps: keep the current step
  // instead of falling back to the Request step.
  if ((scene.key === "trace" || scene.key === "event") && ui.lastScene !== null) {
    scene.step = ui.lastScene.step;
  }
  // The Modify phase repeats the baseline vocabulary (strategy, collection, approval, apply).
  // Keep the step bar at the step the audience already reached instead of rewinding it.
  if (ui.shadow.modifyPhaseStarted && ui.lastScene !== null && scene.step < ui.lastScene.step) {
    scene.step = ui.lastScene.step;
  }
  // A repeated beat (the live planner may evaluate several candidates, and a late span may
  // repeat a scene) adds evidence without replaying the animation and its narration.
  if (
    ui.lastQueuedScene === scene.key &&
    !scene.hold &&
    scene.duration > 0 &&
    REPEATED_SILENT.has(scene.key)
  ) {
    scene.duration = 0;
    scene.silent = true;
  }
  if (scene.key !== "trace" && scene.key !== "event") ui.lastQueuedScene = scene.key;
  if ((scene.key === "trace" || scene.key === "event") && scene.duration <= 0) {
    scene.duration = EVIDENCE_MS;
    scene.evidence = true;
  }
  if (scene.key === "request" && typeof stage.typingRemaining === "function") {
    // The start button began typing while the run was being created. Keep the scene alive until
    // that animation finishes instead of cutting the request bubble off mid-sentence.
    const remaining = stage.typingRemaining();
    if (remaining > 0) scene.duration = Math.max(scene.duration, remaining + 250);
  }
  if (
    frame.type === "trace" &&
    frame.event &&
    frame.event.kind === "message_sent" &&
    !ui.wirePinned
  ) {
    ui.wireEntry = frame.event;
  }
  if (frame.type === "decision_requested") ui.selectedRegion = "";
  applyFrame(ui.shadow, frame);
  ui.frameLog.push(frame);
  player.enqueue(scene);
}

function frameKey(frame) {
  if (!frame || typeof frame !== "object") return null;
  if (frame.type === "event" || frame.type === "trace") {
    const entry = frame.event || {};
    if (Number.isInteger(entry.seq)) return `${frame.type}:${entry.seq}`;
    if (typeof entry.spanId === "string") return `${frame.type}:${entry.spanId}`;
    return null;
  }
  if (frame.type === "intent_review_requested") {
    return `intent:${frame.requestedAt ?? ""}`;
  }
  if (frame.type === "decision_requested" || frame.type === "decision_recommended") {
    return `${frame.type}:${frame.round ?? "?"}:${frame.requestedAt ?? ""}`;
  }
  if (frame.type === "finished") return "finished";
  return frame.type || null;
}

function rebuild(index) {
  const view = createState();
  view.scenario = ui.selected;
  for (let cursor = 0; cursor < index && cursor < ui.frameLog.length; cursor += 1) {
    applyFrame(view, ui.frameLog[cursor]);
  }
  ui.view = view;
  ui.lastScene = lastStoryScene(index - 1);
  ui.maxStep = ui.lastScene ? ui.lastScene.step : 0;
  render();
  // The authored scenes own the stage; a rewind restores the scene it lands on.
  if (ui.lastScene !== null) stage.scene(ui.lastScene, ui.view, ui.copy);
}

/** The nearest story scene at or before ``index`` (evidence rows do not own the stage). */
function lastStoryScene(index) {
  for (let cursor = index; cursor >= 0; cursor -= 1) {
    const scene = player.sceneAt(cursor);
    if (scene && scene.key !== "trace" && scene.key !== "event") return scene;
  }
  return null;
}

function closeStream() {
  if (ui.source !== null) {
    ui.source.close();
    ui.source = null;
  }
}

function setBusy(busy) {
  ui.busy = busy;
  els.start.disabled = busy;
  els.reset.disabled = busy;
  els.requestText.disabled = busy;
  if (els.scenarioSelect) els.scenarioSelect.disabled = busy;
  if (els.startQuick) els.startQuick.disabled = busy;
  if (!busy) {
    ui.cancelling = false;
    if (els.cancelLabel) els.cancelLabel.textContent = ui.copy.cancelRequest;
  }
  if (els.cancelRun) {
    els.cancelRun.disabled = !busy || ui.cancelling || !ui.view.runId;
  }
  renderProtocol();
  renderControls();
}

function render() {
  if (ui.rendering) return;
  ui.rendering = true;
  try {
    renderProtocol();
    renderSteps();
    renderContinuity();
    els.negoTitle.textContent =
      ui.tab === "wire" ? ui.copy.wire.panelTitle : ui.copy.negotiation.title;
    stage.render(ui.view, ui.copy);
    renderRequest();
    renderIntentPanel();
    rail.render(ui.view);
    wire.render(ui.view);
    trace.render(ui.view);
    renderNarrator();
    renderControls();
  } finally {
    ui.rendering = false;
  }
}

/** Show the two-phase continuity evidence: same Task, same plan, before/after rate. */
function renderContinuity() {
  if (!els.continuity) return;
  const model = continuityModel(ui.view);
  els.continuity.hidden = model === null;
  if (model === null) return;
  const text = ui.copy.continuity;
  els.continuityBadge.textContent = text.same;
  const parts = [];
  if (model.taskId) parts.push(`${text.task} ${model.taskId.slice(0, 8)}…`);
  if (model.planId) parts.push(`${text.plan} ${model.planId}`);
  if (model.beforeRate !== null && model.afterRate !== null) {
    parts.push(`${model.beforeRate}% → ${model.afterRate}%`);
  } else if (model.afterRate !== null) {
    parts.push(`${model.afterRate}%`);
  }
  if (model.window) parts.push(model.window);
  els.continuityChange.textContent = parts.join(" · ");
  els.continuity.title = text.note;
}

/** Show the natural-language request from the moment a scenario is selected.
 *
 * The audience should see what the engineer asks before anything runs; the drawer stays the
 * editable copy.
 */
function renderRequest() {
  const text = (ui.view.userRequest || els.requestText.value || "").trim();
  if (text !== "") els.requestCardText.textContent = text;
  // Vertical space is scarce: the engineer bubble already carries the request while it is typed,
  // and the edit drawer opens the full text when the presenter needs it. Do not reserve a second
  // full-width request block on the stage page.
  els.requestCard.hidden = true;
}

function renderIntentPanel() {
  if (ui.renderingIntent) return;
  ui.renderingIntent = true;
  try {
    const text = ui.view.intentText || els.requestText.value;
    renderIntent(els.intentBody, text, ui.copy, {
      rawRequest: ui.view.rawRequest,
      source: ui.view.intentSource,
      warnings: ui.view.intentWarnings || [],
      missingFields: ui.view.businessIntent?.missingFields || [],
      conflicts: ui.view.businessIntent?.conflicts || [],
      businessIntent: ui.view.businessIntent,
    });
  } finally {
    ui.renderingIntent = false;
  }
}

function renderSteps() {
  if (ui.steps === undefined) {
    els.steps.replaceChildren();
    const pureSteps = ui.protocol === "pure" ? ui.copy.pure?.steps || {} : {};
    for (const key of STEP_KEYS) {
      const item = el("li");
      const button = el("button");
      button.type = "button";
      button.dataset.step = key;
      button.append(
        el("b", "", String(STEP_KEYS.indexOf(key) + 1)),
        el("span", "", pureSteps[key] || ui.copy.steps[key] || key),
        el("em", "", ui.copy.stepWhen[key] || ""),
      );
      button.addEventListener("click", () => {
        const index = STEP_KEYS.indexOf(key);
        if (index >= 0 && player.jumpToStep(index)) player.pause();
      });
      item.append(button);
      els.steps.append(item);
    }
    ui.steps = [...els.steps.children];
  }
  let current = ui.lastScene ? ui.lastScene.step : -1;
  if (current >= 0) {
    // The run is paced; a late phase may queue a scene with an earlier step while the playhead is
    // still catching up. Keep the bar monotonic within the current playhead.
    current = Math.max(current, ui.maxStep || 0);
    ui.maxStep = current;
  }
  const pending = pendingHuman(ui.view);
  const stage = stageOf(ui.view);
  const expectedNegotiation = negotiationExpected(ui.view);
  const approvalSeen = ui.view.approval !== null || ui.view.approvalState !== null;
  const intentEnabled = ui.meta?.intentReview === true;
  const runProtocol = ui.view.protocol || ui.protocol;
  // Standard A2A reviews through MIE; the A2A-T extension uses the server gate when it is on.
  const approvalEnabled =
    runProtocol === "pure"
      ? ui.meta?.mieEnabled === true
      : ui.meta?.approvalGate !== false;
  const finished = ui.view.finished;
  const plain = runProtocol === "plain";
  const plainNa = new Set(plain ? ["understand", "intent", "negotiate"] : []);

  ui.steps.forEach((item, index) => {
    const key = STEP_KEYS[index];
    if (plainNa.has(key)) {
      item.className = "na";
      const naButton = item.querySelector("button");
      naButton.disabled = true;
      naButton.querySelector("em").textContent = ui.copy.protocol.na;
      return;
    }
    let state = "pending";
    if (finished) {
      if (stage === "done") state = "done";
      else if (stage === "notSent") state = index < 2 ? "done" : index === 2 ? "ask" : "off";
      else if (stage === "notApplied") state = index <= 7 ? "done" : "off";
      else if (stage === "plainRejected") state = index <= 3 ? "done" : "off";
      else if (stage === "failed") state = index <= current ? "done" : "off";
      else state = index <= 4 ? "done" : "off";
      if (stage === "notApplied" && index === 7) state = "ask";
    } else if (index < current) {
      state = "done";
    } else if (index === current) {
      state = pending ? "ask" : "now";
    }
    if (key === "negotiate" && !expectedNegotiation && ui.view.started) {
      state = "skip";
    }
    if (key === "intent" && !intentEnabled) state = "skip";
    if (key === "approval" && !approvalEnabled) state = "skip";
    if (key === "approval" && stage === "done" && !approvalSeen) state = "skip";
    item.className = state;
    const button = item.querySelector("button");
    const when = button.querySelector("em");
    if (state === "skip" || state === "off") {
      button.disabled = true;
      when.textContent =
        state === "skip"
          ? key === "intent"
            ? ui.copy.auto
            : key === "negotiate"
              ? ui.copy.negotiation.notNeededShort
              : ui.copy.skip
          : ui.copy.off;
    } else {
      button.disabled = false;
      when.textContent = ui.copy.stepWhen[key] || "";
    }
  });
}

function renderNarrator() {
  // A terminal frame may already be queued behind a held card (the server closed the gate while
  // the player was still pacing earlier scenes). Apply it now instead of waiting forever for a
  // click the server no longer expects.
  const terminal = queuedTerminalFrame();
  if (terminal !== null && terminal !== ui.terminalApplied && player.status().hold) {
    ui.terminalApplied = terminal;
    applyFrame(ui.view, terminal);
    applyFrame(ui.shadow, terminal);
    render();
    if (pendingHuman(ui.view) === null) player.release();
    return;
  }
  const card = pendingCard();
  if (card) {
    els.say.classList.add("ask");
    els.sayKicker.textContent = card.kicker;
    els.sayText.textContent = card.title;
    els.sayDetail.hidden = false;
    els.sayDetail.replaceChildren();
    els.sayDetail.append(el("span", "", card.text));
    if (card.detail) els.sayDetail.append(el("span", "reason", card.detail));
    if (card.mml) {
      const block = el("div", "mml-inline");
      block.append(
        el("span", "mml-label", card.mml.label),
        el("code", "", card.mml.text),
      );
      els.sayDetail.append(block);
    }
    if (card.policy) els.sayDetail.append(el("span", "policy", card.policy));
    if (ui.cardError) els.sayDetail.append(el("p", "card-error", ui.cardError));
    if (card.remaining !== null) {
      const line = el("span", "reason");
      line.append(el("span", "", `${copyRemaining(card)} `));
      const value = el("b");
      value.dataset.countdown = "true";
      value.textContent = clock(card.remaining);
      line.append(value);
      els.sayDetail.append(line);
      startCountdown(card.remaining);
    } else {
      stopCountdown();
    }
    renderChoices(card);
    revealCard(card);
    return;
  }

  ui.cardKey = null;
  stopCountdown();
  els.say.classList.remove("ask");
  els.choices.hidden = true;
  clear(els.choices);
  ui.counterOpen = false;
  els.sayDetail.hidden = true;
  els.sayDetail.replaceChildren();

  const scene = ui.lastScene;
  if (scene === null) {
    els.sayKicker.textContent = ui.copy.ready;
    els.sayText.textContent = ui.copy.readyHint;
  } else {
    const stepKey = STEP_KEYS[scene.step] || "request";
    els.sayKicker.textContent = `Step ${scene.step + 1} · ${ui.copy.steps[stepKey]}`;
    els.sayText.textContent = narration(scene, stepKey);
  }
  renderResult();

  if (player.status().hold && pendingHuman(ui.view) === null) player.release();
}

/** Bring a human-gate card fully into view once, without moving the whole page. */
function revealCard(card) {
  const key = `${card.type}:${card.title}:${card.options.map((option) => option.action).join(",")}`;
  if (ui.cardKey === key) return;
  ui.cardKey = key;
  ui.cardError = "";
  requestAnimationFrame(() => {
    const left = document.querySelector(".left");
    if (!left) return;
    const rect = els.say.getBoundingClientRect();
    const frame = left.getBoundingClientRect();
    if (rect.bottom > frame.bottom) {
      left.scrollTop += rect.bottom - frame.bottom + 10;
    } else if (rect.top < frame.top) {
      left.scrollTop -= frame.top - rect.top + 10;
    }
  });
}

function pendingCard() {
  return (
    intentCard(ui.view, ui.copy) ||
    decisionCard(ui.view, ui.copy) ||
    approvalCard(ui.view, ui.copy)
  );
}

function renderChoices(card) {
  els.choices.hidden = false;
  els.choices.replaceChildren();
  if (ui.counterOpen) {
    const editor = el("div", "counter-editor");
    const area = el("textarea");
    area.value = ui.counterText;
    area.setAttribute("aria-label", ui.copy.decision.counterLabel);
    area.placeholder = ui.copy.decision.counterPlaceholder;
    area.addEventListener("input", () => {
      ui.counterText = area.value;
    });
    const row = el("div", "choices");
    const send = el("button", "primary", ui.copy.decision.confirm);
    send.type = "button";
    send.addEventListener("click", () => submitDecision("counter", ui.counterText));
    const cancel = el("button", "", ui.copy.decision.cancel);
    cancel.type = "button";
    cancel.addEventListener("click", () => {
      ui.counterOpen = false;
      renderNarrator();
    });
    row.append(send, cancel);
    editor.append(area, row);
    els.choices.append(editor);
    area.focus();
    return;
  }

  const regionOptions = Array.isArray(card.regionOptions) ? card.regionOptions : [];
  if (regionOptions.length === 0) {
    ui.selectedRegion = "";
  } else {
    if (!regionOptions.some((option) => option.value === ui.selectedRegion)) {
      const recommendedRegion = card.recommendation?.filled_params?.region;
      const preferred =
        regionOptions.find((option) => option.value === recommendedRegion) ||
        regionOptions.find((option) => option.recommended) ||
        regionOptions[0];
      ui.selectedRegion = preferred.value;
    }
    const group = el("div", "region-options");
    group.append(el("span", "region-label", ui.copy.decision.servedArea));
    for (const option of regionOptions) {
      const choice = el(
        "button",
        option.value === ui.selectedRegion ? "region-choice selected" : "region-choice",
        option.label,
      );
      choice.type = "button";
      choice.disabled = ui.submitting;
      choice.addEventListener("click", () => {
        ui.selectedRegion = option.value;
        renderNarrator();
      });
      group.append(choice);
    }
    els.choices.append(group);
  }

  for (const option of card.options) {
    const button = el("button", option.primary ? "primary" : option.danger ? "danger" : "", option.label);
    button.type = "button";
    button.disabled = ui.submitting;
    button.addEventListener("click", () => {
      if (card.type === "intent") {
        submitIntent(option.action);
        return;
      }
      if (card.type === "approval") {
        submitApproval(option.action === "approve");
        return;
      }
      if (option.action === "counter") {
        ui.counterOpen = true;
        ui.counterText = card.counterText || "";
        renderNarrator();
        return;
      }
      submitDecision(option.action);
    });
    els.choices.append(button);
  }
}

async function submitDecision(action, counterText = "") {
  if (ui.submitting) return;
  const card = decisionCard(ui.view, ui.copy);
  if (card === null) return;
  ui.submitting = true;
  renderChoices(card);
  const payload = decisionPayload(card, action, ui.copy, counterText, ui.selectedRegion);
  try {
    const response = await fetch(`/api/runs/${encodeURIComponent(ui.view.runId)}/decisions`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (!response.ok) {
      const detail = await response.json().catch(() => ({}));
      throw new Error(detail.error || `HTTP ${response.status}`);
    }
    ui.submitting = false;
    ui.counterOpen = false;
    ui.selectedRegion = "";
    if (ui.view.decision !== null) ui.view.decision.resolved = true;
    player.release();
    render();
  } catch (error) {
    ui.submitting = false;
    const message = error instanceof Error ? error.message : String(error);
    els.sayDetail.append(el("span", "error", `${ui.copy.decision.submitFailed} ${message}`));
    renderChoices(card);
  }
}

async function submitIntent(action) {
  if (ui.submitting) return;
  const card = intentCard(ui.view, ui.copy);
  if (card === null) return;
  const approved = action === "approve";
  ui.submitting = true;
  renderChoices(card);
  try {
    const response = await fetch(`/api/runs/${encodeURIComponent(ui.view.runId)}/intent`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ approved }),
    });
    if (!response.ok) {
      const detail = await response.json().catch(() => ({}));
      throw new Error(detail.error || `HTTP ${response.status}`);
    }
    ui.submitting = false;
    stage.intentResolved(ui.copy);
    if (ui.view.intentReview !== null) ui.view.intentReview.resolved = true;
    if (!approved && action === "edit") {
      els.drawer.hidden = false;
      els.editRequest.setAttribute("aria-expanded", "true");
      els.requestText.focus();
    }
    player.release();
    render();
  } catch (error) {
    ui.submitting = false;
    const message = error instanceof Error ? error.message : String(error);
    els.sayDetail.append(el("span", "error", `${ui.copy.intentReview.submitFailed} ${message}`));
    renderChoices(card);
  }
}

async function submitApproval(approved) {
  if (ui.submitting) return;
  ui.submitting = true;
  const card = approvalCard(ui.view, ui.copy);
  if (card) renderChoices(card);
  try {
    const endpoint =
      card?.pure && card.taskId
        ? `/api/mie/approvals/${encodeURIComponent(card.taskId)}`
        : `/api/runs/${encodeURIComponent(ui.view.runId)}/approvals`;
    const response = await fetch(endpoint, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ approved }),
    });
    if (!response.ok) {
      const detail = await response.json().catch(() => ({}));
      throw new Error(detail.error || `HTTP ${response.status}`);
    }
    ui.submitting = false;
    ui.cardError = "";
    if (card?.pure) {
      ui.view.mieResolved = { approved, reason: null };
      ui.shadow.mieResolved = ui.view.mieResolved;
      ui.view.mieApproval = null;
      ui.shadow.mieApproval = null;
      ui.view.mieWaiting = false;
      ui.shadow.mieWaiting = false;
      if (approved) stage.approvalResolved(ui.copy, ui.view);
    } else {
      ui.view.approvalState = approved ? "approved" : "rejected";
      // The scene of the next frame is chosen from the shadow state at enqueue time. The server's
      // "Approved. Applying the plan." frame arrives before its approval-resolved span, so without
      // this the show would replay "strategy & start" after the MIE review.
      ui.shadow.approvalState = ui.view.approvalState;
      if (approved) stage.approvalResolved(ui.copy, ui.view);
    }
    player.release();
    render();
  } catch (error) {
    ui.submitting = false;
    const message = error instanceof Error ? error.message : String(error);
    ui.cardError = `${ui.copy.approval.submitFailed} ${message}`;
    render();
    if (card) renderChoices(card);
  }
}

function renderControls() {
  const status = player.status();
  const idle = ui.view.status === "idle" && ui.lastScene === null;
  const finished = ui.view.finished && status.atEnd;
  els.next.hidden = !(idle || status.waitingNext || status.mode === "step");
  els.next.disabled = !(idle ? !ui.busy : status.waitingNext);
  els.next.textContent = idle ? "Start ▶" : "Next step ▶";
  els.next.classList.toggle("next", true);
  els.prev.disabled = status.playhead <= 1;
  els.pause.disabled = status.scenes === 0 || finished;
  els.pause.textContent = status.paused ? "▶ Resume" : "❚❚ Pause";
  els.pause.classList.toggle("on", status.paused);
  els.pbAuto.checked = status.mode === "auto";
  els.pbStep.checked = status.mode === "step";
  els.pausedTag.hidden = !status.paused;
  els.stage.classList.toggle("paused", status.paused);
  if (ui.view.status !== "idle" && !ui.busy) {
    els.start.textContent = ui.copy.start;
  } else if (ui.busy) {
    els.start.textContent = ui.copy.running;
  }
  if (els.startQuick) {
    els.startQuick.textContent = ui.busy ? ui.copy.running : ui.copy.start;
    els.startQuick.disabled = ui.busy;
  }
  if (els.cancelRun) {
    els.cancelRun.disabled = !ui.busy || ui.cancelling || !ui.view.runId;
  }
}

function renderResult() {
  // Show the report as soon as its artifact arrives; the terminal frame may trail a live model by
  // several seconds. The card is cached per phase, so the later finished frame does not duplicate
  // it and the baseline report of a two-phase run does not become the ending card.
  if (!ui.view.finished && !ui.view.taskCompleted && !ui.view.report) return;
  if (ui.view.twoPhase && !ui.view.modifyPhaseStarted) return;
  const cardKey = `${ui.view.runId || ""}:${ui.view.phaseIndex}`;
  // The ending (and the comparison card) is built once per phase and re-appended on later
  // renders; building it again would reset the scroll and any click target in the card.
  if (ui.resultCard === null || ui.resultCardRun !== cardKey) {
    const built = buildResultCard();
    if (built === null) return;
    ui.resultCard = built;
    ui.resultCardRun = cardKey;
    ui.compareSaved = null;
  }
  els.sayDetail.hidden = false;
  els.sayDetail.append(ui.resultCard);
  revealResult();
}

/** Build the ending card of the current run, or ``null`` while the run has no ending. */
function buildResultCard() {
  const stage = stageOf(ui.view);
  const card = el("div", "result-card");
  if (stage === "notSent") {
    card.append(
      el("h3", "result-title", ui.copy.result.intentNotConfirmedTitle),
      el("p", "result-reason", ui.copy.result.intentNotConfirmed),
    );
  } else if (stage === "plainRejected") {
    card.append(
      el("h3", "result-title", ui.copy.result.plainRejectedTitle),
      el("p", "result-reason", ui.copy.result.plainRejected),
    );
  } else if (stage === "notApplied") {
    card.append(
      el("h3", "result-title", ui.copy.result.notAppliedTitle),
      el("p", "result-reason", ui.copy.result.notApplied),
    );
  } else if (ui.view.report) {
    card.append(reportCard(ui.view.report, ui.copy));
  } else if (stage === "ended") {
    const ending = endingOf(ui.view);
    const reason =
      ending.resolution === "aborted"
        ? ui.copy.result.aborted
        : ending.resolution === "rejected"
          ? ui.copy.result.rejected
          : ui.copy.result.endedGeneric;
    card.append(
      el("h3", "result-title", ui.copy.result.endedTitle),
      el("p", "result-reason", reason),
    );
    // Rejection and abort carry their own copy; every other ending is a peer stop whose text
    // names the real cause (for example a failed accept validation).
    if (ending.detail && ending.resolution !== "rejected" && ending.resolution !== "aborted") {
      card.append(el("p", "result-detail", ending.detail));
    }
  } else if (stage === "canceled") {
    card.append(
      el("h3", "result-title", ui.copy.result.canceledTitle),
      el("p", "result-reason", ui.copy.result.canceled),
    );
  } else if (stage === "failed") {
    card.append(
      el("h3", "result-title", ui.copy.result.failedTitle),
      el("p", "result-reason", ui.view.error || ""),
    );
  } else {
    return null;
  }
  if (ui.view.protocol === "pure") {
    const metrics = ui.view.pureMetrics || pureMetricsFromLog();
    if (metrics) card.append(pureMetricsBlock(metrics, ui.copy));
  }
  const langfuse = ui.view.langfuse;
  if (langfuse && langfuse.enabled && typeof langfuse.traceId === "string") {
    if (typeof langfuse.url === "string" && langfuse.url) {
      const link = el("a", "result-langfuse", ui.copy.result.openInLangfuse);
      link.href = langfuse.url;
      link.target = "_blank";
      link.rel = "noopener noreferrer";
      card.append(link);
    } else {
      card.append(el("p", "result-detail", `${ui.copy.result.langfuseTrace}: ${langfuse.traceId}`));
    }
    if (typeof langfuse.warning === "string" && langfuse.warning) {
      card.append(el("p", "result-detail", langfuse.warning));
    }
  }
  return card;
}

/** Read the pure run metrics from the frame log when a terminal bypass skipped the last event. */
function pureMetricsFromLog() {
  for (let index = ui.frameLog.length - 1; index >= 0; index -= 1) {
    const frame = ui.frameLog[index];
    if (frame?.type !== "event") continue;
    const detail = frame.event?.detail;
    const pure = detail?.pure;
    if (pure && Number.isInteger(pure.hops)) {
      return { ...pure, hop: detail.hop ?? pure.hop ?? null };
    }
  }
  return null;
}

/** Compact metrics block of one pure A2A run. */
function pureMetricsBlock(metrics, copy) {
  const block = el("section", "pure-metrics");
  block.append(el("h4", "pure-metrics-title", copy.pure?.metrics || "This pure A2A run"));
  const rows = [
    [copy.pure?.hops || "hops", metrics.hops],
    [copy.pure?.rounds || "rounds", metrics.rounds],
    [copy.pure?.messages || "messages", metrics.messages],
  ];
  for (const [label, value] of rows) {
    if (value === null || value === undefined || value === "") continue;
    const row = el("div", "pure-metric");
    row.append(el("span", "", String(label)), el("b", "", String(value)));
    block.append(row);
  }
  return block;
}

/** Bring the ending (and the comparison card) into view once per finished run. */
function revealResult() {
  if (ui.resultRevealed === ui.view.runId) return;
  ui.resultRevealed = ui.view.runId;
  requestAnimationFrame(() => {
    const target = document.querySelector(".left") || document.scrollingElement;
    if (!target) return;
    target.scrollTo({ top: target.scrollHeight, behavior: "smooth" });
  });
}

function reportCard(report, copy) {
  const wrapper = el("div", "report-card");
  const percent = reportPercent(report);
  const head = el("div", "report-head");
  head.append(el("h3", "result-title", copy.result.report));
  if (percent !== null) head.append(el("span", "report-rate", `${Math.round(percent)}%`));
  wrapper.append(head);
  const cell = report.cell_name || report.cell_id || "—";
  const windowLabel = report.window || "—";
  wrapper.append(el("p", "report-line", `${copy.result.cell} ${cell} · ${copy.result.window} ${windowLabel}`));
  if (percent !== null) {
    const bar = el("div", "energy-bar");
    const fill = el("span", "energy-fill");
    fill.style.width = `${Math.max(0, Math.min(100, percent))}%`;
    bar.append(fill);
    wrapper.append(bar);
  }
  const energy = report.energy && typeof report.energy === "object" ? report.energy : {};
  const baseline = numberOrNull(energy.baseline_kwh);
  const remaining = numberOrNull(energy.remaining_kwh);
  const saved = numberOrNull(energy.saved_kwh) ?? numberOrNull(report.estimated_saving_kwh);
  if (baseline !== null || remaining !== null) {
    const legend = el("div", "energy-legend");
    if (baseline !== null) legend.append(el("span", "", `${copy.result.baseline} ${baseline.toFixed(1)} kWh`));
    if (remaining !== null) legend.append(el("span", "", `${copy.result.remaining} ${remaining.toFixed(1)} kWh`));
    wrapper.append(legend);
  }
  if (saved !== null) {
    wrapper.append(el("p", "report-saved", `${copy.result.saved} ≈ ${saved.toFixed(1)} kWh`));
  }
  wrapper.append(
    el(
      "p",
      "report-note",
      report.status === "modified" ? copy.result.modified : copy.result.onDuty,
    ),
  );
  const download = el("button", "btn report-download", copy.result.download);
  download.type = "button";
  download.addEventListener("click", () => downloadReport(report));
  wrapper.append(download);
  return wrapper;
}

/** Download the current report as JSON, so a demo can leave an evidence file. */
function downloadReport(report) {
  const blob = new Blob([JSON.stringify(report, null, 2)], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const link = el("a");
  link.href = url;
  link.download = `energy-saving-report-${ui.view.runId || "run"}.json`;
  link.click();
  window.setTimeout(() => URL.revokeObjectURL(url), 0);
}

function reportPercent(report) {
  const energy = report.energy && typeof report.energy === "object" ? report.energy : {};
  return (
    numberOrNull(energy.saving_percent) ??
    numberOrNull(report.target_rate) ??
    numberOrNull(report.estimated_saving_percent)
  );
}

function narration(scene, stepKey) {
  const protocol = ui.view.protocol || ui.protocol;
  if (protocol === "pure") {
    const pureKey = {
      send: "pureSend",
      understand: "pureUnderstand",
      strategy: "pureStrategy",
      propose: "purePropose",
      decision: "purePropose",
      "approval-ready": "pureApprovalReady",
      "approval-pure": "pureApproval",
      execute: "pureExecute",
      apply: "pureExecute",
      report: "pureReport",
      finish: "pureFinish",
    }[scene.key];
    if (pureKey && ui.copy.narration[pureKey]) return ui.copy.narration[pureKey];
  }
  if (protocol === "plain" && scene.key === "send") {
    return ui.copy.narration.plainSend || ui.copy.narration.send;
  }
  if (scene.key === "finish" && ui.view.status === "canceled") {
    return ui.copy.narration.canceled || ui.copy.narration.finish;
  }
  return ui.copy.narration[scene.key] || ui.copy.narration[stepKey] || scenarioStory(ui.copy, ui.selected);
}

/** Render the selected request form. One agent serves both; the page switches per run. */
function renderProtocol() {
  const pure = ui.protocol === "pure";
  const switchable = (ui.meta?.protocols || []).length > 1;
  if (els.protocolRow) els.protocolRow.hidden = !switchable;
  if (els.protocolLabel) els.protocolLabel.textContent = ui.copy.protocol.label;
  for (const button of els.protocolOptions || []) {
    const active = button.dataset.protocol === ui.protocol;
    button.setAttribute("aria-pressed", String(active));
    button.disabled = Boolean(ui.busy);
    button.textContent =
      button.dataset.protocol === "pure"
        ? ui.copy.protocol.pure
        : ui.copy.protocol.a2at;
  }
  const hint = pure
    ? ui.copy.protocol.pureHint
    : switchable
      ? ui.copy.protocol.a2atHint
      : "";
  els.protocolHint.textContent = hint;
  els.protocolHint.hidden = !hint;
  if (els.badge) els.badge.textContent = pure ? ui.copy.pure.badge : ui.copy.badge;
  if (els.wireFormat) els.wireFormat.hidden = pure;
  if (els.tabWire) els.tabWire.hidden = pure;

  // The stage carries protocol-specific labels outside the copy registry (the SVG channel and
  // the MIE role). Keep them consistent with the selected request form.
  const setText = (id, value) => {
    const node = document.getElementById(id);
    if (node) node.textContent = value;
  };
  if (pure) {
    els.brandKicker.textContent = ui.copy.pure.kicker;
    els.title.textContent = ui.copy.pure.title;
    document.title = ui.copy.pure.title;
    setText("brand-mark", "A2A");
    setText("chan-label", ui.copy.pure.channel);
    setText("chan-sub1", ui.copy.pure.channelSub1);
    setText("chan-sub2", ui.copy.pure.channelSub2);
    setText("lane-task-label", ui.copy.pure.laneTask);
    setText("lane-status-label", ui.copy.pure.laneStatus);
    setText("approval-org", ui.copy.pure.mieOrg);
    setText("nms-note", ui.copy.pure.nmsNote);
  } else {
    els.brandKicker.textContent = ui.copy.kicker;
    els.title.textContent = ui.copy.title;
    document.title = ui.copy.title;
    setText("brand-mark", "A2A-T");
    setText("chan-label", ui.copy.map.channel);
    setText("chan-sub1", ui.copy.map.channelSub1);
    setText("chan-sub2", ui.copy.map.channelSub2);
    setText("lane-task-label", ui.copy.map.laneTask);
    setText("lane-status-label", ui.copy.map.laneStatus);
    setText("approval-org", ui.copy.roles.approvalOrg);
    setText("nms-note", ui.copy.roles.nmsNote);
  }
}

function copyRemaining(card) {
  if (card.type === "intent") return ui.copy.intentReview.remaining;
  return card.type === "approval" ? ui.copy.approval.remaining : ui.copy.decision.remaining;
}

function startCountdown(remaining) {
  stopCountdown();
  ui.deadline = Date.now() + remaining * 1000;
  ui.countdownTimer = window.setInterval(() => {
    const node = els.sayDetail.querySelector("[data-countdown]");
    if (!node) return;
    const left = Math.max(0, Math.round((ui.deadline - Date.now()) / 1000));
    node.textContent = clock(left);
    if (left === 0) {
      const card = pendingCard();
      if (card) node.textContent = clock(0);
      stopCountdown();
    }
  }, 1000);
}

function stopCountdown() {
  if (ui.countdownTimer !== null) {
    window.clearInterval(ui.countdownTimer);
    ui.countdownTimer = null;
  }
  ui.deadline = null;
}

function handleLoop(scene) {
  if (scene.key !== "finish" || !ui.view.finished || !els.loop.checked || ui.looping) return;
  ui.looping = true;
  window.setTimeout(() => {
    if (!els.loop.checked) {
      ui.looping = false;
      return;
    }
    const names = ui.meta?.scenarios || [];
    const index = names.indexOf(ui.selected);
    const next = names[(index + 1) % names.length] || ui.selected;
    selectScenario(next, { start: true });
  }, 6000);
}

function isApprovalTrace(span) {
  return Boolean(span && typeof span === "object" && span.kind === "approval");
}

/** Whether one frame ends the run (or its Task) while a human gate is still held. */
/** The newest terminal frame already received for this run, or ``null``. */
function queuedTerminalFrame() {
  for (let index = ui.frameLog.length - 1; index >= 0; index -= 1) {
    if (isTerminalFrame(ui.frameLog[index])) return ui.frameLog[index];
  }
  return null;
}

function isTerminalFrame(frame) {
  if (!frame || typeof frame !== "object") return false;
  if (frame.type === "finished") return true;
  if (frame.type !== "event") return false;
  const entry = frame.event;
  if (!entry || entry.kind !== "status") return false;
  return [
    "TASK_STATE_REJECTED",
    "TASK_STATE_COMPLETED",
    "TASK_STATE_FAILED",
    "TASK_STATE_CANCELED",
  ].includes(entry.state);
}

function logFrame(frame) {
  if (els.techLog.childElementCount > 600) return;
  const line = document.createElement("span");
  line.textContent = JSON.stringify(frame);
  els.techLog.append(line, document.createTextNode("\n"));
}

function showError(message) {
  els.requestError.textContent = message;
  els.requestError.hidden = false;
  els.drawer.hidden = false;
  els.editRequest.setAttribute("aria-expanded", "true");
}

function hideError() {
  els.requestError.hidden = true;
  els.requestError.textContent = "";
}

function showBanner() {
  els.banner.hidden = false;
}

function hideBanner() {
  els.banner.hidden = true;
}

function numberOrNull(value) {
  if (typeof value === "number" && Number.isFinite(value)) return value;
  if (typeof value === "string" && value.trim() !== "") {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : null;
  }
  return null;
}

function bindSplitter() {
  const splitter = els.splitter;
  const main = document.querySelector(".main");
  const key = "a2at-presentation-right-px";
  const setWidth = (px, save) => {
    const rect = main.getBoundingClientRect();
    const value = Math.max(300, Math.min(rect.width * 0.6, px));
    main.style.setProperty("--rw", `${value.toFixed(0)}px`);
    if (save) {
      try {
        localStorage.setItem(key, String(value));
      } catch {
        /* storage is optional */
      }
    }
  };
  try {
    const saved = Number(localStorage.getItem(key));
    if (Number.isFinite(saved) && saved >= 300 && saved <= 900) {
      main.style.setProperty("--rw", `${saved}px`);
    }
  } catch {
    /* storage is optional */
  }
  let dragging = false;
  const fromPointer = (x) => {
    const rect = main.getBoundingClientRect();
    const padding = parseFloat(getComputedStyle(main).paddingRight) || 0;
    return rect.right - padding - x - 9;
  };
  splitter.addEventListener("pointerdown", (event) => {
    dragging = true;
    try {
      splitter.setPointerCapture(event.pointerId);
    } catch {
      /* pointer capture is best effort */
    }
    splitter.classList.add("drag");
    document.body.classList.add("resizing");
    event.preventDefault();
  });
  splitter.addEventListener("pointermove", (event) => {
    if (dragging) setWidth(fromPointer(event.clientX), false);
  });
  const end = (event) => {
    if (!dragging) return;
    dragging = false;
    splitter.classList.remove("drag");
    document.body.classList.remove("resizing");
    setWidth(fromPointer(event.clientX), true);
  };
  splitter.addEventListener("pointerup", end);
  splitter.addEventListener("pointercancel", end);
  splitter.addEventListener("dblclick", () => {
    main.style.removeProperty("--rw");
    try {
      localStorage.removeItem(key);
    } catch {
      /* storage is optional */
    }
  });
  splitter.addEventListener("keydown", (event) => {
    const current = document.querySelector(".right").getBoundingClientRect().width;
    if (event.key === "ArrowLeft") {
      event.preventDefault();
      setWidth(current + 30, true);
    }
    if (event.key === "ArrowRight") {
      event.preventDefault();
      setWidth(current - 30, true);
    }
  });
}

/** Resize the session-trace panel; the right column itself no longer scrolls as a whole. */
function bindTraceSplitter() {
  const splitter = els.traceSplitter;
  const panel = document.querySelector("#p-trace");
  const right = document.querySelector(".right");
  if (!splitter || !panel || !right) return;
  const key = "a2at-presentation-trace-px";
  const MIN = 140;
  const maxHeight = () => Math.max(MIN, right.getBoundingClientRect().height * 0.62);
  const setHeight = (px, save) => {
    const value = Math.max(MIN, Math.min(maxHeight(), px));
    right.style.setProperty("--trace-h", `${value.toFixed(0)}px`);
    if (save) {
      try {
        localStorage.setItem(key, String(value));
      } catch {
        /* storage is optional */
      }
    }
  };
  try {
    const saved = Number(localStorage.getItem(key));
    if (Number.isFinite(saved) && saved >= MIN) setHeight(saved, false);
  } catch {
    /* storage is optional */
  }
  let dragging = false;
  let startY = 0;
  let startHeight = 0;
  splitter.addEventListener("pointerdown", (event) => {
    dragging = true;
    startY = event.clientY;
    startHeight = panel.getBoundingClientRect().height;
    try {
      splitter.setPointerCapture(event.pointerId);
    } catch {
      /* pointer capture is best effort */
    }
    splitter.classList.add("drag");
    document.body.classList.add("resizing-y");
    event.preventDefault();
  });
  splitter.addEventListener("pointermove", (event) => {
    if (!dragging) return;
    setHeight(startHeight - (event.clientY - startY), false);
  });
  const end = () => {
    if (!dragging) return;
    dragging = false;
    splitter.classList.remove("drag");
    document.body.classList.remove("resizing-y");
    setHeight(panel.getBoundingClientRect().height, true);
  };
  splitter.addEventListener("pointerup", end);
  splitter.addEventListener("pointercancel", end);
  splitter.addEventListener("dblclick", () => {
    right.style.removeProperty("--trace-h");
    try {
      localStorage.removeItem(key);
    } catch {
      /* storage is optional */
    }
  });
  splitter.addEventListener("keydown", (event) => {
    if (event.key !== "ArrowUp" && event.key !== "ArrowDown") return;
    event.preventDefault();
    const delta = event.key === "ArrowUp" ? 24 : -24;
    setHeight(panel.getBoundingClientRect().height + delta, true);
  });
  // A narrower/shorter window must not leave the trace larger than its rail.
  window.addEventListener("resize", () => {
    const current = panel.getBoundingClientRect().height;
    if (current > maxHeight()) setHeight(maxHeight(), false);
  });
}

/** Copy ``?token=…`` into the UI cookie, then strip it from the address bar.
 *
 * The bearer middleware protects ``/api/*``. A browser cannot set an Authorization header on an
 * EventSource, and asking every view to add one is brittle; a same-origin cookie is carried by
 * both fetch and EventSource. The cookie name mirrors ``UI_TOKEN_COOKIE`` in shared/http_security.
 */
function installTokenFromUrl() {
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

// Debug handle for the demo operator console and the headless smoke test.
if (typeof window !== "undefined") {
  window.__presentation = { ui, player };
}
