// Manual end-to-end smoke for the two-phase planned-outage presentation.
//
// This is deliberately NOT part of the Python/Node unit harness: it needs a running demo UI and a
// Chromium/Edge instance with remote debugging. It is the local equivalent of a browser E2E check
// before a demo (the CI workflow intentionally does not run it).
//
// Prerequisites:
//   1. the sample UI is running, e.g.
//        python run_demo.py --ui --model-mode offline --ui-port 8080
//   2. Edge/Chrome is running with --remote-debugging-port=9222
//
// Run:
//   node tests/js/presentation_smoke.mjs
//
// Optional environment:
//   A2AT_SMOKE_UI=http://127.0.0.1:8080/
//   A2AT_SMOKE_CDP=http://127.0.0.1:9222
//   A2AT_SMOKE_TIMEOUT_MS=120000

const uiBase = process.env.A2AT_SMOKE_UI || "http://127.0.0.1:8080/";
const cdpBase = process.env.A2AT_SMOKE_CDP || "http://127.0.0.1:9222";
const timeoutMs = Number(process.env.A2AT_SMOKE_TIMEOUT_MS || "120000");
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

function fail(message) {
  console.error(`presentation smoke FAILED: ${message}`);
  process.exit(1);
}

let target;
try {
  target = await (
    await fetch(`${cdpBase}/json/new?${encodeURIComponent(uiBase)}`, { method: "PUT" })
  ).json();
} catch (error) {
  fail(`cannot create a CDP target at ${cdpBase}: ${error}`);
}

const socket = new WebSocket(target.webSocketDebuggerUrl);
const pending = new Map();
const exceptions = [];
let nextId = 0;

socket.onmessage = (event) => {
  const message = JSON.parse(event.data);
  if (message.id) {
    const handler = pending.get(message.id);
    if (handler) {
      pending.delete(message.id);
      handler(message);
    }
    return;
  }
  if (message.method === "Runtime.exceptionThrown") {
    exceptions.push(message.params.exceptionDetails?.text || "exception");
  }
};
await new Promise((resolve) => {
  socket.onopen = resolve;
});

function send(method, params = {}) {
  return new Promise((resolve, reject) => {
    const id = ++nextId;
    pending.set(id, (message) =>
      message.error ? reject(new Error(JSON.stringify(message.error))) : resolve(message.result),
    );
    socket.send(JSON.stringify({ id, method, params }));
  });
}

async function evaluate(expression) {
  const result = await send("Runtime.evaluate", {
    expression,
    returnByValue: true,
    awaitPromise: true,
  });
  if (result.exceptionDetails) throw new Error(result.exceptionDetails.text || "evaluate failed");
  return result.result.value;
}

await send("Runtime.enable");
await send("Page.enable");
await send("Network.enable");
await send("Network.setCacheDisabled", { cacheDisabled: true });
await send("Page.navigate", { url: `${uiBase}?brand=orange&v=${Date.now()}` });

for (let attempt = 0; attempt < 60; attempt += 1) {
  const ready = await evaluate(
    "Boolean(document.querySelectorAll('#scenario-select option').length > 1 && document.querySelector('#start-quick') && document.querySelector('#continuity'))",
  );
  if (ready) break;
  await sleep(500);
}

const selected = await evaluate(`(async () => {
  const select = document.querySelector('#scenario-select');
  select.value = 'outage';
  select.dispatchEvent(new Event('change', { bubbles: true }));
  await new Promise((resolve) => setTimeout(resolve, 700));
  return {
    value: select.value,
    description: document.querySelector('#scenario-desc')?.textContent || '',
    start: Boolean(document.querySelector('#start-quick')),
  };
})()`);
if (!selected.start) fail("the start button is missing");
if (selected.value !== "outage") fail(`could not select outage (value=${selected.value})`);
if (!selected.description.includes("Modify")) fail(`unexpected outage description: ${selected.description}`);

await evaluate("document.querySelector('#start-quick').click()");
const deadline = Date.now() + timeoutMs;
let final = null;
while (Date.now() < deadline) {
  const snapshot = await evaluate(`(() => {
    const continuity = document.querySelector('#continuity');
    return {
      continuity: continuity ? continuity.textContent.trim() : '',
      continuityHidden: continuity ? continuity.hidden : true,
      traceHasModify: (document.querySelector('#p-trace')?.textContent || '').includes('Modify Task-T'),
      result: (document.querySelector('.result-card')?.textContent || '').trim(),
    };
  })()`);
  if (snapshot.result) {
    final = snapshot;
    break;
  }
  await sleep(1500);
}

if (final === null) fail(`the run did not reach a result within ${timeoutMs}ms`);
if (!final.continuity.includes("Same Task")) fail(`continuity strip missing: ${final.continuity}`);
if (!/plan-cell-002-\d+/.test(final.continuity)) {
  fail(`continuity strip has no plan id: ${final.continuity}`);
}
if (!final.continuity.includes("10%") || !final.continuity.includes("20%")) {
  fail(`continuity strip has no 10% -> 20% change: ${final.continuity}`);
}
if (!final.result.includes("20%")) fail(`result card has no 20%: ${final.result}`);
if (!final.result.toLowerCase().includes("modified")) fail(`result card has no modified note: ${final.result}`);
if (!final.traceHasModify) fail("the trace has no Modify Task-T row");
if (exceptions.length > 0) fail(`browser exceptions: ${exceptions.join(" | ")}`);

console.log("presentation smoke: ok");
console.log(`  continuity: ${final.continuity.replace(/\s+/g, " ")}`);
console.log(`  result: ${final.result.replace(/\s+/g, " ").slice(0, 180)}`);
socket.close();
