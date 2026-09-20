from __future__ import annotations

from typing import Any

from a2a.client.client import ClientCallContext
from a2a.types import Role, SendMessageRequest
from common.logging_utils import format_payload_log, format_stage_log, summarize_text
from common.sse_event_consumer import normalize_event

from client_example.scenario_data import build_prompt_input as _build_scenario_prompt_input

# Notification-T extension URI.
_NOTIFICATION_T_EXTENSION_URI = (
    "https://projects.tmforum.org/a2aproject/telecommunication/extensions/Notification-T/v1"
)


def build_prompt_input() -> str:
    """Return the prompt generation input matching the configured language."""
    return _build_scenario_prompt_input()


def _require_prompt_text(prompt_result: Any) -> str:
    if prompt_result.success and isinstance(prompt_result.prompt_text, str):
        return prompt_result.prompt_text
    failure = getattr(prompt_result, "failure", None)
    default_code = "PROMPT_GENERATION_FAILED"
    default_message = "prompt generation did not produce text"
    failure_code = getattr(failure, "code", default_code) if failure else default_code
    failure_message = getattr(failure, "message", default_message) if failure else default_message
    raise ValueError(f"{failure_code}: {failure_message}")


def _build_request_metadata(initial_input: dict[str, object]) -> str:
    return str(initial_input.get("scenario", ""))


async def run_client_flow(
    *,
    prompt_client: object,
    a2a_client: object,
    initial_input: dict[str, object],
    max_artifacts: int | None = None,
    log_sink: object | None = None,
) -> list[dict[str, object]]:
    """Generate a prompt via SDK, send it as a streaming request, and consume/normalize all stream events.

    Message-body convention:
      * text part      -> scenario name (request metadata)
      * metadata[extUri] -> generated prompt text
      * header         -> A2A-Extensions: Notification-T/v1
    """
    prompt_input = build_prompt_input()
    prompt_result = prompt_client.generate_task_prompt(prompt_input)
    prompt_text = _require_prompt_text(prompt_result)

    import uuid

    request = SendMessageRequest()
    request.message.message_id = str(uuid.uuid4())
    request.message.role = Role.ROLE_USER
    request.message.parts.add().text = _build_request_metadata(initial_input)
    request.message.metadata[_NOTIFICATION_T_EXTENSION_URI] = prompt_text

    context = ClientCallContext(
        service_parameters={"A2A-Extensions": _NOTIFICATION_T_EXTENSION_URI},
    )

    if log_sink is not None:
        log_sink(
            format_stage_log(
                role="client",
                stage="request-outbound",
                detail=f"prompt_text={summarize_text(prompt_text)}",
            )
        )

    if log_sink is not None:
        log_sink(
            format_payload_log(role="client", stage="scenario-data", payload=initial_input)
        )

    normalized_events: list[dict[str, object]] = []
    artifact_count = 0
    response_started = False
    async for stream_response in a2a_client.send_message(request, context=context):
        event = normalize_event(stream_response)
        if event["kind"] == "artifact" and max_artifacts is not None and artifact_count >= max_artifacts:
            break
        if not response_started:
            response_started = True
            if log_sink is not None:
                log_sink(
                    format_stage_log(
                        role="client",
                        stage="response-inbound",
                        detail="stream started",
                    )
                )
        normalized_events.append(event)
        if log_sink is not None:
            if event["kind"] == "artifact":
                log_sink(
                    format_stage_log(
                        role="client",
                        stage="artifact-received",
                        detail=f"name={event.get('name', '')} artifact_id={event.get('artifact_id', '')}",
                    )
                )
            elif event["kind"] == "status":
                log_sink(
                    format_stage_log(
                        role="client",
                        stage="status-received",
                        detail=f"state={event.get('state', '')} text={summarize_text(str(event.get('text', '')))}",
                    )
                )
            elif event["kind"] == "message":
                log_sink(
                    format_stage_log(
                        role="client",
                        stage="message-received",
                        detail=f"text={summarize_text(str(event.get('text', '')))}",
                    )
                )

        if event["kind"] == "artifact":
            artifact_count += 1

    if log_sink is not None:
        log_sink(
            format_stage_log(
                role="client",
                stage="stream-completed",
                detail=f"events={len(normalized_events)} artifacts={artifact_count}",
            )
        )
    return normalized_events
