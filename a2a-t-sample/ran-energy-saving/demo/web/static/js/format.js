/** Display formatting helpers shared by every Run Explorer view.
 *
 * Everything here is pure: no DOM access, no global state. Missing values render as a neutral
 * em dash so the UI never invents a zero for data the backend did not record (offline token usage
 * in particular must read ``n/a (scripted)``).
 */

import { t } from "./i18n/index.js";

export const EM_DASH = "—";

/** Return a finite number, `null` otherwise. */
export function asNumber(value) {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

/** Format an integer with grouped thousands. */
export function formatNumber(value) {
  const number = asNumber(value);
  return number === null ? EM_DASH : number.toLocaleString("en-US");
}

/** Format a duration in milliseconds as a compact human value. */
export function formatDuration(ms) {
  const value = asNumber(ms);
  if (value === null) return EM_DASH;
  if (value < 1) return "<1 ms";
  if (value < 1000) return `${Math.round(value)} ms`;
  if (value < 60000) return `${(value / 1000).toFixed(value < 10000 ? 1 : 0)} s`;
  return `${Math.floor(value / 60000)}m ${Math.round((value % 60000) / 1000)}s`;
}

/** Format a wall-clock duration in milliseconds as ``mm:ss.s``. */
export function formatClock(ms) {
  const value = Math.max(0, asNumber(ms) ?? 0);
  const minutes = Math.floor(value / 60000);
  const seconds = Math.floor((value % 60000) / 1000);
  const tenths = Math.floor((value % 1000) / 100);
  return `${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}.${tenths}`;
}

/** Format a decision countdown in seconds as ``m:ss``. */
export function formatCountdown(seconds) {
  const value = Math.max(0, Math.ceil(asNumber(seconds) ?? 0));
  return `${Math.floor(value / 60)}:${String(value % 60).padStart(2, "0")}`;
}

/** Return the scripted label carried by an offline token map. */
export function scriptedLabel(tokens) {
  if (!tokens || typeof tokens !== "object") return null;
  if (typeof tokens.label === "string" && tokens.label) return tokens.label;
  if (tokens.scripted) return t("value.notAvailableScripted");
  return null;
}

/** Format prompt → completion token usage of one span.
 *
 * ``scripted`` makes an absent token map read ``n/a (scripted)`` instead of an em dash: offline ADK
 * spans have no usage metadata, and a zero would misrepresent a deterministic model.
 */
export function formatTokens(tokens, { scripted = false } = {}) {
  if (!tokens || typeof tokens !== "object") {
    return scripted ? t("value.notAvailableScripted") : EM_DASH;
  }
  const label = scriptedLabel(tokens);
  if (label) return label;
  const prompt = asNumber(tokens.prompt);
  const completion = asNumber(tokens.completion);
  if (prompt === null && completion === null) {
    return scripted ? t("value.notAvailableScripted") : EM_DASH;
  }
  return `${formatNumber(prompt ?? 0)}→${formatNumber(completion ?? 0)} tok`;
}

/** Format the token total shown in the header and analysis panel. */
export function formatTokenTotal(tokens) {
  if (!tokens || typeof tokens !== "object") return EM_DASH;
  const label = scriptedLabel(tokens);
  if (label) return label;
  const total = asNumber(tokens.total);
  if (total === null) return EM_DASH;
  return `${formatNumber(total)} tok`;
}

/** Shorten a model id for dense rows while keeping its identity readable. */
export function shortModel(model) {
  if (typeof model !== "string" || !model) return EM_DASH;
  if (model === "scripted") return "scripted";
  const trimmed = model.includes("/") ? model.split("/").slice(-1)[0] : model;
  return trimmed.length > 24 ? `${trimmed.slice(0, 22)}…` : trimmed;
}

/** Format an ISO timestamp as a local wall-clock time, `EM_DASH` when invalid. */
export function formatTime(iso) {
  if (typeof iso !== "string" || !iso) return EM_DASH;
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return EM_DASH;
  return date.toLocaleTimeString("en-US", { hour12: false });
}

/** Return milliseconds elapsed between two ISO timestamps, `null` when unusable. */
export function elapsedBetween(startIso, endIso) {
  const start = Date.parse(startIso);
  const end = Date.parse(endIso);
  if (Number.isNaN(start) || Number.isNaN(end)) return null;
  return Math.max(0, end - start);
}

/** Pretty-print one JSON-ish value in full, tolerating cycles and unsupported values.
 *
 * This is the serializer used by copy and download: payload strings are never capped, because the
 * backend allows 256 KB request/response payloads and a silently shortened export would be wrong.
 */
export function formatJson(value, indent = 2) {
  if (value === undefined) return "undefined";
  try {
    return JSON.stringify(value, jsonReplacer(), indent);
  } catch {
    return String(value);
  }
}

/** Pretty-print one JSON-ish value for on-screen display.
 *
 * Strings inside the payload stay intact (the JSON tree already marks its own string cut-offs);
 * only the whole rendered document is capped, with a visible marker explaining that copy/download
 * returns the complete payload.
 */
export function formatJsonForDisplay(value, { indent = 2, maxChars = 120000 } = {}) {
  const text = formatJson(value, indent);
  if (text.length <= maxChars) return text;
  const headLength = Math.floor(maxChars * 0.6);
  const tailLength = Math.max(0, maxChars - headLength);
  const omitted = text.length - headLength - tailLength;
  return (
    `${text.slice(0, headLength)}\n` +
    `${t("format.displayTruncated", { count: formatNumber(omitted) })}\n` +
    text.slice(text.length - tailLength)
  );
}

/** Build a cycle-safe replacer (long strings are intentionally kept intact). */
function jsonReplacer() {
  const seen = new WeakSet();
  return (key, value) => {
    if (value && typeof value === "object") {
      if (seen.has(value)) return "[circular]";
      seen.add(value);
    }
    return value;
  };
}

/** Format one arbitrary value as compact text for facts and summaries. */
export function formatValue(value) {
  if (value === null || value === undefined || value === "") return EM_DASH;
  if (typeof value === "string") return value;
  if (typeof value === "number") return formatNumber(value);
  if (typeof value === "boolean") return value ? "true" : "false";
  if (Array.isArray(value)) {
    return value.length === 1
      ? t("format.itemOne", { count: formatNumber(1) })
      : t("format.item", { count: formatNumber(value.length) });
  }
  const keys = Object.keys(value).length;
  return keys === 1 ? t("format.keyOne", { count: formatNumber(1) }) : t("format.key", { count: formatNumber(keys) });
}

/** Truncate one string on a word-ish boundary. */
export function truncate(text, limit = 160) {
  const value = typeof text === "string" ? text.replace(/\s+/g, " ").trim() : "";
  if (value.length <= limit) return value;
  return `${value.slice(0, Math.max(0, limit - 1)).trimEnd()}…`;
}

/** Parse one string as JSON, returning `{ value, error }`. */
export function parseJsonText(text) {
  if (typeof text !== "string" || !text.trim()) return { value: null, error: null };
  try {
    return { value: JSON.parse(text), error: null };
  } catch (error) {
    return { value: null, error: String(error?.message ?? error) };
  }
}

/** Parse JSON and pretty-print it; return the raw text with an error when invalid. */
export function prettyMaybeJson(text) {
  const parsed = parseJsonText(text);
  if (parsed.error) return { text: String(text ?? ""), parsed: false, error: parsed.error };
  if (parsed.value === null) return { text: "", parsed: false, error: null };
  return { text: formatJson(parsed.value), parsed: true, error: null };
}

/** Shorten an id for dense labels (``9c1e…4f2a``). */
export function shortId(id) {
  if (typeof id !== "string" || !id) return EM_DASH;
  if (id.length <= 14) return id;
  return `${id.slice(0, 6)}…${id.slice(-4)}`;
}

/** Capitalize a lowercase label. */
export function titleCase(value) {
  if (typeof value !== "string" || !value) return EM_DASH;
  return value.charAt(0).toUpperCase() + value.slice(1);
}

/** Return a pluralized count label. */
export function countLabel(count, singular, plural = `${singular}s`) {
  const value = asNumber(count) ?? 0;
  return `${formatNumber(value)} ${value === 1 ? singular : plural}`;
}

/** Sum numeric task-token fields of one token map, `null` when any side is scripted/missing. */
export function numericTokens(tokens) {
  if (!tokens || typeof tokens !== "object" || scriptedLabel(tokens)) return null;
  const prompt = asNumber(tokens.prompt) ?? 0;
  const completion = asNumber(tokens.completion) ?? 0;
  const total = asNumber(tokens.total) ?? prompt + completion;
  return { prompt, completion, total };
}
