# RAN energy saving: OSS and RAN NMS negotiate, then execute (A2A-T solution)

> **In one sentence:** the OSS asks for an energy-saving goal; the RAN network management system
> negotiates to fill or correct it, then evaluates, applies and reports — automatically, with a
> full audit trail.

```mermaid
flowchart LR
    OSS["OSS/BSS<br/>client side<br/>requests energy saving"] -->|"A2A-T pre-task negotiation"| NMS["RAN network management<br/>validate · negotiate · evaluate · apply"]
    NMS -->|"plan applied"| CELL["Cells / base stations"]
    NMS -->|"task status + report"| OSS
```

## 1. System-to-system interaction

The OSS and the RAN NMS do two things: **agree on the goal, then execute the plan**.

| System | Role | Responsibility |
| --- | --- | --- |
| OSS/BSS | client side | requests the energy-saving goal and decides accept / reject / counter |
| RAN NMS | execution side | validates the goal, negotiates, evaluates and applies the plan, writes the report |

```mermaid
sequenceDiagram
    participant O as OSS/BSS
    participant N as RAN NMS
    O->>N: energy-saving goal (for example "save energy while keeping the guaranteed rate")
    N->>O: proposal: add a value / fix a conflict / offer an alternative
    O->>N: accept / reject / abort
    N->>O: task status + energy-saving report
```

Negotiation may take two or three rounds, but **the task is created only after both sides agree**,
so a wrong goal is never applied.

Typical problems map to three kinds of negotiation:

- missing parameters;
- overlapping time segments;
- infeasible conditions (for example a guaranteed rate during an outage);
- an unknown area: the NMS ranks its served areas against the requested name and asks the engineer
  to choose one, keeping the requested throughput windows.

## 2. Typical scenarios

### 2.1 Missing value: the NMS asks, the OSS fills it

```mermaid
sequenceDiagram
    participant O as OSS
    participant N as RAN NMS
    O->>N: energy-saving goal (no time-segmented guaranteed rate)
    N->>O: please provide: the time-segmented guaranteed rate
    O->>N: accept and fill the value
    N->>O: evaluate → apply → report
```

**Outcome:** the task completes; no manual ticket is needed.

### 2.2 Overlapping segments: correct before executing

```mermaid
sequenceDiagram
    participant O as OSS
    participant N as RAN NMS
    O->>N: energy-saving goal (two overlapping windows)
    N->>O: proposal: provide non-overlapping segments
    O->>N: counter-proposal: corrected segments
    N->>O: confirm the revised plan
    O->>N: accept
    N->>O: evaluate → apply → report
```

**Outcome:** the conflict is fixed before any task exists, so nothing wrong is applied.

### 2.3 Infeasible condition: the NMS proposes an alternative

```mermaid
sequenceDiagram
    participant O as OSS
    participant N as RAN NMS
    O->>N: energy-saving goal (guaranteed rate during an outage)
    N->>O: proposal: use a feasible alternative
    O->>N: accept
    N->>O: evaluate → apply → report
```

**Outcome:** the infeasible plan is never applied; the alternative is executable.

The other two outcomes are simple conclusions:

| Scenario | Outcome |
| --- | --- |
| the OSS cannot provide a required value | negotiation ends; no task is created |
| counter-proposals never converge | the NMS aborts at the round limit; no task is created |

## 3. Customer value

| Pain | Without pre-task negotiation | With this solution |
| --- | --- | --- |
| Missing parameters | the task fails, or someone asks by ticket | the NMS asks; the OSS fills the value automatically |
| Conflicting targets | a wrong plan may be applied | the conflict is fixed before the task exists |
| Infeasible conditions | the apply fails and has to be rolled back | an alternative is proposed; nothing runs until accepted |
| Audit | emails and tickets | every proposal, decision and apply is recorded |

## 4. Demo

```bash
# One explicit configuration file per launch script; the launcher no longer guesses
python run_demo.py --env-file configs/yellow-zone.env --nms --ui
# equivalent: A2AT_SAMPLE_ENV_FILE=configs/yellow-zone.env python run_demo.py ...
# CLI (offline, deterministic, no key): planned-outage baseline Create, no negotiation
python run_demo.py --scenario outage --mode auto --model-mode offline
# Web UI (presentation view; opens on the complete use case 1)
python run_demo.py --ui --model-mode offline
# Huawei-themed presentation (the page can also switch it, and ?brand=huawei overrides)
python run_demo.py --ui --brand huawei --model-mode offline
# Web UI with approval 1 (intent) and approval 2 (MML in MIE)
python run_demo.py --ui --mode confirm --approval-gate --scenario target --model-mode offline
# Unknown area: choose the served area manually
python run_demo.py --ui --mode manual --scenario unknown_area --model-mode offline
# Optional: expose the OSS side as an A2A backend
python run_demo.py --ui --gateway --ui-port 8080 --gateway-port 8001 --model-mode offline
# Share the Web UI on the LAN: explicit tokenless opt-in (trusted network only)
python run_demo.py --ui --ui-host 0.0.0.0 --allow-insecure-bind --model-mode offline
# Or keep a token and share a link; the page stores it in a same-origin cookie
A2AT_SAMPLE_AUTH_TOKEN=<token> python run_demo.py --ui --ui-host 0.0.0.0 --model-mode offline
# colleagues open http://<your-lan-ip>:8080/?token=<token>
```

In `--mode manual` / `--mode confirm`, the decision card also has a free-text box. With a live
model, **Parse with the OSS Agent** turns the engineer's answer into `filled_params` (information)
or `counter_params` (target/feasibility) and shows a preview before anything is sent. Offline runs
have no answer parser; the quick Accept/Reject buttons and the served-area selector still work.

`--ui-host 0.0.0.0` exposes only the Web UI; the registry and the NMS server stay on loopback.
Without `A2AT_SAMPLE_AUTH_TOKEN` the launcher refuses a non-loopback bind unless
`--allow-insecure-bind` is passed explicitly; in that mode anyone who can reach the port can start
runs, so use it only on a trusted network. With a token, share the `/?token=…` link: the page copies
the token into a same-origin `a2at_ui_token` cookie and removes it from the address bar, so both
`fetch` and the SSE stream authenticate (a browser cannot set an `Authorization` header on an
`EventSource`).

With `--gateway`, the OSS side is itself an A2A agent: an external caller such as Gemini Enterprise
sends one request and receives progress notifications (negotiation and execution milestones) before
the final energy-saving report, either on a stream or through a registered webhook.

The Web UI opens on the presentation view: a 16:9 stage with the engineer, the OSS Agent, the RAN
Agent, the MIE and the radio sites, plus the A2A-T channel between them. The presentation supports
two brand palettes: **Orange** (the reference demo, default) and **Huawei** (the warm-canvas,
outlined-node ICT style). The top-bar button switches them at runtime and remembers the choice;
`--brand huawei` or `?brand=huawei` selects the start-up default. Eight examples cover use
case 1 (night-time energy saving, with and without negotiation) and use case 2 (planned power
outage). The outage example creates a site-level baseline; a running Task is modified through the
separate **Modify a running task** entry, which sends a real Modify Task-T on the selected Task.
The stage follows the live run at a human pace through nine
steps: request, structured
Task-T, approval 1 (intent), Task-T sent, negotiation when needed, strategy and task start, data
collection (days 2 to 8, compressed), approval 2 (MML in MIE) and apply and report. The request box
shows the exact text a network engineer would type, for example “Create an energy saving task for
Region A with these guaranteed throughputs: …”; that text is what the OSS client sends, and the
OSS Agent reads it into its own structured intent for display and approval. The **Unknown area**
example lists the served areas after a fuzzy match and lets the engineer choose one; selecting an
area only fills the region, so the requested throughput windows are kept. After the Task-T is
accepted the RAN Agent reports `SUBMITTED`, strategy working, strategy ready and task started before
data collection begins. The right column keeps the evidence: what the OSS Agent understood (with
the 24-hour throughput chart), the
negotiation turns, an **On the wire** inspector, and the merged session trace. The normalized
request folds away behind its heading, and the grip above the negotiation panel trades height with
the intent panel so a long message fits in the inspector. On the wire shows the
captured redacted exchange from the run's `wire.jsonl` — request line, headers (including the real
`traceparent`) and the exact `SendMessageRequest` JSON body the SDK sent (`{"message": …}`, with the
Task-T metadata in A2A-T mode). The a2a-sdk HTTP transport is captured too, so local A2A-T and A2A
runs show the real request instead of a message object rebuilt from the trace; when a run
captured none, the panel says so instead of inventing one. Its **Plain A2A** toggle shows the
same request as one natural-language sentence without the extension header or Task-T fields (a
format view of the same message). Clicking a trace row selects that message in the inspector. The
trace labels each row with its demo day (Day 1, Days 2–8, Day 9, Day 9+) and message direction
(OSS → RAN, RAN → OSS, MIE ⇄ Engineer); late server spans are merged by event time and a status and
its matching span collapse to one row.

When the client talks to the real NMS (`--nms`), the toolbar also offers **Modify a running task**.
It reads `GET /api/nms/tasks`, backed by the NMS `/tasks` endpoint, and lists SUBMITTED and WORKING
Tasks (with their real state); the picker refreshes every five seconds while it is open. Selecting
one records its task id and context, switches the example to the planned-outage modification, fills
the editor with a Modify-only request, and waits: nothing is sent on selection. Pressing Start posts
the target ids; the run has no baseline phase, and its first Task-T is the real Modify continuation
of the selected Task, even while it is still SUBMITTED. A Task that reaches a terminal state before
the send is rejected with a clear message. The entry supports A2A-T only. When the NMS stream goes
quiet, the run keeps tracking the Task through a background poll (a page that stays subscribed keeps
the poll alive; closing it stops the tracker after 30 seconds). For local rehearsal against the
simulator, `A2AT_SAMPLE_NMS_SIM_HOLD_WORKING=1` keeps a newly created Task WORKING so the picker has
a target; continuing it completes it normally.

### Tester console (Excel import)

Open `/tester` on the same UI process (the launcher prints the URL). It is a separate entry page
for testers: download the `.xlsx` template, fill one row per case, import the workbook, and run
the batch. The console is intentionally Chinese-only because it is an internal tool for the
Chinese test team. Each row carries the protocol (`A2A-T` / `A2A`) and an optional user request; the
scenario column is optional because a real customer test is usually just a business request. With
an empty scenario the sample still uses the `night` profile internally, but asserts only the final
state (`task_completed` by default); with an explicit scenario, blank expectation cells inherit
that scenario's final state, negotiation kind and round count. Cases inside one batch run
sequentially, while separate batches (and separate webhook runs) run concurrently; the page
shows the verdict, actual final state, negotiation kind/rounds, duration and the run's `wire` /
`trace` links; the results can be exported back to `.xlsx`. An empty approval cell defaults to
`approve`, so an unattended batch does not wait for the approval window; `reject` tests the
rejection path and `auto` leaves the gate to a human or the timeout. Batches are persisted under
`runs/tester`, so reloading the page reopens the latest one; **Retry failed** rebuilds a draft from
the failed, errored or cancelled rows. On a real-NMS deployment the page shows a warning and asks
for confirmation before starting. Each case records its wall-clock duration, model/tool/approval
latency, LLM call count and token usage (prompt/completion/total, split by client and server),
including the provider-reported cached-prefix tokens and cache hit rate when the endpoint returns
them; the exported workbook has **Testing results**, **Metrics** and **Summary** sheets. Scripted
offline LLM spans are shown as unavailable instead of reporting invented token counts. Transport and
discovery are still chosen by the backend per protocol, not by the sheet.

### Internal run console

`/runs` is a diagnostic page for the test team (the launcher prints its address), separate from the
presentation page. It reads persisted client runs from SQLite instead of presentation state, so a
run can be inspected after a page reload or a UI restart:

- the list filters by protocol / status / source / scenario and searches the request, run id and
  conversation id, showing duration, tokens and creation time;
- the detail has Overview, Metrics, Timeline, Negotiation, Wire and Raw JSON tabs. Metrics include
  token usage (with cached tokens and hit rate), the latency split and accuracy; the timeline comes
  from the persisted transcript; negotiation shows protocol events, client decisions and turns; wire
  reads `wire.jsonl` / `wire-oss.jsonl`;
- **request origin** is recorded when the run starts: source kind (`presentation` / `explorer` /
  `tester` / `gateway` / `api` / `cli`), channel, client address, `X-Forwarded-For`, Host, Origin,
  Referer, User-Agent, Accept-Language, request id, entry path and receive time. Tester batches also
  record the batch/case, and the gateway records Task / Context. A `token` query parameter in the
  referer is removed before storage.
- the list auto-refreshes every 5 seconds (optional), and a selected run is addressable through
  `#run=<id>`.

The page is Chinese-only, like the tester console, because it is an internal tool.

### Operator intent layer

The operator's text is the source of truth. The default `processed` mode inserts the business read
a real OSS performs, but it never rewrites the request:

```text
operator request → ADK intent agent (ProcessedIntent: display / approval only)
                 → approval 1 → Task-T extracted from the same operator text
```

- Known scenarios are ordinary example requests. There is no built-in intent table and no
  text-to-intent matching: the ADK intent agent reads whatever text the page shows and the operator
  sends. In live mode it uses the configured model; in offline mode the deterministic fake model
  cannot answer the intent schema, so the intent falls back to the raw text with a warning.
- The request text is WYSIWYG. The page sends the editor text verbatim and A2A-T extracts its
  Task-T from it. `normalizedText` / `plainText` are the operator's own text; the structured
  fields are what the agent read.
- The business layer never repairs a gap or conflict. A missing throughput target, an unknown
  area and overlapping windows stay in the text, so the A2A-T negotiation still happens.
- The negotiation accept is built from the parsed intent plus what the ADK decision agent puts in
  `filled_params`; the decision is authoritative and the sample fabricates no business value when
  the model omits one.
- The sample mounts a small `prompt_resources/` overlay through `A2AT_PROMPT_SOURCE_TYPE=local_file`.
  It refines this scenario's slot schema only (rate targets belong in Task Context, a missing goal
  is null rather than a "Not provided" string); every other SDK resource still comes from the
  packaged bundle.
- A2A keeps the same story inside the OSS Agent: the engineer client sends the raw request,
  the OSS Agent reads it into an intent, publishes it, and forwards the same text to RAN.
- The presentation page shows the operator request and the structured intent fields side by side;
  the trace records `intent_processed`.
- `processed` is the default. Use `A2AT_SAMPLE_INTENT_MODE=raw`, `--intent-mode raw` or
  `POST /api/runs {"intentMode":"raw"}` to skip the intent reading; the text is still what gets
  sent. Gateway input is always treated as raw.

### Pre-send Task-T validation

In A2A-T mode the presentation page validates the rendered Task-T with the SDK content pipeline
before sending it. The gate runs after rendering and before approval 1, so a blocked request is
never sent and no negotiation starts.

- `A2ATClient.validate_task_prompt_and_data_filling` is the client counterpart of the server
  method and uses the same `DefaultContentValidator` and semantic validation prompt.
- Errors that map to a server negotiation kind (`INFORMATION` / `TARGET` / `FEASIBILITY`) are
  negotiable and still go out, so the negotiation scenarios keep working. A missing field inside
  one entry (`content.entry_field_missing`, for example a missing Rate) is an information
  negotiation, not a hard block.
- **The OSS agent intercepts its own required fields.** After the SDK extracts the caller-schema
  parameters, a missing required value (for example the guaranteed rate) stops the request at the
  OSS layer: nothing is sent, and the validation card asks the operator to supply it. This is the
  business gate of `TASK_PARAM_SCHEMA.required`, separate from the network's own Task-T
  negotiation. Fields the caller schema does not require still reach the server and are negotiated
  there. Curated demonstration scenarios that deliberately omit a required value (the information /
  reject / abort scenarios) skip this gate so the server-side negotiation remains demonstrable;
  operator-entered or edited requests always enforce it.
- Unmapped errors block the send: the run ends `finished` with `final_state=input_invalid`, records
  a `task_validation` trace, and the page shows the issues inline.
- A business slot-extraction failure (`slot.constraint_violated` and friends) happens before any
  Task-T exists, so it is surfaced the same way: a blocked `task_validation` record with the
  structured slot errors, and the same edit-and-retry card. Infrastructure failures still raise.
- LLM/infra failures degrade to a warning and let the request through; a model outage cannot stop
  the demo.
- The selected-Task Modify Task-T uses the same gate; a blocked Modify sends nothing.
- Edit the original request in the validation card to start a **new** run; the failed run stays in
  the history and is linked with `source.parentRunId`.
- `A2AT_SAMPLE_PRE_SEND_VALIDATION=on|always|off` selects the scope; `on` is the default and
  covers the presentation page only.

### Langfuse observability

A live client run can export its Run Explorer spans to Langfuse as one trace per run:

- set `LANGFUSE_PUBLIC_KEY` and `LANGFUSE_SECRET_KEY` (optionally `LANGFUSE_BASE_URL`);
- `LANGFUSE_BASE_URL` is the SDK reporting endpoint and may point at a proxy;
  `A2AT_SAMPLE_LANGFUSE_UI_BASE_URL` is the browser-facing UI base used only for the trace link
  (default `https://us.cloud.langfuse.com`);
- `A2AT_SAMPLE_LANGFUSE_ENABLED=0` forces the export off, and
  `A2AT_SAMPLE_LANGFUSE_CAPTURE_CONTENT=0` sends only model, token, status and timing metadata;
- offline/scripted runs are not exported.

The trace follows `run → phase → negotiation round`; `llm` spans become generations with model and
token usage, `tool` spans become tools, and every other span stays a span. The presentation ending
card and the Run Explorer **Exports** panel link to the trace. Export is best effort: a failure is
shown as a warning and never changes the run result. `langfuse` is an optional sample dependency;
without it, or without keys, the exporter is a no-op.

Client and server spans share one trace. Every local A2A call carries the client trace id as a W3C
`traceparent` (plus `X-A2AT-Run-Id`, conversation and user headers); the RAN, OSS and MIE servers
attach that context before the a2a-sdk handler spans are created, so the server-side `SendMessage` /
`CancelTask` spans land in the same Langfuse trace. If a server cannot join, its bridge span carries
`langfuse.trace.metadata.runId` so the trace is still searchable by run. To export the server spans,
point the SDK's OTLP exporter at Langfuse (`OTEL_EXPORTER_OTLP_ENDPOINT` and
`OTEL_EXPORTER_OTLP_HEADERS`); `OTEL_*`/`LANGFUSE_*` keys from the selected `.env` are exported to
the child processes. The vendor NMS keeps the run id as its `traceparent` because it does not report
to the same project.

`POST /api/runs` also accepts an incoming W3C `traceparent`. When an instrumented caller starts a
run, the UI exporter adopts that parent, so the UI, OSS and downstream hops join the caller's trace
instead of opening an unrelated one. Each Run Explorer envelope carries `traceId`, `otelSpanId` and
`otelParentId`, so an OSS span can be inspected (or re-exported) outside the UI. An outbound local
A2A call uses the sender's current span id as the `parent-id` — on a server hop that is the bridge
span created for the request, never the span that called it.

`A2AT_SAMPLE_TRACE_SPAN_LEVEL` controls how much of the Run Explorer stream reaches Langfuse.
`phase` (default) keeps `run_started`, `message_sent`, LLM/tool calls, one representative per phase
and the final status, and drops the per-event status noise; `minimal` keeps only LLM/tool calls and
the run result (about a third of the previous volume); `full` exports every span. The Web UI
timeline always keeps every span, so diagnosis is unaffected. Retries that reuse the run `traceId`
also reuse the Langfuse trace.

### Real vendor NMS mode

The sample can also act as the OSS client of a real vendor NMS. With `--nms`, the launcher starts
no local registry, RAN Agent or MIE: the A2A-T client obtains a session token from the NMS token
endpoint and calls its A2A surface directly. `--nms --protocol a2a` keeps the local OSS Agent
(engineer UI → OSS Agent → NMS) and drops only the RAN Agent and MIE.

A real `--nms` run uses **`POST /a2a/json/message:send` plus a webhook** (the vendor's direct
machine-to-machine mode). The local NMS simulator keeps the SSE `message:stream` contract.

```bash
# Real NMS: fill A2AT_SAMPLE_NMS_* and a reachable A2AT_SAMPLE_WEBHOOK_HOST in .env first
python run_demo.py --nms --ui --brand huawei

# Local NMS simulator: same client path, no lab network, no real network change
python run_demo.py --nms --nms-sim --ui --brand huawei
```

Required `.env` keys:

```ini
A2AT_SAMPLE_NMS_HOST=<nms-host>
A2AT_SAMPLE_NMS_TOKEN_USER=<northbound-user>
A2AT_SAMPLE_NMS_TOKEN_VALUE=<password>
# NMS-mode defaults; override them for another deployment.
A2AT_SAMPLE_NMS_REGION=A2ATestRegion
A2AT_SAMPLE_NMS_SITES=NRES_XCS_6102937,LTE_26441_LNR_OCL
A2AT_SAMPLE_NMS_VERIFY_TLS=false
# The real NMS must be able to reach this address and the webhook port.
A2AT_SAMPLE_WEBHOOK_HOST=<client-lan-address>
A2AT_SAMPLE_WEBHOOK_PORT=9000
# Raw exchange log (default on in NMS mode; set 0 to keep business payloads off disk)
A2AT_SAMPLE_NMS_WIRE_LOG=1
```

Only the host is required. The sample derives the standard 910 surface from it:
`https://<host>:31127/a2a/json` and
`https://<host>:27417/rest/plat/smapp/v1/sessions`. For a non-standard deployment, override
`A2AT_SAMPLE_NMS_SCHEME`, `A2AT_SAMPLE_NMS_A2A_PORT` / `A2AT_SAMPLE_NMS_TOKEN_PORT`, or set the
full `A2AT_SAMPLE_NMS_A2A_BASE` / `A2AT_SAMPLE_NMS_TOKEN_URL`; an explicit full URL wins.

The test environment uses a self-signed certificate, so TLS verification is off by default; set
`A2AT_SAMPLE_NMS_VERIFY_TLS=true` only for a trusted chain. The client sends both accepted auth
headers (`Authorization: Bearer` and `X-Auth-Token`) and both extension-header spellings
(`A2A-Extensions` and `X-A2A-Extensions`).

In NMS mode `A2AT_SAMPLE_NMS_REGION` and `A2AT_SAMPLE_NMS_SITES` replace the neutral demo region
(`Region A` / `Region B`) and the scenario site list in every layer of the run: the request the page
pre-fills and the operator sees, the captured intent (`rawRequest`, `region`, `normalizedText`), and
every Task-T sent to the vendor — the initial prompt, the accept refill and the selected-Task
Modify.
They default to `A2ATestRegion` and the two 910 sites above; other regions and activation windows are
rejected. Nothing silently swaps the region between the stage and the wire. The local three-hop demo
keeps the neutral names and is unaffected. For example: “Create a RAN energy saving task in the
A2ATestRegion region; activation 00:00-06:00 with 30 Mbps for each cell.”

Vendor contract notes:

- The NMS returns structured negotiation metadata only when the request declares **both** Task-T
  and Negotiation-T. The client does that on the initial request; declaring Task-T alone produces a
  bare Message with empty metadata.
- One message may carry only one `-T` metadata key. In NMS mode an accept therefore carries
  Negotiation-T only; the local demo server keeps the refilled Task-T on the accept.
- A continuing PROPOSE can arrive inside an `error` envelope. The client records it in
  `wire-error.jsonl` and stops with a clear protocol error instead of silently ending.
- `negotiationContext.id` is the server's negotiation handle, not the A2A `contextId`. A reply
  carries the proposal's negotiation id; a continuing negotiation may open a **new** negotiation id
  on the same context id.
- After an accept/counter/reject, the client keeps reading the response stream until it idles
  (`A2AT_SAMPLE_NMS_READ_AFTER_SEND`, on by default) so a new proposal on that stream is not lost.
- The idle window is 45 s by default (`A2AT_SAMPLE_NMS_IDLE_TIMEOUT_SECONDS`); `: heartbeat`
  frames do not count as progress.
- Task cancellation returns HTTP 503; the client treats it as a known limitation and never retries
  the send.
- The presentation toolbar has **Cancel task** next to **Edit request**. It asks the remote NMS to
  cancel its Task first (best effort, currently HTTP 503) and then stops the local run, so a
  long-running or repeatedly tested task can be stopped without restarting the page.
- The NMS publishes no AgentCard, so discovery uses a static card; a real registry integration is
  a later step.

The raw exchange is recorded in `runs/<runId>/wire.jsonl` (the A2A OSS Agent writes
`wire-oss.jsonl` in the same directory) and failed frames in `wire-error.jsonl`, with credentials
redacted.

The webhook receiver is **process-level**: it binds `A2AT_SAMPLE_WEBHOOK_PORT` once at startup and
every Web UI "Start" reuses the same callback; a new receiver is never started per request. One
webhook run runs at a time in a process and a second one gets an explicit conflict message. Without
`A2AT_SAMPLE_WEBHOOK_PUBLIC_URL`, a busy configured port falls back to an OS-assigned one; with a
public URL (a gateway forwards a fixed address) it never silently moves and instead explains that
the port is in use.

`--protocol both --nms` runs two receivers: the UI keeps `A2AT_SAMPLE_WEBHOOK_PORT` and the OSS
Agent takes `A2AT_SAMPLE_OSS_WEBHOOK_PORT` (default: the next port).
`A2AT_SAMPLE_OSS_WEBHOOK_PUBLIC_URL` is required for a real NMS run and is never inherited from the
UI callback; both public addresses must be reachable from the NMS. Each public URL's explicit port
must match its receiver port — a gateway/NAT that maps a different external port needs
`A2AT_SAMPLE_ALLOW_CALLBACK_PORT_MISMATCH=1`. Set the OSS port explicitly when the default would
collide with another instance; it must differ from the UI port. The OSS Agent is restarted when its
port is already held by a previously recorded OSS process, so callback changes never reuse stale
configuration.

In A2A + `--nms` the OSS Agent posts its NMS token/send/poll spans into the same run, so the session
trace shows `NMS session token acquired`, `NMS A2A stream opened` and the rest of the real
downstream hop, and status text distinguishes `NMS` from the local `RAN Agent`. Use `--ui --mode confirm` for the send-time intent
review; server-side approval 2 does not exist in NMS mode because the vendor owns execution.

The launcher also pins the topology it selected in each child's environment. The UI receives the
deployment discovery and the OSS endpoint as `A2AT_SAMPLE_DIRECT_SERVER_URL`; the OSS Agent always
receives `A2AT_SAMPLE_DISCOVERY=nms` (or `direct` in the local topology), its transport and its RAN
URL. `shared.config` prefers the process environment over the per-run `.env`, so a launch shell
that sourced `.env` with `set -a` can no longer redirect a standard-A2A run to a local RAN Agent.
At startup the OSS log names its hop — `OSS Agent downstream: NMS <base>` or
`OSS Agent downstream: local RAN Agent (<url>)` — so `oss.log` is the first place to check when a
pure run appears not to reach the vendor NMS.

The OSS Agent reports its spans to the UI process that owns the run. If `oss.log` shows
`POST .../api/runs/<id>/trace` returning 404 with `unknown_run`, the OSS is paired with a different
or restarted UI instance: the run ids and the trace token belong to the previous UI. Restart the
pair with the launcher (`--stop`, then start again); a stale OSS cannot attach to a new UI's runs.
The startup line `OSS trace emitter: http://... (token configured)` names the target, and a
rejected batch now logs the UI's response body (`unknown_run` / `trace_token_invalid` /
`span_invalid`) instead of a bare status code.

### Two instances on one machine

One instance can switch request forms in the page (the UI default, `--protocol both`). Running two instances is
still useful when each side needs its own NMS credentials, data directory or webhook endpoint. Each
instance needs its own launcher state, data directory, UI port and webhook port:

| | A2A-T | A2A |
|---|---|---|
| UI | 8080 | 8081 |
| Webhook | 9000 | 9001 |
| OSS Agent | not started | 8011 |
| `A2AT_SAMPLE_RUN_DIR` | `%TEMP%/a2at-ran-energy-saving-adk` | `%TEMP%/a2at-ran-energy-saving-adk-a2a` |
| `A2AT_SAMPLE_RUNS_DIR` | `runs` | `runs-a2a` |

```bash
# Instance A (A2A-T)
A2AT_SAMPLE_RUN_DIR=/tmp/a2at-adk \
A2AT_SAMPLE_RUNS_DIR=runs \
A2AT_SAMPLE_UI_PORT=8080 \
A2AT_SAMPLE_WEBHOOK_PORT=9000 \
python run_demo.py --nms --ui --brand huawei

# Instance B (A2A: UI -> local OSS Agent -> NMS)
A2AT_SAMPLE_RUN_DIR=/tmp/a2at-adk-a2a \
A2AT_SAMPLE_RUNS_DIR=runs-a2a \
A2AT_SAMPLE_UI_PORT=8081 \
A2AT_SAMPLE_WEBHOOK_PORT=9001 \
python run_demo.py --nms --protocol a2a --ui --brand huawei --oss-port 8011
```

`--stop` reads only that instance's `A2AT_SAMPLE_RUN_DIR/pids.json`, so stopping B leaves A
running. In A2A + NMS the UI talks to the local OSS Agent over direct A2A/SSE; the OSS Agent talks
to the NMS over plain A2A (webhook, or SSE for `--nms-sim`). The OSS Agent answers the NMS's
`INPUT_REQUIRED` with the same ADK decision agent used by the A2A-T client and renders the decision
as natural-language text.

If the NMS rejects a A2A follow-up that carries `taskId`, set
`A2AT_SAMPLE_PURE_FOLLOWUP=context` (or `task` for `taskId` only) and retry; the default is `both`.

A real NMS requires an https callback reachable through a gateway. Set
`A2AT_SAMPLE_WEBHOOK_PUBLIC_URL` to that URL, `A2AT_SAMPLE_WEBHOOK_TLS_CERT` /
`A2AT_SAMPLE_WEBHOOK_TLS_KEY` to the receiver's certificate pair, and
`A2AT_SAMPLE_WEBHOOK_AUTH_CREDENTIALS` to the `user:password` the NMS pushes with. The receiver then
checks Basic auth instead of `A2AT_SAMPLE_WEBHOOK_TOKEN`, and the same credentials are registered in
the inline push config.

### One agent, two request forms

A2A-T is an extension of A2A, not a second protocol. The RAN Agent always speaks standard A2A;
when a request declares or carries `Task-T`, the same agent runs the pre-task negotiation and
stamps A2A-T metadata on the result. The vendor NMS works the same way: one service, and the
request says whether the extension is used.

A UI launch now defaults to `--protocol both`: it starts that single agent and a page with a
protocol switch. A CLI run defaults to A2A-T.

```bash
uv run python -m demo.run_demo --ui --model-mode offline
```

- **A2A** — standard Task lifecycle (`INPUT_REQUIRED` continuation). In the local topology the
  request takes three hops: UI → OSS Agent → RAN Agent → MIE Agent.
- **A2A + A2A-T** — Task-T template validation and pre-task negotiation, served by the same RAN
  Agent.

Every run records its request form, so the session trace, metrics and history stay consistent when
the page switches. `--protocol a2a-t` and `--protocol a2a` remain available for a deployment that
serves only one form or for a deterministic CLI run.

### A2A mode (three hops, standard A2A only)

`--protocol a2a` starts a real three-hop A2A chain:

```
Engineer/UI ──A2A──► OSS Agent (:8001) ──A2A──► RAN Agent (:8000) ──A2A──► MIE Agent (:8002)
```

- The OSS Agent forwards the engineer request to the RAN Agent. When the request needs a value or
  has a conflicting target, the RAN Agent returns a standard A2A Task in `INPUT_REQUIRED`; the OSS
  Agent answers on the same Task with a follow-up message carrying `taskId`/`contextId`. Up to three
  rounds. No `Task-T/v1` or `Negotiation-T/v1` metadata is generated or returned.
- Before applying a plan, the RAN Agent asks the MIE Agent over A2A. The MIE Agent pushes the
  approval card to the page over its own SSE stream and receives the engineer's decision on
  `POST /api/mie/approvals/{taskId}`.
- The presentation shows the OSS, RAN and MIE hops, the negotiation turns, the MIE approval card and
  a per-run metrics block (hops, negotiation rounds, A2A messages). The examples are `night`
  (no negotiation), `information` (one missing-parameter round) and `target` (a counter-proposal
  then an accept).
- The old single-turn `plain` path remains in the code and tests as an internal baseline, but it is
  not exposed by the CLI or the page.

```bash
# A2A, three hops and human approval in MIE
uv run python -m demo.run_demo --protocol a2a --ui --approval-gate --scenario information

# Existing A2A-T mode (Task-T + Negotiation-T), unchanged
uv run python -m demo.run_demo --protocol a2a-t --ui --mode confirm --approval-gate --scenario information
```

Approval 1 is real: in `--mode confirm`, the Task-T is held before it leaves the OSS Agent until the
engineer confirms the structured intent. Approval 2 is the MIE MML review: with `--approval-gate`,
the RAN Agent publishes the real A2A `INPUT_REQUIRED` state before applying a plan; the page shows the
MML suggestion and asks the engineer to approve it in MIE. The review window defaults to 240 s;
if it expires (or the run ends while the card is open), the page closes the card and shows the
ending instead of staying on "review needed". A rejection applies nothing and ends the run without
a report. The data-collection phase is driven by three real `WORKING` status
updates (started, in progress, completed); the page animates days 1–8 and never invents measurements.
No MML command reaches the network before the MIE approval: after it, the green `MML commands`
packet leaves MIE directly for the radio sites, the sites execute it, and the execution results
travel back through the RAN Agent to the OSS Agent.

The playback controls (auto / step by step, speed, pause, previous / next, loop) only pace the
presentation; the backend run is real. Transport, model and language are fixed when the process
starts, so the page itself offers only the scenario switcher and the request text.

For the protocol-level view (phases, LLM and tool calls, raw payloads, run history), start the same
command with `--ui-mode explorer`. Installation, environment variables and the full command
reference are in the [technical guide](TECHNICAL.md).

### Metrics and protocol comparison

Every run writes one metrics record: token usage, a latency split (model / tool / human approval /
other) and accuracy against the scenario's ground truth (terminal state, negotiation rounds, agreed
throughput target and report presence). When the LLM endpoint reports prefix-cache hits
(`prompt_tokens_details.cached_tokens`, or DeepSeek's `prompt_cache_hit_tokens`), the record carries
`tokens.cached`, `tokens.cachedCalls` and `tokens.cacheHitRate`; `None` means the provider did not
report the field, while `0` means it reported a miss. The record is stored in the client SQLite database
(`run_metrics`) and appended to `runs/metrics/run-metrics.jsonl`; the Web UI also serves
`/api/metrics/runs`, `/api/metrics/summary`, `/api/runs/{runId}/metrics` and
`/api/metrics/export.csv`.

Open `http://127.0.0.1:8080/comparison` to compare protocols and scenarios: success rate, token
averages (including cached tokens and cache hit rate), model/total latency, P95 and approval wait.
The page reads the same persisted records.

Because the protocol is process-level, the benchmark starts one demo stack per protocol. It drives
the same scenarios, approves the human gate after a fixed delay (0.3 s by default), and writes
`metrics.json`, `metrics.jsonl`, `summary.csv`, `summary.json` and `report.html` under
`runs/benchmarks/<timestamp>/`:

```bash
# Token/latency/accuracy comparison with the live model
uv run python -m demo.benchmark --model-mode live --scenarios information,target --repeats 3

# One quick offline smoke (tokens are N/A because the scripted model reports no usage)
uv run python -m demo.benchmark --protocols a2a --scenarios information --repeats 1
```

Offline runs still measure latency and accuracy; token fields are reported as `N/A` because the
scripted model produces no usage. Human approval time is recorded separately and excluded from the
model/protocol latency comparison.

## 5. Integration boundary

| OSS side | NMS side |
| --- | --- |
| Integrate the A2A-T client; the SDK carries the protocol | Keep the existing NMS evaluation, apply and reporting capability |
| Provide a decision agent: accept / reject / counter | Replace the sample tools with real NMS interfaces |
| Keep your own policy, approval and audit | Keep the negotiation policy and task store |

In the reference implementation the OSS side uses an ADK decision agent and the NMS side uses
LangGraph; those are implementation choices and do not change the interaction above.

## 6. Key characteristics

| Characteristic | What it means |
| --- | --- |
| Auditable | every proposal, decision, apply and report is recorded |
| Model-independent | offline runs are reproducible; a live model only affects decisions and planning |
| Replaceable | tools and policy can be replaced with the real NMS implementation |

## 7. Technical guide

Protocol details, implementation structure, configuration, tests and production notes are in the
[technical guide](TECHNICAL.md).
