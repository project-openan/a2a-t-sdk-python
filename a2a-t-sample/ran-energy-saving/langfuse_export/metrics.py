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

# Server generation nested under ``SendMessage``; the parent already carries its usage.
_PUSH_NOTIFICATION = "SendMessage-pushNotification"


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


def _row_sort_key(row: dict[str, Any]) -> float:
    """P9b: total order across rows with a missing or naive ``startTime``."""
    parsed = _start(row)
    if parsed is None:
        return float("-inf")
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


def _first_start(rows: list[dict[str, Any]]) -> str | None:
    times = [parsed for parsed in (_start(row) for row in rows) if parsed is not None]
    return _iso(min(times)) if times else None


def _distinct_run_ids(rows: list[dict[str, Any]]) -> list[str]:
    """Return the run ids present in one trace, newest attempt first."""
    groups: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        run_id = _as_text(_meta(row, "runId"))
        if run_id:
            groups.setdefault(run_id, []).append(row)

    def first_start(group: list[dict[str, Any]]) -> float:
        starts = [parsed.timestamp() for parsed in (_start(row) for row in group) if parsed]
        return min(starts) if starts else 0.0

    return sorted(groups, key=lambda run_id: first_start(groups[run_id]), reverse=True)


def select_run_id(rows: list[dict[str, Any]]) -> str | None:
    """Choose the effective attempt of a multi-run trace.

    A shared correction trace contains the blocked preflight and the corrected retry. The attempt
    that actually sent Task-T (``message_sent`` / ``task_created``) is the effective one; among
    several sent attempts the newest wins. A trace with no run id keeps the legacy behavior.
    """
    groups: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        run_id = _as_text(_meta(row, "runId"))
        if run_id:
            groups.setdefault(run_id, []).append(row)
    if not groups:
        return None

    def rank(item: tuple[str, list[dict[str, Any]]]) -> tuple[int, float, int]:
        _, group = item
        sent = any(_as_text(_meta(row, "kind")) in ("message_sent", "task_created") for row in group)
        starts = [parsed.timestamp() for parsed in (_start(row) for row in group) if parsed]
        return (1 if sent else 0, max(starts) if starts else 0.0, len(group))

    return max(groups.items(), key=rank)[0]


def _client_rows(
    rows: list[dict[str, Any]],
    trace_id: str | None,
    run_id: str | None = None,
) -> list[dict[str, Any]]:
    """Rows belonging to one attempt, not just client side.

    With an explicit ``run_id`` the rows are selected by ``metadata.runId``; a shared trace
    otherwise used to select the blocked attempt because its run id equals the trace id. Without
    an explicit id the legacy behavior is kept for single-run traces.

    `side=server` rows are kept: the only `task_created` / `status` observations may come
    from the server executor, while truly remote spans (no `runId`, e.g. the service-side
    `SendMessage`) must not leak into this run's timeline.
    """
    if run_id:
        wanted = run_id.strip()
        return [row for row in rows if _as_text(_meta(row, "runId")) == wanted]
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


def _merged_run_meta(selected: list[dict[str, Any]], all_rows: list[dict[str, Any]]) -> dict[str, str | None]:
    """Run-level keys from the selected attempt; chain-level keys fall back to the anchor."""
    primary = _run_meta(selected)
    fallback = _run_meta(all_rows)
    for key in RUN_META_KEYS:
        if not primary.get(key) and fallback.get(key):
            primary[key] = fallback[key]
    return primary


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


def _client_interceptions(rows: list[dict[str, Any]], trace_id: str | None) -> dict[str, Any]:
    """Issue #14 client-side interceptions: every ``Task-T validation blocked`` span.

    One blocked validation is one interception, whether the run was abandoned or the
    client corrected the request and retried inside the same run. ``blockedRuns`` is
    the de-duplicated set of run ids that were blocked, ``corrected`` records whether a
    ``correction_applied`` span exists, and ``reasons`` keeps the reported verdict
    reasons (e.g. ``negotiation.semantic_rejected``) for quick triage.
    """
    blocked: list[dict[str, Any]] = []
    corrected = False
    for row in rows:
        kind = _as_text(_meta(row, "kind"))
        if kind == "correction_applied":
            corrected = True
            continue
        if kind != "task_validation":
            continue
        name = str(row.get("name") or "").lower()
        verdict = (_as_text(_meta(row, "detail.verdict")) or "").lower()
        if "blocked" in name or verdict == "blocked" or _as_bool(_meta(row, "detail.blocking")):
            blocked.append(row)

    run_ids: list[str] = []
    reasons: list[str] = []
    for row in blocked:
        run_id = _as_text(_meta(row, "runId")) or (trace_id or "")
        if run_id and run_id not in run_ids:
            run_ids.append(run_id)
        reason = _as_text(_meta(row, "detail.reason"))
        if reason and reason not in reasons:
            reasons.append(reason)

    return {
        "count": len(blocked),
        "blockedRuns": len(run_ids),
        "runIds": run_ids,
        "reasons": reasons,
        "corrected": corrected,
    }


def _row_usage(row: dict[str, Any]) -> dict[str, int] | None:
    """Token counts for one row: usageDetails first, then OpenTelemetry attributes.

    Gross model: ``input`` is the total prompt input and includes the cached tokens;
    ``cachedInput`` is a subset of it (reported for analysis, never added again).
    ``total = input + output`` for every source.
    """
    details = row.get("usageDetails")
    if isinstance(details, dict) and details:
        # Client model is Gross (same as server): input includes cache.
        cached = _as_int(details.get("input_cached")) or 0
        input_tokens = _as_int(details.get("input")) or 0
        input_usage = _as_int(row.get("inputUsage"))
        gross_input = input_usage if input_usage is not None else input_tokens + cached
        output_tokens = _as_int(details.get("output")) or 0
        return {
            "input": gross_input,
            "cachedInput": cached,
            "output": output_tokens,
            "total": gross_input + output_tokens,
        }
    top_input = _as_int(row.get("inputUsage")) or 0
    top_output = _as_int(row.get("outputUsage")) or 0
    if top_input or top_output:
        return {
            "input": top_input,
            "cachedInput": 0,
            "output": top_output,
            "total": top_input + top_output,
        }
    # Server-side agent spans (a2a-t-server) expose token usage only in OTel
    # attributes, never in usageDetails/inputUsage.
    meta = row.get("metadata")
    if isinstance(meta, dict):
        input_tokens = _as_int(meta.get("attributes.gen_ai.usage.input_tokens"))
        output_tokens = _as_int(meta.get("attributes.gen_ai.usage.output_tokens"))
        cached = _as_int(meta.get("attributes.gen_ai.usage.cache_read.input_tokens"))
        if cached is None:
            cached = _as_int(meta.get("attributes.gen_ai.usage.input_cached_tokens")) or _as_int(meta.get("tokens.cached")) or 0
        if input_tokens is not None or output_tokens is not None:
            input_tokens = input_tokens or 0
            output_tokens = output_tokens or 0
            # Gross model: OTel input_tokens already includes cache_read/cache_write.
            return {
                "input": input_tokens,
                "cachedInput": cached,
                "output": output_tokens,
                "total": input_tokens + output_tokens,
            }
    return None


def _is_excluded_client_llm(row: dict[str, Any]) -> bool:
    """ADK agent calls (``adk_agent`` / ``client_decision_agent``) are not client tokens."""
    if _row_side(row) != "client":
        return False
    stage = _as_text(_meta(row, "stage")) or ""
    return "agent" in stage.lower()


def _token_sums(rows: list[dict[str, Any]]) -> tuple[dict[str, Any], bool]:
    totals = {"input": 0, "cachedInput": 0, "output": 0, "total": 0}
    by_side: dict[str, dict[str, int]] = {}
    excluded = {"input": 0, "cachedInput": 0, "output": 0, "total": 0}
    found = False

    # The a2a-t-server agent span may aggregate exactly the same tokens as its
    # nested generation (e.g. SendMessage -> SendMessage-negotiation both report
    # 130812/1500). Counting both would double that LLM call; prefer the deeper
    # observation when parent and child usage are identical. A nested
    # ``SendMessage-pushNotification`` is handled below by keeping the parent.
    usages: dict[str, dict[str, int]] = {}
    by_id: dict[str, dict[str, Any]] = {}
    skipped: set[str] = set()
    for row in rows:
        row_id = _as_text(row.get("id"))
        values = _row_usage(row)
        if row_id and values is not None and any(values.values()):
            by_id[row_id] = row
            usages[row_id] = values

    # Issue #15: a server ``SendMessage`` aggregates the usage of its nested
    # ``SendMessage-pushNotification`` generation. Count the parent only, so the
    # same LLM call is never summed twice (the two numbers may differ slightly).
    for row in rows:
        if _as_text(row.get("name")) != _PUSH_NOTIFICATION:
            continue
        parent_id = _as_text(row.get("parentObservationId"))
        row_id = _as_text(row.get("id"))
        if row_id and parent_id and parent_id in usages:
            skipped.add(row_id)

    for row_id, row in by_id.items():
        if row_id in skipped:
            continue
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
        if _is_excluded_client_llm(row):
            for key, value in values.items():
                excluded[key] += value
            continue
        bucket = by_side.setdefault(side, {"input": 0, "cachedInput": 0, "output": 0, "total": 0})
        for key, value in values.items():
            totals[key] += value
            bucket[key] += value
    return {"**totals**": totals, "bySide": by_side, "excludedClient": excluded}, found


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


def _negotiation_decision(
    rows: list[dict[str, Any]],
    triggered_meta: bool | None,
    all_rows: list[dict[str, Any]] | None = None,
    rounds_meta: int | None = None,
) -> dict[str, Any]:
    """Decision-paired round model (``--round-mode decision``).

    One round is a PROPOSE paired with the DECISION that answers it. Proposals that
    carry no ``metadata.round`` (pure A2A does this) attach to the next decision in
    time order instead of inventing a new round, and ``rounds`` is the max round
    number / run metadata, never the number of detail rows.
    """
    def sort_key(row: dict[str, Any]) -> float:
        # P9b: a row without startTime must not be compared to a datetime (TypeError),
        # and a naive timestamp must not be mixed with aware ones.
        parsed = _start(row)
        if parsed is None:
            return float("-inf")
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.timestamp()

    ordered = sorted(rows, key=sort_key)
    rounds_map: dict[int, dict[str, Any]] = {}

    def bucket(index: int) -> dict[str, Any]:
        return rounds_map.setdefault(
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
                "negotiationType": None,
                "hop": None,
            },
        )

    decisions: list[tuple[datetime | None, int, dict[str, Any]]] = []
    for row in ordered:
        kind = _as_text(_meta(row, "kind"))
        if kind in DECISION_KINDS:
            explicit = _as_int(_meta(row, "round"))
            round_no = explicit if explicit is not None else len(decisions) + 1
            decisions.append((_start(row), round_no, row))
            entry = bucket(round_no)
            if entry["decisionAt"] is None:
                entry["decisionAt"] = _iso(_start(row))

    def next_decision_round(at: datetime | None) -> int | None:
        if at is None:
            return None
        for start, round_no, __ in decisions:
            if start is not None and start >= at:
                return round_no
        return None

    def previous_decision_round(at: datetime | None) -> int | None:
        current: int | None = None
        for start, round_no, __ in decisions:
            if start is not None and at is not None and start <= at:
                current = round_no
        return current

    def fallback_round() -> int:
        return rounds_meta or 1

    for row in ordered:
        kind = _as_text(_meta(row, "kind"))
        start = _start(row)
        at = _iso(start)
        explicit = _as_int(_meta(row, "round"))
        if kind == "propose":
            round_no = explicit or next_decision_round(start) or fallback_round()
            entry = bucket(round_no)
            if entry["proposeAt"] is None:
                entry["proposeAt"] = at
                # P9a: keep the sample's P6 contract in decision mode too. Langfuse v2
                # flattens nested OTel attributes, so a real row exposes
                # ``detail.negotiationType``; unit fixtures may still pass a nested dict.
                detail = _meta(row, "detail")
                entry["negotiationType"] = _as_text(_meta(row, "detail.negotiationType")) or (
                    _as_text(detail.get("negotiationType")) if isinstance(detail, dict) else None
                )
                entry["hop"] = _as_text(_meta(row, "detail.hop")) or (
                    _as_text(detail.get("hop")) if isinstance(detail, dict) else None
                )
        elif kind == "negotiation_started":
            round_no = explicit or next_decision_round(start) or fallback_round()
            entry = bucket(round_no)
            if entry["negotiationStartedAt"] is None:
                entry["negotiationStartedAt"] = at
                entry["negotiationStartedAtSource"] = "client:negotiation_started"
                entry["clock"] = "client"
        elif kind == "llm":
            stage = _as_text(_meta(row, "stage"))
            if stage and stage.startswith("negotiation_"):
                round_no = (
                    explicit
                    or previous_decision_round(start)
                    or next_decision_round(start)
                    or fallback_round()
                )
                entry = bucket(round_no)
                if entry["sdkConstructedAt"] is None:
                    entry["sdkConstructedAt"] = at
        elif kind == "message_sent":
            phase = _as_text(_meta(row, "phase"))
            if phase in {"negotiation", "resolution"}:
                round_no = (
                    explicit
                    or previous_decision_round(start)
                    or next_decision_round(start)
                    or fallback_round()
                )
                entry = bucket(round_no)
                if entry["replySentAt"] is None:
                    entry["replySentAt"] = at

    for entry in rounds_map.values():
        if entry["notifiedAt"] is None and entry["proposeAt"] is not None:
            entry["notifiedAt"] = entry["proposeAt"]
            entry["notifiedAtSource"] = "propose"

    detail = [rounds_map[index] for index in sorted(rounds_map)]
    latencies: list[float] = []
    for entry in detail:
        if entry["clock"] == "server":
            continue
        begun = entry["negotiationStartedAt"] or entry["proposeAt"]
        finished = entry["decisionAt"]
        if begun and finished:
            start_dt = parse_dt(begun)
            end_dt = parse_dt(finished)
            if start_dt and end_dt:
                latencies.append((end_dt - start_dt).total_seconds() * 1000)
    __, server_duration = _server_negotiation(all_rows or [])
    return {
        "triggered": bool(detail) or bool(triggered_meta) or (rounds_meta or 0) > 0,
        "rounds": max(len(detail), rounds_meta or 0),
        "latencyMs": int(round(sum(latencies))) if latencies else None,
        "serverDurationMs": server_duration,
        "roundMode": "decision",
        "roundsDetail": detail,
    }


def _negotiation(
    rows: list[dict[str, Any]],
    triggered_meta: bool | None,
    all_rows: list[dict[str, Any]] | None = None,
    rounds_meta: int | None = None,
    mode: str = "legacy",
) -> dict[str, Any]:
    if mode == "decision":
        return _negotiation_decision(rows, triggered_meta, all_rows, rounds_meta)
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
                "negotiationType": None,
                "hop": None,
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
                # Langfuse v2 flattens nested OTel attributes, so a real row exposes
                # ``detail.negotiationType``; unit fixtures may still pass a nested dict.
                detail = _meta(row, "detail")
                entry["negotiationType"] = _as_text(_meta(row, "detail.negotiationType")) or (
                    _as_text(detail.get("negotiationType")) if isinstance(detail, dict) else None
                )
                entry["hop"] = _as_text(_meta(row, "detail.hop")) or (
                    _as_text(detail.get("hop")) if isinstance(detail, dict) else None
                )
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
        "roundMode": "legacy",
        "roundsDetail": ordered,
    }


def _ms_between(start: str | None, end: str | None) -> int | None:
    if not start or not end:
        return None
    start_dt = parse_dt(start)
    end_dt = parse_dt(end)
    if start_dt is None or end_dt is None:
        return None
    return int(round((end_dt - start_dt).total_seconds() * 1000))


def _ms_between_dt(start: datetime | None, end: datetime | None) -> int | None:
    if start is None or end is None:
        return None
    return int(round((end - start).total_seconds() * 1000))


def _activity_ms(
    rows: list[dict[str, Any]],
    start: datetime | None,
    end: datetime | None,
    excluded_stages: set[str],
) -> int | None:
    """Union of non-excluded client activity inside ``[start, end]`` (Issue #6 B)."""
    if start is None or end is None:
        return None
    intervals: list[tuple[datetime, datetime]] = []
    for row in rows:
        name = str(row.get("name") or "")
        if name.startswith("run ·") or name.startswith("workflow ·") or name == "trace opened":
            continue
        if _as_text(row.get("type")) == "EVENT":
            continue
        row_start = _start(row)
        row_end = parse_dt(row.get("endTime"))
        if row_start is None or row_end is None:
            continue
        if row_end < start or row_start > end:
            continue
        if _row_side(row) == "server":
            continue
        kind = _as_text(_meta(row, "kind"))
        stage = _as_text(_meta(row, "stage")) or ""
        if kind == "llm" and (stage in excluded_stages or stage.startswith("client_decision")):
            continue
        clip_start = max(row_start, start)
        clip_end = min(row_end, end)
        if clip_end > clip_start:
            intervals.append((clip_start, clip_end))
    if not intervals:
        return 0
    intervals.sort()
    merged: list[list[datetime]] = [list(intervals[0])]
    for interval_start, interval_end in intervals[1:]:
        if interval_start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], interval_end)
        else:
            merged.append([interval_start, interval_end])
    return int(round(sum((end_at - start_at).total_seconds() for start_at, end_at in merged) * 1000))


def _s_timings(rows: list[dict[str, Any]], trace_id: str | None) -> dict[str, Any]:
    """Issue #6 negotiation timings (S1/S3/S4/S7/S8 → 协商时延).

    S1 anchors on the retry run root when the trace contains one (a blocked first
    attempt is ignored), otherwise on ``trace opened``. S3/S8 are the server
    negotiation ``SendMessage`` spans (a new negotiation generation starts the next
    round); S4/S7 are Decision requested→Decision accept and Decision accept→Accept
    sent. 协商时延 = S3 + S4 + S8 + S7 for completed rounds only.
    """
    def start(row: dict[str, Any] | None) -> datetime | None:
        return _start(row) if row is not None else None

    def sort_key(row: dict[str, Any]) -> float:
        return _row_sort_key(row)

    ordered = sorted(rows, key=sort_key)
    run_roots = [row for row in ordered if str(row.get("name") or "").startswith("run ·")]
    retry_roots = [
        row
        for row in run_roots
        if _as_text(_meta(row, "runId")) not in (None, "", trace_id)
    ]
    opened = next((row for row in ordered if str(row.get("name") or "") == "trace opened"), None)
    if retry_roots:
        anchor_row = retry_roots[-1]
    elif opened is not None:
        anchor_row = opened
    elif run_roots:
        anchor_row = run_roots[0]
    else:
        anchor_row = ordered[0] if ordered else None
    anchor = start(anchor_row) if anchor_row is not None else None
    anchor_key = sort_key(anchor_row) if anchor_row is not None else None
    scoped = [
        row
        for row in ordered
        if anchor_key is None or start(row) is None or sort_key(row) >= anchor_key
    ]

    validations = [row for row in scoped if _as_text(_meta(row, "kind")) == "task_validation"]
    validations.sort(key=sort_key)
    passed = next((row for row in validations if "passed" in str(row.get("name") or "").lower()), None)
    failed = next(
        (
            row
            for row in validations
            if "blocked" in str(row.get("name") or "").lower()
            or "failed" in str(row.get("name") or "").lower()
        ),
        None,
    )
    if passed is not None:
        s1_ms = _ms_between_dt(anchor, start(passed))
    elif failed is not None:
        return {"s1Ms": None, "latencyMs": None, "stopped": True, "rounds": []}
    else:
        sent = next(
            (
                row
                for row in scoped
                if _as_text(_meta(row, "kind")) == "message_sent"
                and "task-t sent" in str(row.get("name") or "").lower()
            ),
            None,
        )
        s1_ms = _ms_between_dt(anchor, start(sent)) if sent is not None else None

    def server_scope(row: dict[str, Any]) -> bool:
        scope = (_as_text(_meta(row, "scope.name")) or "") + (
            _as_text(_meta(row, "resourceAttributes.service.name")) or ""
        )
        return "a2a-t-server" in scope.lower()

    server_sends = [
        row
        for row in ordered
        if server_scope(row)
        and str(row.get("name") or "") == "SendMessage"
        and _as_text(row.get("type")) == "AGENT"
    ]
    negotiation_spans = [row for row in ordered if str(row.get("name") or "").startswith("SendMessage-negotiation")]
    neg_parents = {
        _as_text(row.get("parentObservationId"))
        for row in negotiation_spans
        if row.get("parentObservationId")
    }
    server_rounds: list[dict[str, int | None]] = []
    for row in server_sends:
        duration = _ms_between_dt(start(row), parse_dt(row.get("endTime")))
        if _as_text(row.get("id")) in neg_parents:
            server_rounds.append({"s3Ms": duration, "s8Ms": 0})
        elif server_rounds:
            current = server_rounds[-1]
            current["s8Ms"] = int(current["s8Ms"] or 0) + int(duration or 0)

    def kind_rows(kind: str) -> list[dict[str, Any]]:
        return sorted(
            (row for row in ordered if _as_text(_meta(row, "kind")) == kind),
            key=sort_key,
        )

    proposes = kind_rows("propose")
    requested = kind_rows("decision_requested")
    decisions: list[dict[str, Any]] = []
    for kind in ("accept", "counter", "reject", "abort"):
        decisions.extend(kind_rows(kind))
    decisions.sort(key=sort_key)
    accept_sent = [
        row
        for row in kind_rows("message_sent")
        if _as_text(_meta(row, "phase")) in {"resolution", "negotiation"}
    ]
    accept_sent.sort(key=sort_key)

    rounds_client = max(len(proposes), len(requested), len(decisions))
    client_rounds: list[dict[str, int | None]] = []
    for index in range(rounds_client):
        request = requested[index] if index < len(requested) else None
        decision = decisions[index] if index < len(decisions) else None
        reply = accept_sent[index] if index < len(accept_sent) else None
        request_at = start(request)
        decision_at = start(decision)
        reply_at = start(reply)
        s4_raw = _ms_between_dt(request_at, decision_at)
        s7_raw = (
            _ms_between_dt(decision_at, reply_at)
            if decision_at is not None and reply_at is not None and reply_at >= decision_at
            else None
        )
        s4 = _activity_ms(ordered, request_at, decision_at, {"client_decision_agent"}) if s4_raw is not None else None
        # S7 keeps the negotiation_accept_extraction LLM inside the window (user
        # definition); only idle gaps are dropped.
        s7 = _activity_ms(ordered, decision_at, reply_at, set()) if s7_raw is not None else None
        client_rounds.append(
            {
                "s4Ms": s4,
                "s7Ms": s7,
                "s4RawMs": s4_raw,
                "s7RawMs": s7_raw,
                "s4ExcludedMs": (s4_raw - s4) if s4_raw is not None and s4 is not None else None,
                "s7ExcludedMs": (s7_raw - s7) if s7_raw is not None and s7 is not None else None,
            }
        )

    total_rounds = max(len(server_rounds), len(client_rounds))
    rounds: list[dict[str, Any]] = []
    total_ms = 0
    any_complete = False
    for index in range(total_rounds):
        server = server_rounds[index] if index < len(server_rounds) else {}
        client = client_rounds[index] if index < len(client_rounds) else {}
        s3 = server.get("s3Ms")
        s8 = server.get("s8Ms")
        s4 = client.get("s4Ms")
        s7 = client.get("s7Ms")
        complete = s3 is not None and s4 is not None and s7 is not None
        latency = (s3 or 0) + (s8 or 0) + (s4 or 0) + (s7 or 0) if complete else None
        if latency is not None:
            total_ms += latency
            any_complete = True
        rounds.append(
            {
                "round": index + 1,
                "s3Ms": s3,
                "s4Ms": s4,
                "s4RawMs": client.get("s4RawMs"),
                "s4ExcludedMs": client.get("s4ExcludedMs"),
                "s7Ms": s7,
                "s7RawMs": client.get("s7RawMs"),
                "s7ExcludedMs": client.get("s7ExcludedMs"),
                "s8Ms": s8,
                "negotiationLatencyMs": latency,
            }
        )
    return {
        "s1Ms": s1_ms,
        "latencyMs": total_ms if any_complete else None,
        "stopped": False,
        "rounds": rounds,
    }


def _client_negotiation_rounds(rows: list[dict[str, Any]]) -> int:
    """Rounds evidenced by client negotiation events (accept/decision_requested/propose)."""
    kinds = {"propose", "decision_requested", "accept", "counter", "reject", "abort"}
    events = [row for row in rows if _as_text(_meta(row, "kind")) in kinds]
    if not events:
        return 0
    numbered = [_as_int(_meta(row, "round")) for row in events]
    numbered = [value for value in numbered if value]
    if numbered:
        return max(numbered)
    return max(
        sum(1 for row in events if _as_text(_meta(row, "kind")) == "accept"),
        sum(1 for row in events if _as_text(_meta(row, "kind")) == "decision_requested"),
        sum(1 for row in events if _as_text(_meta(row, "kind")) == "propose"),
    )


def _server_send_blocks(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Server ``SendMessage`` durations with their A2A response state."""
    blocks: list[tuple[datetime, int, str]] = []
    for row in rows:
        scope = (_as_text(_meta(row, "scope.name")) or "") + (
            _as_text(_meta(row, "resourceAttributes.service.name")) or ""
        )
        if "a2a-t-server" not in scope.lower():
            continue
        if str(row.get("name") or "") != "SendMessage":
            continue
        start = _start(row)
        end = parse_dt(row.get("endTime"))
        if start is None or end is None:
            continue
        response = _meta(row, "attributes.gen_ai.agent.a2a.response")
        state = ""
        if isinstance(response, dict):
            status = response.get("status")
            if isinstance(status, dict) and status.get("state"):
                state = _as_text(status.get("state")) or ""
            if not state:
                update = response.get("statusUpdate")
                if isinstance(update, dict):
                    nested = update.get("status")
                    if isinstance(nested, dict) and nested.get("state"):
                        state = _as_text(nested.get("state")) or ""
        blocks.append((start, int(round((end - start).total_seconds() * 1000)), state))
    blocks.sort(key=lambda item: item[0])
    return [{"durationMs": duration, "state": state} for _, duration, state in blocks]


def _server_send_durations(rows: list[dict[str, Any]]) -> list[int]:
    """Durations of the a2a-t-server ``SendMessage`` spans, in start order."""
    durations: list[tuple[datetime, int]] = []
    for row in rows:
        scope = (_as_text(_meta(row, "scope.name")) or "") + (
            _as_text(_meta(row, "resourceAttributes.service.name")) or ""
        )
        if "a2a-t-server" not in scope.lower():
            continue
        if str(row.get("name") or "") != "SendMessage":
            continue
        start = _start(row)
        end = parse_dt(row.get("endTime"))
        if start is None or end is None:
            continue
        durations.append((start, int(round((end - start).total_seconds() * 1000))))
    durations.sort(key=lambda item: item[0])
    return [duration for _, duration in durations]


def build_metrics(
    rows: list[dict[str, Any]],
    trace_id: str | None = None,
    summary: Any = None,
    round_mode: str = "legacy",
    *,
    run_id: str | None = None,
) -> dict[str, Any]:
    """Build the metrics document for one exported trace.

    ``rows`` are the API observation rows (the same objects written to
    ``raw/observations.jsonl``). ``summary`` is optional and only used as a
    fallback for the end-to-end latency of non-A2A traces. ``round_mode`` selects
    the negotiation round model. ``run_id`` selects one attempt of a shared
    correction trace; without it a multi-run trace uses :func:`select_run_id`
    (the attempt that sent Task-T).
    """
    warnings: list[str] = []
    attempt_run_ids = _distinct_run_ids(rows)
    selected_run_id = (run_id or "").strip()
    if not selected_run_id and len(attempt_run_ids) > 1:
        selected_run_id = select_run_id(rows) or ""
        if selected_run_id:
            warnings.append(
                f"trace has {len(attempt_run_ids)} runs; metrics use run {selected_run_id}; "
                "pass run_id to choose another"
            )
    client = _client_rows(rows, trace_id, selected_run_id or None)
    if selected_run_id and not client:
        warnings.append(f"no observations found for run {selected_run_id}")
    elif not client:
        warnings.append("no client-side observations found; A2A fields left null")
    run_rows = client if selected_run_id else (client or rows)
    # A single-attempt trace keeps unattributed remote server rows (no runId) in its token and
    # negotiation audit; a shared trace must not mix the other attempt's rows.
    audit_rows = rows if len(attempt_run_ids) <= 1 else run_rows
    run_meta = _merged_run_meta(run_rows, rows)
    attrs = _trace_attrs(run_rows or rows, summary)
    chain_id = run_meta.get("chainId") or attrs.get("sessionId")

    opened = next((row for row in run_rows if row.get("name") == "trace opened"), None)
    if opened is None and selected_run_id and run_rows:
        # A retry has no "trace opened" bootstrap; its run root is the attempt's first row.
        opened = run_rows[0]
    opened_at = _iso(_start(opened)) if opened is not None else None
    received_at = run_meta.get("receivedAt") or opened_at

    sdk_called_at = _first_start([row for row in run_rows if _meta(row, "kind") == "task_prompt_rendered"])
    request_sent_at = _first_start([row for row in run_rows if _meta(row, "kind") == "message_sent"])
    task_created_at = _first_start([row for row in run_rows if _meta(row, "kind") == "task_created"])
    status_at = _first_start(
        [
            row
            for row in run_rows
            if _meta(row, "kind") == "status"
            and (
                "task modification succeeded" in str(row.get("name") or "").lower()
                or "task creation succeeded" in str(row.get("name") or "").lower()
            )
        ]
    )
    end_at = task_created_at or status_at

    end_to_end_ms: int | None = None
    if received_at and end_at:
        start_dt = parse_dt(received_at)
        end_dt = parse_dt(end_at)
        if start_dt and end_dt:
            end_to_end_ms = int(round((end_dt - start_dt).total_seconds() * 1000))
    elif summary is not None and getattr(summary, "latency_ms", None) is not None:
        end_to_end_ms = int(summary.latency_ms)
    if end_to_end_ms is None:
        warnings.append("end-to-end latency unavailable (no task_created/receivedAt)")

    token_data, tokens_found = _token_sums(audit_rows)
    # "No usage" is data, not absence: keep both side buckets at zero so downstream
    # tables can print 0 instead of blank cells (pure A2A runs have no LLM calls).
    for side in ("client", "server"):
        token_data["bySide"].setdefault(
            side, {"input": 0, "cachedInput": 0, "output": 0, "total": 0}
        )
    tokens = {"input": 0, "cachedInput": 0, "output": 0, "total": 0}
    tokens.update(token_data["**totals**"])
    if not tokens_found:
        warnings.append("no token usage found in observations")

    negotiation = _negotiation(
        run_rows,
        _as_bool(run_meta.get("negotiated")),
        audit_rows,
        _as_int(run_meta.get("rounds")),
        round_mode,
    )
    timing = _s_timings(rows, trace_id)
    negotiation["s1Ms"] = timing["s1Ms"]
    if timing["stopped"]:
        negotiation["latencyMs"] = None
        negotiation["rounds"] = 0
        negotiation["roundsDetail"] = []
        warnings.append("Task-T validation failed; negotiation timings skipped")
    else:
        negotiation["latencyMs"] = timing["latencyMs"]
        if (run_meta.get("protocol") or "").lower() == "pure":
            send_durations = _server_send_durations(rows)
            if send_durations:
                negotiation["latencyMs"] = sum(send_durations)
                negotiation["latencySource"] = "pure:sum(SendMessage durations)"
                negotiation["sendMessageDurationsMs"] = send_durations
        legacy_detail = {
            int(entry.get("round") or 0): entry
            for entry in (negotiation.get("roundsDetail") or [])
            if isinstance(entry, dict)
        }
        merged: list[dict[str, Any]] = []
        for round_timing in timing["rounds"]:
            entry = dict(
                legacy_detail.get(
                    round_timing["round"],
                    {
                        "round": round_timing["round"],
                        "proposeAt": None,
                        "notifiedAt": None,
                        "notifiedAtSource": None,
                        "negotiationStartedAt": None,
                        "negotiationStartedAtSource": None,
                        "clock": None,
                        "decisionAt": None,
                        "sdkConstructedAt": None,
                        "replySentAt": None,
                        "negotiationType": None,
                        "hop": None,
                    },
                )
            )
            entry.update(round_timing)
            merged.append(entry)
        if merged:
            negotiation["roundsDetail"] = merged
            negotiation["rounds"] = len(merged)
    blocks = _server_send_blocks(rows)
    input_required = [
        block for block in blocks if "INPUT_REQUIRED" in str(block["state"]).upper()
    ]
    negotiation["inputRequiredRounds"] = len(input_required)
    client_rounds = _client_negotiation_rounds(run_rows)
    meta_rounds = _as_int(run_meta.get("rounds")) or 0
    meta_negotiated = _as_bool(run_meta.get("negotiated"))
    if not blocks and not client_rounds and not meta_rounds and not meta_negotiated:
        # No server evidence: keep the legacy metadata-driven view.
        pass
    elif not (input_required or client_rounds or meta_rounds or meta_negotiated):
        negotiation["triggered"] = False
        negotiation["rounds"] = 0
        negotiation["roundsDetail"] = []
        negotiation["latencyMs"] = None
        negotiation.pop("latencySource", None)
        negotiation.pop("sendMessageDurationsMs", None)
    else:
        negotiation["triggered"] = True
        rounds = max(int(negotiation.get("rounds") or 0), len(input_required), client_rounds, meta_rounds)
        negotiation["rounds"] = rounds
        if (run_meta.get("protocol") or "").lower() == "pure":
            if blocks:
                negotiation["latencyMs"] = sum(block["durationMs"] for block in blocks)
                negotiation["latencySource"] = "pure:sum(SendMessage durations)"
                negotiation["sendMessageDurationsMs"] = [block["durationMs"] for block in blocks]
    if negotiation["rounds"] and not negotiation["roundsDetail"]:
        warnings.append("negotiation rounds reported by run metadata but no round detail found")
    if any(entry.get("clock") == "server" for entry in negotiation["roundsDetail"]):
        warnings.append(
            "negotiationStartedAt filled from a2a-t-server spans (server clock)"
        )
    if negotiation["rounds"] and negotiation["latencyMs"] is None:
        warnings.append("negotiation rounds found but latency could not be computed")

    counts = {
        "observations": len(rows),
        "runObservations": len(run_rows),
        "clientObservations": sum(1 for row in run_rows if _as_text(_meta(row, "side")) == "client"),
        "serverObservations": sum(1 for row in run_rows if _as_text(_meta(row, "side")) == "server"),
        "generations": sum(
            1
            for row in run_rows
            if str(row.get("type") or "").upper() == "GENERATION" or _meta(row, "kind") == "llm"
        ),
        "toolCalls": sum(
            1
            for row in run_rows
            if str(row.get("type") or "").upper() == "TOOL" or _meta(row, "kind") == "tool"
        ),
    }
    interceptions = _client_interceptions(rows, trace_id)
    counts["clientInterceptions"] = interceptions["count"]

    return {
        "schemaVersion": SCHEMA_VERSION,
        "traceId": trace_id or next((row.get("traceId") for row in rows if row.get("traceId")), None),
        "runId": selected_run_id or run_meta.get("runId"),
        "attemptCount": len(attempt_run_ids),
        "attemptRunIds": attempt_run_ids,
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
            "taskCreatedAt": end_at,
        },
        "latency": {"endToEndMs": end_to_end_ms},
        "tokens": {
            "input": tokens["input"],
            "cachedInput": tokens["cachedInput"],
            "excludedClient": token_data.get("excludedClient"),
            "output": tokens["output"],
            "total": tokens["total"],
            "bySide": token_data["bySide"],
        },
        "negotiation": negotiation,
        "clientInterception": interceptions,
        "counts": counts,
        "sources": {
            "receivedAt": "metadata.receivedAt (trace opened) or trace_opened.startTime",
            "sdkCalledAt": "task_prompt_rendered.startTime",
            "requestSentAt": "first message_sent.startTime",
            "taskCreatedAt": "task_created.startTime",
            "endToEndMs": "taskCreatedAt - receivedAt",
            "tokens": "gross input (usageDetails.input + input_cached, else inputUsage, else OTel input_tokens which already includes cache) per side; cachedInput is a subset and total = input + output",
            "chainId": "metadata.chainId, else trace.sessionId",
            "counts.clientObservations": "run rows (metadata.runId == traceId) with metadata.side == client; side=server rows still feed task_created/status timestamps but are counted separately",
            "negotiation.rounds": "max(distinct rounds from propose/negotiation_started/decision, run metadata rounds)",
            "tokens.siblingDedupe": "identical usage repeated on sibling generations under the same parent is counted once",
            "negotiation.notifiedAt": "decision_requested startTime when present (full level), else the client propose startTime (same instant the operator is notified)",
            "negotiation.negotiationStartedAt": "client negotiation_started span, else the a2a-t-server SendMessage/SendMessage-negotiation start (server clock, not used for latency)",
            "negotiation.serverDurationMs": "metadata attributes.gen_ai.agent.a2at.negotiation.total_duration_ms from a2a-t-server spans",
            "negotiation.latencyMs": "sum(decisionAt - negotiationStartedAt) over rounds; only for client-clock rounds",
            "clientInterception": "every task_validation row whose name/verdict marks it blocked (Issue #14); blockedRuns counts distinct runIds, corrected = a correction_applied span exists",
        },
        "warnings": warnings,
    }
