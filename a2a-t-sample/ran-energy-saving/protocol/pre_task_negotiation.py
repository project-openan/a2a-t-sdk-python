"""Server-side pre-task A2A-T negotiation handler of the RAN energy-saving sample.

The handler is the single protocol entry point of the server executor. It handles every request
that arrives **before a Task exists** (``context.current_task is None``; ``context.task_id`` is not
a reliable signal because ``RequestContext`` generates one in its constructor):

* initial Task-T validation, mapped to an information/target/feasibility negotiation on failure;
* client ``Accept``: validate the negotiation answer and the agreed Task-T prompt, then hand the
  request to the deterministic executor so a Task is created and executed;
* client ``Reject``/``Abort``: terminal; answer with a plain ``Message("协商已结束")`` and create no
  Task;
* client counter-``Propose``: validate it, let the deterministic policy decide the next proposal or
  the abort, then emit the standalone Negotiation-T message and create no Task.

The handler owns protocol flow only. Two deployment hooks keep business rules out of it:
:class:`TaskTPrecheck` decides whether a validated Task-T still needs a negotiation, and the
injected policy decides the next negotiation step. Every SDK call is synchronous (it may call the
LLM), so the handler runs it in a worker thread with ``asyncio.to_thread`` to keep the server event
loop responsive.
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Final, Protocol, TypeVar

from a2a.server.agent_execution.context import RequestContext
from a2a.types import Message, Task, TaskState, TaskStatus
from a2a_t.core.errors.exceptions import A2ATError, ContentValidationError, SlotValidationError
from a2a_t.core.metadata import (
    TASK_T_EXTENSION_URI,
    TEMPLATE_URI_METADATA_KEY,
    MetadataContent,
    NegotiationContext,
    NegotiationPerformative,
)
from a2a_t.core.validation_pipeline import FilledParamData
from shared.logging_setup import log_context
from shared.run_constants import MAX_ROUNDS
from shared.task_schema import TASK_PARAM_SCHEMA
from shared.trace import trace_stage

from protocol.messages import (
    ABORT_MESSAGE_TEXT,
    PROPOSE_MESSAGE_TEXT,
    SAMPLE_NEGOTIATION_OPTIONS_METADATA_KEY,
    SAMPLE_PHASE_MODIFY,
    SAMPLE_PROPOSAL_METADATA_KEY,
    STRUCTURED_ERRORS_METADATA_KEY,
    TASK_TEMPLATE_URI,
    build_agent_message,
    build_end_message,
    build_error_message,
    build_negotiation_message,
    is_negotiation_message,
    metadata_to_dict,
    missing_extensions,
    negotiation_prompt_from_metadata,
    parse_negotiation_context,
    task_prompt_from_metadata,
)
from protocol.negotiation_policy import (
    ABORT_TEMPLATE_URI,
    AbortContent,
    InboundDecision,
    NegotiationKind,
    NegotiationState,
    PolicyDecision,
    ProposalContent,
    SampleProposal,
    decide,
    negotiation_kind_for_errors,
    negotiation_kind_for_template_uri,
)

#: Caller parameter schema of the Negotiation-T validation steps.
#:
#: The SDK merges this schema into its semantic-validation output contract; the sample validates the
#: message and does not need extra extracted parameters, so the schema stays empty.
NEGOTIATION_PARAM_SCHEMA: Final[dict[str, object]] = {"type": "object", "properties": {}}

_LOGGER = logging.getLogger("ran_energy_saving.pre_task_negotiation")

_ResultT = TypeVar("_ResultT")


class NegotiationContentServer(Protocol):
    """The subset of ``A2ATServer`` the handler depends on (also implemented by test fakes)."""

    def validate_task_prompt_and_data_filling(
        self, prompt: str, schema: Mapping[str, object], template_uri: str
    ) -> FilledParamData: ...

    def validate_propose_prompt_and_data_filling(
        self,
        prompt: str | None,
        context: NegotiationContext | None,
        schema: Mapping[str, object],
        template_uri: str,
    ) -> FilledParamData: ...

    def validate_accept_prompt_and_data_filling(
        self,
        prompt: str | None,
        context: NegotiationContext | None,
        schema: Mapping[str, object],
        template_uri: str,
    ) -> FilledParamData: ...

    def validate_reject_prompt_and_data_filling(
        self,
        prompt: str | None,
        context: NegotiationContext | None,
        schema: Mapping[str, object],
        template_uri: str,
    ) -> FilledParamData: ...

    def validate_abort_prompt_and_data_filling(
        self,
        prompt: str | None,
        context: NegotiationContext | None,
        schema: Mapping[str, object],
        template_uri: str,
    ) -> FilledParamData: ...

    def generate_negotiation_propose_prompt_from_text(
        self, text: str | None, context: NegotiationContext, template_uri: str
    ) -> MetadataContent: ...

    def generate_negotiation_abort_prompt_from_text(
        self, text: str | None, context: NegotiationContext, template_uri: str
    ) -> MetadataContent: ...


@dataclass(frozen=True)
class NegotiationOutcome:
    """Result of one pre-task negotiation invocation.

    Attributes:
        short_circuit: ``True`` when the handler produced a standalone response that must be enqueued
            without running the agent.
        message: short-circuit message (plain end/error or negotiation message); ``None`` when
            execution continues or a terminal Task is returned.
        task_request: request context to hand to the deterministic executor; ``None`` on short circuit.
        terminal_task: terminal ``REJECTED`` Task for an unmapped Task-T validation failure; it is
            enqueued as-is, so no agent runs and no Task lifecycle beyond REJECTED is produced.
        input_required_task: non-terminal ``INPUT_REQUIRED`` Task of the pure A2A mode. The outer
            executor enqueues the Task and then a status update carrying its message, so a standard
            A2A client can read the question and continue the Task.
        resume_prompt: prompt the pure A2A mode negotiated; the outer executor resumes the existing
            ``INPUT_REQUIRED`` Task with this prompt instead of creating a new Task.
        modify_prompt: validated Task-T prompt of a Modify follow-up; the outer executor updates the
            existing Task in place instead of creating a new one.
        modify_params: parameters extracted from the Modify Task-T.
    """

    short_circuit: bool
    message: Message | None = None
    task_request: RequestContext | None = None
    terminal_task: Task | None = None
    input_required_task: Task | None = None
    resume_prompt: str | None = None
    modify_prompt: str | None = None
    modify_params: Mapping[str, object] | None = None

    @classmethod
    def execution(cls, context: RequestContext) -> NegotiationOutcome:
        """Return an outcome that lets the deterministic executor create and run the Task."""
        return cls(short_circuit=False, task_request=context)

    @classmethod
    def short(cls, message: Message) -> NegotiationOutcome:
        """Return an outcome that only enqueues the given message."""
        return cls(short_circuit=True, message=message)

    @classmethod
    def rejected(cls, task: Task) -> NegotiationOutcome:
        """Return an outcome that enqueues a terminal ``REJECTED`` Task without running the agent."""
        return cls(short_circuit=True, terminal_task=task)

    @classmethod
    def input_required(cls, task: Task) -> NegotiationOutcome:
        """Return an outcome that enqueues a non-terminal ``INPUT_REQUIRED`` Task."""
        return cls(short_circuit=True, input_required_task=task)

    @classmethod
    def resume(cls, context: RequestContext, prompt: str) -> NegotiationOutcome:
        """Return an outcome that resumes the existing Task with a negotiated prompt."""
        return cls(short_circuit=False, task_request=context, resume_prompt=prompt)

    @classmethod
    def modify(
        cls,
        context: RequestContext,
        prompt: str,
        params: Mapping[str, object],
    ) -> NegotiationOutcome:
        """Return an outcome that updates the existing Task with a validated Modify Task-T."""
        return cls(
            short_circuit=False,
            task_request=context,
            modify_prompt=prompt,
            modify_params=dict(params),
        )


@dataclass(frozen=True)
class PrecheckFinding:
    """Initial Task-T signal that a negotiation must open before the task is executed.

    Attributes:
        kind: negotiation kind the finding maps to.
        missing_items: caller-schema fields the Task-T left empty, when the finding is an
            information negotiation.
        detail: short human-readable reason used in the log record.
        proposal_text: optional business text for the server's first proposal; when empty the
            policy uses its generic information message.
        options: sample-only choices the client may select (for example served areas).
    """

    kind: NegotiationKind
    missing_items: tuple[str, ...] = ()
    detail: str = ""
    proposal_text: str = ""
    options: Mapping[str, object] | None = None


class TaskTPrecheck(Protocol):
    """Deployment hook deciding whether a validated Task-T still needs a negotiation.

    A model-driven semantic validator may accept a Task-T that the deployment considers
    incomplete or infeasible. The composition root injects a checker for those signals; without
    one the handler trusts the SDK verdict.
    """

    def __call__(
        self,
        filled: FilledParamData,
        schema: Mapping[str, object],
        *,
        prompt: str | None = None,
    ) -> PrecheckFinding | None:
        """Return a finding when the Task-T must open a negotiation, else ``None``.

        Args:
            filled: parameters of the validated Task-T (partial when validation failed).
            schema: caller parameter schema of the Task-T validation.
            prompt: rendered Task-T prompt, when the caller has it; a failed validation nulls the
                rejected slot in ``filled``, so the prompt is the only place that value survives.
        """
        ...


class PreTaskNegotiationHandler:
    """Deterministic pre-task negotiation handler over the A2A-T SDK server facade."""

    def __init__(
        self,
        prompt_server: NegotiationContentServer,
        *,
        max_rounds: int = MAX_ROUNDS,
        task_template_uri: str = TASK_TEMPLATE_URI,
        task_schema: Mapping[str, object] | None = None,
        logger: logging.Logger | None = None,
        policy: Callable[[NegotiationState], PolicyDecision] | None = None,
        precheck: TaskTPrecheck | None = None,
    ) -> None:
        """Create one handler.

        Args:
            prompt_server: A2A-T SDK server facade (or a test double).
            max_rounds: round budget of every negotiation.
            task_template_uri: Task-T template the accepted prompt must address.
            task_schema: caller parameter schema of the Task-T validation.
            logger: optional logger; defaults to the module logger.
            policy: decision function of the negotiation state machine; a deployment injects one
                bound to its plan evaluator.
            precheck: optional deployment check of a semantically valid Task-T; without one the
                handler trusts the SDK verdict.
        """
        self._prompt_server = prompt_server
        self._max_rounds = max_rounds
        self._task_template_uri = task_template_uri
        self._task_schema: Mapping[str, object] = task_schema if task_schema is not None else TASK_PARAM_SCHEMA
        self._logger = logger or _LOGGER
        self._policy = policy or decide
        self._precheck = precheck

    async def handle(self, context: RequestContext) -> NegotiationOutcome:
        """Classify one inbound request and return the pre-task outcome.

        Args:
            context: inbound A2A request context.

        Returns:
            Either a short-circuit message (no Task) or the request to hand to the deterministic executor.
        """
        with log_context(
            task_id=context.task_id,
            context_id=context.context_id,
            role="server",
        ):
            if context.current_task is not None:
                return await self._handle_follow_up(context)

            message = context.message
            if message is None:
                return NegotiationOutcome.short(
                    build_error_message(context.context_id, "The request carries no A2A message.")
                )
            metadata = metadata_to_dict(message.metadata)
            if is_negotiation_message(metadata):
                return await self._handle_negotiation(context, metadata)
            return await self._handle_initial_task(context, metadata)

    # ------------------------------------------------------------------
    # Initial Task-T
    # ------------------------------------------------------------------

    async def _handle_follow_up(self, context: RequestContext) -> NegotiationOutcome:
        """Validate a Modify Task-T sent on an existing Task, or keep the legacy no-op.

        A plain follow-up (no ``samplePhase=modify`` marker) keeps the previous behaviour: the
        executor re-enqueues the Task and starts no second plan. The sample's planned-outage
        two-phase run marks its second message, so a real A2A continuation can be validated and
        handed to the modification path.
        """
        current = context.current_task
        message = context.message
        if current is None:
            return NegotiationOutcome.execution(context)
        metadata = metadata_to_dict(message.metadata) if message is not None else {}
        prompt = task_prompt_from_metadata(metadata)
        if not prompt.strip():
            # A negotiation/accept follow-up carries Negotiation-T, not Task-T; keep the legacy
            # no-op so the existing flows are unchanged.
            return NegotiationOutcome.execution(context)
        if not _MODIFY_OPERATION_PATTERN.search(prompt):
            # The operation type is the semantic signal. A non-Modify Task-T on an existing Task
            # is not a modification; do not spend a validation call on it and keep the no-op.
            return NegotiationOutcome.execution(context)
        missing = missing_extensions(context.requested_extensions, (TASK_T_EXTENSION_URI,))
        if missing:
            return NegotiationOutcome.short(
                build_error_message(
                    context.context_id,
                    f"The request does not declare the required A2A extension(s): {', '.join(missing)}.",
                )
            )
        try:
            filled = await self._call(
                self._prompt_server.validate_task_prompt_and_data_filling,
                prompt,
                self._task_schema,
                self._task_template_uri,
                stage="task_t_modify_validation",
            )
        except ContentValidationError as exc:
            return NegotiationOutcome.short(
                build_error_message(context.context_id, f"Modify Task-T validation failed: {exc}")
            )
        except (A2ATError, ValueError) as exc:
            return NegotiationOutcome.short(
                build_error_message(context.context_id, f"Modify Task-T validation failed: {exc}")
            )
        params = filled.data if isinstance(filled.data, dict) else {}
        operation = str(params.get("operationType") or params.get("operation_type") or "")
        if operation.strip().lower() != SAMPLE_PHASE_MODIFY:
            # The Task-T operation type is the semantic signal; a non-Modify Task-T on an existing
            # Task is not a modification and keeps the legacy no-op.
            return NegotiationOutcome.execution(context)
        if current.status.state != TaskState.TASK_STATE_WORKING:
            return NegotiationOutcome.short(
                build_error_message(
                    context.context_id,
                    "The Task is not in a modifiable state; a Modify needs a WORKING Task.",
                )
            )
        finding = self._precheck(filled, self._task_schema, prompt=prompt) if self._precheck is not None else None
        if finding is not None:
            return NegotiationOutcome.short(
                build_error_message(
                    context.context_id,
                    f"The Modify Task-T is not executable: {finding.detail or finding.kind.value}.",
                )
            )
        self._rewrite_task_prompt(context, prompt)
        self._logger.info("Modify Task-T validated; handing the existing Task to the executor")
        return NegotiationOutcome.modify(context, prompt, params)

    async def _handle_initial_task(self, context: RequestContext, metadata: Mapping[str, object]) -> NegotiationOutcome:
        """Validate the initial Task-T prompt and open a negotiation when it maps to one."""
        context_id = context.context_id
        missing = missing_extensions(context.requested_extensions, (TASK_T_EXTENSION_URI,))
        if missing:
            return NegotiationOutcome.short(
                build_error_message(
                    context_id,
                    f"The request does not declare the required A2A extension(s): {', '.join(missing)}.",
                )
            )
        prompt = task_prompt_from_metadata(metadata)
        if not prompt.strip():
            return NegotiationOutcome.short(build_error_message(context_id, "The request carries no Task-T prompt."))
        verified = False
        try:
            filled = await self._call(
                self._prompt_server.validate_task_prompt_and_data_filling,
                prompt,
                self._task_schema,
                self._task_template_uri,
                stage="task_t_server_validation",
            )
        except ContentValidationError as exc:
            kind = negotiation_kind_for_errors(exc.errors)
            if kind is None:
                return NegotiationOutcome.rejected(self._build_rejected_task(context, exc))
            # The live validator may reject an unknown area as a generic value conflict. The
            # sample precheck has the business text for that case; let it win before the generic
            # conflict heuristic treats the verdict as a false overlap.
            if self._precheck is not None:
                finding = self._precheck(_filled_from_error(exc), self._task_schema, prompt=prompt)
                if finding is not None and finding.proposal_text:
                    state = NegotiationState(
                        kind=finding.kind,
                        round=1,
                        max_rounds=self._max_rounds,
                        missing_items=finding.missing_items,
                        proposal_text=finding.proposal_text,
                        options=finding.options,
                    )
                    return await self._emit_policy_decision(context_id, self._policy(state))
            verified = False
            if kind is not NegotiationKind.INFORMATION and _TARGET_SEGMENT_PATTERN.search(prompt):
                # A conflict verdict is model-judged. When the sample's own check of the Task-T
                # finds no conflict, the request is valid and must run: a live model sometimes
                # reports an overlap that the prompt does not contain. A prompt without a target
                # leaves nothing to check against, so the verdict stands.
                confirmed = self._verified_conflict_kind(exc, prompt)
                if confirmed is not None:
                    kind = confirmed
                else:
                    self._logger.warning(
                        "Task-T validation reported a %s conflict (%s) that the sample data does "
                        "not confirm; continuing with the request",
                        kind.value,
                        ", ".join(str(error.code) for error in exc.errors),
                    )
                    filled = _filled_from_error(exc)
                    verified = True
            if not verified:
                state = NegotiationState(
                    kind=kind,
                    round=1,
                    max_rounds=self._max_rounds,
                    missing_items=_missing_slot_names(exc.errors),
                )
                return await self._emit_policy_decision(context_id, self._policy(state))
        except (A2ATError, ValueError) as exc:
            return NegotiationOutcome.short(build_error_message(context_id, f"Task-T validation failed: {exc}"))

        # The SDK's semantic verdict is model-dependent. The deployment may still know signals
        # (missing required fields, an infeasible plan) that must open a negotiation.
        finding = (
            None
            if verified
            else (self._precheck(filled, self._task_schema, prompt=prompt) if self._precheck is not None else None)
        )
        if finding is not None:
            self._logger.info(
                "Task-T precheck opened a %s negotiation: %s",
                finding.kind.value,
                finding.detail or "no detail",
            )
            state = NegotiationState(
                kind=finding.kind,
                round=1,
                max_rounds=self._max_rounds,
                missing_items=finding.missing_items,
                proposal_text=finding.proposal_text,
                options=finding.options,
            )
            return await self._emit_policy_decision(context_id, self._policy(state))

        params = filled.data if isinstance(filled.data, dict) else {}
        operation = str(params.get("operationType") or params.get("operation_type") or "")
        if operation.strip().lower() == SAMPLE_PHASE_MODIFY:
            # This sample implements Modify as a real continuation of an existing Task. A Modify
            # Task-T without a loaded Task has no baseline plan to update, so reject it instead of
            # silently creating a new Task that would execute like a Create.
            return NegotiationOutcome.short(
                build_error_message(
                    context_id,
                    "A Modify Task-T must continue an existing WORKING Task; no Task was loaded.",
                )
            )

        self._rewrite_task_prompt(context, prompt)
        self._logger.info("initial Task-T validated; handing the request to the deterministic executor")
        return NegotiationOutcome.execution(context)

    # ------------------------------------------------------------------
    # Negotiation messages
    # ------------------------------------------------------------------

    async def _handle_negotiation(self, context: RequestContext, metadata: Mapping[str, object]) -> NegotiationOutcome:
        """Dispatch one inbound negotiation message by performative."""
        context_id = context.context_id
        session = parse_negotiation_context(metadata)
        if session is None:
            return NegotiationOutcome.short(
                build_error_message(context_id, "The negotiation message carries no valid negotiationContext.")
            )
        performative = session.performative
        with log_context(negotiation_id=session.id, round=session.round):
            self._logger.info("negotiation message received: %s", performative.value)
            if performative is NegotiationPerformative.ACCEPT:
                return await self._handle_accept(context, metadata, session)
            if performative is NegotiationPerformative.REJECT:
                return await self._handle_terminal(context, metadata, session)
            if performative is NegotiationPerformative.ABORT:
                return await self._handle_terminal(context, metadata, session)
            return await self._handle_counter_propose(context, metadata, session)

    async def _handle_accept(
        self,
        context: RequestContext,
        metadata: Mapping[str, object],
        session: NegotiationContext,
    ) -> NegotiationOutcome:
        """Validate the accept and hand the agreed Task-T prompt to the deterministic executor."""
        context_id = context.context_id
        prompt = negotiation_prompt_from_metadata(metadata)
        template_uri = metadata.get(TEMPLATE_URI_METADATA_KEY)
        if not prompt.strip() or not isinstance(template_uri, str):
            return NegotiationOutcome.short(
                build_error_message(context_id, "The accept message carries no Negotiation-T content.")
            )
        try:
            await self._call(
                self._prompt_server.validate_accept_prompt_and_data_filling,
                prompt,
                session,
                NEGOTIATION_PARAM_SCHEMA,
                template_uri,
                stage="negotiation_accept_validation",
            )
        except (A2ATError, ValueError) as exc:
            return await self._negotiation_failure(context_id, session, f"Accept validation failed: {exc}")

        task_prompt = task_prompt_from_metadata(metadata)
        if not task_prompt.strip():
            return NegotiationOutcome.short(
                build_error_message(context_id, "The accept message carries no agreed Task-T prompt.")
            )
        try:
            await self._call(
                self._prompt_server.validate_task_prompt_and_data_filling,
                task_prompt,
                self._task_schema,
                self._task_template_uri,
                stage="task_t_accept_validation",
            )
        except ContentValidationError as exc:
            kind = negotiation_kind_for_errors(exc.errors)
            confirmable = (
                kind is not None
                and kind is not NegotiationKind.INFORMATION
                and _TARGET_SEGMENT_PATTERN.search(task_prompt) is not None
            )
            if not confirmable or self._verified_conflict_kind(exc, task_prompt) is not None:
                return await self._negotiation_failure(context_id, session, f"Accepted Task-T validation failed: {exc}")
            self._logger.warning(
                "accepted Task-T validation reported a %s conflict (%s) that the sample data does "
                "not confirm; creating the task",
                kind.value,
                ", ".join(str(error.code) for error in exc.errors),
            )
        except (A2ATError, ValueError) as exc:
            return await self._negotiation_failure(context_id, session, f"Accepted Task-T validation failed: {exc}")

        self._rewrite_task_prompt(context, task_prompt)
        self._logger.info("negotiation accepted at round %s; creating the task", session.round)
        return NegotiationOutcome.execution(context)

    async def _handle_terminal(
        self,
        context: RequestContext,
        metadata: Mapping[str, object],
        session: NegotiationContext,
    ) -> NegotiationOutcome:
        """End a rejected/aborted negotiation with a plain message and no Task."""
        is_reject = session.performative is NegotiationPerformative.REJECT
        validator = (
            self._prompt_server.validate_reject_prompt_and_data_filling
            if is_reject
            else self._prompt_server.validate_abort_prompt_and_data_filling
        )
        stage = "negotiation_reject_validation" if is_reject else "negotiation_abort_validation"
        template_uri = metadata.get(TEMPLATE_URI_METADATA_KEY)
        prompt = negotiation_prompt_from_metadata(metadata)
        if isinstance(template_uri, str) and prompt.strip():
            try:
                await self._call(validator, prompt, session, NEGOTIATION_PARAM_SCHEMA, template_uri, stage=stage)
            except (A2ATError, ValueError) as exc:
                self._logger.warning("terminal negotiation message failed validation: %s", exc)
        self._logger.info(
            "negotiation ended by client %s at round %s; no task is created",
            session.performative.value,
            session.round,
        )
        return NegotiationOutcome.short(build_end_message(context.context_id))

    async def _handle_counter_propose(
        self,
        context: RequestContext,
        metadata: Mapping[str, object],
        session: NegotiationContext,
    ) -> NegotiationOutcome:
        """Validate a client counter proposal and let the policy answer it."""
        context_id = context.context_id
        prompt = negotiation_prompt_from_metadata(metadata)
        template_uri = metadata.get(TEMPLATE_URI_METADATA_KEY)
        if not prompt.strip() or not isinstance(template_uri, str):
            return NegotiationOutcome.short(
                build_error_message(context_id, "The counter proposal carries no Negotiation-T content.")
            )
        try:
            await self._call(
                self._prompt_server.validate_propose_prompt_and_data_filling,
                prompt,
                session,
                NEGOTIATION_PARAM_SCHEMA,
                template_uri,
                stage="negotiation_propose_validation",
            )
        except (A2ATError, ValueError) as exc:
            return await self._negotiation_failure(context_id, session, f"Counter proposal validation failed: {exc}")

        kind = negotiation_kind_for_template_uri(template_uri)
        if kind is None:
            return NegotiationOutcome.short(
                build_error_message(context_id, f"Unsupported negotiation template URI: {template_uri}")
            )
        sample = SampleProposal.try_parse(metadata.get(SAMPLE_PROPOSAL_METADATA_KEY))
        state = NegotiationState(
            kind=kind,
            round=session.round,
            max_rounds=session.max_rounds,
            inbound=InboundDecision(performative=NegotiationPerformative.PROPOSE, sample_proposal=sample),
        )
        return await self._emit_policy_decision(context_id, self._policy(state))

    # ------------------------------------------------------------------
    # Shared helpers
    # ------------------------------------------------------------------

    def _build_rejected_task(self, context: RequestContext, error: ContentValidationError) -> Task:
        """Build the terminal ``REJECTED`` Task of an unmapped Task-T validation failure.

        The task is enqueued as-is: the agent never runs, so the pre-task negotiation model is kept.
        The status message carries the readable error text plus the machine-readable structured
        errors (the SDK's ``SlotValidationError`` entries) in its metadata.
        """
        context_id = context.context_id or ""
        task_id = context.task_id or ""
        status_message = build_agent_message(
            context_id=context_id,
            text=_validation_error_text(error),
        )
        status_message.metadata[STRUCTURED_ERRORS_METADATA_KEY] = {  # ty: ignore[invalid-assignment]
            "code": error.code_str,
            "errors": _structured_errors(error.errors),
        }
        message = context.message
        return Task(
            id=task_id,
            context_id=context_id,
            status=TaskStatus(
                state=TaskState.TASK_STATE_REJECTED,
                message=status_message,
            ),
            history=[message] if message is not None else [],
        )

    async def _emit_policy_decision(self, context_id: str | None, decision: PolicyDecision) -> NegotiationOutcome:
        """Turn one policy decision into a standalone negotiation message or an execution request."""
        content = decision.content
        if decision.action == "end":
            return NegotiationOutcome.short(build_end_message(context_id))
        if decision.action == "propose" and isinstance(content, ProposalContent):
            session = NegotiationContext(
                id=context_id or "",
                round=content.round,
                max_rounds=self._max_rounds,
                performative=NegotiationPerformative.PROPOSE,
            )
            try:
                generated = await self._call(
                    self._prompt_server.generate_negotiation_propose_prompt_from_text,
                    content.text,
                    session,
                    content.template_uri,
                    stage="negotiation_propose_extraction",
                )
            except (A2ATError, ValueError) as exc:
                # A failed (e.g. timed-out) proposal generation must reach the client as a plain
                # protocol message, not as a transport error: the client is still waiting for an
                # event, and an unhandled exception would only surface as a connection failure.
                return NegotiationOutcome.short(
                    build_error_message(context_id, f"Negotiation proposal generation failed: {exc}")
                )
            self._logger.info("server propose generated: kind=%s round=%s", content.kind.value, content.round)
            extra_metadata = (
                {SAMPLE_NEGOTIATION_OPTIONS_METADATA_KEY: dict(content.options)} if content.options else None
            )
            return NegotiationOutcome.short(
                build_negotiation_message(
                    generated,
                    context_id=session.id,
                    text=PROPOSE_MESSAGE_TEXT,
                    extra_metadata=extra_metadata,
                )
            )
        if decision.action == "abort" and isinstance(content, AbortContent):
            session = NegotiationContext(
                id=context_id or "",
                round=content.round,
                max_rounds=self._max_rounds,
                performative=NegotiationPerformative.ABORT,
            )
            try:
                generated = await self._call(
                    self._prompt_server.generate_negotiation_abort_prompt_from_text,
                    content.text,
                    session,
                    content.template_uri,
                    stage="negotiation_abort_extraction",
                )
            except (A2ATError, ValueError) as exc:
                return NegotiationOutcome.short(
                    build_error_message(context_id, f"Negotiation abort generation failed: {exc}")
                )
            self._logger.info("server abort generated at round %s", content.round)
            return NegotiationOutcome.short(
                build_negotiation_message(generated, context_id=session.id, text=ABORT_MESSAGE_TEXT)
            )
        raise AssertionError(f"Unsupported policy decision: {decision.action}")

    def _verified_conflict_kind(
        self,
        exc: ContentValidationError,
        prompt: str,
    ) -> NegotiationKind | None:
        """Return the conflict the sample's own Task-T check confirms, or ``None``.

        The failed validation's parameters are not trustworthy for this check: the validator nulls
        the slot it rejected, so the prompt is the only source that still carries the target and
        the background event. A ``None`` answer means the model reported a conflict the sample's
        data does not have.
        """
        if self._precheck is None:
            return None
        finding = self._precheck(_filled_from_error(exc), self._task_schema, prompt=prompt)
        if finding is None or finding.kind is NegotiationKind.INFORMATION:
            return None
        return finding.kind

    async def _negotiation_failure(
        self, context_id: str | None, session: NegotiationContext, text: str
    ) -> NegotiationOutcome:
        """Answer an invalid negotiation message with an abort beyond the round budget, else an error."""
        self._logger.warning("negotiation message rejected: %s", text)
        if session.round <= session.max_rounds:
            return NegotiationOutcome.short(build_error_message(context_id, text))
        self._logger.warning(
            "abort requested after an invalid answer beyond the round budget (round=%s)", session.round
        )
        decision = PolicyDecision(
            "abort",
            AbortContent(
                round=session.round,
                template_uri=ABORT_TEMPLATE_URI,
                text="The negotiation exceeded its round budget without a valid answer.",
                reason="invalid-answer-beyond-budget",
            ),
            "The negotiation exceeded its round budget with an invalid answer.",
        )
        return await self._emit_policy_decision(context_id, decision)

    @staticmethod
    async def _call(function: Callable[..., _ResultT], *args: object, stage: str | None = None) -> _ResultT:
        """Run one synchronous SDK call in a worker thread under its capture stage.

        ``asyncio.to_thread`` copies the current context, so the ``trace_stage`` label reaches the
        traced SDK provider running in the worker thread.
        """
        if stage is None:
            return await asyncio.to_thread(function, *args)
        with trace_stage(stage):
            return await asyncio.to_thread(function, *args)

    def _rewrite_task_prompt(self, context: RequestContext, prompt: str) -> None:
        """Replace the request body with the agreed Task-T prompt.

        The A2A-T prompt itself stays in ``metadata[Task-T/v1]`` for audit; the message part is the
        agreed prompt so transports and logs that only read the text part see the negotiated task.
        """
        message = context.message
        if message is None:
            return
        message.ClearField("parts")
        message.parts.add(text=prompt)
        message.metadata[TASK_T_EXTENSION_URI] = prompt


def _missing_slot_names(errors: Sequence[SlotValidationError]) -> tuple[str, ...]:
    """Return the distinct business labels of a structured validation failure.

    ``content.entry_field_missing`` carries the missing field in ``facts.field_label`` (for example
    "Rate"), which is what the operator must supply; the raw slot name would leak an internal
    camelCase identifier into the negotiation proposal. ``content.param_missing`` carries the
    section label instead. Fall back to the slot name only when neither fact is present.
    """
    names: list[str] = []
    for error in errors:
        facts = error.facts or {}
        label = ""
        for key in ("field_label", "section_label"):
            value = facts.get(key)
            if isinstance(value, str) and value.strip():
                label = value.strip()
                break
        if not label:
            label = error.slot_name.strip()
        if label and label not in names:
            names.append(label)
    return tuple(names)


#: One ``HH:MM~HH:MM,<rate>Mbps`` target segment in a rendered Task-T prompt.
_TARGET_SEGMENT_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"\d{2}:\d{2}~\d{2}:\d{2}\s*,\s*\d+(?:\.\d+)?\s*Mbps", re.IGNORECASE
)

#: The rendered Task-T's ``Operation Type: Modify`` section, in either template language.
_MODIFY_OPERATION_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"(?:^|\n)\s*##\s*(?:Operation Type|操作类型)[^\n]*\n+\s*(?:Modify|修改)\s*(?:\n|$)",
    re.IGNORECASE,
)


def _filled_from_error(exc: ContentValidationError) -> FilledParamData:
    """Return the parameters a failed validation extracted (empty when it carries none)."""
    params = exc.params if isinstance(exc.params, Mapping) else {}
    return FilledParamData(dict(params))


def _structured_errors(errors: Sequence[SlotValidationError]) -> list[dict[str, object]]:
    """Render ``SlotValidationError`` entries as machine-readable mappings."""
    return [
        {
            "slotName": item.slot_name,
            "code": item.code,
            "message": item.message,
            "facts": dict(item.facts or {}),
        }
        for item in errors
    ]


def _validation_error_text(error: ContentValidationError) -> str:
    """Render one unmapped content-validation failure with its structured codes."""
    codes = ", ".join(f"{item.slot_name}:{item.code}" for item in error.errors) or "no structured errors"
    return f"Task-T validation failed and cannot open a negotiation ({codes}): {error}"
