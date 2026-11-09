"""Normalization: raw API rows -> Observation tree, derived summary, scores, media refs."""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from .models import MediaRef, Observation, Score, TraceSummary, Usage

_DT_RE = re.compile(
    r"^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2}):(\d{2})(?:\.(\d+))?(Z|[+-]\d{2}:?\d{2})?$"
)
MEDIA_RE = re.compile(r"@@@langfuseMedia:([^@]*)@@@")
SHELL_PREFIX = "t-"


def parse_dt(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    match = _DT_RE.match(value.strip())
    if not match:
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    year, month, day, hour, minute, second, frac, tz = match.groups()
    micro = int((frac or "0")[:6].ljust(6, "0"))
    if tz in (None, "Z"):
        tzinfo = timezone.utc
    else:
        sign = 1 if tz[0] == "+" else -1
        digits = tz[1:].replace(":", "")
        tzinfo = timezone(sign * timedelta(hours=int(digits[:2]), minutes=int(digits[2:])))
    return datetime(int(year), int(month), int(day), int(hour), int(minute), int(second), micro, tzinfo)


def as_int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def derive_usage(row: dict[str, Any]) -> Usage:
    details = row.get("usageDetails")
    details = details if isinstance(details, dict) else {}
    legacy = row.get("usage")
    legacy = legacy if isinstance(legacy, dict) else {}

    def pick(*values: Any) -> int | None:
        for value in values:
            number = as_int(value)
            if number is not None:
                return number
        return None

    input_tokens = pick(row.get("inputUsage"), details.get("input"), details.get("inputTokens"), details.get("promptTokens"), legacy.get("input"), legacy.get("promptTokens"))
    output_tokens = pick(row.get("outputUsage"), details.get("output"), details.get("outputTokens"), details.get("completionTokens"), legacy.get("output"), legacy.get("completionTokens"))
    total_tokens = pick(row.get("totalUsage"), details.get("total"), legacy.get("total"))
    if input_tokens is not None and output_tokens is not None:
        # The API occasionally reports a `total` that does not equal input+output
        # (e.g. token counts coming from different OTel attributes); prefer the sum
        # so the transcript never shows an inconsistent "input / output / total" line.
        total_tokens = input_tokens + output_tokens
    elif total_tokens is None and (input_tokens is not None or output_tokens is not None):
        total_tokens = (input_tokens or 0) + (output_tokens or 0)

    cost_total: Decimal | None = None
    raw_cost = row.get("totalCost")
    if isinstance(raw_cost, (int, float)) and not isinstance(raw_cost, bool):
        cost_total = Decimal(str(raw_cost))

    cost_details: dict[str, Decimal] = {}
    raw_details = row.get("costDetails")
    if isinstance(raw_details, dict):
        for key, value in raw_details.items():
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                cost_details[str(key)] = Decimal(str(value))

    return Usage(
        input=input_tokens,
        output=output_tokens,
        total=total_tokens,
        cost_total=cost_total,
        cost_details=cost_details,
        raw_usage=dict(details) if details else dict(legacy),
        raw_cost=dict(raw_details) if isinstance(raw_details, dict) else {},
    )


def _iter_strings(value: Any):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _iter_strings(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _iter_strings(item)


def extract_media_refs(field: str, value: Any) -> list[MediaRef]:
    refs: list[MediaRef] = []
    for text in _iter_strings(value):
        for match in MEDIA_RE.finditer(text):
            payload = match.group(1)
            fields: dict[str, str] = {}
            for part in payload.split("|"):
                if "=" in part:
                    key, _, val = part.partition("=")
                    fields[key.strip()] = val.strip()
            media_id = fields.get("id")
            if not media_id:
                continue
            refs.append(
                MediaRef(
                    media_id=media_id,
                    mime_type=fields.get("type") or "application/octet-stream",
                    source_type=fields.get("source") or "",
                    field=field,
                    raw_token=match.group(0),
                )
            )
    return refs


def _to_text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False)


def normalize_observation(row: dict[str, Any]) -> Observation:
    start = parse_dt(row.get("startTime"))
    end = parse_dt(row.get("endTime"))
    latency = row.get("latency")
    latency = float(latency) if isinstance(latency, (int, float)) and not isinstance(latency, bool) else None
    if latency is None and start is not None and end is not None:
        latency = (end - start).total_seconds()
    input_raw = _to_text(row.get("input"))
    output_raw = _to_text(row.get("output"))
    metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
    model_params = row.get("modelParameters") if isinstance(row.get("modelParameters"), dict) else {}

    media_refs: list[MediaRef] = []
    media_refs += extract_media_refs("input", input_raw)
    media_refs += extract_media_refs("output", output_raw)
    media_refs += extract_media_refs("metadata", metadata)

    return Observation(
        id=str(row.get("id") or ""),
        trace_id=row.get("traceId"),
        parent_id=row.get("parentObservationId"),
        type=str(row.get("type") or "SPAN").upper(),
        name=str(row.get("name") or ""),
        start_time=start,
        end_time=end,
        completion_start_time=parse_dt(row.get("completionStartTime")),
        latency=latency,
        level=str(row.get("level") or "DEFAULT").upper(),
        status_message=row.get("statusMessage"),
        is_root=bool(row.get("isRootObservation")),
        model=row.get("model"),
        model_parameters=model_params,
        prompt_name=row.get("promptName") or None,
        prompt_version=str(row["promptVersion"]) if row.get("promptVersion") is not None else None,
        input_raw=input_raw,
        output_raw=output_raw,
        metadata=metadata,
        environment=row.get("environment") or None,
        version=str(row["version"]) if row.get("version") is not None else None,
        usage=derive_usage(row),
        media_refs=media_refs,
        raw=row,
    )


def _sort_key(obs: Observation) -> tuple[datetime, str]:
    return (obs.start_time or datetime.min.replace(tzinfo=timezone.utc), obs.id)


def build_tree(rows: list[dict[str, Any]], trace_id: str) -> tuple[list[Observation], list[str]]:
    warnings: list[str] = []
    observations: list[Observation] = []
    for row in rows:
        obs = normalize_observation(row)
        if obs.id == f"{SHELL_PREFIX}{trace_id}":
            warnings.append("trace shell row detected: trace has no observations")
            continue
        observations.append(obs)

    by_id = {obs.id: obs for obs in observations}
    roots: list[Observation] = []
    attached: list[Observation] = []
    for obs in observations:
        parent = by_id.get(obs.parent_id) if obs.parent_id else None
        if parent is not None and parent is not obs:
            parent.children.append(obs)
            attached.append(obs)
        else:
            roots.append(obs)

    # Break cycles: walk up from every node, detaching nodes that loop back.
    for obs in list(observations):
        seen: set[str] = set()
        current: Observation | None = obs
        while current is not None:
            if current.id in seen:
                break
            seen.add(current.id)
            current = by_id.get(current.parent_id) if current.parent_id else None
            if current is not None and current.id in seen:
                # cycle: detach `obs` if it is inside the loop
                parent = by_id.get(obs.parent_id)
                if parent is not None and obs in parent.children:
                    parent.children.remove(obs)
                    roots.append(obs)
                    warnings.append(f"parent cycle broken at observation {obs.id}")
                break

    for obs in observations:
        if obs.is_root and by_id.get(obs.parent_id) if obs.parent_id else None:
            warnings.append(f"app root has physical parent: {obs.id}")

    for obs in observations:
        obs.children.sort(key=_sort_key)
    roots.sort(key=_sort_key)

    depth = 0
    stack: list[tuple[Observation, int]] = [(root, 0) for root in roots]
    while stack:
        node, level = stack.pop()
        node.depth = level
        for child in node.children:
            stack.append((child, level + 1))

    if len(roots) > 1:
        warnings.append(f"multiple physical roots: {len(roots)}")
    return roots, warnings


def _first_non_empty(values: list[Any]) -> Any:
    for value in values:
        if value is None:
            continue
        if isinstance(value, str) and not value.strip():
            continue
        if isinstance(value, (list, dict)) and not value:
            continue
        return value
    return None


def derive_summary(
    observations: list[Observation],
    roots: list[Observation],
    score_count: int,
    trace_id: str,
) -> tuple[TraceSummary, list[str]]:
    warnings: list[str] = []
    ordered = sorted(observations, key=_sort_key)

    starts = [obs.start_time for obs in ordered if obs.start_time is not None]
    ends: list[datetime] = []
    for obs in ordered:
        if obs.end_time is not None:
            ends.append(obs.end_time)
        elif obs.start_time is not None:
            ends.append(obs.start_time + timedelta(seconds=obs.latency or 0.0))

    start = min(starts) if starts else None
    end = max(ends) if ends else None
    latency_ms = int((end - start).total_seconds() * 1000) if start and end else None

    def total_of(attr: str) -> int | None:
        values = [getattr(obs.usage, attr) for obs in ordered if getattr(obs.usage, attr) is not None]
        return sum(values) if values else None

    input_tokens = total_of("input")
    output_tokens = total_of("output")
    if input_tokens is not None and output_tokens is not None:
        total_tokens = input_tokens + output_tokens
    else:
        total_tokens = total_of("total")
        if total_tokens is None and (input_tokens is not None or output_tokens is not None):
            total_tokens = (input_tokens or 0) + (output_tokens or 0)

    costs = [obs.usage.cost_total for obs in ordered if obs.usage.cost_total is not None]
    total_cost = sum((c for c in costs), Decimal("0")) if costs else None
    if total_cost is None:
        warnings.append("no cost data in trace (pricing may not be configured)")

    def attr(name: str, getter) -> Any:
        values = [getter(obs) for obs in ordered]
        values = [v for v in values if v is not None and v != "" and v != []]
        if not values:
            return None
        first = values[0]
        for value in values[1:]:
            if value != first:
                warnings.append(f"conflicting trace {name} values; kept {first!r}")
                break
        return first

    name = attr("traceName", lambda obs: obs.raw.get("traceName"))
    user_id = attr("userId", lambda obs: obs.raw.get("userId"))
    session_id = attr("sessionId", lambda obs: obs.raw.get("sessionId"))
    tags = attr("tags", lambda obs: obs.raw.get("tags"))
    release = attr("release", lambda obs: obs.raw.get("release"))
    environment = attr("environment", lambda obs: obs.raw.get("environment"))

    if name is None and roots:
        name = roots[0].name or None
    if not name:
        name = None

    summary = TraceSummary(
        trace_id=trace_id,
        name=name,
        user_id=user_id,
        session_id=session_id,
        tags=list(tags) if isinstance(tags, list) else [],
        release=release,
        environment=environment,
        start_time=start,
        end_time=end,
        latency_ms=latency_ms,
        total_cost=total_cost,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=total_tokens,
        observation_count=len(observations),
        score_count=score_count,
        source="derived",
    )
    return summary, warnings


def apply_legacy_trace(summary: TraceSummary, legacy_trace: dict[str, Any] | None) -> None:
    """Fold the v3 legacy `/traces/{id}` object into a derived summary.

    v3 has a real trace entity, so it is authoritative for trace-level attributes:
    non-empty legacy values override values derived from observation rows, while
    fields missing from the legacy object keep the derived value. Aggregated values
    (times, tokens, cost, counts) stay derived from the observations so they keep
    covering every exported row. The summary source becomes ``"legacy"``.
    """
    if not isinstance(legacy_trace, dict) or not legacy_trace:
        return
    summary.source = "legacy"
    for attribute, key in (
        ("name", "name"),
        ("user_id", "userId"),
        ("session_id", "sessionId"),
        ("release", "release"),
        ("environment", "environment"),
    ):
        value = legacy_trace.get(key)
        if isinstance(value, str) and value.strip():
            setattr(summary, attribute, value)
    tags = legacy_trace.get("tags")
    if isinstance(tags, list) and tags:
        summary.tags = [str(tag) for tag in tags]


def normalize_scores(rows: list[dict[str, Any]]) -> list[Score]:
    scores: list[Score] = []
    for row in rows:
        subject = row.get("subject") if isinstance(row.get("subject"), dict) else {}
        kind = subject.get("kind")
        subject_id = subject.get("id")
        if not kind:
            if row.get("observationId"):
                kind, subject_id = "observation", row.get("observationId")
            elif row.get("sessionId"):
                kind, subject_id = "session", row.get("sessionId")
            elif row.get("traceId"):
                kind, subject_id = "trace", row.get("traceId")
        trace_id = row.get("traceId") or subject.get("traceId")
        scores.append(
            Score(
                id=row.get("id"),
                name=str(row.get("name") or ""),
                value=row.get("value"),
                data_type=row.get("dataType"),
                source=row.get("source"),
                timestamp=row.get("timestamp") or row.get("createdAt"),
                subject_kind=kind,
                subject_id=subject_id,
                subject_trace_id=trace_id,
                comment=row.get("comment"),
                author_user_id=row.get("authorUserId"),
                config_id=row.get("configId"),
                metadata=row.get("metadata") if isinstance(row.get("metadata"), dict) else {},
                raw=row,
            )
        )
    scores.sort(key=lambda s: (s.timestamp or "", s.name or ""))
    return scores


def detect_metadata_truncation(rows: list[dict[str, Any]]) -> bool:
    """Detect the documented (but not reproduced on Cloud) 200-char truncation.

    Only a string value that ends with ``...`` and has length 200 or 203 is
    considered truncated: a legitimate 200-char value without the ellipsis must
    not trigger a warning.
    """
    for row in rows:
        metadata = row.get("metadata")
        if not isinstance(metadata, dict):
            continue
        for value in _iter_strings(metadata):
            if isinstance(value, str) and len(value) in (200, 203) and value.endswith("..."):
                return True
    return False
