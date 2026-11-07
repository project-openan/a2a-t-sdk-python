/** Timeline filtering, search and the follow controller.
 *
 * Classification only uses normalized store fields; payload inspection is limited to error markers
 * so a large LLM request never slows down typing in the search box.
 */

import { t } from "./i18n/index.js";
import { hasError, isLlm, isTool } from "./store.js";

/** The filter chips, in display order. */
export const FILTERS = [
  { key: "all", labelKey: "toolbar.filterAll" },
  { key: "protocol", labelKey: "toolbar.filterProtocol" },
  { key: "llm", labelKey: "toolbar.filterLlm" },
  { key: "tools", labelKey: "toolbar.filterTools" },
  { key: "errors", labelKey: "toolbar.filterErrors" },
];

/** One immutable filter state. */
export class FilterState {
  constructor() {
    this.filter = "all";
    this.actor = "all";
    this.round = "all";
    this.search = "";
    this.view = "story";
  }

  /** Return a copy with the given fields replaced. */
  with(changes) {
    const next = new FilterState();
    Object.assign(next, this, changes);
    return next;
  }
}

/** Whether one item is a protocol row (everything that is not an LLM/tool span). */
export function isProtocol(item) {
  return !isLlm(item) && !isTool(item);
}

/** Whether one item passes the active filter chips, actor and round filters. */
export function matchesFilters(item, state) {
  if (state.filter !== "all") {
    if (state.filter === "llm" && !isLlm(item)) return false;
    if (state.filter === "tools" && !isTool(item)) return false;
    if (state.filter === "protocol" && !isProtocol(item)) return false;
    if (state.filter === "errors" && !hasError(item)) return false;
  }
  if (state.actor !== "all" && item.actor !== state.actor) return false;
  if (state.round !== "all") {
    const round = Number(state.round);
    if (item.round !== round) return false;
  }
  return matchesSearch(item, state.search);
}

/** Case-insensitive search over the normalized metadata of one item. */
export function matchesSearch(item, query) {
  const needle = typeof query === "string" ? query.trim().toLowerCase() : "";
  if (!needle) return true;
  const fields = [
    item.title,
    item.summary,
    item.purpose,
    item.rationale,
    item.stage,
    item.model,
    item.actor,
    item.kind,
    item.performative,
    item.negotiationType,
    item.action,
  ];
  return fields.some((field) => typeof field === "string" && field.toLowerCase().includes(needle));
}

/** Return the filtered items of one phase, in ``seq`` order. */
export function filterPhaseItems(group, state) {
  return group.items.filter((item) => matchesFilters(item, state));
}

/** Return every filtered timeline item, flattened. */
export function filterItems(items, state) {
  return items.filter((item) => matchesFilters(item, state));
}

/** Return the rounds visible in one item list, for the round filter. */
export function availableRounds(items) {
  const rounds = new Set();
  for (const item of items) {
    if (Number.isInteger(item.round)) rounds.add(item.round);
  }
  return [...rounds].sort((left, right) => left - right);
}

/**
 * Follow-new-events controller.
 *
 * Following pauses when the reader scrolls away from the bottom or selects an older row, and
 * resumes when they scroll back to the bottom or press ``f``. New frames are counted while paused
 * so the toolbar can show ``Paused · N new``.
 */
export class FollowController extends EventTarget {
  constructor() {
    super();
    this.enabled = true;
    this.paused = false;
    this.newCount = 0;
  }

  /** Toggle following on demand. */
  toggle() {
    this.enabled = !this.enabled;
    this.paused = !this.enabled;
    if (this.enabled) this.newCount = 0;
    this.emit();
  }

  /** Explicitly set the enabled state. */
  setEnabled(enabled) {
    this.enabled = Boolean(enabled);
    this.paused = !this.enabled;
    if (this.enabled) this.newCount = 0;
    this.emit();
  }

  /** Pause following without changing the user's preference. */
  pauseForReader() {
    if (!this.enabled || this.paused) return;
    this.paused = true;
    this.emit();
  }

  /** Record one new frame while paused. */
  noteNew() {
    if (!this.paused) return;
    this.newCount += 1;
    this.emit();
  }

  /** Resume following and clear the new-frame counter. */
  resume() {
    this.enabled = true;
    this.paused = false;
    this.newCount = 0;
    this.emit();
  }

  /** Return the label shown next to the follow toggle. */
  label() {
    if (this.paused) {
      return this.newCount > 0
        ? t("header.pausedNew", { count: this.newCount })
        : t("header.paused");
    }
    return t("header.following");
  }

  /** Emit one change event. */
  emit() {
    this.dispatchEvent(new CustomEvent("change", { detail: this }));
  }
}

/** Return whether one scroll container is (nearly) at the bottom. */
export function isAtBottom(element, slack = 48) {
  if (!element) return true;
  return element.scrollHeight - element.scrollTop - element.clientHeight <= slack;
}
