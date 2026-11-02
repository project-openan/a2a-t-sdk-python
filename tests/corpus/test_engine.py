"""Direct unit tests of the corpus case engine against inline case objects.

Port of the Java ``CaseEngineTest``: the engine drives one hand-built case per suite kind — a happy
from-data case, a from-text case with a two-step retry script, a passing and a failing validate
case, and the task-family closed loop — against the **real production wiring** (builders, packaged
resources, renderers, vocabulary, rule gate, semantic validator) with zero network: the only
scripted seam is the LLM client. The red paths prove the engine is not a rubber stamp: every
flipped expectation fails with the case id and the JSON path of the expectation, the exact
``llmCalls`` counting fails when off by one, and the four P0 contracts fire on mutated
expectations.

The scripted payloads come from the shipped corpus shared responses through the session loader, so
the engine tests and the corpus suites assert through the same fixtures.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from a2a_t.core.metadata import NegotiationContext, NegotiationPerformative
from tests.corpus.assemblers import (
    INJECT_FAILING_SEMANTIC_VALIDATOR,
    INJECT_FAILING_TEMPLATE_LOADER,
)
from tests.corpus.conftest import SESSION_ID
from tests.corpus.engine import CaseEngine, CaseOutcome
from tests.corpus.models import (
    ContextSpec,
    Expectation,
    LlmFailMarker,
    LlmScript,
    LlmScriptStep,
    LoadedCorpus,
    NegotiationApi,
    NegotiationCase,
    PromptSource,
)

#: Fixed corpus session id (the golden session id of the closed loop).
ZH_CN = "zh-CN"

INFORMATION_PROPOSE_URI = "Negotiation-T/information-negotiation/propose/v1"

INFORMATION_ACCEPT_REJECT_URI = "Negotiation-T/information-negotiation/accept-reject/v1"

PRIVATE_LINE_COMPLAINT_URI = "Task-T/network-layer/private-line-complaint/v1"

#: The workbench raw complaint of the five-step closed loop (样例步骤1): deliberately lacking the
#: access port name and the complaint category — the causal starting point of the negotiation.
COMPLAINT_TEXT = (
    "深圳访问广州的专线从5月11号早上8点半开始响应时延从平均12ms骤升至320ms，访问广州机房的核心交易系统非常慢，"
    "柜面和手机银行的交易接口频繁报“连接超时”。OSS侧事件流水号：event-id-20260511-09013。"
)

#: Slot-extraction payload of the from-text success path: the task object carries what the raw
#: text names (the circuit) but no port name, so the rendered prompt stays portless.
TASK_SLOTS_OBJECT_PORTLESS = (
    '{"slots": {"任务对象": "深圳访问广州的专线", "任务上下文": "投诉分类：待补充；问题发生时间：2026-05-11T08:21:46Z；'
    'OSS侧事件流水号：event-id-20260511-09013；投诉详情：深圳访问广州的响应时延从平均12ms骤升至320ms"},'
    ' "slot_errors": []}'
)

#: Slot-extraction payload of the filled task from-data variant: the port name and the complaint
#: category are present.
TASK_SLOTS_FILLED = (
    '{"slots": {"任务对象": "接入端口名称：P533-珠江旧城-PTN3900-23-TPA1EG24-1", "任务上下文":'
    ' "投诉分类：专线质差；问题发生时间：2026-05-11T08:21:46Z；OSS侧事件流水号：'
    'event-id-20260511-09013；投诉详情：深圳访问广州的响应时延从平均12ms骤升至320ms"}, "slot_errors": []}'
)

#: Semantic-validation payload of the task validate step: the prompt is acceptable, but the access
#: port name and the complaint category are missing — the two null-valued parameters the loop then
#: negotiates for.
TASK_SEMANTIC_MISSING_PARAMS = (
    '{"semantic_verdict":true,"errors":[],"params":{"accessPort":null,"bizScenario":null,'
    '"faultTime":"2026-05-11T08:21:46Z","eventSerialNo":"event-id-20260511-09013"}}'
)

#: Semantic payload rejecting the task prompt (key slots missing).
TASK_SEMANTIC_REJECTED = (
    '{"semantic_verdict":false,"errors":[{"slot_name":"accessPort","code":"content.param_missing",'
    '"facts":{"section_label":"接入端口名称"}}],"params":{}}'
)

#: The task parameter schema of the closed loop (server-side keys, dictionary §10).
TASK_PARAM_SCHEMA = (
    '{"type":"object","properties":{"accessPort":{"type":"string"},"bizScenario":'
    '{"type":"string"},"faultTime":{"type":"string"},"eventSerialNo":'
    '{"type":"string"}},"required":["accessPort","bizScenario"]}'
)

#: The rendered task prompt the OMC receives (样例步骤1, shortened).
TASK_PROMPT_MISSING_PARAMS = (
    "## 任务类型(Task Type)\n"
    "传输专线业务投诉诊断\n"
    "\n"
    "## 任务对象(Task Object)\n"
    "接入端口名称：\n"
    "\n"
    "## 任务上下文(Task Context)\n"
    "1. 投诉分类：\n"
    '2. 问题发生时间： "2026-05-11T08:21:46Z"\n'
    '3. OSS侧事件流水号："event-id-20260511-09013"\n'
    '4. 投诉详情："深圳访问广州的响应时延从平均12ms骤升至320ms"\n'
)

#: The structured task input of the from-data-with-schema path.
TASK_INPUT_DATA = {
    "portName": "P533-珠江旧城-PTN3900-23-TPA1EG24-1",
    "complaintScenario": "专线质差",
    "faultStartTime": "2026-05-11T08:21:46Z",
    "ticketNo": "event-id-20260511-09013",
    "faultDetailText": "深圳访问广州的响应时延从平均12ms骤升至320ms",
}

#: The same content as the shared propose payload, disagreeing in one item value (differential red
#: path).
PROPOSE_DATA_DISAGREEING = (
    '{"items":[{"name":"接入端口名称","value":"举例：P781-珠江新城-PTN7900-23-TPA1EG24-17"},'
    '{"name":"投诉分类","value":"举例：专线质差"},{"name":"专线业务标识",'
    '"value":null}],"relationship":"OR"}'
)

#: The minimal JSON schema of the validate-family cases.
VALIDATE_SCHEMA = '{"type":"object","properties":{"accessPort":{"type":"string"}}}'


@pytest.fixture(scope="module")
def responses(loaded_corpus: LoadedCorpus) -> dict[str, str]:
    """The shared LLM response payloads of the shipped corpus."""
    return loaded_corpus.shared_responses


@pytest.fixture(scope="module")
def accept_payload(responses: dict[str, str]) -> str:
    """Extraction payload mapping to the typed content of the information accept golden fixture."""
    return responses["extract.information.accept.full"]


@pytest.fixture(scope="module")
def propose_payload(responses: dict[str, str]) -> str:
    """Extraction payload mapping to the typed content of the information propose golden fixture."""
    return responses["extract.information.propose.full"]


def expectation(**overrides: Any) -> Expectation:
    """Build one expectation block with the success defaults of the Java ``ok`` helper."""
    fields: dict[str, Any] = {
        "success": True,
        "exception": None,
        "code": None,
        "message_contains": [],
        "slot_errors": [],
        "llm_calls": None,
        "prompt_text_equals_golden": None,
        "metadata": None,
        "params": {},
        "contracts": [],
        "differential": False,
        "prompt_text_contains": [],
        "missing_params": None,
        "params_from_step": None,
    }
    fields.update(overrides)
    return Expectation(**fields)


def failed(**overrides: Any) -> Expectation:
    """Build one expectation block with the failure defaults of the Java ``failed`` helper."""
    return expectation(success=False, **overrides)


def negotiation_case(
    api: NegotiationApi,
    template_uri: str,
    expect: Expectation,
    *,
    id: str = "ENGINE/zh-CN",
    source_file: str = "inline/engine.json",
    input_text: str | None = None,
    input_data: dict[str, Any] | None = None,
    script: list[LlmScriptStep.Payload | LlmScriptStep.Fail] | None = None,
    context: ContextSpec | None = None,
    prompt: PromptSource.Golden | PromptSource.Text | PromptSource.FromStep | None = None,
    schema: dict[str, Any] | None = None,
    inject: str | None = None,
) -> NegotiationCase:
    """Build one inline corpus case of the negotiation content families."""
    return NegotiationCase(
        id=id,
        base_id=id.split("/")[0],
        source_file=source_file,
        api=api,
        language=ZH_CN,
        tags=[],
        expect=expect,
        priority="P0",
        summary=None,
        context=context if context is not None else ContextSpec(SESSION_ID, 2, 5),
        template_uri=template_uri,
        input_text=input_text,
        input_data=input_data,
        llm=LlmScript(script) if script is not None else None,
        prompt=prompt,
        schema=schema,
        inject=inject,
    )


def accept_from_text_case(
    expect: Expectation, input_data: dict[str, Any] | None, accept_payload: str
) -> NegotiationCase:
    """Build the from-text accept case of the golden happy path."""
    return negotiation_case(
        NegotiationApi.GENERATE_ACCEPT_FROM_TEXT,
        INFORMATION_ACCEPT_REJECT_URI,
        expect,
        id="FT-HAPPY-01/zh-CN",
        source_file="from-text/happy.json",
        input_text="我确认第一阶段的信息。",
        input_data=input_data,
        script=[LlmScriptStep.Payload(accept_payload)],
    )


def propose_from_text_case(
    expect: Expectation, input_data: dict[str, Any] | None, propose_payload: str
) -> NegotiationCase:
    """Build the from-text propose case of the golden happy path."""
    return negotiation_case(
        NegotiationApi.GENERATE_PROPOSE_FROM_TEXT,
        INFORMATION_PROPOSE_URI,
        expect,
        id="FT-HAPPY-02/zh-CN",
        source_file="from-text/happy.json",
        input_text="请补充接入端口名称（如P533-珠江旧城-PTN3900-23-TPA1EG24-1）、投诉分类（如专线质差）与专线业务标识信息。",
        input_data=input_data,
        script=[LlmScriptStep.Payload(propose_payload)],
    )


def validate_propose_case(expect: Expectation, semantic_payload: str) -> NegotiationCase:
    """Build the validate propose case over the golden propose prompt and the minimal schema."""
    return negotiation_case(
        NegotiationApi.VALIDATE_PROPOSE_PROMPT_AND_DATA_FILLING,
        INFORMATION_PROPOSE_URI,
        expect,
        id="VAL-HAPPY-01/zh-CN",
        source_file="validate/happy.json",
        script=[LlmScriptStep.Payload(semantic_payload)],
        prompt=PromptSource.Golden("information_propose"),
        schema=json.loads(VALIDATE_SCHEMA),
    )


def task_case(
    api: NegotiationApi,
    expect: Expectation,
    payload: str,
    *,
    input_text: str | None = None,
    input_data: dict[str, Any] | None = None,
    prompt: PromptSource.Golden | PromptSource.Text | None = None,
    schema: dict[str, Any] | None = None,
) -> NegotiationCase:
    """Build one task-family case of the closed loop."""
    return negotiation_case(
        api,
        PRIVATE_LINE_COMPLAINT_URI,
        expect,
        id="TASK-CASE/zh-CN",
        source_file="task/inline.json",
        input_text=input_text,
        input_data=input_data,
        script=[LlmScriptStep.Payload(payload)],
        context=None,
        prompt=prompt,
        schema=schema,
    )


# --------------------------------------------------------------------------- green paths per suite


class TestFromTextSuite:
    """The from-text family: one LLM extraction call driving the golden rendering."""

    def test_runs_a_successful_from_text_case_against_the_golden_fixture(self, accept_payload: str) -> None:
        test_case = accept_from_text_case(
            expectation(
                llm_calls=1,
                prompt_text_equals_golden="information_accept",
                metadata=Expectation.Metadata(INFORMATION_ACCEPT_REJECT_URI, True),
            ),
            None,
            accept_payload,
        )

        outcome = CaseEngine().run(test_case)

        message = outcome.message
        assert message is not None
        assert outcome.llm_calls == 1
        assert message.template_uri == INFORMATION_ACCEPT_REJECT_URI
        assert message.negotiation_context == NegotiationContext(SESSION_ID, 2, 5, NegotiationPerformative.ACCEPT)

    def test_from_text_with_a_two_step_retry_script_recovers_and_counts_both_calls(self, accept_payload: str) -> None:
        """The retryable unparseable answer is retried once; the exact count is two."""
        test_case = negotiation_case(
            NegotiationApi.GENERATE_ACCEPT_FROM_TEXT,
            INFORMATION_ACCEPT_REJECT_URI,
            expectation(
                llm_calls=2,
                prompt_text_equals_golden="information_accept",
                metadata=Expectation.Metadata(INFORMATION_ACCEPT_REJECT_URI, True),
            ),
            id="FT-HAPPY-01/zh-CN",
            source_file="from-text/happy.json",
            input_text="我确认第一阶段的信息。",
            script=[LlmScriptStep.Fail(LlmFailMarker.NON_JSON), LlmScriptStep.Payload(accept_payload)],
        )

        outcome = CaseEngine().run(test_case)

        assert outcome.message is not None
        assert outcome.llm_calls == 2


class TestFromDataSuite:
    """The from-data family: the deterministic pipeline makes zero LLM calls."""

    def test_runs_from_data_cases_deterministically_without_any_llm_call(self, accept_payload: str) -> None:
        test_case = negotiation_case(
            NegotiationApi.GENERATE_ACCEPT_FROM_DATA,
            INFORMATION_ACCEPT_REJECT_URI,
            expectation(
                llm_calls=0,
                prompt_text_equals_golden="information_accept",
                metadata=Expectation.Metadata(INFORMATION_ACCEPT_REJECT_URI, True),
            ),
            id="FD-HAPPY-01/zh-CN",
            source_file="from-data/happy.json",
            input_data=json.loads(accept_payload),
        )

        outcome = CaseEngine().run(test_case)

        assert outcome.message is not None
        assert outcome.llm_calls == 0, "the from-data run must not call the LLM (assertion-only client)"


class TestValidateSuite:
    """The validate family: one semantic call merging context keys into the parameter data."""

    def test_validate_pass_merges_the_context_keys_into_the_extracted_params(self, responses: dict[str, str]) -> None:
        test_case = validate_propose_case(
            expectation(
                llm_calls=1,
                params={
                    "id": SESSION_ID,
                    "round": 2,
                    "maxRounds": 5,
                    "accessPort": "P533-珠江旧城-PTN3900-23-TPA1EG24-1",
                    "bizScenario": "专线质差",
                },
                contracts=["contextKeysInMergedParams"],
            ),
            responses["semantic.information.accept.full"],
        )

        outcome = CaseEngine().run(test_case)

        filled = outcome.filled_params
        assert filled is not None
        assert outcome.llm_calls == 1
        assert filled.data["accessPort"] == "P533-珠江旧城-PTN3900-23-TPA1EG24-1"

    def test_validate_fail_asserts_the_semantic_rejection_code_and_slot_errors(self, responses: dict[str, str]) -> None:
        test_case = validate_propose_case(
            failed(
                exception="NegotiationParamExtractionException",
                code="negotiation.semantic_rejected",
                llm_calls=1,
                slot_errors=[Expectation.SlotError("accessPort", "negotiation.field_missing")],
            ),
            responses["semantic.information.reject"],
        )

        outcome = CaseEngine().run(test_case)

        assert outcome.llm_calls == 1
        assert type(outcome.failure).__name__ == "NegotiationParamExtractionError"

    def test_slot_errors_are_compared_order_insensitively(self) -> None:
        """Two slot errors asserted in reversed order still pass: the comparison is a bag."""
        payload = json.dumps(
            {
                "semantic_verdict": False,
                "negotiation_type": "information",
                "errors": [
                    {
                        "slot_name": "accessPort",
                        "code": "negotiation.field_missing",
                        "facts": {"field": "接入端口名称"},
                    },
                    {
                        "slot_name": "bizScenario",
                        "code": "negotiation.field_missing",
                        "facts": {"field": "投诉分类"},
                    },
                ],
                "params": {},
            },
            ensure_ascii=False,
        )
        test_case = negotiation_case(
            NegotiationApi.VALIDATE_PROPOSE_PROMPT_AND_DATA_FILLING,
            INFORMATION_PROPOSE_URI,
            failed(
                exception="NegotiationParamExtractionException",
                code="negotiation.semantic_rejected",
                llm_calls=1,
                slot_errors=[
                    Expectation.SlotError("bizScenario", "negotiation.field_missing"),
                    Expectation.SlotError("accessPort", "negotiation.field_missing"),
                ],
            ),
            script=[LlmScriptStep.Payload(payload)],
            prompt=PromptSource.Golden("information_propose"),
            schema=json.loads(VALIDATE_SCHEMA),
        )

        CaseEngine().run(test_case)

    def test_slot_errors_fail_on_an_extra_expected_pair(self, responses: dict[str, str]) -> None:
        test_case = validate_propose_case(
            failed(
                exception="NegotiationParamExtractionException",
                code="negotiation.semantic_rejected",
                llm_calls=1,
                slot_errors=[
                    Expectation.SlotError("accessPort", "negotiation.field_missing"),
                    Expectation.SlotError("round", "negotiation.round_exceeded"),
                ],
            ),
            responses["semantic.information.reject"],
        )

        with pytest.raises(AssertionError, match=r"\$\.expect\.slotErrors"):
            CaseEngine().run(test_case)


# --------------------------------------------------------------------------- exact llmCalls counting


class TestLlmCallCounting:
    """The ``llmCalls`` expectation is the only calibration point: off-by-one fails."""

    @pytest.mark.parametrize("delta", [1, -1], ids=["over-by-one", "under-by-one"])
    def test_a_count_off_by_one_fails(self, accept_payload: str, delta: int) -> None:
        test_case = accept_from_text_case(expectation(llm_calls=1 + delta), None, accept_payload)

        with pytest.raises(AssertionError, match=r"\$\.expect\.llmCalls"):
            CaseEngine().run(test_case)

    def test_the_failure_message_carries_both_counts(self, accept_payload: str) -> None:
        test_case = accept_from_text_case(expectation(llm_calls=2), None, accept_payload)

        with pytest.raises(AssertionError, match=r"expected 2 but was 1"):
            CaseEngine().run(test_case)

    def test_a_run_without_an_llm_expectation_passes_any_count(self, accept_payload: str) -> None:
        test_case = accept_from_text_case(expectation(), None, accept_payload)

        outcome = CaseEngine().run(test_case)

        assert outcome.llm_calls == 1


# --------------------------------------------------------------------------- P0 contracts


class TestContracts:
    """The four P0 contracts, and the loud failures of unknown or unlit names."""

    def test_conclusion_literal_present_contract_accepts_a_terminal_api(self, accept_payload: str) -> None:
        test_case = accept_from_text_case(
            expectation(llm_calls=1, contracts=["conclusionLiteralPresent"]), None, accept_payload
        )

        CaseEngine().run(test_case)

    def test_conclusion_literal_present_contract_rejects_non_terminal_apis(self, propose_payload: str) -> None:
        test_case = propose_from_text_case(
            expectation(llm_calls=1, contracts=["conclusionLiteralPresent"]), None, propose_payload
        )

        with pytest.raises(AssertionError) as failure:
            CaseEngine().run(test_case)

        message = str(failure.value)
        assert "conclusionLiteralPresent" in message and "a terminal or abort generation API" in message

    def test_context_keys_in_merged_params_contract(self, responses: dict[str, str]) -> None:
        test_case = validate_propose_case(
            expectation(
                llm_calls=1,
                params={
                    "id": SESSION_ID,
                    "round": 2,
                    "maxRounds": 5,
                    "accessPort": "P533-珠江旧城-PTN3900-23-TPA1EG24-1",
                    "bizScenario": "专线质差",
                },
                contracts=["contextKeysInMergedParams"],
            ),
            responses["semantic.information.accept.full"],
        )

        CaseEngine().run(test_case)

    def test_no_llm_leak_in_user_message_contract(self) -> None:
        test_case = negotiation_case(
            NegotiationApi.GENERATE_ACCEPT_FROM_TEXT,
            INFORMATION_ACCEPT_REJECT_URI,
            failed(
                exception="NegotiationGenerationException",
                code="llm.response_invalid",
                llm_calls=3,
                contracts=["noLlmLeakInUserMessage"],
            ),
            id="FT-RETRY-01/zh-CN",
            source_file="from-text/retry.json",
            input_text="请接受。",
            script=[
                LlmScriptStep.Fail(LlmFailMarker.NON_JSON),
                LlmScriptStep.Fail(LlmFailMarker.NON_JSON),
                LlmScriptStep.Fail(LlmFailMarker.NON_JSON),
            ],
        )

        CaseEngine().run(test_case)

    def test_metadata_triple_shape_contract(self, accept_payload: str) -> None:
        test_case = accept_from_text_case(
            expectation(llm_calls=1, contracts=["metadataTripleShape"]), None, accept_payload
        )

        outcome = CaseEngine().run(test_case)

        assert len(outcome.message.build_metadata_content()) == 3

    def test_rejects_unknown_and_not_yet_lit_contract_names(self, accept_payload: str) -> None:
        unknown = accept_from_text_case(expectation(llm_calls=1, contracts=["not-a-contract"]), None, accept_payload)
        with pytest.raises(AssertionError, match="a registered contract name"):
            CaseEngine().run(unknown)

        not_yet_lit = accept_from_text_case(
            expectation(llm_calls=1, contracts=["noRenderSlotLeak"]), None, accept_payload
        )
        with pytest.raises(AssertionError, match="not yet lit"):
            CaseEngine().run(not_yet_lit)

    def test_context_keys_contract_fires_on_a_non_validate_outcome(self, accept_payload: str) -> None:
        test_case = accept_from_text_case(
            expectation(llm_calls=1, contracts=["contextKeysInMergedParams"]), None, accept_payload
        )

        with pytest.raises(AssertionError, match=r"contextKeysInMergedParams"):
            CaseEngine().run(test_case)


# --------------------------------------------------------------------------- differential (Q17 C+)


class TestDifferential:
    """The from-text == from-data == golden double run with a zero-call from-data leg."""

    def test_runs_the_differential_double_run(self, propose_payload: str) -> None:
        test_case = propose_from_text_case(
            expectation(
                llm_calls=1,
                prompt_text_equals_golden="information_propose",
                metadata=Expectation.Metadata(INFORMATION_PROPOSE_URI, True),
                differential=True,
            ),
            json.loads(propose_payload),
            propose_payload,
        )

        outcome = CaseEngine().run(test_case)

        assert outcome.message is not None
        assert outcome.llm_calls == 1, "only the from-text leg may call the LLM"

    def test_differential_fails_when_the_typed_data_disagrees_with_the_text(self, propose_payload: str) -> None:
        test_case = propose_from_text_case(
            expectation(
                llm_calls=1,
                prompt_text_equals_golden="information_propose",
                metadata=Expectation.Metadata(INFORMATION_PROPOSE_URI, True),
                differential=True,
            ),
            json.loads(PROPOSE_DATA_DISAGREEING),
            propose_payload,
        )

        with pytest.raises(AssertionError) as failure:
            CaseEngine().run(test_case)

        message = str(failure.value)
        assert "$.expect.differential" in message and "fromText == fromData" in message

    def test_differential_requires_input_data(self, propose_payload: str) -> None:
        test_case = propose_from_text_case(
            expectation(differential=True, prompt_text_equals_golden="information_propose"),
            None,
            propose_payload,
        )

        with pytest.raises(AssertionError, match=r"\$\.expect\.differential"):
            CaseEngine().run(test_case)


# --------------------------------------------------------------------------- inject hooks


class TestInjectHooks:
    """The two harness injection hooks wire onto real builder seams."""

    def test_injects_the_failing_template_loader_for_the_template_not_found_matrix(self, accept_payload: str) -> None:
        test_case = negotiation_case(
            NegotiationApi.GENERATE_ACCEPT_FROM_TEXT,
            INFORMATION_ACCEPT_REJECT_URI,
            failed(exception="NegotiationGenerationException", code="template.not_found", llm_calls=0),
            id="FT-TPL-01/zh-CN",
            source_file="from-text/template-resolution.json",
            input_text="请接受。",
            script=[LlmScriptStep.Payload(accept_payload)],
            inject=INJECT_FAILING_TEMPLATE_LOADER,
        )

        CaseEngine().run(test_case)

    def test_injects_the_failing_semantic_validator_for_the_validate_template_not_found_mapping(self) -> None:
        test_case = negotiation_case(
            NegotiationApi.VALIDATE_PROPOSE_PROMPT_AND_DATA_FILLING,
            INFORMATION_PROPOSE_URI,
            failed(exception="NegotiationParamExtractionException", code="template.not_found", llm_calls=0),
            id="VAL-MAP-05/zh-CN",
            source_file="validate/error-code-mapping.json",
            script=[LlmScriptStep.Fail(LlmFailMarker.ASSERTION)],
            prompt=PromptSource.Golden("information_propose"),
            schema=json.loads(VALIDATE_SCHEMA),
            inject=INJECT_FAILING_SEMANTIC_VALIDATOR,
        )

        CaseEngine().run(test_case)


# --------------------------------------------------------------------------- red paths (no rubber stamp)


class TestRedPaths:
    """Every flipped expectation fails with the case id and the JSON path."""

    def test_fails_with_case_id_and_json_path_when_the_expected_code_mismatches(self) -> None:
        test_case = negotiation_case(
            NegotiationApi.GENERATE_ACCEPT_FROM_TEXT,
            INFORMATION_ACCEPT_REJECT_URI,
            failed(code="negotiation.invalid_input", llm_calls=3),
            id="FT-EXTRACT-01/zh-CN",
            source_file="from-text/extraction-failures.json",
            input_text="请接受。",
            script=[
                LlmScriptStep.Fail(LlmFailMarker.NON_JSON),
                LlmScriptStep.Fail(LlmFailMarker.NON_JSON),
                LlmScriptStep.Fail(LlmFailMarker.NON_JSON),
            ],
        )

        with pytest.raises(AssertionError) as failure:
            CaseEngine().run(test_case)

        message = str(failure.value)
        assert "FT-EXTRACT-01/zh-CN" in message
        assert "$.expect.code" in message
        assert "llm.response_invalid" in message

    def test_fails_with_case_id_and_json_path_when_the_golden_name_mismatches(self, accept_payload: str) -> None:
        test_case = accept_from_text_case(
            expectation(llm_calls=1, prompt_text_equals_golden="information_reject"), None, accept_payload
        )

        with pytest.raises(AssertionError, match=r"\$\.expect\.promptTextEqualsGolden"):
            CaseEngine().run(test_case)

    def test_fails_when_a_success_case_actually_fails(self) -> None:
        test_case = negotiation_case(
            NegotiationApi.GENERATE_ACCEPT_FROM_TEXT,
            INFORMATION_ACCEPT_REJECT_URI,
            expectation(llm_calls=3),
            id="FT-HAPPY-99/zh-CN",
            source_file="from-text/happy.json",
            input_text="请接受。",
            script=[LlmScriptStep.Fail(LlmFailMarker.NON_JSON)],
        )

        with pytest.raises(AssertionError, match=r"\$\.expect\.outcome"):
            CaseEngine().run(test_case)

    def test_fails_when_a_failure_case_actually_succeeds(self, accept_payload: str) -> None:
        test_case = accept_from_text_case(failed(code="llm.response_invalid", llm_calls=1), None, accept_payload)

        with pytest.raises(AssertionError, match=r"\$\.expect\.outcome"):
            CaseEngine().run(test_case)


# --------------------------------------------------------------------------- task family (Q21)


class TestTaskFamily:
    """The closed-loop task APIs run through the real facade builders' wiring."""

    def test_runs_a_task_from_text_case_through_the_mirrored_client_wiring(self) -> None:
        test_case = task_case(
            NegotiationApi.GENERATE_TASK_PROMPT_FROM_TEXT,
            expectation(
                llm_calls=1,
                prompt_text_contains=["## 任务类型", "## 任务对象", "## 任务上下文", "event-id-20260511-09013"],
            ),
            TASK_SLOTS_OBJECT_PORTLESS,
            input_text=COMPLAINT_TEXT,
        )

        outcome = CaseEngine().run(test_case)

        message = outcome.message
        assert message is not None
        assert outcome.llm_calls == 1, "the task from-text pipeline makes exactly one slot-extraction call"
        assert message.template_uri == PRIVATE_LINE_COMPLAINT_URI
        assert "## 任务对象" in (message.prompt_text or "")
        assert "P533" not in (message.prompt_text or ""), "no port name may leak into the incomplete task prompt"

    def test_runs_a_task_from_data_with_schema_case_through_the_mirrored_client_wiring(self) -> None:
        test_case = task_case(
            NegotiationApi.GENERATE_TASK_PROMPT_FROM_DATA_WITH_SCHEMA,
            expectation(
                llm_calls=1,
                prompt_text_contains=["P533-珠江旧城-PTN3900-23-TPA1EG24-1", "专线质差"],
            ),
            TASK_SLOTS_FILLED,
            input_data=TASK_INPUT_DATA,
            schema={"type": "object", "properties": {key: {"type": "string"} for key in TASK_INPUT_DATA}},
        )

        outcome = CaseEngine().run(test_case)

        message = outcome.message
        assert message is not None
        assert outcome.llm_calls == 1, "the from-data-with-schema pipeline also extracts slots through the LLM"
        assert message.template_uri == PRIVATE_LINE_COMPLAINT_URI

    def test_validate_task_prompt_reports_the_missing_parameters_as_null_valued_params(self) -> None:
        test_case = task_case(
            NegotiationApi.VALIDATE_TASK_PROMPT_AND_DATA_FILLING,
            expectation(
                llm_calls=1,
                missing_params=["accessPort", "bizScenario"],
                params={"faultTime": "2026-05-11T08:21:46Z", "eventSerialNo": "event-id-20260511-09013"},
            ),
            TASK_SEMANTIC_MISSING_PARAMS,
            prompt=PromptSource.Text(TASK_PROMPT_MISSING_PARAMS),
            schema=json.loads(TASK_PARAM_SCHEMA),
        )

        outcome = CaseEngine().run(test_case)

        filled = outcome.filled_params
        assert filled is not None
        assert outcome.llm_calls == 1, "the task validation pipeline makes exactly one semantic call"
        assert filled.data["faultTime"] == "2026-05-11T08:21:46Z"
        assert filled.data["eventSerialNo"] == "event-id-20260511-09013"
        assert filled.data["accessPort"] is None, "a missing parameter must surface as a null-valued entry"

    def test_task_validate_missing_params_flip_fails(self) -> None:
        test_case = task_case(
            NegotiationApi.VALIDATE_TASK_PROMPT_AND_DATA_FILLING,
            expectation(llm_calls=1, missing_params=["accessPort", "bizScenario", "faultDetail"], params={}),
            TASK_SEMANTIC_MISSING_PARAMS,
            prompt=PromptSource.Text(TASK_PROMPT_MISSING_PARAMS),
            schema=json.loads(TASK_PARAM_SCHEMA),
        )

        with pytest.raises(AssertionError) as failure:
            CaseEngine().run(test_case)

        message = str(failure.value)
        assert "$.expect.missingParams" in message and "faultDetail" in message

    def test_task_validate_semantic_rejection_carries_the_validation_code(self) -> None:
        test_case = task_case(
            NegotiationApi.VALIDATE_TASK_PROMPT_AND_DATA_FILLING,
            failed(exception="ContentValidationException", code="negotiation.semantic_rejected", llm_calls=1),
            TASK_SEMANTIC_REJECTED,
            prompt=PromptSource.Text(TASK_PROMPT_MISSING_PARAMS),
            schema=json.loads(TASK_PARAM_SCHEMA),
        )

        CaseEngine().run(test_case)

    def test_task_prompt_text_contains_fails_on_an_absent_fragment(self) -> None:
        test_case = task_case(
            NegotiationApi.GENERATE_TASK_PROMPT_FROM_TEXT,
            expectation(llm_calls=1, prompt_text_contains=["## 不存在的节头"]),
            TASK_SLOTS_OBJECT_PORTLESS,
            input_text=COMPLAINT_TEXT,
        )

        with pytest.raises(AssertionError) as failure:
            CaseEngine().run(test_case)

        message = str(failure.value)
        assert "$.expect.promptTextContains" in message and "不存在的节头" in message

    def test_params_from_step_is_reserved_for_the_scenario_engine(self) -> None:
        test_case = task_case(
            NegotiationApi.VALIDATE_TASK_PROMPT_AND_DATA_FILLING,
            expectation(llm_calls=1, params_from_step=1),
            TASK_SEMANTIC_MISSING_PARAMS,
            prompt=PromptSource.Text(TASK_PROMPT_MISSING_PARAMS),
            schema=json.loads(TASK_PARAM_SCHEMA),
        )

        with pytest.raises(RuntimeError, match="paramsFromStep 1"):
            CaseEngine().run(test_case)


# --------------------------------------------------------------------------- outcome record


class TestCaseOutcome:
    """The normalized outcome record exposes its two success shapes."""

    def test_the_outcome_distinguishes_messages_from_filled_params(self, responses: dict[str, str]) -> None:
        validate_case = validate_propose_case(
            expectation(
                llm_calls=1,
                params={
                    "id": SESSION_ID,
                    "round": 2,
                    "maxRounds": 5,
                    "accessPort": "P533-珠江旧城-PTN3900-23-TPA1EG24-1",
                    "bizScenario": "专线质差",
                },
            ),
            responses["semantic.information.accept.full"],
        )

        outcome: CaseOutcome = CaseEngine().run(validate_case)

        assert outcome.message is None
        assert outcome.filled_params is not None
