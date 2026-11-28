"""Batch service of the redesigned tester console.

A batch is a list of ``(case, protocol)`` items. Each item runs the SDK-owned stages
(``intent`` / ``task_t`` / ``validate``) in-process, then the protocol runtime stages
(``send`` / ``negotiate`` / ``final``) through the shared :class:`RunManager`. The case YAML is the
source of truth; the Excel workbook is no longer an input.

One live client agent, with the same frozen principles and case conditions, authors every
negotiation answer on both protocols; the Q and P columns become reference text (P is exported)
and the server dialogue stays the oracle.

A Modify A2A-T item first seeds a baseline Create task, then runs the Modify request against it.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import time
import uuid
from collections import deque
from collections.abc import Iterable, Mapping, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

from eval.osp.catalog import PROTOCOL_A2A, PROTOCOL_A2A_T, CaseCatalog
from eval.osp.client_quality import ClientQualityObservation, judge_client_quality
from eval.osp.compare import (
    compare_intent,
    compare_task_t,
    flatten_intent,
    flatten_task_t,
)
from eval.osp.conditions import case_client_conditions
from eval.osp.expectations import (
    match_negotiation_messages,
    negotiation_message_expectation,
    slot_error_evidence,
)
from eval.osp.followup import (
    followup_allowed,
    followup_case,
    followup_input,
    has_followup,
    is_blocked_case,
    original_dispatch_case,
)
from eval.osp.intent_judge import STATUS_MISMATCH, STATUS_PASS, judge_intent
from eval.osp.principles import NegotiationPrinciples, load_principles
from eval.osp.runner import (
    STAGE_ERROR,
    STAGE_FAILED,
    STAGE_PASSED,
    STAGE_RECORDED,
    CaseOutcome,
    build_client_with_env,
    run_case,
)
from eval.osp.runner import (
    STAGE_ORDER as SDK_STAGE_ORDER,
)
from langfuse_export.config import Config as LangfuseConfig
from protocol.client_runtime import MODE_AUTO
from protocol.client_service import STATUS_FAILED, RunManager, RunState
from protocol.run_metrics import build_run_metrics
from shared.config import DISCOVERY_NMS, MODEL_MODE_LIVE
from shared.run_constants import (
    FINAL_STATE_DECISION_UNGROUNDED,
    FINAL_STATE_INCOMPLETE,
    FINAL_STATE_INPUT_INVALID,
    FINAL_STATE_INTENT_NOT_CONFIRMED,
    FINAL_STATE_TASK_FAILED,
    FINAL_STATE_TASK_REJECTED,
    LANGUAGE_ZH,
    PROTOCOL_PURE,
    SUPPORTED_LANGUAGES,
    TASK_STATE_SUBMITTED,
)
from shared.run_constants import (
    PROTOCOL_A2A_T as RUNTIME_A2A_T,
)
from shared.trace import reset_task_sink, set_task_sink

from demo.web.tester.decisions import pure_answers
from demo.web.tester.export_view import build_answer, read_traceparent, verdict_reason
from demo.web.tester.langfuse_columns import build_trace_columns
from demo.web.tester.messages import TesterMessages

_LOGGER = logging.getLogger("ran_energy_saving.tester")

#: Delay after a run finishes before its Langfuse columns are prefetched (OTel ingestion lag).
#: Longer than ``TRACE_SETTLE_SECONDS``, so the first prefetch is normally already settled.
TRACE_PREFETCH_DELAY_SECONDS: Final[float] = 20.0
#: A fetch taken earlier than this after the run finished may miss late spans: it is cached but
#: marked unsettled, so the next export refetches it.
TRACE_SETTLE_SECONDS: Final[float] = 15.0
#: Concurrent background Langfuse prefetches of one tester process.
TRACE_PREFETCH_WORKERS: Final[int] = 2

#: Every stage the console can run, in execution order.
ALL_STAGES: Final[tuple[str, ...]] = (
    "intent",
    "task_t",
    "validate",
    "send",
    "negotiate",
    "accepted",
    "working",
    "final",
)

#: Stage status used by ``working`` when the Task never left ``SUBMITTED``: yellow, not red.
STAGE_WAITING: Final[str] = "waiting"
#: Stage status for a deviation the client did not cause: the case is not contradicted, but the
#: observation is not the recorded one either, so a human must confirm it.
STAGE_WARNING: Final[str] = "warning"
#: Stage status used when the case expects no send, so the stage cannot be judged.
STAGE_SKIPPED: Final[str] = "skipped"

#: Batch / item states.
BATCH_DRAFT: Final[str] = "draft"
BATCH_RUNNING: Final[str] = "running"
BATCH_FINISHED: Final[str] = "finished"
BATCH_CANCELLED: Final[str] = "cancelled"

CASE_PENDING: Final[str] = "pending"
CASE_RUNNING: Final[str] = "running"
CASE_PASSED: Final[str] = "passed"
CASE_FAILED: Final[str] = "failed"
CASE_CANCELLED: Final[str] = "cancelled"
CASE_ERROR: Final[str] = "error"
#: At least one stage is ``waiting`` but none failed: the request is not proven either way.
CASE_PARTIAL: Final[str] = "partial"

#: How many items one batch runs at the same time unless the operator asks for more. One keeps
#: the historical sequential behaviour, including its single ``current_index``.
DEFAULT_CONCURRENCY: Final[int] = 1

#: Hard ceiling of a batch's concurrency. The process does not model an environment's real
#: capacity; the operator raises the value when they hold an exclusive environment and lowers it
#: again when the NMS reports a task-quota or conflict error.
MAX_CONCURRENCY: Final[int] = 32

#: How many times the whole batch repeats unless the operator asks for more. One runs the batch
#: exactly once, which is the historical behaviour.
DEFAULT_ROUNDS: Final[int] = 1

#: Hard ceiling of a batch's rounds. Each round is an independent pass (new runs, new Tasks), so
#: the value is a deliberate operator choice, not an environment property.
MAX_ROUNDS: Final[int] = 8


#: Persisted batch schema; v1-v4 batches are still readable and load with the live defaults.
SCHEMA: Final[str] = "osp-tester/v5"
SCHEMA_V1: Final[str] = "osp-tester/v1"
SCHEMA_V2: Final[str] = "osp-tester/v2"
SCHEMA_V3: Final[str] = "osp-tester/v3"
SCHEMA_V4: Final[str] = "osp-tester/v4"
_SCHEMAS: Final[frozenset[str]] = frozenset({SCHEMA, SCHEMA_V4, SCHEMA_V3, SCHEMA_V2, SCHEMA_V1})

#: Task states that end the item in red.
_FAILED_TASK_STATES: Final[frozenset[str]] = frozenset(
    {"TASK_STATE_FAILED", "TASK_STATE_REJECTED", "TASK_STATE_CANCELED"}
)
#: The A2A state a pure outer Task reports once the OSS Agent accepted the Create. It is also the
#: only non-terminal state a pure Modify can continue: ``COMPLETED`` rejects the continuation and
#: ``INPUT_REQUIRED`` would route the Modify text as an answer to the pending question.
TASK_STATE_WORKING: Final[str] = "TASK_STATE_WORKING"
#: Task states that prove the Task moved past SUBMITTED.
_WORKING_TASK_STATES: Final[frozenset[str]] = frozenset(
    {TASK_STATE_WORKING, "TASK_STATE_COMPLETED"}
)
#: Task states that prove the downstream accepted the Create/Modify request. A2A-T reports
#: :data:`TASK_STATE_SUBMITTED` (the machine-readable success signal); pure A2A starts its outer
#: Task at ``TASK_STATE_WORKING``. ``INPUT_REQUIRED`` is not acceptance: the negotiation is still
#: in progress, so the item keeps observing.
_ACCEPTED_TASK_STATES: Final[frozenset[str]] = frozenset(
    {TASK_STATE_SUBMITTED}
) | _WORKING_TASK_STATES

#: Client outcomes that prove the request ended before any Task was sent.
_PRE_SEND_TERMINAL_STATES: Final[frozenset[str]] = frozenset(
    {
        FINAL_STATE_INPUT_INVALID,
        FINAL_STATE_DECISION_UNGROUNDED,
        FINAL_STATE_INTENT_NOT_CONFIRMED,
    }
)

#: Prefix of the reason the client flow records when the vendor answered with an ``INPUT_REQUIRED``
#: that carried no question. ``protocol.pure_client_flow`` owns the full sentence; the marker is
#: kept narrow so no client-side reason can match it by accident. It stays Chinese on purpose:
#: detection works on the raw protocol error, which is evidence and is never translated (D10).
_VENDOR_DEFECT_MARKER: Final[str] = "NMS 侧 bug"

#: Scenario used to seed a Modify target task; it is a complete request that creates a task.
BASELINE_SCENARIO: Final[str] = "night"

#: Region placeholder of the baseline request; ``_baseline_request`` replaces it with the region
#: the configured NMS actually serves.
BASELINE_REGION: Final[str] = "A2ATestRegion"

#: One-day rate plan of the shared fallback baseline: the Task-T the testers use as the manual
#: baseline (``00:00-07:00 2Mbps``, ``07:00-18:00 10Mbps``, ``18:00-24:00 20Mbps``). It is a
#: complete Create the vendor accepts without a pre-task negotiation, so baselines reuse it instead
#: of inventing a per-site plan. The plan is written in prose rather than an explicit
#: ``{HH:MM-HH:MM,NMbps}`` list. Every Modify case ships its own
#: ``baseline.request`` (see eval/osp/README.md 3.5); this plan is the fallback.
BASELINE_SCHEDULE: Final[str] = (
    "guarantee 2Mbps from 00:00 to 07:00, 10Mbps from 07:00 to 18:00 "
    "and 20Mbps from 18:00 to 24:00"
)

#: Fallback baseline request used when a Modify case declares no ``baseline.request``.
BASELINE_REQUEST: Final[str] = (
    f"Create an energy saving task for {BASELINE_REGION}: {BASELINE_SCHEDULE}."
)

#: Polling interval of the batch runner.
_POLL_SECONDS: Final[float] = 0.2

#: Default observation window of the ``working`` stage when the batch does not override it.
DEFAULT_WORKING_TIMEOUT_SECONDS: Final[float] = 60.0

#: Grace after a stop-after stage before the run is cancelled.
_CANCEL_GRACE_SECONDS: Final[float] = 1.0



def _now_iso() -> str:
    """Return the current UTC time in ISO 8601 form."""
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


@dataclass
class StageResult:
    """Result of one stage of one item."""

    stage: str
    status: str
    message: str = ""
    expected: object = None
    actual: object = None
    diffs: list[dict[str, object]] = field(default_factory=list)
    duration_ms: int = 0
    #: Structured evidence of the stage (for a validation, the SDK code and per-slot errors).
    detail: dict[str, object] | None = None

    def to_dict(self) -> dict[str, object]:
        """Return the JSON-friendly representation.

        A ``task_t`` stage also carries its two prompts split into ``## Heading`` sections, so the
        tester can show one comparison row per section instead of two whole prompts.
        """
        data: dict[str, object] = {
            "stage": self.stage,
            "status": self.status,
            "message": self.message,
            "expected": self.expected,
            "actual": self.actual,
            "diffs": self.diffs,
            "durationMs": self.duration_ms,
        }
        if self.detail:
            data["detail"] = self.detail
        if (
            self.stage == "task_t"
            and isinstance(self.expected, str)
            and isinstance(self.actual, str)
        ):
            data["sections"] = [
                row.to_dict() for row in flatten_task_t(self.expected, self.actual)
            ]
        return data


@dataclass
class CaseItem:
    """One runnable ``(case, protocol)`` pair."""

    case_id: int
    name: str
    protocol: str
    #: One-based repetition of the batch this item belongs to (``1`` for a single-round batch).
    round_index: int = 1
    status: str = CASE_PENDING
    stages: dict[str, StageResult] = field(default_factory=dict)
    run_id: str | None = None
    duration_ms: int | None = None
    tokens: dict[str, object] = field(default_factory=dict)
    error: str | None = None
    baseline: dict[str, object] | None = None
    scratch: str | None = None
    #: Previous executions of this item (a rerun appends before it resets the row).
    attempts: list[dict[str, object]] = field(default_factory=list)
    #: Vendor-facing trace id of the run (``run.trace_id``, 32 lowercase hex digits).
    trace_id: str = ""
    #: Clickable Langfuse trace URL of the run, empty when observability is off or unresolved.
    langfuse_url: str = ""
    #: Why Langfuse rejected the run's export (project suspended, bad key), empty when healthy.
    langfuse_warning: str = ""
    #: OTel/Langfuse trace id used to fetch this run's observations (``run.langfuse["traceId"]``),
    #: distinct from the vendor-facing :attr:`trace_id` in the first report column.
    langfuse_trace_id: str = ""
    #: OSP 11-key structured intent extracted by the tester's SDK ``intent`` stage.
    intent: dict[str, object] | None = None
    #: Structured Task payload the downstream returned with ``TASK_STATE_SUBMITTED``: the
    #: downstream's own reading of the Task-T, and the oracle the arbitration sheet compares with.
    server_intent: dict[str, object] | None = None
    #: Raw/normalized operator request and how the intent was produced.
    intent_meta: dict[str, object] = field(default_factory=dict)
    #: Case metadata and expectations frozen when the item was collected.
    refs: dict[str, object] = field(default_factory=dict)
    #: Reference-workbook answer/origin object (columns 76 and 87).
    answer: dict[str, object] | None = None
    #: The operator's original request this item's run executes. A blocked case's replacement is a
    #: correction inside the same run, so it never replaces this text.
    run_request: str = ""
    #: The interception the case expects before its follow-up request (``blocked_followup``).
    interception: dict[str, object] | None = None
    #: Set by an operator cancelling this item alone. Transient: never persisted, and reset by a
    #: rerun. The item's outcome is the persisted ``status`` once it unwinds.
    cancel_requested: bool = False
    #: Decision-quality appendix of an agent-authored negotiation (either protocol; P2). It never
    #: changes ``status``: the NMS acceptance verdict stays authoritative.
    client_quality: dict[str, object] | None = None
    #: Final-intent verdict (P3): structured exact/normalized, or the model judge's reading of a
    #: residual. Annotation only — the ``intent`` stage status stays the dimension verdict.
    intent_verdict: dict[str, object] | None = None
    #: A residual intent that no judge could decide is not green: it needs human review and makes
    #: the item ``partial`` instead of ``passed``.
    needs_review: bool = False

    def intent_comparison(self) -> list[dict[str, object]]:
        """Return the flattened expected-vs-server intent rows, empty when one side is absent."""
        expected = self.refs.get("expectedIntent")
        if not isinstance(expected, Mapping) or not isinstance(self.server_intent, Mapping):
            return []
        return [row.to_dict() for row in flatten_intent(expected, self.server_intent)]

    def to_dict(self, messages: TesterMessages | None = None) -> dict[str, object]:
        """Return the JSON-friendly representation.

        ``messages`` is the batch's message set: the verdict reason is rendered in the language the
        batch was created in, not the process language (D9).
        """
        item: dict[str, object] = {
            "caseId": self.case_id,
            "name": self.name,
            "protocol": self.protocol,
            "round": self.round_index,
            "status": self.status,
            "stages": {stage: result.to_dict() for stage, result in self.stages.items()},
            "runId": self.run_id,
            "durationMs": self.duration_ms,
            "tokens": dict(self.tokens),
            "error": self.error,
            "baseline": self.baseline,
            "scratch": self.scratch,
            "attempts": [dict(attempt) for attempt in self.attempts],
            "traceId": self.trace_id,
            "langfuseUrl": self.langfuse_url,
            "langfuseWarning": self.langfuse_warning,
            "langfuseTraceId": self.langfuse_trace_id,
            "intent": self.intent,
            "serverIntent": self.server_intent,
            "intentCompare": self.intent_comparison(),
            "intentMeta": dict(self.intent_meta),
            "refs": dict(self.refs),
            "answer": self.answer,
            "interception": self.interception,
            "clientQuality": self.client_quality,
            "intentVerdict": self.intent_verdict,
            "needsReview": self.needs_review,
        }
        item["verdictReason"] = verdict_reason(item, messages=messages)
        return item


@dataclass
class _Batch:
    """Mutable state of one tester batch."""

    batch_id: str
    case_ids: list[int]
    protocols: list[str]
    stop_after: str
    timeout_seconds: float | None
    items: list[CaseItem]
    #: Cases dropped because a paired batch cannot compare them (a blocked case sends a different
    #: request on each protocol side). They are reported instead of silently disappearing.
    excluded_case_ids: list[int] = field(default_factory=list)
    working_timeout_seconds: float | None = None
    #: Shared negotiation principles frozen when the batch is created (P1 fills these).
    principles_version: str = ""
    principles_text: str = ""
    #: Operator-entered NMS version label; report-only, never used for judging.
    nms_version: str = ""
    #: Keep every Task this batch created alive after its item ends. Off by default: a finished
    #: tester item releases its remote Task so a long acceptance run does not exhaust the NMS quota.
    keep_tasks: bool = False
    #: How many items may run at once (``1`` = the historical sequential loop).
    concurrency: int = DEFAULT_CONCURRENCY
    #: How many times the whole item set repeats (``1`` = the historical single pass).
    rounds: int = DEFAULT_ROUNDS
    #: The round currently executing, kept for the progress line (``1`` before/after a run).
    current_round: int = 1
    #: Concurrency the currently executing items actually use. A single-item rerun forces ``1``
    #: even on a batch configured for more, so the SDK stage keeps its exclusive client.
    active_concurrency: int = 1
    transport: str = "sse"
    discovery: str = "direct"
    #: Process language the batch was created in. Its messages and export stay in this language
    #: even when the batch is opened after the process restarted with another language (D9).
    language: str = LANGUAGE_ZH
    owner_id: str = ""
    #: HTTP origin of the browser that created the batch: anonymous cookie, source address and
    #: user agent. The status page shows it to answer "whose run is holding the quota".
    owner_source: dict[str, object] = field(default_factory=dict)
    status: str = BATCH_DRAFT
    created_at: str = field(default_factory=_now_iso)
    started_at: str | None = None
    finished_at: str | None = None
    current_index: int | None = None
    #: Items executing right now, in start order. Maintained by both execution paths so
    #: cancellation has one implementation and the UI can show every running row. The main pass
    #: and the rerun queue both contribute to it.
    active_indices: list[int] = field(default_factory=list)
    #: Item indices waiting to be re-executed. A rerun may be queued while the main pass still
    #: runs; it shares the batch's slot pool, so it starts at the next free slot.
    rerun_queue: deque[int] = field(default_factory=deque)
    #: Indices whose previous remote Task is being released before they join ``rerun_queue``;
    #: keeps a double click from queueing the same item twice.
    rerun_pending: set[int] = field(default_factory=set)
    #: True while the rerun drainer owns work.
    rerun_active: bool = False
    #: True while the main pass (the selected item list) is executing.
    main_active: bool = False
    #: Per-batch slot pool shared by the main pass and the rerun queue (size = concurrency).
    slots: asyncio.Semaphore | None = field(default=None, repr=False)
    cancel_requested: bool = False
    error: str | None = None
    task: asyncio.Task[None] | None = field(default=None, repr=False)
    #: The rerun drainer task; separate from ``task`` so both lanes can coexist.
    rerun_task: asyncio.Task[None] | None = field(default=None, repr=False)


def _stops_on_a_vendor_defect(error: str | None) -> bool:
    """Return whether one run error says the vendor broke the exchange."""
    return isinstance(error, str) and error.lstrip().startswith(_VENDOR_DEFECT_MARKER)


def _final_state_unpushed(run: RunState, actual_final: object) -> bool:
    """Return whether the only deviation is a terminal state the vendor never pushed."""
    if actual_final != FINAL_STATE_INCOMPLETE:
        return False
    if run.acceptance_copy is None or run.latest_task_state not in _WORKING_TASK_STATES:
        return False
    final_error = run.result.error if run.result is not None else None
    return not final_error


class EvalBatchManager:
    """Own the tester batches of one Web UI process."""

    def __init__(
        self,
        *,
        runs: RunManager,
        catalog: CaseCatalog,
        protocol_defaults: Mapping[str, Mapping[str, str]],
        server_url: str | None,
        server_auth_token: str | None,
        runs_dir: Path | None = None,
        nms: bool = False,
        language: str = "",
        model_mode: str = "",
        model_label: str = "",
        metrics_grace_seconds: float = 0.0,
        prompt_client: Any | None = None,
        env_path: Path | None = None,
        judge: Any | None = None,
        judge_model_label: str = "",
        pure_hop: str = "",
        trace_config: LangfuseConfig | None = None,
    ) -> None:
        self._runs = runs
        self._catalog = catalog
        self._protocol_defaults = protocol_defaults
        self._server_url = (server_url or "").rstrip("/")
        self._server_auth_token = server_auth_token
        self._runs_dir = Path(runs_dir) if runs_dir is not None else None
        self._history_dir = (Path(runs_dir) / "tester") if runs_dir is not None else None
        #: Langfuse credentials used to warm the per-run metrics cache after a run finishes.
        self._trace_config = trace_config
        self._trace_prefetch_tasks: set[asyncio.Task[None]] = set()
        self._trace_prefetch_slots = asyncio.Semaphore(TRACE_PREFETCH_WORKERS)
        self._nms = nms
        self._language = language
        #: Messages of new batches (the process language). Existing batches render through the
        #: message set of the language they were created in.
        self._messages = TesterMessages(language)
        self._model_mode = model_mode
        self._model_label = model_label
        self._metrics_grace_seconds = max(0.0, metrics_grace_seconds)
        self._prompt_client = prompt_client
        #: The UI's source ``.env``. The SDK clients of the SDK stages must be built from the same
        #: file the run runtime reads, or the preflight and the run can resolve different models
        #: and disagree on the same request (2026-11-17 incident: deepseek vs qwen3.8-27b).
        self._env_path = Path(env_path) if env_path is not None else None
        #: Independent judge of the decision-quality appendix (P2); ``None`` leaves it unjudged.
        self._judge = judge
        self._judge_model_label = judge_model_label
        #: Deployment hop of a standard-A2A run. ``oss`` is the deprecated proxy: it cannot host
        #: an agent-authored reply, because that answer would measure the proxy, not the client.
        self._pure_hop = pure_hop
        #: One SDK client per model mode; a client is reused only within its own model mode.
        self._prompt_clients: dict[str, Any] = {}
        #: Idle clients per model mode, used only by the concurrent path. Each pool slot owns one
        #: client because ``A2ATClient`` is not documented as thread-safe.
        self._prompt_pool: dict[str, asyncio.Queue[Any]] = {}
        #: Serializes pool growth: ``build_client`` registers the traced providers with a
        #: check-then-register dance, so building two clients at once would raise.
        self._prompt_build_lock = asyncio.Lock()
        #: Temporary SDK ``.env`` directories owned by pooled clients, removed on shutdown.
        self._prompt_env_dirs: list[Path] = []
        self._prompt_errors: dict[str, str] = {}
        self._baseline_request = _baseline_request()
        self._sdk_lock = asyncio.Lock()
        #: Bounds the ``run_case`` threads across every concurrent batch of the process.
        self._sdk_slots = asyncio.Semaphore(MAX_CONCURRENCY)
        #: SDK-stage threads holding a pool slot right now. ``Semaphore`` exposes no public
        #: count, and the status page needs to say how much of the process-wide pool is in use.
        self._sdk_inflight = 0
        self._batches: dict[str, _Batch] = {}
        #: Wall-clock start of this manager (one per UI process); the status page shows uptime.
        self._started_at = _now_iso()

    # ------------------------------------------------------------------
    # Catalog and batch lifecycle
    # ------------------------------------------------------------------

    @property
    def language(self) -> str:
        """Return the process language new batches are created in."""
        return self._messages.language

    def _batch_messages(self, batch: _Batch) -> TesterMessages:
        """Return the message set a batch was created in."""
        return TesterMessages(batch.language)

    def catalog(self) -> list[dict[str, object]]:
        """Return every catalog row."""
        return [summary.to_dict() for summary in self._catalog.summaries()]

    def create(
        self,
        *,
        case_ids: Sequence[int],
        protocols: Sequence[str],
        stop_after: str,
        timeout_seconds: float | None = None,
        working_timeout_seconds: float | None = None,
        principles_version: str | None = None,
        nms_version: str | None = None,
        keep_tasks: bool = False,
        concurrency: int = DEFAULT_CONCURRENCY,
        rounds: int = DEFAULT_ROUNDS,
        owner: str = "",
        owner_source: Mapping[str, object] | None = None,
    ) -> dict[str, object]:
        """Register one draft batch from the selected cases and protocols.

        ``timeout_seconds``/``working_timeout_seconds`` may be ``None``: the batch then uses each
        case's own ``runner`` block (or the process defaults for a scratch request).
        ``keep_tasks`` keeps every Task alive after its item ends (long-task verification); by
        default each item releases its remote Task once its evidence is collected.
        ``concurrency`` is how many items run at once (1..:data:`MAX_CONCURRENCY`); ``1`` is the
        sequential behaviour every existing batch has.
        ``rounds`` is how many independent passes the whole item set makes
        (1..:data:`MAX_ROUNDS`); ``1`` is the historical single pass. A multi-round batch cannot
        keep its Tasks: every round would multiply the Tasks left behind.
        """
        if stop_after not in ALL_STAGES:
            raise ValueError(f"unknown stop_after {stop_after!r}")
        resolved_concurrency = validate_concurrency(concurrency, self._messages)
        resolved_rounds = validate_rounds(rounds, self._messages)
        if resolved_rounds > DEFAULT_ROUNDS and keep_tasks:
            raise ValueError(self._messages.get("multi_round_keep_tasks"))
        # The shared principles are frozen with the batch: a later edit of the asset must not
        # change what this batch's agent was told when it decided.
        principles = self._load_principles(principles_version)
        selected_protocols = [protocol for protocol in protocols if protocol in {PROTOCOL_A2A_T, PROTOCOL_A2A}]
        if not selected_protocols:
            raise ValueError(self._messages.get("select_protocol"))
        if PROTOCOL_A2A in selected_protocols and stop_after in SDK_STAGE_ORDER:
            raise ValueError(self._messages.get("pure_no_sdk_stage"))
        items: list[CaseItem] = []
        # Round-major expansion: the first round keeps the exact order a single-pass batch has.
        for round_index in range(1, resolved_rounds + 1):
            for case_id in case_ids:
                case = self._catalog.get(case_id)
                if case is None:
                    continue
                # Every selected case runs under every selected protocol, interception cases
                # included: the console must be able to run whatever the operator selected. A
                # paired report can still tell the two rows apart by the case's own
                # ``validate: blocked`` marker.
                for protocol in selected_protocols:
                    items.append(
                        CaseItem(
                            case_id=case_id,
                            name=str(case["name"]),
                            protocol=protocol,
                            round_index=round_index,
                        )
                    )
        if not items:
            raise ValueError(self._messages.get("no_runnable_cases"))
        defaults = self._protocol_defaults.get(PROTOCOL_A2A_T, {})
        batch = _Batch(
            batch_id=f"batch-{uuid.uuid4().hex[:12]}",
            case_ids=list(case_ids),
            protocols=selected_protocols,
            stop_after=stop_after,
            timeout_seconds=_positive_or_none(timeout_seconds),
            working_timeout_seconds=_positive_or_none(working_timeout_seconds),
            principles_version=principles.version,
            principles_text=principles.text,
            nms_version=(nms_version or "").strip(),
            keep_tasks=keep_tasks,
            concurrency=resolved_concurrency,
            rounds=resolved_rounds,
            transport=defaults.get("transport", "sse"),
            discovery=defaults.get("discovery", "direct"),
            language=self._messages.language,
            items=items,
            # Kept for the persisted-batch schema and the pair report; nothing is excluded now.
            excluded_case_ids=[],
            owner_id=owner,
            owner_source=_mapping_or_empty(owner_source),
        )
        self._batches[batch.batch_id] = batch
        self._persist(batch)
        return self._snapshot(batch)

    def create_scratch(
        self,
        *,
        user_input: str,
        protocol: str,
        timeout_seconds: float | None = 300.0,
        keep_tasks: bool = False,
        owner: str = "",
        owner_source: Mapping[str, object] | None = None,
    ) -> dict[str, object]:
        """Create a one-item batch for an ad-hoc request (full flow only)."""
        text = user_input.strip()
        if not text:
            raise ValueError(self._messages.get("scratch_empty"))
        if protocol not in {PROTOCOL_A2A_T, PROTOCOL_A2A}:
            raise ValueError(f"unknown protocol {protocol!r}")
        principles = self._load_principles(None)
        defaults = self._protocol_defaults.get(_runtime_protocol(protocol), {})
        item = CaseItem(
            case_id=0,
            name=self._messages.get("scratch_item_name"),
            protocol=protocol,
            scratch=text,
        )
        batch = _Batch(
            batch_id=f"batch-{uuid.uuid4().hex[:12]}",
            case_ids=[0],
            protocols=[protocol],
            stop_after="accepted",
            timeout_seconds=_positive_or_none(timeout_seconds) or 300.0,
            principles_version=principles.version,
            principles_text=principles.text,
            keep_tasks=keep_tasks,
            transport=defaults.get("transport", "sse"),
            discovery=defaults.get("discovery", "direct"),
            language=self._messages.language,
            items=[item],
            owner_id=owner,
            owner_source=_mapping_or_empty(owner_source),
        )
        self._batches[batch.batch_id] = batch
        self._persist(batch)
        return self._snapshot(batch)

    def _load_principles(self, version: str | None) -> NegotiationPrinciples:
        """Load the shared principles frozen for one batch."""
        try:
            return load_principles(version)
        except (FileNotFoundError, ValueError) as error:
            raise ValueError(self._messages.get("principles_load_failed", error=error)) from error

    def get(self, batch_id: str, *, owner: str = "") -> dict[str, object] | None:
        """Return one batch snapshot, or ``None`` when unknown or owned by someone else."""
        batch = self._owned(batch_id, owner)
        return None if batch is None else self._snapshot(batch)

    async def start(self, batch_id: str, *, owner: str = "") -> dict[str, object]:
        """Start one draft batch."""
        batch = self._owned(batch_id, owner)
        if batch is None:
            raise KeyError(batch_id)
        if batch.status != BATCH_DRAFT:
            raise ValueError(self._batch_messages(batch).get("batch_already_run"))
        batch.status = BATCH_RUNNING
        batch.started_at = _now_iso()
        batch.main_active = True
        self._ensure_slots(batch)
        self._persist(batch)
        batch.task = asyncio.create_task(self._run_batch(batch), name=f"tester-{batch_id}")
        await asyncio.sleep(0)
        return self._snapshot(batch)

    async def cancel(self, batch_id: str, *, owner: str | None = None) -> bool:
        """Cancel one running batch and every run its items still own.

        ``owner=None`` is the internal shutdown path; HTTP callers pass their browser identity.
        """
        batch = self._owned(batch_id, owner)
        if batch is None or batch.status != BATCH_RUNNING:
            return False
        batch.cancel_requested = True
        # A queued rerun has not started yet; dropping it is enough. Its previous result stays.
        batch.rerun_queue.clear()
        # Snapshot the active items: workers append and remove entries while the cancellations
        # below await, so iterating the live list would skip items.
        active = [
            batch.items[index]
            for index in list(batch.active_indices)
            if 0 <= index < len(batch.items)
        ]
        await asyncio.gather(*(self._cancel_item_runs(item) for item in active))
        for task in (batch.task, batch.rerun_task):
            if task is not None and not task.done():
                task.cancel()
        self._persist(batch)
        return True

    async def cancel_item(
        self, batch_id: str, index: int, *, owner: str = ""
    ) -> dict[str, object]:
        """Cancel one item of a running batch without touching the others.

        A queued item is marked cancelled and skipped when a worker reaches it; a running item
        has its own run (and a Modify's baseline run) cancelled, and unwinds to ``cancelled``.
        The batch itself keeps running and still finishes when the remaining items are done.
        """
        batch = self._owned(batch_id, owner)
        if batch is None:
            raise KeyError(batch_id)
        if index < 0 or index >= len(batch.items):
            raise IndexError(index)
        messages = self._batch_messages(batch)
        if batch.status != BATCH_RUNNING:
            raise ValueError(messages.get("batch_not_running"))
        item = batch.items[index]
        if index in batch.rerun_queue:
            # A queued rerun has not started; removing it leaves the previous result intact.
            batch.rerun_queue.remove(index)
            item.cancel_requested = False
            self._persist(batch)
            return {"cancelled": True, "batchId": batch.batch_id, "index": index, "queued": True}
        if item.status not in {CASE_PENDING, CASE_RUNNING}:
            raise ValueError(messages.get("item_already_finished"))
        item.cancel_requested = True
        if item.status == CASE_PENDING:
            # A worker checks this flag before it starts the item, so marking the row is enough.
            _mark_item_cancelled(item, messages)
        else:
            await self._cancel_item_runs(item)
        self._persist(batch)
        return {"cancelled": True, "batchId": batch.batch_id, "index": index}

    async def _cancel_item_runs(self, item: CaseItem) -> None:
        """Cancel the run one item owns and, for a Modify, the baseline run it seeded."""
        if item.run_id:
            await self._runs.cancel(item.run_id)
        baseline = _baseline_run_id(item)
        if baseline is not None and baseline != item.run_id:
            await self._runs.cancel(baseline)
            if isinstance(item.baseline, dict) and item.baseline.get("status") == "running":
                # The evidence must not claim an accepted baseline that was cancelled.
                item.baseline["status"] = "cancelled"

    async def shutdown(self) -> None:
        """Cancel every running batch and release the Tasks its items still own."""
        prefetches = [task for task in self._trace_prefetch_tasks if not task.done()]
        for task in prefetches:
            task.cancel()
        if prefetches:
            await asyncio.gather(*prefetches, return_exceptions=True)
        for batch_id, batch in list(self._batches.items()):
            if batch.status == BATCH_RUNNING:
                await self.cancel(batch_id)
        # Let every cancelled batch coroutine unwind before the pool and its temp dirs go away.
        pending = [
            task
            for batch in self._batches.values()
            for task in (batch.task, batch.rerun_task)
            if task is not None and not task.done()
        ]
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        for batch in list(self._batches.values()):
            if batch.keep_tasks:
                continue
            for item in batch.items:
                await self._release_item_runs(
                    item, reason="The tester shut down and released the run."
                )
        self._close_prompt_pool()

    def _owned(self, batch_id: str, owner: str | None) -> _Batch | None:
        """Return one batch, or ``None`` when it belongs to a different browser.

        ``owner=None`` bypasses the check for in-process callers such as ``shutdown``.
        """
        batch = self._batches.get(batch_id) or self._load(batch_id)
        if batch is None:
            return None
        if owner is not None and batch.owner_id != owner:
            return None
        return batch

    async def retry(self, batch_id: str, *, owner: str = "") -> dict[str, object]:
        """Create a new draft batch from the failed, errored or cancelled items.

        The previous attempt may still hold a remote Task (a failed item keeps tracking it);
        retrying releases those Tasks first so both attempts never occupy quota at once.
        """
        batch = self._owned(batch_id, owner)
        if batch is None:
            raise KeyError(batch_id)
        messages = self._batch_messages(batch)
        if batch.status == BATCH_RUNNING:
            # A rerun queue may still be running or the main pass is not done; retrying the whole
            # set now would create a duplicate batch and double the quota it consumes.
            raise ValueError(messages.get("batch_still_running"))
        selected = [
            item
            for item in batch.items
            if item.status in {CASE_FAILED, CASE_ERROR, CASE_CANCELLED}
        ]
        if not selected:
            raise ValueError(messages.get("no_failed_items"))
        for item in selected:
            await self._release_item_runs(
                item, reason="The tester retried this case and released the previous run."
            )
        # The new batch keeps the original round count: a failed case is retried in every round,
        # so the operator can see whether the failure repeats or was a single-pass flake.
        unique: list[CaseItem] = []
        seen: set[tuple[int, str, str | None]] = set()
        for item in selected:
            key = (item.case_id, item.protocol, item.scratch)
            if key in seen:
                continue
            seen.add(key)
            unique.append(item)
        retried = _Batch(
            batch_id=f"batch-{uuid.uuid4().hex[:12]}",
            case_ids=batch.case_ids,
            protocols=batch.protocols,
            stop_after=batch.stop_after,
            timeout_seconds=batch.timeout_seconds,
            working_timeout_seconds=batch.working_timeout_seconds,
            keep_tasks=batch.keep_tasks,
            concurrency=batch.concurrency,
            rounds=batch.rounds,
            transport=batch.transport,
            discovery=batch.discovery,
            # A retry is a new batch and records the process language, not the old batch's.
            language=self._messages.language,
            items=[
                CaseItem(
                    case_id=item.case_id,
                    name=item.name,
                    protocol=item.protocol,
                    scratch=item.scratch,
                    round_index=round_index,
                )
                for round_index in range(1, batch.rounds + 1)
                for item in unique
            ],
            owner_id=batch.owner_id,
        )
        self._batches[retried.batch_id] = retried
        self._persist(retried)
        return self._snapshot(retried)

    def list_batches(self, *, owner: str = "", limit: int = 20) -> list[dict[str, object]]:
        """Return the most recent batches of one browser identity, newest first."""
        if self._history_dir is None or not self._history_dir.is_dir():
            return [
                self._snapshot(batch)
                for batch in sorted(
                    self._batches.values(), key=lambda item: item.created_at, reverse=True
                )
                if batch.owner_id == owner
            ][:limit]
        summaries: list[dict[str, object]] = []
        for path in sorted(
            self._history_dir.glob("*.json"),
            key=lambda candidate: candidate.stat().st_mtime,
            reverse=True,
        ):
            try:
                document = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if not isinstance(document, Mapping) or document.get("schema") not in _SCHEMAS:
                continue
            if str(document.get("ownerId") or "") != owner:
                continue
            summaries.append(
                {
                    "batchId": document.get("batchId"),
                    "status": document.get("status"),
                    "createdAt": document.get("createdAt"),
                    "startedAt": document.get("startedAt"),
                    "finishedAt": document.get("finishedAt"),
                    "summary": document.get("summary", {}),
                    "stopAfter": document.get("stopAfter"),
                    "keepTasks": bool(document.get("keepTasks")),
                    "concurrency": _clamp_concurrency(document.get("concurrency")),
                    "rounds": _clamp_rounds(document.get("rounds")),
                    "transport": document.get("transport"),
                    "discovery": document.get("discovery"),
                }
            )
            if len(summaries) >= limit:
                break
        return summaries

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------

    async def rerun_item(
        self, batch_id: str, index: int, *, owner: str = ""
    ) -> dict[str, object]:
        """Queue one item for re-execution with a fresh run identity.

        A rerun may be queued while the main pass still runs: it shares the batch's slot pool, so
        it starts at the next free slot and never lifts the batch above ``concurrency`` active
        items. Only an item that already reached a terminal result can be rerun; a pending or
        running item would be executed twice. The previous attempt stays in ``item.attempts``.
        """
        batch = self._owned(batch_id, owner)
        if batch is None:
            raise KeyError(batch_id)
        messages = self._batch_messages(batch)
        if index < 0 or index >= len(batch.items):
            raise ValueError(messages.get("invalid_item_index"))
        item = batch.items[index]
        if item.status == CASE_RUNNING:
            raise ValueError(messages.get("item_still_running"))
        if item.status == CASE_PENDING and batch.status == BATCH_RUNNING:
            # The main pass has not reached this row yet; rerunning it now would execute it twice.
            raise ValueError(messages.get("item_not_run_yet"))
        if index in batch.rerun_queue or index in batch.rerun_pending:
            raise ValueError(messages.get("item_already_queued"))
        # A stale flag from an earlier cancel must not swallow this request; only a cancel that
        # arrives during the release below aborts the enqueue.
        item.cancel_requested = False
        batch.rerun_pending.add(index)
        try:
            # The previous attempt may still hold a remote Task; release it before the new
            # identity starts, so a rerun never doubles the quota the batch consumes.
            await self._release_item_runs(
                item, reason="The tester replaced this run with a new attempt."
            )
        finally:
            batch.rerun_pending.discard(index)
        if item.cancel_requested:
            # The row was cancelled while its previous Task was being released.
            item.cancel_requested = False
            return self._snapshot(batch)
        batch.rerun_queue.append(index)
        if batch.status != BATCH_RUNNING:
            batch.status = BATCH_RUNNING
            batch.cancel_requested = False
            batch.finished_at = None
            batch.error = None
        self._ensure_slots(batch)
        if not batch.rerun_active:
            batch.rerun_active = True
            batch.rerun_task = asyncio.create_task(
                self._run_rerun_queue(batch), name=f"tester-{batch_id}-rerun"
            )
        self._persist(batch)
        await asyncio.sleep(0)
        return self._snapshot(batch)

    async def _run_rerun_queue(self, batch: _Batch) -> None:
        """Drain the rerun queue, sharing the main pass's slot pool and completion gate.

        A worker takes an item out of the queue only after it holds a slot, so a queued rerun
        stays visible as ``排队中`` while the main pass (or another rerun) occupies the pool.
        """

        async def worker() -> None:
            while not batch.cancel_requested:
                if not batch.rerun_queue:
                    return
                async with self._batch_slot(batch):
                    if batch.cancel_requested:
                        return
                    index = self._pop_rerun(batch)
                    if index is None:
                        return
                    item = batch.items[index]
                    self._begin_rerun(batch, item)
                    batch.active_indices.append(index)
                    batch.current_index = index
                    batch.active_concurrency = min(
                        batch.concurrency, len(batch.active_indices)
                    )
                    try:
                        # The item may use the pool whenever the batch has more than one slot:
                        # the main pass may be using the others, and the global serial lock
                        # would make this rerun wait for unrelated batches.
                        await self._run_item(
                            batch, index, item, concurrent=batch.concurrency > 1
                        )
                    finally:
                        if index in batch.active_indices:
                            batch.active_indices.remove(index)
                        batch.current_index = (
                            batch.active_indices[0] if batch.active_indices else None
                        )

        try:
            await asyncio.gather(
                *(worker() for _ in range(max(1, batch.concurrency)))
            )
        except asyncio.CancelledError:
            self._mark_pending_cancelled(batch)
            raise
        finally:
            batch.rerun_active = False
            batch.rerun_task = None
            self._finish_batch_if_idle(batch)

    def _pop_rerun(self, batch: _Batch) -> int | None:
        """Return the next rerun index that was not cancelled, ``None`` when the queue is empty."""
        while batch.rerun_queue:
            index = batch.rerun_queue.popleft()
            if 0 <= index < len(batch.items) and not batch.items[index].cancel_requested:
                return index
        return None

    def _begin_rerun(self, batch: _Batch, item: CaseItem) -> None:
        """Record the previous attempt and reset the row before the queued rerun starts."""
        item.attempts.append(
            {
                "runId": item.run_id,
                "round": item.round_index,
                "status": item.status,
                "finishedAt": _now_iso(),
            }
        )
        item.status = CASE_PENDING
        item.cancel_requested = False
        item.stages = {}
        item.run_id = None
        item.duration_ms = None
        item.tokens = {}
        item.error = None
        item.baseline = None
        # The arbitration evidence belongs to the attempt that produced it; a rerun must not
        # export the previous run's intent/trace/answer when the new attempt fails early.
        item.trace_id = ""
        item.langfuse_warning = ""
        item.langfuse_trace_id = ""
        item.intent = None
        item.server_intent = None
        item.intent_meta = {}
        item.refs = {}
        item.answer = None
        item.interception = None
        item.client_quality = None
        item.intent_verdict = None
        item.needs_review = False

    def _ensure_slots(self, batch: _Batch) -> None:
        """Create the per-batch slot pool the main pass and the rerun queue share."""
        if batch.slots is None:
            batch.slots = asyncio.Semaphore(max(1, batch.concurrency))

    @asynccontextmanager
    async def _batch_slot(self, batch: _Batch):
        """Hold one batch slot while one item executes, releasing it on cancellation too."""
        self._ensure_slots(batch)
        slots = batch.slots
        assert slots is not None
        await slots.acquire()
        try:
            yield
        finally:
            slots.release()

    def _finish_batch_if_idle(self, batch: _Batch) -> None:
        """Finish the batch only when both execution lanes are done and nothing is queued."""
        if batch.status != BATCH_RUNNING:
            return
        if batch.main_active or batch.rerun_active or batch.rerun_queue:
            return
        batch.status = BATCH_CANCELLED if batch.cancel_requested else BATCH_FINISHED
        batch.finished_at = _now_iso()
        batch.active_indices = []
        batch.current_index = None
        batch.active_concurrency = 1
        batch.slots = None
        self._persist(batch)

    async def _run_batch(self, batch: _Batch) -> None:
        """Run the selected items once, round by round; the rerun queue runs beside it."""
        selected = list(range(len(batch.items)))
        concurrent = batch.concurrency > 1 and len(selected) > 1
        groups = _round_groups(batch, selected)
        batch.active_concurrency = batch.concurrency if concurrent else 1
        try:
            for group in groups:
                if batch.cancel_requested:
                    break
                if group:
                    batch.current_round = batch.items[group[0]].round_index
                group_concurrent = concurrent and len(group) > 1
                batch.active_concurrency = batch.concurrency if group_concurrent else 1
                if group_concurrent:
                    await self._run_items_concurrently(batch, group, concurrent=True)
                else:
                    await self._run_items_sequentially(
                        batch, group, concurrent=group_concurrent
                    )
        except asyncio.CancelledError:
            self._mark_pending_cancelled(batch)
            raise
        finally:
            batch.main_active = False
            batch.current_index = None
            self._finish_batch_if_idle(batch)

    async def _run_items_sequentially(
        self, batch: _Batch, selected: Sequence[int], *, concurrent: bool = False
    ) -> None:
        """Run the selected items one after another, holding one shared batch slot each.

        Kept separate from :meth:`_run_items_concurrently` so a single-item batch always takes
        exactly the code path it always took; the only addition is the per-item cancel check and
        the slot acquisition a rerun may be waiting on.
        """
        messages = self._batch_messages(batch)
        for index in selected:
            if batch.cancel_requested:
                break
            item = batch.items[index]
            if item.cancel_requested:
                # Cancelled while queued: it must not start, and the batch keeps going.
                _mark_item_cancelled(item, messages)
                continue
            async with self._batch_slot(batch):
                # A cancel may arrive while the item waited for a shared slot.
                if item.cancel_requested:
                    _mark_item_cancelled(item, messages)
                    continue
                batch.active_indices.append(index)
                batch.current_index = index
                try:
                    await self._run_item(batch, index, item, concurrent=concurrent)
                finally:
                    if index in batch.active_indices:
                        batch.active_indices.remove(index)
                    batch.current_index = (
                        batch.active_indices[0] if batch.active_indices else None
                    )

    async def _run_items_concurrently(
        self, batch: _Batch, selected: Sequence[int], *, concurrent: bool = True
    ) -> None:
        """Run up to ``batch.concurrency`` items at once, taking them in catalog order."""
        pending = deque(selected)
        messages = self._batch_messages(batch)

        async def worker() -> None:
            while pending:
                if batch.cancel_requested:
                    return
                # No await between the check and the pop, so two workers cannot take one index.
                index = pending.popleft()
                item = batch.items[index]
                if item.cancel_requested:
                    _mark_item_cancelled(item, messages)
                    continue
                async with self._batch_slot(batch):
                    # A cancel may arrive while the item waited for a shared slot.
                    if item.cancel_requested:
                        _mark_item_cancelled(item, messages)
                        continue
                    batch.active_indices.append(index)
                    batch.current_index = index
                    try:
                        await self._run_item(batch, index, item, concurrent=concurrent)
                    finally:
                        if index in batch.active_indices:
                            batch.active_indices.remove(index)
                        # ``currentIndex`` stays meaningful for older consumers of the snapshot.
                        batch.current_index = (
                            batch.active_indices[0] if batch.active_indices else None
                        )

        workers = max(1, min(batch.concurrency, len(selected)))
        await asyncio.gather(*(worker() for _ in range(workers)))

    def _mark_pending_cancelled(self, batch: _Batch) -> None:
        message = self._batch_messages(batch).get("batch_cancelled")
        for item in batch.items:
            if item.status in {CASE_PENDING, CASE_RUNNING}:
                item.status = CASE_CANCELLED
                item.error = message

    async def _run_item(
        self, batch: _Batch, index: int, item: CaseItem, *, concurrent: bool = False
    ) -> None:
        item.status = CASE_RUNNING
        started = time.monotonic()
        messages = self._batch_messages(batch)
        try:
            if item.scratch is not None:
                await self._run_scratch(batch, index, item, messages)
            else:
                await self._run_case_item(batch, index, item, messages, concurrent=concurrent)
        except asyncio.CancelledError:
            item.status = CASE_CANCELLED
            item.error = messages.get("batch_cancelled")
            raise
        except Exception as exc:  # noqa: BLE001 - one bad item must not stop the batch
            _LOGGER.warning("tester item %s/%s failed", item.case_id, item.protocol, exc_info=True)
            item.status = CASE_ERROR
            item.error = f"{type(exc).__name__}: {exc}"
        finally:
            item.duration_ms = int(round((time.monotonic() - started) * 1000))
            if item.cancel_requested:
                # An item cancelled on its own must never be reported green or red: the flow may
                # have judged part of its stages before the cancellation unwound. This also
                # overrides ``_run_case_item``'s unconditional status mapping.
                _mark_item_cancelled(item, messages)
            elif item.status == CASE_RUNNING and not batch.cancel_requested:
                # A scratch item never calls ``_run_case_item``'s status mapping; derive the
                # status from its stages so a failed or timed-out scratch is not counted as a pass.
                item.status = self._item_status(item, messages)
            self._persist(batch)

    async def _run_case_item(
        self,
        batch: _Batch,
        index: int,
        item: CaseItem,
        messages: TesterMessages,
        *,
        concurrent: bool = False,
    ) -> None:
        case = self._catalog.get(item.case_id)
        if case is None:
            item.status = CASE_ERROR
            item.error = messages.get("unknown_case_id", case_id=item.case_id)
            return
        stages = list(ALL_STAGES[: ALL_STAGES.index(batch.stop_after) + 1])
        effective_case = case
        #: Whether the case carries an expected interception plus a replacement request. The run
        #: always starts with the operator's original request; the replacement is a correction the
        #: run applies only after its own pre-send gate blocks, so a miss dispatches the original.
        blocked_followup = False
        correction: str | None = None
        #: Whether the original request was not intercepted, so the replacement must not run.
        interception_missed = False
        if item.protocol == PROTOCOL_A2A:
            # OSP defines no SDK stages for pure A2A; only the runtime chain applies. The
            # blocked_followup replacement belongs to the A2A-T pre-send gate: a pure request is
            # free text with no Task-T to intercept, and the case's A2A dialogue expects the
            # vendor's own overlap question before the client answers.
            stages = [stage for stage in stages if stage not in SDK_STAGE_ORDER]
        else:
            blocked_followup = is_blocked_case(case) and has_followup(case)
            if blocked_followup:
                # The corpus expectations describe the replacement request; the run still starts
                # with the original text and reaches the replacement through its own gate.
                effective_case = followup_case(case)
                correction = followup_input(case)
            if batch.stop_after in SDK_STAGE_ORDER:
                # SDK-only batch (``intent`` / ``task_t`` / ``validate``): no run is started, so the
                # tester runs the three SDK stages itself and judges their columns.
                #
                # The SDK runner owns the two-segment split of a blocked case: it returns the
                # follow-up request's stages as the outcome and keeps the intercepted original as
                # ``outcome.interception``.
                await self._run_sdk_stages(
                    case,
                    item,
                    batch.stop_after,
                    messages,
                    outcome_case=effective_case if blocked_followup else None,
                    concurrent=concurrent,
                )
                interception_missed = blocked_followup and not _interception_held(item)
        # The run executes the operator's original request; a correction is a later input of it.
        item.run_request = str(case.get("user_input") or "")
        if item.cancel_requested:
            # The SDK call runs in a thread and cannot be interrupted; a cancel that arrived
            # during it must stop here, before a baseline is seeded or a real request is sent.
            return
        # The frozen evidence names the operator's original request; the expectations describe the
        # replacement a blocked case eventually dispatches.
        self._collect_reference(item, case if interception_missed else effective_case)
        if interception_missed:
            # Only an SDK-only batch can know the miss before a run exists; a full-flow run
            # discovers it at its own gate and records it there.
            self._stamp_interception_miss(item, stages, dispatched=False, messages=messages)
        if batch.stop_after in SDK_STAGE_ORDER:
            if blocked_followup and not interception_missed:
                # The SDK stages are this item's whole evidence, so a follow-up that was blocked
                # again must name itself here instead of looking like the expected interception.
                item.refs["dispatchedRequest"] = correction or ""
                self._stamp_followup_block(item, messages=messages)
            # No run will collect this evidence: freeze the request the SDK stages read so the
            # arbitration export still names it.
            if not item.intent_meta.get("rawRequest"):
                item.intent_meta["rawRequest"] = str(item.refs.get("userInput") or "")
                item.intent_meta["normalizedText"] = item.intent_meta["rawRequest"]
            if not item.intent_meta.get("source"):
                item.intent_meta["source"] = "sdk" if item.intent is not None else ""
            item.status = self._item_status(item, messages)
            return
        await self._run_full_flow(
            batch,
            index,
            item,
            effective_case,
            stages,
            run_request=item.run_request,
            correction=correction if blocked_followup else None,
            interception_case=case if blocked_followup else None,
        )
        item.status = self._item_status(item, messages)

    async def _run_sdk_stages(
        self,
        case: Mapping[str, Any],
        item: CaseItem,
        stop_after: str,
        messages: TesterMessages,
        *,
        outcome_case: Mapping[str, Any] | None = None,
        concurrent: bool = False,
        interception_only: bool = False,
    ) -> None:
        """Run the SDK stages of one SDK-only case and record its interception segment, when split.

        ``outcome_case`` is the derived replacement case whose expectations judge the returned
        stages; the runner already judged the intercepted original, so those stages are recorded
        verbatim (the runner derived their assertion flags). ``concurrent`` selects the pooled
        client path; the default keeps the historical single locked client.

        A preflight belongs to no run, so its producer spans are silenced.
        """
        sink_token = set_task_sink(None)
        try:
            results, interception, error = await self._sdk_stage_results(
                case,
                stop_after,
                messages,
                outcome_case=outcome_case,
                concurrent=concurrent,
            )
        finally:
            reset_task_sink(sink_token)
        item.stages.update(results)
        if interception is not None:
            record: dict[str, object] = {
                "input": str(case.get("user_input") or ""),
                "followupInput": followup_input(case) or "",
                "stages": {stage: result.to_dict() for stage, result in interception.items()},
            }
            if error:
                record["error"] = error
            item.interception = record
        if error:
            item.error = error

    async def _sdk_stage_results(
        self,
        case: Mapping[str, Any],
        stop_after: str,
        messages: TesterMessages,
        *,
        outcome_case: Mapping[str, Any] | None = None,
        concurrent: bool = False,
    ) -> tuple[dict[str, StageResult], dict[str, StageResult] | None, str | None]:
        """Run the SDK-owned stages of one case and return stages, interception and error.

        A blocked case with a replacement request makes the runner execute two segments; the
        follow-up segment is the returned outcome and the original is ``outcome.interception``.
        """
        judge = outcome_case if outcome_case is not None else case
        model_mode = MODEL_MODE_LIVE
        outcome = await self._run_case_with_client(
            case,
            stop_after,
            model_mode,
            concurrent=concurrent,
        )
        negotiation_expected = _negotiation_expected(judge, PROTOCOL_A2A_T)
        results: dict[str, StageResult] = {}
        for stage in outcome.stages:
            result = StageResult(
                stage=stage.stage,
                status=stage.status,
                message=stage.message,
                expected=stage.expected,
                actual=stage.actual,
                diffs=[diff.to_dict() for diff in stage.diffs],
                detail=dict(stage.detail) if stage.detail else None,
            )
            protocol = "" if stage.stage == "intent" else PROTOCOL_A2A_T
            result = _apply_assertion(judge, protocol, stage.stage, result, messages)
            if stage.stage == "task_t":
                if negotiation_expected:
                    # The case's Task-T is the post-negotiation one; the SDK runner only sees the
                    # initial request, so comparing them would be a false failure.
                    result.status = STAGE_RECORDED
                    result.message = messages.get("negotiated_task_t")
                elif result.status == STAGE_FAILED:
                    # A Task-T difference is a warning for the reviewer, not a failed case: the
                    # case verdict follows the downstream's own reading of the Task-T, not our
                    # rendering of it. ``eval/osp`` keeps its own failure semantics because
                    # Task-T accuracy is what that report measures.
                    result.status = STAGE_WARNING
                    result.message = messages.get("task_t_mismatch")
            results[stage.stage] = result
        interception: dict[str, StageResult] | None = None
        if outcome.interception is not None:
            interception = {
                stage.stage: StageResult(
                    stage=stage.stage,
                    status=stage.status,
                    message=stage.message,
                    expected=stage.expected,
                    actual=stage.actual,
                    diffs=[diff.to_dict() for diff in stage.diffs],
                    detail=dict(stage.detail) if stage.detail else None,
                )
                for stage in outcome.interception
            }
        return results, interception, outcome.error

    async def _run_full_flow(
        self,
        batch: _Batch,
        index: int,
        item: CaseItem,
        case: Mapping[str, Any],
        stages: Sequence[str],
        *,
        run_request: str,
        correction: str | None = None,
        interception_case: Mapping[str, Any] | None = None,
    ) -> None:
        if item.cancel_requested:
            # Cancelled between the SDK stages and here: never seed a baseline or send a request.
            return
        messages = self._batch_messages(batch)
        runtime_protocol = _runtime_protocol(item.protocol)
        runner = _case_runner(case)
        scenario = str(runner.get("scenario") or "night")
        language = runner.get("language")
        resolved_language = language if isinstance(language, str) and language else None
        resolved_timeout = self._resolved_timeout(batch, case)
        target_task_id: str | None = None
        target_context_id: str | None = None
        target_server_task_id: str | None = None
        target_server_context_id: str | None = None
        defaults = self._protocol_defaults.get(runtime_protocol, {})
        transport = defaults.get("transport", "sse")
        discovery = defaults.get("discovery", "direct")
        negotiation_expected = _negotiation_expected(case, item.protocol)
        # The Q/P column is only an expectation; the client agent authors every reply.
        agent_negotiation = negotiation_expected
        # A blocked A2A-T request never reaches the NMS, so it has nothing to continue; without a
        # follow-up, seeding would only create a Task that the case then refuses to modify.
        blocked_without_followup = (
            runtime_protocol == RUNTIME_A2A_T
            and is_blocked_case(case)
            and not has_followup(case)
        )
        if case.get("operation") == "Modify" and not blocked_without_followup:
            # A Modify continues an existing Task. The corpus says nothing about where that Task
            # came from, so the tester pairs every Modify row with one explicit baseline Create
            # (see ``_baseline_request``) and continues the Task it produced. A2A-T and a direct
            # standard-A2A hop both continue the vendor Task; only the local hop continues the
            # client agent's outer Task, which is the Task id that hop owns.
            baseline = await self._seed_baseline(batch, index, item, runtime_protocol, case)
            if baseline is None:
                if item.cancel_requested:
                    # Cancelled while the baseline was starting; the item is already unwinding.
                    return
                for stage in ("send", "negotiate", "accepted", "working", "final"):
                    if stage in stages:
                        item.stages[stage] = StageResult(
                            stage, STAGE_ERROR, messages.get("modify_baseline_create_failed")
                        )
                item.status = CASE_ERROR
                return
            if runtime_protocol == RUNTIME_A2A_T or discovery == DISCOVERY_NMS:
                target_task_id = str(baseline["taskId"])
                target_context_id = str(baseline["contextId"])
            else:
                target_server_task_id = str(baseline["taskId"])
                target_server_context_id = str(baseline["contextId"])

        resolved_conditions = case_client_conditions(case)
        conversation_id, idempotency_key = self._run_identity(batch, index, item)
        mode = MODE_AUTO
        run = await self._runs.start(
            scenario,
            mode,
            transport,
            discovery,
            run_request,
            protocol=runtime_protocol,
            conversation_id=conversation_id,
            user_id="tester",
            idempotency_key=idempotency_key,
            source={
                "kind": "tester",
                "channel": "tester-console",
                "batchId": batch.batch_id,
                "caseId": item.case_id,
                "caseName": item.name,
                # A rate-less site-level negotiation is expected to reach the server, which
                # proposes the missing value; only the corpus' negotiation cases get this pass.
                "negotiationExpected": negotiation_expected,
                # The run's request is the operator's original text; a replacement is a
                # correction of the run's own gate, never a second request of its own.
                "requestRole": "blocked_followup" if correction else "",
            },
            target_task_id=target_task_id,
            target_context_id=target_context_id,
            target_server_task_id=target_server_task_id,
            target_server_context_id=target_server_context_id,
            language=resolved_language,
            correction=correction,
            client_conditions=resolved_conditions,
            negotiation_principles=batch.principles_text,
            principles_version=batch.principles_version,
        )
        item.run_id = run.run_id
        self._persist(batch)
        if item.cancel_requested:
            # The cancel arrived while the run was being created; stop it before observing it,
            # otherwise its Task would keep running and the item would wait for the full timeout.
            await self._runs.cancel(run.run_id)
        try:
            await self._wait_for_stage(
                run,
                item,
                batch,
                stages,
                case=case,
                timeout_seconds=resolved_timeout,
                working_timeout_seconds=self._resolved_working_timeout(batch),
            )
        finally:
            # The run's own pre-send gate is the interception segment of a blocked case. Read it
            # before the evidence collection so the export names the case the run actually
            # reached: the replacement's expectations on a hit, the original's on a miss.
            interception_held = True
            if interception_case is not None:
                interception_held = self._record_run_interception(
                    item, run, interception_case, messages
                )
            await self._collect_metrics(
                item, run, case if interception_held else interception_case, messages
            )
            # Warm the Langfuse column cache off the item's critical path: the export then reads
            # a local file instead of refetching every trace under a rate limit.
            self._schedule_trace_prefetch(run)
            if interception_case is not None and not interception_held:
                # The gate let the original request through, so the replacement never ran. The
                # downstream evidence is recorded against the original, never judged against the
                # replacement's expectations.
                self._demote_missed_interception(item, interception_case, messages)
                self._stamp_interception_miss(item, (), dispatched=True, messages=messages)
            elif interception_case is not None:
                # The correction was applied; a block of the corrected Task-T is the follow-up
                # being rejected, not the interception the case expects.
                self._stamp_followup_block(item, run, messages=messages)
            if agent_negotiation:
                # The decision-quality appendix reads the run evidence; it must never change the
                # item's acceptance status, so it is recorded after every other verdict.
                await self._record_client_quality(item, case, run, resolved_conditions)
            # The evidence is collected; release the remote Task unless this batch keeps Tasks for
            # long-task verification. ``accepted``/``working`` end the observation window, and the
            # tester — unlike the presentation page — never reattaches to that run, so leaving the
            # Task running through a 51-case acceptance batch would exhaust the NMS quota.
            if not batch.keep_tasks:
                await self._release_item_runs(
                    item,
                    run,
                    reason=f"The tester ended observation at the '{batch.stop_after}' stage.",
                )

    def _schedule_trace_prefetch(self, run: RunState) -> None:
        """Fetch one finished run's Langfuse columns in the background and cache them."""
        if self._trace_config is None or self._runs_dir is None:
            return
        trace_id = _langfuse_trace_id(run)
        if not trace_id:
            return
        task = asyncio.create_task(
            self._prefetch_trace_columns(run.run_id, trace_id, run.finished_at),
            name=f"tester-langfuse-prefetch-{run.run_id}",
        )
        self._trace_prefetch_tasks.add(task)
        task.add_done_callback(self._trace_prefetch_tasks.discard)

    async def _prefetch_trace_columns(
        self, run_id: str, trace_id: str, finished_at: float | None
    ) -> None:
        """Wait for the ingestion lag, then fill the run's metrics cache under the shared slots.

        ``settled`` tells the cache whether this fetch happened late enough after the run finished
        to hold every span; an unsettled entry is never served, so the export refreshes it.
        """
        await asyncio.sleep(TRACE_PREFETCH_DELAY_SECONDS)
        settled = finished_at is not None and time.monotonic() - finished_at >= TRACE_SETTLE_SECONDS
        out_root = self._runs_dir / run_id / "langfuse-export" if self._runs_dir else None
        if out_root is None:
            return
        async with self._trace_prefetch_slots:
            try:
                await asyncio.to_thread(
                    build_trace_columns,
                    self._trace_config,
                    run_id=run_id,
                    trace_id=trace_id,
                    out_root=out_root,
                    settled=settled,
                )
            except Exception:  # noqa: BLE001 - a cache warm-up must never disturb the batch
                _LOGGER.debug(
                    "tester Langfuse prefetch failed for run %s", run_id, exc_info=True
                )

    async def _record_client_quality(
        self,
        item: CaseItem,
        case: Mapping[str, Any],
        run: RunState,
        conditions: str,
    ) -> None:
        """Record the decision-quality appendix of one agent-authored negotiation item.

        The appendix is advisory: a judge outage or a checker bug is recorded as ``unjudged`` and
        never touches ``item.status``.
        """
        final_state = run.result.final_state if run.result is not None else None
        gate_reason = ""
        if final_state in (FINAL_STATE_INPUT_INVALID, FINAL_STATE_DECISION_UNGROUNDED):
            gate_reason = (run.result.error if run.result is not None else None) or ""
        try:
            result = await judge_client_quality(
                case=case,
                observations=_client_quality_observations(run),
                judge=self._judge,
                final_state=final_state,
                gate_reason=gate_reason,
                client_conditions=conditions,
                judge_label=self._judge_model_label,
                protocol=item.protocol,
            )
        except Exception as exc:  # noqa: BLE001 - the appendix must never fail the item
            _LOGGER.warning(
                "tester client quality of %s/%s failed: %s",
                item.case_id,
                item.protocol,
                exc,
                exc_info=True,
            )
            item.client_quality = {
                "quality": "unjudged",
                "method": "unjudged",
                "needsHumanReview": False,
                "rounds": [],
            }
            return
        item.client_quality = result.to_dict()

    async def _run_scratch(
        self, batch: _Batch, index: int, item: CaseItem, messages: TesterMessages
    ) -> None:
        """Run an ad-hoc request through the full flow; no expectations are asserted."""
        if item.scratch is None or item.cancel_requested:
            return
        runtime_protocol = _runtime_protocol(item.protocol)
        defaults = self._protocol_defaults.get(runtime_protocol, {})
        conversation_id, idempotency_key = self._run_identity(batch, index, item)
        run = await self._runs.start(
            "night",
            self._mode_for(batch, item.protocol),
            defaults.get("transport", "sse"),
            defaults.get("discovery", "direct"),
            item.scratch,
            protocol=runtime_protocol,
            conversation_id=conversation_id,
            user_id="tester",
            idempotency_key=idempotency_key,
            source={"kind": "tester", "channel": "tester-scratch", "batchId": batch.batch_id},
            negotiation_principles=batch.principles_text,
            principles_version=batch.principles_version,
        )
        item.run_id = run.run_id
        self._persist(batch)
        if item.cancel_requested:
            await self._runs.cancel(run.run_id)
        timed_out = await self._wait_for_run(run, self._resolved_timeout(batch))
        actual = run.result.final_state if run.result is not None else None
        if run.status == STATUS_FAILED:
            failure = run.error or messages.get("run_failed")
            item.stages["final"] = StageResult(
                "final", STAGE_ERROR, failure, expected=None, actual=actual
            )
            item.error = failure
        elif timed_out:
            failure = messages.get("scratch_timeout")
            item.stages["final"] = StageResult(
                "final", STAGE_ERROR, failure, expected=None, actual=actual
            )
            item.error = failure
        else:
            item.stages["final"] = StageResult(
                "final",
                STAGE_RECORDED,
                messages.get("scratch_recorded"),
                expected=None,
                actual=actual,
            )
        await self._collect_metrics(item, run, messages=messages)
        if not batch.keep_tasks:
            await self._release_item_runs(
                item,
                run,
                reason="The tester ended observation after the scratch request.",
            )

    async def _release_item_runs(
        self,
        item: CaseItem,
        run: RunState | None = None,
        *,
        reason: str = "The tester ended observation.",
    ) -> None:
        """End the runs one item created and release their remote Tasks.

        ``run`` is the item's own run when the caller still holds it; it is then awaited so the
        observation unwinds before the batch moves on to the next item. Reaching the configured
        stage is a normal end of observation: the run is released (finished, no error), never
        labelled as an operator cancel. A Modify item's baseline run is released too: it is
        normally already stopped by the Task handover, in which case release is a no-op. Unknown
        or finished runs are ignored, so the shutdown sweep can call this for every item.
        """
        if run is not None and run.status == "running":
            await self._runs.release(run.run_id, reason=reason)
            await self._wait_for_run(run, _CANCEL_GRACE_SECONDS)
        elif run is None and item.run_id:
            await self._runs.release(item.run_id, reason=reason)
        baseline = _baseline_run_id(item)
        if baseline is not None and baseline != item.run_id:
            await self._runs.release(baseline, reason=reason)

    def _run_identity(self, batch: _Batch, index: int, item: CaseItem) -> tuple[str, str]:
        """Return the conversation id and idempotency key of this attempt.

        A rerun must not reuse the previous run: ``RunManager`` deduplicates by idempotency key, and
        a finished conversation would return the old run instead of executing again.
        """
        attempt = len(item.attempts)
        suffix = f"-r{attempt}" if attempt else ""
        base = f"tester-{batch.batch_id}-{index}{suffix}"
        return base, base

    @staticmethod
    def _mode_for(batch: _Batch, protocol: str) -> str:
        """Return the runtime decision mode of one item."""
        del batch, protocol
        return MODE_AUTO

    @staticmethod
    def _resolved_timeout(batch: _Batch, case: Mapping[str, Any] | None = None) -> float:
        """Return the run timeout of one item: batch override, else case runner, else 300s."""
        if batch.timeout_seconds is not None:
            return batch.timeout_seconds
        value = _case_runner(case).get("timeout_seconds")
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return max(1.0, float(value))
        return 300.0

    @staticmethod
    def _resolved_working_timeout(batch: _Batch) -> float:
        """Return the ``working`` observation window of one item."""
        if batch.working_timeout_seconds is not None:
            return batch.working_timeout_seconds
        return DEFAULT_WORKING_TIMEOUT_SECONDS

    async def _seed_baseline(
        self,
        batch: _Batch,
        index: int,
        item: CaseItem,
        protocol: str,
        case: Mapping[str, Any],
    ) -> dict[str, object] | None:
        """Create the Task a Modify item continues, and wait until it can be continued.

        The case's ``baseline.request`` is the paired Create (a normal-day task for the same region
        and sites); the fallback fixture is used only when the case declares none. The Modify never
        targets a Task the tester did not just create.
        """
        messages = self._batch_messages(batch)
        defaults = self._protocol_defaults.get(protocol, {})
        conversation_id, idempotency_key = self._run_identity(batch, index, item)
        request = _baseline_request(case)
        mode = MODE_AUTO
        run = await self._runs.start(
            BASELINE_SCENARIO,
            mode,
            defaults.get("transport", "sse"),
            defaults.get("discovery", "direct"),
            request,
            protocol=protocol,
            conversation_id=f"{conversation_id}-baseline",
            user_id="tester",
            idempotency_key=f"{idempotency_key}-baseline",
            source={
                "kind": "tester",
                "channel": f"tester-baseline-{protocol}",
                "batchId": batch.batch_id,
                "caseId": item.case_id,
            },
            # The paired Create follows the same frozen principles as the batch.
            negotiation_principles=batch.principles_text,
            principles_version=batch.principles_version,
        )
        # Publish the baseline as soon as it starts: the tester UI polls the batch snapshot, and
        # without this the Modify item showed no run and no progress until the Create finished.
        item.baseline = {
            "runId": run.run_id,
            "protocol": protocol,
            "status": "running",
            "state": run.latest_task_state,
            "request": request,
        }
        if item.cancel_requested:
            # Cancelled while the baseline Create was starting: release it and stop the item.
            item.baseline["status"] = "cancelled"
            await self._runs.cancel(run.run_id)
            return None
        # A Modify needs the Task accepted and still continuable. It must not wait for the
        # baseline run to reach a terminal state: a real NMS keeps the Create Task WORKING for the
        # whole energy-saving window, which made every Modify item time out as a baseline failure.
        discovery = defaults.get("discovery", "")
        state = await self._wait_for_baseline_acceptance(
            run,
            protocol,
            self._resolved_timeout(batch),
            item=item,
            discovery=discovery,
        )
        task_id = run.remote_task_id or (run.result.task_id if run.result is not None else None)
        context_id = run.remote_context_id or (
            run.result.context_id if run.result is not None else None
        )
        accepted = _baseline_continuable(run, protocol, discovery, state) and run.error is None
        if not task_id or not context_id or not accepted:
            message = (
                run.error
                or _last_failed_status_text(run)
                or _baseline_failure_message(protocol, state, discovery, messages)
            )
            item.baseline = {
                "runId": run.run_id,
                "protocol": protocol,
                "status": "failed",
                "state": state,
                "finalState": run.result.final_state if run.result is not None else None,
                "request": request,
                "error": message,
                "downstreamTaskId": run.downstream_task_id,
            }
            item.error = message
            return None
        baseline = {
            "runId": run.run_id,
            "protocol": protocol,
            "status": "accepted",
            "taskId": task_id,
            "contextId": context_id,
            "state": state,
            "request": request,
            "finalState": run.result.final_state if run.result is not None else None,
            "downstreamTaskId": run.downstream_task_id,
        }
        item.baseline = baseline
        return baseline

    async def _wait_for_baseline_acceptance(
        self,
        run: RunState,
        protocol: str,
        timeout_seconds: float,
        *,
        item: CaseItem | None = None,
        discovery: str = "",
    ) -> str | None:
        """Poll until the baseline Task reaches the state a Modify can continue from.

        A2A-T and a direct standard-A2A hop continue the vendor Task, which the vendor reports as
        ``SUBMITTED`` on acceptance. The local hop continues the client agent's outer Task, which must
        be ``WORKING``: ``COMPLETED`` is terminal (the A2A layer rejects the continuation) and
        ``INPUT_REQUIRED`` would route the Modify text as an answer to the pending question.

        ``item`` is updated in place on every poll so the batch snapshot (and therefore the tester
        UI) shows the baseline's current state while the Create is still in flight.
        """
        deadline = time.monotonic() + max(0.1, timeout_seconds)
        while time.monotonic() < deadline:
            state = run.latest_task_state
            if item is not None and isinstance(item.baseline, dict):
                item.baseline["state"] = state
                if run.remote_task_id:
                    item.baseline["taskId"] = run.remote_task_id
                if run.remote_context_id:
                    item.baseline["contextId"] = run.remote_context_id
                if run.downstream_task_id:
                    item.baseline["downstreamTaskId"] = run.downstream_task_id
            if run.error is not None:
                # A failed Create can still have published a transient outer WORKING frame. The
                # run error is the later, authoritative signal.
                return state
            if state in _FAILED_TASK_STATES:
                return state
            if _baseline_continuable(run, protocol, discovery, state):
                return state
            if run.finished():
                return state
            await asyncio.sleep(_POLL_SECONDS)
        return run.latest_task_state

    async def _wait_for_stage(
        self,
        run: RunState,
        item: CaseItem,
        batch: _Batch,
        stages: Sequence[str],
        *,
        case: Mapping[str, Any] | None,
        timeout_seconds: float,
        working_timeout_seconds: float,
    ) -> None:
        """Wait for the last requested runtime stage and record its assertion."""
        messages = self._batch_messages(batch)
        expected_send = True
        expected_rounds = 0
        expected_kinds: list[str] | None = None
        expected_final: object = None
        if case is not None:
            protocol_block = case["expect"]["protocols"][item.protocol]
            expected_final = protocol_block.get("final_state")
            if item.protocol == PROTOCOL_A2A_T:
                expected_send = bool(protocol_block.get("send"))
            negotiation = protocol_block["negotiation"]
            expected_rounds = int(negotiation.get("expected_rounds", 0))
            expected_kinds = negotiation.get("kinds")

        # ``accepted``/``working`` only make sense when the case expects a send. Disabling the
        # ``send`` assertion does not mean no Task is created, so it must not skip them.
        send_expected = expected_send

        started = time.monotonic()
        if "send" in stages:
            sent = await self._wait_for_span(run, "message_sent", timeout_seconds)
            status = STAGE_PASSED if sent == expected_send else STAGE_FAILED
            send_result = StageResult(
                "send",
                status,
                "" if status == STAGE_PASSED else messages.get("send_mismatch"),
                expected=expected_send,
                actual=sent,
                duration_ms=int(round((time.monotonic() - started) * 1000)),
            )
            if case is not None:
                send_result = _apply_assertion(
                    case, item.protocol, "send", send_result, messages
                )
            item.stages["send"] = send_result
            if batch.stop_after == "send":
                return

        started = time.monotonic()
        if "negotiate" in stages:
            await self._wait_for_negotiation(run, expected_rounds, timeout_seconds)
            actual_rounds = len(run.proposals)
            actual_kinds = [proposal.kind for proposal in run.proposals]
            rounds_ok = actual_rounds == expected_rounds
            kinds_ok = expected_kinds is None or all(
                kind in actual_kinds for kind in expected_kinds
            )
            # The message expectation is the oracle of the dialogue itself; the round count and the
            # kind stay as a cheap pre-check so a wrong shape is explained before the prose.
            messages_expectation = (
                negotiation_message_expectation(case, item.protocol)
                if case is not None
                else None
            )
            observed_messages = (
                [
                    _observed_proposal(proposal, index)
                    for index, proposal in enumerate(run.proposals)
                ]
                if messages_expectation is not None
                else []
            )
            message_match = (
                match_negotiation_messages(messages_expectation, observed_messages)
                if messages_expectation is not None
                else None
            )
            messages_ok = message_match is None or message_match.matched
            # The round count is the workbook's record of how the original dialogue unfolded, not
            # a requirement on ours: a shorter dialogue that reached the same answer is a deviation
            # to review, while a wrong kind or a wrong message contradicts the case.
            status = STAGE_PASSED if kinds_ok and messages_ok else STAGE_FAILED
            if status == STAGE_PASSED and not rounds_ok:
                status = STAGE_WARNING
            expected_payload: dict[str, object] = {
                "rounds": expected_rounds,
                "kinds": expected_kinds,
            }
            actual_payload: dict[str, object] = {
                "rounds": actual_rounds,
                "kinds": actual_kinds,
            }
            if messages_expectation is not None:
                expected_payload["messages"] = [
                    list(alternatives) for alternatives in messages_expectation.rounds
                ]
                actual_payload["messages"] = observed_messages
            if status == STAGE_PASSED:
                negotiate_message = ""
            elif status == STAGE_WARNING:
                negotiate_message = messages.get("negotiate_rounds_warning")
            else:
                negotiate_message = messages.get("negotiate_failed")
            negotiate_result = StageResult(
                "negotiate",
                status,
                negotiate_message,
                expected=expected_payload,
                actual=actual_payload,
                diffs=_message_diffs(message_match),
                duration_ms=int(round((time.monotonic() - started) * 1000)),
                detail=(
                    {"messageMatch": message_match.to_dict()}
                    if message_match is not None
                    else None
                ),
            )
            if case is not None:
                negotiate_result = _apply_assertion(
                    case, item.protocol, "negotiation", negotiate_result, messages
                )
            item.stages["negotiate"] = negotiate_result
            if batch.stop_after == "negotiate":
                return

        if "accepted" in stages:
            item.stages["accepted"] = await self._judge_accepted(
                run, item, case, send_expected, timeout_seconds, messages
            )
            if batch.stop_after == "accepted":
                return

        if "working" in stages:
            item.stages["working"] = await self._judge_working(
                run, item, case, send_expected, working_timeout_seconds, messages
            )
            if batch.stop_after == "working":
                return

        started = time.monotonic()
        if "final" in stages:
            await self._wait_for_run(run, timeout_seconds)
            actual_final = run.result.final_state if run.result is not None else None
            if run.status == STATUS_FAILED:
                status = STAGE_ERROR
                message = run.error or messages.get("run_failed")
                item.error = message
            elif expected_final is None:
                status = STAGE_RECORDED
                # A flow that stopped unfinished carries its reason here; keep it on the stage so
                # the chip tooltip explains the run even when the result column is truncated.
                message = (run.result.error or "") if run.result is not None else ""
            elif actual_final == expected_final:
                status = STAGE_PASSED
                message = ""
            elif _final_state_unpushed(run, actual_final):
                # The NMS acknowledged the Task and pushes no terminal state. Reporting that as a
                # failure blames the client for a push the vendor never sends; the deviation is
                # still shown so the row is not read as a complete Task.
                status = STAGE_WARNING
                message = messages.get("final_state_unpushed")
            else:
                status = STAGE_FAILED
                message = messages.get("final_mismatch")
            final_result = StageResult(
                "final",
                status,
                message,
                expected=expected_final,
                actual=actual_final,
                duration_ms=int(round((time.monotonic() - started) * 1000)),
            )
            if case is not None:
                final_result = _apply_assertion(
                    case, item.protocol, "final_state", final_result, messages
                )
            item.stages["final"] = final_result

    async def _judge_accepted(
        self,
        run: RunState,
        item: CaseItem,
        case: Mapping[str, Any] | None,
        send_expected: bool,
        timeout_seconds: float,
        messages: TesterMessages | None = None,
    ) -> StageResult:
        """Judge whether the remote side accepted the request (the success status arrived).

        ``TASK_STATE_SUBMITTED`` is the A2A-T success signal for Create and Modify alike; a pure
        A2A outer Task starts at ``TASK_STATE_WORKING``. An ``INPUT_REQUIRED`` Task carries an
        identity but the negotiation is not finished, so it must not turn the row green.

        The vendor's contract ships its success copy (``Task creation succeeded{...}``) with the
        status that accepts the request. A real NMS also violates that contract: it reports a bare
        ``SUBMITTED`` and later cancels the Task. Treating the bare state as acceptance would
        report a green row for a Task the vendor never accepted, so for the vendor hop the item
        keeps observing until the success copy or the structured payload arrives (see
        :func:`_acceptance_evidence`). The defect is the NMS's; this is only our robustness.
        """
        if messages is None:
            messages = self._messages
        started = time.monotonic()
        actual = _task_observation(run)
        if not send_expected:
            return StageResult(
                "accepted",
                STAGE_SKIPPED,
                messages.get("expected_no_send"),
                expected=None,
                actual=actual,
                duration_ms=int(round((time.monotonic() - started) * 1000)),
            )

        requires_evidence = _requires_acceptance_evidence(run)
        await self._wait_for_acceptance_state(
            run, timeout_seconds, require_evidence=requires_evidence
        )
        actual = _task_observation(run)
        state = run.latest_task_state
        final_state = run.result.final_state if run.result is not None else None
        if final_state in _PRE_SEND_TERMINAL_STATES:
            status = STAGE_FAILED
            message = messages.get("run_ended_before_send", final_state=final_state)
        elif state in _FAILED_TASK_STATES:
            status = STAGE_FAILED
            message = messages.get("task_state_is", state=state)
        elif state in _ACCEPTED_TASK_STATES:
            if requires_evidence and not _acceptance_evidence(run, state):
                status = STAGE_WAITING
                message = messages.get("acceptance_unproven")
            else:
                status = STAGE_PASSED
                message = ""
        elif run.status == STATUS_FAILED:
            # The flow died while the last observed Task state was still INPUT_REQUIRED (a vendor
            # HTTP 500 is the common case). Reading only the stale state turned a real failure into
            # "waiting", and the item then read as an inconclusive partial.
            status = STAGE_ERROR
            message = run.error or messages.get("run_failed")
        elif run.remote_task_id is None or run.remote_context_id is None:
            status = STAGE_FAILED
            message = messages.get(
                "run_finished_without_task" if run.finished() else "wait_task_timeout"
            )
        elif state == "TASK_STATE_INPUT_REQUIRED":
            status = STAGE_WAITING
            message = messages.get("task_negotiating_input_required")
        else:
            status = STAGE_WAITING
            message = messages.get("no_acceptance_observed")
        return StageResult(
            "accepted",
            status,
            message,
            expected={
                "taskId": messages.get("expected_task_id"),
                "contextId": messages.get("expected_context_id"),
                "state": messages.get("expected_accepted_state"),
            },
            actual=actual,
            duration_ms=int(round((time.monotonic() - started) * 1000)),
        )

    async def _judge_working(
        self,
        run: RunState,
        item: CaseItem,
        case: Mapping[str, Any] | None,
        send_expected: bool,
        working_timeout_seconds: float,
        messages: TesterMessages | None = None,
    ) -> StageResult:
        """Judge whether the Task was observed moving past ``SUBMITTED``."""
        if messages is None:
            messages = self._messages
        started = time.monotonic()
        actual = _task_observation(run)
        # Pure A2A has no workbook final-state oracle, but "the outer Task was accepted and is
        # running" is a protocol fact, not a corpus expectation. Recording the stage made every
        # pure item green by construction; judge the outer Task state instead.
        if not send_expected:
            return StageResult(
                "working",
                STAGE_SKIPPED,
                messages.get("expected_no_send"),
                expected=None,
                actual=actual,
                duration_ms=int(round((time.monotonic() - started) * 1000)),
            )

        state = await self._wait_for_working_state(run, working_timeout_seconds)
        actual = _task_observation(run)
        final_state = run.result.final_state if run.result is not None else None
        refused_completion = (
            state == "TASK_STATE_COMPLETED"
            and final_state in {FINAL_STATE_TASK_FAILED, FINAL_STATE_TASK_REJECTED}
        )
        if state in _WORKING_TASK_STATES and not refused_completion:
            status = STAGE_PASSED
            message = ""
        elif refused_completion:
            # The vendor reports a refusal as TASK_STATE_COMPLETED; the run layer already
            # downgraded it to task_failed. A green card here would be a false acceptance.
            status = STAGE_FAILED
            message = messages.get("refused_completion", final_state=final_state)
        elif state in _FAILED_TASK_STATES:
            status = STAGE_FAILED
            message = messages.get("task_state_is", state=state)
        elif run.status == STATUS_FAILED:
            # The flow failed after the last observed Task state; that state alone must not turn
            # the row into "still working".
            status = STAGE_ERROR
            message = run.error or messages.get("run_failed")
        elif state == TASK_STATE_SUBMITTED:
            status = STAGE_WAITING
            message = messages.get(
                "task_stuck_submitted", seconds=f"{working_timeout_seconds:g}"
            )
        elif state == "TASK_STATE_INPUT_REQUIRED":
            status = STAGE_WAITING
            message = messages.get("task_negotiating")
        else:
            final_state = run.result.final_state if run.result is not None else None
            if final_state == "task_completed":
                status = STAGE_PASSED
                message = messages.get("completed_via_final_state")
            elif final_state in {"task_failed", "task_rejected"}:
                status = STAGE_FAILED
                message = messages.get("run_final_state_is", final_state=final_state)
            else:
                status = STAGE_WAITING
                message = messages.get(
                    "no_working_observed", seconds=f"{working_timeout_seconds:g}"
                ) if run.finished() else messages.get("no_working_observed_short")
        return StageResult(
            "working",
            status,
            message,
            expected=messages.get("expected_working_or_completed"),
            actual=actual,
            duration_ms=int(round((time.monotonic() - started) * 1000)),
        )

    async def _wait_for_acceptance_state(
        self, run: RunState, timeout_seconds: float, *, require_evidence: bool = False
    ) -> str | None:
        """Poll until the Task identity and an accepted or failed state are observed.

        A Task only proves the downstream accepted the request once its status state arrives;
        ``INPUT_REQUIRED`` means the negotiation is still in progress. ``require_evidence`` keeps
        observing a bare ``SUBMITTED`` until the vendor pushes its success copy, or until the Task
        turns terminal or the run ends, so an acknowledgement is never reported as an acceptance.
        """
        deadline = time.monotonic() + max(0.1, timeout_seconds)
        while time.monotonic() < deadline:
            state = run.latest_task_state
            if state in _FAILED_TASK_STATES:
                return state
            if (
                run.remote_task_id
                and run.remote_context_id
                and state in _ACCEPTED_TASK_STATES
                and (not require_evidence or _acceptance_evidence(run, state))
            ):
                return state
            if run.finished():
                return state
            await asyncio.sleep(_POLL_SECONDS)
        return run.latest_task_state

    async def _wait_for_working_state(
        self, run: RunState, timeout_seconds: float
    ) -> str | None:
        """Poll the observed Task state until it leaves SUBMITTED, the run ends or time is up."""
        deadline = time.monotonic() + max(1.0, timeout_seconds)
        while time.monotonic() < deadline:
            state = run.latest_task_state
            if state in _WORKING_TASK_STATES or state in _FAILED_TASK_STATES:
                return state
            if run.finished():
                # A task-carried negotiation can still be INPUT_REQUIRED right after the accept;
                # keep polling rather than reporting a premature "waiting".
                return state
            await asyncio.sleep(_POLL_SECONDS)
        return run.latest_task_state

    async def _wait_for_run(self, run: RunState, timeout_seconds: float) -> bool:
        deadline = time.monotonic() + max(0.1, timeout_seconds)
        while not run.finished():
            if time.monotonic() >= deadline:
                return True
            await asyncio.sleep(_POLL_SECONDS)
        return False

    async def _wait_for_span(self, run: RunState, kind: str, timeout_seconds: float) -> bool:
        deadline = time.monotonic() + max(0.1, timeout_seconds)
        while time.monotonic() < deadline:
            if any(span.get("kind") == kind for span in run.traces):
                return True
            if run.finished():
                return any(span.get("kind") == kind for span in run.traces)
            await asyncio.sleep(_POLL_SECONDS)
        return any(span.get("kind") == kind for span in run.traces)

    async def _wait_for_negotiation(
        self, run: RunState, expected_rounds: int, timeout_seconds: float
    ) -> None:
        """Wait for the expected rounds, or for the first response when none is expected.

        A case that expects no negotiation still has to observe the first response: a proposal
        there is a failure and the Task identity there ends the item. Waiting for the run to
        finish instead is wrong for a long task — the run stays alive while the remote Task
        executes, so the item would sit for the whole timeout.
        """
        deadline = time.monotonic() + max(0.1, timeout_seconds)
        while time.monotonic() < deadline:
            if expected_rounds > 0:
                if len(run.proposals) >= expected_rounds:
                    return
            elif run.proposals or run.remote_task_id or run.remote_context_id:
                return
            if run.finished():
                return
            await asyncio.sleep(_POLL_SECONDS)

    async def _collect_metrics(
        self,
        item: CaseItem,
        run: RunState,
        case: Mapping[str, Any] | None = None,
        messages: TesterMessages | None = None,
    ) -> None:
        if messages is None:
            messages = self._messages
        if self._metrics_grace_seconds:
            await asyncio.sleep(self._metrics_grace_seconds)
        # The export evidence does not depend on the metrics pipeline; collect it even when the
        # metrics builder fails, because a missing answer is worse than missing token counts.
        self._collect_run_evidence(item, run, case, messages)
        # The intent judge is async and owns the only model call this stage may make, so it runs
        # after the synchronous evidence collection has frozen ``refs``/the raw request. A failure
        # in the evidence/judging code must stay best effort, never turn the item into an error.
        try:
            await self._judge_server_intent(item, run, case, messages)
        except Exception:  # noqa: BLE001 - observation must not change the protocol result
            _LOGGER.warning("tester intent judge failed for run %s", run.run_id, exc_info=True)
            item.stages.setdefault(
                "intent", StageResult(stage="intent", status=STAGE_RECORDED)
            )
        try:
            metrics = build_run_metrics(
                run_id=run.run_id,
                conversation_id=run.conversation_id,
                protocol=run.protocol,
                scenario=run.scenario,
                mode=run.mode,
                transport=run.transport,
                language=self._language,
                model_mode=self._model_mode,
                model_label=self._model_label or None,
                status=run.status,
                transcript=run.transcript,
                result=run.result,
                spans=run.traces,
                expected=None,
                error=run.error,
            )
        except Exception:  # noqa: BLE001 - metrics must never fail the item
            _LOGGER.warning("tester metrics failed for run %s", run.run_id, exc_info=True)
            return
        tokens = metrics.get("tokens")
        item.tokens = dict(tokens) if isinstance(tokens, Mapping) else {}

    def _collect_reference(
        self, item: CaseItem, case: Mapping[str, Any] | None = None
    ) -> None:
        """Freeze the case metadata and expectations the arbitration export needs.

        Called twice per item: once right after the SDK stages (a ``validate``-only batch never
        starts a run, but must still export intent and reference values) and once at metrics
        collection time. ``case`` is the effective case: for a blocked case with a follow-up it
        is the replacement request, so the export shows what was actually sent.
        """
        if case is None:
            case = self._catalog.get(item.case_id)
        if case is None:
            return
        expect = case.get("expect")
        protocols = expect.get("protocols") if isinstance(expect, Mapping) else None
        # A pure item is judged against its own A2A block; reading the A2A-T block would export
        # another protocol's round count and final state.
        protocol_block = (
            protocols.get(item.protocol) if isinstance(protocols, Mapping) else None
        )
        block = protocol_block if isinstance(protocol_block, Mapping) else {}
        negotiation = block.get("negotiation")
        negotiation_block = negotiation if isinstance(negotiation, Mapping) else {}
        raw_intent = expect.get("intent") if isinstance(expect, Mapping) else None
        item.refs = {
            "caseName": str(case.get("name") or ""),
            "operation": str(case.get("operation") or ""),
            "designer": str(case.get("designer") or ""),
            "qaId": f"TC{item.case_id:08d}",
            "uid": str(case.get("uid") or ""),
            "taskT": str(block.get("task_t") or ""),
            # The operator's original request, even when ``case`` is the derived replacement
            # document whose expectations judge that replacement.
            "userInput": item.run_request or str(case.get("user_input") or ""),
            "expectedIntent": (
                {str(key): value for key, value in raw_intent.items()}
                if isinstance(raw_intent, Mapping)
                else {}
            ),
            "negotiationExpected": bool(negotiation_block.get("expected")),
            "expectedRounds": int(negotiation_block.get("expected_rounds") or 0),
            "expectedKinds": list(negotiation_block.get("kinds") or []),
            "expectedFinalState": block.get("final_state"),
            # The recorded A2A client reply (P column) is reference text, not replayed; it stays
            # in the export as the reply the agent's own answer is reviewed against.
            "clientReplies": (
                pure_answers(case) if item.protocol == PROTOCOL_A2A else []
            ),
        }
        stage = item.stages.get("intent")
        # Only the A2A-T SDK stage is our own extraction. A pure-A2A intent stage holds the
        # downstream's payload, which the ``serverIntent`` column already owns.
        if (
            item.protocol != PROTOCOL_A2A
            and stage is not None
            and isinstance(stage.actual, Mapping)
        ):
            item.intent = {str(key): value for key, value in stage.actual.items()}

    def _collect_run_evidence(
        self,
        item: CaseItem,
        run: RunState,
        case: Mapping[str, Any] | None = None,
        messages: TesterMessages | None = None,
    ) -> None:
        """Collect the trace id, the operator request and the reference answer of one run."""
        if messages is None:
            messages = self._messages
        try:
            item.trace_id = run.trace_id or run.run_id
            if item.error is None:
                # The flow finished unfinished on purpose (an exhausted answer script) or failed
                # outright. Either reason belongs in the result column so a batch run does not
                # need the drawer to read it.
                result_error = run.result.error if run.result is not None else None
                item.error = result_error or run.error
            langfuse_url = _langfuse_url(run)
            if langfuse_url:
                item.langfuse_url = langfuse_url
            langfuse_warning = _langfuse_warning(run)
            if langfuse_warning:
                item.langfuse_warning = langfuse_warning
            langfuse_trace_id = _langfuse_trace_id(run)
            if langfuse_trace_id:
                item.langfuse_trace_id = langfuse_trace_id
            self._collect_reference(item, case)
            correction = _correction_detail(run)
            if correction is not None:
                corrected = correction.get("correctedRequest")
                if isinstance(corrected, str) and corrected.strip():
                    # The operator's request stays ``userInput``; this names what the run
                    # actually dispatched after its own gate blocked.
                    item.refs["dispatchedRequest"] = corrected.strip()
            raw_request = run.raw_request or str(item.refs.get("userInput") or "")
            item.intent_meta["rawRequest"] = raw_request
            item.intent_meta["normalizedText"] = run.normalized_request or raw_request
            # The arbitration intent is what the downstream understood, not our own extraction.
            # ``item.intent`` keeps the SDK document as auxiliary evidence. The server-intent
            # stage is judged asynchronously in ``_collect_metrics``.
            self._judge_run_task_t(item, run, case, messages)
            self._judge_run_validation(item, run, case, messages)
            traceparent, traceparent_source = read_traceparent(
                self._history_dir.parent if self._history_dir is not None else None,
                run.run_id,
                trace_id=item.trace_id,
            )
            item.intent_meta["traceparentSource"] = traceparent_source
            item.answer = build_answer(
                run, refs=item.refs, traceparent=traceparent, messages=messages
            )
        except Exception:  # noqa: BLE001 - the export evidence must never fail the item
            _LOGGER.warning("tester export evidence failed for run %s", run.run_id, exc_info=True)

    async def _judge_server_intent(
        self,
        item: CaseItem,
        run: RunState,
        case: Mapping[str, Any] | None,
        messages: TesterMessages | None = None,
    ) -> None:
        """Re-judge the ``intent`` stage against the downstream's own reading.

        The case asks what the server understood, not what our SDK extracted from the Task-T. A
        full-flow item has no SDK preflight stage any more, so the stage is created here and its
        actual is the downstream's own payload (``TASK_STATE_SUBMITTED``); an SDK-only batch keeps
        the SDK extraction it already recorded. ``item.intent`` is only filled from an SDK stage,
        so the auxiliary SDK column never duplicates the server payload.

        The comparison is structured first (exact/normalized notation) and only a genuine residual
        reaches the independent judge — one model call for every residual field. A missing reading
        or an unavailable judge stays record-only, never a fabricated pass or failure.
        """
        if messages is None:
            messages = self._messages
        payload = run.server_intent
        item.server_intent = dict(payload) if isinstance(payload, Mapping) else None
        item.intent_meta["source"] = (
            "server"
            if item.server_intent is not None
            else ("sdk" if item.intent is not None else "")
        )
        stage = item.stages.get("intent")
        if stage is None and item.protocol == PROTOCOL_A2A and item.server_intent is not None:
            # Pure A2A runs no SDK stages, but a direct vendor payload is still the downstream's
            # own reading. Give that payload a comparison stage; no payload keeps the old shape.
            stage = StageResult(stage="intent", status=STAGE_RECORDED)
            item.stages["intent"] = stage
        if stage is None and item.protocol == PROTOCOL_A2A_T:
            # A full-flow A2A-T item has no SDK preflight any more: the run generates the Task-T
            # itself and the downstream's payload is the only reading an arbitration can judge.
            # No payload keeps the stage record-only, never red.
            stage = StageResult(stage="intent", status=STAGE_RECORDED)
            item.stages["intent"] = stage
        if stage is None or case is None:
            return
        expected = item.refs.get("expectedIntent")
        has_expected = isinstance(expected, Mapping) and bool(expected)
        if item.server_intent is None:
            # Our own RAN Agent accepts a Task without returning its reading; that is evidence
            # missing, not a wrong understanding, so it must not turn the row red.
            verdict = await judge_intent(
                expected if isinstance(expected, Mapping) else {},
                None,
                judge=None,
            )
            item.intent_verdict = verdict.to_dict()
            stage.status = STAGE_RECORDED
            stage.actual = None
            stage.diffs = []
            stage.message = messages.get("no_server_intent")
            return
        stage.actual = dict(item.server_intent)
        if has_expected and stage.expected is None:
            stage.expected = dict(expected)
        stage.diffs = (
            [diff.to_dict() for diff in compare_intent(expected, item.server_intent)]
            if has_expected
            else []
        )
        if item.protocol == PROTOCOL_A2A and run.discovery != DISCOVERY_NMS:
            # The local hop owns a different business-intent payload; judge only a direct vendor
            # payload so a local hop never turns a case red on a key set it cannot reproduce.
            stage.status = STAGE_RECORDED
            stage.message = messages.get("a2a_proxy_server_intent")
            return
        if not has_expected:
            stage.status = STAGE_RECORDED
            stage.message = messages.get("no_expected_intent")
            return
        if not _assertion_enabled(case, "", "intent"):
            stage.status = STAGE_RECORDED
            stage.message = messages.get("assertion_off")
            return
        assert isinstance(expected, Mapping)
        verdict = await judge_intent(
            expected,
            item.server_intent,
            judge=self._judge,
            judge_label=self._judge_model_label,
            user_request=item.run_request or str(item.refs.get("userInput") or ""),
            proposal_summary=_intent_proposal_summary(run),
        )
        item.intent_verdict = verdict.to_dict()
        if verdict.status == STATUS_PASS:
            stage.status = STAGE_PASSED
            stage.message = ""
            # The model accepted the residual fields: the deterministic rows are no longer a
            # failure, so the drawer must not keep showing them as differences.
            stage.diffs = []
        elif verdict.status == STATUS_MISMATCH:
            stage.status = STAGE_FAILED
            stage.message = messages.get("server_intent_mismatch")
            stage.diffs = [
                {
                    "path": str(field.get("path") or ""),
                    "expected": "",
                    "actual": "",
                    "reason": str(field.get("reason") or ""),
                }
                for field in verdict.fields
                if field.get("equal") is False
            ] or stage.diffs
        else:
            stage.status = STAGE_RECORDED
            stage.message = verdict.reason or messages.get("intent_unjudged")
            item.needs_review = bool(verdict.residual)

    def _judge_run_task_t(
        self,
        item: CaseItem,
        run: RunState,
        case: Mapping[str, Any] | None,
        messages: TesterMessages | None = None,
    ) -> None:
        """Judge a full-flow run's Task-T against the case's expectation.

        A full-flow item skips the SDK preflight, so its Task-T is generated inside the run. The
        ``task_prompt_rendered`` span carries the prompt the SDK rendered and sent; comparing it
        with the case restores the section rows an SDK-only batch gets, without a model call. A
        difference is a warning, never a failed case: whether the case failed is decided by the
        downstream's own reading (``serverIntent``), not by our rendering. A case whose
        expectation is the post-negotiation Task-T, or that turns the assertion off, stays
        record-only. Missing evidence creates no stage rather than a fabricated one.
        """
        if messages is None:
            messages = self._messages
        if item.protocol != PROTOCOL_A2A_T or "task_t" in item.stages or case is None:
            return
        expected = item.refs.get("taskT")
        actual = _rendered_task_t(run)
        if not isinstance(expected, str) or not expected.strip() or actual is None:
            return
        diffs = [diff.to_dict() for diff in compare_task_t(expected, actual)]
        result = StageResult(
            "task_t",
            STAGE_PASSED,
            expected=expected,
            actual=actual,
            diffs=diffs,
        )
        if _negotiation_expected(case, PROTOCOL_A2A_T):
            # The case's Task-T is the post-negotiation one; the initial run prompt would be a
            # false failure.
            result.status = STAGE_RECORDED
            result.message = messages.get("negotiated_task_t")
        elif not _assertion_enabled(case, PROTOCOL_A2A_T, "task_t"):
            result.status = STAGE_RECORDED
            result.message = messages.get("assertion_off")
        elif diffs:
            result.status = STAGE_WARNING
            result.message = messages.get("task_t_mismatch")
        item.stages["task_t"] = result

    def _stamp_interception_miss(
        self,
        item: CaseItem,
        stages: Sequence[str],
        *,
        dispatched: bool,
        messages: TesterMessages | None = None,
    ) -> None:
        """Record an interception that did not hold; the replacement request is not executed.

        A blocked case is "intercepted first, then the replacement is sent". When the gate lets
        the original through, the case has nothing to judge — the corpus expectations describe
        the replacement — so the interception verdict is recorded and the item passes.
        ``dispatched`` says whether the original request itself went downstream (full-flow) or no
        run was started at all (SDK-only); the given stages are marked ``skipped`` so the unjudged
        segment does not read as missing evidence.
        """
        if messages is None:
            messages = self._messages
        key = "interception_miss_dispatched" if dispatched else "interception_missed"
        message = messages.get(key, detail=_interception_miss_detail(item, messages))
        for stage in stages:
            if stage not in item.stages:
                item.stages[stage] = StageResult(stage, STAGE_SKIPPED, message)
        if item.interception is not None:
            item.interception["followupSkipped"] = True
            if dispatched:
                # The original request is what the run dispatched; the review reads this flag to
                # record — never judge — the downstream evidence.
                item.interception["originalDispatched"] = True
            self._record_interception_miss(item, message)

    @staticmethod
    def _record_interception_miss(item: CaseItem, message: str) -> None:
        """Demote a missed interception's verdict to record-only in the stored record."""
        record = item.interception if isinstance(item.interception, Mapping) else {}
        stages = record.get("stages")
        validate = stages.get("validate") if isinstance(stages, Mapping) else None
        if not isinstance(validate, Mapping):
            return
        validate["status"] = STAGE_RECORDED
        validate["message"] = message

    def _stamp_followup_block(
        self,
        item: CaseItem,
        run: RunState | None = None,
        *,
        messages: TesterMessages | None = None,
    ) -> None:
        """Report a follow-up that did not get through differently from the expected block.

        A ``blocked_followup.input`` case *expects* its original request to be intercepted; the
        replacement request is the one the case actually asks to be sent. A replacement that is
        blocked again, or a run that ends before dispatching it, is therefore a defect of the case
        or of the SDK, not the expected interception. Naming the follow-up on the validation stage,
        the item error and the interception record keeps the row from reading like a normal block.
        """
        validate = item.stages.get("validate")
        verdict = _stage_verdict(validate)
        final_state = (
            run.result.final_state if run is not None and run.result is not None else None
        )
        if messages is None:
            messages = self._messages
        if verdict == "blocked":
            message = messages.get("followup_still_blocked")
        elif final_state in _PRE_SEND_TERMINAL_STATES:
            message = messages.get("followup_not_sent", final_state=final_state)
        else:
            return
        reason = _validation_reason(validate)
        if reason:
            message = messages.get("followup_detail", message=message, reason=reason)
        if validate is not None:
            validate.message = message
        if item.interception is not None:
            item.interception["followupBlocked"] = True
        item.error = (
            messages.get("followup_error_detail", message=message, error=item.error)
            if item.error
            else message
        )

    def _record_run_interception(
        self,
        item: CaseItem,
        run: RunState,
        case: Mapping[str, Any],
        messages: TesterMessages | None = None,
    ) -> bool:
        """Record the run's own gate as the interception segment of one blocked case.

        A blocked case is one run with two gate attempts: the original request is blocked, the
        operator's replacement is applied, and the gate runs again. Returns ``True`` when that
        block happened and the replacement was dispatched. ``False`` means the gate let the
        original through: the caller records the miss and treats the downstream evidence as
        record-only.
        """
        if messages is None:
            messages = self._messages
        correction = _correction_detail(run)
        original = str(item.run_request or case.get("user_input") or "")
        if correction is None:
            detail = _first_validation_detail(run)
            result = (
                self._validation_stage(case, detail, messages)
                if detail is not None
                else StageResult(
                    "validate",
                    STAGE_ERROR,
                    messages.get("validate_no_evidence"),
                    expected="blocked",
                    actual=None,
                )
            )
            item.interception = {
                "input": original,
                "followupInput": followup_input(case) or "",
                "stages": {"validate": result.to_dict()},
            }
            return False
        raw_errors = correction.get("errors")
        slot_errors = (
            [dict(error) for error in raw_errors] if isinstance(raw_errors, list) else []
        )
        detail: dict[str, object] = {
            "verdict": "blocked",
            "blocking": True,
            "reason": str(correction.get("reason") or ""),
            "code": str(correction.get("reason") or ""),
            "slotErrors": slot_errors,
        }
        result = self._validation_stage(case, detail, messages)
        item.interception = {
            "input": str(correction.get("originalRequest") or original),
            "followupInput": str(correction.get("correctedRequest") or ""),
            "stages": {"validate": result.to_dict()},
        }
        return True

    @staticmethod
    def _demote_missed_interception(
        item: CaseItem, case: Mapping[str, Any], messages: TesterMessages
    ) -> None:
        """Record — never judge — the downstream stages of a run whose interception missed.

        The corpus expectations describe the replacement request, which a miss never dispatches,
        so the original request's downstream evidence stays visible without failing more stages
        than the interception itself.
        """
        derived = original_dispatch_case(case)
        for stage_name, key in (
            ("intent", "intent"),
            ("send", "send"),
            ("task_t", "task_t"),
            ("validate", "validate"),
            ("negotiate", "negotiation"),
            ("final", "final_state"),
        ):
            stage = item.stages.get(stage_name)
            if stage is None:
                continue
            protocol = "" if key == "intent" else PROTOCOL_A2A_T
            item.stages[stage_name] = _apply_assertion(
                derived, protocol, key, stage, messages
            )

    @staticmethod
    def _validation_stage(
        case: Mapping[str, Any],
        detail: Mapping[str, object],
        messages: TesterMessages | None = None,
    ) -> StageResult:
        """Judge one observed validation verdict against the case's expectation."""
        if messages is None:
            messages = TesterMessages()
        verdict = str(detail.get("verdict") or "")
        expected = _case_validation_expectation(case)
        observed = detail.get("slotErrors")
        evidence = slot_error_evidence(
            case,
            expected,
            verdict,
            observed if isinstance(observed, list) else (),
        )
        merged = dict(detail)
        if evidence is not None:
            merged.update(evidence.to_detail())
        result = StageResult(
            "validate",
            STAGE_PASSED,
            expected=expected,
            actual=verdict,
            diffs=[dict(item) for item in evidence.diffs()] if evidence is not None else [],
            detail=merged,
        )
        if expected is None:
            result.status = STAGE_RECORDED
            result.message = messages.get("no_expected_validation")
        elif not _assertion_enabled(case, PROTOCOL_A2A_T, "validate"):
            result.status = STAGE_RECORDED
            result.message = messages.get("assertion_off")
        elif not _validation_matches(expected, verdict) or (
            evidence is not None and not evidence.matched()
        ):
            result.status = STAGE_FAILED
            result.message = messages.get("validation_mismatch")
        return result

    def _judge_run_validation(
        self,
        item: CaseItem,
        run: RunState,
        case: Mapping[str, Any] | None,
        messages: TesterMessages | None = None,
    ) -> None:
        """Judge the run's pre-send Task-T validation, the "拦截" stage of a full-flow item.

        A full-flow item skips the SDK preflight; the run validates the Task-T itself before
        sending and emits a ``task_validation`` span with the verdict and slot errors. With a
        correction the run emits two verdicts; the last one is the request actually dispatched.
        No validation evidence creates no stage, and a case without an expectation stays
        record-only.
        """
        if messages is None:
            messages = self._messages
        if item.protocol != PROTOCOL_A2A_T or "validate" in item.stages or case is None:
            return
        detail = _run_validation_detail(run)
        if detail is None:
            return
        item.stages["validate"] = self._validation_stage(case, detail, messages)

    async def _ensure_prompt_client(self, model_mode: str | None = None) -> Any:
        """Return the pooled SDK prompt client."""
        if self._prompt_client is not None:
            return self._prompt_client
        key = model_mode or self._model_mode or MODEL_MODE_LIVE
        if key in self._prompt_errors:
            raise RuntimeError(self._prompt_errors[key])
        cached = self._prompt_clients.get(key)
        if cached is not None:
            return cached
        # The concurrent pool builds under the same lock: the provider registrations inside
        # ``build_client_with_env`` are check-then-register and raise on a duplicate.
        async with self._prompt_build_lock:
            cached = self._prompt_clients.get(key)
            if cached is not None:
                return cached
            if key in self._prompt_errors:
                raise RuntimeError(self._prompt_errors[key])
            try:
                client, directory = await asyncio.to_thread(
                    build_client_with_env,
                    self._env_path,
                )
            except Exception as exc:  # noqa: BLE001 - reported per case
                self._prompt_errors[key] = f"{type(exc).__name__}: {exc}"
                raise
            self._prompt_env_dirs.append(directory)
            self._prompt_clients[key] = client
            return client

    async def _run_case_with_client(
        self,
        case: Mapping[str, Any],
        stop_after: str,
        model_mode: str | None,
        *,
        concurrent: bool,
    ) -> CaseOutcome:
        """Run the SDK stages on a client that is not borrowed by another thread.

        The sequential path and any injected client keep the historical single client behind
        ``_sdk_lock``. The concurrent path checks a client out of the per-mode pool, so no two
        runner threads ever share one ``A2ATClient``.
        """
        runner = run_case
        if not concurrent or self._prompt_client is not None:
            async with self._sdk_lock:
                client = await self._ensure_prompt_client(model_mode)
                return await asyncio.to_thread(runner, case, client, stop_after=stop_after)
        await self._sdk_slots.acquire()
        self._sdk_inflight += 1
        try:
            client = await self._checkout_prompt_client(model_mode)
        except BaseException:
            self._sdk_inflight -= 1
            self._sdk_slots.release()
            raise
        # ``to_thread`` cannot stop its thread. A task plus ``shield`` lets the item unwind on
        # cancellation while the worker finishes, and keeps the slot held until it really does.
        task = asyncio.create_task(
            asyncio.to_thread(runner, case, client, stop_after=stop_after),
            name="tester-sdk-stage",
        )

        def _finished(finished: asyncio.Task[CaseOutcome]) -> None:
            self._sdk_inflight -= 1
            self._sdk_slots.release()
            if not finished.cancelled():
                # Consume a late exception so a cancelled item does not log "never retrieved".
                finished.exception()

        task.add_done_callback(_finished)
        try:
            outcome = await asyncio.shield(task)
        except asyncio.CancelledError:
            # The thread may still be inside this client: never return it to the pool.
            raise
        except BaseException:
            # The thread finished with an error, so the client is idle again.
            self._checkin_prompt_client(model_mode, client)
            raise
        self._checkin_prompt_client(model_mode, client)
        return outcome

    async def _checkout_prompt_client(self, model_mode: str | None) -> Any:
        """Return an idle pooled client, building one under the build lock when none is free."""
        key = model_mode or self._model_mode or MODEL_MODE_LIVE
        queue = self._prompt_pool.setdefault(key, asyncio.Queue())
        try:
            return queue.get_nowait()
        except asyncio.QueueEmpty:
            pass
        # ``build_client`` registers the traced providers with a check-then-register pattern that
        # raises on a duplicate, so two parallel first-time builds must not run at once.
        async with self._prompt_build_lock:
            try:
                return queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
            if key in self._prompt_errors:
                raise RuntimeError(self._prompt_errors[key])
            try:
                client, directory = await asyncio.to_thread(
                    build_client_with_env,
                    self._env_path,
                )
            except Exception as exc:  # noqa: BLE001 - reported per case
                self._prompt_errors[key] = f"{type(exc).__name__}: {exc}"
                raise
            if directory is not None:
                self._prompt_env_dirs.append(directory)
            return client

    def _checkin_prompt_client(self, model_mode: str | None, client: Any) -> None:
        """Return a pooled client so the next item of this mode can reuse it."""
        key = model_mode or self._model_mode or MODEL_MODE_LIVE
        self._prompt_pool.setdefault(key, asyncio.Queue()).put_nowait(client)

    def _close_prompt_pool(self) -> None:
        """Drop the pooled clients and remove the temporary SDK env directories they own."""
        for directory in self._prompt_env_dirs:
            shutil.rmtree(directory, ignore_errors=True)
        self._prompt_env_dirs.clear()
        self._prompt_pool.clear()

    def _item_status(self, item: CaseItem, messages: TesterMessages | None = None) -> str:
        if messages is None:
            messages = self._messages
        statuses = [result.status for result in item.stages.values()]
        if item.interception is not None:
            statuses.extend(_interception_statuses(item.interception))
        if any(status == STAGE_ERROR for status in statuses):
            return CASE_ERROR
        if any(status == STAGE_FAILED for status in statuses):
            return CASE_FAILED
        if any(status == STAGE_WAITING for status in statuses):
            return CASE_PARTIAL
        if any(status == STAGE_WARNING for status in statuses):
            # A deviation the client did not cause still needs a human to confirm it. The stage is
            # yellow in the console; the row must not read as a clean pass next to it.
            return CASE_PARTIAL
        if _stops_on_a_vendor_defect(item.error):
            # The vendor broke the exchange (an empty INPUT_REQUIRED): the client cannot prove its
            # work, so the row is an error of the system under test, never an inconclusive partial.
            return CASE_ERROR
        if item.needs_review:
            # A residual the judge could not decide is unproven, not green.
            return CASE_PARTIAL
        if _terminal_state_unconfirmed(item, messages):
            # The corpus named a terminal state and the run never observed it. "Recorded" keeps the
            # evidence visible; it must not turn the row green.
            return CASE_PARTIAL
        if item.error:
            # The run failed, but its only red stage was recorded (assertion off). A green row
            # would overstate what the batch proved.
            return CASE_PARTIAL
        return CASE_PASSED

    # ------------------------------------------------------------------
    # Persistence and snapshots
    # ------------------------------------------------------------------

    def status_snapshot(self) -> dict[str, object]:
        """Return the process-wide running status of every browser's work.

        This is the one view that deliberately ignores the owner filter: the status page exists so
        an operator can see whether someone else already occupies the shared SDK pool or the NMS
        quota before starting another batch. It is read-only and never touches a batch.
        """
        running = [
            batch for batch in self._batches.values() if batch.status == BATCH_RUNNING
        ]
        running.sort(key=lambda batch: batch.started_at or batch.created_at)
        runs = self._runs.live_runs()
        return {
            "generatedAt": _now_iso(),
            "process": {
                "pid": os.getpid(),
                "startedAt": self._started_at,
                "uptimeSeconds": _elapsed_seconds(self._started_at),
                "modelMode": self._model_mode,
                "modelLabel": self._model_label,
                "nms": self._nms,
                "serverUrl": self._server_url,
                "runsDir": str(self._history_dir.parent) if self._history_dir is not None else "",
                "maxConcurrency": MAX_CONCURRENCY,
                "sdkInFlight": self._sdk_inflight,
                "sdkSerialBusy": self._sdk_lock.locked(),
                "activeItems": sum(len(batch.active_indices) for batch in running),
                "runningBatches": len(running),
                "draftBatches": sum(
                    1 for batch in self._batches.values() if batch.status == BATCH_DRAFT
                ),
                "keptTaskBatches": sum(
                    1 for batch in self._batches.values() if batch.keep_tasks
                ),
                "liveRuns": len(runs),
                "runsHoldingTask": sum(1 for run in runs if run.remote_task_id),
                "detachedRuns": sum(1 for run in runs if run.detached),
            },
            "batches": [_status_batch(batch) for batch in running],
            "runs": [_status_run(run) for run in runs],
            "recent": _status_recent(self._batches.values()),
        }

    def _persist(self, batch: _Batch) -> None:
        if self._history_dir is None:
            return
        try:
            self._history_dir.mkdir(parents=True, exist_ok=True)
            document = self._snapshot(batch)
            target = self._history_dir / f"{batch.batch_id}.json"
            temporary = target.with_suffix(".json.tmp")
            temporary.write_text(json.dumps(document, ensure_ascii=False, indent=2), encoding="utf-8")
            temporary.replace(target)
        except OSError as exc:
            _LOGGER.warning("tester batch %s could not be persisted: %s", batch.batch_id, exc)
            batch.error = self._batch_messages(batch).get("persist_failed", error=exc)

    def _load(self, batch_id: str) -> _Batch | None:
        if self._history_dir is None:
            return None
        path = self._history_dir / f"{batch_id}.json"
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if not isinstance(document, Mapping) or document.get("schema") not in _SCHEMAS:
            return None
        raw_items = document.get("items")
        if not isinstance(raw_items, list):
            return None
        items = [
            CaseItem(
                case_id=int(item.get("caseId") or 0),
                name=str(item.get("name") or ""),
                protocol=str(item.get("protocol") or PROTOCOL_A2A_T),
                round_index=_clamp_rounds(item.get("round")),
                status=str(item.get("status") or CASE_PENDING),
                stages=_load_stages(item.get("stages")),
                run_id=item.get("runId") if isinstance(item.get("runId"), str) else None,
                duration_ms=item.get("durationMs") if isinstance(item.get("durationMs"), int) else None,
                tokens=dict(item.get("tokens") or {}),
                error=item.get("error") if isinstance(item.get("error"), str) else None,
                baseline=dict(item.get("baseline") or {}) if isinstance(item.get("baseline"), Mapping) else None,
                scratch=item.get("scratch") if isinstance(item.get("scratch"), str) else None,
                attempts=_load_attempts(item.get("attempts")),
                trace_id=item.get("traceId") if isinstance(item.get("traceId"), str) else "",
                langfuse_url=(
                    item.get("langfuseUrl") if isinstance(item.get("langfuseUrl"), str) else ""
                ),
                langfuse_warning=(
                    item.get("langfuseWarning")
                    if isinstance(item.get("langfuseWarning"), str)
                    else ""
                ),
                langfuse_trace_id=(
                    item.get("langfuseTraceId")
                    if isinstance(item.get("langfuseTraceId"), str)
                    else ""
                ),
                intent=_optional_mapping(item.get("intent")),
                server_intent=_optional_mapping(item.get("serverIntent")),
                intent_meta=_mapping_or_empty(item.get("intentMeta")),
                refs=_mapping_or_empty(item.get("refs")),
                answer=_optional_mapping(item.get("answer")),
                interception=_optional_mapping(item.get("interception")),
                client_quality=_optional_mapping(item.get("clientQuality")),
                intent_verdict=_optional_mapping(item.get("intentVerdict")),
                needs_review=bool(item.get("needsReview")),
            )
            for item in raw_items
            if isinstance(item, Mapping)
        ]
        defaults = self._protocol_defaults.get(PROTOCOL_A2A_T, {})
        loaded_language = _language_or_default(document.get("language"))
        loaded_version = _optional_text(document.get("principlesVersion"))
        loaded_text = _optional_text(document.get("principlesText"))
        if not loaded_text:
            try:
                loaded_principles = load_principles(loaded_version or None)
            except (FileNotFoundError, ValueError):
                raise
            else:
                loaded_version = loaded_principles.version
                loaded_text = loaded_principles.text
        batch = _Batch(
            batch_id=str(document.get("batchId") or batch_id),
            case_ids=[int(value) for value in document.get("caseIds", []) if isinstance(value, int)],
            protocols=[str(value) for value in document.get("protocols", [])],
            stop_after=str(document.get("stopAfter") or "accepted"),
            timeout_seconds=_optional_float(document.get("timeoutSeconds")),
            working_timeout_seconds=_optional_float(document.get("workingTimeoutSeconds")),
            principles_version=loaded_version,
            principles_text=loaded_text,
            nms_version=_optional_text(document.get("nmsVersion")),
            # A multi-round batch never keeps Tasks; an edited document cannot re-enable it.
            keep_tasks=bool(document.get("keepTasks"))
            and _clamp_rounds(document.get("rounds")) == DEFAULT_ROUNDS,
            concurrency=_clamp_concurrency(document.get("concurrency")),
            rounds=_clamp_rounds(document.get("rounds")),
            current_round=_clamp_rounds(document.get("currentRound")),
            transport=str(document.get("transport") or defaults.get("transport", "sse")),
            discovery=str(document.get("discovery") or defaults.get("discovery", "direct")),
            # All pre-feature batches were created in Chinese; an unknown value falls back too.
            language=loaded_language,
            items=items,
            excluded_case_ids=[
                int(value)
                for value in document.get("excludedCaseIds", [])
                if isinstance(value, int)
            ],
            owner_id=str(document.get("ownerId") or ""),
            owner_source=_mapping_or_empty(document.get("ownerSource")),
            status=str(document.get("status") or BATCH_DRAFT),
            created_at=str(document.get("createdAt") or _now_iso()),
            started_at=document.get("startedAt") if isinstance(document.get("startedAt"), str) else None,
            finished_at=document.get("finishedAt") if isinstance(document.get("finishedAt"), str) else None,
        )
        if batch.status == BATCH_RUNNING:
            restart_message = TesterMessages(loaded_language).get("restart_incomplete")
            for item in batch.items:
                if item.status in {CASE_PENDING, CASE_RUNNING}:
                    item.status = CASE_CANCELLED
                    item.error = restart_message
            batch.status = BATCH_CANCELLED
            batch.finished_at = _now_iso()
        self._batches[batch_id] = batch
        return batch

    def _item_snapshot(
        self, item: CaseItem, messages: TesterMessages | None = None
    ) -> dict[str, object]:
        """Return one item, recovering Langfuse state that resolved after the evidence pass.

        The exporter finishes (and resolves the URL, and probes ingestion) after the item's
        evidence is collected, so every snapshot re-reads the live run instead of freezing the
        values seen at collection time. ``messages`` is the batch's message set (D9).
        """
        data = item.to_dict(messages)
        if not item.run_id:
            return data
        getter = getattr(self._runs, "get", None)
        run = getter(item.run_id) if callable(getter) else None
        if run is None:
            return data
        url = _langfuse_url(run)
        if url and url != item.langfuse_url:
            item.langfuse_url = url
            data["langfuseUrl"] = url
        warning = _langfuse_warning(run)
        if warning and warning != item.langfuse_warning:
            item.langfuse_warning = warning
            data["langfuseWarning"] = warning
        trace_id = _langfuse_trace_id(run)
        if trace_id and trace_id != item.langfuse_trace_id:
            item.langfuse_trace_id = trace_id
            data["langfuseTraceId"] = trace_id
        return data

    def _snapshot(self, batch: _Batch) -> dict[str, object]:
        messages = self._batch_messages(batch)
        items = [self._item_snapshot(item, messages) for item in batch.items]
        return {
            "schema": SCHEMA,
            "batchId": batch.batch_id,
            "status": batch.status,
            "language": batch.language,
            "caseIds": list(batch.case_ids),
            "excludedCaseIds": list(batch.excluded_case_ids),
            "protocols": list(batch.protocols),
            "stopAfter": batch.stop_after,
            "timeoutSeconds": batch.timeout_seconds,
            "workingTimeoutSeconds": batch.working_timeout_seconds,
            "principlesVersion": batch.principles_version,
            "principlesText": batch.principles_text,
            "nmsVersion": batch.nms_version,
            # One batch must be reproducible: which models, environment, principles version and
            # NMS the operator intended. The report exports this block as its own sheet.
            "plan": {
                "protocols": list(batch.protocols),
                "environment": {
                    "transport": batch.transport,
                    "discovery": batch.discovery,
                    "nms": self._nms,
                },
                "negotiationModel": self._model_label,
                "judgeModel": self._judge_model_label,
                "principlesVersion": batch.principles_version,
                "nmsVersion": batch.nms_version,
                "stopAfter": batch.stop_after,
                "rounds": batch.rounds,
            },
            # The report groups evidence by model and by downstream, so the batch records the
            # process identity it ran against. Older files lack these keys.
            "modelLabel": self._model_label,
            "modelMode": self._model_mode,
            "nms": self._nms,
            "keepTasks": batch.keep_tasks,
            "concurrency": batch.concurrency,
            "rounds": batch.rounds,
            "currentRound": batch.current_round,
            "roundSummaries": _round_summaries(batch.items, batch.rounds),
            # Cross-round stability is only meaningful with at least two rounds; the console
            # hides it when ``total`` is zero.
            "crossRound": _cross_round(batch.items),
            "activeIndices": list(batch.active_indices),
            # Reruns may be queued while the main pass still runs; the console marks those rows
            # and keeps their buttons usable instead of disabling the whole table.
            "rerunQueue": list(batch.rerun_queue),
            "rerunActive": batch.rerun_active,
            "mainActive": batch.main_active,
            "transport": batch.transport,
            "discovery": batch.discovery,
            "resolvedRunner": {
                    "timeoutSeconds": batch.timeout_seconds,
                "workingTimeoutSeconds": batch.working_timeout_seconds,
            },
            "ownerId": batch.owner_id,
            "ownerSource": dict(batch.owner_source),
            "createdAt": batch.created_at,
            "startedAt": batch.started_at,
            "finishedAt": batch.finished_at,
            "currentIndex": batch.current_index,
            "error": batch.error,
            "items": items,
            "summary": _summary(batch.items),
            # A paired batch exists to compare the two protocols case by case; the flat item list
            # makes that join manual, so the snapshot carries it ready-made.
            "pairs": _pairs(batch.items),
        }


def _round_groups(batch: _Batch, selected: Sequence[int]) -> list[list[int]]:
    """Split the selected items into their rounds, preserving round and catalog order.

    A single-round batch returns one group holding every selected index, which is exactly the
    list the executor used before rounds existed.
    """
    if batch.rounds <= DEFAULT_ROUNDS:
        return [list(selected)]
    groups: dict[int, list[int]] = {}
    for index in selected:
        groups.setdefault(batch.items[index].round_index, []).append(index)
    return [group for _, group in sorted(groups.items())]


#: Trace kinds the client runtime emits for a decision (the decision's action is the kind).
_DECISION_TRACE_KINDS: Final[frozenset[str]] = frozenset(
    {"accept", "reject", "abort", "counter"}
)
#: Outbound performative that proves a decision was actually dispatched.
_PERFORMATIVE_BY_ACTION: Final[dict[str, str]] = {
    "accept": "ACCEPT",
    "reject": "REJECT",
    "abort": "ABORT",
    "counter": "PROPOSE",
}


def _round_number(value: object) -> int | None:
    """Return a positive 1-based round number, or ``None`` when the value is not one."""
    if isinstance(value, int) and not isinstance(value, bool) and value >= 1:
        return value
    return None


def _decision_text(value: object) -> str:
    """Return the best reply text a decision carries, empty when it carries none."""
    if not isinstance(value, Mapping):
        return ""
    for key in ("feasibilitySummary", "confirmedIntent", "counterProposal"):
        text = value.get(key)
        if isinstance(text, str) and text.strip():
            return text.strip()
    rationale = value.get("rationale")
    if isinstance(rationale, str) and rationale.strip():
        return rationale.strip()
    filled = value.get("filledParams")
    if isinstance(filled, Mapping) and filled:
        return "; ".join(f"{key}={item_value}" for key, item_value in sorted(filled.items()))
    return ""


def _intent_proposal_summary(run: RunState) -> str:
    """Return the negotiation proposals of one run as business text for the intent judge."""
    parts: list[str] = []
    for proposal in run.proposals or []:
        content = getattr(proposal, "content", "")
        if isinstance(content, str) and content.strip():
            parts.append(content.strip())
    return "\n".join(parts)


def _client_quality_observations(run: RunState) -> list[ClientQualityObservation]:
    """Reduce one run's negotiation evidence to the decisions the quality judge reads.

    The real client flow records a decision as a trace whose ``kind`` is the action and a
    ``message_sent`` trace when it dispatches it. A test harness may only fill
    ``RunResult.decisions``; the fallback pairs those with the recorded proposals, so the appendix
    sees the same decision either way.
    """
    traces = [trace for trace in (run.traces or []) if isinstance(trace, Mapping)]
    proposals = list(run.proposals or [])
    proposals_by_round = {proposal.round: proposal for proposal in proposals}
    sent: list[dict[str, object]] = []
    records: list[dict[str, object]] = []
    for index, trace in enumerate(traces):
        kind = trace.get("kind")
        if kind == "message_sent":
            detail = trace.get("detail")
            detail_map = detail if isinstance(detail, Mapping) else {}
            sent.append(
                {
                    "round": _round_number(trace.get("round")),
                    "performative": str(detail_map.get("performative") or "").upper(),
                    "summary": str(detail_map.get("summary") or ""),
                }
            )
            continue
        if not isinstance(kind, str) or kind not in _DECISION_TRACE_KINDS:
            continue
        detail = trace.get("detail")
        detail_map = detail if isinstance(detail, Mapping) else {}
        records.append(
            {
                "round": _round_number(trace.get("round"))
                or _round_number(detail_map.get("round"))
                or index + 1,
                "action": str(detail_map.get("action") or kind).strip().lower(),
                "text": _decision_text(detail_map),
            }
        )
    if not records:
        result = run.result
        decisions = list(result.decisions) if result is not None else []
        for index, decision in enumerate(decisions):
            proposal = proposals[index] if index < len(proposals) else None
            records.append(
                {
                    "round": proposal.round if proposal is not None else index + 1,
                    "action": str(decision.action).strip().lower(),
                    "text": _decision_text(
                        {
                            "feasibilitySummary": decision.feasibility_summary,
                            "confirmedIntent": decision.confirmed_intent,
                            "counterProposal": decision.counter_proposal,
                            "rationale": decision.rationale,
                            "filledParams": decision.filled_params,
                        }
                    ),
                }
            )
    observations: list[ClientQualityObservation] = []
    for index, record in enumerate(records):
        raw_round = record["round"]
        round_number = raw_round if isinstance(raw_round, int) else None
        proposal = proposals_by_round.get(round_number) if round_number is not None else None
        if proposal is None and index < len(proposals):
            proposal = proposals[index]
        action = str(record["action"])
        wanted = _PERFORMATIVE_BY_ACTION.get(action)
        matching = next(
            (
                entry
                for entry in sent
                if wanted is not None
                and entry["performative"] == wanted
                and (
                    round_number is None
                    or entry["round"] is None
                    or entry["round"] == round_number
                )
            ),
            None,
        )
        # The dispatched message's summary is the Task-T action label, not the negotiation reply;
        # the substantive answer is the decision trace. Only use the summary when no decision text
        # was recorded at all.
        accepted_text = str(record["text"]).strip()
        if not accepted_text and matching is not None:
            accepted_text = str(matching["summary"])
        observations.append(
            ClientQualityObservation(
                round=round_number if isinstance(round_number, int) else index + 1,
                kind=proposal.kind if proposal is not None else "",
                proposal=proposal.content if proposal is not None else "",
                decision=action,
                accepted_text=accepted_text,
                # A decision that never dispatched its performative is structurally wrong.
                structure_ok=matching is not None,
            )
        )
    return observations


def _pairs(items: Sequence[CaseItem]) -> list[dict[str, object]]:
    """Join the per-protocol items of the same ``(case, round)`` into one comparable row."""
    by_key: dict[tuple[int, int], dict[str, CaseItem]] = {}
    for item in items:
        by_key.setdefault((item.case_id, item.round_index), {})[item.protocol] = item
    rows: list[dict[str, object]] = []
    for (case_id, round_index), runs in sorted(by_key.items()):
        if len(runs) < 2:
            continue
        entries = {
            protocol: _pair_entry(item) for protocol, item in sorted(runs.items())
        }
        a2at = entries.get(PROTOCOL_A2A_T)
        pure = entries.get(PROTOCOL_A2A)
        if a2at is None or pure is None:
            continue
        # Every delta is (A2A-T - A2A): negative rounds/tokens mean A2A-T was cheaper.
        rows.append(
            {
                "caseId": case_id,
                "round": round_index,
                "name": next(iter(runs.values())).name,
                "protocols": entries,
                "roundsDelta": _delta(a2at["rounds"], pure["rounds"]),
                "durationDeltaMs": _delta(a2at["durationMs"], pure["durationMs"]),
                "tokensDelta": _delta(a2at["tokensTotal"], pure["tokensTotal"]),
                "sameStatus": a2at["status"] == pure["status"],
                # A blocked case sends the follow-up text on the A2A-T side and the original text
                # on the A2A side, so the two rows are not the same request.
                "comparable": not any(
                    entry["blocked"] for entry in entries.values()
                ),
            }
        )
    return rows


def _terminal_state_unconfirmed(
    item: CaseItem, messages: TesterMessages | None = None
) -> bool:
    """Return whether the case expected a terminal state the run never observed.

    ``_wait_for_stage`` records ``final`` when the corpus names no terminal state (pure A2A) or
    when the case turns the assertion off. Both are deliberate record-only decisions; only a
    named expectation that was not observed leaves the row unproven.
    """
    if messages is None:
        messages = TesterMessages()
    final = item.stages.get("final")
    if final is None or final.status != STAGE_RECORDED:
        return False
    if final.expected is None or final.message == messages.get("assertion_off"):
        return False
    return True


def _pair_entry(item: CaseItem) -> dict[str, object]:
    """Return the comparable fields of one item in a paired row."""
    return {
        "status": item.status,
        "rounds": _item_rounds(item),
        "durationMs": item.duration_ms,
        "tokensTotal": _item_tokens_total(item),
        "runId": item.run_id,
        "error": item.error,
        "blocked": bool(item.interception),
    }


def _item_rounds(item: CaseItem) -> int | None:
    """Return the observed negotiation rounds recorded by the ``negotiate`` stage."""
    stage = item.stages.get("negotiate")
    actual = getattr(stage, "actual", None)
    if not isinstance(actual, Mapping):
        return None
    rounds = actual.get("rounds")
    return rounds if isinstance(rounds, int) and not isinstance(rounds, bool) else None


def _item_tokens_total(item: CaseItem) -> float | None:
    """Return the item's reported total token count, when the run reported one."""
    total = item.tokens.get("total")
    return float(total) if isinstance(total, (int, float)) and not isinstance(total, bool) else None


def _delta(later: object, earlier: object) -> float | None:
    """Return ``later - earlier`` when both sides are numeric."""
    if isinstance(later, bool) or isinstance(earlier, bool):
        return None
    if isinstance(later, (int, float)) and isinstance(earlier, (int, float)):
        return float(later) - float(earlier)
    return None


def _summary(items: Sequence[CaseItem]) -> dict[str, int]:
    counts = {
        "total": len(items),
        "pending": 0,
        "running": 0,
        "passed": 0,
        "failed": 0,
        "error": 0,
        "cancelled": 0,
        "partial": 0,
        "durationMs": 0,
        "stagePassed": 0,
        "stageFailed": 0,
        "stageRecorded": 0,
        "stageError": 0,
        "stageWaiting": 0,
        "stageWarning": 0,
    }
    for item in items:
        if item.status in counts:
            counts[item.status] += 1
        if isinstance(item.duration_ms, int):
            counts["durationMs"] += item.duration_ms
        for stage in item.stages.values():
            if stage.status == STAGE_PASSED:
                counts["stagePassed"] += 1
            elif stage.status == STAGE_FAILED:
                counts["stageFailed"] += 1
            elif stage.status == STAGE_RECORDED:
                counts["stageRecorded"] += 1
            elif stage.status == STAGE_ERROR:
                counts["stageError"] += 1
            elif stage.status == STAGE_WAITING:
                counts["stageWaiting"] += 1
            elif stage.status == STAGE_WARNING:
                counts["stageWarning"] += 1
    return counts


def _status_batch(batch: _Batch) -> dict[str, object]:
    """Render one running batch for the status page, including its running items."""
    items = batch.items
    finished = sum(
        1 for item in items if item.status not in {CASE_PENDING, CASE_RUNNING}
    )
    active = [
        {
            "caseId": item.case_id,
            "name": item.name,
            "protocol": item.protocol,
            "round": item.round_index,
            "status": item.status,
            "runId": item.run_id or "",
        }
        for item in (
            items[index] for index in batch.active_indices if 0 <= index < len(items)
        )
    ]
    started_at = batch.started_at or batch.created_at
    return {
        "batchId": batch.batch_id,
        "ownerId": batch.owner_id,
        "ownerSource": dict(batch.owner_source),
        "protocols": list(batch.protocols),
        "status": batch.status,
        "round": batch.current_round,
        "rounds": batch.rounds,
        "done": finished,
        "total": len(items),
        "active": active,
        "queued": len(batch.rerun_queue),
        "concurrency": batch.active_concurrency,
        "keepTasks": batch.keep_tasks,
        "stopAfter": batch.stop_after,
        "transport": batch.transport,
        "discovery": batch.discovery,
        "startedAt": started_at,
        "elapsedSeconds": _elapsed_seconds(started_at),
    }


def _status_run(run: RunState) -> dict[str, object]:
    """Render one live run for the status page: enough to place it, not its whole state."""
    source = run.source if isinstance(run.source, Mapping) else {}
    started_at = source.get("receivedAt")
    return {
        "runId": run.run_id,
        "sourceKind": str(source.get("kind") or ""),
        "remoteAddress": str(source.get("remoteAddress") or ""),
        "forwardedFor": str(source.get("forwardedFor") or ""),
        "userAgent": str(source.get("userAgent") or ""),
        "protocol": run.protocol,
        "transport": run.transport,
        "status": run.status,
        "detached": run.detached,
        "remoteTaskId": run.remote_task_id or "",
        "latestTaskState": run.latest_task_state or "",
        "startedAt": str(started_at or ""),
        "elapsedSeconds": _elapsed_seconds(started_at),
    }


def _status_recent(batches: Iterable[_Batch]) -> list[dict[str, object]]:
    """Return the ten most recently finished batches this process still remembers."""
    finished = [
        batch
        for batch in batches
        if batch.status not in {BATCH_RUNNING, BATCH_DRAFT}
    ]
    finished.sort(
        key=lambda batch: batch.finished_at or batch.started_at or batch.created_at,
        reverse=True,
    )
    rows: list[dict[str, object]] = []
    for batch in finished[:10]:
        items = batch.items
        rows.append(
            {
                "batchId": batch.batch_id,
                "ownerId": batch.owner_id,
                "ownerSource": dict(batch.owner_source),
                "status": batch.status,
                "protocols": list(batch.protocols),
                "done": sum(
                    1 for item in items if item.status not in {CASE_PENDING, CASE_RUNNING}
                ),
                "total": len(items),
                "startedAt": batch.started_at or "",
                "finishedAt": batch.finished_at or "",
                "error": batch.error or "",
            }
        )
    return rows


def _elapsed_seconds(value: object) -> int:
    """Return whole seconds since one ISO timestamp, ``0`` when it cannot be parsed."""
    if not isinstance(value, str) or not value:
        return 0
    try:
        started = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return 0
    return max(0, int((datetime.now(UTC) - started).total_seconds()))


def _round_summaries(items: Sequence[CaseItem], rounds: int) -> list[dict[str, object]]:
    """Return one summary per round, keeping the rounds with no items visible as zeroes."""
    return [
        {
            "round": round_index,
            "summary": _summary(
                [item for item in items if item.round_index == round_index]
            ),
        }
        for round_index in range(1, rounds + 1)
    ]


def _cross_round(items: Sequence[CaseItem]) -> dict[str, int]:
    """Count the ``(case, protocol)`` pairs that passed every round they ran.

    Scratch items are excluded: they have no case identity, so they cannot be compared across
    rounds. A single-round batch reports ``total=0`` and lets the console hide the figure.
    """
    groups: dict[tuple[int, str], list[CaseItem]] = {}
    for item in items:
        if item.scratch is not None:
            continue
        groups.setdefault((item.case_id, item.protocol), []).append(item)
    repeated = [group for group in groups.values() if len(group) > 1]
    return {
        "stable": sum(
            1 for group in repeated if all(item.status == CASE_PASSED for item in group)
        ),
        "total": len(repeated),
    }


def _baseline_request(case: Mapping[str, Any] | None = None) -> str:
    """Return the Create request paired with one Modify case.

    Every Modify case ships a ``baseline.request``: the manual-acceptance Create Task-T for
    ``A2ATestRegion`` (three day segments, no outage), which the vendor accepts without opening a
    negotiation (see eval/osp/README.md 3.5). This function returns it. A case without one falls
    back to the shared fixture below, which keeps the previous behaviour for hand-written rows.

    The demo's ``Region A`` placeholder is not a served region: the real NMS answers it with an
    information negotiation. A Modify baseline must therefore name the configured
    ``A2AT_SAMPLE_NMS_REGION``.
    """
    override = _case_baseline_request(case)
    if override is not None:
        return override
    region = os.environ.get("A2AT_SAMPLE_NMS_REGION", "").strip()
    if not region:
        try:
            from shared.config import load_config

            region = (load_config().nms_region or "").strip()
        except Exception:  # noqa: BLE001 - the baseline falls back to the test region
            region = ""
    region = region or BASELINE_REGION
    return BASELINE_REQUEST.replace(BASELINE_REGION, region)


def _case_baseline_request(case: Mapping[str, Any] | None) -> str | None:
    """Return a case's own ``baseline.request``, when it overrides the shared fixture."""
    if not isinstance(case, Mapping):
        return None
    block = case.get("baseline")
    if not isinstance(block, Mapping):
        return None
    request = block.get("request")
    return request.strip() if isinstance(request, str) and request.strip() else None


def _baseline_accepted_states(protocol: str, discovery: str = "") -> frozenset[str]:
    """Return the states a Modify can continue from, for one protocol and hop.

    ``COMPLETED`` is excluded on purpose even though it is an acceptance signal for a Create:
    the A2A layer rejects a message addressed to a terminal Task. A direct hop sees the vendor
    Task itself, so the vendor's ``SUBMITTED`` already means "accepted and continuable"; the
    local hop sees the client agent's outer Task, which is ``WORKING`` from the first acknowledgement.
    """
    if protocol == RUNTIME_A2A_T or discovery == DISCOVERY_NMS:
        return frozenset({TASK_STATE_SUBMITTED, TASK_STATE_WORKING})
    return frozenset({TASK_STATE_WORKING})


def _requires_acceptance_evidence(run: RunState) -> bool:
    """Return whether a bare Task state cannot prove that the request was accepted.

    A direct vendor NMS reports :data:`TASK_STATE_SUBMITTED` without the success copy its contract
    requires and then cancels the Task when it refuses the request -- a vendor-side defect we
    tolerate by waiting for the copy. Our own RAN Agent answers the same state as its final
    acceptance and ships no copy, so only the vendor hop is held to it.
    """
    return run.discovery == DISCOVERY_NMS and run.protocol == RUNTIME_A2A_T


def _acceptance_evidence(run: RunState, state: str | None) -> str:
    """Return the evidence that proves the downstream accepted the request.

    ``state`` means the Task moved past the acknowledgement (``WORKING`` / ``COMPLETED``);
    ``intent`` is the structured Task payload the vendor ships with its success copy; ``copy`` is
    that copy without a payload. An empty string means nothing but the acknowledgement was seen.
    """
    if state in _WORKING_TASK_STATES:
        return "state"
    if state != TASK_STATE_SUBMITTED:
        return ""
    if run.server_intent is not None:
        return "intent"
    if run.acceptance_copy is not None:
        return "copy"
    return ""


def _baseline_continuable(
    run: RunState,
    protocol: str,
    discovery: str,
    state: str | None,
) -> bool:
    """Return whether one baseline run has a Task a Modify can really continue.

    On the local hop the outer Task reports ``WORKING`` as soon as the client agent acknowledges
    it, before the downstream Create is sent. A downstream failure (the field's HTTP 500
    ``Running task limit reached``) then leaves a non-existent Task behind. The outer↔inner
    binding is the evidence that the downstream actually accepted the Create.

    The vendor hop has the same gap from the other side: when the vendor reports ``SUBMITTED``
    without the success copy its contract requires, the Create may still be cancelled. Continuing
    such a Task makes the Modify fail for a reason the item cannot explain, so the baseline waits
    for the same acceptance evidence as the ``accepted`` stage. That is robustness against a
    vendor defect, not a change of our own contract.
    """
    if not run.remote_task_id or not run.remote_context_id:
        return False
    if state not in _baseline_accepted_states(protocol, discovery):
        return False
    if protocol != RUNTIME_A2A_T and discovery != DISCOVERY_NMS:
        return bool(run.downstream_task_id)
    if discovery == DISCOVERY_NMS and not _acceptance_evidence(run, state):
        return False
    return True


def _last_failed_status_text(run: RunState) -> str | None:
    """Return the latest non-empty status text of one baseline run.

    The status message carries the downstream reason ("Running task limit reached") that the
    outer Task state alone hides.
    """
    if run.result is None:
        return None
    for event in reversed(run.result.events):
        if event.get("kind") != "status":
            continue
        text = event.get("text")
        if isinstance(text, str) and text.strip():
            return text.strip()
    return None


def _baseline_failure_message(
    protocol: str,
    state: str | None,
    discovery: str = "",
    messages: TesterMessages | None = None,
) -> str:
    """Explain why the paired Create cannot be continued by a Modify."""
    if messages is None:
        messages = TesterMessages()
    state_text = state or messages.get("baseline_state_unknown")
    if protocol == RUNTIME_A2A_T or discovery == DISCOVERY_NMS:
        if state == "TASK_STATE_INPUT_REQUIRED":
            return messages.get("baseline_modify_input_required")
        if state == TASK_STATE_SUBMITTED and discovery == DISCOVERY_NMS:
            return messages.get("baseline_modify_unproven")
        return messages.get("baseline_modify_not_continuable", state=state_text)
    return messages.get("baseline_pure_not_working", state=state_text)


def _negotiation_expected(case: Mapping[str, Any], protocol: str) -> bool:
    """Return whether the case expects a negotiation round on one protocol."""
    expect = case.get("expect")
    protocols = expect.get("protocols") if isinstance(expect, Mapping) else None
    block = protocols.get(protocol) if isinstance(protocols, Mapping) else None
    negotiation = block.get("negotiation") if isinstance(block, Mapping) else None
    return bool(isinstance(negotiation, Mapping) and negotiation.get("expected"))


def _observed_proposal(proposal: object, index: int) -> dict[str, object]:
    """Render one inbound proposal as the observed side of the message comparison."""
    round_number = getattr(proposal, "round", None)
    return {
        "round": (
            round_number
            if isinstance(round_number, int) and round_number > 0
            else index + 1
        ),
        "kind": str(getattr(proposal, "kind", "") or ""),
        "content": str(getattr(proposal, "content", "") or ""),
    }


def _message_diffs(match: object) -> list[dict[str, object]]:
    """Render the failed rounds of one message match as stage diffs."""
    failed_rounds = getattr(match, "failed_rounds", None)
    if not callable(failed_rounds):
        return []
    return [
        {
            "path": f"negotiate.messages[{round_match.round}]",
            "expected": list(round_match.expected),
            "actual": round_match.actual,
            "reason": round_match.reason,
        }
        for round_match in failed_rounds()
    ]


def _case_runner(case: Mapping[str, Any] | None) -> Mapping[str, Any]:
    """Return the ``runner`` block of a case, empty when absent."""
    if not isinstance(case, Mapping):
        return {}
    runner = case.get("runner")
    return runner if isinstance(runner, Mapping) else {}


def _assertion_enabled(case: Mapping[str, Any], protocol: str, key: str) -> bool:
    """Return whether ``case.assert`` enables one assertion.

    ``protocol=""`` reads the top-level key (``intent``); otherwise the protocol block is used
    (``a2a-t.task_t`` / ``a2a.negotiation``). A missing flag means enabled.
    """
    block = case.get("assert")
    if not isinstance(block, Mapping):
        return True
    container: object = block.get(protocol) if protocol else block
    if not isinstance(container, Mapping):
        return True
    value = container.get(key)
    return True if value is None else bool(value)


def _stage_verdict(stage: StageResult | None) -> str:
    """Return the validation verdict of one stage, preferring its structured detail."""
    if stage is None:
        return ""
    detail = stage.detail if isinstance(stage.detail, Mapping) else {}
    verdict = detail.get("verdict")
    if isinstance(verdict, str) and verdict:
        return verdict
    actual = stage.actual
    return actual if isinstance(actual, str) else ""


def _validation_reason(stage: StageResult | None) -> str:
    """Return the failing code (else message) of one validation stage."""
    if stage is None:
        return ""
    detail = stage.detail if isinstance(stage.detail, Mapping) else {}
    code = detail.get("code")
    if isinstance(code, str) and code:
        return code
    return stage.message if isinstance(stage.message, str) else ""


def _apply_assertion(
    case: Mapping[str, Any],
    protocol: str,
    key: str,
    result: StageResult,
    messages: TesterMessages | None = None,
) -> StageResult:
    """Keep the observed values but record (never judge) a stage whose assertion is disabled."""
    if _assertion_enabled(case, protocol, key):
        return result
    if messages is None:
        messages = TesterMessages()
    result.status = STAGE_RECORDED
    result.message = messages.get("assertion_off")
    return result


def _mark_item_cancelled(item: CaseItem, messages: TesterMessages | None = None) -> None:
    """Record an operator's per-item cancel on the item itself.

    Shared by the queued-item skip of both execution paths and by ``_run_item``'s finally, so the
    cancel contract has one implementation.
    """
    if messages is None:
        messages = TesterMessages()
    item.status = CASE_CANCELLED
    item.error = messages.get("item_cancelled")


def _interception_held(item: CaseItem) -> bool:
    """Return whether one item's recorded interception lets the replacement request run.

    The gate reads the observed verdict, not the stage status: a block whose slot errors differ
    from the case still intercepted the request, so the replacement still runs. A missing record
    or a missing verdict keeps the historical two-segment behaviour rather than inventing a stop.
    """
    record = item.interception if isinstance(item.interception, Mapping) else {}
    stages = record.get("stages")
    validate = stages.get("validate") if isinstance(stages, Mapping) else None
    actual = validate.get("actual") if isinstance(validate, Mapping) else None
    return followup_allowed(actual if isinstance(actual, str) and actual else None)


def _interception_miss_detail(
    item: CaseItem, messages: TesterMessages | None = None
) -> str:
    """Return the one-line reason of an interception that did not hold, for the item's message."""
    if messages is None:
        messages = TesterMessages()
    record = item.interception if isinstance(item.interception, Mapping) else {}
    stages = record.get("stages")
    validate = stages.get("validate") if isinstance(stages, Mapping) else None
    actual = validate.get("actual") if isinstance(validate, Mapping) else None
    if isinstance(actual, str) and actual:
        return messages.get("interception_detail", actual=actual)
    return messages.get("no_validation_conclusion")


def _interception_statuses(record: Mapping[str, object]) -> list[str]:
    """Return the stage statuses of one interception record (empty when malformed)."""
    stages = record.get("stages")
    if not isinstance(stages, Mapping):
        return []
    return [
        str(stage.get("status"))
        for stage in stages.values()
        if isinstance(stage, Mapping) and stage.get("status")
    ]


def _langfuse_warning(run: RunState) -> str:
    """Return the Langfuse exporter warning of one run, empty when the export is healthy."""
    snapshot = run.langfuse if isinstance(run.langfuse, Mapping) else {}
    warning = snapshot.get("warning")
    return warning if isinstance(warning, str) else ""


def _langfuse_url(run: RunState) -> str:
    """Return the clickable Langfuse trace URL of one run, empty when it is unresolved."""
    snapshot = run.langfuse if isinstance(run.langfuse, Mapping) else {}
    url = snapshot.get("url")
    return url if isinstance(url, str) else ""


def _langfuse_trace_id(run: RunState) -> str:
    """Return the OTel/Langfuse trace id of one run, empty when observability is off."""
    snapshot = run.langfuse if isinstance(run.langfuse, Mapping) else {}
    trace_id = snapshot.get("traceId")
    return trace_id if isinstance(trace_id, str) else ""


def _baseline_run_id(item: CaseItem) -> str | None:
    """Return the run that created a Modify item's baseline Task, when one was seeded."""
    baseline = item.baseline
    if not isinstance(baseline, Mapping):
        return None
    value = baseline.get("runId")
    return value if isinstance(value, str) and value else None


def _task_observation(run: RunState) -> dict[str, object]:
    """Return the Task identity/state the run last observed."""
    return {
        "taskId": run.remote_task_id,
        "contextId": run.remote_context_id,
        "state": run.latest_task_state,
    }


def _run_validation_detail(run: RunState) -> dict[str, object] | None:
    """Return the verdict and slot errors of the run's own pre-send validation.

    The run emits one ``task_validation`` span when it validates the Task-T it is about to send.
    An interception span replayed from the tester's preflight describes the blocked original, not
    what this run dispatched, so it is skipped.
    """
    for span in reversed(run.traces):
        if span.get("kind") != "task_validation":
            continue
        detail = span.get("detail")
        if not isinstance(detail, Mapping) or detail.get("source") == "interception":
            continue
        verdict = detail.get("verdict")
        if not isinstance(verdict, str) or not verdict:
            continue
        verdict = _normalize_validation_verdict(verdict)
        errors = detail.get("errors")
        slot_errors = [dict(error) for error in errors] if isinstance(errors, list) else []
        reason = detail.get("reason")
        code = reason if isinstance(reason, str) and reason else ""
        if not code and slot_errors:
            code = str(slot_errors[0].get("code") or "")
        return {
            "verdict": verdict,
            "negotiationKind": detail.get("negotiationKind"),
            "code": code,
            "slotErrors": slot_errors,
        }
    return None


def _first_validation_detail(run: RunState) -> dict[str, object] | None:
    """Return the run's first gate verdict, which is the blocked original of a blocked case."""
    for span in run.traces:
        if span.get("kind") != "task_validation":
            continue
        detail = span.get("detail")
        if not isinstance(detail, Mapping) or detail.get("source") == "interception":
            continue
        verdict = detail.get("verdict")
        if isinstance(verdict, str) and verdict:
            return dict(detail)
    return None


def _correction_detail(run: RunState) -> dict[str, object] | None:
    """Return the run's correction hand-off, ``None`` when the gate never blocked."""
    for span in reversed(run.traces):
        if span.get("kind") != "correction_applied":
            continue
        detail = span.get("detail")
        if isinstance(detail, Mapping):
            return dict(detail)
    return None


def _normalize_validation_verdict(value: str) -> str:
    """Map the run's TaskValidation verdict to the OSP vocabulary a case expects.

    The client flow reports ``passed`` for a clean validation and ``negotiable`` when the content
    is repairable by a negotiation (the request is still sent). The cases and the SDK runner call
    both ``pass``: the OSP column asks whether the Task-T is intercepted, not how the SDK spelled
    the verdict.
    """
    if value in {"passed", "negotiable", "pass"}:
        return "pass"
    return value


def _case_validation_expectation(case: Mapping[str, Any]) -> object:
    """Return the case's expected validation verdict (``blocked`` / ``pass`` / ...)."""
    expect = case.get("expect")
    protocols = expect.get("protocols") if isinstance(expect, Mapping) else None
    block = protocols.get(PROTOCOL_A2A_T) if isinstance(protocols, Mapping) else None
    return block.get("validate") if isinstance(block, Mapping) else None


def _validation_matches(expected: object, actual: str) -> bool:
    """Return whether one observed verdict satisfies the case's expectation."""
    if expected == "pass_with_terminology":
        return actual in {"pass", "pass_with_terminology"}
    return expected == actual


def _rendered_task_t(run: RunState) -> str | None:
    """Return the Task-T prompt a run rendered, from its ``task_prompt_rendered`` span.

    The span's ``promptText`` is the wire payload the SDK produced, so a full-flow item can be
    compared with its case without running the Task-T generation a second time. The last span wins:
    a retried segment renders its own prompt after any earlier one.
    """
    for span in reversed(run.traces):
        if span.get("kind") != "task_prompt_rendered":
            continue
        detail = span.get("detail")
        if isinstance(detail, Mapping):
            text = detail.get("promptText")
            if isinstance(text, str) and text.strip():
                return text
    return None


def _load_stages(raw: object) -> dict[str, StageResult]:
    """Restore the persisted stage results of one item (v1 batches have none)."""
    if not isinstance(raw, Mapping):
        return {}
    stages: dict[str, StageResult] = {}
    for name, value in raw.items():
        if not isinstance(value, Mapping):
            continue
        raw_diffs = value.get("diffs")
        raw_detail = value.get("detail")
        stages[str(name)] = StageResult(
            stage=str(value.get("stage") or name),
            status=str(value.get("status") or STAGE_ERROR),
            message=str(value.get("message") or ""),
            expected=value.get("expected"),
            actual=value.get("actual"),
            diffs=[
                dict(diff) for diff in raw_diffs if isinstance(diff, Mapping)
            ]
            if isinstance(raw_diffs, list)
            else [],
            duration_ms=int(value.get("durationMs") or 0),
            detail=dict(raw_detail) if isinstance(raw_detail, Mapping) else None,
        )
    return stages


def _load_attempts(raw: object) -> list[dict[str, object]]:
    """Restore the previous attempts of one item."""
    if not isinstance(raw, list):
        return []
    return [dict(attempt) for attempt in raw if isinstance(attempt, Mapping)]


def _optional_mapping(raw: object) -> dict[str, object] | None:
    """Restore an optional JSON object persisted in a batch document."""
    return _mapping_or_empty(raw) if isinstance(raw, Mapping) else None


def _mapping_or_empty(raw: object) -> dict[str, object]:
    """Restore a JSON object, empty when the batch predates the field."""
    return {str(key): value for key, value in raw.items()} if isinstance(raw, Mapping) else {}


def validate_concurrency(
    value: object, messages: TesterMessages | None = None
) -> int:
    """Return the requested item concurrency, rejecting values outside ``1..MAX_CONCURRENCY``."""
    if messages is None:
        messages = TesterMessages()
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(messages.get("concurrency_must_be_int"))
    if value < 1 or value > MAX_CONCURRENCY:
        raise ValueError(
            messages.get("concurrency_out_of_range", min=1, max=MAX_CONCURRENCY)
        )
    return value


def _clamp_concurrency(value: object) -> int:
    """Return a persisted concurrency, normalized to the supported range (old docs get 1)."""
    if isinstance(value, bool) or not isinstance(value, int):
        return DEFAULT_CONCURRENCY
    return min(MAX_CONCURRENCY, max(DEFAULT_CONCURRENCY, value))


def validate_rounds(value: object, messages: TesterMessages | None = None) -> int:
    """Return the requested batch rounds, rejecting values outside ``1..MAX_ROUNDS``."""
    if messages is None:
        messages = TesterMessages()
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(messages.get("rounds_must_be_int"))
    if value < DEFAULT_ROUNDS or value > MAX_ROUNDS:
        raise ValueError(
            messages.get("rounds_out_of_range", min=DEFAULT_ROUNDS, max=MAX_ROUNDS)
        )
    return value


def _clamp_rounds(value: object) -> int:
    """Return a persisted round value, normalized to the supported range (old docs get 1)."""
    if isinstance(value, bool) or not isinstance(value, int):
        return DEFAULT_ROUNDS
    return min(MAX_ROUNDS, max(DEFAULT_ROUNDS, value))


def _positive_or_none(value: float | None) -> float | None:
    """Return a positive float, or ``None`` when the caller wants the per-case value."""
    if value is None:
        return None
    return max(1.0, float(value))


def _optional_float(value: object) -> float | None:
    """Return a float persisted in a batch document, or ``None`` when absent/invalid."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _optional_text(value: object) -> str:
    """Return a text field persisted in a batch document, or an empty string."""
    return value if isinstance(value, str) else ""


def _language_or_default(value: object) -> str:
    """Return a supported batch language, defaulting to Chinese (history was Chinese-only)."""
    if isinstance(value, str) and value in SUPPORTED_LANGUAGES:
        return value
    return LANGUAGE_ZH


def _runtime_protocol(protocol: str) -> str:
    """Map the catalog protocol label to the value the run manager expects."""
    return PROTOCOL_PURE if protocol == PROTOCOL_A2A else protocol


__all__ = [
    "ALL_STAGES",
    "BATCH_CANCELLED",
    "BATCH_DRAFT",
    "BATCH_FINISHED",
    "BATCH_RUNNING",
    "CASE_CANCELLED",
    "CASE_ERROR",
    "CASE_FAILED",
    "CASE_PARTIAL",
    "CASE_PASSED",
    "CASE_PENDING",
    "CASE_RUNNING",
    "DEFAULT_CONCURRENCY",
    "DEFAULT_ROUNDS",
    "DEFAULT_WORKING_TIMEOUT_SECONDS",
    "EvalBatchManager",
    "MAX_CONCURRENCY",
    "MAX_ROUNDS",
    "validate_concurrency",
    "validate_rounds",
    "SCHEMA",
    "STAGE_SKIPPED",
    "STAGE_WAITING",
    "STAGE_WARNING",
]
