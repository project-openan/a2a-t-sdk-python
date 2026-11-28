// Tester copy-layer checks (no DOM, no network).
//
// Run with: node tests/js/tester_i18n.test.mjs
// The Python suite shells out to this file and skips cleanly when Node is absent.

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import {
  COPY,
  copyFor,
  localizeValidateLabel,
  t,
} from "../../demo/web/tester/copy.js";

const here = dirname(fileURLToPath(import.meta.url));
const testerDir = resolve(here, "../../demo/web/tester");
const shell = readFileSync(resolve(testerDir, "index.html"), "utf8");

const zh = COPY["zh-CN"];
const en = COPY["en-US"];

assert.ok(zh, "the Chinese copy table exists");
assert.ok(en, "the English copy table exists");

const zhKeys = Object.keys(zh).sort();
const enKeys = Object.keys(en).sort();
assert.deepEqual(enKeys, zhKeys, "both languages define exactly the same keys");

const dataKeys = [...shell.matchAll(/data-i18n="([^"]+)"/g)].map((match) => match[1]);
assert.ok(dataKeys.length > 0, "the shell binds static copy through data-i18n");
for (const key of dataKeys) {
  assert.ok(key in zh, `zh-CN defines the shell key ${key}`);
  assert.ok(key in en, `en-US defines the shell key ${key}`);
}

assert.equal(copyFor("zh-CN"), zh);
assert.equal(copyFor("en-US"), en);
assert.equal(copyFor("fr-FR"), zh, "unknown languages fall back to the template language");
assert.equal(copyFor(""), zh);

assert.equal(t(zh, "toast.selected", { count: 3 }), "已选择 3 条用例");
assert.equal(t(en, "toast.selected", { count: 3 }), "3 cases selected");
assert.equal(t(en, "missing.key"), "missing.key", "an unknown key stays visible");

assert.equal(localizeValidateLabel(en, "拦截"), "blocked");
assert.equal(localizeValidateLabel(en, "不拦截，携带术语上下文信息"), "not blocked (terminology context carried)");
assert.equal(localizeValidateLabel(en, "不拦截"), "not blocked");
assert.equal(localizeValidateLabel(zh, "拦截"), "拦截");
assert.equal(localizeValidateLabel(en, ""), "");
assert.equal(localizeValidateLabel(en, "unknown label"), "unknown label");

for (const [key, value] of Object.entries(en)) {
  assert.equal(typeof value, "string", `en-US ${key} is a string`);
  assert.ok(!/[一-鿿]/.test(value), `en-US ${key} contains no CJK text`);
}

console.log("tester i18n harness: ok");
