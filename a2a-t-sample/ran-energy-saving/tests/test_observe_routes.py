"""ASGI tests of the /observe Langfuse console routes (offline, no network).

Design: ``docs-local/2026-11-09-langfuse-observer-design.md`` §6.5-§6.6.
"""

from __future__ import annotations

import dataclasses
import io
import re
import uuid
import zipfile
from collections.abc import AsyncGenerator, Mapping, Sequence
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx
import langfuse_export
from demo.web.app import create_app
from demo.web.observe.routes import build_observe_routes
from demo.web.observe.service import ObserveBusyError, ObserveJobManager
from demo.web.observe.table import FIELD_NOTES, MAIN_COLUMNS, ROUNDS_COLUMNS
from protocol.client_store import ClientRunRecord, SqliteClientStore
from shared.config import SampleConfig
from starlette.applications import Starlette
from starlette.routing import Route

TID_A = "a" * 32
TID_B = "b" * 32
TID_C = "c" * 32

METRICS_A: dict[str, Any] = {
    "traceId": TID_A,
    "runId": "run-a",
    "protocol": "a2a-t",
    "scenario": "night",
    "counts": {"serverObservations": 1},
    "negotiation": {
        "roundsDetail": [
            {
                "round": 1,
                "proposeAt": "2026-11-09T10:00:00.000Z",
                "decisionAt": "2026-11-09T10:00:02.000Z",
                "replySentAt": "2026-11-09T10:00:03.000Z",
            }
        ]
    },
}

METRICS_B: dict[str, Any] = {"traceId": TID_B, "runId": "run-b", "counts": {"serverObservations": 1}}


class FakeExporter:
    """Offline TraceExporter double."""

    def __init__(
        self,
        *,
        metrics_by_trace: Mapping[str, Mapping[str, Any]] | None = None,
        full_results: Mapping[str, Mapping[str, Any]] | None = None,
    ) -> None:
        self.metrics_by_trace = dict(metrics_by_trace or {})
        self.full_results = dict(full_results or {})

    def metrics(self, trace_id: str) -> tuple[Mapping[str, Any], Sequence[str]]:
        return self.metrics_by_trace.get(trace_id, {"counts": {"serverObservations": 1}}), []

    def full_export(self, trace_id: str) -> Mapping[str, Any]:
        return self.full_results.get(
            trace_id,
            {
                "exportDir": f"/tmp/export/{trace_id}",
                "zip": f"/tmp/export/{trace_id}.zip",
                "metrics": {"counts": {"serverObservations": 1}},
                "warnings": [],
            },
        )


class FailingExporter(FakeExporter):
    """FakeExporter that fails the given trace ids, to exercise the mixed-status table."""

    def __init__(
        self,
        failing: set[str],
        *,
        metrics_by_trace: Mapping[str, Mapping[str, Any]] | None = None,
    ) -> None:
        super().__init__(metrics_by_trace=metrics_by_trace)
        self.failing = failing

    def metrics(self, trace_id: str) -> tuple[Mapping[str, Any], Sequence[str]]:
        if trace_id in self.failing:
            raise RuntimeError(f"cannot export {trace_id}")
        return super().metrics(trace_id)


class StubManager:
    """Duck-typed manager for the running-job guards the real manager cannot stay in."""

    configured = True
    running_job_id = "job-running"

    def get(self, job_id: str, *, since: int = 0) -> dict[str, object] | None:
        del since
        if job_id != "job-running":
            return None
        return {"jobId": job_id, "status": "running", "results": []}

    def rows(self, job_id: str) -> list[dict[str, Any]] | None:
        if job_id != "job-running":
            return None
        return []

    def list_jobs(self, *, limit: int = 20) -> list[dict[str, object]]:
        del limit
        return []

    def archive_path(self, job_id: str, trace_id: str) -> Path | None:
        del job_id, trace_id
        return None

    def create(self, trace_ids: Sequence[str], *, full: bool = False) -> dict[str, object]:
        del trace_ids, full
        raise ObserveBusyError("an observe job is already running")


def _manager(tmp_path: Path, exporter: FakeExporter) -> ObserveJobManager:
    return ObserveJobManager(runs_dir=tmp_path, exporter=exporter)


def _routes(
    config: SampleConfig,
    tmp_path: Path,
    *,
    manager: Any | None = None,
    client_db_path: Path | None = None,
) -> list[Route]:
    return build_observe_routes(
        config=config,
        runs_dir=tmp_path,
        env_path=None,
        client_db_path=client_db_path,
        manager=manager,
    )


@asynccontextmanager
async def _asgi(routes: Sequence[Route]) -> AsyncGenerator[httpx.AsyncClient]:
    app = Starlette(routes=list(routes))
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


# ------------------------------------------------------------------ page/static


async def test_page_and_static_assets_are_served(offline_config: SampleConfig, tmp_path: Path) -> None:
    async with _asgi(_routes(offline_config, tmp_path)) as client:
        page = await client.get("/observe")
        css = await client.get("/observe/app.css")
        script = await client.get("/observe/main.js")

    assert page.status_code == 200
    for control in ("trace-input", "full-export", "start-button", "results-table", "legend"):
        assert f'id="{control}"' in page.text, control
    assert css.status_code == 200
    assert css.headers["content-type"].startswith("text/css")
    assert script.status_code == 200
    assert script.headers["content-type"].startswith("text/javascript")


# ------------------------------------------------------------------------ meta


async def test_meta_reports_an_unconfigured_langfuse(offline_config: SampleConfig, tmp_path: Path) -> None:
    async with _asgi(_routes(offline_config, tmp_path)) as client:
        response = await client.get("/api/observe/meta")

    assert response.status_code == 200
    meta = response.json()
    assert meta["configured"] is False
    assert meta["hostLabel"] == "us.cloud.langfuse.com"
    assert meta["uiBaseUrl"] == offline_config.langfuse_ui_base_url
    assert meta["toolVersion"] == langfuse_export.__version__
    assert meta["maxTraces"] == 200
    assert meta["runningJobId"] is None


async def test_meta_host_label_strips_credentials_and_path(offline_config: SampleConfig, tmp_path: Path) -> None:
    config = dataclasses.replace(
        offline_config,
        langfuse_base_url="https://user:secret@langfuse.internal:8443/api/public",
        langfuse_ui_base_url="https://langfuse.internal:8443",
    )
    async with _asgi(_routes(config, tmp_path)) as client:
        response = await client.get("/api/observe/meta")

    meta = response.json()
    assert meta["hostLabel"] == "langfuse.internal:8443"
    assert "secret" not in response.text


async def test_post_without_credentials_returns_409(offline_config: SampleConfig, tmp_path: Path) -> None:
    async with _asgi(_routes(offline_config, tmp_path)) as client:
        response = await client.post("/api/observe/jobs", json={"traceIds": [TID_A]})

    assert response.status_code == 409
    assert response.json()["error"] == "langfuse_not_configured"


# ---------------------------------------------------------------------- fields


async def test_fields_endpoint_matches_the_table_contract(offline_config: SampleConfig, tmp_path: Path) -> None:
    async with _asgi(_routes(offline_config, tmp_path)) as client:
        response = await client.get("/api/observe/fields")

    assert response.status_code == 200
    payload = response.json()
    assert payload["mainColumns"] == list(MAIN_COLUMNS)
    assert payload["roundsColumns"] == list(ROUNDS_COLUMNS)
    assert len(payload["fields"]) == len(FIELD_NOTES)
    assert set(payload["fields"][0]) == {"column", "group", "meaning_zh", "source", "caveat"}


# ------------------------------------------------------------------------ runs


async def test_runs_endpoint_lists_only_rows_with_a_trace_id(offline_config: SampleConfig, tmp_path: Path) -> None:
    path = tmp_path / "client.sqlite3"
    store = SqliteClientStore(path)
    await store.ensure_conversation(conversation_id="conv-observe")
    await store.create_run(
        ClientRunRecord(
            run_id="run-observe",
            conversation_id="conv-observe",
            scenario="night",
            mode="auto",
            transport="sse",
            discovery="direct",
            user_request="save energy",
            protocol="a2a-t",
            source_kind="tester",
            source={"kind": "tester", "caseId": "TC-001", "batchId": "batch-1"},
            trace_id=TID_A,
        )
    )
    await store.ensure_conversation(conversation_id="conv-legacy")
    await store.create_run(
        ClientRunRecord(
            run_id="run-no-trace",
            conversation_id="conv-legacy",
            scenario="night",
            mode="auto",
            transport="sse",
            discovery="direct",
            user_request="legacy",
        )
    )

    async with _asgi(_routes(offline_config, tmp_path, client_db_path=path)) as client:
        response = await client.get("/api/observe/runs?limit=50")

    assert response.status_code == 200
    runs = response.json()["runs"]
    assert [row["runId"] for row in runs] == ["run-observe"]
    assert runs[0]["traceId"] == TID_A
    assert runs[0]["caseId"] == "TC-001"
    assert runs[0]["batchId"] == "batch-1"
    assert runs[0]["protocol"] == "a2a-t"
    assert runs[0]["scenario"] == "night"
    assert "createdAt" in runs[0]


async def test_runs_endpoint_without_a_database_returns_empty(offline_config: SampleConfig, tmp_path: Path) -> None:
    async with _asgi(_routes(offline_config, tmp_path)) as client:
        response = await client.get("/api/observe/runs")

    assert response.status_code == 200
    assert response.json() == {"runs": []}


# ------------------------------------------------------------------------- jobs


async def test_post_creates_a_job_and_job_get_returns_its_results(offline_config: SampleConfig, tmp_path: Path) -> None:
    manager = _manager(tmp_path, FakeExporter(metrics_by_trace={TID_A: METRICS_A}))
    async with _asgi(_routes(offline_config, tmp_path, manager=manager)) as client:
        created = await client.post("/api/observe/jobs", json={"traceIds": [TID_A], "full": False})
        assert created.status_code == 201
        snapshot = created.json()
        job_id = snapshot["jobId"]
        assert snapshot["total"] == 1
        assert snapshot["full"] is False

        await manager.wait(job_id)
        fetched = await client.get(f"/api/observe/jobs/{job_id}")

    assert fetched.status_code == 200
    body = fetched.json()
    assert body["status"] == "done"
    assert body["done"] == 1
    assert body["results"][0]["traceId"] == TID_A
    assert body["results"][0]["status"] == "ok"


async def test_post_rejects_an_invalid_trace_id(offline_config: SampleConfig, tmp_path: Path) -> None:
    manager = _manager(tmp_path, FakeExporter())
    async with _asgi(_routes(offline_config, tmp_path, manager=manager)) as client:
        bad = await client.post("/api/observe/jobs", json={"traceIds": ["not-hex"]})
        url = await client.post(
            "/api/observe/jobs",
            json={"traceIds": ["https://langfuse.example/api/public/traces/" + TID_A]},
        )

    assert bad.status_code == 400
    assert bad.json()["error"] == "invalid_trace_id"
    assert url.status_code == 400
    assert url.json()["error"] == "invalid_trace_id"


async def test_post_rejects_too_many_traces(offline_config: SampleConfig, tmp_path: Path) -> None:
    manager = _manager(tmp_path, FakeExporter())
    trace_ids = [uuid.uuid4().hex for _ in range(201)]
    async with _asgi(_routes(offline_config, tmp_path, manager=manager)) as client:
        response = await client.post("/api/observe/jobs", json={"traceIds": trace_ids})

    assert response.status_code == 400
    assert response.json()["error"] == "too_many_traces"


async def test_post_rejects_a_malformed_body(offline_config: SampleConfig, tmp_path: Path) -> None:
    manager = _manager(tmp_path, FakeExporter())
    async with _asgi(_routes(offline_config, tmp_path, manager=manager)) as client:
        not_json = await client.post(
            "/api/observe/jobs",
            content=b"not json",
            headers={"Content-Type": "application/json"},
        )
        not_a_list = await client.post("/api/observe/jobs", json={"traceIds": TID_A})

    assert not_json.status_code == 400
    assert not_json.json()["error"] == "invalid_request"
    assert not_a_list.status_code == 400
    assert not_a_list.json()["error"] == "invalid_request"


async def test_post_while_another_job_runs_returns_409(offline_config: SampleConfig, tmp_path: Path) -> None:
    stub = StubManager()
    async with _asgi(_routes(offline_config, tmp_path, manager=stub)) as client:
        response = await client.post("/api/observe/jobs", json={"traceIds": [TID_A]})

    assert response.status_code == 409
    assert response.json()["error"] == "observe_busy"
    assert response.json()["message"]


async def test_unknown_job_returns_404(offline_config: SampleConfig, tmp_path: Path) -> None:
    manager = _manager(tmp_path, FakeExporter())
    async with _asgi(_routes(offline_config, tmp_path, manager=manager)) as client:
        job = await client.get("/api/observe/jobs/unknown")
        metrics = await client.get("/api/observe/jobs/unknown/metrics.csv")
        archive = await client.get("/api/observe/jobs/unknown/archives/" + TID_A + ".zip")

    assert job.status_code == 404
    assert job.json()["error"] == "job_not_found"
    assert metrics.status_code == 404
    assert archive.status_code == 404


async def test_job_since_filters_results(offline_config: SampleConfig, tmp_path: Path) -> None:
    manager = _manager(tmp_path, FakeExporter(metrics_by_trace={TID_A: METRICS_A, TID_B: METRICS_B}))
    async with _asgi(_routes(offline_config, tmp_path, manager=manager)) as client:
        created = await client.post("/api/observe/jobs", json={"traceIds": [TID_A, TID_B]})
        job_id = created.json()["jobId"]
        await manager.wait(job_id)

        tail = await client.get(f"/api/observe/jobs/{job_id}?since=2")
        invalid_since = await client.get(f"/api/observe/jobs/{job_id}?since=abc")

    assert [row["seq"] for row in tail.json()["results"]] == [2]
    assert tail.json()["nextSeq"] == 3
    assert [row["seq"] for row in invalid_since.json()["results"]] == [1, 2]


# ------------------------------------------------------------------------- csv


async def test_metrics_csv_requires_a_finished_job(offline_config: SampleConfig, tmp_path: Path) -> None:
    stub = StubManager()
    async with _asgi(_routes(offline_config, tmp_path, manager=stub)) as client:
        metrics = await client.get("/api/observe/jobs/job-running/metrics.csv")
        rounds = await client.get("/api/observe/jobs/job-running/rounds.csv")

    assert metrics.status_code == 409
    assert metrics.json()["error"] == "observe_not_finished"
    assert rounds.status_code == 409
    assert rounds.json()["error"] == "observe_not_finished"


async def test_metrics_csv_has_a_bom_and_the_fixed_header(offline_config: SampleConfig, tmp_path: Path) -> None:
    manager = _manager(tmp_path, FakeExporter(metrics_by_trace={TID_A: METRICS_A}))
    async with _asgi(_routes(offline_config, tmp_path, manager=manager)) as client:
        created = await client.post("/api/observe/jobs", json={"traceIds": [TID_A]})
        job_id = created.json()["jobId"]
        await manager.wait(job_id)
        response = await client.get(f"/api/observe/jobs/{job_id}/metrics.csv")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    disposition = response.headers["content-disposition"]
    assert re.fullmatch(r'attachment; filename="langfuse-metrics-\d{8}-\d{6}\.csv"', disposition)
    text = response.content.decode("utf-8-sig")
    assert response.content.startswith(b"\xef\xbb\xbf")
    lines = text.splitlines()
    assert lines[0] == ",".join(MAIN_COLUMNS)
    assert lines[1].startswith(TID_A + ",run-a,")


async def test_rounds_csv_lists_the_negotiation_rounds(offline_config: SampleConfig, tmp_path: Path) -> None:
    manager = _manager(tmp_path, FakeExporter(metrics_by_trace={TID_A: METRICS_A}))
    async with _asgi(_routes(offline_config, tmp_path, manager=manager)) as client:
        created = await client.post("/api/observe/jobs", json={"traceIds": [TID_A]})
        job_id = created.json()["jobId"]
        await manager.wait(job_id)
        response = await client.get(f"/api/observe/jobs/{job_id}/rounds.csv")

    assert response.status_code == 200
    assert response.content.startswith(b"\xef\xbb\xbf")
    lines = response.content.decode("utf-8-sig").splitlines()
    assert lines[0] == ",".join(ROUNDS_COLUMNS)
    assert lines[1].startswith(TID_A + ",1,")
    assert "langfuse-rounds-" in response.headers["content-disposition"]


async def test_rounds_csv_returns_404_without_rounds(offline_config: SampleConfig, tmp_path: Path) -> None:
    manager = _manager(tmp_path, FakeExporter(metrics_by_trace={TID_B: METRICS_B}))
    async with _asgi(_routes(offline_config, tmp_path, manager=manager)) as client:
        created = await client.post("/api/observe/jobs", json={"traceIds": [TID_B]})
        job_id = created.json()["jobId"]
        await manager.wait(job_id)
        response = await client.get(f"/api/observe/jobs/{job_id}/rounds.csv")

    assert response.status_code == 404
    assert response.json()["error"] == "no_negotiation_rounds"


async def test_fields_csv_is_available_for_a_known_job(offline_config: SampleConfig, tmp_path: Path) -> None:
    manager = _manager(tmp_path, FakeExporter(metrics_by_trace={TID_A: METRICS_A}))
    async with _asgi(_routes(offline_config, tmp_path, manager=manager)) as client:
        created = await client.post("/api/observe/jobs", json={"traceIds": [TID_A]})
        job_id = created.json()["jobId"]
        await manager.wait(job_id)
        response = await client.get(f"/api/observe/jobs/{job_id}/fields.csv")
        unknown = await client.get("/api/observe/jobs/unknown/fields.csv")

    assert response.status_code == 200
    assert response.content.startswith(b"\xef\xbb\xbf")
    lines = response.content.decode("utf-8-sig").splitlines()
    assert lines[0] == "column,group,meaningZh,source,caveat"
    assert len(lines) == len(FIELD_NOTES) + 1
    assert response.headers["content-disposition"].endswith('"langfuse-fields.csv"')
    assert unknown.status_code == 404


# ------------------------------------------------------------------------ xlsx


async def test_table_xlsx_exports_only_ok_metrics(offline_config: SampleConfig, tmp_path: Path) -> None:
    exporter = FailingExporter(
        {TID_C},
        metrics_by_trace={TID_A: METRICS_A, TID_B: METRICS_B},
    )
    manager = _manager(tmp_path, exporter)
    async with _asgi(_routes(offline_config, tmp_path, manager=manager)) as client:
        created = await client.post("/api/observe/jobs", json={"traceIds": [TID_A, TID_B, TID_C]})
        job_id = created.json()["jobId"]
        await manager.wait(job_id)
        response = await client.get(f"/api/observe/jobs/{job_id}/table.xlsx")
        saved = tmp_path / "observability" / "jobs" / f"{job_id}-table.xlsx"

    assert response.status_code == 200
    assert response.content.startswith(b"PK")
    assert response.headers["content-type"] == (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    assert response.headers["content-disposition"] == (
        f'attachment; filename="langfuse-table-{job_id}.xlsx"'
    )
    assert saved.is_file()
    assert saved.read_bytes().startswith(b"PK")
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        sheet = archive.read("xl/worksheets/sheet1.xml").decode("utf-8")
    assert TID_A in sheet
    assert TID_B in sheet
    assert TID_C not in sheet


async def test_table_xlsx_requires_a_finished_job(offline_config: SampleConfig, tmp_path: Path) -> None:
    stub = StubManager()
    async with _asgi(_routes(offline_config, tmp_path, manager=stub)) as client:
        running = await client.get("/api/observe/jobs/job-running/table.xlsx")
        unknown = await client.get("/api/observe/jobs/unknown/table.xlsx")

    assert running.status_code == 409
    assert running.json()["error"] == "observe_not_finished"
    assert unknown.status_code == 404
    assert unknown.json()["error"] == "job_not_found"


async def test_table_xlsx_returns_404_without_ok_metrics(offline_config: SampleConfig, tmp_path: Path) -> None:
    manager = _manager(tmp_path, FailingExporter({TID_C}))
    async with _asgi(_routes(offline_config, tmp_path, manager=manager)) as client:
        created = await client.post("/api/observe/jobs", json={"traceIds": [TID_C]})
        job_id = created.json()["jobId"]
        await manager.wait(job_id)
        response = await client.get(f"/api/observe/jobs/{job_id}/table.xlsx")

    assert response.status_code == 404
    payload = response.json()
    assert payload["error"] == "no_metrics"
    assert payload["message"]
    assert re.search(r"[一-鿿]", payload["message"])


# --------------------------------------------------------------------- archive


async def test_archive_route_serves_only_files_under_runs_observability(
    offline_config: SampleConfig, tmp_path: Path
) -> None:
    inside_dir = tmp_path / "observability" / "exports"
    inside_dir.mkdir(parents=True)
    inside = inside_dir / f"{TID_A}.zip"
    inside.write_bytes(b"zip-inside")
    outside_dir = tmp_path / "elsewhere"
    outside_dir.mkdir()
    outside = outside_dir / f"{TID_B}.zip"
    outside.write_bytes(b"zip-outside")
    metrics = {"counts": {"serverObservations": 1}}
    exporter = FakeExporter(
        full_results={
            TID_A: {
                "exportDir": str(inside_dir),
                "zip": str(inside),
                "metrics": metrics,
                "warnings": [],
            },
            TID_B: {
                "exportDir": str(outside_dir),
                "zip": str(outside),
                "metrics": metrics,
                "warnings": [],
            },
        }
    )
    manager = _manager(tmp_path, exporter)
    async with _asgi(_routes(offline_config, tmp_path, manager=manager)) as client:
        created = await client.post("/api/observe/jobs", json={"traceIds": [TID_A, TID_B], "full": True})
        job_id = created.json()["jobId"]
        await manager.wait(job_id)
        served = await client.get(f"/api/observe/jobs/{job_id}/archives/{TID_A}.zip")
        rejected = await client.get(f"/api/observe/jobs/{job_id}/archives/{TID_B}.zip")

    assert served.status_code == 200
    assert served.headers["content-type"] == "application/zip"
    assert served.content == b"zip-inside"
    assert served.headers["content-disposition"].endswith(f'"{TID_A}.zip"')
    assert rejected.status_code == 404
    assert rejected.json()["error"] == "archive_not_found"


# ---------------------------------------------------------------------- wiring


async def test_create_app_exposes_the_observe_console(offline_config: SampleConfig, tmp_path: Path) -> None:
    app = create_app(offline_config, runs_dir=tmp_path, env_path=None)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        page = await client.get("/observe")
        meta = await client.get("/api/observe/meta")

    assert page.status_code == 200
    assert "trace-input" in page.text
    assert meta.status_code == 200
    assert meta.json()["configured"] is False
