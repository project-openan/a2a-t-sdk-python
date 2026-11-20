# Field integration: collecting and retesting a missing negotiation

> Applies to A2A-T with `--nms` (vendor NMS): `message:send` is accepted
> (`TASK_STATE_SUBMITTED`), but no `Negotiation-T` / `decision_requested` ever arrives and the run
> stays `incomplete` or `running`.
>
> This file is checked in for the yellow-zone field integration. Group A can be run now and needs
> no new blue-zone code. Group B is post-fix acceptance; the current branch does not implement it
> yet, so wait for the blue-zone notice before treating it as a defect.
>
> Internal analysis and raw measurements are not checked in.

---

## 0. Before retesting

| Item | Record |
| --- | --- |
| Yellow-zone build | full `git rev-parse HEAD` hash |
| Commits to look for | `0f6cb82` (wait-state UI), `7eb9414` (validator failure surfacing/retry), `210a117` (`json_schema`) |
| Instance / ports | e.g. `A2AT_MODEL=qwen38`, UI `8084`, webhook `9004` |
| Scenario / mode / protocol | e.g. `outage`, `confirm`, A2A-T, `--nms` |
| Startup | always `./run_demo.py --stop` and start clean; do not reuse an old process |
| Time base | state the local timezone and whether a timestamp is local or the NMS `Z` time |

> If the “task creation time” disagrees with the `timestamp` from `GET /tasks/{id}`, first decide
> whether it is the build, the timezone, or a Task actually created by an earlier run.

---

## 1. Read first: three known behaviors

1. **The client never opens a negotiation.** In `--nms` mode it only sends a Task-T; a `PROPOSE`
   can only arrive in the `message:send` response or a later push. No server proposal, no
   `decision_requested`.
2. **A local “negotiable” verdict does not mean the NMS will negotiate.** It only means the
   request is still sent. The client does not send a negotiation request; the NMS decides from its
   own validation.
3. **A Task staying in `SUBMITTED` is a known NMS-side behavior (A12).** It happens to Create and
   Modify alike, and it is a separate phenomenon from “no negotiation”.

A report that can be attributed therefore answers all four:

- What is the outbound Task-T?
- What did the local validator actually see?
- What are the real Task state and timestamp on the NMS?
- Does the NMS negotiate on **Modify** at all? (needs a control experiment)

---

## 2. Group A: run now, no code change

### A1 (highest priority) return the outbound Task-T

From `runs/<runId>/wire.jsonl`, take the outbound `message:send` metadata Task-T for:

- the failing run (e.g. `354d814b…`)
- the previous run on the same Task (e.g. `a4ad4189…`)
- the run behind a Task that did receive a `PROPOSE` (e.g. `451ee200…`)

Return at least the `Task Context` section, preferably the whole Task-T. Check:

- does it contain `Time-segmented rate guarantee target: {08:00-18:00,10Mbps}` (or an equivalent)?
- does it contain only `Cell RAT: NR, LNR`?
- is `Operation Type` `Create` or `Modify`?

> Generating the Task-T and validating it are two independent model calls. The `actual` value in a
> validation error is the **validator's** reading, not the Task-T text. Only the original text can
> separate “slot extraction dropped the rate” from “the validator misread it”.

### A2 return the full `task_validation` detail

Export the `task_validation` span `detail` from the event stream/trace:

- `verdict`, `reason`, `negotiationKind`, `attempts`
- `errors[]`: `slotName`, `code`, `facts`
- the validator's extracted `params`

Record `params.guaranteedThroughputTarget`: `null` / `"NR, LNR"` / `"{08:00-18:00,10Mbps}"`.
This is what decides why the required-field gate let the request through.

### A3 return the full NMS Task JSON

```text
GET /a2a/json/tasks/<stuck-task-id>
GET /a2a/json/tasks/<healthy-task-id>     # control
```

Do not paste only `state` / `timestamp`. Confirm:

- is `state` top-level or under `status.state`?
- is `timestamp` / `updatedAt` top-level or under `status`?
- are `contextId`, `history`, `artifacts` complete?
- does `history` show both Modify messages?

> A pre-send stall check needs a real timestamp field; the exact vendor document shape decides
> whether that is even possible.

### A4 identify the control Task

- Was the Task that received a `PROPOSE` a **Create** or a **Modify**?
- Which runId / `contextId` owns it?
- Is its outbound Task-T the same as the failing run's?

### A5 (key control) Modify a healthy `WORKING` Task

**Steps**

1. Prepare a Task actually in `TASK_STATE_WORKING`:
   - real NMS: pick a `WORKING` row in the Running-tasks picker; or
   - local simulator: `A2AT_SAMPLE_NMS_SIM_HOLD_WORKING=1` keeps a new Task `WORKING`.
2. Record `targetTaskId` / `contextId` and the selection time.
3. Send the **same** infeasible request as the failing run (e.g. 10 Mbps with an 08:00–18:00 outage).
4. Record:
   - the `message:send` response state;
   - whether `Negotiation-T` / `negotiationContext` / `PROPOSE` appears;
   - whether the Task state advances;
   - whether the NMS Task `timestamp` changes.

**Verdict**

| Result | Meaning |
| --- | --- |
| `PROPOSE` / `INPUT_REQUIRED` | the NMS negotiates on Modify; the problem is the target Task or the gate |
| `SUBMITTED` only, no `PROPOSE`, no advance | the NMS may not negotiate on Modify; confirm the product expectation before only changing the client gate |

### A6 (key control) Modify a Task that was just accepted and is still `SUBMITTED`

1. Create a Task; record `taskId` / `contextId` / creation time.
2. Within **60 seconds**, send the same infeasible Modify.
3. Record whether the NMS advances, negotiates, or changes the `timestamp`.

> This separates “SUBMITTED is never modifiable” from “only a long-stalled SUBMITTED is broken”.

### A7 stall observation (record only, no verdict)

For a Task that stays `SUBMITTED` for a long time, record:

- `activity` frames: `state`, `stateSince`, `pollCount`, `pollIntervalSeconds`, `detached`;
- the wait-strip text and elapsed time;
- whether a “stayed in SUBMITTED” hint appears after 2 minutes;
- whether the run stays `running` and never finishes;
- the `task_poll` / `task_created` rows in the timeline.

---

## 3. Group B: post-fix acceptance (not implemented on this branch yet)

> These are target behaviors, not current behavior. The blue zone will update this file when they
> land; do not report them as defects before then.

### B1 Modify target-state gate

- A Task stalled in `SUBMITTED` beyond the threshold: **rejected before sending**, with the stall
  duration and a next step (cancel or pick another Task).
- `WORKING` Task: Modify allowed.
- Terminal Task: rejected.
- **Every entry point agrees**: presentation `/api/runs`, tester console, direct API, CLI all pass
  the same gate; not only the presentation route.

### B2 timestamps and stall detection

- Task list / single-Task reads expose a usable `updatedAt` / `timestamp`;
- the simulator exposes it too, so offline tests cover it;
- the threshold is configurable and applies to `SUBMITTED` only; do not reject a legitimate long
  `WORKING` Task by age.

### B3 explicit “no proposal received” observation

- when the flow ends `incomplete`, the result carries a reason
  (e.g. `no Negotiation-T received; remote state TASK_STATE_SUBMITTED`);
- timeline / UI distinguish “waiting for the NMS proposal” from “the NMS is executing”;
- a long unchanged period produces an actionable hint instead of “NMS is executing”.

### B4 acceptance wording

- “Task modified / Task created” reflects the real semantics (accepted / state advanced);
- a `SUBMITTED` snapshot must not imply the modification succeeded.

### B5 regression

- the normal Create → WORKING → Modify → COMPLETED two-phase path still works;
- the offline `--nms-sim` Modify case passes;
- the full sample suite passes (take the count from `pytest`, do not copy a doc).

---

## 4. Required evidence format

For every conclusion, use this format; write “not collected” for a missing item:

```text
- runId:
- time (local / NMS):
- build commit:
- instance / ports / model:
- scenario / mode / protocol:
- steps:
- observed behavior:
- raw evidence:
  - key wire.jsonl frames (out / in)
  - task_validation detail
  - full GET /tasks/{id} JSON
  - activity frames / screenshot
- initial ownership: client / NMS / model / network
```

**Do not send only a conclusion sentence** (“the NMS does not negotiate”, “it is not the model”).
A conclusion without the raw evidence above cannot be attributed.

---

## 5. Initial ownership triage

| Symptom | Initial ownership | Note |
| --- | --- | --- |
| Task accepted but stays `SUBMITTED` | NMS side (known A12) | still return the Task JSON proving the timestamp did not move |
| A healthy `WORKING` Task's Modify also gets no `PROPOSE` | NMS contract / product expectation | run A5 before changing client code |
| Task-T `Task Context` has no rate, only RAT | client (slot extraction) | prove with A1 |
| Validator `params` is non-empty but clearly wrong, yet the gate passes | client (validation / gate) | prove with A2 |
| UI says “NMS is executing” while the Task is stalled | client (observability) | does not change the protocol result |
| Modify is allowed on a stalled `SUBMITTED` Task | client (entry policy) | B1 |

---

## 6. Reusable structure for future blue → yellow notes

1. **Prerequisites**: build, instance, ports, model, timezone, clean start;
2. **Known behaviors to read first**: keep separate phenomena separate;
3. **Group A**: evidence / control experiments runnable now; each item has steps, expected result
   and the exact evidence to return;
4. **Group B**: post-fix acceptance; each item has target behavior, entry points, regression checks,
   and whether it is implemented yet;
5. **Uniform evidence format**: runId + raw frames + JSON + screenshots;
6. **Ownership triage table**: separate client observability gaps from NMS-side blocks.
