// Replays a real, complete SSE frame list through the Run Explorer store.
//
// Run with: node tests/js/frames.test.mjs <frames.json>
// The Python integration test writes the payloads of a real offline run to a temporary file and
// shells out to this harness, so the JS contract is exercised against produced frames, not a mock.
// The harness only asserts invariants that hold for any successful run (not exact model counts).

import assert from "node:assert/strict";
import fs from "node:fs";

import { RunStore } from "../../demo/web/static/js/store.js";

const framesPath = process.argv[2];
assert.ok(framesPath, "usage: node tests/js/frames.test.mjs <frames.json>");

const frames = JSON.parse(fs.readFileSync(framesPath, "utf8"));
assert.ok(Array.isArray(frames) && frames.length > 0, "frames must be a non-empty list");
assert.equal(frames[0].type, "run_started", "the first frame must be run_started");

const store = new RunStore();
const first = frames[0];
store.setMeta({
  language: first.language,
  modelMode: first.modelMode,
  modelLabel: first.modelLabel,
});

for (const frame of frames) {
  assert.equal(store.applyFrame(frame), true, `frame ${frame.seq} must be new`);
}
for (const frame of frames) {
  assert.equal(store.applyFrame(frame), false, `duplicate ${frame.seq} must be ignored`);
}

const items = store.list();
assert.equal(items.length, frames.length);
assert.deepEqual(
  items.map((item) => item.number),
  items.map((_, index) => index + 1),
);
const seqs = [...store.frames.values()].map((frame) => frame.seq);
assert.deepEqual(
  seqs,
  [...seqs].sort((left, right) => left - right),
  "frames must be stored in seq order",
);
assert.equal(store.status, "finished");

const derived = store.derived();
assert.equal(derived.groups.length, 8);
for (const group of derived.groups) assert.ok(group.items.length > 0, `${group.key} must have items`);

// The report card holds only the report artifact and the COMPLETED status; per-tool artifacts
// belong to Execution.
const reportGroup = derived.groups.find((group) => group.key === "report");
assert.equal(reportGroup.items.length, 2, "report phase must have exactly two rows");
assert.deepEqual(
  reportGroup.items.map((item) => item.kind).sort(),
  ["artifact", "status"],
);
const executionArtifacts = derived.groups
  .find((group) => group.key === "execution")
  .items.filter((item) => item.kind === "artifact");
assert.ok(executionArtifacts.length > 0, "tool artifacts must be execution rows");

// The finished frame carries the terminal span start; the Done card must render a duration.
const finishedItem = items.at(-1);
assert.equal(finishedItem.entryType, "finished");
assert.ok(finishedItem.startedAt, "the finished row must keep its startedAt");
const doneGroup = derived.groups.find((group) => group.key === "done");
assert.ok(doneGroup, "the Done group must exist");
assert.notEqual(doneGroup.stats.durationMs, null, "the Done phase must have a duration");

const llm = items.filter((item) => item.kind === "llm");
const tools = items.filter((item) => item.kind === "tool");
assert.ok(llm.length > 0, "the run must contain LLM spans");
assert.ok(tools.length > 0, "the run must contain tool spans");
assert.equal(derived.analysis.llmCalls, llm.length);
assert.equal(derived.analysis.tools, tools.length);
assert.equal(derived.analysis.decisions, 1, "the information run makes one decision");
assert.equal(derived.analysis.numericCalls, 0, "offline usage is never reported as a number");
assert.equal(derived.analysis.scriptedCalls, llm.length);
assert.equal(store.tokenTotal().scripted, llm.length);
assert.equal(derived.decisionLog[0].source, "scripted");
assert.equal(store.resolutionLabel(), "Accepted");
assert.ok(store.ids().contextId, "the run must expose a context id");

// Nested spans: every tool resolves its parent row and is linked as a child.
for (const tool of tools) {
  assert.ok(tool.parentSpanId, `tool ${tool.spanId} must be nested`);
  const parent = store.getItemBySpan(tool.parentSpanId);
  assert.ok(parent, `tool ${tool.spanId} parent should resolve`);
  assert.ok(store.childrenByKey.get(parent.key)?.includes(tool), `tool ${tool.spanId} should be a child`);
}

// Both LLM producer shapes carry their request/response payloads.
const adk = llm.find((item) => item.stage === "client_decision_agent");
assert.ok(adk, "the ADK decision call must be traced");
assert.ok(adk.detail.request.contents && adk.detail.request.systemInstruction);
assert.ok(adk.detail.response.text);
const sdk = llm.find((item) => item.stage === "task_t_slot_extraction");
assert.ok(sdk, "the SDK slot-extraction call must be traced");
assert.ok(sdk.detail.request.messages && sdk.detail.request.jsonSchema);
assert.ok(sdk.detail.response.rawContent && sdk.detail.response.parsed);

console.log(
  "real-frame store checks passed:",
  items.length,
  "rows,",
  derived.analysis.llmCalls,
  "LLM,",
  derived.analysis.tools,
  "tools",
);
