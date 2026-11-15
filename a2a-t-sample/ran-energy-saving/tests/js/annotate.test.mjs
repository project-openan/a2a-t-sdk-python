import assert from "node:assert/strict";

import {
  defaultRunKey,
  filterCases,
  filterObservations,
  groupByRound,
  parseTraceIds,
  progressText,
  protocolLabel,
  runKey,
  runOptions,
  statusLabel,
  subtaskLabel,
  subtasksOf,
  traceNotice,
} from "../../demo/web/annotate/main.js";

const CASES = [
  {
    caseId: 16,
    caseName: "设置 gain target",
    operation: "Create",
    protocols: [
      {
        protocol: "a2a-t",
        status: "pending",
        subtasks: [
          { dimension: "slot_errors", status: "pending", observations: 2 },
        ],
      },
      { protocol: "a2a", status: "none", subtasks: [] },
    ],
  },
  {
    caseId: 26,
    caseName: "时间重叠 + 可行性协商",
    operation: "Modify",
    protocols: [
      {
        protocol: "a2a-t",
        status: "confirmed",
        subtasks: [
          { dimension: "slot_errors", status: "confirmed", observations: 3 },
          { dimension: "messages", status: "confirmed", observations: 2 },
        ],
      },
      {
        protocol: "a2a",
        status: "draft",
        subtasks: [{ dimension: "messages", status: "draft", observations: 1 }],
      },
    ],
  },
  {
    caseId: 50,
    caseName: "待黄区跑",
    operation: "Modify",
    protocols: [
      {
        protocol: "a2a-t",
        status: "pending",
        subtasks: [{ dimension: "messages", status: "pending", observations: 0 }],
      },
      { protocol: "a2a", status: "none", subtasks: [] },
    ],
  },
  {
    caseId: 45,
    caseName: "双协议都不协商",
    operation: "Create",
    protocols: [
      { protocol: "a2a-t", status: "none", subtasks: [] },
      { protocol: "a2a", status: "none", subtasks: [] },
    ],
  },
];

assert.equal(statusLabel("draft"), "草稿");
assert.equal(statusLabel("none"), "无需标注");
assert.equal(statusLabel(null), "未采集");
assert.equal(protocolLabel("a2a-t"), "A2A-T");
assert.equal(protocolLabel("a2a"), "A2A");
assert.equal(subtaskLabel("slot_errors"), "拦截 slot_errors");
assert.equal(subtaskLabel("messages"), "协商报文");

assert.deepEqual(
  subtasksOf(CASES[1], "a2a").map((row) => row.dimension),
  ["messages"],
);
assert.deepEqual(subtasksOf(CASES[0], "a2a"), []);

// hideNone on: the second protocol of case 16 and the whole case 45 disappear.
assert.deepEqual(
  filterCases(CASES, { hideNone: true }).map((row) => [
    row.caseId,
    row.protocols.map((task) => task.protocol),
  ]),
  [
    [16, ["a2a-t"]],
    [26, ["a2a-t", "a2a"]],
    [50, ["a2a-t"]],
  ],
);
// hideNone off keeps the "无需标注" tasks.
assert.deepEqual(
  filterCases(CASES, { hideNone: false }).map((row) => row.caseId),
  [16, 26, 50, 45],
);
assert.deepEqual(
  filterCases(CASES, { protocol: "a2a", hideNone: true }).map((row) => row.caseId),
  [26],
);
assert.deepEqual(
  filterCases(CASES, { subtask: "messages", hideNone: true }).map((row) => row.caseId),
  [26, 50],
);
assert.deepEqual(
  filterCases(CASES, { status: "confirmed", hideNone: true }).map((row) => row.caseId),
  [26],
);
assert.deepEqual(
  filterCases(CASES, { candidates: "without", hideNone: false }).map((row) => row.caseId),
  [50],
);
assert.deepEqual(
  filterCases(CASES, { query: "26" }).map((row) => row.caseId),
  [26],
);
assert.deepEqual(
  filterCases(CASES, { query: "时间" }).map((row) => row.caseId),
  [26],
);

const OLD = {
  id: 1,
  round: 1,
  sourceBatch: "batch-old",
  sourceRound: 1,
  observedAt: "2026-11-14T09:00:00+00:00",
  model: "offline",
  nms: false,
  decisionMode: "scripted",
  consistent: true,
};
const LIVE = {
  id: 2,
  round: 1,
  sourceBatch: "batch-live",
  sourceRound: 1,
  observedAt: "2026-11-15T18:22:00+00:00",
  model: "qwen3.8",
  nms: true,
  decisionMode: "live",
  consistent: true,
};
const LIVE_DIVERGENT = { ...LIVE, id: 3, consistent: false };

assert.equal(runKey(LIVE), "batch-live#1");

const options = runOptions([OLD, LIVE, LIVE_DIVERGENT, { ...LIVE, id: 4 }]);
assert.deepEqual(
  options.map((option) => option.key),
  ["batch-live#1", "batch-old#1"],
);
assert.equal(options[0].count, 3);
assert.equal(options[0].nms, true);
assert.equal(defaultRunKey(options), "batch-live#1");
assert.equal(
  defaultRunKey(runOptions([{ ...OLD, nms: true, decisionMode: "scripted" }])),
  "batch-old#1",
);
assert.equal(defaultRunKey([]), "");

assert.deepEqual(
  filterObservations([OLD, LIVE, LIVE_DIVERGENT], { run: "", nms: true, live: true, consistent: true })
    .map((row) => row.id),
  [2],
);
assert.deepEqual(
  filterObservations([OLD, LIVE, LIVE_DIVERGENT], { run: "", consistent: false }).map((row) => row.id),
  [1, 2, 3],
);
assert.deepEqual(
  filterObservations([OLD, LIVE], { run: "batch-old#1" }).map((row) => row.id),
  [1],
);

assert.deepEqual(
  groupByRound([
    { id: 2, round: 2 },
    { id: 1, round: 1 },
    { id: 3, round: 1 },
  ]).map(([round, rows]) => [round, rows.length]),
  [
    [1, 2],
    [2, 1],
  ],
);

assert.equal(
  progressText({ counts: { total: 151, pending: 149, draft: 0, confirmed: 2 } }),
  "子任务 151 · 未采集 149 · 草稿 0 · 已确认 2",
);
assert.equal(progressText(null), "");

assert.deepEqual(parseTraceIds(" A1\nb2, A1，c3;；d4 "), ["a1", "b2", "c3", "d4"]);
assert.deepEqual(parseTraceIds(""), []);
assert.deepEqual(parseTraceIds(null), []);

assert.equal(
  traceNotice({
    requested: 3,
    matched: ["a1", "b2"],
    unknown: ["c3"],
    batches: 4,
    observations: 9,
    confirm: true,
    adopted: [{ caseId: 16 }, { caseId: 26 }],
    skipped: [
      { reason: "inconsistent" },
      { reason: "already_adopted" },
      { reason: "already_adopted" },
    ],
  }),
  "匹配 2/3 个标识；扫描 4 个批次、9 条观测；采纳 2 个子任务（已确认）；"
    + "跳过 无一致候选 1，已有采纳 2；未找到 c3",
);
assert.equal(traceNotice(null), "");
