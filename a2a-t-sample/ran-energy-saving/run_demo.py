#!/usr/bin/env python3
"""Cross-platform, non-blocking launcher for the ran-energy-saving (Task-T) sample.

The registry center and the A2A server each bind a port; started in the foreground they would
block the terminal. This launcher starts them as **background subprocesses**, waits for their
ports, runs the client in the foreground, then stops the background processes (unless
``--keep-alive`` is given).

The language defaults to English and is applied without touching the shared
``a2a-t-sample/.env``: the launcher writes a temporary ``.env`` (a copy of the shared one with
``A2AT_LANGUAGE`` overridden) into its own run directory and points the child processes at that
working directory. Temporary files are removed on exit (see ``--keep-logs`` / ``--keep-alive``).

Works on Windows, macOS and Linux. Run it with the same interpreter that has the sample
dependencies installed (e.g. the repository ``.venv``).

Usage:
    python run_demo.py                      # English, streams the energy-saving steps, then exits
    python run_demo.py --language zh-CN
    python run_demo.py --max-artifacts 2
    python run_demo.py --keep-alive         # leave registry/server running; stop with --stop
    python run_demo.py --stop               # stop a previous --keep-alive run
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

CASE_DIR = Path(__file__).resolve().parent
SAMPLE_DIR = CASE_DIR.parent
REPO_ROOT = SAMPLE_DIR.parent
SRC_DIR = CASE_DIR / "src"
ENV_FILE = SAMPLE_DIR / ".env"
#: Everything the launcher writes (run .env, subprocess logs, pids) lives under here.
RUN_DIR = Path(tempfile.gettempdir()) / "a2at-ran-energy-saving"
PID_FILE = RUN_DIR / "pids.json"

REGISTRY_PORT = 5001
SERVER_PORT = 8000


def _port_open(port: int, host: str = "127.0.0.1") -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.3)
        return sock.connect_ex((host, port)) == 0


def _wait_port(port: int, timeout: float, proc: subprocess.Popen[bytes] | None = None) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if proc is not None and proc.poll() is not None:
            return False  # the child already exited (e.g. missing .env); fail fast
        if _port_open(port):
            return True
        time.sleep(0.3)
    return False


def _child_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    env = os.environ.copy()
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = str(SRC_DIR) + (os.pathsep + existing if existing else "")
    env["PYTHONIOENCODING"] = "utf-8"
    if extra:
        env.update(extra)
    return env


def _write_run_env(language: str) -> None:
    """Write the per-run ``.env`` (shared .env copy with A2AT_LANGUAGE overridden)."""
    text = ENV_FILE.read_text(encoding="utf-8")
    if re.search(r"(?m)^A2AT_LANGUAGE=", text):
        text = re.sub(r"(?m)^A2AT_LANGUAGE=.*$", f"A2AT_LANGUAGE={language}", text)
    else:
        text = text.rstrip("\n") + f"\nA2AT_LANGUAGE={language}\n"
    env_path = RUN_DIR / ".env"
    env_path.write_text(text, encoding="utf-8", newline="\n")
    try:  # best effort: keep the copied config (may carry an API key) owner-only
        os.chmod(env_path, 0o600)
    except OSError:
        pass


def _stop_pids(pids: list[int]) -> int:
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


def _load_pids() -> list[int]:
    if not PID_FILE.exists():
        return []
    try:
        data = json.loads(PID_FILE.read_text(encoding="utf-8"))
        return [int(pid) for pid in data.get("pids", [])]
    except (ValueError, OSError):
        return []


def _save_pids(pids: list[int]) -> None:
    PID_FILE.write_text(json.dumps({"pids": pids}), encoding="utf-8")


def _show_tail(path: Path) -> None:
    if path.exists():
        print(f"--- {path} (tail) ---")
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines()[-20:]:
            print(line)


def _print_server_summary() -> None:
    log_path = RUN_DIR / "server.out.log"
    if not log_path.exists():
        return
    lines = [
        line
        for line in log_path.read_text(encoding="utf-8", errors="replace").splitlines()
        if line.startswith("[server]") and "llm-" not in line
    ]
    if lines:
        print("\n[run_demo] server-side A2A-T SDK activity (from server.out.log):")
        for line in lines:
            print(f"  {line}")


def _preflight(python: str) -> bool:
    if not ENV_FILE.exists():
        print(f"[run_demo] missing {ENV_FILE}; copy env.example to .env first")
        return False
    check = subprocess.run(
        [python, "-c", "import a2a, a2a_t"],
        cwd=str(SAMPLE_DIR),
        env=_child_env(),
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    if check.returncode != 0:
        print(f"[run_demo] '{python}' is missing the sample dependencies.")
        print(f"           Run:  cd {SAMPLE_DIR}  &&  uv pip install -r requirements.txt")
        return False
    return True


def main() -> int:
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except (AttributeError, ValueError):
        pass

    parser = argparse.ArgumentParser(description="Non-blocking launcher for the ran-energy-saving sample.")
    parser.add_argument(
        "--language",
        default="en-US",
        choices=["en-US", "zh-CN"],
        help="run language (default: en-US)",
    )
    parser.add_argument("--max-artifacts", type=int, default=4, help="max artifacts the client receives")
    parser.add_argument("--keep-alive", action="store_true", help="leave registry/server running afterwards")
    parser.add_argument("--keep-logs", action="store_true", help="keep the temporary run directory (logs/.env)")
    parser.add_argument("--stop", action="store_true", help="stop a previous --keep-alive run and exit")
    parser.add_argument("--timeout", type=float, default=30.0, help="seconds to wait for each port")
    args = parser.parse_args()
    if args.max_artifacts < 1:
        parser.error("--max-artifacts must be >= 1")

    if args.stop:
        pids = _load_pids()
        if not pids:
            print("[run_demo] nothing to stop (no recorded background processes)")
            return 0
        stopped = _stop_pids(pids)
        PID_FILE.unlink(missing_ok=True)
        print(f"[run_demo] stopped {stopped}/{len(pids)} recorded background processes")
        return 0

    python = sys.executable
    if not _preflight(python):
        return 1

    RUN_DIR.mkdir(parents=True, exist_ok=True)
    existing = _load_pids()
    # Drop a stale PID file only when its processes are gone (both ports closed).
    if existing and not _port_open(REGISTRY_PORT) and not _port_open(SERVER_PORT):
        PID_FILE.unlink(missing_ok=True)
        existing = []

    _write_run_env(args.language)
    managed: list[int] = list(existing)
    log_handles = []

    def start(name: str, module: str, port: int) -> str | None:
        """Return "started"/"reused", or None on failure."""
        if _port_open(port):
            if not existing:
                print(f"[run_demo] port {port} is already in use by another process; free it first")
                return None
            print(f"[run_demo] {name} already listening on {port} (reusing recorded process)")
            return "reused"
        print(f"[run_demo] starting {name} on {port} ...")
        out = (RUN_DIR / f"{name}.out.log").open("w", encoding="utf-8")
        err = (RUN_DIR / f"{name}.err.log").open("w", encoding="utf-8")
        log_handles.extend([out, err])
        proc = subprocess.Popen(
            [python, "-m", module],
            cwd=str(RUN_DIR),
            env=_child_env(),
            stdout=out,
            stderr=err,
        )
        if not _wait_port(port, args.timeout, proc):
            print(f"[run_demo] {name} failed to start")
            _show_tail(RUN_DIR / f"{name}.err.log")
            _stop_pids(managed + [proc.pid])
            return None
        managed.append(proc.pid)
        return "started"

    client_returncode = 0
    try:
        if start("registry", "agentcard_example.registry_main", REGISTRY_PORT) is None:
            return 1
        if start("server", "server_example.server_main", SERVER_PORT) is None:
            return 1

        _save_pids(list(dict.fromkeys(managed)))

        print(f"[run_demo] running client (language = {args.language}, max artifacts = {args.max_artifacts}) ...")
        client_returncode = subprocess.run(
            [python, "-m", "client_example.client_main"],
            cwd=str(RUN_DIR),
            env=_child_env({"A2AT_SAMPLE_MAX_ARTIFACTS": str(args.max_artifacts)}),
            check=False,
        ).returncode

        _print_server_summary()
        return client_returncode
    except KeyboardInterrupt:
        print("\n[run_demo] interrupted")
        return 130
    finally:
        for handle in log_handles:
            handle.close()
        if args.keep_alive:
            print(f"\n[run_demo] --keep-alive set; stop later with: {python} {Path(__file__).name} --stop")
        else:
            stopped = _stop_pids(managed)
            PID_FILE.unlink(missing_ok=True)
            print(f"\n[run_demo] stopped {stopped} background process(es)")
            if args.keep_logs:
                print(f"[run_demo] logs kept at {RUN_DIR}")
            else:
                shutil.rmtree(RUN_DIR, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
