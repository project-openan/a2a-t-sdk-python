/** Timeline view: phase cards, numbered rows, nested LLM/tool spans and the waterfall mode.
 *
 * The rows are rebuilt from the store on every frame. A run has tens of rows, so a full rebuild is
 * cheap and keeps the view purely derived. Collapsed phase state and the selected row live here and
 * in the store respectively, never in the payload objects.
 */

import {
  actorClass,
  actorLabel,
  append,
  clear,
  el,
  nodeClass,
  onActivate,
  sideBadge,
  sideLabel,
  statusBadge,
} from "./dom.js";
import {
  countLabel,
  formatDuration,
  formatTokens,
  shortId,
  shortModel,
  truncate,
} from "./format.js";
import { filterPhaseItems, filterItems } from "./filters.js";
import { t } from "./i18n/index.js";
import { PHASE_INDEX, phaseLabel } from "./store.js";

/** Timeline renderer. */
export class TimelineView {
  constructor({ timeline, stats, onSelect, states = {} }) {
    this.timeline = timeline;
    this.stats = stats;
    this.onSelect = onSelect;
    this.states = states;
    this.collapsed = new Set();
    this.orderedKeys = [];
    this.lastModel = null;
    this.lastStore = null;
  }

  /** Expand every phase card. */
  expandAll() {
    this.collapsed.clear();
    if (this.lastModel) this.render(this.lastModel);
  }

  /** Collapse every phase card. */
  collapseAll() {
    for (const phase of Object.keys(PHASE_INDEX)) this.collapsed.add(phase);
    if (this.lastModel) this.render(this.lastModel);
  }

  /** Expand one phase (used by the stepper). */
  expandPhase(phase) {
    this.collapsed.delete(phase);
  }

  /** Scroll to one phase card, expanding it first. */
  scrollToPhase(phase) {
    this.collapsed.delete(phase);
    if (this.lastModel) this.render(this.lastModel);
    const section = this.timeline.querySelector(`[data-phase-card="${phase}"]`);
    if (section) section.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  /** Scroll to one row, expanding its phase when a programmatic jump targets a collapsed card. */
  scrollToItem(key) {
    const item = this.lastStore?.getItem(key);
    if (item && this.collapsed.has(item.phase)) {
      this.collapsed.delete(item.phase);
      if (this.lastModel) this.render(this.lastModel);
    }
    const row = this.timeline.querySelector(`[data-key="${cssEscape(key)}"]`);
    if (row) row.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }

  /** Render the timeline from one store/filter snapshot. */
  render(model) {
    this.lastModel = model;
    this.lastStore = model.store;
    const { store, filters, selectedKey, viewState = {} } = model;
    const derived = store.derived();
    const items = store.list();
    const story = filters.view === "story";
    const activeFilter =
      filters.filter !== "all" || filters.actor !== "all" || filters.round !== "all" || Boolean(filters.search.trim());

    clear(this.timeline);
    this.orderedKeys = [];
    this._renderStates(store, activeFilter, viewState);

    if (items.length === 0) {
      this.stats.textContent = t("toolbar.step", { count: 0 });
      return;
    }

    const visible = filterItems(items, filters);
    const visibleCount = visible.length;
    const phaseCount = derived.groups.filter((group) => filterPhaseItems(group, filters).length > 0).length;
    this.stats.textContent = t("toolbar.stats", {
      steps: countLabel(visibleCount, t("units.stepOne"), t("units.step")),
      phases: countLabel(phaseCount, t("units.phaseOne"), t("units.phase")),
    });

    if (visibleCount === 0) {
      const empty = el("div", "timeline-filter-empty");
      append(
        empty,
        el("p", "", t("toolbar.noMatch")),
        this._button(t("toolbar.clearFilters"), "link-button", () => {
          model.onClearFilters?.();
        }),
      );
      this.timeline.appendChild(empty);
      return;
    }

    if (story) {
      for (const group of derived.groups) {
        const groupItems = filterPhaseItems(group, filters);
        if (activeFilter && groupItems.length === 0) continue;
        this._renderPhase(group, groupItems, derived, filters, selectedKey, store);
      }
    } else {
      this._renderWaterfall(visible, filters, selectedKey, store);
    }
  }

  /** Show the empty/loading/error/no-trace callouts. */
  _renderStates(store, activeFilter, viewState) {
    const items = store.list();
    const noTrace =
      items.length > 0 &&
      !activeFilter &&
      !items.some((item) => item.kind === "llm" || item.kind === "tool") &&
      (store.status === "finished" || store.status === "failed");
    setHidden(this.states.loading, !viewState.loading);
    setHidden(this.states.error, !viewState.error);
    if (viewState.error && this.states.errorText) {
      this.states.errorText.textContent = viewState.error;
    }
    setHidden(this.states.noTrace, !noTrace);
    setHidden(this.states.empty, viewState.loading || Boolean(viewState.error) || items.length > 0);
  }

  /** Render one phase card and its rows. */
  _renderPhase(group, items, derived, filters, selectedKey, store) {
    const state = derived.phaseStates[group.key] || "pending";
    const collapsed = this.collapsed.has(group.key);
    const section = el("section", `phase-card phase-${state}`);
    section.dataset.phaseCard = group.key;

    const head = el("button", "phase-head");
    head.type = "button";
    head.setAttribute("aria-expanded", String(!collapsed));
    const resolution = group.key === "resolution" ? store.resolutionLabel() : null;
    const title = el("span", "phase-title");
    append(
      title,
      el(
        "span",
        "phase-index",
        t("timeline.phaseIndex", { index: String(PHASE_INDEX[group.key]).padStart(2, "0") }),
      ),
      el("span", "phase-name", phaseLabel(group.key).toUpperCase()),
      resolution ? el("span", "phase-resolution", `· ${resolution}`) : null,
    );
    const meta = el("span", "phase-meta", this._phaseMeta(group));
    const chevron = el("span", "phase-chevron", collapsed ? "▸" : "▾");
    chevron.setAttribute("aria-hidden", "true");
    append(head, title, meta, this._roundChips(group.rounds, store, group.key), chevron);
    head.addEventListener("click", () => {
      if (this.collapsed.has(group.key)) this.collapsed.delete(group.key);
      else this.collapsed.add(group.key);
      if (this.lastModel) this.render(this.lastModel);
    });
    section.appendChild(head);

    if (!collapsed) {
      const body = el("div", "phase-body");
      if (items.length === 0) {
        const note = store.phaseNote(group.key);
        body.appendChild(
          el(
            "p",
            "phase-empty",
            note || (state === "skipped" ? t("timeline.emptySkipped") : t("timeline.empty")),
          ),
        );
      } else {
        const { roots, children } = this._nest(items, store);
        for (const item of roots) this._renderRow(body, item, 0, children, selectedKey, filters, store);
      }
      section.appendChild(body);
    }
    this.timeline.appendChild(section);
  }

  /** Return the summary meta of one phase card. */
  _phaseMeta(group) {
    const stats = group.stats;
    const parts = [countLabel(stats.steps, t("units.stepOne"), t("units.step"))];
    if (stats.durationMs !== null) parts.push(formatDuration(stats.durationMs));
    if (stats.llm > 0) parts.push(countLabel(stats.llm, t("units.llmOne"), t("units.llm")));
    if (stats.tools > 0) parts.push(countLabel(stats.tools, t("units.toolOne"), t("units.tool")));
    if (stats.errors > 0) {
      parts.push(countLabel(stats.errors, t("units.errorOne"), t("units.error")));
    }
    return parts.join(" · ");
  }

  /** Render the round chips of one phase. */
  _roundChips(rounds, store, phase) {
    if (phase !== "negotiation" || rounds.length === 0) return null;
    const wrap = el("span", "round-chips");
    for (const entry of rounds) {
      const state = store.derived().rounds.find((round) => round.round === entry.round)?.state || "done";
      const chip = el("span", `round-chip round-${state}`, `R${entry.round}`);
      chip.title = entry.terminal
        ? t("timeline.roundResolved", { round: entry.round })
        : t("timeline.roundState", { round: entry.round, state });
      wrap.appendChild(chip);
    }
    return wrap;
  }

  /** Build the root/child row structure of one filtered phase. */
  _nest(items, store) {
    const included = new Set(items.map((item) => item.key));
    const children = new Map();
    const roots = [];
    for (const item of items) {
      const parent =
        item.parentSpanId && included.has(store.getItemBySpan(item.parentSpanId)?.key)
          ? store.getItemBySpan(item.parentSpanId)
          : null;
      if (parent) {
        const list = children.get(parent.key) || [];
        list.push(item);
        children.set(parent.key, list);
      } else {
        roots.push(item);
      }
    }
    return { roots, children };
  }

  /** Render one row and its nested children. */
  _renderRow(parent, item, depth, children, selectedKey, filters, store) {
    const row = this._row(item, depth, selectedKey, store);
    parent.appendChild(row);
    this.orderedKeys.push(item.key);
    for (const child of children.get(item.key) || []) {
      if (depth < 3) this._renderRow(parent, child, depth + 1, children, selectedKey, filters, store);
    }
  }

  /** Build one timeline row. */
  _row(item, depth, selectedKey, store) {
    const row = el(
      "article",
      `tl-row ${actorClass(item.actor)} ${nodeClass(item)} depth-${depth}` +
        (item.key === selectedKey ? " is-selected" : "") +
        (item.status === "error" ? " is-error" : ""),
    );
    row.dataset.key = item.key;
    row.dataset.seq = String(item.seq);
    row.dataset.phase = item.phase;
    row.tabIndex = 0;
    row.setAttribute("role", "button");
    row.setAttribute("aria-selected", String(item.key === selectedKey));
    row.setAttribute(
      "aria-label",
      t("timeline.rowAria", {
        number: item.number,
        title: item.title,
        side: sideLabel(item.side) || "",
        actor: item.actor,
        status: item.status,
      }),
    );
    onActivate(row, () => this.onSelect?.(item.key));

    append(row, el("span", "tl-number", `#${String(item.number).padStart(2, "0")}`));

    const node = el("span", "tl-node");
    node.setAttribute("aria-hidden", "true");
    append(row, node);

    const main = el("div", "tl-main");
    const line = el("div", "tl-line");
    append(
      line,
      sideBadge(item.side),
      el("span", "actor-badge", actorLabel(item.actor)),
      this._kindBadge(item),
      el("span", "tl-title", truncate(item.title, 120)),
      statusBadge(item.status),
    );
    main.appendChild(line);
    const summary = this._rowSummary(item);
    if (summary) main.appendChild(el("div", "tl-summary", summary));
    main.appendChild(this._rowMeta(item, store));
    row.appendChild(main);

    const metrics = el("div", "tl-metrics");
    append(
      metrics,
      el("span", "metric metric-duration", formatDuration(item.durationMs)),
      item.kind === "llm"
        ? el(
            "span",
            "metric metric-tokens",
            formatTokens(item.tokens, { scripted: store.runMeta().modelMode === "offline" }),
          )
        : null,
      item.kind === "llm" ? el("span", "metric metric-model", shortModel(item.model)) : null,
    );
    row.appendChild(metrics);

    if (item.kind === "llm" || item.kind === "tool") {
      const bar = el("span", "tl-bar");
      bar.setAttribute("aria-hidden", "true");
      const width = Math.min(100, Math.max(2, ((item.durationMs || 1) / this._maxDuration(store)) * 100));
      bar.style.width = `${width}%`;
      row.appendChild(bar);
    }
    return row;
  }

  /** Return the kind badge of one row (LLM stage, tool name or protocol kind). */
  _kindBadge(item) {
    if (item.kind === "llm") {
      return el("span", "kind-badge kind-llm", `LLM${item.stage ? ` · ${item.stage}` : ""}`);
    }
    if (item.kind === "tool") {
      return el(
        "span",
        "kind-badge kind-tool",
        `${t("timeline.toolBadge")}${item.stage ? ` · ${item.stage}` : ""}`,
      );
    }
    const label = item.performative || item.negotiationType || item.kind;
    return label ? el("span", "kind-badge kind-protocol", String(label).toUpperCase()) : null;
  }

  /** Return the secondary summary line of one row. */
  _rowSummary(item) {
    if (item.entryType === "decision") {
      return item.rationale ? truncate(item.rationale, 180) : null;
    }
    if (item.kind === "llm" && item.purpose) return truncate(item.purpose, 180);
    if (item.detail?.toolName) {
      return truncate(
        t("timeline.args", { json: JSON.stringify(item.detail.args ?? {}) }),
        160,
      );
    }
    if (item.kind === "event" && item.detail?.event?.text) return truncate(item.detail.event.text, 180);
    if (item.summary && item.summary !== item.title) return truncate(item.summary, 180);
    if (item.templateUri) return truncate(item.templateUri, 160);
    return null;
  }

  /** Return the dense metadata strip of one row. */
  _rowMeta(item, store) {
    const meta = el("div", "tl-meta");
    const parts = [];
    if (item.stage) parts.push(t("timeline.stageLabel", { stage: item.stage }));
    if (Number.isInteger(item.round)) parts.push(`R${item.round}`);
    if (Number.isInteger(item.phaseStep)) parts.push(`P${PHASE_INDEX[item.phase] || "?"}.${item.phaseStep}`);
    if (item.contextId) parts.push(t("timeline.contextLabel", { id: shortId(item.contextId) }));
    if (item.kind === "tool" && item.detail?.toolName) parts.push(item.detail.toolName);
    if (item.direction && item.direction !== "internal") parts.push(item.direction);
    const text = parts.join(" · ");
    if (text) meta.textContent = text;
    else meta.textContent = phaseLabel(item.phase);
    store.getItem(item.key);
    return meta;
  }

  /** Render the flat waterfall view. */
  _renderWaterfall(items, filters, selectedKey, store) {
    const section = el("section", "waterfall");
    const head = el("div", "waterfall-head");
    append(
      head,
      el("span", "", t("timeline.waterfallSteps")),
      el("span", "", t("timeline.waterfallDuration")),
    );
    section.appendChild(head);
    const max = this._maxDuration(store);
    for (const item of items) {
      const row = this._row(item, 0, selectedKey, store);
      row.classList.add("waterfall-row");
      const bar = row.querySelector(".tl-bar");
      if (bar) {
        const width = Math.max(1, ((item.durationMs || 0) / max) * 100);
        bar.style.width = `${Math.min(100, width)}%`;
        bar.classList.add("waterfall-bar");
      }
      const phase = el("span", "waterfall-phase", phaseLabel(item.phase));
      row.querySelector(".tl-meta")?.appendChild(phase);
      section.appendChild(row);
      this.orderedKeys.push(item.key);
    }
    this.timeline.appendChild(section);
  }

  /** Return the largest measured duration of the run (at least 1 ms). */
  _maxDuration(store) {
    let max = 1;
    for (const item of store.list()) {
      if (typeof item.durationMs === "number" && item.durationMs > max) max = item.durationMs;
    }
    return max;
  }

  /** Create a small button. */
  _button(label, className, handler) {
    const button = el("button", className, label);
    button.type = "button";
    button.addEventListener("click", handler);
    return button;
  }
}

/** Toggle the ``hidden`` attribute. */
function setHidden(node, hidden) {
  if (!node) return;
  if (hidden) node.setAttribute("hidden", "");
  else node.removeAttribute("hidden");
}

/** Escape a value for a CSS attribute selector. */
function cssEscape(value) {
  if (window.CSS?.escape) return window.CSS.escape(value);
  return String(value).replace(/["\\]/g, "\\$&");
}
