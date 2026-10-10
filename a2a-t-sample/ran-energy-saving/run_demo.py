#!/usr/bin/env python3
"""Cross-platform, non-blocking launcher for the ran-energy-saving (Task-T) sample.

The registry center and the A2A server each bind a port; started in the foreground they would
block the terminal. This launcher starts them as **background subprocesses**, waits for their
ports, runs the client in the foreground, then stops the background processes (unless
``--keep-alive`` is given).

Both sides' output is streamed to the terminal **live and interleaved**: the background processes
keep writing their logs to the run directory, and a follower thread echoes every appended line to
the terminal as it happens, so the server prints when the server runs and the client prints when the
client runs.

The language defaults to English and is applied without touching the shared
``a2a-t-sample/.env``: the launcher writes a temporary ``.env`` (a copy of the shared one with
``A2AT_LANGUAGE`` overridden) into its own run directory and points the child processes at that
working directory. Temporary files are removed on exit (see ``--keep-logs`` / ``--keep-alive``).

Works on Windows, macOS and Linux. Run it with the same interpreter that has the sample
dependencies installed (e.g. the repository ``.venv``).

Usage:
    python run_demo.py                      # English, streams the energy-saving steps, then exits
    python run_demo.py --language zh-CN
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
import threading
import time
from pathlib import Path
from typing import TextIO

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


def _wait_port(port: int, timeout: float, proc: subprocess.Popen | None = None) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if proc is not None and proc.poll() is not None:
            return False  # the child already exited (e.g. missing .env); fail fast
        if _port_open(port):
            return True
        time.sleep(0.3)
    return False


def _child_env() -> dict[str, str]:
    env = os.environ.copy()
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = str(SRC_DIR) + (os.pathsep + existing if existing else "")
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUNBUFFERED"] = "1"
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
    except (ValueError, OSError):
        return []
    if not isinstance(data, dict):
        return []
    try:
        return [int(pid) for pid in data.get("pids", [])]
    except (TypeError, ValueError):
        return []


def _save_pids(pids: list[int]) -> None:
    """Persist the managed PIDs atomically so a concurrent ``--stop`` never reads a partial file."""
    tmp_path = PID_FILE.with_suffix(".json.tmp")
    tmp_path.write_text(json.dumps({"pids": pids}), encoding="utf-8")
    os.replace(tmp_path, PID_FILE)


def _show_tail(path: Path) -> None:
    if path.exists():
        print(f"--- {path} (tail) ---")
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines()[-20:]:
            print(line)


def _follow_log(path: Path, stop: threading.Event, *, from_end: bool = False) -> None:
    """Echo lines appended to ``path`` to the terminal until ``stop`` is set.

    The child processes write their output to a log file (with ``PYTHONUNBUFFERED`` so every line is
    flushed immediately); this follower thread tails that file and prints new lines as they arrive,
    which interleaves the background processes' output with the foreground client's output in
    real time. With ``from_end`` the follower skips existing content (used when reusing a process
    whose log already holds a previous run's output).
    """
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
    """Blocking-read a child's merged stdout pipe, echoing each line to the terminal and log file.

    Unlike the file-follower (which tails a file and can lag), a blocking read on the pipe returns
    each line as soon as the child writes it, so the background processes' output reaches the
    terminal in near real time.
    """
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
    parser.add_argument("--keep-alive", action="store_true", help="leave registry/server running afterwards")
    parser.add_argument("--keep-logs", action="store_true", help="keep the temporary run directory (logs/.env)")
    parser.add_argument("--stop", action="store_true", help="stop a previous --keep-alive run and exit")
    parser.add_argument("--timeout", type=float, default=30.0, help="seconds to wait for each port")
    args = parser.parse_args()

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
    started_pids: list[int] = []
    log_handles: list = []
    followers: list[threading.Thread] = []
    pumps: list[threading.Thread] = []
    stop_events: list[threading.Event] = []

    def attach_follower(log_path: Path, *, from_end: bool = False) -> None:
        """Tail ``log_path`` to the terminal until the run's stop event is set."""
        stop_event = threading.Event()
        stop_events.append(stop_event)
        follower = threading.Thread(
            target=_follow_log, args=(log_path, stop_event), kwargs={"from_end": from_end}, daemon=True
        )
        follower.start()
        followers.append(follower)

    def start(name: str, module: str, port: int) -> str | None:
        """Return "started"/"reused", or None on failure."""
        if _port_open(port):
            if not existing:
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
            # --keep-alive leaves the child running after the launcher exits, so the child must own a
            # file handle (a pipe would break once the launcher exits); tail the file for display.
            proc = subprocess.Popen(
                [python, "-m", module],
                cwd=str(RUN_DIR),
                env=_child_env(),
                stdout=log_handle,
                stderr=subprocess.STDOUT,
            )
            attach_follower(log_path)
        else:
            # Normal run: read the child's merged output straight off a pipe, so the echo is
            # near-real-time instead of lagging behind a file tail.
            proc = subprocess.Popen(
                [python, "-m", module],
                cwd=str(RUN_DIR),
                env=_child_env(),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
            )
            pump = threading.Thread(target=_pump_pipe, args=(proc.stdout, log_handle), daemon=True)
            pump.start()
            pumps.append(pump)
        if not _wait_port(port, args.timeout, proc):
            print(f"[run_demo] {name} failed to start")
            if pump is not None:
                pump.join(timeout=1.0)  # let the pump drain the child's diagnostics before tailing
            _show_tail(log_path)
            # Only stop what this run started; never the reused keep-alive processes.
            _stop_pids(list(dict.fromkeys(started_pids + [proc.pid])))
            return None
        started_pids.append(proc.pid)
        managed.append(proc.pid)
        return "started"

    client_returncode = 0
    stopped = 0
    persisted = False
    try:
        if start("registry", "registry.registry_main", REGISTRY_PORT) is None:
            return 1
        if start("server", "server.server_main", SERVER_PORT) is None:
            return 1

        _save_pids(list(dict.fromkeys(managed)))
        persisted = True

        print(f"[run_demo] running client (language = {args.language}) ...")
        client_returncode = subprocess.run(
            [python, "-m", "client.client_main"],
            cwd=str(RUN_DIR),
            env=_child_env(),
            check=False,
        ).returncode
        return client_returncode
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
        # Pipe pumps end on their own once the children are stopped (EOF on the pipe).
        for pump in pumps:
            pump.join(timeout=2.0)
        if followers:
            time.sleep(0.3)  # let the file followers echo the final lines
        for stop_event in stop_events:
            stop_event.set()
        for follower in followers:
            follower.join(timeout=1.0)
        for handle in log_handles:
            handle.close()
        if not args.keep_alive:
            print(f"\n[run_demo] stopped {stopped} background process(es)")
            if args.keep_logs:
                print(f"[run_demo] logs kept at {RUN_DIR}")
            else:
                shutil.rmtree(RUN_DIR, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
