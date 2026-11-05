/** Run state of the presentation page.
 *
 * The reducer consumes the same SSE frames as the Run Explorer. It keeps what the stage and the
 * evidence panels need: the negotiation turns, the structured intent, the trace rows, the pending
 * human decisions and the final report. It is a pure function of the frames so a replay after a
 * reconnect (or a step back) never duplicates a row.
 */

import { hhmm, parseIntent, taskTFromTask } from "./intent.js";

export const STATUS = {
  IDLE: "idle",
  RUNNING: "running",
  FINISHED: "finished",
  CANCELED: "canceled",
  FAILED: "failed",
};

export const APPROVAL = {
  WAITING: "waiting",
  APPROVED: "approved",
  REJECTED: "rejected",
};

/** One page state, before any run. */
export function createState() {
  return {
    status: STATUS.IDLE,
    started: false,
    runId: null,
    // Redacted NMS exchanges captured for this run; the wire inspector renders the request from
    // them instead of reconstructing one.
    wire: [],
    scenario: null,
    mode: null,
    protocol: "a2a-t",
    userRequest: null,
    rawRequest: null,
    normalizedRequest: null,
    businessIntent: null,
    intentSource: null,
    intentWarnings: [],
    expectedSequence: [],
    artifactTools: [],
    negotiation: [],
    seen: new Set(),
    taskCreated: false,
    taskState: null,
    taskCompleted: false,
    taskId: null,
    //: Report of the baseline phase, kept when the Modify phase starts.
    baselineReport: null,
    //: 0 for the baseline Create phase, 1 for the Modify continuation.
    phaseIndex: 0,
    //: Phase discriminator used by the trace dedupe keys.
    phaseKey: "baseline",
    //: Whether at least one Task-T has been sent (the next Task-T is a Modify).
    taskTSent: false,
    //: Whether this run has a planned second (Modify) phase.
    twoPhase: false,
    //: Whether the second (Modify) phase has started.
    modifyPhaseStarted: false,
    executionArtifacts: 0,
    reportArtifact: false,
    report: null,
    finalState: null,
    error: null,
    finished: false,
    negotiationEnded: false,
    intentText: null,
    taskTPrompt: null,
    intentReview: null,
    dataCollection: null,
    mmlSent: false,
    executed: false,
    trace: [],
    decision: null,
    approval: null,
    approvalState: null,
    nmsTool: null,
    lastStatusText: "",
    nmsStage: "strategy",
    pure: null,
    //: Langfuse export state carried by ``run_started`` / ``finished``.
    langfuse: null,
    hop: null,
    pureRound: 0,
    pureMetrics: null,
    mieApproval: null,
    mieResolved: null,
    mieWaiting: false,
  };
}

/**
 * Start the Modify phase of a two-phase run.
 *
 * The A2A Task is the same, so `taskCreated`/`taskState` stay. Everything that describes the
 * current execution pass resets, so the second MIE approval gate can reopen and the second report
 * becomes the result card instead of being hidden by the baseline one.
 */
function startModifyPhase(state) {
  if (state.modifyPhaseStarted) return;
  state.modifyPhaseStarted = true;
  state.phaseIndex += 1;
  state.phaseKey = "modify";
  state.baselineReport = isObject(state.report) ? state.report : null;
  state.reportArtifact = false;
  state.report = null;
  state.executionArtifacts = 0;
  state.artifactTools = [];
  state.dataCollection = null;
  state.mmlSent = false;
  state.executed = false;
  state.approval = null;
  state.approvalState = null;
  state.decision = null;
  state.intentReview = null;
  state.negotiationEnded = false;
  state.taskTPrompt = null;
  state.lastStatusText = "";
  state.nmsTool = null;
  state.nmsStage = "strategy";
  state.mieApproval = null;
  state.mieResolved = null;
  state.mieWaiting = false;
}

/** Apply one SSE frame to the state; returns the same state for chaining. */
export function applyFrame(state, frame) {
  if (!isObject(frame)) return state;
  switch (frame.type) {
    case "run_started": {
      if (state.runId !== null && frame.runId !== state.runId) return state;
      state.started = true;
      state.runId = typeof frame.runId === "string" ? frame.runId : state.runId;
      if (typeof frame.scenario === "string") state.scenario = frame.scenario;
      if (typeof frame.mode === "string") state.mode = frame.mode;
      if (frame.protocol === "plain") state.protocol = "plain";
      else if (frame.protocol === "pure") state.protocol = "pure";
      else if (frame.protocol === "a2a-t") state.protocol = "a2a-t";
      // The page starts with the engineer's concise request. The backend `run_started` frame may
      // carry the richer catalog prompt used for Task-T generation; keep the visible request as
      // the engineer wrote it.
      if (typeof frame.userRequest === "string" && !state.userRequest) {
        state.userRequest = frame.userRequest;
      }
      if (state.userRequest && !state.intentText) state.intentText = state.userRequest;
      if (Array.isArray(frame.expectedSequence)) state.expectedSequence = frame.expectedSequence;
      if (isObject(frame.langfuse)) state.langfuse = frame.langfuse;
      if (state.status === STATUS.IDLE) state.status = STATUS.RUNNING;
      return state;
    }
    case "event":
      return applyEvent(state, frame.event);
    case "trace":
      return applyTrace(state, frame.event);
    case "intent_review_requested": {
      state.intentReview = {
        promptText: typeof frame.promptText === "string" ? frame.promptText : "",
        timeoutSeconds: numberOrNull(frame.timeoutSeconds),
        requestedAt: typeof frame.requestedAt === "string" ? frame.requestedAt : null,
        resolved: false,
      };
      if (!state.intentText && state.intentReview.promptText) {
        state.intentText = state.intentReview.promptText;
      }
      return state;
    }
    case "decision_requested": {
      state.decision = {
        proposal: isObject(frame.proposal) ? frame.proposal : {},
        round: intOrNull(frame.round),
        maxRounds: intOrNull(frame.maxRounds),
        performative: typeof frame.performative === "string" ? frame.performative : "",
        timeoutSeconds: numberOrNull(frame.timeoutSeconds),
        requestedAt: typeof frame.requestedAt === "string" ? frame.requestedAt : null,
        recommendation: isObject(frame.recommendation) ? frame.recommendation : null,
        resolved: false,
      };
      pushTrace(state, {
        tag: "human",
        title: "Decision requested",
        titleKey: "decisionRequested",
        sub: `round ${frame.round ?? "?"} · ${frame.proposal?.kind ?? ""}`,
        chip: "decision",
        chipKind: "neg",
        decision: true,
        stage: "negotiation",
        dir: "OSS internal",
        order: traceOrder({ ts: frame.requestedAt }, Date.now()),
        dedupeKey: `decision:${frame.round ?? "?"}`,
      });
      return state;
    }
    case "decision_recommended": {
      if (state.decision !== null) {
        state.decision.recommendation = isObject(frame.decision) ? frame.decision : null;
      }
      return state;
    }
    case "finished": {
      if (state.finished) return state;
      state.finished = true;
      if (frame.status === "interrupted") {
        // The run task was cancelled: either by the operator's Cancel button or by a process
        // shutdown. Both stop the same way and the presentation says so.
        state.status = STATUS.CANCELED;
      } else {
        state.status =
          frame.status === "failed" || (typeof frame.error === "string" && frame.error)
            ? STATUS.FAILED
            : STATUS.FINISHED;
      }
      state.finalState = typeof frame.finalState === "string" ? frame.finalState : null;
      state.report = isObject(frame.report) ? frame.report : null;
      if (isObject(frame.langfuse)) state.langfuse = frame.langfuse;
      state.error =
        typeof frame.error === "string" && frame.error ? frame.error : state.error;
      if (state.decision !== null) state.decision.resolved = true;
      if (state.intentReview !== null) state.intentReview.resolved = true;
      // If the run ended while a gate was still open (timeout, disconnect or failure), close the
      // card so the ending is visible instead of an immovable "waiting" card.
      if (state.approvalState === APPROVAL.WAITING) {
        state.approvalState = APPROVAL.REJECTED;
      }
      if (state.mieResolved === null && (state.mieWaiting || state.mieApproval !== null)) {
        state.mieResolved = { approved: false, reason: "finished" };
      }
      state.mieApproval = null;
      state.mieWaiting = false;
      return state;
    }
    default:
      return state;
  }
}

/** Record the business intent carried by a trace or a pure-mode OSS status event. */
function applyBusinessIntent(state, detail) {
  if (!isObject(detail)) return;
  const document = isObject(detail.intent) ? detail.intent : detail;
  if (typeof detail.rawRequest === "string" && detail.rawRequest) {
    state.rawRequest = detail.rawRequest;
  } else if (typeof document.rawRequest === "string" && document.rawRequest) {
    state.rawRequest = document.rawRequest;
  }
  if (typeof detail.normalizedText === "string" && detail.normalizedText) {
    state.normalizedRequest = detail.normalizedText;
    state.intentText = detail.normalizedText;
  } else if (typeof document.normalizedText === "string" && document.normalizedText) {
    state.normalizedRequest = document.normalizedText;
    state.intentText = document.normalizedText;
  }
  if (typeof document.source === "string") state.intentSource = document.source;
  else if (typeof detail.source === "string") state.intentSource = detail.source;
  const warnings = Array.isArray(document.warnings) ? document.warnings : detail.warnings;
  state.intentWarnings = Array.isArray(warnings)
    ? warnings.filter((item) => typeof item === "string")
    : [];
  state.businessIntent = document;
}

function applyEvent(state, entry) {
  if (!isObject(entry)) return state;
  const seq = entry.seq;
  if (Number.isInteger(seq)) {
    if (state.seen.has(seq)) return state;
    state.seen.add(seq);
  }
  const round = intOrNull(entry.round);
  const maxRounds = intOrNull(entry.maxRounds);
  const summary = typeof entry.summary === "string" ? entry.summary : "";
  const content = typeof entry.content === "string" ? entry.content : "";
  const pure = isObject(entry.detail?.pure) ? entry.detail.pure : null;
  const hop = typeof entry.detail?.hop === "string" ? entry.detail.hop : null;
  const intentProcessed = isObject(entry.detail?.intentProcessed)
    ? entry.detail.intentProcessed
    : null;
  if (isObject(entry.task) && typeof entry.task.id === "string" && entry.task.id) {
    state.taskId = entry.task.id;
  }
  if (intentProcessed !== null) applyBusinessIntent(state, intentProcessed);

  if (pure !== null || hop !== null) {
    if (pure !== null) state.pure = { ...(state.pure || {}), ...pure };
    if (hop !== null) state.hop = hop;
    if (pure !== null && pure.kind === "negotiation") {
      state.negotiation.push({
        side: "nms",
        action: "propose",
        round: intOrNull(pure.round) ?? round,
        maxRounds: intOrNull(pure.maxRounds) ?? maxRounds,
        summary,
        content,
        pure: true,
      });
      if (Number.isInteger(pure.round)) {
        state.pureRound = Math.max(state.pureRound || 0, pure.round);
      }
    }
    if (pure !== null && hop === "oss" && pure.phase === "answer") {
      state.negotiation.push({
        side: "oss",
        action: "accept",
        round: intOrNull(pure.round) ?? round,
        maxRounds: intOrNull(pure.maxRounds) ?? maxRounds,
        summary,
        content,
        pure: true,
      });
    }
    if (pure !== null && pure.kind === "approval") {
      state.mieWaiting = true;
    }
    if (pure !== null && Number.isInteger(pure.hops)) {
      state.pureMetrics = { ...pure, hop: hop ?? pure.hop ?? null };
    }
  }

  pushTrace(state, {
    tag: traceTag(entry, state.protocol),
    title: traceTitle(entry, summary),
    titleKey:
      entry.kind === "status" &&
      (entry.state === "TASK_STATE_INPUT_REQUIRED" || summary.includes("INPUT_REQUIRED"))
        ? "approval"
        : null,
    sub: traceSub(entry) || taskCreationSub(entry, state),
    chip: traceChip(entry),
    chipKind: chipKind(entry),
    entry,
  });

  if (entry.entryType === "decision") {
    state.negotiation.push({
      side: "oss",
      action: normaliseAction(entry.kind),
      round,
      maxRounds,
      summary,
      content,
      detail: isObject(entry.detail) ? entry.detail : null,
      confirmedBy: entry.confirmedBy || entry.detail?.confirmedBy || null,
    });
    if (state.decision !== null) state.decision.resolved = true;
    return state;
  }

  switch (entry.kind) {
    case "propose":
      state.negotiation.push({
        side: "nms",
        action: "propose",
        round,
        maxRounds,
        summary,
        content,
        negotiationType: entry.negotiationType || null,
      });
      break;
    case "abort":
      state.negotiation.push({
        side: "nms",
        action: "abort",
        round,
        maxRounds,
        summary,
        content,
      });
      break;
    case "message":
      if (entry.phase === "negotiation") {
        state.negotiationEnded = true;
        state.negotiation.push({
          side: "nms",
          action: "end",
          round,
          maxRounds,
          summary,
          content,
        });
      }
      break;
    case "status": {
      const label = summary || "";
      const taskState = typeof entry.state === "string" ? entry.state : "";
      if (label) state.lastStatusText = label;
      if (label.includes("Data collection started")) state.dataCollection = "started";
      if (label.includes("Data collection in progress")) state.dataCollection = "progress";
      if (label.includes("Data collection completed")) state.dataCollection = "completed";
      const inputRequired =
        taskState === "TASK_STATE_INPUT_REQUIRED" || label.includes("INPUT_REQUIRED");
      const working = taskState === "TASK_STATE_WORKING" || label.includes("WORKING");
      if (
        taskState === "TASK_STATE_SUBMITTED" ||
        label.includes("SUBMITTED") ||
        working ||
        inputRequired
      ) {
        state.taskCreated = true;
      }
      if (inputRequired) {
        if (state.approval === null) {
          // The status text already names the plan; the approval span (which also carries the
          // timeout) may arrive a moment later. Show the real plan immediately either way.
          state.approval = approvalFromText(label) || { generic: true };
        }
        // A replay or a late span must not reopen a gate the engineer already resolved.
        if (state.approvalState !== APPROVAL.APPROVED && state.approvalState !== APPROVAL.REJECTED) {
          state.approvalState = APPROVAL.WAITING;
        }
      } else if (working && state.approvalState === APPROVAL.WAITING) {
        // A WORKING update after the gate means the change was approved and MIE is dispatching
        // the MML to the radio sites.
        state.approvalState = APPROVAL.APPROVED;
        state.mmlSent = true;
      } else if (working && state.approvalState === APPROVAL.APPROVED) {
        // The page already marked the gate approved when the engineer clicked; this WORKING
        // update is the server confirming that MIE is dispatching.
        state.mmlSent = true;
      } else if (taskState === "TASK_STATE_REJECTED" || label.includes("REJECTED")) {
        state.approvalState = APPROVAL.REJECTED;
      }
      // The apply/modify result now arrives as a status notification only. Treat it as the
      // execution confirmation so a gate-off run still advances the apply/execute beat.
      if (label.includes("was applied to cell") || label.includes("was modified on cell")) {
        state.mmlSent = true;
        state.executed = true;
      }
      if (taskState === "TASK_STATE_COMPLETED") state.taskCompleted = true;
      if (label) state.taskState = label;
      break;
    }
    case "artifact": {
      if (entry.phase === "report") {
        state.reportArtifact = true;
        // The transcript carries the report data on the artifact, so the ending card can appear
        // before the terminal frame (a live planner may take seconds to close the Task).
        if (isObject(entry.data)) state.report = entry.data;
      } else {
        state.executionArtifacts += 1;
      }
      const tool = artifactTool(entry);
      if (tool) state.artifactTools.push(tool);
      if (tool === "apply_energy_saving_plan" || tool === "modify_energy_saving_plan") {
        // The artifact is the execution confirmation: MIE dispatched the MML and the sites
        // executed it. A gate-off run has no earlier WORKING update to mark the dispatch.
        state.mmlSent = true;
        state.executed = true;
      }
      break;
    }
    default:
      break;
  }
  return state;
}

function applyTrace(state, span) {
  if (!isObject(span)) return state;
  const seq = span.seq;
  if (Number.isInteger(seq)) {
    if (state.seen.has(seq)) return state;
    state.seen.add(seq);
  }
  const kind = typeof span.kind === "string" ? span.kind : "trace";
  const detail = isObject(span.detail) ? span.detail : null;
  // The second Task-T of a two-phase run is the Modify continuation. Its prompt renders before
  // the send, so the phase boundary is the task_modify trace, not the second message.
  if (
    (span.phase === "task_modify" || (detail && detail.samplePhase === "modify")) &&
    state.taskTSent
  ) {
    startModifyPhase(state);
  }
  if (kind === "message_sent" && detail && detail.performative === "TASK_T") {
    state.taskTSent = true;
    if (typeof detail.taskId === "string" && detail.taskId) state.taskId = detail.taskId;
    if (detail.samplePhase === "baseline" || detail.samplePhase === "modify") {
      state.twoPhase = true;
    }
  }
  // A planner turn is internal evidence for the tool that follows it. Tagging every turn as
  // "strategy" made the day badge jump back to Day 1 between collection and approval rows.
  let stage = traceStageKey(span);
  if (kind === "llm" && span.title === "Planner turn") stage = state.nmsStage || "strategy";
  if (kind === "tool") state.nmsStage = stage;

  pushTrace(state, {
    tag: traceSpanTag(span, kind, state.protocol),
    title: typeof span.title === "string" ? span.title : kind,
    titleKey:
      detail && typeof detail.titleKey === "string"
        ? detail.titleKey
        : kind === "decision_requested"
          ? "decisionRequested"
          : kind === "approval"
            ? "approval"
            : kind === "intent_review_requested"
              ? "intentReview"
              : kind === "intent_processed"
                ? "intentProcessed"
                : null,
    sub: typeof span.purpose === "string" ? span.purpose : "",
    chip: kind === "tool" ? "tool" : "",
    chipKind: kind === "tool" ? "status" : "",
    stage,
    entry: span,
  });

  if (kind === "intent_processed" && detail) {
    applyBusinessIntent(state, detail);
  }
  if (kind === "task_prompt_rendered" && detail && typeof detail.promptText === "string") {
    // The rendered prompt is the Task-T wire payload. The OSS intent panel shows the engineer's
    // own structured intent, not the Task-T; the wire inspector opens the prompt when needed.
    state.taskTPrompt = detail.promptText;
  }
  if (kind === "tool" && detail && typeof detail.toolName === "string") {
    state.nmsTool = detail.toolName;
  }
  if (kind === "approval") {
    const approved = detail && detail.approved === true;
    if (span.title === "Approval requested" || (detail && detail.approvalState === "requested")) {
      state.approval = {
        cellId: detail?.cellId ?? null,
        window: detail?.window ?? null,
        rate: detail?.rate ?? detail?.targetRate ?? null,
        taskId: detail?.taskId ?? null,
        timeoutSeconds: numberOrNull(detail?.timeoutSeconds),
        requestedAt:
          typeof span.startedAt === "string"
            ? span.startedAt
            : typeof span.ts === "string"
              ? span.ts
              : null,
        generic: false,
      };
      // The resolved span (or the engineer's click) may already have settled this gate.
      if (state.approvalState !== APPROVAL.APPROVED && state.approvalState !== APPROVAL.REJECTED) {
        state.approvalState = APPROVAL.WAITING;
      }
    } else if (approved) {
      state.approvalState = APPROVAL.APPROVED;
    } else if (detail && detail.approvalState === "rejected") {
      state.approvalState = APPROVAL.REJECTED;
    }
  }
  return state;
}

/** The act the page should show. */
export function stageOf(state) {
  if (state.status === STATUS.CANCELED) return "canceled";
  if (state.status === STATUS.FAILED) return "failed";
  if (state.status === STATUS.FINISHED) {
    if (state.finalState === "intent_not_confirmed") return "notSent";
    if (state.finalState === "task_rejected" && state.protocol === "plain") return "plainRejected";
    if (state.finalState === "task_rejected" || state.approvalState === APPROVAL.REJECTED) {
      return "notApplied";
    }
    const completed =
      state.report !== null || state.reportArtifact || state.finalState === "task_completed";
    return completed ? "done" : "ended";
  }
  if (state.status === STATUS.RUNNING) {
    return state.taskCreated ? "executing" : "negotiating";
  }
  return "idle";
}

/** The highest round seen and its budget. */
export function roundInfo(state) {
  let round = 0;
  let maxRounds = 0;
  for (const message of state.negotiation) {
    if (Number.isInteger(message.round)) round = Math.max(round, message.round);
    if (Number.isInteger(message.maxRounds)) maxRounds = Math.max(maxRounds, message.maxRounds);
  }
  return { round, maxRounds };
}

/** How the negotiation ended: ``accepted``, ``rejected``, ``aborted`` or ``null``. */
export function resolutionOf(state) {
  for (let index = state.negotiation.length - 1; index >= 0; index -= 1) {
    const message = state.negotiation[index];
    if (message.action === "abort") return "aborted";
    if (message.side === "oss" && message.action === "accept") return "accepted";
    if (message.side === "oss" && message.action === "reject") return "rejected";
  }
  return null;
}

/** Ending of a run that stopped without a task: the resolution plus the peer's last text.
 *
 * A plain terminal message from the RAN Agent is the only place its real reason survives (for
 * example a failed accept validation); a rejected or aborted negotiation carries its own copy.
 */
export function endingOf(state) {
  let detail = "";
  for (let index = state.negotiation.length - 1; index >= 0; index -= 1) {
    const message = state.negotiation[index];
    if (message.side === "nms" && message.action === "end") {
      detail = String(message.summary || message.content || "").trim();
      break;
    }
  }
  return { resolution: resolutionOf(state), detail };
}

/** Whether this run can open a pre-task negotiation at all.
 *
 * A complete request has an empty expected sequence: the RAN Agent validates the Task-T and
 * creates the task directly. The presentation must not show a negotiation phase for it.
 */
export function negotiationExpected(state) {
  if (state.protocol === "pure") {
    return (
      state.scenario === "information" ||
      state.scenario === "target" ||
      state.negotiation.length > 0
    );
  }
  return (
    state.protocol !== "plain" &&
    (state.expectedSequence.length > 0 || state.negotiation.length > 0)
  );
}

/** The tool that produced one artifact, or ``""`` when the frame carries no tool metadata. */
export function artifactTool(entry) {
  const detail = isObject(entry?.detail) ? entry.detail : null;
  return detail && typeof detail.toolName === "string" ? detail.toolName : "";
}

/** The NMS execution milestones and their live status. */
export function executionSteps(state) {
  // The NMS publishes one artifact per tool call. Classify them by the tool that produced them:
  // the live planner may read several profiles and evaluate several candidates, so the artifact
  // position says nothing about whether a plan was applied.
  const changed =
    state.executed ||
    state.artifactTools.includes("apply_energy_saving_plan") ||
    state.artifactTools.includes("modify_energy_saving_plan");
  const steps = [
    { key: "evaluate", done: state.artifactTools.includes("evaluate_energy_saving_plan") },
    { key: "apply", done: changed },
    { key: "report", done: state.reportArtifact || state.report !== null },
  ];
  const activeIndex =
    state.status === STATUS.RUNNING ? steps.findIndex((step) => !step.done) : -1;
  return steps.map((step, index) => ({ ...step, active: index === activeIndex }));
}

/** Whether the run currently waits for the engineer. */
export function pendingHuman(state) {
  if (state.status !== STATUS.RUNNING) return null;
  if (state.mieWaiting && state.mieResolved === null) return "approval";
  if (state.intentReview !== null && !state.intentReview.resolved) return "intent";
  if (state.approvalState === APPROVAL.WAITING) return "approval";
  if (state.decision !== null && !state.decision.resolved) return "decision";
  return null;
}

/** Continuity evidence of a two-phase run: same Task, same plan, before/after rate. */
export function continuityModel(state) {
  if (!state.twoPhase || !state.modifyPhaseStarted) return null;
  const before = isObject(state.baselineReport) ? state.baselineReport : null;
  const after = isObject(state.report) ? state.report : null;
  if (before === null && after === null) return null;
  const beforeRate = numberOrNull(before?.target_rate ?? before?.energy?.saving_percent);
  const afterRate = numberOrNull(after?.target_rate ?? after?.energy?.saving_percent);
  return {
    taskId: typeof state.taskId === "string" ? state.taskId : "",
    planId: String(after?.plan_id || before?.plan_id || ""),
    beforeRate,
    afterRate,
    window: String(after?.window || before?.window || ""),
    status: String(after?.status || ""),
  };
}

/** Tag of the channel that carries one message: A2A-T or plain/pure A2A. */
function protocolTag(protocol) {
  return protocol === "a2a-t" ? "a2at" : "a2a";
}

//: Performatives that carry a Negotiation-T payload.
const NEGOTIATION_PERFORMATIVES = new Set(["PROPOSE", "ACCEPT", "REJECT", "COUNTER", "ABORT"]);

/** Return ``task-t``, ``neg-t`` or ``null`` when one row carries an A2A-T extension payload. */
function extensionTag(entry) {
  const detail = isObject(entry.detail) ? entry.detail : {};
  const template =
    (typeof detail.templateUri === "string" && detail.templateUri) ||
    (typeof entry.templateUri === "string" && entry.templateUri) ||
    "";
  if (template.startsWith("Task-T")) return "task-t";
  if (template.startsWith("Negotiation-T")) return "neg-t";
  const negotiationUri =
    typeof detail.negotiationUri === "string" ? detail.negotiationUri : "";
  if (negotiationUri.includes("Negotiation-T")) return "neg-t";
  const extensionUri = typeof detail.extensionUri === "string" ? detail.extensionUri : "";
  if (extensionUri.includes("Task-T")) return "task-t";
  const keys = Array.isArray(detail.metadataKeys)
    ? detail.metadataKeys.map((key) => String(key))
    : [];
  if (keys.some((key) => key.includes("Negotiation-T"))) return "neg-t";
  if (keys.some((key) => key.includes("Task-T"))) return "task-t";
  const performative = detail.performative || entry.performative || "";
  if (performative === "TASK_T") return "task-t";
  if (NEGOTIATION_PERFORMATIVES.has(performative) || entry.kind === "propose") return "neg-t";
  return null;
}

function traceSpanTag(span, kind, protocol) {
  if (kind === "llm") return "llm";
  if (kind === "approval" || span.actor === "human") return "human";
  if (kind === "intent_review_requested" || kind === "decision_requested") return "human";
  if (kind === "task_prompt_rendered") return "task-t";
  if (kind === "tool" || kind === "execution") return "nms";
  if (kind === "message_sent") return extensionTag(span) || protocolTag(protocol);
  return "oss";
}

function traceTag(entry, protocol) {
  if (entry.entryType === "decision") return "human";
  if (entry.kind === "approval" || entry.actor === "human") return "human";
  const extension = extensionTag(entry);
  if (extension !== null) return extension;
  const hop = isObject(entry.detail) ? entry.detail.hop : null;
  if (hop) return protocolTag(protocol);
  if (
    entry.kind === "status" ||
    entry.kind === "artifact" ||
    entry.kind === "message" ||
    entry.kind === "propose" ||
    entry.kind === "accept" ||
    entry.kind === "reject" ||
    entry.kind === "counter" ||
    entry.kind === "abort"
  ) {
    return protocolTag(protocol);
  }
  if (entry.direction === "client->server" || entry.direction === "server->client") {
    return protocolTag(protocol);
  }
  return "oss";
}

function traceTitle(entry, summary) {
  if (entry.kind === "propose") return "PROPOSE · NMS";
  if (entry.kind === "abort") return "ABORT · NMS";
  if (entry.kind === "accept") return "ACCEPT · OSS";
  if (entry.kind === "reject") return "REJECT · OSS";
  if (entry.kind === "counter") return "COUNTER · OSS";
  if (entry.kind === "status") return statusTitle(entry, summary);
  if (entry.kind === "artifact") return artifactTitle(entry);
  return summary || entry.kind || "event";
}

/** Short human title of one status notification; the full sentence stays in the wire/inspector. */
function statusTitle(entry, summary) {
  const text = summary.toLowerCase();
  const stateOnly = !summary || /^task_state_/i.test(summary);
  if (stateOnly && entry.state === "TASK_STATE_SUBMITTED") return "Task created";
  if (stateOnly && entry.state === "TASK_STATE_WORKING") return "Working";
  if (text.includes("baseline plan applied")) return "Baseline plan ready";
  if (text.includes("target modified")) return "Modify accepted";
  if (text.includes("strategy recommendation is being executed")) return "Strategy generation started";
  if (text.includes("strategy recommendation and parameter setting")) return "Strategy ready";
  if (text.includes("energy-saving task has started")) return "Task started";
  if (text.includes("data collection started")) return "Data collection started";
  if (text.includes("data collection in progress")) return "Data collection in progress";
  if (text.includes("data collection completed")) return "Data collection completed";
  if (text.includes("mie agent's approval") || text.includes("engineer's approval")) {
    return "Approval 2 requested";
  }
  if (text.includes("mie approved") || text.includes("approved. applying")) return "MML approved";
  if (text.includes("was applied to cell")) return "Suggestions executed";
  if (text.includes("was modified on cell")) return "Suggestions modified";
  if (text.includes("report is ready")) return "Intent report ready";
  if (text.includes("ran agent is working")) return "Task started";
  if (text.includes("oss agent completed")) return "Task completed";
  if (text.includes("ran energy-saving task completed")) return "Task completed";
  if (entry.state) return String(entry.state).replace("TASK_STATE_", "");
  return summary.length > 80 ? `${summary.slice(0, 79)}…` : summary || "status";
}

/** Short title of one artifact update. */
function artifactTitle(entry) {
  if (entry.phase === "report" || entry.name === "energy saving intent report") return "Intent report";
  const tool =
    isObject(entry.detail) && typeof entry.detail.toolName === "string" ? entry.detail.toolName : "";
  if (tool === "get_cell_energy_profile") return "Cell profile read";
  if (tool === "evaluate_energy_saving_plan") return "Plan evaluated";
  if (tool === "apply_energy_saving_plan") return "Suggestions executed";
  if (tool === "modify_energy_saving_plan") return "Suggestions modified";
  if (tool === "generate_energy_saving_report") return "Intent report";
  return "Tool result";
}

/** Short right-hand chip: the A2A task state, not the generic word "status". */
function traceChip(entry) {
  if (entry.kind === "status") {
    if (entry.state) return String(entry.state).replace("TASK_STATE_", "");
    return "status";
  }
  if (entry.kind === "artifact") return "artifact";
  if (entry.kind === "message") return entry.performative || "message";
  return entry.performative || entry.kind || "";
}

function traceSub(entry) {
  if (entry.entryType === "decision") return entry.rationale || "";
  if (entry.kind === "status" || entry.kind === "artifact") return "";
  return entry.negotiationType || entry.performative || "";
}

/** Compact creation parameters of the ``SUBMITTED`` status: area and guaranteed throughput. */
function taskCreationSub(entry, state) {
  if (entry.kind !== "status" || entry.state !== "TASK_STATE_SUBMITTED") return "";
  const prompt = state.taskTPrompt || taskTFromTask(entry.task);
  const intent = parseIntent(prompt);
  if (intent === null) return "";
  const parts = [];
  if (intent.area) parts.push(intent.area);
  if (intent.slots.length > 0) {
    // Keep the trace row short: the first target window plus the number of the remaining ones.
    // The wire inspector shows the full Task-T.
    const first = intent.slots[0];
    const window = `${hhmm(first.start)}–${hhmm(first.end)} ${first.rate}Mbps`;
    parts.push(intent.slots.length === 1 ? window : `${window} +${intent.slots.length - 1}`);
  }
  return parts.join(" · ");
}

function chipKind(entry) {
  if (entry.kind === "status") return "status";
  if (entry.kind === "artifact") return "art";
  if (entry.entryType === "decision") return "neg";
  if (entry.kind === "reject" || entry.kind === "abort") return "no";
  return "";
}

//: ``cell-001, 00:00~06:00, 30%`` inside the INPUT_REQUIRED status text.
const APPROVAL_PLAN_PATTERN = /([A-Za-z0-9_-]+),\s*(\d{2}:\d{2}\s*[~-]\s*\d{2}:\d{2}),\s*([\d.]+%)/;

/**
 * Demo stage of one trace row: the reference demo's day and step vocabulary.
 *
 * Protocol phases (``task_prompt``, ``negotiation``, ``execution``, ``report``) describe the SDK
 * layers, not what the audience is watching. The trace groups the same rows into the nine demo
 * steps: Day 1 covers request, negotiation and strategy; Days 2–8 are data collection; Day 9 is
 * the MML review; Day 9+ is apply and report.
 */
export function traceStageKey(entry) {
  if (!isObject(entry)) return "request";
  const kind = typeof entry.kind === "string" ? entry.kind : "";
  const summary = typeof entry.summary === "string" ? entry.summary : "";
  const state = typeof entry.state === "string" ? entry.state : "";
  const tool = isObject(entry.detail) && typeof entry.detail.toolName === "string"
    ? entry.detail.toolName
    : "";
  if (
    entry.phase === "task_modify" &&
    (kind === "message_sent" || kind === "task_prompt_rendered" || kind === "llm")
  ) {
    return "report";
  }
  if (kind === "artifact") {
    if (entry.phase === "report" || entry.name === "energy saving intent report") return "report";
    if (
      tool === "apply_energy_saving_plan" ||
      tool === "modify_energy_saving_plan" ||
      tool === "generate_energy_saving_report"
    ) {
      return "report";
    }
    if (tool === "get_cell_energy_profile") return "collection";
    if (tool === "evaluate_energy_saving_plan") return "mml";
    return "strategy";
  }
  if (kind === "approval") return "mml";
  if (kind === "decision_requested") return "negotiation";
  if (entry.phase === "negotiation" || entry.phase === "resolution") return "negotiation";
  if (kind === "tool") {
    if (entry.title === "get_cell_energy_profile") return "collection";
    if (
      entry.title === "apply_energy_saving_plan" ||
      entry.title === "modify_energy_saving_plan" ||
      entry.title === "generate_energy_saving_report"
    ) {
      return "report";
    }
    if (entry.title === "evaluate_energy_saving_plan") return "mml";
    return "strategy";
  }
  if (kind === "status") {
    if (summary.includes("Data collection")) return "collection";
    if (
      summary.includes("MIE Agent's approval") ||
      summary.includes("engineer's approval") ||
      summary.includes("MIE approved") ||
      summary.includes("Approved. Applying") ||
      state === "TASK_STATE_INPUT_REQUIRED"
    ) {
      return "mml";
    }
    if (
      summary.includes("was applied") ||
      summary.includes("was modified") ||
      summary.includes("Baseline plan applied") ||
      summary.includes("Target modified") ||
      summary.includes("report is ready") ||
      state === "TASK_STATE_COMPLETED"
    ) {
      return "report";
    }
    return "strategy";
  }
  if (kind === "message_sent" && summary.includes("Accept")) return "negotiation";
  if (kind === "llm") {
    const stage = typeof entry.stage === "string" ? entry.stage : "";
    if (stage.startsWith("negotiation") || stage.includes("accept")) return "negotiation";
    if (stage.startsWith("task_t_") || stage.includes("task_prompt")) return "request";
    return "strategy";
  }
  if (entry.phase === "negotiation" || ["propose", "accept", "counter", "reject", "abort"].includes(kind)) {
    return "negotiation";
  }
  if (entry.phase === "task_prompt" || entry.phase === "task_validation" || entry.phase === "dispatch") {
    return "request";
  }
  if (entry.phase === "execution") return "strategy";
  return "request";
}

/** Who is talking to whom, in the demo's roles, or ``""`` when the row is not a message. */
export function traceDirection(entry) {
  if (!isObject(entry)) return "";
  const hop = isObject(entry.detail) && typeof entry.detail.hop === "string" ? entry.detail.hop : "";
  if (hop === "oss") return "OSS → RAN";
  if (hop === "ran") return "RAN → OSS";
  if (hop === "mie") return "MIE ⇄ RAN";
  if (entry.kind === "approval" || entry.actor === "human") return "MIE ⇄ Engineer";
  if (entry.direction === "client->server") return "OSS → RAN";
  if (entry.direction === "server->client") return "RAN → OSS";
  if (entry.side === "server") return "RAN internal";
  if (entry.side === "client") return "OSS internal";
  if (entry.actor === "engineer") return "Engineer";
  return "";
}

/** Event time used to keep late server spans in causal order instead of arrival order. */
export function traceOrder(entry, fallback = Date.now()) {
  if (!isObject(entry)) return fallback;
  for (const value of [entry.ts, entry.startedAt, entry.endedAt]) {
    if (typeof value !== "string" || value === "") continue;
    const parsed = Date.parse(value);
    if (!Number.isNaN(parsed)) return parsed;
  }
  return fallback;
}

/**
 * Logical identity of a row that the wire and the trace describe twice.
 *
 * The server emits an ``INPUT_REQUIRED`` status and an approval span for the same gate; the
 * client also renders a status and a span for the same decision request. Keeping both looked like
 * "approval requested" repeated several times. The event row wins because it opens the real wire
 * message; the span only supplies state.
 */
export function traceDedupeKey(entry) {
  if (!isObject(entry)) return null;
  const kind = typeof entry.kind === "string" ? entry.kind : "";
  const summary = typeof entry.summary === "string" ? entry.summary : "";
  const detail = isObject(entry.detail) ? entry.detail : {};
  // A two-phase run repeats the same status/approval vocabulary. Prefixing the phase keeps the
  // second phase's rows instead of treating them as duplicates of the baseline phase.
  const phase = typeof entry.phase === "string" ? entry.phase : "";
  const prefix = `${phase}:`;
  if (kind === "status") {
    if (summary.includes("Data collection completed")) return `${prefix}status:collection-completed`;
    if (entry.state === "TASK_STATE_INPUT_REQUIRED") return `${prefix}approval:requested`;
    if (summary.includes("MIE Agent's approval")) return `${prefix}approval:requested`;
    if (/MIE approved\. Applying|^Approved\. Applying/.test(summary)) {
      return `${prefix}approval:resolved`;
    }
    if (summary.includes("was applied to cell") || summary.includes("was modified on cell")) {
      return `${prefix}status:apply-completed`;
    }
    if (summary.includes("energy-saving report is ready")) return `${prefix}status:report-ready`;
  }
  if (kind === "approval" || detail.pureKind === "approval") {
    const requested =
      summary.includes("requested") ||
      entry.title === "Approval requested" ||
      detail.approvalState === "requested";
    if (requested) return `${prefix}approval:requested`;
    const resolved =
      summary.includes("resolved") ||
      entry.title === "Approval resolved" ||
      detail.approvalState === "approved" ||
      detail.approvalState === "rejected";
    if (resolved) return `${prefix}approval:resolved`;
  }
  if (kind === "decision_requested") return `${prefix}decision:${entry.round ?? "?"}`;
  if (kind === "propose" && entry.phase === "negotiation") {
    return `${prefix}propose:${entry.round ?? "?"}:${entry.performative ?? ""}`;
  }
  return null;
}

/**
 * Insert one trace row in event-time order and drop duplicate protocol/span pairs.
 *
 * Rows arrive from two producers: the live event stream and the batched server trace. Server spans
 * are flushed later but carry an earlier timestamp, so appending them at the end made the time
 * column jump backwards. Inserting by timestamp keeps the session readable; the trace component
 * rebuilds its small window when an insertion is not an append.
 */
export function pushTrace(state, row) {
  const entry = isObject(row.entry) ? row.entry : null;
  const order = Number.isFinite(row.order) ? row.order : traceOrder(entry);
  const baseKey = row.dedupeKey ?? traceDedupeKey(entry);
  // The same protocol row can repeat in the baseline and the Modify phase; the state phase keeps
  // the second one instead of treating it as a duplicate.
  const dedupeKey = baseKey === null ? null : `${state.phaseKey || "baseline"}:${baseKey}`;
  if (dedupeKey !== null) {
    const existingIndex = state.trace.findIndex((item) => item.dedupeKey === dedupeKey);
    if (existingIndex >= 0) {
      const existing = state.trace[existingIndex];
      const existingIsEvent = existing.entry?.entryType === "event";
      const nextIsEvent = entry?.entryType === "event";
      if (existingIsEvent || !nextIsEvent) return false;
      state.trace.splice(existingIndex, 1);
    }
  }
  const item = {
    ...row,
    order,
    phase: typeof entry?.phase === "string" ? entry.phase : "",
    stage: row.stage ?? traceStageKey(entry),
    dir: row.dir ?? traceDirection(entry),
    dedupeKey,
  };
  let index = state.trace.length;
  while (index > 0 && (state.trace[index - 1].order ?? 0) > order) index -= 1;
  state.trace.splice(index, 0, item);
  return true;
}

/** Read the plan the approval status names, or ``null`` when the text carries none. */
function approvalFromText(text) {
  const match = APPROVAL_PLAN_PATTERN.exec(text);
  if (!match) return null;
  return {
    cellId: match[1],
    window: match[2].replace(/\s+/g, ""),
    rate: match[3],
    taskId: null,
    timeoutSeconds: null,
    requestedAt: null,
    generic: false,
  };
}

function normaliseAction(value) {
  const action = typeof value === "string" ? value.trim().toLowerCase() : "";
  return ["accept", "reject", "abort", "counter"].includes(action) ? action : "counter";
}

function intOrNull(value) {
  return Number.isInteger(value) && value > 0 ? value : null;
}

function numberOrNull(value) {
  if (typeof value === "number" && Number.isFinite(value)) return value;
  if (typeof value === "string" && value.trim() !== "") {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : null;
  }
  return null;
}

function isObject(value) {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}
