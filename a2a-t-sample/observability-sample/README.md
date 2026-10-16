# observability-sample — see what your A2A-T agents are actually doing

A runnable sample that shows the **full lifecycle of an A2A-T interaction** — a client
asks, a server answers, artifacts stream back — with every step automatically traced,
measured and logged by the `a2a-t-sdk` observability layer.

There is **no manual OpenTelemetry code** in the sample: the server adds one wrapper,
the client adds one registration, and everything else is emitted by the SDK.

---

## What you can try

| Scenario (`--scenario=`) | Story |
|--------------------------|-------|
| `native` | A task flows end to end: the client submits, the server works and streams artifacts back. Watch the request light up on both sides. |
| `a2at` | Prompt generation with an LLM (a mock LLM by default — no API key needed) and a manual "L4" span around the SDK call. |
| `negotiation` | The agent **bargains before it commits**: two offer rounds happen over plain messages (no task yet), and only after both sides agree does the real task start and stream its result. |

## Quick start

Prerequisites: you are in the repository root and [uv](https://docs.astral.sh/uv/) is installed.

```bash
# from the repo root — terminal 1: start the server (port 8100)
cd a2a-t-sample
export PYTHONPATH="$PWD/observability-sample/src"
export A2AT_OBSERVABILITY_ENABLED=true
uv run python -m obs_sample.server_main

# terminal 2: run a client scenario
cd a2a-t-sample
export PYTHONPATH="$PWD/observability-sample/src"
uv run python -m obs_sample.client_main --scenario=native
```

Windows PowerShell: use `$env:PYTHONPATH = "$PWD\observability-sample\src"` instead of `export`.

You will see log lines on both terminals (`[server] ...` / `[client] ...`), and — with the
default configuration — every span printed as JSON on the server console.

## Scenario walkthroughs

### native — one task, both sides

1. Terminal 1 starts the server. Terminal 2 runs the client with `--scenario=native`.
2. The client submits a task; the server moves it through *submitted → working* and
   streams an artifact back.
3. Each step appears as a span on **both** sides, joined into one trace
   (the client passes a W3C `traceparent` to the server automatically).

### a2at — generate a prompt with an LLM

```bash
uv run python -m obs_sample.client_main --scenario=a2at
```

The client generates a task prompt through the SDK's LLM facade. With an empty
`A2AT_LLM_API_KEY` in `a2a-t-sample/.env` a canned **mock LLM** answers, so this runs
fully offline. The LLM's token usage is measured automatically and attached to the trace.

### negotiation — bargain first, execute later

```bash
# terminal 1: restart the server in negotiation mode
uv run python -m obs_sample.server_main --scenario=negotiation
# terminal 2:
uv run python -m obs_sample.client_main --scenario=negotiation
```

This is the real-world timing of a Negotiation-T deal:

1. **Round 1** — the client proposes; the server counters with a different offer and
   disconnects. *No task exists yet — bargaining happens over plain messages.*
2. **Round 2** — the client reconnects, accepts the counter-offer; the server confirms
   the agreement and disconnects again.
3. **The task starts only after agreement** — a separate request now runs the full
   task lifecycle and streams the result.

Each round is an independent exchange; the sample ties them together so you can follow
the whole deal in one place (see Langfuse below).

## Send everything to Langfuse (optional)

Set three variables before starting **both** processes and every span lands in your
Langfuse project as one browsable trace per interaction:

```bash
export LANGFUSE_PUBLIC_KEY="pk-lf-..."
export LANGFUSE_SECRET_KEY="sk-lf-..."
export LANGFUSE_BASE_URL="https://us.cloud.langfuse.com"
```

In the Langfuse UI you will see the client and server spans of every round and the final
task on a single timeline — including who proposed what, which round was accepted, and
how long each step took.

## What gets recorded (cheat sheet)

| You do | The SDK records |
|--------|-----------------|
| Client sends a request | `SendStreamingMessage` span (CLIENT) + duration metric |
| Server receives it | `SendStreamingMessage` span (SERVER), child of the client's |
| Server streams an event | `SendStreamingMessage-event` span, linked to the entry |
| A negotiation message is sent / received | `SendStreamingMessage-negotiation` span with round & performative |
| Server pushes a notification | `SendMessage-pushNotification` span, linked to the server entry |
| The LLM generates tokens | token-usage metric + `gen_ai.usage.*` on the next trace |
| Anything noteworthy | a structured log line on logger `a2at.observability` |

Signals can be toggled independently — see the environment variables below.

## Environment variables

Commonly used:

| Variable | Default | Meaning |
|----------|---------|---------|
| `A2AT_OBSERVABILITY_ENABLED` | `true` | Master switch — `false` makes the SDK fully transparent |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | *(unset)* | Send spans/metrics/logs to an OTLP collector instead of the console |
| `A2AT_CONSOLE_EXPORTER` | `true` | `false` disables the dev-mode console fallback (no stdout JSON) |
| `OTEL_SERVICE_NAME` (or `A2AT_SERVICE_NAME`) | `a2a-t-agent` | How this process is named in the backend |
| `A2AT_OBS_SAMPLE_PORT` | `8100` | Sample server port |
| `LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY` / `LANGFUSE_BASE_URL` | *(unset)* | Enable the Langfuse backend |

Advanced (rarely needed): `A2AT_TRACE_ENABLED`, `A2AT_METRIC_ENABLED`, `A2AT_LOG_ENABLED`
(per-signal switches — e.g. trace off keeps metrics and logs alive), `A2AT_EXPORTER_PROTOCOL`
(`grpc` / `http/protobuf`), `OTEL_INSTRUMENTATION_A2AT_SDK_ENABLED=false` (hard disable).

## Notes

- The server uses the **official default** `DefaultRequestHandler` (a2a-sdk 1.1.x alias
  for `DefaultRequestHandlerV2`): the observability decorator detects the V2 shape and
  rebuilds its task registry with the decorated executor / push sender, so per-event and
  push spans work on the official handler too. (`LegacyRequestHandler` remains supported
  via per-attribute wrapping.)
- Push notifications are echoed back to the server's `/push-sink` endpoint, which is
  why the sample can show the `SendMessage-pushNotification` span end to end.
- Offline verification helpers live next to the sample code (`check_*.py`,
  `verify_langfuse.py`) — they are one-off debugging tools, not part of the sample flow.
