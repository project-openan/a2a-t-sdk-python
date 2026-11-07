"""A2A and session routes of the NMS simulator."""

from __future__ import annotations

import json
import logging
import uuid
from collections.abc import Mapping

from a2a_t.core.metadata import NEGOTIATION_T_EXTENSION_URI, TASK_T_EXTENSION_URI
from starlette.requests import Request
from starlette.responses import JSONResponse, Response, StreamingResponse
from starlette.routing import BaseRoute, Route

from demo import ran_data
from demo.nms_sim.config import SimConfig
from demo.nms_sim.executor import apply_intent, report_artifact, structured_output
from demo.nms_sim.intent import (
    ParsedIntent,
    SimNegotiationRequiredError,
    SimRequestError,
    declares_negotiation,
    extension_header_value,
    message_text,
    parse_message_request,
)
from demo.nms_sim.sse import message_stream, status_stream, task_stream
from demo.nms_sim.tasks import (
    TASK_CANCELED,
    TASK_INPUT_REQUIRED,
    TASK_REJECTED,
    TASK_SUBMITTED,
    TERMINAL_STATES,
    SimTask,
    TaskStore,
)
from demo.nms_sim.token import TokenStore

_LOGGER = logging.getLogger("ran_energy_saving.nms_sim")

#: SSE media type.
EVENT_STREAM: str = "text/event-stream"


def _bearer_token(request: Request) -> str:
    """Return the bearer token of one request, from either accepted header."""
    header_token = request.headers.get("x-auth-token", "").strip()
    if header_token:
        return header_token
    authorization = request.headers.get("authorization", "").strip()
    scheme, _, value = authorization.partition(" ")
    return value.strip() if scheme.lower() == "bearer" else ""


async def _json_body(request: Request) -> Mapping[str, object] | None:
    """Read a JSON object body, or ``None`` when it is malformed."""
    try:
        body = await request.json()
    except ValueError:
        return None
    return body if isinstance(body, Mapping) else None


def build_routes(
    config: SimConfig,
    *,
    store: TaskStore | None = None,
    tokens: TokenStore | None = None,
) -> tuple[list[BaseRoute], TaskStore, TokenStore]:
    """Build the simulator routes and their stores."""
    resolved_store = store or TaskStore(limit=config.task_limit)
    resolved_tokens = tokens or TokenStore(config)

    def unauthorized() -> JSONResponse:
        return JSONResponse(
            {"error": {"code": 401, "message": "A valid session token is required"}},
            status_code=401,
            headers={"WWW-Authenticate": "Bearer"},
        )

    def authorized(request: Request) -> bool:
        token = _bearer_token(request)
        return bool(token) and resolved_tokens.valid(token)

    async def sessions(request: Request) -> Response:
        """Issue one session token."""
        body = await _json_body(request)
        if body is None:
            return JSONResponse({"error": "invalid JSON body"}, status_code=400)
        if str(body.get("grantType", "")) != "password":
            return JSONResponse({"error": "unsupported grantType"}, status_code=400)
        token = resolved_tokens.issue(
            user=str(body.get("userName", "")),
            value=str(body.get("value", "")),
        )
        if token is None:
            return JSONResponse({"error": "invalid credentials"}, status_code=401)
        return JSONResponse(
            {"accessSession": token, "expires": config.token_ttl_seconds},
            status_code=200,
        )

    def _completed_response(
        task: SimTask,
        intent: ParsedIntent,
        config: SimConfig,
        *,
        hold: bool = False,
    ) -> Response:
        """Stream one parsed request from execution to its completed report."""
        output = structured_output(intent)
        task.message = f"Task creation succeeded{_json_compact(output)}"
        try:
            report = apply_intent(intent)
        except ran_data.RanDataError as exc:
            _LOGGER.warning("simulator execution failed: %s", exc)
            return JSONResponse(
                {"error": {"code": 500, "message": str(exc)}},
                status_code=500,
            )
        artifact = report_artifact(task.task_id, report)
        return StreamingResponse(
            task_stream(task, report=report, artifact=artifact, config=config, hold=hold),
            media_type=EVENT_STREAM,
            headers={"Cache-Control": "no-cache"},
        )

    def _pure_response(body: Mapping[str, object], request: Request) -> Response:
        """Handle one plain A2A request: ask for missing values and resume on the follow-up."""
        message = body.get("message")
        message_map = message if isinstance(message, Mapping) else {}
        text = message_text(message_map)
        requested_task_id = message_map.get("taskId") or message_map.get("task_id")
        task = (
            resolved_store.get(str(requested_task_id))
            if isinstance(requested_task_id, str)
            else None
        )
        if task is None:
            task = resolved_store.create(
                context_id=_context_id(body) or str(uuid.uuid4()),
                message="Task creation succeeded",
            )
            task.pure_text = text
        else:
            task.pure_text = f"{task.pure_text}\n{text}".strip()
            task.pure_round += 1
            resolved_store.update(
                task.task_id, state=TASK_SUBMITTED, message="Task creation succeeded"
            )
        synthetic = {"message": {"parts": [{"text": task.pure_text}], "metadata": {}}}
        try:
            intent = parse_message_request(synthetic, {}, config)
        except SimNegotiationRequiredError as exc:
            if task.pure_round >= task.pure_max_rounds:
                task.state = TASK_REJECTED
                task.message = f"Negotiation did not converge: {exc.text}"
            else:
                task.state = TASK_INPUT_REQUIRED
                task.message = exc.text
            return StreamingResponse(
                status_stream(task, config=config),
                media_type=EVENT_STREAM,
                headers={"Cache-Control": "no-cache"},
            )
        except SimRequestError as exc:
            return JSONResponse(exc.payload, status_code=exc.status_code)
        return _completed_response(task, intent, config)

    def _is_pure_request(body: Mapping[str, object], request: Request) -> bool:
        """Return whether the message is plain A2A (no Task-T / Negotiation-T extension)."""
        message = body.get("message")
        if not isinstance(message, Mapping):
            return False
        metadata = message.get("metadata")
        metadata_map = metadata if isinstance(metadata, Mapping) else {}
        if extension_header_value(request.headers):
            return False
        return (
            TASK_T_EXTENSION_URI not in metadata_map
            and NEGOTIATION_T_EXTENSION_URI not in metadata_map
        )

    async def send_message(request: Request) -> Response:
        """Accept one A2A message and stream the Task or negotiation message."""
        if not authorized(request):
            return unauthorized()
        body = await _json_body(request)
        if body is None:
            return JSONResponse({"error": "invalid JSON body"}, status_code=400)
        if _is_pure_request(body, request):
            return _pure_response(body, request)
        try:
            intent = parse_message_request(body, request.headers, config)
        except SimRequestError as exc:
            return JSONResponse(exc.payload, status_code=exc.status_code)
        except SimNegotiationRequiredError as exc:
            return StreamingResponse(
                message_stream(
                    exc.text,
                    context_id=_context_id(body),
                    config=config,
                    structured=declares_negotiation(request.headers),
                ),
                media_type=EVENT_STREAM,
                headers={"Cache-Control": "no-cache"},
            )

        message = body.get("message")
        message_map = message if isinstance(message, Mapping) else {}
        requested_task_id = message_map.get("taskId") or message_map.get("task_id")
        task = resolved_store.get(str(requested_task_id)) if isinstance(requested_task_id, str) else None
        is_new = task is None
        if task is None:
            task = resolved_store.create(
                context_id=_context_id(body) or str(uuid.uuid4()),
                message="Task creation succeeded",
            )
        else:
            resolved_store.update(task.task_id, state=TASK_SUBMITTED, message="Task creation succeeded")
        # The hold mode keeps a newly created Task open for the Modify picker; a continuation
        # (the Modify itself) completes normally.
        return _completed_response(task, intent, config, hold=config.hold_working and is_new)

    async def list_tasks(request: Request) -> Response:
        """List the latest Tasks."""
        if not authorized(request):
            return unauthorized()
        return JSONResponse({"tasks": resolved_store.list()}, status_code=200)

    async def get_task(request: Request) -> Response:
        """Return one Task."""
        if not authorized(request):
            return unauthorized()
        task = resolved_store.get(str(request.path_params["task_id"]))
        if task is None:
            return JSONResponse({"error": "Task not found"}, status_code=404)
        return JSONResponse({"task": task.to_dict()}, status_code=200)

    async def cancel_task(request: Request) -> Response:
        """Cancel one non-terminal Task.

        ``cancel_supported=False`` reproduces the vendor's older HTTP 503 answer; otherwise a
        terminal Task is returned unchanged so a repeated cancel stays idempotent.
        """
        if not authorized(request):
            return unauthorized()
        if not config.cancel_supported:
            return JSONResponse(
                {"error": {"code": 503, "message": "Task cancellation is unavailable"}},
                status_code=503,
            )
        task_id = str(request.path_params["task_id"])
        task = resolved_store.get(task_id)
        if task is None:
            return JSONResponse({"error": "Task not found"}, status_code=404)
        if task.state not in TERMINAL_STATES:
            resolved_store.update(task_id, state=TASK_CANCELED, message="Task canceled.")
        return JSONResponse({"task": task.to_dict()}, status_code=200)

    routes: list[BaseRoute] = [
        Route("/rest/plat/smapp/v1/sessions", sessions, methods=["PUT"]),
        Route("/a2a/json/message:stream", send_message, methods=["POST"]),
        Route("/a2a/json/tasks", list_tasks, methods=["GET"]),
        Route("/a2a/json/tasks/{task_id}", get_task, methods=["GET"]),
        Route("/a2a/json/tasks/{task_id}:cancel", cancel_task, methods=["POST"]),
    ]
    return routes, resolved_store, resolved_tokens


def _context_id(body: Mapping[str, object]) -> str | None:
    """Return the message context id, when present."""
    message = body.get("message")
    if not isinstance(message, Mapping):
        return None
    for key in ("contextId", "context_id"):
        value = message.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return None


def _json_compact(payload: Mapping[str, object]) -> str:
    """Render the vendor's compact structured suffix."""
    return json.dumps(dict(payload), ensure_ascii=False, separators=(",", ":"))
