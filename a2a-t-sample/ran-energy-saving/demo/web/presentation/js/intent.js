/** Structured intent and the 24-hour guaranteed-throughput chart.
 *
 * The intent is the client's reading of the engineer's request: the rendered Task-T once the
 * A2A-T flow produces it (the wire truth), otherwise the business intent layer or the request
 * itself. Parsing is deliberately small: it accepts the Task-T ``{HH:MM-HH:MM,NMbps}`` notation
 * and the concise ``HH:MM–HH:MM at N Mbps`` wording.
 */

import { clear, el, isObject, svg } from "./dom.js";
import { format } from "../copy.js";

const SLOT_RE =
  /\{?\s*(\d{1,2}):(\d{2})\s*[~\-–]\s*(\d{1,2}):(\d{2})\s*,\s*([\d.]+)\s*Mbps\s*\}?/gi;
const NATURAL_SLOT_RE =
  /(\d{1,2}):(\d{2})\s*[~\-–]\s*(\d{1,2}):(\d{2})\s*(?:,\s*|\s+at\s+|\s+)([\d.]+)\s*Mbps/gi;
const OUTAGE_EN_RE =
  /(?:power outage|outage)[^0-9]*(?:from\s+)?(\d{1,2}):(\d{2})\s*(?:to|[~\-–])\s*(\d{1,2}):(\d{2})/i;
const OUTAGE_TIME_FIRST_EN_RE =
  /(\d{1,2}):(\d{2})\s*[~\-–]\s*(\d{1,2}):(\d{2})\s*(?:power\s+)?outage/i;
const OUTAGE_ZH_RE = /(\d{1,2}):(\d{2})\s*[~\-–]\s*(\d{1,2}):(\d{2})\s*(?:有)?\s*停电/;

/** Return the Task-T text echoed by a Task creation payload's history, or ``""``. */
export function taskTFromTask(task) {
  const history = isObject(task) && Array.isArray(task.history) ? task.history : [];
  for (let index = history.length - 1; index >= 0; index -= 1) {
    const message = history[index];
    const metadata = isObject(message) && isObject(message.metadata) ? message.metadata : null;
    if (metadata === null) continue;
    const key = Object.keys(metadata).find((name) => String(name).includes("Task-T"));
    if (key && typeof metadata[key] === "string" && metadata[key]) return metadata[key];
  }
  return "";
}

/** Parse the Task-T text (or the request) into the facts the page shows.
 *
 * ``businessIntent`` is the server's structured intent; when present it is authoritative. The text
 * is still parsed for the fields the server does not carry (outage windows and chart gaps), but a
 * value the server resolved — notably a region replaced for a real NMS — must not be re-guessed
 * from a free-form sentence.
 */
export function parseIntent(text, businessIntent = null) {
  if (typeof text !== "string" || text.trim() === "") return null;
  const sections = sectionMap(text);
  const operationRaw = sections["operation type"] || "";
  const objectRaw = sections["task object"] || "";
  const contextRaw = sections["task context"] || text;

  const slots = parseSlots(contextRaw);
  const outage = parseOutage(contextRaw);
  const sites = parseSites(`${objectRaw} ${contextRaw}`);
  const area = cleanArea(objectRaw) || parseArea(text);
  const operation = /modify|修改/i.test(operationRaw)
    ? "modify"
    : /create|创建/i.test(operationRaw) || slots.length > 0 || area
      ? "create"
      : null;

  return mergeBusinessIntent(
    {
      raw: text,
      operation,
      area,
      sites,
      slots,
      outage,
      overlap: findOverlap(slots),
      gaps: findGaps(slots),
    },
    businessIntent,
  );
}

/** Overlay the server's structured intent on the text-derived facts. */
function mergeBusinessIntent(parsed, businessIntent) {
  if (!isObject(businessIntent)) return parsed;
  const merged = { ...parsed };
  const region = typeof businessIntent.region === "string" ? businessIntent.region.trim() : "";
  if (region) merged.area = region;
  const operation = String(businessIntent.operationType || "").toLowerCase();
  if (operation === "create" || operation === "modify") merged.operation = operation;
  if (Array.isArray(businessIntent.sites)) {
    const sites = businessIntent.sites.filter((item) => typeof item === "string" && item.trim());
    if (sites.length > 0) merged.sites = sites;
  }
  if (Array.isArray(businessIntent.windows)) {
    const windows = businessIntent.windows.filter((item) => typeof item === "string" && item.trim());
    if (windows.length > 0) {
      const slots = parseSlots(windows.join(" "));
      if (slots.length > 0) {
        merged.slots = slots;
        merged.overlap = findOverlap(slots);
        merged.gaps = findGaps(slots);
      }
    }
  }
  if (merged.outage === null && typeof businessIntent.backgroundEvent === "string") {
    const outage = parseOutage(businessIntent.backgroundEvent);
    if (outage !== null) merged.outage = outage;
  }
  return merged;
}

/** Return the plain-A2A sentence of one parsed intent (no Task-T fields). */
export function plainSentence(intent, language = "en-US") {
  if (!intent) return "";
  const area = intent.area || (language === "zh-CN" ? "目标区域" : "the target area");
  if (language === "zh-CN") {
    const verb = intent.operation === "modify" ? "修改运行中的" : "创建";
    let text = `在${area}${verb}无线网络节能任务`;
    if (intent.sites.length > 0) text += `，站点列表为 ${intent.sites.join("、")}`;
    if (intent.slots.length > 0) {
      text += `，保障速率为 ${intent.slots
        .map((slot) => `${hhmm(slot.start)}–${hhmm(slot.end)} ${slot.rate}Mbps`)
        .join("、")}`;
    }
    if (intent.outage !== null) {
      text += `，背景事件为 ${hhmm(intent.outage.start)}–${hhmm(intent.outage.end)} 停电`;
    }
    return `${text}；在满足这些目标的前提下最大化节能，并报告节能收益和实际速率。`;
  }
  const verb =
    intent.operation === "modify"
      ? "Modify the running energy-saving task"
      : "Perform RAN energy saving";
  let text = `${verb} in ${area}`;
  if (intent.sites.length > 0) text += ` for ${intent.sites.join(", ")}`;
  if (intent.slots.length > 0) {
    text += `. Guarantee ${intent.slots
      .map((slot) => `${slot.rate} Mbps from ${hhmm(slot.start)} to ${hhmm(slot.end)}`)
      .join(", ")}`;
  }
  if (intent.outage !== null) {
    text += `, because of a power outage from ${hhmm(intent.outage.start)} to ${hhmm(
      intent.outage.end,
    )}`;
  }
  return `${text}. Maximize energy saving while meeting these targets, and report the energy saving gain and the throughput reached.`;
}

/** Render the facts grid and the chart into one container. */
export function renderIntent(
  container,
  intentText,
  copy,
  {
    update,
    rawRequest = null,
    normalizedText = null,
    source = null,
    warnings = [],
    missingFields = [],
    conflicts = [],
    businessIntent = null,
    validation = null,
  } = {},
) {
  clear(container);
  if (rawRequest || source || validation) {
    container.append(
      businessBlock(copy, {
        rawRequest,
        normalizedText: normalizedText ?? intentText,
        source,
        warnings,
        missingFields,
        conflicts,
        validation,
      }),
    );
  }
  const intent = parseIntent(intentText, businessIntent);
  if (intent === null) {
    container.append(el("p", "empty", copy.intent.empty));
    return null;
  }

  const facts = el("div", "facts");
  facts.append(
    fact(copy.intent.operation, operationLabel(intent, copy)),
    fact(copy.intent.area, intent.area || copy.intent.none, intent.area ? null : "flag"),
  );
  if (intent.sites.length > 0) {
    facts.append(fact(copy.intent.sites, intent.sites.join(", ")));
  }
  if (intent.outage !== null) {
    facts.append(
      fact(
        copy.intent.event,
        format(copy.intent.outage, {
          from: hhmm(intent.outage.start),
          to: hhmm(intent.outage.end),
        }),
      ),
    );
  }
  const targetText =
    intent.slots.length > 0
      ? intent.slots.map((slot) => `${hhmm(slot.start)}–${hhmm(slot.end)}: ${slot.rate}`).join(" · ") +
        " Mbps"
      : copy.intent.noTarget;
  facts.append(fact(copy.intent.targets, targetText, null, true));
  if (intent.overlap !== null) {
    facts.append(
      fact(
        copy.intent.check,
        format(copy.intent.overlap, {
          from: hhmm(intent.overlap.start),
          to: hhmm(intent.overlap.end),
        }),
        "flag",
      ),
    );
  }
  container.append(facts, chart(intent, copy, update ?? false));
  return intent;
}

/** Render the SDK Task-T validation result as a compact inline block.
 *
 * ``negotiable`` means the server will still receive the message, so the block is a warning;
 * ``blocked`` means nothing was sent and the operator must repair the request; ``unavailable``
 * means the validator itself failed and the request was allowed through.
 */
/** Return whether one validation code names a validator/infrastructure failure. */
function isInfrastructureCode(code) {
  return typeof code === "string" && (code.startsWith("llm.") || code.startsWith("infra."));
}

export function validationBlock(validation, copy) {
  if (!isObject(validation)) return null;
  const verdict = typeof validation.verdict === "string" ? validation.verdict : "";
  const errors = Array.isArray(validation.errors) ? validation.errors : [];
  if (verdict === "passed" || (errors.length === 0 && verdict !== "unavailable")) return null;
  const labels = copy.validation || {};
  const section = el("div", `validation-block ${verdict}`);
  const missingInfo =
    errors.length > 0 &&
    errors.every((error) => isObject(error) && error.code === "content.param_missing");
  // A blocked result whose reason or error is an infrastructure code means the validator itself
  // failed, not that the operator left a field empty.
  const validatorFailed =
    isInfrastructureCode(validation.reason) ||
    errors.some((error) => isObject(error) && isInfrastructureCode(error.code));
  let note = labels.blocked;
  if (verdict === "negotiable") note = labels.negotiable;
  else if (verdict === "unavailable") note = labels.unavailable;
  else if (verdict === "blocked" && validatorFailed) note = labels.validatorFailed;
  else if (verdict === "blocked" && missingInfo) note = labels.missingInfo;
  if (note) section.append(el("p", "validation-note", note));
  const attempts = Number.isInteger(validation.attempts) ? validation.attempts : 1;
  if (attempts > 1 && labels.retried) {
    section.append(el("p", "validation-note", format(labels.retried, { count: attempts })));
  }
  if (errors.length > 0) {
    const list = el("ul", "validation-errors");
    for (const error of errors) {
      const item = el("li");
      const facts = isObject(error.facts) ? error.facts : {};
      const name =
        facts.slot_label || facts.field_label || facts.section_label || error.slotName || error.code || "—";
      item.append(el("b", "", name));
      if (error.message) item.append(el("span", "", error.message));
      if (error.code) item.append(el("code", "", error.code));
      const hint = validationHint(error, labels);
      if (hint) item.append(el("span", "validation-hint", hint));
      list.append(item);
    }
    section.append(list);
  }
  return section;
}

/** Return one actionable line for a validation error, or ``""`` when none applies.
 *
 * The SDK reports structural facts (the missing field, the section, the offending value); the
 * presentation turns them into a next step without inventing a business rule.
 */
function validationHint(error, labels) {
  if (!isObject(error)) return "";
  const facts = isObject(error.facts) ? error.facts : {};
  const field = typeof facts.field_label === "string" ? facts.field_label.trim() : "";
  const section = typeof facts.section_label === "string" ? facts.section_label.trim() : "";
  const code = typeof error.code === "string" ? error.code : "";
  if (field && labels.missingField) return format(labels.missingField, { field });
  if (section && code === "content.param_missing" && labels.sectionHint) {
    return format(labels.sectionHint, { section });
  }
  if (labels.constraintHint && (code.startsWith("slot.") || code === "content.value_not_allowed")) {
    return labels.constraintHint;
  }
  if (section && labels.sectionHint) return format(labels.sectionHint, { section });
  return "";
}

/** Whether the normalized-request disclosure is open; the panel is rebuilt on every event. */
let normalizedOpen = false;
/** Whether the whole business-intent block is open; same rebuild caveat as ``normalizedOpen``. */
let businessOpen = false;

/** Render the raw operator request next to the normalized business intent.
 *
 * The whole block folds away by default so the parsed intent and its chart get the panel height.
 * A blocking validation forces it open: the reason must never be hidden behind a disclosure.
 */
function businessBlock(
  copy,
  { rawRequest, normalizedText, source, warnings, missingFields, conflicts, validation },
) {
  const section = el("details", "business-intent business-collapsible");
  section.open = businessOpen || (isObject(validation) && validation.blocking === true);
  section.addEventListener("toggle", () => {
    businessOpen = section.open;
  });
  const summary = el("summary", "");
  summary.append(el("span", "micro-label", copy.intent.business));
  section.append(summary);
  if (rawRequest) {
    const raw = el("div", "business-text");
    raw.append(el("span", "", copy.intent.raw), el("p", "", rawRequest));
    section.append(raw);
  }
  // WYSIWYG: the intent never rewrites the request, so only show the normalization when it is
  // actually a different text (a deployment that supplies one).
  if (normalizedText && normalizedText !== rawRequest) {
    // The panel re-renders on every event, so remember the disclosure state outside the DOM.
    const normalized = el("details", "business-text business-collapsible");
    normalized.open = normalizedOpen;
    normalized.addEventListener("toggle", () => {
      normalizedOpen = normalized.open;
    });
    normalized.append(el("summary", "", copy.intent.normalized), el("p", "", normalizedText));
    section.append(normalized);
  }
  const meta = [];
  if (source) {
    const label = copy.intent.sources?.[source] || source;
    meta.push([copy.intent.source, label]);
  }
  if (missingFields.length > 0) {
    meta.push([copy.intent.missingFields, missingFields.join(", "), "flag"]);
  }
  if (conflicts.length > 0) {
    meta.push([copy.intent.conflicts, conflicts.join(", "), "flag"]);
  }
  if (meta.length > 0) {
    const facts = el("div", "facts");
    for (const [label, value, flag] of meta) facts.append(fact(label, value, flag));
    section.append(facts);
  }
  if (warnings.length > 0) {
    section.append(el("p", "business-warning", warnings.join(" ")));
  }
  const validationNode = validationBlock(validation, copy);
  if (validationNode) section.append(validationNode);
  return section;
}

function fact(label, value, flag, wide = false) {
  const node = el("div", wide ? "fact wide" : "fact");
  node.append(el("span", "", label));
  const bold = el("b", flag ? "flag" : "", value);
  node.append(bold);
  return node;
}

function operationLabel(intent, copy) {
  if (intent.operation === "modify") return copy.intent.modify;
  if (intent.operation === "create") return copy.intent.create;
  return copy.intent.none;
}

function chart(intent, copy, update) {
  const W = 560;
  const H = 160;
  const pl = 40;
  const pr = 12;
  const pt = 20;
  const pb = 28;
  const maxV = Math.max(20, ...intent.slots.map((slot) => slot.rate)) * 1.15;
  const x = (minutes) => pl + (minutes / 1440) * (W - pl - pr);
  const y = (value) => pt + (1 - value / maxV) * (H - pt - pb);
  const y0 = H - pb;

  const root = svg("svg", {
    class: "chart",
    viewBox: `0 0 ${W} ${H}`,
    role: "img",
    "aria-label": "Guaranteed throughput per time slot over 24 hours",
  });

  const defs = svg("defs");
  defs.append(hatch("hatch-g", "#F7F7F7", "#CCCCCC"));
  defs.append(hatch("hatch-r", "#FFFFFF", "#CD3C14"));
  root.append(defs);

  for (let hour = 0; hour <= 24; hour += 3) {
    root.append(
      svg("line", {
        class: "ch-grid",
        x1: x(hour * 60),
        x2: x(hour * 60),
        y1: pt,
        y2: y0,
      }),
      svg(
        "text",
        { class: "ch-txt", x: x(hour * 60), y: H - 10, "text-anchor": "middle" },
        `${String(hour).padStart(2, "0")}h`,
      ),
    );
  }
  for (const value of [0, 10, 20]) {
    root.append(
      svg(
        "text",
        { class: "ch-txt", x: pl - 6, y: y(value) + 4, "text-anchor": "end" },
        String(value),
      ),
      svg("line", { class: "ch-grid", x1: pl, x2: W - pr, y1: y(value), y2: y(value) }),
    );
  }
  root.append(svg("text", { class: "ch-txt", x: pl - 6, y: pt - 7, "text-anchor": "end" }, "Mbps"));

  for (const gap of intent.gaps) {
    root.append(
      svg("rect", {
        fill: "url(#hatch-g)",
        x: x(gap.start),
        y: pt,
        width: x(gap.end) - x(gap.start),
        height: y0 - pt,
      }),
    );
    if (x(gap.end) - x(gap.start) > 60) {
      root.append(
        svg(
          "text",
          {
            class: "ch-txt",
            x: (x(gap.start) + x(gap.end)) / 2,
            y: pt + 13,
            "text-anchor": "middle",
          },
          copy.intent.defaultPolicy,
        ),
      );
    }
  }

  if (intent.outage !== null) {
    root.append(
      svg("rect", {
        class: "ch-out",
        x: x(intent.outage.start),
        y: pt,
        width: x(intent.outage.end) - x(intent.outage.start),
        height: y0 - pt,
      }),
      svg(
        "text",
        {
          class: "ch-val",
          x: x(intent.outage.end) + 5,
          y: pt + 11,
        },
        format(copy.intent.outage, {
          from: hhmm(intent.outage.start),
          to: hhmm(intent.outage.end),
        }),
      ),
    );
  }

  for (const slot of intent.slots) {
    root.append(
      svg("rect", {
        class: "ch-slot",
        x: x(slot.start),
        y: y(slot.rate),
        width: x(slot.end) - x(slot.start),
        height: y0 - y(slot.rate),
      }),
      svg("line", {
        class: "ch-top",
        x1: x(slot.start),
        x2: x(slot.end),
        y1: y(slot.rate),
        y2: y(slot.rate),
      }),
    );
    if (x(slot.end) - x(slot.start) > 34) {
      root.append(
        svg(
          "text",
          {
            class: "ch-val",
            x: (x(slot.start) + x(slot.end)) / 2,
            y: y(slot.rate) - 6,
            "text-anchor": "middle",
          },
          String(slot.rate),
        ),
      );
    }
  }

  if (intent.overlap !== null) {
    root.append(
      svg("rect", {
        fill: "url(#hatch-r)",
        "fill-opacity": "0.5",
        x: x(intent.overlap.start),
        y: pt,
        width: x(intent.overlap.end) - x(intent.overlap.start),
        height: y0 - pt,
      }),
      svg(
        "text",
        {
          class: "ch-val ch-overlap-label",
          x: x(intent.overlap.start) - 3,
          y: y0 - 6,
          "text-anchor": "end",
          fill: "#CD3C14",
        },
        copy.intent.overlapLabel,
      ),
    );
  }

  root.append(svg("line", { class: "ch-axis", x1: pl, x2: W - pr, y1: y0, y2: y0 }));
  root.dataset.update = update ? "true" : "false";
  return root;
}

function hatch(id, fill, stroke) {
  const pattern = svg("pattern", {
    id,
    width: 6,
    height: 6,
    patternUnits: "userSpaceOnUse",
    patternTransform: "rotate(45)",
  });
  pattern.append(
    svg("rect", { class: "ch-hatch-bg", width: 6, height: 6, fill }),
    svg("line", {
      class: "ch-hatch-line",
      x1: 0,
      y1: 0,
      x2: 0,
      y2: 6,
      stroke,
      "stroke-width": 2,
    }),
  );
  return pattern;
}

function sectionMap(text) {
  const map = {};
  let current = null;
  for (const raw of text.split(/\r?\n/)) {
    const line = raw.trim();
    const heading = line.match(/^##\s+(.+?)\s*$/);
    if (heading) {
      // The rendered Task-T carries bilingual headings such as "任务对象(Task Object)".
      const english = heading[1].match(/\(([^)]+)\)\s*$/);
      current = (english ? english[1] : heading[1]).trim().toLowerCase();
      map[current] = "";
      continue;
    }
    if (current !== null && line !== "") {
      map[current] = map[current] ? `${map[current]} ${line}` : line;
    }
  }
  return map;
}

function parseSlots(text) {
  const slots = [];
  const seen = new Set();
  for (const regex of [SLOT_RE, NATURAL_SLOT_RE]) {
    for (const match of text.matchAll(regex)) {
      const start = timeToMinutes(match[1], match[2]);
      let end = timeToMinutes(match[3], match[4]);
      if (end === 0) end = 1440;
      const rate = Number(match[5]);
      const key = `${start}:${end}:${rate}`;
      if (end > start && end <= 1440 && rate > 0 && !seen.has(key)) {
        seen.add(key);
        slots.push({ start, end, rate });
      }
    }
  }
  return slots.sort((a, b) => a.start - b.start);
}

function parseOutage(text) {
  const en = text.match(OUTAGE_EN_RE) || text.match(OUTAGE_TIME_FIRST_EN_RE);
  if (en) {
    return {
      start: timeToMinutes(en[1], en[2]),
      end: normalizeEnd(timeToMinutes(en[3], en[4])),
    };
  }
  const zh = text.match(OUTAGE_ZH_RE);
  if (zh) {
    return {
      start: timeToMinutes(zh[1], zh[2]),
      end: normalizeEnd(timeToMinutes(zh[3], zh[4])),
    };
  }
  return null;
}

function parseSites(text) {
  const listed = text.match(/(?:Station list|Site list|站点列表)\s*1\s*(?:为|[:：])?\s*\[([^\]]+)\]/i);
  if (listed) {
    return listed[1]
      .split(",")
      .map((item) => item.trim().replace(/^["'“”]+|["'“”]+$/g, ""))
      .filter(Boolean);
  }
  const natural =
    text.match(/\b(?:for|at)\s+((?:Site|Station)\s*\d+(?:\s*(?:,|and|、|和)\s*(?:Site|Station)\s*\d+)*)/i) ||
    text.match(/(?:为|站点)\s*((?:站点|Site|Station)\s*\d+(?:\s*(?:,|and|、|和)\s*(?:站点|Site|Station)\s*\d+)*)/i);
  if (!natural) return [];
  return natural[1]
    .split(/\s*(?:,|and|、|和)\s*/i)
    .map((item) => item.trim())
    .filter(Boolean);
}

function cleanArea(raw) {
  if (!raw) return "";
  let value = raw.trim();
  value = value.replace(/^The area is the\s+/i, "").replace(/^the area is\s+/i, "");
  value = value.replace(/^任务对象\s*[:：]\s*/, "");
  value = value.replace(/^区域为\s*/, "").replace(/^区域\s*[:：]\s*/, "");
  // The site-level Task-T puts the site list in the same section; keep only the area name.
  value = value.split(/(?:[;；]\s*|\.\s+)(?:Station list|Site list|站点列表)/i)[0];
  value = value.replace(/[.。]$/, "");
  return value.trim();
}

function parseArea(text) {
  // The neutral demo names are "Region A" / "区域 A"; the shorter spelling omits the "area"
  // word the older pattern required.
  const neutralEn = text.match(/\b(Region\s+[A-Z])\b/i);
  if (neutralEn) return neutralEn[1].trim();
  // A real-NMS region is one compact token ("A2ATestRegion") with no space before "Region".
  const compactEn = text.match(/\bin\s+([A-Za-z0-9_-]*Region[A-Za-z0-9_-]*)\b/i);
  if (compactEn) return compactEn[1].trim();
  const neutralZh = text.match(/(区域\s*[A-Z])/i);
  if (neutralZh) return neutralZh[1].trim();
  const compactZh = text.match(/(?:在|区域[:：])\s*([A-Za-z0-9_-]*Region[A-Za-z0-9_-]*)/i);
  if (compactZh) return compactZh[1].trim();
  const en = text.match(/\bin\s+the\s+(.+?)\s+area\b/i);
  if (en) return en[1].trim();
  const zh = text.match(/(?:在|区域[:：]\s*)([一-龥A-Za-z0-9]+?)(?:创建|区域|进行|开启|为|，|,)/);
  return zh ? zh[1].trim() : "";
}

function findOverlap(slots) {
  const coverage = new Array(1440).fill(0);
  for (const slot of slots) {
    for (let minute = slot.start; minute < slot.end; minute += 1) coverage[minute] += 1;
  }
  return firstRun(coverage, (value) => value > 1);
}

function findGaps(slots) {
  if (slots.length === 0) return [];
  const coverage = new Array(1440).fill(0);
  for (const slot of slots) {
    for (let minute = slot.start; minute < slot.end; minute += 1) coverage[minute] += 1;
  }
  const gaps = [];
  let start = -1;
  for (let minute = 0; minute <= 1440; minute += 1) {
    const empty = minute < 1440 && coverage[minute] === 0;
    if (empty && start < 0) start = minute;
    if (!empty && start >= 0) {
      gaps.push({ start, end: minute });
      start = -1;
    }
  }
  return gaps;
}

function firstRun(coverage, predicate) {
  let start = -1;
  for (let minute = 0; minute <= 1440; minute += 1) {
    const hit = minute < 1440 && predicate(coverage[minute]);
    if (hit && start < 0) start = minute;
    if (!hit && start >= 0) return { start, end: minute };
  }
  return null;
}

function normalizeEnd(minutes) {
  return minutes === 0 ? 1440 : minutes;
}

function timeToMinutes(hours, minutes) {
  return Number(hours) * 60 + Number(minutes);
}

/** Format minutes as ``HH:MM`` (24:00 for the end of day). */
export function hhmm(minutes) {
  if (minutes >= 1440) return "24:00";
  const value = Math.max(0, Math.floor(minutes));
  return `${String(Math.floor(value / 60)).padStart(2, "0")}:${String(value % 60).padStart(2, "0")}`;
}

export { isObject };
