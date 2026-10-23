"""Embedded webhook push receiver for the ran-energy-saving client (webhook transport mode).

In webhook transport mode the client does not consume the SSE stream: the server's push sender
delivers each task event as an HTTP POST whose JSON body is the ``MessageToDict`` of a
``StreamResponse`` — the same shape the SSE stream carries, one of the ``task`` /
``statusUpdate`` / ``artifactUpdate`` / ``message`` top-level keys — and whose
``X-A2A-Notification-Token`` header carries the registered push token. ``WebhookReceiver`` serves
the single ``POST /webhook`` route: a missing or wrong token answers ``401`` and the event is
dropped; an unparseable JSON body, or one without a recognized ``StreamResponse`` key, answers
``400``; a valid event is handed to the injected callback as the raw parsed dict (typically
enqueueing it on the client's asyncio loop). The receiver stays transport-agnostic on purpose —
the dict-to-event adaptation lives with the caller, not here.

Hosting follows the client's lifecycle: ``start()`` runs ``uvicorn.Server.serve()`` as an asyncio
task on the running event loop and resolves the bound port before returning (an OS-assigned
ephemeral port when constructed with ``port=0``); ``stop()`` shuts the server down gracefully and
is idempotent. Uvicorn's own signal handling is disabled (the pre-uvicorn-0.52
``install_signal_handlers=False`` semantics, which that version removed — reimplemented by
``_HostSignalServer``) so Ctrl+C in the host client process raises ``KeyboardInterrupt`` exactly
as it does in SSE mode — the client's ``run_main`` ``finally`` then calls ``stop()`` (same
semantics as the SSE path).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import time
from collections.abc import Callable

import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from common.logging_utils import format_stage_log

#: Default bind address and port of the embedded receiver (mirrors the ``A2AT_SAMPLE_WEBHOOK_PORT``
#: resolver default in ``client.scenario_data``).
DEFAULT_WEBHOOK_HOST = "127.0.0.1"
DEFAULT_WEBHOOK_PORT = 9000

#: Path of the single webhook route (the path part of the registered push-config URL).
_WEBHOOK_PATH = "/webhook"

#: Push-token header carried by the a2a-sdk push sender on every webhook POST.
_TOKEN_HEADER = "X-A2A-Notification-Token"

#: Top-level keys of a StreamResponse JSON body (``MessageToDict`` camelCase field names); a body
#: carrying none of them is not a task event.
_STREAM_RESPONSE_KEYS = ("task", "statusUpdate", "artifactUpdate", "message")

#: Budgets of the embedded receiver: the poll deadline for the uvicorn bind, and the graceful
#: drain budget of ``stop()``.
_STARTUP_TIMEOUT_SECONDS = 10.0
_STOP_TIMEOUT_SECONDS = 5.0


class _HostSignalServer(uvicorn.Server):
    """uvicorn Server that leaves process-signal handling to the host.

    Equivalent to the retired ``Config.install_signal_handlers=False``: the bundled uvicorn 0.52
    removed that option, and ``Server.serve()`` on the main thread calls ``capture_signals()``
    unconditionally. Left as is, the first Ctrl+C would make uvicorn gracefully shut the receiver
    down while the flow keeps running, which differs from the KeyboardInterrupt semantics of SSE
    mode. Overriding it as a no-op lets Ctrl+C raise KeyboardInterrupt in the host ``client_main``,
    whose ``run_main`` ``finally`` stops the receiver — so webhook and SSE mode behave identically
    on Ctrl+C.
    """

    @contextlib.contextmanager
    def capture_signals(self):  # type: ignore[override]
        yield


def _recognized_stream_key(payload: object) -> str | None:
    """Return the recognized StreamResponse top-level key of ``payload``, ``None`` when none."""
    if not isinstance(payload, dict):
        return None
    for key in _STREAM_RESPONSE_KEYS:
        if key in payload:
            return key
    return None


class WebhookReceiver:
    """In-process webhook receiver serving one authenticated ``POST /webhook`` route.

    The message contract is the a2a-sdk push-notification POST: a StreamResponse JSON body (one of
    the ``task`` / ``statusUpdate`` / ``artifactUpdate`` / ``message`` top-level keys) with the
    push token in the ``X-A2A-Notification-Token`` header. A token mismatch answers ``401`` and
    the event is dropped; a malformed or unrecognized body answers ``400``; a valid event is
    passed to ``on_event`` as the raw parsed dict and answered with ``200``.
    """

    def __init__(
        self,
        *,
        token: str,
        on_event: Callable[[dict[str, object]], None],
        host: str = DEFAULT_WEBHOOK_HOST,
        port: int = DEFAULT_WEBHOOK_PORT,
        log_sink: Callable[[str], None] | None = None,
    ) -> None:
        self._token = token
        self._on_event = on_event
        self._host = host
        self._port = port
        self._log_sink: Callable[[str], None] = log_sink if log_sink is not None else print
        self._resolved_port = port
        self._server: uvicorn.Server | None = None
        self._serve_task: asyncio.Task[None] | None = None
        self._app = Starlette(routes=[Route(_WEBHOOK_PATH, self._handle_webhook, methods=["POST"])])

    @property
    def port(self) -> int:
        """The resolved port: the configured one, or the OS-assigned port once started with 0."""
        return self._resolved_port

    @property
    def url(self) -> str:
        """The webhook callback URL to register in the task push-notification config."""
        return f"http://{self._host}:{self.port}{_WEBHOOK_PATH}"

    async def start(self) -> None:
        """Serve the webhook route as an asyncio task on the running event loop.

        Waits for the uvicorn server to bind before returning (with ``port=0`` the OS-assigned
        port is read back from the server); a bind failure (e.g. the port is already taken), or a
        startup that does not complete within ``_STARTUP_TIMEOUT_SECONDS``, raises ``RuntimeError``.
        """
        if self._serve_task is not None:
            return
        config = uvicorn.Config(self._app, host=self._host, port=self._port, log_level="warning", access_log=False)
        server: uvicorn.Server = _HostSignalServer(config)
        serve_task = asyncio.create_task(self._serve(server), name="webhook-receiver")
        deadline = time.monotonic() + _STARTUP_TIMEOUT_SECONDS
        while not server.started:
            if serve_task.done():
                failure = serve_task.exception()
                if failure is not None:
                    raise failure  # the bind-failure conversion (or an unexpected serve error)
                raise RuntimeError(f"webhook receiver on {self._host}:{self._port} exited before listening")
            if time.monotonic() >= deadline:
                serve_task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await serve_task
                raise RuntimeError(
                    f"webhook receiver startup did not complete within {_STARTUP_TIMEOUT_SECONDS:.0f}s "
                    f"on {self._host}:{self._port}"
                )
            await asyncio.sleep(0.01)
        self._server = server
        self._serve_task = serve_task
        if self._port == 0:
            self._resolved_port = int(server.servers[0].sockets[0].getsockname()[1])
        self._log("listening", f"url={self.url}")

    async def _serve(self, server: uvicorn.Server) -> None:
        """Run ``server.serve()``, converting uvicorn's bind-failure ``SystemExit`` to ``RuntimeError``.

        uvicorn's ``startup()`` answers a bind failure (e.g. the port is already taken) with
        ``sys.exit``; a task-raised ``SystemExit`` is re-raised through the event loop (tearing it
        down) instead of being observable on the serve task, so it must be converted inside the
        coroutine for the ``start()`` contract to hold.
        """
        try:
            await server.serve()
        except SystemExit as exc:
            raise RuntimeError(f"webhook receiver failed to start on {self._host}:{self._port}") from exc

    async def stop(self) -> None:
        """Shut the receiver down gracefully and release the port (idempotent)."""
        serve_task = self._serve_task
        server = self._server
        self._serve_task = None
        self._server = None
        if serve_task is None or server is None:
            return
        server.should_exit = True  # graceful: uvicorn stops accepting and drains
        try:
            await asyncio.wait_for(serve_task, timeout=_STOP_TIMEOUT_SECONDS)
        except asyncio.TimeoutError:
            # wait_for (3.12+) already cancelled and reaped the serve task on timeout; the drain
            # simply exceeded the budget, so record it instead of forcing anything — and return
            # WITHOUT the ``stopped`` line: the graceful stop did not complete.
            self._log("stop-timeout", f"url={self.url} graceful stop exceeded {_STOP_TIMEOUT_SECONDS:.0f}s")
            return
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._log("stop-error", f"{type(exc).__name__}: {exc}")
        self._log("stopped", f"url={self.url}")

    async def _handle_webhook(self, request: Request) -> Response:
        """Authenticate, parse and deliver one webhook POST (the class docstring's contract)."""
        if request.headers.get(_TOKEN_HEADER, "") != self._token:
            self._log("token-rejected", f"status=401 header={_TOKEN_HEADER} mismatch")
            return JSONResponse(status_code=401, content={"error": "missing or invalid X-A2A-Notification-Token"})
        raw_body = await request.body()
        try:
            payload = json.loads(raw_body)
        except ValueError:
            self._log("body-rejected", "status=400 malformed JSON body")
            return JSONResponse(status_code=400, content={"error": "malformed JSON body"})
        event_type = _recognized_stream_key(payload)
        if event_type is None:
            self._log("body-rejected", "status=400 no recognized StreamResponse key")
            return JSONResponse(status_code=400, content={"error": "unrecognized StreamResponse body"})
        self._log("event-received", f"type={event_type} bytes={len(raw_body)}")
        self._on_event(payload)
        return JSONResponse(content={"received": event_type})

    def _log(self, stage: str, detail: str) -> None:
        self._log_sink(format_stage_log(role="webhook-receiver", stage=stage, detail=detail))
