import assert from "node:assert/strict";

import {
  compactObject,
  formatDuration,
  phaseLabel,
} from "../../demo/web/runs_console/main.js";

assert.equal(phaseLabel("negotiation"), "协商");
assert.equal(phaseLabel("task_validation"), "校验");
assert.equal(phaseLabel("custom-phase"), "custom-phase");
assert.equal(phaseLabel(undefined), "未知");

assert.equal(formatDuration(900), "900 ms");
assert.equal(formatDuration(1500), "1.5 s");
assert.equal(formatDuration(65000), "1m 5s");

assert.equal(compactObject({ a: 1, b: 2 }), "对象 · 2 个字段");
assert.equal(compactObject([1, 2, 3]), "数组 · 3 项");
assert.equal(compactObject("plain"), "plain");
assert.equal(compactObject(null), "—");
