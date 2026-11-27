"""Tests for the launcher defaults and PID reuse decisions of ``run_demo.py``."""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import demo.run_demo as run_demo
import pytest
from dotenv import dotenv_values
from shared.run_constants import (
    PROTOCOL_A2A_T,
    PROTOCOL_MODE_BOTH,
    PROTOCOL_PURE,
    normalize_protocol_name,
)


def test_write_run_env_points_both_processes_at_the_task_database(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The server writes and the UI reads the same absolute SQLite task store."""
    monkeypatch.setattr(run_demo, "RUN_DIR", tmp_path)
    env_path = run_demo._write_run_env(
        source=None,
        language="en-US",
        model_mode="live",
        discovery="direct",
        transport="sse",
        webhook_port=0,
        server_host="127.0.0.1",
        server_port=8000,
        ui_port=8080,
        gateway_port=8001,
    )

    values = dotenv_values(env_path)
    task_db = Path(str(values["A2AT_SAMPLE_TASK_DB"]))
    client_db = Path(str(values["A2AT_SAMPLE_CLIENT_DB"]))

    assert task_db.is_absolute()
    assert task_db == run_demo.TASK_DB_PATH
    assert task_db.name == "task-status.sqlite3"
    assert client_db.is_absolute()
    assert client_db == run_demo.CLIENT_DB_PATH
    assert client_db.name == "client-state.sqlite3"
    gateway_db = Path(str(values["A2AT_SAMPLE_GATEWAY_TASK_DB"]))
    assert gateway_db.is_absolute()
    assert gateway_db == run_demo.GATEWAY_TASK_DB_PATH
    assert gateway_db.name == "gateway-task-status.sqlite3"
    assert values["A2AT_SAMPLE_GATEWAY_PORT"] == "8001"
    assert Path(str(values["A2AT_SAMPLE_RUNS_DIR"])) == run_demo.RUNS_DIR


def test_write_run_env_carries_the_pure_message_mode_override(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The launcher's override must reach every file reader, not only its own process."""
    monkeypatch.setattr(run_demo, "RUN_DIR", tmp_path)
    monkeypatch.delenv("A2AT_SAMPLE_PURE_MESSAGE_MODE", raising=False)
    env_path = run_demo._write_run_env(
        source=None,
        language="en-US",
        model_mode="live",
        discovery="nms",
        transport="webhook",
        webhook_port=9001,
        server_host="127.0.0.1",
        server_port=8000,
        ui_port=8081,
        protocol="both",
        pure_message_mode="stream",
    )

    assert dotenv_values(env_path)["A2AT_SAMPLE_PURE_MESSAGE_MODE"] == "stream"


def test_write_run_env_keeps_nms_discovery_for_pure_nms(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """pure + NMS must keep nms discovery; the direct URL stays the local RAN fallback.

    The UI pins ``A2AT_SAMPLE_DIRECT_SERVER_URL`` to the vendor endpoint for a direct hop, so the
    value written here must not silently point at the OSS port.
    """
    monkeypatch.setattr(run_demo, "RUN_DIR", tmp_path)
    env_path = run_demo._write_run_env(
        source=None,
        language="en-US",
        model_mode="live",
        discovery="nms",
        transport="webhook",
        webhook_port=9001,
        server_host="127.0.0.1",
        server_port=8000,
        ui_port=8081,
        protocol="pure",
    )

    values = dotenv_values(env_path)
    assert values["A2AT_SAMPLE_DISCOVERY"] == "nms"
    assert values["A2AT_SAMPLE_TRANSPORT"] == "webhook"
    assert values["A2AT_SAMPLE_DIRECT_SERVER_URL"] == "http://127.0.0.1:8000"


def test_run_state_and_data_directories_are_configurable(tmp_path: Path) -> None:
    """Two launchers can use isolated pids/logs and isolated runs/sqlite directories."""
    env = dict(os.environ)
    env["A2AT_SAMPLE_RUN_DIR"] = str(tmp_path / "state")
    env["A2AT_SAMPLE_RUNS_DIR"] = str(tmp_path / "data")
    sample_root = str(Path(__file__).resolve().parents[1])
    env["PYTHONPATH"] = os.pathsep.join(
        part for part in (sample_root, env.get("PYTHONPATH")) if part
    )
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import demo.run_demo as m; print(m.RUN_DIR); print(m.RUNS_DIR); print(m.TASK_DB_PATH)",
        ],
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    lines = result.stdout.strip().splitlines()

    assert Path(lines[0]) == tmp_path / "state"
    assert Path(lines[1]) == tmp_path / "data"
    assert Path(lines[2]) == tmp_path / "data" / "task-status.sqlite3"


def test_ui_port_default_reads_the_env_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """``A2AT_SAMPLE_UI_PORT`` is the default of ``--ui-port``; absent means 8080."""
    monkeypatch.delenv("A2AT_SAMPLE_UI_PORT", raising=False)
    assert run_demo._ui_port_default({}) == run_demo.DEFAULT_UI_PORT
    assert run_demo._ui_port_default({"A2AT_SAMPLE_UI_PORT": "18080"}) == 18080


def test_ui_host_default_reads_the_env_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """``A2AT_SAMPLE_UI_HOST`` is the default of ``--ui-host``; absent means loopback."""
    monkeypatch.delenv("A2AT_SAMPLE_UI_HOST", raising=False)
    assert run_demo._ui_host_default({}) == run_demo.DEFAULT_UI_HOST
    assert run_demo._ui_host_default({"A2AT_SAMPLE_UI_HOST": "0.0.0.0"}) == "0.0.0.0"


def test_the_user_facing_a2a_name_maps_to_the_standard_form() -> None:
    """``a2a`` is accepted by the CLI while the internal value stays stable."""
    assert normalize_protocol_name("a2a") == PROTOCOL_PURE
    assert normalize_protocol_name("a2a-t") == PROTOCOL_A2A_T
    assert normalize_protocol_name("both") == PROTOCOL_MODE_BOTH


def test_the_web_ui_serves_both_protocols_by_default() -> None:
    """A UI process advertises the in-page protocol switch unless one form is requested."""
    from demo.web import serve

    args = serve._build_parser().parse_args(
        [
            "--env-file",
            ".env",
            "--port",
            "8080",
            "--scenario",
            "night",
            "--mode",
            "auto",
            "--transport",
            "sse",
        ]
    )
    assert args.protocol == PROTOCOL_MODE_BOTH


def test_parser_uses_the_configured_ui_port_default() -> None:
    """An explicit ``--ui-port`` overrides the configured default."""
    parser = run_demo._build_parser(default_ui_port=18080)

    assert parser.parse_args([]).ui_port == 18080
    assert parser.parse_args(["--ui-port", "9999"]).ui_port == 9999


def test_parser_uses_the_configured_ui_host_default() -> None:
    """An explicit ``--ui-host`` overrides the configured default."""
    parser = run_demo._build_parser(default_ui_host="0.0.0.0")

    assert parser.parse_args([]).ui_host == "0.0.0.0"
    assert parser.parse_args(["--ui-host", "127.0.0.1"]).ui_host == "127.0.0.1"


def test_server_trace_env_targets_the_ui_endpoint_or_the_cli_file() -> None:
    """``--ui`` posts to the UI with a local token; the CLI writes the per-run JSONL file."""
    ui_env = run_demo._server_trace_env(ui=True, ui_port=18080)

    assert ui_env["A2AT_SAMPLE_TRACE_URL"] == "http://127.0.0.1:18080"
    assert ui_env["A2AT_SAMPLE_TRACE_TOKEN"]

    cli_env = run_demo._server_trace_env(ui=False, ui_port=18080)

    assert "A2AT_SAMPLE_TRACE_URL" not in cli_env
    assert cli_env["A2AT_SAMPLE_TRACE_DIR"].replace("\\", "/").endswith("ran-energy-saving/runs")


def test_server_trace_env_reuses_a_supplied_token() -> None:
    """A reused keep-alive server keeps its recorded token, so the UI accepts its spans."""
    reused = run_demo._server_trace_env(ui=True, ui_port=18080, trace_token="recorded-token")

    assert reused["A2AT_SAMPLE_TRACE_TOKEN"] == "recorded-token"
    fresh = run_demo._server_trace_env(ui=True, ui_port=18080)
    assert fresh["A2AT_SAMPLE_TRACE_TOKEN"] != "recorded-token"
    assert len(fresh["A2AT_SAMPLE_TRACE_TOKEN"]) == 32


def test_state_round_trip_records_the_run_metadata(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """The PID file carries the mode/language/token a later launch needs to decide reuse."""
    pid_file = tmp_path / "pids.json"
    monkeypatch.setattr(run_demo, "PID_FILE", pid_file)

    run_demo._save_state(
        [11, 12],
        model_mode="live",
        language="en-US",
        trace_token="tok",
        trace_url="http://127.0.0.1:8080",
        ui_port=8080,
    )

    state = run_demo._load_state()
    assert state == {
        "pids": [11, 12],
        "model_mode": "live",
        "language": "en-US",
        "protocol_mode": "a2a-t",
        "trace_token": "tok",
        "trace_url": "http://127.0.0.1:8080",
        "ui_port": 8080,
        "gateway_port": None,
    }
    assert run_demo._load_pids() == [11, 12]


def test_state_file_is_written_owner_only(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The PID file carries the local trace token, so it must be ``0600`` like the SDK ``.env``."""
    pid_file = tmp_path / "pids.json"
    monkeypatch.setattr(run_demo, "PID_FILE", pid_file)
    chmod_calls: list[tuple[str, int]] = []
    monkeypatch.setattr(run_demo.os, "chmod", lambda path, mode: chmod_calls.append((str(path), mode)))

    run_demo._save_state([1], model_mode="live", language="en-US", trace_token="tok")

    assert chmod_calls == [(str(pid_file), 0o600)]


def test_state_loader_accepts_the_legacy_pid_only_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """An older ``{"pids": [...]}`` file still stops, but carries no reusable metadata."""
    pid_file = tmp_path / "pids.json"
    pid_file.write_text(json.dumps({"pids": [7, "8", "nope"]}), encoding="utf-8")
    monkeypatch.setattr(run_demo, "PID_FILE", pid_file)

    assert run_demo._load_state() == {"pids": [7, "8", "nope"]}
    assert run_demo._load_pids() == [7, 8]


def test_state_loader_ignores_a_corrupt_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    pid_file = tmp_path / "pids.json"
    pid_file.write_text("not-json", encoding="utf-8")
    monkeypatch.setattr(run_demo, "PID_FILE", pid_file)

    assert run_demo._load_state() == {}
    assert run_demo._load_pids() == []


class TestServerReuseDecision:
    """The launcher may only reuse a listening server with the requested configuration."""

    #: Metadata of a compatible, UI-mode keep-alive record.
    STATE: dict[str, object] = {
        "pids": [11, 12],
        "model_mode": "live",
        "language": "en-US",
        "trace_token": "tok",
        "trace_url": "http://127.0.0.1:8080",
        "ui_port": 8080,
    }

    def decide(self, **overrides: Any) -> str:
        """Call the reuse decision with a compatible UI-mode request by default."""
        params: dict[str, Any] = {
            "port_open": True,
            "state": self.STATE,
            "language": "en-US",
            "ui": True,
            "trace_url": "http://127.0.0.1:8080",
            "ui_port": 8080,
        }
        params.update(overrides)
        return run_demo._server_reuse_decision(**params)

    def test_nothing_listening_starts(self) -> None:
        assert self.decide(port_open=False) == run_demo.REUSE_START

    def test_matching_metadata_reuses_the_process(self) -> None:
        assert self.decide() == run_demo.REUSE_REUSE

    def test_language_mismatch_restarts(self) -> None:
        assert self.decide(state={**self.STATE, "language": "zh-CN"}) == run_demo.REUSE_RESTART

    def test_protocol_mode_mismatch_restarts(self) -> None:
        """A pure-only server cannot serve the A2A-T switch of a ``both`` launch."""
        assert (
            self.decide(state={**self.STATE, "protocol_mode": "pure"}, protocol_mode="a2a-t") == run_demo.REUSE_RESTART
        )
        assert (
            self.decide(state={**self.STATE, "protocol_mode": "a2a-t"}, protocol_mode="both") == run_demo.REUSE_RESTART
        )

    def test_legacy_record_defaults_to_a2a_t(self) -> None:
        """Records written before the switch existed are A2A-T deployments."""
        legacy = {key: value for key, value in self.STATE.items() if key != "protocol_mode"}
        assert self.decide(state=legacy) == run_demo.REUSE_REUSE

    def test_trace_url_mismatch_restarts(self) -> None:
        """A server pointed at another UI port would post its spans to the wrong process."""
        assert self.decide(trace_url="http://127.0.0.1:9090", ui_port=8080) == run_demo.REUSE_RESTART

    def test_ui_port_mismatch_restarts(self) -> None:
        assert self.decide(ui_port=9090) == run_demo.REUSE_RESTART

    def test_legacy_record_without_a_trace_url_restarts(self) -> None:
        legacy = {key: value for key, value in self.STATE.items() if key != "trace_url"}
        assert self.decide(state=legacy) == run_demo.REUSE_RESTART

    def test_ui_run_restarts_a_cli_keep_alive_server(self) -> None:
        """A CLI server records spans to a file; the UI would get no server trace."""
        assert self.decide(state={**self.STATE, "trace_token": ""}) == run_demo.REUSE_RESTART

    def test_cli_run_restarts_a_ui_keep_alive_server(self) -> None:
        """A UI server posts spans to its UI; the CLI runs cannot consume them."""
        assert self.decide(ui=False, trace_url="", ui_port=None) == run_demo.REUSE_RESTART

    def test_legacy_record_without_metadata_restarts(self) -> None:
        assert self.decide(state={"pids": [11]}) == run_demo.REUSE_RESTART

    def test_foreign_listener_is_refused(self) -> None:
        """A port with no PID record is not ours: refuse instead of assuming a mode."""
        assert self.decide(state={}) == run_demo.REUSE_REFUSE


def test_stop_pids_does_not_count_already_exited_processes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The reported ``stopped N`` counts real terminations, not kill attempts."""
    monkeypatch.setattr(run_demo, "_pid_alive", lambda pid: False)
    monkeypatch.setattr(
        run_demo.subprocess,
        "run",
        lambda *args, **kwargs: pytest.fail("a dead PID must not be killed again"),
    )

    assert run_demo._stop_pids([11, 12, 12]) == 0


def test_probe_host_maps_wildcard_binds_to_loopback() -> None:
    """A UI bound to ``0.0.0.0`` is probed through loopback; a specific host is used directly."""
    assert run_demo._probe_host("0.0.0.0") == "127.0.0.1"
    assert run_demo._probe_host("") == "127.0.0.1"
    assert run_demo._probe_host("192.168.1.20") == "192.168.1.20"


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        # managed child of THIS instance: the per-run env file names our state directory
        ("python -m demo.web.serve --env-file {run_env} --port 8080", True),
        # same sample, but the state directory belongs to a second instance
        ("python -m demo.web.serve --env-file C:/elsewhere/adk/.env --port 8080", False),
        # managed child without the instance marker cannot be proven ours
        ("python -m demo.web.serve --port 8080", False),
        # an older launcher served the UI itself; the port it was started with is the proof
        ("python -m demo.run_demo --ui --ui-port 8080", True),
        ("python -m demo.run_demo --ui --ui-port 8084", False),
        ("python run_demo.py --ui", False),
        ("python -m demo.run_demo --scenario night", False),
        ("nginx -g daemon off;", False),
        ("", False),
    ],
)
def test_instance_ui_requires_proof_of_ownership(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, command: str, expected: bool
) -> None:
    """Matching the sample is not enough: another instance runs the same command line."""
    monkeypatch.setattr(run_demo, "RUN_DIR", tmp_path)
    run_env = str(tmp_path / ".env")
    monkeypatch.setattr(
        run_demo, "_process_command_line", lambda pid: command.replace("{run_env}", run_env)
    )

    assert run_demo._is_instance_ui(1234, 8080) is expected


def test_instance_child_only_restarts_a_child_of_this_instance(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Starting the UI replaces our stale child, never another instance's live one."""
    monkeypatch.setattr(run_demo, "RUN_DIR", tmp_path)
    monkeypatch.setattr(
        run_demo,
        "_process_command_line",
        lambda pid: f"python -m demo.web.serve --env-file {tmp_path / '.env'}",
    )
    assert run_demo._is_instance_child(111, run_demo.UI_MODULE, 8080) is True

    monkeypatch.setattr(
        run_demo,
        "_process_command_line",
        lambda pid: f"python -m demo.web.serve --env-file {tmp_path}/../other/.env",
    )
    assert run_demo._is_instance_child(111, run_demo.UI_MODULE, 8080) is False

    # A background module carries no instance marker on its command line; the module name is the
    # only check available, and an occupied port there is a real conflict.
    monkeypatch.setattr(run_demo, "_process_command_line", lambda pid: "python -m protocol.server_app")
    assert run_demo._is_instance_child(111, run_demo.SERVER_MODULE, 8000) is True


def test_reclaim_is_opt_in_and_leaves_our_orphan_running(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """``--stop`` alone never scans the recorded port; it names the orphan instead."""
    killed: list[list[int]] = []
    monkeypatch.setattr(run_demo, "_listener_pids", lambda port: [111])
    monkeypatch.setattr(run_demo, "_is_instance_ui", lambda pid, port: True)
    monkeypatch.setattr(run_demo, "_stop_pids", lambda pids: killed.append(pids) or len(pids))

    assert run_demo._reclaim_recorded_ui(8080, explicit=False) == 0

    assert killed == []
    assert "--reclaim-port" in capsys.readouterr().out


def test_reclaim_stops_this_instances_orphan_when_asked(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """``--stop --reclaim-port`` frees the port from an orphan this instance owns."""
    killed: list[list[int]] = []
    monkeypatch.setattr(run_demo, "_listener_pids", lambda port: [111, 222])
    monkeypatch.setattr(run_demo, "_is_instance_ui", lambda pid, port: pid == 222)
    monkeypatch.setattr(run_demo, "_stop_pids", lambda pids: killed.append(pids) or len(pids))

    assert run_demo._reclaim_recorded_ui(8080, explicit=True) == 1

    assert killed == [[222]]
    assert "reclaimed 1 orphaned Web UI process(es) on port 8080 (pid 222)" in capsys.readouterr().out


def test_reclaim_never_stops_another_instances_ui(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The field incident: the recorded port was held by another instance's Web UI."""
    killed: list[list[int]] = []
    monkeypatch.setattr(run_demo, "_listener_pids", lambda port: [222])
    monkeypatch.setattr(run_demo, "_is_instance_ui", lambda pid, port: False)
    monkeypatch.setattr(run_demo, "_stop_pids", lambda pids: killed.append(pids) or len(pids))

    assert run_demo._reclaim_recorded_ui(8080, explicit=True) == 0

    assert killed == []
    assert "does not own" in capsys.readouterr().out


def test_stop_leaves_another_instances_web_ui_running(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Field regression 2026-11-26: a 8084 ``--stop`` killed the 8080 instance's Web UI.

    The recorded state pointed at a port another instance's ``demo.web.serve`` was holding; a
    default ``--stop`` must stop only its own recorded PIDs and report the foreign listener.
    """
    run_dir = tmp_path / "state"
    run_dir.mkdir()
    monkeypatch.setattr(run_demo, "RUN_DIR", run_dir)
    monkeypatch.setattr(run_demo, "PID_FILE", run_dir / "pids.json")
    run_demo._write_state({"pids": [111], "ui_port": 8080})
    monkeypatch.setattr(run_demo, "_pid_alive", lambda pid: False)
    monkeypatch.setattr(run_demo, "_port_open", lambda port, *a, **k: port == 8080)
    monkeypatch.setattr(run_demo, "_listener_pids", lambda port: [222] if port == 8080 else [])
    monkeypatch.setattr(
        run_demo,
        "_process_command_line",
        lambda pid: "python -m demo.web.serve --env-file C:/elsewhere/adk/.env --port 8080",
    )
    stopped: list[int] = []
    monkeypatch.setattr(run_demo, "_stop_pids", lambda pids: stopped.extend(pids) or 0)
    env_file = tmp_path / "source.env"
    env_file.write_text("A2AT_LANGUAGE=en-US\n", encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["run_demo.py", "--env-file", str(env_file), "--stop"])

    assert run_demo.main() == 0

    assert 222 not in stopped
    out = capsys.readouterr().out
    assert "does not own" in out
    assert "reclaimed" not in out


def test_reclaim_port_requires_stop(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """``--reclaim-port`` on its own is a usage error, not a silent no-op."""
    env_file = tmp_path / "source.env"
    env_file.write_text("A2AT_LANGUAGE=en-US\n", encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["run_demo.py", "--env-file", str(env_file), "--reclaim-port"])

    with pytest.raises(SystemExit) as exit_info:
        run_demo.main()

    assert exit_info.value.code == 2


def test_web_serve_parser_accepts_the_launcher_arguments(tmp_path: Path) -> None:
    """The UI child entrypoint understands everything the launcher passes."""
    from demo.web import serve

    args = serve._build_parser().parse_args(
        [
            "--env-file",
            str(tmp_path / ".env"),
            "--host",
            "0.0.0.0",
            "--port",
            "8080",
            "--scenario",
            "night",
            "--mode",
            "auto",
            "--transport",
            "sse",
            "--server-url",
            "http://127.0.0.1:8000",
        ]
    )

    assert args.env_file == tmp_path / ".env"
    assert args.port == 8080


def test_trace_summary_reports_llm_and_tool_counts() -> None:
    """The CLI summary prints LLM/tool counts and never fabricates usage."""
    unmeasured: list[dict[str, object]] = [
        {"kind": "llm", "tokens": {"prompt": None, "completion": None, "total": None}},
        {"kind": "tool"},
        {"kind": "message_sent"},
    ]
    assert run_demo._trace_summary(unmeasured) == "llm=1 tool=1 tokens=n/a (no usage)"

    live: list[dict[str, object]] = [{"kind": "llm", "tokens": {"prompt": 10, "completion": 5, "total": 15}}]
    assert run_demo._trace_summary(live) == "llm=1 tool=0 tokens=prompt=10 completion=5 total=15"


def test_write_run_env_anchors_relative_webhook_tls_paths_to_the_sample_root(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The client runs from RUN_DIR, so relative certificate paths must be made absolute."""
    monkeypatch.setattr(run_demo, "RUN_DIR", tmp_path)
    source = tmp_path / "source.env"
    source.write_text(
        "A2AT_SAMPLE_WEBHOOK_PUBLIC_URL=https://141.5.146.234:9000/webhook\n"
        "A2AT_SAMPLE_WEBHOOK_TLS_CERT=Orange/server.cer\n"
        "A2AT_SAMPLE_WEBHOOK_TLS_KEY=Orange/server_key_nopass.pem\n"
        "A2AT_SAMPLE_WEBHOOK_AUTH_CREDENTIALS=north:secret\n",
        encoding="utf-8",
    )

    env_path = run_demo._write_run_env(
        source=source,
        language="en-US",
        model_mode="live",
        discovery="nms",
        transport="webhook",
        webhook_port=9000,
        server_host="127.0.0.1",
        server_port=8000,
        ui_port=8080,
    )

    values = dotenv_values(env_path)
    assert Path(str(values["A2AT_SAMPLE_WEBHOOK_TLS_CERT"])) == run_demo.CASE_DIR / "Orange" / "server.cer"
    assert Path(str(values["A2AT_SAMPLE_WEBHOOK_TLS_KEY"])) == run_demo.CASE_DIR / "Orange" / "server_key_nopass.pem"
    assert values["A2AT_SAMPLE_WEBHOOK_PUBLIC_URL"] == "https://141.5.146.234:9000/webhook"
    assert values["A2AT_SAMPLE_WEBHOOK_AUTH_CREDENTIALS"] == "north:secret"


def test_write_run_env_carries_the_langfuse_keys(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """The client exports to Langfuse from the per-run .env, so the source keys must survive."""
    monkeypatch.setattr(run_demo, "RUN_DIR", tmp_path)
    source = tmp_path / "source.env"
    source.write_text(
        "LANGFUSE_PUBLIC_KEY=pk-lf-test\nLANGFUSE_SECRET_KEY=sk-lf-test\nLANGFUSE_BASE_URL=http://localhost:18080\n",
        encoding="utf-8",
    )

    env_path = run_demo._write_run_env(
        source=source,
        language="en-US",
        model_mode="live",
        discovery="nms",
        transport="webhook",
        webhook_port=9000,
        server_host="127.0.0.1",
        server_port=8000,
        ui_port=8080,
    )

    values = dotenv_values(env_path)
    assert values["LANGFUSE_PUBLIC_KEY"] == "pk-lf-test"
    assert values["LANGFUSE_SECRET_KEY"] == "sk-lf-test"
    assert values["LANGFUSE_BASE_URL"] == "http://localhost:18080"


def test_write_run_env_lets_the_shell_override_the_webhook_callback(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A second instance registers its own callback instead of the shared one in the source .env."""
    monkeypatch.setattr(run_demo, "RUN_DIR", tmp_path)
    monkeypatch.setenv("A2AT_SAMPLE_WEBHOOK_PUBLIC_URL", "https://example.test:9001/webhook")
    source = tmp_path / "source.env"
    source.write_text(
        "A2AT_SAMPLE_WEBHOOK_PUBLIC_URL=https://example.test:9000/webhook\n",
        encoding="utf-8",
    )

    env_path = run_demo._write_run_env(
        source=source,
        language="en-US",
        model_mode="live",
        discovery="nms",
        transport="webhook",
        webhook_port=9000,
        server_host="127.0.0.1",
        server_port=8000,
        ui_port=8080,
    )

    assert dotenv_values(env_path)["A2AT_SAMPLE_WEBHOOK_PUBLIC_URL"] == ("https://example.test:9001/webhook")


def test_write_run_env_lets_the_shell_override_the_model_keys(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A launcher script exporting the lab model must not be shadowed by the source .env."""
    monkeypatch.setattr(run_demo, "RUN_DIR", tmp_path)
    monkeypatch.setenv("ADK_MODEL_NAME", "openai/qwen3-32b")
    monkeypatch.setenv("ADK_MODEL_API_BASE", "http://71.77.65.40:8008/v1")
    monkeypatch.setenv("ADK_MODEL_API_KEY", "lab-key")
    monkeypatch.setenv("A2AT_LLM_MODEL", "qwen3-32b")
    monkeypatch.setenv("A2AT_LLM_BASE_URL", "http://71.77.65.40:8008/v1")
    source = tmp_path / "source.env"
    source.write_text(
        "ADK_MODEL_NAME=openai/deepseek-from-file\n"
        "ADK_MODEL_API_BASE=http://127.0.0.1:31943/v1\n"
        "A2AT_LLM_MODEL=deepseek-from-file\n"
        "A2AT_LLM_BASE_URL=http://127.0.0.1:31943/v1\n",
        encoding="utf-8",
    )

    env_path = run_demo._write_run_env(
        source=source,
        language="en-US",
        model_mode="live",
        discovery="nms",
        transport="webhook",
        webhook_port=9000,
        server_host="127.0.0.1",
        server_port=8000,
        ui_port=8080,
    )

    values = dotenv_values(env_path)
    assert values["ADK_MODEL_NAME"] == "openai/qwen3-32b"
    assert values["ADK_MODEL_API_BASE"] == "http://71.77.65.40:8008/v1"
    assert values["ADK_MODEL_API_KEY"] == "lab-key"
    assert values["A2AT_LLM_MODEL"] == "qwen3-32b"
    assert values["A2AT_LLM_BASE_URL"] == "http://71.77.65.40:8008/v1"


def test_env_file_argument_selects_one_configuration(tmp_path: Path) -> None:
    """Launch scripts select their profile explicitly instead of relying on the launcher's guess."""
    path = tmp_path / "yellow.env"
    path.write_text("A2AT_LANGUAGE=en-US\n", encoding="utf-8")

    assert run_demo._env_file_argument(["--env-file", str(path)]) == str(path)
    assert run_demo._env_file_argument([f"--env-file={path}"]) == str(path)

    selected, error = run_demo._resolve_source_env(str(path))
    assert selected == path
    assert error is None


def test_a_missing_env_file_is_reported(tmp_path: Path) -> None:
    selected, error = run_demo._resolve_source_env(str(tmp_path / "missing.env"))

    assert selected is None
    assert error is not None
    assert "not found" in error


def test_the_parser_exposes_the_env_file_option() -> None:
    args = run_demo._build_parser().parse_args(["--env-file", "profile.env"])

    assert args.env_file == "profile.env"


def test_observability_keys_from_the_selected_file_reach_child_processes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The SDK exporter reads os.getenv, so a key only in --env-file must be exported."""
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    monkeypatch.setenv("OTEL_SERVICE_NAME", "from-shell")

    run_demo._export_observability_env(
        {
            "OTEL_EXPORTER_OTLP_ENDPOINT": "http://langfuse.local/api/public/otel",
            "OTEL_SERVICE_NAME": "from-file",
            "LANGFUSE_PUBLIC_KEY": "pk-lf-test",
            "A2AT_SAMPLE_RUNS_DIR": "runs",
        }
    )

    import os

    assert os.environ["OTEL_EXPORTER_OTLP_ENDPOINT"] == "http://langfuse.local/api/public/otel"
    # A shell export wins over the file value.
    assert os.environ["OTEL_SERVICE_NAME"] == "from-shell"
    assert os.environ["LANGFUSE_PUBLIC_KEY"] == "pk-lf-test"
    # Non-observability sample settings stay in the per-run .env, not the launcher environment.
    assert "A2AT_SAMPLE_RUNS_DIR" not in os.environ


class _FakeChild:
    """Minimal ``Popen`` stand-in that records the env of every started child."""

    started: list[dict[str, object]] = []

    def __init__(self, command: list[str], **kwargs: Any) -> None:
        self.command = command
        self.pid = 5000 + len(_FakeChild.started)
        self.returncode = 0
        self.stdout = io.StringIO("")
        _FakeChild.started.append({"command": command, "env": dict(kwargs.get("env") or {})})

    def poll(self) -> int:
        return 0


def _resolved_config() -> SimpleNamespace:
    """Return the config fields printed and used by the launcher after ``load_config``."""
    return SimpleNamespace(
        model_mode="live",
        model_label="test-model",
        language="zh-CN",
        adk_model_provider="litellm",
        discovery="nms",
        transport="webhook",
        pure_hop="oss",
        pure_message_mode="send",
        intent_mode="processed",
        nms_region="A2ATestRegion",
        nms_sites=["site-a"],
        nms_a2a_base="https://nms.example/a2a/json",
    )


def _launch_both_nms(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    source_lines: list[str],
    configure: Callable[[pytest.MonkeyPatch], None] | None = None,
    extra_args: list[str] | None = None,
) -> tuple[int, list[dict[str, object]], list[int]]:
    """Run ``main`` for ``--protocol both --nms`` with every child process stubbed out."""
    env_file = tmp_path / "source.env"
    env_file.write_text("\n".join(source_lines), encoding="utf-8")
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    warmups: list[int] = []
    _FakeChild.started = []

    monkeypatch.setattr(run_demo, "RUN_DIR", run_dir)
    monkeypatch.setattr(run_demo, "PID_FILE", run_dir / "pids.json")
    monkeypatch.setattr(sys, "argv", [
        "run_demo.py",
        "--env-file",
        str(env_file),
        "--protocol",
        "both",
        "--nms",
        "--transport",
        "webhook",
        "--ui",
        "--ui-port",
        "18080",
        *(extra_args or []),
    ])
    monkeypatch.setattr(run_demo, "_preflight", lambda *_: True)
    monkeypatch.setattr(run_demo, "_warm_live_model", lambda _config: warmups.append(1))
    monkeypatch.setattr(run_demo, "_port_open", lambda *a, **k: False)
    monkeypatch.setattr(run_demo, "_wait_port", lambda *a, **k: True)
    monkeypatch.setattr(run_demo, "_pid_alive", lambda _pid: True)
    monkeypatch.setattr(run_demo, "_stop_pids", lambda pids: len(pids))
    monkeypatch.setattr(run_demo.subprocess, "Popen", _FakeChild)
    monkeypatch.setattr("shared.config.load_config", lambda **_: _resolved_config())
    if configure is not None:
        configure(monkeypatch)

    code = run_demo.main()
    return code, _FakeChild.started, warmups


def _both_nms_source(*extra: str, oss_port: int = 9001) -> list[str]:
    del oss_port
    return [
        "A2AT_SAMPLE_WEBHOOK_PORT=9000",
        "A2AT_SAMPLE_WEBHOOK_PUBLIC_URL=https://nms.example:9000/webhook",
        *extra,
    ]


def test_pure_hop_nms_skips_the_oss_and_points_the_ui_at_the_nms(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A direct standard-A2A hop calls the vendor itself; no OSS Agent process is needed."""

    def configure(mp: pytest.MonkeyPatch) -> None:
        direct = _resolved_config()
        direct.pure_hop = "nms"
        mp.setattr("shared.config.load_config", lambda **_: direct)

    code, started, _ = _launch_both_nms(
        monkeypatch,
        tmp_path,
        _both_nms_source(),
        configure=configure,
    )

    assert code == 0
    modules = [str(call["command"][2]) for call in started]
    assert "protocol.oss_agent" not in modules
    ui = started[modules.index("demo.web.serve")]["env"]
    assert ui["A2AT_SAMPLE_DISCOVERY"] == "nms"
    assert ui["A2AT_SAMPLE_PURE_HOP"] == "nms"
    assert ui["A2AT_SAMPLE_DIRECT_SERVER_URL"] == "https://nms.example/a2a/json"
    assert "--discovery" not in started[modules.index("demo.web.serve")]["command"]


def test_both_nms_defaults_to_the_direct_hop_without_the_oss_callback(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The deployment default is the NMS hop: no OSS process, no OSS callback requirement."""

    def configure(mp: pytest.MonkeyPatch) -> None:
        direct = _resolved_config()
        direct.pure_hop = "nms"
        mp.setattr("shared.config.load_config", lambda **_: direct)

    source = [
        "A2AT_SAMPLE_WEBHOOK_PORT=9000",
        "A2AT_SAMPLE_WEBHOOK_PUBLIC_URL=https://nms.example:9000/webhook",
    ]

    code, started, _ = _launch_both_nms(
        monkeypatch, tmp_path, source, configure=configure
    )

    assert code == 0
    modules = [str(call["command"][2]) for call in started]
    assert "protocol.oss_agent" not in modules
    ui = started[modules.index("demo.web.serve")]["env"]
    assert ui["A2AT_SAMPLE_PURE_HOP"] == "nms"
    assert ui["A2AT_SAMPLE_DIRECT_SERVER_URL"] == "https://nms.example/a2a/json"


def test_local_both_defaults_to_the_direct_ran_hop(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A local ``--protocol both`` deployment keeps the UI as the only agent layer."""
    env_file = tmp_path / "source.env"
    env_file.write_text("A2AT_SAMPLE_WEBHOOK_PORT=9000\n", encoding="utf-8")
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    _FakeChild.started = []

    def configure(mp: pytest.MonkeyPatch) -> None:
        local = _resolved_config()
        local.discovery = "direct"
        local.transport = "sse"
        local.pure_hop = "direct"
        local.nms_region = None
        local.nms_sites = []
        local.nms_a2a_base = ""
        mp.setattr("shared.config.load_config", lambda **_: local)

    monkeypatch.setattr(run_demo, "RUN_DIR", run_dir)
    monkeypatch.setattr(run_demo, "PID_FILE", run_dir / "pids.json")
    monkeypatch.setattr(sys, "argv", [
        "run_demo.py",
        "--env-file",
        str(env_file),
        "--protocol",
        "both",
        "--ui",
        "--ui-port",
        "18080",
    ])
    monkeypatch.setattr(run_demo, "_preflight", lambda *_: True)
    monkeypatch.setattr(run_demo, "_warm_live_model", lambda _config: None)
    monkeypatch.setattr(run_demo, "_port_open", lambda *a, **k: False)
    monkeypatch.setattr(run_demo, "_wait_port", lambda *a, **k: True)
    monkeypatch.setattr(run_demo, "_pid_alive", lambda _pid: True)
    monkeypatch.setattr(run_demo, "_stop_pids", lambda pids: len(pids))
    monkeypatch.setattr(run_demo.subprocess, "Popen", _FakeChild)
    configure(monkeypatch)

    code = run_demo.main()

    assert code == 0
    modules = [str(call["command"][2]) for call in _FakeChild.started]
    assert "protocol.oss_agent" not in modules
    assert "protocol.server_app" in modules
    ui = _FakeChild.started[modules.index("demo.web.serve")]["env"]
    assert ui["A2AT_SAMPLE_PURE_HOP"] == "direct"
    assert ui["A2AT_SAMPLE_DIRECT_SERVER_URL"] == "http://127.0.0.1:8000"


def test_both_nms_passes_the_pure_message_mode_override_to_the_ui(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A deployment can pin the standard-A2A method; the UI child inherits the choice."""
    monkeypatch.delenv("A2AT_SAMPLE_PURE_MESSAGE_MODE", raising=False)

    code, started, _ = _launch_both_nms(
        monkeypatch,
        tmp_path,
        _both_nms_source(),
        extra_args=["--pure-message-mode", "stream"],
    )

    assert code == 0
    modules = [str(call["command"][2]) for call in started]
    ui = started[modules.index("demo.web.serve")]["env"]
    assert ui["A2AT_SAMPLE_PURE_MESSAGE_MODE"] == "stream"


def test_a_real_nms_run_still_requires_the_webhook_transport(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sys, "argv", ["run_demo.py", "--nms", "--transport", "sse"])
    with pytest.raises(SystemExit) as error:
        run_demo.main()
    assert error.value.code == 2


def test_child_env_pins_the_launcher_run_dir(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The web process must read the same launcher log directory the console indexes."""
    monkeypatch.setattr(run_demo, "RUN_DIR", tmp_path / "state")
    monkeypatch.setenv("A2AT_SAMPLE_RUN_DIR", "/inherited/elsewhere")

    env = run_demo._child_env("en-US")

    assert env["A2AT_SAMPLE_RUN_DIR"] == str(tmp_path / "state")


def test_real_nms_can_start_the_inbound_gateway(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Gemini Enterprise -> gateway -> vendor NMS is one supported launcher topology."""
    monkeypatch.setenv("A2AT_SAMPLE_GATEWAY_PUBLIC_URL", "https://ran-client.example/gateway")

    code, started, _ = _launch_both_nms(
        monkeypatch, tmp_path, _both_nms_source(), extra_args=["--gateway"]
    )

    assert code == 0
    modules = [str(call["command"][2]) for call in started]
    assert "protocol.client_gateway" in modules
    gateway = started[modules.index("protocol.client_gateway")]["env"]
    assert gateway["A2AT_SAMPLE_GATEWAY_PUBLIC_URL"] == "https://ran-client.example/gateway"
    assert gateway["A2AT_SAMPLE_WEBHOOK_PORT"] == "9001"


def test_real_nms_gateway_requires_a_public_callback_url(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("A2AT_SAMPLE_GATEWAY_PUBLIC_URL", raising=False)

    code, started, _ = _launch_both_nms(
        monkeypatch, tmp_path, _both_nms_source(), extra_args=["--gateway"]
    )

    assert code == 2
    assert started == []
