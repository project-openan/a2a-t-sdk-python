"""Tests for the OSP AI instance profile and its launcher translation."""

from __future__ import annotations

from pathlib import Path

from eval.osp.ai.paths import sample_root
from eval.osp.ai.profile import (
    launcher_argv,
    load_profile,
    manifest,
    port_conflicts,
    sanitized_env,
    validate_profile,
)
from eval.osp.ai.types import Profile


def _profile(tmp_path: Path, **overrides: object) -> Profile:
    """Return a minimal real-NMS profile for the tests."""
    values: dict[str, object] = {
        "name": "osp-ai",
        "sample_root": sample_root(),
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
        "webhook_public_url": "https://yellow:19000/webhook",
        "oss_webhook_public_url": "https://yellow:19001/webhook",
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
            "A2AT_SAMPLE_NMS_A2A_BASE": "https://nms:31127/a2a/json",
            "A2AT_SAMPLE_NMS_TOKEN_URL": "https://nms:27417/sessions",
            "A2AT_SAMPLE_NMS_TOKEN_USER": "user",
            "A2AT_SAMPLE_NMS_TOKEN_VALUE": "secret",
        },
    }
    values.update(overrides)
    return Profile(**values)  # type: ignore[arg-type]


def test_load_profile_resolves_paths_and_ports(tmp_path: Path) -> None:
    profile_file = tmp_path / "osp-ai.env"
    profile_file.write_text(
        "\n".join(
            [
                "A2AT_OSP_AI_RUN_DIR=launcher",
                "A2AT_OSP_AI_RUNS_DIR=runs-ai",
                "A2AT_OSP_AI_NMS=0",
                "A2AT_OSP_AI_UI_PORT=18090",
                "A2AT_OSP_AI_OSS_PORT=18091",
                "A2AT_OSP_AI_MIE_PORT=18092",
                "A2AT_OSP_AI_WEBHOOK_PORT=19090",
                "A2AT_OSP_AI_OSS_WEBHOOK_PORT=19091",
                "A2AT_LLM_MODEL=test-model",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    profile = load_profile(profile_file)
    assert profile.run_dir == (tmp_path / "launcher").resolve()
    assert profile.runs_dir == (sample_root() / "runs-ai").resolve()
    assert profile.ui_port == 18090
    assert profile.nms is False
    assert profile.env["A2AT_LLM_MODEL"] == "test-model"
    assert "A2AT_OSP_AI_UI_PORT" not in profile.env


def test_sanitized_env_drops_every_inherited_instance_key(tmp_path: Path) -> None:
    profile = _profile(tmp_path)
    base = {
        "PATH": "/bin",
        "A2AT_SAMPLE_WEBHOOK_PORT": "9000",
        "A2AT_SAMPLE_RUNS_DIR": "/manual/runs",
        "A2AT_SAMPLE_CLIENT_DB": "/manual/client.sqlite3",
        "A2AT_LLM_API_BASE": "http://manual",
        "ADK_MODEL_NAME": "manual",
        "GOOGLE_CLOUD_PROJECT": "manual",
        "REGISTRY_CENTER_PORT": "5001",
        "LANGFUSE_ENABLED": "1",
    }
    env = sanitized_env(profile, base)
    # The inherited values must be gone; the derived keys below are the profile's own.
    assert "A2AT_LLM_API_BASE" not in env
    assert "ADK_MODEL_NAME" not in env
    assert "GOOGLE_CLOUD_PROJECT" not in env
    assert "REGISTRY_CENTER_PORT" not in env
    assert "LANGFUSE_ENABLED" not in env
    assert env["PATH"] == "/bin"
    assert env["A2AT_SAMPLE_RUNS_DIR"] == str(profile.runs_dir)
    assert env["A2AT_SAMPLE_WEBHOOK_PORT"] == "19000"
    assert env["A2AT_SAMPLE_OSS_WEBHOOK_PORT"] == "19001"
    assert env["A2AT_SAMPLE_NMS_A2A_BASE"] == "https://nms:31127/a2a/json"
    assert env["A2AT_SAMPLE_CLIENT_DB"] == str(profile.runs_dir / "client-state.sqlite3")
    assert env["A2AT_SAMPLE_TASK_DB"] == str(profile.runs_dir / "task-status.sqlite3")


def test_launcher_argv_passes_ports_as_arguments_not_environment(tmp_path: Path) -> None:
    profile = _profile(tmp_path)
    argv = launcher_argv(profile)
    joined = " ".join(argv)
    assert "--ui-port 18080" in joined
    assert "--oss-port 18081" in joined
    assert "--mie-port 18082" in joined
    assert "--protocol both" in joined
    assert "--nms" in argv
    assert "--keep-alive" in argv
    assert "--nms-sim" not in argv
    env = sanitized_env(profile, {})
    assert "A2AT_SAMPLE_UI_PORT" not in env
    assert "A2AT_SAMPLE_OSS_PORT" not in env


def test_launcher_stop_only_targets_the_profile_state(tmp_path: Path) -> None:
    profile = _profile(tmp_path)
    argv = launcher_argv(profile, action="stop")
    assert argv[-1] == "--stop"
    assert "--ui-port" not in argv


def test_validate_profile_accepts_a_complete_real_nms_profile(tmp_path: Path) -> None:
    assert validate_profile(_profile(tmp_path)) == []


def test_validate_profile_rejects_the_manual_runs_directory(tmp_path: Path) -> None:
    profile = _profile(tmp_path, runs_dir=sample_root() / "runs")
    errors = validate_profile(profile)
    assert any("manual tester" in error for error in errors)


def test_validate_profile_rejects_the_manual_launcher_state(tmp_path: Path) -> None:
    import tempfile

    profile = _profile(tmp_path, run_dir=Path(tempfile.gettempdir()) / "a2at-ran-energy-saving-adk")
    errors = validate_profile(profile)
    assert any("manual tester's launcher" in error for error in errors)


def test_validate_profile_requires_distinct_run_and_runs_directories(tmp_path: Path) -> None:
    shared = tmp_path / "shared"
    profile = _profile(tmp_path, run_dir=shared, runs_dir=shared)
    errors = validate_profile(profile)
    assert any("must differ" in error for error in errors)


def test_validate_profile_requires_both_public_urls_for_real_nms(tmp_path: Path) -> None:
    profile = _profile(tmp_path, webhook_public_url="", oss_webhook_public_url="")
    errors = validate_profile(profile)
    assert any("WEBHOOK_PUBLIC_URL" in error for error in errors)
    assert any("OSS_WEBHOOK_PUBLIC_URL" in error for error in errors)


def test_validate_profile_requires_the_vendor_credentials(tmp_path: Path) -> None:
    profile = _profile(tmp_path, env={})
    errors = validate_profile(profile)
    assert any("A2AT_SAMPLE_NMS_A2A_BASE" in error for error in errors)


def test_port_conflicts_reports_every_taken_port(monkeypatch, tmp_path: Path) -> None:
    profile = _profile(tmp_path)

    def _in_use(port: int, host: str = "127.0.0.1") -> bool:
        del host
        return port in {18080, 19001}

    monkeypatch.setattr("eval.osp.ai.profile.port_in_use", _in_use)
    conflicts = port_conflicts(profile)
    assert len(conflicts) == 2
    assert any("ui port 18080" in item for item in conflicts)
    assert any("oss-webhook port 19001" in item for item in conflicts)


def test_manifest_records_the_build_and_the_isolation(monkeypatch, tmp_path: Path) -> None:
    profile = _profile(tmp_path)
    def _git(cwd: Path, *args: str) -> str:
        del cwd
        return "" if args == ("status", "--porcelain") else "deadbeef"

    monkeypatch.setattr("eval.osp.ai.profile._git", _git)
    document = manifest(profile, batch_id="batch-1", case_ids=[1, 34], protocols=["a2a-t"], stop_after="working")
    assert document["schema"] == "osp-ai-manifest/v1"
    assert document["gitHead"] == "deadbeef"
    assert document["gitDirty"] is False
    assert document["concurrency"] == 1
    assert document["runsDir"] == str(profile.runs_dir)
    assert document["caseIds"] == [1, 34]
    assert document["nmsEndpoint"] == "https://nms:31127/a2a/json"
