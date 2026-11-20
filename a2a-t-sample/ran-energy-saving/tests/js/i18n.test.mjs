// Runtime checks of the UI copy layer: dictionary parity, lookup, resolution and the guarantee that
// every key referenced by a module or the page shell exists in both languages.

import assert from "node:assert/strict";
import { readFileSync, readdirSync, statSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { EN } from "../../demo/web/static/js/i18n/en.js";
import { ZH_CN } from "../../demo/web/static/js/i18n/zh-CN.js";
import {
  DICTS,
  LOCALES,
  hasKey,
  initLocale,
  localeFromSearch,
  normalizeLocale,
  resolveLocale,
  setLocaleMode,
  t,
  translateTraceTitle,
} from "../../demo/web/static/js/i18n/index.js";

const here = dirname(fileURLToPath(import.meta.url));
const staticDir = join(here, "..", "..", "demo", "web", "static");

/** Flatten nested dictionaries to dotted keys. */
function flatten(value, prefix = "") {
  const out = {};
  for (const [key, child] of Object.entries(value)) {
    const dotted = prefix ? `${prefix}.${key}` : key;
    if (child && typeof child === "object" && !Array.isArray(child)) {
      Object.assign(out, flatten(child, dotted));
    } else {
      out[dotted] = child;
    }
  }
  return out;
}

const en = flatten(EN);
const zh = flatten(ZH_CN);
const enKeys = Object.keys(en).sort();
const zhKeys = Object.keys(zh).sort();

// 1. Both dictionaries carry exactly the same keys.
assert.deepEqual(zhKeys, enKeys, "en.js and zh-CN.js must declare the same keys");

// 2. Every value is a non-empty string, and Chinese copy is actually Chinese.
for (const key of enKeys) {
  assert.equal(typeof en[key], "string", `${key} must be a string`);
  assert.equal(typeof zh[key], "string", `${key} must be a string`);
  assert.ok(en[key].trim().length > 0, `${key} must not be empty`);
  assert.ok(zh[key].trim().length > 0, `${key} must not be empty`);
}
for (const key of ["phase.dispatch", "inspector.story", "history.title", "rail.submit"]) {
  assert.match(zh[key], /[一-鿿]/, `${key} should be translated to Chinese`);
}

// 3. Locales and dictionary registry agree.
assert.deepEqual(LOCALES, ["en", "zh-CN"]);
assert.equal(DICTS.en, EN);
assert.equal(DICTS["zh-CN"], ZH_CN);

// 4. Default lookup is English, with placeholders interpolated.
assert.equal(t("inspector.story"), "Story");
assert.equal(t("header.run", { id: "abc123" }), "Run abc123");
assert.equal(t("missing.key"), "missing.key", "a missing key stays visible");

// 5. Language tag normalization and `?lang=` parsing.
assert.equal(normalizeLocale("zh"), "zh-CN");
assert.equal(normalizeLocale("zh-CN"), "zh-CN");
assert.equal(normalizeLocale("en-US"), "en");
assert.equal(normalizeLocale("fr"), null);
assert.equal(localeFromSearch("?lang=zh"), "zh-CN");
assert.equal(localeFromSearch("?lang=bogus"), null);
assert.equal(localeFromSearch(""), null);
assert.equal(resolveLocale("en", "zh-CN"), "en", "an explicit locale wins");
assert.equal(resolveLocale("auto", "zh-CN"), "zh-CN", "auto follows the process language");
assert.ok(
  LOCALES.includes(resolveLocale("auto", null)),
  "without a process language auto falls back to the browser language, then English",
);

// 6. Switching updates lookup and can be initialized from the URL.
initLocale({ search: "?lang=zh-CN" });
assert.equal(t("phase.dispatch"), "派发");
assert.equal(t("header.run", { id: "x" }), "运行 x");
setLocaleMode("en");
assert.equal(t("phase.dispatch"), "Dispatch");

// 7. Trace titles localize known narrative patterns only.
setLocaleMode("zh-CN");
assert.equal(translateTraceTitle("Task-T sent"), "Task-T 已发送");
assert.equal(
  translateTraceTitle("Decision requested · negotiation R2"),
  "请求决策 · negotiation R2",
);
assert.equal(translateTraceTitle("LLM call · server_planner"), "LLM 调用 · server_planner");
assert.equal(translateTraceTitle("get_cell_energy_profile"), "get_cell_energy_profile");
assert.equal(translateTraceTitle("Late tool call"), "Late tool call");
setLocaleMode("en");

// 8. Every key referenced by the page shell or a module exists in both dictionaries.
function walk(dir) {
  const files = [];
  for (const name of readdirSync(dir)) {
    const path = join(dir, name);
    if (statSync(path).isDirectory()) files.push(...walk(path));
    else if (name.endsWith(".js")) files.push(path);
  }
  return files;
}

const referenced = new Set();
for (const file of walk(join(staticDir, "js"))) {
  const source = readFileSync(file, "utf8");
  for (const match of source.matchAll(/\bt\("([\w.-]+)"/g)) referenced.add(match[1]);
}
const page = readFileSync(join(staticDir, "index.html"), "utf8");
for (const match of page.matchAll(/data-i18n(?:-title|-placeholder|-aria-label)?="([\w.-]+)"/g)) {
  referenced.add(match[1]);
}
assert.ok(referenced.size > 100, `expected a broad copy inventory, saw ${referenced.size}`);
for (const key of [...referenced].sort()) {
  assert.ok(hasKey(key), `missing dictionary key: ${key}`);
}

console.log(`i18n runtime checks passed (${enKeys.length} keys, ${referenced.size} referenced)`);
