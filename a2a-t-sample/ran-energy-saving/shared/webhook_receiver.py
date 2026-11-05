"""Embedded webhook push receiver for the ran-energy-saving client (webhook transport mode).

In webhook transport mode the client does not consume the SSE stream: the server's push sender
delivers each task event as an HTTP POST whose JSON body is the ``MessageToDict`` of a
``StreamResponse`` — the same shape the SSE stream carries, one of the ``task`` /
``statusUpdate`` / ``artifactUpdate`` / ``message`` top-level keys. The a2a-sdk push sender carries
the registered push token in ``X-A2A-Notification-Token``; the vendor NMS has no push-token concept
and authenticates with ``Authorization: Basic`` instead, so the receiver supports both. ``WebhookReceiver``
serves the single ``POST /webhook`` route: a missing or wrong credential answers ``401`` and the
event is dropped; an unparseable JSON body, or one without a recognized ``StreamResponse`` key,
answers ``400``; a valid event is handed to the injected callback as the raw parsed dict (typically
enqueueing it on the client's asyncio loop). The receiver stays transport-agnostic on purpose —
the dict-to-event adaptation lives with the caller, not here.

The bind address is not necessarily the address the server reaches: ``public_url`` is registered in
the push config instead, and ``tls_cert`` / ``tls_key`` make the listener serve https (the vendor
NMS requires an https callback).

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
import base64
import contextlib
import json
import logging
import time
from collections.abc import Callable

import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

_LOGGER = logging.getLogger("ran_energy_saving.webhook_receiver")


class WebhookPortInUseError(RuntimeError):
    """The webhook callback port is pinned by configuration and cannot be bound."""


async def start_receiver_with_fallback(
    *,
    make: Callable[[int], WebhookReceiver],
    port: int,
    public_url: str = "",
) -> WebhookReceiver:
    """Start one receiver, falling back to an OS-assigned port when the configured one is taken.

    A public URL pins the callback to the configured port because a gateway forwards exactly that
    address; a collision there is reported instead of registering a port the server cannot reach.
    Without one, the server reaches the bind address directly, so an ephemeral port keeps a second
    run (or a second instance) usable instead of failing after 90 ms.

    Args:
        make: factory returning a fresh receiver for one port (``0`` asks the OS).
        port: configured callback port.
        public_url: externally reachable callback URL, empty when the bind address is registered.

    Returns:
        The started receiver; its ``url`` carries the resolved port.

    Raises:
        WebhookPortInUseError: when the configured port is pinned by a public URL and is taken.
    """
    receiver = make(port)
    try:
        await receiver.start()
        return receiver
    except RuntimeError as exc:
        if public_url or port == 0:
            raise WebhookPortInUseError(str(exc)) from exc
        _LOGGER.warning(
            "webhook callback port %s is busy; retrying on an OS-assigned port "
            "(concurrent runs are fine, but the NMS must reach the new port)",
            port,
        )
    fallback = make(0)
    await fallback.start()
    return fallback


#: Default bind address and port of the embedded receiver (mirrors the ``A2AT_SAMPLE_WEBHOOK_PORT``
#: resolver default in ``client.scenario_data``).
DEFAULT_WEBHOOK_HOST = "127.0.0.1"
DEFAULT_WEBHOOK_PORT = 9000

#: Path of the single webhook route (the path part of the registered push-config URL).
_WEBHOOK_PATH = "/webhook"

#: Push-token header carried by the a2a-sdk push sender on every webhook POST. The real NMS does
#: not send it (it has no push token concept); it authenticates with Basic auth instead.
_TOKEN_HEADER = "X-A2A-Notification-Token"

#: Header carrying the optional Basic credentials the real NMS sends with every push.
_AUTH_HEADER = "Authorization"

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
        public_url: str = "",
        tls_cert: str = "",
        tls_key: str = "",
        auth_credentials: str = "",
    ) -> None:
        self._token = token
        self._on_event = on_event
        self._host = host
        self._port = port
        self._public_url = public_url
        self._tls_cert = tls_cert
        self._tls_key = tls_key
        self._auth_header = (
            f"Basic {base64.b64encode(auth_credentials.encode()).decode()}" if auth_credentials else ""
        )
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
        """The webhook callback URL to register in the task push-notification config.

        The configured public URL wins: the real NMS requires ``https`` and reaches the client
        through a gateway address that is not the bind address. Otherwise the bind address is used
        with the scheme of the served listener.
        """
        if self._public_url:
            return self._public_url
        scheme = "https" if self._tls_cert else "http"
        return f"{scheme}://{self._host}:{self.port}{_WEBHOOK_PATH}"

    async def start(self) -> None:
        """Serve the webhook route as an asyncio task on the running event loop.

        Waits for the uvicorn server to bind before returning (with ``port=0`` the OS-assigned
        port is read back from the server); a bind failure (e.g. the port is already taken), or a
        startup that does not complete within ``_STARTUP_TIMEOUT_SECONDS``, raises ``RuntimeError``.
        """
        if self._serve_task is not None:
            return
        config = uvicorn.Config(
            self._app,
            host=self._host,
            port=self._port,
            log_level="warning",
            access_log=False,
            ssl_certfile=self._tls_cert or None,
            ssl_keyfile=self._tls_key or None,
        )
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
            raise RuntimeError(
                f"webhook receiver cannot bind {self._host}:{self._port}: the address is already "
                "in use by another process. Another run may still be waiting for an NMS push "
                "(the idle timeout can hold it for minutes); wait for it to finish or cancel it, "
                "set A2AT_SAMPLE_WEBHOOK_PORT to a free port, or give each instance its own "
                "A2AT_SAMPLE_RUN_DIR and ports."
            ) from exc

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
        received = request.headers.get(_AUTH_HEADER, "")
        if self._auth_header and received != self._auth_header:
            # Never log the value: it is the sender's credential. The shape tells us whether the
            # NMS echoed the push config or used different/basic-mangled credentials.
            scheme, _, payload = received.partition(" ")
            self._log(
                "auth-rejected",
                f"status=401 Basic credentials mismatch scheme={scheme or '<none>'} "
                f"present={bool(received)} length={len(payload)}",
            )
            return JSONResponse(status_code=401, content={"error": "missing or invalid Basic credentials"})
        if not self._auth_header and request.headers.get(_TOKEN_HEADER, "") != self._token:
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
        """Emit one receiver event at the level its operational meaning deserves."""
        level = {
            "auth-rejected": logging.WARNING,
            "token-rejected": logging.WARNING,
            "body-rejected": logging.WARNING,
            "stop-error": logging.ERROR,
            "stop-timeout": logging.WARNING,
            "event-received": logging.DEBUG,
        }.get(stage, logging.INFO)
        _LOGGER.log(level, "webhook %s: %s", stage, detail)
