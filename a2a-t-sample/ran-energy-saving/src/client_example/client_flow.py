from __future__ import annotations

import json
import uuid
from pathlib import Path

from a2a.client.client import ClientCallContext
from a2a.types import Role, SendMessageRequest
from common.logging_utils import format_payload_log, format_stage_log, summarize_text
from common.sse_event_consumer import normalize_event

from client_example.scenario_data import ENERGY_SAVING_TEMPLATE_URI
from client_example.scenario_data import build_prompt_input as _build_scenario_prompt_input


def build_prompt_input(*, env_path: Path | None = None) -> str:
    """Return the prompt generation input matching the configured language."""
    return _build_scenario_prompt_input(env_path=env_path)


def _require_prompt_text(content: object) -> tuple[str, str]:
    """Return ``(prompt_text, extension_uri)`` from a successful MetadataContent result."""
    prompt_text = getattr(content, "prompt_text", None)
    extension_uri = getattr(content, "extension_uri", None)
    if isinstance(prompt_text, str) and prompt_text.strip() and isinstance(extension_uri, str):
        return prompt_text, extension_uri
    raise ValueError("prompt generation did not produce text")


def _build_request_metadata(initial_input: dict[str, object]) -> str:
    return str(initial_input.get("scenario", ""))


async def run_client_flow(
    *,
    prompt_client: object,
    a2a_client: object,
    initial_input: dict[str, object],
    max_artifacts: int | None = None,
    env_path: Path | None = None,
    log_sink: object | None = None,
) -> list[dict[str, object]]:
    """Generate a Task-T prompt with the A2A-T client SDK, then stream the A2A server's artifacts.

    Pipeline and its logged boundaries:
      1. raw natural-language input                            -> ``[client] input-raw``
      2. ``A2ATClient.generate_task_prompt_from_text``         -> ``[client] sdk-call`` /
                                                                   ``[client] sdk-output-prompt``
      3. A2A SDK ``send_message`` (streaming) to the server    -> ``[client] a2a-send-message``
      4. streamed status/artifact events                       -> ``[client] status-received`` /
                                                                   ``[client] artifact-received``

    Message-body convention:
      * text part                 -> task name (request metadata)
      * metadata[Task-T/v1]       -> generated prompt text
      * header A2A-Extensions     -> Task-T/v1
    """
    if log_sink is not None:
        log_sink(format_payload_log(role="client", stage="scenario-data", payload=initial_input))

    # (1) raw input handed to the A2A-T client SDK
    prompt_input = build_prompt_input(env_path=env_path)
    if log_sink is not None:
        log_sink(format_stage_log(role="client", stage="input-raw", detail=prompt_input))

    # (2) A2A-T client SDK prompt generation (one slot-extraction LLM call)
    if log_sink is not None:
        log_sink(
            format_stage_log(
                role="client",
                stage="sdk-call",
                detail=(
                    "A2ATClient.generate_task_prompt_from_text("
                    f"text={prompt_input}, template_uri={ENERGY_SAVING_TEMPLATE_URI})"
                ),
            )
        )
    content = prompt_client.generate_task_prompt_from_text(prompt_input, ENERGY_SAVING_TEMPLATE_URI)
    prompt_text, extension_uri = _require_prompt_text(content)

    if log_sink is not None:
        log_sink(format_stage_log(role="client", stage="sdk-output-prompt", detail=prompt_text))
        log_sink(
            format_stage_log(
                role="client",
                stage="sdk-output-meta",
                detail=f"template_uri={getattr(content, 'template_uri', None)} extension_uri={extension_uri}",
            )
        )

    # (3) build the A2A task message and send it (streaming) to the A2A server
    request = SendMessageRequest()
    request.message.message_id = str(uuid.uuid4())
    request.message.role = Role.ROLE_USER
    request.message.parts.add().text = _build_request_metadata(initial_input)
    request.message.metadata[extension_uri] = prompt_text
    request.message.metadata["templateUri"] = getattr(content, "template_uri", None) or ENERGY_SAVING_TEMPLATE_URI

    context = ClientCallContext(
        service_parameters={"A2A-Extensions": extension_uri},
    )

    if log_sink is not None:
        log_sink(
            format_stage_log(
                role="client",
                stage="a2a-send-message",
                detail=(
                    f"streaming text_part={_build_request_metadata(initial_input)} "
                    f"metadata_keys=[{extension_uri}, templateUri] "
                    f"header[A2A-Extensions]={extension_uri}"
                ),
            )
        )

    # (4) consume the streamed status/artifact events
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
                log_sink(format_stage_log(role="client", stage="response-inbound", detail="stream started"))
        normalized_events.append(event)
        if log_sink is not None:
            if event["kind"] == "artifact":
                payload_preview = summarize_text(
                    json.dumps(event.get("data", {}), ensure_ascii=False), max_length=160
                )
                log_sink(
                    format_stage_log(
                        role="client",
                        stage="artifact-received",
                        detail=(
                            f"name={event.get('name', '')} artifact_id={event.get('artifact_id', '')} "
                            f"data={payload_preview}"
                        ),
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
