/** Small DOM construction helpers.
 *
 * Every payload value is inserted with ``textContent``/``append``; this module intentionally has no
 * helper that accepts a markup string. Views stay declarative and the contract test can assert that
 * payload rendering always builds nodes.
 */

import { t } from "./i18n/index.js";

/** Create an element with an optional class and text. */
export function el(tag, className = "", text = null) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== null && text !== undefined) node.textContent = String(text);
  return node;
}

/** Remove every child of one node. */
export function clear(node) {
  if (node) node.replaceChildren();
}

/** Append children (skipping ``null``/``undefined``) and return the parent. */
export function append(parent, ...children) {
  for (const child of children) {
    if (child === null || child === undefined || child === false) continue;
    parent.appendChild(child);
  }
  return parent;
}

/** Create a paragraph-like block. */
export function paragraph(text, className = "") {
  return el("p", className, text);
}

/** Create a status pill with a glyph (never colour-only). */
export function statusBadge(status = "ok") {
  const info = statusInfo(status);
  const node = el("span", `status-badge status-${info.key}`);
  append(node, el("span", "status-glyph", info.glyph), el("span", "status-label", info.label));
  node.title = info.label;
  return node;
}

/** Return the glyph/label/class of one status. */
export function statusInfo(status) {
  const key = typeof status === "string" && status ? status : "ok";
  const table = {
    ok: { glyph: "✓", label: "status.ok" },
    completed: { glyph: "✓", label: "status.completed" },
    finished: { glyph: "✓", label: "status.finished" },
    error: { glyph: "✕", label: "status.failed" },
    failed: { glyph: "✕", label: "status.failed" },
    pending: { glyph: "○", label: "status.pending" },
    skipped: { glyph: "–", label: "status.skipped" },
    running: { glyph: "●", label: "status.running" },
    active: { glyph: "●", label: "status.active" },
    waiting: { glyph: "◷", label: "status.waiting" },
    retry: { glyph: "↻", label: "status.retry" },
    paused: { glyph: "Ⅱ", label: "status.paused" },
  };
  const entry = table[key];
  return { key, glyph: entry?.glyph ?? "•", label: entry ? t(entry.label) : key };
}

const SIDES = new Set(["client", "server"]);

/** Return the localized client/server label, ``null`` when the span did not record a side. */
export function sideLabel(side) {
  return SIDES.has(side) ? t(`side.${side}`).toUpperCase() : null;
}

/** Return the explicit client/server chip of one row, ``null`` when no side was recorded. */
export function sideBadge(side) {
  const label = sideLabel(side);
  return label === null ? null : el("span", `side-badge side-${side}`, label);
}

const ACTORS = new Set(["client", "server", "llm", "tool", "human", "system"]);

/** Return the visible UPPERCASE actor label. */
export function actorLabel(actor) {
  if (typeof actor !== "string" || !actor) return t("actor.system").toUpperCase();
  return ACTORS.has(actor) ? t(`actor.${actor}`).toUpperCase() : actor.toUpperCase();
}

/** Return the CSS actor class of one normalized item. */
export function actorClass(actor) {
  const known = ["client", "server", "llm", "tool", "human", "system"];
  return known.includes(actor) ? `actor-${actor}` : "actor-system";
}

/** Return the timeline node class of one item (circle / diamond / square). */
export function nodeClass(item) {
  if (item.kind === "llm") return "node-llm";
  if (item.kind === "tool") return "node-tool";
  if (item.actor === "human") return "node-human";
  return "node-protocol";
}

/** Attach click and Enter/Space activation to one interactive row. */
export function onActivate(node, handler) {
  node.addEventListener("click", handler);
  node.addEventListener("keydown", (event) => {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      handler(event);
    }
  });
}

/** Create a key/value fact row. */
export function fact(label, value, className = "") {
  const wrapper = el("div", `fact ${className}`.trim());
  append(wrapper, el("dt", "fact-label", label), el("dd", "fact-value", value));
  return wrapper;
}

/** Create a callout block. */
export function callout(title, body, tone = "info") {
  const node = el("div", `callout callout-${tone}`);
  append(node, el("h3", "callout-title", title), el("p", "callout-body", body));
  return node;
}
