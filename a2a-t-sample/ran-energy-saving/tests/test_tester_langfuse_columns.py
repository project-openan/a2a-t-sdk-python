"""Unit tests of the Langfuse cells appended to the tester arbitration sheet."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import pytest
from demo.web.tester import langfuse_columns
from demo.web.tester import routes as tester_routes
from demo.web.tester.langfuse_columns import TRACE_COLUMN_HEADERS, build_trace_columns
from langfuse_export.config import Config

TRACE_ID = "0123456789abcdef0123456789abcdef"
RUN_ID = "run-1"


def _config() -> Config:
    return Config(host="http://langfuse", public_key="pk", secret_key="sk", host_source="env")


def _metrics() -> dict[str, Any]:
    return {
        "tokens": {
            "input": 1261,
            "cachedInput": 630,
            "output": 2035,
            "total": 3296,
        },
        "negotiation": {"triggered": True, "rounds": 2, "latencyMs": 4200},
        "latency": {"endToEndMs": 9000},
        "clientInterception": {"count": 1},
    }


def test_the_nine_columns_map_the_metrics_document(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    calls: list[dict[str, Any]] = []

    def fake_export(config: Config, trace_id: str, out_root: Path, **kwargs: Any) -> dict[str, Any]:
        calls.append({"config": config, "trace_id": trace_id, "out_root": out_root, **kwargs})
        return {"metrics": _metrics()}

    monkeypatch.setattr(langfuse_columns, "export_full", fake_export)

    values, note = build_trace_columns(
        _config(), run_id=RUN_ID, trace_id=TRACE_ID, out_root=tmp_path / "export"
    )

    assert note == ""
    assert values == [1261, 630, 2035, 3296, "是", 4200, 2, 9000, 1]
    assert calls == [
        {
            "config": _config(),
            "trace_id": TRACE_ID,
            "out_root": tmp_path / "export",
            "zip_file": False,
            "run_id": RUN_ID,
            "expand_metadata": ["detail"],
        }
    ]


def test_missing_numbers_are_blank_not_zero(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        langfuse_columns,
        "export_full",
        lambda *args, **kwargs: {
            "metrics": {
                "tokens": {},
                "negotiation": {"rounds": None, "latencyMs": None},
                "latency": {},
                "clientInterception": {},
            }
        },
    )
    values, note = build_trace_columns(
        _config(), run_id=RUN_ID, trace_id=TRACE_ID, out_root=tmp_path
    )

    assert note == ""
    assert values == [0, 0, 0, 0, "", "", "", "", 0]


def test_an_untouched_run_reports_no_negotiation(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        langfuse_columns,
        "export_full",
        lambda *args, **kwargs: {"metrics": {"negotiation": {"rounds": 0}}},
    )
    values, __ = build_trace_columns(_config(), run_id=RUN_ID, trace_id=TRACE_ID, out_root=tmp_path)
    assert values[4] == "否"
    assert values[6] == 0


def test_no_config_skips_the_fetch(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    def fail(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("must not fetch without credentials")

    monkeypatch.setattr(langfuse_columns, "export_full", fail)
    values, note = build_trace_columns(None, run_id=RUN_ID, trace_id=TRACE_ID, out_root=tmp_path)
    assert values == [""] * len(TRACE_COLUMN_HEADERS)
    assert note == "Langfuse 指标未取到：未配置 Langfuse 凭据"


def test_a_missing_or_malformed_trace_id_is_reported(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def fail(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("must not fetch an invalid trace id")

    monkeypatch.setattr(langfuse_columns, "export_full", fail)
    _values, missing = build_trace_columns(
        _config(), run_id=RUN_ID, trace_id="", out_root=tmp_path
    )
    _values, malformed = build_trace_columns(
        _config(), run_id=RUN_ID, trace_id="not-a-trace", out_root=tmp_path
    )
    assert missing == "Langfuse 指标未取到：缺少 Langfuse trace id"
    assert malformed == "Langfuse 指标未取到：Langfuse trace id 格式无效"


def test_an_export_exception_leaves_nine_blanks(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    def fail(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("boom")

    monkeypatch.setattr(langfuse_columns, "export_full", fail)
    values, note = build_trace_columns(
        _config(), run_id=RUN_ID, trace_id=TRACE_ID, out_root=tmp_path
    )
    assert values == [""] * len(TRACE_COLUMN_HEADERS)
    assert note == "Langfuse 指标未取到：RuntimeError: boom"


def test_an_empty_export_reports_its_warnings(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        langfuse_columns,
        "export_full",
        lambda *args, **kwargs: {"metrics": None, "warnings": ["trace not found"]},
    )
    values, note = build_trace_columns(
        _config(), run_id=RUN_ID, trace_id=TRACE_ID, out_root=tmp_path
    )
    assert values == [""] * len(TRACE_COLUMN_HEADERS)
    assert note == "Langfuse 指标未取到：trace not found"


def test_each_run_is_fetched_once_per_batch(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    calls: list[tuple[str, str, Path]] = []

    def fake(config: Config | None, run_id: str, trace_id: str, out_root: Path) -> tuple[list[object], str]:
        calls.append((run_id, trace_id, out_root))
        return [1] * len(TRACE_COLUMN_HEADERS), ""

    monkeypatch.setattr(tester_routes, "_trace_columns_with_timeout", fake)
    snapshot = {
        "items": [
            {"runId": "run-1", "langfuseTraceId": TRACE_ID, "round": 1},
            {"runId": "run-1", "langfuseTraceId": TRACE_ID, "round": 2},
            {"runId": "run-2", "langfuseTraceId": ""},
            {},
        ]
    }

    columns = tester_routes._trace_columns(snapshot, _config(), tmp_path)

    assert set(columns) == {"run-1", "run-2"}
    assert sorted(calls) == [
        ("run-1", TRACE_ID, tmp_path / "run-1" / "langfuse-export"),
        ("run-2", "", tmp_path / "run-2" / "langfuse-export"),
    ]


def test_a_missing_runs_dir_becomes_a_note_not_a_crash(tmp_path: Path) -> None:
    snapshot = {"items": [{"runId": "run-1", "langfuseTraceId": TRACE_ID}]}

    columns = tester_routes._trace_columns(snapshot, _config(), None)

    assert columns == {
        "run-1": (
            [""] * len(TRACE_COLUMN_HEADERS),
            "Langfuse 指标未取到：导出时未提供 run 目录",
        )
    }


def test_a_slow_trace_times_out_without_failing_the_batch(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def slow(*args: Any, **kwargs: Any) -> tuple[list[object], str]:
        time.sleep(0.2)
        return [1] * len(TRACE_COLUMN_HEADERS), ""

    monkeypatch.setattr(tester_routes, "TRACE_FETCH_TIMEOUT_SECONDS", 0.01)
    monkeypatch.setattr(tester_routes, "build_trace_columns", slow)

    values, note = tester_routes._trace_columns_with_timeout(
        _config(), RUN_ID, TRACE_ID, tmp_path
    )

    assert values == [""] * len(TRACE_COLUMN_HEADERS)
    assert "秒内未完成" in note
