import assert from "node:assert/strict";

import {
  filterTargets,
  groupByRound,
  progressText,
  statusLabel,
} from "../../demo/web/annotate/main.js";

const targets = [
  { caseId: 16, caseName: "gain target", protocol: "a2a-t", dimension: "slot_errors", status: "pending" },
  { caseId: 28, caseName: "时间重叠", protocol: "a2a-t", dimension: "messages", status: "confirmed" },
  { caseId: 28, caseName: "时间重叠", protocol: "a2a", dimension: "messages", status: "applied" },
];

assert.equal(statusLabel("draft"), "草稿");
assert.equal(statusLabel("applied"), "已应用");
assert.equal(statusLabel(null), "未采集");

assert.deepEqual(
  filterTargets(targets, {}).map((target) => target.caseId),
  [16, 28, 28],
);
assert.deepEqual(
  filterTargets(targets, { dimension: "messages" }).map((target) => target.protocol),
  ["a2a-t", "a2a"],
);
assert.deepEqual(
  filterTargets(targets, { protocol: "a2a" }).map((target) => target.caseId),
  [28],
);
assert.deepEqual(
  filterTargets(targets, { status: "pending" }).map((target) => target.caseId),
  [16],
);
assert.deepEqual(
  filterTargets(targets, { query: "28" }).map((target) => target.protocol),
  ["a2a-t", "a2a"],
);
assert.deepEqual(
  filterTargets(targets, { query: "时间" }).length,
  2,
);

const grouped = groupByRound([
  { id: 2, round: 2 },
  { id: 1, round: 1 },
  { id: 3, round: 1 },
]);
assert.deepEqual(
  grouped.map(([round, rows]) => [round, rows.length]),
  [
    [1, 2],
    [2, 1],
  ],
);
assert.deepEqual(groupByRound([]).length, 0);

assert.equal(
  progressText({ counts: { total: 151, pending: 149, draft: 0, confirmed: 2, applied: 0 } }),
  "目标 151 · 未采集 149 · 草稿 0 · 已确认 2 · 已应用 0",
);
assert.equal(progressText(null), "");
