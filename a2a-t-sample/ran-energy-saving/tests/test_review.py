"""Tests of the acceptance review console (corpus, judging projection, store and routes)."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import httpx
import yaml
from demo.web.annotate.overlay import confirmed_overlay, overlay_token
from demo.web.annotate.store import AnnotationStore, TargetKey
from demo.web.review.__main__ import build_app, main
from demo.web.review.corpus import scan_evidence
from demo.web.review.export import build_payload, payload_json, payload_yaml, report_markdown
from demo.web.review.routes import build_review_routes
from demo.web.review.service import STATUS_NOT_RUN, STATUS_STALE, ReviewService
from demo.web.review.store import (
    DECISION_ACCEPTED,
    DECISION_DEFECT,
    DECISION_PENDING,
    DecisionKey,
    ReviewStore,
)
from eval.osp.catalog import CaseCatalog
from eval.osp.expectations import SLOT_DIMENSION
from protocol.client_store import NegotiationEventRecord, SqliteClientStore
from protocol.client_store.store import NEGOTIATION_DIRECTION_INBOUND
from starlette.applications import Starlette

INTENT: dict[str, object] = {
    "Operation type": "Modify",
    "Energy saving cell RAT": "ALL",
    "Energy saving aggressiveness": "",
    "Energy saving region": "A2ATestRegion",
    "Whether Experience-Preserving Energy Saving is Required": "",
    "Special Requirements for Carrier Shutdown Frequencies": "",
    "Energy saving time": "",
    "Frequencies of carriers that can be shut down": [],
    "Gain target": "",
    "Guaranteed throughput target by zone": [],
    "Scenario-based energy saving": [],
}

TASK_T = """## Operation Type
Modify

## Task Type
RAN energy saving

## Task Description
Execute the energy-saving task.

## Task Object
A2ATestRegion

## Task Target

## Task Context
Site list: ["LNR_KPI_7745"], rate target: 2Mbps.
"""

MESSAGE = """## Feasibility Negotiation
Reduce the throughput target from 10Mbps to 2Mbps for 08:00-18:00.
"""


def _case_document(
    case_id: int = 1,
    *,
    blocked: bool = True,
    rounds: int = 1,
    followup: str | None = "do it properly",
) -> dict[str, Any]:
    """Return one case document the review judge and catalog understand."""
    case: dict[str, Any] = {
        "id": case_id,
        "name": f"Case {case_id}",
        "dimension": "D1",
        "operation": "Modify",
        "tags": ["tag"],
        "user_input": "do it",
        "expect": {
            "intent": dict(INTENT),
            "protocols": {
                "a2a-t": {
                    "validate": "blocked" if blocked else "pass",
                    "validate_label": "拦截" if blocked else "不拦截",
                    "send": True,
                    "task_t": TASK_T,
                    "negotiation": {
                        "expected": rounds > 0,
                        "expected_rounds": rounds,
                        "kinds": None,
                        "rounds": [],
                    },
                    "final_state": "task_completed",
                },
                "a2a": {
                    "negotiation": {
                        "expected": False,
                        "expected_rounds": 0,
                        "kinds": None,
                        "rounds": [],
                    },
                    "final_state": None,
                },
            },
        },
        "assert": {
            "intent": True,
            "a2a-t": {
                "validate": True,
                "task_t": True,
                "send": True,
                "negotiation": True,
                "final_state": True,
            },
            "a2a": {"negotiation": True, "final_state": False},
        },
    }
    if blocked and followup is not None:
        case["blocked_followup"] = {"input": followup}
    return case


def _write_cases(cases_dir: Path, *documents: Mapping[str, object]) -> None:
    cases_dir.mkdir(parents=True, exist_ok=True)
    for document in documents:
        (cases_dir / f"case-{document['id']}.yaml").write_text(
            yaml.safe_dump(dict(document), allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )


def _write_overlay(path: Path, cases: list[dict[str, object]]) -> None:
    path.write_text(
        yaml.safe_dump(
            {"schema": "osp-expectations-overlay/v1", "cases": cases},
            allow_unicode=True,
            sort_keys=False,
        ),
        encoding="utf-8",
    )


def _evidence_item(
    *,
    case_id: int = 1,
    protocol: str = "a2a-t",
    run_id: str = "run-1",
    trace_id: str = "trace-1",
    round_index: int = 1,
    server_intent: Mapping[str, object] | None = None,
    intent_status: str = "passed",
    rounds: int = 1,
    final_state: str = "task_completed",
    blocked: bool = True,
    slot_error: bool = False,
    refs_input: str = "do it properly",
) -> dict[str, object]:
    """Return one persisted tester item."""
    item: dict[str, object] = {
        "caseId": case_id,
        "name": f"Case {case_id}",
        "protocol": protocol,
        "round": round_index,
        "status": "passed",
        "runId": run_id,
        "traceId": trace_id,
        "durationMs": 1200,
        "tokens": {"total": 10},
        "refs": {"userInput": refs_input},
        "intent": dict(INTENT),
        "serverIntent": dict(server_intent or INTENT),
        "stages": {
            "intent": {"stage": "intent", "status": intent_status, "actual": dict(INTENT)},
            "task_t": {"stage": "task_t", "status": "passed", "actual": TASK_T},
            "validate": {
                "stage": "validate",
                "status": "passed",
                "actual": "pass",
                "detail": {"verdict": "pass", "slotErrors": []},
            },
            "negotiate": {
                "stage": "negotiate",
                "status": "passed",
                "actual": {"rounds": rounds, "kinds": ["feasibility"]},
            },
            "final": {"stage": "final", "status": "passed", "actual": final_state},
        },
    }
    if blocked:
        item["interception"] = {
            "stages": {
                "validate": {
                    "stage": "validate",
                    "status": "failed",
                    "detail": {
                        "verdict": "blocked",
                        "slotErrors": (
                            [
                                {
                                    "slotName": "Gain target",
                                    "code": "slot.constraint_violated",
                                    "message": "gain target is not allowed",
                                    "facts": {"actual": "20%"},
                                }
                            ]
                            if slot_error
                            else []
                        ),
                    },
                }
            }
        }
    return item


def _write_batch(
    runs_dir: Path,
    items: list[dict[str, object]],
    *,
    batch_id: str = "batch-1",
    model: str = "qwen3.8",
    decision_mode: str = "live",
    finished_at: str = "2026-11-15T10:05:00+00:00",
) -> None:
    directory = runs_dir / "tester"
    directory.mkdir(parents=True, exist_ok=True)
    document = {
        "schema": "osp-tester/v4",
        "batchId": batch_id,
        "createdAt": "2026-11-15T10:00:00+00:00",
        "finishedAt": finished_at,
        "decisionMode": decision_mode,
        "modelLabel": model,
        "discovery": "nms",
        "transport": "webhook",
        "rounds": 1,
        "items": items,
    }
    (directory / f"{batch_id}.json").write_text(json.dumps(document), encoding="utf-8")


def _record_message(path: Path, run_id: str, content: str = MESSAGE, protocol: str = "a2a-t") -> None:
    store = SqliteClientStore(path)
    asyncio.run(
        store.record_negotiation_event(
            NegotiationEventRecord.new(
                run_id=run_id,
                seq=1,
                direction=NEGOTIATION_DIRECTION_INBOUND,
                actor="server",
                negotiation_id="neg-1",
                kind="feasibility",
                round=1,
                performative="PROPOSE",
                content={"content": content, "templateUri": None},
                protocol=protocol,
            )
        )
    )


def _fixture(
    tmp_path: Path,
    *,
    blocked: bool = True,
    rounds: int = 1,
    overlay_slot_error: bool = True,
    item_slot_error: bool = True,
) -> tuple[ReviewService, CaseCatalog, Path, Path]:
    """Build a service over one temp corpus, overlay, batch and client store."""
    cases_dir = tmp_path / "cases"
    overlay = tmp_path / "overlay.yaml"
    runs_dir = tmp_path / "runs"
    client_db = runs_dir / "client-state.sqlite3"
    _write_cases(cases_dir, _case_document(blocked=blocked, rounds=rounds))
    overlay_cases: list[dict[str, object]] = []
    protocol_block: dict[str, object] = {}
    if blocked and overlay_slot_error:
        protocol_block["slot_errors"] = {
            "mode": "any",
            "expect": [{"code": "slot.constraint_violated", "contains": ["gain"]}],
        }
    if rounds:
        protocol_block["negotiation"] = {"messages": [[MESSAGE]]}
    if protocol_block:
        overlay_cases.append({"id": 1, "a2a-t": protocol_block})
    _write_overlay(overlay, overlay_cases)
    _record_message(client_db, "run-1")
    _write_batch(
        runs_dir,
        [
            _evidence_item(
                rounds=rounds,
                blocked=blocked,
                slot_error=item_slot_error,
            )
        ],
    )
    catalog = CaseCatalog(cases_dir, overlay_path=overlay)
    store = ReviewStore(tmp_path / "review.sqlite3")
    store.ensure()
    service = ReviewService(
        store=store,
        catalog=catalog,
        runs_dir=runs_dir,
        client_db_path=client_db,
    )
    return service, catalog, overlay, runs_dir


# ---------------------------------------------------------------------------
# Corpus
# ---------------------------------------------------------------------------


def test_scan_stores_the_evidence_and_joins_the_negotiation_messages(tmp_path: Path) -> None:
    service, _catalog, _overlay, _runs = _fixture(tmp_path)
    service.scan(days=2.0)

    rows = service.store.observations(1, "a2a-t")

    assert len(rows) == 1
    evidence = rows[0]["evidence"]
    assert isinstance(evidence, dict)
    assert evidence["messages"] == [{"round": 1, "kind": "feasibility", "content": MESSAGE}]
    assert rows[0]["nms"] is True
    assert rows[0]["decisionMode"] == "live"


def test_scan_reads_the_real_machine_flag_from_the_batch_discovery(tmp_path: Path) -> None:
    _write_cases(tmp_path / "cases", _case_document())
    scan = scan_evidence(
        runs_dir=tmp_path,
        client_db_path=None,
        cases={1: _case_document()},
        days=2.0,
    )
    assert scan.observations == ()  # no batch written yet

    _write_batch(tmp_path, [_evidence_item()])
    scan = scan_evidence(
        runs_dir=tmp_path,
        client_db_path=None,
        cases={1: _case_document()},
        days=2.0,
    )

    assert scan.observations[0]["nms"] is True
    assert scan.observations[0]["discovery"] == "nms"


def test_scan_skips_items_without_stages(tmp_path: Path) -> None:
    item = _evidence_item()
    del item["stages"]
    _write_batch(tmp_path, [item])

    scan = scan_evidence(
        runs_dir=tmp_path,
        client_db_path=None,
        cases={1: _case_document()},
        days=2.0,
    )

    assert scan.observations == ()


# ---------------------------------------------------------------------------
# Judging projection
# ---------------------------------------------------------------------------


def test_the_projection_judges_with_the_current_overlay(tmp_path: Path) -> None:
    service, _catalog, _overlay, _runs = _fixture(tmp_path)
    service.scan(days=2.0)

    rows = service.cases()
    task = rows[0]["protocols"][0]

    assert task["protocol"] == "a2a-t"
    assert task["status"] == "pass"
    assert {row["dimension"]: row["status"] for row in task["dimensions"]}["validate"] == "pass"
    assert task["decision"]["status"] == DECISION_PENDING


def test_confirmed_annotations_are_used_without_an_export(tmp_path: Path) -> None:
    """The review judges against the annotation database by case id, with no export step."""
    cases_dir = tmp_path / "cases"
    _write_cases(cases_dir, _case_document())
    annotation_db = tmp_path / "annotations.sqlite3"
    annotations = AnnotationStore(annotation_db)
    annotations.ensure()
    annotations.ingest(
        [
            {
                "dimension": SLOT_DIMENSION,
                "caseId": 1,
                "protocol": "a2a-t",
                "round": 0,
                "code": "slot.constraint_violated",
                "slotName": "Gain target",
                "facts": {"actual": "20%"},
                "message": "gain target is not allowed",
                "content": None,
                "sourceBatch": "batch-1",
                "sourceRound": 1,
                "runId": "run-1",
                "traceId": "trace-1",
                "observedAt": "2026-11-15T10:05:00+00:00",
                "consistent": True,
            }
        ]
    )
    observation = annotations.detail(TargetKey(1, "a2a-t", SLOT_DIMENSION))["observations"]
    assert isinstance(observation, list)
    annotations.apply(
        TargetKey(1, "a2a-t", SLOT_DIMENSION),
        [int(observation[0]["id"])],
        confirm=True,
        actor="op",
    )
    catalog = CaseCatalog(
        cases_dir,
        overlay_path=tmp_path / "absent.yaml",
        annotation_overlay=lambda: confirmed_overlay(annotation_db),
        annotation_token=lambda: overlay_token(annotation_db),
    )
    case = catalog.get(1)
    assert case is not None
    assert case["expect"]["protocols"]["a2a-t"]["slot_errors"]["mode"] == "any"

    # The stored run reports a different error than the confirmed annotation, so the review fails
    # the interception. Without the database the case states no slot expectation and would pass.
    _write_batch(
        tmp_path / "runs",
        [
            _evidence_item(
                blocked=True,
                slot_error=True,
            )
        ],
    )
    # Replace the observed error with a non-matching one.
    batch_path = tmp_path / "runs" / "tester" / "batch-1.json"
    document = json.loads(batch_path.read_text(encoding="utf-8"))
    document["items"][0]["interception"]["stages"]["validate"]["detail"]["slotErrors"][0][
        "code"
    ] = "slot.not_provided"
    batch_path.write_text(json.dumps(document), encoding="utf-8")
    store = ReviewStore(tmp_path / "review.sqlite3")
    store.ensure()
    service = ReviewService(
        store=store,
        catalog=catalog,
        runs_dir=tmp_path / "runs",
        client_db_path=None,
    )
    service.scan(days=2.0)

    task = service.cases()[0]["protocols"][0]
    assert task["status"] == "fail"
    assert {row["dimension"]: row["status"] for row in task["dimensions"]}["validate"] == "fail"


def test_a_changed_input_marks_the_evidence_stale(tmp_path: Path) -> None:
    service, catalog, _overlay, _runs = _fixture(tmp_path)
    service.scan(days=2.0)
    changed = _case_document()
    changed["blocked_followup"] = {"input": "a different follow-up"}
    _write_cases(tmp_path / "cases", changed)
    catalog.reload()

    rows = service.cases()

    assert rows[0]["protocols"][0]["status"] == STATUS_STALE
    assert rows[0]["protocols"][0]["inputChanged"] is True


def test_a_case_without_evidence_is_not_run(tmp_path: Path) -> None:
    cases_dir = tmp_path / "cases"
    _write_cases(cases_dir, _case_document())
    catalog = CaseCatalog(cases_dir, overlay_path=tmp_path / "absent.yaml")
    store = ReviewStore(tmp_path / "review.sqlite3")
    store.ensure()
    service = ReviewService(
        store=store, catalog=catalog, runs_dir=tmp_path / "runs", client_db_path=None
    )

    rows = service.cases()

    assert [task["status"] for task in rows[0]["protocols"]] == [STATUS_NOT_RUN, STATUS_NOT_RUN]


def test_the_detail_judges_every_round(tmp_path: Path) -> None:
    service, _catalog, _overlay, runs = _fixture(tmp_path)
    service.scan(days=2.0)
    _write_batch(
        runs,
        [
            _evidence_item(
                run_id="run-2",
                trace_id="trace-2",
                server_intent={**INTENT, "Energy saving region": "Other"},
            )
        ],
        batch_id="batch-2",
        finished_at="2026-11-15T10:06:00+00:00",
    )
    service.scan(days=2.0)

    detail = service.detail(1, "a2a-t")

    assert len(detail["rounds"]) == 2
    assert detail["rounds"][0]["judgement"]["status"] == "fail"
    assert detail["rounds"][1]["judgement"]["status"] == "pass"
    assert detail["case"]["blocked"] is True
    assert detail["case"]["hasSlotErrors"] is True


# ---------------------------------------------------------------------------
# Decisions
# ---------------------------------------------------------------------------


def test_a_decision_must_explain_an_override(tmp_path: Path) -> None:
    service, _catalog, _overlay, runs = _fixture(tmp_path)
    _write_batch(
        runs,
        [_evidence_item(server_intent={**INTENT, "Energy saving region": "Other"})],
        batch_id="batch-bad",
        finished_at="2026-11-15T10:06:00+00:00",
    )
    service.scan(days=2.0)

    try:
        service.set_decision(1, "a2a-t", status=DECISION_ACCEPTED)
    except ValueError as exc:
        assert "理由" in str(exc)
    else:  # pragma: no cover - the assertion above is the contract
        raise AssertionError("a pass over a failing verdict must require a note")

    detail = service.set_decision(
        1, "a2a-t", status=DECISION_ACCEPTED, note="standard deviation accepted"
    )

    assert detail["decision"]["status"] == DECISION_ACCEPTED
    assert detail["decision"]["note"] == "standard deviation accepted"


def test_a_defect_requires_a_category_and_a_note(tmp_path: Path) -> None:
    service, _catalog, _overlay, _runs = _fixture(tmp_path)
    service.scan(days=2.0)

    for kwargs, message in (
        ({"status": DECISION_DEFECT, "note": "x"}, "归属"),
        ({"status": DECISION_DEFECT, "category": "sdk"}, "备注"),
    ):
        try:
            service.set_decision(1, "a2a-t", **kwargs)
        except ValueError as exc:
            assert message in str(exc)
        else:  # pragma: no cover
            raise AssertionError("an unexplained defect must be rejected")

    detail = service.set_decision(
        1, "a2a-t", status=DECISION_DEFECT, category="sdk", note="Task-T drops a site"
    )

    assert detail["decision"]["category"] == "sdk"


def test_a_decision_becomes_stale_when_the_case_changes(tmp_path: Path) -> None:
    service, catalog, _overlay, runs = _fixture(tmp_path)
    service.scan(days=2.0)
    service.set_decision(1, "a2a-t", status=DECISION_ACCEPTED)
    assert service.detail(1, "a2a-t")["decision"]["stale"] is False

    changed = _case_document()
    changed["expect"]["protocols"]["a2a-t"]["task_t"] = TASK_T + "\nextra\n"
    _write_cases(tmp_path / "cases", changed)
    catalog.reload()

    decision = service.detail(1, "a2a-t")["decision"]

    assert decision["stale"] is True
    assert "变更" in decision["staleReason"]


def test_a_decision_is_flagged_when_new_evidence_arrives(tmp_path: Path) -> None:
    service, _catalog, _overlay, runs = _fixture(tmp_path)
    service.scan(days=2.0)
    service.set_decision(1, "a2a-t", status=DECISION_ACCEPTED)
    _write_batch(
        runs,
        [_evidence_item(run_id="run-2", trace_id="trace-2")],
        batch_id="batch-2",
        finished_at="2026-11-15T10:06:00+00:00",
    )
    service.scan(days=2.0)

    assert service.detail(1, "a2a-t")["decision"]["newEvidence"] is True


def test_the_store_keeps_one_decision_per_target_and_audits_it(tmp_path: Path) -> None:
    store = ReviewStore(tmp_path / "review.sqlite3")
    store.ensure()
    key = DecisionKey(1, "a2a-t")

    store.set_decision(key, status=DECISION_ACCEPTED, note="ok", reviewer="op")
    store.set_decision(key, status=DECISION_DEFECT, category="sdk", note="broken", reviewer="op")

    decision = store.decision(key)
    assert decision is not None
    assert decision["status"] == DECISION_DEFECT
    assert store.decisions()[(1, "a2a-t")]["category"] == "sdk"

    store.set_decision(key, status=DECISION_PENDING)
    assert store.decision(key) is None


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------


def test_the_payload_and_report_carry_the_decisions(tmp_path: Path) -> None:
    service, _catalog, _overlay, _runs = _fixture(tmp_path)
    service.scan(days=2.0)
    service.set_decision(
        1, "a2a-t", status=DECISION_DEFECT, category="sdk", note="Task-T drops a site"
    )
    rows = service.cases()
    payload = build_payload(
        rows, counts=service.progress(rows), corpus_version=service.catalog.version
    )

    assert payload["schema"] == "osp-review/v1"
    text = report_markdown(payload)

    assert "## 2. 缺陷清单" in text
    assert "Task-T drops a site" in text
    assert json.loads(payload_json(payload))["cases"][0]["protocols"]["a2a-t"]["decision"]["status"] == DECISION_DEFECT
    assert payload_yaml(payload).startswith("# osp-review/v1")


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


def _app(tmp_path: Path) -> Starlette:
    cases_dir = tmp_path / "cases"
    _write_cases(cases_dir, _case_document())
    routes = build_review_routes(
        catalog=CaseCatalog(cases_dir, overlay_path=tmp_path / "absent.yaml"),
        runs_dir=tmp_path / "runs",
        client_db_path=None,
        store_path=tmp_path / "review.sqlite3",
    )
    return Starlette(routes=list(routes))


def _client(app: Starlette) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    )


async def test_route_scan_cases_detail_and_decision(tmp_path: Path) -> None:
    _write_batch(tmp_path / "runs", [_evidence_item(blocked=False, slot_error=False)])
    app = _app(tmp_path)
    async with _client(app) as client:
        scanned = (await client.post("/api/review/scan?days=2")).json()
        cases = (await client.get("/api/review/cases")).json()
        detail = (await client.get("/api/review/case/1/a2a-t")).json()
        decided = (
            await client.post(
                "/api/review/case/1/a2a-t/decision",
                json={"status": "accepted", "note": "reviewed", "category": ""},
            )
        ).json()
        exported = (await client.get("/api/review/export.json")).json()
        report = await client.get("/api/review/report.md")

    assert scanned["scan"]["observations"] == 1
    assert cases["cases"][0]["caseId"] == 1
    assert detail["rounds"][0]["judgement"]["status"] == "pass"
    assert decided["ok"] is True
    assert decided["detail"]["decision"]["status"] == "accepted"
    assert exported["schema"] == "osp-review/v1"
    assert report.status_code == 200
    assert "验收审核报告" in report.text


async def test_route_rejects_invalid_decisions_and_unknown_targets(tmp_path: Path) -> None:
    app = _app(tmp_path)
    async with _client(app) as client:
        bad_status = await client.post(
            "/api/review/case/1/a2a-t/decision", json={"status": "maybe"}
        )
        bad_category = await client.post(
            "/api/review/case/1/a2a-t/decision",
            json={"status": "defect", "category": "sdk", "note": ""},
        )
        unknown_case = await client.get("/api/review/case/99/a2a-t")
        bad_protocol = await client.get("/api/review/case/1/other")

    assert bad_status.status_code == 400
    assert bad_category.status_code == 400
    assert unknown_case.status_code == 404
    assert bad_protocol.status_code == 400


# ---------------------------------------------------------------------------
# Standalone read-only server
# ---------------------------------------------------------------------------


def test_the_standalone_app_serves_the_page_and_keeps_the_store_outside(tmp_path: Path) -> None:
    source = tmp_path / "live" / "runs"
    _write_batch(source, [_evidence_item()])
    cases_dir = tmp_path / "cases"
    _write_cases(cases_dir, _case_document())
    store = tmp_path / "own" / "review.sqlite3"

    app = build_app(
        runs_dir=source,
        client_db_path=source / "client-state.sqlite3",
        store_path=store,
        cases_dir=cases_dir,
        overlay_path=tmp_path / "absent.yaml",
    )

    async def _probe() -> tuple[int, list[object], bool]:
        async with _client(app) as client:
            page = await client.get("/review")
            cases = (await client.get("/api/review/cases")).json()
        return page.status_code, list(cases.get("cases") or []), (source / "review").exists()

    status, cases, wrote_beside_source = asyncio.run(_probe())

    assert status == 200
    assert len(cases) == 1
    assert store.is_file()
    assert wrote_beside_source is False


def test_the_standalone_parser_defaults_to_the_review_port() -> None:
    from demo.web.review.__main__ import _build_parser

    args = _build_parser().parse_args([])

    assert args.port == 8091
    assert args.host == "127.0.0.1"
    assert args.store is None
    assert args.overlay is None
    assert args.annotations is None


def test_the_standalone_check_mode_exits_without_serving(tmp_path: Path, capsys: Any) -> None:
    runs = tmp_path / "runs"
    runs.mkdir()
    code = main(["--runs-dir", str(runs), "--store", str(tmp_path / "r.sqlite3"), "--check"])

    assert code == 0
    assert "source batches" in capsys.readouterr().out
