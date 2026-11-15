"""Tests of the expectation annotation console (corpus scan, store, export and routes)."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
from demo.web.annotate.__main__ import build_app, main
from demo.web.annotate.corpus import scan_corpus
from demo.web.annotate.export import case_snippet, payload_json, payload_yaml
from demo.web.annotate.routes import build_annotate_routes
from demo.web.annotate.service import STATUS_NONE, AnnotateService
from demo.web.annotate.store import (
    TARGET_CONFIRMED,
    TARGET_DRAFT,
    TARGET_PENDING,
    AnnotationStore,
    TargetKey,
)
from eval.osp.catalog import CaseCatalog
from eval.osp.expectations import MESSAGE_DIMENSION, SLOT_DIMENSION
from protocol.client_store import NegotiationEventRecord, SqliteClientStore
from protocol.client_store.store import NEGOTIATION_DIRECTION_INBOUND
from starlette.applications import Starlette

SAMPLES = Path(__file__).resolve().parents[1] / "eval" / "osp" / "cases"
FEASIBILITY = "## Feasibility Negotiation\nReduce the target to 2Mbps for 08:00-18:00.\n"


def _case_id() -> int:
    """Return a negotiation case id of the shipped corpus (26 negotiates on both protocols)."""
    return 26


def _blocked_case_id() -> int:
    """Return a blocked case id of the shipped corpus."""
    return 16


def _write_batch(
    runs_dir: Path,
    *,
    batch_id: str = "batch-test",
    items: list[dict[str, object]],
    model: str = "test-model",
    nms: bool = True,
    decision_mode: str = "live",
) -> None:
    """Write one persisted tester batch snapshot the annotation scan can read."""
    directory = runs_dir / "tester"
    directory.mkdir(parents=True, exist_ok=True)
    document = {
        "schema": "osp-tester/v4",
        "batchId": batch_id,
        "createdAt": "2026-11-15T10:00:00+00:00",
        "finishedAt": "2026-11-15T10:05:00+00:00",
        "decisionMode": decision_mode,
        "modelLabel": model,
        "nms": nms,
        "nmsSim": False,
        "rounds": 1,
        "items": items,
    }
    (directory / f"{batch_id}.json").write_text(json.dumps(document), encoding="utf-8")


def _blocked_item(case_id: int, run_id: str | None = None) -> dict[str, object]:
    """Return a blocked A2A-T item whose interception recorded one slot error."""
    slot_error = {
        "slotName": "Gain target",
        "code": "slot.constraint_violated",
        "message": "the gain target is not allowed",
        "facts": {"slot_label": "Gain target", "actual": "20% - 30%"},
    }
    return {
        "caseId": case_id,
        "name": "blocked case",
        "protocol": "a2a-t",
        "round": 1,
        "status": "passed",
        "runId": run_id,
        "interception": {
            "stages": {
                "validate": {
                    "stage": "validate",
                    "status": "passed",
                    "detail": {"verdict": "blocked", "slotErrors": [slot_error]},
                }
            }
        },
    }


def _negotiation_item(case_id: int, run_id: str, protocol: str = "a2a-t") -> dict[str, object]:
    """Return one item whose run produced the stored proposals."""
    return {
        "caseId": case_id,
        "name": "negotiation case",
        "protocol": protocol,
        "round": 1,
        "status": "passed",
        "runId": run_id,
    }


def _record_proposals(path: Path, run_id: str, *, count: int, protocol: str = "a2a-t") -> None:
    """Write the inbound proposals of one run into a client store."""
    store = SqliteClientStore(path)
    for index in range(1, count + 1):
        asyncio.run(
            store.record_negotiation_event(
                NegotiationEventRecord.new(
                    run_id=run_id,
                    seq=index,
                    direction=NEGOTIATION_DIRECTION_INBOUND,
                    actor="server",
                    negotiation_id="neg-1",
                    kind="feasibility",
                    round=index,
                    performative="PROPOSE",
                    content={"content": FEASIBILITY, "templateUri": None},
                    protocol=protocol,
                )
            )
        )


def _cases() -> dict[int, Mapping[str, Any]]:
    """Return the shipped case documents keyed by id."""
    return {int(case["id"]): case for case in CaseCatalog(SAMPLES).documents()}


def _first_observation(detail: Mapping[str, object]) -> dict[str, object]:
    """Return the first candidate of one detail payload."""
    rows = detail.get("observations")
    assert isinstance(rows, list) and rows
    return dict(rows[0])


def _observation_id(detail: Mapping[str, object]) -> int:
    """Return the id of the first candidate of one detail payload."""
    value = _first_observation(detail)["id"]
    assert isinstance(value, int)
    return value


def _protocol_tasks(row: Mapping[str, object]) -> list[Mapping[str, object]]:
    """Return the protocol tasks of one case row."""
    tasks = row.get("protocols")
    assert isinstance(tasks, list)
    return [task for task in tasks if isinstance(task, Mapping)]


def _subtask_rows(task: Mapping[str, object]) -> list[Mapping[str, object]]:
    """Return the subtasks of one protocol task."""
    rows = task.get("subtasks")
    assert isinstance(rows, list)
    return [row for row in rows if isinstance(row, Mapping)]


def _target(detail: Mapping[str, object]) -> Mapping[str, object]:
    """Return the target block of one detail payload."""
    target = detail.get("target")
    assert isinstance(target, Mapping)
    return target


def _counts(progress: Mapping[str, object]) -> Mapping[str, object]:
    """Return the status counters of one progress payload."""
    counts = progress.get("counts")
    assert isinstance(counts, Mapping)
    return counts


# ---------------------------------------------------------------------------
# Corpus scan
# ---------------------------------------------------------------------------


def test_scan_collects_a_consistent_slot_error(tmp_path: Path) -> None:
    _write_batch(tmp_path, items=[_blocked_item(_blocked_case_id())])

    scan = scan_corpus(runs_dir=tmp_path, client_db_path=None, cases=_cases(), days=2.0)

    slot_rows = [row for row in scan.observations if row["dimension"] == SLOT_DIMENSION]
    assert len(slot_rows) == 1
    assert slot_rows[0]["code"] == "slot.constraint_violated"
    assert slot_rows[0]["consistent"] is True
    assert slot_rows[0]["model"] == "test-model"
    assert slot_rows[0]["decisionMode"] == "live"
    assert slot_rows[0]["nms"] is True


def test_scan_marks_a_slot_error_of_an_unexpected_verdict_inconsistent(tmp_path: Path) -> None:
    item = _blocked_item(1)  # case 1 expects `pass`: a block is a divergence
    _write_batch(tmp_path, items=[item])

    scan = scan_corpus(runs_dir=tmp_path, client_db_path=None, cases=_cases(), days=2.0)

    assert [row["consistent"] for row in scan.observations] == [False]


def test_scan_joins_the_server_messages_by_run_id(tmp_path: Path) -> None:
    client_db = tmp_path / "client.sqlite3"
    _record_proposals(client_db, "run-1", count=1)
    _write_batch(tmp_path, items=[_negotiation_item(_case_id(), "run-1")])

    scan = scan_corpus(runs_dir=tmp_path, client_db_path=client_db, cases=_cases(), days=2.0)

    message_rows = [row for row in scan.observations if row["dimension"] == MESSAGE_DIMENSION]
    assert len(message_rows) == 1
    assert message_rows[0]["round"] == 1
    assert message_rows[0]["kind"] == "feasibility"
    assert str(message_rows[0]["content"]).startswith("## Feasibility Negotiation")
    assert message_rows[0]["consistent"] is True


def test_scan_marks_a_dialogue_with_the_wrong_round_count_inconsistent(tmp_path: Path) -> None:
    client_db = tmp_path / "client.sqlite3"
    _record_proposals(client_db, "run-1", count=2)
    _write_batch(tmp_path, items=[_negotiation_item(_case_id(), "run-1")])

    scan = scan_corpus(runs_dir=tmp_path, client_db_path=client_db, cases=_cases(), days=2.0)

    assert [row["consistent"] for row in scan.observations] == [False, False]


def test_scan_reports_truncation_instead_of_hiding_it(tmp_path: Path) -> None:
    _write_batch(tmp_path, batch_id="batch-a", items=[_blocked_item(_blocked_case_id())])
    _write_batch(tmp_path, batch_id="batch-b", items=[_blocked_item(_blocked_case_id())])

    scan = scan_corpus(
        runs_dir=tmp_path,
        client_db_path=None,
        cases=_cases(),
        days=2.0,
        max_batches=1,
    )

    assert scan.truncated is True
    assert scan.skipped["limit"] == 1


def test_scan_ignores_files_outside_the_window(tmp_path: Path) -> None:
    _write_batch(tmp_path, items=[_blocked_item(_blocked_case_id())])
    future = datetime.now(UTC) + timedelta(days=30)

    scan = scan_corpus(
        runs_dir=tmp_path,
        client_db_path=None,
        cases=_cases(),
        days=1.0,
        now=future,
    )

    assert scan.observations == ()
    assert scan.skipped["outside_window"] == 1


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------


def _store(tmp_path: Path) -> AnnotationStore:
    store = AnnotationStore(tmp_path / "annotations.sqlite3")
    store.ensure()
    return store


def _ingest(store: AnnotationStore, *, case_id: int = 16) -> None:
    store.ingest(
        [
            {
                "dimension": SLOT_DIMENSION,
                "caseId": case_id,
                "protocol": "a2a-t",
                "round": 0,
                "code": "slot.constraint_violated",
                "slotName": "Gain target",
                "facts": {"actual": "20% - 30%"},
                "message": "not allowed",
                "content": None,
                "sourceBatch": "batch-1",
                "sourceRound": 1,
                "runId": "run-1",
                "observedAt": "2026-11-15T10:00:00+00:00",
                "model": "m",
                "decisionMode": "live",
                "nms": True,
                "consistent": True,
            },
            {
                "dimension": MESSAGE_DIMENSION,
                "caseId": 28,
                "protocol": "a2a-t",
                "round": 1,
                "kind": "feasibility",
                "content": FEASIBILITY,
                "sourceBatch": "batch-1",
                "sourceRound": 1,
                "runId": "run-2",
                "observedAt": "2026-11-15T10:00:00+00:00",
                "consistent": True,
            },
        ]
    )


def test_the_store_projects_targets_with_counts(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _ingest(store)

    rows = {(row["caseId"], row["dimension"]): row for row in store.targets()}

    slot = rows[(16, SLOT_DIMENSION)]
    assert slot["observations"] == 1
    assert slot["consistentObservations"] == 1
    assert slot["status"] == TARGET_PENDING
    assert rows[(28, MESSAGE_DIMENSION)]["maxRound"] == 1


def test_an_ingested_observation_is_counted_once_per_batch(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _ingest(store)
    _ingest(store)

    row = next(row for row in store.targets() if row["caseId"] == 16)
    assert row["observations"] == 1

    detail = store.detail(TargetKey(16, "a2a-t", SLOT_DIMENSION))
    assert _first_observation(detail)["seenCount"] == 2


def test_accepting_a_candidate_produces_the_yaml_value(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _ingest(store)
    key = TargetKey(16, "a2a-t", SLOT_DIMENSION)
    detail = store.detail(key)

    store.set_choice(key, _observation_id(detail), accepted=True, edited_text="gain, 增益")
    payload = store.export_payload(confirmed_only=False)

    assert payload["cases"] == [
        {
            "id": 16,
            "a2a-t": {
                "slot_errors": {
                    "mode": "any",
                    "expect": [{"code": "slot.constraint_violated", "contains": ["gain", "增益"]}],
                }
            },
        }
    ]


def test_confirming_changes_the_status_and_the_confirmed_payload(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _ingest(store)
    key = TargetKey(28, "a2a-t", MESSAGE_DIMENSION)
    detail = store.detail(key)

    store.set_choice(key, _observation_id(detail), accepted=True)
    assert store.targets()[1]["status"] == TARGET_DRAFT
    store.confirm(key, actor="tester-cookie")

    row = store.targets()[1]
    assert row["status"] == TARGET_CONFIRMED
    assert row["confirmedBy"] == "tester-cookie"
    assert store.export_payload()["cases"] == [
        {"id": 28, "a2a-t": {"messages": [[FEASIBILITY]]}}
    ]


def test_changing_a_confirmed_target_downgrades_it_to_draft(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _ingest(store)
    key = TargetKey(16, "a2a-t", SLOT_DIMENSION)
    store.set_choice(key, _observation_id(store.detail(key)), accepted=True)
    store.confirm(key)

    store.set_mode(key, "cover")

    assert store.targets()[0]["status"] == TARGET_DRAFT
    assert _target(store.detail(key))["mode"] == "cover"


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------


def _payload() -> dict[str, object]:
    return {
        "schema": "osp-annotations/v1",
        "generatedAt": "2026-11-15T10:00:00+00:00",
        "cases": [
            {
                "id": 16,
                "a2a-t": {
                    "slot_errors": {
                        "mode": "any",
                        "expect": [{"code": "slot.constraint_violated", "contains": ["gain"]}],
                    }
                },
            },
            {
                "id": 26,
                "a2a-t": {"messages": [[FEASIBILITY]]},
                "a2a": {"messages": [["Not feasible, please confirm"]]},
            },
        ],
    }


def test_the_global_yaml_export_carries_the_schema_and_cases() -> None:
    text = payload_yaml(_payload())

    assert text.startswith("# osp-annotations/v1")
    assert "    expect:" in text


def test_the_case_snippet_renders_at_the_protocols_nesting() -> None:
    snippet = case_snippet(_payload(), 16, case_name="设置 gain target")

    assert snippet.startswith("# case 16 · 设置 gain target")
    assert "\n      a2a-t:\n        slot_errors:" in snippet
    assert "        mode: any" in snippet


def test_the_case_snippet_says_when_nothing_is_confirmed() -> None:
    snippet = case_snippet(_payload(), 45, case_name="no expectation")

    assert "# case 45 · no expectation" in snippet
    assert "没有已确认的期望" in snippet


def test_the_json_export_round_trips() -> None:
    payload = _payload()

    assert json.loads(payload_json(payload)) == payload


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


def _service(tmp_path: Path) -> AnnotateService:
    store = _store(tmp_path)
    return AnnotateService(
        store=store,
        catalog=CaseCatalog(SAMPLES),
        runs_dir=tmp_path,
        client_db_path=None,
    )


def test_every_case_carries_both_protocol_tasks(tmp_path: Path) -> None:
    service = _service(tmp_path)

    rows = service.cases()

    assert len(rows) == 100
    assert all(
        [task["protocol"] for task in _protocol_tasks(row)] == ["a2a-t", "a2a"] for row in rows
    )
    assert all(row["caseName"] for row in rows)


def test_the_subtask_space_matches_the_corpus(tmp_path: Path) -> None:
    service = _service(tmp_path)

    rows = service.cases()
    subtasks = [
        (row["caseId"], task["protocol"], subtask["dimension"])
        for row in rows
        for task in _protocol_tasks(row)
        for subtask in _subtask_rows(task)
    ]
    slot = [item for item in subtasks if item[2] == SLOT_DIMENSION]
    messages = [item for item in subtasks if item[2] == MESSAGE_DIMENSION]

    assert len(slot) == 52
    assert all(item[1] == "a2a-t" for item in slot)
    assert len(messages) == 99
    assert len([item for item in messages if item[1] == "a2a-t"]) == 10
    assert len([item for item in messages if item[1] == "a2a"]) == 89


def test_a_protocol_without_a_subtask_reports_none(tmp_path: Path) -> None:
    service = _service(tmp_path)

    row = next(item for item in service.cases() if item["caseId"] == 45)
    tasks = {task["protocol"]: task for task in _protocol_tasks(row)}

    assert tasks["a2a-t"]["status"] == STATUS_NONE
    assert tasks["a2a"]["status"] == STATUS_NONE
    assert tasks["a2a-t"]["subtasks"] == []


def test_progress_counts_every_subtask(tmp_path: Path) -> None:
    service = _service(tmp_path)

    progress = service.progress()

    assert progress["caseCount"] == 100
    assert _counts(progress)["total"] == 151
    assert _counts(progress)["pending"] == 151


def test_the_service_merges_the_evidence_of_one_subtask(tmp_path: Path) -> None:
    service = _service(tmp_path)
    _write_batch(tmp_path, items=[_blocked_item(_blocked_case_id())])
    service.scan(days=2.0)

    detail = service.detail(_blocked_case_id(), "a2a-t", SLOT_DIMENSION)

    assert _first_observation(detail)["code"] == "slot.constraint_violated"
    assert detail["current"] is None
    assert detail["pending"] is None


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


def _app(tmp_path: Path) -> Starlette:
    routes = build_annotate_routes(
        catalog=CaseCatalog(SAMPLES),
        runs_dir=tmp_path,
        client_db_path=None,
        store_path=tmp_path / "annotations.sqlite3",
    )
    return Starlette(routes=list(routes))


def _client(app: Starlette) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    )


async def test_route_meta_and_cases(tmp_path: Path) -> None:
    app = _app(tmp_path)
    async with _client(app) as client:
        meta = (await client.get("/api/annotate/meta")).json()
        cases = (await client.get("/api/annotate/cases")).json()

    assert meta["protocols"] == ["a2a-t", "a2a"]
    assert meta["dimensions"] == [SLOT_DIMENSION, MESSAGE_DIMENSION]
    assert meta["statuses"] == ["pending", "draft", "confirmed"]
    assert len(cases["cases"]) == 100
    assert cases["counts"]["total"] == 151


async def test_route_scan_choice_confirm_and_export(tmp_path: Path) -> None:
    _write_batch(tmp_path, items=[_blocked_item(_blocked_case_id())])
    app = _app(tmp_path)
    async with _client(app) as client:
        scanned = (await client.post("/api/annotate/scan?days=2")).json()
        detail = (
            await client.get(f"/api/annotate/case/{_blocked_case_id()}/a2a-t/{SLOT_DIMENSION}")
        ).json()
        observation_id = detail["observations"][0]["id"]
        chosen = await client.post(
            f"/api/annotate/case/{_blocked_case_id()}/a2a-t/{SLOT_DIMENSION}/choice",
            json={"observationId": observation_id, "accepted": True},
        )
        confirmed = await client.post(
            f"/api/annotate/case/{_blocked_case_id()}/a2a-t/{SLOT_DIMENSION}/confirm",
            json={},
        )
        exported = (await client.get("/api/annotate/export.json")).json()
        snippet = await client.get(f"/api/annotate/export/case/{_blocked_case_id()}")
        all_yaml = await client.get("/api/annotate/export.yaml")

    assert scanned["scan"]["inserted"] == 1
    assert chosen.json() == {"ok": True}
    assert confirmed.json()["status"] == TARGET_CONFIRMED
    assert exported["cases"][0]["id"] == _blocked_case_id()
    assert snippet.status_code == 200
    assert "slot_errors:" in snippet.text
    assert all_yaml.status_code == 200


async def test_route_rejects_an_unknown_dimension(tmp_path: Path) -> None:
    app = _app(tmp_path)
    async with _client(app) as client:
        response = await client.get("/api/annotate/case/1/a2a-t/unknown")

    assert response.status_code == 404


async def test_route_rejects_the_case_export_of_an_unknown_case(tmp_path: Path) -> None:
    app = _app(tmp_path)
    async with _client(app) as client:
        response = await client.get("/api/annotate/export/case/999")

    assert response.status_code == 404


# ---------------------------------------------------------------------------
# Standalone read-only server
# ---------------------------------------------------------------------------


def test_the_standalone_app_reads_another_runs_dir_and_keeps_the_store_outside(
    tmp_path: Path,
) -> None:
    """Pointing at a live instance's runs must not write beside its data."""
    source = tmp_path / "live" / "runs"
    _write_batch(source, items=[_blocked_item(_blocked_case_id())])
    store = tmp_path / "own" / "annotations.sqlite3"
    app = build_app(
        runs_dir=source,
        client_db_path=source / "client-state.sqlite3",
        store_path=store,
        cases_dir=SAMPLES,
    )

    async def _probe() -> tuple[int, list[object], bool]:
        async with _client(app) as client:
            page = await client.get("/annotate")
            cases = (await client.get("/api/annotate/cases")).json()
        return page.status_code, list(cases.get("cases") or []), (source / "annotations").exists()

    status, cases, wrote_beside_source = asyncio.run(_probe())

    assert status == 200
    assert len(cases) == 100
    assert store.is_file()
    assert wrote_beside_source is False


def test_the_standalone_parser_defaults_to_the_sample_runs(tmp_path: Path) -> None:
    from demo.web.annotate.__main__ import _build_parser

    args = _build_parser().parse_args([])

    assert args.port == 8090
    assert args.host == "127.0.0.1"
    assert args.store is None


def test_the_standalone_check_mode_exits_without_serving(tmp_path: Path, capsys: Any) -> None:
    runs = tmp_path / "runs"
    runs.mkdir()
    code = main(["--runs-dir", str(runs), "--store", str(tmp_path / "a.sqlite3"), "--check"])

    assert code == 0
    assert "source batches" in capsys.readouterr().out
