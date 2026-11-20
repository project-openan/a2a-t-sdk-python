"""Start, inspect and stop one isolated AI evaluation instance.

The launcher (``run_demo.py``) owns process management; this module only translates a
:class:`~eval.osp.ai.types.Profile` into one launcher invocation and observes the result. Two
rules drive the code:

* The bind ports are command-line arguments, never environment keys, while the webhook receivers
  and the model/NMS endpoints are environment keys. ``launcher_argv`` and ``sanitized_env`` are the
  only translation point.
* An instance owns a ``run_dir``. A marker file records the owner so ``status``/``down`` never
  operate on the manual testers' instance by accident. This is what keeps the AI run from
  cancelling or killing another session's processes and Tasks.

All state observation is best effort: a missing marker, missing PID file or unreachable UI is
reported in the returned value, not raised. Only ``up`` raises :class:`InstanceError`, because an
instance that never became ready must not be reported as usable.
"""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from eval.osp.ai import nms
from eval.osp.ai.http import HttpError, JsonClient
from eval.osp.ai.nms import CANCEL_RECHECK_SECONDS
from eval.osp.ai.profile import (
    launcher_argv,
    port_conflicts,
    port_in_use,
    sanitized_env,
    validate_profile,
)
from eval.osp.ai.types import Profile

#: Marker schema written into ``run_dir``; bump it when the ownership contract changes.
MARKER_SCHEMA: str = "osp-ai-instance/v1"
#: Name of the marker file inside ``run_dir``.
MARKER_FILE: str = "ai-profile.json"
#: Launcher stdout/stderr file inside ``run_dir``.
LAUNCHER_LOG: str = "ai-launcher.log"

_READY_PATH: str = "/api/meta"
_POLL_INTERVAL_SECONDS: float = 0.5
_STATUS_PROBE_TIMEOUT_SECONDS: float = 2.0
_PORT_POLL_INTERVAL_SECONDS: float = 0.25
_LOG_TAIL_LINES: int = 40

#: Handles are kept for the lifetime of the process so an orphaned launcher is not garbage
#: collected while it is still supervising its children.
_LAUNCHERS: list[subprocess.Popen[bytes]] = []


class InstanceError(RuntimeError):
    """An instance could not be started (pre-flight failure or readiness timeout)."""


@dataclass(frozen=True)
class InstanceState:
    """The observable state of one AI evaluation instance."""

    running: bool
    ui_ready: bool
    pids: tuple[int, ...]
    logs: tuple[Path, ...]
    detail: str = ""

    def to_dict(self) -> dict[str, object]:
        """Return the JSON-friendly representation used by ``instance status``."""
        return {
            "running": self.running,
            "uiReady": self.ui_ready,
            "pids": list(self.pids),
            "logs": [str(path) for path in self.logs],
            "detail": self.detail,
        }


def up(
    profile: Profile,
    *,
    wait_seconds: float = 120.0,
    log: Callable[[str], None] = print,
) -> InstanceState:
    """Start the launcher for ``profile`` and wait until ``GET /api/meta`` answers 200.

    Raises:
        InstanceError: when the profile is invalid, a bind port is already taken, the launcher
            cannot be spawned, or the UI does not become ready within ``wait_seconds``. The
            timeout message carries the tail of the launcher log.
    """
    errors = validate_profile(profile)
    if errors:
        raise InstanceError("invalid profile: " + "; ".join(errors))
    conflicts = port_conflicts(profile)
    if conflicts:
        raise InstanceError("port conflict: " + "; ".join(conflicts))
    owner = _marker_owner(profile)
    if owner is not None and owner != profile.owner:
        raise InstanceError(
            f"{profile.run_dir} belongs to profile owner {owner!r}, not {profile.owner!r}"
        )

    log_path = profile.run_dir / LAUNCHER_LOG
    profile.run_dir.mkdir(parents=True, exist_ok=True)
    _write_marker(profile)

    argv = launcher_argv(profile, action="up")
    env = sanitized_env(profile)
    log(f"[instance] starting {profile.name}: {' '.join(argv)}")
    try:
        with log_path.open("a", encoding="utf-8") as handle:
            process = subprocess.Popen(
                argv,
                cwd=profile.sample_root,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=handle,
                stderr=subprocess.STDOUT,
            )
    except OSError as exc:
        raise InstanceError(f"cannot start launcher {argv[0]!r}: {exc}") from exc
    _LAUNCHERS.append(process)

    _wait_ready(profile, wait_seconds=wait_seconds, log_path=log_path)
    # ``/api/meta`` can answer before the launcher finishes writing pids.json; wait briefly so the
    # returned state names the processes the next ``down`` will stop.
    deadline = time.monotonic() + 5.0
    while not (profile.run_dir / "pids.json").exists() and time.monotonic() < deadline:
        time.sleep(0.1)
    log(f"[instance] {profile.name} is ready at {profile.ui_url}")
    return _state(profile, ui_ready=True)


def status(profile: Profile) -> InstanceState:
    """Return the current state of ``profile`` without changing anything.

    A marker written by another owner makes the instance foreign: the state is reported as not
    running and the detail explains the mismatch, so no caller acts on it.
    """
    owner = _marker_owner(profile)
    logs = _logs(profile)
    if owner is not None and owner != profile.owner:
        return InstanceState(
            running=False,
            ui_ready=False,
            pids=(),
            logs=logs,
            detail=(
                f"{profile.run_dir} belongs to profile owner {owner!r}, not {profile.owner!r}; "
                "refusing to operate on a foreign instance"
            ),
        )
    pids = tuple(pid for pid in _read_pids(profile.run_dir / "pids.json") if _pid_alive(pid))
    ui_ready = _probe_ready(profile)
    return InstanceState(
        running=bool(pids) or ui_ready,
        ui_ready=ui_ready,
        pids=pids,
        logs=logs,
    )


def down(
    profile: Profile,
    *,
    cancel_tasks: bool = True,
    wait_seconds: float = 30.0,
    log: Callable[[str], None] = print,
    recheck_delays: Sequence[float] = CANCEL_RECHECK_SECONDS,
) -> dict[str, object]:
    """Stop the instance, cancelling only the NMS Tasks this instance can prove it created.

    The vendor Task list is the authority for what is still active; the local ``task_snapshots``
    table supplies ownership. Only the intersection is cancelled, once each, then re-checked at
    ``recheck_delays``. An active Task with no local snapshot is reported under ``unknownActive``
    and deliberately left alone, so a manual tester's Task is never disturbed.

    Missing local databases and an unreachable NMS are recorded under ``errors`` and never raise.
    """
    errors: list[str] = []
    notes: list[str] = []
    result: dict[str, object] = {
        "stopped": False,
        "portsClosed": False,
        "tasks": [],
        "unknownActive": [],
        "notes": notes,
        "errors": errors,
    }
    owner = _marker_owner(profile)
    if owner is not None and owner != profile.owner:
        errors.append(
            f"{profile.run_dir} belongs to profile owner {owner!r}, not {profile.owner!r}; "
            "refusing to stop a foreign instance"
        )
        return result

    if cancel_tasks and profile.nms and not profile.nms_sim:
        try:
            tasks, unknown = _cancel_local_tasks(
                profile, delays=recheck_delays, errors=errors, log=log
            )
        except Exception as exc:  # noqa: BLE001 - cleanup is best effort
            errors.append(f"task cancellation failed: {exc}")
        else:
            result["tasks"] = tasks
            result["unknownActive"] = unknown
    elif cancel_tasks:
        reason = (
            "simulator instance; its Tasks are local and do not hold vendor quota"
            if profile.nms_sim
            else "NMS not configured; skipping Task cancellation"
        )
        notes.append(reason)
        log(f"[instance] {reason}")

    argv = launcher_argv(profile, action="stop")
    log(f"[instance] stopping {profile.name}: {' '.join(argv)}")
    try:
        completed = subprocess.run(
            argv,
            cwd=profile.sample_root,
            env=sanitized_env(profile),
            capture_output=True,
            text=True,
            timeout=max(30.0, wait_seconds),
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        errors.append(f"launcher --stop failed: {exc}")
    else:
        result["stopped"] = completed.returncode == 0
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout or "").strip()
            errors.append(f"launcher --stop exited {completed.returncode}: {detail[-800:]}")

    result["portsClosed"] = _wait_ports_closed(profile, wait_seconds)
    return result


def _cancel_local_tasks(
    profile: Profile,
    *,
    delays: Sequence[float],
    errors: list[str],
    log: Callable[[str], None],
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    """Cancel the locally-attributed active Tasks once and re-check until they clear."""
    local, local_error = _local_task_ids(profile)
    if local_error is not None:
        errors.append(local_error)
        local = set()

    active: dict[str, dict[str, object]] = {}
    for row in nms.active_tasks(profile):
        task_id = row.get("taskId")
        if isinstance(task_id, str) and task_id:
            active[task_id] = dict(row)

    unknown = [
        {
            "taskId": task_id,
            "state": row.get("state", ""),
            "contextId": row.get("contextId", ""),
        }
        for task_id, row in active.items()
        if task_id not in local
    ]

    records: list[dict[str, object]] = []
    unresolved: set[str] = set()
    for task_id, row in active.items():
        if task_id not in local:
            continue
        record: dict[str, object] = {
            "taskId": task_id,
            "accepted": False,
            "cleared": False,
            "state": row.get("state", ""),
        }
        try:
            record["accepted"] = bool(nms.cancel_task(profile, task_id))
        except Exception as exc:  # noqa: BLE001 - one failed cancel must not skip the rest
            errors.append(f"cancel {task_id} failed: {exc}")
        records.append(record)
        unresolved.add(task_id)
        log(f"[instance] cancel Task {task_id}")

    for delay in delays:
        if not unresolved:
            break
        time.sleep(max(0.0, float(delay)))
        try:
            current = {
                str(row.get("taskId")): row
                for row in nms.active_tasks(profile)
                if isinstance(row.get("taskId"), str)
            }
        except Exception as exc:  # noqa: BLE001 - re-check is best effort
            errors.append(f"re-check active Tasks failed: {exc}")
            break
        for record in records:
            task_id = str(record["taskId"])
            if task_id not in unresolved:
                continue
            if task_id in current:
                record["state"] = current[task_id].get("state", "")
            else:
                record["cleared"] = True
                unresolved.discard(task_id)
    return records, unknown


#: Local Task ownership sources. A2A-T records the vendor Task in ``task_snapshots``; pure A2A
#: records the inner vendor Task on the OSS binding, so one source alone cannot attribute cleanup.
_LOCAL_TASK_SOURCES: tuple[tuple[str, str], ...] = (
    ("client-state.sqlite3", "SELECT task_id FROM task_snapshots"),
    ("task-status.sqlite3", "SELECT task_id FROM tasks"),
    ("oss-task-status.sqlite3", "SELECT inner_task_id AS task_id FROM downstream_bindings"),
    ("gateway-task-status.sqlite3", "SELECT task_id FROM tasks"),
)


def _local_task_ids(profile: Profile) -> tuple[set[str], str | None]:
    """Return every Task id this instance's own stores can prove it created."""
    ids: set[str] = set()
    errors: list[str] = []
    found = False
    for name, query in _LOCAL_TASK_SOURCES:
        database = profile.runs_dir / name
        if not database.is_file():
            continue
        found = True
        try:
            with sqlite3.connect(f"{database.as_uri()}?mode=ro", uri=True) as connection:
                rows = connection.execute(query).fetchall()
        except sqlite3.Error as exc:
            errors.append(f"cannot read {name}: {exc}")
            continue
        ids.update(
            str(row[0]).strip()
            for row in rows
            if isinstance(row[0], str) and str(row[0]).strip()
        )
    if not found:
        database = profile.runs_dir / "client-state.sqlite3"
        return set(), f"client-state.sqlite3 not found at {database}; cannot attribute active Tasks"
    return ids, ("; ".join(errors) or None)


def _wait_ready(profile: Profile, *, wait_seconds: float, log_path: Path) -> None:
    """Poll ``/api/meta`` until it answers 200 or the deadline expires."""
    client = JsonClient(profile.ui_url, timeout=_probe_timeout(wait_seconds))
    deadline = time.monotonic() + max(0.0, wait_seconds)
    last_error = "no response"
    while True:
        try:
            status_code, _ = client.get_json(_READY_PATH)
        except HttpError as exc:
            last_error = str(exc)
        except Exception as exc:  # noqa: BLE001 - readiness probe never raises
            last_error = str(exc)
        else:
            if status_code == 200:
                return
            last_error = f"HTTP {status_code}"
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        time.sleep(min(_POLL_INTERVAL_SECONDS, remaining))
    tail = _tail(log_path, _LOG_TAIL_LINES)
    raise InstanceError(
        f"{profile.ui_url}{_READY_PATH} did not become ready within {wait_seconds:.0f}s "
        f"({last_error}); launcher log tail ({log_path}):\n{tail}"
    )


def _probe_ready(profile: Profile) -> bool:
    """Return whether ``/api/meta`` answers 200 within the status probe budget."""
    try:
        status_code, _ = JsonClient(
            profile.ui_url, timeout=_STATUS_PROBE_TIMEOUT_SECONDS
        ).get_json(_READY_PATH)
    except Exception:  # noqa: BLE001 - a dead UI is a state, not an error
        return False
    return status_code == 200


def _probe_timeout(wait_seconds: float) -> float:
    """Return a per-request timeout that fits inside the overall readiness budget."""
    return min(_STATUS_PROBE_TIMEOUT_SECONDS, max(0.5, wait_seconds))


def _state(profile: Profile, *, ui_ready: bool) -> InstanceState:
    """Build the state of a just-started instance without re-probing the UI."""
    owner = _marker_owner(profile)
    logs = _logs(profile)
    if owner is not None and owner != profile.owner:
        return InstanceState(
            running=False,
            ui_ready=False,
            pids=(),
            logs=logs,
            detail=(
                f"{profile.run_dir} belongs to profile owner {owner!r}, not {profile.owner!r}; "
                "refusing to operate on a foreign instance"
            ),
        )
    pids = tuple(pid for pid in _read_pids(profile.run_dir / "pids.json") if _pid_alive(pid))
    return InstanceState(
        running=bool(pids) or ui_ready,
        ui_ready=ui_ready,
        pids=pids,
        logs=logs,
    )


def _wait_ports_closed(profile: Profile, wait_seconds: float) -> bool:
    """Poll the instance's bind ports until they are all closed or the deadline expires."""
    deadline = time.monotonic() + max(0.0, wait_seconds)
    while True:
        still_open = _open_ports(profile)
        if not still_open:
            return True
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        time.sleep(min(_PORT_POLL_INTERVAL_SECONDS, remaining))


def _open_ports(profile: Profile) -> list[str]:
    """Return the labels of the instance's bind ports that still accept connections."""
    ports: list[tuple[str, int, str]] = [
        (
            label,
            port,
            profile.webhook_host if label in {"webhook", "oss-webhook"} else profile.ui_host,
        )
        for label, port in profile.bind_ports
    ]
    if profile.nms_sim:
        ports.append(("nms-sim", profile.nms_sim_port, profile.ui_host))
    return [
        f"{label}:{port}"
        for label, port, host in ports
        if port_in_use(port, host)
    ]


def _read_pids(path: Path) -> tuple[int, ...]:
    """Return the integer PIDs of a launcher ``pids.json`` record, tolerating every bad shape."""
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ()
    if not isinstance(document, dict):
        return ()
    raw = document.get("pids", [])
    if not isinstance(raw, list):
        return ()
    pids: list[int] = []
    for item in raw:
        if isinstance(item, bool):
            continue
        try:
            pids.append(int(item))
        except (TypeError, ValueError):
            continue
    return tuple(pids)


def _pid_alive(pid: int) -> bool:
    """Return whether ``pid`` is still running.

    On Windows ``os.kill(pid, 0)`` would terminate the process, so ``tasklist`` is used instead.
    """
    if pid <= 0:
        return False
    if sys.platform == "win32" or os.name == "nt":
        try:
            completed = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return False
        return completed.returncode == 0 and str(pid) in (completed.stdout or "")
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _logs(profile: Profile) -> tuple[Path, ...]:
    """Return the existing ``*.log`` files of the run directory, in stable order."""
    if not profile.run_dir.is_dir():
        return ()
    return tuple(sorted(path for path in profile.run_dir.glob("*.log") if path.is_file()))


def _marker_owner(profile: Profile) -> str | None:
    """Return the marker's owner, or ``None`` when no usable marker exists."""
    path = profile.run_dir / MARKER_FILE
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(document, dict):
        return None
    owner = document.get("owner")
    return owner if isinstance(owner, str) and owner else None


def _write_marker(profile: Profile) -> None:
    """Atomically record this profile's ownership of ``run_dir``."""
    path = profile.run_dir / MARKER_FILE
    document = {
        "schema": MARKER_SCHEMA,
        "owner": profile.owner,
        "uiPort": profile.ui_port,
        "runsDir": str(profile.runs_dir),
        "profile": profile.name,
    }
    temporary = path.with_suffix(".json.tmp")
    try:
        temporary.write_text(json.dumps(document), encoding="utf-8")
        os.replace(temporary, path)
    except OSError as exc:
        raise InstanceError(f"cannot write instance marker {path}: {exc}") from exc


def _tail(path: Path, lines: int) -> str:
    """Return the last ``lines`` lines of ``path`` (empty when unreadable)."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    return "\n".join(text.splitlines()[-lines:])
