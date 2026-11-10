"""Tests for the Langfuse observe job manager (design 2026-11-09 §6.2-§6.5)."""

from __future__ import annotations

import json
import time
from collections.abc import Mapping, Sequence
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from demo.web.observe import service
from demo.web.observe.service import (
    DEFAULT_JOB_TIMEOUT_SECONDS,
    DEFAULT_MAX_TRACES,
    DEFAULT_TRACE_TIMEOUT_SECONDS,
    TRACE_ID_PATTERN,
    InvalidTraceIdError,
    ObserveBusyError,
    ObserveJobManager,
    TooManyTracesError,
    build_exporter,
)
from langfuse_export.client import AuthError, NetworkError, NotFoundError
from shared.config import SampleConfig

TID_A = "a" * 32
TID_B = "b" * 32
TID_C = "c" * 32


class FakeExporter:
    """Offline TraceExporter double with per-trace results and call counters."""

    def __init__(
        self,
        *,
        metrics_by_trace: Mapping[str, Mapping[str, Any]] | None = None,
        errors: Mapping[str, Exception] | None = None,
        delay: float = 0.0,
        full_results: Mapping[str, Mapping[str, Any]] | None = None,
    ) -> None:
        self.metrics_by_trace = dict(metrics_by_trace or {})
        self.errors = dict(errors or {})
        self.delay = delay
        self.full_results = dict(full_results or {})
        self.metrics_calls: list[str] = []
        self.full_calls: list[str] = []

    def metrics(self, trace_id: str, *, run_id: str | None = None) -> tuple[Mapping[str, Any], Sequence[str]]:
        del run_id
        self.metrics_calls.append(trace_id)
        if self.delay:
            time.sleep(self.delay)
        error = self.errors.get(trace_id)
        if error is not None:
            raise error
        return self.metrics_by_trace.get(trace_id, {"counts": {"serverObservations": 1}}), []

    def full_export(self, trace_id: str, *, run_id: str | None = None) -> Mapping[str, Any]:
        del run_id
        self.full_calls.append(trace_id)
        if self.delay:
            time.sleep(self.delay)
        error = self.errors.get(trace_id)
        if error is not None:
            raise error
        if trace_id in self.full_results:
            return self.full_results[trace_id]
        return {
            "exportDir": f"/tmp/export/{trace_id}",
            "zip": f"/tmp/export/{trace_id}.zip",
            "metrics": {"counts": {"serverObservations": 1}},
            "warnings": [],
        }


class FakeClock:
    """Deterministic monotonic clock: every read advances by ``step`` seconds."""

    def __init__(self, step: float) -> None:
        self.value = 0.0
        self.step = step

    def __call__(self) -> float:
        current = self.value
        self.value += self.step
        return current


def fake_config(configured: bool) -> SampleConfig:
    return cast(
        SampleConfig,
        SimpleNamespace(
            langfuse_configured=configured,
            langfuse_base_url="https://langfuse.example",
            langfuse_public_key="pk" if configured else "",
            langfuse_secret_key="sk" if configured else "",
        ),
    )


def test_constants_match_the_design_contract() -> None:
    assert TRACE_ID_PATTERN.pattern == r"^[0-9a-fA-F]{32}$"
    assert DEFAULT_MAX_TRACES == 200
    assert DEFAULT_TRACE_TIMEOUT_SECONDS == 180.0
    assert DEFAULT_JOB_TIMEOUT_SECONDS == 3600.0


def test_configured_is_false_without_an_exporter(tmp_path: Path) -> None:
    manager = ObserveJobManager(runs_dir=tmp_path, exporter=None)
    assert manager.configured is False


async def test_create_rejects_an_empty_input(tmp_path: Path) -> None:
    manager = ObserveJobManager(runs_dir=tmp_path, exporter=FakeExporter())
    with pytest.raises(InvalidTraceIdError):
        manager.create([])
    with pytest.raises(InvalidTraceIdError):
        manager.create(["   "])


async def test_create_rejects_urls_and_malformed_ids(tmp_path: Path) -> None:
    manager = ObserveJobManager(runs_dir=tmp_path, exporter=FakeExporter())
    for value in ("https://cloud.langfuse.com/trace/abc", "abc123", TID_A + "0"):
        with pytest.raises(InvalidTraceIdError):
            manager.create([value])


async def test_create_strips_and_dedupes_preserving_order(tmp_path: Path) -> None:
    manager = ObserveJobManager(runs_dir=tmp_path, exporter=FakeExporter())
    snapshot = manager.create([f" {TID_B} ", TID_A, TID_B])
    assert snapshot["total"] == 2
    await manager.wait(cast(str, snapshot["jobId"]))
    assert [row["traceId"] for row in manager.rows(cast(str, snapshot["jobId"])) or []] == [TID_B, TID_A]


async def test_create_rejects_too_many_traces(tmp_path: Path) -> None:
    manager = ObserveJobManager(runs_dir=tmp_path, exporter=FakeExporter(), max_traces=2)
    with pytest.raises(TooManyTracesError):
        manager.create([TID_A, TID_B, TID_C])


async def test_create_rejects_a_second_job_while_busy(tmp_path: Path) -> None:
    exporter = FakeExporter(delay=0.1)
    manager = ObserveJobManager(runs_dir=tmp_path, exporter=exporter)
    first = manager.create([TID_A])
    with pytest.raises(ObserveBusyError):
        manager.create([TID_B])
    await manager.wait(cast(str, first["jobId"]))
    second = manager.create([TID_B])
    assert second["status"] == "running"
    await manager.wait(cast(str, second["jobId"]))


async def test_job_exports_sequentially_and_keeps_partial_failures(tmp_path: Path) -> None:
    exporter = FakeExporter(
        metrics_by_trace={TID_A: {"counts": {"serverObservations": 2}}},
        errors={TID_B: NetworkError("down")},
    )
    manager = ObserveJobManager(runs_dir=tmp_path, exporter=exporter)

    created = manager.create([TID_A, TID_B, TID_C])
    final = await manager.wait(cast(str, created["jobId"]))

    assert exporter.metrics_calls == [TID_A, TID_B, TID_C]
    assert final["status"] == "done"
    assert final["total"] == 3
    assert final["done"] == 2
    assert final["failed"] == 1
    assert final["nextSeq"] == 4
    statuses = [row["status"] for row in cast(list[dict[str, Any]], final["results"])]
    assert statuses == ["ok", "network", "ok"]


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (NotFoundError("missing"), "not_found"),
        (AuthError("denied"), "auth"),
        (NetworkError("down"), "network"),
        (ValueError("boom"), "error"),
    ],
)
async def test_export_errors_map_to_row_status(tmp_path: Path, error: Exception, expected: str) -> None:
    manager = ObserveJobManager(runs_dir=tmp_path, exporter=FakeExporter(errors={TID_A: error}))
    created = manager.create([TID_A])
    final = await manager.wait(cast(str, created["jobId"]))
    rows = cast(list[dict[str, Any]], final["results"])
    assert rows[0]["status"] == expected
    assert rows[0]["metrics"] is None
    # The CSV row must say why it failed, not just mark the trace as failed.
    assert any(str(error) in warning for warning in rows[0]["warnings"])


async def test_full_mode_uses_full_export_and_keeps_archive_fields(tmp_path: Path) -> None:
    archive = tmp_path / "observability" / f"{TID_A}.zip"
    exporter = FakeExporter(
        full_results={
            TID_A: {
                "exportDir": str(tmp_path / "observability" / TID_A),
                "zip": str(archive),
                "metrics": {"counts": {"serverObservations": 1}},
                "warnings": ["legacy trace fallback"],
            }
        }
    )
    manager = ObserveJobManager(runs_dir=tmp_path, exporter=exporter)

    created = manager.create([TID_A], full=True)
    final = await manager.wait(cast(str, created["jobId"]))

    assert exporter.full_calls == [TID_A]
    assert exporter.metrics_calls == []
    row = cast(list[dict[str, Any]], final["results"])[0]
    assert row["status"] == "ok"
    assert row["exportDir"] == str(tmp_path / "observability" / TID_A)
    assert row["archive"] == str(archive)
    assert row["warnings"] == ["legacy trace fallback"]
    assert final["full"] is True


async def test_missing_server_observations_adds_a_warning(tmp_path: Path) -> None:
    exporter = FakeExporter(metrics_by_trace={TID_A: {"counts": {"serverObservations": 0}}})
    manager = ObserveJobManager(runs_dir=tmp_path, exporter=exporter)
    final = await manager.wait(cast(str, manager.create([TID_A])["jobId"]))
    row = cast(list[dict[str, Any]], final["results"])[0]
    assert row["status"] == "ok"
    assert "no server-side observations yet; re-export after the run flushes" in row["warnings"]


async def test_local_run_is_attached_and_lookup_errors_are_swallowed(tmp_path: Path) -> None:
    def local_run(trace_id: str) -> Mapping[str, object] | None:
        if trace_id == TID_A:
            return {"runId": "run-a", "status": "completed"}
        raise RuntimeError("database is locked")

    manager = ObserveJobManager(runs_dir=tmp_path, exporter=FakeExporter(), local_run=local_run)
    final = await manager.wait(cast(str, manager.create([TID_A, TID_B])["jobId"]))
    rows = cast(list[dict[str, Any]], final["results"])
    assert rows[0]["localRun"] == {"runId": "run-a", "status": "completed"}
    assert rows[1]["localRun"] is None


async def test_job_writes_nothing_to_stdout_or_stderr(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    manager = ObserveJobManager(runs_dir=tmp_path, exporter=FakeExporter())
    final = await manager.wait(cast(str, manager.create([TID_A])["jobId"]))
    assert final["status"] == "done"
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""


async def test_slow_trace_times_out_without_aborting_the_job(tmp_path: Path) -> None:
    manager = ObserveJobManager(
        runs_dir=tmp_path,
        exporter=FakeExporter(delay=0.2),
        trace_timeout_seconds=0.05,
    )
    final = await manager.wait(cast(str, manager.create([TID_A])["jobId"]))
    row = cast(list[dict[str, Any]], final["results"])[0]
    assert row["status"] == "timeout"
    assert row["metrics"] is None
    assert any("timed out" in warning for warning in row["warnings"])
    assert final["status"] == "done"


async def test_job_time_limit_marks_remaining_traces_without_exporting(tmp_path: Path) -> None:
    exporter = FakeExporter()
    manager = ObserveJobManager(
        runs_dir=tmp_path,
        exporter=exporter,
        job_timeout_seconds=5.0,
        clock=FakeClock(step=1.0),
    )
    final = await manager.wait(cast(str, manager.create([TID_A, TID_B, TID_C])["jobId"]))

    rows = cast(list[dict[str, Any]], final["results"])
    assert [row["status"] for row in rows] == ["ok", "ok", "timeout"]
    assert rows[2]["warnings"] == ["job time limit reached"]
    assert rows[2]["metrics"] is None
    assert exporter.metrics_calls == [TID_A, TID_B]


async def test_get_since_returns_only_rows_after_the_cursor(tmp_path: Path) -> None:
    manager = ObserveJobManager(runs_dir=tmp_path, exporter=FakeExporter())
    job_id = cast(str, manager.create([TID_A, TID_B])["jobId"])
    await manager.wait(job_id)

    cursor = manager.get(job_id, since=2)
    assert cursor is not None
    assert [row["seq"] for row in cast(list[dict[str, Any]], cursor["results"])] == [2]
    empty = manager.get(job_id, since=9)
    assert empty is not None
    assert empty["results"] == []
    assert manager.get("unknown") is None


async def test_final_job_is_persisted_and_listed_newest_first(tmp_path: Path) -> None:
    manager = ObserveJobManager(runs_dir=tmp_path, exporter=FakeExporter())
    job_one = cast(str, manager.create([TID_A])["jobId"])
    await manager.wait(job_one)
    job_two = cast(str, manager.create([TID_B])["jobId"])
    await manager.wait(job_two)

    path = tmp_path / "observability" / "jobs" / f"{job_two}.json"
    assert path.is_file()
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["jobId"] == job_two
    assert payload["status"] == "done"
    assert payload["results"][0]["traceId"] == TID_B

    summaries = manager.list_jobs(limit=1)
    assert len(summaries) == 1
    assert summaries[0]["jobId"] == job_two
    assert set(summaries[0]) == {
        "jobId",
        "createdAt",
        "full",
        "total",
        "done",
        "failed",
        "status",
    }
    all_jobs = manager.list_jobs()
    assert [summary["jobId"] for summary in all_jobs] == [job_two, job_one]


async def test_rows_returns_all_entries_and_unknown_job_is_none(tmp_path: Path) -> None:
    manager = ObserveJobManager(runs_dir=tmp_path, exporter=FakeExporter())
    job_id = cast(str, manager.create([TID_A, TID_B])["jobId"])
    await manager.wait(job_id)
    rows = manager.rows(job_id)
    assert rows is not None
    assert [row["traceId"] for row in rows] == [TID_A, TID_B]
    assert manager.rows("unknown") is None


async def test_archive_path_guards_against_traversal_and_missing_files(tmp_path: Path) -> None:
    inside = tmp_path / "observability" / "archives" / f"{TID_A}.zip"
    inside.parent.mkdir(parents=True)
    inside.write_bytes(b"PK")
    outside = tmp_path / "outside.zip"
    outside.write_bytes(b"PK")
    missing = tmp_path / "observability" / "archives" / f"{TID_C}.zip"
    exporter = FakeExporter(
        full_results={
            TID_A: {"zip": str(inside), "metrics": {}, "warnings": [], "exportDir": None},
            TID_B: {"zip": str(outside), "metrics": {}, "warnings": [], "exportDir": None},
            TID_C: {"zip": str(missing), "metrics": {}, "warnings": [], "exportDir": None},
        }
    )
    manager = ObserveJobManager(runs_dir=tmp_path, exporter=exporter)
    job_id = cast(str, manager.create([TID_A, TID_B, TID_C], full=True)["jobId"])
    await manager.wait(job_id)

    assert manager.archive_path(job_id, TID_A) == inside.resolve()
    assert manager.archive_path(job_id, TID_B) is None
    assert manager.archive_path(job_id, TID_C) is None
    assert manager.archive_path("unknown", TID_A) is None


def test_build_exporter_requires_credentials(tmp_path: Path) -> None:
    assert build_exporter(fake_config(False), None, tmp_path) is None
    exporter = build_exporter(fake_config(True), None, tmp_path)
    assert isinstance(exporter, service.LangfuseExporter)


def test_langfuse_exporter_merges_env_and_uses_runs_dir_as_cwd(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    env_file = tmp_path / "run.env"
    env_file.write_text(
        "LANGFUSE_PROXY=http://dotenv.example:1\nLANGFUSE_CACERT=/dotenv/ca.pem\nSOME_OTHER_KEY=ignored\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("LANGFUSE_PROXY", "http://process.example:2")
    runs_dir = tmp_path / "runs"
    captured: dict[str, Any] = {}

    def fake_load_config(**kwargs: Any) -> object:
        captured.update(kwargs)
        return object()

    def fake_export_metrics(config: object, trace_id: str, *, run_id: str | None = None) -> dict[str, Any]:
        del run_id
        assert isinstance(config, object)
        return {"metrics": {"counts": {"serverObservations": 1}}, "warnings": ["upstream"]}

    monkeypatch.setattr(service, "load_config", fake_load_config)
    monkeypatch.setattr(service, "export_metrics", fake_export_metrics)

    exporter = service.LangfuseExporter(
        base_url="https://langfuse.example",
        public_key="pk",
        secret_key="sk",
        env_path=env_file,
        runs_dir=runs_dir,
    )
    metrics, warnings = exporter.metrics(TID_A)

    assert metrics == {"counts": {"serverObservations": 1}}
    assert warnings == ("upstream",)
    assert captured["host"] == "https://langfuse.example"
    assert captured["public_key"] == "pk"
    assert captured["cwd"] == runs_dir
    environ = cast(dict[str, str], captured["environ"])
    assert environ["LANGFUSE_PROXY"] == "http://process.example:2"
    assert environ["LANGFUSE_CACERT"] == "/dotenv/ca.pem"
    assert "SOME_OTHER_KEY" not in environ

    exporter.metrics(TID_B)
    assert len(captured) == 5  # load_config called once; Config is cached


async def test_running_job_id_follows_the_active_job(tmp_path: Path) -> None:
    """The page resumes polling the active job after a reload."""
    manager = ObserveJobManager(runs_dir=tmp_path, exporter=FakeExporter(delay=0.1))
    assert manager.running_job_id is None
    created = manager.create([TID_A])
    assert manager.running_job_id == created["jobId"]
    await manager.wait(cast(str, created["jobId"]))
    assert manager.running_job_id is None
