// Store/filter runtime checks of the Run Explorer (no DOM, no network).
//
// Run with: node tests/js/store.test.mjs
// The Python test suite shells out to this file and skips cleanly when Node is absent.

import assert from "node:assert/strict";

import { RunStore } from "../../demo/web/static/js/store.js";
import {
  FilterState,
  availableRounds,
  filterItems,
} from "../../demo/web/static/js/filters.js";

const store = new RunStore();
store.setMeta({ language: "en-US", modelMode: "offline", modelLabel: "offline (scripted)" });

const frames = [
  {
    type: "run_started",
    seq: 1,
    runId: "r1",
    scenario: "information",
    description: "desc",
    userRequest: "request",
    language: "en-US",
    mode: "auto",
    transport: "sse",
    discovery: "direct",
    modelMode: "offline",
    modelLabel: "offline (scripted)",
    expectedSequence: [{ actor: "server", performative: "PROPOSE", round: 1 }],
    phase: "dispatch",
    phaseStep: 1,
    spanId: "ui_0001",
    startedAt: "2026-10-23T00:00:00.000Z",
  },
  {
    type: "trace",
    seq: 2,
    event: {
      seq: 2,
      spanId: "llm_c_1",
      parentSpanId: null,
      phase: "task_prompt",
      phaseStep: 1,
      round: null,
      actor: "llm",
      side: "client",
      kind: "llm",
      stage: "task_t_slot_extraction",
      title: "LLM call · task_t_slot_extraction",
      purpose: "extract",
      status: "ok",
      startedAt: "2026-10-23T00:00:00.500Z",
      endedAt: "2026-10-23T00:00:00.600Z",
      durationMs: 100,
      model: "scripted",
      tokens: {
        prompt: null,
        completion: null,
        total: null,
        estimated: true,
        scripted: true,
        label: "n/a (scripted)",
      },
      error: null,
      detail: {
        provider: "scripted_traced",
        request: { messages: [] },
        response: { rawContent: "{}", parsed: {} },
      },
    },
  },
  {
    type: "event",
    seq: 3,
    event: {
      seq: 3,
      entryType: "event",
      phase: "negotiation",
      phaseStep: 1,
      spanId: "ui_0002",
      parentSpanId: null,
      kind: "propose",
      eventKind: "message",
      round: 1,
      maxRounds: 3,
      direction: "server->client",
      summary: "Proposal",
      rationale: null,
      status: "ok",
      durationMs: null,
      detail: null,
      contextId: "ctx",
      templateUri: "Negotiation-T/information-negotiation/propose/v1",
      negotiationType: "information",
      performative: "PROPOSE",
      ts: "2026-10-23T00:00:01.000Z",
    },
  },
  {
    type: "event",
    seq: 4,
    event: {
      seq: 4,
      entryType: "decision",
      phase: "resolution",
      phaseStep: 1,
      spanId: "ui_0003",
      parentSpanId: null,
      kind: "accept",
      eventKind: "decision",
      round: 1,
      maxRounds: 3,
      direction: "client->server",
      summary: "ok",
      rationale: "accept it",
      status: "ok",
      durationMs: null,
      detail: null,
      action: "accept",
      filledParams: { region: "x" },
      ts: "2026-10-23T00:00:02.000Z",
    },
  },
  {
    type: "trace",
    seq: 5,
    event: {
      seq: 5,
      spanId: "tool_s_1",
      parentSpanId: "llm_s_1",
      phase: "execution",
      phaseStep: 2,
      round: null,
      actor: "tool",
      side: "server",
      kind: "tool",
      stage: "server_execution_agent",
      title: "Tool call · get",
      purpose: "tool",
      status: "ok",
      startedAt: "2026-10-23T00:00:03.000Z",
      endedAt: "2026-10-23T00:00:03.010Z",
      durationMs: 10,
      model: null,
      tokens: null,
      error: null,
      detail: { toolName: "get", args: {}, result: {} },
    },
  },
  {
    type: "finished",
    seq: 6,
    runId: "r1",
    status: "finished",
    finalState: "task_completed",
    report: { ok: true },
    error: null,
    transcriptJson: "/x",
    transcriptMarkdown: "/y",
    traceJson: "/z",
    phase: "done",
    phaseStep: 1,
    spanId: "ui_0004",
    startedAt: "2026-10-23T00:00:04.000Z",
  },
];

for (const frame of frames) assert.equal(store.applyFrame(frame), true);
for (const frame of frames) {
  assert.equal(store.applyFrame(frame), false, "duplicate seq must be ignored");
}
assert.equal(store.frameCount, 6);
assert.equal(store.status, "finished");
assert.equal(store.list().length, 6);
assert.deepEqual(
  store.list().map((item) => item.number),
  [1, 2, 3, 4, 5, 6],
);
assert.deepEqual(
  store.list().map((item) => item.seq),
  [1, 2, 3, 4, 5, 6],
);

// Every row keeps the process that produced it, so the timeline can label client vs server.
const llmRow = store.list().find((item) => item.kind === "llm");
const toolRow = store.list().find((item) => item.kind === "tool");
assert.equal(llmRow?.side, "client", "the client LLM span keeps its side");
assert.equal(toolRow?.side, "server", "the server tool span keeps its side");

const derived = store.derived();
assert.equal(derived.groups.length, 8);
assert.equal(derived.groups.find((group) => group.key === "negotiation").items.length, 1);
assert.equal(derived.phaseStates.dispatch, "done");
assert.equal(derived.phaseStates.negotiation, "done");
assert.equal(derived.phaseStates.report, "skipped");
assert.equal(derived.analysis.llmCalls, 1);
assert.equal(derived.analysis.scriptedCalls, 1);
assert.equal(derived.analysis.tools, 1);
assert.equal(derived.analysis.decisions, 1);
assert.equal(derived.decisionLog[0].action, "accept");
assert.equal(derived.decisionLog[0].source, "scripted");
assert.deepEqual(
  derived.rounds.map((round) => round.round),
  [1],
);
assert.equal(store.tokenTotal().scripted, 1);
assert.equal(store.runMeta().scenario, "information");
assert.equal(store.ids().contextId, "ctx");
assert.equal(store.resolutionLabel(), "Accepted");
assert.equal(store.phaseNote("negotiation"), null, "a phase with rows has no skip note");

// A run whose Task-T validation passed emits no Negotiation-T proposal; the skipped phases must
// explain why instead of only saying "Skipped".
const noNegotiation = new RunStore();
noNegotiation.beginRun("r-no-negotiation");
noNegotiation.applyFrame({
  type: "run_started",
  seq: 1,
  runId: "r-no-negotiation",
  scenario: "target",
});
noNegotiation.applyFrame({
  type: "finished",
  seq: 2,
  status: "finished",
  finalState: "task_completed",
});
const skipped = noNegotiation.derived();
assert.equal(skipped.phaseStates.negotiation, "skipped");
assert.equal(
  noNegotiation.phaseNote("negotiation"),
  "Task-T validation passed; no negotiation was required.",
);
assert.equal(
  noNegotiation.phaseNote("resolution"),
  "No resolution message was sent.",
);

const failed = new RunStore();
failed.beginRun("r-failed");
failed.applyFrame({
  type: "run_started",
  seq: 1,
  runId: "r-failed",
  scenario: "target",
});
failed.applyFrame({
  type: "finished",
  seq: 2,
  status: "failed",
  error: "PromptGenerationError: 'Task Description' is not provided in the input.",
});
assert.match(failed.phaseNote("negotiation"), /PromptGenerationError/);

// The terminal frame's start time must survive into the item, so the Done phase has a duration
// instead of a null one (the backend stamps `startedAt` on the finished frame).
const finishedItem = store.list().at(-1);
assert.equal(finishedItem.entryType, "finished");
assert.equal(finishedItem.startedAt, "2026-10-23T00:00:04.000Z");
const doneGroup = derived.groups.find((group) => group.key === "done");
assert.equal(doneGroup.stats.steps, 1);
assert.notEqual(doneGroup.stats.durationMs, null, "the Done phase card needs a duration");
assert.equal(derived.phaseStates.done, "done");

const filters = new FilterState();
assert.equal(filterItems(store.list(), filters).length, 6);
assert.equal(filterItems(store.list(), filters.with({ filter: "llm" })).length, 1);
assert.equal(filterItems(store.list(), filters.with({ filter: "tools" })).length, 1);
assert.equal(filterItems(store.list(), filters.with({ filter: "protocol" })).length, 4);
assert.equal(filterItems(store.list(), filters.with({ search: "accept it" })).length, 1);
assert.deepEqual(availableRounds(store.list()), [1]);

// A live LLM call without usage metadata is missing ("n/a"), never "n/a (scripted)".
const live = new RunStore();
live.setMeta({ language: "en-US", modelMode: "live", modelLabel: "live" });
live.beginRun("r-live");
live.applyFrame({
  type: "run_started",
  seq: 1,
  runId: "r-live",
  scenario: "information",
  mode: "auto",
  transport: "sse",
  discovery: "direct",
  modelMode: "live",
  modelLabel: "live",
  phase: "dispatch",
  phaseStep: 1,
  spanId: "ui_live_1",
  startedAt: "2026-10-23T00:00:00.000Z",
});
live.applyFrame({
  type: "trace",
  seq: 2,
  event: {
    seq: 2,
    spanId: "llm_live_1",
    phase: "execution",
    phaseStep: 1,
    actor: "llm",
    side: "client",
    kind: "llm",
    title: "LLM call",
    status: "ok",
    model: "gpt",
    tokens: null,
    detail: {},
  },
});
const liveAnalysis = live.derived().analysis;
assert.equal(liveAnalysis.numericCalls, 0);
assert.equal(liveAnalysis.scriptedCalls, 0);
assert.equal(liveAnalysis.missingCalls, 1);
assert.deepEqual(live.tokenTotal(), { missing: 1 });

// A late server span batch (fetched from trace.json after finalize) merges without duplicating.
const late = {
  seq: 7,
  spanId: "tool_s_late",
  phase: "execution",
  phaseStep: 3,
  actor: "tool",
  side: "server",
  kind: "tool",
  title: "Late tool call",
  status: "ok",
  startedAt: "2026-10-23T00:00:04.000Z",
  detail: { toolName: "late", args: {}, result: {} },
};
assert.equal(store.mergeSpans([late]), 1);
assert.equal(store.list().length, 7);
assert.equal(store.list().at(-1).title, "Late tool call");
assert.equal(store.mergeSpans([late]), 0, "the same span must not merge twice");
assert.equal(store.mergeSpans([{ ...late, seq: 5, spanId: "span_on_taken_seq" }]), 0);
assert.equal(store.list().length, 7);

console.log("store/filters runtime checks passed");
