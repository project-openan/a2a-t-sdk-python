"""Raw A2A client of a vendor NMS.

The vendor NMS (Huawei 910 test environment) does not publish an AgentCard and its A2A surface has
three non-standard behaviors the plain a2a-sdk client does not handle:

* the SSE stream never closes on its own; it only emits ``: heartbeat`` comments every 15 s;
* the first ``data:`` frame may be a ``message`` instead of a ``task`` (the NMS asks for missing
  information without creating a Task);
* task cancellation returns HTTP 503.

This module therefore talks to the wire directly: it caches the session token, sends
``POST <base>/message:stream``, skips heartbeat comments, leaves the stream after an idle window,
falls back to ``GET <base>/tasks/{id}`` polling, and keeps a redacted ``wire.jsonl`` of the
exchange. The normalized events it produces still go through
:func:`shared.sse_event_consumer.normalize_event`, so the existing client flow keeps working.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Final

import httpx
from a2a.client.client import ClientCallContext
from a2a.types import SendMessageRequest, StreamResponse
from a2a_t.core.metadata import (
    NEGOTIATION_CONTEXT_METADATA_KEY,
    NEGOTIATION_T_EXTENSION_URI,
)
from google.protobuf.json_format import MessageToDict, ParseDict, ParseError
from shared.config import (
    DEFAULT_NMS_IDLE_TIMEOUT_SECONDS,
    DEFAULT_NMS_POLL_INTERVAL_SECONDS,
    DEFAULT_NMS_REQUEST_TIMEOUT_SECONDS,
    SampleConfig,
)
from shared.run_constants import PROTOCOL_A2A_T
from shared.sse_event_consumer import normalize_event
from shared.trace import PHASE_DISPATCH, PHASE_EXECUTION, PHASE_NEGOTIATION, record_span

from protocol.client_flow import is_stop_event, is_terminal_event

_LOGGER = logging.getLogger("ran_energy_saving.nms_client")

#: Task states that end a poll. The vendor spells cancellation with one ``L``.
NMS_TERMINAL_STATES: Final[frozenset[str]] = frozenset(
    {
        "TASK_STATE_COMPLETED",
        "TASK_STATE_CANCELED",
        "TASK_STATE_FAILED",
        "TASK_STATE_REJECTED",
    }
)

#: JSON keys redacted from ``wire.jsonl``.
_REDACT_KEYS: Final[frozenset[str]] = frozenset(
    {"authorization", "x-auth-token", "accesssession", "access_session", "cookie", "set-cookie"}
)

#: Default request budget of one NMS call.
DEFAULT_NMS_CONNECT_TIMEOUT_SECONDS: Final[float] = 10.0

#: Upper bound of a stored request/response body excerpt in an error message.
_ERROR_EXCERPT_LENGTH: Final[int] = 500


class NmsError(RuntimeError):
    """Base class of the vendor NMS errors."""


class NmsConfigError(NmsError):
    """Raised when the NMS configuration is incomplete."""


class NmsAuthError(NmsError):
    """Raised when the session token cannot be obtained."""


class NmsProtocolError(NmsError):
    """Raised on an unexpected HTTP status or SSE frame."""


class NmsTimeoutError(NmsError):
    """Raised when a Task does not reach a terminal state within the budget."""


class NmsNegotiationRequiredError(NmsError):
    """Raised when the NMS answers with a message instead of creating a Task.

    This is a transport-level fact (no ``task`` frame), not a semantic guess: the sample does not
    implement the A2A-T negotiation fallback.

    Attributes:
        context_id: Context id the NMS expects the follow-up to carry, when present.
        text: Full text of the NMS message.
    """

    def __init__(self, context_id: str, text: str) -> None:
        self.context_id = context_id
        self.text = text
        detail = text.strip() or "the NMS asked for more information"
        super().__init__(f"The NMS did not create a Task: {detail}")


@dataclass(frozen=True)
class NmsConfig:
    """Resolved vendor NMS connection settings.

    Attributes:
        a2a_base: Full A2A surface URL (``.../a2a/json``).
        token_url: Session endpoint.
        token_user: Northbound user name.
        token_value: Password; excluded from ``repr``.
        verify_tls: Whether TLS certificates are verified.
        idle_timeout: Seconds without a ``data:`` frame before switching to polling.
        request_timeout: Per-request budget.
        poll_interval: Seconds between task polls.
        token_refresh_skew: Refresh the token this many seconds before it expires.
        wire_log: Whether the raw exchange is written to ``wire.jsonl``.
    """

    a2a_base: str
    token_url: str
    token_user: str
    token_value: str = field(repr=False)
    verify_tls: bool = False
    idle_timeout: float = DEFAULT_NMS_IDLE_TIMEOUT_SECONDS
    request_timeout: float = DEFAULT_NMS_REQUEST_TIMEOUT_SECONDS
    poll_interval: float = DEFAULT_NMS_POLL_INTERVAL_SECONDS
    token_refresh_skew: float = 60.0
    wire_log: bool = True

    @classmethod
    def from_config(cls, config: SampleConfig) -> NmsConfig:
        """Build the settings from one sample configuration.

        Raises:
            NmsConfigError: when one of the four required values is empty.
        """
        missing = [
            name
            for name, value in (
                ("A2AT_SAMPLE_NMS_A2A_BASE", config.nms_a2a_base),
                ("A2AT_SAMPLE_NMS_TOKEN_URL", config.nms_token_url),
                ("A2AT_SAMPLE_NMS_TOKEN_USER", config.nms_token_user),
                ("A2AT_SAMPLE_NMS_TOKEN_VALUE", config.nms_token_value),
            )
            if not value.strip()
        ]
        if missing:
            raise NmsConfigError(f"NMS configuration is missing {', '.join(missing)}")
        return cls(
            a2a_base=config.nms_a2a_base.rstrip("/"),
            token_url=config.nms_token_url,
            token_user=config.nms_token_user,
            token_value=config.nms_token_value,
            verify_tls=config.nms_verify_tls,
            idle_timeout=config.nms_idle_timeout,
            request_timeout=config.nms_request_timeout,
            poll_interval=config.nms_poll_interval,
            wire_log=config.nms_wire_log,
        )


class NmsClient:
    """Session token, raw SSE stream, task polling and wire log of one vendor NMS."""

    def __init__(
        self,
        config: NmsConfig,
        *,
        http_client: httpx.AsyncClient | None = None,
        wire_root: Path | None = None,
    ) -> None:
        """Create one client.

        Args:
            config: resolved NMS settings.
            http_client: optional injected HTTP client (tests); the caller owns it.
            wire_root: optional directory root for ``<runId>/wire.jsonl``.
        """
        self._config = config
        self._http = http_client or httpx.AsyncClient(
            verify=config.verify_tls,
            trust_env=False,
            timeout=config.request_timeout,
        )
        self._owns_http = http_client is None
        self._token = ""
        self._token_deadline = 0.0
        self._token_lock = asyncio.Lock()
        self._wire_root = wire_root
        self._wire_path: Path | None = None

    @property
    def config(self) -> NmsConfig:
        """Return the resolved settings."""
        return self._config

    def set_wire_path(self, path: Path | None) -> None:
        """Set the ``wire.jsonl`` path of the current run; ``None`` disables the log."""
        self._wire_path = path

    def set_run(self, run_id: str | None) -> None:
        """Point the wire log at ``<wire_root>/<run_id>/wire.jsonl``."""
        if self._wire_root is None or not run_id:
            self._wire_path = None
            return
        self._wire_path = self._wire_root / run_id / "wire.jsonl"

    async def token(self) -> str:
        """Return a valid session token, refreshing it before it expires.

        Raises:
            NmsAuthError: when the token endpoint rejects the credentials or returns no token.
        """
        now = time.monotonic()
        if self._token and now < self._token_deadline:
            return self._token
        async with self._token_lock:
            now = time.monotonic()
            if self._token and now < self._token_deadline:
                return self._token
            payload = {
                "grantType": "password",
                "userName": self._config.token_user,
                "value": self._config.token_value,
            }
            try:
                response = await self._http.put(
                    self._config.token_url,
                    json=payload,
                    timeout=self._config.request_timeout,
                )
            except httpx.HTTPError as exc:
                raise NmsAuthError(f"NMS token request failed: {exc}") from exc
            if response.status_code != 200:
                raise NmsAuthError(f"NMS token request returned HTTP {response.status_code}: {_excerpt(response.text)}")
            try:
                data = response.json()
            except ValueError as exc:
                raise NmsAuthError("NMS token response is not JSON") from exc
            access_session = _first_str(data, "accessSession", "access_session", "accessToken")
            if not access_session:
                raise NmsAuthError("NMS token response carries no accessSession")
            expires = _positive_number(data.get("expires"), default=1800.0)
            ttl = max(1.0, expires - self._config.token_refresh_skew)
            self._token = access_session
            self._token_deadline = now + ttl
            _LOGGER.info("NMS session token acquired (ttl=%.0fs)", ttl)
            _nms_span(
                phase=PHASE_DISPATCH,
                title="NMS session token acquired",
                title_key="nmsToken",
                detail={"expiresInSeconds": round(ttl)},
            )
            return self._token

    def _headers(self, token: str, extension_header: str) -> dict[str, str]:
        """Return the headers of one A2A call."""
        headers = {
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
            "Authorization": f"Bearer {token}",
            "X-Auth-Token": token,
            "A2A-Version": "1.0",
        }
        if extension_header:
            headers["A2A-Extensions"] = extension_header
            headers["X-A2A-Extensions"] = extension_header
        return headers

    async def stream_message(
        self,
        payload: Mapping[str, object],
        *,
        extension_header: str = "",
    ) -> AsyncIterator[dict[str, object]]:
        """Send one message and yield its raw ``data:`` frames.

        The iterator ends when the stream closes or no ``data:`` frame arrives within
        ``idle_timeout`` seconds. Heartbeat comments are skipped.

        Raises:
            NmsAuthError: when the token cannot be acquired.
            NmsProtocolError: on a non-200 response or a malformed frame.
        """
        token = await self.token()
        url = f"{self._config.a2a_base}/message:stream"
        headers = self._headers(token, extension_header)
        self._write_wire("out", method="POST", url=url, headers=headers, body=dict(payload))
        _nms_span(
            phase=PHASE_DISPATCH,
            title="NMS A2A stream opened",
            title_key="nmsStream",
            detail={"url": url, "a2aT": bool(extension_header)},
        )
        timeout = httpx.Timeout(
            self._config.request_timeout,
            connect=min(DEFAULT_NMS_CONNECT_TIMEOUT_SECONDS, self._config.request_timeout),
        )
        try:
            async with self._http.stream("POST", url, json=dict(payload), headers=headers, timeout=timeout) as response:
                if response.status_code != 200:
                    body = (await response.aread()).decode("utf-8", "replace")
                    raise NmsProtocolError(f"NMS A2A request returned HTTP {response.status_code}: {_excerpt(body)}")
                lines = response.aiter_lines()
                while True:
                    try:
                        line = await asyncio.wait_for(anext(lines), timeout=self._config.idle_timeout)
                    except StopAsyncIteration:
                        break
                    except asyncio.TimeoutError:
                        self._write_wire("in", line=f": idle {self._config.idle_timeout:g}s; polling")
                        _nms_span(
                            phase=PHASE_EXECUTION,
                            title="NMS stream idle; polling the task",
                            title_key="nmsIdle",
                            detail={"idleSeconds": self._config.idle_timeout},
                        )
                        break
                    self._write_wire("in", line=line)
                    if not line.startswith("data:"):
                        # ``: heartbeat`` comments and blank separator lines.
                        continue
                    raw = line[5:].strip()
                    if not raw:
                        continue
                    try:
                        frame = json.loads(raw)
                    except json.JSONDecodeError as exc:
                        self.record_error(
                            "ParseError",
                            f"Invalid SSE data frame: {_excerpt(raw)}",
                            {"line": line},
                        )
                        raise NmsProtocolError(f"Invalid SSE data frame: {_excerpt(raw)}") from exc
                    if not isinstance(frame, dict):
                        self.record_error(
                            "ParseError",
                            "SSE data frame is not a JSON object",
                            frame,
                        )
                        raise NmsProtocolError("SSE data frame is not a JSON object")
                    if "error" in frame:
                        message = _frame_error_message(frame)
                        self.record_error("NmsProtocolError", message, frame)
                        _nms_span(
                            phase=PHASE_EXECUTION,
                            title="NMS returned an error frame",
                            title_key="nmsError",
                            detail={"message": message[:1000]},
                        )
                        raise NmsProtocolError(message)
                    yield frame
        except httpx.HTTPError as exc:
            raise NmsProtocolError(f"NMS A2A request failed: {exc}") from exc

    async def get_task(self, task_id: str) -> dict[str, object]:
        """Return one Task document.

        Raises:
            NmsProtocolError: on a non-200 response or a non-object body.
        """
        token = await self.token()
        url = f"{self._config.a2a_base}/tasks/{task_id}"
        headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {token}",
            "X-Auth-Token": token,
        }
        try:
            response = await self._http.get(url, headers=headers, timeout=self._config.request_timeout)
        except httpx.HTTPError as exc:
            raise NmsProtocolError(f"NMS task request failed: {exc}") from exc
        if response.status_code != 200:
            raise NmsProtocolError(f"NMS task request returned HTTP {response.status_code}: {_excerpt(response.text)}")
        try:
            data = response.json()
        except ValueError as exc:
            raise NmsProtocolError("NMS task response is not JSON") from exc
        if not isinstance(data, dict):
            raise NmsProtocolError("NMS task response is not a JSON object")
        task = data.get("task")
        return task if isinstance(task, dict) else data

    async def list_tasks(self) -> dict[str, object]:
        """Return the NMS task list document (the NMS keeps the latest 50)."""
        token = await self.token()
        url = f"{self._config.a2a_base}/tasks"
        headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {token}",
            "X-Auth-Token": token,
        }
        try:
            response = await self._http.get(url, headers=headers, timeout=self._config.request_timeout)
        except httpx.HTTPError as exc:
            raise NmsProtocolError(f"NMS task list request failed: {exc}") from exc
        if response.status_code != 200:
            raise NmsProtocolError(f"NMS task list returned HTTP {response.status_code}: {_excerpt(response.text)}")
        try:
            data = response.json()
        except ValueError as exc:
            raise NmsProtocolError("NMS task list response is not JSON") from exc
        return data if isinstance(data, dict) else {}

    async def cancel_task(self, task_id: str) -> bool:
        """Cancel one Task; ``False`` when the NMS answers the known HTTP 503.

        Raises:
            NmsProtocolError: on an unexpected status.
        """
        token = await self.token()
        url = f"{self._config.a2a_base}/tasks/{task_id}:cancel"
        headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {token}",
            "X-Auth-Token": token,
        }
        try:
            response = await self._http.post(url, headers=headers, timeout=self._config.request_timeout)
        except httpx.HTTPError as exc:
            raise NmsProtocolError(f"NMS cancel request failed: {exc}") from exc
        if response.status_code == 503:
            _LOGGER.warning("NMS cancel is unsupported (HTTP 503) for task %s", task_id)
            _nms_span(
                phase=PHASE_EXECUTION,
                title="NMS cancel unavailable (HTTP 503)",
                title_key="nmsCancel",
                detail={"taskId": task_id},
            )
            return False
        if response.status_code >= 400:
            raise NmsProtocolError(f"NMS cancel returned HTTP {response.status_code}: {_excerpt(response.text)}")
        return True

    async def wait_for_terminal(self, task_id: str, *, timeout: float | None = None) -> dict[str, object]:
        """Poll one Task until it reaches a terminal state.

        Args:
            task_id: Task to poll.
            timeout: Budget in seconds; defaults to ``request_timeout``.

        Raises:
            NmsTimeoutError: when the Task is still running at the deadline.
        """
        budget = self._config.request_timeout if timeout is None else timeout
        deadline = time.monotonic() + budget
        last_state = ""
        while True:
            task = await self.get_task(task_id)
            last_state = _task_state(task)
            if last_state in NMS_TERMINAL_STATES:
                return task
            if time.monotonic() >= deadline:
                raise NmsTimeoutError(
                    f"Task {task_id} did not reach a terminal state within {budget:g}s "
                    f"(last state: {last_state or 'unknown'})"
                )
            self._write_wire("poll", task_id=task_id, state=last_state)
            _nms_span(
                phase=PHASE_EXECUTION,
                title="NMS task polled",
                title_key="nmsPoll",
                detail={"taskId": task_id, "state": last_state},
            )
            await asyncio.sleep(self._config.poll_interval)

    async def aclose(self) -> None:
        """Close the owned HTTP client."""
        if self._owns_http:
            await self._http.aclose()

    def _write_wire(self, direction: str, **fields: object) -> None:
        """Append one redacted record to the run's ``wire.jsonl``."""
        if not self._config.wire_log or self._wire_path is None:
            return
        record: dict[str, object] = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "dir": direction,
            **fields,
        }
        self._append_wire(self._wire_path, record)

    def record_error(
        self,
        kind: str,
        message: str,
        frame: object | None = None,
    ) -> None:
        """Append one failed exchange to ``wire-error.jsonl``.

        The SDK or the raw reader may reject a frame before any ``wire.jsonl`` record is written;
        this keeps the frame that explains the failure.
        """
        if not self._config.wire_log or self._wire_path is None:
            return
        record: dict[str, object] = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "type": kind,
            "message": message,
        }
        if frame is not None:
            record["frame"] = frame
        self._append_wire(self._wire_path.with_name("wire-error.jsonl"), record)

    def _append_wire(self, path: Path, record: Mapping[str, object]) -> None:
        """Append one redacted record to a wire log file."""
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(_redact(record), ensure_ascii=False) + "\n")
        except OSError:
            _LOGGER.debug("wire log write failed", exc_info=True)


class NmsSseTransport:
    """Existing client-flow transport backed by :class:`NmsClient`.

    It converts raw JSON frames back into ``StreamResponse`` objects so
    :func:`shared.sse_event_consumer.normalize_event` stays the single normalization point.
    """

    def __init__(
        self,
        client: NmsClient,
        *,
        protocol: str = PROTOCOL_A2A_T,
        request_timeout: float | None = None,
    ) -> None:
        """Create one transport.

        Args:
            client: raw NMS client.
            protocol: interaction protocol; only ``a2a-t`` sends extension headers.
            request_timeout: terminal-poll budget; defaults to the client configuration.
        """
        self._client = client
        self._protocol = protocol
        self._request_timeout = client.config.request_timeout if request_timeout is None else request_timeout
        self._event_sink: Any | None = None

    def set_event_sink(self, event_sink: Any | None) -> None:
        """Attach the live event sink used by ``run_client_flow``."""
        self._event_sink = event_sink

    def _emit(self, event: dict[str, object]) -> None:
        """Forward one normalized event to the attached sink, when any."""
        if self._event_sink is not None:
            self._event_sink(event)

    async def send(
        self,
        request: SendMessageRequest,
        call_context: ClientCallContext,
    ) -> list[dict[str, object]]:
        """Send one message and return the normalized events up to the first stop point.

        Raises:
            NmsNegotiationRequiredError: when the NMS returns a message without a Task.
            NmsProtocolError: on a malformed frame or an unexpected HTTP status.
        """
        payload = MessageToDict(request)
        extension_header = ""
        if self._protocol == PROTOCOL_A2A_T:
            extension_header = str(call_context.service_parameters.get("A2A-Extensions", "") or "")
        events: list[dict[str, object]] = []
        task_id: str | None = None
        async for frame in self._client.stream_message(payload, extension_header=extension_header):
            if "message" in frame and task_id is None:
                message = frame.get("message")
                if isinstance(message, Mapping):
                    metadata = message.get("metadata")
                    if isinstance(metadata, Mapping) and _is_negotiation_metadata(metadata):
                        # A structured negotiation: let the existing flow read the
                        # Negotiation-T metadata and ask the decision provider.
                        event = _frame_to_event(frame)
                        events.append(event)
                        self._emit(event)
                        return events
                    context_id = _context_id(message)
                    text = _message_text(message)
                    self._client.record_error("NmsNegotiationRequiredError", text, frame)
                    _nms_span(
                        phase=PHASE_NEGOTIATION,
                        title="NMS asked for more information",
                        title_key="nmsNegotiation",
                        detail={"contextId": context_id, "text": text[:1000]},
                    )
                    raise NmsNegotiationRequiredError(context_id, text)
            if "task" in frame:
                task = frame.get("task")
                if isinstance(task, Mapping) and isinstance(task.get("id"), str):
                    task_id = task["id"]
            event = _frame_to_event(frame)
            if event.get("kind") == "raw":
                self._client.record_error("ParseError", "Unrecognized StreamResponse frame", frame)
            events.append(event)
            self._emit(event)
            if is_stop_event(event):
                break
        if task_id is not None and not any(is_terminal_event(event) for event in events):
            try:
                task = await self._client.wait_for_terminal(task_id, timeout=self._request_timeout)
            except NmsTimeoutError:
                _LOGGER.warning(
                    "NMS task %s did not finish within %.0fs; returning the events received",
                    task_id,
                    self._request_timeout,
                )
            else:
                event = _frame_to_event({"task": task})
                events.append(event)
                self._emit(event)
        return events

    async def cancel(self, task_id: str, call_context: ClientCallContext | None = None) -> None:
        """Cancel one Task; the known HTTP 503 is not an error."""
        del call_context
        await self._client.cancel_task(task_id)


def _frame_to_event(frame: Mapping[str, object]) -> dict[str, object]:
    """Convert one raw JSON frame into the normalized event shape."""
    try:
        response = StreamResponse()
        ParseDict(dict(frame), response)
        return normalize_event(response)
    except (ParseError, TypeError, ValueError):
        return {"kind": "raw", "frame": dict(frame)}


def _task_state(task: Mapping[str, object]) -> str:
    """Return the state name of one Task document."""
    status = task.get("status")
    if isinstance(status, Mapping):
        state = status.get("state")
        if isinstance(state, str):
            return state
    state = task.get("state")
    return state if isinstance(state, str) else ""


def _message_text(message: Mapping[str, object]) -> str:
    """Return the first text part of one Message JSON document."""
    parts = message.get("parts")
    if isinstance(parts, list) and parts:
        first = parts[0]
        if isinstance(first, Mapping) and isinstance(first.get("text"), str):
            return str(first["text"])
    return ""


def _context_id(message: Mapping[str, object]) -> str:
    """Return the context id of one Message JSON document."""
    for key in ("contextId", "context_id"):
        value = message.get(key)
        if isinstance(value, str):
            return value
    return ""


def _first_str(data: Mapping[str, object], *keys: str) -> str:
    """Return the first non-empty string value among ``keys``."""
    for key in keys:
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _positive_number(value: object, *, default: float) -> float:
    """Return a positive finite number, or ``default`` for a missing/invalid value."""
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return default
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if number > 0 else default


def _excerpt(text: str, limit: int = _ERROR_EXCERPT_LENGTH) -> str:
    """Return a short single-line excerpt of one response body."""
    compact = " ".join(text.split())
    return compact[:limit] + ("..." if len(compact) > limit else "")


def _is_negotiation_metadata(metadata: Mapping[str, object]) -> bool:
    """Return whether one message metadata map carries a structured negotiation."""
    return NEGOTIATION_T_EXTENSION_URI in metadata or NEGOTIATION_CONTEXT_METADATA_KEY in metadata


def _frame_error_message(frame: Mapping[str, object]) -> str:
    """Render the vendor's ``error`` envelope as one readable message."""
    error = frame.get("error")
    if isinstance(error, Mapping):
        code = error.get("code")
        message = str(error.get("message") or "").strip()
        status = str(error.get("status") or "").strip()
        parts = [part for part in (status, f"code={code}" if code is not None else "") if part]
        prefix = f"NMS error frame ({', '.join(parts)})" if parts else "NMS error frame"
        return f"{prefix}: {message or _excerpt(json.dumps(dict(frame), ensure_ascii=False))}"
    return f"NMS error frame: {_excerpt(json.dumps(dict(frame), ensure_ascii=False))}"


def _nms_span(
    *,
    phase: str,
    title: str,
    title_key: str,
    detail: Mapping[str, object] | None = None,
) -> None:
    """Emit one developer-evidence span of the raw NMS exchange."""
    payload = dict(detail or {})
    payload["titleKey"] = title_key
    record_span(
        kind="tool",
        phase=phase,
        title=title,
        prefix="nms",
        actor="nms",
        side="client",
        detail=payload,
    )


def _redact(value: object) -> object:
    """Recursively redact credentials from one wire record."""
    if isinstance(value, Mapping):
        return {str(key): "***" if str(key).lower() in _REDACT_KEYS else _redact(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_redact(item) for item in value]
    return value
