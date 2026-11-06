"""Per-run metrics used to compare the A2A-T and pure A2A modes.

The protocol layers already emit token usage and durations on trace spans. This module turns one
finished run into a single JSON-serializable record:

* token usage, normalized across the three producer shapes (ADK, A2A-T SDK, LangGraph planner);
* latency, split into model/tool/approval wait and the remaining orchestration time;
* accuracy against the scenario's ground truth (terminal state, negotiation rounds, agreed target
  and report presence).

It is deliberately free of business data: the expected values come from :class:`RunSpec`.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

from shared.run_constants import FINAL_STATE_TASK_COMPLETED

from protocol.client_flow import ClientFlowResult
from protocol.pure_a2a import canonical_target
from protocol.run_spec import RunSpec
from protocol.transcript import NegotiationTranscript

#: Token counters that mean "the provider reported no usage" (offline/scripted runs).
TOKEN_KEYS_PROMPT: Final[tuple[str, ...]] = ("prompt", "prompt_tokens", "input_tokens")
TOKEN_KEYS_COMPLETION: Final[tuple[str, ...]] = (
    "completion",
    "completion_tokens",
    "output_tokens",
)
TOKEN_KEYS_TOTAL: Final[tuple[str, ...]] = ("total", "total_tokens")
TOKEN_KEYS_REASONING: Final[tuple[str, ...]] = ("reasoning", "reasoning_tokens")
#: Cached-prefix counters. ``cached`` is the sample's normalized key; the others are raw provider
#: spellings that may appear on spans produced outside the traced clients.
TOKEN_KEYS_CACHED: Final[tuple[str, ...]] = (
    "cached",
    "cached_tokens",
    "input_cached",
    "prompt_cache_hit_tokens",
)

#: Approval span states that start and close one human wait.
APPROVAL_OPEN: Final[str] = "requested"
APPROVAL_CLOSED: Final[frozenset[str]] = frozenset({"approved", "rejected", "timeout"})

#: JSONL file (under the runs directory) that receives every finished run's metrics.
METRICS_JSONL_NAME: Final[str] = "metrics/run-metrics.jsonl"

_JSONL_LOCK: Final[threading.Lock] = threading.Lock()


def append_metrics_jsonl(runs_dir: Path, metrics: Mapping[str, object]) -> Path:
    """Append one metrics document to ``<runs_dir>/metrics/run-metrics.jsonl``.

    The JSONL file is the analysis-friendly mirror of the SQLite table: one self-contained record
    per run, readable with pandas or jq without opening the database.

    Returns:
        The path of the JSONL file.
    """
    path = runs_dir / METRICS_JSONL_NAME
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(dict(metrics), ensure_ascii=False)
    with _JSONL_LOCK, path.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")
    return path


def build_run_metrics(
    *,
    run_id: str,
    conversation_id: str | None,
    protocol: str,
    scenario: str,
    mode: str,
    transport: str,
    language: str,
    model_mode: str,
    model_label: str | None,
    status: str,
    transcript: NegotiationTranscript | None,
    result: ClientFlowResult | None,
    spans: Iterable[Mapping[str, object]],
    expected: RunSpec | None,
    error: str | None = None,
    source: Mapping[str, object] | None = None,
) -> dict[str, object]:
    """Build the metrics record of one run.

    Args:
        run_id: Run identifier.
        conversation_id: Client conversation, when the run belongs to one.
        protocol: ``a2a-t`` / ``pure`` / ``plain``.
        scenario: Scenario name.
        mode: Client decision mode.
        transport: Event transport.
        language: Process-level language.
        model_mode: ``live`` or ``offline``.
        model_label: Display label of the model.
        status: Run status (``finished`` / ``failed`` / ``interrupted``).
        transcript: Run transcript, when one exists.
        result: Client flow result, when the run finished.
        spans: Every trace span ingested for the run (client and server).
        expected: Scenario ground truth, when the catalog provides it.
        error: Failure message of a failed run.
        source: Request origin recorded when the run started.

    Returns:
        A JSON-serializable metrics document with ``tokens``, ``latency``, ``negotiation`` and
        ``accuracy`` sections.
    """
    span_list = [dict(span) for span in spans if isinstance(span, Mapping)]
    started_at = transcript.started_at if transcript is not None else None
    finished_at = transcript.finished_at if transcript is not None else None
    tokens = _tokens(span_list, model_mode=model_mode)
    latency = _latency(span_list, started_at=started_at, finished_at=finished_at)
    negotiation = _negotiation(
        protocol=protocol,
        transcript=transcript,
        result=result,
        expected=expected,
    )
    accuracy = _accuracy(
        protocol=protocol,
        language=language,
        expected=expected,
        transcript=transcript,
        result=result,
    )
    report = result.report if result is not None else None
    return {
        "runId": run_id,
        "conversationId": conversation_id,
        "protocol": protocol,
        "scenario": scenario,
        "mode": mode,
        "transport": transport,
        "language": language,
        "modelMode": model_mode,
        "modelLabel": model_label,
        "status": status,
        "finalState": result.final_state if result is not None else None,
        "startedAt": started_at,
        "finishedAt": finished_at,
        "durationMs": latency["totalMs"],
        "error": error,
        "source": dict(source) if isinstance(source, Mapping) else {},
        "sourceKind": (
            str(source.get("kind") or "") if isinstance(source, Mapping) else ""
        ),
        "tokens": tokens,
        "latency": latency,
        "negotiation": negotiation,
        "accuracy": accuracy,
        "report": report,
    }


def _tokens(spans: list[dict[str, object]], *, model_mode: str) -> dict[str, object]:
    """Aggregate every LLM span's token usage, normalized to one shape."""
    by_side: dict[str, dict[str, object]] = {}
    by_component: dict[str, dict[str, object]] = {}
    totals = {"prompt": 0, "completion": 0, "total": 0, "reasoning": 0}
    cached_total = 0
    cached_prompt_total = 0
    cached_calls = 0
    calls = 0
    known = 0
    estimated = False
    scripted = model_mode == "offline"
    for span in spans:
        if span.get("kind") != "llm":
            continue
        calls += 1
        usage = _span_usage(span)
        if usage is None:
            continue
        known += 1
        estimated = estimated or bool(usage.get("estimated"))
        scripted = scripted or bool(usage.get("scripted"))
        for key in totals:
            value = usage.get(key)
            if isinstance(value, int):
                totals[key] += value
        cached = usage.get("cached")
        if isinstance(cached, int):
            cached_calls += 1
            cached_total += cached
            prompt = usage.get("prompt")
            if isinstance(prompt, int):
                cached_prompt_total += prompt
        side = str(span.get("side") or "unknown")
        component = _component_key(span)
        _add_usage(by_side.setdefault(side, _empty_usage()), usage)
        _add_usage(by_component.setdefault(component, _empty_usage()), usage)
    available = known > 0 and not scripted
    cache_reported = available and cached_calls > 0
    _finalize_cache_buckets(by_side)
    _finalize_cache_buckets(by_component)
    return {
        "available": available,
        "scripted": scripted,
        "estimated": estimated,
        "llmCalls": calls,
        "reportedCalls": known,
        "missingCalls": calls - known,
        "prompt": totals["prompt"] if available else None,
        "completion": totals["completion"] if available else None,
        "total": totals["total"] if available else None,
        "reasoning": totals["reasoning"] if available else None,
        # ``None`` means "the provider never reported cache hits"; ``0`` means "reported, no hits".
        "cached": cached_total if cache_reported else None,
        "cachedCalls": cached_calls,
        "cacheHitRate": (
            round(cached_total / cached_prompt_total, 4)
            if cache_reported and cached_prompt_total
            else None
        ),
        "bySide": by_side,
        "byComponent": by_component,
    }


def _span_usage(span: Mapping[str, object]) -> dict[str, object] | None:
    """Return the normalized token map of one LLM span from its envelope or detail."""
    normalized = _normalize_usage(span.get("tokens"))
    if normalized is not None:
        return normalized
    detail = span.get("detail")
    if not isinstance(detail, Mapping):
        return None
    response = detail.get("response")
    if isinstance(response, Mapping):
        normalized = _normalize_usage(response.get("usage"))
        if normalized is not None:
            return normalized
    return _normalize_usage(detail.get("usage"))


def _normalize_usage(raw: object) -> dict[str, object] | None:
    """Normalize one producer token map to ``prompt/completion/total/reasoning``."""
    if not isinstance(raw, Mapping):
        return None
    prompt = _first_int(raw, TOKEN_KEYS_PROMPT)
    completion = _first_int(raw, TOKEN_KEYS_COMPLETION)
    total = _first_int(raw, TOKEN_KEYS_TOTAL)
    reasoning = _first_int(raw, TOKEN_KEYS_REASONING)
    cached = _first_int(raw, TOKEN_KEYS_CACHED)
    if cached is None:
        cached = _nested_cached_tokens(raw)
    if total is None and (prompt is not None or completion is not None):
        total = (prompt or 0) + (completion or 0)
    if prompt is None and completion is None and total is None:
        return None
    return {
        "prompt": prompt,
        "completion": completion,
        "total": total,
        "reasoning": reasoning,
        "cached": cached,
        "estimated": bool(raw.get("estimated")),
        "scripted": bool(raw.get("scripted")),
    }


def _nested_cached_tokens(raw: Mapping[str, object]) -> int | None:
    """Read ``prompt_tokens_details.cached_tokens`` from one raw OpenAI-style usage map."""
    details = raw.get("prompt_tokens_details")
    if not isinstance(details, Mapping):
        return None
    return _first_int(details, ("cached_tokens",))


def _component_key(span: Mapping[str, object]) -> str:
    """Return the comparison bucket of one LLM span (side + stage/phase)."""
    side = str(span.get("side") or "unknown")
    stage = span.get("stage")
    if isinstance(stage, str) and stage:
        return f"{side}:{stage}"
    phase = span.get("phase")
    return f"{side}:{phase or 'llm'}"


def _empty_usage() -> dict[str, object]:
    """Return one zeroed usage bucket."""
    return {
        "prompt": 0,
        "completion": 0,
        "total": 0,
        "reasoning": 0,
        "cached": 0,
        "cachedCalls": 0,
        "cachedPrompt": 0,
        "calls": 0,
    }


def _add_usage(bucket: dict[str, object], usage: Mapping[str, object]) -> None:
    """Add one normalized usage map into a bucket."""
    bucket["calls"] = int(bucket.get("calls", 0)) + 1
    for key in ("prompt", "completion", "total", "reasoning"):
        value = usage.get(key)
        if isinstance(value, int):
            bucket[key] = int(bucket.get(key, 0)) + value
    cached = usage.get("cached")
    if isinstance(cached, int):
        bucket["cached"] = int(bucket.get("cached", 0)) + cached
        bucket["cachedCalls"] = int(bucket.get("cachedCalls", 0)) + 1
        prompt = usage.get("prompt")
        if isinstance(prompt, int):
            bucket["cachedPrompt"] = int(bucket.get("cachedPrompt", 0)) + prompt


def _finalize_cache_buckets(buckets: Mapping[str, dict[str, object]]) -> None:
    """Add the per-bucket hit rate and drop the internal prompt accumulator."""
    for bucket in buckets.values():
        prompt_total = int(bucket.pop("cachedPrompt", 0))
        cached = int(bucket.get("cached", 0))
        reported = int(bucket.get("cachedCalls", 0))
        bucket["cacheHitRate"] = (
            round(cached / prompt_total, 4) if reported and prompt_total else None
        )


def _latency(
    spans: list[dict[str, object]],
    *,
    started_at: str | None,
    finished_at: str | None,
) -> dict[str, object]:
    """Aggregate total, model, tool, approval and orchestration latency."""
    total_ms = _delta_ms(started_at, finished_at)
    model_ms = _sum_duration(spans, "llm")
    tool_ms = _sum_duration(spans, "tool")
    approval_ms, time_to_approval_ms, approval_count = _approval_wait(spans, started_at)
    other_ms = None
    if total_ms is not None:
        other_ms = max(0.0, total_ms - model_ms - tool_ms - approval_ms)
    return {
        "totalMs": round(total_ms, 3) if total_ms is not None else None,
        "modelMs": round(model_ms, 3),
        "toolMs": round(tool_ms, 3),
        "approvalWaitMs": round(approval_ms, 3),
        "approvalCount": approval_count,
        "timeToApprovalMs": (
            round(time_to_approval_ms, 3) if time_to_approval_ms is not None else None
        ),
        "otherMs": round(other_ms, 3) if other_ms is not None else None,
        "llmCalls": sum(1 for span in spans if span.get("kind") == "llm"),
        "toolCalls": sum(1 for span in spans if span.get("kind") == "tool"),
    }


def _sum_duration(spans: Iterable[Mapping[str, object]], kind: str) -> float:
    """Sum the ``durationMs`` of one span kind."""
    total = 0.0
    for span in spans:
        if span.get("kind") != kind:
            continue
        value = span.get("durationMs")
        if isinstance(value, (int, float)):
            total += float(value)
    return total


def _approval_wait(
    spans: list[dict[str, object]],
    started_at: str | None,
) -> tuple[float, float | None, int]:
    """Pair approval requested/resolved spans and return wait, first-request offset, count."""
    ordered = sorted(
        (span for span in spans if span.get("kind") == "approval"),
        key=lambda span: _iso_ms(span.get("startedAt")) or 0.0,
    )
    pending: float | None = None
    first_request: float | None = None
    total = 0.0
    count = 0
    for span in ordered:
        detail = span.get("detail")
        state = detail.get("approvalState") if isinstance(detail, Mapping) else None
        at = _iso_ms(span.get("startedAt"))
        if at is None:
            continue
        if state == APPROVAL_OPEN and pending is None:
            pending = at
            if first_request is None:
                first_request = at
        elif state in APPROVAL_CLOSED and pending is not None:
            total += max(0.0, at - pending)
            count += 1
            pending = None
    start = _iso_ms(started_at)
    time_to_approval = (
        max(0.0, first_request - start)
        if first_request is not None and start is not None
        else None
    )
    return total, time_to_approval, count


def _negotiation(
    *,
    protocol: str,
    transcript: NegotiationTranscript | None,
    result: ClientFlowResult | None,
    expected: RunSpec | None,
) -> dict[str, object]:
    """Summarize negotiation rounds/messages and the expected round budget."""
    observed = _observed_sequence(protocol, transcript, result)
    rounds = max((step["round"] for step in observed if isinstance(step.get("round"), int)), default=0)
    expected_rounds = (
        max(
            (
                step.round
                for step in expected.expected_sequence
                if isinstance(step.round, int)
            ),
            default=0,
        )
        if expected is not None
        else None
    )
    return {
        "rounds": rounds,
        "expectedRounds": expected_rounds,
        "messages": len(observed),
        "observedSequence": observed,
        "outcome": result.final_state if result is not None else None,
    }


def _accuracy(
    *,
    protocol: str,
    language: str,
    expected: RunSpec | None,
    transcript: NegotiationTranscript | None,
    result: ClientFlowResult | None,
) -> dict[str, object]:
    """Evaluate the run against the scenario ground truth."""
    checks: dict[str, object] = {}
    if expected is not None and expected.expected_final_state:
        actual_state = result.final_state if result is not None else None
        checks["finalState"] = _check(expected.expected_final_state, actual_state)
    if expected is not None and expected.expected_sequence:
        observed = _observed_sequence(protocol, transcript, result)
        if protocol == "a2a-t":
            checks["sequence"] = _check(
                [_step_dict(step) for step in expected.expected_sequence],
                observed,
            )
        else:
            # Pure A2A answers are not Negotiation-T performatives; the round count and the
            # agreed target are the comparable checks.
            checks["sequence"] = None
    if expected is not None:
        rounds = _negotiation(
            protocol=protocol,
            transcript=transcript,
            result=result,
            expected=expected,
        )
        checks["rounds"] = _check(rounds["expectedRounds"], rounds["rounds"])
    if expected is not None and expected.expected_negotiation_target:
        observed_target = _observed_target(protocol, language, transcript, result)
        checks["target"] = _check(
            canonical_target(expected.expected_negotiation_target, language),
            observed_target,
        )
    if expected is not None:
        expects_report = expected.expected_final_state == FINAL_STATE_TASK_COMPLETED
        checks["report"] = _check(
            expects_report, result.report is not None if result else False
        )
    applicable = [check for check in checks.values() if isinstance(check, Mapping)]
    passed = sum(1 for check in applicable if check.get("passed") is True)
    score = passed / len(applicable) if applicable else None
    all_passed = passed == len(applicable) if applicable else None
    return {
        "score": round(score, 4) if score is not None else None,
        "passed": all_passed,
        "checks": checks,
        "expected": {
            "finalState": expected.expected_final_state if expected is not None else None,
            "rounds": _expected_rounds(expected),
            "target": (
                expected.expected_negotiation_target if expected is not None else None
            ),
        },
    }


def _check(expected: object, actual: object) -> dict[str, object]:
    """Build one comparison check."""
    return {"expected": expected, "actual": actual, "passed": expected == actual}


def _expected_rounds(expected: RunSpec | None) -> int | None:
    """Return the highest expected negotiation round."""
    if expected is None:
        return None
    return max(
        (step.round for step in expected.expected_sequence if isinstance(step.round, int)),
        default=0,
    )


def _observed_sequence(
    protocol: str,
    transcript: NegotiationTranscript | None,
    result: ClientFlowResult | None,
) -> list[dict[str, object]]:
    """Return the observed negotiation steps in order, normalized across protocols."""
    if protocol == "pure":
        return _pure_sequence(result)
    if transcript is None:
        return []
    steps: list[tuple[int, dict[str, object]]] = []
    for entry in transcript.events:
        performative = entry.get("performative")
        if isinstance(performative, str) and performative:
            steps.append(
                (
                    int(entry.get("seq") or 0),
                    {
                        "actor": "server",
                        "performative": performative,
                        "round": entry.get("round"),
                    },
                )
            )
        elif entry.get("kind") == "message":
            steps.append(
                (
                    int(entry.get("seq") or 0),
                    {"actor": "server", "performative": "MESSAGE", "round": None},
                )
            )
    for entry in transcript.decisions:
        performative = entry.get("performative")
        if isinstance(performative, str) and performative:
            steps.append(
                (
                    int(entry.get("seq") or 0),
                    {
                        "actor": "client",
                        "performative": performative,
                        "round": entry.get("round"),
                    },
                )
            )
    steps.sort(key=lambda item: item[0])
    return [step for _, step in steps]


def _pure_sequence(result: ClientFlowResult | None) -> list[dict[str, object]]:
    """Return the pure A2A propose/answer steps."""
    if result is None:
        return []
    steps: list[tuple[int, dict[str, object]]] = []
    for index, event in enumerate(result.events):
        pure = _pure_meta(event)
        kind = pure.get("kind")
        if kind not in ("negotiation", "answer"):
            continue
        round_number = pure.get("round")
        steps.append(
            (
                index,
                {
                    "actor": "client" if kind == "answer" else "server",
                    "performative": "ANSWER" if kind == "answer" else "PROPOSE",
                    "round": int(round_number) if isinstance(round_number, (int, float)) else None,
                },
            )
        )
    return [step for _, step in steps]


def _observed_target(
    protocol: str,
    language: str,
    transcript: NegotiationTranscript | None,
    result: ClientFlowResult | None,
) -> str | None:
    """Return the target the client finally agreed to, canonicalized."""
    if protocol == "pure":
        if result is None:
            return None
        for event in reversed(result.events):
            if _pure_meta(event).get("kind") != "answer":
                continue
            text = event.get("text")
            if not isinstance(text, str):
                detail = event.get("detail")
                text = detail.get("text") if isinstance(detail, Mapping) else None
            if isinstance(text, str):
                target = canonical_target(text, language)
                if target:
                    return target
        return None
    if transcript is None:
        return None
    # The accept decision does not carry the refilled Task-T values; the outbound message trace
    # does. Its last Task-T copy is the value the client actually agreed to.
    for entry in reversed(transcript.events):
        if entry.get("entryType") != "trace" or entry.get("kind") != "message_sent":
            continue
        detail = entry.get("detail")
        task_t = detail.get("taskT") if isinstance(detail, Mapping) else None
        if isinstance(task_t, str):
            canonical = canonical_target(task_t, language)
            if canonical:
                return canonical
    for entry in reversed(transcript.decisions):
        if entry.get("action") != "accept":
            continue
        filled = entry.get("filledParams")
        if not isinstance(filled, Mapping):
            continue
        target = filled.get("guaranteedThroughputTarget")
        if isinstance(target, str):
            canonical = canonical_target(target, language)
            if canonical:
                return canonical
    return None


def _pure_meta(event: Mapping[str, object]) -> dict[str, object]:
    """Return the sample-local pure metadata of one normalized event."""
    metadata = event.get("metadata")
    if not isinstance(metadata, Mapping):
        return {}
    pure = metadata.get("pure")
    return dict(pure) if isinstance(pure, Mapping) else {}


def _step_dict(step: object) -> dict[str, object]:
    """Return one ``ExpectedStep`` as a comparable dict."""
    return {
        "actor": getattr(step, "actor", None),
        "performative": getattr(step, "performative", None),
        "round": getattr(step, "round", None),
    }


def _first_int(values: Mapping[str, object], keys: tuple[str, ...]) -> int | None:
    """Return the first integer value among ``keys``."""
    for key in keys:
        value = values.get(key)
        if isinstance(value, bool):
            continue
        if isinstance(value, int):
            return value
    return None


def _delta_ms(start: object, end: object) -> float | None:
    """Return the millisecond difference between two ISO timestamps."""
    start_ms = _iso_ms(start)
    end_ms = _iso_ms(end)
    if start_ms is None or end_ms is None:
        return None
    return max(0.0, end_ms - start_ms)


def _iso_ms(value: object) -> float | None:
    """Parse one ISO 8601 timestamp to epoch milliseconds."""
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.timestamp() * 1000.0


__all__ = ["METRICS_JSONL_NAME", "append_metrics_jsonl", "build_run_metrics"]
