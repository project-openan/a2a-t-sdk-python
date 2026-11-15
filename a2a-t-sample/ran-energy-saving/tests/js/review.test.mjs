import assert from "node:assert/strict";

import {
  categoryLabel,
  decisionLabel,
  defaultRoundIndex,
  dimensionLabel,
  filterCases,
  progressText,
  protocolLabel,
  roundLabel,
  stabilityOf,
  statusLabel,
} from "../../demo/web/review/main.js";

const CASES = [
  {
    caseId: 16,
    caseName: "设置 gain target",
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
assert.equal(dimensionLabel("final_state"), "终态");

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
assert.equal(defaultRoundIndex([]), 0);
assert.match(roundLabel(ROUNDS[0]), /batch-new · r1 · 2026-11-15 18:22 · qwen3\.8 · 真机 · live · 通过/);

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
