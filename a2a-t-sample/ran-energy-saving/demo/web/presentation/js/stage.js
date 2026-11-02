/** The live stage: four actors, two zones, the A2A-T channel, bubbles and flying packets. */

import { format, proposalLine, decisionLine } from "../copy.js";
import {
  executionSteps,
  negotiationExpected,
  pendingHuman,
  resolutionOf,
  roundInfo,
  stageOf,
} from "./state.js";
import { svg } from "./dom.js";
import { hhmm, parseIntent, plainSentence } from "./intent.js";

const REDUCED =
  typeof window !== "undefined" &&
  window.matchMedia &&
  window.matchMedia("(prefers-reduced-motion: reduce)").matches;

export function createStage(els) {
  buildCells(els.cellsFigure);
  let bubbles = {};
  let typingTarget = "";
  let typingUntil = 0;
  const packetTimers = new Set();
  let dayProgress = 0;
  let dayTimer = null;
  let previewTimer = null;
  let barTimer = null;
  // The authored scenes own the actor/MIE/clock states once a run starts; the derived view is
  // only the idle/summary fallback. This mirrors the reference demo's explicit actor() calls.
  let directed = false;
  let sceneToken = 0;

  function stopDays() {
    if (dayTimer !== null) {
      window.cancelAnimationFrame(dayTimer);
      dayTimer = null;
    }
  }

  function stopPreview() {
    if (previewTimer !== null) {
      window.cancelAnimationFrame(previewTimer);
      previewTimer = null;
    }
  }

  function stopBar() {
    if (barTimer !== null) {
      window.cancelAnimationFrame(barTimer);
      barTimer = null;
    }
  }

  function setClock(main, sub) {
    if (els.cellsClockMain) els.cellsClockMain.textContent = main;
    if (els.cellsClockSub) els.cellsClockSub.textContent = sub;
  }

  function setMie(cls, text) {
    els.approval.className = `approval${cls ? ` ${cls}` : ""}`;
    els.approvalState.textContent = text;
  }

  function focusIntent(on) {
    const panel = document.getElementById("p-intent");
    if (panel) panel.classList.toggle("focus", Boolean(on));
  }

  function highlightSkill(index) {
    const nodes = document.querySelectorAll("#oss-skills span");
    nodes.forEach((node, position) => node.classList.toggle("on", position === index - 1));
  }

  function progressBar(to, ms) {
    stopBar();
    const from = parseFloat(els.nmsBar.style.width) || 0;
    const start = performance.now();
    const frame = (now) => {
      const t = Math.min(1, (now - start) / ms);
      els.nmsBar.style.width = `${from + (to - from) * t}%`;
      if (t < 1) barTimer = window.requestAnimationFrame(frame);
      else barTimer = null;
    };
    barTimer = window.requestAnimationFrame(frame);
  }

  function typingDuration(text) {
    // Human typing pace: the reference request is one sentence, not a protocol dump. Keep the
    // animation between 2.2 s and 6 s so a long edited request is still readable.
    return Math.min(6000, Math.max(2200, String(text || "").length * 40));
  }

  function typingRemaining() {
    return Math.max(0, typingUntil - performance.now());
  }

  function typeBubble(actor, who, text, duration) {
    if (REDUCED || !text) {
      note(actor, who, text);
      return;
    }
    if (bubbles[actor]?.text === text) {
      // The start button already typed the same request while the run was being created.
      paintBubble(actor);
      return;
    }
    if (typingTarget === text && performance.now() < typingUntil) {
      // The optimistic start animation is still running; let it finish instead of restarting.
      return;
    }
    typingTarget = text;
    typingUntil = performance.now() + duration;
    bubbles[actor] = { who, text: "", neg: false };
    paintBubble(actor);
    const start = performance.now();
    const length = text.length;
    const frame = (now) => {
      const t = Math.min(1, (now - start) / duration);
      bubbles[actor].text = text.slice(0, Math.ceil(length * t));
      paintBubble(actor);
      if (t < 1) window.requestAnimationFrame(frame);
    };
    window.requestAnimationFrame(frame);
  }

  function animatePreview(state, copy) {
    stopPreview();
    const intent = parseIntent(state.intentText || state.userRequest || "");
    const slots = intent && Array.isArray(intent.slots) ? intent.slots : [];
    const rateAt = (minute) => {
      for (const slot of slots) {
        if (minute >= slot.start && minute < slot.end) return slot.rate;
      }
      return null;
    };
    const start = performance.now();
    const duration = 4800;
    const frame = (now) => {
      const t = Math.min(1, (now - start) / duration);
      const minute = Math.min(1439, Math.floor(t * 1440));
      const rate = rateAt(minute);
      const carriers = rate === null ? 2 : rate >= 20 ? 3 : rate >= 10 ? 2 : 1;
      for (let index = 0; index < 6; index += 1) setCell(index, carriers, false, false);
      setClock(hhmm(minute), copy.dataCollection.savingOn);
      if (t < 1) previewTimer = window.requestAnimationFrame(frame);
      else previewTimer = null;
    };
    previewTimer = window.requestAnimationFrame(frame);
  }

  function animateDays(copy) {
    stopDays();
    const start = performance.now();
    const frame = (now) => {
      const t = Math.min(1, (now - start) / 2600);
      dayProgress = t;
      const day = Math.max(1, Math.min(8, Math.round(1 + t * 7)));
      els.nmsBar.style.width = `${Math.round(t * 100)}%`;
      setActor("nms", { cls: "active", text: format(copy.states.nmsCollecting, { day }) });
      setClock(format(copy.dataCollection.dayClock, { day }), copy.dataCollection.clockSub);
      if (t < 1) {
        dayTimer = window.requestAnimationFrame(frame);
      } else {
        dayTimer = null;
      }
    };
    dayTimer = window.requestAnimationFrame(frame);
  }

  function clearPackets() {
    for (const timer of packetTimers) window.clearTimeout(timer);
    packetTimers.clear();
    for (const node of els.stage.querySelectorAll(".packet")) node.remove();
    for (const node of els.stage.querySelectorAll(".lane.hot")) node.classList.remove("hot");
  }

  function reset() {
    sceneToken += 1;
    directed = false;
    clearPackets();
    stopDays();
    stopPreview();
    stopBar();
    focusIntent(false);
    highlightSkill(0);
    dayProgress = 0;
    bubbles = {};
    for (const node of Object.values(els.bubbles)) {
      node.className = "bubble";
      node.replaceChildren();
    }
    els.nmsBar.style.width = "0";
    els.approval.className = "approval";
    els.approvalState.textContent = "";
    for (let index = 0; index < 6; index += 1) setCell(index, 3, false, false);
  }

  /** Reflect the engineer's intent decision before the next frame arrives. */
  function intentResolved(copy) {
    directed = true;
    setActor("eng", { cls: "ok", text: copy.states.engApproved });
    note("eng", copy.roles.eng, copy.states.engApproved);
    fly("lane-request", copy.intentReview.approvedPacket, "req", 900);
    focusIntent(false);
  }

  /** Reflect the MIE decision immediately; the next frame carries the workflow. */
  function approvalResolved(copy, state) {
    directed = true;
    if (state?.protocol === "pure") {
      setMie("ok", copy.states.approvalConfirmed);
      setActor("nms", {
        cls: "active",
        text: copy.states.nmsResuming || copy.states.nmsExecuted,
      });
      note("nms", copy.roles.nms, copy.pure?.mieApproved || copy.states.approvalConfirmed);
      fly("lane-data", copy.pure?.mieApproved || copy.states.approvalConfirmed, "data", 900);
      return;
    }
    setActor("eng", { cls: "ok", text: copy.states.engAcknowledged });
    note("eng", copy.roles.eng, copy.approval.acknowledgedInMie);
    setMie("ok", copy.states.approvalConfirmed);
  }

  function note(actor, who, text, neg = false) {
    bubbles[actor] = { who, text, neg };
    paintBubble(actor);
  }

  function paintBubble(actor) {
    const node = els.bubbles[actor];
    if (!node) return;
    const entry = bubbles[actor];
    if (!entry || !entry.text) {
      node.className = "bubble";
      node.replaceChildren();
      return;
    }
    node.className = `bubble show${entry.neg ? " neg" : ""}`;
    node.replaceChildren();
    const who = document.createElement("span");
    who.className = "who";
    who.textContent = entry.who;
    const body = document.createElement("div");
    body.className = "clamp";
    body.textContent = entry.text;
    node.append(who, body);
  }

  function fly(laneId, label, kind, duration = 1100) {
    if (REDUCED) return;
    const path = document.getElementById(laneId);
    if (!path) return;
    const length = path.getTotalLength();
    const packet = document.createElement("div");
    packet.className = `packet ${kind}`;
    packet.textContent = label;
    els.stage.append(packet);
    path.classList.add("hot");
    const total = Math.max(160, duration);
    const start = performance.now();
    const frame = (now) => {
      const t = Math.min(1, (now - start) / total);
      const eased = t < 0.5 ? 2 * t * t : 1 - Math.pow(-2 * t + 2, 2) / 2;
      const point = path.getPointAtLength(length * eased);
      packet.style.left = `${(point.x / 12).toFixed(2)}%`;
      packet.style.top = `${(point.y / 5.2).toFixed(2)}%`;
      if (t < 1) {
        requestAnimationFrame(frame);
      } else {
        path.classList.remove("hot");
        packet.classList.add("land");
        const timer = window.setTimeout(() => {
          packet.remove();
          packetTimers.delete(timer);
        }, 320);
        packetTimers.add(timer);
      }
    };
    requestAnimationFrame(frame);
  }

  /** React to one scene: transient packet and bubble. */
  function scene(scene, state, copy) {
    sceneToken += 1;
    const token = sceneToken;
    directed = true;
    stopBar();
    const later = (ms, fn) =>
      window.setTimeout(() => {
        if (token === sceneToken) fn();
      }, ms);
    const key = scene.key;
    const pureMode = state.protocol === "pure";
    const scenario = state.scenario;
    const { round } = roundInfo(state);
    const roles = copy.roles;
    const base = {
      oss: { cls: "", text: copy.states.ossWaiting },
      nms: { cls: "", text: copy.states.nmsWaiting },
      cells: { cls: "", text: copy.states.cellsNormal },
    };

    if (key === "request") {
      setActors({ ...base, eng: { cls: "active", text: copy.states.engWriting } });
      setClock(roles.cellsRegion, roles.cellsSub);
      setMie("", copy.states.approvalIdle);
      highlightSkill(0);
      focusIntent(false);
      const request = (state.userRequest || "").trim();
      const text = request || copy.states.engWriting;
      const alreadyTyped = bubbles.eng?.text === text;
      const remaining = typingRemaining();
      const typingMs = alreadyTyped ? 400 : remaining > 0 ? remaining : typingDuration(text);
      if (!alreadyTyped) typeBubble("eng", roles.eng, text, typingMs);
      fly("lane-request", copy.packet.request, "req", 1000);
      later(typingMs + 300, () => setActor("eng", { cls: "", text: copy.states.engSent }));
      return;
    }
    if (key === "understand") {
      setActors({
        ...base,
        eng: { cls: "", text: copy.states.engSent },
        oss: { cls: "active", text: copy.states.ossUnderstanding },
      });
      highlightSkill(0);
      note("oss", roles.oss, copy.states.ossReading);
      later(1200, () => {
        const intent = parseIntent(state.intentText || state.userRequest || "");
        const sentence = intent ? plainSentence(intent, copy.lang) : "";
        note(
          "oss",
          roles.oss,
          sentence ? `${copy.intent.summaryPrefix}${sentence}` : copy.states.ossUnderstanding,
        );
      });
      return;
    }
    if (key === "intent-review") {
      setActors({
        ...base,
        eng: { cls: "wait", text: copy.states.engIntent },
        oss: { cls: "wait", text: copy.states.ossWaitingHuman },
      });
      focusIntent(true);
      fly("lane-return", copy.intentReview.checkPacket, "req", 1000);
      note("oss", roles.oss, copy.intentReview.text, true);
      note("eng", roles.eng, copy.states.engIntent, true);
      return;
    }
    if (key === "send") {
      const plain = (state.protocol || "a2a-t") === "plain";
      setActors({
        ...base,
        eng: { cls: "ok", text: copy.states.engApproved },
        oss: { cls: "active", text: plain ? copy.states.ossSendingPlain : copy.states.ossBuilding },
        nms: { cls: "active", text: plain ? copy.states.nmsPlainReading : copy.states.nmsReading },
      });
      focusIntent(false);
      fly("lane-task", plain ? copy.packet.plain : copy.packet.taskT, "task", 1600);
      note("oss", roles.oss, plain ? copy.states.ossSendingPlain : copy.states.ossSending);
      note(
        "nms",
        roles.nms,
        plain ? copy.states.nmsPlainReadingBubble : copy.states.nmsReadingBubble,
      );
      later(900, () => {
        setActor("oss", { cls: "", text: copy.states.ossSent });
        setActor("nms", { cls: "active", text: copy.states.nmsReading });
      });
      return;
    }
    if (key === "propose") {
      setActors({
        ...base,
        eng: { cls: "wait", text: copy.states.engAnswering },
        oss: { cls: "active", text: copy.states.ossAsking },
        nms: { cls: "wait", text: copy.states.nmsNeedsConfirm },
      });
      highlightSkill(1);
      note("nms", roles.nms, proposalLine(copy, scenario, round) || copy.negotiation.generic, true);
      fly("lane-status", copy.negotiation.inputRequired, "neg", 1400);
      later(500, () => {
        note("oss", roles.oss, copy.states.ossAskingBubble, true);
        fly("lane-return", copy.negotiation.question, "neg", 1000);
      });
      return;
    }
    if (key === "decision") {
      const action = latestDecisionAction(state);
      setActors({
        ...base,
        eng: { cls: "ok", text: copy.states.engAnswered },
        oss: { cls: "active", text: copy.states.ossSendingAnswer },
        nms: { cls: "active", text: copy.states.nmsReadingAnswer },
      });
      highlightSkill(2);
      const selectedRegion = latestDecisionRegion(state);
      const answer = selectedRegion
        ? `${decisionLine(copy, scenario, round) || action} · ${selectedRegion}`
        : decisionLine(copy, scenario, round) || action;
      note("oss", roles.oss, answer, true);
      note("eng", roles.eng, answer, true);
      fly("lane-request", copy.negotiation.answer, "neg", 1000);
      later(400, () => fly("lane-task", copy.negotiation.clarified, "task", 1400));
      later(900, () => note("nms", roles.nms, copy.states.nmsClarified));
      return;
    }
    if (key === "strategy") {
      setActors({
        ...base,
        eng: { cls: "ok", text: copy.states.engApproved },
        oss: { cls: "", text: copy.states.ossFollowing },
        nms: { cls: "active", text: copy.states.nmsStrategy },
      });
      note("nms", roles.nms, copy.states.nmsStrategyBubble);
      note("oss", roles.oss, copy.states.ossTaskRunningBubble);
      fly("lane-status", "SUBMITTED", "status", 900);
      later(400, () => progressBar(100, 2400));
      return;
    }
    if (key === "strategy-ready") {
      setActors({
        ...base,
        eng: { cls: "ok", text: copy.states.engApproved },
        oss: { cls: "", text: copy.states.ossFollowing },
        nms: { cls: "active", text: copy.states.nmsStrategyReadyShort },
      });
      note("nms", roles.nms, copy.states.nmsStrategyReadyShort);
      fly("lane-status", copy.states.nmsStrategyReadyShort, "status", 900);
      return;
    }
    if (key === "task-started") {
      setActors({
        ...base,
        eng: { cls: "ok", text: copy.states.engApproved },
        oss: { cls: "", text: copy.states.ossFollowing },
        nms: { cls: "ok", text: copy.states.nmsTaskStarted },
      });
      note("nms", roles.nms, copy.states.nmsStrategyReadyBubble);
      note("cells", roles.cells, copy.dataCollection.noMml);
      fly("lane-status", copy.states.nmsTaskStarted, "status", 900);
      return;
    }
    if (key === "collect-start") {
      setActors({
        ...base,
        eng: { cls: "ok", text: copy.states.engApproved },
        oss: { cls: "", text: copy.states.ossFollowing },
        nms: { cls: "active", text: format(copy.states.nmsCollecting, { day: 1 }) },
        cells: { cls: "active", text: copy.states.cellsMeasuring },
      });
      setClock(format(copy.dataCollection.dayClock, { day: 1 }), copy.dataCollection.clockSub);
      note("nms", roles.nms, copy.dataCollection.start);
      fly("lane-data", copy.dataCollection.measurements, "data", 900);
      later(1100, () => fly("lane-data", copy.dataCollection.measurements, "data", 900));
      return;
    }
    if (key === "collect-progress") {
      setActors({
        ...base,
        eng: { cls: "ok", text: copy.states.engApproved },
        oss: { cls: "", text: copy.states.ossFollowing },
        cells: { cls: "active", text: copy.states.cellsMeasuring },
      });
      note("nms", roles.nms, copy.dataCollection.progress);
      animateDays(copy);
      fly("lane-data", copy.dataCollection.measurements, "data", 900);
      later(1400, () => fly("lane-data", copy.dataCollection.measurements, "data", 900));
      return;
    }
    if (key === "collect-done") {
      stopDays();
      dayProgress = 1;
      els.nmsBar.style.width = "100%";
      setActors({
        ...base,
        eng: { cls: "ok", text: copy.states.engApproved },
        oss: { cls: "", text: copy.states.ossFollowing },
        nms: { cls: "ok", text: copy.states.nmsCollectingDone },
        cells: { cls: "", text: copy.states.cellsBaseline },
      });
      setClock(format(copy.dataCollection.dayClock, { day: 8 }), copy.dataCollection.clockSub);
      note("nms", roles.nms, copy.dataCollection.done);
      note("oss", roles.oss, copy.dataCollection.ossDone);
      note("cells", roles.cells, copy.dataCollection.baselineMeasured);
      fly("lane-status", copy.dataCollection.donePacket, "status", 900);
      return;
    }
    if (key === "approval-ready") {
      if (state.protocol === "pure") {
        setActors({
          ...base,
          eng: { cls: "wait", text: copy.pure?.engWait || copy.states.engWaiting },
          oss: { cls: "active", text: copy.states.ossFollowing },
          nms: { cls: "active", text: copy.states.nmsEvaluating },
          cells: { cls: "", text: copy.states.cellsBaseline },
        });
        setMie("active", copy.states.approvalWaiting);
        note("nms", roles.nms, copy.pure?.planReady || copy.approval.suggestionsReady, true);
        note("oss", roles.oss, copy.states.ossFollowing, true);
        note("eng", roles.eng, copy.pure?.engWait || copy.states.engWaiting, true);
        return;
      }
      setActors({
        ...base,
        eng: { cls: "active", text: copy.states.engReviewing },
        oss: { cls: "active", text: copy.states.ossTelling },
        nms: { cls: "wait", text: copy.states.nmsSuggestions },
        cells: { cls: "", text: copy.states.cellsBaseline },
      });
      setMie("active", copy.states.approvalWaiting);
      note("nms", roles.nms, copy.approval.suggestionsReady, true);
      fly("lane-status", copy.approval.confirmInMie, "neg", 1300);
      later(500, () => {
        note("oss", roles.oss, copy.approval.tellingEngineer, true);
        fly("lane-return", copy.approval.reviewNeeded, "neg", 1000);
      });
      later(1100, () => {
        note("eng", roles.eng, copy.approval.checkingMml, true);
        fly("lane-approval", copy.approval.reviewInMie, "neg", 1600);
        setActor("eng", { cls: "wait", text: copy.states.engChecking });
      });
      return;
    }
    if (key === "approval-pure") {
      setActors({
        ...base,
        eng: { cls: "wait", text: copy.pure?.engWait || copy.states.engWaiting },
        oss: { cls: "wait", text: copy.states.ossWaitingResults },
        nms: { cls: "wait", text: copy.states.nmsWaitingApproval || copy.states.nmsSuggestions },
        cells: { cls: "", text: copy.states.cellsBaseline },
      });
      setMie("waiting", copy.states.approvalWaiting);
      note("nms", roles.nms, copy.pure?.mieRequest || copy.approval.suggestions, true);
      note("eng", roles.eng, copy.pure?.engWait || "");
      fly("lane-mie-request", copy.pure?.mieRequest || copy.approval.suggestions, "neg", 1200);
      later(700, () => {
        note("oss", roles.oss, copy.pure?.mieWait || copy.approval.waiting, true);
        setActor("nms", {
          cls: "wait",
          text: copy.states.nmsWaitingApproval || copy.states.nmsSuggestions,
        });
      });
      return;
    }
    if (key === "approval") {
      setActors({
        ...base,
        eng: { cls: "wait", text: copy.states.engChecking },
        oss: { cls: "wait", text: copy.states.ossWaitingHuman },
        nms: { cls: "wait", text: copy.states.nmsSuggestions },
        cells: { cls: "", text: copy.states.cellsBaseline },
      });
      setMie("waiting", copy.states.approvalWaiting);
      note("nms", roles.nms, copy.approval.suggestions, true);
      note("oss", roles.oss, copy.approval.waiting, true);
      note("eng", roles.eng, copy.approval.checkingMml, true);
      note("cells", roles.cells, copy.approval.policy, true);
      return;
    }
    if (key === "apply") {
      if (state.approvalState === "waiting") return;
      setActors({
        ...base,
        eng: { cls: "ok", text: copy.states.engAcknowledged },
        oss: { cls: "", text: copy.states.ossWaitingResults },
        nms: { cls: "active", text: copy.states.nmsWaitingResults },
        cells: { cls: "active", text: copy.states.cellsApplying },
      });
      setMie("ok", copy.states.approvalApproved);
      note("nms", roles.nms, copy.approval.awaitingResults);
      note("oss", roles.oss, copy.states.ossWaitingResults);
      note("eng", roles.eng, copy.approval.acknowledgedInMie);
      note("cells", roles.cells, copy.approval.previewBubble);
      fly("lane-mie-apply", copy.approval.mmlCommands, "cfg", 1400);
      later(1300, () => animatePreview(state, copy));
      return;
    }
    if (key === "execute") {
      setActors({
        ...base,
        eng: {
          cls: "ok",
          text: pureMode
            ? copy.pure?.engAcknowledged || copy.states.engAcknowledged
            : copy.states.engAcknowledged,
        },
        oss: { cls: "", text: copy.states.ossWaitingResults },
        nms: { cls: "active", text: copy.states.nmsExecuted },
        cells: { cls: "ok", text: copy.states.cellsApplied },
      });
      note("cells", roles.cells, copy.states.cellsApplied);
      fly("lane-data", copy.approval.executionResults, "data", 1200);
      later(600, () => {
        setActor("nms", { cls: "active", text: copy.states.nmsExecuted });
        note("nms", roles.nms, copy.states.nmsExecutedBubble);
        fly("lane-status", copy.approval.executionResults, "status", 900);
      });
      later(800, () => {
        setActor("oss", { cls: "ok", text: copy.states.ossResultsReceived });
        note("oss", roles.oss, copy.states.ossResultsReceived);
      });
      return;
    }
    if (key === "report") {
      setActors({
        ...base,
        eng: { cls: "ok", text: copy.states.engApproved },
        oss: { cls: "active", text: copy.states.ossSummarising },
        nms: { cls: "active", text: copy.states.nmsReporting },
        cells: { cls: "ok", text: copy.states.cellsApplied },
      });
      note("nms", roles.nms, copy.states.nmsReporting);
      note("oss", roles.oss, copy.states.ossSummarising);
      note("eng", roles.eng, copy.states.engApproved);
      fly("lane-data", copy.dataCollection.dailyKpis, "data", 900);
      later(700, () => fly("lane-status", copy.approval.reportToEngineer, "status", 1100));
      later(1400, () => {
        setActor("eng", { cls: "ok", text: copy.states.engReportReceived });
        note("eng", roles.eng, copy.states.engReportReceived);
        fly("lane-return", copy.approval.reportToEngineer, "req", 900);
      });
      return;
    }
    if (key === "finish") {
      const completed =
        state.finalState === "task_completed" || state.report !== null || state.reportArtifact;
      const ending = completed ? "done" : stageOf(state);
      if (ending === "done") {
        setActors({
          eng: { cls: "ok", text: copy.states.engReportReceived },
          oss: { cls: "ok", text: copy.states.ossDone },
          nms: { cls: "ok", text: copy.states.nmsDone },
          cells: { cls: "ok", text: copy.states.cellsApplied },
        });
        note("eng", roles.eng, copy.states.engReportReceived);
        note("oss", roles.oss, copy.states.ossDone);
        note("nms", roles.nms, copy.states.nmsDone);
      } else if (ending === "notSent") {
        setActors({
          eng: { cls: "wait", text: copy.result.intentNotConfirmedTitle },
          oss: { cls: "", text: copy.states.ossWaiting },
          nms: { cls: "", text: copy.states.nmsWaiting },
          cells: { cls: "", text: copy.states.cellsNormal },
        });
      } else if (ending === "notApplied" || ending === "plainRejected") {
        setActors({
          eng: { cls: "fail", text: copy.approval.rejected },
          oss: { cls: "", text: copy.states.ossDone },
          nms: { cls: "fail", text: copy.approval.rejected },
          cells: { cls: "", text: copy.states.cellsUnchanged },
        });
      } else if (ending === "failed") {
        setActors({
          eng: { cls: "", text: copy.states.engSent },
          oss: { cls: "", text: copy.states.ossDone },
          nms: { cls: "fail", text: copy.states.nmsDone },
          cells: { cls: "", text: copy.states.cellsNormal },
        });
      }
      return;
    }
    if (key === "end" || key === "abort") {
      setActors({
        eng: { cls: "", text: copy.states.engSent },
        oss: { cls: "", text: copy.negotiation.statusEnded },
        nms: { cls: "", text: copy.negotiation.statusEnded },
        cells: { cls: "", text: copy.states.cellsNormal },
      });
      note("nms", roles.nms, copy.negotiation.endLine);
      note("oss", roles.oss, copy.negotiation.endLine);
      return;
    }
    if (key === "approval-rejected") {
      setActors({
        ...base,
        eng: { cls: "fail", text: copy.approval.rejected },
        oss: { cls: "", text: copy.states.ossDone },
        nms: { cls: "fail", text: copy.approval.rejected },
        cells: { cls: "", text: copy.states.cellsUnchanged },
      });
      setMie("no", copy.states.approvalRejected);
      fly("lane-status", "REJECTED", "no", 900);
      note("nms", roles.nms, copy.approval.rejected, true);
      note("cells", roles.cells, copy.states.cellsUnchanged);
    }
  }

  /** Draw the persistent actor state of the current run. */
  function render(state, copy) {
    // Once a run starts, the authored scenes own the actor/MIE/clock state.
    if (directed) return;
    const stage = stageOf(state);
    if (stage === "idle") {
      setClock(copy.roles.cellsRegion, copy.roles.cellsSub);
      setMie("", copy.states.approvalIdle);
      highlightSkill(0);
      focusIntent(false);
    }
    const pending = pendingHuman(state);
    const { round, maxRounds } = roundInfo(state);
    const resolution = resolutionOf(state);
    const steps = executionSteps(state);
    const applyDone = steps[1].done;

    let eng = { cls: "", text: copy.states.idle };
    let oss = { cls: "", text: copy.states.ossWaiting };
    let nms = { cls: "", text: copy.states.nmsWaiting };
    let cells = { cls: "", text: copy.states.cellsNormal };

    if (stage === "negotiating") {
      eng = { cls: "ok", text: copy.states.engSent };
      if (negotiationExpected(state)) {
        oss = { cls: "active", text: copy.states.ossNegotiating };
        nms = {
          cls: "active",
          text: maxRounds > 0
            ? format(copy.states.nmsNegotiating, {
                round: Math.max(round, 1),
                max: maxRounds,
              })
            : copy.states.nmsValidating,
        };
      } else if (state.protocol === "plain") {
        oss = { cls: "active", text: copy.states.ossSendingPlain };
        nms = { cls: "active", text: copy.states.nmsPlainReading };
      } else {
        // The request is complete: the OSS is only waiting for the Task-T check, and the RAN
        // Agent is validating it. There is no negotiation to display.
        oss = { cls: "active", text: copy.states.ossWaitingAck };
        nms = { cls: "active", text: copy.states.nmsValidating };
      }
    } else if (stage === "executing") {
      eng = { cls: "ok", text: copy.states.engApproved };
      oss = { cls: "ok", text: copy.states.ossFollowing };
      if (state.approvalState === "waiting") {
        nms = { cls: "wait", text: copy.states.nmsApproval };
        eng = { cls: "wait", text: copy.states.engReviewing };
      } else if (state.reportArtifact || state.report !== null) {
        // The sites executed the plan and the RAN Agent returned the intent report.
        eng = { cls: "ok", text: copy.states.engReportReceived };
        oss = { cls: "ok", text: copy.states.ossDone };
        nms = { cls: "ok", text: copy.states.nmsDone };
        cells = { cls: "ok", text: copy.states.cellsApplied };
      } else if (applyDone || state.executed) {
        // The radio sites reported the execution results back to the RAN Agent.
        oss = { cls: "ok", text: copy.states.ossResultsReceived };
        nms = { cls: "active", text: copy.states.nmsExecuted };
        cells = { cls: "ok", text: copy.states.cellsApplied };
      } else if (state.mmlSent) {
        // MIE dispatched the MML; the RAN Agent waits for the sites to execute it.
        oss = { cls: "ok", text: copy.states.ossWaitingResults };
        nms = { cls: "active", text: copy.states.nmsWaitingResults };
        cells = { cls: "active", text: copy.states.cellsApplying };
      } else if (state.approvalState === "approved") {
        // The engineer approved; the server has not confirmed the MIE dispatch yet.
        oss = { cls: "ok", text: copy.states.ossWaitingResults };
        nms = { cls: "wait", text: copy.states.nmsWaitingDispatch };
        cells = { cls: "", text: copy.states.cellsBaseline };
      } else if (state.dataCollection === "started" || state.dataCollection === "progress") {
        const day = Math.max(1, Math.min(8, Math.round(1 + dayProgress * 7)));
        nms = {
          cls: "active",
          text: format(copy.states.nmsCollecting, { day }),
        };
        cells = { cls: "active", text: copy.states.cellsMeasuring };
      } else if (state.dataCollection === "completed") {
        nms = { cls: "active", text: copy.states.nmsCollectingDone };
        cells = { cls: "", text: copy.states.cellsBaseline };
      } else {
        nms = { cls: "active", text: copy.states.nmsStrategy };
        cells = { cls: "", text: copy.states.cellsNormal };
      }
    } else if (stage === "done") {
      eng = { cls: "ok", text: copy.states.engReportReceived };
      oss = { cls: "ok", text: copy.states.ossDone };
      nms = { cls: "ok", text: copy.states.nmsDone };
      cells = { cls: "ok", text: copy.states.cellsApplied };
    } else if (stage === "notApplied") {
      eng = { cls: "fail", text: copy.approval.rejected };
      oss = { cls: "ok", text: copy.states.ossDone };
      nms = { cls: "fail", text: copy.approval.rejected };
      cells = { cls: "", text: copy.states.cellsUnchanged };
    } else if (stage === "notSent") {
      eng = { cls: "wait", text: copy.states.engIntent };
      oss = { cls: "", text: copy.states.ossWaiting };
      nms = { cls: "", text: copy.states.nmsWaiting };
      cells = { cls: "", text: copy.states.cellsNormal };
    } else if (stage === "ended" || stage === "failed") {
      eng = { cls: "", text: copy.states.engSent };
      oss = { cls: "", text: copy.states.ossDone };
      nms = { cls: stage === "failed" ? "fail" : "", text: copy.negotiation.statusEnded };
      cells = { cls: "", text: copy.states.cellsNormal };
    }

    if (pending === "intent") {
      oss = { cls: "wait", text: copy.states.ossWaitingHuman };
      eng = { cls: "wait", text: copy.states.engIntent };
    }
    if (pending === "decision") {
      oss = { cls: "wait", text: copy.states.ossWaitingHuman };
      eng = { cls: "wait", text: copy.states.engWaiting };
    }
    if (pending === "approval") {
      nms = { cls: "wait", text: copy.states.nmsApproval };
      eng = { cls: "wait", text: copy.states.engReviewing };
    }
    if (resolution === "rejected" || resolution === "aborted") {
      nms = { cls: stage === "failed" ? "fail" : "", text: copy.negotiation.statusEnded };
    }

    setActor("eng", eng);
    setActor("oss", oss);
    setActor("nms", nms);
    setActor("cells", cells);

    const progress =
      stage === "done"
        ? 100
        : stage === "executing"
          ? applyDone || state.dataCollection === "completed" || state.approvalState !== null
            ? 100
            : state.dataCollection === "started" || state.dataCollection === "progress"
              ? Math.round(dayProgress * 100)
              : 20
          : stage === "negotiating"
            ? 12
            : 0;
    els.nmsBar.style.width = `${progress}%`;

    if (pending === "approval" || state.approvalState === "waiting") {
      els.approval.className = "approval waiting";
      els.approvalState.textContent = copy.states.approvalWaiting;
    } else if (state.approvalState === "approved") {
      els.approval.className = "approval approved";
      els.approvalState.textContent = state.mmlSent
        ? copy.states.approvalApproved
        : copy.states.approvalConfirmed;
    } else if (state.approvalState === "rejected") {
      els.approval.className = "approval rejected";
      els.approvalState.textContent = copy.states.approvalRejected;
    } else {
      els.approval.className = "approval";
      els.approvalState.textContent = copy.states.approvalIdle;
    }

    // The sites change when MIE dispatches the MML, not when the execution report arrives.
    const savingOn =
      stage === "done" || (stage === "executing" && (state.mmlSent || applyDone));
    const carriers = savingOn ? 2 : 3;
    const battery = savingOn;
    for (let index = 0; index < 6; index += 1) setCell(index, carriers, battery, false);
  }

  function setActor(name, value) {
    const actor = els.actors[name];
    actor.classList.remove("active", "wait", "ok", "fail");
    if (value.cls) actor.classList.add(value.cls);
    els.states[name].textContent = value.text;
  }

  function setActors(map) {
    for (const [name, value] of Object.entries(map)) setActor(name, value);
  }

  function buildCells(figure) {
    figure.replaceChildren();
    for (let index = 0; index < 6; index += 1) {
      const column = index % 3;
      const row = Math.floor(index / 3);
      const cx = 28 + column * 56;
      const cy = 10 + row * 56;
      const ay = cy + 12;
      const group = svg("g", { id: `cell-${index}` });
      for (let ring = 1; ring <= 3; ring += 1) {
        const r = 5 + ring * 5;
        const angle = (40 * Math.PI) / 180;
        const x1 = cx + r * Math.cos(-angle);
        const y1 = ay + r * Math.sin(-angle);
        const x2 = cx + r * Math.cos(angle);
        const y2 = ay + r * Math.sin(angle);
        group.append(
          svg("path", {
            class: "cell-wave on",
            "data-k": ring,
            d: `M${x1.toFixed(1)} ${y1.toFixed(1)}A${r} ${r} 0 0 1 ${x2.toFixed(1)} ${y2.toFixed(1)}`,
          }),
          svg("path", {
            class: "cell-wave on",
            "data-k": ring,
            d: `M${(2 * cx - x1).toFixed(1)} ${y1.toFixed(1)}A${r} ${r} 0 0 0 ${(2 * cx - x2).toFixed(1)} ${y2.toFixed(1)}`,
          }),
        );
      }
      group.append(
        svg("path", {
          class: "cell-mast",
          d: `M${cx} ${ay}L${cx - 7} ${cy + 36}M${cx} ${ay}L${cx + 7} ${cy + 36}M${cx - 4} ${cy + 27}h8`,
        }),
        svg("circle", { cx, cy: ay, r: 2.6, fill: "#000" }),
        svg("g", { class: "cell-bat", id: `cell-bat-${index}` }),
      );
      const batteryGroup = group.querySelector(`#cell-bat-${index}`);
      batteryGroup.append(
        svg("rect", {
          x: cx + 9,
          y: cy + 24,
          width: 13,
          height: 8,
          fill: "#fff",
          stroke: "#000",
          "stroke-width": 1.3,
        }),
        svg("rect", { x: cx + 22, y: cy + 26.5, width: 2, height: 3, fill: "#000" }),
        svg("rect", { x: cx + 10.5, y: cy + 25.5, width: 5, height: 5, fill: "#FFD200" }),
      );
      group.append(
        svg(
          "text",
          {
            class: "cell-lbl",
            id: `cell-lbl-${index}`,
            x: cx,
            y: cy + 47,
            "text-anchor": "middle",
          },
          index < 2 ? `cell-00${index + 1}` : `site-${index + 1}`,
        ),
      );
      figure.append(group);
    }
  }

  function setCell(index, carriers, battery, hit) {
    const group = document.getElementById(`cell-${index}`);
    if (!group) return;
    for (const wave of group.querySelectorAll(".cell-wave")) {
      wave.classList.toggle("on", Number(wave.dataset.k) <= carriers);
    }
    const bat = document.getElementById(`cell-bat-${index}`);
    if (bat) bat.classList.toggle("on", Boolean(battery));
    const label = document.getElementById(`cell-lbl-${index}`);
    if (label) label.classList.toggle("hit", Boolean(hit));
  }

  function latestDecisionAction(state) {
    for (let index = state.negotiation.length - 1; index >= 0; index -= 1) {
      const turn = state.negotiation[index];
      if (turn.side === "oss") return turn.action;
    }
    return null;
  }

  function latestDecisionRegion(state) {
    for (let index = state.negotiation.length - 1; index >= 0; index -= 1) {
      const turn = state.negotiation[index];
      if (turn.side === "oss") return turn.detail?.filledParams?.region || null;
    }
    return null;
  }

  function showRequest(copy, text) {
    const value = String(text || "").trim();
    if (!value) return;
    directed = false;
    setActor("eng", { cls: "active", text: copy.states.engWriting || "" });
    typeBubble("eng", copy.roles.eng, value, typingDuration(value));
  }

  return {
    render,
    scene,
    note,
    reset,
    fly,
    showRequest,
    typingRemaining,
    intentResolved,
    approvalResolved,
  };
}

export function setStageCopy(copy) {
  const set = (id, value) => {
    const node = document.getElementById(id);
    if (node) node.textContent = value;
  };
  set("zone-label-it", copy.map.zones[0]);
  set("zone-label-net", copy.map.zones[1]);
  set("chan-label", copy.map.channel);
  set("chan-sub1", copy.map.channelSub1);
  set("chan-sub2", copy.map.channelSub2);
  set("lane-task-label", copy.map.laneTask);
  set("lane-status-label", copy.map.laneStatus);
  set("lane-approval-label", copy.map.laneApproval);
  set("eng-name", copy.roles.eng);
  set("eng-org", copy.roles.engOrg);
  set("oss-name", copy.roles.oss);
  set("oss-org", copy.roles.ossOrg);
  set("nms-name", copy.roles.nms);
  set("nms-org", copy.roles.nmsOrg);
  set("nms-note", copy.roles.nmsNote);
  set("approval-name", copy.roles.approval);
  set("approval-org", copy.roles.approvalOrg);
  set("cells-clock-main", copy.roles.cellsRegion);
  set("cells-clock-sub", copy.roles.cellsSub);
}
