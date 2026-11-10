"""Adapter contract tests for the sample-side patches P1-P6.

The vendored upstream package is kept byte-for-byte except for the patches
documented in ``langfuse_export/UPSTREAM.md``. These tests pin the behaviour the
web observer depends on, without touching the upstream CLI contracts.
"""

from __future__ import annotations

import shutil
import types
from collections.abc import Iterator
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from langfuse_export import cli
from langfuse_export.client import LangfuseClient, NotFoundError
from langfuse_export.config import Config, ConfigError

TRACE = "9f2c1a7de4b34c0e8a5f2d1c6b9e3a70"
TRACE_2 = "1a2b3c4d5e6f708192a3b4c5d6e7f809"
EXPORTED_AT = datetime(2026, 11, 10, 1, 2, 3, tzinfo=timezone.utc)


class FakeClient:
    """Stand-in for ``LangfuseClient`` that records closure."""

    def __init__(self) -> None:
        self.records: list[Any] = []
        self.proxies: dict[str, str] | None = None
        self.verify: bool | str = True
        self.log: Any = lambda message: None
        self.closed = 0

    def close(self) -> None:
        self.closed += 1


class FakeBackend:
    """Minimal backend: returns canned rows/scores and no media."""

    def __init__(self, rows: list[dict[str, Any]], scores: list[dict[str, Any]] | None = None) -> None:
        self.rows = rows
        self.scores = scores or []
        self.warnings: list[str] = []
        self.truncated = False
        self.client = types.SimpleNamespace()
        self.kind = "v4"
        self.api_version = "v4"

    def fetch_observations(self, *args: Any, **kwargs: Any) -> list[dict[str, Any]]:
        return self.rows

    def fetch_scores(self, *args: Any, **kwargs: Any) -> list[dict[str, Any]]:
        return self.scores

    def fetch_project(self) -> tuple[str | None, str | None]:
        return "project_1", "Project"

    def fetch_media(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        raise AssertionError("media fetch is not expected")

    def fetch_session_observations(self, *args: Any, **kwargs: Any) -> list[dict[str, Any]]:
        return []


@pytest.fixture()
def workdir() -> Iterator[Path]:
    root = Path("tmp_test_integration_adapter")
    shutil.rmtree(root, ignore_errors=True)
    root.mkdir()
    try:
        yield root
    finally:
        shutil.rmtree(root, ignore_errors=True)


def _config(**overrides: Any) -> Config:
    values: dict[str, Any] = {
        "host": "https://cloud.langfuse.com",
        "public_key": "pk-lf-test",
        "secret_key": "sk-lf-test",
        "host_source": "flag",
    }
    values.update(overrides)
    return Config(**values)


def _args(*extra: str) -> Any:
    return cli.build_parser().parse_args([TRACE, *extra])


def _wire(monkeypatch: pytest.MonkeyPatch, backend: FakeBackend, client: FakeClient) -> FakeClient:
    """Install the fakes; ``_client_from`` keeps the upstream ``(config, args)`` shape."""

    def fake_client_from(config: Config, args: Any) -> FakeClient:
        return client

    monkeypatch.setattr(cli, "select_backend", lambda client_, forced: backend)
    monkeypatch.setattr(cli, "_client_from", fake_client_from)
    return client


# -- P1: config= and emit= ------------------------------------------------
def test_run_export_uses_supplied_config_without_loading(
    monkeypatch: pytest.MonkeyPatch,
    workdir: Path,
    sample_rows: list[dict[str, Any]],
    sample_scores: list[dict[str, Any]],
) -> None:
    def explode(**kwargs: Any) -> Config:
        raise AssertionError("load_config must not be called when config= is given")

    monkeypatch.setattr(cli, "load_config", explode)
    _wire(monkeypatch, FakeBackend(sample_rows, sample_scores), FakeClient())

    result = cli.run_export(
        _args("--out", str(workdir / "out"), "--no-media", "--metrics"),
        config=_config(),
        emit=False,
    )
    assert result["traceId"] == TRACE
    assert result["metrics"] is not None


def test_run_export_emit_false_suppresses_every_write(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    workdir: Path,
    sample_rows: list[dict[str, Any]],
    sample_scores: list[dict[str, Any]],
) -> None:
    client = _wire(monkeypatch, FakeBackend(sample_rows, sample_scores), FakeClient())

    result = cli.run_export(
        _args("--out", str(workdir / "out"), "--no-media", "--verbose"),
        config=_config(insecure=True),
        emit=False,
    )
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""
    assert client.log is None
    assert result["notices"]


def test_run_export_cli_path_still_prints(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    workdir: Path,
    sample_rows: list[dict[str, Any]],
    sample_scores: list[dict[str, Any]],
) -> None:
    _wire(monkeypatch, FakeBackend(sample_rows, sample_scores), FakeClient())
    monkeypatch.setattr(cli, "load_config", lambda **kwargs: _config())

    cli.run_export(_args("--out", str(workdir / "out"), "--no-media"))
    captured = capsys.readouterr()
    assert f"exported {TRACE} ->" in captured.out


def test_run_export_print_result_false_keeps_notices_but_drops_the_result_block(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    workdir: Path,
    sample_rows: list[dict[str, Any]],
    sample_scores: list[dict[str, Any]],
) -> None:
    _wire(monkeypatch, FakeBackend(sample_rows, sample_scores), FakeClient())

    cli.run_export(
        _args("--out", str(workdir / "out"), "--no-media", "--verbose"),
        config=_config(insecure=True),
        print_result=False,
    )
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "TLS verification disabled" in captured.err


def test_run_export_accepts_the_multi_trace_namespace(
    monkeypatch: pytest.MonkeyPatch,
    workdir: Path,
    sample_rows: list[dict[str, Any]],
    sample_scores: list[dict[str, Any]],
) -> None:
    _wire(monkeypatch, FakeBackend(sample_rows, sample_scores), FakeClient())

    args = cli.build_parser().parse_args([TRACE, TRACE_2, "--out", str(workdir / "out"), "--no-media"])
    assert args.trace_id == [TRACE, TRACE_2]

    result = cli.run_export(args, config=_config(), emit=False)
    assert result["traceId"] == TRACE


# -- P2: session close ----------------------------------------------------
def test_client_close_closes_the_authenticated_session(monkeypatch: pytest.MonkeyPatch) -> None:
    client = LangfuseClient(host="https://example.test", public_key="pk", secret_key="sk")
    closed: list[bool] = []
    monkeypatch.setattr(client.session, "close", lambda: closed.append(True))

    client.close()

    assert closed == [True]


def test_run_export_closes_client_on_success(
    monkeypatch: pytest.MonkeyPatch,
    workdir: Path,
    sample_rows: list[dict[str, Any]],
    sample_scores: list[dict[str, Any]],
) -> None:
    client = FakeClient()
    _wire(monkeypatch, FakeBackend(sample_rows, sample_scores), client)

    cli.run_export(_args("--out", str(workdir / "out"), "--no-media"), config=_config(), emit=False)
    assert client.closed == 1


def test_run_export_closes_client_on_exception(
    monkeypatch: pytest.MonkeyPatch,
    workdir: Path,
    sample_rows: list[dict[str, Any]],
) -> None:
    client = FakeClient()
    backend = FakeBackend([])
    _wire(monkeypatch, backend, client)

    with pytest.raises(NotFoundError):
        cli.run_export(_args("--out", str(workdir / "out"), "--no-media"), config=_config(), emit=False)
    assert client.closed == 1


# -- P3: export_metrics / export_full ------------------------------------
def test_export_metrics_matches_run_export_without_writing(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    workdir: Path,
    sample_rows: list[dict[str, Any]],
    sample_scores: list[dict[str, Any]],
) -> None:
    client = FakeClient()
    _wire(monkeypatch, FakeBackend(sample_rows, sample_scores), client)
    config = _config()

    exported = cli.run_export(
        _args("--out", str(workdir / "out"), "--no-media", "--metrics"),
        config=config,
        emit=False,
    )
    before = {path for path in workdir.rglob("*")}
    metrics = cli.export_metrics(config, TRACE)
    after = {path for path in workdir.rglob("*")}

    assert set(metrics) == {"metrics", "warnings"}
    assert metrics["metrics"] == exported["metrics"]
    assert metrics["warnings"] == exported["warnings"]
    assert metrics["metrics"]["latency"]["endToEndMs"] == exported["metrics"]["latency"]["endToEndMs"]
    assert before == after
    assert capsys.readouterr() == ("", "")
    assert client.closed == 2


def test_export_metrics_keeps_not_found_semantics(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    client = FakeClient()
    _wire(monkeypatch, FakeBackend([]), client)

    with pytest.raises(NotFoundError):
        cli.export_metrics(_config(), TRACE)

    assert client.closed == 1
    assert capsys.readouterr() == ("", "")


def test_export_metrics_rejects_invalid_trace_id(monkeypatch: pytest.MonkeyPatch) -> None:
    _wire(monkeypatch, FakeBackend([]), FakeClient())
    with pytest.raises(ConfigError):
        cli.export_metrics(_config(), "not a trace id!")


def test_export_metrics_forwards_round_mode(
    monkeypatch: pytest.MonkeyPatch,
    sample_rows: list[dict[str, Any]],
    sample_scores: list[dict[str, Any]],
) -> None:
    _wire(monkeypatch, FakeBackend(sample_rows, sample_scores), FakeClient())

    legacy = cli.export_metrics(_config(), TRACE)
    decision = cli.export_metrics(_config(), TRACE, round_mode="decision")

    assert legacy["metrics"]["negotiation"]["roundMode"] == "legacy"
    assert decision["metrics"]["negotiation"]["roundMode"] == "decision"


@pytest.mark.parametrize("wrapper", ["metrics", "full"])
def test_an_unknown_round_mode_is_rejected_before_argparse(
    monkeypatch: pytest.MonkeyPatch,
    sample_rows: list[dict[str, Any]],
    sample_scores: list[dict[str, Any]],
    tmp_path: Path,
    wrapper: str,
) -> None:
    """A bad mode must raise ValueError, not argparse's SystemExit (sample patch P9c)."""
    _wire(monkeypatch, FakeBackend(sample_rows, sample_scores), FakeClient())

    with pytest.raises(ValueError, match="round_mode"):
        if wrapper == "metrics":
            cli.export_metrics(_config(), TRACE, round_mode="bogus")
        else:
            cli.export_full(_config(), TRACE, tmp_path, round_mode="bogus")


def test_export_full_builds_the_expected_namespace(
    monkeypatch: pytest.MonkeyPatch, workdir: Path
) -> None:
    config = _config()
    captured: dict[str, Any] = {}

    def fake_run_export(
        args: Any,
        exported_at: Any = None,
        *,
        config: Config | None = None,
        emit: bool = True,
        print_result: bool = True,
    ) -> dict[str, Any]:
        captured.update(args=args, config=config, emit=emit, print_result=print_result)
        return {"traceId": args.trace_id[0]}

    monkeypatch.setattr(cli, "run_export", fake_run_export)

    result = cli.export_full(config, TRACE, workdir / "full", zip_file=True)

    args = captured["args"]
    # 0.4.0 made trace_id nargs="+"; a single id arrives as a one-item list.
    assert args.trace_id == [TRACE]
    assert args.out == str(workdir / "full")
    assert args.metrics is True
    assert args.no_media is True
    assert args.zip is True
    assert captured["config"] is config
    assert captured["emit"] is False
    assert captured["print_result"] is True
    assert result == {"traceId": TRACE}
    # The default stays legacy, so --round-mode is omitted from the argv.
    assert args.round_mode == "legacy"

    cli.export_full(config, TRACE, workdir / "full", round_mode="decision")
    assert captured["args"].round_mode == "decision"

    cli.export_full(config, TRACE, workdir / "full", zip_file=False)
    assert captured["args"].zip is False


def test_export_full_runs_silently(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    workdir: Path,
    sample_rows: list[dict[str, Any]],
    sample_scores: list[dict[str, Any]],
) -> None:
    client = FakeClient()
    _wire(monkeypatch, FakeBackend(sample_rows, sample_scores), client)

    result = cli.export_full(_config(), TRACE, workdir / "full", zip_file=True)

    assert set(result) >= {"exportDir", "zip", "metrics", "warnings"}
    assert Path(result["zip"]).is_file()
    assert result["metrics"] is not None
    assert capsys.readouterr() == ("", "")
    assert client.closed == 1


# -- 0.4.0 multi-trace batch ---------------------------------------------
def test_run_batch_export_uses_the_patched_run_export(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    workdir: Path,
    sample_rows: list[dict[str, Any]],
    sample_scores: list[dict[str, Any]],
) -> None:
    client = _wire(monkeypatch, FakeBackend(sample_rows, sample_scores), FakeClient())
    monkeypatch.setattr(cli, "load_config", lambda **kwargs: _config())

    args = cli.build_parser().parse_args(
        [TRACE, TRACE_2, "--out", str(workdir / "batch"), "--no-media", "--metrics", "--table", "--quiet"]
    )
    summary = cli.run_batch_export(args, [TRACE, TRACE_2], exported_at=EXPORTED_AT)

    assert summary["exitCode"] == 0
    assert len(summary["exports"]) == 2
    assert len(summary["errors"]) == 0
    assert Path(summary["table"]).is_file()
    # Only the batch directory line reaches stdout; per-trace results stay silent.
    assert capsys.readouterr().out.strip().splitlines() == [summary["batchDir"]]
    assert client.closed == 2
