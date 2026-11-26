# Field notes — real-NMS integration

Operational notes for the delivered live profile. The demo-era notes about offline/scripted runs,
the OSS proxy, MIE approval and the observation consoles no longer apply.

## Before the first run

1. Fill `env.example` → `.env`: model endpoint, `A2AT_SAMPLE_NMS_*`, the callback public URL and
   (when used) the gateway public URL.
2. Keep `A2AT_SAMPLE_LONG_TASK_TRACKING=1`; a real NMS Task often outlives the synchronous window.
3. Start clean: `python run_demo.py --stop`, then the target mode. Do not reuse a process started
   with another configuration.

## Evidence to collect

- `RUN_DIR/.env`, `RUN_DIR/pids.json` and the process logs;
- `runs/<runId>/wire.jsonl` (every A2A frame, verbatim);
- `runs/<runId>/server-trace.jsonl` when the local reference server is used;
- the run id, instance/port/model, the operator's original request and the observed Task-T.

## Common symptoms

| Symptom | Check |
|---|---|
| UI starts, gateway does not | gateway public URL and `--gateway-port` availability |
| NMS pushes never arrive | `A2AT_SAMPLE_WEBHOOK_PUBLIC_URL`, firewall/NAT, receiver port |
| Run ends before the NMS Task | long-task tracking and the poll interval budget |
| Task-T rejected before sending | operator text, SDK validation error code, model endpoint |
| Task stuck in `SUBMITTED` | vendor status push/subscription; wire log shows whether a push arrived |

## Rules

- The operator's original text is the only Task-T input; never "fix" it in the client.
- Approvals beyond approval 1 belong to the vendor NMS.
- Trace/Langfuse/metrics failures are best effort and never change a protocol result.
