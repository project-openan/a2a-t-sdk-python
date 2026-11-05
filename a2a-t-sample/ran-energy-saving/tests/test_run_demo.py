"""Tests for the launcher defaults and PID reuse decisions of ``run_demo.py``."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import demo.run_demo as run_demo
import pytest
from dotenv import dotenv_values
from protocol.approval_gate import DEFAULT_APPROVAL_TIMEOUT_SECONDS


def test_write_run_env_points_both_processes_at_the_task_database(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The server writes and the UI reads the same absolute SQLite task store."""
    monkeypatch.setattr(run_demo, "RUN_DIR", tmp_path)
    env_path = run_demo._write_run_env(
        source=None,
        language="en-US",
        model_mode="offline",
        discovery="direct",
        transport="sse",
        webhook_port=0,
        server_host="127.0.0.1",
        server_port=8000,
        registry_host="127.0.0.1",
        registry_port=5001,
        ui_port=8080,
        gateway_port=8001,
        gateway_register=True,
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
    assert values["A2AT_SAMPLE_GATEWAY_REGISTER"] == "1"
    assert Path(str(values["A2AT_SAMPLE_RUNS_DIR"])) == run_demo.RUNS_DIR


def test_write_run_env_keeps_nms_discovery_for_pure_nms(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The OSS Agent reads the same .env; pure + NMS must keep nms discovery for it."""
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
        registry_host="127.0.0.1",
        registry_port=5001,
        ui_port=8081,
        protocol="pure",
        oss_port=8011,
    )

    values = dotenv_values(env_path)
    assert values["A2AT_SAMPLE_DISCOVERY"] == "nms"
    assert values["A2AT_SAMPLE_TRANSPORT"] == "webhook"
    assert values["A2AT_SAMPLE_DIRECT_SERVER_URL"] == "http://127.0.0.1:8011"
    assert values["A2AT_SAMPLE_OSS_PORT"] == "8011"


def test_run_state_and_data_directories_are_configurable(tmp_path: Path) -> None:
    """Two launchers can use isolated pids/logs and isolated runs/sqlite directories."""
    env = dict(os.environ)
    env["A2AT_SAMPLE_RUN_DIR"] = str(tmp_path / "state")
    env["A2AT_SAMPLE_RUNS_DIR"] = str(tmp_path / "data")
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1])
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


def test_write_run_env_applies_the_nms_simulator_overrides(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """`--nms-sim` points the per-run .env at the local simulator."""
    monkeypatch.setattr(run_demo, "RUN_DIR", tmp_path)
    env_path = run_demo._write_run_env(
        source=None,
        language="en-US",
        model_mode="offline",
        discovery="nms",
        transport="sse",
        webhook_port=0,
        server_host="127.0.0.1",
        server_port=8000,
        registry_host="127.0.0.1",
        registry_port=5001,
        ui_port=8080,
        extra={
            "A2AT_SAMPLE_NMS_HOST": "127.0.0.1",
            "A2AT_SAMPLE_NMS_SCHEME": "http",
            "A2AT_SAMPLE_NMS_A2A_PORT": "18080",
            "A2AT_SAMPLE_NMS_TOKEN_PORT": "18080",
            "A2AT_SAMPLE_NMS_TOKEN_USER": "snbitestDefaultNorth",
            "A2AT_SAMPLE_NMS_TOKEN_VALUE": "Changeme_123",
            "A2AT_SAMPLE_NMS_VERIFY_TLS": "false",
        },
    )

    values = dotenv_values(env_path)
    assert values["A2AT_SAMPLE_DISCOVERY"] == "nms"
    assert values["A2AT_SAMPLE_NMS_HOST"] == "127.0.0.1"
    assert values["A2AT_SAMPLE_NMS_A2A_PORT"] == "18080"
    assert values["A2AT_SAMPLE_NMS_TOKEN_USER"] == "snbitestDefaultNorth"
    assert values["A2AT_SAMPLE_NMS_VERIFY_TLS"] == "false"


def test_parser_exposes_the_nms_flags() -> None:
    parser = run_demo._build_parser(
        default_ui_port=8080,
        default_ui_host="127.0.0.1",
        default_gateway_port=8001,
    )

    args = parser.parse_args(["--nms", "--nms-sim", "--nms-sim-port", "18090"])

    assert args.nms is True
    assert args.nms_sim is True
    assert args.nms_sim_port == 18090


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
        model_mode="offline",
        language="en-US",
        trace_token="tok",
        trace_url="http://127.0.0.1:8080",
        ui_port=8080,
    )

    state = run_demo._load_state()
    assert state == {
        "pids": [11, 12],
        "model_mode": "offline",
        "language": "en-US",
        "trace_token": "tok",
        "trace_url": "http://127.0.0.1:8080",
        "ui_port": 8080,
        "gateway_port": None,
        "approval_gate": False,
        "approval_timeout": DEFAULT_APPROVAL_TIMEOUT_SECONDS,
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

    run_demo._save_state([1], model_mode="offline", language="en-US", trace_token="tok")

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
        "model_mode": "offline",
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
            "model_mode": "offline",
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

    def test_model_mode_mismatch_restarts(self) -> None:
        """The offline-UI/live-server bug: the recorded live server must not be reused."""
        assert self.decide(state={**self.STATE, "model_mode": "live"}) == run_demo.REUSE_RESTART

    def test_language_mismatch_restarts(self) -> None:
        assert self.decide(state={**self.STATE, "language": "zh-CN"}) == run_demo.REUSE_RESTART

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
        ("python -m demo.web.serve --port 8080", True),
        ("python -m demo.run_demo --ui --ui-port 8080", True),
        ("python run_demo.py --ui", True),
        ("python -m demo.run_demo --scenario night", False),
        ("nginx -g daemon off;", False),
        ("", False),
    ],
)
def test_is_demo_process_only_accepts_this_samples_ui(
    monkeypatch: pytest.MonkeyPatch, command: str, expected: bool
) -> None:
    """The port fallback of ``--stop`` must never kill an unrelated service."""
    monkeypatch.setattr(run_demo, "_process_command_line", lambda pid: command)

    assert run_demo._is_demo_process(1234) is expected


def test_is_ui_child_only_matches_the_managed_ui_process(monkeypatch: pytest.MonkeyPatch) -> None:
    """Restarting a stale UI must not kill an older launcher (it owns the server too)."""
    monkeypatch.setattr(run_demo, "_process_command_line", lambda pid: "python -m demo.web.serve")
    assert run_demo._is_ui_child(111) is True

    monkeypatch.setattr(
        run_demo,
        "_process_command_line",
        lambda pid: "python -m demo.run_demo --ui --keep-alive",
    )
    assert run_demo._is_ui_child(111) is False


def test_kill_port_listener_skips_unrelated_listeners(monkeypatch: pytest.MonkeyPatch) -> None:
    """A foreign listener on the recorded UI port is left alone."""
    killed: list[list[int]] = []
    monkeypatch.setattr(run_demo, "_listener_pids", lambda port: [111, 222])
    monkeypatch.setattr(run_demo, "_is_demo_process", lambda pid: False)
    monkeypatch.setattr(run_demo, "_stop_pids", lambda pids: killed.append(pids) or 0)

    assert run_demo._kill_port_listener(8080) == 0
    assert killed == [[]]


def test_kill_port_listener_stops_a_verified_ui(monkeypatch: pytest.MonkeyPatch) -> None:
    """An orphaned UI whose command line names this sample is reclaimed."""
    monkeypatch.setattr(run_demo, "_listener_pids", lambda port: [111, 222])
    monkeypatch.setattr(run_demo, "_is_demo_process", lambda pid: pid == 222)
    monkeypatch.setattr(run_demo, "_stop_pids", lambda pids: len(pids))

    assert run_demo._kill_port_listener(8080) == 1


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
            "--approval-gate",
        ]
    )

    assert args.env_file == tmp_path / ".env"
    assert args.port == 8080
    assert args.approval_gate is True


def test_trace_summary_reports_llm_and_tool_counts() -> None:
    """The CLI summary prints LLM/tool counts and never fabricates scripted tokens."""
    scripted: list[dict[str, object]] = [
        {"kind": "llm", "tokens": {"prompt": None, "completion": None, "total": None}},
        {"kind": "tool"},
        {"kind": "message_sent"},
    ]
    assert run_demo._trace_summary(scripted) == "llm=1 tool=1 tokens=n/a (scripted)"

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
        registry_host="127.0.0.1",
        registry_port=5001,
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
        "LANGFUSE_PUBLIC_KEY=pk-lf-test\n"
        "LANGFUSE_SECRET_KEY=sk-lf-test\n"
        "LANGFUSE_BASE_URL=http://localhost:18080\n",
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
        registry_host="127.0.0.1",
        registry_port=5001,
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
        registry_host="127.0.0.1",
        registry_port=5001,
        ui_port=8080,
    )

    assert dotenv_values(env_path)["A2AT_SAMPLE_WEBHOOK_PUBLIC_URL"] == (
        "https://example.test:9001/webhook"
    )


def test_write_run_env_lets_the_shell_override_the_model_keys(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
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
        registry_host="127.0.0.1",
        registry_port=5001,
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
