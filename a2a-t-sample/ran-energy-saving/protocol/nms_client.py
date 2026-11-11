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
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Final

import httpx
from a2a.client.client import ClientCallContext
from a2a.types import SendMessageRequest, StreamResponse, TaskPushNotificationConfig
from a2a_t.core.metadata import (
    NEGOTIATION_CONTEXT_METADATA_KEY,
    NEGOTIATION_T_EXTENSION_URI,
)
from google.protobuf.json_format import MessageToDict, ParseDict, ParseError
from shared.config import (
    DEFAULT_NMS_IDLE_TIMEOUT_SECONDS,
    DEFAULT_NMS_POLL_AFTER_SECONDS,
    DEFAULT_NMS_POLL_INTERVAL_SECONDS,
    DEFAULT_NMS_REQUEST_TIMEOUT_SECONDS,
    DEFAULT_NMS_SEND_TIMEOUT_SECONDS,
    SampleConfig,
)
from shared.run_constants import PROTOCOL_A2A_T, PROTOCOL_PURE
from shared.sse_event_consumer import normalize_event, stream_response_payload
from shared.trace import (
    PHASE_DISPATCH,
    PHASE_EXECUTION,
    PHASE_NEGOTIATION,
    current_trace,
    is_report_artifact,
    record_span,
)
from shared.trace_propagation import current_downstream_trace_id

from protocol.client_flow import is_stop_event, is_terminal_event
from protocol.messages import metadata_to_dict
from protocol.wire_log import redact

_LOGGER = logging.getLogger("ran_energy_saving.nms_client")

#: Sentinel meaning "this task did not select a run directory".
_WIRE_UNSET: Final[object] = object()

#: Task states that end a poll. The vendor spells cancellation with one ``L``.
NMS_TERMINAL_STATES: Final[frozenset[str]] = frozenset(
    {
        "TASK_STATE_COMPLETED",
        "TASK_STATE_CANCELED",
        "TASK_STATE_FAILED",
        "TASK_STATE_REJECTED",
    }
)

#: Default request budget of one NMS call.
DEFAULT_NMS_CONNECT_TIMEOUT_SECONDS: Final[float] = 10.0

#: Upper bound of a stored request/response body excerpt in an error message.
_ERROR_EXCERPT_LENGTH: Final[int] = 500

#: Response headers kept when an NMS call fails. The vendor puts its quota diagnosis in
#: ``x-hiro-apigw-err-desc`` / ``x-tiered-rate-limit`` and its recovery hint in ``retry-after``;
#: the rest identify the exchange. Every other header is dropped before it reaches the wire log.
_FAILURE_HEADER_NAMES: Final[tuple[str, ...]] = (
    "date",
    "retry-after",
    "x-tiered-rate-limit",
    "x-hiro-apigw-err-desc",
    "content-type",
    "x-request-id",
)


class NmsError(RuntimeError):
    """Base class of the vendor NMS errors."""


class NmsConfigError(NmsError):
    """Raised when the NMS configuration is incomplete."""


class NmsAuthError(NmsError):
    """Raised when the session token cannot be obtained."""


class NmsProtocolError(NmsError):
    """Raised on an unexpected HTTP status or SSE frame.

    Attributes:
        http_status: HTTP status of the failed exchange; ``None`` for a non-HTTP failure.
        retry_after: ``Retry-After`` of that response in seconds, when the NMS sent a usable one.
    """

    def __init__(
        self,
        message: str,
        *,
        http_status: int | None = None,
        retry_after: float | None = None,
    ) -> None:
        super().__init__(message)
        self.http_status = http_status
        self.retry_after = retry_after


#: Delays before retrying a follow-up the vendor refused with ``TASK_STREAM_CONFLICT``. The vendor
#: asks the client to close the previous stream; the webhook path owns no stream, so a short
#: backoff is the only client-side action. The answer carries the same message id, so a vendor that
#: deduplicates retries does not apply the answer twice.
STREAM_CONFLICT_RETRY_DELAYS: Final[tuple[float, ...]] = (2.0, 4.0)


def is_stream_conflict(exc: BaseException) -> bool:
    """Return whether one NMS error is the transient per-Task stream conflict."""
    if not isinstance(exc, NmsProtocolError) or exc.http_status != 409:
        return False
    message = str(exc)
    return "TASK_STREAM_CONFLICT" in message or "active stream" in message.lower()


def _http_error_detail(exc: httpx.HTTPError) -> str:
    """Return a self-describing HTTP error string.

    ``str(httpx.ReadTimeout())`` is empty, so the field UI showed a bare
    ``NMS A2A request failed:`` and the operator could not tell a timeout from a protocol error.
    """
    text = str(exc).strip()
    name = type(exc).__name__
    return f"{name}: {text}" if text else name


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
        poll_after_silence: Silence before a non-terminal Task is handed to background tracking.
        defer_to_tracker: Whether the process owns a background tracker for the Task.
        token_refresh_skew: Refresh the token this many seconds before it expires.
        wire_log: Whether the raw exchange is written to ``wire.jsonl``.
    """

    a2a_base: str
    token_url: str
    token_user: str
    token_value: str = field(repr=False)
    verify_tls: bool = False
    idle_timeout: float = DEFAULT_NMS_IDLE_TIMEOUT_SECONDS
    read_after_send: bool = True
    request_timeout: float = DEFAULT_NMS_REQUEST_TIMEOUT_SECONDS
    send_timeout: float = DEFAULT_NMS_SEND_TIMEOUT_SECONDS
    poll_interval: float = DEFAULT_NMS_POLL_INTERVAL_SECONDS
    poll_after_silence: float = DEFAULT_NMS_POLL_AFTER_SECONDS
    defer_to_tracker: bool = False
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
            read_after_send=config.nms_read_after_send,
            request_timeout=config.nms_request_timeout,
            send_timeout=config.nms_send_timeout,
            poll_interval=config.nms_poll_interval,
            poll_after_silence=config.nms_poll_after_silence,
            defer_to_tracker=config.defer_to_tracker,
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
        # Polls and picker calls repeat the same success headers; record them once per key so a
        # long task does not add a full header block to every poll.
        self._response_header_seen: set[str] = set()
        # One NmsClient is shared by concurrent OSS Agent requests; the context variable keeps
        # each request's wire file private to its own asyncio task.
        self._wire_var: ContextVar[Path | None | object] = ContextVar(f"a2at_nms_wire_{id(self)}", default=_WIRE_UNSET)

    @property
    def config(self) -> NmsConfig:
        """Return the resolved settings."""
        return self._config

    def _current_wire_path(self) -> Path | None:
        """Return this task's wire path, falling back to the instance value."""
        value = self._wire_var.get()
        if value is _WIRE_UNSET:
            return self._wire_path
        return value if isinstance(value, Path) else None

    def set_wire_path(self, path: Path | None) -> None:
        """Set the ``wire.jsonl`` path of the current run; ``None`` disables the log."""
        self._wire_path = path
        self._wire_var.set(path)

    def set_run(self, run_id: str | None) -> None:
        """Point the wire log at ``<wire_root>/<run_id>/wire.jsonl``."""
        if self._wire_root is None or not run_id:
            self._wire_path = None
            self._wire_var.set(None)
            return
        self._wire_path = self._wire_root / run_id / "wire.jsonl"
        self._wire_var.set(self._wire_path)

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
                raise NmsAuthError(f"NMS token request failed: {_http_error_detail(exc)}") from exc
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
                title="RAN session token acquired",
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
        traceparent = self._traceparent()
        if traceparent:
            headers["traceparent"] = traceparent
        if extension_header:
            headers["A2A-Extensions"] = extension_header
            headers["X-A2A-Extensions"] = extension_header
        return headers

    def _traceparent(self) -> str:
        """Return the W3C ``traceparent`` correlating this call with the sample run.

        The stable downstream trace id is preferred so a blocked run and its retry carry the same
        vendor-facing trace. Without one, the run id (already 32 hex digits) is the trace-id: a
        vendor that keeps the header can join its own logs to one run. The span-id is generated
        here because the sample's own span ids are not 16 hex digits. Empty when the call runs
        outside a traced run.
        """
        trace_id = current_downstream_trace_id() or str(current_trace().run_id or "").strip().lower()
        if not _is_hex(trace_id, 32) or trace_id == "0" * 32:
            return ""
        span_id = uuid.uuid4().hex[:16]
        injected = f"00-{trace_id}-{span_id}-01"
        # Print the id the vendor actually receives: this log can belong to another process
        # (UI vs OSS), and without it the Langfuse trace id cannot be matched to a hop.
        _LOGGER.info("NMS call traceparent=%s", injected)
        return injected

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
            title="RAN A2A stream opened",
            title_key="nmsStream",
            detail={"url": url, "a2aT": bool(extension_header)},
        )
        timeout = httpx.Timeout(
            self._config.send_timeout,
            connect=min(DEFAULT_NMS_CONNECT_TIMEOUT_SECONDS, self._config.send_timeout),
        )
        started = time.monotonic()
        try:
            async with self._http.stream("POST", url, json=dict(payload), headers=headers, timeout=timeout) as response:
                if response.status_code != 200:
                    body = (await response.aread()).decode("utf-8", "replace")
                    self._record_failure_response(
                        response,
                        url=url,
                        elapsed_seconds=time.monotonic() - started,
                    )
                    raise NmsProtocolError(
                        f"NMS A2A request returned HTTP {response.status_code}: {_excerpt(body)}",
                        http_status=response.status_code,
                        retry_after=_retry_after_seconds(response),
                    )
                self._record_success_response(
                    response,
                    url=url,
                    elapsed_seconds=time.monotonic() - started,
                    stream=True,
                )
                lines = response.aiter_lines()
                # The NMS emits ``: heartbeat`` every 15 s forever, so an idle window measured
                # between *any* two lines never expires: the stream hung for minutes after the
                # Task went to WORKING and never reached polling. Only a ``data:`` frame proves
                # progress; heartbeats are skipped without moving ``idle_deadline``. Once a Task
                # is known, a process with a background tracker leaves the stream sooner and
                # hands that Task over; a negotiation without a Task keeps the normal window.
                idle_timeout = self._config.idle_timeout
                idle_deadline = time.monotonic() + idle_timeout
                while True:
                    remaining = idle_deadline - time.monotonic()
                    if remaining <= 0:
                        self._write_wire(
                            "in",
                            line=f": idle {idle_timeout:g}s without a data frame; polling",
                        )
                        _nms_span(
                            phase=PHASE_EXECUTION,
                            title="RAN stream idle; polling the task",
                            title_key="nmsIdle",
                            detail={"idleSeconds": idle_timeout},
                        )
                        break
                    try:
                        line = await asyncio.wait_for(anext(lines), timeout=remaining)
                    except StopAsyncIteration:
                        break
                    except asyncio.TimeoutError:
                        continue
                    self._write_wire("in", line=line)
                    if not line.startswith("data:"):
                        # ``: heartbeat`` comments and blank separator lines; they do not reset
                        # the deadline.
                        continue
                    raw = line[5:].strip()
                    if not raw:
                        continue
                    idle_deadline = time.monotonic() + idle_timeout
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
                            title="RAN side returned an error frame",
                            title_key="nmsError",
                            detail={"message": message[:1000]},
                        )
                        raise NmsProtocolError(message)
                    if self._config.defer_to_tracker and "task" in frame:
                        task = frame.get("task")
                        if isinstance(task, Mapping) and isinstance(task.get("id"), str):
                            # The Task is now known: a process-owned tracker can take over after
                            # the shorter silence instead of occupying this stream for the budget.
                            idle_timeout = self._config.poll_after_silence
                            idle_deadline = time.monotonic() + idle_timeout
                    yield frame
        except httpx.HTTPError as exc:
            raise NmsProtocolError(f"NMS A2A request failed: {_http_error_detail(exc)}") from exc

    async def send_message(
        self,
        payload: Mapping[str, object],
        *,
        extension_header: str = "",
    ) -> dict[str, object]:
        """Send one non-streaming ``message:send`` request and return its JSON reply.

        The real NMS uses this endpoint for direct machine-to-machine integration; task progress
        is delivered through the registered webhook instead of an SSE stream.

        Raises:
            NmsAuthError: when the token cannot be acquired.
            NmsProtocolError: on a non-200 response or a non-object body.
        """
        token = await self.token()
        url = f"{self._config.a2a_base}/message:send"
        headers = self._headers(token, extension_header)
        headers["Accept"] = "application/json"
        self._write_wire("out", method="POST", url=url, headers=headers, body=dict(payload))
        _nms_span(
            phase=PHASE_DISPATCH,
            title="RAN A2A message sent",
            title_key="nmsSend",
            detail={"url": url, "a2aT": bool(extension_header)},
        )
        timeout = httpx.Timeout(
            self._config.send_timeout,
            connect=min(DEFAULT_NMS_CONNECT_TIMEOUT_SECONDS, self._config.send_timeout),
        )
        started = time.monotonic()
        try:
            response = await self._http.post(
                url,
                json=dict(payload),
                headers=headers,
                timeout=timeout,
            )
        except httpx.HTTPError as exc:
            raise NmsProtocolError(f"NMS A2A request failed: {_http_error_detail(exc)}") from exc
        if response.status_code != 200:
            body = response.text
            headers_seen = _failure_headers(response)
            self._record_failure_response(
                response,
                url=url,
                elapsed_seconds=time.monotonic() - started,
            )
            message = f"NMS A2A request returned HTTP {response.status_code}: {_excerpt(body)}"
            self.record_error(
                "NmsProtocolError",
                message,
                body,
                status=response.status_code,
                headers=headers_seen,
            )
            raise NmsProtocolError(
                message,
                http_status=response.status_code,
                retry_after=_retry_after_seconds(response),
            )
        try:
            body = response.json()
        except ValueError as exc:
            self.record_error("ParseError", "NMS response is not JSON", response.text)
            raise NmsProtocolError("NMS A2A response is not JSON") from exc
        if not isinstance(body, dict):
            self.record_error("ParseError", "NMS response is not a JSON object", body)
            raise NmsProtocolError("NMS A2A response is not a JSON object")
        self._record_success_response(
            response,
            url=url,
            elapsed_seconds=time.monotonic() - started,
            body=body,
        )
        if "error" in body:
            message = _frame_error_message(body)
            self.record_error("NmsProtocolError", message, body)
            _nms_span(
                phase=PHASE_EXECUTION,
                title="RAN side returned an error frame",
                title_key="nmsError",
                detail={"message": message[:1000]},
            )
            raise NmsProtocolError(message)
        return body

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
        started = time.monotonic()
        try:
            response = await self._http.get(url, headers=headers, timeout=self._config.request_timeout)
        except httpx.HTTPError as exc:
            raise NmsProtocolError(f"NMS task request failed: {_http_error_detail(exc)}") from exc
        if response.status_code != 200:
            self._record_failure_response(response, url=url, elapsed_seconds=time.monotonic() - started)
            raise NmsProtocolError(
                f"NMS task request returned HTTP {response.status_code}: {_excerpt(response.text)}",
                http_status=response.status_code,
                retry_after=_retry_after_seconds(response),
            )
        try:
            data = response.json()
        except ValueError as exc:
            raise NmsProtocolError("NMS task response is not JSON") from exc
        if not isinstance(data, dict):
            raise NmsProtocolError("NMS task response is not a JSON object")
        task = data.get("task")
        self._record_success_response(
            response,
            url=url,
            elapsed_seconds=time.monotonic() - started,
            once_key=f"task:{task_id}",
        )
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
        started = time.monotonic()
        try:
            response = await self._http.get(url, headers=headers, timeout=self._config.request_timeout)
        except httpx.HTTPError as exc:
            raise NmsProtocolError(f"NMS task list request failed: {_http_error_detail(exc)}") from exc
        if response.status_code != 200:
            self._record_failure_response(response, url=url, elapsed_seconds=time.monotonic() - started)
            raise NmsProtocolError(
                f"NMS task list returned HTTP {response.status_code}: {_excerpt(response.text)}",
                http_status=response.status_code,
                retry_after=_retry_after_seconds(response),
            )
        try:
            data = response.json()
        except ValueError as exc:
            raise NmsProtocolError("NMS task list response is not JSON") from exc
        self._record_success_response(
            response,
            url=url,
            elapsed_seconds=time.monotonic() - started,
            once_key="tasks",
        )
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
        started = time.monotonic()
        try:
            # The NMS answers 500 INTERNAL for an empty or ``{"taskId": …}`` body.
            response = await self._http.post(
                url, headers=headers, json={"id": task_id}, timeout=self._config.request_timeout
            )
        except httpx.HTTPError as exc:
            raise NmsProtocolError(f"NMS cancel request failed: {_http_error_detail(exc)}") from exc
        if response.status_code == 503:
            _LOGGER.warning("NMS cancel is unsupported (HTTP 503) for task %s", task_id)
            _nms_span(
                phase=PHASE_EXECUTION,
                title="RAN cancel unavailable (HTTP 503)",
                title_key="nmsCancel",
                detail={"taskId": task_id},
            )
            self._record_success_response(
                response,
                url=url,
                elapsed_seconds=time.monotonic() - started,
                once_key=f"cancel:{task_id}",
            )
            return False
        if response.status_code >= 400:
            self._record_failure_response(response, url=url, elapsed_seconds=time.monotonic() - started)
            raise NmsProtocolError(
                f"NMS cancel returned HTTP {response.status_code}: {_excerpt(response.text)}",
                http_status=response.status_code,
                retry_after=_retry_after_seconds(response),
            )
        self._record_success_response(
            response,
            url=url,
            elapsed_seconds=time.monotonic() - started,
            once_key=f"cancel:{task_id}",
        )
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
                title="RAN task polled",
                title_key="nmsPoll",
                detail={"taskId": task_id, "state": last_state},
            )
            await asyncio.sleep(self._config.poll_interval)

    async def aclose(self) -> None:
        """Close the owned HTTP client."""
        if self._owns_http:
            await self._http.aclose()

    def _record_failure_response(
        self,
        response: httpx.Response,
        *,
        url: str,
        elapsed_seconds: float,
    ) -> None:
        """Record a non-200 response as an ``in`` frame in ``wire.jsonl``.

        A successful exchange writes its reply body; a failed one used to write nothing, so the
        status, the response headers and the time it took were lost. The vendor answers a quota
        rejection with an empty body, which made a rate-limited call indistinguishable from a
        call that was never logged.
        """
        self._write_wire(
            "in",
            method=response.request.method,
            url=url,
            status=response.status_code,
            headers=_failure_headers(response),
            body=response.text or None,
            elapsedMs=int(elapsed_seconds * 1000),
        )

    def _record_success_response(
        self,
        response: httpx.Response,
        *,
        url: str,
        elapsed_seconds: float,
        body: object | None = None,
        stream: bool = False,
        once_key: str = "",
    ) -> None:
        """Record one successful NMS response, including its headers, in ``wire.jsonl``.

        A vendor may echo its own trace id, request id or quota state on a successful response; the
        body alone cannot prove whether it did. Headers are captured in full and credentials are
        masked by :func:`redact` in ``_append_wire``. ``once_key`` keeps routine polls / task-list
        calls from adding the same header block to every poll of a long task.

        Args:
            response: successful HTTP response.
            url: request URL (the response may not carry a request in tests).
            elapsed_seconds: time to the response headers.
            body: parsed body, when the caller already has it.
            stream: mark the record as the stream opening (the body arrives as line records).
            once_key: optional key deduplicating repeated calls; empty records every response.
        """
        if once_key and once_key in self._response_header_seen:
            return
        request = getattr(response, "request", None)
        fields: dict[str, object] = {
            "method": getattr(request, "method", ""),
            "url": url,
            "status": response.status_code,
            "headers": _response_headers(response),
            "elapsedMs": int(elapsed_seconds * 1000),
        }
        if body is not None:
            fields["body"] = body
        if stream:
            fields["stream"] = True
        self._write_wire("in", **fields)
        if once_key:
            self._response_header_seen.add(once_key)

    def record_inbound(
        self,
        frame: Mapping[str, object],
        *,
        source: str,
        dropped: str = "",
    ) -> None:
        """Record one inbound push or poll reply in ``wire.jsonl`` (best effort).

        A vendor push never passes through ``message:send``, so without this the wire log proves
        only what the client sent. Failing to record must not change the protocol result. A frame
        the flow ignores is still recorded verbatim; ``dropped`` explains why it did not run.
        """
        try:
            fields: dict[str, object] = {"source": source}
            if dropped:
                fields["dropped"] = dropped
            self._write_wire("in", **fields, body=dict(frame))
        except Exception:  # noqa: BLE001 - observability must never change the protocol result
            _LOGGER.warning("failed to record an inbound %s frame in the wire log", source, exc_info=True)

    def _write_wire(self, direction: str, **fields: object) -> None:
        """Append one redacted record to the run's ``wire.jsonl``."""
        path = self._current_wire_path()
        if not self._config.wire_log or path is None:
            return
        record: dict[str, object] = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "dir": direction,
            **fields,
        }
        self._append_wire(path, record)

    def record_error(
        self,
        kind: str,
        message: str,
        frame: object | None = None,
        *,
        status: int | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        """Append one failed exchange to ``wire-error.jsonl``.

        The SDK or the raw reader may reject a frame before any ``wire.jsonl`` record is written;
        this keeps the frame that explains the failure. ``status``/``headers`` carry the parts of
        an HTTP rejection the body does not (the vendor's 429 body is empty).
        """
        path = self._current_wire_path()
        if not self._config.wire_log or path is None:
            return
        record: dict[str, object] = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "type": kind,
            "message": message,
        }
        if frame is not None:
            record["frame"] = frame
        if status is not None:
            record["status"] = status
        if headers:
            record["headers"] = dict(headers)
        self._append_wire(path.with_name("wire-error.jsonl"), record)

    def _append_wire(self, path: Path, record: Mapping[str, object]) -> None:
        """Append one redacted record to a wire log file."""
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(redact(record), ensure_ascii=False) + "\n")
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
        read_after_send: bool | None = None,
    ) -> None:
        """Create one transport.

        Args:
            client: raw NMS client.
            protocol: interaction protocol; only ``a2a-t`` sends extension headers.
            request_timeout: terminal-poll budget; defaults to the client configuration.
            read_after_send: keep reading a negotiation-response stream for the next proposal;
                defaults to the client configuration.
        """
        self._client = client
        self._protocol = protocol
        self._request_timeout = client.config.request_timeout if request_timeout is None else request_timeout
        self._read_after_send = client.config.read_after_send if read_after_send is None else read_after_send
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
        # The vendor answers a negotiation response on a new stream; after ACCEPT the next
        # proposal carries a *new* negotiation id on the same context id. Returning at the first
        # structured message would drop it, so response sends keep reading until the stream idles
        # or reaches a terminal Task.
        read_after_send = self._read_after_send and _is_negotiation_response(payload)
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
                        if not read_after_send:
                            return events
                        continue
                    if self._protocol == PROTOCOL_PURE:
                        # A pure request declares no Negotiation-T extension, so the vendor
                        # answers with a plain agent message. It is the turn boundary for the
                        # pure flow, exactly like the webhook transport already treats it.
                        event = _frame_to_event(frame)
                        events.append(event)
                        self._emit(event)
                        return events
                    context_id = _context_id(message)
                    text = _message_text(message)
                    self._client.record_error("NmsNegotiationRequiredError", text, frame)
                    _nms_span(
                        phase=PHASE_NEGOTIATION,
                        title="RAN side asked for more information",
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
            if self._protocol == PROTOCOL_PURE and _is_input_required(event):
                # Pure A2A: INPUT_REQUIRED is the server's turn boundary; do not wait for a
                # terminal state that arrives only after the client answers.
                return events
            if is_stop_event(event):
                break
        has_negotiation = any(
            isinstance(event.get("metadata"), Mapping) and _is_negotiation_metadata(event["metadata"])
            for event in events
        )
        if (
            task_id is not None
            and not has_negotiation
            and not any(is_terminal_event(event) for event in events)
            and not self._client.config.defer_to_tracker
        ):
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


class NmsWebhookTransport:
    """Client-flow transport for the vendor's non-streaming ``message:send`` + webhook mode.

    The real NMS is addressed with ``POST <base>/message:send``; task progress arrives through the
    embedded webhook receiver. The initial reply is inspected first because a negotiation message
    may be returned synchronously; pushed events are then consumed until a terminal state, a
    negotiation message or the idle backstop.
    """

    def __init__(
        self,
        client: NmsClient,
        *,
        queue: asyncio.Queue[dict[str, object]],
        push_url: str,
        push_token: str,
        push_auth_credentials: str = "",
        protocol: str = PROTOCOL_A2A_T,
        idle_timeout: float = DEFAULT_NMS_IDLE_TIMEOUT_SECONDS,
        bind: Callable[[str | None, str | None], None] | None = None,
        context_id: str = "",
        register_push_config: bool = True,
        retry_delays: tuple[float, ...] = STREAM_CONFLICT_RETRY_DELAYS,
        attached: asyncio.Event | None = None,
        on_task_bound: Callable[[str, str], Awaitable[None]] | None = None,
    ) -> None:
        """Create one webhook transport.

        Args:
            client: raw NMS client.
            queue: queue fed by the webhook router for this run only.
            push_url: callback URL registered inline on every request.
            push_token: push token registered inline; the vendor ignores it.
            push_auth_credentials: ``user:password`` registered as the push Basic auth. The vendor
                rejects a push config without ``id`` or ``authentication`` with 500 INTERNAL.
            protocol: interaction protocol; only ``a2a-t`` sends extension headers.
            idle_timeout: backstop when no push arrives.
            bind: optional callback binding ``(task_id, context_id)`` to this run's queue. The
                vendor ignores the push token, so task/context identity is the only way to route
                its pushes on a process-level receiver.
            context_id: optional client-assigned A2A context id stamped on the first message, so
                every pushed event is routable from its ``contextId`` before a Task reply arrives.
            register_push_config: whether a message that names no Task registers the webhook
                inline. A follow-up that answers a vendor question keeps the registration made by
                the original request; registering again is rejected by the real NMS.
            retry_delays: backoff schedule for a transient ``TASK_STREAM_CONFLICT``; tests pass an
                empty tuple to keep the retry immediate.
            attached: caller-attachment lease. When it is still unset, the phase waits for the
                next push without an idle deadline: the A2A client's stream is open, so the caller
                must see the vendor's question on that stream instead of a background handoff. The
                lease is set by the request handler when the caller disconnects.
            on_task_bound: called with ``(task_id, context_id)`` as soon as the vendor identity is
                known, so the outer↔downstream binding is durable before a long wait.
        """
        self._retry_delays = retry_delays
        self._client = client
        self._queue = queue
        self._push_url = push_url
        self._push_token = push_token
        self._push_auth_credentials = push_auth_credentials
        self._protocol = protocol
        self._idle_timeout = idle_timeout
        self._bind = bind
        self._context_id = context_id
        self._register_push_config = register_push_config
        self._attached = attached
        self._on_task_bound = on_task_bound
        self._event_sink: Any | None = None
        #: Status message ids already returned by a synchronous reply or consumed from a push.
        #: The NMS reports every status on both channels with one id, and its webhook retry sender
        #: may deliver it more than once; the phase-level dedupe lives here so every protocol and
        #: every round of the same run shares it.
        self._seen_message_ids: set[str] = set()

    def set_event_sink(self, event_sink: Any | None) -> None:
        """Attach the live event sink used by ``run_client_flow``."""
        self._event_sink = event_sink

    def _emit(self, event: dict[str, object]) -> None:
        """Forward one normalized event to the attached sink, when any."""
        if self._event_sink is not None:
            self._event_sink(event)

    def _remember_message(self, event: Mapping[str, object]) -> bool:
        """Record one inbound status message id; ``True`` when it was already seen.

        Only a push is dropped on a repeat. The synchronous reply of the current send is the
        authoritative answer to that request even when the same status was observed earlier, so a
        repeated id there must not swallow the phase's only event.
        """
        message_id = _optional_str(event.get("message_id"))
        if not message_id:
            return False
        if message_id in self._seen_message_ids:
            return True
        self._seen_message_ids.add(message_id)
        return False

    def _bind_context(self, request: SendMessageRequest) -> None:
        """Stamp and bind the run's A2A context before the request leaves the process.

        A Modify send already names the Task it continues, so the Task identity is handed over to
        this run before the HTTP call: a push that beats the reply is routable from the send on,
        without a window where only the previous run ever owned the Task.
        """
        if not self._context_id:
            return
        if not request.message.context_id:
            request.message.context_id = self._context_id
        if self._bind is not None:
            self._bind(_optional_str(request.message.task_id), request.message.context_id)

    async def send(
        self,
        request: SendMessageRequest,
        call_context: ClientCallContext,
    ) -> list[dict[str, object]]:
        """Send one non-streaming message, register the webhook, and collect its events."""
        self._bind_context(request)
        # Only the send that creates the Task asks for a non-blocking reply: the webhook contract
        # depends on it. A Modify (``task_id`` set) continues a Task that already exists, and a
        # Negotiation-T reply names no Task at all, so there is nothing to return immediately —
        # the vendor asks for those two to carry no ``returnImmediately`` (their own modify and
        # negotiation sample messages carry no ``configuration`` at all).
        if not request.message.task_id and not _is_negotiation_metadata(
            metadata_to_dict(request.message.metadata)
        ):
            request.configuration.return_immediately = True
        if (
            not request.message.task_id
            and self._register_push_config
            and not _is_negotiation_metadata(metadata_to_dict(request.message.metadata))
        ):
            # Register the webhook only on the message that creates the Task. A Modify send
            # continues an existing Task whose registration is still in force, and the vendor
            # answers a second registration for that Task with "config already exists". A
            # Negotiation-T reply creates nothing: the vendor rejects a push configuration on it,
            # even when the reply carries no task id.
            config = TaskPushNotificationConfig(id=str(uuid.uuid4()), url=self._push_url)
            if self._push_auth_credentials:
                # The vendor uses Basic authentication for the callback. Sending the SDK token as
                # well is noise the NMS may ignore (or reject); keep the two modes exclusive.
                config.authentication.scheme = "Basic"
                config.authentication.credentials = self._push_auth_credentials
            elif self._push_token:
                config.token = self._push_token
            request.configuration.task_push_notification_config.CopyFrom(config)
        extension_header = ""
        if self._protocol == PROTOCOL_A2A_T:
            extension_header = str(call_context.service_parameters.get("A2A-Extensions", "") or "")
        payload = MessageToDict(request)
        response_mode = _is_negotiation_response(payload)
        reply = await self._send_with_stream_conflict_retry(payload, extension_header)
        events: list[dict[str, object]] = []
        reply_event = _reply_event(reply)
        # The vendor also pushes this same status; remember the reply's id before waiting so the
        # queued twin is recognised regardless of which channel arrives first.
        self._remember_message(reply_event)
        events.append(reply_event)
        self._emit(reply_event)
        task_id = _optional_str(reply_event.get("task_id"))
        event_context_id = _optional_str(reply_event.get("context_id"))
        if self._bind is not None:
            # The vendor ignores the push token, so the task/context identity is the only way to
            # tell this run's pushes from another concurrent run's on the shared callback port.
            self._bind(task_id, event_context_id)
        if self._on_task_bound is not None and task_id:
            # Persist the outer↔downstream identity before a potentially hours-long wait: an OSS
            # restart adopts the Task from it.
            await self._on_task_bound(task_id, event_context_id or "")
        if self._protocol == PROTOCOL_PURE and _is_input_required(reply_event):
            return events
        if _is_negotiation_event(reply_event):
            # A synchronous negotiation reply is the whole response for an initial send. For a
            # negotiation response, the vendor may push the next proposal on the same webhook, so
            # keep waiting like the SSE response path.
            if not response_mode:
                return events
        elif is_stop_event(reply_event):
            return events
        # A process-owned run manager keeps polling the Task itself, so it does not need to
        # hold this callback quiet for the whole HTTP budget; it takes over after the shorter
        # silence window. Without a tracker the phase keeps the original idle backstop.
        budget = (
            self._client.config.poll_after_silence
            if self._client.config.defer_to_tracker and task_id
            else self._idle_timeout
        )
        # One budget per phase, counted from the reply: a vendor that keeps pushing (every 15 s
        # forever) must not restart the clock, or the phase and its caller never end. While the
        # caller's A2A stream is still attached there is no deadline at all: the caller is reading
        # this phase and must receive the vendor's next question on its own stream.
        deadline = asyncio.get_running_loop().time() + budget
        while True:
            pushed: dict[str, object]
            if self._attached is not None and not self._attached.is_set():
                pushed_or_none = await self._wait_attached()
                if pushed_or_none is None:
                    # The caller disconnected: return what we have and let the OSS hand the Task
                    # to the background pump.
                    _LOGGER.info("caller detached; returning the events received so far")
                    return events
                pushed = pushed_or_none
            else:
                remaining = max(deadline - asyncio.get_running_loop().time(), 0.0)
                try:
                    pushed = await asyncio.wait_for(self._queue.get(), timeout=remaining)
                except asyncio.TimeoutError:
                    _LOGGER.info(
                        "NMS webhook phase idle for %.0fs; returning the events received%s",
                        budget,
                        " (handing the task to background tracking)" if budget != self._idle_timeout else "",
                    )
                    return events
            event = _frame_to_event(pushed)
            duplicate = self._remember_message(event)
            # Record the frame even when the flow ignores it: wire.jsonl is evidence, and a
            # dropped duplicate must stay visible (with its reason) rather than disappear.
            recorder = getattr(self._client, "record_inbound", None)
            if callable(recorder):
                recorder(pushed, source="webhook", dropped="duplicate" if duplicate else "")
            if duplicate:
                _LOGGER.info(
                    "ignoring a duplicate webhook push: message_id=%s state=%s",
                    event.get("message_id"),
                    event.get("state"),
                )
                continue
            events.append(event)
            self._emit(event)
            task_id = _optional_str(event.get("task_id")) or task_id
            if self._protocol == PROTOCOL_PURE and _is_input_required(event):
                return events
            if is_stop_event(event) or is_report_artifact(event) or _is_negotiation_event(event):
                return events

    async def _wait_attached(self) -> dict[str, object] | None:
        """Wait for the next push while the caller is attached; ``None`` when it disconnects.

        No idle deadline applies here: the caller's A2A stream owns the phase, and the A2A client
        stops its stream at ``INPUT_REQUIRED``, so a question reaches the page directly instead of
        a background handoff.
        """
        assert self._attached is not None
        push_task = asyncio.create_task(self._queue.get())
        detach_task = asyncio.create_task(self._attached.wait())
        try:
            done, _ = await asyncio.wait(
                {push_task, detach_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
        finally:
            for task in (push_task, detach_task):
                if not task.done():
                    task.cancel()
        if push_task in done:
            return push_task.result()
        return None

    async def _send_with_stream_conflict_retry(
        self,
        payload: Mapping[str, object],
        extension_header: str,
    ) -> dict[str, object]:
        """Send once, retrying the vendor's transient per-Task stream conflict.

        The vendor answers a follow-up while it still considers the Task stream active with HTTP
        409 ``TASK_STREAM_CONFLICT``. The webhook path owns no stream to close, so the client waits
        briefly and retries the same message; the message id is unchanged, so a vendor that
        deduplicates does not apply the answer twice.
        """
        attempt = 0
        while True:
            try:
                return await self._client.send_message(payload, extension_header=extension_header)
            except NmsProtocolError as exc:
                if attempt >= len(self._retry_delays) or not is_stream_conflict(exc):
                    raise
                delay = self._retry_delays[attempt]
                attempt += 1
                _LOGGER.warning(
                    "NMS task stream busy (TASK_STREAM_CONFLICT); retrying in %.0fs (attempt %d/%d)",
                    delay,
                    attempt,
                    len(self._retry_delays),
                )
                await asyncio.sleep(delay)

    async def cancel(self, task_id: str, call_context: ClientCallContext | None = None) -> None:
        """Cancel one Task; the known HTTP 503 is not an error."""
        del call_context
        await self._client.cancel_task(task_id)


def _reply_event(reply: Mapping[str, object]) -> dict[str, object]:
    """Normalize one ``message:send`` reply into the shared event shape."""
    raw = dict(reply)
    if "task" in reply or "message" in reply:
        event = _frame_to_event(reply)
    elif isinstance(reply.get("id"), str) and "status" in reply:
        event = _frame_to_event({"task": reply})
    elif isinstance(reply.get("messageId"), str):
        event = _frame_to_event({"message": reply})
    else:
        event = _frame_to_event(reply)
    # The reply the vendor sent, before templateSpecific shaping: this is the raw response the
    # On-the-wire panel must show.
    event["wireBody"] = raw
    return event


def _is_negotiation_event(event: Mapping[str, object]) -> bool:
    """Return whether one normalized event carries structured negotiation metadata."""
    metadata = event.get("metadata")
    return isinstance(metadata, Mapping) and _is_negotiation_metadata(metadata)


def _frame_to_event(frame: Mapping[str, object]) -> dict[str, object]:
    """Convert one raw JSON frame into the normalized event shape."""
    raw = dict(frame)
    try:
        response = StreamResponse()
        ParseDict(stream_response_payload(frame), response)
        event = normalize_event(response)
    except (ParseError, TypeError, ValueError):
        return {"kind": "raw", "frame": raw, "wireBody": raw}
    event["wireBody"] = raw
    return event


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


def _optional_str(value: object) -> str | None:
    """Return a stripped string value, ``None`` for a missing or non-string value."""
    if not isinstance(value, str):
        return None
    return value.strip() or None


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
    if not compact:
        # The vendor answers a quota rejection with an empty body; an empty excerpt would make
        # the error read ``HTTP 429: `` and hide that the body was in fact empty.
        return "<empty body>"
    return compact[:limit] + ("..." if len(compact) > limit else "")


def _failure_headers(response: httpx.Response) -> dict[str, str]:
    """Return the whitelisted headers of one failed response (credentials are never included)."""
    return {name: response.headers[name] for name in _FAILURE_HEADER_NAMES if name in response.headers}


def _response_headers(response: httpx.Response) -> dict[str, str]:
    """Return every header of one successful response; :func:`redact` masks credentials."""
    return {name: value for name, value in response.headers.items()}


def _retry_after_seconds(response: httpx.Response) -> float | None:
    """Return the response's ``Retry-After`` in seconds, or ``None`` when absent or unusable.

    Only the delta-seconds form is parsed; an HTTP-date is ignored so a parsing mistake can never
    produce a huge or negative delay.
    """
    value = response.headers.get("retry-after", "").strip()
    if not value:
        return None
    try:
        seconds = float(value)
    except ValueError:
        return None
    return seconds if seconds > 0 else None


def _is_negotiation_metadata(metadata: Mapping[str, object]) -> bool:
    """Return whether one message metadata map carries a structured negotiation."""
    return NEGOTIATION_T_EXTENSION_URI in metadata or NEGOTIATION_CONTEXT_METADATA_KEY in metadata


def _is_input_required(event: Mapping[str, object]) -> bool:
    """Return whether one normalized event hands the turn back to the client (pure A2A)."""
    return event.get("state") == "TASK_STATE_INPUT_REQUIRED"


def _is_negotiation_response(payload: Mapping[str, object]) -> bool:
    """Return whether an outbound payload is a negotiation response (not the initial Task-T)."""
    message = payload.get("message")
    if not isinstance(message, Mapping):
        return False
    metadata = message.get("metadata")
    if not isinstance(metadata, Mapping):
        return False
    context = metadata.get(NEGOTIATION_CONTEXT_METADATA_KEY)
    if not isinstance(context, Mapping):
        return False
    performative = context.get("performative")
    return isinstance(performative, str) and performative.upper() in {
        "ACCEPT",
        "PROPOSE",
        "REJECT",
        "ABORT",
    }


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
    """Emit one developer-evidence span of the raw NMS exchange.

    The kind is ``transport``, not ``tool``: these rows are wire evidence (token acquisition,
    stream opens, every poll), not model tool calls. Only the ``full`` trace level exports them,
    so a real-NMS long task does not add one ``tool`` observation per poll.
    """
    payload = dict(detail or {})
    payload["titleKey"] = title_key
    record_span(
        kind="transport",
        phase=phase,
        title=title,
        prefix="nms",
        actor="nms",
        side="client",
        detail=payload,
    )


def _is_hex(value: str, length: int) -> bool:
    """Return whether ``value`` is exactly ``length`` lowercase hex digits."""
    return len(value) == length and all(char in "0123456789abcdef" for char in value)
