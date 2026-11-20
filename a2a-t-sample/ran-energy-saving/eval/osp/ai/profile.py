"""Load, validate and translate one isolated AI evaluation instance profile.

The profile is the CLI's input, not the launcher's. ``run_demo.py`` reads the bind ports only from
its command line, while the webhook receivers, model endpoint and vendor NMS come from the process
environment. ``launcher_argv`` and ``sanitized_env`` are therefore the single translation point:
one profile value produces exactly one launcher argument or one environment key.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Final, Mapping

from dotenv import dotenv_values

from eval.osp.ai.paths import sample_root
from eval.osp.ai.types import Profile

DEFAULT_PROFILE_PATH: Final[Path] = Path.home() / ".config" / "a2at" / "osp-ai.env"

#: Keys owned by one instance. They are dropped from the inherited environment before the profile
#: is applied, so a launched instance can never inherit the manual tester's ports or callback.
ISOLATED_PREFIXES: Final[tuple[str, ...]] = (
    "A2AT_SAMPLE_",
    "A2AT_LLM_",
    "A2AT_LANGUAGE",
    "ADK_",
    "GOOGLE_",
    "REGISTRY_CENTER_",
    "LANGFUSE_",
)

_PROFILE_PREFIX: Final[str] = "A2AT_OSP_AI_"
_DEFAULTS: Final[Mapping[str, str]] = {
    "UI_HOST": "127.0.0.1",
    "UI_PORT": "18080",
    "OSS_PORT": "18081",
    "MIE_PORT": "18082",
    "WEBHOOK_HOST": "127.0.0.1",
    "WEBHOOK_PORT": "19000",
    "OSS_WEBHOOK_PORT": "19001",
    "LANGUAGE": "en-US",
    "MODEL_MODE": "live",
    "PROTOCOL": "both",
    "DECISION_MODE": "live",
    "APPROVAL": "auto",
    "OWNER": "osp-ai",
    "NMS": "1",
    "NMS_SIM": "0",
    "NMS_SIM_PORT": "18083",
    "TRANSPORT": "webhook",
}


def load_profile(path: Path | None = None) -> Profile:
    """Load one profile from ``path``, ``$A2AT_OSP_AI_PROFILE`` or the default location.

    Raises:
        FileNotFoundError: when an explicitly named profile does not exist.
    """
    resolved = _resolve_path(path or os.environ.get("A2AT_OSP_AI_PROFILE"))
    values: dict[str, str] = {}
    if resolved is not None:
        if not resolved.is_file():
            raise FileNotFoundError(f"profile not found: {resolved}")
        values = {key: value for key, value in dotenv_values(resolved).items() if value is not None}

    root = sample_root()
    name = _value(values, "NAME", "osp-ai")
    run_dir = _dir_value(
        values,
        "RUN_DIR",
        Path.home() / ".config" / "a2at" / name / "launcher",
        base=resolved.parent if resolved is not None else Path.cwd(),
    )
    runs_dir = _dir_value(values, "RUNS_DIR", root / "runs-ai", base=root)
    language = _value(values, "LANGUAGE", _DEFAULTS["LANGUAGE"])
    model_mode = _value(values, "MODEL_MODE", _DEFAULTS["MODEL_MODE"])
    protocol = _value(values, "PROTOCOL", _DEFAULTS["PROTOCOL"])
    nms = _bool_value(values, "NMS", _DEFAULTS["NMS"])
    nms_sim = _bool_value(values, "NMS_SIM", _DEFAULTS["NMS_SIM"])

    profile_env = {
        key: value
        for key, value in values.items()
        if not key.startswith(_PROFILE_PREFIX) and not key.startswith("A2AT_OSP_AI_")
    }
    profile_env.setdefault("A2AT_SAMPLE_MODEL_MODE", model_mode)
    profile_env.setdefault("A2AT_LANGUAGE", language)
    profile_env.setdefault("A2AT_SAMPLE_PROTOCOL_MODE", protocol)
    profile_env.setdefault("A2AT_SAMPLE_TRANSPORT", _value(values, "TRANSPORT", _DEFAULTS["TRANSPORT"]))

    return Profile(
        name=name,
        sample_root=root,
        source_path=resolved,
        run_dir=run_dir,
        runs_dir=runs_dir,
        ui_host=_value(values, "UI_HOST", _DEFAULTS["UI_HOST"]),
        ui_port=_int_value(values, "UI_PORT", _DEFAULTS["UI_PORT"]),
        oss_port=_int_value(values, "OSS_PORT", _DEFAULTS["OSS_PORT"]),
        mie_port=_int_value(values, "MIE_PORT", _DEFAULTS["MIE_PORT"]),
        webhook_host=_value(values, "WEBHOOK_HOST", _DEFAULTS["WEBHOOK_HOST"]),
        webhook_port=_int_value(values, "WEBHOOK_PORT", _DEFAULTS["WEBHOOK_PORT"]),
        oss_webhook_port=_int_value(values, "OSS_WEBHOOK_PORT", _DEFAULTS["OSS_WEBHOOK_PORT"]),
        webhook_public_url=_value(values, "WEBHOOK_PUBLIC_URL", ""),
        oss_webhook_public_url=_value(values, "OSS_WEBHOOK_PUBLIC_URL", ""),
        language=language,
        model_mode=model_mode,
        protocol=protocol,
        decision_mode=_value(values, "DECISION_MODE", _DEFAULTS["DECISION_MODE"]),
        approval=_value(values, "APPROVAL", _DEFAULTS["APPROVAL"]),
        owner=_value(values, "OWNER", _DEFAULTS["OWNER"]),
        nms=nms,
        nms_sim=nms_sim,
        nms_sim_port=_int_value(values, "NMS_SIM_PORT", _DEFAULTS["NMS_SIM_PORT"]),
        env=profile_env,
    )


def sanitized_env(profile: Profile, base: Mapping[str, str] | None = None) -> dict[str, str]:
    """Return the launcher environment with every inherited instance key removed.

    The parent shell may have been started from the manual tester's instance. Because
    ``run_demo.py`` inherits its whole environment into the children, an inherited
    ``A2AT_SAMPLE_WEBHOOK_PORT`` would silently win over the profile.
    """
    env = dict(os.environ if base is None else base)
    for key in list(env):
        if key.startswith(ISOLATED_PREFIXES):
            env.pop(key, None)
    env.update({str(key): str(value) for key, value in profile.env.items()})
    env["A2AT_SAMPLE_RUN_DIR"] = str(profile.run_dir)
    env["A2AT_SAMPLE_RUNS_DIR"] = str(profile.runs_dir)
    env["A2AT_SAMPLE_PROTOCOL_MODE"] = profile.protocol
    env["A2AT_SAMPLE_MODEL_MODE"] = profile.model_mode
    env["A2AT_LANGUAGE"] = profile.language
    env["A2AT_SAMPLE_WEBHOOK_HOST"] = profile.webhook_host
    env["A2AT_SAMPLE_WEBHOOK_PORT"] = str(profile.webhook_port)
    env["A2AT_SAMPLE_OSS_WEBHOOK_PORT"] = str(profile.oss_webhook_port)
    env["A2AT_SAMPLE_WEBHOOK_PUBLIC_URL"] = profile.webhook_public_url
    env["A2AT_SAMPLE_OSS_WEBHOOK_PUBLIC_URL"] = profile.oss_webhook_public_url
    # Pin every store under this instance's runs directory. A stale ``TASK_DB``/``CLIENT_DB`` in
    # the profile or the parent shell would otherwise point the AI instance at the manual tester's
    # database while its wire logs went to ``runs-ai``.
    env["A2AT_SAMPLE_TASK_DB"] = str(profile.runs_dir / "task-status.sqlite3")
    env["A2AT_SAMPLE_OSS_TASK_DB"] = str(profile.runs_dir / "oss-task-status.sqlite3")
    env["A2AT_SAMPLE_CLIENT_DB"] = str(profile.runs_dir / "client-state.sqlite3")
    env["A2AT_SAMPLE_GATEWAY_TASK_DB"] = str(profile.runs_dir / "gateway-task-status.sqlite3")
    if profile.source_path is not None:
        env["A2AT_OSP_AI_PROFILE"] = str(profile.source_path)
    return env


def launcher_argv(profile: Profile, *, action: str = "up") -> list[str]:
    """Return the ``run_demo.py`` command line of this profile.

    The bind ports are arguments, never environment keys: the launcher does not read
    ``A2AT_SAMPLE_UI_PORT`` / ``OSS_PORT`` / ``MIE_PORT`` from its environment.
    """
    script = str(profile.sample_root / "run_demo.py")
    argv = [sys.executable, script]
    if profile.source_path is not None:
        argv.extend(["--env-file", str(profile.source_path)])
    if action == "stop":
        argv.append("--stop")
        return argv
    if action != "up":
        raise ValueError(f"unknown launcher action {action!r}")
    if profile.nms:
        argv.append("--nms")
    if profile.nms_sim:
        argv.extend(["--nms-sim", "--nms-sim-port", str(profile.nms_sim_port)])
    argv.extend(
        [
            "--ui",
            "--ui-host",
            profile.ui_host,
            "--ui-port",
            str(profile.ui_port),
            "--oss-port",
            str(profile.oss_port),
            "--mie-port",
            str(profile.mie_port),
            "--protocol",
            profile.protocol,
            "--model-mode",
            profile.model_mode,
            "--language",
            profile.language,
            "--keep-alive",
        ]
    )
    return argv


def validate_profile(profile: Profile) -> list[str]:
    """Return every reason this profile must not start, in report order."""
    errors: list[str] = []
    if profile.protocol not in {"a2a-t", "a2a", "both"}:
        errors.append(f"unsupported protocol {profile.protocol!r}; expected a2a-t, a2a or both")
    if profile.model_mode not in {"live", "offline"}:
        errors.append(f"unsupported model mode {profile.model_mode!r}")
    if profile.decision_mode not in {"live", "scripted"}:
        errors.append(f"unsupported decision mode {profile.decision_mode!r}")
    if profile.approval not in {"approve", "reject", "auto"}:
        errors.append(f"unsupported approval {profile.approval!r}")

    ports = [port for _, port in profile.bind_ports]
    if len(set(ports)) != len(ports):
        errors.append("ui/oss/mie/webhook/oss-webhook ports must all differ")
    for label, port in profile.bind_ports:
        if not 1 <= port <= 65535:
            errors.append(f"{label} port {port} is outside 1..65535")

    default_runs = (profile.sample_root / "runs").resolve()
    if profile.runs_dir.resolve() == default_runs:
        errors.append("runs_dir must not be the manual tester's runs/ directory")
    default_launcher = (Path(tempfile.gettempdir()) / "a2at-ran-energy-saving-adk").resolve()
    if profile.run_dir.resolve() == default_launcher:
        errors.append("run_dir must not be the manual tester's launcher state directory")
    if profile.run_dir.resolve() == profile.sample_root.resolve():
        errors.append("run_dir must not be the sample root")
    if profile.run_dir.resolve() == profile.runs_dir.resolve():
        errors.append("run_dir and runs_dir must differ")

    if profile.nms and not profile.nms_sim:
        if profile.protocol in {"a2a-t", "both"} and not profile.webhook_public_url:
            errors.append("A2AT_SAMPLE_WEBHOOK_PUBLIC_URL is required for a real-NMS A2A-T run")
        if profile.protocol in {"a2a", "both"} and not profile.oss_webhook_public_url:
            errors.append("A2AT_SAMPLE_OSS_WEBHOOK_PUBLIC_URL is required for a real-NMS pure-A2A run")
        missing = [
            key
            for key in (
                "A2AT_SAMPLE_NMS_A2A_BASE",
                "A2AT_SAMPLE_NMS_TOKEN_URL",
                "A2AT_SAMPLE_NMS_TOKEN_USER",
                "A2AT_SAMPLE_NMS_TOKEN_VALUE",
            )
            if not profile.env.get(key, "").strip()
        ]
        if missing:
            errors.append(f"real-NMS profile is missing {', '.join(missing)}")
    return errors


def port_in_use(port: int, host: str = "127.0.0.1") -> bool:
    """Return whether a TCP listener already accepts connections on ``host:port``."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(0.2)
        return probe.connect_ex((host, port)) == 0


def port_conflicts(profile: Profile) -> list[str]:
    """Return the profile ports that are already taken by another process."""
    return [
        f"{label} port {port} is already in use"
        for label, port in profile.bind_ports
        if port_in_use(port, profile.ui_host)
    ]


def manifest(
    profile: Profile,
    *,
    batch_id: str | None = None,
    case_ids: list[int] | None = None,
    protocols: list[str] | None = None,
    stop_after: str | None = None,
    extra: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Return the environment manifest of one run or batch."""
    document: dict[str, Any] = {
        "schema": "osp-ai-manifest/v1",
        "generatedAt": _now(),
        "batchId": batch_id,
        "profile": profile.name,
        "profilePath": str(profile.source_path) if profile.source_path is not None else None,
        "hostname": socket.gethostname(),
        "sampleRoot": str(profile.sample_root),
        "worktree": _git(profile.sample_root, "rev-parse", "--show-toplevel"),
        "gitHead": _git(profile.sample_root, "rev-parse", "HEAD"),
        "gitDirty": bool(_git(profile.sample_root, "status", "--porcelain")),
        "ports": {label: port for label, port in profile.bind_ports},
        "uiUrl": profile.ui_url,
        "runDir": str(profile.run_dir),
        "runsDir": str(profile.runs_dir),
        "protocol": profile.protocol,
        "protocols": list(protocols or []),
        "caseIds": list(case_ids or []),
        "stopAfter": stop_after,
        "model": profile.env.get("A2AT_LLM_MODEL") or profile.env.get("ADK_MODEL_NAME") or "",
        "modelEndpoint": profile.env.get("A2AT_LLM_API_BASE") or profile.env.get("ADK_MODEL_API_BASE") or "",
        "modelMode": profile.model_mode,
        "decisionMode": profile.decision_mode,
        "approval": profile.approval,
        "language": profile.language,
        "nms": profile.nms,
        "nmsSim": profile.nms_sim,
        "nmsSimPort": profile.nms_sim_port if profile.nms_sim else None,
        "nmsEndpoint": profile.env.get("A2AT_SAMPLE_NMS_A2A_BASE", ""),
        "concurrency": 1,
    }
    if extra:
        document.update(extra)
    return document


def _resolve_path(value: str | Path | None) -> Path | None:
    """Expand one optional profile path."""
    if value is None or str(value).strip() == "":
        return None
    return Path(str(value)).expanduser()


def _value(values: Mapping[str, str], key: str, default: str) -> str:
    """Return a profile value, accepting both ``A2AT_OSP_AI_`` aliases."""
    for candidate in (f"{_PROFILE_PREFIX}{key}", f"A2AT_SAMPLE_{key}"):
        raw = values.get(candidate)
        if raw is not None and raw.strip() != "":
            return raw.strip()
    return default


def _int_value(values: Mapping[str, str], key: str, default: str) -> int:
    """Return an integer profile value."""
    raw = _value(values, key, default)
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(f"{key} must be an integer, got {raw!r}") from exc


def _bool_value(values: Mapping[str, str], key: str, default: str) -> bool:
    """Return a boolean profile value (``1/true/yes/on``)."""
    return _value(values, key, default).lower() in {"1", "true", "yes", "on"}


def _dir_value(
    values: Mapping[str, str],
    key: str,
    default: Path,
    *,
    base: Path | None = None,
) -> Path:
    """Return one resolved directory; a relative value resolves against ``base``."""
    raw = _value(values, key, str(default))
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = (base or Path.cwd()) / path
    return path.resolve()


def _git(cwd: Path, *args: str) -> str:
    """Run one read-only git command and return its stripped stdout (empty on failure)."""
    try:
        completed = subprocess.run(
            ["git", *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return completed.stdout.strip() if completed.returncode == 0 else ""


def _now() -> str:
    """Return the current UTC time in ISO form."""
    return datetime.now(timezone.utc).isoformat()
