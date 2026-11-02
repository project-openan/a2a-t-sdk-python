#!/usr/bin/env python3
"""Three-process launcher of the RAN energy-saving A2A-T sample (ADK client only).

The registry center and the A2A server each bind a port; started in the foreground they would
block the terminal. This launcher starts them as **background subprocesses** (new layout:
``registry.registry_main`` and ``protocol.server_app``), waits for their ports, then runs the client
in the foreground — or serves the Web UI with ``--ui`` — and finally stops the background processes
unless ``--keep-alive`` is given.

Both background processes stream their output to the terminal live and interleaved (through a pipe
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
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from pathlib import Path
from typing import TextIO

from shared.run_constants import DEMO_PROTOCOLS, PROTOCOL_A2A_T

#: The demo lives one level below the sample directory that owns ``runs/`` and the SDK path.
CASE_DIR = Path(__file__).resolve().parent.parent
SAMPLE_DIR = CASE_DIR.parent
REPO_ROOT = SAMPLE_DIR.parent
RUN_DIR = Path(tempfile.gettempdir()) / "a2at-ran-energy-saving-adk"
PID_FILE = RUN_DIR / "pids.json"
TASK_DB_PATH = CASE_DIR / "runs" / "task-status.sqlite3"
CLIENT_DB_PATH = CASE_DIR / "runs" / "client-state.sqlite3"
GATEWAY_TASK_DB_PATH = CASE_DIR / "runs" / "gateway-task-status.sqlite3"

#: Source ``.env`` candidates, in resolution order.
ENV_CANDIDATES = (CASE_DIR / ".env", SAMPLE_DIR / ".env", SAMPLE_DIR / "env.example")

REGISTRY_MODULE = "demo.registry.registry_main"
SERVER_MODULE = "protocol.server_app"
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


def _wait_port(port: int, timeout: float, proc: subprocess.Popen[str] | None = None) -> bool:
    """Wait until a port accepts connections, failing fast when the child exits."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if proc is not None and proc.poll() is not None:
            return False
        if _port_open(port):
            return True
        time.sleep(0.3)
    return False


def _source_env_path() -> Path | None:
    """Return the first existing source ``.env`` (or ``env.example``)."""
    for candidate in ENV_CANDIDATES:
        if candidate.exists():
            return candidate
    return None


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


def _env_int(values: dict[str, str], key: str, default: int) -> int:
    """Resolve one integer value with a readable failure."""
    raw = values.get(key) or os.environ.get(key) or str(default)
    try:
        return int(raw)
    except ValueError as exc:
        raise SystemExit(f"[run_demo] invalid integer for {key}: {raw}") from exc


def _ui_port_default(values: dict[str, str]) -> int:
    """Return the ``--ui-port`` default: ``A2AT_SAMPLE_UI_PORT``, else ``8080``."""
    return _env_int(values, "A2AT_SAMPLE_UI_PORT", DEFAULT_UI_PORT)


def _ui_host_default(values: dict[str, str]) -> str:
    """Return the ``--ui-host`` default: ``A2AT_SAMPLE_UI_HOST``, else ``127.0.0.1``."""
    return values.get("A2AT_SAMPLE_UI_HOST") or os.environ.get("A2AT_SAMPLE_UI_HOST") or DEFAULT_UI_HOST


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
        getattr(config, "planner_model", "")
        or str(getattr(config, "adk_model_name", "")).removeprefix("openai/")
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


def _child_env(language: str, extra: dict[str, str] | None = None) -> dict[str, str]:
    """Return the environment of the child processes (sample root on ``PYTHONPATH``)."""
    env = os.environ.copy()
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
    approval_timeout: float = 240.0,
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
    usage = (
        f"prompt={prompt} completion={completion} total={total}"
        if measured
        else "n/a (scripted)"
    )
    return f"llm={len(llm)} tool={len(tools)} tokens={usage}"


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
    registry_host: str,
    registry_port: int,
    ui_port: int,
    gateway_port: int = 8001,
    gateway_register: bool = False,
    protocol: str = PROTOCOL_A2A_T,
    oss_port: int = 8001,
    mie_port: int = 8002,
) -> Path:
    """Write the per-run ``.env`` and return its path."""
    values = _read_env(source) if source is not None else {}
    direct_port = oss_port if protocol == "pure" else server_port
    # The pure engineer client must discover the OSS Agent, not the RAN Agent. The OSS card is
    # not registered under the RAN card's name, so direct discovery of its URL is the only path.
    client_discovery = "direct" if protocol == "pure" else discovery
    values.update(
        {
            "A2AT_LANGUAGE": language,
            "A2AT_SAMPLE_MODEL_MODE": model_mode,
            "A2AT_SAMPLE_DISCOVERY": client_discovery,
            "A2AT_SAMPLE_TRANSPORT": transport,
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
            "A2AT_SAMPLE_TASK_DB": str(TASK_DB_PATH),
            "A2AT_SAMPLE_CLIENT_DB": str(CLIENT_DB_PATH),
            "A2AT_SAMPLE_GATEWAY_TASK_DB": str(GATEWAY_TASK_DB_PATH),
            "A2AT_SAMPLE_GATEWAY_HOST": server_host,
            "A2AT_SAMPLE_GATEWAY_PORT": str(gateway_port),
            "A2AT_SAMPLE_GATEWAY_REGISTER": "1" if gateway_register else "0",
            "REGISTRY_CENTER_HOST": registry_host,
            "REGISTRY_CENTER_PORT": str(registry_port),
        }
    )
    text = "\n".join(f"{key}={value}" for key, value in values.items()) + "\n"
    env_path = RUN_DIR / ".env"
    env_path.write_text(text, encoding="utf-8", newline="\n")
    try:  # best effort: keep the copied config (may carry an API key) owner-only
        os.chmod(env_path, 0o600)
    except OSError:
        pass
    return env_path


def _stop_pids(pids: list[int]) -> int:
    """Terminate the recorded background processes."""
    stopped = 0
    for pid in pids:
        try:
            if os.name == "nt":
                subprocess.run(
                    ["taskkill", "/PID", str(pid), "/T", "/F"],
                    check=False,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            else:
                os.kill(pid, 15)
            stopped += 1
        except (ProcessLookupError, PermissionError, OSError):
            pass
    return stopped


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
    trace_token: str = "",
    trace_url: str = "",
    ui_port: int | None = None,
    gateway_port: int | None = None,
    approval_gate: bool = False,
    approval_timeout: float = 240.0,
) -> None:
    """Persist the managed PIDs and their run metadata atomically.

    A concurrent ``--stop`` never reads a partial file; a later launcher can tell whether a
    listening server was started with the requested model mode/language and trace target. The file
    carries the local trace token, so it is owner-only like the SDK ``.env``.
    """
    state: dict[str, object] = {
        "pids": pids,
        "model_mode": model_mode,
        "language": language,
        "trace_token": trace_token,
        "trace_url": trace_url,
        "ui_port": ui_port,
        "gateway_port": gateway_port,
        "approval_gate": approval_gate,
        "approval_timeout": approval_timeout,
    }
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
    ui: bool,
    trace_url: str = "",
    ui_port: int | None = None,
    approval_gate: bool = False,
    approval_timeout: float = 240.0,
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
    if bool(state.get("approval_gate")) != approval_gate:
        return REUSE_RESTART
    if approval_gate and float(state.get("approval_timeout") or 240.0) != approval_timeout:
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
            "import a2a, a2a_t, google.adk, langgraph, langchain_openai, "
            "sse_starlette, starlette, uvicorn",
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

    def trace_sink(span: dict[str, object]) -> None:
        trace_spans.append(span)
        transcript.record_trace(span)

    runtime = None
    try:
        with log_context(run_id=run_id, role="client"):
            runtime = await build_demo_runtime(
                config,
                scenario,
                mode,
                transport,
                config.discovery,
                protocol=protocol,
                env_path=env_path,
            )
            result = await runtime.run(
                event_sink=event_sink,
                on_decision_requested=on_decision_requested,
                on_decision_made=on_decision_made,
                trace_sink=trace_sink,
                run_id=run_id,
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
        print(f"[client] server trace: {CASE_DIR / 'runs' / run_id / 'server-trace.jsonl'}")
        if result.report:
            print(f"[client] report={json.dumps(result.report, ensure_ascii=False)}")
    except Exception as exc:  # noqa: BLE001 - the launcher reports the failure and exits non-zero
        transcript.record_error(f"{type(exc).__name__}: {exc}")
        print(f"[client] failed: {type(exc).__name__}: {exc}")
        return 1
    finally:
        if runtime is not None:
            await runtime.aclose()
        paths = transcript.write()
        print(f"[client] transcript: {paths.json_path}")
        print(f"[client] transcript: {paths.markdown_path}")
    return 0


def _run_ui(
    *,
    env_path: Path,
    ui_port: int,
    scenario: str,
    mode: str,
    transport: str,
    ui_mode: str = "presentation",
    host: str = "127.0.0.1",
    server_url: str = "",
    approval_gate: bool = False,
    approval_timeout: float = 240.0,
    allow_insecure_bind: bool = False,
    protocol: str = PROTOCOL_A2A_T,
    mie_url: str | None = None,
) -> int:
    """Serve the Web UI of the client process until interrupted."""
    import uvicorn
    from protocol.client_store import SqliteClientStore
    from shared.config import load_config
    from shared.http_security import is_loopback
    from shared.logging_setup import configure_logging

    from demo.catalog import DemoRunCatalog
    from demo.web.app import create_app

    configure_logging()
    config = load_config(env_path=env_path)
    client_store = (
        SqliteClientStore(config.client_db_path)
        if config.client_db_path is not None
        else None
    )
    try:
        app = create_app(
            config,
            env_path=env_path,
            catalog=DemoRunCatalog(),
            client_store=client_store,
            memory_turns=config.client_memory_turns,
            default_scenario=scenario,
            default_mode=mode,
            default_transport=transport,
            bind_host=host,
            ui_mode=ui_mode,
            server_url=server_url or None,
            approval_gate=approval_gate,
            approval_timeout_seconds=approval_timeout,
            allow_insecure_bind=allow_insecure_bind,
            protocol=protocol,
            mie_url=mie_url,
            # The server trace emitter flushes in batches; aggregate the persisted metrics after
            # the same short window the SSE stream uses to forward its last spans.
            metrics_grace_seconds=2.0,
        )
    except ValueError as exc:
        print(f"[run_demo] {exc}")
        return 2
    trace_enabled = bool(os.environ.get("A2AT_SAMPLE_TRACE_TOKEN", "").strip())
    print(
        f"[run_demo] Web UI on http://{host}:{ui_port} (ui_mode={ui_mode}) "
        f"(language={config.language}, model_mode={config.model_mode} [{config.model_label}], "
        f"provider={config.adk_model_provider}, "
        f"defaults: scenario={scenario}, mode={mode}, transport={transport}, "
        f"approval_gate={approval_gate}, "
        f"server trace ingestion: {'enabled' if trace_enabled else 'disabled (no local token)'})"
    )
    if not is_loopback(host):
        try:
            lan_ip = socket.gethostbyname(socket.gethostname())
        except OSError:
            lan_ip = "<your-lan-ip>"
        if config.auth_token:
            print(
                f"[run_demo] share on the LAN: http://{lan_ip}:{ui_port}/?token="
                "<A2AT_SAMPLE_AUTH_TOKEN>"
            )
        else:
            print(
                "[run_demo] WARNING: the Web UI is reachable on the LAN without a bearer "
                "token; anyone who can reach the port can start runs. Use only on a trusted "
                f"network. Share: http://{lan_ip}:{ui_port}/"
            )
    try:
        uvicorn.run(app, host=host, port=ui_port, log_level="info")
    except KeyboardInterrupt:
        print("\n[run_demo] Web UI stopped")
        return 130
    return 0


def _build_parser(
    default_ui_port: int = DEFAULT_UI_PORT,
    default_ui_host: str = DEFAULT_UI_HOST,
    default_gateway_port: int = 8001,
) -> argparse.ArgumentParser:
    """Build the CLI parser of the new launcher.

    Args:
        default_ui_port: default of ``--ui-port``, resolved from ``A2AT_SAMPLE_UI_PORT`` by the
            caller (``8080`` when the key is absent).
        default_ui_host: default of ``--ui-host``, resolved from ``A2AT_SAMPLE_UI_HOST`` by the
            caller (``127.0.0.1`` when the key is absent).
        default_gateway_port: default of ``--gateway-port``, resolved from
            ``A2AT_SAMPLE_GATEWAY_PORT`` by the caller (``8001`` when the key is absent).
    """
    from protocol.client_runtime import SUPPORTED_MODES
    from shared.config import SUPPORTED_DISCOVERY_MODES, SUPPORTED_MODEL_MODES, SUPPORTED_TRANSPORTS
    from shared.run_constants import SUPPORTED_LANGUAGES

    from demo.scenario import SUPPORTED_SCENARIO_NAMES

    cli_modes = [mode for mode in SUPPORTED_MODES if mode != "scripted"]
    parser = argparse.ArgumentParser(description="Non-blocking launcher of the RAN energy-saving ADK sample.")
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
            "pause the NMS before applying a plan and wait for the engineer's approval in the "
            "Web UI (requires --ui)"
        ),
    )
    parser.add_argument(
        "--approval-timeout",
        type=float,
        default=240.0,
        help="seconds the approval gate waits before treating silence as a rejection (default: 240)",
    )
    parser.add_argument(
        "--protocol",
        default=PROTOCOL_A2A_T,
        choices=list(DEMO_PROTOCOLS),
        help=(
            "interaction protocol: a2a-t (Task-T + Negotiation-T, default) or pure (standard "
            "A2A Task INPUT_REQUIRED continuation, no A2A-T extension)"
        ),
    )
    parser.add_argument(
        "--transport",
        default="sse",
        choices=list(SUPPORTED_TRANSPORTS),
        help="event transport (default: sse)",
    )
    parser.add_argument(
        "--discovery",
        default="registry",
        choices=list(SUPPORTED_DISCOVERY_MODES),
        help="server discovery: registry center (default) or direct AgentCard",
    )
    parser.add_argument(
        "--language",
        default="en-US",
        choices=list(SUPPORTED_LANGUAGES),
        help="process-level language of registry, server and client (default: en-US)",
    )
    parser.add_argument(
        "--model-mode",
        default="offline",
        choices=list(SUPPORTED_MODEL_MODES),
        help="offline (scripted + fake ADK model) or live (OpenAI-compatible endpoint)",
    )
    parser.add_argument("--ui", action="store_true", help="serve the Web UI instead of the CLI client")
    parser.add_argument(
        "--ui-mode",
        default="presentation",
        choices=("presentation", "explorer"),
        help=(
            "Web UI variant: presentation (audience-facing demo, default) or explorer "
            "(protocol-level Run Explorer)"
        ),
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
        default=os.environ.get("A2AT_SAMPLE_SERVER_URL", ""),
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
        help=f"bind port of the OSS Agent in pure mode (default: {DEFAULT_OSS_PORT})",
    )
    parser.add_argument(
        "--mie-port",
        type=int,
        default=DEFAULT_MIE_PORT,
        help=f"bind port of the MIE Agent in pure mode (default: {DEFAULT_MIE_PORT})",
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

    source_env = _source_env_path()
    source_values = _read_env(source_env) if source_env is not None else {}
    parser = _build_parser(
        default_ui_port=_ui_port_default(source_values),
        default_ui_host=_ui_host_default(source_values),
        default_gateway_port=_gateway_port_default(source_values),
    )
    args = parser.parse_args()

    from shared.logging_setup import configure_logging

    configure_logging()

    if args.stop:
        pids = _load_pids()
        if not pids:
            print("[run_demo] nothing to stop (no recorded background processes)")
            return 0
        stopped = _stop_pids(pids)
        PID_FILE.unlink(missing_ok=True)
        print(f"[run_demo] stopped {stopped}/{len(pids)} recorded background processes")
        return 0

    if args.mode in ("manual", "confirm") and not args.ui:
        parser.error(
            f"--mode {args.mode} requires --ui (the decision lives in the Web UI)"
        )
    if args.protocol == "pure":
        from demo.scenario import PURE_PROTOCOL_SCENARIOS

        if args.scenario not in PURE_PROTOCOL_SCENARIOS:
            parser.error(
                f"--protocol pure supports only {', '.join(PURE_PROTOCOL_SCENARIOS)}"
            )
        if args.gateway:
            parser.error("--gateway is not supported with --protocol pure")

    python = sys.executable
    if not _preflight(python, source_env):
        return 1

    os.environ["A2AT_LANGUAGE"] = args.language
    RUN_DIR.mkdir(parents=True, exist_ok=True)
    state = _load_state()
    existing = _state_pids(state)
    registry_host = source_values.get("REGISTRY_CENTER_HOST") or "127.0.0.1"
    registry_port = _env_int(source_values, "REGISTRY_CENTER_PORT", DEFAULT_REGISTRY_PORT)
    server_host = source_values.get("A2AT_SAMPLE_HOST") or "127.0.0.1"
    server_port = _env_int(source_values, "A2AT_SAMPLE_PORT", DEFAULT_SERVER_PORT)
    webhook_port = _env_int(source_values, "A2AT_SAMPLE_WEBHOOK_PORT", 9000)
    reserved_ports = {registry_port, server_port}
    if args.ui:
        reserved_ports.add(args.ui_port)
    if args.gateway and args.gateway_port in reserved_ports:
        parser.error(
            f"--gateway-port {args.gateway_port} collides with the registry/server/UI port"
        )

    env_path = _write_run_env(
        source=source_env,
        language=args.language,
        model_mode=args.model_mode,
        discovery=args.discovery,
        transport=args.transport,
        webhook_port=webhook_port,
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
    )

    from shared.config import load_config

    resolved_config = load_config(env_path=env_path)
    _warm_live_model(resolved_config)
    print(
        f"[run_demo] resolved: model_mode={resolved_config.model_mode}, "
        f"model={resolved_config.model_label}, language={resolved_config.language}, "
        f"provider={resolved_config.adk_model_provider}, discovery={resolved_config.discovery}, "
        f"transport={resolved_config.transport}"
    )

    # Drop a stale PID file only when its processes are gone (both ports closed).
    if existing and not _port_open(registry_port) and not _port_open(server_port):
        PID_FILE.unlink(missing_ok=True)
        state = {}
        existing = []

    # A followed run is only reusable when its recorded configuration matches the request; the
    # server owns the model mode and language, so a mismatch stops it instead of silently keeping
    # live LiteLlm behind an offline UI.
    reuse = _server_reuse_decision(
        port_open=_port_open(server_port),
        state=state,
        model_mode=args.model_mode,
        language=args.language,
        ui=args.ui,
        trace_url=_trace_url(args.ui_port) if args.ui else "",
        ui_port=args.ui_port if args.ui else None,
        approval_gate=args.approval_gate,
        approval_timeout=args.approval_timeout,
    )
    if reuse == REUSE_REFUSE:
        print(
            f"[run_demo] port {server_port} is already in use by a process this launcher does not "
            f"manage (no PID record); cannot verify its model_mode/language. Free the port or run "
            f"--stop, then retry."
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
    ) -> str | None:
        """Return ``started``/``reused``, or ``None`` on failure."""
        if _port_open(port):
            if not managed:
                print(f"[run_demo] port {port} is already in use by another process; free it first")
                return None
            print(f"[run_demo] {name} already listening on {port} (reusing recorded process)")
            attach_follower(RUN_DIR / f"{name}.log", from_end=True)
            return "reused"
        print(f"[run_demo] starting {name} on {port} ...")
        log_path = RUN_DIR / f"{name}.log"
        log_handle = log_path.open("w", encoding="utf-8")
        log_handles.append(log_handle)
        pump: threading.Thread | None = None
        if args.keep_alive:
            proc = subprocess.Popen(
                [python, "-m", module],
                cwd=str(RUN_DIR),
                env=_child_env(args.language, extra_env),
                stdout=log_handle,
                stderr=subprocess.STDOUT,
                text=True,
            )
            attach_follower(log_path)
        else:
            proc = subprocess.Popen(
                [python, "-m", module],
                cwd=str(RUN_DIR),
                env=_child_env(args.language, extra_env),
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
        if not _wait_port(port, args.timeout, proc):
            print(f"[run_demo] {name} failed to start")
            if pump is not None:
                pump.join(timeout=1.0)
            _show_tail(log_path)
            _stop_pids(list(dict.fromkeys(started_pids + [proc.pid])))
            return None
        started_pids.append(proc.pid)
        managed.append(proc.pid)
        return "started"

    return_code = 0
    persisted = False
    try:
        if start("registry", REGISTRY_MODULE, registry_port) is None:
            return 1
        # Reusing a compatible keep-alive server must also reuse its trace token: the UI validates
        # that token, so a fresh one would silently drop every server span.
        recorded_token = str(state.get("trace_token") or "") if reuse == REUSE_REUSE else ""
        server_trace_env = _server_trace_env(
            ui=args.ui,
            ui_port=args.ui_port,
            trace_token=recorded_token or None,
            approval_gate=args.approval_gate,
            approval_timeout=args.approval_timeout,
        )
        if args.ui:
            # The UI process checks the same token the server sends; the URL is only read by the
            # server process, but keeping both keys together documents the paired configuration.
            os.environ["A2AT_SAMPLE_TRACE_TOKEN"] = server_trace_env["A2AT_SAMPLE_TRACE_TOKEN"]
        server_env = dict(server_trace_env)
        server_env["A2AT_SAMPLE_PROTOCOL_MODE"] = args.protocol
        if args.protocol == "pure":
            mie_env = {"A2AT_SAMPLE_MIE_PORT": str(args.mie_port)}
            if start("mie", MIE_MODULE, args.mie_port, mie_env) is None:
                return 1
            server_env["A2AT_SAMPLE_MIE_URL"] = (
                f"http://{server_host}:{args.mie_port}"
            )
            if args.ui or args.approval_gate:
                server_env["A2AT_SAMPLE_APPROVAL_GATE"] = "1"
                server_env["A2AT_SAMPLE_APPROVAL_TIMEOUT_SECONDS"] = str(
                    args.approval_timeout
                )
        if start("server", SERVER_MODULE, server_port, server_env) is None:
            return 1
        if args.protocol == "pure":
            oss_env = {
                "A2AT_SAMPLE_RAN_URL": f"http://{server_host}:{server_port}",
                "A2AT_SAMPLE_OSS_HOST": server_host,
                "A2AT_SAMPLE_OSS_PORT": str(args.oss_port),
            }
            if start("oss", OSS_MODULE, args.oss_port, oss_env) is None:
                return 1
        elif args.gateway and start("gateway", GATEWAY_MODULE, args.gateway_port) is None:
            return 1
        _save_state(
            list(dict.fromkeys(managed)),
            model_mode=args.model_mode,
            language=args.language,
            trace_token=server_trace_env.get("A2AT_SAMPLE_TRACE_TOKEN", ""),
            trace_url=_trace_url(args.ui_port) if args.ui else "",
            ui_port=args.ui_port if args.ui else None,
            gateway_port=args.gateway_port if args.gateway else None,
            approval_gate=args.approval_gate,
            approval_timeout=args.approval_timeout,
        )
        persisted = True

        if args.ui:
            print(
                f"[run_demo] starting Web UI (scenario selectable, mode manual/auto, "
                f"transport={args.transport}, language={args.language}, model_mode={args.model_mode}) ..."
            )
            ui_server_url = args.server_url or (
                f"http://{server_host}:{args.oss_port}"
                if args.protocol == "pure"
                else f"http://{server_host}:{server_port}"
            )
            mie_url = (
                f"http://{server_host}:{args.mie_port}"
                if args.protocol == "pure"
                else None
            )
            return_code = _run_ui(
                env_path=env_path,
                ui_port=args.ui_port,
                host=args.ui_host,
                scenario=args.scenario,
                mode=args.mode,
                transport=args.transport,
                ui_mode=args.ui_mode,
                server_url=ui_server_url,
                approval_gate=args.approval_gate or args.protocol == "pure",
                approval_timeout=args.approval_timeout,
                allow_insecure_bind=args.allow_insecure_bind,
                protocol=args.protocol,
                mie_url=mie_url,
            )
        else:
            print(
                f"[run_demo] running client (scenario={args.scenario}, mode={args.mode}, "
                f"transport={args.transport}, discovery={args.discovery}, language={args.language}, "
                f"model_mode={args.model_mode}) ..."
            )
            return_code = asyncio.run(
                _run_client_foreground(
                    env_path=env_path,
                    scenario=args.scenario,
                    mode=args.mode,
                    transport=args.transport,
                    language=args.language,
                    protocol=args.protocol,
                )
            )
        return return_code
    except KeyboardInterrupt:
        print("\n[run_demo] interrupted")
        return 130
    finally:
        if args.keep_alive:
            if persisted:
                print(f"\n[run_demo] --keep-alive set; stop later with: {python} {Path(__file__).name} --stop")
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
