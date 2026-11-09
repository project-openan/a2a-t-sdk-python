// Presentation view-model checks (no DOM, no network).
//
// Run with: node tests/js/presentation.test.mjs
// The Python suite shells out to this file and skips cleanly when Node is absent.

import assert from "node:assert/strict";

import {
  STATUS,
  activityView,
  applyFrame,
  canStartModify,
  continuityModel,
  createState,
  endingOf,
  executionSteps,
  isModifiableTaskState,
  keepsAttemptChain,
  modifyStateLabel,
  negotiationExpected,
  negotiationTurn,
  pendingHuman,
  pushTrace,
  resolutionOf,
  roundInfo,
  stageOf,
  traceDirection,
  traceStageKey,
} from "../../demo/web/presentation/js/state.js";
import {
  clear as clearChain,
  identity as chainIdentity,
  pending as pendingChain,
  restore as restoreChain,
  save as saveChain,
} from "../../demo/web/presentation/js/chain.js";
import { sceneFor } from "../../demo/web/presentation/js/scenes.js";
import { createPlayer } from "../../demo/web/presentation/js/player.js";
import { entryOfRow, rowKey } from "../../demo/web/presentation/js/trace.js";
import { parseIntent, plainSentence } from "../../demo/web/presentation/js/intent.js";
import {
  approvalCard,
  decisionCard,
  decisionPayload,
  intentCard,
  pendingDecisionCard,
  validationCard,
} from "../../demo/web/presentation/js/decisions.js";
import { copyFor } from "../../demo/web/presentation/copy.js";
import { buildRecord, compareRows, outcomeText } from "../../demo/web/presentation/js/compare.js";
import { buildWireModel } from "../../demo/web/presentation/js/wire.js";

// The wire-model builder creates detached nodes; no page is rendered in this harness.
globalThis.document = {
  createElement(tag) {
    return { tagName: String(tag).toUpperCase(), className: "", textContent: "", setAttribute() {} };
  },
  createTextNode(text) {
    return { textContent: String(text) };
  },
};

const copy = copyFor("zh-CN");
const event = (payload) => ({ type: "event", event: payload });

// Every approval failure code the page can show has both translations.
assert.deepEqual(
  Object.keys(copyFor("en-US").approval.errors).sort(),
  Object.keys(copy.approval.errors).sort(),
);

// The sample has exactly two use cases: use case 1 covers the energy-saving scenarios (complete,
// negotiation and ending), use case 2 is the planned outage.
const tagOrder = ["night", "information", "unknown_area", "target", "feasibility", "reject", "abort", "outage"];
assert.deepEqual(
  tagOrder.map((name) => copyFor("en-US").scenarios[name].tag),
  ["Use case 1", "Use case 1 · negotiation", "Use case 1 · negotiation", "Use case 1 · negotiation", "Use case 1 · negotiation", "Use case 1 · ending", "Use case 1 · ending", "Use case 2"],
);
assert.deepEqual(
  tagOrder.map((name) => copy.scenarios[name].tag),
  ["用例 1", "用例 1 · 协商", "用例 1 · 协商", "用例 1 · 协商", "用例 1 · 协商", "用例 1 · 终止", "用例 1 · 终止", "用例 2"],
);

// The OSS Agent exposes one skill per use case; the tools behind them are not skills.
assert.deepEqual(copyFor("en-US").roles.ossSkills, ["Skill 1 · Energy saving", "Skill 2 · Planned outage"]);
assert.deepEqual(copy.roles.ossSkills, ["技能 1 · 节能", "技能 2 · 计划停电"]);
assert.equal(copy.roles.ossSkillTools, undefined);
assert.equal(modifyStateLabel(copy, "TASK_STATE_SUBMITTED"), "已提交");
assert.equal(modifyStateLabel(copyFor("en-US"), "TASK_STATE_SUBMITTED"), "SUBMITTED");
assert.equal(modifyStateLabel(copy, "TASK_STATE_WORKING"), "运行中");
assert.equal(modifyStateLabel(copy, "TASK_STATE_COMPLETED"), "TASK_STATE_COMPLETED");
assert.equal(modifyStateLabel(copy, "TASK_STATE_INPUT_REQUIRED"), "等待输入");
assert.equal(modifyStateLabel(copyFor("en-US"), "TASK_STATE_INPUT_REQUIRED"), "INPUT_REQUIRED");
assert.equal(isModifiableTaskState("TASK_STATE_SUBMITTED"), true);
assert.equal(isModifiableTaskState("TASK_STATE_WORKING"), true);
assert.equal(isModifiableTaskState("TASK_STATE_INPUT_REQUIRED"), false);
assert.equal(canStartModify(null), false);
assert.equal(canStartModify({ taskId: "" }), false);
assert.equal(canStartModify({ taskId: "task-1" }), true);

// A retry after a pre-send block keeps the attempt chain (conversation and trace id); a run that
// reached the server, completed or was cancelled starts a new one.
assert.equal(
  keepsAttemptChain(
    { type: "finished", status: "finished", finalState: "input_invalid" },
    createState(),
  ),
  true,
);
assert.equal(
  keepsAttemptChain(
    { type: "finished", status: "failed", error: "PromptGenerationError: 'Task Target' is not provided" },
    createState(),
  ),
  true,
);
assert.equal(
  keepsAttemptChain(
    { type: "finished", status: "failed", error: "negotiation failed" },
    { ...createState(), taskTSent: true },
  ),
  false,
);
assert.equal(
  keepsAttemptChain(
    { type: "finished", status: "finished", finalState: "task_completed" },
    createState(),
  ),
  false,
);
assert.equal(
  keepsAttemptChain(
    { type: "finished", status: "interrupted", finalState: null },
    createState(),
  ),
  false,
);

function proposal(seq, round, maxRounds = 3) {
  return event({
    seq,
    entryType: "event",
    kind: "propose",
    phase: "negotiation",
    round,
    maxRounds,
    performative: "PROPOSE",
    content: `full proposal ${round}`,
  });
}

function decision(seq, action, round, maxRounds = 3, detail = null) {
  return event({
    seq,
    entryType: "decision",
    kind: action,
    phase: "negotiation",
    round,
    maxRounds,
    performative: action.toUpperCase(),
    detail,
  });
}

function status(seq, summary, extra = null) {
  return event({
    seq,
    entryType: "event",
    kind: "status",
    phase: "execution",
    summary,
    ...(extra || {}),
  });
}

function artifact(seq, phase, tool = null) {
  return event({
    seq,
    entryType: "event",
    kind: "artifact",
    phase,
    detail: tool ? { toolName: tool } : null,
  });
}

function finished(finalState, report = null, error = null) {
  return { type: "finished", status: error ? "failed" : "finished", finalState, report, error };
}

// --- Accepted negotiation with a task and a report ----------------------

const accepted = createState();
assert.equal(stageOf(accepted), "idle");

applyFrame(accepted, { type: "run_started", runId: "r1", scenario: "target", mode: "auto" });
assert.equal(accepted.scenario, "target");
assert.equal(accepted.mode, "auto");
assert.equal(stageOf(accepted), "negotiating");

applyFrame(accepted, proposal(1, 1));
assert.deepEqual(roundInfo(accepted), { round: 1, maxRounds: 3 });
assert.equal(accepted.negotiation[0].content, "full proposal 1");

// The same frame replayed after a reconnect must not duplicate the row.
applyFrame(accepted, proposal(1, 1));
assert.equal(accepted.negotiation.length, 1);

applyFrame(accepted, decision(2, "counter", 1, 3, { counterParams: { window: "00:00~06:00" } }));
assert.deepEqual(accepted.negotiation[1].detail.counterParams, { window: "00:00~06:00" });
applyFrame(accepted, proposal(3, 2));
applyFrame(accepted, decision(4, "accept", 2));
assert.equal(resolutionOf(accepted), "accepted");
assert.equal(stageOf(accepted), "negotiating");

applyFrame(accepted, status(5, "TASK_STATE_SUBMITTED"));
assert.equal(stageOf(accepted), "executing");

// The generic WORKING lifecycle update after SUBMITTED is evidence, not a second strategy beat.
const genericWorking = status(6, "TASK_STATE_WORKING", { state: "TASK_STATE_WORKING" });
assert.equal(sceneFor(genericWorking, accepted).key, "trace");
assert.equal(accepted.taskCreated, true);

assert.deepEqual(
  executionSteps(accepted).map((step) => step.done),
  [false, false, false],
);

applyFrame(accepted, artifact(6, "execution", "get_cell_energy_profile"));
assert.deepEqual(
  executionSteps(accepted).map((step) => step.done),
  [false, false, false],
);
// The live planner may read several cells and evaluate several candidates. Position in the
// artifact stream says nothing about applying a plan.
applyFrame(accepted, artifact(7, "execution", "get_cell_energy_profile"));
assert.deepEqual(
  executionSteps(accepted).map((step) => step.done),
  [false, false, false],
);
applyFrame(accepted, artifact(8, "execution", "evaluate_energy_saving_plan"));
assert.deepEqual(
  executionSteps(accepted).map((step) => step.done),
  [true, false, false],
);
applyFrame(accepted, artifact(9, "execution", "evaluate_energy_saving_plan"));
assert.deepEqual(
  executionSteps(accepted).map((step) => step.done),
  [true, false, false],
);
applyFrame(accepted, artifact(10, "execution", "apply_energy_saving_plan"));
assert.deepEqual(
  executionSteps(accepted).map((step) => step.done),
  [true, true, false],
);

applyFrame(accepted, artifact(11, "report"));
assert.deepEqual(
  executionSteps(accepted).map((step) => step.done),
  [true, true, true],
);

applyFrame(
  accepted,
  finished("task_completed", { energy: { saving_percent: 30 }, cell_name: "Cell 1" }),
);
assert.equal(stageOf(accepted), "done");
assert.equal(accepted.report.energy.saving_percent, 30);

// --- Rejected negotiation: no task --------------------------------------

const rejected = createState();
applyFrame(rejected, { type: "run_started", runId: "r2", scenario: "reject", mode: "auto" });
applyFrame(rejected, proposal(1, 1));
applyFrame(rejected, decision(2, "reject", 1));
applyFrame(
  rejected,
  event({ seq: 3, entryType: "event", kind: "message", phase: "negotiation" }),
);
applyFrame(rejected, finished("no_task_negotiation_ended"));
assert.equal(resolutionOf(rejected), "rejected");
assert.equal(stageOf(rejected), "ended");
assert.equal(rejected.taskCreated, false);
assert.equal(endingOf(rejected).resolution, "rejected");

// --- An accepted negotiation the peer ended keeps the peer's own text ----

const endedAfterAccept = createState();
applyFrame(endedAfterAccept, {
  type: "run_started",
  runId: "r5",
  scenario: "information",
  mode: "confirm",
});
applyFrame(endedAfterAccept, proposal(1, 1));
applyFrame(endedAfterAccept, decision(2, "accept", 1));
applyFrame(
  endedAfterAccept,
  event({
    seq: 3,
    entryType: "event",
    kind: "message",
    phase: "negotiation",
    summary: "Accept validation failed: guaranteedThroughputTarget is missing.",
  }),
);
applyFrame(endedAfterAccept, finished("no_task_negotiation_ended"));
assert.equal(endingOf(endedAfterAccept).resolution, "accepted");
assert.match(endingOf(endedAfterAccept).detail, /Accept validation failed/);
assert.equal(stageOf(endedAfterAccept), "ended");

// --- Aborted negotiation: the NMS abort wins over the last counter ------

const aborted = createState();
applyFrame(aborted, { type: "run_started", runId: "r3", scenario: "abort", mode: "auto" });
applyFrame(aborted, proposal(1, 1));
applyFrame(aborted, decision(2, "counter", 1));
applyFrame(aborted, proposal(3, 2));
applyFrame(aborted, decision(4, "counter", 2));
applyFrame(aborted, proposal(5, 3));
applyFrame(aborted, decision(6, "counter", 3));
applyFrame(
  aborted,
  event({ seq: 7, entryType: "event", kind: "abort", phase: "negotiation", round: 3 }),
);
applyFrame(aborted, finished("no_task_negotiation_aborted"));
assert.equal(resolutionOf(aborted), "aborted");
assert.equal(stageOf(aborted), "ended");
assert.deepEqual(roundInfo(aborted), { round: 3, maxRounds: 3 });

// --- A failed run keeps its error --------------------------------------

const failed = createState();
applyFrame(failed, { type: "run_started", runId: "r4", scenario: "information", mode: "auto" });
applyFrame(failed, finished("incomplete", null, "server unavailable"));
assert.equal(stageOf(failed), "failed");
assert.equal(failed.error, "server unavailable");

// --- A downstream Task failure is a failure, not a neutral ending ---------

const downstreamFailed = createState();
applyFrame(downstreamFailed, { type: "run_started", runId: "r4f", scenario: "night", mode: "confirm" });
applyFrame(downstreamFailed, finished("task_failed", null, ""));
assert.equal(downstreamFailed.status, STATUS.FINISHED);
assert.equal(stageOf(downstreamFailed), "failed");

// --- A cancelled run reports its own ending ------------------------------

const canceled = createState();
applyFrame(canceled, { type: "run_started", runId: "r4c", scenario: "information", mode: "manual" });
applyFrame(canceled, {
  type: "finished",
  status: "interrupted",
  finalState: null,
  report: null,
  error: "The run was cancelled by the operator.",
});
assert.equal(canceled.status, STATUS.CANCELED);
assert.equal(stageOf(canceled), "canceled");

// --- Confirm mode: the decision frame carries the recommendation --------

const confirming = createState();
applyFrame(confirming, { type: "run_started", runId: "r5", scenario: "target", mode: "confirm" });
applyFrame(confirming, proposal(1, 1));
applyFrame(confirming, {
  type: "decision_requested",
  round: 1,
  maxRounds: 3,
  proposal: { kind: "target", round: 1 },
  recommendation: {
    action: "counter",
    rationale: "overlap",
    counter_proposal: "keep 17:30~20:00",
  },
  timeoutSeconds: 300,
  requestedAt: new Date().toISOString(),
});
assert.equal(pendingHuman(confirming), "decision");
assert.equal(confirming.decision.recommendation.action, "counter");
const card = decisionCard(confirming, copy);
assert.ok(card);
assert.deepEqual(
  card.options.map((option) => option.action),
  ["confirm", "accept", "counter", "reject"],
);
assert.equal(card.options[0].label, copy.decision.confirmCounter);
assert.equal(card.manualOpen, false);
assert.ok(card.options.length <= 4);
assert.equal(decisionPayload(card, "confirm", copy).action, "counter");
assert.equal(decisionPayload(card, "counter", copy, "new text").counter_proposal, "new text");
assert.equal(decisionPayload(card, "reject", copy).action, "reject");

// In confirm mode a decision without a recommendation is not offered yet.
const thinking = createState();
applyFrame(thinking, { type: "run_started", runId: "r6", scenario: "target", mode: "confirm" });
applyFrame(thinking, {
  type: "decision_requested",
  round: 1,
  maxRounds: 3,
  proposal: { kind: "target" },
});
assert.equal(decisionCard(thinking, copy), null);

// A failed recommendation is published as unavailable: the engineer still decides.
const unavailable = createState();
applyFrame(unavailable, { type: "run_started", runId: "r7", scenario: "target", mode: "confirm" });
applyFrame(unavailable, {
  type: "decision_requested",
  round: 1,
  maxRounds: 3,
  proposal: { kind: "target", round: 1 },
  recommendationUnavailable: true,
  recommendationError: "DecisionProviderError: no parseable decision",
  timeoutSeconds: 300,
  requestedAt: new Date().toISOString(),
});
assert.equal(unavailable.decision.recommendationUnavailable, true);
const unavailableCard = decisionCard(unavailable, copy);
assert.ok(unavailableCard);
assert.deepEqual(
  unavailableCard.options.map((option) => option.action),
  ["accept", "reject"],
);
assert.equal(unavailableCard.manualOpen, true);
assert.match(unavailableCard.detail, /OSS Agent/);
assert.equal(decisionPayload(unavailableCard, "accept", copy).action, "accept");

// An information proposal has no counter action; its answer goes through the free-text box.
const unavailableInfo = createState();
applyFrame(unavailableInfo, { type: "run_started", runId: "r8", scenario: "information", mode: "confirm" });
applyFrame(unavailableInfo, {
  type: "decision_requested",
  round: 1,
  maxRounds: 3,
  proposal: { kind: "information", round: 1 },
  recommendationUnavailable: true,
  requestedAt: new Date().toISOString(),
});
const unavailableInfoCard = decisionCard(unavailableInfo, copy);
assert.ok(unavailableInfoCard);
assert.deepEqual(
  unavailableInfoCard.options.map((option) => option.action),
  ["reject"],
);
assert.equal(unavailableInfoCard.manualOpen, true);
assert.ok(!unavailableInfoCard.options.some((option) => option.action === "counter"));
assert.ok(!unavailableInfoCard.options.some((option) => option.action === "accept"));
assert.ok(copy.decision.manualExample);
assert.ok(copy.decision.manualUseExample);

// The vendor question drives the card text and the manual example: asking for a region must not
// suggest a throughput answer. With no recommendation only the reject escape action remains.
const regionQuestion = createState();
applyFrame(regionQuestion, {
  type: "run_started",
  runId: "r-region-question",
  scenario: "information",
  mode: "confirm",
});
applyFrame(regionQuestion, {
  type: "decision_requested",
  round: 1,
  maxRounds: 3,
  proposal: {
    kind: "information",
    round: 1,
    content: "Energy saving region is not recognized. Please provide the correct region name.",
  },
  missingItems: ["Region"],
  recommendationUnavailable: true,
  requestedAt: new Date().toISOString(),
});
assert.deepEqual(regionQuestion.decision.missingItems, ["Region"]);
const regionQuestionCard = decisionCard(regionQuestion, copy);
assert.ok(regionQuestionCard);
assert.equal(regionQuestionCard.manualOpen, true);
assert.deepEqual(
  regionQuestionCard.options.map((option) => option.action),
  ["reject"],
);
assert.equal(regionQuestionCard.text, copy.decision.askedItems.replace("{items}", "Region"));
assert.equal(regionQuestionCard.manualExample, copy.decision.manualExamples.region);
assert.notEqual(regionQuestionCard.manualExample, copy.decision.manualExample);

// An information proposal that does carry a recommendation offers confirm and reject only: the
// "accept RAN proposal" shortcut is meaningless when the NMS asked a question.
const recommendedInfo = createState();
applyFrame(recommendedInfo, {
  type: "run_started",
  runId: "r-recommended-info",
  scenario: "information",
  mode: "confirm",
});
applyFrame(recommendedInfo, {
  type: "decision_requested",
  round: 1,
  maxRounds: 3,
  proposal: {
    kind: "information",
    round: 1,
    content: "Energy saving region is not recognized. Please provide the correct region name.",
  },
  missingItems: ["Region"],
  recommendation: { action: "accept", rationale: "use Region A", filled_params: { region: "Region A" } },
  requestedAt: new Date().toISOString(),
});
const recommendedInfoCard = decisionCard(recommendedInfo, copy);
assert.ok(recommendedInfoCard);
assert.deepEqual(
  recommendedInfoCard.options.map((option) => option.action),
  ["confirm", "reject"],
);
assert.equal(recommendedInfoCard.options[0].label, copy.decision.confirmInformation);
assert.equal(recommendedInfoCard.manualExample, copy.decision.manualExamples.region);

// A recommendation to accept still leaves a counter path and keeps the manual area collapsed.
const acceptDecision = createState();
applyFrame(acceptDecision, {
  type: "run_started",
  runId: "r-accept",
  scenario: "target",
  mode: "confirm",
});
applyFrame(acceptDecision, {
  type: "decision_requested",
  round: 1,
  maxRounds: 3,
  proposal: { kind: "target", round: 1 },
  recommendation: { action: "accept", rationale: "feasible", filled_params: {} },
  requestedAt: new Date().toISOString(),
});
const acceptCard = decisionCard(acceptDecision, copy);
assert.ok(acceptCard);
assert.deepEqual(
  acceptCard.options.map((option) => option.action),
  ["confirm", "counter", "reject"],
);
assert.equal(acceptCard.options[0].label, copy.decision.confirmAccept);
assert.equal(acceptCard.manualOpen, false);
assert.ok(acceptCard.options.length <= 4);

// A recommendation to reject does not duplicate the reject action.
const rejectDecision = createState();
applyFrame(rejectDecision, {
  type: "run_started",
  runId: "r-reject",
  scenario: "target",
  mode: "confirm",
});
applyFrame(rejectDecision, {
  type: "decision_requested",
  round: 1,
  maxRounds: 3,
  proposal: { kind: "target", round: 1 },
  recommendation: { action: "reject", rationale: "not available" },
  requestedAt: new Date().toISOString(),
});
const rejectCard = decisionCard(rejectDecision, copy);
assert.ok(rejectCard);
assert.deepEqual(
  rejectCard.options.map((option) => option.action),
  ["confirm", "accept", "counter"],
);
assert.equal(rejectCard.options[0].label, copy.decision.confirmReject);
assert.equal(rejectCard.manualOpen, false);
assert.ok(rejectCard.options.length <= 4);

applyFrame(confirming, decision(2, "counter", 1));
assert.equal(pendingHuman(confirming), null);

// --- Unknown area: choose one of the served areas -----------------------
const areaDecision = createState();
applyFrame(areaDecision, { type: "run_started", runId: "r-area", scenario: "unknown_area", mode: "manual" });
applyFrame(areaDecision, {
  type: "decision_requested",
  round: 1,
  maxRounds: 3,
  proposal: {
    kind: "information",
    content:
      '## Information Negotiation\n\n## Required Information Items\n' +
      '1. region: The requested area "Region Z" is not recognized by the RAN. ' +
      "Served areas: Region A; Region B.",
    options: {
      field: "region",
      requested: "Region Z",
      keep: ["guaranteedThroughputTarget"],
      options: [
        {
          value: "Region A",
          labels: { "en-US": "Region A", "zh-CN": "区域 A" },
          score: 0,
          recommended: false,
        },
        {
          value: "Region B",
          labels: { "en-US": "Region B", "zh-CN": "区域 B" },
          score: 0,
          recommended: false,
        },
      ],
    },
  },
  timeoutSeconds: 300,
  requestedAt: new Date().toISOString(),
});
const areaCard = decisionCard(areaDecision, copy);
assert.ok(areaCard);
assert.equal(areaCard.regionOptions.length, 2);
assert.equal(areaCard.regionOptions[0].label, "区域 A");
assert.ok(areaCard.text.includes("Region Z"));
assert.deepEqual(decisionPayload(areaCard, "accept", copy, "", "区域 B").filled_params, {
  region: "区域 B",
});
assert.equal(decisionPayload(areaCard, "reject", copy, "", "区域 B").filled_params, undefined);

// --- Approval 1: intent review ------------------------------------------

const intentReview = createState();
applyFrame(intentReview, { type: "run_started", runId: "r9", scenario: "night", mode: "confirm" });
applyFrame(intentReview, {
  type: "intent_review_requested",
  promptText: "## Operation Type\nCreate",
  timeoutSeconds: 300,
  requestedAt: new Date().toISOString(),
});
assert.equal(pendingHuman(intentReview), "intent");
const intentReviewCard = intentCard(intentReview, copy);
assert.ok(intentReviewCard);
assert.ok(intentReviewCard.options.some((option) => option.action === "approve"));
intentReview.intentReview.resolved = true;
assert.equal(pendingHuman(intentReview), null);
applyFrame(intentReview, finished("intent_not_confirmed"));
assert.equal(stageOf(intentReview), "notSent");

// --- Pre-send Task-T validation ---------------------------------------
const blocked = createState();
applyFrame(blocked, { type: "run_started", runId: "r-val", scenario: "night" });
applyFrame(blocked, {
  type: "trace",
  event: {
    seq: 1,
    kind: "task_validation",
    phase: "task_prompt",
    title: "Task-T validation blocked",
    detail: {
      verdict: "blocked",
      blocking: true,
      errors: [
        {
          slotName: "guaranteedThroughputTarget",
          code: "content.format_error",
          message: "bad target",
        },
      ],
      negotiationKind: null,
      promptText: "## Task Object\nRegion A",
    },
  },
});
applyFrame(blocked, finished("input_invalid"));
assert.equal(stageOf(blocked), "notSent");
assert.equal(blocked.validation.verdict, "blocked");
const blockedCard = validationCard(blocked, copy);
assert.ok(blockedCard);
assert.equal(blockedCard.type, "validation");
assert.equal(blockedCard.validation.errors.length, 1);
assert.equal(blockedCard.options[0].action, "retry");

// A validator failure keeps its real reason and the attempt count, so the UI can say "the
// validation model returned an invalid response" instead of "Operation Type is empty".
const validatorFailed = createState();
applyFrame(validatorFailed, { type: "run_started", runId: "r-val-fail", scenario: "night" });
applyFrame(validatorFailed, {
  type: "trace",
  event: {
    seq: 1,
    kind: "task_validation",
    phase: "task_prompt",
    detail: {
      verdict: "blocked",
      blocking: true,
      reason: "llm.response_invalid",
      attempts: 2,
      errors: [
        { slotName: "_llm", code: "llm.response_invalid", message: "invalid json" },
        { slotName: "operationType", code: "content.param_missing", message: "'Operation Type' is empty" },
      ],
    },
  },
});
applyFrame(validatorFailed, finished("input_invalid"));
assert.equal(validatorFailed.validation.reason, "llm.response_invalid");
assert.equal(validatorFailed.validation.attempts, 2);
const validatorFailedCard = validationCard(validatorFailed, copy);
assert.ok(validatorFailedCard);
assert.equal(validatorFailedCard.validation.reason, "llm.response_invalid");
assert.equal(validatorFailedCard.validation.attempts, 2);

const negotiable = createState();
applyFrame(negotiable, { type: "run_started", runId: "r-neg", scenario: "information" });
applyFrame(negotiable, {
  type: "intent_review_requested",
  promptText: "## Operation Type\nCreate",
  timeoutSeconds: 300,
  requestedAt: new Date().toISOString(),
});
applyFrame(negotiable, {
  type: "trace",
  event: {
    seq: 2,
    kind: "task_validation",
    phase: "task_prompt",
    detail: {
      verdict: "negotiable",
      blocking: false,
      errors: [{ slotName: "guaranteedThroughputTarget", code: "content.param_missing" }],
      negotiationKind: "information",
    },
  },
});
const negotiableCard = intentCard(negotiable, copy);
assert.ok(negotiableCard);
assert.equal(negotiableCard.validation.verdict, "negotiable");

// --- Approval gate ------------------------------------------------------

const approval = createState();
applyFrame(approval, {
  type: "run_started",
  runId: "r7",
  scenario: "information",
  mode: "confirm",
  approvalGate: true,
});
applyFrame(approval, status(1, "TASK_STATE_SUBMITTED"));
applyFrame(approval, status(2, "TASK_STATE_WORKING"));
applyFrame(approval, status(3, "TASK_STATE_INPUT_REQUIRED"));
assert.equal(approval.approvalState, "waiting");
assert.equal(pendingHuman(approval), "approval");
const approvalModel = approvalCard(approval, copy);
assert.ok(approvalModel);
// Review is one step: approve or reject, with the commands visible immediately.
assert.deepEqual(
  approvalModel.options.map((option) => option.action),
  ["approve", "reject"],
);
assert.ok(approvalModel.mml.text.includes("SET ENERGYSAVING"));

applyFrame(approval, {
  type: "trace",
  event: {
    seq: 4,
    kind: "approval",
    title: "Approval requested",
    startedAt: new Date().toISOString(),
    detail: {
      cellId: "cell-001",
      window: "00:00~06:00",
      rate: "30%",
      approvalState: "requested",
      timeoutSeconds: 120,
    },
  },
});
assert.equal(approval.approval.cellId, "cell-001");
assert.equal(approvalCard(approval, copy).text.includes("cell-001"), true);

// The status text names the plan even when the approval span has not arrived yet.
const namedApproval = createState();
applyFrame(namedApproval, {
  type: "run_started",
  runId: "r70",
  scenario: "night",
  mode: "confirm",
  approvalGate: true,
});
applyFrame(
  namedApproval,
  event({
    seq: 1,
    entryType: "event",
    kind: "status",
    phase: "execution",
    state: "TASK_STATE_INPUT_REQUIRED",
    summary:
      "Waiting for the engineer's approval before applying the plan: cell-002, 23:00~24:00, 20%.",
  }),
);
assert.equal(namedApproval.approval.cellId, "cell-002");
assert.equal(namedApproval.approval.window, "23:00~24:00");
assert.equal(approvalCard(namedApproval, copy).mml.text.includes("cell-002"), true);
assert.equal(approvalCard(namedApproval, copy).options.length, 2);

applyFrame(approval, status(5, "TASK_STATE_WORKING"));
assert.equal(approval.approvalState, "approved");
assert.equal(approval.mmlSent, true);
assert.equal(pendingHuman(approval), null);

// A late approval-requested span (server spans arrive in batches) must not reopen the gate.
applyFrame(approval, {
  type: "trace",
  event: {
    seq: 6,
    kind: "approval",
    title: "Approval requested",
    detail: { cellId: "cell-001", approvalState: "requested", timeoutSeconds: 120 },
  },
});
assert.equal(approval.approvalState, "approved");
assert.equal(approvalCard(approval, copy), null);

// A gate still marked waiting when the run ends must not keep the card on screen.
const timedOut = createState();
applyFrame(timedOut, {
  type: "run_started",
  runId: "r71",
  scenario: "night",
  mode: "confirm",
  approvalGate: true,
});
applyFrame(timedOut, status(1, "TASK_STATE_INPUT_REQUIRED"));
assert.ok(approvalCard(timedOut, copy));
applyFrame(timedOut, finished("task_rejected", null, "approval timeout"));
assert.equal(timedOut.approvalState, "rejected");
assert.equal(approvalCard(timedOut, copy), null);

// A bare INPUT_REQUIRED without an approval gate is evidence, not an approval: a --nms run has no
// local /approvals route, so the page must not hold on a card nobody can submit.
const noGate = createState();
applyFrame(noGate, { type: "run_started", runId: "r72", scenario: "night", mode: "confirm" });
applyFrame(noGate, status(1, "TASK_STATE_INPUT_REQUIRED", { state: "TASK_STATE_INPUT_REQUIRED" }));
assert.equal(noGate.approvalState, null);
assert.equal(pendingHuman(noGate), null);
assert.equal(approvalCard(noGate, copy), null);
assert.equal(
  sceneFor(
    { type: "event", event: { kind: "status", state: "TASK_STATE_INPUT_REQUIRED" } },
    noGate,
  ).hold,
  false,
);

// The vendor hands a Negotiation-T proposal back inside INPUT_REQUIRED. The backend marks the
// entry; the page must treat it as a negotiation turn, never as the MIE review.
const negotiationStatus = createState();
applyFrame(negotiationStatus, {
  type: "run_started",
  runId: "r73",
  scenario: "feasibility",
  mode: "confirm",
});
applyFrame(
  negotiationStatus,
  status(1, "The requested rate conflicts with the outage window.", {
    state: "TASK_STATE_INPUT_REQUIRED",
    negotiation: true,
    round: 1,
    maxRounds: 5,
    negotiationType: "feasibility",
  }),
);
assert.equal(negotiationStatus.approvalState, null);
assert.equal(negotiationStatus.approval, null);
assert.equal(negotiationStatus.negotiation.at(-1).action, "propose");
assert.equal(negotiationStatus.negotiation.at(-1).round, 1);
assert.equal(negotiationStatus.negotiation.at(-1).maxRounds, 5);
assert.equal(negotiationTurn(negotiationStatus.trace.at(-1).entry).performative, "PROPOSE");
assert.equal(pendingHuman(negotiationStatus), null);
assert.equal(approvalCard(negotiationStatus, copy), null);
assert.equal(sceneFor({ type: "event", event: negotiationStatus.trace.at(-1).entry }, negotiationStatus).key, "propose");
assert.equal(sceneFor({ type: "event", event: negotiationStatus.trace.at(-1).entry }, negotiationStatus).hold, false);

// A vendor status that carries the extension but no round/template is still a negotiation turn.
const bareNegotiation = createState();
applyFrame(bareNegotiation, { type: "run_started", runId: "r74", scenario: "feasibility" });
applyFrame(
  bareNegotiation,
  status(1, "Please adjust the request.", {
    state: "TASK_STATE_INPUT_REQUIRED",
    negotiation: true,
  }),
);
assert.equal(bareNegotiation.approvalState, null);
assert.equal(bareNegotiation.negotiation.at(-1).action, "propose");
assert.equal(negotiationTurn(bareNegotiation.trace.at(-1).entry).performative, "PROPOSE");

// Audit metadata on a completed Task is not a proposal hand-back.
assert.equal(
  negotiationTurn({
    kind: "status",
    state: "TASK_STATE_COMPLETED",
    negotiation: true,
    round: 1,
  }),
  null,
);

// Confirm mode opens the decision surface before the recommendation. The operator can answer;
// the later recommendation must not reopen the card.
const earlyDecision = createState();
applyFrame(earlyDecision, {
  type: "run_started",
  runId: "r75",
  scenario: "feasibility",
  mode: "confirm",
});
applyFrame(earlyDecision, {
  type: "decision_pending",
  mode: "confirm",
  round: 1,
  maxRounds: 5,
  performative: "PROPOSE",
  recommendationTimeoutSeconds: 120,
  requestedAt: "2026-11-09T02:29:45.000Z",
  proposal: { kind: "feasibility", content: "10 Mbps and a 10 h outage conflict." },
});
assert.equal(earlyDecision.decision, null);
assert.equal(earlyDecision.decisionPending.round, 1);
assert.equal(earlyDecision.decisionPending.recommendationTimeoutSeconds, 120);
assert.equal(pendingHuman(earlyDecision), "decision");
assert.equal(decisionCard(earlyDecision, copy), null);
const earlyCard = pendingDecisionCard(earlyDecision, copy);
assert.ok(earlyCard);
assert.equal(earlyCard.pendingRecommendation, true);
assert.equal(earlyCard.manualOpen, true);
assert.equal(earlyCard.recommendation, null);
assert.equal(earlyCard.options.some((option) => option.action === "accept"), true);
assert.equal(
  sceneFor({ type: "decision_pending", round: 1 }, earlyDecision).key,
  "decision-pending",
);
assert.equal(sceneFor({ type: "decision_pending", round: 1 }, earlyDecision).hold, true);

// The recommendation for the same round replaces the early card.
applyFrame(earlyDecision, {
  type: "decision_requested",
  mode: "confirm",
  round: 1,
  maxRounds: 5,
  performative: "PROPOSE",
  requestedAt: "2026-11-09T02:31:00.000Z",
  proposal: { kind: "feasibility", content: "10 Mbps and a 10 h outage conflict." },
  recommendation: { action: "accept", rationale: "reduce to 2 Mbps", filled_params: {} },
});
assert.equal(earlyDecision.decisionPending, null);
assert.ok(decisionCard(earlyDecision, copy));
assert.equal(decisionCard(earlyDecision, copy).hasRecommendation, true);

// A resolved decision is not reopened by a late/replayed recommendation.
earlyDecision.decision.resolved = true;
applyFrame(earlyDecision, {
  type: "decision_requested",
  mode: "confirm",
  round: 1,
  maxRounds: 5,
  performative: "PROPOSE",
  requestedAt: "2026-11-09T02:32:00.000Z",
  proposal: { kind: "feasibility" },
  recommendation: { action: "reject", rationale: "late" },
});
assert.equal(earlyDecision.decision.resolved, true);
assert.equal(decisionCard(earlyDecision, copy), null);

const notApplied = createState();
applyFrame(notApplied, { type: "run_started", runId: "r8", scenario: "information", mode: "confirm" });
applyFrame(notApplied, status(1, "TASK_STATE_SUBMITTED"));
applyFrame(notApplied, {
  type: "trace",
  event: {
    seq: 2,
    kind: "approval",
    title: "Approval requested",
    detail: { cellId: "cell-001", approvalState: "requested" },
  },
});
applyFrame(notApplied, status(3, "TASK_STATE_REJECTED"));
applyFrame(notApplied, finished("task_rejected"));
assert.equal(stageOf(notApplied), "notApplied");

// --- Scene mapping ------------------------------------------------------

const sceneState = createState();
applyFrame(sceneState, {
  type: "run_started",
  runId: "s1",
  scenario: "target",
  mode: "confirm",
  approvalGate: true,
});
assert.equal(sceneFor({ type: "decision_requested", round: 1 }, sceneState).hold, true);
assert.equal(
  sceneFor({ type: "event", event: { kind: "status", summary: "TASK_STATE_INPUT_REQUIRED" } }, sceneState)
    .hold,
  true,
);
assert.equal(
  sceneFor(
    { type: "trace", event: { kind: "task_prompt_rendered", title: "Task-T prompt rendered" } },
    sceneState,
  ).step,
  1,
);
assert.equal(
  sceneFor({ type: "event", event: { kind: "artifact", phase: "report" } }, sceneState).step,
  8,
);
assert.equal(
  sceneFor(
    { type: "event", event: { kind: "status", summary: "Data collection started: ..." } },
    sceneState,
  ).step,
  6,
);
// The reference demo's post-Task-T ladder maps to three distinct beats.
assert.equal(
  sceneFor(
    status(31, "Strategy recommendation is being executed; no optimization is applied yet.", {
      state: "TASK_STATE_WORKING",
    }),
    sceneState,
  ).key,
  "strategy",
);
assert.equal(
  sceneFor(
    status(32, "Strategy recommendation and parameter setting are complete.", {
      state: "TASK_STATE_WORKING",
    }),
    sceneState,
  ).key,
  "strategy-ready",
);
assert.equal(
  sceneFor(
    status(33, "The energy-saving task has started; the RAN Agent is now running it.", {
      state: "TASK_STATE_WORKING",
    }),
    sceneState,
  ).key,
  "task-started",
);
assert.equal(sceneFor({ type: "finished", finalState: "task_completed" }, sceneState).key, "finish");

// Server spans are evidence, not story beats: a late batch must not replay a step.
assert.equal(
  sceneFor(
    {
      type: "trace",
      event: {
        kind: "tool",
        side: "server",
        title: "apply_energy_saving_plan",
        detail: { toolName: "apply_energy_saving_plan" },
      },
    },
    sceneState,
  ),
  null,
);

// Artifacts are classified by the tool that produced them, never by their position.
const artifactState = createState();
applyFrame(artifactState, { type: "run_started", runId: "s2", scenario: "night", mode: "confirm" });
assert.equal(
  sceneFor(artifact(20, "execution", "get_cell_energy_profile"), artifactState).key,
  "collect-done",
);
assert.equal(
  sceneFor(artifact(21, "execution", "get_cell_energy_profile"), artifactState).key,
  "collect-done",
);
assert.equal(
  sceneFor(artifact(22, "execution", "evaluate_energy_saving_plan"), artifactState).key,
  "approval-ready",
);
assert.equal(
  sceneFor(artifact(23, "execution", "evaluate_energy_saving_plan"), artifactState).key,
  "approval-ready",
);
// While the MIE review is open, an apply artifact is evidence only.
artifactState.approvalState = "waiting";
assert.equal(sceneFor(artifact(24, "execution", "apply_energy_saving_plan"), artifactState).key, "trace");
artifactState.approvalState = "approved";
assert.equal(sceneFor(artifact(25, "execution", "apply_energy_saving_plan"), artifactState).key, "apply");
// The apply result is a status notification now, so it drives the execution beat itself; the
// report-ready status stays evidence and the report artifact drives the report beat.
assert.equal(
  sceneFor(
    status(
      29,
      "Region: Region A; Task: Multi-RAT Intelligent Network Energy Saving; Progress: plan plan-cell-001-001 was applied to cell cell-001 (Region-A-North) for 00:00~06:00 at 30% energy saving.",
      { state: "TASK_STATE_WORKING" },
    ),
    artifactState,
  ).key,
  "apply",
);
assert.equal(
  sceneFor(
    status(
      30,
      "Region: Region A; Task: Multi-RAT Intelligent Network Energy Saving; Progress: the energy-saving report is ready. Plan plan-cell-001-001 applied.",
      { state: "TASK_STATE_WORKING" },
    ),
    artifactState,
  ).key,
  "trace",
);
// An unknown artifact is never mistaken for an applied plan.
assert.equal(sceneFor(artifact(26, "execution"), artifactState).key, "trace");

// The apply artifact is the execution report once MIE has dispatched the MML; only a run
// without that dispatch shows it as the apply beat.
artifactState.mmlSent = true;
assert.equal(
  sceneFor(artifact(27, "execution", "apply_energy_saving_plan"), artifactState).key,
  "execute",
);
artifactState.mmlSent = false;
assert.equal(
  sceneFor(artifact(28, "execution", "apply_energy_saving_plan"), artifactState).key,
  "apply",
);

const executedState = createState();
applyFrame(executedState, { type: "run_started", runId: "s3", scenario: "night", mode: "auto" });
applyFrame(executedState, artifact(1, "execution", "apply_energy_saving_plan"));
assert.equal(executedState.mmlSent, true);
assert.equal(executedState.executed, true);

// --- Trace: demo day labels, direction and duplicate collapse --------------

assert.equal(traceStageKey({ kind: "status", summary: "Data collection started: ..." }), "collection");
assert.equal(
  traceStageKey({ kind: "status", state: "TASK_STATE_INPUT_REQUIRED", summary: "Waiting ..." }),
  "mml",
);
assert.equal(
  traceStageKey({ kind: "artifact", phase: "report", name: "energy saving intent report" }),
  "report",
);
assert.equal(
  traceStageKey({ kind: "tool", title: "evaluate_energy_saving_plan", side: "server" }),
  "mml",
);
assert.equal(traceDirection({ direction: "client->server" }), "OSS → RAN");
assert.equal(traceDirection({ direction: "server->client" }), "RAN → OSS");
assert.equal(traceDirection({ direction: "poll" }), "Local poll");
assert.equal(traceDirection({ origin: "poll", direction: "poll" }), "Local poll");
assert.equal(traceDirection({ kind: "approval", side: "server", actor: "human" }), "MIE ⇄ Engineer");
assert.equal(traceDirection({ detail: { hop: "mie" }, direction: "server->client" }), "MIE ⇄ RAN");

const orderedTrace = createState();
pushTrace(orderedTrace, { title: "later", order: 200 });
pushTrace(orderedTrace, { title: "earlier", order: 100 });
assert.deepEqual(orderedTrace.trace.map((row) => row.title), ["earlier", "later"]);

const dedupedTrace = createState();
pushTrace(dedupedTrace, {
  title: "Approval 2 · MIE",
  entry: {
    entryType: "event",
    kind: "status",
    state: "TASK_STATE_INPUT_REQUIRED",
    summary: "Waiting for the engineer's approval before applying the plan.",
  },
});
pushTrace(dedupedTrace, {
  title: "Approval requested",
  entry: {
    entryType: "trace",
    kind: "approval",
    title: "Approval requested",
    detail: { approvalState: "requested" },
  },
});
assert.equal(dedupedTrace.trace.length, 1);
assert.equal(dedupedTrace.trace[0].title, "Approval 2 · MIE");

// Status rows use a short title plus the task state as the right-hand chip, like the reference
// demo's title / from-to / state row.
const titledTrace = createState();
applyFrame(
  titledTrace,
  status(41, "Strategy recommendation is being executed; no optimization is applied yet.", {
    state: "TASK_STATE_WORKING",
  }),
);
assert.equal(titledTrace.trace[0].title, "Strategy generation started");
assert.equal(titledTrace.trace[0].chip, "WORKING");
assert.equal(titledTrace.trace[0].stage, "strategy");
applyFrame(titledTrace, status(40, "", { state: "TASK_STATE_SUBMITTED" }));
assert.equal(titledTrace.trace[1].title, "Task created");
assert.equal(titledTrace.trace[1].chip, "SUBMITTED");
applyFrame(
  titledTrace,
  event({
    seq: 42,
    entryType: "event",
    kind: "artifact",
    phase: "execution",
    summary: "tool result",
    detail: { toolName: "apply_energy_saving_plan" },
  }),
);
const artifactRow = titledTrace.trace.find((row) => row.title === "Suggestions executed");
assert.ok(artifactRow);
assert.equal(artifactRow.chip, "artifact");

// --- Player: a hold waits for the engineer, then resumes ----------------

const applied = [];
const player = createPlayer({
  onApply: (frame) => applied.push(frame.id),
  onRewind: () => {},
  onChange: () => {},
  onScene: () => {},
});
player.enqueue({ key: "decision", step: 3, duration: 0, hold: true, frame: { id: 1 } });
assert.deepEqual(applied, [1]);
assert.equal(player.status().hold, true);
player.enqueue({ key: "propose", step: 3, duration: 0, hold: false, frame: { id: 2 } });
assert.deepEqual(applied, [1]);
player.release();
// The release waits one short beat so the next scene does not snap in under the click.
assert.deepEqual(applied, [1]);
await new Promise((resolve) => setTimeout(resolve, 600));
assert.deepEqual(applied, [1, 2]);
assert.equal(player.status().hold, false);

const stepping = [];
const stepper = createPlayer({
  onApply: (frame) => stepping.push(frame.id),
  onRewind: () => {},
  onChange: () => {},
  onScene: () => {},
});
stepper.setMode("step");
stepper.enqueue({ key: "request", step: 0, duration: 0, frame: { id: 1 } });
assert.deepEqual(stepping, [1]);
assert.equal(stepper.status().waitingNext, true);
stepper.enqueue({ key: "understand", step: 1, duration: 0, frame: { id: 2 } });
assert.deepEqual(stepping, [1]);
stepper.next();
assert.deepEqual(stepping, [1, 2]);

// --- Intent parsing -----------------------------------------------------

const englishTaskT = `## Operation Type
Create

## Task Object
The area is Region A.

## Task Context
Time-segment-based guaranteed throughput target: {00:00~07:00,2Mbps}, {07:00~17:30,10Mbps}, {17:30~23:00,20Mbps}, {23:00~24:00,2Mbps}.`;
const english = parseIntent(englishTaskT);
assert.equal(english.operation, "create");
assert.equal(english.area, "Region A");
assert.equal(english.slots.length, 4);
assert.equal(english.overlap, null);
const englishSentence = plainSentence(english, "en-US");
assert.ok(englishSentence.includes("Perform RAN energy saving in Region A"));
assert.ok(englishSentence.includes("Guarantee 2 Mbps from 00:00 to 07:00"));

const overlapTaskT = englishTaskT.replace(
  "{23:00~24:00,2Mbps}",
  "{23:00~24:00,2Mbps}, {20:00~00:00,10Mbps}",
);
assert.deepEqual(parseIntent(overlapTaskT).overlap, { start: 1200, end: 1440 });

const chineseTaskT = `## 操作类型(Operation Type)

创建

## 任务对象(Task Object)

区域为区域 A。

## 任务上下文(Task Context)

分时段保障速率目标：{00:00~07:00,2Mbps}、{07:00~17:30,10Mbps}、{17:30~23:00,20Mbps}、{23:00~24:00,2Mbps}。`;
const chinese = parseIntent(chineseTaskT);
assert.equal(chinese.operation, "create");
assert.equal(chinese.area, "区域 A");
assert.equal(chinese.slots.length, 4);

const feasibility = parseIntent(
  `## 操作类型(Operation Type)

创建

## 任务上下文(Task Context)

站点列表1为[站点名称1, 站点名称2]；分时段保障速率目标为 {00:00~06:00,2Mbps}、{06:00~18:00,10Mbps}；背景事件为在00:00~18:00停电。`,
);
assert.deepEqual(feasibility.outage, { start: 0, end: 1080 });
assert.deepEqual(feasibility.sites, ["站点名称1", "站点名称2"]);

const outageTaskT = `## Operation Type

Create

## Task Object

The area is Region A. Site list 1: ["Site 1", "Site 2"].

## Task Context

Time-segment-based guaranteed throughput target: {00:00~03:00,10Mbps}, {03:00~05:00,2Mbps}, {05:00~24:00,10Mbps}. Background event: power outage from 03:00 to 05:00.`;
const outage = parseIntent(outageTaskT);
assert.equal(outage.area, "Region A");
assert.deepEqual(outage.sites, ["Site 1", "Site 2"]);
assert.deepEqual(outage.outage, { start: 180, end: 300 });
assert.equal(outage.slots.length, 3);

// The presentation shows a concise engineer request, not the Task-T wire text. The OSS still
// parses it into its own structured intent before the Task-T exists.
const conciseNight = parseIntent(
  "Start energy saving in Region A with these guaranteed throughputs: " +
    "00:00–17:30 at 10 Mbps, 17:30–23:00 at 20 Mbps, 23:00–24:00 at 10 Mbps.",
);
assert.equal(conciseNight.operation, "create");
assert.equal(conciseNight.area, "Region A");
assert.equal(conciseNight.slots.length, 3);
assert.deepEqual(conciseNight.slots[1], { start: 1050, end: 1380, rate: 20 });

const conciseOutage = parseIntent(
  "Start energy saving for Site 1 and Site 2 in Region A. During tomorrow's " +
    "03:00–05:00 outage keep 2 Mbps; otherwise use 10 Mbps: 00:00–03:00 at 10 Mbps, " +
    "03:00–05:00 at 2 Mbps, 05:00–24:00 at 10 Mbps.",
);
assert.deepEqual(conciseOutage.sites, ["Site 1", "Site 2"]);
assert.deepEqual(conciseOutage.outage, { start: 180, end: 300 });
assert.equal(conciseOutage.slots.length, 3);

// --- On the wire: the structured payloads --------------------------------

function wireText(model) {
  return model.nodes.map((node) => String(node.textContent ?? "")).join("");
}

const structuredTaskT = `## Operation Type

Create

## Task Object

The area is Region A.

## Task Context

Time-segment-based guaranteed throughput target: {00:00~17:30,10Mbps}, {17:30~23:00,20Mbps}, {23:00~23:59,10Mbps}.`;
const rawRequest = "在区域 A 开启节能，保障速率为 00:00–06:00 30Mbps。";

const taskTSentModel = buildWireModel(
  {
    kind: "message_sent",
    spanId: "a".repeat(16),
    detail: {
      performative: "TASK_T",
      summary: "create ran energy saving task",
      extensionUri: "urn:task-t/v1",
      templateUri: "urn:template",
      taskT: structuredTaskT,
    },
  },
  { runId: "run-wire", intentText: rawRequest, taskTPrompt: structuredTaskT },
  "t",
  copy,
);
const taskTSentText = wireText(taskTSentModel);
assert.ok(taskTSentText.includes("The area is Region A."));
assert.ok(!taskTSentText.includes(rawRequest));

// The plain-A2A comparison is derived from the same structured Task-T, not from the request.
const plainTaskTModel = buildWireModel(
  {
    kind: "message_sent",
    spanId: "c".repeat(16),
    detail: {
      performative: "TASK_T",
      summary: "create ran energy saving task",
      extensionUri: "urn:task-t/v1",
      templateUri: "urn:template",
      taskT: structuredTaskT,
    },
  },
  { runId: "run-wire", intentText: rawRequest, taskTPrompt: structuredTaskT },
  "a",
  copy,
);
const plainTaskTText = wireText(plainTaskTModel);
assert.ok(plainTaskTText.includes("保障速率"));
assert.ok(!plainTaskTText.includes("## Operation Type"));

const acceptModel = buildWireModel(
  {
    kind: "message_sent",
    spanId: "b".repeat(16),
    detail: {
      performative: "ACCEPT",
      summary: "create ran energy saving task",
      extensionUri: "urn:task-t/v1",
      negotiationUri: "urn:negotiation-t/v1",
      taskT: "Refilled Task-T\n## Task Object",
      negotiationT: "Accept. target: 00:00~06:00.",
      templateUri: "urn:accept-template",
    },
  },
  { runId: "run-wire", intentText: rawRequest },
  "t",
  copy,
);
const acceptText = wireText(acceptModel);
assert.ok(acceptText.includes("urn:negotiation-t/v1"));
assert.ok(acceptText.includes("Accept. target: 00:00~06:00."));
assert.ok(acceptText.includes("Refilled Task-T"));
assert.ok(acceptText.includes("urn:accept-template"));

const creationModel = buildWireModel(
  {
    entryType: "event",
    kind: "status",
    state: "TASK_STATE_SUBMITTED",
    summary: "TASK_STATE_SUBMITTED",
    task: {
      id: "task-1",
      status: { state: "TASK_STATE_SUBMITTED" },
      history: [{ role: "ROLE_USER", metadata: { "urn:task-t/v1": structuredTaskT } }],
    },
  },
  { runId: "run-wire" },
  "t",
  copy,
);
const creationText = wireText(creationModel);
assert.ok(creationText.includes('"task"'));
assert.ok(creationText.includes("The area is Region A."));

const workingModel = buildWireModel(
  {
    entryType: "event",
    kind: "status",
    state: "TASK_STATE_WORKING",
    summary: "Region: Region A; Progress: strategy ready.",
  },
  { runId: "run-wire" },
  "t",
  copy,
);
const workingText = wireText(workingModel);
assert.ok(workingText.includes('"state": "TASK_STATE_WORKING"'));
assert.ok(workingText.includes("Region: Region A"));
assert.ok(!workingText.includes('"state": "Region'));

// A locally polled Task state is a trace row, never a fabricated wire statusUpdate.
const polledModel = buildWireModel(
  {
    entryType: "trace",
    kind: "task_poll",
    direction: "poll",
    title: "Polled task state: TASK_STATE_WORKING",
    summary: "Polled task state: TASK_STATE_WORKING",
    detail: { state: "TASK_STATE_WORKING", origin: "poll" },
  },
  { runId: "run-wire" },
  "t",
  copy,
);
const polledText = wireText(polledModel);
assert.ok(polledText.includes("task_poll"));
assert.ok(!polledText.includes('"statusUpdate"'));

const proposeModel = buildWireModel(
  {
    entryType: "event",
    kind: "propose",
    content: "Please provide the guaranteed throughput target.",
    summary: "Please provide the target.",
    templateUri: "urn:information-propose",
    negotiationType: "information",
  },
  { runId: "run-wire" },
  "t",
  copy,
);
const proposeText = wireText(proposeModel);
assert.ok(proposeText.includes('"Negotiation-T": "Please provide the guaranteed throughput target."'));
assert.ok(proposeText.includes('"templateUri": "urn:information-propose"'));
assert.ok(!proposeText.includes('"Negotiation-T": "information"'));

const createdState = createState();
applyFrame(createdState, { type: "run_started", runId: "r-created", scenario: "night", mode: "auto" });
applyFrame(createdState, {
  type: "trace",
  event: { seq: 1, kind: "task_prompt_rendered", detail: { promptText: structuredTaskT } },
});
applyFrame(
  createdState,
  event({
    seq: 2,
    entryType: "event",
    kind: "status",
    state: "TASK_STATE_SUBMITTED",
    summary: "TASK_STATE_SUBMITTED",
  }),
);
const createdRow = createdState.trace.find((row) => row.title === "Task created");
assert.ok(createdRow);
assert.ok(createdRow.sub.includes("Region A"));
assert.ok(createdRow.sub.includes("00:00–17:30 10Mbps +2"));

// --- Trace rows are all viewable ----------------------------------------

assert.equal(rowKey({ entry: { spanId: "s1" } }, 0), "span:s1");
assert.equal(rowKey({ entry: { seq: 7 } }, 0), "seq:7");
assert.equal(rowKey({ title: "Decision requested" }, 12), "row:12");

const synthetic = entryOfRow({
  title: "Decision requested",
  sub: "round 1 · information",
  tag: "a2at",
});
assert.equal(synthetic.entryType, "trace");
assert.equal(synthetic.title, "Decision requested");
assert.equal(synthetic.summary, "round 1 · information");

const real = { kind: "message_sent", seq: 3 };
assert.equal(entryOfRow({ entry: real }), real);

// --- Plain A2A mode ------------------------------------------------------

const plainView = createState();
applyFrame(plainView, {
  type: "run_started",
  runId: "r6",
  scenario: "information",
  mode: "auto",
  protocol: "plain",
});
assert.equal(plainView.protocol, "plain");
applyFrame(
  plainView,
  event({
    seq: 1,
    entryType: "event",
    kind: "status",
    phase: "execution",
    state: "TASK_STATE_REJECTED",
    summary: "The request does not define the time-segmented guaranteed throughput target.",
  }),
);
applyFrame(plainView, finished("task_rejected"));
assert.equal(stageOf(plainView), "plainRejected");
const plainRecord = buildRecord(plainView);
assert.equal(plainRecord.protocol, "plain");
assert.equal(plainRecord.rounds, 0);
assert.equal(plainRecord.finalState, "task_rejected");
assert.match(plainRecord.detail, /time-segmented/);

const a2atView = createState();
applyFrame(a2atView, { type: "run_started", runId: "r7", scenario: "information", mode: "auto" });
applyFrame(a2atView, proposal(2, 1));
applyFrame(a2atView, decision(3, "accept", 1));
applyFrame(a2atView, finished("task_completed", { target_rate: 30 }));
const a2atRecord = buildRecord(a2atView);
assert.equal(a2atRecord.protocol, "a2at");
assert.equal(a2atRecord.rounds, 1);
assert.equal(a2atRecord.resolution, "accepted");

const comparison = compareRows(copy, a2atRecord, plainRecord);
assert.equal(comparison.length, 4);
assert.equal(comparison[0].label, copy.compare.intent);
assert.match(comparison[1].a2at, /1/);
assert.equal(comparison[1].plain, copy.compare.notApplicable);
assert.match(comparison[3].plain, /拒绝/);
assert.match(outcomeText(copy, a2atRecord), /30/);

// --- Pure A2A three-hop mode ---------------------------------------------

const pureCopy = copyFor("en-US");
const pureView = createState();
applyFrame(pureView, {
  type: "run_started",
  runId: "r8",
  scenario: "information",
  mode: "auto",
  protocol: "pure",
});
assert.equal(pureView.protocol, "pure");
assert.equal(negotiationExpected(pureView), true);
assert.equal(pendingHuman(pureView), null);

const receive = event({
  seq: 1,
  entryType: "event",
  kind: "status",
  phase: "execution",
  state: "TASK_STATE_WORKING",
  summary: "OSS Agent received the request and is forwarding it to the RAN Agent.",
  detail: { hop: "oss", pure: { hop: "oss", phase: "receive" } },
});
assert.equal(sceneFor(receive, pureView).key, "understand");
applyFrame(pureView, receive);

const ask = event({
  seq: 2,
  entryType: "event",
  kind: "status",
  phase: "negotiation",
  state: "TASK_STATE_WORKING",
  summary: "Please provide the guaranteed throughput target.",
  detail: {
    hop: "ran",
    pure: { kind: "negotiation", phase: "information", round: 1, maxRounds: 3 },
  },
});
assert.equal(sceneFor(ask, pureView).key, "propose");
applyFrame(pureView, ask);
assert.equal(pureView.negotiation.at(-1).side, "nms");
assert.equal(pureView.negotiation.at(-1).round, 1);

const answer = event({
  seq: 3,
  entryType: "event",
  kind: "status",
  phase: "negotiation",
  state: "TASK_STATE_WORKING",
  summary: "OSS Agent answered the RAN Agent: {00:00~06:00,10Mbps}.",
  detail: { hop: "oss", pure: { kind: "answer", phase: "answer", round: 1 } },
});
assert.equal(sceneFor(answer, pureView).key, "decision");
applyFrame(pureView, answer);
assert.equal(pureView.negotiation.at(-1).side, "oss");

const pureApproval = event({
  seq: 4,
  entryType: "event",
  kind: "status",
  phase: "approval",
  state: "TASK_STATE_WORKING",
  summary: "RAN Agent is waiting for the MIE Agent's approval.",
  detail: {
    hop: "ran->mie",
    pure: {
      kind: "approval",
      phase: "approval",
      plan: { cellId: "cell-001", window: "00:00~06:00", rate: "30%" },
    },
  },
});
const approvalScene = sceneFor(pureApproval, pureView);
assert.equal(approvalScene.key, "approval-pure");
assert.equal(approvalScene.hold, true);
applyFrame(pureView, pureApproval);
assert.equal(pureView.mieWaiting, true);
assert.equal(pendingHuman(pureView), "approval");
assert.equal(approvalCard(pureView, pureCopy), null);

pureView.mieApproval = {
  taskId: "approval-1",
  plan: { cellId: "cell-001", window: "00:00~06:00", rate: "30%" },
  timeoutSeconds: 240,
  requestedAt: new Date().toISOString(),
};
const mieCard = approvalCard(pureView, pureCopy);
assert.ok(mieCard);
assert.equal(mieCard.pure, true);
assert.equal(mieCard.taskId, "approval-1");

pureView.mieApproval = null;
pureView.mieResolved = { approved: true, reason: null };
assert.equal(pendingHuman(pureView), null);
assert.equal(approvalCard(pureView, pureCopy), null);
assert.equal(sceneFor(pureApproval, pureView).key, "trace");

// Pure statuses use their own phase vocabulary: the outer OSS lifecycle and RAN progress after
// the decision are evidence, not a rewind to the strategy step.
const submitted = event({
  seq: 41,
  entryType: "event",
  kind: "status",
  state: "TASK_STATE_SUBMITTED",
  summary: "TASK_STATE_SUBMITTED",
});
assert.equal(sceneFor(submitted, pureView).key, "trace");
const working = event({
  seq: 42,
  entryType: "event",
  kind: "status",
  state: "TASK_STATE_WORKING",
  summary: "MIE approved. Applying the plan.",
  detail: { hop: "ran", pure: { hop: "ran" } },
});
assert.equal(sceneFor(working, pureView).key, "trace");
const planning = event({
  seq: 43,
  entryType: "event",
  kind: "status",
  state: "TASK_STATE_WORKING",
  summary: "RAN Agent is working.",
  detail: { hop: "ran", pure: { hop: "ran" } },
});
assert.equal(sceneFor(planning, pureView).key, "strategy");
const taskDone = event({
  seq: 44,
  entryType: "event",
  kind: "status",
  state: "TASK_STATE_WORKING",
  summary: "RAN energy-saving task completed.",
  detail: { hop: "ran", pure: { hop: "ran" } },
});
assert.equal(sceneFor(taskDone, pureView).key, "trace");
const appliedArtifact = event({
  seq: 45,
  entryType: "event",
  kind: "artifact",
  summary: "",
  detail: { hop: "ran", pure: { hop: "ran" }, toolName: "apply_energy_saving_plan" },
});
assert.equal(sceneFor(appliedArtifact, pureView).key, "execute");

const completed = event({
  seq: 5,
  entryType: "event",
  kind: "status",
  phase: "execution",
  state: "TASK_STATE_COMPLETED",
  summary: "OSS Agent completed the request.",
  detail: {
    hop: "oss",
    pure: { phase: "report", rounds: 1, messages: 13, hops: 3 },
  },
});
applyFrame(pureView, completed);
assert.equal(pureView.pureMetrics.hops, 3);
assert.equal(pureView.pureMetrics.rounds, 1);

applyFrame(pureView, finished("task_completed", { target_rate: 30 }));
assert.equal(stageOf(pureView), "done");

const pureNight = createState();
applyFrame(pureNight, {
  type: "run_started",
  runId: "r9",
  scenario: "night",
  mode: "auto",
  protocol: "pure",
});
assert.equal(negotiationExpected(pureNight), false);

// A gate scene queued before the decision must not hold the player once that decision exists.
let gateMeta = null;
const resolvedGatePlayer = createPlayer({
  onApply: () => {},
  onRewind: () => {},
  onChange: () => {},
  onScene: (scene, meta) => {
    if (scene.key === "approval-pure") gateMeta = meta;
  },
  holds: () => false,
});
resolvedGatePlayer.enqueue({
  key: "approval-pure",
  step: 8,
  duration: 0,
  hold: true,
  frame: pureApproval,
});
assert.equal(gateMeta.hold, false);
assert.equal(resolvedGatePlayer.status().hold, false);
assert.equal(resolvedGatePlayer.status().playhead, 1);

const openGatePlayer = createPlayer({
  onApply: () => {},
  onRewind: () => {},
  onChange: () => {},
  onScene: () => {},
  holds: () => true,
});
openGatePlayer.enqueue({
  key: "approval-pure",
  step: 8,
  duration: 0,
  hold: true,
  frame: pureApproval,
});
assert.equal(openGatePlayer.status().hold, true);

// --- Two-phase planned outage: Create baseline, then Modify on the same Task --------------

const twoPhase = createState();
applyFrame(twoPhase, {
  type: "run_started",
  runId: "run-two-phase",
  scenario: "outage",
  protocol: "a2a-t",
  approvalGate: true,
});
twoPhase.intentText = "Create a baseline, then modify the running task for the outage.";
twoPhase.taskId = "task-1";
applyFrame(twoPhase, {
  type: "trace",
  event: {
    seq: 1,
    entryType: "trace",
    kind: "task_prompt_rendered",
    phase: "task_prompt",
    detail: { promptText: "## Operation Type\n\nCreate" },
  },
});
applyFrame(twoPhase, {
  type: "trace",
  event: {
    seq: 2,
    entryType: "trace",
    kind: "message_sent",
    phase: "task_validation",
    detail: {
      performative: "TASK_T",
      summary: "create ran energy saving task",
      samplePhase: "baseline",
      taskT: "## Operation Type\n\nCreate",
    },
  },
});
assert.equal(twoPhase.taskTSent, true);
assert.equal(twoPhase.twoPhase, true);
assert.equal(twoPhase.phaseIndex, 0);
assert.equal(twoPhase.taskTPrompt, "## Operation Type\n\nCreate");

applyFrame(
  twoPhase,
  status(3, "Waiting for the engineer's approval before applying the plan: cell-002, 01:00~05:00, 10%.", {
    state: "TASK_STATE_INPUT_REQUIRED",
  }),
);
assert.equal(twoPhase.approvalState, "waiting");
applyFrame(twoPhase, status(4, "Approved. Applying the plan.", { state: "TASK_STATE_WORKING" }));
assert.equal(twoPhase.approvalState, "approved");
applyFrame(
  twoPhase,
  event({
    seq: 5,
    entryType: "event",
    kind: "artifact",
    phase: "report",
    name: "energy saving intent report",
    data: {
      target_rate: 10,
      plan_id: "plan-cell-002-001",
      window: "01:00~05:00",
      status: "applied",
    },
  }),
);
assert.equal(twoPhase.report.target_rate, 10);

// The Modify prompt is the phase boundary: the execution pass resets, the same Task remains.
applyFrame(twoPhase, {
  type: "trace",
  event: {
    seq: 6,
    entryType: "trace",
    kind: "task_prompt_rendered",
    phase: "task_modify",
    detail: { promptText: "## Operation Type\n\nModify" },
  },
});
assert.equal(twoPhase.phaseIndex, 1);
assert.equal(twoPhase.phaseKey, "modify");
assert.equal(twoPhase.modifyPhaseStarted, true);
assert.equal(twoPhase.taskTPrompt, "## Operation Type\n\nModify");
assert.equal(twoPhase.report, null);
assert.equal(twoPhase.approvalState, null);
assert.equal(twoPhase.mmlSent, false);
assert.equal(twoPhase.taskCreated, true);

applyFrame(
  twoPhase,
  status(7, "Waiting for the engineer's approval before applying the plan: cell-002, 01:00~05:00, 20%.", {
    state: "TASK_STATE_INPUT_REQUIRED",
  }),
);
assert.equal(twoPhase.approvalState, "waiting");
assert.equal(pendingHuman(twoPhase), "approval");
applyFrame(
  twoPhase,
  event({
    seq: 8,
    entryType: "event",
    kind: "artifact",
    phase: "report",
    name: "energy saving intent report",
    data: {
      target_rate: 20,
      plan_id: "plan-cell-002-001",
      window: "01:00~05:00",
      status: "modified",
    },
  }),
);
applyFrame(twoPhase, status(9, "", { state: "TASK_STATE_COMPLETED" }));
assert.equal(twoPhase.report.target_rate, 20);
assert.equal(twoPhase.taskCompleted, true);
assert.deepEqual(continuityModel(twoPhase), {
  taskId: "task-1",
  planId: "plan-cell-002-001",
  beforeRate: 10,
  afterRate: 20,
  window: "01:00~05:00",
  status: "modified",
});
assert.equal(continuityModel(createState()), null);

// The same protocol row may repeat in both phases; the phase-aware key keeps both rows.
const phaseRows = createState();
applyFrame(phaseRows, status(1, "Data collection completed: baseline ready.", { state: "TASK_STATE_WORKING" }));
applyFrame(phaseRows, {
  type: "trace",
  event: {
    seq: 2,
    entryType: "trace",
    kind: "message_sent",
    phase: "task_validation",
    detail: { performative: "TASK_T", samplePhase: "baseline", taskT: "Create" },
  },
});
applyFrame(phaseRows, {
  type: "trace",
  event: {
    seq: 3,
    entryType: "trace",
    kind: "task_prompt_rendered",
    phase: "task_modify",
    detail: { promptText: "Modify" },
  },
});
applyFrame(phaseRows, status(4, "Data collection completed: baseline ready.", { state: "TASK_STATE_WORKING" }));
assert.equal(phaseRows.trace.filter((row) => row.title === "Data collection completed").length, 2);

// The second Task-T uses its own scene and stays on the apply step instead of rewinding.
const secondSendFrame = {
  type: "trace",
  event: {
    seq: 20,
    entryType: "trace",
    kind: "message_sent",
    phase: "task_modify",
    detail: { performative: "TASK_T", summary: "modify ran energy saving task", taskT: "Modify" },
  },
};
assert.equal(sceneFor(secondSendFrame, twoPhase).key, "modify-send");
assert.equal(sceneFor(secondSendFrame, twoPhase).step, 8);

// A Modify-only run has no baseline: its first Task-T is the Modify and uses the send step.
const modifyOnly = createState();
applyFrame(modifyOnly, {
  type: "run_started",
  runId: "m1",
  scenario: "outage",
  runMode: "modify",
  targetTaskId: "task-9",
  targetContextId: "ctx-9",
});
assert.equal(modifyOnly.phaseKey, "modify");
assert.equal(modifyOnly.modifyPhaseStarted, true);
assert.equal(modifyOnly.twoPhase, false);
assert.equal(sceneFor(secondSendFrame, modifyOnly).key, "modify-start");
assert.equal(sceneFor(secondSendFrame, modifyOnly).step, 3);
const firstSendFrame = {
  type: "trace",
  event: {
    seq: 21,
    entryType: "trace",
    kind: "message_sent",
    phase: "task_validation",
    detail: { performative: "TASK_T", summary: "create ran energy saving task", taskT: "Create" },
  },
};
assert.equal(sceneFor(firstSendFrame, createState()).key, "send");

// The wire inspector keeps each send's own Task-T instead of the state's latest prompt.
const firstWire = buildWireModel(
  {
    kind: "message_sent",
    spanId: "d".repeat(16),
    detail: {
      performative: "TASK_T",
      summary: "create ran energy saving task",
      extensionUri: "urn:task-t/v1",
      templateUri: "urn:template",
      taskT: "## Operation Type\n\nCreate\n\n## Task Object\n\nBaseline area.",
      samplePhase: "baseline",
      sampleRunId: "run-1",
    },
  },
  { runId: "run-two-phase", intentText: rawRequest, taskTPrompt: "## Operation Type\n\nModify" },
  "t",
  copy,
);
const secondWire = buildWireModel(
  {
    kind: "message_sent",
    spanId: "e".repeat(16),
    detail: {
      performative: "TASK_T",
      summary: "modify ran energy saving task",
      extensionUri: "urn:task-t/v1",
      templateUri: "urn:template",
      taskT: "## Operation Type\n\nModify\n\n## Task Object\n\nModified area.",
    },
  },
  { runId: "run-two-phase", intentText: rawRequest, taskTPrompt: "## Operation Type\n\nModify" },
  "t",
  copy,
);
assert.ok(wireText(firstWire).includes("Create"));
assert.ok(!wireText(firstWire).includes("Modify"));
assert.ok(wireText(firstWire).includes('"samplePhase": "baseline"'));
assert.ok(wireText(firstWire).includes('"sampleRunId": "run-1"'));
assert.ok(wireText(secondWire).includes("Modify"));
assert.equal(secondWire.title, copy.wire.modifyTitle);

// The execution milestones understand the Modify tool.
const modifyMilestones = createState();
applyFrame(
  modifyMilestones,
  event({
    seq: 30,
    entryType: "event",
    kind: "artifact",
    phase: "execution",
    detail: { toolName: "modify_energy_saving_plan" },
  }),
);
assert.equal(executionSteps(modifyMilestones).find((step) => step.key === "apply").done, true);
assert.equal(
  traceStageKey({
    kind: "message_sent",
    phase: "task_modify",
    summary: "Modify Task-T sent",
  }),
  "report",
);

// Trace tags describe the channel or the actor type: communication is A2A-T (or A2A in pure
// mode), human gates are Human, and model calls are LLM. OSS/RAN stay for internal spans only.
const tagState = createState();
applyFrame(tagState, { type: "run_started", runId: "tags", scenario: "night", protocol: "a2a-t" });
applyFrame(tagState, event({ seq: 60, entryType: "event", kind: "status", state: "TASK_STATE_WORKING", summary: "Working" }));
applyFrame(tagState, event({ seq: 61, entryType: "event", kind: "artifact", detail: { toolName: "evaluate_energy_saving_plan" } }));
applyFrame(tagState, event({ seq: 62, entryType: "decision", kind: "accept", summary: "accept" }));
applyFrame(tagState, { type: "trace", event: { seq: 63, kind: "llm", title: "LLM call", actor: "llm" } });
applyFrame(tagState, { type: "trace", event: { seq: 64, kind: "message_sent", title: "Task-T sent", actor: "oss" } });
assert.deepEqual(tagState.trace.map((row) => row.tag), ["a2at", "a2at", "human", "llm", "a2at"]);

const pureTagState = createState();
applyFrame(pureTagState, { type: "run_started", runId: "tags-pure", scenario: "night", protocol: "pure" });
applyFrame(pureTagState, event({ seq: 70, entryType: "event", kind: "status", state: "TASK_STATE_WORKING", summary: "Working" }));
assert.deepEqual(pureTagState.trace.map((row) => row.tag), ["a2a"]);

// The apply result is a status notification now; it still advances the apply milestone and beat.
const applyResult = event({
  seq: 80,
  entryType: "event",
  kind: "status",
  state: "TASK_STATE_WORKING",
  summary: "Progress: plan plan-cell-001-001 was applied to cell cell-001.",
});
const applyStatusState = createState();
applyFrame(applyStatusState, applyResult);
assert.equal(executionSteps(applyStatusState).find((step) => step.key === "apply").done, true);
assert.equal(sceneFor(applyResult, createState()).key, "apply");
const dispatched = createState();
dispatched.mmlSent = true;
assert.equal(sceneFor(applyResult, dispatched).key, "execute");

// Task-T / Negotiation-T rows are distinguished by the extension payload they carry.
const extensionTagState = createState();
applyFrame(extensionTagState, {
  type: "trace",
  event: { seq: 90, kind: "message_sent", title: "Task-T sent", detail: { performative: "TASK_T" } },
});
applyFrame(extensionTagState, {
  type: "trace",
  event: { seq: 91, kind: "task_prompt_rendered", title: "Task-T prompt rendered" },
});
applyFrame(
  extensionTagState,
  event({
    seq: 92,
    entryType: "event",
    kind: "artifact",
    phase: "report",
    templateUri: "Task-T/network-layer/ran-energy-saving/v1",
  }),
);
applyFrame(
  extensionTagState,
  event({ seq: 93, entryType: "event", kind: "propose", phase: "negotiation", performative: "PROPOSE" }),
);
applyFrame(extensionTagState, {
  type: "trace",
  event: {
    seq: 94,
    kind: "message_sent",
    title: "ACCEPT sent",
    detail: { performative: "ACCEPT", templateUri: "Negotiation-T/information/accept/v1" },
  },
});
assert.deepEqual(extensionTagState.trace.map((row) => row.tag), [
  "task-t",
  "task-t",
  "task-t",
  "neg-t",
  "neg-t",
]);

// The Extension view shows only the Task-T / Negotiation-T payload, and an artifact update must
// not invent a "phase" field that the A2A standard does not define.
const extensionEntry = {
  entryType: "event",
  kind: "artifact",
  metadata: {
    "https://projects.tmforum.org/a2aproject/telecommunication/extensions/Task-T/v1":
      "## Operation Type\nModify",
    templateUri: "Task-T/network-layer/ran-energy-saving/v1",
  },
};
const extensionModel = buildWireModel(extensionEntry, createState(), "x", copy);
assert.ok(extensionModel);
assert.ok(wireText(extensionModel).includes("Operation Type"));
assert.ok(!wireText(extensionModel).includes("artifactUpdate"));
assert.equal(
  buildWireModel({ entryType: "event", kind: "status", summary: "Working" }, createState(), "x", copy),
  null,
);
const artifactModel = buildWireModel(
  {
    entryType: "event",
    kind: "artifact",
    name: "tool result",
    summary: '{"ok": true}',
    phase: "execution",
  },
  createState(),
  "t",
  copy,
);
assert.ok(!wireText(artifactModel).includes('"phase"'));
assert.ok(wireText(artifactModel).includes("ok"));

// --- Business intent processing ------------------------------------------

const processed = createState();
applyFrame(processed, {
  type: "run_started",
  runId: "intent-1",
  scenario: "night",
  mode: "confirm",
  userRequest: "Start energy saving in Region A.",
  intentMode: "processed",
});
applyFrame(processed, {
  type: "trace",
  event: {
    seq: 2,
    phase: "task_prompt",
    kind: "intent_processed",
    title: "Intent processed",
    actor: "client",
    side: "client",
    detail: {
      mode: "processed",
      source: "scenario",
      rawRequest: "Start energy saving in Region A.",
      normalizedText: "## Operation Type\nCreate\n\n## Task Object\nThe area is Region A.",
      intent: {
        rawRequest: "Start energy saving in Region A.",
        normalizedText: "## Operation Type\nCreate\n\n## Task Object\nThe area is Region A.",
        operationType: "Create",
        region: "Region A",
        sites: [],
        windows: ["{00:00~06:00,10Mbps}"],
        missingFields: [],
        conflicts: [],
        source: "scenario",
        warnings: [],
      },
      warnings: [],
    },
  },
});
assert.equal(processed.rawRequest, "Start energy saving in Region A.");
assert.ok(processed.normalizedRequest.includes("Operation Type"));
assert.equal(processed.intentSource, "scenario");
assert.equal(processed.businessIntent.operationType, "Create");
assert.equal(processed.intentText, processed.normalizedRequest);

applyFrame(processed, {
  type: "intent_review_requested",
  promptText: processed.normalizedRequest,
  timeoutSeconds: 300,
});
assert.equal(processed.intentReview.promptText, processed.normalizedRequest);

const pureIntent = createState();
applyFrame(pureIntent, {
  type: "event",
  event: {
    seq: 1,
    kind: "status",
    summary: "Business intent ready: Region A",
    detail: {
      hop: "oss",
      pure: { hop: "oss", phase: "intent" },
      intentProcessed: {
        rawRequest: "raw text",
        normalizedText: "normalized text",
        operationType: "Create",
        source: "scenario",
        warnings: [],
      },
    },
  },
});
assert.equal(pureIntent.rawRequest, "raw text");
assert.equal(pureIntent.normalizedRequest, "normalized text");

// Langfuse: the trace id arrives with run_started, the URL in a late langfuse frame.
assert.equal(copy.result.openInLangfuse, "在 Langfuse 中打开");
assert.equal(copyFor("en-US").result.openInLangfuse, "Open in Langfuse");
assert.equal(copyFor("zh-CN").result.runLabel, "Run");
assert.equal(copyFor("zh-CN").result.traceLabel, "Trace");
const langfuseState = createState();
applyFrame(langfuseState, {
  type: "run_started",
  runId: "run-lf",
  traceId: "trace-1",
  langfuse: { enabled: true, traceId: "trace-1", status: "exporting" },
});
assert.equal(langfuseState.traceId, "trace-1");
assert.equal(langfuseState.langfuse.traceId, "trace-1");
assert.equal(langfuseState.langfuse.url, undefined);
applyFrame(langfuseState, {
  type: "langfuse",
  runId: "run-lf",
  langfuse: {
    enabled: true,
    traceId: "trace-1",
    url: "https://langfuse.example/project/p/traces/trace-1",
    status: "exported",
  },
});
assert.equal(langfuseState.langfuse.url, "https://langfuse.example/project/p/traces/trace-1");
assert.equal(langfuseState.langfuse.status, "exported");
// A late frame from another run must not overwrite this page's link.
applyFrame(langfuseState, {
  type: "langfuse",
  runId: "run-other",
  langfuse: { enabled: true, traceId: "trace-2", url: "https://langfuse.example/trace-2" },
});
assert.equal(langfuseState.langfuse.traceId, "trace-1");

console.log("presentation model harness: ok");

// A real-NMS region is one compact token; the free-form normalized request must still resolve it.
const compactRegion = parseIntent(
  "Create a RAN energy saving task in A2ATestRegion. Based on the task object and task context, " +
    "the time-segment-based guaranteed throughput target is {00:00~06:00,30Mbps}.",
);
assert.equal(compactRegion.area, "A2ATestRegion");
assert.deepEqual(compactRegion.slots, [{ start: 0, end: 360, rate: 30 }]);

const compactRegionZh = parseIntent(
  "在 A2ATestRegion 区域创建无线网络节能任务：分时段保障速率目标为 {00:00~06:00,30Mbps}。",
);
assert.equal(compactRegionZh.area, "A2ATestRegion");

// The structured intent is authoritative: it wins over a sentence the parser cannot classify.
const serverIntent = parseIntent("Start energy saving in the requested area.", {
  region: "A2ATestRegion",
  operationType: "Create",
  sites: ["NRES_XCS_6102937", "LTE_26441_LNR_OCL"],
  windows: ["{00:00~06:00,30Mbps}"],
});
assert.equal(serverIntent.area, "A2ATestRegion");
assert.equal(serverIntent.operation, "create");
assert.deepEqual(serverIntent.sites, ["NRES_XCS_6102937", "LTE_26441_LNR_OCL"]);
assert.deepEqual(serverIntent.slots, [{ start: 0, end: 360, rate: 30 }]);

// The wire inspector renders the captured exchange, never a reconstructed request line.
const capturedEntry = {
  kind: "message_sent",
  spanId: "d".repeat(16),
  detail: {
    performative: "TASK_T",
    summary: "create ran energy saving task",
    extensionUri: "urn:task-t/v1",
    taskT: structuredTaskT,
  },
};
const capturedState = {
  runId: "run-wire",
  intentText: rawRequest,
  taskTPrompt: structuredTaskT,
  wire: [
    {
      dir: "out",
      method: "POST",
      url: "https://nms.test:31127/a2a/json/message:send",
      headers: { Accept: "application/json", Authorization: "***" },
      body: { message: { parts: [{ text: "Task-T" }] } },
    },
  ],
};
const capturedText = wireText(buildWireModel(capturedEntry, capturedState, "t", copy));
assert.ok(capturedText.includes("POST /a2a/json/message:send HTTP/1.1"));
assert.ok(capturedText.includes("Authorization"));
assert.ok(capturedText.includes("***"));
assert.ok(!capturedText.includes("/a2a/v1/"));
assert.ok(!capturedText.includes("traceparent: 00-"));
// The body is the captured SendMessageRequest ({"message": {...}}), not a rebuilt one.
assert.ok(capturedText.includes('"message"'));
assert.ok(capturedText.includes('"text": "Task-T"'));
assert.ok(!capturedText.includes("## Operation Type"));

const uncapturedText = wireText(
  buildWireModel(capturedEntry, { ...capturedState, wire: [] }, "t", copy),
);
assert.ok(uncapturedText.includes(copy.wire.noWireCapture));
// Without a capture the fallback still uses the real request envelope, never a bare Message.
assert.ok(uncapturedText.includes('"message"'));
assert.ok(uncapturedText.includes("## Operation Type"));

// A pure-A2A send has no Task-T: the panel shows the captured request and picks the record by
// message id, so a later round never renders an earlier round's body.
const pureSentEntry = {
  kind: "message_sent",
  spanId: "e".repeat(16),
  detail: {
    protocol: "pure",
    performative: "MESSAGE",
    summary: "please answer with the missing rate",
    messageId: "msg-2",
  },
};
const pureSentState = {
  runId: "run-pure",
  wire: [
    {
      dir: "out",
      method: "POST",
      url: "http://127.0.0.1:8001/a2a/json/message:stream",
      headers: { "content-type": "application/json" },
      body: { message: { messageId: "msg-1", parts: [{ text: "first round" }] } },
    },
    {
      dir: "out",
      method: "POST",
      url: "http://127.0.0.1:8001/a2a/json/message:stream",
      headers: { "content-type": "application/json" },
      body: { message: { messageId: "msg-2", parts: [{ text: "second round" }] } },
    },
  ],
};
const pureSentModel = buildWireModel(pureSentEntry, pureSentState, "t", copy);
const pureSentText = wireText(pureSentModel);
assert.equal(pureSentModel.title, copy.wire.pureMessageTitle);
assert.ok(pureSentText.includes('"messageId": "msg-2"'));
assert.ok(pureSentText.includes('"text": "second round"'));
assert.ok(!pureSentText.includes("first round"));
assert.ok(pureSentText.includes("POST /a2a/json/message:stream HTTP/1.1"));

// The Extension view prefers the captured metadata (full value) over the send span's excerpt.
const capturedExtensionEntry = {
  kind: "message_sent",
  spanId: "f".repeat(16),
  detail: { performative: "TASK_T", messageId: "msg-ext", taskT: "truncated" },
};
const capturedExtensionState = {
  runId: "run-ext",
  wire: [
    {
      dir: "out",
      method: "POST",
      url: "http://127.0.0.1:8000/message:stream",
      headers: {},
      body: {
        message: {
          messageId: "msg-ext",
          metadata: {
            "urn:Task-T/v1": "FULL TASK T PAYLOAD",
            templateUri: "Task-T/network-layer/ran-energy-saving/v1",
          },
        },
      },
    },
  ],
};
const capturedExtensionText = wireText(
  buildWireModel(capturedExtensionEntry, capturedExtensionState, "x", copy),
);
assert.ok(capturedExtensionText.includes("FULL TASK T PAYLOAD"));
assert.ok(!capturedExtensionText.includes("truncated"));
assert.ok(capturedExtensionText.includes("Task-T/network-layer/ran-energy-saving/v1"));

// ---------------------------------------------------------------------------
// Correction chain
// ---------------------------------------------------------------------------

function memoryStorage() {
  const data = new Map();
  return {
    getItem: (key) => (data.has(key) ? data.get(key) : null),
    setItem: (key, value) => data.set(key, String(value)),
    removeItem: (key) => data.delete(key),
  };
}

assert.equal(chainIdentity("run-1", "chain-1", "information", "a2a-t").chainId, "chain-1");
// A server without a chain id still gives the retry a parent to follow.
assert.equal(chainIdentity("run-1", "", "information", "a2a-t").chainId, "run-1");
assert.equal(chainIdentity("", "chain-1", "information", "a2a-t"), null);

const pending = chainIdentity("run-1", "chain-1", "information", "a2a-t");
assert.equal(pendingChain(pending, "information", "a2a-t"), pending);
// A different example or protocol is a new intent, not a retry.
assert.equal(pendingChain(pending, "outage", "a2a-t"), null);
assert.equal(pendingChain(pending, "information", "plain"), null);
assert.equal(pendingChain(null, "information", "a2a-t"), null);

const storage = memoryStorage();
saveChain(storage, pending);
assert.deepEqual(restoreChain(storage), pending);
clearChain(storage);
assert.equal(restoreChain(storage), null);

storage.setItem("a2at-ran-correction-chain", "{not json");
assert.equal(restoreChain(storage), null);

// --- Live wait state ---------------------------------------------------------
// A wait-state frame explains a long silence; it must never become a timeline row.
const waiting = createState();
const startedAt = new Date(Date.now() - 42_000).toISOString();
applyFrame(waiting, {
  type: "activity",
  activity: {
    activityId: "act-1",
    kind: "model",
    labelKey: "waitingModel",
    status: "running",
    stage: "task_t_slot_extraction",
    startedAt,
    updatedAt: startedAt,
    detail: {},
  },
});
assert.equal(waiting.activity.kind, "model");
assert.equal(waiting.trace.length, 0);
const waitView = activityView(waiting, copy, Date.now());
assert.ok(waitView !== null);
assert.equal(waitView.elapsedSeconds, 42);
assert.ok(waitView.label.length > 0);

applyFrame(waiting, {
  type: "activity",
  activity: {
    activityId: "act-1",
    kind: "model",
    labelKey: "waitingModel",
    status: "done",
    stage: "task_t_slot_extraction",
    startedAt,
    detail: {},
  },
});
assert.equal(waiting.activity, null);
assert.equal(activityView(waiting, copy, Date.now()), null);

const broken = {
  getItem: () => {
    throw new Error("blocked");
  },
  setItem: () => {
    throw new Error("blocked");
  },
  removeItem: () => {
    throw new Error("blocked");
  },
};
saveChain(broken, pending);
clearChain(broken);
assert.equal(restoreChain(broken), null);

// --- Pure A2A trace fidelity: real hop names, visible status text, answer provenance ----

// OSS-owned statuses are narration to the page, not a message from the OSS to the RAN.
assert.equal(traceDirection({ detail: { hop: "oss" }, direction: "server->client" }), "OSS internal");
assert.equal(traceDirection({ detail: { hop: "nms" }, direction: "server->client" }), "NMS → OSS");
// The OSS->downstream message must name the downstream that actually exists.
assert.equal(
  traceDirection(
    { detail: { hop: "oss", role: "oss", protocol: "pure" }, direction: "client->server" },
    { downstream: "nms" },
  ),
  "OSS → NMS",
);
assert.equal(
  traceDirection(
    { detail: { hop: "oss", role: "oss", protocol: "pure" }, direction: "client->server" },
    { downstream: "ran" },
  ),
  "OSS → RAN",
);
// In a pure run the engineer client talks to the OSS Agent, not to the RAN.
assert.equal(
  traceDirection(
    { detail: { role: "client", protocol: "pure" }, direction: "client->server" },
    { protocol: "pure" },
  ),
  "UI → OSS",
);

// A pure status keeps its sentence instead of collapsing to the bare state name.
const pureStatus = createState();
applyFrame(pureStatus, { type: "run_started", runId: "r8", scenario: "night", mode: "confirm", protocol: "pure" });
applyFrame(pureStatus, {
  type: "event",
  event: {
    seq: 1,
    entryType: "event",
    kind: "status",
    state: "TASK_STATE_WORKING",
    phase: "answer",
    summary: "OSS Agent answered the NMS: The region is Region A.",
    direction: "server->client",
    detail: { hop: "oss", pure: { hop: "oss", phase: "answer", downstream: "nms" } },
  },
});
const pureRow = pureStatus.trace.at(-1);
assert.equal(pureRow.title, "OSS Agent answered the NMS: The region is Region A.");
assert.equal(pureRow.dir, "OSS internal");
assert.equal(pureStatus.downstream, "nms");

// The confirmation card shows the concrete AI-suggested values, not only the action.
const suggestion = createState();
applyFrame(suggestion, { type: "run_started", runId: "r9", scenario: "night", mode: "confirm", protocol: "pure" });
applyFrame(suggestion, {
  type: "decision_requested",
  round: 1,
  maxRounds: 5,
  proposal: { kind: "information", content: "Region not recognized." },
  recommendation: {
    action: "accept",
    rationale: "the only grounded value",
    filled_params: { region: "Region A" },
  },
  requestedAt: new Date().toISOString(),
});
const suggestionCard = decisionCard(suggestion, copy);
assert.deepEqual(suggestionCard.recommendationFields, [{ name: "region", value: "Region A" }]);
assert.equal(decisionPayload(suggestionCard, "confirm", copy).answerSource, "ai_recommendation");
assert.equal(decisionPayload(suggestionCard, "reject", copy).answerSource, "operator");
