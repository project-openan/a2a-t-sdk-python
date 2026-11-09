"""Run-level metrics derived from raw observation rows (A2A / A2A-T aware).

The exporter archives raw Langfuse rows. The A2A sample writes one observation per
phase with a small metadata envelope (`kind`, `phase`, `side`, `round`, `stage`,
`tokens.*`, `detail.*`), plus trace-level keys on the `trace opened` event. This
module turns those rows into a stable, key-based JSON document so callers never
have to remember spreadsheet column letters.

The function is pure: no I/O, no network, no clock, so tests can pin every value.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from .normalize import parse_dt

SCHEMA_VERSION = 1

RUN_META_KEYS = (
    "runId",
    "chainId",
    "protocol",
    "scenario",
    "runMode",
    "mode",
    "modelMode",
    "sourceKind",
    "transport",
    "language",
    "discovery",
    "intentMode",
    "caseId",
    "batchId",
    "receivedAt",
    "rounds",
    "negotiated",
)

TRACE_ATTR_KEYS = ("name", "userId", "sessionId", "environment", "release")
TRACE_ATTR_SOURCES = {
    "name": "traceName",
    "userId": "userId",
    "sessionId": "sessionId",
    "environment": "environment",
    "release": "release",
}

DECISION_KINDS = {"accept", "counter", "reject", "abort"}
NEGOTIATION_KINDS = {"propose", "negotiation_started"} | DECISION_KINDS


def _meta(row: dict[str, Any], key: str) -> Any:
    metadata = row.get("metadata")
    if not isinstance(metadata, dict):
        return None
    return metadata.get(key)


def _as_text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return "true" if value else "false"
    text = str(value).strip()
    return text or None


def _as_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        try:
            return int(value.strip())
        except ValueError:
            return None
    return None


def _as_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    text = _as_text(value)
    if text is None:
        return None
    lowered = text.lower()
    if lowered in {"true", "1", "yes", "on"}:
        return True
    if lowered in {"false", "0", "no", "off"}:
        return False
    return None


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    utc = value.astimezone(timezone.utc)
    return utc.strftime("%Y-%m-%dT%H:%M:%S.") + f"{utc.microsecond // 1000:03d}Z"


def _start(row: dict[str, Any]) -> datetime | None:
    return parse_dt(row.get("startTime"))


def _first_start(rows: list[dict[str, Any]]) -> str | None:
    times = [parsed for parsed in (_start(row) for row in rows) if parsed is not None]
    return _iso(min(times)) if times else None


def _client_rows(rows: list[dict[str, Any]], trace_id: str | None) -> list[dict[str, Any]]:
    """Rows belonging to this run (`metadata.runId == traceId`), not just client side.

    `side=server` rows are kept: the only `task_created` / `status` observations may come
    from the server executor, while truly remote spans (no `runId`, e.g. the service-side
    `SendMessage`) must not leak into this run's timeline.
    """
    matched = [row for row in rows if _as_text(_meta(row, "runId")) == trace_id and trace_id]
    if matched:
        return matched
    sided = [row for row in rows if _as_text(_meta(row, "side")) == "client"]
    if sided:
        return sided
    return [row for row in rows if _meta(row, "kind") or _meta(row, "stage")]


def _run_meta(rows: list[dict[str, Any]]) -> dict[str, str | None]:
    """Merge run-level keys; the `trace opened` event usually carries them all."""
    ordered = sorted(
        rows,
        key=lambda row: 0 if row.get("name") == "trace opened" else 1,
    )
    resolved: dict[str, str | None] = {}
    for key in RUN_META_KEYS:
        value = next(
            (_as_text(_meta(row, key)) for row in ordered if _as_text(_meta(row, key))),
            None,
        )
        resolved[key] = value
    return resolved


def _trace_attrs(rows: list[dict[str, Any]], summary: Any = None) -> dict[str, str | None]:
    resolved: dict[str, str | None] = {}
    for name, source in TRACE_ATTR_SOURCES.items():
        value = next((row.get(source) for row in rows if row.get(source)), None)
        resolved[name] = _as_text(value)
    if summary is not None:
        # The derived summary resolves conflicting traceName rows (a specific name vs
        # "Run finished") with a warning, so prefer it over a row-order guess.
        overrides = {
            "name": getattr(summary, "name", None),
            "userId": getattr(summary, "user_id", None),
            "sessionId": getattr(summary, "session_id", None),
            "environment": getattr(summary, "environment", None),
            "release": getattr(summary, "release", None),
        }
        for key, value in overrides.items():
            if value:
                resolved[key] = _as_text(value)
    return resolved


def _row_side(row: dict[str, Any]) -> str:
    """Best-effort side for a row.

    Client SDK rows carry ``metadata.side``. The remote service's ``SendMessage``
    span often has no ``side``/``runId`` but its scope/service name is
    ``a2a-t-server``; that is exactly the RAN-side token consumption that must not
    be silently dropped from the token totals.
    """
    side = _as_text(_meta(row, "side"))
    if side:
        return side.lower()
    scope = (
        _as_text(_meta(row, "scope.name"))
        or _as_text(_meta(row, "resourceAttributes.service.name"))
        or ""
    ).lower()
    if "server" in scope:
        return "server"
    if scope:
        return "client" if "ran-energy-saving" in scope else "unknown"
    return "unknown"


def _row_usage(row: dict[str, Any]) -> dict[str, int] | None:
    """Token counts for one row: usageDetails first, then OpenTelemetry attributes."""
    details = row.get("usageDetails")
    if isinstance(details, dict) and details:
        return {
            "input": _as_int(details.get("input")) or 0,
            "cachedInput": _as_int(details.get("input_cached")) or 0,
            "output": _as_int(details.get("output")) or 0,
            "total": _as_int(details.get("total")) or 0,
        }
    top = {
        "input": _as_int(row.get("inputUsage")) or 0,
        "cachedInput": 0,
        "output": _as_int(row.get("outputUsage")) or 0,
        "total": _as_int(row.get("totalUsage")) or 0,
    }
    if any(top.values()):
        return top
    # Server-side agent spans (a2a-t-server) expose token usage only in OTel
    # attributes, never in usageDetails/inputUsage.
    meta = row.get("metadata")
    if isinstance(meta, dict):
        input_tokens = _as_int(meta.get("attributes.gen_ai.usage.input_tokens"))
        output_tokens = _as_int(meta.get("attributes.gen_ai.usage.output_tokens"))
        cached = _as_int(meta.get("attributes.gen_ai.usage.input_cached_tokens")) or _as_int(meta.get("tokens.cached")) or 0
        total = _as_int(meta.get("tokens.total"))
        if input_tokens is not None or output_tokens is not None:
            input_tokens = input_tokens or 0
            output_tokens = output_tokens or 0
            return {
                "input": input_tokens,
                "cachedInput": cached,
                "output": output_tokens,
                "total": total if total is not None else input_tokens + output_tokens,
            }
    return None


def _token_sums(rows: list[dict[str, Any]]) -> tuple[dict[str, Any], bool]:
    totals = {"input": 0, "cachedInput": 0, "output": 0, "total": 0}
    by_side: dict[str, dict[str, int]] = {}
    found = False

    # The a2a-t-server agent span may aggregate exactly the same tokens as its
    # nested generation (e.g. SendMessage -> SendMessage-negotiation both report
    # 130812/1500). Counting both would double that LLM call; prefer the deeper
    # observation when parent and child usage are identical.
    usages: dict[str, dict[str, int]] = {}
    by_id: dict[str, dict[str, Any]] = {}
    for row in rows:
        row_id = _as_text(row.get("id"))
        values = _row_usage(row)
        if row_id and values is not None and any(values.values()):
            by_id[row_id] = row
            usages[row_id] = values
    skipped: set[str] = set()
    for row_id, row in by_id.items():
        parent_id = _as_text(row.get("parentObservationId"))
        if parent_id and parent_id in usages and usages[parent_id] == usages[row_id]:
            skipped.add(parent_id)

    # Some server instrumentations repeat the same aggregate usage on every sibling
    # generation (e.g. SendMessage-negotiation and SendMessage-pushNotification under one
    # SendMessage). Identical usage under the same parent is one LLM call, not two.
    seen_siblings: set[tuple[str, str, tuple[tuple[str, int], ...]]] = set()
    for row in rows:
        row_id = _as_text(row.get("id"))
        values = _row_usage(row)
        parent_id = _as_text(row.get("parentObservationId"))
        if not row_id or values is None or not any(values.values()) or not parent_id:
            continue
        key = (_row_side(row), parent_id, tuple(sorted(values.items())))
        if key in seen_siblings:
            skipped.add(row_id)
        else:
            seen_siblings.add(key)

    for row in rows:
        row_id = _as_text(row.get("id"))
        if row_id and row_id in skipped:
            continue
        values = _row_usage(row)
        if values is None or not any(values.values()):
            continue
        found = True
        side = _row_side(row)
        bucket = by_side.setdefault(side, {"input": 0, "cachedInput": 0, "output": 0, "total": 0})
        for key, value in values.items():
            totals[key] += value
            bucket[key] += value
    return {"**totals**": totals, "bySide": by_side}, found


def _round_of(row: dict[str, Any], fallback: int) -> int:
    for key in ("round", "rounds", "negotiation.round"):
        value = _as_int(_meta(row, key))
        if value is not None:
            return value
    return fallback


def _server_negotiation(rows: list[dict[str, Any]]) -> tuple[dict[int, dict[str, Any]], int | None]:
    """Collect negotiation timestamps reported by the a2a-t-server spans.

    The remote RAN agent does not emit a client-side ``negotiation_started`` span;
    its ``SendMessage`` / ``SendMessage-negotiation`` observations carry the round
    and performative plus (sometimes) ``negotiation.total_duration_ms``. Those
    timestamps live on the server clock, so they can fill AO but must not be used
    for client-side latency arithmetic.
    """
    starts: dict[int, dict[str, Any]] = {}
    duration: int | None = None
    for row in rows:
        if _row_side(row) != "server":
            continue
        meta = row.get("metadata")
        if not isinstance(meta, dict):
            continue
        if _as_text(meta.get("kind")) == "status":
            # Status rows may carry a round number and negotiation text, but they are
            # not the server's negotiation span; using them would pick the delivery time.
            continue
        performative = _as_text(meta.get("attributes.gen_ai.agent.a2at.negotiation.performative"))
        round_no = _as_int(meta.get("attributes.gen_ai.agent.a2at.negotiation.round")) or _as_int(meta.get("round"))
        total_ms = _as_int(meta.get("attributes.gen_ai.agent.a2at.negotiation.total_duration_ms"))
        if total_ms is not None:
            duration = total_ms if duration is None else max(duration, total_ms)
        name = str(row.get("name") or "SendMessage")
        if performative is None:
            # Without a performative only the dedicated server spans qualify; arbitrary
            # status/artifact rows must not be mistaken for the negotiation start.
            if round_no is None or not name.startswith("SendMessage"):
                continue
        at = _iso(_start(row))
        if at is None:
            continue
        label = "SendMessage-negotiation" if name.startswith("SendMessage-negotiation") else "SendMessage"
        score = 2 if "negotiation" in label.lower() else 1
        key = round_no or 0
        current = starts.get(key)
        if current is None or score > current["score"]:
            starts[key] = {
                "at": at,
                "round": round_no,
                "source": label,
                "score": score,
                "performative": performative,
            }
    return starts, duration


def _negotiation(
    rows: list[dict[str, Any]],
    triggered_meta: bool | None,
    all_rows: list[dict[str, Any]] | None = None,
    rounds_meta: int | None = None,
) -> dict[str, Any]:
    kinds = [_as_text(_meta(row, "kind")) for row in rows]
    current_round = 0
    rounds: dict[int, dict[str, Any]] = {}

    def bucket(index: int) -> dict[str, Any]:
        return rounds.setdefault(
            index,
            {
                "round": index,
                "proposeAt": None,
                "notifiedAt": None,
                "notifiedAtSource": None,
                "negotiationStartedAt": None,
                "negotiationStartedAtSource": None,
                "clock": None,
                "decisionAt": None,
                "sdkConstructedAt": None,
                "replySentAt": None,
            },
        )

    for row, kind in zip(rows, kinds):
        start = _iso(_start(row))
        if kind in NEGOTIATION_KINDS:
            round_no = _round_of(row, current_round + 1)
            current_round = round_no
            entry = bucket(round_no)
            if kind == "propose" and entry["proposeAt"] is None:
                entry["proposeAt"] = start
            elif kind == "negotiation_started" and entry["negotiationStartedAt"] is None:
                entry["negotiationStartedAt"] = start
                entry["negotiationStartedAtSource"] = "client:negotiation_started"
                entry["clock"] = "client"
            elif entry["decisionAt"] is None:
                entry["decisionAt"] = start
        elif kind == "decision_requested":
            # Full-level only today (the phase allow-list drops it), but when a full
            # export is available it is the most precise "operator was notified" time.
            round_no = _round_of(row, current_round + 1)
            entry = bucket(round_no)
            if entry["notifiedAt"] is None:
                entry["notifiedAt"] = start
                entry["notifiedAtSource"] = "decision_requested"
        elif kind == "llm":
            stage = _as_text(_meta(row, "stage"))
            # Only explicit negotiation_* extraction stages mean "call the SDK to
            # build a negotiation reply". An unstructured `LLM call · structured`
            # can carry phase=negotiation (SDK default) while actually validating
            # the Task-T, so do not infer negotiation from phase alone.
            if stage and stage.startswith("negotiation_"):
                round_no = _round_of(row, current_round or 1)
                entry = bucket(round_no)
                if entry["sdkConstructedAt"] is None:
                    entry["sdkConstructedAt"] = start
        elif kind == "message_sent":
            phase = _as_text(_meta(row, "phase"))
            has_round = _as_int(_meta(row, "round")) is not None
            if phase in {"negotiation", "resolution"} or (has_round and current_round):
                round_no = _round_of(row, current_round or 1)
                entry = bucket(round_no)
                if entry["replySentAt"] is None:
                    entry["replySentAt"] = start

    for entry in rounds.values():
        if entry["notifiedAt"] is None and entry["proposeAt"] is not None:
            # The latest SDK emits a `propose` span the moment the client receives
            # PROPOSE, which is the same instant the operator is notified (OSP AK/AP).
            entry["notifiedAt"] = entry["proposeAt"]
            entry["notifiedAtSource"] = "propose"

    server_starts, server_duration = _server_negotiation(all_rows or [])
    if server_starts:
        fallback = server_starts[min(server_starts)]
        for entry in rounds.values():
            if entry["negotiationStartedAt"] is None:
                candidate = server_starts.get(entry["round"]) or fallback
                entry["negotiationStartedAt"] = candidate["at"]
                entry["negotiationStartedAtSource"] = f"server:{candidate['source']}"
                entry["clock"] = "server"

    # A server-initiated round (feasibility negotiation) may have no client decision
    # rows at all. Keep one entry per round reported by the server and/or by run metadata.
    for round_no in sorted(server_starts):
        if round_no and round_no not in rounds:
            entry = bucket(round_no)
            candidate = server_starts[round_no]
            entry["negotiationStartedAt"] = candidate["at"]
            entry["negotiationStartedAtSource"] = f"server:{candidate['source']}"
            entry["clock"] = "server"

    ordered = [rounds[index] for index in sorted(rounds)]
    latencies: list[float] = []
    for entry in ordered:
        if entry["clock"] == "server":
            # Server and client clocks differ by minutes in real traces; subtracting
            # them would fabricate a latency, so leave latencyMs null for those rounds.
            continue
        begun = entry["negotiationStartedAt"] or entry["proposeAt"]
        finished = entry["decisionAt"]
        if begun and finished:
            start_dt = parse_dt(begun)
            end_dt = parse_dt(finished)
            if start_dt and end_dt:
                latencies.append((end_dt - start_dt).total_seconds() * 1000)
    latency_ms = int(round(sum(latencies))) if latencies else None
    rounds_total = max(len(ordered), rounds_meta or 0)
    triggered = bool(ordered) or bool(triggered_meta) or (rounds_meta or 0) > 0
    return {
        "triggered": triggered,
        "rounds": rounds_total,
        "latencyMs": latency_ms,
        "serverDurationMs": server_duration,
        "roundsDetail": ordered,
    }


def build_metrics(
    rows: list[dict[str, Any]],
    trace_id: str | None = None,
    summary: Any = None,
) -> dict[str, Any]:
    """Build the metrics document for one exported trace.

    ``rows`` are the API observation rows (the same objects written to
    ``raw/observations.jsonl``). ``summary`` is optional and only used as a
    fallback for the end-to-end latency of non-A2A traces.
    """
    warnings: list[str] = []
    client = _client_rows(rows, trace_id)
    if not client:
        warnings.append("no client-side observations found; A2A fields left null")
    run_meta = _run_meta(client or rows)
    attrs = _trace_attrs(client or rows, summary)
    chain_id = run_meta.get("chainId") or attrs.get("sessionId")

    opened = next((row for row in client if row.get("name") == "trace opened"), None)
    opened_at = _iso(_start(opened)) if opened is not None else None
    received_at = run_meta.get("receivedAt") or opened_at

    sdk_called_at = _first_start([row for row in client if _meta(row, "kind") == "task_prompt_rendered"])
    request_sent_at = _first_start([row for row in client if _meta(row, "kind") == "message_sent"])
    task_created_at = _first_start([row for row in client if _meta(row, "kind") == "task_created"])

    end_to_end_ms: int | None = None
    if received_at and task_created_at:
        start_dt = parse_dt(received_at)
        end_dt = parse_dt(task_created_at)
        if start_dt and end_dt:
            end_to_end_ms = int(round((end_dt - start_dt).total_seconds() * 1000))
    elif summary is not None and getattr(summary, "latency_ms", None) is not None:
        end_to_end_ms = int(summary.latency_ms)
    if end_to_end_ms is None:
        warnings.append("end-to-end latency unavailable (no task_created/receivedAt)")

    token_data, tokens_found = _token_sums(rows)
    tokens = {"input": 0, "cachedInput": 0, "output": 0, "total": 0}
    tokens.update(token_data["**totals**"])
    if not tokens_found:
        warnings.append("no token usage found in observations")

    negotiation = _negotiation(
        client or rows,
        _as_bool(run_meta.get("negotiated")),
        rows,
        _as_int(run_meta.get("rounds")),
    )
    if negotiation["rounds"] and not negotiation["roundsDetail"]:
        warnings.append("negotiation rounds reported by run metadata but no round detail found")
    if any(entry.get("clock") == "server" for entry in negotiation["roundsDetail"]):
        warnings.append(
            "negotiationStartedAt filled from a2a-t-server spans (server clock); latencyMs left null"
        )
    if negotiation["rounds"] and negotiation["latencyMs"] is None:
        warnings.append("negotiation rounds found but latency could not be computed")

    run_rows = client or rows
    counts = {
        "observations": len(rows),
        "runObservations": len(run_rows),
        "clientObservations": sum(1 for row in run_rows if _as_text(_meta(row, "side")) == "client"),
        "serverObservations": sum(1 for row in run_rows if _as_text(_meta(row, "side")) == "server"),
        "generations": sum(
            1
            for row in (client or rows)
            if str(row.get("type") or "").upper() == "GENERATION" or _meta(row, "kind") == "llm"
        ),
        "toolCalls": sum(
            1
            for row in (client or rows)
            if str(row.get("type") or "").upper() == "TOOL" or _meta(row, "kind") == "tool"
        ),
    }

    return {
        "schemaVersion": SCHEMA_VERSION,
        "traceId": trace_id or next((row.get("traceId") for row in rows if row.get("traceId")), None),
        "runId": run_meta.get("runId"),
        "protocol": run_meta.get("protocol"),
        "scenario": run_meta.get("scenario"),
        "runMode": run_meta.get("runMode"),
        "mode": run_meta.get("mode"),
        "modelMode": run_meta.get("modelMode"),
        "sourceKind": run_meta.get("sourceKind"),
        "transport": run_meta.get("transport"),
        "language": run_meta.get("language"),
        "discovery": run_meta.get("discovery"),
        "intentMode": run_meta.get("intentMode"),
        "chainId": chain_id,
        "caseId": run_meta.get("caseId"),
        "batchId": run_meta.get("batchId"),
        "trace": attrs,
        "timeline": {
            "receivedAt": received_at,
            "openedAt": opened_at,
            "sdkCalledAt": sdk_called_at,
            "requestSentAt": request_sent_at,
            "taskCreatedAt": task_created_at,
        },
        "latency": {"endToEndMs": end_to_end_ms},
        "tokens": {
            "input": tokens["input"],
            "cachedInput": tokens["cachedInput"],
            "output": tokens["output"],
            "total": tokens["total"],
            "bySide": token_data["bySide"],
        },
        "negotiation": negotiation,
        "counts": counts,
        "sources": {
            "receivedAt": "metadata.receivedAt (trace opened) or trace_opened.startTime",
            "sdkCalledAt": "task_prompt_rendered.startTime",
            "requestSentAt": "first message_sent.startTime",
            "taskCreatedAt": "task_created.startTime",
            "endToEndMs": "taskCreatedAt - receivedAt",
            "tokens": "sum(usageDetails.input/input_cached/output/total, else top-level usage, else OTel attributes.gen_ai.usage.*) per side, de-duplicated when a parent span repeats its child generation tokens; includes the a2a-t-server side",
            "chainId": "metadata.chainId, else trace.sessionId",
            "counts.clientObservations": "run rows (metadata.runId == traceId) with metadata.side == client; side=server rows still feed task_created/status timestamps but are counted separately",
            "negotiation.rounds": "max(distinct rounds from propose/negotiation_started/decision, run metadata rounds)",
            "tokens.siblingDedupe": "identical usage repeated on sibling generations under the same parent is counted once",
            "negotiation.notifiedAt": "decision_requested startTime when present (full level), else the client propose startTime (same instant the operator is notified)",
            "negotiation.negotiationStartedAt": "client negotiation_started span, else the a2a-t-server SendMessage/SendMessage-negotiation start (server clock, not used for latency)",
            "negotiation.serverDurationMs": "metadata attributes.gen_ai.agent.a2at.negotiation.total_duration_ms from a2a-t-server spans",
            "negotiation.latencyMs": "sum(decisionAt - negotiationStartedAt) over rounds; only for client-clock rounds",
        },
        "warnings": warnings,
    }
