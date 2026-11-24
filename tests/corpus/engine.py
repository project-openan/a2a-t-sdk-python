# Copyright (c) 2026 Huawei Technologies Co., Ltd.
# All Rights Reserved.
#
# SPDX-License-Identifier: Apache-2.0
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.

"""Case and scenario engines of the negotiation test corpus.

Port of the Java ``a2a-t-corpus`` ``CaseEngine`` and ``ScenarioEngine``: executes one expanded
corpus case against the production negotiation content wiring and asserts its expectation block.

Everything except the LLM client is production assembly: the case engine builds a real
:class:`~a2a_t.negotiation.generation.orchestrator.NegotiationGenerationOrchestrator` through the
real builder for the language of the case, wires the ``inject`` hooks onto real builder injection
points (``resource_access`` for the generation template-not-found matrix, ``semantic_validator``
for the validate-family prompt-resource-not-found mapping) and dispatches on the
:class:`~tests.corpus.models.NegotiationApi` enum, so a misspelled API name fails
at corpus load time and a renamed service method fails this import. The three task-family APIs run
through the :class:`~tests.corpus.assemblers.TaskApiAssembler`, the real facade builders' assembly
of the closed loop (Q21) with the scripted LLM client injected at the builders' LLM seam.

Result normalization: a success run returns the produced
:class:`~a2a_t.core.metadata.MetadataContent` or
:class:`~a2a_t.core.validation_pipeline.FilledParamData`, a failure run captures the raised
exception. The expectation comparison covers outcome, exception name (through the D22 Java-name
shim), error code, message fragments, slot errors (slot plus code pairs, exact but
order-insensitive), the exact LLM call count, the golden fixture text (per language, byte equality
after CRLF normalization), the metadata echoes, the expected merged parameter map, the P0 behavior
contracts and the Q17 C+ differential double run (fromText == fromData == golden, the from-data
leg proven zero-call with an assertion-only client). Every mismatch fails with the case id, the
JSON path of the expectation and the expected-versus-actual pair.

The scenario engine is deliberately thin: every step is a full corpus case executed by the case
engine with its own scripted LLM behavior and its exact per-step ``llmCalls`` expectation; the
scenario layer adds only what a step cannot express alone (``prompt.fromStep`` resolution,
fail-fast, per-role LLM accounting, the flow-level expectation and the
``expect.paramsFromStep`` closed-loop causal chain).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Final, TypeAlias

from a2a_t.core.errors.exceptions import (
    A2ATError,
    A2ATParamExtractionError,
    NegotiationParamExtractionError,
    SlotValidationError,
)
from a2a_t.core.metadata import (
    NEGOTIATION_CONTEXT_METADATA_KEY,
    TEMPLATE_URI_METADATA_KEY,
    MetadataContent,
    NegotiationContext,
    NegotiationPerformative,
)
from a2a_t.core.template_uri import TemplateUri
from a2a_t.core.validation_pipeline import FilledParamData
from a2a_t.negotiation.generation import NegotiationContentService
from a2a_t.negotiation.generation.builder import NegotiationGenerationOrchestratorBuilder
from tests.corpus.assemblers import (
    DEFAULT_TASK_MAX_ATTEMPTS,
    INJECT_FAILING_SEMANTIC_VALIDATOR,
    INJECT_FAILING_TEMPLATE_LOADER,
    TaskApiAssembler,
    assemble_typed_input,
    failing_semantic_validator,
    failing_template_loader_access,
)
from tests.corpus.contracts import KNOWN_CONTRACT_NAMES, Contract
from tests.corpus.llm_stub import ScriptedNegotiationLlmClient
from tests.corpus.models import (
    ContextSpec,
    Expectation,
    Family,
    NegotiationApi,
    NegotiationCase,
    PromptSource,
    ScenarioCase,
)
from tests.corpus.shim import resolve_java_exception

__all__ = ["CaseEngine", "CaseOutcome", "ScenarioEngine", "ScenarioRunResult"]

#: The scenario step record of the authoritative corpus models (nested in ``ScenarioCase``).
ScenarioStep: TypeAlias = ScenarioCase.ScenarioStep

#: Root of the committed golden fixtures, per language (``golden/<lang>/<name>.md``).
GOLDEN_ROOT: Final[Path] = Path(__file__).resolve().parents[1] / "resources" / "negotiation-cases" / "golden"

#: Longest reported text excerpt in expectation failure messages.
_MAX_REPORTED_TEXT_LENGTH: Final[int] = 400

#: Context keys the merged parameter data of the validate family must always carry.
_CONTEXT_PARAM_KEYS: Final[tuple[str, ...]] = ("id", "round", "maxRounds")

#: Conclusion literal each terminal or abort generation API must render.
_CONCLUSION_LITERALS: Final[dict[NegotiationApi, str]] = {
    NegotiationApi.GENERATE_ACCEPT_FROM_TEXT: "Accept",
    NegotiationApi.GENERATE_ACCEPT_FROM_DATA: "Accept",
    NegotiationApi.GENERATE_REJECT_FROM_TEXT: "Reject",
    NegotiationApi.GENERATE_REJECT_FROM_DATA: "Reject",
    NegotiationApi.GENERATE_ABORT_FROM_TEXT: "Abort",
    NegotiationApi.GENERATE_ABORT_FROM_DATA: "Abort",
}

#: Performative each generation and validate API addresses; the task family addresses none.
_PERFORMATIVE_OF: Final[dict[NegotiationApi, NegotiationPerformative]] = {
    NegotiationApi.GENERATE_PROPOSE_FROM_TEXT: NegotiationPerformative.PROPOSE,
    NegotiationApi.GENERATE_PROPOSE_FROM_DATA: NegotiationPerformative.PROPOSE,
    NegotiationApi.VALIDATE_PROPOSE_PROMPT_AND_DATA_FILLING: NegotiationPerformative.PROPOSE,
    NegotiationApi.GENERATE_ACCEPT_FROM_TEXT: NegotiationPerformative.ACCEPT,
    NegotiationApi.GENERATE_ACCEPT_FROM_DATA: NegotiationPerformative.ACCEPT,
    NegotiationApi.VALIDATE_ACCEPT_PROMPT_AND_DATA_FILLING: NegotiationPerformative.ACCEPT,
    NegotiationApi.GENERATE_REJECT_FROM_TEXT: NegotiationPerformative.REJECT,
    NegotiationApi.GENERATE_REJECT_FROM_DATA: NegotiationPerformative.REJECT,
    NegotiationApi.VALIDATE_REJECT_PROMPT_AND_DATA_FILLING: NegotiationPerformative.REJECT,
    NegotiationApi.GENERATE_ABORT_FROM_TEXT: NegotiationPerformative.ABORT,
    NegotiationApi.GENERATE_ABORT_FROM_DATA: NegotiationPerformative.ABORT,
    NegotiationApi.VALIDATE_ABORT_PROMPT_AND_DATA_FILLING: NegotiationPerformative.ABORT,
}


class CaseEngine:
    """Executes one expanded corpus case against the production wiring and asserts its expect block."""

    def run(
        self,
        test_case: NegotiationCase,
        prompt_override: str | None = None,
        in_scenario: bool = False,
    ) -> CaseOutcome:
        """Run one case, optionally overriding the prompt input of the validate family.

        The scenario engine resolves ``prompt.from_step`` references into prompt texts and hands
        them in through ``prompt_override``; a standalone validate case resolves its golden or
        inline prompt itself. Scenario mode defers the ``expect.params_from_step`` check to the
        scenario engine, which resolves the cross-step reference after the step ran.

        Args:
            test_case: expanded corpus case.
            prompt_override: prompt text resolved from an earlier scenario step, or ``None``.
            in_scenario: ``True`` inside the ScenarioEngine, ``False`` for a standalone run.

        Returns:
            the normalized outcome of the run.

        Raises:
            AssertionError: when any expectation mismatches.
        """
        llm_client = self._llm_client_for(test_case)
        service = NegotiationContentService(self._orchestrator_for(test_case, llm_client))
        task_api = (
            TaskApiAssembler(test_case.language, _task_max_attempts(test_case), llm_client)
            if test_case.api.family is Family.TASK
            else None
        )
        value: MetadataContent | FilledParamData | None = None
        failure: BaseException | None = None
        try:
            value = self._invoke(service, task_api, test_case, prompt_override)
        except Exception as error:  # noqa: BLE001 - the outcome comparison owns the reporting
            failure = error
        outcome = CaseOutcome(
            test_case=test_case,
            value=value,
            failure=failure,
            llm_calls=llm_client.call_count,
            llm_client=llm_client,
        )
        self._assert_expectations(outcome, in_scenario)
        return outcome

    # ------------------------------------------------------------------ outcome record

    # ------------------------------------------------------------------ production wiring

    @staticmethod
    def _llm_client_for(test_case: NegotiationCase) -> ScriptedNegotiationLlmClient:
        """Build the scripted client of one case: strict steps, or the zero-call proof client."""
        if test_case.api.family is Family.FROM_DATA or test_case.llm is None:
            return ScriptedNegotiationLlmClient.assertion_only()
        return ScriptedNegotiationLlmClient(test_case.llm.steps)

    @staticmethod
    def _orchestrator_for(
        test_case: NegotiationCase, llm_client: ScriptedNegotiationLlmClient
    ) -> NegotiationGenerationOrchestratorBuilder:
        """Build the real production orchestrator of one case through the real builder.

        The ``inject`` hooks are wired onto their real builder injection points: the failing
        template loader onto ``resource_access`` (the D31 seam of the Java ``templateLoader``),
        the failing semantic validator onto ``semantic_validator``.
        """
        builder = NegotiationGenerationOrchestratorBuilder(language=test_case.language, llm_client=llm_client)
        if test_case.llm is not None and test_case.llm.max_attempts is not None:
            builder.max_attempts = test_case.llm.max_attempts
        if test_case.inject == INJECT_FAILING_TEMPLATE_LOADER:
            builder.resource_access = failing_template_loader_access()
        if test_case.inject == INJECT_FAILING_SEMANTIC_VALIDATOR:
            builder.semantic_validator = failing_semantic_validator()
        return builder.build()

    # ------------------------------------------------------------------ API dispatch

    def _invoke(
        self,
        service: NegotiationContentService,
        task_api: TaskApiAssembler | None,
        test_case: NegotiationCase,
        prompt_override: str | None,
    ) -> MetadataContent | FilledParamData:
        """Dispatch one case onto its production API with the case's resolved inputs."""
        template_uri = _parse_template_uri(test_case)
        context = _to_context(test_case.context, test_case.api)
        match test_case.api:
            case NegotiationApi.GENERATE_PROPOSE_FROM_TEXT:
                return service.generate_propose_from_text(test_case.input_text, context, template_uri)
            case NegotiationApi.GENERATE_ACCEPT_FROM_TEXT:
                return service.generate_accept_from_text(test_case.input_text, context, template_uri)
            case NegotiationApi.GENERATE_REJECT_FROM_TEXT:
                return service.generate_reject_from_text(test_case.input_text, context, template_uri)
            case NegotiationApi.GENERATE_ABORT_FROM_TEXT:
                return service.generate_abort_from_text(test_case.input_text, context, template_uri)
            case NegotiationApi.GENERATE_PROPOSE_FROM_DATA:
                return service.generate_propose_from_data(
                    assemble_typed_input(
                        _require_input_data(test_case),
                        context,
                        template_uri,
                        NegotiationPerformative.PROPOSE,
                        test_case.language,
                    ),
                    template_uri,
                )
            case NegotiationApi.GENERATE_ACCEPT_FROM_DATA:
                return service.generate_accept_from_data(
                    assemble_typed_input(
                        _require_input_data(test_case),
                        context,
                        template_uri,
                        NegotiationPerformative.ACCEPT,
                        test_case.language,
                    ),
                    template_uri,
                )
            case NegotiationApi.GENERATE_REJECT_FROM_DATA:
                return service.generate_reject_from_data(
                    assemble_typed_input(
                        _require_input_data(test_case),
                        context,
                        template_uri,
                        NegotiationPerformative.REJECT,
                        test_case.language,
                    ),
                    template_uri,
                )
            case NegotiationApi.GENERATE_ABORT_FROM_DATA:
                return service.generate_abort_from_data(
                    assemble_typed_input(
                        _require_input_data(test_case),
                        context,
                        template_uri,
                        NegotiationPerformative.ABORT,
                        test_case.language,
                    ),
                    template_uri,
                )
            case NegotiationApi.VALIDATE_PROPOSE_PROMPT_AND_DATA_FILLING:
                return service.validate_propose_prompt_and_data_filling(
                    _prompt_of(test_case, prompt_override), context, _schema_of(test_case), template_uri
                )
            case NegotiationApi.VALIDATE_ACCEPT_PROMPT_AND_DATA_FILLING:
                return service.validate_accept_prompt_and_data_filling(
                    _prompt_of(test_case, prompt_override), context, _schema_of(test_case), template_uri
                )
            case NegotiationApi.VALIDATE_REJECT_PROMPT_AND_DATA_FILLING:
                return service.validate_reject_prompt_and_data_filling(
                    _prompt_of(test_case, prompt_override), context, _schema_of(test_case), template_uri
                )
            case NegotiationApi.VALIDATE_ABORT_PROMPT_AND_DATA_FILLING:
                return service.validate_abort_prompt_and_data_filling(
                    _prompt_of(test_case, prompt_override), context, _schema_of(test_case), template_uri
                )
            case NegotiationApi.GENERATE_TASK_PROMPT_FROM_TEXT:
                return _require_task_api(task_api, test_case).generate_task_prompt_from_text(
                    _require_input_text(test_case), _require_typed_uri(template_uri, test_case)
                )
            case NegotiationApi.GENERATE_TASK_PROMPT_FROM_DATA_WITH_SCHEMA:
                return _require_task_api(task_api, test_case).generate_task_prompt_from_data_with_schema(
                    _data_of(test_case), _require_schema(test_case), _require_typed_uri(template_uri, test_case)
                )
            case NegotiationApi.VALIDATE_TASK_PROMPT_AND_DATA_FILLING:
                return _require_task_api(task_api, test_case).validate_task_prompt_and_data_filling(
                    _prompt_of(test_case, prompt_override),
                    _require_schema(test_case),
                    _require_typed_uri(template_uri, test_case),
                )
        raise RuntimeError(f"unhandled corpus api {test_case.api}")  # pragma: no cover

    # ------------------------------------------------------------------ expectation comparison

    def _assert_expectations(self, outcome: CaseOutcome, in_scenario: bool) -> None:
        """Assert the whole expectation block of one executed case."""
        test_case = outcome.test_case
        expect = test_case.expect
        if expect.success:
            if outcome.failure is not None:
                _fail(test_case, "$.expect.outcome", "success", f"failure ({outcome.failure})")
            self._assert_success_fields(outcome, in_scenario)
        else:
            if outcome.failure is None:
                _fail(
                    test_case,
                    "$.expect.outcome",
                    "failure",
                    f"success ({_type_name(outcome.value)}: {outcome.value})",
                )
            self._assert_failure_fields(outcome)
        if expect.llm_calls is not None and expect.llm_calls != outcome.llm_calls:
            _fail(test_case, "$.expect.llmCalls", str(expect.llm_calls), str(outcome.llm_calls))
        for contract_name in expect.contracts:
            self._assert_contract(outcome, contract_name)
        if expect.differential:
            self._run_differential(outcome)

    def _assert_failure_fields(self, outcome: CaseOutcome) -> None:
        """Assert the failure-only fields of one expectation: exception, code, message, slot errors."""
        test_case = outcome.test_case
        expect = test_case.expect
        failure = outcome.failure
        if expect.exception is not None:
            shim = resolve_java_exception(expect.exception)
            if shim.python_type is None:
                # Collapsed Java type: only the paired error-code expectation can assert it.
                if expect.code is None:
                    raise ValueError(
                        f"{test_case.error_prefix()} $.expect.exception: the Java exception name "
                        f"'{expect.exception}' maps to no Python exception type ({shim.note}); a "
                        "code expectation is required to assert this failure."
                    )
            elif not isinstance(failure, shim.python_type):
                _fail(test_case, "$.expect.exception", expect.exception, type(failure).__name__)
        if expect.code is not None:
            if not isinstance(failure, A2ATError):
                _fail(
                    test_case,
                    "$.expect.code",
                    expect.code,
                    f"no error code (exception {type(failure).__name__})",
                )
            if failure.code_str != expect.code:
                _fail(test_case, "$.expect.code", expect.code, failure.code_str)
        for fragment in expect.message_contains:
            message = str(failure)
            if fragment not in message and _performative_wording(fragment) not in message:
                _fail(
                    test_case,
                    "$.expect.messageContains",
                    f"a message containing '{fragment}'",
                    f"message {_quoted(message)}",
                )
        if expect.slot_errors:
            slot_errors = _slot_errors_of(failure)
            if slot_errors is None:
                _fail(
                    test_case,
                    "$.expect.slotErrors",
                    _render_slot_errors(expect.slot_errors),
                    f"exception {type(failure).__name__} carries no slot errors",
                )
            _assert_slot_errors(test_case, expect.slot_errors, slot_errors)

    def _assert_success_fields(self, outcome: CaseOutcome, in_scenario: bool) -> None:
        """Assert the success-only fields of one expectation: golden text, metadata, params, fragments."""
        test_case = outcome.test_case
        expect = test_case.expect
        if expect.prompt_text_equals_golden is not None:
            message = _require_message(outcome, "$.expect.promptTextEqualsGolden")
            golden = _read_golden_fixture(test_case, expect.prompt_text_equals_golden)
            if _normalize(message.prompt_text) != golden:
                _fail(
                    test_case,
                    "$.expect.promptTextEqualsGolden",
                    _quoted(_truncate(golden)),
                    _quoted(_truncate(_normalize(message.prompt_text))),
                )
        if expect.metadata is not None:
            message = _require_message(outcome, "$.expect.metadata")
            if (
                expect.metadata.template_uri_echo is not None
                and expect.metadata.template_uri_echo != message.template_uri
            ):
                _fail(
                    test_case,
                    "$.expect.metadata.templateUriEcho",
                    expect.metadata.template_uri_echo,
                    str(message.template_uri),
                )
            if expect.metadata.context_echo is True:
                # The input context is built with the performative of the case's API, so it equals
                # the emitted context the generation pipeline stamped with the performative of the
                # addressed template.
                expected_context = _to_context(test_case.context, test_case.api)
                if expected_context != message.negotiation_context:
                    _fail(
                        test_case,
                        "$.expect.metadata.contextEcho",
                        str(expected_context),
                        str(message.negotiation_context),
                    )
        if expect.params or expect.missing_params is not None:
            if not isinstance(outcome.value, FilledParamData):
                json_path = (
                    "$.expect.missingParams"
                    if expect.missing_params is not None and not expect.params
                    else "$.expect.params"
                )
                expected_render = (
                    f"filled parameter data with the missing-parameter set {expect.missing_params}"
                    if expect.missing_params is not None
                    else _render(expect.params)
                )
                _fail(
                    test_case,
                    json_path,
                    expected_render,
                    f"no filled parameter data ({_type_name(outcome.value)})",
                )
            filled = outcome.value
            if expect.missing_params is not None:
                # Task semantics (Q21): the expected params are a subset check with exact values,
                # and the missing parameter set — the None-valued entries of the filled data — is
                # asserted exactly.
                for key, value in expect.params.items():
                    if key not in filled.data or filled.data.get(key) != value:
                        _fail(
                            test_case,
                            "$.expect.params",
                            f"{key}={value}",
                            f"{key}={filled.data.get(key)}",
                        )
                actual_missing = [key for key, value in filled.data.items() if value is None]
                expected_missing = set(expect.missing_params)
                if expected_missing != set(actual_missing):
                    _fail(
                        test_case,
                        "$.expect.missingParams",
                        ", ".join(expect.missing_params),
                        ", ".join(actual_missing) if actual_missing else "(none)",
                    )
            elif expect.params != filled.data:
                _fail(test_case, "$.expect.params", _render(expect.params), _render(filled.data))
        for fragment in expect.prompt_text_contains:
            message = _require_message(outcome, "$.expect.promptTextContains")
            prompt_text = message.prompt_text or ""
            if fragment not in prompt_text:
                _fail(
                    test_case,
                    "$.expect.promptTextContains",
                    f"a prompt text containing '{fragment}'",
                    _quoted(_truncate(prompt_text)),
                )
        if expect.params_from_step is not None and not in_scenario:
            raise RuntimeError(
                f"{test_case.error_prefix()} expect.paramsFromStep {expect.params_from_step} is "
                "resolved by the ScenarioEngine; run the enclosing scenario through the "
                "ScenarioEngine"
            )

    # ------------------------------------------------------------------ contracts (P0)

    def _assert_contract(self, outcome: CaseOutcome, contract_name: str) -> None:
        """Assert one referenced behavior contract, failing loudly on unknown or unlit names."""
        test_case = outcome.test_case
        contract = Contract.from_json_name(contract_name)
        if contract is None:
            _fail(
                test_case,
                "$.expect.contracts",
                f"a registered contract name (one of {', '.join(KNOWN_CONTRACT_NAMES)})",
                f"'{contract_name}'",
            )
        if not contract.is_p0:
            _fail(
                test_case,
                "$.expect.contracts",
                "a P0 contract the engine asserts today",
                f"'{contract_name}' is registered as a P1 expectation and is not yet lit",
            )
        if contract is Contract.CONCLUSION_LITERAL_PRESENT:
            self._assert_conclusion_literal_present(outcome)
        elif contract is Contract.CONTEXT_KEYS_IN_MERGED_PARAMS:
            self._assert_context_keys_in_merged_params(outcome)
        elif contract is Contract.NO_LLM_LEAK_IN_USER_MESSAGE:
            self._assert_no_llm_leak_in_user_message(outcome)
        elif contract is Contract.METADATA_TRIPLE_SHAPE:
            self._assert_metadata_triple_shape(outcome)
        else:  # pragma: no cover - the P0 set is closed
            _fail(
                test_case,
                "$.expect.contracts",
                "an implemented contract",
                f"'{contract_name}' is flagged P0 but has no implementation",
            )

    def _assert_conclusion_literal_present(self, outcome: CaseOutcome) -> None:
        """Assert the message of a terminal or abort generation API carries its conclusion literal."""
        test_case = outcome.test_case
        literal = _CONCLUSION_LITERALS.get(test_case.api)
        if literal is None:
            _fail(
                test_case,
                "$.expect.contracts[conclusionLiteralPresent]",
                "a terminal or abort generation API",
                test_case.api.json_name,
            )
        message = _require_message(outcome, "$.expect.contracts[conclusionLiteralPresent]")
        if literal not in (message.prompt_text or ""):
            _fail(
                test_case,
                "$.expect.contracts[conclusionLiteralPresent]",
                f"a prompt text containing the literal '{literal}'",
                _quoted(_truncate(message.prompt_text)),
            )

    def _assert_context_keys_in_merged_params(self, outcome: CaseOutcome) -> None:
        """Assert the merged parameter data of a validate run carries the context keys."""
        test_case = outcome.test_case
        if not isinstance(outcome.value, FilledParamData):
            _fail(
                test_case,
                "$.expect.contracts[contextKeysInMergedParams]",
                "filled parameter data carrying the context keys",
                _type_name(outcome.value),
            )
        for key in _CONTEXT_PARAM_KEYS:
            if key not in outcome.value.data:
                _fail(
                    test_case,
                    "$.expect.contracts[contextKeysInMergedParams]",
                    f"merged params containing '{key}'",
                    _render(outcome.value.data),
                )

    def _assert_no_llm_leak_in_user_message(self, outcome: CaseOutcome) -> None:
        """Assert no raw LLM failure detail reached a user-visible message."""
        test_case = outcome.test_case
        details = outcome.llm_client.leaked_failure_details
        if not details:
            return
        if outcome.failure is not None:
            visible = str(outcome.failure)
        elif outcome.message is not None:
            visible = outcome.message.prompt_text or ""
        else:
            visible = None
        if visible is None:
            _fail(
                test_case,
                "$.expect.contracts[noLlmLeakInUserMessage]",
                "a user-visible message to inspect",
                "neither a failure message nor a generated message was produced",
            )
        for detail in details:
            if detail in (visible or ""):
                _fail(
                    test_case,
                    "$.expect.contracts[noLlmLeakInUserMessage]",
                    f"a message free of the raw LLM failure detail '{detail}'",
                    _quoted(_truncate(visible)),
                )

    def _assert_metadata_triple_shape(self, outcome: CaseOutcome) -> None:
        """Assert ``build_metadata_content`` carries exactly the three negotiation metadata entries."""
        test_case = outcome.test_case
        message = _require_message(outcome, "$.expect.contracts[metadataTripleShape]")
        metadata = message.build_metadata_content()
        if (
            len(metadata) != 3
            or message.extension_uri not in metadata
            or TEMPLATE_URI_METADATA_KEY not in metadata
            or NEGOTIATION_CONTEXT_METADATA_KEY not in metadata
        ):
            _fail(
                test_case,
                "$.expect.contracts[metadataTripleShape]",
                "exactly the three entries <extensionUri, templateUri, negotiationContext>",
                str(list(metadata.keys())),
            )
        performative = _performative_of(test_case.api)
        nested = metadata.get(NEGOTIATION_CONTEXT_METADATA_KEY)
        if (
            not isinstance(nested, dict)
            or len(nested) != 4
            or "id" not in nested
            or "round" not in nested
            or "maxRounds" not in nested
            or nested.get("performative") != performative.value
        ):
            _fail(
                test_case,
                "$.expect.contracts[metadataTripleShape]",
                "the nested negotiation context with exactly <id, round, maxRounds, "
                f"performative={performative.value}>",
                str(nested),
            )

    # ------------------------------------------------------------------ differential (Q17 C+)

    def _run_differential(self, main: CaseOutcome) -> None:
        """Run the from-text/from-data/golden double assertion with a zero-call from-data leg."""
        test_case = main.test_case
        if test_case.api.family is not Family.FROM_TEXT:
            _fail(test_case, "$.expect.differential", "a from-text family API", test_case.api.json_name)
        if test_case.input_data is None:
            _fail(test_case, "$.expect.differential", "input.data alongside input.text", "no input.data")
        if test_case.expect.prompt_text_equals_golden is None:
            _fail(
                test_case,
                "$.expect.differential",
                "promptTextEqualsGolden as the third comparison leg",
                "no golden name",
            )
        from_text = _require_message(main, "$.expect.differential")
        zero_call_client = ScriptedNegotiationLlmClient.assertion_only()
        service = NegotiationContentService(self._orchestrator_for(test_case, zero_call_client))
        try:
            from_data = self._generate_from_data(service, test_case)
        except Exception as error:  # noqa: BLE001 - reported through the expectation failure
            _fail(test_case, "$.expect.differential", "a successful from-data message", f"failure ({error})")
            return
        if from_text.prompt_text != from_data.prompt_text:
            _fail(
                test_case,
                "$.expect.differential",
                f"fromText == fromData ({_quoted(_truncate(from_text.prompt_text))})",
                _quoted(_truncate(from_data.prompt_text)),
            )
        golden = _read_golden_fixture(test_case, test_case.expect.prompt_text_equals_golden)
        if _normalize(from_data.prompt_text) != golden:
            _fail(
                test_case,
                "$.expect.differential",
                f"fromData == golden {_quoted(_truncate(golden))}",
                _quoted(_truncate(_normalize(from_data.prompt_text))),
            )
        if zero_call_client.call_count != 0:
            _fail(
                test_case,
                "$.expect.differential",
                "0 LLM calls on the from-data leg",
                str(zero_call_client.call_count),
            )

    def _generate_from_data(self, service: NegotiationContentService, test_case: NegotiationCase) -> MetadataContent:
        """Run the from-data twin of one from-text case for the differential double run."""
        template_uri = _parse_template_uri(test_case)
        context = _to_context(test_case.context, test_case.api)
        data = test_case.input_data
        match test_case.api:
            case NegotiationApi.GENERATE_PROPOSE_FROM_TEXT:
                return service.generate_propose_from_data(
                    assemble_typed_input(
                        data, context, template_uri, NegotiationPerformative.PROPOSE, test_case.language
                    ),
                    template_uri,
                )
            case NegotiationApi.GENERATE_ACCEPT_FROM_TEXT:
                return service.generate_accept_from_data(
                    assemble_typed_input(
                        data, context, template_uri, NegotiationPerformative.ACCEPT, test_case.language
                    ),
                    template_uri,
                )
            case NegotiationApi.GENERATE_REJECT_FROM_TEXT:
                return service.generate_reject_from_data(
                    assemble_typed_input(
                        data, context, template_uri, NegotiationPerformative.REJECT, test_case.language
                    ),
                    template_uri,
                )
            case NegotiationApi.GENERATE_ABORT_FROM_TEXT:
                return service.generate_abort_from_data(
                    assemble_typed_input(
                        data, context, template_uri, NegotiationPerformative.ABORT, test_case.language
                    ),
                    template_uri,
                )
        raise RuntimeError(
            f"{test_case.error_prefix()} the differential run pairs a from-text API with its "
            f"from-data twin but got {test_case.api.json_name}."
        )


@dataclass
class CaseOutcome:
    """The normalized outcome of one executed case.

    Attributes:
        test_case: the executed expanded corpus case.
        value: the returned :class:`MetadataContent` or :class:`FilledParamData` on success, or
            ``None`` on failure.
        failure: the captured exception on failure, or ``None`` on success.
        llm_calls: exact number of LLM calls the run made.
        llm_client: the scripted client the run used.
    """

    test_case: NegotiationCase
    value: MetadataContent | FilledParamData | None
    failure: BaseException | None
    llm_calls: int
    llm_client: ScriptedNegotiationLlmClient

    @property
    def message(self) -> MetadataContent | None:
        """The generated message of a successful generation run, or ``None``."""
        return self.value if isinstance(self.value, MetadataContent) else None

    @property
    def filled_params(self) -> FilledParamData | None:
        """The filled parameters of a successful validation run, or ``None``."""
        return self.value if isinstance(self.value, FilledParamData) else None


@dataclass
class ScenarioRunResult:
    """The per-step accounting of one executed scenario.

    Attributes:
        scenario: the executed expanded scenario.
        step_prompt_texts: prompt text each generation step produced, keyed by step number.
        step_missing_params: missing-parameter set each validation step discovered, keyed by step
            number.
        step_filled_params: filled parameter data each validation step produced, keyed by step
            number.
        role_call_counts: LLM call totals per acting role.
        max_round: largest round value any step's context carried.
        max_rounds_limit: largest round budget any step's context carried.
        last_prompt_text: prompt text of the last successful generation step, or ``None``.
    """

    scenario: ScenarioCase
    step_prompt_texts: dict[int, str] = field(default_factory=dict)
    step_missing_params: dict[int, set[str]] = field(default_factory=dict)
    step_filled_params: dict[int, dict[str, object]] = field(default_factory=dict)
    role_call_counts: dict[str, int] = field(default_factory=dict)
    max_round: int = 0
    max_rounds_limit: int = 0
    last_prompt_text: str | None = None


class ScenarioEngine:
    """Executes one expanded corpus scenario step by step and asserts its flow-level expectation.

    Fail-fast: the first step failure aborts the whole scenario, later steps never run. Every step
    runs on its own scripted client, so the roles' counters stay independent by construction and
    the totals are tracked per role.
    """

    def __init__(self) -> None:
        """Create one scenario engine over one fresh case engine."""
        self._case_engine = CaseEngine()

    def run_scenario(self, scenario: ScenarioCase) -> ScenarioRunResult:
        """Run one expanded scenario step by step and assert its flow-level expectation.

        Args:
            scenario: expanded corpus scenario.

        Returns:
            the per-step accounting of the run (the step expectations themselves have already been
            asserted with their exact per-step ``llmCalls`` counts).

        Raises:
            AssertionError: when a step fails (fail-fast: later steps do not run) or a flow
                expectation mismatches.
        """
        result = ScenarioRunResult(scenario=scenario)
        for step in scenario.steps:
            step_case = step.case_data
            prompt_override = self._resolve_prompt_override(scenario, step_case, result.step_prompt_texts)
            outcome = self._case_engine.run(step_case, prompt_override, True)
            message = outcome.message
            if message is not None:
                result.step_prompt_texts[step.step] = message.prompt_text or ""
                result.last_prompt_text = message.prompt_text
            filled = outcome.filled_params
            if filled is not None:
                result.step_filled_params[step.step] = dict(filled.data)
                result.step_missing_params[step.step] = _missing_params_of(filled)
            role = step.role if step.role is not None else "(no role)"
            result.role_call_counts[role] = result.role_call_counts.get(role, 0) + outcome.llm_calls
            if step_case.context is not None:
                result.max_round = max(result.max_round, step_case.context.round)
                result.max_rounds_limit = max(result.max_rounds_limit, step_case.context.max_rounds)
            if step_case.expect.params_from_step is not None:
                self._assert_params_from_step(scenario, step, result)
        self._assert_expect_flow(scenario, result)
        self._print_summary(scenario, result)
        return result

    # ------------------------------------------------------------------ fromStep resolution

    @staticmethod
    def _resolve_prompt_override(
        scenario: ScenarioCase,
        step_case: NegotiationCase,
        step_prompt_texts: dict[int, str],
    ) -> str | None:
        """Resolve the ``prompt.from_step`` reference of one step into the earlier step's text."""
        prompt = step_case.prompt
        if not isinstance(prompt, PromptSource.FromStep):
            return None
        prompt_text = step_prompt_texts.get(prompt.step)
        if prompt_text is None:
            raise AssertionError(
                f"{_scenario_prefix(scenario)} prompt.fromStep {prompt.step}: the referenced step "
                "produced no prompt text (unknown step number, a step that has not run yet, or a "
                "non-generation API)"
            )
        return prompt_text

    # ------------------------------------------------------------------ causal chain (Q21)

    @staticmethod
    def _assert_params_from_step(scenario: ScenarioCase, step: ScenarioStep, result: ScenarioRunResult) -> None:
        """Assert the step-level ``expect.params_from_step`` causal expectation.

        The referenced step must have discovered a non-empty missing-parameter set, and every one
        of those parameters must carry a value in this step's filled parameter data — the
        missing-parameter to filled-parameter link.
        """
        from_step = step.case_data.expect.params_from_step
        missing = result.step_missing_params.get(from_step)
        prefix = _step_prefix(scenario, step)
        if missing is None:
            raise AssertionError(
                f"{prefix} expect.paramsFromStep {from_step}: the referenced step produced no "
                "filled parameter data (unknown step number, a step that has not run yet, or a "
                "non-validation API)"
            )
        if not missing:
            raise AssertionError(
                f"{prefix} expect.paramsFromStep {from_step}: the referenced step found no missing "
                "parameters, so there is nothing this step could fill"
            )
        filled = result.step_filled_params.get(step.step)
        unfilled = [name for name in missing if filled is None or filled.get(name) is None]
        if unfilled:
            raise AssertionError(
                f"{prefix} expect.paramsFromStep {from_step}: the parameters extracted by this "
                f"step do not fill the missing parameters of step {from_step}; still missing: "
                f"{', '.join(unfilled)}"
            )

    # ------------------------------------------------------------------ flow-level expectation

    @staticmethod
    def _assert_expect_flow(scenario: ScenarioCase, result: ScenarioRunResult) -> None:
        """Assert the flow-level expectation of one scenario."""
        flow = scenario.expect_flow
        if flow is None:
            return
        if flow.terminal_condition is not None:
            condition = flow.terminal_condition
            if condition in ("accept", "reject", "abort"):
                literal = _TERMINAL_LITERALS[condition]
                if result.last_prompt_text is None:
                    _scenario_fail(
                        scenario,
                        "$.expectFlow.terminalCondition",
                        f"a generated message carrying the '{literal}' literal",
                        "no generation step succeeded",
                    )
                if literal not in (result.last_prompt_text or ""):
                    _scenario_fail(
                        scenario,
                        "$.expectFlow.terminalCondition",
                        f"a final message containing '{literal}'",
                        f"<{result.last_prompt_text}>",
                    )
            elif condition == "exhausted":
                if result.max_round != result.max_rounds_limit or result.max_rounds_limit == 0:
                    _scenario_fail(
                        scenario,
                        "$.expectFlow.terminalCondition",
                        f"the round limit reached (largest round equals maxRounds {result.max_rounds_limit})",
                        f"largest round {result.max_round}, maxRounds {result.max_rounds_limit}",
                    )
            else:
                _scenario_fail(
                    scenario,
                    "$.expectFlow.terminalCondition",
                    "accept, reject, abort or exhausted",
                    condition,
                )
        if flow.rounds_used is not None and flow.rounds_used != result.max_round:
            _scenario_fail(
                scenario,
                "$.expectFlow.roundsUsed",
                str(flow.rounds_used),
                str(result.max_round),
            )
        if flow.distinct_messages is True:
            messages = list(result.step_prompt_texts.values())
            distinct = set(messages)
            if len(distinct) != len(messages):
                _scenario_fail(
                    scenario,
                    "$.expectFlow.distinctMessages",
                    f"{len(messages)} pairwise distinct generated messages",
                    f"{len(distinct)} distinct message(s)",
                )
        if flow.missing_params_filled is not None:
            ScenarioEngine._assert_missing_params_filled(scenario, flow.missing_params_filled, result)

    @staticmethod
    def _assert_missing_params_filled(scenario: ScenarioCase, missing_step: int, result: ScenarioRunResult) -> None:
        """Assert the flow-level ``expect_flow.missing_params_filled`` causal expectation (Q21).

        Step ``missing_step`` must have discovered a non-empty missing-parameter set, and the union
        of the filled parameter data of the later steps — later steps winning on key conflicts —
        must carry a value for every one of them.
        """
        missing = result.step_missing_params.get(missing_step)
        if missing is None:
            _scenario_fail(
                scenario,
                "$.expectFlow.missingParamsFilled",
                f"a task-validation step {missing_step} carrying a missing-parameter set",
                f"step {missing_step} produced no filled parameter data",
            )
        if not missing:
            _scenario_fail(
                scenario,
                "$.expectFlow.missingParamsFilled",
                f"a non-empty missing-parameter set at step {missing_step}",
                f"step {missing_step} found no missing parameters",
            )
        filled_union: dict[str, object] = {}
        for step in sorted(result.step_filled_params):
            if step > missing_step:
                filled_union.update(result.step_filled_params[step])
        unfilled = [name for name in missing if filled_union.get(name) is None]
        if unfilled:
            _scenario_fail(
                scenario,
                "$.expectFlow.missingParamsFilled",
                f"the missing parameters of step {missing_step} ({', '.join(sorted(missing))}) "
                "filled by the later steps",
                f"still missing after step {missing_step}: {', '.join(unfilled)}",
            )

    # ------------------------------------------------------------------ role-semantic run summary

    @staticmethod
    def _print_summary(scenario: ScenarioCase, result: ScenarioRunResult) -> None:
        """Print the one-line flow summary of a successful scenario run (Java ``printSummary``)."""
        summary = f"[scenario] {scenario.id} completed {len(scenario.steps)} step(s)"
        roles = scenario.describe_roles()
        if roles:
            summary += f"; {roles}"
        for step in sorted(result.step_missing_params):
            missing = result.step_missing_params[step]
            if not missing:
                continue
            summary += f"; step-{step} missing params: {', '.join(sorted(missing))}"
            for later in sorted(result.step_filled_params):
                if later <= step:
                    continue
                filled_here = [
                    f"{name}={result.step_filled_params[later][name]}"
                    for name in sorted(missing)
                    if result.step_filled_params[later].get(name) is not None
                ]
                if filled_here:
                    summary += f" -> filled at step-{later}: {', '.join(filled_here)}"
        print(summary)


# --------------------------------------------------------------------------- module helpers


#: Terminal-condition literal of each terminal scenario flow expectation.
_TERMINAL_LITERALS: Final[dict[str, str]] = {"accept": "Accept", "reject": "Reject", "abort": "Abort"}


def _task_max_attempts(test_case: NegotiationCase) -> int:
    """Return the task API retry limit of one case: its LLM script's limit or the builder default."""
    if test_case.llm is not None and test_case.llm.max_attempts is not None:
        return test_case.llm.max_attempts
    return DEFAULT_TASK_MAX_ATTEMPTS


def _require_task_api(task_api: TaskApiAssembler | None, test_case: NegotiationCase) -> TaskApiAssembler:
    """Return the task API assembly of one task-family case."""
    if task_api is None:
        raise RuntimeError(
            f"{test_case.error_prefix()} the task API assembly is only wired for the task family "
            f"but got {test_case.api.json_name}"
        )
    return task_api


def _require_input_text(test_case: NegotiationCase) -> str:
    """Return the required text input of one task from-text case."""
    if test_case.input_text is None:
        raise RuntimeError(f"{test_case.error_prefix()} input.text: the task from-text API requires a text input")
    return test_case.input_text


def _require_input_data(test_case: NegotiationCase) -> dict[str, object]:
    """Return the required typed input data of one from-data case."""
    if test_case.input_data is None:
        raise RuntimeError(f"{test_case.error_prefix()} input.data: the from-data family requires typed input data")
    return test_case.input_data


def _require_schema(test_case: NegotiationCase) -> dict[str, object]:
    """Return the required JSON schema of one task-family case."""
    schema = _schema_of(test_case)
    if schema is None:
        raise RuntimeError(f"{test_case.error_prefix()} schema: the task APIs require a schema")
    return schema


def _data_of(test_case: NegotiationCase) -> dict[str, object]:
    """Return the structured input data of one task from-data case as a plain mapping."""
    data = test_case.input_data
    if data is None or not isinstance(data, dict):
        raise RuntimeError(f"{test_case.error_prefix()} input.data: the task from-data API requires typed input data")
    return data


def _schema_of(test_case: NegotiationCase) -> dict[str, object] | None:
    """Return the resolved JSON schema of one case, or ``None`` when the case carries none."""
    return test_case.schema


def _parse_template_uri(test_case: NegotiationCase) -> TemplateUri | None:
    """Parse the template URI of one case: ``None`` for the null-URI probes, fail-fast otherwise."""
    raw = test_case.template_uri
    if raw is None:
        return None
    parsed = TemplateUri.parse(raw)
    if parsed is None:
        raise ValueError(f"Unparseable template URI: {raw}")
    return parsed


def _require_typed_uri(template_uri: TemplateUri | None, test_case: NegotiationCase) -> TemplateUri:
    """Return the required typed template URI of one task-family case."""
    if template_uri is None:
        raise RuntimeError(f"{test_case.error_prefix()} templateUri: the task APIs require a template URI")
    return template_uri


def _prompt_of(test_case: NegotiationCase, prompt_override: str | None) -> str | None:
    """Resolve the prompt input of one validate-family case."""
    if prompt_override is not None:
        return prompt_override
    source = test_case.prompt
    if source is None:
        return None
    if isinstance(source, PromptSource.Text):
        return source.text
    if isinstance(source, PromptSource.Golden):
        return _read_golden_fixture(test_case, source.golden)
    raise RuntimeError(
        f"{test_case.error_prefix()} prompt.fromStep {source.step} is resolved by the "
        "ScenarioEngine; run the enclosing scenario through the ScenarioEngine"
    )


def _to_context(spec: ContextSpec | None, api: NegotiationApi) -> NegotiationContext | None:
    """Build the input context of a case, stamped with the performative of the case's API.

    The generation pipeline stamps the performative of the addressed template onto the emitted
    context, so an input context built with the API's performative echoes back unchanged; a
    validate API receives the message of exactly that performative, so its input context carries it
    as well. The task family produces no negotiation message, so it carries no context.
    """
    performative = _performative_of(api)
    if spec is None or performative is None:
        return None
    return NegotiationContext(spec.id, spec.round, spec.max_rounds, performative)


def _performative_of(api: NegotiationApi) -> NegotiationPerformative | None:
    """Return the performative an API addresses, or ``None`` for the task family."""
    return _PERFORMATIVE_OF.get(api)


def _performative_wording(fragment: str) -> str:
    """Rewrite the pre-rename wording of the frozen corpus fixtures.

    The case JSON files still say ``expected phase``, while the orchestrator's
    reference-resolution error has said ``expected performative`` since the rename. The corpus
    case JSON files are frozen by policy, so the harness translates the legacy fragment instead of
    the fixtures.
    """
    return fragment.replace("expected phase", "expected performative")


def _slot_errors_of(failure: BaseException) -> list[SlotValidationError] | None:
    """Return the slot errors the failure carries, or ``None`` when the failure type carries none.

    The negotiation content layer surfaces its failures as
    :class:`~a2a_t.core.errors.exceptions.NegotiationParamExtractionError` while the prompt families
    still use :class:`~a2a_t.core.errors.exceptions.A2ATParamExtractionError`; the engine accepts
    both carriers.
    """
    if isinstance(failure, A2ATParamExtractionError):
        return list(failure.errors)
    if isinstance(failure, NegotiationParamExtractionError):
        return list(failure.errors)
    return None


def _assert_slot_errors(
    test_case: NegotiationCase,
    expected: list[Expectation.SlotError],
    actual: list[SlotValidationError],
) -> None:
    """Assert the expected slot errors exactly but order-insensitively."""
    expected_counts: dict[str, int] = {}
    for slot_error in expected:
        key = f"{slot_error.slot}:{slot_error.code}"
        expected_counts[key] = expected_counts.get(key, 0) + 1
    actual_counts: dict[str, int] = {}
    actual_pairs: list[str] = []
    for error in actual:
        pair = f"{error.slot_name}:{error.code}"
        actual_counts[pair] = actual_counts.get(pair, 0) + 1
        actual_pairs.append(pair)
    if expected_counts != actual_counts:
        _fail(
            test_case,
            "$.expect.slotErrors",
            _render_slot_errors(expected),
            ", ".join(actual_pairs) if actual_pairs else "(none)",
        )


def _missing_params_of(filled: FilledParamData) -> set[str]:
    """Return the missing-parameter set of one validation outcome: the ``None``-valued names."""
    return {name for name, value in filled.data.items() if value is None}


def _read_golden_fixture(test_case: NegotiationCase, golden_name: str) -> str:
    """Read one committed golden fixture with CRLF→LF normalization (the Windows lesson 3bdacb2)."""
    fixture = GOLDEN_ROOT / test_case.language / f"{golden_name}.md"
    if not fixture.is_file():
        raise AssertionError(
            f"{test_case.error_prefix()} golden fixture '{golden_name}' does not exist on the test classpath: {fixture}"
        )
    try:
        return fixture.read_text(encoding="utf-8").replace("\r\n", "\n")
    except OSError as error:
        raise AssertionError(f"{test_case.error_prefix()} failed to read the golden fixture {fixture}") from error


def _normalize(text: str | None) -> str | None:
    """Normalize one text for byte comparison (CRLF→LF)."""
    return None if text is None else text.replace("\r\n", "\n")


def _require_message(outcome: CaseOutcome, json_path: str) -> MetadataContent:
    """Return the generated message of one successful generation run."""
    message = outcome.message
    if message is None:
        _fail(
            outcome.test_case,
            json_path,
            "a generated negotiation message",
            f"failure ({outcome.failure}) or {_type_name(outcome.value)}",
        )
    return message


def _type_name(value: object) -> str:
    """Return the short type name of one outcome value."""
    return "null" if value is None else type(value).__name__


def _render(values: dict[str, object]) -> str:
    """Render one parameter map for failure messages."""
    return "{" + ", ".join(f"{key!r}: {value!r}" for key, value in values.items()) + "}"


def _render_slot_errors(slot_errors: list[Expectation.SlotError]) -> str:
    """Render one expected slot-error list for failure messages."""
    return ", ".join(f"{error.slot}:{error.code}" for error in slot_errors)


def _truncate(text: str | None) -> str | None:
    """Truncate one text excerpt for failure messages."""
    if text is None:
        return None
    return text if len(text) <= _MAX_REPORTED_TEXT_LENGTH else text[:_MAX_REPORTED_TEXT_LENGTH] + "..."


def _quoted(text: str | None) -> str:
    """Quote one text excerpt for failure messages."""
    return "null" if text is None else f"<{text}>"


def _fail(test_case: NegotiationCase, json_path: str, expected: str, actual: str) -> None:
    """Raise the expectation mismatch carrying the case id, the JSON path and both sides."""
    raise AssertionError(f"{test_case.error_prefix()} {json_path}: expected {expected} but was {actual}")


def _scenario_prefix(scenario: ScenarioCase) -> str:
    """Return the failure-message prefix of one scenario."""
    return f"{scenario.source_file} [{scenario.id}]"


def _step_prefix(scenario: ScenarioCase, step: ScenarioStep) -> str:
    """Return the failure-message prefix of one scenario step, carrying the role semantics (Q23)."""
    return f"{scenario.source_file} [{scenario.id}#step-{step.step} ({scenario.describe_role(step.role)})]"


def _scenario_fail(scenario: ScenarioCase, json_path: str, expected: str, actual: str) -> None:
    """Raise the flow-expectation mismatch of one scenario."""
    raise AssertionError(f"{_scenario_prefix(scenario)} {json_path}: expected {expected} but was {actual}")
