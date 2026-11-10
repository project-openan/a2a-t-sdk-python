"""Headless driver of the tester batch API.

The CLI drives the same HTTP API the tester page uses: create a draft batch, start it, then poll
until the batch reaches a terminal state. It never imports the tester's internals, so a change to
``/tester`` stays invisible here as long as the documented JSON contract holds.

Every CLI batch is pinned to one owner cookie (``a2at_tester_client=<profile.owner>``). The server
scopes batch lookup by that cookie, which keeps the CLI's batches separate from the operator's
browser batches and makes a 404 mean "not mine or not there" rather than "gone".
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final, cast
from urllib.parse import quote

from eval.osp.ai.http import HttpError, JsonClient
from eval.osp.ai.types import Profile

#: Anonymous identity cookie the tester page sets; the CLI reuses it to own its batches.
CLIENT_COOKIE: Final[str] = "a2at_tester_client"

#: Tester batch endpoints, relative to the Web UI base URL.
BATCHES_PATH: Final[str] = "/api/tester/batches"

#: Prefix of the persisted batch document schema (``osp-tester/v1`` ... ``osp-tester/v3``).
BATCH_SCHEMA_PREFIX: Final[str] = "osp-tester"

#: Protocol wire names shared with ``eval.osp.catalog``.
PROTOCOL_A2A_T: Final[str] = "a2a-t"
PROTOCOL_A2A: Final[str] = "a2a"

#: Stages the tester can stop after, in execution order (mirrors ``tester.service.ALL_STAGES``).
STOP_AFTER_STAGES: Final[tuple[str, ...]] = (
    "intent",
    "task_t",
    "validate",
    "send",
    "negotiate",
    "accepted",
    "working",
    "final",
)

#: Stages only A2A-T can run; a pure A2A batch has no SDK pipeline.
SDK_STAGES: Final[frozenset[str]] = frozenset({"intent", "task_t", "validate"})

#: Batch states the server never leaves (``draft`` / ``running`` are not terminal).
TERMINAL_STATUSES: Final[frozenset[str]] = frozenset({"finished", "cancelled"})

#: Item error the server writes when it loads a batch the UI process was running when it restarted.
UI_RESTART_MARKER: Final[str] = "UI 进程已重启"

#: Default polling budget of :func:`wait`; a full real-NMS batch can take hours.
DEFAULT_WAIT_TIMEOUT_SECONDS: Final[float] = 21600.0

#: Default per-request timeout of the tester API client.
DEFAULT_HTTP_TIMEOUT_SECONDS: Final[float] = 30.0


class BatchError(RuntimeError):
    """One tester batch call failed or returned a non-2xx response.

    ``status`` and ``body`` keep the HTTP status and the decoded response so the caller can print
    the server's own reason instead of a generic failure.
    """

    def __init__(self, message: str, *, status: int | None = None, body: object = None) -> None:
        super().__init__(message)
        self.status = status
        self.body = body


@dataclass(frozen=True)
class BatchSnapshot:
    """One tester batch as the API reports it.

    ``raw`` is the server document unchanged, so the CLI can read fields this value object does not
    promote (``transport``, ``currentIndex``, ``resolvedRunner``, ...) without a second request.
    """

    batch_id: str
    status: str
    summary: Mapping[str, int]
    items: tuple[Mapping[str, object], ...]
    raw: Mapping[str, object]

    def to_dict(self) -> dict[str, object]:
        """Return the JSON-friendly snapshot: the server document with the promoted fields set."""
        document = dict(self.raw)
        document["batchId"] = self.batch_id
        document["status"] = self.status
        document["summary"] = dict(self.summary)
        document["items"] = [dict(item) for item in self.items]
        return document


def default_client(profile: Profile, *, timeout: float = DEFAULT_HTTP_TIMEOUT_SECONDS) -> JsonClient:
    """Return the tester API client of this profile, pinned to the CLI's owner cookie."""
    return JsonClient(profile.ui_url, cookie={CLIENT_COOKIE: profile.owner}, timeout=timeout)


def is_terminal(status: str) -> bool:
    """Return whether a tester batch status is terminal (``finished`` or ``cancelled``)."""
    return status.strip().lower() in TERMINAL_STATUSES


def ui_restart_cancelled(snapshot: BatchSnapshot) -> bool:
    """Return whether the UI process restart cancelled this batch.

    Such a batch proves nothing about the cases: the CLI must tell the operator to rerun it, not
    record the items as failures. The server marks only the pending/running items, so the marker is
    searched on the item errors rather than on the batch status alone.
    """
    if snapshot.status != "cancelled":
        return False
    return any(UI_RESTART_MARKER in _item_error(item) for item in snapshot.items)


def validate_selection(protocols: Sequence[str], stop_after: str) -> None:
    """Raise ``ValueError`` when the protocol / ``stop_after`` pair cannot be submitted.

    The server enforces the same two rules; checking them before the request keeps a rejected
    selection from looking like an infrastructure failure.
    """
    selected = [str(protocol).strip() for protocol in protocols]
    if not selected:
        raise ValueError("at least one protocol is required (a2a-t, a2a)")
    unknown = sorted({protocol for protocol in selected if protocol not in {PROTOCOL_A2A_T, PROTOCOL_A2A}})
    if unknown:
        raise ValueError(
            f"unsupported protocol(s) {', '.join(unknown)}; expected {PROTOCOL_A2A_T} or {PROTOCOL_A2A}"
        )
    if stop_after not in STOP_AFTER_STAGES:
        raise ValueError(
            f"unsupported stop_after {stop_after!r}; expected one of {', '.join(STOP_AFTER_STAGES)}"
        )
    if PROTOCOL_A2A in selected and stop_after in SDK_STAGES:
        raise ValueError(
            f"pure A2A has no SDK stages; stop after send / negotiate / accepted / working / final, "
            f"not {stop_after!r}"
        )


def create(
    profile: Profile,
    *,
    case_ids: Sequence[int],
    protocols: Sequence[str],
    stop_after: str,
    approval: str | None = None,
    timeout_seconds: float | None = None,
    working_timeout_seconds: float | None = None,
    decision_mode: str | None = None,
    keep_tasks: bool = False,
    client: JsonClient | None = None,
) -> BatchSnapshot:
    """Create one draft batch owned by this profile.

    ``None`` for ``approval``, the timeouts and ``decision_mode`` means "use each case's own
    ``runner`` block / the process default", exactly like the tester page.
    """
    ids = [int(case_id) for case_id in case_ids]
    if not ids:
        raise ValueError("case_ids must not be empty")
    validate_selection(protocols, stop_after)
    payload: dict[str, object] = {
        "caseIds": ids,
        "protocols": [str(protocol).strip() for protocol in protocols],
        "stopAfter": stop_after,
        "approval": approval,
        "timeoutSeconds": timeout_seconds,
        "workingTimeoutSeconds": working_timeout_seconds,
        "decisionMode": decision_mode,
        "keepTasks": bool(keep_tasks),
    }
    return _checked(
        *_post(client or default_client(profile), BATCHES_PATH, payload),
        action="create batch",
        owner=profile.owner,
    )


def start(profile: Profile, batch_id: str, *, client: JsonClient | None = None) -> BatchSnapshot:
    """Start one draft batch."""
    _require_batch_id(batch_id)
    path = f"{_batch_path(batch_id)}/start"
    return _checked(
        *_post(client or default_client(profile), path, {}),
        action="start batch",
        batch_id=batch_id,
        owner=profile.owner,
    )


def fetch(profile: Profile, batch_id: str, *, client: JsonClient | None = None) -> BatchSnapshot:
    """Return the live state of one batch owned by this profile."""
    _require_batch_id(batch_id)
    return _checked(
        *_get(client or default_client(profile), _batch_path(batch_id)),
        action="fetch batch",
        batch_id=batch_id,
        owner=profile.owner,
    )


def wait(
    profile: Profile,
    batch_id: str,
    *,
    timeout_seconds: float = DEFAULT_WAIT_TIMEOUT_SECONDS,
    poll_seconds: float = 1.0,
    on_tick: Callable[[BatchSnapshot], None] | None = None,
    client: JsonClient | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> BatchSnapshot:
    """Poll one batch until it is terminal or the budget ends.

    ``on_tick`` runs after every fetch, so a caller can print progress without polling twice.
    A batch cancelled by a UI restart is returned like any other terminal batch; use
    :func:`ui_restart_cancelled` to distinguish it. On timeout the last observed status and
    summary are part of the error, because "still running" and "server stopped answering" must not
    look alike.

    Raises:
        BatchError: on a failed request, or when the batch does not reach a terminal state within
            ``timeout_seconds``.
        ValueError: when ``poll_seconds`` is not positive.
    """
    if poll_seconds <= 0:
        raise ValueError("poll_seconds must be positive")
    _require_batch_id(batch_id)
    api = client or default_client(profile)
    deadline = time.monotonic() + max(0.0, float(timeout_seconds))
    while True:
        snapshot = fetch(profile, batch_id, client=api)
        if on_tick is not None:
            on_tick(snapshot)
        if is_terminal(snapshot.status):
            return snapshot
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise BatchError(
                f"batch {batch_id} did not reach a terminal state within {timeout_seconds}s; "
                f"last status {snapshot.status!r}, summary {dict(snapshot.summary)}"
            )
        sleep(min(poll_seconds, remaining))


def run(profile: Profile, **kwargs: object) -> BatchSnapshot:
    """Create, start and poll one batch in a single call.

    Accepts the :func:`create` keywords (``case_ids``, ``protocols``, ``stop_after``, ``approval``,
    ``timeout_seconds``, ``working_timeout_seconds``, ``decision_mode``, ``keep_tasks``) plus the
    :func:`wait` options ``wait_timeout_seconds``, ``poll_seconds``, ``on_tick``, ``sleep`` and
    ``client``. ``timeout_seconds`` stays the per-run timeout of the batch; the polling budget is
    ``wait_timeout_seconds`` so the two cannot be confused. Unknown keywords raise ``TypeError``
    before any request is sent.
    """
    wait_options: dict[str, object] = {}
    for key in ("wait_timeout_seconds", "poll_seconds", "on_tick", "sleep"):
        if key in kwargs:
            wait_options[key] = kwargs.pop(key)
    client = cast("JsonClient | None", kwargs.pop("client", None))
    options: dict[str, object] = {
        "case_ids": _int_sequence(kwargs.pop("case_ids", None), "case_ids"),
        "protocols": _str_sequence(kwargs.pop("protocols", None), "protocols"),
        "stop_after": _str_value(kwargs.pop("stop_after", "final"), "stop_after"),
        "approval": _optional_str(kwargs.pop("approval", None), "approval"),
        "timeout_seconds": _optional_float(kwargs.pop("timeout_seconds", None), "timeout_seconds"),
        "working_timeout_seconds": _optional_float(
            kwargs.pop("working_timeout_seconds", None), "working_timeout_seconds"
        ),
        "decision_mode": _optional_str(kwargs.pop("decision_mode", None), "decision_mode"),
        "keep_tasks": _bool_value(kwargs.pop("keep_tasks", False), "keep_tasks"),
    }
    if kwargs:
        raise TypeError(f"unexpected batch option(s): {', '.join(sorted(kwargs))}")
    created = create(
        profile,
        case_ids=cast("Sequence[int]", options["case_ids"]),
        protocols=cast("Sequence[str]", options["protocols"]),
        stop_after=options["stop_after"],
        approval=options["approval"],
        timeout_seconds=options["timeout_seconds"],
        working_timeout_seconds=options["working_timeout_seconds"],
        decision_mode=options["decision_mode"],
        keep_tasks=options["keep_tasks"],
        client=client,
    )
    started = start(profile, created.batch_id, client=client)
    return wait(
        profile,
        started.batch_id,
        timeout_seconds=_float_value(
            wait_options.pop("wait_timeout_seconds", DEFAULT_WAIT_TIMEOUT_SECONDS),
            "wait_timeout_seconds",
        ),
        poll_seconds=_float_value(wait_options.pop("poll_seconds", 1.0), "poll_seconds"),
        on_tick=cast("Callable[[BatchSnapshot], None] | None", wait_options.pop("on_tick", None)),
        client=client,
        sleep=cast("Callable[[float], None]", wait_options.pop("sleep", time.sleep)),
    )


def load_file(path: Path) -> Mapping[str, object]:
    """Read and validate one persisted ``runs-ai/tester/<batch>.json`` document.

    Raises:
        BatchError: when the file is unreadable, is not a JSON object, or does not carry an
            ``osp-tester`` schema. Triage must not silently analyse a file it does not understand.
    """
    target = Path(path)
    try:
        text = target.read_text(encoding="utf-8")
    except OSError as exc:
        raise BatchError(f"cannot read batch file {target}: {exc}") from exc
    try:
        document = json.loads(text)
    except ValueError as exc:
        raise BatchError(f"batch file {target} is not valid JSON: {exc}") from exc
    if not isinstance(document, Mapping):
        raise BatchError(f"batch file {target} is not a JSON object")
    schema = document.get("schema")
    if not isinstance(schema, str) or not schema.startswith(BATCH_SCHEMA_PREFIX):
        raise BatchError(
            f"batch file {target} has schema {schema!r}; expected one starting with {BATCH_SCHEMA_PREFIX!r}"
        )
    return {str(key): value for key, value in document.items()}


def _batch_path(batch_id: str) -> str:
    """Return the URL path of one batch."""
    return f"{BATCHES_PATH}/{quote(batch_id, safe='')}"


def _post(
    client: JsonClient, path: str, payload: Mapping[str, object]
) -> tuple[int, object]:
    """POST one payload, turning a transport failure into a ``BatchError``."""
    try:
        return client.post_json(path, payload)
    except HttpError as exc:
        raise BatchError(f"POST {path} failed: {exc}") from exc


def _get(client: JsonClient, path: str) -> tuple[int, object]:
    """GET one path, turning a transport failure into a ``BatchError``."""
    try:
        return client.get_json(path)
    except HttpError as exc:
        raise BatchError(f"GET {path} failed: {exc}") from exc


def _checked(
    status: int,
    body: object,
    *,
    action: str,
    batch_id: str | None = None,
    owner: str | None = None,
) -> BatchSnapshot:
    """Return the snapshot of a 2xx response, else raise a ``BatchError`` with the server reason."""
    if 200 <= status < 300:
        return _snapshot(body)
    reason = _server_reason(body)
    if status == 404 and batch_id is not None:
        detail = "batch does not exist or is not owned by this profile"
        if owner is not None:
            detail = f"{detail} (owner {owner!r})"
        if reason:
            detail = f"{detail}: {reason}"
    else:
        detail = reason or "unexpected response"
    raise BatchError(f"{action} failed with HTTP {status}: {detail}", status=status, body=body)


def _server_reason(body: object) -> str:
    """Return the server's ``error`` (plus ``code``) from an error body, or an empty string."""
    if not isinstance(body, Mapping):
        return ""
    error = body.get("error")
    if not isinstance(error, str) or not error.strip():
        return ""
    code = body.get("code")
    if isinstance(code, str) and code:
        return f"{error.strip()} ({code})"
    return error.strip()


def _snapshot(document: object) -> BatchSnapshot:
    """Build one snapshot from the server document, rejecting a body without identity."""
    if not isinstance(document, Mapping):
        raise BatchError("tester batch response is not a JSON object")
    batch_id = document.get("batchId")
    if not isinstance(batch_id, str) or not batch_id:
        raise BatchError("tester batch response has no batchId")
    status = document.get("status")
    status_text = status.strip().lower() if isinstance(status, str) else ""
    if not status_text:
        raise BatchError(f"tester batch {batch_id} has no status")
    raw_items = document.get("items")
    items: tuple[Mapping[str, object], ...] = ()
    if isinstance(raw_items, Sequence) and not isinstance(raw_items, (str, bytes)):
        items = tuple(
            {str(key): value for key, value in item.items()}
            for item in raw_items
            if isinstance(item, Mapping)
        )
    return BatchSnapshot(
        batch_id=batch_id,
        status=status_text,
        summary=_int_summary(document.get("summary")),
        items=items,
        raw={str(key): value for key, value in document.items()},
    )


def _int_summary(value: object) -> dict[str, int]:
    """Return the integer counters of a server summary, dropping anything non-numeric."""
    if not isinstance(value, Mapping):
        return {}
    return {
        str(key): int(count)
        for key, count in value.items()
        if isinstance(count, (int, float)) and not isinstance(count, bool)
    }


def _item_error(item: Mapping[str, object]) -> str:
    """Return one item's error text, or an empty string."""
    error = item.get("error")
    return error if isinstance(error, str) else ""


def _require_batch_id(batch_id: str) -> None:
    """Reject an empty batch id before it reaches the URL."""
    if not isinstance(batch_id, str) or not batch_id.strip():
        raise ValueError("batch_id must not be empty")


def _int_sequence(value: object, name: str) -> list[int]:
    """Return a list of ``int`` from a keyword value, or raise ``TypeError``."""
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise TypeError(f"{name} must be a sequence of integers")
    return [int(item) for item in value]


def _str_sequence(value: object, name: str) -> list[str]:
    """Return a list of ``str`` from a keyword value, or raise ``TypeError``."""
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise TypeError(f"{name} must be a sequence of strings")
    return [str(item) for item in value]


def _str_value(value: object, name: str) -> str:
    """Return a non-empty string keyword value, or raise ``TypeError``."""
    if not isinstance(value, str) or not value.strip():
        raise TypeError(f"{name} must be a non-empty string")
    return value


def _optional_str(value: object, name: str) -> str | None:
    """Return ``None`` or a non-empty string keyword value."""
    if value is None:
        return None
    return _str_value(value, name)


def _float_value(value: object, name: str) -> float:
    """Return a numeric keyword value, or raise ``TypeError``."""
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise TypeError(f"{name} must be a number")
    try:
        return float(value)
    except ValueError as exc:
        raise TypeError(f"{name} must be a number") from exc


def _optional_float(value: object, name: str) -> float | None:
    """Return ``None`` or a numeric keyword value."""
    if value is None:
        return None
    return _float_value(value, name)


def _bool_value(value: object, name: str) -> bool:
    """Return a boolean keyword value, or raise ``TypeError``."""
    if not isinstance(value, bool):
        raise TypeError(f"{name} must be a boolean")
    return value


__all__ = [
    "BATCH_SCHEMA_PREFIX",
    "BatchError",
    "BatchSnapshot",
    "CLIENT_COOKIE",
    "DEFAULT_WAIT_TIMEOUT_SECONDS",
    "STOP_AFTER_STAGES",
    "UI_RESTART_MARKER",
    "create",
    "default_client",
    "fetch",
    "is_terminal",
    "load_file",
    "run",
    "start",
    "ui_restart_cancelled",
    "validate_selection",
    "wait",
]
