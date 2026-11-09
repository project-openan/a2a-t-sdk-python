/** Map one SSE frame to one stage scene.
 *
 * The backend runs at its own speed; the presentation player consumes these scenes at a human
 * pace. A scene carries the step it belongs to, how long it should stay on screen, and whether it
 * must wait for the engineer (approval 1, a negotiation decision or the MML review).
 */

import { artifactTool, negotiationTurn } from "./state.js";

export const STEP_KEYS = [
  "request",
  "understand",
  "intent",
  "send",
  "negotiate",
  "strategy",
  "collect",
  "approval",
  "apply",
];

/** Return the zero-based index of a step key, or -1. */
export function stepIndex(key) {
  return STEP_KEYS.indexOf(key);
}

/**
 * Return the scene of one frame, or ``null`` when the frame should be applied without a pause.
 *
 * @param {object} frame - one SSE frame.
 * @param {object} state - the view state *before* the frame is applied.
 * @returns {{key: string, step: number, duration: number, hold: boolean, frame: object}|null}
 */
export function sceneFor(frame, state) {
  if (!isObject(frame)) return null;
  switch (frame.type) {
    case "run_started": {
      // The engineer's request is typed at a human pace. Match the scene duration to that typing
      // animation; the start button may already have started it while the run was being created.
      const request = typeof frame.userRequest === "string" ? frame.userRequest : "";
      const duration = Math.min(6000, Math.max(2200, request.length * 40));
      return scene("request", duration, frame);
    }
    case "event":
      return eventScene(frame.event, state, frame);
    case "trace":
      return traceScene(frame.event, state, frame);
    case "intent_review_requested":
      return scene("intent-review", 0, frame, true);
    case "decision_requested":
      // Confirm mode delivers the proposal and the agent recommendation in one frame; manual
      // mode delivers the proposal alone. Both stop the show for the engineer.
      return scene("decision", 0, frame, true);
    case "decision_recommended":
      return scene("decision", 0, frame, false);
    case "finished":
      return scene("finish", 1200, frame);
    default:
      return null;
  }
}

function eventScene(entry, state, frame) {
  if (!isObject(entry)) return scene("event", 0, frame);
  if (entry.entryType === "decision") return scene("decision", 1600, frame);
  const pure = isObject(entry.detail?.pure) ? entry.detail.pure : null;
  const hop = typeof entry.detail?.hop === "string" ? entry.detail.hop : null;
  if (state.protocol === "pure" && entry.kind === "status") {
    // Pure A2A has its own phase vocabulary. The generic A2A-T status mapping assumes the server
    // Task is the one advancing the story, but in pure mode the outer OSS Task emits SUBMITTED /
    // WORKING lifecycle updates and the RAN reports ordinary progress after the MIE decision.
    // Letting those fall through to the generic WORKING branch would jump to the strategy step at
    // the start ("SUBMITTED") and rewind to it after approval ("MIE approved. Applying the plan.",
    // "task completed"), desynchronising the stage from the actual hop.
    const summary = typeof entry.summary === "string" ? entry.summary : "";
    const taskState = typeof entry.state === "string" ? entry.state : "";
    if (pure?.kind === "approval") {
      // The OSS Agent relays RAN's approval wait as WORKING; the MIE card itself arrives on the
      // separate MIE SSE. Hold at this scene until the human decision is known. A replay after
      // the decision is evidence only.
      if (state.mieResolved !== null) return scene("trace", 0, frame);
      return scene("approval-pure", 0, frame, true);
    }
    if (pure?.kind === "negotiation") return scene("propose", 1800, frame);
    if (hop === "oss" && pure?.phase === "receive") return scene("understand", 2600, frame);
    if (hop === "oss" && pure?.phase === "intent") return scene("understand", 2200, frame);
    if (hop === "oss" && pure?.phase === "answer") return scene("decision", 1600, frame);
    if (hop === "oss" && pure?.phase === "report") return scene("report", 2600, frame);
    if (summary.includes("Data collection started")) return scene("collect-start", 2200, frame);
    if (summary.includes("Data collection in progress")) {
      return scene("collect-progress", 3000, frame);
    }
    if (summary.includes("Data collection completed")) return scene("collect-done", 1800, frame);
    // The RAN starts planning only after the negotiation is settled; this is the strategy beat.
    if (summary.includes("RAN Agent is working")) return scene("strategy", 4200, frame);
    if (taskState === "TASK_STATE_REJECTED" || summary.includes("REJECTED")) {
      return scene("approval-rejected", 1200, frame);
    }
    if (["TASK_STATE_COMPLETED", "TASK_STATE_FAILED", "TASK_STATE_CANCELED"].includes(taskState)) {
      return scene("finish", 1200, frame);
    }
    // Lifecycle and post-approval progress are evidence; the apply artifact drives execution.
    return scene("trace", 0, frame);
  }
  if (state.protocol === "pure" && entry.kind === "artifact") {
    if (artifactTool(entry) === "apply_energy_saving_plan") {
      // Pure A2A has no MML dispatch; the apply artifact is RAN's execution confirmation.
      return scene("execute", 1800, frame);
    }
  }
  switch (entry.kind) {
    case "propose":
      return scene("propose", 1800, frame);
    case "abort":
      return scene("abort", 1400, frame);
    case "reject":
      return scene("decision", 1600, frame);
    case "accept":
      return scene("decision", 1600, frame);
    case "counter":
      return scene("decision", 1600, frame);
    case "message":
      return entry.phase === "negotiation" ? scene("end", 1100, frame) : scene("event", 0, frame);
    case "status": {
      const label = typeof entry.summary === "string" ? entry.summary : "";
      const taskState = typeof entry.state === "string" ? entry.state : "";
      if (label.includes("Baseline plan applied")) return scene("trace", 0, frame);
      if (label.includes("Target modified")) return scene("strategy", 4200, frame);
      if (label.includes("Data collection started")) {
        return scene("collect-start", 2200, frame);
      }
      if (label.includes("Data collection in progress")) {
        return scene("collect-progress", 3000, frame);
      }
      if (label.includes("Data collection completed")) {
        return scene("collect-done", 1800, frame);
      }
      // The reference demo's post-Task-T ladder: strategy started -> strategy ready -> the
      // long-running task started. Each is a separate RAN -> OSS notification.
      if (label.includes("Strategy recommendation is being executed")) {
        return scene("strategy", 4200, frame);
      }
      if (label.includes("Strategy recommendation and parameter setting")) {
        return scene("strategy-ready", 1800, frame);
      }
      if (label.includes("energy-saving task has started")) {
        return scene("task-started", 1600, frame);
      }
      // The apply/modify result is a status notification now, not a tool artifact. It drives the
      // execution beat: after a gate approval MIE already dispatched, otherwise this is the apply.
      if (label.includes("was applied to cell") || label.includes("was modified on cell")) {
        return state.mmlSent ? scene("execute", 1800, frame) : scene("apply", 6500, frame);
      }
      // The ready report is wire evidence: the apply status drove the execution beat above and
      // the report artifact drives the report beat. Treating it as a generic WORKING update would
      // replay a story beat after execution.
      if (label.includes("Progress: plan ")) return scene("trace", 0, frame);
      if (label.includes("Progress: the energy-saving report is ready.")) {
        return scene("trace", 0, frame);
      }
      if (negotiationTurn(entry) !== null) {
        // The vendor hands a Negotiation-T proposal back inside an INPUT_REQUIRED status. It is a
        // negotiation turn, not the MIE review: advance the negotiation beat without holding.
        return scene("propose", 1800, frame);
      }
      if (taskState === "TASK_STATE_INPUT_REQUIRED" || label.includes("INPUT_REQUIRED")) {
        // The MIE review is a human gate, but only when this run really has one. A --nms run has
        // no local approval route, so the status stays evidence instead of holding the page.
        return state.approvalGate === true
          ? scene("approval", 0, frame, true)
          : scene("trace", 0, frame);
      }
      if (taskState === "TASK_STATE_SUBMITTED" || label.includes("SUBMITTED")) {
        return scene("strategy", 4200, frame);
      }
      // The generic WORKING lifecycle update carries no business text; it is evidence that the
      // RAN Agent started working, and the strategy-started notification drives the beat itself.
      if (taskState === "TASK_STATE_WORKING" && (label === "" || label === taskState)) {
        return scene("trace", 0, frame);
      }
      if (taskState === "TASK_STATE_WORKING" || label.includes("WORKING")) {
        // Only a change the engineer approved may reach the network. Without that approval a
        // WORKING update is just progress; the apply artifact is the only other apply signal.
        if (state.approvalState === "approved") return scene("apply", 6500, frame);
        return scene("strategy", 4200, frame);
      }
      if (taskState === "TASK_STATE_REJECTED" || label.includes("REJECTED")) {
        return scene("approval-rejected", 1200, frame);
      }
      if (
        ["TASK_STATE_COMPLETED", "TASK_STATE_FAILED", "TASK_STATE_CANCELED"].includes(taskState) ||
        label.includes("COMPLETED") ||
        label.includes("FAILED")
      ) {
        return scene("finish", 1200, frame);
      }
      return scene("event", 0, frame);
    }
    case "artifact": {
      if (entry.phase === "report") return scene("report", 2600, frame);
      const tool = artifactTool(entry);
      if (tool === "get_cell_energy_profile") {
        return state.dataCollection === "completed"
          ? scene("trace", 0, frame)
          : scene("collect-done", 1400, frame);
      }
      if (tool === "evaluate_energy_saving_plan") return scene("approval-ready", 2000, frame);
      if (tool === "apply_energy_saving_plan" || tool === "modify_energy_saving_plan") {
        // Never show MML going to the network while the MIE review is still open. The artifact
        // is the execution confirmation: after the MIE dispatch it is the sites' report, not a
        // second dispatch.
        if (state.approvalState === "waiting") return scene("trace", 0, frame);
        return state.mmlSent ? scene("execute", 1800, frame) : scene("apply", 6500, frame);
      }
      return scene("trace", 0, frame);
    }
    default:
      return scene("event", 0, frame);
  }
}

function traceScene(span, state, frame) {
  if (!isObject(span)) return scene("trace", 0, frame);
  // Server spans are evidence, not story beats. They are posted to the UI in batches and can
  // arrive after the client events they explain; letting them drive the stage would replay a
  // step the audience has already passed (the approval replay was the visible symptom).
  if (span.side === "server") return null;
  if (span.kind === "intent_review_requested") {
    // The dedicated intent frame drives the hold and the card; its trace span only adds a row.
    return null;
  }
  if (span.kind === "intent_processed") {
    return scene("understand", 2200, frame);
  }
  if (span.kind === "task_prompt_rendered") {
    return state.intentText === null ? scene("understand", 2600, frame) : scene("trace", 0, frame);
  }
  if (span.kind === "message_sent") {
    const detail = isObject(span.detail) ? span.detail : {};
    const performative = typeof detail.performative === "string" ? detail.performative : "";
    if (performative === "TASK_T") {
      if (state.runMode === "modify") return scene("modify-start", 1800, frame);
      return state.taskTSent ? scene("modify-send", 1800, frame) : scene("send", 1800, frame);
    }
    return scene("negotiate-send", 500, frame);
  }
  if (span.kind === "tool") {
    const detail = isObject(span.detail) ? span.detail : {};
    const name = typeof detail.toolName === "string" ? detail.toolName : "";
    if (name === "get_cell_energy_profile") {
      if (state.dataCollection === "completed") return scene("trace", 0, frame);
      return state.dataCollection === "started"
        ? scene("collect-done", 900, frame)
        : scene("collect-start", 1200, frame);
    }
    if (name === "evaluate_energy_saving_plan") return scene("approval-ready", 900, frame);
    if (name === "apply_energy_saving_plan" || name === "modify_energy_saving_plan") {
      return scene("apply", 900, frame);
    }
  }
  if (span.kind === "approval") {
    const detail = isObject(span.detail) ? span.detail : {};
    if (span.title === "Approval requested" || detail.approvalState === "requested") {
      return state.approvalState === "waiting" ? scene("trace", 0, frame) : scene("approval", 0, frame, true);
    }
    return scene("trace", 300, frame);
  }
  return scene("trace", 0, frame);
}

function scene(key, duration, frame, hold = false) {
  return {
    key,
    step: stepIndex(stepOf(key)),
    duration,
    hold,
    frame,
  };
}

function stepOf(key) {
  switch (key) {
    case "request":
      return "request";
    case "understand":
      return "understand";
    case "intent-review":
      return "intent";
    case "send":
      return "send";
    case "modify-start":
      // A Modify-only run sends its Modify Task-T as the send step; there is no baseline.
      return "send";
    case "modify-send":
      // The Modify happens after the baseline report. Reusing the apply step keeps the step bar
      // from rewinding; the narration and the wire payload make the Modify explicit.
      return "apply";
    case "propose":
    case "decision":
    case "negotiate-send":
    case "end":
    case "abort":
      return "negotiate";
    case "strategy":
    case "strategy-ready":
    case "task-started":
      return "strategy";
    case "collect-start":
    case "collect-progress":
    case "collect-done":
      return "collect";
    case "approval":
    case "approval-pure":
    case "approval-ready":
    case "approval-rejected":
      return "approval";
    case "apply":
    case "execute":
    case "report":
    case "finish":
      return "apply";
    default:
      return "request";
  }
}

function isObject(value) {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}
