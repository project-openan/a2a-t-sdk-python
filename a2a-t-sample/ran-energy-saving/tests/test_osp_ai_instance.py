"""Unit tests for :mod:`eval.osp.ai.instance`.

Every test is deterministic: the launcher is faked, the UI probe is faked, the NMS is faked and no
real port, process or network call happens. The tests pin the two contracts the CLI relies on:

* the launcher receives bind ports as arguments and webhook/model keys as environment;
* ``down`` cancels only the NMS Tasks this instance can prove it created.
"""

from __future__ import annotations

import json
import sqlite3
import subprocess
from pathlib import Path
from typing import Callable

import pytest
from eval.osp.ai import instance
from eval.osp.ai import profile as profile_module
from eval.osp.ai.http import HttpError
from eval.osp.ai.profile import launcher_argv, sanitized_env
from eval.osp.ai.types import Profile


def _profile(tmp_path: Path, **overrides: object) -> Profile:
    """Return a valid real-NMS profile rooted inside ``tmp_path``."""
    values: dict[str, object] = {
        "name": "osp-ai",
        "sample_root": tmp_path / "sample",
        "source_path": None,
        "run_dir": tmp_path / "launcher",
        "runs_dir": tmp_path / "runs-ai",
        "ui_host": "127.0.0.1",
        "ui_port": 18080,
        "oss_port": 18081,
        "mie_port": 18082,
        "webhook_host": "127.0.0.1",
        "webhook_port": 19000,
        "oss_webhook_port": 19001,
        "webhook_public_url": "https://yellow.example:19000/webhook",
        "oss_webhook_public_url": "https://yellow.example:19001/webhook",
        "language": "en-US",
        "model_mode": "live",
        "protocol": "both",
        "decision_mode": "live",
        "approval": "auto",
        "owner": "osp-ai",
        "nms": True,
        "nms_sim": False,
        "nms_sim_port": 18083,
        "env": {
            "A2AT_SAMPLE_NMS_A2A_BASE": "https://nms.example/a2a",
            "A2AT_SAMPLE_NMS_TOKEN_URL": "https://nms.example/token",
            "A2AT_SAMPLE_NMS_TOKEN_USER": "user",
            "A2AT_SAMPLE_NMS_TOKEN_VALUE": "secret",
        },
    }
    values.update(overrides)
    return Profile(**values)  # type: ignore[arg-type]


def _install_popen(monkeypatch: pytest.MonkeyPatch) -> list[tuple[list[str], dict[str, object]]]:
    """Replace ``subprocess.Popen`` and return the captured ``(argv, kwargs)`` calls."""
    calls: list[tuple[list[str], dict[str, object]]] = []

    class FakePopen:
        def __init__(self, argv: list[str], **kwargs: object) -> None:
            self.pid = 4242
            calls.append((list(argv), kwargs))

    monkeypatch.setattr(instance.subprocess, "Popen", FakePopen)
    return calls


def _install_run(
    monkeypatch: pytest.MonkeyPatch, *, returncode: int = 0, stdout: str = "", stderr: str = ""
) -> list[tuple[list[str], dict[str, object]]]:
    """Replace ``subprocess.run`` and return the captured ``(argv, kwargs)`` calls."""
    calls: list[tuple[list[str], dict[str, object]]] = []

    def fake_run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append((list(argv), kwargs))
        return subprocess.CompletedProcess(argv, returncode, stdout, stderr)

    monkeypatch.setattr(instance.subprocess, "run", fake_run)
    return calls


def _install_client(
    monkeypatch: pytest.MonkeyPatch, handler: Callable[[str], tuple[int, object]]
) -> list[str]:
    """Replace the module's ``JsonClient`` with one that answers through ``handler``."""
    calls: list[str] = []

    class FakeClient:
        def __init__(self, base_url: str, *, cookie: object = None, timeout: float = 15.0) -> None:
            self.base_url = base_url
            self.timeout = timeout

        def get_json(self, path: str) -> tuple[int, object]:
            calls.append(path)
            return handler(path)

    monkeypatch.setattr(instance, "JsonClient", FakeClient)
    return calls


def _allow_ports(monkeypatch: pytest.MonkeyPatch) -> None:
    """Report every port free so ``up`` passes the pre-flight check."""
    monkeypatch.setattr(profile_module, "port_in_use", lambda port, host="127.0.0.1": False)


def _write_pids(profile: Profile, pids: list[object]) -> None:
    """Write a launcher state file with the given raw PID list."""
    profile.run_dir.mkdir(parents=True, exist_ok=True)
    (profile.run_dir / "pids.json").write_text(json.dumps({"pids": pids}), encoding="utf-8")


def _write_marker(profile: Profile, owner: str) -> None:
    """Write an ownership marker directly."""
    profile.run_dir.mkdir(parents=True, exist_ok=True)
    (profile.run_dir / "ai-profile.json").write_text(
        json.dumps({"schema": instance.MARKER_SCHEMA, "owner": owner}),
        encoding="utf-8",
    )


def _create_task_snapshots(profile: Profile, task_ids: list[str]) -> Path:
    """Create a client store with the given ``task_snapshots`` rows."""
    profile.runs_dir.mkdir(parents=True, exist_ok=True)
    database = profile.runs_dir / "client-state.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute(
            "CREATE TABLE task_snapshots (run_id TEXT PRIMARY KEY, task_id TEXT, context_id TEXT, "
            "state TEXT, updated_at TEXT NOT NULL, payload_json TEXT NOT NULL DEFAULT '{}')"
        )
        for index, task_id in enumerate(task_ids):
            connection.execute(
                "INSERT INTO task_snapshots (run_id, task_id, context_id, state, updated_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (f"run-{index}", task_id, f"ctx-{index}", "WORKING", "2026-11-10T00:00:00Z"),
            )
        connection.commit()
    return database


def test_up_spawns_launcher_and_returns_ready_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    profile = _profile(tmp_path)
    monkeypatch.setenv("A2AT_SAMPLE_UI_PORT", "9999")
    monkeypatch.setenv("A2AT_SAMPLE_WEBHOOK_PORT", "1111")
    _allow_ports(monkeypatch)
    popen_calls = _install_popen(monkeypatch)
    client_calls = _install_client(monkeypatch, lambda path: (200, {"language": "en-US"}))
    messages: list[str] = []

    state = instance.up(profile, wait_seconds=5.0, log=messages.append)

    assert state.running is True
    assert state.ui_ready is True
    assert state.pids == ()
    assert state.detail == ""
    assert client_calls == ["/api/meta"]
    assert messages  # progress is reported

    argv, kwargs = popen_calls[0]
    assert argv == launcher_argv(profile, action="up")
    assert kwargs["cwd"] == profile.sample_root
    env = kwargs["env"]
    assert isinstance(env, dict)
    # Bind ports are arguments, not inherited environment keys.
    assert "--ui-port" in argv and argv[argv.index("--ui-port") + 1] == "18080"
    assert "--oss-port" in argv and argv[argv.index("--oss-port") + 1] == "18081"
    assert "--mie-port" in argv and argv[argv.index("--mie-port") + 1] == "18082"
    assert "--webhook-port" not in argv
    assert "A2AT_SAMPLE_UI_PORT" not in env
    # Webhook receiver keys come from the profile, never from the parent shell.
    assert env["A2AT_SAMPLE_WEBHOOK_PORT"] == "19000"
    assert env["A2AT_SAMPLE_OSS_WEBHOOK_PORT"] == "19001"
    assert env["A2AT_SAMPLE_WEBHOOK_PUBLIC_URL"] == profile.webhook_public_url

    marker = json.loads((profile.run_dir / "ai-profile.json").read_text(encoding="utf-8"))
    assert marker == {
        "schema": "osp-ai-instance/v1",
        "owner": "osp-ai",
        "uiPort": 18080,
        "runsDir": str(profile.runs_dir),
        "profile": "osp-ai",
    }
    assert (profile.run_dir / "ai-launcher.log").is_file()


def test_up_rejects_port_conflicts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    profile = _profile(tmp_path)
    monkeypatch.setattr(
        profile_module,
        "port_in_use",
        lambda port, host="127.0.0.1": port == profile.ui_port,
    )
    popen_calls = _install_popen(monkeypatch)

    with pytest.raises(instance.InstanceError, match="port conflict"):
        instance.up(profile)

    assert popen_calls == []


def test_up_timeout_includes_launcher_log_tail(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    profile = _profile(tmp_path)
    profile.run_dir.mkdir(parents=True, exist_ok=True)
    log_path = profile.run_dir / "ai-launcher.log"
    log_path.write_text(
        "\n".join(f"launcher-line-{index}" for index in range(60)) + "\n", encoding="utf-8"
    )
    _allow_ports(monkeypatch)
    _install_popen(monkeypatch)

    def fail(path: str) -> tuple[int, object]:
        raise HttpError("connection refused")

    _install_client(monkeypatch, fail)

    with pytest.raises(instance.InstanceError) as info:
        instance.up(profile, wait_seconds=0.0)

    message = str(info.value)
    assert "did not become ready" in message
    assert "launcher-line-59" in message
    assert "launcher-line-19" not in message


def test_status_without_marker_reports_dead_pids_and_logs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    profile = _profile(tmp_path)
    _write_pids(profile, [111, "not-a-pid", True])
    (profile.run_dir / "ui.log").write_text("ui log", encoding="utf-8")
    monkeypatch.setattr(instance, "_pid_alive", lambda pid: False)

    def fail(path: str) -> tuple[int, object]:
        raise HttpError("connection refused")

    _install_client(monkeypatch, fail)

    state = instance.status(profile)

    assert state.running is False
    assert state.ui_ready is False
    assert state.pids == ()
    assert state.detail == ""
    assert state.logs == (profile.run_dir / "ui.log",)
    assert state.to_dict() == {
        "running": False,
        "uiReady": False,
        "pids": [],
        "logs": [str(profile.run_dir / "ui.log")],
        "detail": "",
    }


def test_status_refuses_a_foreign_marker_owner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    profile = _profile(tmp_path)
    _write_marker(profile, "manual-tester")
    _write_pids(profile, [111])
    monkeypatch.setattr(instance, "_pid_alive", lambda pid: True)
    client_calls = _install_client(monkeypatch, lambda path: (200, {}))

    state = instance.status(profile)

    assert state.running is False
    assert state.ui_ready is False
    assert state.pids == ()
    assert "manual-tester" in state.detail
    assert client_calls == []


def test_down_cancels_only_locally_known_tasks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    profile = _profile(tmp_path)
    _create_task_snapshots(profile, ["local-1", "local-2"])
    first: list[dict[str, object]] = [
        {"taskId": "local-1", "state": "TASK_STATE_WORKING", "contextId": "ctx-1"},
        {"taskId": "local-2", "state": "TASK_STATE_SUBMITTED", "contextId": "ctx-2"},
        {"taskId": "manual-9", "state": "TASK_STATE_WORKING", "contextId": "manual-ctx"},
    ]
    after_cancel: list[dict[str, object]] = [
        {"taskId": "manual-9", "state": "TASK_STATE_WORKING"}
    ]
    active_calls: list[int] = []

    def fake_active(profile_arg: Profile) -> list[dict[str, object]]:
        active_calls.append(1)
        return first if len(active_calls) == 1 else after_cancel

    cancelled: list[str] = []
    monkeypatch.setattr(instance.nms, "active_tasks", fake_active)
    monkeypatch.setattr(
        instance.nms,
        "cancel_task",
        lambda profile_arg, task_id: cancelled.append(task_id) or True,
    )
    run_calls = _install_run(monkeypatch, returncode=0, stdout="stopped 2")
    monkeypatch.setattr(instance, "port_in_use", lambda port, host="127.0.0.1": False)

    result = instance.down(profile, recheck_delays=(0.0,))

    assert cancelled == ["local-1", "local-2"]
    assert len(active_calls) == 2
    tasks = result["tasks"]
    assert isinstance(tasks, list)
    assert {record["taskId"] for record in tasks} == {"local-1", "local-2"}
    assert all(record["cleared"] is True for record in tasks)
    unknown = result["unknownActive"]
    assert isinstance(unknown, list)
    assert [row["taskId"] for row in unknown] == ["manual-9"]
    assert result["stopped"] is True
    assert result["portsClosed"] is True
    assert result["errors"] == []
    argv = run_calls[0][0]
    assert "--stop" in argv


def test_down_attributes_a_pure_a2a_inner_task_from_the_oss_binding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Pure A2A keeps the vendor Task on the OSS binding, not on ``task_snapshots``."""
    profile = _profile(tmp_path)
    profile.runs_dir.mkdir(parents=True, exist_ok=True)
    database = profile.runs_dir / "oss-task-status.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute(
            "CREATE TABLE downstream_bindings (inner_task_id TEXT PRIMARY KEY, "
            "inner_context_id TEXT, outer_task_id TEXT)"
        )
        connection.execute(
            "INSERT INTO downstream_bindings (inner_task_id, inner_context_id, outer_task_id) "
            "VALUES ('inner-1', 'ctx-1', 'outer-1')"
        )
        connection.commit()
    monkeypatch.setattr(
        instance.nms,
        "active_tasks",
        lambda profile_arg: [{"taskId": "inner-1", "state": "TASK_STATE_WORKING"}],
    )
    cancelled: list[str] = []
    monkeypatch.setattr(
        instance.nms,
        "cancel_task",
        lambda profile_arg, task_id: cancelled.append(task_id) or True,
    )
    _install_run(monkeypatch, returncode=0, stdout="stopped")
    monkeypatch.setattr(instance, "port_in_use", lambda port, host="127.0.0.1": False)

    result = instance.down(profile, recheck_delays=(0.0,))

    assert cancelled == ["inner-1"]
    assert result["unknownActive"] == []


def test_down_never_cancels_an_active_task_with_no_local_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    profile = _profile(tmp_path)
    # No client-state.sqlite3 at all: nothing can be attributed, so nothing may be cancelled.
    monkeypatch.setattr(
        instance.nms,
        "active_tasks",
        lambda profile_arg: [{"taskId": "manual-9", "state": "TASK_STATE_WORKING"}],
    )
    cancelled: list[str] = []
    monkeypatch.setattr(
        instance.nms,
        "cancel_task",
        lambda profile_arg, task_id: cancelled.append(task_id) or True,
    )
    _install_run(monkeypatch, returncode=0)
    monkeypatch.setattr(instance, "port_in_use", lambda port, host="127.0.0.1": False)

    result = instance.down(profile, recheck_delays=(0.0,))

    assert cancelled == []
    assert result["tasks"] == []
    unknown = result["unknownActive"]
    assert isinstance(unknown, list)
    assert [row["taskId"] for row in unknown] == ["manual-9"]
    errors = result["errors"]
    assert isinstance(errors, list)
    assert any("client-state.sqlite3 not found" in str(error) for error in errors)
    assert result["stopped"] is True


def test_down_refuses_a_foreign_marker_owner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    profile = _profile(tmp_path)
    _write_marker(profile, "manual-tester")
    monkeypatch.setattr(
        instance.nms,
        "active_tasks",
        lambda profile_arg: pytest.fail("must not touch the NMS for a foreign instance"),
    )
    run_calls = _install_run(monkeypatch, returncode=0)

    result = instance.down(profile)

    assert result["stopped"] is False
    assert result["portsClosed"] is False
    assert run_calls == []
    errors = result["errors"]
    assert isinstance(errors, list)
    assert any("manual-tester" in str(error) for error in errors)


def test_launcher_ports_are_arguments_and_webhooks_are_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    profile = _profile(tmp_path)
    monkeypatch.setenv("A2AT_SAMPLE_UI_PORT", "9999")
    monkeypatch.setenv("A2AT_SAMPLE_OSS_WEBHOOK_PORT", "1111")

    argv = launcher_argv(profile, action="up")
    for flag, port in (
        ("--ui-port", profile.ui_port),
        ("--oss-port", profile.oss_port),
        ("--mie-port", profile.mie_port),
    ):
        assert flag in argv
        assert argv[argv.index(flag) + 1] == str(port)
    assert "--webhook-port" not in argv
    assert "--oss-webhook-port" not in argv

    env = sanitized_env(profile)
    assert "A2AT_SAMPLE_UI_PORT" not in env
    assert env["A2AT_SAMPLE_WEBHOOK_PORT"] == "19000"
    assert env["A2AT_SAMPLE_OSS_WEBHOOK_PORT"] == "19001"
    assert env["A2AT_SAMPLE_WEBHOOK_PUBLIC_URL"] == profile.webhook_public_url
    assert env["A2AT_SAMPLE_OSS_WEBHOOK_PUBLIC_URL"] == profile.oss_webhook_public_url
