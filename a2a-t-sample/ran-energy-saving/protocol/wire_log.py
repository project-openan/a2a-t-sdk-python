"""Captured A2A HTTP exchanges of one client run.

The On-the-wire inspector must show what actually left the process. The vendor NMS client already
writes its raw exchange to ``runs/<runId>/wire.jsonl``; this module adds the same record shape to
the a2a-sdk HTTP transport. Local A2A-T and pure A2A runs then show the real ``SendMessageRequest``
body (``{"message": {...}}``) instead of an envelope rebuilt from a normalized event.

Records are redacted before they reach the disk: authorization headers and push tokens never leave
the process in clear text.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

import httpx

#: File name of the captured client exchange inside ``runs/<runId>/``.
WIRE_LOG_FILENAME: Final[str] = "wire.jsonl"

#: File name of the downstream hop captured by the OSS Agent (pure A2A).
WIRE_OSS_FILENAME: Final[str] = "wire-oss.jsonl"

#: JSON keys and header names redacted from ``wire.jsonl``.
REDACT_KEYS: Final[frozenset[str]] = frozenset(
    {"authorization", "x-auth-token", "accesssession", "access_session", "cookie", "set-cookie"}
)

_LOGGER = logging.getLogger("ran_energy_saving.wire_log")


def redact(value: object) -> object:
    """Recursively redact credentials from one wire record or payload."""
    if isinstance(value, Mapping):
        return {
            str(key): "***" if str(key).lower() in REDACT_KEYS else redact(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact(item) for item in value]
    return value


def _request_body(request: httpx.Request) -> object | None:
    """Return the sent JSON body, or its text when the payload is not JSON."""
    try:
        raw = request.content
    except Exception:  # noqa: BLE001 - streaming bodies are not readable before send
        return None
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (UnicodeDecodeError, ValueError):
        return raw.decode("utf-8", "replace")


class HttpWireLog:
    """Append the outbound HTTP exchanges of one run to ``wire.jsonl``.

    One instance is attached to the a2a-sdk ``httpx.AsyncClient`` as a request event hook. The run
    id is only known at run start, so :meth:`set_run` points the log at the current run directory;
    without a run id the hook is a no-op.
    """

    def __init__(self, *, logger: logging.Logger | None = None) -> None:
        """Create one log that is disabled until a run directory is set."""
        self._path: Path | None = None
        self._logger = logger or _LOGGER

    @property
    def path(self) -> Path | None:
        """Return the current ``wire.jsonl`` path, ``None`` when capture is off."""
        return self._path

    def set_run(
        self,
        runs_dir: Path | str | None,
        run_id: str | None,
        *,
        filename: str = WIRE_LOG_FILENAME,
    ) -> None:
        """Point the log at ``<runs_dir>/<run_id>/<filename>``.

        The OSS Agent writes ``wire-oss.jsonl`` so its hop never interleaves lines with the
        engineer-client log written by another process for the same run.

        Args:
            runs_dir: root holding one directory per run.
            run_id: current Run Explorer id; empty disables the log.
            filename: log file name inside the run directory.
        """
        if not runs_dir or not run_id:
            self._path = None
            return
        self._path = Path(runs_dir) / run_id / filename

    async def record_request(self, request: httpx.Request) -> None:
        """Append one outbound request to the run's wire log.

        This is an ``httpx.AsyncClient`` request event hook: it must never raise, because an
        exception would abort the A2A call it is observing.
        """
        path = self._path
        if path is None:
            return
        try:
            record: dict[str, object] = {
                "ts": datetime.now(UTC).isoformat(),
                "dir": "out",
                "method": request.method,
                "url": str(request.url),
                "headers": dict(request.headers),
                "body": _request_body(request),
            }
            self._append(path, record)
        except Exception:  # noqa: BLE001 - evidence capture never breaks a run
            self._logger.debug("wire log capture failed", exc_info=True)

    def _append(self, path: Path, record: Mapping[str, object]) -> None:
        """Append one redacted record to a wire log file."""
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(redact(record), ensure_ascii=False) + "\n")
        except OSError:
            self._logger.debug("wire log write failed", exc_info=True)


__all__ = [
    "HttpWireLog",
    "REDACT_KEYS",
    "WIRE_LOG_FILENAME",
    "WIRE_OSS_FILENAME",
    "redact",
]
