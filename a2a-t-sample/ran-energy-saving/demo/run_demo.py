#!/usr/bin/env python3
"""Three-process launcher of the RAN energy-saving A2A-T sample (ADK client only).

The registry center and the A2A server each bind a port; started in the foreground they would
block the terminal. This launcher starts them as **background subprocesses** (new layout:
``registry.registry_main`` and ``protocol.server_app``), waits for their ports, then runs the client
in the foreground — or starts the Web UI as one more managed subprocess with ``--ui`` — and finally
stops the background processes unless ``--keep-alive`` is given.

The Web UI is a managed child (``demo.web.serve``), not ``uvicorn.run`` inside this process: its PID
is therefore recorded in the state file, so ``--stop`` also reclaims a UI left behind when the
launcher itself was killed abruptly. ``--stop`` additionally probes the recorded UI port and stops
the listener when its command line proves it is this sample (a legacy UI from older launchers).

All background processes stream their output to the terminal live and interleaved (through a pipe
in a normal run, through a file follower under ``--keep-alive``). ``PYTHONPATH`` points at the sample
root; the legacy ``src/`` tree is never imported.

Every run gets its own ``.env`` (a copy of ``a2a-t-sample/.env`` or the sample ``.env`` with the
run-level keys overridden) in a temporary run directory, so the shared configuration is never
mutated. Language and model mode are process-level: they are written into the run ``.env`` and into
the child environment (design D18).

With ``--keep-alive``, the PID file also records the model mode, language and trace token of the
background processes. A later launch reuses them only when the record matches the requested run; a
mismatch stops them and starts fresh, so an offline UI can never talk to a server still running live
LiteLlm. A listening port with no PID record is refused instead of reused.

Usage:
    python run_demo.py --scenario information
    python run_demo.py --scenario target --mode manual --ui
    python run_demo.py --scenario reject --transport webhook --model-mode offline
    python run_demo.py --ui --ui-port 8080 --language zh-CN
    python run_demo.py --keep-alive          # leave registry/server running; stop with --stop
    python run_demo.py --stop
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from pathlib import Path
from typing import Final, TextIO
from urllib.parse import urlsplit

from protocol.approval_gate import DEFAULT_APPROVAL_TIMEOUT_SECONDS
from shared.config import merge_sample_process_env
from shared.run_constants import (
    DEMO_PROTOCOLS,
    PROTOCOL_A2A_NAME,
    PROTOCOL_A2A_T,
    PROTOCOL_MODE_BOTH,
    PROTOCOL_PURE,
    normalize_protocol_name,
)

#: The demo lives one level below the sample directory that owns ``runs/`` and the SDK path.
CASE_DIR = Path(__file__).resolve().parent.parent
SAMPLE_DIR = CASE_DIR.parent
REPO_ROOT = SAMPLE_DIR.parent
#: Launcher state (pids, logs, per-run .env). A second instance sets a different value so
#: ``--stop`` only touches its own processes.
RUN_DIR = Path(os.environ.get("A2AT_SAMPLE_RUN_DIR") or Path(tempfile.gettempdir()) / "a2at-ran-energy-saving-adk")
PID_FILE = RUN_DIR / "pids.json"
#: Data directory (transcripts, sqlite stores, NMS wire logs, Web UI history). It defaults to the
#: sample's ``runs/`` and is also written into the per-run .env as ``A2AT_SAMPLE_RUNS_DIR``.
RUNS_DIR = Path(os.environ.get("A2AT_SAMPLE_RUNS_DIR") or CASE_DIR / "runs")
TASK_DB_PATH = RUNS_DIR / "task-status.sqlite3"
OSS_TASK_DB_PATH = RUNS_DIR / "oss-task-status.sqlite3"
CLIENT_DB_PATH = RUNS_DIR / "client-state.sqlite3"
GATEWAY_TASK_DB_PATH = RUNS_DIR / "gateway-task-status.sqlite3"

#: Source ``.env`` candidates, in resolution order.
ENV_CANDIDATES = (CASE_DIR / ".env", SAMPLE_DIR / ".env", SAMPLE_DIR / "env.example")

#: Webhook TLS files whose relative source paths are anchored to the sample root in the run ``.env``
#: (the child processes run from ``RUN_DIR``, where relative paths do not resolve).
_TLS_PATH_KEYS = ("A2AT_SAMPLE_WEBHOOK_TLS_CERT", "A2AT_SAMPLE_WEBHOOK_TLS_KEY")

REGISTRY_MODULE = "demo.registry.registry_main"
SERVER_MODULE = "protocol.server_app"
UI_MODULE = "demo.web.serve"
GATEWAY_MODULE = "protocol.client_gateway"
OSS_MODULE = "protocol.oss_agent"
MIE_MODULE = "protocol.mie_agent"

DEFAULT_REGISTRY_PORT = 5001
DEFAULT_SERVER_PORT = 8000
DEFAULT_OSS_PORT = 8001
DEFAULT_MIE_PORT = 8002
DEFAULT_UI_PORT = 8080
DEFAULT_UI_HOST = "127.0.0.1"

#: Decisions of the launcher when a background port is already listening.
REUSE_START = "start"
REUSE_REUSE = "reuse"
REUSE_RESTART = "restart"
REUSE_REFUSE = "refuse"


def _port_open(port: int, host: str = "127.0.0.1") -> bool:
    """Return whether a TCP port accepts a connection."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.3)
        return sock.connect_ex((host, port)) == 0


def _probe_host(bind_host: str) -> str:
    """Return the host used to probe a port bound to ``bind_host``."""
    return "127.0.0.1" if bind_host in ("", "0.0.0.0", "::") else bind_host


def _wait_port(
    port: int,
    timeout: float,
    proc: subprocess.Popen[str] | None = None,
    host: str = "127.0.0.1",
) -> bool:
    """Wait until a port accepts connections, failing fast when the child exits."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if proc is not None and proc.poll() is not None:
            return False
        if _port_open(port, host):
            return True
        time.sleep(0.3)
    return False


def _source_env_path() -> Path | None:
    """Return the first existing source ``.env`` (or ``env.example``)."""
    for candidate in ENV_CANDIDATES:
        if candidate.exists():
            return candidate
    return None


def _explicit_env_path(value: str) -> Path:
    """Resolve an explicitly selected configuration file against the current directory."""
    path = Path(value).expanduser()
    return path if path.is_absolute() else Path.cwd() / path


def _env_file_argument(argv: list[str]) -> str:
    """Return ``--env-file`` from raw argv before argparse builds the environment defaults."""
    for index, token in enumerate(argv):
        if token == "--env-file" and index + 1 < len(argv):
            return argv[index + 1]
        if token.startswith("--env-file="):
            return token.partition("=")[2]
    return ""


def _resolve_source_env(explicit: str) -> tuple[Path | None, str | None]:
    """Return the selected configuration file and an error message when it is missing."""
    if not explicit:
        return _source_env_path(), None
    path = _explicit_env_path(explicit)
    if not path.is_file():
        return None, f"configuration file not found: {path}"
    return path, None


def _read_env(path: Path) -> dict[str, str]:
    """Read one dotenv file as plain string values."""
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        values[key.strip()] = value.strip()
    return values


def _env_str(values: dict[str, str], key: str, default: str = "") -> str:
    """Resolve one string value: shell environment, then the source .env, then a default."""
    return os.environ.get(key) or values.get(key) or default


def _env_int(values: dict[str, str], key: str, default: int) -> int:
    """Resolve one integer value with a readable failure (shell environment first)."""
    raw = _env_str(values, key, str(default))
    try:
        return int(raw)
    except ValueError as exc:
        raise SystemExit(f"[run_demo] invalid integer for {key}: {raw}") from exc


def _env_truthy(values: dict[str, str], key: str) -> bool:
    """Return whether one flag is set to a truthy value (shell environment first)."""
    return _env_str(values, key, "").strip().lower() in {"1", "true", "yes", "on"}


def _nms_sim_settings(values: dict[str, str], port: int) -> dict[str, str]:
    """Return the simulator child environment.

    Every switch is resolved here (shell environment, then the source ``.env``, then the default)
    so the per-run file can omit it without the simulator silently falling back to a different
    carrier or business constraint. The simulator does not read dotenv itself.
    """
    return {
        "A2AT_SAMPLE_NMS_SIM_HOST": "127.0.0.1",
        "A2AT_SAMPLE_NMS_SIM_PORT": str(port),
        "A2AT_SAMPLE_NMS_SIM_FIDELITY": _env_str(values, "A2AT_SAMPLE_NMS_SIM_FIDELITY", "strict"),
        "A2AT_SAMPLE_NMS_SIM_HOLD_WORKING": _env_str(values, "A2AT_SAMPLE_NMS_SIM_HOLD_WORKING", "0"),
        "A2AT_SAMPLE_NMS_SIM_CANCEL_UNSUPPORTED": _env_str(
            values, "A2AT_SAMPLE_NMS_SIM_CANCEL_UNSUPPORTED", "0"
        ),
        "A2AT_SAMPLE_NMS_SIM_REGION": _env_str(values, "A2AT_SAMPLE_NMS_SIM_REGION", "A2ATestRegion"),
        "A2AT_SAMPLE_NMS_SIM_WINDOW": _env_str(values, "A2AT_SAMPLE_NMS_SIM_WINDOW", "00:00~06:00"),
        "A2AT_SAMPLE_NMS_SIM_MAX_SAVING_RATE": _env_str(
            values, "A2AT_SAMPLE_NMS_SIM_MAX_SAVING_RATE", "35"
        ),
        "A2AT_SAMPLE_NMS_SIM_SITES": _env_str(values, "A2AT_SAMPLE_NMS_SIM_SITES", ""),
        "A2AT_SAMPLE_NMS_SIM_NEGOTIATION_CARRIER": _env_str(
            values, "A2AT_SAMPLE_NMS_SIM_NEGOTIATION_CARRIER", "task"
        ),
        "A2AT_SAMPLE_NMS_SIM_NEGOTIATION_KINDS": _env_str(
            values, "A2AT_SAMPLE_NMS_SIM_NEGOTIATION_KINDS", "information,target,feasibility"
        ),
        "A2AT_SAMPLE_NMS_SIM_MAX_ROUNDS": _env_str(values, "A2AT_SAMPLE_NMS_SIM_MAX_ROUNDS", "5"),
        "A2AT_SAMPLE_NMS_SIM_TASK_SHAPE": _env_str(values, "A2AT_SAMPLE_NMS_SIM_TASK_SHAPE", "standard"),
        "A2AT_SAMPLE_NMS_SIM_PURE_FAILURE": _env_str(
            values, "A2AT_SAMPLE_NMS_SIM_PURE_FAILURE", "completed"
        ),
        "A2AT_SAMPLE_NMS_SIM_WINDOW_POLICY": _env_str(
            values, "A2AT_SAMPLE_NMS_SIM_WINDOW_POLICY", "exact"
        ),
    }


def _callback_url_port_error(
    key: str,
    url: str,
    bind_port: int,
    *,
    allow_mismatch: bool,
) -> str | None:
    """Return an error when one callback URL names a port different from its receiver.

    A URL without an explicit port is treated as a gateway address and left alone; the explicit
    escape hatch covers a NAT/gateway that maps a different external port to the local receiver.
    """
    if not url or bind_port <= 0 or allow_mismatch:
        return None
    try:
        port = urlsplit(url).port
    except ValueError:
        return f"[run_demo] {key} is not a valid URL: {url}"
    if port is not None and port != bind_port:
        return (
            f"[run_demo] {key} port {port} does not match the receiver port {bind_port}; "
            "fix the URL or set A2AT_SAMPLE_ALLOW_CALLBACK_PORT_MISMATCH=1 when a gateway/NAT "
            "maps a different external port."
        )
    return None


def _ui_port_default(values: dict[str, str]) -> int:
    """Return the ``--ui-port`` default: ``A2AT_SAMPLE_UI_PORT``, else ``8080``."""
    return _env_int(values, "A2AT_SAMPLE_UI_PORT", DEFAULT_UI_PORT)


def _ui_host_default(values: dict[str, str]) -> str:
    """Return the ``--ui-host`` default: ``A2AT_SAMPLE_UI_HOST``, else ``127.0.0.1``."""
    return _env_str(values, "A2AT_SAMPLE_UI_HOST", DEFAULT_UI_HOST)


def _gateway_port_default(values: dict[str, str]) -> int:
    """Return the ``--gateway-port`` default: ``A2AT_SAMPLE_GATEWAY_PORT``, else ``8001``."""
    return _env_int(values, "A2AT_SAMPLE_GATEWAY_PORT", 8001)


def _warm_live_model(config: object) -> None:
    """Prime the live model endpoint before the first demo turn.

    A cold OpenAI-compatible endpoint can take tens of seconds on its first call; that delay lands
    on the Task-T validation and looks like the demo is stuck. One tiny completion at startup
    keeps the first visible step on the normal path. Failures are ignored: the run itself still
    reports the real error.
    """
    import httpx

    model_mode = getattr(config, "model_mode", "")
    if model_mode != "live":
        return
    base = str(getattr(config, "planner_api_base", "") or getattr(config, "adk_model_api_base", ""))
    if not base:
        return
    model = str(
        getattr(config, "planner_model", "") or str(getattr(config, "adk_model_name", "")).removeprefix("openai/")
    )
    if not model:
        return
    key = str(getattr(config, "planner_api_key", "") or getattr(config, "adk_model_api_key", ""))
    headers = {"authorization": f"Bearer {key}"} if key else {}
    try:
        response = httpx.post(
            f"{base.rstrip('/')}/chat/completions",
            json={
                "model": model,
                "messages": [{"role": "user", "content": "ping"}],
                "max_tokens": 1,
            },
            headers=headers,
            timeout=8.0,
        )
        response.raise_for_status()
        print("[run_demo] live model warm-up completed")
    except Exception as exc:  # noqa: BLE001 - warm-up must never block the demo
        print(f"[run_demo] live model warm-up skipped: {exc}")


def _child_env(
    language: str,
    extra: dict[str, str] | None = None,
    *,
    exclude: tuple[str, ...] = (),
) -> dict[str, str]:
    """Return the environment of the child processes (sample root on ``PYTHONPATH``).

    ``exclude`` drops keys inherited from this process before ``extra`` is applied. A child that
    owns its own callback receiver must not inherit the launcher's UI callback: process env wins
    over the per-run ``.env`` in ``shared.config``, so an inherited value would silently override
    the OSS-specific one.
    """
    env = os.environ.copy()
    for key in exclude:
        env.pop(key, None)
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = str(CASE_DIR) + (os.pathsep + existing if existing else "")
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUNBUFFERED"] = "1"
    env["A2AT_LANGUAGE"] = language
    if extra:
        env.update(extra)
    return env


def _server_trace_env(
    *,
    ui: bool,
    ui_port: int,
    trace_token: str | None = None,
    approval_gate: bool = False,
    approval_timeout: float = DEFAULT_APPROVAL_TIMEOUT_SECONDS,
) -> dict[str, str]:
    """Return the Run Explorer and approval-gate environment of the server process.

    ``--ui`` points the server at the Web UI trace endpoint with a per-run local token (a reused
    keep-alive server passes its recorded token back so both processes agree); the CLI points it at
    ``runs/<runId>/server-trace.jsonl`` under the sample root. ``--approval-gate`` makes the NMS
    wait for the engineer before applying a plan.
    """
    if ui:
        env = {
            "A2AT_SAMPLE_TRACE_URL": _trace_url(ui_port),
            "A2AT_SAMPLE_TRACE_TOKEN": trace_token or secrets.token_hex(16),
        }
    else:
        env = {"A2AT_SAMPLE_TRACE_DIR": str(CASE_DIR / "runs")}
    if approval_gate:
        env["A2AT_SAMPLE_APPROVAL_GATE"] = "1"
        env["A2AT_SAMPLE_APPROVAL_TIMEOUT_SECONDS"] = str(approval_timeout)
    return env


def _trace_url(ui_port: int) -> str:
    """Return the local trace endpoint the server targets for one UI port."""
    return f"http://127.0.0.1:{ui_port}"


def _trace_summary(spans: list[dict[str, object]]) -> str:
    """Summarize the LLM/tool spans of one CLI client run (counts and token usage)."""
    llm = [span for span in spans if span.get("kind") == "llm"]
    tools = [span for span in spans if span.get("kind") == "tool"]
    prompt = 0
    completion = 0
    total = 0
    measured = False
    for span in llm:
        tokens = span.get("tokens")
        if not isinstance(tokens, dict):
            continue
        prompt_value = tokens.get("prompt")
        completion_value = tokens.get("completion")
        total_value = tokens.get("total")
        if isinstance(prompt_value, int):
            prompt += prompt_value
        if isinstance(completion_value, int):
            completion += completion_value
        if isinstance(total_value, int):
            total += total_value
            measured = True
    usage = f"prompt={prompt} completion={completion} total={total}" if measured else "n/a (scripted)"
    return f"llm={len(llm)} tool={len(tools)} tokens={usage}"


#: Prefixes/exact keys of the OTel/Langfuse configuration the SDK reads from the *process*
#: environment. Children inherit os.environ, so keys selected by ``--env-file`` must be exported
#: here or the server-side exporter never starts.
_OBSERVABILITY_ENV_PREFIXES: Final[tuple[str, ...]] = (
    "OTEL_",
    "LANGFUSE_",
    "A2AT_OBSERVABILITY_",
    "A2AT_TRACE_",
    "A2AT_METRIC_",
    "A2AT_LOG_",
    "A2AT_EXPORTER_",
    "A2AT_EXTRACT_",
    "A2AT_LOGS_",
    "A2AT_SERVICE_NAME",
)


def _export_observability_env(values: dict[str, str]) -> None:
    """Expose the selected file's OTel/Langfuse settings to child processes.

    A shell export still wins; a key that is only in the configuration file is copied into the
    launcher environment because the SDK observability reads ``os.getenv`` rather than the file.
    """
    for key, value in values.items():
        if key in os.environ:
            continue
        if key.startswith(_OBSERVABILITY_ENV_PREFIXES):
            os.environ[key] = value


def _write_run_env(
    *,
    source: Path | None,
    language: str,
    model_mode: str,
    discovery: str,
    transport: str,
    webhook_port: int,
    server_host: str,
    server_port: int,
    webhook_host: str | None = None,
    registry_host: str,
    registry_port: int,
    ui_port: int,
    gateway_port: int = 8001,
    gateway_register: bool = False,
    protocol: str = PROTOCOL_A2A_T,
    oss_port: int = 8001,
    mie_port: int = 8002,
    intent_mode: str | None = None,
    extra: dict[str, str] | None = None,
) -> Path:
    """Write the per-run ``.env`` and return its path."""
    values = _read_env(source) if source is not None else {}
    # Precedence: CLI arguments > shell environment > source .env > defaults. The launcher's CLI
    # values are written below, so the shell overlay happens first; every sample-prefixed export
    # (model endpoints included) survives into the per-run file read by the children and the SDK.
    values = merge_sample_process_env(values)
    pure_stack = protocol in (PROTOCOL_PURE, PROTOCOL_MODE_BOTH)
    direct_port = oss_port if pure_stack else server_port
    # The pure engineer client must discover the OSS Agent, not the RAN Agent. The OSS card is
    # not registered under the RAN card's name, so direct discovery of its URL is the only path.
    # In pure + NMS the same .env is also read by the OSS Agent: it must keep ``nms`` for its
    # downstream hop, while the Web UI chooses the discovery per run.
    client_discovery = "direct" if pure_stack and discovery != "nms" else discovery
    values.update(
        {
            "A2AT_LANGUAGE": language,
            "A2AT_SAMPLE_MODEL_MODE": model_mode,
            "A2AT_SAMPLE_DISCOVERY": client_discovery,
            "A2AT_SAMPLE_TRANSPORT": transport,
            "A2AT_SAMPLE_WEBHOOK_HOST": (webhook_host or values.get("A2AT_SAMPLE_WEBHOOK_HOST") or "127.0.0.1"),
            "A2AT_SAMPLE_WEBHOOK_PORT": str(webhook_port),
            "A2AT_SAMPLE_HOST": server_host,
            "A2AT_SAMPLE_PORT": str(server_port),
            "A2AT_SAMPLE_UI_PORT": str(ui_port),
            "A2AT_SAMPLE_DIRECT_SERVER_URL": f"http://{server_host}:{direct_port}",
            "A2AT_SAMPLE_PROTOCOL_MODE": protocol,
            "A2AT_SAMPLE_RAN_URL": f"http://{server_host}:{server_port}",
            "A2AT_SAMPLE_OSS_HOST": server_host,
            "A2AT_SAMPLE_OSS_PORT": str(oss_port),
            "A2AT_SAMPLE_MIE_URL": f"http://{server_host}:{mie_port}",
            "A2AT_SAMPLE_MIE_PORT": str(mie_port),
            "A2AT_SAMPLE_RUNS_DIR": str(RUNS_DIR),
            "A2AT_SAMPLE_TASK_DB": str(TASK_DB_PATH),
            "A2AT_SAMPLE_OSS_TASK_DB": str(OSS_TASK_DB_PATH),
            "A2AT_SAMPLE_CLIENT_DB": str(CLIENT_DB_PATH),
            "A2AT_SAMPLE_GATEWAY_TASK_DB": str(GATEWAY_TASK_DB_PATH),
            "A2AT_SAMPLE_GATEWAY_HOST": server_host,
            "A2AT_SAMPLE_GATEWAY_PORT": str(gateway_port),
            "A2AT_SAMPLE_GATEWAY_REGISTER": "1" if gateway_register else "0",
            "REGISTRY_CENTER_HOST": registry_host,
            "REGISTRY_CENTER_PORT": str(registry_port),
        }
    )
    if intent_mode:
        values["A2AT_SAMPLE_INTENT_MODE"] = intent_mode
    if extra:
        values.update(extra)
    for key in _TLS_PATH_KEYS:
        path = values.get(key)
        if path and not Path(path).is_absolute():
            # Relative certificate paths are resolved against the sample root here: the client
            # runs from the run directory, where they do not exist.
            values[key] = str(CASE_DIR / path)
    text = "\n".join(f"{key}={value}" for key, value in values.items()) + "\n"
    env_path = RUN_DIR / ".env"
    env_path.write_text(text, encoding="utf-8", newline="\n")
    try:  # best effort: keep the copied config (may carry an API key) owner-only
        os.chmod(env_path, 0o600)
    except OSError:
        pass
    return env_path


def _pid_alive(pid: int) -> bool:
    """Return whether ``pid`` is still running."""
    if pid <= 0:
        return False
    if os.name == "nt":
        try:
            result = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}", "/NH", "/FO", "CSV"],
                check=False,
                capture_output=True,
            )
        except OSError:
            return True
        if result.returncode != 0:
            return True
        # ``tasklist`` follows the console code page (e.g. GBK), so match the ASCII PID bytes
        # instead of decoding the whole line.
        marker = f'","{pid}"'.encode("ascii")
        return any(marker in line for line in result.stdout.splitlines())
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    # A zombie is not running any more; it only waits to be reaped by its parent. Counting it as
    # alive would make ``--stop`` report a kill that already happened.
    try:
        stat = Path(f"/proc/{pid}/stat").read_text(encoding="ascii", errors="replace")
    except OSError:
        return True
    try:
        return stat.rsplit(")", 1)[1].split()[0] != "Z"
    except IndexError:
        return True


def _wait_pid_exit(pid: int, timeout: float) -> bool:
    """Wait until ``pid`` exits; return whether it did."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not _pid_alive(pid):
            return True
        time.sleep(0.1)
    return not _pid_alive(pid)


def _stop_pids(pids: list[int]) -> int:
    """Terminate the recorded background processes and return how many actually exited.

    PIDs that are already gone are not counted, so the printed ``stopped N`` never claims a
    kill that did not happen (the previous implementation counted attempts).
    """
    stopped = 0
    for pid in dict.fromkeys(pids):
        if not _pid_alive(pid):
            continue
        try:
            if os.name == "nt":
                result = subprocess.run(
                    ["taskkill", "/PID", str(pid), "/T", "/F"],
                    check=False,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                if result.returncode == 0 and _wait_pid_exit(pid, 3.0):
                    stopped += 1
            else:
                os.kill(pid, signal.SIGTERM)
                if not _wait_pid_exit(pid, 3.0):
                    os.kill(pid, signal.SIGKILL)
                if _wait_pid_exit(pid, 2.0):
                    stopped += 1
        except (ProcessLookupError, PermissionError, OSError):
            pass
    return stopped


def _listener_pids(port: int) -> list[int]:
    """Return the PIDs listening on a TCP ``port`` (best effort, platform-specific)."""
    if os.name == "nt":
        try:
            result = subprocess.run(
                ["netstat", "-ano", "-p", "TCP"],
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
        except OSError:
            return []
        pids: list[int] = []
        for line in result.stdout.splitlines():
            fields = line.split()
            if len(fields) < 5 or fields[0].upper() != "TCP" or fields[3].upper() != "LISTENING":
                continue
            if fields[1].rsplit(":", 1)[-1] != str(port):
                continue
            try:
                pids.append(int(fields[4]))
            except ValueError:
                continue
        return list(dict.fromkeys(pids))
    if shutil.which("lsof"):
        result = subprocess.run(
            ["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-t"],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        return [int(token) for token in result.stdout.split() if token.isdigit()]
    if shutil.which("ss"):
        result = subprocess.run(
            ["ss", "-ltnpH"],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        pids = []
        for line in result.stdout.splitlines():
            fields = line.split()
            if len(fields) < 4 or not fields[3].endswith(f":{port}"):
                continue
            pids.extend(int(match) for match in re.findall(r"pid=(\d+)", line))
        return list(dict.fromkeys(pids))
    return []


def _process_command_line(pid: int) -> str:
    """Return the command line of ``pid``, or an empty string when it cannot be read."""
    if os.name == "nt":
        try:
            result = subprocess.run(
                [
                    "powershell",
                    "-NoProfile",
                    "-Command",
                    f'(Get-CimInstance Win32_Process -Filter "ProcessId={pid}").CommandLine',
                ],
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
        except OSError:
            return ""
        return result.stdout.strip()
    try:
        raw = Path(f"/proc/{pid}/cmdline").read_bytes()
    except OSError:
        raw = b""
    if raw:
        return raw.replace(b"\0", b" ").decode("utf-8", "replace").strip()
    result = subprocess.run(
        ["ps", "-p", str(pid), "-o", "command="],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return result.stdout.strip()


def _is_module_child(pid: int, module: str) -> bool:
    """Return whether ``pid`` is a child started with ``python -m <module>``."""
    return module in _process_command_line(pid)


def _is_ui_child(pid: int) -> bool:
    """Return whether ``pid`` is the managed ``demo.web.serve`` UI child.

    Only this leaf process is safe to restart from the launcher: killing an older launcher that
    served the UI in-process would also take down the registry/server children it owns.
    """
    return _is_module_child(pid, "demo.web.serve")


def _is_demo_process(pid: int) -> bool:
    """Return whether ``pid`` runs this sample's launcher or its Web UI.

    The check guards :func:`_kill_port_listener`: a port can be occupied by an unrelated service,
    so only a command line that names ``demo.web.serve`` (managed UI child) or the launcher with
    ``--ui`` (UI served inside an older launcher) may be stopped.
    """
    command = _process_command_line(pid)
    if not command:
        return False
    if "demo.web.serve" in command:
        return True
    return ("demo.run_demo" in command or "run_demo.py" in command) and "--ui" in command


def _kill_port_listener(port: int) -> int:
    """Stop the listener of ``port`` when it is demonstrably this sample; return the count."""
    return _stop_pids([pid for pid in _listener_pids(port) if _is_demo_process(pid)])


def _load_state() -> dict[str, object]:
    """Load the persisted launcher state (``pids`` plus the run metadata).

    A PID file written by an older launcher only carries ``pids``; the missing metadata is what
    makes a reuse unsafe, so :func:`_server_reuse_decision` restarts in that case.
    """
    if not PID_FILE.exists():
        return {}
    try:
        data = json.loads(PID_FILE.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return {}
    return dict(data) if isinstance(data, dict) else {}


def _state_pids(state: dict[str, object]) -> list[int]:
    """Return the managed PIDs of one launcher state record."""
    raw_pids = state.get("pids", [])
    if not isinstance(raw_pids, list):
        return []
    pids: list[int] = []
    for raw_pid in raw_pids:
        try:
            pids.append(int(raw_pid))
        except (TypeError, ValueError):
            continue
    return pids


def _load_pids() -> list[int]:
    """Load the persisted background PIDs."""
    return _state_pids(_load_state())


def _save_state(
    pids: list[int],
    *,
    model_mode: str,
    language: str,
    protocol_mode: str = PROTOCOL_A2A_T,
    trace_token: str = "",
    trace_url: str = "",
    ui_port: int | None = None,
    gateway_port: int | None = None,
    approval_gate: bool = False,
    approval_timeout: float = DEFAULT_APPROVAL_TIMEOUT_SECONDS,
) -> None:
    """Persist the managed PIDs and their run metadata atomically.

    A concurrent ``--stop`` never reads a partial file; a later launcher can tell whether a
    listening server was started with the requested model mode/language and trace target. The file
    carries the local trace token, so it is owner-only like the SDK ``.env``.
    """
    _write_state(
        {
            "pids": pids,
            "model_mode": model_mode,
            "language": language,
            "protocol_mode": protocol_mode,
            "trace_token": trace_token,
            "trace_url": trace_url,
            "ui_port": ui_port,
            "gateway_port": gateway_port,
            "approval_gate": approval_gate,
            "approval_timeout": approval_timeout,
        }
    )


def _write_state(state: dict[str, object]) -> None:
    """Atomically write a launcher state record; the file carries the local trace token."""
    tmp_path = PID_FILE.with_suffix(".json.tmp")
    tmp_path.write_text(json.dumps(state), encoding="utf-8")
    os.replace(tmp_path, PID_FILE)
    try:  # best effort: the state carries the local trace token
        os.chmod(PID_FILE, 0o600)
    except OSError:
        pass


def _server_reuse_decision(
    *,
    port_open: bool,
    state: dict[str, object],
    model_mode: str,
    language: str,
    protocol_mode: str = PROTOCOL_A2A_T,
    ui: bool,
    trace_url: str = "",
    ui_port: int | None = None,
    approval_gate: bool = False,
    approval_timeout: float = DEFAULT_APPROVAL_TIMEOUT_SECONDS,
) -> str:
    """Decide whether a listening A2A server may be reused for the requested run.

    PID reuse is only safe when the recorded process is demonstrably the same configuration: a
    reused server keeps its own client/ADK provider, so an offline UI talking to a live server would
    silently make live LiteLlm calls (and the UI would report ``offline``). The trace token pairs
    the server with this UI, so a UI run cannot reuse a server recording server spans to a file
    (CLI keep-alive), and vice versa. A UI run must also post to the same trace URL/UI port: a
    server launched for another port would send its spans to a UI that is not serving this run.

    Args:
        port_open: whether the configured server port already accepts connections.
        state: persisted launcher state (empty when no PID file exists).
        model_mode: requested model mode (``offline`` / ``live``).
        language: requested process-level language.
        ui: whether this launch serves the Web UI.
        trace_url: trace endpoint requested for this launch (UI mode).
        ui_port: UI port requested for this launch (UI mode).

    Returns:
        ``REUSE_START`` (nothing listening), ``REUSE_REUSE`` (compatible),
        ``REUSE_RESTART`` (our processes, incompatible configuration) or ``REUSE_REFUSE``
        (a process the launcher does not own).
    """
    if not port_open:
        return REUSE_START
    if not _state_pids(state):
        return REUSE_REFUSE
    if state.get("model_mode") != model_mode or state.get("language") != language:
        return REUSE_RESTART
    if str(state.get("protocol_mode") or PROTOCOL_A2A_T) != protocol_mode:
        return REUSE_RESTART
    if bool(state.get("approval_gate")) != approval_gate:
        return REUSE_RESTART
    recorded_timeout = state.get("approval_timeout") or DEFAULT_APPROVAL_TIMEOUT_SECONDS
    if approval_gate and float(str(recorded_timeout)) != approval_timeout:
        return REUSE_RESTART
    recorded_token = str(state.get("trace_token") or "")
    if ui and not recorded_token:
        return REUSE_RESTART
    if not ui and recorded_token:
        return REUSE_RESTART
    if ui:
        recorded_url = str(state.get("trace_url") or "")
        if recorded_url != trace_url:
            return REUSE_RESTART
        recorded_port = state.get("ui_port")
        if isinstance(recorded_port, int) and ui_port is not None and recorded_port != ui_port:
            return REUSE_RESTART
    return REUSE_REUSE


def _wait_ports_closed(ports: list[int], timeout: float = 10.0) -> bool:
    """Wait until every port stops accepting connections (after stopping reused processes)."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not any(_port_open(port) for port in ports):
            return True
        time.sleep(0.1)
    return False


def _show_tail(path: Path) -> None:
    """Print the tail of one child log."""
    if path.exists():
        print(f"--- {path} (tail) ---")
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines()[-20:]:
            print(line)


def _follow_log(path: Path, stop: threading.Event, *, from_end: bool = False) -> None:
    """Echo lines appended to ``path`` to the terminal until ``stop`` is set."""
    while not stop.is_set() and not path.exists():
        time.sleep(0.1)
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            if from_end:
                handle.seek(0, os.SEEK_END)
            pending = ""
            while not stop.is_set():
                line = handle.readline()
                if not line:
                    time.sleep(0.15)
                    continue
                if line.endswith("\n"):
                    sys.stdout.write(pending + line)
                    sys.stdout.flush()
                    pending = ""
                else:
                    pending += line
            if pending:
                sys.stdout.write(pending)
                sys.stdout.flush()
    except OSError:
        pass


def _pump_pipe(stream: TextIO, log_handle: TextIO) -> None:
    """Echo a child's merged output pipe to the terminal and the log file in real time."""
    try:
        while True:
            line = stream.readline()
            if not line:
                break
            sys.stdout.write(line)
            sys.stdout.flush()
            log_handle.write(line)
            log_handle.flush()
    except (OSError, ValueError):
        pass
    finally:
        try:
            stream.close()
        except OSError:
            pass


def _preflight(python: str, source_env: Path | None) -> bool:
    """Check the source configuration and the runtime dependencies."""
    if source_env is None:
        print("[run_demo] no source .env found; copy a2a-t-sample/env.example to a2a-t-sample/.env")
        return False
    check = subprocess.run(
        [
            python,
            "-c",
            "import a2a, a2a_t, google.adk, langgraph, langchain_openai, sse_starlette, starlette, uvicorn",
        ],
        cwd=str(SAMPLE_DIR),
        env=_child_env("en-US"),
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    if check.returncode != 0:
        print(f"[run_demo] '{python}' is missing the sample dependencies.")
        print(f"           Run:  cd {SAMPLE_DIR}  &&  uv pip install -r requirements.txt")
        return False
    return True


async def _run_client_foreground(
    *,
    env_path: Path,
    scenario: str,
    mode: str,
    transport: str,
    language: str,
    protocol: str = PROTOCOL_A2A_T,
    discovery: str | None = None,
    user_request: str | None = None,
) -> int:
    """Run the client flow in-process and write its transcript."""
    from protocol.transcript import NegotiationTranscript
    from shared.config import load_config
    from shared.logging_setup import log_context
    from shared.models import NegotiationProposeView

    from demo.composition import build_demo_runtime

    config = load_config(env_path=env_path)
    transcript = NegotiationTranscript(
        scenario=scenario,
        language=language,
        model_mode=config.model_mode,
        mode=mode,
        transport=transport,
        runs_dir=Path(config.runs_dir),
        source={"kind": "cli", "channel": "cli"},
    )

    def event_sink(event: dict[str, object]) -> None:
        entry = transcript.record_event(event)
        performative = entry.get("performative") or entry.get("kind")
        print(f"[client] event kind={entry.get('kind')} performative={performative} summary={entry.get('summary')}")

    proposals: list[NegotiationProposeView] = []

    def on_decision_requested(propose: NegotiationProposeView, session: object) -> None:
        proposals.append(propose)
        print(f"[client] decision-requested round={getattr(session, 'round', '?')}")

    def on_decision_made(
        decision: object,
        propose: NegotiationProposeView,
        session: object,
    ) -> None:
        from shared.models import NegotiationDecision
        from shared.trace import infer_decision_phase

        if not isinstance(decision, NegotiationDecision):
            return
        transcript.record_decision(
            decision,
            propose=propose,
            phase=infer_decision_phase(decision.action),
        )
        print(f"[client] decision-made action={decision.action} round={getattr(session, 'round', '?')}")

    run_id = uuid.uuid4().hex
    trace_spans: list[dict[str, object]] = []
    from shared.langfuse_tracing import LangfuseRunMetadata, create_langfuse_exporter

    langfuse_exporter = create_langfuse_exporter(
        config,
        LangfuseRunMetadata(
            run_id=run_id,
            scenario=scenario,
            conversation_id=run_id,
            user_id="cli",
            language=language,
            mode=mode,
            protocol=protocol,
            transport=transport,
            discovery=config.discovery,
            intent_mode=config.intent_mode,
            model_mode=config.model_mode,
            model_label=config.model_label,
        ),
    )

    def trace_sink(span: dict[str, object]) -> None:
        trace_spans.append(span)
        transcript.record_trace(span)
        if langfuse_exporter is not None:
            langfuse_exporter(span)

    runtime = None
    result = None
    error_text: str | None = None
    from shared.trace_propagation import propagated_trace

    try:
        with (
            log_context(run_id=run_id, role="client"),
            propagated_trace(
                trace_id=(langfuse_exporter.trace_id if langfuse_exporter is not None else ""),
                parent_span_id=(langfuse_exporter.root_span_id if langfuse_exporter is not None else ""),
                run_id=run_id,
                conversation_id=run_id,
                user_id="cli",
            ),
        ):
            runtime = await build_demo_runtime(
                config,
                scenario,
                mode,
                transport,
                discovery or config.discovery,
                protocol=protocol,
                run_id=run_id,
                env_path=env_path,
            )
            result = await runtime.run(
                event_sink=event_sink,
                on_decision_requested=on_decision_requested,
                on_decision_made=on_decision_made,
                trace_sink=trace_sink,
                run_id=run_id,
                user_request=user_request,
            )
        for index, decision in enumerate(result.decisions):
            if index < len(transcript.decisions):
                continue
            propose = proposals[index] if index < len(proposals) else None
            transcript.record_decision(decision, propose=propose)
        transcript.record_final(result)
        print(f"[client] final_state={result.final_state} task_id={result.task_id or '-'}")
        print(f"[client] trace: {_trace_summary(trace_spans)}")
        print(f"[client] run id: {run_id}")
        print(f"[client] server trace: {Path(config.runs_dir) / run_id / 'server-trace.jsonl'}")
        if result.report:
            print(f"[client] report={json.dumps(result.report, ensure_ascii=False)}")
    except Exception as exc:  # noqa: BLE001 - the launcher reports the failure and exits non-zero
        error_text = f"{type(exc).__name__}: {exc}"
        transcript.record_error(error_text)
        print(f"[client] failed: {error_text}")
        return 1
    finally:
        if runtime is not None:
            await runtime.aclose()
        if langfuse_exporter is not None:
            await asyncio.to_thread(langfuse_exporter.finish, result=result, error=error_text)
            langfuse_snapshot = await asyncio.to_thread(langfuse_exporter.snapshot, resolve_url=True)
            print(f"[client] langfuse trace: {langfuse_snapshot.get('url') or langfuse_snapshot.get('traceId')}")
        paths = transcript.write()
        print(f"[client] transcript: {paths.json_path}")
        print(f"[client] transcript: {paths.markdown_path}")
    return 0


def _wait_ui(proc: subprocess.Popen[str]) -> int:
    """Block until the managed Web UI child exits and return its (normalized) exit code."""
    try:
        while proc.poll() is None:
            time.sleep(0.2)
    except KeyboardInterrupt:
        raise
    return 0 if proc.returncode == 0 else 1


def _build_parser(
    default_ui_port: int = DEFAULT_UI_PORT,
    default_ui_host: str = DEFAULT_UI_HOST,
    default_gateway_port: int = 8001,
    values: dict[str, str] | None = None,
    env_file_default: str = "",
) -> argparse.ArgumentParser:
    """Build the CLI parser of the new launcher.

    Args:
        default_ui_port: default of ``--ui-port``, resolved from ``A2AT_SAMPLE_UI_PORT`` by the
            caller (``8080`` when the key is absent).
        default_ui_host: default of ``--ui-host``, resolved from ``A2AT_SAMPLE_UI_HOST`` by the
            caller (``127.0.0.1`` when the key is absent).
        default_gateway_port: default of ``--gateway-port``, resolved from
            ``A2AT_SAMPLE_GATEWAY_PORT`` by the caller (``8001`` when the key is absent).
        values: source ``.env`` values; a shell export wins over them for the defaults that have
            an environment key (precedence: CLI argument > shell > ``.env``).
    """
    env = values or {}
    from protocol.client_runtime import SUPPORTED_MODES
    from shared.config import (
        SUPPORTED_DISCOVERY_MODES,
        SUPPORTED_INTENT_MODES,
        SUPPORTED_MODEL_MODES,
        SUPPORTED_TRANSPORTS,
    )
    from shared.run_constants import BRAND_ORANGE, SUPPORTED_BRANDS, SUPPORTED_LANGUAGES

    from demo.scenario import SUPPORTED_SCENARIO_NAMES

    cli_modes = [mode for mode in SUPPORTED_MODES if mode != "scripted"]
    parser = argparse.ArgumentParser(description="Non-blocking launcher of the RAN energy-saving ADK sample.")
    parser.add_argument(
        "--env-file",
        default=env_file_default,
        metavar="PATH",
        help=(
            "configuration file to load (default: $A2AT_SAMPLE_ENV_FILE, then the sample .env). "
            "Use one file per launch script instead of waiting for the launcher to guess"
        ),
    )
    parser.add_argument(
        "--scenario",
        default="night",
        choices=list(SUPPORTED_SCENARIO_NAMES),
        help="demonstration scenario (default: night)",
    )
    parser.add_argument(
        "--mode",
        default="auto",
        choices=cli_modes,
        help=(
            "client decision mode: auto (ADK agent), confirm (agent recommends, engineer "
            "confirms in the Web UI) or manual (the engineer decides; requires --ui)"
        ),
    )
    parser.add_argument(
        "--approval-gate",
        action="store_true",
        help=(
            "pause the NMS before applying a plan and wait for the engineer's approval in the Web UI (requires --ui)"
        ),
    )
    parser.add_argument(
        "--approval-timeout",
        type=float,
        default=DEFAULT_APPROVAL_TIMEOUT_SECONDS,
        help=(
            "seconds the approval gate waits before treating silence as a rejection "
            f"(default: {DEFAULT_APPROVAL_TIMEOUT_SECONDS:g})"
        ),
    )
    parser.add_argument(
        "--protocol",
        default=_env_str(env, "A2AT_SAMPLE_PROTOCOL_MODE", "") or None,
        choices=[*DEMO_PROTOCOLS, PROTOCOL_A2A_NAME, PROTOCOL_MODE_BOTH],
        help=(
            "interaction protocol: both (one Web UI with the A2A-T/A2A switch; default with --ui), "
            "A2A-T (Task-T + Negotiation-T), or A2A (standard A2A Task INPUT_REQUIRED "
            "continuation, no A2A-T extension). The CLI defaults to A2A-T"
        ),
    )
    parser.add_argument(
        "--transport",
        default=None,
        choices=list(SUPPORTED_TRANSPORTS),
        help=(
            "event transport (default: sse for the local demo and NMS simulator; webhook for a "
            "real --nms run, which uses message:send + push notifications)"
        ),
    )
    parser.add_argument(
        "--webhook-host",
        default=None,
        help=(
            "host the real NMS uses to reach the webhook receiver; use a reachable LAN address "
            "(default: the source .env A2AT_SAMPLE_WEBHOOK_HOST or 127.0.0.1)"
        ),
    )
    parser.add_argument(
        "--discovery",
        default=_env_str(env, "A2AT_SAMPLE_DISCOVERY", "registry"),
        choices=list(SUPPORTED_DISCOVERY_MODES),
        help="server discovery: registry center (default), direct AgentCard, or nms",
    )
    parser.add_argument(
        "--nms",
        action="store_true",
        help=(
            "connect the client to a real vendor NMS: skip the local registry, RAN server, "
            "MIE and OSS processes and use A2AT_SAMPLE_NMS_* from the source .env"
        ),
    )
    parser.add_argument(
        "--nms-sim",
        action="store_true",
        help=(
            "with --nms, start the local NMS simulator and point the client at it instead of "
            "the values in the source .env"
        ),
    )
    parser.add_argument(
        "--nms-sim-port",
        type=int,
        default=18080,
        help="bind port of the local NMS simulator (default: 18080)",
    )
    parser.add_argument(
        "--language",
        default=_env_str(env, "A2AT_LANGUAGE", "en-US"),
        choices=list(SUPPORTED_LANGUAGES),
        help="process-level language of registry, server and client (default: en-US)",
    )
    parser.add_argument(
        "--request",
        default="",
        help=(
            "natural-language request override of the CLI run; required to name a region the "
            "real NMS serves (for example A2ATestRegion) in live mode"
        ),
    )
    parser.add_argument(
        "--model-mode",
        default=_env_str(env, "A2AT_SAMPLE_MODEL_MODE", "offline"),
        choices=list(SUPPORTED_MODEL_MODES),
        help="offline (scripted + fake ADK model) or live (OpenAI-compatible endpoint)",
    )
    parser.add_argument(
        "--intent-mode",
        default=None,
        choices=list(SUPPORTED_INTENT_MODES),
        help=(
            "operator intent handling: processed (business intent layer, default from "
            "A2AT_SAMPLE_INTENT_MODE) or raw (send the operator text directly)"
        ),
    )
    parser.add_argument("--ui", action="store_true", help="serve the Web UI instead of the CLI client")
    parser.add_argument(
        "--ui-mode",
        default="presentation",
        choices=("presentation", "explorer"),
        help=("Web UI variant: presentation (audience-facing demo, default) or explorer (protocol-level Run Explorer)"),
    )
    parser.add_argument(
        "--brand",
        default=_env_str(env, "A2AT_SAMPLE_UI_BRAND", BRAND_ORANGE),
        choices=list(SUPPORTED_BRANDS),
        help=("presentation color theme: orange (reference demo) or huawei (default: A2AT_SAMPLE_UI_BRAND or orange)"),
    )
    parser.add_argument(
        "--ui-port",
        type=int,
        default=default_ui_port,
        help=f"bind port of the Web UI (default: {default_ui_port}; A2AT_SAMPLE_UI_PORT)",
    )
    parser.add_argument(
        "--ui-host",
        default=default_ui_host,
        help=(
            f"bind host of the Web UI (default: {default_ui_host}; A2AT_SAMPLE_UI_HOST). "
            "Use 0.0.0.0 to expose it on all interfaces."
        ),
    )
    parser.add_argument(
        "--server-url",
        default=_env_str(env, "A2AT_SAMPLE_SERVER_URL", ""),
        help=(
            "A2A server base URL the Web UI forwards the MIE approval to; defaults to the "
            "server this launcher starts (A2AT_SAMPLE_SERVER_URL)"
        ),
    )
    parser.add_argument(
        "--allow-insecure-bind",
        action="store_true",
        help=(
            "allow --ui-host on a non-loopback address without A2AT_SAMPLE_AUTH_TOKEN "
            "(trusted-LAN demo only; anyone who can reach the port can start runs)"
        ),
    )
    parser.add_argument(
        "--oss-port",
        type=int,
        default=DEFAULT_OSS_PORT,
        help=f"bind port of the OSS Agent in A2A mode (default: {DEFAULT_OSS_PORT})",
    )
    parser.add_argument(
        "--mie-port",
        type=int,
        default=DEFAULT_MIE_PORT,
        help=f"bind port of the MIE Agent in A2A mode (default: {DEFAULT_MIE_PORT})",
    )
    parser.add_argument(
        "--gateway",
        action="store_true",
        help="also serve the client agent's inbound A2A gateway (Gemini Enterprise path)",
    )
    parser.add_argument(
        "--gateway-port",
        type=int,
        default=default_gateway_port,
        help=f"bind port of the A2A gateway (default: {default_gateway_port}; A2AT_SAMPLE_GATEWAY_PORT)",
    )
    parser.add_argument(
        "--gateway-register",
        action="store_true",
        help="register the A2A gateway AgentCard with the registry center",
    )
    parser.add_argument("--keep-alive", action="store_true", help="leave registry/server running afterwards")
    parser.add_argument("--keep-logs", action="store_true", help="keep the temporary run directory (logs/.env)")
    parser.add_argument("--stop", action="store_true", help="stop a previous --keep-alive run and exit")
    parser.add_argument("--timeout", type=float, default=30.0, help="seconds to wait for each port")
    return parser


def main() -> int:
    """Assemble the run directory, start the background processes and run the client."""
    reconfigure = getattr(sys.stdout, "reconfigure", None)
    if callable(reconfigure):
        try:
            reconfigure(line_buffering=True)
        except ValueError:
            pass

    # ``--env-file`` selects the configuration explicitly; otherwise the first sample .env wins.
    # The raw argv is inspected first because the parser defaults themselves come from that file.
    default_env_file = os.environ.get("A2AT_SAMPLE_ENV_FILE", "").strip()
    explicit_env = _env_file_argument(sys.argv[1:]) or default_env_file
    source_env, env_error = _resolve_source_env(explicit_env)
    if env_error is not None:
        print(f"[run_demo] {env_error}")
        return 2
    source_values = _read_env(source_env) if source_env is not None else {}
    _export_observability_env(source_values)
    parser = _build_parser(
        default_ui_port=_ui_port_default(source_values),
        default_ui_host=_ui_host_default(source_values),
        default_gateway_port=_gateway_port_default(source_values),
        values=source_values,
        env_file_default=default_env_file,
    )
    args = parser.parse_args()
    if args.protocol:
        # ``a2a`` is the user-facing name of the standard-A2A form; the rest of the launcher keeps
        # the historical internal value.
        args.protocol = normalize_protocol_name(args.protocol)
    else:
        # A UI launch advertises both request forms by default. A CLI run stays A2A-T, and the
        # gateway keeps its single A2A-T form.
        args.protocol = PROTOCOL_MODE_BOTH if args.ui and not args.gateway else PROTOCOL_A2A_T
    if args.env_file:
        source_env, env_error = _resolve_source_env(args.env_file)
        if env_error is not None:
            print(f"[run_demo] {env_error}")
            return 2
    if args.transport is None:
        args.transport = "sse" if args.nms_sim or not args.nms else "webhook"

    from shared.logging_setup import configure_logging

    configure_logging()

    if args.stop:
        state = _load_state()
        pids = _state_pids(state)
        ui_port = state.get("ui_port")
        ui_listening = isinstance(ui_port, int) and _port_open(ui_port)
        if not pids and not ui_listening:
            PID_FILE.unlink(missing_ok=True)
            print("[run_demo] nothing to stop (no recorded background processes)")
            return 0
        stopped = _stop_pids(pids)
        # Older launchers served the UI in their own foreground process and never recorded it, so
        # its port is the only trace left. Reclaim it only when the command line proves ownership.
        reclaimed = 0
        if isinstance(ui_port, int) and _port_open(ui_port):
            reclaimed = _kill_port_listener(ui_port)
            if reclaimed:
                print(f"[run_demo] reclaimed {reclaimed} orphaned Web UI process(es) on port {ui_port}")
            else:
                print(
                    f"[run_demo] port {ui_port} is still in use by a process this launcher does not "
                    f"recognize; free it manually or start with another --ui-port"
                )
        PID_FILE.unlink(missing_ok=True)
        print(
            f"[run_demo] stopped {stopped + reclaimed} process(es) "
            f"({len(pids)} recorded; already-exited processes are not counted)"
        )
        return 0

    if args.mode in ("manual", "confirm") and not args.ui:
        parser.error(f"--mode {args.mode} requires --ui (the decision lives in the Web UI)")
    if args.protocol == PROTOCOL_MODE_BOTH and not args.ui:
        parser.error("--protocol both requires --ui (the protocol is chosen per run in the page)")
    if args.protocol == PROTOCOL_MODE_BOTH and args.gateway:
        parser.error("--gateway is not supported with --protocol both")
    if args.protocol == PROTOCOL_PURE:
        from demo.scenario import PURE_PROTOCOL_SCENARIOS

        if args.scenario not in PURE_PROTOCOL_SCENARIOS:
            parser.error(f"--protocol a2a supports only {', '.join(PURE_PROTOCOL_SCENARIOS)}")
        if args.gateway:
            parser.error("--gateway is not supported with --protocol a2a")
    if args.nms_sim and not args.nms:
        parser.error("--nms-sim requires --nms")
    if args.nms:
        if not args.nms_sim and args.transport != "webhook":
            parser.error(
                "a real --nms run requires --transport webhook (message:send + push notifications); "
                "use --nms-sim for the local SSE simulator"
            )
        if args.gateway:
            parser.error("--nms is not supported with --gateway")
        if args.approval_gate:
            parser.error(
                "--approval-gate is the local MIE review; a real NMS owns execution approval. "
                "Use --mode confirm for the send-time intent review instead."
            )
        if args.discovery != "registry":
            parser.error("--nms selects nms discovery; do not pass --discovery")

    python = sys.executable
    if not _preflight(python, source_env):
        return 1

    os.environ["A2AT_LANGUAGE"] = args.language
    RUN_DIR.mkdir(parents=True, exist_ok=True)
    state = _load_state()
    existing = _state_pids(state)
    registry_host = _env_str(source_values, "REGISTRY_CENTER_HOST", "127.0.0.1")
    registry_port = _env_int(source_values, "REGISTRY_CENTER_PORT", DEFAULT_REGISTRY_PORT)
    server_host = _env_str(source_values, "A2AT_SAMPLE_HOST", "127.0.0.1")
    server_port = _env_int(source_values, "A2AT_SAMPLE_PORT", DEFAULT_SERVER_PORT)
    webhook_port = _env_int(source_values, "A2AT_SAMPLE_WEBHOOK_PORT", 9000)
    # A registered callback must name the port this process actually binds. Process env, the
    # source .env and a launch script's exports can disagree, and the NMS would push to the wrong
    # place while the receiver still looks healthy, so fail before warming the model.
    allow_callback_port_mismatch = _env_truthy(
        source_values, "A2AT_SAMPLE_ALLOW_CALLBACK_PORT_MISMATCH"
    )
    if allow_callback_port_mismatch:
        # The receiver warns from its own process env; keep the child view consistent with the
        # launcher's accept decision even when the flag came from the source .env.
        os.environ["A2AT_SAMPLE_ALLOW_CALLBACK_PORT_MISMATCH"] = "1"
    base_public_url = _env_str(source_values, "A2AT_SAMPLE_WEBHOOK_PUBLIC_URL", "")
    if args.transport == "webhook":
        error = _callback_url_port_error(
            "A2AT_SAMPLE_WEBHOOK_PUBLIC_URL",
            base_public_url,
            webhook_port,
            allow_mismatch=allow_callback_port_mismatch,
        )
        if error is not None:
            print(error)
            return 2
        if args.ui and webhook_port == args.ui_port:
            print(
                "[run_demo] A2AT_SAMPLE_WEBHOOK_PORT must differ from --ui-port; "
                "the UI process binds both."
            )
            return 2
        if (
            args.protocol in (PROTOCOL_PURE, PROTOCOL_MODE_BOTH)
            and webhook_port == args.oss_port
        ):
            print(
                "[run_demo] A2AT_SAMPLE_WEBHOOK_PORT must differ from --oss-port; "
                "the OSS Agent binds its HTTP port and its callback receiver on the same host."
            )
            return 2
    # In the one-instance NMS mode both the UI (A2A-T) and the OSS Agent (standard A2A) keep a
    # callback receiver. They are different processes, so the OSS hop defaults to the next port;
    # A2AT_SAMPLE_OSS_WEBHOOK_PORT overrides it for deployments where +1 collides or NAT remaps it.
    oss_webhook_env: dict[str, str] = {}
    oss_webhook_keys: tuple[str, ...] = ()
    if args.protocol == PROTOCOL_MODE_BOTH and args.nms:
        # The OSS Agent owns this receiver. Never let the launcher's UI callback keys reach it:
        # process env wins over the per-run .env, so an inherited UI public URL would silently
        # replace the OSS address while the process still binds the OSS port.
        oss_webhook_keys = (
            "A2AT_SAMPLE_WEBHOOK_PORT",
            "A2AT_SAMPLE_WEBHOOK_PUBLIC_URL",
            "A2AT_SAMPLE_OSS_WEBHOOK_PORT",
            "A2AT_SAMPLE_OSS_WEBHOOK_PUBLIC_URL",
        )
        oss_public_url = _env_str(source_values, "A2AT_SAMPLE_OSS_WEBHOOK_PUBLIC_URL", "")
        oss_webhook_port = _env_int(
            source_values, "A2AT_SAMPLE_OSS_WEBHOOK_PORT", webhook_port + 1
        )
        real_nms = not args.nms_sim
        if real_nms:
            for label, other_port in (
                ("A2AT_SAMPLE_WEBHOOK_PORT", webhook_port),
                ("--ui-port", args.ui_port),
                ("--oss-port", args.oss_port),
            ):
                if oss_webhook_port == other_port:
                    print(
                        f"[run_demo] A2AT_SAMPLE_OSS_WEBHOOK_PORT must differ from {label}; "
                        "the UI and the OSS Agent need separate receivers."
                    )
                    return 2
        if real_nms and not oss_public_url:
            print(
                "[run_demo] --protocol both --nms needs two reachable callbacks: set "
                "A2AT_SAMPLE_OSS_WEBHOOK_PUBLIC_URL for the OSS Agent (the UI keeps "
                "A2AT_SAMPLE_WEBHOOK_PUBLIC_URL). It is never inherited from the UI."
            )
            return 2
        if real_nms:
            error = _callback_url_port_error(
                "A2AT_SAMPLE_OSS_WEBHOOK_PUBLIC_URL",
                oss_public_url,
                oss_webhook_port,
                allow_mismatch=allow_callback_port_mismatch,
            )
            if error is not None:
                print(error)
                return 2
        oss_webhook_env["A2AT_SAMPLE_WEBHOOK_PORT"] = str(oss_webhook_port)
        if oss_public_url:
            # Unconditional when present: this key must always override the inherited UI value.
            oss_webhook_env["A2AT_SAMPLE_WEBHOOK_PUBLIC_URL"] = oss_public_url
    reserved_ports = {args.ui_port} if args.nms else {registry_port, server_port}
    if args.ui:
        reserved_ports.add(args.ui_port)
    if args.nms_sim:
        reserved_ports.add(args.nms_sim_port)
        # The simulator is its own process on its own port; it cannot share with any process this
        # launcher starts (the UI, the OSS hop or either callback receiver).
        sim_conflicts = [
            (label, other)
            for label, other in (
                ("--ui-port", args.ui_port if args.ui else None),
                ("--oss-port", args.oss_port if args.protocol in (PROTOCOL_PURE, PROTOCOL_MODE_BOTH) else None),
                ("A2AT_SAMPLE_WEBHOOK_PORT", webhook_port if args.transport == "webhook" else None),
                (
                    "A2AT_SAMPLE_OSS_WEBHOOK_PORT",
                    int(oss_webhook_env["A2AT_SAMPLE_WEBHOOK_PORT"])
                    if oss_webhook_env.get("A2AT_SAMPLE_WEBHOOK_PORT")
                    else None,
                ),
            )
            if other is not None and other == args.nms_sim_port
        ]
        if sim_conflicts:
            labels = ", ".join(label for label, _ in sim_conflicts)
            parser.error(f"--nms-sim-port {args.nms_sim_port} collides with {labels}")
    if args.gateway and args.gateway_port in reserved_ports:
        parser.error(f"--gateway-port {args.gateway_port} collides with the registry/server/UI port")

    sim_settings: dict[str, str] = {}
    nms_overrides: dict[str, str] = {}
    if args.nms_sim:
        sim_settings = _nms_sim_settings(source_values, args.nms_sim_port)
        nms_overrides = {
            "A2AT_SAMPLE_NMS_HOST": "127.0.0.1",
            "A2AT_SAMPLE_NMS_SCHEME": "http",
            "A2AT_SAMPLE_NMS_A2A_PORT": str(args.nms_sim_port),
            "A2AT_SAMPLE_NMS_TOKEN_PORT": str(args.nms_sim_port),
            "A2AT_SAMPLE_NMS_TOKEN_USER": "snbitestDefaultNorth",
            "A2AT_SAMPLE_NMS_TOKEN_VALUE": "Changeme_123",
            "A2AT_SAMPLE_NMS_VERIFY_TLS": "false",
            "A2AT_SAMPLE_NMS_REGION": sim_settings["A2AT_SAMPLE_NMS_SIM_REGION"],
            "A2AT_SAMPLE_NMS_SITES": sim_settings["A2AT_SAMPLE_NMS_SIM_SITES"]
            or "NRES_XCS_6102937,LTE_26441_LNR_OCL",
            "A2AT_SAMPLE_NMS_WIRE_LOG": "1",
        }

    env_path = _write_run_env(
        source=source_env,
        language=args.language,
        model_mode=args.model_mode,
        discovery="nms" if args.nms else args.discovery,
        transport=args.transport,
        webhook_port=webhook_port,
        webhook_host=args.webhook_host,
        server_host=server_host,
        server_port=server_port,
        registry_host=registry_host,
        registry_port=registry_port,
        ui_port=args.ui_port,
        gateway_port=args.gateway_port,
        gateway_register=args.gateway_register,
        protocol=args.protocol,
        oss_port=args.oss_port,
        mie_port=args.mie_port,
        intent_mode=args.intent_mode,
        extra=nms_overrides,
    )

    from shared.config import load_config

    try:
        resolved_config = load_config(env_path=env_path)
    except ValueError as exc:
        print(f"[run_demo] {exc}")
        return 1
    _warm_live_model(resolved_config)
    print(
        f"[run_demo] resolved: model_mode={resolved_config.model_mode}, "
        f"model={resolved_config.model_label}, language={resolved_config.language}, "
        f"provider={resolved_config.adk_model_provider}, discovery={resolved_config.discovery}, "
        f"transport={resolved_config.transport}, intent_mode={resolved_config.intent_mode}, "
        f"nms_region={resolved_config.nms_region or '-'}, "
        f"nms_sites={','.join(resolved_config.nms_sites) or '-'}"
    )

    if args.nms:
        # A real NMS run starts no local server, so the registry/server reuse decision does not
        # apply. A previous local demo's PID record is left untouched.
        reuse = REUSE_START
    else:
        # Drop a stale PID file only when its processes are gone (both ports closed).
        if existing and not _port_open(registry_port) and not _port_open(server_port):
            PID_FILE.unlink(missing_ok=True)
            state = {}
            existing = []

        # A followed run is only reusable when its recorded configuration matches the request; the
        # server owns the model mode and language, so a mismatch stops it instead of silently
        # keeping live LiteLlm behind an offline UI.
        reuse = _server_reuse_decision(
            port_open=_port_open(server_port),
            state=state,
            model_mode=args.model_mode,
            language=args.language,
            protocol_mode=args.protocol,
            ui=args.ui,
            trace_url=_trace_url(args.ui_port) if args.ui else "",
            ui_port=args.ui_port if args.ui else None,
            approval_gate=args.approval_gate,
            approval_timeout=args.approval_timeout,
        )
        if reuse == REUSE_REFUSE:
            print(
                f"[run_demo] port {server_port} is already in use by a process this launcher does "
                f"not manage (no PID record); cannot verify its model_mode/language. Free the port "
                f"or run --stop, then retry."
            )
            return 1
        if reuse == REUSE_RESTART:
            recorded_mode = state.get("model_mode", "unknown")
            recorded_language = state.get("language", "unknown")
            recorded_target = state.get("trace_url") or "CLI file"
            requested_target = _trace_url(args.ui_port) if args.ui else "CLI file"
            print(
                f"[run_demo] recorded server configuration does not match this run "
                f"(recorded model_mode={recorded_mode}, language={recorded_language}, "
                f"trace={recorded_target}; requested model_mode={args.model_mode}, "
                f"language={args.language}, trace={requested_target}); stopping the recorded "
                f"processes and starting fresh"
            )
            _stop_pids(existing)
            close_ports = [registry_port, server_port]
            if args.gateway:
                close_ports.append(args.gateway_port)
            if not _wait_ports_closed(close_ports):
                print("[run_demo] recorded processes did not release their ports; free them and retry")
                PID_FILE.unlink(missing_ok=True)
                return 1
            PID_FILE.unlink(missing_ok=True)
            state = {}
            existing = []

    managed: list[int] = list(existing)
    started_pids: list[int] = []
    processes: dict[str, subprocess.Popen[str]] = {}
    log_handles: list[TextIO] = []
    followers: list[threading.Thread] = []
    pumps: list[threading.Thread] = []
    stop_events: list[threading.Event] = []

    def attach_follower(log_path: Path, *, from_end: bool = False) -> None:
        """Tail ``log_path`` to the terminal until the run's stop event is set."""
        stop_event = threading.Event()
        stop_events.append(stop_event)
        follower = threading.Thread(
            target=_follow_log,
            args=(log_path, stop_event),
            kwargs={"from_end": from_end},
            daemon=True,
        )
        follower.start()
        followers.append(follower)

    def start(
        name: str,
        module: str,
        port: int,
        extra_env: dict[str, str] | None = None,
        *,
        cli_args: list[str] | None = None,
        probe_host: str = "127.0.0.1",
        reuse: bool = True,
        drop_env: tuple[str, ...] = (),
    ) -> str | None:
        """Return ``started``/``reused``, or ``None`` on failure.

        ``reuse=False`` is used for processes whose behavior depends on per-run options the reuse
        record does not cover (the Web UI, and the OSS Agent with its callback ports): an occupied
        port means "restart our own child of this module" or "refuse".

        ``drop_env`` removes inherited keys before the child's ``extra_env`` is applied.
        """
        if _port_open(port, probe_host):
            if not reuse:
                stale = [
                    pid
                    for pid in _listener_pids(port)
                    if _is_module_child(pid, module) or _is_ui_child(pid)
                ]
                if not stale:
                    print(
                        f"[run_demo] {name} port {port} is already in use by a process this "
                        f"launcher cannot restart safely; run --stop to reclaim a previous demo "
                        f"process, free the port, or choose another port"
                    )
                    return None
                print(f"[run_demo] {name} port {port} is held by a stale {name} process; restarting it")
                _stop_pids(stale)
                if not _wait_ports_closed([port], timeout=args.timeout):
                    print(f"[run_demo] {name} port {port} was not released; free it and retry")
                    return None
                managed[:] = [pid for pid in managed if pid not in stale]
            elif not managed:
                print(f"[run_demo] port {port} is already in use by another process; free it first")
                return None
            else:
                print(f"[run_demo] {name} already listening on {port} (reusing recorded process)")
                attach_follower(RUN_DIR / f"{name}.log", from_end=True)
                return "reused"
        print(f"[run_demo] starting {name} on {port} ...")
        command = [python, "-m", module, *(cli_args or [])]
        log_path = RUN_DIR / f"{name}.log"
        log_handle = log_path.open("w", encoding="utf-8")
        log_handles.append(log_handle)
        pump: threading.Thread | None = None
        if args.keep_alive:
            proc = subprocess.Popen(
                command,
                cwd=str(RUN_DIR),
                env=_child_env(args.language, extra_env, exclude=drop_env),
                stdout=log_handle,
                stderr=subprocess.STDOUT,
                text=True,
            )
            attach_follower(log_path)
        else:
            proc = subprocess.Popen(
                command,
                cwd=str(RUN_DIR),
                env=_child_env(args.language, extra_env, exclude=drop_env),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
            )
            assert proc.stdout is not None
            pump = threading.Thread(target=_pump_pipe, args=(proc.stdout, log_handle), daemon=True)
            pump.start()
            pumps.append(pump)
        if not _wait_port(port, args.timeout, proc, probe_host):
            print(f"[run_demo] {name} failed to start")
            if pump is not None:
                pump.join(timeout=1.0)
            _show_tail(log_path)
            _stop_pids(list(dict.fromkeys(started_pids + [proc.pid])))
            return None
        started_pids.append(proc.pid)
        managed.append(proc.pid)
        processes[name] = proc
        return "started"

    return_code = 0
    persisted = False
    try:
        server_trace_env: dict[str, str] = {}
        # The OSS Agent is a separate process: without this env its NMS token/send/poll spans never
        # reach the UI, and a pure + --nms run looks like the local three-hop demo.
        oss_trace_env: dict[str, str] = {}
        if args.protocol in (PROTOCOL_PURE, PROTOCOL_MODE_BOTH) and args.ui:
            recorded_token = str(state.get("trace_token") or "") if reuse == REUSE_REUSE else ""
            oss_trace_env = _server_trace_env(
                ui=True,
                ui_port=args.ui_port,
                trace_token=recorded_token or None,
            )
            os.environ["A2AT_SAMPLE_TRACE_TOKEN"] = oss_trace_env["A2AT_SAMPLE_TRACE_TOKEN"]
        if args.nms_sim:
            print(
                f"[nms-sim] transport={args.transport}, "
                f"carrier={sim_settings['A2AT_SAMPLE_NMS_SIM_NEGOTIATION_CARRIER']}, "
                f"kinds={sim_settings['A2AT_SAMPLE_NMS_SIM_NEGOTIATION_KINDS']}, "
                f"region={sim_settings['A2AT_SAMPLE_NMS_SIM_REGION']}, "
                f"window={sim_settings['A2AT_SAMPLE_NMS_SIM_WINDOW']}"
            )
            if start("nms-sim", "demo.nms_sim.main", args.nms_sim_port, sim_settings) is None:
                return 1
        if not args.nms:
            if start("registry", REGISTRY_MODULE, registry_port) is None:
                return 1
            # Reusing a compatible keep-alive server must also reuse its trace token: the UI
            # validates that token, so a fresh one would silently drop every server span.
            recorded_token = str(state.get("trace_token") or "") if reuse == REUSE_REUSE else ""
            server_trace_env = _server_trace_env(
                ui=args.ui,
                ui_port=args.ui_port,
                trace_token=(oss_trace_env.get("A2AT_SAMPLE_TRACE_TOKEN") or recorded_token or None),
                approval_gate=args.approval_gate,
                approval_timeout=args.approval_timeout,
            )
            if args.ui:
                # The UI process checks the same token the server sends; the URL is only read by
                # the server process, but keeping both keys together documents the pairing.
                os.environ["A2AT_SAMPLE_TRACE_TOKEN"] = server_trace_env["A2AT_SAMPLE_TRACE_TOKEN"]
            server_env = dict(server_trace_env)
            server_env["A2AT_SAMPLE_PROTOCOL_MODE"] = args.protocol
            needs_pure_stack = args.protocol in (PROTOCOL_PURE, PROTOCOL_MODE_BOTH)
            if needs_pure_stack:
                mie_env = {"A2AT_SAMPLE_MIE_PORT": str(args.mie_port)}
                if start("mie", MIE_MODULE, args.mie_port, mie_env) is None:
                    return 1
                server_env["A2AT_SAMPLE_MIE_URL"] = f"http://{server_host}:{args.mie_port}"
                if args.protocol == PROTOCOL_MODE_BOTH:
                    # Standard A2A keeps its MIE review; the A2A-T extension pauses only when the
                    # operator explicitly asked for the server-side gate.
                    server_env["A2AT_SAMPLE_PURE_APPROVAL_GATE"] = "1"
                    if args.approval_gate:
                        server_env["A2AT_SAMPLE_APPROVAL_GATE"] = "1"
                        server_env["A2AT_SAMPLE_APPROVAL_TIMEOUT_SECONDS"] = str(args.approval_timeout)
                elif args.ui or args.approval_gate:
                    server_env["A2AT_SAMPLE_APPROVAL_GATE"] = "1"
                    server_env["A2AT_SAMPLE_APPROVAL_TIMEOUT_SECONDS"] = str(args.approval_timeout)
            if start("server", SERVER_MODULE, server_port, server_env) is None:
                return 1
            if needs_pure_stack:
                oss_env = {
                    # The OSS hop is direct in the local topology; pin it so a sourced .env in the
                    # launch shell cannot turn this process into an NMS client.
                    "A2AT_SAMPLE_DISCOVERY": "direct",
                    "A2AT_SAMPLE_TRANSPORT": args.transport,
                    "A2AT_SAMPLE_RAN_URL": f"http://{server_host}:{server_port}",
                    "A2AT_SAMPLE_OSS_HOST": server_host,
                    "A2AT_SAMPLE_OSS_PORT": str(args.oss_port),
                    **oss_trace_env,
                    **oss_webhook_env,
                }
                if (
                    start(
                        "oss",
                        OSS_MODULE,
                        args.oss_port,
                        oss_env,
                        reuse=False,
                        drop_env=oss_webhook_keys,
                    )
                    is None
                ):
                    return 1
            elif args.gateway and start("gateway", GATEWAY_MODULE, args.gateway_port) is None:
                return 1
        elif args.protocol in (PROTOCOL_PURE, PROTOCOL_MODE_BOTH):
            # The NMS deployment keeps the OSS Agent but drops the local RAN Agent and MIE: the OSS
            # Agent forwards the normalized request to the vendor NMS over plain A2A. ``both`` also
            # serves A2A-T in the UI, which talks to the NMS directly.
            oss_env = {
                # ``shared.config`` prefers the process environment over the per-run .env, so a
                # deployment shell that sourced the source .env would otherwise let a stale
                # discovery/transport redirect the OSS hop to a local RAN Agent.
                "A2AT_SAMPLE_DISCOVERY": "nms",
                "A2AT_SAMPLE_TRANSPORT": args.transport,
                "A2AT_SAMPLE_RAN_URL": f"http://{server_host}:{server_port}",
                "A2AT_SAMPLE_OSS_HOST": server_host,
                "A2AT_SAMPLE_OSS_PORT": str(args.oss_port),
                **oss_trace_env,
                **oss_webhook_env,
            }
            if (
                start(
                    "oss",
                    OSS_MODULE,
                    args.oss_port,
                    oss_env,
                    reuse=False,
                    drop_env=oss_webhook_keys,
                )
                is None
            ):
                return 1

        ui_proc: subprocess.Popen[str] | None = None
        if args.ui:
            print(
                f"[run_demo] starting Web UI (scenario selectable, mode manual/auto, "
                f"transport={args.transport}, language={args.language}, model_mode={args.model_mode}) ..."
            )
            # ``shared.config`` prefers the process environment over the per-run .env, so a launch
            # shell that sourced the source .env (``set -a``) would otherwise override the hop the
            # launcher selected. Pin the deployment discovery and the direct endpoint so a stale
            # export cannot point a standard-A2A run at a local RAN Agent.
            pure_stack = args.protocol in (PROTOCOL_PURE, PROTOCOL_MODE_BOTH)
            ui_topology_env = {
                "A2AT_SAMPLE_DISCOVERY": "nms" if args.nms else args.discovery,
                "A2AT_SAMPLE_DIRECT_SERVER_URL": (
                    f"http://{server_host}:{args.oss_port if pure_stack else server_port}"
                ),
                "A2AT_SAMPLE_NMS_SIM": "1" if args.nms_sim else "0",
            }
            pure_nms = args.nms and args.protocol == "pure"
            if pure_nms:
                # The engineer client talks to the local OSS Agent; the OSS Agent owns the NMS hop.
                ui_server_url = args.server_url or f"http://{server_host}:{args.oss_port}"
            elif args.nms:
                ui_server_url = args.server_url or resolved_config.nms_a2a_base
            else:
                ui_server_url = args.server_url or (
                    f"http://{server_host}:{args.oss_port}"
                    if args.protocol == "pure"
                    else f"http://{server_host}:{server_port}"
                )
            mie_url = (
                f"http://{server_host}:{args.mie_port}"
                if args.protocol in (PROTOCOL_PURE, PROTOCOL_MODE_BOTH) and not args.nms
                else None
            )
            # The UI -> OSS hop is local plain A2A; only the OSS Agent uses the NMS webhook.
            ui_transport = "sse" if pure_nms else args.transport
            ui_args = [
                "--env-file",
                str(env_path),
                "--host",
                args.ui_host,
                "--port",
                str(args.ui_port),
                "--scenario",
                args.scenario,
                "--mode",
                args.mode,
                "--transport",
                ui_transport,
                "--ui-mode",
                args.ui_mode,
                "--brand",
                args.brand,
                "--server-url",
                ui_server_url,
                "--protocol",
                args.protocol,
            ]
            if pure_nms:
                ui_args += ["--discovery", "direct"]
            if args.nms:
                # The UI derives each protocol's discovery/transport default: A2A-T talks to the
                # vendor NMS, standard A2A goes through the OSS Agent.
                ui_args.append("--nms")
            if not args.nms and (args.approval_gate or args.protocol == PROTOCOL_PURE):
                # In ``both`` the pure review is driven by mieEnabled, not by the A2A-T server
                # gate, so the page must not advertise a gate the A2A-T request form will not hold.
                ui_args += ["--approval-gate", "--approval-timeout", str(args.approval_timeout)]
            if args.allow_insecure_bind:
                ui_args.append("--allow-insecure-bind")
            if mie_url is not None:
                ui_args += ["--mie-url", mie_url]
            if (
                start(
                    "ui",
                    UI_MODULE,
                    args.ui_port,
                    cli_args=ui_args,
                    probe_host=_probe_host(args.ui_host),
                    reuse=False,
                    extra_env=ui_topology_env,
                )
                is None
            ):
                return 1
            ui_proc = processes.get("ui")

        # Record every live child, including the UI. Reused processes keep their previous PID;
        # dead PIDs from an older record are dropped instead of being carried forward.
        _save_state(
            [pid for pid in dict.fromkeys(managed) if _pid_alive(pid)],
            model_mode=args.model_mode,
            language=args.language,
            protocol_mode=args.protocol,
            trace_token=server_trace_env.get("A2AT_SAMPLE_TRACE_TOKEN", "")
            or oss_trace_env.get("A2AT_SAMPLE_TRACE_TOKEN", ""),
            trace_url=_trace_url(args.ui_port) if args.ui else "",
            ui_port=args.ui_port if args.ui else None,
            gateway_port=args.gateway_port if args.gateway else None,
            approval_gate=args.approval_gate,
            approval_timeout=args.approval_timeout,
        )
        persisted = True

        if ui_proc is not None:
            return_code = _wait_ui(ui_proc)
            if return_code != 0:
                # A concurrent ``--stop`` kills the UI child and removes the state file; that is a
                # requested shutdown, not a failure of this run.
                if args.keep_alive:
                    deadline = time.monotonic() + 2.0
                    while time.monotonic() < deadline and PID_FILE.exists():
                        time.sleep(0.1)
                if args.keep_alive and not PID_FILE.exists():
                    print("[run_demo] Web UI stopped by --stop")
                    return_code = 0
                else:
                    print(f"[run_demo] Web UI exited with code {ui_proc.returncode}")
        else:
            print(
                f"[run_demo] running client (scenario={args.scenario}, mode={args.mode}, "
                f"transport={args.transport}, discovery={resolved_config.discovery}, "
                f"language={args.language}, "
                f"model_mode={args.model_mode}) ..."
            )
            pure_nms = args.nms and args.protocol == "pure"
            return_code = asyncio.run(
                _run_client_foreground(
                    env_path=env_path,
                    scenario=args.scenario,
                    mode=args.mode,
                    transport="sse" if pure_nms else args.transport,
                    language=args.language,
                    protocol=args.protocol,
                    discovery="direct" if pure_nms else None,
                    user_request=args.request or None,
                )
            )
        return return_code
    except KeyboardInterrupt:
        print("\n[run_demo] interrupted")
        return 130
    finally:
        ui_pid = processes["ui"].pid if "ui" in processes else None
        if args.keep_alive:
            # The UI belongs to this launcher run, not to the long-lived registry/server pair:
            # stop it with the launcher and keep only the processes that stay alive in the state.
            if ui_pid is not None:
                _stop_pids([ui_pid])
            if persisted and PID_FILE.exists():
                current = _load_state()
                remaining = [pid for pid in _state_pids(current) if pid != ui_pid and _pid_alive(pid)]
                if remaining:
                    current["pids"] = remaining
                    _write_state(current)
                    print(f"\n[run_demo] --keep-alive set; stop later with: {python} {Path(__file__).name} --stop")
                else:
                    PID_FILE.unlink(missing_ok=True)
        else:
            stopped = _stop_pids(managed)
            PID_FILE.unlink(missing_ok=True)
            print(f"\n[run_demo] stopped {stopped} background process(es)")
        for pump in pumps:
            pump.join(timeout=2.0)
        if followers:
            time.sleep(0.3)
        for stop_event in stop_events:
            stop_event.set()
        for follower in followers:
            follower.join(timeout=1.0)
        for handle in log_handles:
            handle.close()
        if not args.keep_alive:
            if args.keep_logs:
                print(f"[run_demo] logs kept at {RUN_DIR}")
            else:
                shutil.rmtree(RUN_DIR, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
