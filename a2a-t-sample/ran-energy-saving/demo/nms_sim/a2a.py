"""A2A and session routes of the NMS simulator.

Both transports share one Task store and one negotiation state machine:

* ``POST /a2a/json/message:stream`` streams the first event and keeps the vendor's never-closing
  SSE stream in strict mode.
* ``POST /a2a/json/message:send`` returns the first event as JSON and POSTs every later
  ``statusUpdate``/``artifactUpdate`` to the registered push-notification URL.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import uuid
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass
from typing import Final

import httpx
from a2a_t.core.metadata import (
    NEGOTIATION_CONTEXT_METADATA_KEY,
    NEGOTIATION_T_EXTENSION_URI,
    TASK_T_EXTENSION_URI,
)
from starlette.requests import Request
from starlette.responses import JSONResponse, Response, StreamingResponse
from starlette.routing import BaseRoute, Route

from demo import ran_data
from demo.nms_sim.config import (
    CARRIER_TASK,
    PURE_FAILURE_COMPLETED,
    PURE_FAILURE_REJECTED,
    PURE_QUESTION_CONFIRM,
    WINDOW_POLICY_ANY,
    SimConfig,
)
from demo.nms_sim.executor import apply_intent, report_artifact, structured_output
from demo.nms_sim.intent import (
    ExtractedContent,
    ParsedIntent,
    SimRequestError,
    accepted_intent,
    declares_negotiation,
    extension_header_value,
    extract_content,
    message_text,
    parse_message_request,
    read_message_request,
    select_issue,
)
from demo.nms_sim.negotiation import (
    PERFORMATIVE_ABORT,
    PERFORMATIVE_ACCEPT,
    PERFORMATIVE_REJECT,
    NegotiationContext,
    NegotiationIssue,
    NegotiationSession,
    NegotiationStore,
    abort_metadata,
    negotiation_message,
    propose_metadata,
)
from demo.nms_sim.push import PushConfig, PushConfigExistsError, PushRegistry, PushSender
from demo.nms_sim.sse import message_stream, status_stream, task_stream
from demo.nms_sim.tasks import (
    TASK_CANCELED,
    TASK_COMPLETED,
    TASK_FAILED,
    TASK_INPUT_REQUIRED,
    TASK_REJECTED,
    TASK_SUBMITTED,
    TASK_WORKING,
    TERMINAL_STATES,
    SimTask,
    TaskStore,
)
from demo.nms_sim.token import TokenStore

_LOGGER = logging.getLogger("ran_energy_saving.nms_sim")

#: SSE media type.
EVENT_STREAM: str = "text/event-stream"

#: The fixed plain-A2A question the real vendor asks on every negotiation round. Its A2A front
#: end does not render the business issue: the operator answers in free text and the vendor merges
#: the reply into its intent before validating again.
PURE_CONFIRM_TEXT: str = "Not feasible, please confirm"

#: Explicit refusal in a plain-A2A answer. The vendor skill terminates only on a clear refusal;
#: the simulator must do the same, otherwise an automatic client's "Reject: ..." would be read as
#: another answer and the Task would keep asking until the round budget ran out.
PURE_REJECTION: Final[re.Pattern[str]] = re.compile(
    r"^\s*(?:reject|rejected|refuse|decline)\b|\bnot\s+acceptable\b|拒绝|不接受",
    re.IGNORECASE,
)


@dataclass
class SimRuntime:
    """Stores and background workers shared by both transports.

    Attributes:
        store: Tasks served by both endpoints.
        tokens: Session tokens.
        negotiations: Negotiation state machine keyed by negotiation id.
        pushes: Background webhook sender.
        registry: Per-Task push configs.
    """

    store: TaskStore
    tokens: TokenStore
    negotiations: NegotiationStore
    pushes: PushSender
    registry: PushRegistry


def build_runtime(
    config: SimConfig,
    *,
    push_transport: httpx.AsyncBaseTransport | None = None,
) -> SimRuntime:
    """Build the simulator runtime for one app instance."""
    return SimRuntime(
        store=TaskStore(limit=config.task_limit, shape=config.task_shape),
        tokens=TokenStore(config),
        negotiations=NegotiationStore(),
        pushes=PushSender(transport=push_transport),
        registry=PushRegistry(),
    )


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
    runtime: SimRuntime | None = None,
) -> tuple[list[BaseRoute], SimRuntime]:
    """Build the simulator routes and their runtime."""
    resolved = runtime or build_runtime(config)
    resolved_store = resolved.store
    resolved_tokens = resolved.tokens
    negotiations = resolved.negotiations
    pushes = resolved.pushes
    registry = resolved.registry

    def unauthorized() -> JSONResponse:
        return JSONResponse(
            {"error": {"code": 401, "message": "A valid session token is required"}},
            status_code=401,
            headers={"WWW-Authenticate": "Bearer"},
        )

    def authorized(request: Request) -> bool:
        token = _bearer_token(request)
        return bool(token) and resolved_tokens.valid(token)

    def negotiation_error(message: str) -> JSONResponse:
        return JSONResponse(
            {"error": {"code": 422, "status": "INVALID_ARGUMENT", "message": message}},
            status_code=422,
        )

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

    # ---------------------------------------------------------------- execution

    def _prepare_report(
        task: SimTask,
        intent: ParsedIntent,
    ) -> tuple[dict[str, object], dict[str, object]] | None:
        """Render the report of one accepted intent and stash its structured Task message."""
        output = structured_output(intent)
        resolved_store.update(
            task.task_id,
            state=TASK_SUBMITTED,
            message=f"Task creation succeeded{_json_compact(output)}",
        )
        try:
            report = apply_intent(
                intent, allow_any_window=config.window_policy == WINDOW_POLICY_ANY
            )
        except ran_data.RanDataError as exc:
            _LOGGER.warning("simulator execution failed: %s", exc)
            resolved_store.update(task.task_id, state=TASK_FAILED, message=str(exc))
            return None
        return report, report_artifact(task.task_id, report)

    def _stream_completion(
        task: SimTask,
        intent: ParsedIntent,
        *,
        hold: bool = False,
    ) -> Response:
        """Stream one accepted request from execution to its completed report."""
        prepared = _prepare_report(task, intent)
        if prepared is None:
            return JSONResponse(
                {"error": {"code": 500, "message": task.message}},
                status_code=500,
            )
        report, artifact = prepared
        return StreamingResponse(
            task_stream(task, report=report, artifact=artifact, config=config, hold=hold),
            media_type=EVENT_STREAM,
            headers={"Cache-Control": "no-cache"},
        )

    async def _execute_task(
        task: SimTask,
        artifact: dict[str, object],
        *,
        hold: bool,
        push: PushConfig | None,
    ) -> None:
        """Advance one Task and POST each transition to its webhook."""
        try:
            await asyncio.sleep(config.working_delay)
            if task.state == TASK_CANCELED:
                await pushes.send(push, task.status_frame())
                return
            resolved_store.update(
                task.task_id,
                state=TASK_WORKING,
                message="Task is executing the energy-saving plan.",
            )
            await pushes.send(push, task.status_frame())
            await asyncio.sleep(config.completion_delay)
            if task.state == TASK_CANCELED:
                await pushes.send(push, task.status_frame())
                return
            resolved_store.add_artifact(task.task_id, artifact)
            await pushes.send(push, task.artifact_frame())
            if hold:
                resolved_store.update(
                    task.task_id,
                    state=TASK_WORKING,
                    message="Task is running; ready to be modified.",
                )
                await pushes.send(push, task.status_frame())
                return
            resolved_store.update(task.task_id, state=TASK_COMPLETED, message="Task completed.")
            await pushes.send(push, task.status_frame())
        except Exception:  # noqa: BLE001 - a background push must never crash the server
            _LOGGER.warning("simulated task execution failed for %s", task.task_id, exc_info=True)

    def _json_completion(
        task: SimTask,
        intent: ParsedIntent,
        *,
        hold: bool,
        push: PushConfig | None,
    ) -> Response:
        """Return the first event as JSON and push the rest in the background."""
        prepared = _prepare_report(task, intent)
        if prepared is None:
            return JSONResponse(
                {"error": {"code": 500, "message": task.message}},
                status_code=500,
            )
        _report, artifact = prepared
        pushes.run(_execute_task(task, artifact, hold=hold, push=push))
        return JSONResponse({"task": task.to_dict(embedded=True)}, status_code=200)

    # ---------------------------------------------------------------- push config

    def _push_config_for(
        task: SimTask,
        request_config: PushConfig | None,
        *,
        explicit_task: bool,
        context_id: str = "",
    ) -> PushConfig | None:
        """Attach one request's config to a Task, or return the existing/pending one.

        A config registered by the message that started the negotiation is held by context id; a
        Modify continues a Task whose own context predates this run, so both ids are tried.
        """
        if request_config is None:
            return (
                registry.for_task(task.task_id)
                or registry.take_pending(task.context_id)
                or registry.take_pending(context_id)
            )
        registry.register(task.task_id, request_config, explicit=explicit_task)
        return request_config

    def _register_config_error(exc: PushConfigExistsError) -> JSONResponse:
        """Return the vendor's 500 answer for a second registration."""
        return JSONResponse(
            {"error": {"code": 500, "status": "INTERNAL", "message": str(exc)}},
            status_code=500,
        )

    # ---------------------------------------------------------------- tasks

    def _requested_task_id(body: Mapping[str, object]) -> str | None:
        """Return the Task id named by a message, when it carries one."""
        message = body.get("message")
        if not isinstance(message, Mapping):
            return None
        for key in ("taskId", "task_id"):
            value = message.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return None

    def _resolve_task(
        body: Mapping[str, object],
        *,
        context_id: str,
        fallback_task_id: str | None = None,
    ) -> tuple[SimTask, bool]:
        """Return the Task a request continues, or create a new one.

        Returns:
            The Task and whether it was created by this request.
        """
        requested = _requested_task_id(body) or fallback_task_id
        task = resolved_store.get(requested) if requested else None
        if task is None:
            return resolved_store.create(context_id=context_id), True
        resolved_store.update(task.task_id, state=TASK_SUBMITTED, message="Task creation succeeded")
        return task, False

    # ---------------------------------------------------------------- negotiation

    def _new_negotiation(
        content: ExtractedContent,
        issue: NegotiationIssue,
        *,
        context_id: str,
        task_id: str | None,
        structured: bool,
    ) -> NegotiationSession:
        """Start one negotiation at round 1."""
        return negotiations.create(
            kind=issue.kind,
            max_rounds=config.max_rounds,
            context_id=context_id,
            task_id=task_id if structured and config.negotiation_carrier == CARRIER_TASK else None,
            baseline=content,
        )

    def _propose(
        session: NegotiationSession,
        issue: NegotiationIssue,
        *,
        structured: bool,
        task: SimTask | None,
        stream: bool,
        context_id: str,
        request_config: PushConfig | None = None,
        explicit_task: bool = False,
    ) -> Response:
        """Answer one request with a Negotiation-T proposal."""
        context = negotiations.context(session, performative="PROPOSE")
        if not structured:
            # Root-cause path: the vendor answers an undeclared negotiation as a bare message.
            if stream:
                return StreamingResponse(
                    message_stream(issue.body(), context_id=context_id, config=config, structured=False),
                    media_type=EVENT_STREAM,
                    headers={"Cache-Control": "no-cache"},
                )
            return JSONResponse(
                {"message": negotiation_message(context_id=context_id, body=issue.body(), metadata={})},
                status_code=200,
            )
        metadata = propose_metadata(issue, context)
        if config.negotiation_carrier != CARRIER_TASK:
            if stream:
                return StreamingResponse(
                    message_stream(
                        issue.body(),
                        context_id=context_id,
                        config=config,
                        structured=True,
                        metadata=metadata,
                    ),
                    media_type=EVENT_STREAM,
                    headers={"Cache-Control": "no-cache"},
                )
            return JSONResponse(
                {"message": negotiation_message(context_id=context_id, body=issue.body(), metadata=metadata)},
                status_code=200,
            )
        if task is None:
            task = resolved_store.create(context_id=context_id)
            session.task_id = task.task_id
        else:
            session.task_id = task.task_id
        try:
            _push_config_for(
                task,
                request_config,
                explicit_task=explicit_task,
                context_id=context_id,
            )
        except PushConfigExistsError as exc:
            return _register_config_error(exc)
        resolved_store.update(
            task.task_id,
            state=TASK_INPUT_REQUIRED,
            message=issue.body(),
            metadata=metadata,
        )
        if stream:
            # Close the stream: the client answers from the status message, not from a heartbeat.
            return StreamingResponse(
                status_stream(task, config=config, keep_open=False),
                media_type=EVENT_STREAM,
                headers={"Cache-Control": "no-cache"},
            )
        return JSONResponse({"task": task.to_dict(embedded=True)}, status_code=200)

    def _finish_accept(
        session: NegotiationSession,
        content: ExtractedContent,
        body: Mapping[str, object],
        request_config: PushConfig | None,
        *,
        stream: bool,
    ) -> Response:
        """Complete an accepted negotiation, continuing the carried Task when there is one."""
        intent = accepted_intent(content, session.baseline, config)
        context_id = _context_id(body) or session.context_id
        task, is_new = _resolve_task(body, context_id=context_id, fallback_task_id=session.task_id)
        negotiations.close(session)
        try:
            push = _push_config_for(
                task,
                request_config,
                explicit_task=bool(_requested_task_id(body)),
                context_id=context_id,
            )
        except PushConfigExistsError as exc:
            return _register_config_error(exc)
        if stream:
            return _stream_completion(task, intent, hold=config.hold_working and is_new)
        return _json_completion(task, intent, hold=config.hold_working and is_new, push=push)

    def _finish_reject(
        session: NegotiationSession,
        *,
        stream: bool,
        context_id: str,
    ) -> Response:
        """End a negotiation with ``TASK_STATE_REJECTED`` (no Task is created)."""
        task = resolved_store.get(session.task_id) if session.task_id else None
        negotiations.close(session)
        if task is not None:
            resolved_store.update(task.task_id, state=TASK_REJECTED, message="The negotiation was rejected.")
            if stream:
                return StreamingResponse(
                    status_stream(task, config=config, keep_open=False),
                    media_type=EVENT_STREAM,
                    headers={"Cache-Control": "no-cache"},
                )
            return JSONResponse({"task": task.to_dict(embedded=True)}, status_code=200)
        message = negotiation_message(
            context_id=context_id,
            body="The negotiation was rejected.",
            metadata={},
        )
        if stream:
            return StreamingResponse(
                _single_message_stream(message),
                media_type=EVENT_STREAM,
                headers={"Cache-Control": "no-cache"},
            )
        return JSONResponse({"message": message}, status_code=200)

    def _finish_abort(
        session: NegotiationSession,
        *,
        stream: bool,
        context_id: str,
    ) -> Response:
        """End a negotiation that exhausted its round budget."""
        task = resolved_store.get(session.task_id) if session.task_id else None
        context = negotiations.context(session)
        metadata = abort_metadata(context)
        negotiations.close(session)
        if task is not None:
            resolved_store.update(
                task.task_id,
                state=TASK_REJECTED,
                message="The negotiation exceeded its round budget.",
            )
        message = negotiation_message(context_id=context_id, body="", metadata=metadata)
        if stream:
            return StreamingResponse(
                _single_message_stream(message),
                media_type=EVENT_STREAM,
                headers={"Cache-Control": "no-cache"},
            )
        return JSONResponse({"message": message}, status_code=200)

    def _negotiated_request(
        body: Mapping[str, object],
        request: Request,
        parsed_context: NegotiationContext,
        *,
        stream: bool,
    ) -> Response:
        """Handle a message that answers an open negotiation."""
        session = negotiations.get(parsed_context.id)
        if session is None:
            # The real NMS rejects a negotiation id it does not own; using the A2A context id as
            # the negotiation id is the concrete mistake this reproduces.
            return negotiation_error(
                f"negotiationContext.id '{parsed_context.id}' does not match an active negotiation; "
                "use the negotiation id returned by the server, not the A2A context id"
            )
        try:
            content = read_message_request(body, request.headers)
        except SimRequestError as exc:
            return JSONResponse(exc.payload, status_code=exc.status_code)
        context_id = _context_id(body) or session.context_id
        if parsed_context.performative == PERFORMATIVE_REJECT:
            return _finish_reject(session, stream=stream, context_id=context_id)
        if parsed_context.performative == PERFORMATIVE_ABORT:
            return _finish_abort(session, stream=stream, context_id=context_id)
        request_config = PushConfig.from_request(body)
        if parsed_context.performative == PERFORMATIVE_ACCEPT:
            return _finish_accept(session, content, body, request_config, stream=stream)
        effective = content.merged_with(session.baseline)
        issue = select_issue(effective, config)
        if issue is None:
            return _finish_accept(session, content, body, request_config, stream=stream)
        if session.round >= session.max_rounds:
            return _finish_abort(session, stream=stream, context_id=context_id)
        negotiations.advance(session, kind=issue.kind)
        task = resolved_store.get(session.task_id) if session.task_id else None
        return _propose(
            session,
            issue,
            structured=declares_negotiation(request.headers),
            task=task,
            stream=stream,
            context_id=context_id,
            request_config=request_config,
            explicit_task=bool(_requested_task_id(body)),
        )

    # ---------------------------------------------------------------- A2A-T

    def _a2a_t_request(body: Mapping[str, object], request: Request, *, stream: bool) -> Response:
        """Handle one Task-T request (initial or negotiation reply)."""
        # A Task already owns its callback: a second registration is rejected before the request
        # content is even judged, matching the vendor's "config already exists" answer.
        early_task_id = _requested_task_id(body)
        early_config = PushConfig.from_request(body)
        if early_config is not None and early_task_id and registry.for_task(early_task_id) is not None:
            return _register_config_error(PushConfigExistsError(early_task_id))
        metadata = _message_metadata(body)
        raw_context = metadata.get(NEGOTIATION_CONTEXT_METADATA_KEY)
        if raw_context is not None:
            parsed_context = NegotiationContext.from_metadata(metadata)
            if parsed_context is None:
                return negotiation_error(
                    "negotiationContext must carry an id, a round, maxRounds and a performative"
                )
            return _negotiated_request(body, request, parsed_context, stream=stream)
        try:
            content = read_message_request(body, request.headers)
        except SimRequestError as exc:
            return JSONResponse(exc.payload, status_code=exc.status_code)
        issue = select_issue(content, config)
        context_id = _context_id(body) or str(uuid.uuid4())
        requested_task_id = _requested_task_id(body)
        request_config = PushConfig.from_request(body)
        if issue is None:
            task, is_new = _resolve_task(body, context_id=context_id)
            try:
                push = _push_config_for(
                    task,
                    request_config,
                    explicit_task=bool(requested_task_id),
                    context_id=context_id,
                )
            except PushConfigExistsError as exc:
                return _register_config_error(exc)
            intent = parse_message_request(body, request.headers, config)
            if stream:
                return _stream_completion(task, intent, hold=config.hold_working and is_new)
            return _json_completion(task, intent, hold=config.hold_working and is_new, push=push)
        task = resolved_store.get(requested_task_id) if requested_task_id else None
        if task is None and request_config is not None and config.negotiation_carrier != CARRIER_TASK:
            registry.remember_pending(context_id, request_config)
        session = _new_negotiation(
            content,
            issue,
            context_id=context_id,
            task_id=task.task_id if task is not None else None,
            structured=declares_negotiation(request.headers),
        )
        if task is not None and declares_negotiation(request.headers) and config.negotiation_carrier == CARRIER_TASK:
            session.task_id = task.task_id
        return _propose(
            session,
            issue,
            structured=declares_negotiation(request.headers),
            task=task,
            stream=stream,
            context_id=context_id,
            request_config=request_config,
            explicit_task=bool(requested_task_id),
        )

    # ---------------------------------------------------------------- plain A2A

    def _pure_task_response(task: SimTask, *, stream: bool) -> Response:
        """Return one terminal plain-A2A Task; a stream always closes at a terminal state."""
        if stream:
            return StreamingResponse(
                status_stream(task, config=config, keep_open=False),
                media_type=EVENT_STREAM,
                headers={"Cache-Control": "no-cache"},
            )
        return JSONResponse({"task": task.to_dict(embedded=True)}, status_code=200)

    def _pure_request(
        body: Mapping[str, object],
        request: Request,
        *,
        stream: bool,
    ) -> Response:
        """Handle one plain A2A request: ask for missing values and resume on the follow-up."""
        message = body.get("message")
        message_map = message if isinstance(message, Mapping) else {}
        text = message_text(message_map)
        requested_task_id = _requested_task_id(body)
        context_id = _context_id(body) or str(uuid.uuid4())
        request_config = PushConfig.from_request(body)
        # A plain question is a standalone Message, so the follow-up carries only the context id.
        task = (
            resolved_store.get(requested_task_id)
            if requested_task_id
            else resolved_store.find_by_context(context_id)
        )
        # A Task in INPUT_REQUIRED is waiting for the answer to the question it just asked. Any
        # other state (including a held WORKING Task) means this message starts a new exchange,
        # e.g. a standard-A2A Modify. That distinction is what keeps the Modify's own outage
        # window from being merged into the Create the baseline was built from.
        is_answer = task is not None and task.state == TASK_INPUT_REQUIRED
        if task is None:
            task = resolved_store.create(context_id=context_id)
            task.pure_text = text
            task.pure_baseline_text = text
            task.pure_hold = config.hold_working
        elif is_answer:
            task.pure_text = f"{task.pure_text}\n{text}".strip()
            task.pure_round += 1
            resolved_store.update(task.task_id, state=TASK_SUBMITTED, message="Task creation succeeded")
        else:
            task.pure_text = text
            task.pure_baseline_text = text
            task.pure_round = 0
            task.pure_hold = False
            resolved_store.update(task.task_id, state=TASK_SUBMITTED, message="Task creation succeeded")
        task.pure_max_rounds = config.max_rounds
        try:
            push = _push_config_for(
                task,
                request_config,
                explicit_task=bool(requested_task_id),
                context_id=context_id,
            )
        except PushConfigExistsError as exc:
            return _register_config_error(exc)
        # Only the latest reply is judged; the request of this exchange supplies the fields it did
        # not repeat. Concatenating and parsing the whole conversation would let the first mention
        # of a field win, so a corrected region/rate in the answer would be ignored.
        baseline = extract_content(task.pure_baseline_text)
        content = extract_content(text).merged_with(baseline) if is_answer else baseline
        if is_answer and PURE_REJECTION.search(text):
            # The vendor skill terminates on a clear refusal. Without this the simulator would
            # treat the client's "Reject: ..." as another answer and keep asking until the round
            # budget ran out, leaving the flow without a terminal state.
            resolved_store.update(task.task_id, state=TASK_REJECTED, message=text)
            return _pure_task_response(task, stream=stream)
        if config.pure_question == PURE_QUESTION_CONFIRM and not is_answer:
            # The vendor's plain-A2A front end asks the same terse sentence whatever the business
            # issue is; the caller's free-text answer is merged over this request and re-validated.
            return _pure_question_response(task, PURE_CONFIRM_TEXT, stream=stream)
        issue = select_issue(content, config, effective_rate_only=is_answer)
        if issue is not None:
            return _pure_issue(task, issue, content, stream=stream, is_answer=is_answer)
        intent = accepted_intent(content, None, config)
        if stream:
            # The pure stream owns the same rehearsal switch as the webhook path: a held Task must
            # publish its report and stay WORKING so the Modify picker can select it.
            return _stream_completion(task, intent, hold=task.pure_hold)
        return _json_completion(task, intent, hold=task.pure_hold, push=push)

    def _pure_issue(
        task: SimTask,
        issue: NegotiationIssue,
        content: ExtractedContent,
        *,
        stream: bool,
        is_answer: bool,
    ) -> Response:
        """Answer a plain-A2A request that cannot be executed yet.

        The initial request gets a vendor text question. A follow-up that still cannot be served
        follows the configured ``pure_failure`` policy: the vendor answers ``TASK_STATE_COMPLETED``
        with an error sentence and no artifact -- a refusal that must not be reported as success;
        ``retry`` asks again until the round budget; ``rejected`` ends the Task.
        """
        if is_answer and config.pure_failure == PURE_FAILURE_COMPLETED:
            resolved_store.update(
                task.task_id,
                state=TASK_COMPLETED,
                message=_pure_failure_text(issue, content),
            )
            return _pure_task_response(task, stream=stream)
        if is_answer and config.pure_failure == PURE_FAILURE_REJECTED:
            resolved_store.update(task.task_id, state=TASK_REJECTED, message=issue.body())
            return _pure_task_response(task, stream=stream)
        if task.pure_round >= task.pure_max_rounds:
            resolved_store.update(
                task.task_id,
                state=TASK_REJECTED,
                message=f"Negotiation did not converge: {issue.body()}",
            )
            return _pure_task_response(task, stream=stream)
        if config.pure_question == PURE_QUESTION_CONFIRM:
            # The vendor's front end never renders the business issue: every round is the same
            # terse sentence, and the answer is merged over the request before re-validating.
            return _pure_question_response(task, PURE_CONFIRM_TEXT, stream=stream)
        return _pure_question_response(task, issue.body(), stream=stream)

    def _pure_question_response(task: SimTask, text: str, *, stream: bool) -> Response:
        """Ask one plain-A2A question as a standalone agent Message.

        The real NMS asks a plain-A2A question as a standalone message with no negotiation
        metadata, even when it also marks its internal Task ``INPUT_REQUIRED``. Returning a Task
        status here would make the OSS answer it itself instead of relaying the question.
        """
        resolved_store.update(task.task_id, state=TASK_INPUT_REQUIRED, message=text)
        if stream:
            return StreamingResponse(
                message_stream(
                    text,
                    context_id=task.context_id,
                    config=config,
                    structured=False,
                ),
                media_type=EVENT_STREAM,
                headers={"Cache-Control": "no-cache"},
            )
        return JSONResponse(
            {
                "message": negotiation_message(
                    context_id=task.context_id,
                    body=text,
                    metadata={},
                )
            },
            status_code=200,
        )

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

    # ---------------------------------------------------------------- routes

    async def _handle(request: Request, *, stream: bool) -> Response:
        """Authorize, parse and dispatch one request on either transport."""
        if not authorized(request):
            return unauthorized()
        body = await _json_body(request)
        if body is None:
            return JSONResponse({"error": "invalid JSON body"}, status_code=400)
        if _is_pure_request(body, request):
            return _pure_request(body, request, stream=stream)
        return _a2a_t_request(body, request, stream=stream)

    async def send_message(request: Request) -> Response:
        """Accept one A2A message and stream the Task or negotiation message."""
        return await _handle(request, stream=True)

    async def send_message_json(request: Request) -> Response:
        """Accept one A2A message, answer its first event and push the rest."""
        return await _handle(request, stream=False)

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
        Route("/a2a/json/message:send", send_message_json, methods=["POST"]),
        Route("/a2a/json/tasks", list_tasks, methods=["GET"]),
        Route("/a2a/json/tasks/{task_id}", get_task, methods=["GET"]),
        Route("/a2a/json/tasks/{task_id}:cancel", cancel_task, methods=["POST"]),
    ]
    return routes, resolved


async def _single_message_stream(message: Mapping[str, object]) -> AsyncIterator[str]:
    """Yield one ``message`` SSE frame and close."""
    yield f"data: {json.dumps({'message': dict(message)}, ensure_ascii=False)}\n\n"


def _pure_failure_text(issue: NegotiationIssue, content: ExtractedContent) -> str:
    """Render the vendor-style failure sentence of one unservable plain-A2A request."""
    if "Energy saving region" in issue.items:
        if content.region:
            return (
                f'The region name "{content.region}" is not recognized. '
                "Please provide the correct region name."
            )
        return (
            "The energy saving region is not recognized. "
            "Please provide the correct region name."
        )
    return issue.detail or issue.body()


def _context_id(body: Mapping[str, object]) -> str | None:
    """Return the message context id, when present."""
    message = body.get("message")
    if not isinstance(message, Mapping):
        return None
    for key in ("contextId", "context_id"):
        value = message.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _message_metadata(body: Mapping[str, object]) -> Mapping[str, object]:
    """Return the metadata map of one request's message."""
    message = body.get("message")
    if not isinstance(message, Mapping):
        return {}
    metadata = message.get("metadata")
    return metadata if isinstance(metadata, Mapping) else {}


def _json_compact(payload: Mapping[str, object]) -> str:
    """Render the vendor's compact structured suffix."""
    return json.dumps(dict(payload), ensure_ascii=False, separators=(",", ":"))


__all__ = ["SimRuntime", "build_routes", "build_runtime"]
