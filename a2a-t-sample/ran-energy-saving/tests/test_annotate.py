"""Tests of the expectation annotation console (corpus scan, store, patch and routes)."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
from demo.web.annotate.apply import apply, patch_text, preview
from demo.web.annotate.corpus import scan_corpus
from demo.web.annotate.routes import build_annotate_routes
from demo.web.annotate.service import AnnotateService
from demo.web.annotate.store import (
    TARGET_APPLIED,
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
    """Return a negotiation case id of the shipped corpus (26 is a feasibility negotiation)."""
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
) -> None:
    """Write one persisted tester batch snapshot the annotation scan can read."""
    directory = runs_dir / "tester"
    directory.mkdir(parents=True, exist_ok=True)
    document = {
        "schema": "osp-tester/v4",
        "batchId": batch_id,
        "createdAt": "2026-11-15T10:00:00+00:00",
        "finishedAt": "2026-11-15T10:05:00+00:00",
        "decisionMode": "live",
        "modelLabel": model,
        "nms": True,
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


# ---------------------------------------------------------------------------
# Corpus scan
# ---------------------------------------------------------------------------


def test_scan_collects_a_consistent_slot_error(tmp_path: Path) -> None:
    _write_batch(tmp_path, items=[_blocked_item(_blocked_case_id())])

    scan = scan_corpus(
        runs_dir=tmp_path,
        client_db_path=None,
        cases=_cases(),
        days=2.0,
    )

    slot_rows = [row for row in scan.observations if row["dimension"] == SLOT_DIMENSION]
    assert len(slot_rows) == 1
    assert slot_rows[0]["code"] == "slot.constraint_violated"
    assert slot_rows[0]["consistent"] is True
    assert slot_rows[0]["model"] == "test-model"
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


def _first_observation(detail: Mapping[str, object]) -> dict[str, object]:
    """Return the first candidate of one detail payload."""
    rows = detail.get("observations")
    assert isinstance(rows, list) and rows
    return dict(rows[0])


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
    observation = _first_observation(store.detail(key))

    store.set_choice(key, int(observation["id"]), accepted=True, edited_text="gain, 增益")
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
    observation = _first_observation(store.detail(key))

    store.set_choice(key, int(observation["id"]), accepted=True)
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
    observation = _first_observation(store.detail(key))
    store.set_choice(key, int(observation["id"]), accepted=True)
    store.confirm(key)

    store.set_mode(key, "cover")

    assert store.targets()[0]["status"] == TARGET_DRAFT
    assert _target(store.detail(key))["mode"] == "cover"


def test_applied_targets_are_marked(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _ingest(store)
    key = TargetKey(16, "a2a-t", SLOT_DIMENSION)
    observation = _first_observation(store.detail(key))
    store.set_choice(key, int(observation["id"]), accepted=True)
    store.confirm(key)

    store.mark_applied([key], actor="tester-cookie")

    assert store.targets()[0]["status"] == TARGET_APPLIED


# ---------------------------------------------------------------------------
# Corpus patch
# ---------------------------------------------------------------------------

CORPUS = """# Generated from OSP-Unified Evaluation Set_v.01.xlsx; do not edit by hand.
schema: osp-eval-case/v1
id: 1
name: sample
operation: Create
expect:
  intent:
    Operation type: Create
  protocols:
    a2a-t:
      validate: blocked
      validate_label: 拦截
      send: false
      negotiation:
        expected: false
        expected_rounds: 0
        kinds: null
        rounds: []
      final_state: input_invalid
    a2a:
      negotiation:
        expected: true
        expected_rounds: 1
        kinds: null
        rounds:
          - 'Set the target.'
      final_state: null
assert:
  intent: true
"""


def test_patch_inserts_the_slot_errors_after_the_label() -> None:
    payload = {
        "cases": [
            {
                "id": 1,
                "a2a-t": {"slot_errors": {"mode": "any", "expect": [{"code": "slot.not_provided"}]}},
            }
        ]
    }

    text, changed, errors = patch_text(CORPUS, payload)

    assert changed == (1,)
    assert errors == ()
    lines = text.splitlines()
    label = lines.index("      validate_label: 拦截")
    assert lines[label + 1] == "      slot_errors:"
    assert "        mode: any" in lines


def test_patch_inserts_the_messages_after_the_rounds() -> None:
    message = "## Target Negotiation\nReduce the target to 2Mbps."
    payload = {"cases": [{"id": 1, "a2a": {"messages": [[message]]}}]}

    text, changed, errors = patch_text(CORPUS, payload)

    assert changed == (1,)
    assert errors == ()
    lines = text.splitlines()
    assert "          - - |-" in lines
    assert "              ## Target Negotiation" in lines
    assert "      final_state: null" in lines
    assert lines.index("          - - |-") < lines.index("      final_state: null")


def test_patch_replaces_an_existing_block() -> None:
    payload = {
        "cases": [
            {
                "id": 1,
                "a2a-t": {"slot_errors": {"mode": "any", "expect": [{"code": "slot.not_provided"}]}},
            }
        ]
    }
    once, _changed, _errors = patch_text(CORPUS, payload)
    second = {
        "cases": [
            {
                "id": 1,
                "a2a-t": {"slot_errors": {"mode": "cover", "expect": [{"code": "slot.rule_violation"}]}},
            }
        ]
    }

    twice, changed, errors = patch_text(once, second)

    assert changed == (1,)
    assert errors == ()
    assert twice.count("slot_errors:") == 1
    assert "        mode: cover" in twice.splitlines()


def test_patch_leaves_an_unknown_case_untouched() -> None:
    payload = {"cases": [{"id": 99, "a2a-t": {"slot_errors": ["slot.not_provided"]}}]}

    text, changed, errors = patch_text(CORPUS, payload)

    assert text == CORPUS
    assert changed == ()
    assert errors == ()


def test_apply_writes_a_backup_and_the_patched_source(tmp_path: Path) -> None:
    source = tmp_path / "cases-all.yaml"
    source.write_text(CORPUS, encoding="utf-8")
    payload = {
        "cases": [
            {
                "id": 1,
                "a2a-t": {"slot_errors": {"mode": "any", "expect": [{"code": "slot.not_provided"}]}},
            }
        ]
    }

    result = apply(payload, source=source, run_generator=False)

    assert result.ok is True
    assert result.changed_cases == (1,)
    assert "slot_errors:" in source.read_text(encoding="utf-8")
    backups = list(tmp_path.glob("cases-all.yaml.bak-*"))
    assert len(backups) == 1
    assert backups[0].read_text(encoding="utf-8") == CORPUS


def test_apply_reports_an_invalid_payload_without_writing(tmp_path: Path) -> None:
    source = tmp_path / "cases-all.yaml"
    source.write_text(CORPUS, encoding="utf-8")

    result = apply({"cases": "not-a-list"}, source=source, run_generator=False)

    assert result.ok is False
    assert source.read_text(encoding="utf-8") == CORPUS


def test_preview_returns_the_review_diff(tmp_path: Path) -> None:
    source = tmp_path / "cases-all.yaml"
    source.write_text(CORPUS, encoding="utf-8")
    payload = {"cases": [{"id": 1, "a2a-t": {"slot_errors": ["slot.not_provided"]}}]}

    result = preview(payload, source=source)

    assert result.ok is True
    assert "+      slot_errors:" in result.diff
    assert source.read_text(encoding="utf-8") == CORPUS


# ---------------------------------------------------------------------------
# Service and routes
# ---------------------------------------------------------------------------


def _service(tmp_path: Path) -> AnnotateService:
    store = _store(tmp_path)
    return AnnotateService(
        store=store,
        catalog=CaseCatalog(SAMPLES),
        runs_dir=tmp_path,
        client_db_path=None,
    )


def test_the_service_lists_the_whole_expectation_space(tmp_path: Path) -> None:
    service = _service(tmp_path)

    rows = service.targets()
    progress = service.progress()

    slot_rows = [row for row in rows if row["dimension"] == SLOT_DIMENSION]
    assert len(slot_rows) == 52
    assert all(row["protocol"] == "a2a-t" for row in slot_rows)
    assert len([row for row in rows if row["dimension"] == MESSAGE_DIMENSION]) == 99
    assert _counts(progress)["total"] == 151
    assert _counts(progress)["pending"] == 151


def test_the_service_merges_the_evidence_of_one_target(tmp_path: Path) -> None:
    service = _service(tmp_path)
    _write_batch(
        tmp_path,
        items=[_blocked_item(_blocked_case_id())],
    )
    service.scan(days=2.0)

    detail = service.detail(_blocked_case_id(), "a2a-t", SLOT_DIMENSION)

    assert _first_observation(detail)["code"] == "slot.constraint_violated"
    assert detail["current"] is None
    assert detail["pending"] is None


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


async def test_route_meta_and_targets(tmp_path: Path) -> None:
    app = _app(tmp_path)
    async with _client(app) as client:
        meta = (await client.get("/api/annotate/meta")).json()
        targets = (await client.get("/api/annotate/targets")).json()

    assert meta["dimensions"] == [SLOT_DIMENSION, MESSAGE_DIMENSION]
    assert meta["slotModes"] == ["any", "cover"]
    assert targets["targets"]
    assert targets["counts"]["total"] == len(targets["targets"])


async def test_route_scan_choice_and_export(tmp_path: Path) -> None:
    _write_batch(tmp_path, items=[_blocked_item(_blocked_case_id())])
    app = _app(tmp_path)
    async with _client(app) as client:
        scanned = (await client.post("/api/annotate/scan?days=2")).json()
        assert scanned["scan"]["observations"] == 1
        detail = (
            await client.get(f"/api/annotate/target/{_blocked_case_id()}/a2a-t/{SLOT_DIMENSION}")
        ).json()
        observation_id = detail["observations"][0]["id"]
        chosen = await client.post(
            f"/api/annotate/target/{_blocked_case_id()}/a2a-t/{SLOT_DIMENSION}/choice",
            json={"observationId": observation_id, "accepted": True},
        )
        confirmed = await client.post(
            f"/api/annotate/target/{_blocked_case_id()}/a2a-t/{SLOT_DIMENSION}/confirm",
            json={},
        )
        exported = (await client.get("/api/annotate/export.json")).json()

    assert scanned["scan"]["inserted"] == 1
    assert chosen.json() == {"ok": True}
    assert confirmed.json()["status"] == TARGET_CONFIRMED
    assert exported["cases"][0]["id"] == _blocked_case_id()


async def test_route_rejects_an_unknown_dimension(tmp_path: Path) -> None:
    app = _app(tmp_path)
    async with _client(app) as client:
        response = await client.get("/api/annotate/target/1/a2a-t/unknown")

    assert response.status_code == 404


