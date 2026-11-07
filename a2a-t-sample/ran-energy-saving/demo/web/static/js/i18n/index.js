/** UI copy runtime: locale resolution, dictionary lookup and static binding.
 *
 * No build step and no fetch: both dictionaries are ES modules, so the page switches language
 * instantly and the Node harnesses can import them directly.
 *
 * Resolution order for the initial mode is ``?lang=`` → ``localStorage`` → process language
 * (``/api/meta``) → the browser language → ``en``. ``auto`` means "follow the process language",
 * so the interface matches the language the backend already used for scenario templates.
 */

import { EN } from "./en.js";
import { ZH_CN } from "./zh-CN.js";

/** Dictionary of every supported locale. */
export const DICTS = { en: EN, "zh-CN": ZH_CN };

/** Locale codes the UI can display. */
export const LOCALES = Object.keys(DICTS);

/** ``auto`` plus one explicit locale per mode the menu offers. */
export const LOCALE_MODES = ["auto", ...LOCALES];

/** localStorage key of the explicit preference (``auto`` included). */
export const LOCALE_STORAGE_KEY = "a2at-ran-locale";

const LANGUAGE_ALIASES = {
  en: "en",
  "en-us": "en",
  "en-gb": "en",
  zh: "zh-CN",
  "zh-cn": "zh-CN",
  "zh-hans": "zh-CN",
  "zh-sg": "zh-CN",
  "zh-tw": "zh-CN",
  "zh-hk": "zh-CN",
};

/** Static bindings: attribute carrying the key → attribute it writes. */
const STATIC_ATTRIBUTES = [
  ["data-i18n-title", "title"],
  ["data-i18n-placeholder", "placeholder"],
  ["data-i18n-aria-label", "aria-label"],
];

let mode = "auto";
let processLanguage = null;
let locale = "en";
const listeners = new Set();

/** Normalize a language tag to one supported locale, ``null`` when unsupported. */
export function normalizeLocale(value) {
  if (typeof value !== "string") return null;
  return LANGUAGE_ALIASES[value.trim().toLowerCase()] || null;
}

/** Normalize one preference (``auto`` or a supported locale), ``null`` when invalid. */
export function normalizeMode(value) {
  if (value === "auto") return "auto";
  return normalizeLocale(value);
}

/** Return the browser's first supported language, ``null`` outside a browser. */
function browserLocale() {
  if (typeof navigator === "undefined") return null;
  const list =
    Array.isArray(navigator.languages) && navigator.languages.length > 0
      ? navigator.languages
      : [navigator.language];
  for (const item of list) {
    const normalized = normalizeLocale(item);
    if (normalized) return normalized;
  }
  return null;
}

/** Resolve the effective locale of one mode. */
export function resolveLocale(modeValue, processValue = null) {
  if (modeValue !== "auto") return modeValue;
  return normalizeLocale(processValue) || browserLocale() || "en";
}

/** Return the locale currently used for rendering. */
export function getLocale() {
  return locale;
}

/** Return the preference currently selected (``auto`` or an explicit locale). */
export function getLocaleMode() {
  return mode;
}

/** Return the process-level language reported by ``/api/meta``, if any. */
export function getProcessLanguage() {
  return processLanguage;
}

/** Read the stored preference, ``null`` when storage is unavailable or empty. */
export function readStoredMode() {
  try {
    return normalizeMode(localStorage.getItem(LOCALE_STORAGE_KEY));
  } catch {
    return null;
  }
}

/** Persist the current preference, ignoring private-browsing failures. */
function persistMode() {
  try {
    localStorage.setItem(LOCALE_STORAGE_KEY, mode);
  } catch {
    // Storage failures only cost the preference across reloads.
  }
}

/** Recompute the effective locale and notify every listener on change. */
function recompute({ notify = true } = {}) {
  const previous = locale;
  locale = resolveLocale(mode, processLanguage);
  if (typeof document !== "undefined") {
    document.documentElement.lang = locale;
    applyStatic(document);
  }
  if (notify && previous !== locale) {
    for (const listener of listeners) listener(locale);
  }
}

/** Read ``?lang=`` from one query string, ``null`` when absent or unsupported. */
export function localeFromSearch(search) {
  try {
    const raw = new URLSearchParams(search).get("lang");
    return raw ? normalizeMode(raw) : null;
  } catch {
    return null;
  }
}

/**
 * Resolve the initial preference and apply it to the document.
 *
 * @param {object} options ``search`` query string and the process language from ``/api/meta``.
 * @returns {string} the effective locale.
 */
export function initLocale({ search = "", processLanguage: process = null } = {}) {
  processLanguage = typeof process === "string" && process ? process : null;
  mode = localeFromSearch(search) || readStoredMode() || "auto";
  recompute({ notify: false });
  return locale;
}

/** Select one preference (``auto``, ``en`` or ``zh-CN``); returns ``false`` when unsupported. */
export function setLocaleMode(value) {
  const next = normalizeMode(value);
  if (!next) return false;
  mode = next;
  persistMode();
  recompute();
  return true;
}

/** Record the process language; ``auto`` mode follows it. */
export function setProcessLanguage(value) {
  processLanguage = typeof value === "string" && value ? value : null;
  recompute();
}

/** Subscribe to locale changes; returns an unsubscribe function. */
export function onLocaleChange(listener) {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

/** Look up a dotted key in one dictionary. */
function lookup(dict, key) {
  let node = dict;
  for (const part of String(key).split(".")) {
    if (!node || typeof node !== "object") return undefined;
    node = node[part];
  }
  return node;
}

/** Whether one key exists in every dictionary. */
export function hasKey(key) {
  return LOCALES.every((code) => typeof lookup(DICTS[code], key) === "string");
}

/** Interpolate ``{name}`` placeholders; unknown placeholders stay visible. */
function interpolate(text, vars) {
  return text.replace(/\{(\w+)\}/g, (match, name) =>
    Object.prototype.hasOwnProperty.call(vars, name) ? String(vars[name]) : match,
  );
}

/**
 * Return one localized string.
 *
 * A missing key falls back to English, then to the key itself: a typo is visible during review
 * instead of silently rendering an empty node.
 */
export function t(key, vars = null) {
  const value = lookup(DICTS[locale], key) ?? lookup(EN, key);
  if (typeof value !== "string") return String(key);
  return vars ? interpolate(value, vars) : value;
}

/** Apply every static binding under one root. */
export function applyStatic(root = typeof document !== "undefined" ? document : null) {
  if (!root || typeof root.querySelectorAll !== "function") return;
  for (const node of root.querySelectorAll("[data-i18n]")) {
    node.textContent = t(node.getAttribute("data-i18n"));
  }
  for (const [source, target] of STATIC_ATTRIBUTES) {
    for (const node of root.querySelectorAll(`[${source}]`)) {
      node.setAttribute(target, t(node.getAttribute(source)));
    }
  }
}

/** Return the menu label of one preference. */
export function localeLabel(value = mode) {
  return value === "auto" ? t("locale.auto") : t(`locale.${value}`);
}

const TRACE_TITLE_PATTERNS = [
  [/^Run started$/, () => t("trace.runStarted")],
  [/^Run finished$/, () => t("trace.runFinished")],
  [/^Run failed$/, () => t("trace.runFailed")],
  [/^Task-T prompt rendered$/, () => t("trace.taskPromptRendered")],
  [/^Task-T sent$/, () => t("trace.taskTSent")],
  [/^Decision requested · (.+?) R(\d+)$/, (match) => t("trace.decisionRequested", { kind: match[1], round: match[2] })],
  [/^Decision made · (.+)$/, (match) => t("trace.decisionMade", { action: match[1] })],
  [/^Decision · (.+)$/, (match) => t("trace.decisionEntry", { action: match[1] })],
  [/^Client runtime ready$/, () => t("trace.clientRuntimeReady")],
  [/^Server discovered$/, () => t("trace.serverDiscovered")],
  [/^Planner turn$/, () => t("trace.plannerTurn")],
  [/^LLM call · (.+)$/, (match) => t("trace.llmCall", { stage: match[1] })],
  [/^Tool call · (.+)$/, (match) => t("trace.toolCall", { tool: match[1] })],
  [/^RAN NMS task execution$/, () => t("trace.nmsExecution")],
  [/^LangGraph OSS task execution$/, () => t("trace.nmsExecution")],
];

/**
 * Localize a narrative title produced by the backend trace.
 *
 * Tool names, scenario text and protocol identifiers are not patterns here: they are data and stay
 * exactly as recorded. An unknown title is returned unchanged, so a future backend string degrades
 * to English instead of disappearing.
 */
export function translateTraceTitle(title) {
  if (typeof title !== "string" || !title) return title;
  for (const [pattern, render] of TRACE_TITLE_PATTERNS) {
    const match = pattern.exec(title);
    if (match) return render(match);
  }
  return title;
}
