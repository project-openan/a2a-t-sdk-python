"""Service layer of the acceptance review console.

The console says one thing the tester cannot: *does the stored evidence satisfy the corpus as it is
today, and has a human accepted it?* Evidence comes from the review store; the verdict is computed
on read with :func:`eval.osp.review.judge_protocol`, so a newly annotated expectation applies to
old batches without a re-run.

A protocol task has two independent axes and the page shows both:

* the **automatic verdict** — pass / fail / partial / not run / stale (the evidence was produced
  from a different input);
* the **human decision** — pending / accepted / defect / known issue / recheck, with the runIds it
  was based on. A decision is marked stale when the case fingerprint changes or newer evidence
  arrives, and the page asks for a re-confirmation instead of silently keeping it.
"""

from __future__ import annotations

import logging
import re
import threading
import time
from collections.abc import Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from eval.osp.catalog import PROTOCOL_A2A, PROTOCOL_A2A_T, CaseCatalog, CatalogReloadError
from eval.osp.review import (
    DIMENSION_TRACE,
    VERDICT_FAIL,
    VERDICT_MISSING,
    VERDICT_NOT_APPLICABLE,
    VERDICT_NOT_EXPECTED,
    VERDICT_PARTIAL,
    VERDICT_PASS,
    VERDICT_RECORDED,
    ProtocolJudgement,
    case_signature,
    judge_protocol,
)
from eval.osp.tracecheck import (
    TRACE_ID_PATTERN,
    TraceApplicability,
    nms_kind,
    original_request,
    trace_applicability,
)
from langfuse_export.config import Config
from protocol.client_store import read_client_run_detail, read_client_run_summaries
from shared.config import SampleConfig

from demo.web.review.corpus import (
    TesterLookup,
    TesterRunRef,
    case_documents,
    find_tester_evidence,
    scan_evidence,
)
from demo.web.review.store import (
    CATEGORIES,
    DECISION_ACCEPTED,
    DECISION_DEFECT,
    DECISION_KNOWN_ISSUE,
    DECISION_PENDING,
    DECISIONS,
    DecisionKey,
    ReviewStore,
    now_iso,
)
from demo.web.review.traces import (
    SNAPSHOT_SCHEMA,
    InvalidTraceIdError,
    TraceStore,
    trace_negotiation_messages,
)

_LOGGER = logging.getLogger("ran_energy_saving.review")

#: Protocol tasks every case carries, in display order.
PROTOCOLS: Final[tuple[str, ...]] = (PROTOCOL_A2A_T, PROTOCOL_A2A)

#: Status of a protocol task with no evidence in the window.
STATUS_NOT_RUN: Final[str] = "not_run"

#: Statuses the protocol list can show (automatic verdicts plus the no-evidence state).
STATUSES: Final[tuple[str, ...]] = (
    VERDICT_PASS,
    VERDICT_FAIL,
    VERDICT_PARTIAL,
    VERDICT_RECORDED,
    VERDICT_MISSING,
    VERDICT_NOT_EXPECTED,
    VERDICT_NOT_APPLICABLE,
    STATUS_NOT_RUN,
)

#: Export states whose snapshot is complete enough to serve from cache without a request.
_EXPORTED_STATES: Final[frozenset[str]] = frozenset({"exported", "partial"})

#: Cooldown after a Langfuse 429. The public API admits 30 requests/minute; one minute is enough
#: for the bucket to refill and short enough that a reviewer can retry by hand.
FETCH_COOLDOWN_SECONDS: Final[float] = 60.0

#: Concurrent Langfuse fetches of one round. Two in flight keep a full round moving while staying
#: well inside the 30 requests/minute public API; the 429 cooldown still stops the whole round.
FETCH_CONCURRENCY: Final[int] = 2

#: Hard bound of one trace id / run id lookup. A pasted list is a triage aid, not a bulk export;
#: everything beyond this bound is reported as dropped instead of silently truncated.
MAX_TRACE_IDS: Final[int] = 200

#: Dimensions of the judged rows that the trace list summarises, in display order.
TRACE_DIMENSIONS: Final[tuple[str, ...]] = (
    "intent",
    "task_t",
    "validate",
    "negotiation",
    "final_state",
    "trace",
)

#: Verdicts that mean the reviewer still has to look at that dimension.
_ATTENTION_STATUSES: Final[frozenset[str]] = frozenset(
    {VERDICT_FAIL, VERDICT_PARTIAL, VERDICT_MISSING, VERDICT_RECORDED}
)

#: A 32-hex token is a trace id (and, in this sample, usually also the run id). A Langfuse URL or
#: a ``traceId=...`` log line is normalised to the token it contains.
_TRACE_TOKEN: Final[re.Pattern[str]] = re.compile(r"[0-9a-fA-F]{32}")
_RUN_TOKEN: Final[re.Pattern[str]] = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


@dataclass(frozen=True)
class TraceQuery:
    """The normalised ids of one trace / run lookup."""

    ids: tuple[str, ...]
    inputs: int
    dropped: int


def parse_trace_input(raw: str | Sequence[str]) -> TraceQuery:
    """Return the unique trace ids / run ids in one pasted list.

    Splitting is deliberately generous: field reports arrive as log lines, comma lists or Langfuse
    URLs. A 32-hex token is preferred over the surrounding text, so
    ``traceId=0123...`` and ``https://.../trace/0123...`` both resolve to the id. A token that is
    not a hex trace id is kept as a run id when it looks like one.
    """
    if isinstance(raw, str):
        parts: list[str] = re.split(r"[\s,;，；]+", raw)
    else:
        parts = []
        for value in raw:
            if isinstance(value, str):
                parts.extend(re.split(r"[\s,;，；]+", value))
    ordered: list[str] = []
    seen: set[str] = set()
    for part in parts:
        token = part.strip().strip("()[]<>\"'")
        if not token:
            continue
        hexes = _TRACE_TOKEN.findall(token)
        candidates = [value.lower() for value in hexes]
        if not candidates and _RUN_TOKEN.match(token):
            candidates = [token]
        for candidate in candidates:
            if candidate in seen:
                continue
            seen.add(candidate)
            ordered.append(candidate)
    inputs = len(ordered)
    return TraceQuery(tuple(ordered[:MAX_TRACE_IDS]), inputs, max(0, inputs - MAX_TRACE_IDS))


class ReviewService:
    """Evidence projection, current-corpus judging and decision storage."""

    def __init__(
        self,
        *,
        store: ReviewStore,
        catalog: CaseCatalog,
        runs_dir: Path,
        client_db_path: Path | None,
        trace_store: TraceStore | None = None,
        trace_config: Config | None = None,
        trace_fetch_enabled: bool = True,
    ) -> None:
        self._store = store
        self._catalog = catalog
        self._runs_dir = Path(runs_dir)
        self._client_db_path = client_db_path
        self._trace_store = trace_store if trace_store is not None else TraceStore(self._runs_dir)
        #: Langfuse credentials for the opt-in fetch; ``None`` means "not configured yet".
        self._trace_config = trace_config
        #: The standalone read-only instance leaves this off: it must not call Langfuse at all.
        self._trace_fetch_enabled = trace_fetch_enabled
        #: Bounded fetch concurrency. The public API admits 30 requests/minute; two in flight is
        #: the default for a whole-round fetch, and the permanent on-disk cache keeps a page that
        #: opens several cases from bursting through the budget.
        self._fetch_lock = threading.BoundedSemaphore(FETCH_CONCURRENCY)
        #: Wall-clock moment before which no fetch is attempted after a 429.
        self._fetch_cooldown_until = 0.0
        self._last_scan: dict[str, object] | None = None
        #: Trace blocks re-derived from the snapshot because the stored payload used an older
        #: normalizer. Keyed by (trace id, run id); a page view re-judges the same rows often.
        self._trace_payload_cache: dict[tuple[str, str], dict[str, object]] = {}

    @property
    def store(self) -> ReviewStore:
        """Return the review store (routes export from it)."""
        return self._store

    @property
    def catalog(self) -> CaseCatalog:
        """Return the case catalog (routes report its version)."""
        return self._catalog

    @property
    def last_scan(self) -> dict[str, object] | None:
        """Return the last scan summary, for the report header."""
        return self._last_scan

    def reload_catalog(self) -> None:
        """Re-read the corpus and the live annotation overlay before a scan."""
        try:
            self._catalog.reload()
        except CatalogReloadError as exc:
            _LOGGER.warning("catalog reload before a review scan failed: %s", exc)

    def scan(self, *, days: float = 2.0, max_batches: int = 500) -> dict[str, object]:
        """Collect the evidence of the recent tester batches into the store."""
        self.reload_catalog()
        cases = case_documents(self._catalog.documents())
        scan = scan_evidence(
            runs_dir=self._runs_dir,
            client_db_path=self._client_db_path,
            cases=cases,
            days=days,
            max_batches=max_batches,
        )
        inserted = self._store.ingest(scan.observations)
        attached = self._attach_traces(scan.observations)
        summary: dict[str, object] = {
            "inserted": inserted,
            "observations": len(scan.observations),
            "batches": len(scan.batches),
            "traces": attached,
            "windowStart": scan.window_start,
            "windowEnd": scan.window_end,
            "skipped": dict(scan.skipped),
            "truncated": scan.truncated,
        }
        self._last_scan = summary
        return summary

    def _attach_traces(self, observations: Sequence[Mapping[str, object]]) -> int:
        """Attach the trace snapshot already on disk to every judgeable observation.

        No network: ``cached_payload`` without a config only reads the snapshot root. A scan must
        never pull every trace of a window from Langfuse — that is the page's explicit action.
        """
        groups: dict[str, tuple[dict[str, object], list[Mapping[str, object]]]] = {}
        for observation in observations:
            target = _trace_target(observation)
            if target is None:
                continue
            trace_id = str(target["traceId"])
            entry = groups.get(trace_id)
            if entry is None:
                groups[trace_id] = (target, [observation])
            else:
                entry[1].append(observation)
        attached = 0
        stamp = now_iso()
        for trace_id, (target, rows) in groups.items():
            try:
                payload = self._trace_store.cached_payload(
                    trace_id,
                    run_id=str(target["runId"]),
                    nms_kind=_row_nms_kind(rows[0]),
                    local_points=_string_list(target.get("localPoints")),
                    local_known=_string_list(target.get("localKnown")),
                )
            except (InvalidTraceIdError, OSError):
                continue
            if str(payload.get("exportState") or "") not in {"exported", "partial"}:
                continue
            for observation in rows:
                attached += self._store.set_trace(
                    case_id=_as_int(observation.get("caseId")),
                    protocol=str(observation.get("protocol") or ""),
                    batch_id=str(observation.get("batchId") or ""),
                    source_round=_as_int(observation.get("sourceRound"), 1),
                    run_id=str(observation.get("runId") or ""),
                    trace=payload,
                    fetched_at=stamp,
                )
        return attached

    def fetch_trace(
        self,
        case_id: int,
        protocol: str,
        *,
        trace_id: str | None = None,
        run_id: str | None = None,
        batch_id: str | None = None,
        source_round: int | None = None,
        force: bool = False,
    ) -> dict[str, object]:
        """Fetch one round's trace snapshot (explicit opt-in) and attach it.

        The snapshot is a **permanent cache**: without ``force`` an existing export is reused
        without touching Langfuse. Opening a case therefore costs at most one request per run, and
        the reviewer's "重新抓取" is the only way to spend quota again.

        Fetches are serialized because the public API admits 30 requests/minute. A 429 puts the
        service into a short cooldown instead of retrying; the page shows the remaining seconds.
        """
        if not self._trace_fetch_enabled:
            raise ValueError("该实例是只读模式，未启用轨迹抓取（启动时加 --fetch-traces）。")
        case = self._catalog.get(case_id)
        if case is None:
            raise ValueError("未知用例。")
        if protocol not in PROTOCOLS:
            raise ValueError(f"protocol 只能是 {', '.join(PROTOCOLS)}。")
        rows = self._store.observations(case_id, protocol)
        if not rows:
            raise ValueError("该协议任务没有证据。")
        observation = _select_round(
            rows,
            trace_id=trace_id,
            run_id=run_id,
            batch_id=batch_id,
            source_round=source_round,
        )
        if observation is None:
            raise ValueError("找不到对应的运行证据，请先重新扫描。")
        target = _trace_target(observation)
        if target is None:
            raise ValueError(_row_applicability(observation).text or "该运行没有可抓取的轨迹。")
        resolved_trace = str(target["traceId"])
        if not force:
            cached = self._trace_store.load(resolved_trace)
            if cached.export_state in _EXPORTED_STATES:
                return self._attach_trace(observation, target)
        remaining = self._cooldown_remaining()
        if remaining > 0:
            raise ValueError(f"Langfuse 限流冷却中，请 {remaining} 秒后重试。")
        with self._fetch_lock:
            # Another tab may have fetched this trace while this request waited for the lock.
            if not force:
                cached = self._trace_store.load(resolved_trace)
                if cached.export_state in _EXPORTED_STATES:
                    return self._attach_trace(observation, target)
            config = self._resolved_trace_config()
            result = self._trace_store.fetch(resolved_trace, config, run_id=str(target["runId"]))
            if str(result.get("exportState") or "") != "exported":
                error = str(result.get("error") or result.get("exportState") or "")
                if _is_rate_limited(error):
                    self._fetch_cooldown_until = time.monotonic() + FETCH_COOLDOWN_SECONDS
                    raise ValueError(f"Langfuse 限流（429），请 {int(FETCH_COOLDOWN_SECONDS)} 秒后重试。")
                raise ValueError(f"轨迹抓取失败：{error}")
            return self._attach_trace(observation, target)

    def fetch_round_traces(
        self,
        batch_id: str,
        round: int | None = None,
        *,
        force: bool = False,
        concurrency: int = FETCH_CONCURRENCY,
    ) -> dict[str, object]:
        """Fetch every trace of one batch round, at most ``FETCH_CONCURRENCY`` at a time.

        The reviewer's whole-round action: one request per run (not per case), cached snapshots are
        attached without spending quota, and a Langfuse 429 stops the round instead of retrying.
        ``force`` is reserved for a caller that explicitly wants to refresh every snapshot; the
        page's button uses the cached-first default.
        """
        if not self._trace_fetch_enabled:
            raise ValueError("该实例是只读模式，未启用轨迹抓取（启动时加 --fetch-traces）。")
        remaining = self._cooldown_remaining()
        if remaining > 0:
            raise ValueError(f"Langfuse 限流冷却中，请 {remaining} 秒后重试。")
        rows = self._store.observations_for_batch(batch_id, round)
        if not rows:
            raise ValueError("未知批次，或该轮次没有证据。")
        work: list[dict[str, object]] = []
        seen: set[tuple[str, str]] = set()
        skipped = 0
        for observation in rows:
            target = _trace_target(observation)
            if target is None:
                skipped += 1
                continue
            trace_value = str(target["traceId"])
            run_value = str(target["runId"])
            key = (trace_value, run_value)
            if key in seen:
                # Baseline Create and retry rows can name the same run; fetch it once.
                skipped += 1
                continue
            seen.add(key)
            work.append(
                {
                    "caseId": _as_int(observation.get("caseId")),
                    "protocol": str(observation.get("protocol") or ""),
                    "traceId": trace_value,
                    "runId": run_value,
                    "batchId": batch_id,
                    "sourceRound": _as_int(observation.get("sourceRound"), 1),
                }
            )
        workers = max(1, min(FETCH_CONCURRENCY, int(concurrency or FETCH_CONCURRENCY)))
        counters = {"fetched": 0, "cached": 0, "failed": 0}
        errors: list[str] = []
        if work:
            with ThreadPoolExecutor(max_workers=workers) as pool:
                futures = [
                    pool.submit(self._fetch_round_item, item, force=force) for item in work
                ]
                for future in as_completed(futures):
                    outcome, message = future.result()
                    counters[outcome] += 1
                    if message:
                        errors.append(message)
        return {
            "batchId": batch_id,
            "round": round,
            "total": len(work),
            "fetched": counters["fetched"],
            "cached": counters["cached"],
            "skipped": skipped,
            "failed": counters["failed"],
            "stopped": self._cooldown_remaining() > 0,
            "concurrency": workers,
            "errors": errors[:5],
        }

    def export_trace_archive(self, batch_id: str, round: int | None = None) -> Path:
        """Zip the cached raw observations of one batch round, or of the whole batch.

        The archive is rebuilt on every request and overwrites the previous file of the same
        scope, so the export directory holds at most one pack per ``(batch, round)``.
        """
        rows = self._store.observations_for_batch(batch_id, round)
        if not rows:
            raise ValueError("未知批次，或该轮次没有证据。")
        trace_ids = [str(row.get("traceId") or "") for row in rows]
        suffix = f"r{round}" if round is not None else "all"
        safe_id = re.sub(r"[^0-9A-Za-z._-]", "_", batch_id)
        destination = self._runs_dir / "review" / "exports" / f"{safe_id}-{suffix}.zip"
        return self._trace_store.build_archive(trace_ids, destination)

    def _fetch_round_item(self, item: Mapping[str, object], *, force: bool) -> tuple[str, str]:
        """Fetch one round item and classify it; one failure never aborts the round.

        A cached snapshot still goes through :meth:`fetch_trace` so the evidence row of this batch
        gets attached; the classification only says whether an HTTP request was spent.
        """
        trace_value = str(item["traceId"])
        case_value = item.get("caseId")
        case_id = case_value if isinstance(case_value, int) else _as_int(case_value)
        cached = "" if force else self._trace_store.load(trace_value).export_state
        try:
            self.fetch_trace(
                case_id,
                str(item["protocol"]),
                trace_id=trace_value,
                run_id=str(item["runId"]),
                batch_id=str(item.get("batchId") or ""),
                source_round=_as_int(item.get("sourceRound"), 1),
                force=force,
            )
        except ValueError as exc:
            return "failed", f"{trace_value}: {exc}"
        except Exception as exc:  # noqa: BLE001 - one item must not abort the round
            return "failed", f"{trace_value}: {type(exc).__name__}: {exc}"
        if cached in _EXPORTED_STATES and not force:
            return "cached", ""
        return "fetched", ""

    def _attach_trace(
        self,
        observation: Mapping[str, object],
        target: Mapping[str, object],
    ) -> dict[str, object]:
        """Attach one run's snapshot to its evidence row and return the payload."""
        payload = self._trace_store.cached_payload(
            str(target["traceId"]),
            run_id=str(target["runId"]),
            nms_kind=_row_nms_kind(observation),
            local_points=_string_list(target.get("localPoints")),
            local_known=_string_list(target.get("localKnown")),
        )
        stored = self._store.set_trace(
            case_id=_as_int(observation.get("caseId")),
            protocol=str(observation.get("protocol") or ""),
            batch_id=str(observation.get("batchId") or ""),
            source_round=_as_int(observation.get("sourceRound"), 1),
            run_id=str(observation.get("runId") or ""),
            trace=payload,
            fetched_at=now_iso(),
        )
        if not stored:
            raise ValueError("该证据行已不在库中，请重新采集。")
        return payload

    def _cooldown_remaining(self) -> int:
        """Return the whole seconds left of the 429 cooldown, or ``0``."""
        remaining = self._fetch_cooldown_until - time.monotonic()
        return max(0, int(remaining + 0.999)) if remaining > 0 else 0

    def _resolved_trace_config(self) -> Config:
        """Return the Langfuse credentials the sample itself reports with.

        The sample does not use ``langfuse_export``'s own ``LANGFUSE_HOST``: it reads
        ``LANGFUSE_PUBLIC_KEY`` / ``LANGFUSE_SECRET_KEY`` / ``LANGFUSE_BASE_URL`` through
        :func:`shared.config.load_config`, and ``LANGFUSE_BASE_URL`` is what points at the local
        proxy that fronts ``us.cloud.langfuse.com``. Building the fetch config from the same values
        keeps the console and the run export talking to the same endpoint.
        """
        if self._trace_config is not None:
            return self._trace_config
        sample = self._sample_config()
        if not sample.langfuse_configured:
            raise ValueError(
                "未配置 Langfuse 凭据（LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY），"
                "或 A2AT_SAMPLE_LANGFUSE_ENABLED=0。"
            )
        self._trace_config = Config(
            host=sample.langfuse_base_url.rstrip("/"),
            public_key=sample.langfuse_public_key,
            secret_key=sample.langfuse_secret_key,
            # The sample's precedence is process env > .env > default; ``load_config`` already
            # merged it, so the origin only matters for diagnostics.
            host_source="env",
        )
        return self._trace_config

    def _sample_config(self) -> SampleConfig:
        """Load the sample configuration from the runs directory, then its parent, then cwd."""
        from shared.config import load_config as load_sample_config

        for candidate in (self._runs_dir / ".env", self._runs_dir.parent / ".env"):
            if candidate.is_file():
                try:
                    return load_sample_config(env_path=candidate)
                except ValueError as exc:
                    raise ValueError(f"无法读取 sample 配置 {candidate}：{exc}") from exc
        return load_sample_config()

    def trace_configured(self) -> bool:
        """Return whether this instance can resolve Langfuse credentials."""
        if self._trace_config is not None:
            return True
        try:
            self._resolved_trace_config()
        except ValueError:
            return False
        return True

    def cases(self) -> list[dict[str, object]]:
        """Return every case with its two protocol tasks, verdicts and decisions."""
        latest = self._store.latest_by_target()
        decisions = self._store.decisions()
        rows: list[dict[str, object]] = []
        for case in self._catalog.documents():
            case_id = int(case["id"])
            rows.append(
                {
                    "caseId": case_id,
                    "caseName": str(case.get("name") or ""),
                    "operation": case.get("operation"),
                    "dimension": case.get("dimension"),
                    "tags": list(case.get("tags") or ()),
                    "blocked": _is_blocked(case),
                    "negotiation": _negotiation_flags(case),
                    "protocols": [
                        self._protocol_row(
                            case,
                            case_id,
                            protocol,
                            latest.get((case_id, protocol)),
                            decisions.get((case_id, protocol)),
                        )
                        for protocol in PROTOCOLS
                    ],
                }
            )
        return rows

    def export_cases(self) -> list[dict[str, object]]:
        """Return the case projection with the latest round's detail attached.

        The list endpoint must stay lean, so the export — which needs the full Task-T texts and
        the trace comparison — re-judges the newest round of every target and attaches it here.
        """
        rows = self.cases()
        for row in rows:
            protocols = row.get("protocols")
            for task in protocols if isinstance(protocols, list) else ():
                if not isinstance(task, dict) or task.get("status") == STATUS_NOT_RUN:
                    continue
                detail = self.detail(_as_int(row.get("caseId")), str(task.get("protocol") or ""))
                rounds = detail.get("rounds")
                if not isinstance(rounds, list) or not rounds:
                    continue
                latest = rounds[0]
                judgement = latest.get("judgement")
                task["exportDetail"] = judgement.get("detail") if isinstance(judgement, Mapping) else {}
                task["exportRequests"] = latest.get("requests") or {}
        return rows

    def export_traces(self, raw: str | Sequence[str]) -> dict[str, object]:
        """Return the trace lookup with each judged run's full detail attached.

        The list must stay lean, so the export — which is a handoff artifact — re-judges every
        selected run and attaches the same detail the case view shows. Run-only and snapshot-only
        rows keep their degradation; nothing is invented to fill the gap.
        """
        result = self.resolve_traces(raw)
        rows = result.get("rows")
        for row in rows if isinstance(rows, list) else ():
            if not isinstance(row, dict):
                continue
            if row.get("resolution") not in ("store", "tester") or not row.get("caseId"):
                continue
            try:
                detail = self.trace_detail(
                    str(row.get("traceId") or ""),
                    run_id=str(row.get("runId") or "") or None,
                )
            except ValueError:
                continue
            rounds = detail.get("rounds")
            if not isinstance(rounds, list) or not rounds or not isinstance(rounds[0], Mapping):
                continue
            latest = rounds[0]
            judgement = latest.get("judgement")
            row["exportDetail"] = (
                judgement.get("detail") if isinstance(judgement, Mapping) else {}
            )
            row["exportRequests"] = latest.get("requests") or {}
            row["exportDecision"] = detail.get("decision") or {}
        return result

    def detail(self, case_id: int, protocol: str) -> dict[str, object]:
        """Return every round of one protocol task, judged, plus its decision."""
        case = self._catalog.get(case_id)
        if case is None:
            raise ValueError("未知用例。")
        if protocol not in PROTOCOLS:
            raise ValueError(f"protocol 只能是 {', '.join(PROTOCOLS)}。")
        observations = self._store.observations(case_id, protocol)
        rounds = [self._round_row(case, protocol, observation) for observation in observations]
        decision = self._decision_block(
            case,
            protocol,
            self._store.decision(DecisionKey(case_id, protocol)),
            rounds,
        )
        return {
            "case": _case_summary(case),
            "protocol": protocol,
            "rounds": rounds,
            "decision": decision,
            "corpusVersion": self._catalog.version,
        }

    def batches(self) -> list[dict[str, object]]:
        """Return the ingested batches, newest first, without judging them."""
        return self._store.batches()

    # ------------------------------------------------------------------
    # Trace / run lookup
    # ------------------------------------------------------------------

    def resolve_traces(self, raw: str | Sequence[str]) -> dict[str, object]:
        """Resolve one or many trace ids / run ids to their judged evidence.

        The lookup is deliberately not window-bound: a field report carries a run id that may be
        older than the acceptance window. A tester item found outside the window is ingested into
        the review store (the same write a scan makes), so the verdict, the trace snapshot and an
        eventual export all refer to one evidence row instead of an ephemeral file read.

        Rows whose evidence cannot support the six-dimension verdict are returned as ``client``
        (a run only) or ``trace`` (a snapshot only); they are never painted as a pass or a fail.
        """
        query = parse_trace_input(raw)
        if not query.ids:
            raise ValueError("没有解析到 traceId / runId。")
        cases = case_documents(self._catalog.documents())
        grouped, resolved = _group_observations(
            query.ids, self._store.observations_by_ids(query.ids)
        )
        unmatched = [value for value in query.ids if value not in resolved]
        lookup = TesterLookup((), (), {}, False)
        ingested: set[str] = set()
        inserted = 0
        if unmatched:
            lookup = find_tester_evidence(
                runs_dir=self._runs_dir,
                client_db_path=self._client_db_path,
                cases=cases,
                ids=unmatched,
            )
            inserted = self._store.ingest(lookup.observations)
            ingested = {str(row.get("runId") or "") for row in lookup.observations}
            if inserted:
                grouped, resolved = _group_observations(
                    query.ids, self._store.observations_by_ids(query.ids)
                )
        # A targeted ingest bypasses ``scan``, so attach the snapshots already on disk here. No
        # network: an uncollected trace stays missing and waits for the page's explicit fetch.
        cached = [row for rows in grouped.values() for row in rows]
        if cached and self._attach_traces(cached):
            grouped, resolved = _group_observations(
                query.ids, self._store.observations_by_ids(query.ids)
            )
        refs = {ref.input: ref for ref in lookup.refs}
        rows: list[dict[str, object]] = []
        covered: set[str] = set(resolved)
        for value in query.ids:
            matched = grouped.get(value) or []
            if matched:
                for observation in matched:
                    rows.append(
                        self._trace_row(
                            observation,
                            value,
                            cases=cases,
                            resolution=(
                                "tester"
                                if str(observation.get("runId") or "") in ingested
                                else "store"
                            ),
                        )
                    )
                continue
            if value in covered:
                # The same run was pasted both as its run id and its trace id; one row is enough.
                continue
            ref = refs.get(value)
            if (
                ref is not None
                and ref.has_evidence
                and ref.case_id is not None
                and ref.case_id not in cases
            ):
                covered.add(value)
                rows.append(self._missing_case_row(value, ref))
                continue
            client_rows = self._client_rows_for(value, ref, cases)
            if client_rows:
                covered.add(value)
                rows.extend(client_rows)
                continue
            trace_row = self._trace_only_row(value)
            if trace_row is not None:
                covered.add(value)
                rows.append(trace_row)
        unmatched_rows = [
            _unmatched_row(value, refs.get(value))
            for value in query.ids
            if value not in covered
        ]
        return {
            "query": {
                "ids": list(query.ids),
                "resolved": sorted(resolved),
                "inputs": query.inputs,
                "unique": len(query.ids),
                "dropped": query.dropped,
            },
            "rows": rows,
            "unmatched": unmatched_rows,
            "summary": _trace_summary(query, rows, unmatched_rows, inserted),
            "corpusVersion": self._catalog.version,
        }

    def trace_detail(self, trace_id: str, *, run_id: str | None = None) -> dict[str, object]:
        """Return one run's full detail, or the run / snapshot facts when no evidence exists.

        The response is ``mode: evidence`` with the same shape as :meth:`detail` when a judged row
        exists, so the page can reuse the case detail renderer. ``run`` and ``trace`` modes are the
        honest degradations: no tester stages means no six-dimension verdict.
        """
        trace_value = trace_id.strip()
        if not trace_value:
            raise ValueError("traceId 不能为空。")
        rows = self._store.observations_by_ids(
            [trace_value] + ([run_id] if run_id else [])
        )
        rows = [
            row
            for row in rows
            if _same_id(row.get("traceId"), trace_value) or _same_id(row.get("runId"), trace_value)
        ]
        if run_id:
            narrowed = [row for row in rows if _same_id(row.get("runId"), run_id)]
            if narrowed:
                rows = narrowed
        missing_case_hint = ""
        if rows:
            observation = rows[0]
            case_id = _as_int(observation.get("caseId"))
            protocol = str(observation.get("protocol") or "")
            case = self._catalog.get(case_id)
            if case is None:
                missing_case_hint = f"用例 {case_id} 不在当前语料，无法重判。"
            if case is not None:
                round_row = self._round_row(case, protocol, observation)
                decision = self._decision_block(
                    case,
                    protocol,
                    self._store.decision(DecisionKey(case_id, protocol)),
                    [{"runId": observation.get("runId"), "batchId": observation.get("batchId")}],
                    scope={"runId": observation.get("runId")},
                )
                return {
                    "mode": "evidence",
                    "case": _case_summary(case),
                    "protocol": protocol,
                    "rounds": [round_row],
                    "decision": decision,
                    "corpusVersion": self._catalog.version,
                    "traceId": str(observation.get("traceId") or trace_value),
                    "runId": str(observation.get("runId") or ""),
                    "relatedRunIds": [
                        str(row.get("runId") or "") for row in rows[1:] if row.get("runId")
                    ],
                    "trace": self._trace_brief(
                        trace_id=str(observation.get("traceId") or trace_value),
                        run_id=str(observation.get("runId") or ""),
                        observation=observation,
                    ),
                    "hints": [],
                }
        details = self._client_details(trace_value, run_id=run_id)
        payload = self._cached_trace_payload(trace_value, run_id)
        if not details and payload is None:
            raise ValueError("找不到该 traceId / runId 的证据或轨迹快照。")
        resolved_run = run_id or (str(details[0].get("runId") or "") if details else "")
        hints: list[str] = []
        if missing_case_hint:
            hints.append(missing_case_hint)
        if not details:
            hints.append("没有找到运行记录，仅展示已缓存的轨迹快照。")
        elif payload is None:
            hints.append("没有找到轨迹快照；该运行也没有可抓取的 traceId。")
        mode = "run" if details else "trace"
        return {
            "mode": mode,
            "traceId": trace_value,
            "runId": resolved_run,
            "run": details[0] if details else None,
            "runs": details,
            "relatedRunIds": [str(row.get("runId") or "") for row in details[1:]],
            "trace": self._trace_brief(trace_id=trace_value, run_id=resolved_run),
            "tracePayload": payload or {},
            "hints": hints,
            "corpusVersion": self._catalog.version,
        }

    def fetch_trace_by_id(
        self,
        trace_id: str,
        *,
        run_id: str | None = None,
        force: bool = False,
    ) -> dict[str, object]:
        """Fetch one trace without requiring an ingested case row.

        This is the trace view's entry point: a reviewer can paste a trace id from Langfuse and
        collect its snapshot even when the run is outside every tester batch. The snapshot stays a
        permanent cache, the 429 cooldown is shared with the case view, and an evidence row that
        names the trace is updated so the next render sees the snapshot too.
        """
        if not self._trace_fetch_enabled:
            raise ValueError("该实例是只读模式，未启用轨迹抓取（启动时加 --fetch-traces）。")
        trace_value = trace_id.strip()
        if not TRACE_ID_PATTERN.match(trace_value):
            raise ValueError("traceId 必须是 32 位十六进制。")
        if not force:
            cached = self._trace_store.load(trace_value)
            if cached.export_state in _EXPORTED_STATES:
                return self._attach_fetched_trace(trace_value, run_id)
        remaining = self._cooldown_remaining()
        if remaining > 0:
            raise ValueError(f"Langfuse 限流冷却中，请 {remaining} 秒后重试。")
        with self._fetch_lock:
            if not force:
                cached = self._trace_store.load(trace_value)
                if cached.export_state in _EXPORTED_STATES:
                    return self._attach_fetched_trace(trace_value, run_id)
            config = self._resolved_trace_config()
            result = self._trace_store.fetch(trace_value, config, run_id=run_id)
            if str(result.get("exportState") or "") != "exported":
                error = str(result.get("error") or result.get("exportState") or "")
                if _is_rate_limited(error):
                    self._fetch_cooldown_until = time.monotonic() + FETCH_COOLDOWN_SECONDS
                    raise ValueError(
                        f"Langfuse 限流（429），请 {int(FETCH_COOLDOWN_SECONDS)} 秒后重试。"
                    )
                raise ValueError(f"轨迹抓取失败：{error}")
            return self._attach_fetched_trace(trace_value, run_id)

    def _attach_fetched_trace(
        self,
        trace_id: str,
        run_id: str | None,
    ) -> dict[str, object]:
        """Attach a fetched snapshot to every evidence row that names the trace."""
        rows = self._store.observations_by_ids([trace_id])
        payload: dict[str, object] | None = None
        for observation in rows:
            target = _trace_target(observation)
            if target is None:
                continue
            try:
                attached = self._attach_trace(observation, target)
            except ValueError:
                continue
            if payload is None or _same_id(observation.get("runId"), run_id):
                payload = attached
        if payload is None:
            payload = self._cached_trace_payload(trace_id, run_id)
        if payload is None:
            raise ValueError("轨迹已抓取，但无法读取快照。")
        payload["fetchEnabled"] = self._trace_fetch_enabled
        payload["canFetch"] = True
        return payload

    def _trace_row(
        self,
        observation: Mapping[str, object],
        input_value: str,
        *,
        cases: Mapping[int, Mapping[str, Any]],
        resolution: str,
    ) -> dict[str, object]:
        """Build one trace-list row from a stored observation."""
        case_id = _as_int(observation.get("caseId"))
        protocol = str(observation.get("protocol") or "")
        case = cases.get(case_id)
        trace = self._trace_brief(
            trace_id=str(observation.get("traceId") or ""),
            run_id=str(observation.get("runId") or ""),
            observation=observation,
        )
        base: dict[str, object] = {
            "input": input_value,
            "traceId": str(observation.get("traceId") or ""),
            "runId": str(observation.get("runId") or ""),
            "caseId": case_id or None,
            "caseName": str(case.get("name") or "") if case is not None else "",
            "protocol": protocol,
            "batchId": str(observation.get("batchId") or ""),
            "sourceRound": _as_int(observation.get("sourceRound"), 1),
            "observedAt": observation.get("observedAt"),
            "model": observation.get("model"),
            "decisionMode": observation.get("decisionMode"),
            "nms": bool(observation.get("nms")),
            "runStatus": str(observation.get("status") or ""),
            "resolution": resolution,
            "trace": trace,
            "hints": [],
        }
        if case is None:
            base.update(
                {
                    "status": STATUS_NOT_RUN,
                    "dimensions": [],
                    "failures": [],
                    "decision": _pending_decision_block(),
                    "hints": [f"用例 {case_id} 不在当前语料，无法重判。"],
                }
            )
            return base
        row = self._protocol_row(
            case,
            case_id,
            protocol,
            observation,
            self._store.decision(DecisionKey(case_id, protocol)),
            scope={"runId": observation.get("runId")},
        )
        row.update(base)
        row["trace"] = trace
        return row

    def _missing_case_row(self, input_value: str, ref: TesterRunRef) -> dict[str, object]:
        """Return a row for a tester item whose case left the corpus."""
        return {
            "input": input_value,
            "traceId": ref.trace_id,
            "runId": ref.run_id,
            "caseId": ref.case_id,
            "caseName": "",
            "protocol": ref.protocol,
            "batchId": ref.batch_id,
            "sourceRound": None,
            "observedAt": "",
            "model": None,
            "decisionMode": "",
            "nms": False,
            "runStatus": "",
            "status": STATUS_NOT_RUN,
            "dimensions": [],
            "failures": [],
            "resolution": "case_missing",
            "trace": self._trace_brief(trace_id=ref.trace_id, run_id=ref.run_id),
            "decision": _pending_decision_block(),
            "hints": [f"用例 {ref.case_id} 不在当前语料，无法重判。"],
        }

    def _client_rows_for(
        self,
        input_value: str,
        ref: TesterRunRef | None,
        cases: Mapping[int, Mapping[str, Any]],
    ) -> list[dict[str, object]]:
        """Return run-only rows for an id that has no tester evidence."""
        return [
            self._client_row(detail, input_value, cases)
            for detail in self._client_details(input_value, run_id=None, ref=ref)
        ]

    def _client_details(
        self,
        input_value: str,
        *,
        run_id: str | None,
        ref: TesterRunRef | None = None,
    ) -> list[dict[str, object]]:
        """Return the client-store run records that match one id.

        A run id is looked up exactly. A trace id has no exact run column in the client store, so
        it is resolved through the existing ``trace_id`` filter; one workflow trace can therefore
        return several runs.
        """
        if self._client_db_path is None:
            return []
        candidates = [value for value in (run_id, input_value) if value]
        details: list[dict[str, object]] = []
        seen: set[str] = set()
        for candidate in candidates:
            detail = read_client_run_detail(self._client_db_path, candidate)
            if detail is None:
                continue
            resolved = str(detail.get("runId") or "")
            if resolved and resolved not in seen:
                seen.add(resolved)
                details.append(detail)
        if details:
            return details
        if ref is not None and ref.run_id:
            detail = read_client_run_detail(self._client_db_path, ref.run_id)
            if detail is not None:
                return [detail]
        if TRACE_ID_PATTERN.match(input_value):
            for summary in read_client_run_summaries(
                self._client_db_path, trace_id=input_value, limit=50
            ):
                resolved = str(summary.get("runId") or "")
                if not resolved or resolved in seen:
                    continue
                seen.add(resolved)
                detail = read_client_run_detail(self._client_db_path, resolved)
                details.append(detail if detail is not None else summary)
        return details

    def _client_row(
        self,
        detail: Mapping[str, object],
        input_value: str,
        cases: Mapping[int, Mapping[str, Any]],
    ) -> dict[str, object]:
        """Build one run-only row from a client-store record."""
        source = detail.get("source")
        source_map = source if isinstance(source, Mapping) else {}
        case_id = _as_int(source_map.get("caseId")) or _as_int(detail.get("caseId"))
        batch_id = str(source_map.get("batchId") or detail.get("batchId") or "")
        protocol = str(detail.get("protocol") or "")
        run_id = str(detail.get("runId") or "")
        trace_id = str(detail.get("traceId") or "")
        case = cases.get(case_id) if case_id else None
        decision = (
            self._decision_block(
                case,
                protocol,
                self._store.decision(DecisionKey(case_id, protocol)),
                [{"runId": run_id}],
                scope={"runId": run_id},
            )
            if case is not None
            else _pending_decision_block()
        )
        return {
            "input": input_value,
            "traceId": trace_id,
            "runId": run_id,
            "caseId": case_id or None,
            "caseName": str(case.get("name") or "") if case is not None else "",
            "protocol": protocol,
            "batchId": batch_id,
            "sourceRound": None,
            "observedAt": detail.get("createdAt"),
            "model": None,
            "decisionMode": "",
            "nms": False,
            "runStatus": str(detail.get("status") or ""),
            "finalState": detail.get("finalState"),
            "status": STATUS_NOT_RUN,
            "dimensions": [],
            "failures": [],
            "resolution": "client",
            "trace": self._trace_brief(trace_id=trace_id, run_id=run_id),
            "decision": decision,
            "hints": ["没有 tester 阶段证据，未做六维判定。"],
        }

    def _trace_only_row(self, input_value: str) -> dict[str, object] | None:
        """Return a snapshot-only row for a valid trace id with no run record."""
        if not TRACE_ID_PATTERN.match(input_value):
            return None
        trace = self._trace_brief(trace_id=input_value, run_id="")
        trace["applicable"] = "no"
        trace["text"] = (
            "只有轨迹快照，没有运行证据可对照。"
            if trace.get("present")
            else "未找到运行证据，可抓取轨迹查看。"
        )
        return {
            "input": input_value,
            "traceId": input_value,
            "runId": "",
            "caseId": None,
            "caseName": "",
            "protocol": "",
            "batchId": "",
            "sourceRound": None,
            "observedAt": "",
            "model": None,
            "decisionMode": "",
            "nms": False,
            "runStatus": "",
            "status": STATUS_NOT_RUN,
            "dimensions": [],
            "failures": [],
            "resolution": "trace",
            "trace": trace,
            "decision": _pending_decision_block(),
            "hints": [str(trace.get("text") or "")],
        }

    def _trace_brief(
        self,
        *,
        trace_id: str,
        run_id: str,
        observation: Mapping[str, object] | None = None,
    ) -> dict[str, object]:
        """Return the trace state of one row without fetching anything."""
        applicability = (
            _row_applicability(observation)
            if observation is not None
            else trace_applicability(run_id, trace_id)
        )
        trace = observation.get("trace") if observation is not None else None
        present = isinstance(trace, Mapping) and trace.get("present") is True
        export_state = str(trace.get("exportState") or "") if isinstance(trace, Mapping) else ""
        captured_at = str(trace.get("capturedAt") or "") if isinstance(trace, Mapping) else ""
        if trace_id and TRACE_ID_PATTERN.match(trace_id) and (not present or not export_state):
            try:
                snapshot = self._trace_store.load(trace_id)
            except (InvalidTraceIdError, OSError):
                snapshot = None
            if snapshot is not None:
                present = present or snapshot.export_state in _EXPORTED_STATES
                export_state = export_state or snapshot.export_state
                captured_at = captured_at or str(
                    getattr(snapshot, "captured_at", "") or ""
                )
        return {
            "traceId": trace_id,
            "present": present,
            "exportState": export_state,
            "capturedAt": captured_at,
            "canFetch": bool(
                self._trace_fetch_enabled
                and trace_id
                and TRACE_ID_PATTERN.match(trace_id)
            ),
            "fetchEnabled": self._trace_fetch_enabled,
            "applicable": applicability.applicable,
            "text": applicability.text,
        }

    def _cached_trace_payload(
        self,
        trace_id: str,
        run_id: str | None,
    ) -> dict[str, object] | None:
        """Return the on-disk snapshot of one trace, without a network call."""
        if not TRACE_ID_PATTERN.match(trace_id):
            return None
        observation = self._observation_for_trace(trace_id, run_id)
        try:
            payload = self._trace_store.cached_payload(
                trace_id,
                run_id=run_id
                or (str(observation.get("runId") or "") if observation is not None else None),
                nms_kind=(
                    _row_nms_kind(observation)
                    if observation is not None
                    else nms_kind(None, None)
                ),
                local_points=_local_points(observation),
                local_known=_local_known(observation),
            )
        except (InvalidTraceIdError, OSError):
            return None
        payload["fetchEnabled"] = self._trace_fetch_enabled
        payload["canFetch"] = self._trace_fetch_enabled
        return payload

    def _observation_for_trace(
        self,
        trace_id: str,
        run_id: str | None,
    ) -> dict[str, object] | None:
        """Return one stored observation naming the trace, preferring ``run_id``."""
        rows = self._store.observations_by_ids([trace_id] + ([run_id] if run_id else []))
        for row in rows:
            if run_id and _same_id(row.get("runId"), run_id):
                return row
        for row in rows:
            if _same_id(row.get("traceId"), trace_id):
                return row
        return None

    def batch_rows(self, batch_id: str, round: int | None = None) -> dict[str, object]:
        """Judge every case × protocol against one batch, optionally narrowed to one round.

        Rows come from the corpus, not from the evidence: a case the batch never ran appears as
        ``not_run``, so the coverage of the round stays visible instead of reading as a pass.
        """
        observations = self._store.observations_for_batch(batch_id, round)
        if not observations:
            raise ValueError("未知批次，或该轮次没有证据。")
        by_target: dict[tuple[int, str], dict[str, object]] = {}
        attempts: dict[tuple[int, str], int] = {}
        for observation in observations:
            key = (_as_int(observation.get("caseId")), str(observation.get("protocol") or ""))
            attempts[key] = attempts.get(key, 0) + 1
            by_target.setdefault(key, observation)
        decisions = self._store.decisions()
        scope: dict[str, object] = {"batchId": batch_id, "round": round}
        observed = 0
        rows: list[dict[str, object]] = []
        documents = self._catalog.documents()
        for case in documents:
            case_id = int(case.get("id") or 0)
            tasks: list[dict[str, object]] = []
            for protocol in PROTOCOLS:
                observation = by_target.get((case_id, protocol))
                if observation is None:
                    tasks.append(
                        self._protocol_row(
                            case,
                            case_id,
                            protocol,
                            None,
                            decisions.get((case_id, protocol)),
                            scope=scope,
                        )
                    )
                    continue
                observed += 1
                tasks.append(
                    self._protocol_row(
                        case,
                        case_id,
                        protocol,
                        {**observation, "observations": attempts.get((case_id, protocol), 1)},
                        decisions.get((case_id, protocol)),
                        scope=scope,
                    )
                )
            rows.append(
                {
                    "caseId": case_id,
                    "caseName": str(case.get("name") or ""),
                    "operation": case.get("operation"),
                    "dimension": case.get("dimension"),
                    "tags": list(case.get("tags") or ()),
                    "blocked": _is_blocked(case),
                    "negotiation": _negotiation_flags(case),
                    "protocols": tasks,
                }
            )
        summary = self._batch_summary(batch_id)
        progress = self.progress(rows)
        return {
            "batch": {**summary, "round": round},
            "cases": rows,
            "coverage": {
                "observed": observed,
                "targets": len(documents) * len(PROTOCOLS),
                "cases": summary.get("cases"),
            },
            **progress,
            "corpusVersion": self._catalog.version,
        }

    def _batch_summary(self, batch_id: str) -> dict[str, object]:
        """Return one batch's metadata, or raise when it has no evidence."""
        for batch in self._store.batches():
            if batch.get("batchId") == batch_id:
                return batch
        raise ValueError("未知批次。")

    def progress(self, rows: Sequence[Mapping[str, object]] | None = None) -> dict[str, object]:
        """Return the protocol-task and decision counters for the header."""
        cases = rows if rows is not None else self.cases()
        statuses: dict[str, int] = {status: 0 for status in STATUSES}
        decisions: dict[str, int] = {status: 0 for status in DECISIONS}
        for case in cases:
            protocols = case.get("protocols")
            if not isinstance(protocols, list):
                continue
            for task in protocols:
                if not isinstance(task, Mapping):
                    continue
                status = str(task.get("status") or STATUS_NOT_RUN)
                statuses[status] = statuses.get(status, 0) + 1
                decision = task.get("decision")
                decided = (
                    str(decision.get("status") or DECISION_PENDING)
                    if isinstance(decision, Mapping)
                    else DECISION_PENDING
                )
                decisions[decided] = decisions.get(decided, 0) + 1
        return {
            "statuses": statuses,
            "decisions": decisions,
            "targets": sum(statuses.values()),
            "caseCount": len(cases),
        }

    def set_decision(
        self,
        case_id: int,
        protocol: str,
        *,
        status: str,
        category: str = "",
        note: str = "",
        reviewer: str = "",
        actor: str = "",
    ) -> dict[str, object]:
        """Record one human decision, rejecting an unexplained override of the verdict."""
        case = self._catalog.get(case_id)
        if case is None:
            raise ValueError("未知用例。")
        if protocol not in PROTOCOLS:
            raise ValueError(f"protocol 只能是 {', '.join(PROTOCOLS)}。")
        if status not in DECISIONS:
            raise ValueError(f"status 只能是 {', '.join(DECISIONS)}。")
        if status == DECISION_DEFECT and category not in CATEGORIES:
            raise ValueError(f"缺陷归属只能是 {', '.join(CATEGORIES)}。")
        if status != DECISION_DEFECT and category:
            raise ValueError("只有缺陷结论可以填写归属。")
        note = note.strip()
        if status in (DECISION_DEFECT, DECISION_KNOWN_ISSUE) and not note:
            raise ValueError("缺陷与已知问题必须填写备注。")
        key = DecisionKey(case_id, protocol)
        evidence_rows = self._store.observations(case_id, protocol)
        rounds = [self._round_row(case, protocol, row) for row in evidence_rows]
        auto = _auto_status(rounds)
        if status == DECISION_ACCEPTED and auto != VERDICT_PASS and not note:
            raise ValueError("自动判定不是通过，确认通过时必须填写理由。")
        if status == DECISION_DEFECT and auto == VERDICT_PASS and not note:
            raise ValueError("自动判定通过，判为缺陷时必须填写理由。")
        evidence = {
            "runIds": [row.get("runId") for row in rounds if row.get("runId")],
            "batchIds": [row.get("batchId") for row in rounds],
            "models": sorted({str(row.get("model")) for row in rounds if row.get("model")}),
            "modes": sorted({str(row.get("decisionMode")) for row in rounds}),
            "nms": any(bool(row.get("nms")) for row in rounds),
            "autoStatus": auto,
            #: The per-dimension verdicts at confirmation time. A later round that reaches the same
            #: overall verdict through different dimensions still needs a recheck.
            "dimensions": _dimension_statuses(rounds[0]) if rounds else {},
            "observedAt": str(evidence_rows[0].get("observedAt") or "") if evidence_rows else "",
        }
        self._store.set_decision(
            key,
            status=status,
            category=category,
            note=note,
            reviewer=reviewer,
            evidence=evidence,
            case_signature=case_signature(case),
            corpus_version=self._catalog.version,
            actor=actor,
        )
        return self.detail(case_id, protocol)

    # ------------------------------------------------------------------
    # Projection helpers
    # ------------------------------------------------------------------

    def _evidence_with_current_trace(self, observation: Mapping[str, object]) -> dict[str, object]:
        """Return one observation's judged evidence with a current trace block.

        The trace block is a normalized projection stored at scan time. A normalizer fix (the
        Langfuse v4 ``detail.*`` flattening, the nested status ``event``) only reaches old rows
        when the scan runs again, so a stored block of an older schema is re-derived from the
        snapshot on disk before judging. No network and no hashing: the snapshot is the same
        evidence, only read with today's field mapping.
        """
        trace = observation.get("trace")
        schema = trace.get("schema") if isinstance(trace, Mapping) else None
        if not isinstance(trace, Mapping) or not isinstance(schema, str) or schema == SNAPSHOT_SCHEMA:
            # No stored payload, or one written before the schema was versioned (a test double).
            return _judged_evidence(observation)
        updated = dict(observation)
        fresh = self._current_trace_payload(observation, trace)
        if fresh is not None:
            updated["trace"] = fresh
        return _judged_evidence(updated)

    def _current_trace_payload(
        self,
        observation: Mapping[str, object],
        stored: Mapping[str, object],
    ) -> dict[str, object] | None:
        """Return the stored payload rebuilt from disk, or ``None`` when that is not possible."""
        trace_id = str(observation.get("traceId") or stored.get("traceId") or "")
        if not TRACE_ID_PATTERN.match(trace_id):
            return None
        run_id = str(observation.get("runId") or "")
        key = (trace_id, run_id)
        cached = self._trace_payload_cache.get(key)
        if cached is not None:
            return cached
        try:
            payload = self._trace_store.cached_payload(
                trace_id,
                run_id=run_id or None,
                nms_kind=_row_nms_kind(observation),
                local_points=_local_points(observation),
                local_known=_local_known(observation),
                hashes=False,
            )
        except (InvalidTraceIdError, OSError):
            return None
        if str(payload.get("exportState") or "") not in {"exported", "partial"}:
            return None
        self._trace_payload_cache[key] = payload
        return payload

    def _protocol_row(
        self,
        case: Mapping[str, Any],
        case_id: int,
        protocol: str,
        observation: Mapping[str, object] | None,
        decision: Mapping[str, object] | None,
        *,
        scope: Mapping[str, object] | None = None,
    ) -> dict[str, object]:
        """Build one protocol task row for the list and the header counters.

        ``scope`` names the batch/round the row is projected from; the decision block then reports
        whether the stored decision was made from that evidence.
        """
        if observation is None:
            return {
                "protocol": protocol,
                "status": STATUS_NOT_RUN,
                "dimensions": [],
                "observations": 0,
                "observedAt": "",
                "model": None,
                "decisionMode": "",
                "nms": False,
                "data": [],
                "traceId": "",
                "langfuseUrl": "",
                "decision": self._decision_block(case, protocol, decision, [], scope=scope),
            }
        evidence_map = self._evidence_with_current_trace(observation)
        langfuse_url = evidence_map.get("langfuseUrl")
        # The verdict is identified by the case id it is judged under. The operator text a run was
        # produced from is frozen in its evidence and shown in the round detail, but a case edited
        # in place must not turn the whole history into "rerun" noise.
        judgement = judge_protocol(case, protocol, evidence_map)
        status = judgement.status
        trace_reasons: list[str] = []
        trace_dimension = judgement.dimension(DIMENSION_TRACE)
        if trace_dimension is not None:
            # One round can fail for several reasons at once; the report counts each of them.
            trace_reasons = list(
                dict.fromkeys(str(diff.get("reason") or "") for diff in trace_dimension.diffs if diff.get("reason"))
            )
        return {
            "protocol": protocol,
            "status": status,
            "dimensions": [
                {"dimension": row.dimension, "status": row.status, "reason": row.reason} for row in judgement.dimensions
            ],
            "failures": [
                {
                    "dimension": row.dimension,
                    "status": row.status,
                    "reason": row.reason,
                    "diffs": [dict(diff) for diff in row.diffs][:20],
                }
                for row in judgement.dimensions
                if row.status in (VERDICT_FAIL, VERDICT_PARTIAL, VERDICT_MISSING, VERDICT_RECORDED)
            ],
            "observations": _as_int(observation.get("observations"), 1),
            "observedAt": observation.get("observedAt"),
            "model": observation.get("model"),
            "decisionMode": observation.get("decisionMode"),
            "nms": bool(observation.get("nms")),
            "traceReason": trace_reasons[0] if trace_reasons else "",
            "traceReasons": trace_reasons,
            # The key-data table is one row per case × protocol. Keep the presence status and the
            # copy names only: the verbatim text stays in the round detail so a 200-case batch
            # does not duplicate every Task-T into the list payload.
            "data": _compact_data(judgement.detail.get("trace") if isinstance(judgement.detail, Mapping) else None),
            # The data table replaces its verdict column with the Langfuse trace link: the
            # acceptance evidence is the exported trace, and one click should open it.
            "traceId": str(observation.get("traceId") or ""),
            "langfuseUrl": langfuse_url if isinstance(langfuse_url, str) else "",
            "decision": self._decision_block(
                case,
                protocol,
                decision,
                [
                    {
                        "runId": observation.get("runId"),
                        "batchId": observation.get("batchId"),
                        # The verdict comparison needs this row's judgement; the identity fields
                        # alone can only say which run the decision was based on.
                        "judgement": judgement.to_dict(),
                    }
                ],
                scope=scope,
            ),
        }

    def _round_row(
        self,
        case: Mapping[str, Any],
        protocol: str,
        observation: Mapping[str, object],
    ) -> dict[str, object]:
        """Build one judged evidence round for the detail payload."""
        evidence_map = self._evidence_with_current_trace(observation)
        judgement: ProtocolJudgement = judge_protocol(case, protocol, evidence_map)
        detail = judgement.to_dict()
        _mark_fetch_state(detail, self._trace_fetch_enabled)
        self._mark_snapshot_state(detail, observation)
        return {
            "id": observation.get("id"),
            "batchId": observation.get("batchId"),
            "sourceRound": observation.get("sourceRound"),
            "runId": observation.get("runId"),
            "traceId": observation.get("traceId"),
            "observedAt": observation.get("observedAt"),
            "model": observation.get("model"),
            "decisionMode": observation.get("decisionMode"),
            "nms": bool(observation.get("nms")),
            "nmsSim": bool(observation.get("nmsSim")),
            "discovery": observation.get("discovery"),
            "status": observation.get("status"),
            "judgement": detail,
            "requests": _request_sources(case, evidence_map),
        }

    def _mark_snapshot_state(
        self,
        judgement: dict[str, Any],
        observation: Mapping[str, object],
    ) -> None:
        """Reflect a failed or on-disk snapshot that the store has not attached yet.

        A failed fetch leaves only a ``failure.json`` marker, so the stored evidence still has no
        trace. The page must say "上次抓取失败" and offer a retry instead of pretending the trace
        was never requested.
        """
        block = judgement.get("detail")
        trace = block.get("trace") if isinstance(block, dict) else None
        if not isinstance(trace, dict) or trace.get("present"):
            return
        trace_id = str(observation.get("traceId") or "")
        if not trace_id:
            return
        try:
            snapshot = self._trace_store.load(trace_id)
        except (InvalidTraceIdError, OSError):
            return
        state = str(snapshot.export_state or "")
        if state in _EXPORTED_STATES:
            trace["exportState"] = state
            trace["blockers"] = []
        elif state == "failed":
            trace["exportState"] = "failed"
            trace["blockers"] = [
                {
                    "code": "trace_snapshot_failed",
                    "text": "上次抓取失败，请重试",
                    "action": "retry",
                }
            ]

    def _decision_block(
        self,
        case: Mapping[str, Any],
        protocol: str,
        decision: Mapping[str, object] | None,
        rounds: Sequence[Mapping[str, object]],
        *,
        scope: Mapping[str, object] | None = None,
    ) -> dict[str, object]:
        """Return the decision together with its staleness flags.

        With ``scope`` (the batch view) the block also reports whether the decision's evidence
        includes the scoped run, and stops claiming "new evidence": that flag compares against the
        newest run of the whole corpus, which is meaningless for one batch.

        ``verdictChanged`` compares the newest round with the verdict the decision recorded. A
        newer round inherits the human conclusion only when it reached the same automatic verdict;
        a different status or dimension keeps the conclusion saved but asks for a recheck.
        """
        if decision is None:
            return {
                "status": DECISION_PENDING,
                "category": "",
                "note": "",
                "reviewer": "",
                "confirmedAt": "",
                "evidence": {},
                "stale": False,
                "staleReason": "",
                "newEvidence": False,
                "verdictChanged": False,
                "verdictChange": {"status": None, "dimensions": []},
                "onThisRound": None,
            }
        stale_reason = ""
        if str(decision.get("caseSignature") or "") != case_signature(case):
            stale_reason = "用例或期望已变更，结论需复核"
        evidence = decision.get("evidence")
        evidence_map = evidence if isinstance(evidence, Mapping) else {}
        run_ids = evidence_map.get("runIds")
        known = {str(value) for value in run_ids} if isinstance(run_ids, list) else set()
        latest = rounds[0].get("runId") if rounds else None
        newest = str(latest or "")
        on_this_round: bool | None = None
        if scope is None:
            new_evidence = bool(newest) and newest not in known
        else:
            on_this_round = bool(newest) and newest in known
            new_evidence = False
        verdict_changed, verdict_change = _verdict_change(evidence_map, rounds)
        return {
            "status": str(decision.get("status") or DECISION_PENDING),
            "category": str(decision.get("category") or ""),
            "note": str(decision.get("note") or ""),
            "reviewer": str(decision.get("reviewer") or ""),
            "confirmedAt": str(decision.get("confirmedAt") or ""),
            "evidence": dict(evidence_map),
            "stale": bool(stale_reason),
            "staleReason": stale_reason,
            "newEvidence": new_evidence,
            "verdictChanged": verdict_changed,
            "verdictChange": verdict_change,
            "onThisRound": on_this_round,
        }


def _group_observations(
    ids: Sequence[str],
    observations: Sequence[Mapping[str, object]],
) -> tuple[dict[str, list[dict[str, object]]], set[str]]:
    """Group evidence rows by the first query id that names them.

    A run id and its trace id are often both pasted. The row is shown once, under the first id,
    while every matching id counts as resolved so the second one does not fall through to a run-only
    or snapshot-only row.
    """
    grouped: dict[str, list[dict[str, object]]] = {value: [] for value in ids}
    resolved: set[str] = set()
    for observation in observations:
        matches = [
            value
            for value in ids
            if _same_id(value, observation.get("runId"))
            or _same_id(value, observation.get("traceId"))
        ]
        if not matches:
            continue
        grouped[matches[0]].append(dict(observation))
        resolved.update(matches)
    return grouped, resolved


def _same_id(value: object, other: object) -> bool:
    """Compare two ids, folding case only for 32-hex trace ids."""
    left = str(value or "").strip()
    right = str(other or "").strip()
    if not left or not right:
        return False
    if left == right:
        return True
    return bool(
        TRACE_ID_PATTERN.match(left)
        and TRACE_ID_PATTERN.match(right)
        and left.lower() == right.lower()
    )


def _pending_decision_block() -> dict[str, object]:
    """Return the empty decision block the page renders for a target without a decision."""
    return {
        "status": DECISION_PENDING,
        "category": "",
        "note": "",
        "reviewer": "",
        "confirmedAt": "",
        "evidence": {},
        "stale": False,
        "staleReason": "",
        "newEvidence": False,
        "verdictChanged": False,
        "verdictChange": {"status": None, "dimensions": []},
        "onThisRound": None,
    }


def _unmatched_row(value: str, ref: TesterRunRef | None) -> dict[str, object]:
    """Return the explanation for an id no source could resolve."""
    if ref is not None and not ref.has_evidence:
        kind = "基线" if ref.matched == "baseline" else "重试"
        return {
            "input": value,
            "reason": "tester_identity_only",
            "hint": (
                f"只出现在 tester 批次 {ref.batch_id} 的{kind}记录中；"
                "客户端库没有这次运行的记录。"
            ),
        }
    return {
        "input": value,
        "reason": "not_found",
        "hint": "审核库、tester 批次和客户端库都没有这个 id；确认是否正确，或现场证据文件已轮转。",
    }


def _trace_summary(
    query: TraceQuery,
    rows: Sequence[Mapping[str, object]],
    unmatched: Sequence[Mapping[str, object]],
    inserted: int,
) -> dict[str, object]:
    """Return the counters the trace view header shows."""
    dimensions: dict[str, int] = {name: 0 for name in TRACE_DIMENSIONS}
    judged = 0
    runs = 0
    trace_missing = 0
    for row in rows:
        if row.get("runId"):
            runs += 1
        trace = row.get("trace")
        trace_map = trace if isinstance(trace, Mapping) else {}
        if not trace_map.get("present") and trace_map.get("canFetch"):
            trace_missing += 1
        if row.get("resolution") not in ("store", "tester") or not row.get("caseId"):
            continue
        if row.get("status") != STATUS_NOT_RUN:
            judged += 1
        dimensions_rows = row.get("dimensions")
        if not isinstance(dimensions_rows, list):
            continue
        for dimension in dimensions_rows:
            if not isinstance(dimension, Mapping):
                continue
            if dimension.get("status") not in _ATTENTION_STATUSES:
                continue
            name = str(dimension.get("dimension") or "")
            if name in dimensions:
                dimensions[name] += 1
    return {
        "inputs": query.inputs,
        "unique": len(query.ids),
        "dropped": query.dropped,
        "runs": runs,
        "judged": judged,
        "unmatched": len(unmatched),
        "traceMissing": trace_missing,
        "ingested": inserted,
        "dimensions": dimensions,
    }


def _local_points(observation: Mapping[str, object] | None) -> list[str] | None:
    """Return the locally provable trace points of one observation."""
    if observation is None:
        return None
    evidence = observation.get("evidence")
    value = evidence.get("localPoints") if isinstance(evidence, Mapping) else None
    return _string_list(value)


def _local_known(observation: Mapping[str, object] | None) -> list[str] | None:
    """Return the trace points one observation's local evidence can adjudicate."""
    if observation is None:
        return None
    evidence = observation.get("evidence")
    value = evidence.get("localKnown") if isinstance(evidence, Mapping) else None
    return _string_list(value)


def _as_int(value: object, default: int = 0) -> int:
    """Return ``value`` as an integer, or ``default`` when it is not one."""
    if isinstance(value, bool):
        return default
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return default


def _mark_fetch_state(detail: dict[str, Any], fetch_enabled: bool) -> None:
    """Attach the instance's fetch capability to the trace block of one judged round.

    ``judge_protocol`` is pure and cannot know whether this process may call Langfuse. The page
    needs that bit to decide whether the fetch button is enabled and whether the automatic load
    runs at all, so the service adds it here instead of duplicating the rule in JavaScript.
    """
    block = detail.get("detail")
    if not isinstance(block, dict):
        return
    trace = block.get("trace")
    if not isinstance(trace, dict):
        return
    applicability = trace.get("applicability")
    applicable = isinstance(applicability, Mapping) and applicability.get("applicable") == "yes"
    trace["fetchEnabled"] = fetch_enabled
    trace["canFetch"] = bool(fetch_enabled and applicable)


def _select_round(
    rows: Sequence[Mapping[str, object]],
    *,
    trace_id: str | None,
    run_id: str | None,
    batch_id: str | None,
    source_round: int | None,
) -> Mapping[str, object] | None:
    """Return the evidence row a fetch request names, defaulting to the newest one.

    The page always sends the trace id of the round the reviewer is looking at, so a multi-round
    batch can no longer fetch the newest round while showing an older card. Every selector must
    match an already-ingested row: the endpoint never fetches an arbitrary trace id.
    """
    for row in rows:
        if trace_id and str(row.get("traceId") or "") != trace_id:
            continue
        if run_id and str(row.get("runId") or "") != run_id:
            continue
        if batch_id and str(row.get("batchId") or "") != batch_id:
            continue
        if source_round is not None and _as_int(row.get("sourceRound"), 1) != source_round:
            continue
        return row
    return None


def _is_rate_limited(error: str) -> bool:
    """Return whether a fetch error is a Langfuse rate-limit response."""
    lowered = error.lower()
    return "429" in lowered or "rate limit" in lowered or "rate_limit" in lowered


def _trace_target(observation: Mapping[str, object]) -> dict[str, object] | None:
    """Return the trace lookup of one observation, or ``None`` when it is not judgeable.

    A run that dispatched and carries a Langfuse trace id has a trace to look up. The declared
    downstream is deliberately not checked here: batches from before the tester recorded
    ``nms``/``nmsSim`` are still real-machine runs, and refusing them would leave the console
    unable to fetch the very evidence the acceptance gate needs.
    """
    applicability = _row_applicability(observation)
    if not applicability.applicable:
        return None
    evidence = observation.get("evidence")
    evidence_map = evidence if isinstance(evidence, Mapping) else {}
    return {
        "runId": applicability.run_id,
        "traceId": applicability.trace_id,
        "localPoints": evidence_map.get("localPoints"),
        "localKnown": evidence_map.get("localKnown"),
    }


def _row_nms_kind(observation: Mapping[str, object]) -> str:
    """Return the display-only downstream identity of one stored evidence row."""
    declared = observation.get("nmsDeclared")
    simulated = observation.get("nmsSimDeclared")
    return nms_kind(
        declared if isinstance(declared, bool) else None,
        simulated if isinstance(simulated, bool) else None,
    )


def _row_applicability(observation: Mapping[str, object]) -> TraceApplicability:
    """Return the trace applicability of one stored evidence row."""
    return trace_applicability(
        observation.get("runId"),
        observation.get("traceId"),
        simulated=observation.get("nmsSimDeclared") is True,
    )


def _string_list(value: object) -> list[str] | None:
    """Return a list of strings, or ``None`` when the value is not a list."""
    if not isinstance(value, list):
        return None
    return [str(item) for item in value]


def _compact_data(trace: object) -> list[dict[str, object]]:
    """Return the batch-level key-data row without the verbatim copy text."""
    block = trace if isinstance(trace, Mapping) else {}
    rows = block.get("data")
    if not isinstance(rows, list):
        return []
    compact: list[dict[str, object]] = []
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        copies = row.get("copies")
        compact.append(
            {
                "id": str(row.get("id") or ""),
                "label": str(row.get("label") or ""),
                "required": bool(row.get("required")),
                "applicable": row.get("applicable") is not False,
                "status": str(row.get("status") or ""),
                "present": bool(row.get("present")),
                "copies": [
                    {
                        "source": str(copy.get("source") or ""),
                        "span": str(copy.get("span") or ""),
                        "runId": str(copy.get("runId") or ""),
                        "side": str(copy.get("side") or ""),
                        "round": copy.get("round"),
                        "verdict": str(copy.get("verdict") or ""),
                        "expected": str(copy.get("expected") or ""),
                    }
                    for copy in copies
                    if isinstance(copy, Mapping)
                ]
                if isinstance(copies, list)
                else [],
            }
        )
    return compact


def _judged_evidence(observation: Mapping[str, object]) -> dict[str, object]:
    """Merge the row's trace snapshot and declared downstream into the stored evidence.

    The tester item carries neither: the trace lives in its own column and the declared downstream
    flags are batch properties. ``judge_protocol`` stays a pure function over one mapping, so the
    service is the only place that knows the store layout.
    """
    evidence = observation.get("evidence")
    merged = dict(evidence) if isinstance(evidence, Mapping) else {}
    merged["runId"] = observation.get("runId")
    # The trace id decides applicability now; the stored evidence carries the snapshot, but the
    # round row is the authority for identity (a rescanned batch may re-point the same run).
    merged["traceId"] = observation.get("traceId")
    trace = observation.get("trace")
    if trace is not None:
        merged["trace"] = trace
    if not merged.get("messages") and isinstance(trace, Mapping):
        # The run-store join is the primary evidence, but a pure A2A question delivered by push can
        # be absent there while the trace still carries it. The reviewer must not report the round
        # unobserved when the server's own record has the message.
        derived = trace_negotiation_messages(trace)
        if derived:
            merged["messages"] = derived
    merged["batch"] = {
        "nms": observation.get("nmsDeclared"),
        "nmsSim": observation.get("nmsSimDeclared"),
    }
    return merged


def _request_sources(case: Mapping[str, Any], evidence: Mapping[str, Any]) -> dict[str, object]:
    """Return the three requests a verdict can be about: original, blocked, dispatched.

    A blocked case dispatches its replacement request, so the run's frozen input is the follow-up,
    while the blocked original only survives in the interception record. The precedence is the one
    that never substitutes today's corpus for what the run actually read:

    * original — for a blocked case only the intercepted text, else the current corpus flagged
      ``corpus``; both the run's ``rawRequest`` and ``refs.userInput`` are the *follow-up* text
      there, so they must never fill the original column. For a non-blocked case: ``refs.userInput``
      (the frozen input) and then the corpus, again flagged;
    * dispatched — the run's ``rawRequest``, else the frozen case input.
    """
    refs = evidence.get("refs")
    refs_map = refs if isinstance(refs, Mapping) else {}
    intent_meta = evidence.get("intentMeta")
    meta_map = intent_meta if isinstance(intent_meta, Mapping) else {}
    interception = evidence.get("interception")
    blocked = interception if isinstance(interception, Mapping) else {}
    frozen_input = refs_map.get("userInput")
    raw_request = meta_map.get("rawRequest")
    intercepted = blocked.get("input")
    trace = evidence.get("trace")
    traced_original = original_request(trace) if isinstance(trace, Mapping) else ""
    # A blocked run dispatched its follow-up, so ``rawRequest`` and ``refs.userInput`` both hold
    # that replacement text: neither may stand in for the original the operator wrote.
    if isinstance(interception, Mapping) or _is_blocked(case):
        if intercepted:
            original, original_source = intercepted, "interception"
        elif traced_original:
            original, original_source = traced_original, "trace"
        else:
            # The corpus text is the current file, not what the run read: mark it as degraded.
            original, original_source = case.get("user_input"), "corpus"
    elif frozen_input:
        original, original_source = frozen_input, "refs"
    elif traced_original:
        original, original_source = traced_original, "trace"
    else:
        original, original_source = case.get("user_input"), "corpus"
    if raw_request:
        dispatched, dispatched_source = raw_request, "rawRequest"
    elif frozen_input:
        dispatched, dispatched_source = frozen_input, "refs"
    else:
        dispatched, dispatched_source = None, ""
    return {
        "original": original,
        "originalSource": original_source,
        "intercepted": intercepted,
        "followup": blocked.get("followupInput"),
        "dispatched": dispatched,
        "dispatchedSource": dispatched_source,
    }


def _auto_status(rounds: Sequence[Mapping[str, object]]) -> str:
    """Return the newest round's automatic verdict, or ``not_run``."""
    if not rounds:
        return STATUS_NOT_RUN
    first = rounds[0]
    judgement = first.get("judgement")
    if not isinstance(judgement, Mapping):
        return STATUS_NOT_RUN
    return str(judgement.get("status") or STATUS_NOT_RUN)


def _dimension_statuses(round: Mapping[str, object]) -> dict[str, str]:
    """Return the dimension → verdict map of one judged round."""
    judgement = round.get("judgement")
    dimensions = judgement.get("dimensions") if isinstance(judgement, Mapping) else None
    rows = dimensions if isinstance(dimensions, (list, tuple)) else ()
    return {
        str(row.get("dimension")): str(row.get("status"))
        for row in rows
        if isinstance(row, Mapping) and row.get("dimension")
    }


def _verdict_change(
    evidence: Mapping[str, object],
    rounds: Sequence[Mapping[str, object]],
) -> tuple[bool, dict[str, object]]:
    """Compare the newest round with the verdict the decision was confirmed against.

    The overall status and the per-dimension verdicts are both compared: a round can keep
    ``partial`` while moving the failing dimension, and that still means the confirmed conclusion
    was about different evidence. Decisions saved before the dimension snapshot existed fall back
    to the overall status alone.
    """
    newest = rounds[0] if rounds else None
    judgement = newest.get("judgement") if isinstance(newest, Mapping) else None
    if not isinstance(judgement, Mapping):
        # A run-only projection has no judged round; there is nothing to compare against.
        return False, {"status": None, "dimensions": []}
    recorded_status = str(evidence.get("autoStatus") or "")
    current_status = _auto_status(rounds)
    status_change: dict[str, str] | None = None
    if recorded_status and current_status and recorded_status != current_status:
        status_change = {"from": recorded_status, "to": current_status}
    dimension_changes: list[dict[str, str]] = []
    recorded_dimensions = evidence.get("dimensions")
    if isinstance(recorded_dimensions, Mapping) and rounds:
        current_dimensions = _dimension_statuses(rounds[0])
        for name, before in recorded_dimensions.items():
            after = current_dimensions.get(str(name))
            if after is not None and after != str(before):
                dimension_changes.append({"dimension": str(name), "from": str(before), "to": after})
    changed = bool(status_change or dimension_changes)
    return changed, {"status": status_change, "dimensions": dimension_changes}


def _case_summary(case: Mapping[str, Any]) -> dict[str, object]:
    """Return the case metadata the page shows above the rounds."""
    a2a_t = _protocol_block(case, PROTOCOL_A2A_T)
    a2a = _protocol_block(case, PROTOCOL_A2A)
    followup = case.get("blocked_followup")
    followup_map = followup if isinstance(followup, Mapping) else {}
    return {
        "id": int(case["id"]),
        "name": str(case.get("name") or ""),
        "operation": case.get("operation"),
        "dimension": case.get("dimension"),
        "userInput": case.get("user_input"),
        # The replacement request a blocked case dispatches. The detail shows it next to the
        # original so a reviewer can see what changed without opening the case YAML.
        "followupInput": followup_map.get("input"),
        "blocked": _is_blocked(case),
        "validate": a2a_t.get("validate"),
        "a2aTRounds": _rounds(a2a_t),
        "a2aRounds": _rounds(a2a),
        "finalState": a2a_t.get("final_state"),
        "hasSlotErrors": a2a_t.get("slot_errors") is not None,
        "hasMessages": bool(_messages(a2a_t) or _messages(a2a)),
        "caseSignature": case_signature(case),
    }


def _protocol_block(case: Mapping[str, Any], protocol: str) -> Mapping[str, Any]:
    """Return one protocol's expectation block of one case."""
    expect = case.get("expect")
    protocols = expect.get("protocols") if isinstance(expect, Mapping) else None
    block = protocols.get(protocol) if isinstance(protocols, Mapping) else None
    return block if isinstance(block, Mapping) else {}


def _rounds(block: Mapping[str, Any]) -> int:
    """Return one protocol block's expected negotiation rounds."""
    negotiation = block.get("negotiation")
    value = negotiation.get("expected_rounds") if isinstance(negotiation, Mapping) else None
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _messages(block: Mapping[str, Any]) -> object:
    """Return the messages expectation of one protocol block, or ``None``."""
    negotiation = block.get("negotiation")
    return negotiation.get("messages") if isinstance(negotiation, Mapping) else None


def _is_blocked(case: Mapping[str, Any]) -> bool:
    """Return whether the case expects an interception."""
    return _protocol_block(case, PROTOCOL_A2A_T).get("validate") == "blocked"


def _negotiates(block: Mapping[str, Any]) -> bool:
    """Return whether one protocol block expects a negotiation at all."""
    negotiation = block.get("negotiation")
    if not isinstance(negotiation, Mapping):
        return False
    if negotiation.get("expected") is True:
        return True
    # Some converted cases spell the expectation as rounds or messages only.
    return _rounds(block) > 0 or bool(_messages(block))


def _negotiation_flags(case: Mapping[str, Any]) -> dict[str, bool]:
    """Return the negotiation expectation of both protocols, for the list markers and filters."""
    return {
        protocol: _negotiates(_protocol_block(case, protocol)) for protocol in PROTOCOLS
    }


__all__ = [
    "MAX_TRACE_IDS",
    "PROTOCOLS",
    "STATUSES",
    "STATUS_NOT_RUN",
    "TRACE_DIMENSIONS",
    "ReviewService",
    "TraceQuery",
    "parse_trace_input",
]
