"""Tests of the extension-free pure A2A negotiation handler."""

from __future__ import annotations

from a2a.server.agent_execution.context import RequestContext
from a2a.server.context import ServerCallContext, UnauthenticatedUser
from a2a.types import Message, Role, SendMessageRequest, Task, TaskState
from a2a_t.core.metadata import TASK_T_EXTENSION_URI
from demo.pure_policy import (
    build_demo_pure_policy,
    pure_answer_for,
    pure_base_target,
    pure_corrected_target,
    render_pure_answer,
)
from demo.scenario import (
    KIND_TARGET,
    NATURAL_LANGUAGE_PROMPT_INPUT_EN,
    NATURAL_LANGUAGE_PROMPT_INPUT_MISSING_EN,
    NATURAL_LANGUAGE_PROMPT_INPUT_TARGET_EN,
)
from protocol.pure_a2a import PureTaskHandler
from protocol.server_executor import _report_metadata
from shared.models import NegotiationDecision
from shared.run_constants import LANGUAGE_EN


def _context(
    text: str,
    *,
    task: Task | None = None,
    task_id: str | None = None,
    context_id: str | None = None,
) -> RequestContext:
    """Build one inbound pure A2A request context."""
    message = Message(
        message_id="pure-1",
        role=Role.ROLE_USER,
        context_id=context_id or "ctx-pure",
    )
    if task is not None:
        message.task_id = task.id
    message.parts.add(text=text)
    call_context = ServerCallContext(
        state={},
        user=UnauthenticatedUser(),
        requested_extensions=set(),
    )
    return RequestContext(
        call_context=call_context,
        request=SendMessageRequest(message=message),
        task_id=task_id or (task.id if task is not None else None),
        context_id=context_id or (task.context_id if task is not None else None),
        task=task,
    )


def _handler() -> PureTaskHandler:
    return PureTaskHandler(
        policy=build_demo_pure_policy(LANGUAGE_EN),
        language=LANGUAGE_EN,
        max_rounds=3,
    )


async def test_a_complete_request_is_handed_to_the_executor() -> None:
    handler = _handler()
    context = _context(NATURAL_LANGUAGE_PROMPT_INPUT_EN)

    outcome = await handler.handle(context)

    assert outcome.short_circuit is False
    assert outcome.task_request is context
    assert outcome.input_required_task is None


async def test_a_missing_target_opens_an_input_required_task() -> None:
    handler = _handler()

    outcome = await handler.handle(_context(NATURAL_LANGUAGE_PROMPT_INPUT_MISSING_EN))

    task = outcome.input_required_task
    assert task is not None
    assert task.status.state == TaskState.TASK_STATE_INPUT_REQUIRED
    assert task.status.message.parts[0].text.startswith(
        "The request does not define the time-segmented guaranteed throughput target"
    )
    pure = handler.state_for(task.id)
    assert pure is not None
    assert pure.kind == "information"
    assert pure.round == 1


async def test_an_information_answer_resumes_with_the_negotiated_target() -> None:
    handler = _handler()
    first = await handler.handle(_context(NATURAL_LANGUAGE_PROMPT_INPUT_MISSING_EN))
    task = first.input_required_task
    assert task is not None

    outcome = await handler.handle(
        _context(
            pure_base_target(LANGUAGE_EN),
            task=task,
            task_id=task.id,
            context_id=task.context_id,
        )
    )

    assert outcome.short_circuit is False
    assert outcome.resume_prompt is not None
    assert "Negotiated guaranteed throughput target" in outcome.resume_prompt
    assert pure_base_target(LANGUAGE_EN) in outcome.resume_prompt
    assert handler.state_for(task.id) is None


async def test_an_overlapping_target_opens_a_target_negotiation() -> None:
    handler = _handler()

    outcome = await handler.handle(_context(NATURAL_LANGUAGE_PROMPT_INPUT_TARGET_EN))

    task = outcome.input_required_task
    assert task is not None
    assert task.status.state == TaskState.TASK_STATE_INPUT_REQUIRED
    pure = handler.state_for(task.id)
    assert pure is not None
    assert pure.kind == "target"
    assert pure.proposed_target == pure_corrected_target(LANGUAGE_EN)


async def test_a_counter_proposal_gets_a_second_proposal_then_resolves() -> None:
    handler = _handler()
    first = await handler.handle(_context(NATURAL_LANGUAGE_PROMPT_INPUT_TARGET_EN))
    task = first.input_required_task
    assert task is not None

    counter = await handler.handle(
        _context(
            pure_answer_for(KIND_TARGET, 1, LANGUAGE_EN),
            task=task,
            task_id=task.id,
            context_id=task.context_id,
        )
    )
    assert counter.input_required_task is task
    state = handler.state_for(task.id)
    assert state is not None
    assert state.round == 2

    resolved = await handler.handle(
        _context(
            pure_answer_for(KIND_TARGET, 2, LANGUAGE_EN),
            task=task,
            task_id=task.id,
            context_id=task.context_id,
        )
    )
    assert resolved.resume_prompt is not None
    assert pure_corrected_target(LANGUAGE_EN) in resolved.resume_prompt


async def test_the_round_budget_rejects_a_non_converging_negotiation() -> None:
    handler = _handler()
    first = await handler.handle(_context(NATURAL_LANGUAGE_PROMPT_INPUT_TARGET_EN))
    task = first.input_required_task
    assert task is not None

    outcome = first
    for _ in range(3):
        outcome = await handler.handle(
            _context(
                "{00:00~06:00,10Mbps}, {05:00~12:00,20Mbps}",
                task=task,
                task_id=task.id,
                context_id=task.context_id,
            )
        )

    assert outcome.terminal_task is not None
    assert outcome.terminal_task.status.state == TaskState.TASK_STATE_REJECTED


def test_pure_report_metadata_carries_no_a2a_t_keys() -> None:
    metadata = _report_metadata(
        _context(NATURAL_LANGUAGE_PROMPT_INPUT_EN),
        "summary",
        None,
        pure=True,
    )

    assert TASK_T_EXTENSION_URI not in metadata
    assert "templateUri" not in metadata
    assert "negotiationContext" not in metadata


def test_a_counter_decision_renders_its_counter_proposal() -> None:
    decision = NegotiationDecision(action="counter", rationale="Use a non-overlapping target.")

    assert render_pure_answer(decision) == "Use a non-overlapping target."


def test_an_accept_decision_renders_the_negotiated_target() -> None:
    decision = NegotiationDecision(
        action="accept",
        rationale="Accepted.",
        filled_params={"guaranteedThroughputTarget": "{00:00~06:00,30Mbps}"},
    )

    assert render_pure_answer(decision) == "{00:00~06:00,30Mbps}"


def test_a_reject_decision_renders_its_rationale() -> None:
    decision = NegotiationDecision(action="reject", rationale="The target is not feasible.")

    assert render_pure_answer(decision) == "The target is not feasible."
