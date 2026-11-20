/** On-the-wire inspector: the A2A-T request and its plain-A2A comparison.
 *
 * Whenever the run captured the exchange, the panel shows the request line, headers and body that
 * the SDK really sent (a ``SendMessageRequest``, i.e. ``{"message": {...}}``). Only a run without a
 * capture falls back to the documented envelope, and the header area says so. The Plain A2A view
 * keeps the same message id and content but removes the extension header and Task-T fields,
 * replacing the short label with one natural-language sentence. It is a format comparison, not a
 * second run.
 */

import { actionLabel } from "../copy.js";
import { el, isObject } from "./dom.js";
import { parseIntent, plainSentence } from "./intent.js";

export function createWire(els, getCopy, getUi) {
  let lastKey = null;
  let lastFormat = null;
  let lastSignature = null;

  function render(state) {
    const ui = getUi();
    const copy = getCopy();
    const entry = ui.wireEntry || latestWireEntry(state);
    const signature = entry
      ? `${wireKey(entry)}|${ui.wireFormat}|${state.runId || ""}|${copy.lang}|${
          state.intentText || ""
        }|${state.taskTPrompt || ""}|${wireRevision(state.wire)}`
      : "empty";
    // The page renders on every frame; the message only changes when the shown entry, the
    // format or the rendered intent changes.
    if (signature === lastSignature) return;
    const model = entry ? buildWireModel(entry, state, ui.wireFormat, copy) : null;
    if (model === null) {
      lastKey = null;
      lastFormat = null;
      lastSignature = "empty";
      els.title.textContent = copy.wire.emptyTitle;
      els.code.replaceChildren(el("span", "wire-empty", copy.wire.empty));
      els.note.hidden = true;
      if (els.copyButton) els.copyButton.disabled = true;
      setFormat(copy, "t", false, false);
      return;
    }
    if (!model.alternate && !model.extension) ui.wireFormat = "t";
    const key = wireKey(entry);
    const changed = key !== lastKey || ui.wireFormat !== lastFormat;
    const scroll = els.code.scrollTop;
    els.title.textContent = model.title;
    els.code.replaceChildren(...model.nodes);
    els.code.scrollTop = changed ? 0 : scroll;
    if (els.copyButton) els.copyButton.disabled = false;
    lastKey = key;
    lastFormat = ui.wireFormat;
    lastSignature = `${key}|${ui.wireFormat}|${state.runId || ""}|${copy.lang}|${
      state.intentText || ""
    }|${state.taskTPrompt || ""}|${wireRevision(state.wire)}`;
    els.note.textContent = model.note || "";
    els.note.hidden = !model.note;
    setFormat(copy, ui.wireFormat, model.alternate, extensionPayload(entry, state.wire) !== null);
  }

  function setFormat(copy, format, alternate, hasExtension) {
    els.formatT.textContent = copy.wire.formatT;
    els.formatX.textContent = copy.wire.formatX;
    els.formatA.textContent = copy.wire.formatA;
    els.formatT.setAttribute("aria-pressed", String(format === "t"));
    els.formatX.setAttribute("aria-pressed", String(format === "x"));
    els.formatA.setAttribute("aria-pressed", String(format === "a"));
    els.formatT.disabled = false;
    els.formatX.disabled = !hasExtension;
    els.formatA.disabled = !alternate;
  }

  function showEntry(entry) {
    const ui = getUi();
    ui.wireEntry = entry;
    ui.wireFormat = "t";
    ui.tab = "wire";
    render(ui.view);
  }

  return { render, showEntry };
}

/** Identity of the currently loaded capture; a new record must re-render the panel. */
function wireRevision(records) {
  if (!Array.isArray(records) || records.length === 0) return "0";
  const last = records[records.length - 1];
  return `${records.length}:${isObject(last) && typeof last.ts === "string" ? last.ts : ""}`;
}

/** Stable identity of one displayed entry; a changed entry resets the panel scroll. */
export function wireKey(entry) {
  if (!entry || typeof entry !== "object") return "";
  if (entry.spanId) return `span:${entry.spanId}`;
  if (entry.seq !== undefined && entry.seq !== null) return `seq:${entry.seq}`;
  if (entry.messageId) return `msg:${entry.messageId}`;
  return `${entry.entryType || ""}:${entry.kind || ""}:${entry.title || entry.summary || ""}`;
}

/** Return the last outbound message span, so the wire tab has content without a click. */
export function latestWireEntry(state) {
  for (let index = state.trace.length - 1; index >= 0; index -= 1) {
    const entry = state.trace[index].entry;
    if (entry && entry.kind === "message_sent") return entry;
  }
  return null;
}

/** The Task-T / Negotiation-T payload of one entry, or ``null`` when it carries none. */
export function extensionPayload(entry, wireRecords = null) {
  if (!isObject(entry)) return null;
  // A captured request is the authoritative copy of the metadata; the send span keeps only a
  // bounded excerpt. Use it when the message id proves it is this message's exchange.
  const captured = capturedMessageMetadata(entry, wireRecords);
  const metadata = captured || (isObject(entry.metadata) ? entry.metadata : {});
  const detail = isObject(entry.detail) ? entry.detail : {};
  const payload = {};
  const taskKey = Object.keys(metadata).find((key) => String(key).includes("Task-T"));
  const taskT = taskKey ? metadata[taskKey] : detail.taskT;
  if (typeof taskT === "string" && taskT) payload[taskKey || "Task-T"] = taskT;
  const negotiationKey = Object.keys(metadata).find((key) =>
    String(key).includes("Negotiation-T"),
  );
  const negotiation =
    negotiationKey && typeof metadata[negotiationKey] === "string"
      ? metadata[negotiationKey]
      : detail.negotiationT ||
        (entry.kind === "propose" && typeof entry.content === "string" ? entry.content : "");
  if (typeof negotiation === "string" && negotiation) {
    payload[negotiationKey || "Negotiation-T"] = negotiation;
  }
  const template =
    (typeof metadata.templateUri === "string" && metadata.templateUri) ||
    detail.templateUri ||
    entry.templateUri;
  if (typeof template === "string" && template) payload.templateUri = template;
  return Object.keys(payload).length > 0 ? payload : null;
}

/** Build the headers, JSON body, title and note of one wire entry. */
export function buildWireModel(entry, state, format, copy) {
  if (!isObject(entry)) return null;
  if (format === "x") {
    const payload = extensionPayload(entry, state.wire);
    if (payload === null) return null;
    return {
      title: copy.wire.extensionTitle,
      alternate: false,
      extension: true,
      note: copy.wire.extensionNote,
      nodes: jsonNodes(payload, 0),
    };
  }
  // A server event carries the response frame the peer actually sent. Show it verbatim: the
  // reconstructed status/artifact model below exists only for older runs without a capture.
  if (entry.entryType === "event" && isObject(entry.wireBody)) {
    return {
      title: copy.wire.responseTitle,
      alternate: false,
      note: copy.wire.responseNote,
      nodes: jsonNodes(entry.wireBody, 0),
    };
  }
  if (entry.kind === "message_sent") {
    const detail = isObject(entry.detail) ? entry.detail : {};
    // What actually left the process wins over anything rebuilt from the span. The SDK sends a
    // SendMessageRequest ({"message": {...}}); showing the inner Message object is not the wire.
    const record = outboundRecord(state.wire, entry);
    const headers = record ? capturedHeaders(record) : [copy.wire.noWireCapture];
    if (detail.performative === "TASK_T") {
      const extensionUri =
        (typeof detail.extensionUri === "string" && detail.extensionUri) ||
        (Array.isArray(detail.metadataKeys)
          ? detail.metadataKeys.find((key) => String(key).includes("Task-T"))
          : null) ||
        copy.wire.extensionFallback;
      const templateUri = typeof detail.templateUri === "string" ? detail.templateUri : null;
      const messageId = detail.messageId || "<generated by the SDK>";
      const label = detail.summary || copy.wire.requestLabel;
      // The structured Task-T is the payload under the extension URI. The text part is only a
      // short label; the engineer's natural-language request is never the wire payload.
      const prompt = sentTaskT(entry, state);
      const intent = parseIntent(prompt);
      const metadata = { [extensionUri]: prompt };
      if (templateUri) metadata.templateUri = templateUri;
      // ``samplePhase`` is sample-only bookkeeping and is not sent on the wire, so the fallback
      // body must not invent it. ``sampleRunId`` remains part of the historical sample envelope.
      if (typeof detail.sampleRunId === "string" && detail.sampleRunId) {
        metadata.sampleRunId = detail.sampleRunId;
      }
      const a2aBody = {
        message: {
          role: "ROLE_USER",
          parts: [{ text: label }],
          metadata,
          messageId,
        },
      };
      const plainBody = {
        message: {
          role: "ROLE_USER",
          parts: [{ text: plainSentence(intent, copy.lang) || label }],
          messageId,
        },
      };
      const usePlain = format === "a";
      const plainHeaders = record
        ? headers.filter((line) => !isExtensionHeader(line))
        : headers;
      const modify = detail.summary === "modify ran energy saving task";
      return {
        title: modify ? copy.wire.modifyTitle : copy.wire.requestTitle,
        alternate: true,
        note: usePlain ? copy.wire.plainNote : copy.wire.taskTNote,
        nodes: [
          ...headerNodes(usePlain ? plainHeaders : headers),
          ...jsonNodes(usePlain ? plainBody : capturedBody(record, a2aBody), 0),
        ],
      };
    }
    const plain = detail.protocol === "plain";
    const pure = detail.protocol === "pure";
    const metadata = plain ? {} : sentMetadata(detail, copy);
    const message = {
      role: "ROLE_USER",
      parts: [{ text: detail.summary || "" }],
      ...(Object.keys(metadata).length > 0 ? { metadata } : {}),
      ...(detail.messageId ? { messageId: detail.messageId } : {}),
    };
    return {
      title: plain
        ? copy.wire.plainMessageTitle
        : pure
          ? copy.wire.pureMessageTitle
          : copy.wire.messageTitle,
      alternate: false,
      note: plain ? copy.wire.plainMessageNote : pure ? copy.wire.pureMessageNote : "",
      nodes: [...headerNodes(headers), ...jsonNodes(capturedBody(record, { message }), 0)],
    };
  }
  if (entry.entryType === "event" && entry.kind === "propose") {
    return eventModel(entry, copy.wire.negotiationTitle, "ROLE_AGENT", copy);
  }
  if (entry.entryType === "event" && ["accept", "counter", "reject", "abort"].includes(entry.kind)) {
    return eventModel(entry, copy.wire.decisionTitle, "ROLE_USER", copy);
  }
  if (entry.entryType === "event" && entry.kind === "status") {
    const task = taskPayload(entry);
    // Standard A2A echoes the plain message in the Task history, never a Task-T, so the A2A-T
    // reading of this payload would describe something the run did not send.
    if (task !== null && state.protocol !== "pure") {
      // The first status is the Task object the RAN Agent created, or the running Task a
      // Modify-only run continued. Its history carries the accepted Task-T either way.
      const modified = state.runMode === "modify";
      return {
        title: modified ? copy.wire.taskModifiedTitle : copy.wire.taskCreatedTitle,
        alternate: false,
        note: modified ? copy.wire.taskModifiedNote : copy.wire.taskCreatedNote,
        nodes: jsonNodes({ task }, 0),
      };
    }
    const stateName =
      typeof entry.state === "string" && entry.state ? entry.state : entry.summary || "";
    const text =
      entry.content || (entry.summary && entry.summary !== stateName ? entry.summary : "");
    const status = { state: stateName };
    if (text) status.message = { role: "ROLE_AGENT", parts: [{ text }] };
    const statusUpdate = { status };
    if (isObject(entry.metadata) && Object.keys(entry.metadata).length > 0) {
      statusUpdate.metadata = entry.metadata;
    }
    return {
      title: copy.wire.statusTitle,
      alternate: false,
      note: "",
      nodes: jsonNodes({ statusUpdate }, 0),
    };
  }
  if (entry.entryType === "event" && entry.kind === "artifact") {
    const metadata = isObject(entry.metadata) ? entry.metadata : null;
    return {
      title: copy.wire.artifactTitle,
      alternate: false,
      note: "",
      nodes: jsonNodes(
        {
          artifactUpdate: {
            artifact: {
              name: entry.name || "tool result",
              part: { text: entry.text || entry.summary || "" },
              ...(metadata ? { metadata } : {}),
            },
          },
        },
        0,
      ),
    };
  }
  // Trace spans (and the stand-in built for a row without an entry) are not wire messages:
  // show the span fields so a click on any trace row always has something to read.
  if (entry.entryType === "trace" || (entry.entryType === undefined && entry.spanId)) {
    const detail = isObject(entry.detail) ? entry.detail : {};
    const payload = { kind: entry.kind || "trace", title: entry.title || "" };
    if (entry.summary) payload.detail = entry.summary;
    if (entry.purpose) payload.purpose = entry.purpose;
    if (detail.toolName) payload.tool = detail.toolName;
    if (detail.promptText) payload.promptText = String(detail.promptText);
    if (Object.keys(detail).length > 0 && !detail.toolName && !detail.promptText) {
      payload.detailFields = detail;
    }
    return { title: copy.wire.traceTitle, alternate: false, note: "", nodes: jsonNodes(payload, 0) };
  }
  return {
    title: copy.wire.eventTitle,
    alternate: false,
    note: "",
    nodes: jsonNodes({ kind: entry.kind || "event", text: entry.summary || entry.content || "" }, 0),
  };
}

/** Return the Task-T payload that was actually sent with one message. */
function sentTaskT(entry, state) {
  // Every send span carries its own payload. A two-phase run has a baseline Create prompt and a
  // later Modify prompt; the state's last prompt must not leak into the earlier row.
  const detail = isObject(entry.detail) ? entry.detail : {};
  if (typeof detail.taskT === "string" && detail.taskT) return detail.taskT;
  if (typeof state.taskTPrompt === "string" && state.taskTPrompt) return state.taskTPrompt;
  return typeof state.intentText === "string" ? state.intentText : "";
}

/** Metadata of a negotiation response, using the extension URIs the send span recorded. */
function sentMetadata(detail, copy) {
  const metadata = {};
  if (typeof detail.negotiationT === "string" && detail.negotiationT) {
    const key =
      typeof detail.negotiationUri === "string" && detail.negotiationUri
        ? detail.negotiationUri
        : copy.wire.negotiationFallback;
    metadata[key] = detail.negotiationT;
  }
  if (typeof detail.taskT === "string" && detail.taskT) {
    const key =
      typeof detail.extensionUri === "string" && detail.extensionUri
        ? detail.extensionUri
        : copy.wire.extensionFallback;
    metadata[key] = detail.taskT;
  }
  if (typeof detail.templateUri === "string" && detail.templateUri) {
    metadata.templateUri = detail.templateUri;
  }
  return metadata;
}

/** Return the Task envelope of a status event, from the entry or its trace detail. */
function taskPayload(entry) {
  if (isObject(entry.task)) return entry.task;
  const detail = isObject(entry.detail) ? entry.detail : {};
  const event = isObject(detail.event) ? detail.event : null;
  return event && isObject(event.task) ? event.task : null;
}

function eventModel(entry, title, role, copy) {
  // The event's full negotiation text belongs under the extension key; the negotiation type is
  // not the payload. A decision without content is a readable summary, not a wire message.
  const metadata = {};
  if (typeof entry.content === "string" && entry.content) {
    metadata["Negotiation-T"] = entry.content;
  }
  if (typeof entry.templateUri === "string" && entry.templateUri) {
    metadata.templateUri = entry.templateUri;
  }
  return {
    title,
    alternate: false,
    note: "",
    nodes: jsonNodes(
      {
        message: {
          role,
          parts: [{ text: entry.content || entry.summary || actionLabel(copy, entry.kind) }],
          ...(Object.keys(metadata).length > 0 ? { metadata } : {}),
        },
      },
      0,
    ),
  };
}

/** Render the captured request line and headers of one outbound record. */
function capturedHeaders(record) {
  const url = typeof record.url === "string" ? record.url : "";
  // The path carries no host: the inspector shows what went on the wire, not the origin.
  let path = url;
  try {
    const parsed = new URL(url);
    path = parsed.pathname + parsed.search;
  } catch {
    // A relative or malformed URL is shown verbatim.
  }
  const method = typeof record.method === "string" ? record.method : "POST";
  const lines = [`${method} ${path} HTTP/1.1`];
  const headers = isObject(record.headers) ? record.headers : {};
  for (const [name, value] of Object.entries(headers)) {
    // The NMS client already redacts credentials to "***" when it writes the log.
    lines.push(`${name}: ${value}`);
  }
  return lines;
}

/** Pick the outbound record of one displayed message, never another round's exchange. */
function outboundRecord(records, entry) {
  if (!Array.isArray(records)) return null;
  const outbound = records.filter(
    (record) => isObject(record) && record.dir === "out" && typeof record.url === "string",
  );
  if (outbound.length === 0) return null;
  // The send span and the request body carry the same A2A message id, so a multi-round run never
  // shows the previous message's body under the current trace row.
  const messageId = entryMessageId(entry);
  if (messageId) {
    const exact = outbound.find((record) => recordMessageId(record) === messageId);
    if (exact) return exact;
  }
  return (
    outbound.find((record) => {
      const text = JSON.stringify(record.body ?? "");
      return text.includes("Task-T");
    }) || outbound[0]
  );
}

/** A2A message id of one trace entry (send span detail or transcript entry). */
function entryMessageId(entry) {
  const detail = isObject(entry) && isObject(entry.detail) ? entry.detail : {};
  if (typeof detail.messageId === "string") return detail.messageId;
  if (isObject(entry) && typeof entry.messageId === "string") return entry.messageId;
  return "";
}

/** A2A message id of one captured request body (``{"message": {...}}``). */
function recordMessageId(record) {
  if (!isObject(record) || !isObject(record.body)) return "";
  const body = record.body;
  const message = isObject(body.message) ? body.message : body;
  return typeof message.messageId === "string" ? message.messageId : "";
}

/** Metadata of the captured request whose message id matches this entry, when there is one. */
function capturedMessageMetadata(entry, records) {
  if (!Array.isArray(records)) return null;
  const messageId = entryMessageId(entry);
  if (!messageId) return null;
  for (const record of records) {
    if (!isObject(record) || record.dir !== "out" || recordMessageId(record) !== messageId) {
      continue;
    }
    const body = isObject(record.body) ? record.body : {};
    const message = isObject(body.message) ? body.message : body;
    return isObject(message.metadata) ? message.metadata : null;
  }
  return null;
}

/** The body that was really sent; the documented envelope is only a no-capture fallback. */
function capturedBody(record, fallback) {
  if (record && record.body !== undefined && record.body !== null) return record.body;
  return fallback;
}

/** Whether one captured header line is an A2A extension opt-in. */
function isExtensionHeader(line) {
  const colon = line.indexOf(":");
  if (colon < 0) return false;
  const name = line.slice(0, colon).trim().toLowerCase();
  return name === "a2a-extensions" || name === "x-a2a-extensions";
}

function headerNodes(lines) {
  const nodes = [];
  for (const line of lines) {
    if (line.startsWith("POST ")) {
      nodes.push(el("span", "wire-request", line), document.createTextNode("\n"));
      continue;
    }
    const colon = line.indexOf(":");
    if (colon < 0) {
      nodes.push(document.createTextNode(line + "\n"));
      continue;
    }
    const name = line.slice(0, colon);
    const value = line.slice(colon + 1);
    nodes.push(
      el("span", name === "A2A-Extensions" ? "wire-ext-key" : "wire-head-key", name),
      el("span", "wire-head-value", value),
      document.createTextNode("\n"),
    );
  }
  nodes.push(document.createTextNode("\n"));
  return nodes;
}

function jsonNodes(value, depth) {
  const pad = "  ".repeat(depth);
  if (Array.isArray(value)) {
    if (value.length === 0) return [document.createTextNode("[]")];
    const nodes = [document.createTextNode("[\n")];
    value.forEach((item, index) => {
      nodes.push(document.createTextNode(pad + "  "));
      nodes.push(...jsonNodes(item, depth + 1));
      nodes.push(document.createTextNode(index === value.length - 1 ? "\n" : ",\n"));
    });
    nodes.push(document.createTextNode(pad + "]"));
    return nodes;
  }
  if (isObject(value)) {
    const entries = Object.entries(value).filter(([, item]) => item !== undefined);
    if (entries.length === 0) return [document.createTextNode("{}")];
    const nodes = [document.createTextNode("{\n")];
    entries.forEach(([key, item], index) => {
      nodes.push(
        document.createTextNode(pad + "  "),
        el("span", "wire-json-key", `"${key}"`),
        document.createTextNode(": "),
      );
      nodes.push(...jsonNodes(item, depth + 1));
      nodes.push(document.createTextNode(index === entries.length - 1 ? "\n" : ",\n"));
    });
    nodes.push(document.createTextNode(pad + "}"));
    return nodes;
  }
  if (typeof value === "string") {
    // The wire view is the raw payload: render every string in full (the container scrolls).
    // Truncating here hid the end of real Task-T prompts and looked like data loss.
    return [el("span", "wire-json-string", JSON.stringify(value))];
  }
  if (typeof value === "number") return [el("span", "wire-json-number", String(value))];
  if (typeof value === "boolean") return [el("span", "wire-json-bool", String(value))];
  if (value === null) return [el("span", "wire-json-null", "null")];
  return [document.createTextNode(String(value))];
}
