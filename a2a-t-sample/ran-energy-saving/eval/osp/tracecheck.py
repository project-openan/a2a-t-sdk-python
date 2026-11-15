"""Pure trace-completeness judgement for the acceptance review console.

The module performs no I/O. ``demo/web/review/traces.py`` normalizes one local Langfuse
snapshot into observation mappings and passes them here with the facts of the run (protocol,
run mode, interception, negotiation rounds, whether a Task was accepted).

The expected points are derived from the sample's phase whitelist, so the acceptance oracle
changes with the exporter rather than with a hand-maintained table. A configuration change
(``full`` / ``phase`` / ``minimal``) is therefore a contract change, not a silent gap.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Final

from eval.osp.compare import flatten_intent, flatten_task_t

PROTOCOL_A2A_T: Final[str] = "a2a-t"
PROTOCOL_A2A: Final[str] = "a2a"

TRACE_PASS: Final[str] = "pass"
TRACE_FAIL: Final[str] = "fail"
TRACE_PARTIAL: Final[str] = "partial"
TRACE_NOT_APPLICABLE: Final[str] = "not_applicable"

REASON_NOT_EXPORTED: Final[str] = "trace_not_exported"
REASON_FRAMES_MISMATCH: Final[str] = "trace_frames_mismatch"
REASON_MISSING_CLIENT: Final[str] = "trace_missing_client"
REASON_MISSING_INGEST: Final[str] = "trace_missing_ingest"
REASON_MISSING_VENDOR: Final[str] = "trace_missing_vendor"
REASON_CONTENT_UNAVAILABLE: Final[str] = "trace_content_unavailable"
REASON_NMS_HOP_UNKNOWN: Final[str] = "trace_nms_hop_unknown"

#: Every reason code the console exposes. The report iterates this tuple instead of matching
#: strings, so a new code cannot silently disappear from the acceptance counts.
TRACE_REASONS: Final[tuple[str, ...]] = (
    REASON_NOT_EXPORTED,
    REASON_FRAMES_MISMATCH,
    REASON_MISSING_CLIENT,
    REASON_MISSING_INGEST,
    REASON_MISSING_VENDOR,
    REASON_CONTENT_UNAVAILABLE,
    REASON_NMS_HOP_UNKNOWN,
)

ATTRIBUTION_OK: Final[str] = "ok"
ATTRIBUTION_UNKNOWN: Final[str] = "unknown"
ATTRIBUTION_MISSING_CLIENT: Final[str] = "missing_client"
ATTRIBUTION_MISSING_INGEST: Final[str] = "missing_ingest"
ATTRIBUTION_MISSING_VENDOR: Final[str] = "missing_vendor"
ATTRIBUTION_CONTENT_UNAVAILABLE: Final[str] = "content_unavailable"

#: Attribution states that make the trace axis fail. ``unknown`` deliberately stays out: the
#: local emission layer is not persisted today (``client-state.sqlite3.trace_json`` is always
#: NULL), so a missing point we cannot attribute is not evidence of a producer defect.
FAILING_ATTRIBUTION: Final[frozenset[str]] = frozenset(
    {ATTRIBUTION_MISSING_INGEST, ATTRIBUTION_MISSING_VENDOR, ATTRIBUTION_MISSING_CLIENT,
     ATTRIBUTION_CONTENT_UNAVAILABLE}
)

_DECISION_KINDS: Final[frozenset[str]] = frozenset({"accept", "counter", "reject", "abort"})


@dataclass(frozen=True)
class TracePoint:
    """One expected observation, with the count the run should produce."""

    id: str
    label: str
    kind: str | None = None
    stage: str | None = None
    round: int | None = None
    count: int = 1
    interception: bool | None = None


@dataclass(frozen=True)
class TraceContext:
    """Facts of one tester item that drive the expected trace points."""

    protocol: str
    run_mode: str = "create"
    blocked: bool = False
    followup: bool = False
    rounds: int = 0
    accepted: bool = True
    validation_on: bool = True
    run_id: str | None = None


@dataclass(frozen=True)
class PointResult:
    """Observed count of one expected point."""

    point: TracePoint
    observed: int

    @property
    def ok(self) -> bool:
        return self.observed >= self.point.count


@dataclass(frozen=True)
class AttributionFinding:
    """Why one expected point is missing from the Langfuse snapshot."""

    point: str
    label: str
    status: str
    detail: str = ""


@dataclass(frozen=True)
class TraceJudgement:
    """Result of the pure trace judgement."""

    status: str
    reason: str
    points: tuple[PointResult, ...]
    attribution: str
    findings: tuple[AttributionFinding, ...]
    frames: Mapping[str, object]


@dataclass(frozen=True)
class Fidelity:
    """Comparison of two copies of the same evidence (judged vs exported)."""

    comparable: bool
    equal: bool
    rows: tuple[Mapping[str, object], ...]
    truncated: bool = False


def task_t_fidelity(judged: str, dispatched: str, *, truncated: bool = False) -> Fidelity:
    """Compare the Task-T the console judged with the Task-T the Langfuse export carries.

    ``recorded`` sections (prose the comparison deliberately does not judge) never make the
    copies unequal; only ``changed`` / ``missing`` do.
    """
    left = judged.strip()
    right = dispatched.strip()
    if not left or not right:
        return Fidelity(False, False, (), truncated)
    rows = tuple(
        {
            "path": row.path,
            "judged": row.expected,
            "dispatched": row.actual,
            "status": row.status,
            "verdict": row.verdict,
            "reason": row.reason,
        }
        for row in flatten_task_t(left, right)
    )
    failures = [
        row
        for row in rows
        if (row["verdict"] or row["status"]) in {"changed", "missing"}
        and not (_is_missing(row["judged"]) and _is_missing(row["dispatched"]))
    ]
    return Fidelity(True, not failures, rows, truncated)


def intent_corroboration(
    server: Mapping[str, object] | None,
    params: Mapping[str, object] | None,
) -> Fidelity:
    """Compare the acceptance oracle with the Langfuse ``task_created`` structured reading.

    Both copies should be the same downstream Task reading. A drift is evidence-chain noise, not
    a replacement oracle: the console keeps ``serverIntent`` as the only final intent.
    """
    if not server or not params or "Operation type" not in params:
        return Fidelity(False, False, ())
    rows = tuple(
        {
            "path": row.path,
            "server": row.expected,
            "langfuse": row.actual,
            "status": row.status,
            "verdict": row.verdict,
            "reason": row.reason,
        }
        for row in flatten_intent(dict(server), dict(params))
    )
    failures = [row for row in rows if row["status"] in {"changed", "missing", "added"}]
    return Fidelity(True, not failures, rows)


def process_points(
    payload: Mapping[str, object],
    context: TraceContext,
) -> tuple[PointResult, ...]:
    """Return the per-point expectation table the UI renders under the Task-T card."""
    observations_value = payload.get("observations")
    observations: Sequence[Mapping[str, object]] = (
        observations_value if isinstance(observations_value, Sequence) else ()
    )
    return match_points(expected_client_points(context), observations, run_id=context.run_id)


def dispatched_task_t(
    payload: Mapping[str, object],
) -> tuple[str, bool]:
    """Return the Task-T text the Langfuse export carries, preferring the non-interception copy."""
    observations = _observations(payload)
    candidates = [
        observation
        for observation in observations
        if _text(observation.get("kind")) == "task_prompt_rendered"
        and _text(observation.get("promptText"))
    ]
    candidates.sort(key=lambda observation: observation.get("interception") is True)
    if not candidates:
        return "", False
    chosen = candidates[0]
    return _text(chosen.get("promptText")), chosen.get("promptTruncated") is True


def original_request(payload: Mapping[str, object]) -> str:
    """Return the request the interception recorded, or ``""`` when it is not in the export."""
    for observation in _observations(payload):
        if observation.get("interception") is True and _text(observation.get("userRequest")):
            return _text(observation.get("userRequest"))
    return ""


def intent_params(payload: Mapping[str, object]) -> Mapping[str, object] | None:
    """Return the structured Task reading carried by the Langfuse ``task_created`` span."""
    for observation in _observations(payload):
        if _text(observation.get("kind")) == "task_created":
            params = observation.get("params")
            if isinstance(params, Mapping):
                return params
    return None


def _observations(payload: Mapping[str, object]) -> tuple[Mapping[str, object], ...]:
    value = payload.get("observations")
    if not isinstance(value, Sequence):
        return ()
    return tuple(item for item in value if isinstance(item, Mapping))


def expected_client_points(context: TraceContext) -> tuple[TracePoint, ...]:
    """Return the client-side observations one run should produce.

    ``pure`` A2A has no Task-T pipeline, so only the client hop's message and the negotiation
    decisions are expected there. Everything else follows the run mode and the interception.
    """
    if context.protocol == PROTOCOL_A2A:
        points: list[TracePoint] = [
            TracePoint("message_sent", "客户端消息帧", kind="message_sent")
        ]
        points.extend(_round_points(context.rounds, agent=False))
        if context.accepted:
            points.append(TracePoint("task_created", "任务接受", kind="task_created"))
        return tuple(points)

    modify = context.run_mode == "modify"
    points = [
        TracePoint("task_prompt_rendered", "Task-T 渲染", kind="task_prompt_rendered"),
        TracePoint("task_validation", "Task-T 校验", kind="task_validation", interception=False),
    ]
    if context.validation_on:
        points.append(
            TracePoint(
                "gen:task_t_client_validation",
                "LLM 客户端校验",
                stage="task_t_client_validation",
            )
        )
    if context.blocked and context.followup:
        points.append(
            TracePoint(
                "blocked_replay_extraction",
                "拦截回放的抽取调用",
                stage="task_t_slot_extraction",
            )
        )
        points.append(
            TracePoint(
                "blocked_replay_validation",
                "拦截回放的校验结果",
                kind="task_validation",
                interception=True,
            )
        )
    if modify:
        points.append(
            TracePoint("gen:task_t_modify_extraction", "LLM 修改抽取", stage="task_t_modify_extraction")
        )
    elif not (context.blocked and context.followup):
        points.append(
            TracePoint("gen:task_t_slot_extraction", "LLM 槽位抽取", stage="task_t_slot_extraction")
        )
    elif context.blocked and context.followup:
        points.append(
            TracePoint("gen:task_t_slot_extraction", "LLM 槽位抽取", stage="task_t_slot_extraction")
        )
    points.append(
        TracePoint(
            "message_sent",
            "客户端消息帧",
            kind="message_sent",
            count=1 + max(context.rounds, 0),
        )
    )
    points.extend(_round_points(context.rounds))
    if context.accepted:
        points.append(TracePoint("task_created", "任务接受", kind="task_created"))
    return tuple(points)


def _round_points(rounds: int, *, agent: bool = True) -> tuple[TracePoint, ...]:
    """Return the per-round negotiation points of one run.

    The decision LLM and its tool call do not carry a round in their metadata, so they are one
    point with ``count = rounds`` rather than one point per round. The decision request, the
    proposal and the decision itself do carry ``round`` and stay per-round.
    """
    points: list[TracePoint] = []
    for number in range(1, max(rounds, 0) + 1):
        points.extend(
            (
                TracePoint(
                    f"decision_requested:R{number}",
                    f"决策请求 R{number}",
                    kind="decision_requested",
                    round=number,
                ),
                TracePoint(
                    f"propose:R{number}",
                    f"服务端提案 R{number}",
                    kind="propose",
                    round=number,
                ),
                TracePoint(
                    f"decision:R{number}",
                    f"决策结果 R{number}",
                    kind="decision",
                    round=number,
                ),
            )
        )
    if agent and rounds > 0:
        points.append(
            TracePoint(
                "gen:client_decision_agent",
                "LLM 决策",
                stage="client_decision_agent",
                count=rounds,
            )
        )
        points.append(
            TracePoint(
                "tool",
                "决策工具调用",
                stage="client_decision_agent",
                count=rounds,
            )
        )
    return tuple(points)


def match_points(
    points: Sequence[TracePoint],
    observations: Sequence[Mapping[str, object]],
    *,
    run_id: str | None,
) -> tuple[PointResult, ...]:
    """Count how many observations satisfy each expected point."""
    return tuple(
        PointResult(point, _count_point(point, observations, run_id=run_id))
        for point in points
    )


def _count_point(
    point: TracePoint,
    observations: Sequence[Mapping[str, object]],
    *,
    run_id: str | None,
) -> int:
    matched = 0
    for observation in observations:
        if not _matches(point, observation, run_id=run_id):
            continue
        matched += 1
    return matched


def _matches(
    point: TracePoint,
    observation: Mapping[str, object],
    *,
    run_id: str | None,
) -> bool:
    kind = _text(observation.get("kind"))
    stage = _text(observation.get("stage"))
    observation_type = _text(observation.get("type")).upper()
    round_number = _int(observation.get("round"))
    if point.kind == "decision":
        if kind not in _DECISION_KINDS:
            return False
    elif point.kind and kind != point.kind:
        return False
    if point.kind == "message_sent" and run_id:
        observation_run = observation.get("run_id") or observation.get("runId")
        if _text(observation_run) != run_id:
            return False
    if point.interception is True and observation.get("interception") is not True:
        return False
    if point.interception is False and observation.get("interception") is True:
        return False
    if point.stage:
        if stage != point.stage or observation_type not in {"GENERATION", "TOOL", ""}:
            return False
    if point.id.startswith("gen:") and observation_type != "GENERATION":
        return False
    if point.id == "tool" and observation_type != "TOOL":
        return False
    if point.round is not None and round_number != point.round:
        return False
    if point.kind == "task_validation" and observation.get("interception") is True and point.interception is False:
        return False
    return True


def attribute_points(
    points: Sequence[PointResult],
    *,
    local_points: Sequence[str] | None,
    local_known: Sequence[str] | None = None,
) -> tuple[str, tuple[AttributionFinding, ...]]:
    """Attribute every missing point to ingest, client, or unknown.

    ``local_points`` is the set of point ids the local producer provably emitted;
    ``local_known`` is the subset the local evidence can adjudicate at all. A point outside
    ``local_known`` stays ``unknown`` — a partial local view must not turn into a client defect.
    ``None`` for either means the local layer is unavailable.
    """
    known = set(local_known) if local_known is not None else None
    findings: list[AttributionFinding] = []
    for result in points:
        if result.ok:
            continue
        if local_points is None:
            status = ATTRIBUTION_UNKNOWN
            detail = "本地发射层不可用，无法区分漏发与入库"
        elif result.point.id in local_points:
            status = ATTRIBUTION_MISSING_INGEST
            detail = "本地已发射，Langfuse 未入库"
        elif known is not None and result.point.id not in known:
            status = ATTRIBUTION_UNKNOWN
            detail = "本地证据无法裁决该点"
        else:
            status = ATTRIBUTION_MISSING_CLIENT
            detail = "本地未发射"
        findings.append(
            AttributionFinding(result.point.id, result.point.label, status, detail)
        )
    if any(finding.status == ATTRIBUTION_MISSING_INGEST for finding in findings):
        return ATTRIBUTION_MISSING_INGEST, tuple(findings)
    if any(finding.status == ATTRIBUTION_MISSING_CLIENT for finding in findings):
        return ATTRIBUTION_MISSING_CLIENT, tuple(findings)
    if findings:
        return ATTRIBUTION_UNKNOWN, tuple(findings)
    return ATTRIBUTION_OK, ()


def frame_gate(frames: Mapping[str, object]) -> tuple[str, str]:
    """Return the NMS frame-gate status and reason.

    The NMS hop is the only hard count: one request frame plus one per negotiation round. The
    client-hop count is corroboration, not a gate, because a pure A2A trace also contains the
    UI→OSS hop.
    """
    nms = frames.get("nms")
    if not isinstance(nms, Mapping):
        return TRACE_NOT_APPLICABLE, REASON_NMS_HOP_UNKNOWN
    expected = nms.get("expected")
    observed = nms.get("observed")
    if expected is None:
        return TRACE_NOT_APPLICABLE, REASON_NMS_HOP_UNKNOWN
    if observed is None or _int(observed) != _int(expected):
        return TRACE_FAIL, REASON_FRAMES_MISMATCH
    return TRACE_PASS, ""


def judge_trace(
    payload: Mapping[str, object],
    context: TraceContext,
    *,
    not_exported_status: str = TRACE_FAIL,
) -> TraceJudgement:
    """Judge one snapshot against the run facts.

    The order is the acceptance contract: N/A for non-real-NMS and no-run items, exported
    evidence required, then the attribution gaps, the client hop, the NMS hop, and finally the
    frame count. ``not_exported_status`` is injectable so the unresolved "missing evidence is a
    fail or blocked" decision stays a single line.
    """
    frames_value = payload.get("frames")
    frames: Mapping[str, object] = frames_value if isinstance(frames_value, Mapping) else {}
    observations_value = payload.get("observations")
    observations: Sequence[Mapping[str, object]] = (
        observations_value if isinstance(observations_value, Sequence) else ()
    )
    if context.protocol not in {PROTOCOL_A2A_T, PROTOCOL_A2A}:
        return TraceJudgement(TRACE_NOT_APPLICABLE, "", (), ATTRIBUTION_OK, (), frames)
    if not context.run_id:
        return TraceJudgement(TRACE_NOT_APPLICABLE, "", (), ATTRIBUTION_OK, (), frames)
    export_state = _text(payload.get("export_state")) or _text(payload.get("exportState"))
    if export_state in {"", "not_exported", "failed"}:
        return TraceJudgement(not_exported_status, REASON_NOT_EXPORTED, (), ATTRIBUTION_OK, (), frames)

    points = expected_client_points(context)
    results = match_points(points, observations, run_id=context.run_id)
    local_value = payload.get("local_points")
    if local_value is None:
        local_value = payload.get("localPoints")
    local_points = tuple(str(item) for item in local_value) if isinstance(local_value, (list, tuple)) else None
    known_value = payload.get("local_known")
    if known_value is None:
        known_value = payload.get("localKnown")
    local_known = tuple(str(item) for item in known_value) if isinstance(known_value, (list, tuple)) else None
    attribution, findings = attribute_points(
        results, local_points=local_points, local_known=local_known
    )
    if findings:
        # The customer gate is the presence of the expected points, not the attribution. A point
        # we cannot attribute (local emission is not persisted) still fails; the report shows the
        # attribution separately instead of hiding the gap behind an ``unknown``.
        return TraceJudgement(TRACE_FAIL, _reason_for(attribution, findings), results, attribution, findings, frames)

    client = frames.get("client")
    if (
        context.protocol == PROTOCOL_A2A_T
        and isinstance(client, Mapping)
        and _int(client.get("observed")) == 0
    ):
        return TraceJudgement(
            TRACE_FAIL, REASON_MISSING_CLIENT, results, attribution, findings, frames
        )

    gate_status, gate_reason = frame_gate(frames)
    if gate_status == TRACE_FAIL:
        return TraceJudgement(gate_status, gate_reason, results, attribution, findings, frames)
    if gate_status == TRACE_NOT_APPLICABLE:
        return TraceJudgement(gate_status, gate_reason, results, attribution, findings, frames)
    if payload.get("capture_content") is False and not findings:
        return TraceJudgement(
            TRACE_PARTIAL, REASON_CONTENT_UNAVAILABLE, results, attribution, findings, frames
        )
    return TraceJudgement(TRACE_PASS, "", results, attribution, findings, frames)


def _reason_for(attribution: str, findings: Sequence[AttributionFinding]) -> str:
    if attribution == ATTRIBUTION_CONTENT_UNAVAILABLE:
        return REASON_CONTENT_UNAVAILABLE
    if any(finding.status == ATTRIBUTION_MISSING_VENDOR for finding in findings):
        return REASON_MISSING_VENDOR
    if attribution in {ATTRIBUTION_MISSING_CLIENT, ATTRIBUTION_UNKNOWN}:
        return REASON_MISSING_CLIENT
    return REASON_MISSING_INGEST


def _text(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def _is_missing(value: object) -> bool:
    return value is None or (isinstance(value, str) and value.strip() in {"", "<missing>"})


def _int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().lstrip("-").isdigit():
        return int(value.strip())
    return None
