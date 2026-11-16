import assert from "node:assert/strict";

import {
  attentionDimensions,
  batchLabel,
  caseExpectations,
  categoryLabel,
  expectationMarkers,
  filterTaskTRows,
  decisionLabel,
  defaultRoundIndex,
  dimensionLabel,
  dimensionNeedsAttention,
  filterBatches,
  filterCases,
  filterTraceRows,
  messageJudgement,
  missingRoundNotes,
  progressText,
  protocolLabel,
  roundEvidence,
  roundLabel,
  stabilityOf,
  statusLabel,
  textExcerpt,
  traceButtonLabel,
  traceResolutionLabel,
  traceRowNeedsAttention,
  traceSummaryText,
  traceUnmatchedLabel,
} from "../../demo/web/review/main.js";

const CASES = [
  {
    caseId: 16,
    caseName: "设置 gain target",
    blocked: false,
    negotiation: { "a2a-t": false, a2a: true },
    protocols: [
      {
        protocol: "a2a-t",
        status: "fail",
        dimensions: [
          { dimension: "intent", status: "pass" },
          { dimension: "task_t", status: "fail" },
          { dimension: "validate", status: "pass" },
          { dimension: "negotiation", status: "not_expected" },
          { dimension: "final_state", status: "pass" },
        ],
        decision: { status: "defect", category: "sdk", stale: false, newEvidence: false },
      },
      {
        protocol: "a2a",
        status: "not_applicable",
        dimensions: [],
        decision: { status: "pending", category: "" },
      },
    ],
  },
  {
    caseId: 26,
    caseName: "时间重叠 + 可行性协商",
    blocked: true,
    negotiation: { "a2a-t": true, a2a: false },
    protocols: [
      {
        protocol: "a2a-t",
        status: "fail",
        dimensions: [{ dimension: "intent", status: "fail" }],
        decision: { status: "pending", category: "" },
      },
      {
        protocol: "a2a",
        status: "stale",
        dimensions: [{ dimension: "negotiation", status: "missing" }],
        decision: { status: "accepted", category: "", stale: true, newEvidence: false },
      },
    ],
  },
];

assert.equal(statusLabel("pass"), "通过");
assert.equal(statusLabel("partial"), "部分可判");
assert.equal(statusLabel("stale"), "需重跑");
assert.equal(statusLabel(null), "未跑");
assert.equal(decisionLabel("defect"), "缺陷");
assert.equal(decisionLabel(null), "未审核");
assert.equal(categoryLabel("vendor"), "网管");
assert.equal(categoryLabel(""), "—");
assert.equal(protocolLabel("a2a-t"), "A2A-T");
assert.equal(dimensionLabel("task_t"), "Task-T");
assert.equal(dimensionLabel("final_state"), "结果");

assert.equal(
  progressText({
    targets: 200,
    statuses: { pass: 10, fail: 2, partial: 3, stale: 4, not_run: 100 },
    decisions: { accepted: 5, defect: 1, known_issue: 2, recheck: 3 },
  }),
  "协议任务 200 · 通过 10 · 失败 2 · 部分 3 · 需重跑 4 · 未跑 100"
    + " ｜ 已审核 5 · 缺陷 1 · 已知问题 2 · 待复核 3",
);
assert.equal(progressText(null), "");

assert.deepEqual(
  filterCases(CASES, {}).map((row) => [row.caseId, row.protocols.map((task) => task.protocol)]),
  [
    [16, ["a2a-t", "a2a"]],
    [26, ["a2a-t", "a2a"]],
  ],
);
assert.deepEqual(
  filterCases(CASES, { protocol: "a2a" }).map((row) => [row.caseId, row.protocols.length]),
  [
    [16, 1],
    [26, 1],
  ],
);
assert.deepEqual(
  filterCases(CASES, { status: "fail" }).map((row) => row.caseId),
  [16, 26],
);
assert.deepEqual(
  filterCases(CASES, { decision: "defect" }).map((row) => row.caseId),
  [16],
);
assert.deepEqual(
  filterCases(CASES, { category: "sdk" }).map((row) => row.caseId),
  [16],
);
assert.deepEqual(
  filterCases(CASES, { onlyFailures: true }).map((row) => row.caseId),
  [16, 26],
);
assert.deepEqual(
  filterCases(CASES, { onlyPending: true }).map((row) => row.caseId),
  [16, 26],
);
assert.deepEqual(filterCases(CASES, { query: "26" }).map((row) => row.caseId), [26]);

// The static expectation markers and their filter are distinct from the verdict.
assert.deepEqual(
  caseExpectations(CASES[1]),
  { blocked: true, a2aT: true, a2a: false },
);
assert.deepEqual(
  expectationMarkers(CASES[1]).map(([, label]) => label),
  ["拦截", "A2A-T 协商"],
);
assert.deepEqual(
  expectationMarkers(CASES[0]).map(([, label]) => label),
  ["A2A 协商"],
);
assert.deepEqual(expectationMarkers({}), []);
assert.deepEqual(
  filterCases(CASES, { expect: "blocked" }).map((row) => row.caseId),
  [26],
);
assert.deepEqual(
  filterCases(CASES, { expect: "a2a-t" }).map((row) => row.caseId),
  [26],
);
assert.deepEqual(
  filterCases(CASES, { expect: "a2a" }).map((row) => row.caseId),
  [16],
);

// A dimension filter keeps the case and only the protocol tasks failing that point.
assert.deepEqual(
  filterCases(CASES, { dimension: "task_t" }).map((row) => [
    row.caseId,
    row.protocols.map((task) => task.protocol),
  ]),
  [[16, ["a2a-t"]]],
);
assert.deepEqual(
  filterCases(CASES, { dimension: "negotiation" }).map((row) => [
    row.caseId,
    row.protocols.map((task) => task.protocol),
  ]),
  [[26, ["a2a"]]],
);
assert.deepEqual(filterCases(CASES, { dimension: "trace" }).map((row) => row.caseId), []);
assert.equal(dimensionNeedsAttention("pass"), false);
assert.equal(dimensionNeedsAttention("not_applicable"), false);
assert.equal(dimensionNeedsAttention("not_expected"), false);
assert.equal(dimensionNeedsAttention("fail"), true);
assert.equal(dimensionNeedsAttention("partial"), true);
assert.equal(dimensionNeedsAttention("missing"), true);
assert.equal(dimensionNeedsAttention("recorded"), true);
assert.deepEqual(
  attentionDimensions(CASES[0].protocols[0]).map((row) => row.dimension),
  ["task_t"],
);
assert.deepEqual(attentionDimensions(null), []);

const TASK_T_ROWS = [
  { path: "task_t.Operation Type", verdict: "same" },
  { path: "task_t.Task Description", verdict: "recorded" },
  { path: "task_t.Task Context", verdict: "changed" },
  { path: "task_t.Task Target", verdict: "missing" },
];

assert.deepEqual(
  filterTaskTRows(TASK_T_ROWS, true).map((row) => row.path),
  ["task_t.Task Context", "task_t.Task Target"],
);
assert.equal(filterTaskTRows(TASK_T_ROWS, false).length, 4);
assert.deepEqual(filterTaskTRows(null, true), []);

const BATCHES = [
  {
    batchId: "batch-new",
    observedAt: "2026-11-15T18:22:00+00:00",
    model: "qwen3.8",
    nms: true,
    nmsSim: false,
    decisionMode: "live",
    discovery: "nms",
    transport: "webhook",
    rounds: 2,
    cases: 100,
    observations: 200,
    roundsDetail: [
      { round: 1, observations: 120, cases: 100 },
      { round: 2, observations: 80, cases: 60 },
    ],
  },
  {
    batchId: "batch-sim",
    observedAt: "2026-11-14T09:00:00+00:00",
    model: "offline",
    nms: true,
    nmsSim: true,
    decisionMode: "scripted",
    discovery: "nms",
    transport: "sse",
    rounds: 1,
    cases: 40,
    observations: 40,
  },
  {
    batchId: "batch-local",
    observedAt: "2026-11-13T08:00:00+00:00",
    model: "offline",
    nms: false,
    nmsSim: false,
    decisionMode: "offline",
    discovery: "direct",
    transport: "sse",
    rounds: 1,
    cases: 10,
    observations: 10,
  },
];

assert.deepEqual(
  filterBatches(BATCHES, {}).map((batch) => batch.batchId),
  ["batch-new", "batch-sim", "batch-local"],
);
assert.deepEqual(
  filterBatches(BATCHES, { nms: "nms" }).map((batch) => batch.batchId),
  ["batch-new"],
);
assert.deepEqual(
  filterBatches(BATCHES, { nms: "local" }).map((batch) => batch.batchId),
  ["batch-local"],
);
assert.deepEqual(
  filterBatches(BATCHES, { nms: "sim" }).map((batch) => batch.batchId),
  ["batch-sim"],
);
assert.deepEqual(
  filterBatches(BATCHES, { mode: "offline" }).map((batch) => batch.batchId),
  ["batch-local"],
);
assert.deepEqual(
  filterBatches(BATCHES, { query: "2026-11-14" }).map((batch) => batch.batchId),
  ["batch-sim"],
);
assert.deepEqual(
  filterBatches(BATCHES, { query: "QWEN" }).map((batch) => batch.batchId),
  ["batch-new"],
);
assert.equal(
  batchLabel(BATCHES[0]),
  "batch-new · 2026-11-15 18:22 · qwen3.8 · 真机 · live · 2 轮",
);
assert.equal(
  batchLabel(BATCHES[1]),
  "batch-sim · 2026-11-14 09:00 · offline · 模拟器 · scripted · 1 轮",
);
assert.equal(batchLabel(null), "");

// A multi-round batch counts evidence per round, not with the batch-wide total.
assert.deepEqual(roundEvidence(BATCHES[0], 2), { round: 2, observations: 80, cases: 60 });
assert.deepEqual(roundEvidence(BATCHES[0], 9), { round: 9, observations: 0, cases: 0 });
assert.deepEqual(roundEvidence(null, 1), { round: 1, observations: 0, cases: 0 });

const ROUNDS = [
  {
    batchId: "batch-new",
    sourceRound: 1,
    observedAt: "2026-11-15T18:22:00+00:00",
    model: "qwen3.8",
    nms: true,
    decisionMode: "live",
    judgement: { status: "pass", dimensions: [] },
  },
  {
    batchId: "batch-old",
    sourceRound: 2,
    observedAt: "2026-11-14T09:00:00+00:00",
    model: "offline",
    nms: false,
    decisionMode: "scripted",
    judgement: { status: "fail", dimensions: [] },
  },
];
assert.equal(defaultRoundIndex(ROUNDS), 0);
assert.equal(
  defaultRoundIndex([{ ...ROUNDS[1], nms: true, decisionMode: "scripted" }]),
  0,
);
assert.equal(
  defaultRoundIndex([
    { ...ROUNDS[0], nmsSim: true, decisionMode: "live" },
    { ...ROUNDS[0], batchId: "batch-real", nmsSim: false, decisionMode: "live" },
  ]),
  1,
);
assert.equal(defaultRoundIndex([]), 0);
assert.match(roundLabel(ROUNDS[0]), /batch-new · r1 · 2026-11-15 18:22 · qwen3\.8 · 真机 · live · 通过/);
assert.match(roundLabel({ ...ROUNDS[0], nmsSim: true }), /模拟器/);

assert.deepEqual(
  stabilityOf([
    {
      judgement: {
        dimensions: [
          { dimension: "intent", status: "pass" },
          { dimension: "task_t", status: "pass" },
        ],
      },
    },
    {
      judgement: {
        dimensions: [
          { dimension: "intent", status: "pass" },
          { dimension: "task_t", status: "fail" },
        ],
      },
    },
  ]),
  [
    { dimension: "intent", pass: 2, total: 2 },
    { dimension: "task_t", pass: 1, total: 2 },
  ],
);

// The fetch button follows what the round can do, not one failure code: a real-machine round
// with no snapshot must offer 抓取轨迹 instead of a bare "不适用".
assert.equal(
  traceButtonLabel({ canFetch: true, present: false, exportState: "not_exported" }),
  "抓取轨迹",
);
assert.equal(
  traceButtonLabel({ canFetch: true, present: true, exportState: "exported" }),
  "重新抓取",
);
assert.equal(traceButtonLabel({ canFetch: false, present: false }), "");
assert.equal(
  traceButtonLabel({ canFetch: true, present: true, exportState: "failed" }),
  "重试抓取",
);
assert.equal(
  traceButtonLabel({ canFetch: true, present: false, exportState: "failed" }),
  "重试抓取",
);

// Every fetch is explicit: opening a case or a round never spends Langfuse quota on its own.

// Trace / run view: one row per run, a trace id may fan out, and an unjudged row is not a failure.
const TRACE_ROWS = [
  {
    input: "run-a",
    traceId: "run-a",
    runId: "run-a",
    caseId: 16,
    caseName: "设置 gain target",
    protocol: "a2a-t",
    resolution: "store",
    status: "fail",
    dimensions: [
      { dimension: "task_t", status: "fail", reason: "missing target" },
      { dimension: "trace", status: "missing" },
    ],
    trace: { present: false, canFetch: true },
    decision: { status: "pending" },
  },
  {
    input: "trace-b",
    traceId: "trace-b",
    runId: "run-b",
    caseId: 26,
    caseName: "时间重叠",
    protocol: "a2a",
    resolution: "tester",
    status: "pass",
    dimensions: [{ dimension: "trace", status: "pass" }],
    trace: { present: true, canFetch: true },
    decision: { status: "accepted" },
  },
  {
    input: "run-c",
    traceId: "run-c",
    runId: "run-c",
    caseId: null,
    caseName: "",
    protocol: "a2a-t",
    resolution: "client",
    status: "not_run",
    dimensions: [],
    trace: { present: false, canFetch: true },
    decision: { status: "pending" },
  },
];

assert.equal(traceResolutionLabel("store"), "审核库");
assert.equal(traceResolutionLabel("client"), "仅运行");
assert.equal(traceUnmatchedLabel("tester_identity_only"), "仅基线/重试");
assert.equal(traceRowNeedsAttention(TRACE_ROWS[0]), true);
assert.equal(traceRowNeedsAttention(TRACE_ROWS[1]), false);
assert.equal(traceRowNeedsAttention(TRACE_ROWS[2]), false);
assert.deepEqual(
  filterTraceRows(TRACE_ROWS, { dimension: "task_t" }).map((row) => row.runId),
  ["run-a"],
);
assert.deepEqual(
  filterTraceRows(TRACE_ROWS, { status: "attention" }).map((row) => row.runId),
  ["run-a"],
);
assert.deepEqual(
  filterTraceRows(TRACE_ROWS, { status: "unjudged" }).map((row) => row.runId),
  ["run-c"],
);
assert.deepEqual(
  filterTraceRows(TRACE_ROWS, { status: "trace-missing" }).map((row) => row.runId),
  ["run-a", "run-c"],
);
assert.deepEqual(
  filterTraceRows(TRACE_ROWS, { identity: "trace-b" }).map((row) => row.runId),
  ["run-b"],
);
assert.deepEqual(
  filterTraceRows(TRACE_ROWS, { resolution: "tester" }).map((row) => row.runId),
  ["run-b"],
);
assert.deepEqual(filterTraceRows(TRACE_ROWS, { query: "26" }).map((row) => row.runId), ["run-b"]);
assert.match(
  traceSummaryText({
    inputs: 3,
    runs: 3,
    judged: 1,
    unmatched: 1,
    traceMissing: 2,
    ingested: 1,
    dimensions: { task_t: 1, trace: 1 },
  }),
  /输入 3 · run 3 · 可判定 1 · 未命中 1 · 缺轨迹 2 · 写入 1/,
);

// ---------------------------------------------------------------------------
// Long cell values: the table may truncate for scanning, but the full text must stay available.
// ---------------------------------------------------------------------------

const LONG_INTENT = "x".repeat(250);
const longExcerpt = textExcerpt(LONG_INTENT);
assert.equal(longExcerpt.truncated, true);
assert.equal(longExcerpt.full, LONG_INTENT);
assert.equal(longExcerpt.preview.length, 181);
assert.ok(longExcerpt.preview.endsWith("…"));
assert.deepEqual(textExcerpt("short"), { preview: "short", full: "short", truncated: false });
assert.deepEqual(textExcerpt(null), { preview: "—", full: "—", truncated: false });

// ---------------------------------------------------------------------------
// Negotiation messages: every frame is shown, the verdict names the frames it used.
// ---------------------------------------------------------------------------

const MESSAGE_MATCH = {
  matched: false,
  rounds: [
    { round: 1, matched: true, sourceIndex: 0, alternative: 1, reason: "" },
    { round: 2, matched: false, sourceIndex: 2, alternative: null, reason: "缺少业务事实 rate:2" },
    { round: 3, matched: false, sourceIndex: null, alternative: null, reason: "未观测到该轮协商" },
  ],
};
assert.deepEqual(messageJudgement(MESSAGE_MATCH, 0), { judged: true, ok: true, label: "命中备选 2" });
assert.deepEqual(messageJudgement(MESSAGE_MATCH, 1), {
  judged: false,
  ok: null,
  label: "未参与判定（同轮多余报文）",
});
assert.deepEqual(messageJudgement(MESSAGE_MATCH, 2), {
  judged: true,
  ok: false,
  label: "缺少业务事实 rate:2",
});
assert.deepEqual(messageJudgement(null, 0), { judged: false, ok: null, label: "未参与判定" });
assert.deepEqual(missingRoundNotes(MESSAGE_MATCH), ["第 3 轮：未观测到该轮协商"]);
assert.deepEqual(missingRoundNotes(null), []);
