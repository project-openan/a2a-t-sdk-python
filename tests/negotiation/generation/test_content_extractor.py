"""Tests of the from-text LLM content extraction leg of the negotiation generation pipeline.

Port of Java ``NegotiationContentExtractorTest`` plus the retry-policy slice of
``FromTextLlmPipelineTest``. The LLM is replaced by a scripted client that returns queued payloads
or raises queued failures and repeats the last entry once the queue is exhausted; every other
collaborator is the production wiring (the real packaged extraction prompts and the real schema
builder), so the suite stays offline and deterministic.

The retry semantics are pinned by exact call counts (port plan 7.3): a retryable failure code is
retried until it succeeds, a non-retryable code stops after exactly one call, and the exhaustion
failure re-raises the original code with its original facts.
"""

from __future__ import annotations

from typing import Any

import pytest

from a2a_t.config.models import LLM_MAX_ATTEMPTS_KEY, LlmRuntimeConfig
from a2a_t.core.errors.catalog import ErrorCatalog
from a2a_t.core.errors.exceptions import A2ATError, NegotiationGenerationError, ResourceNotFoundError
from a2a_t.core.errors.input_limit import DEFAULT_MAX_TEXT_CHARS, InputLimitConfig
from a2a_t.core.metadata import NegotiationPerformative
from a2a_t.llm.models import LLMResponse
from a2a_t.negotiation.content.enums import NegotiationAction, NegotiationConclusion, NegotiationType
from a2a_t.negotiation.content.models import (
    FeasibilityProposeContent,
    InformationEndingContent,
    InformationProposeContent,
    NegotiationAbortContent,
    NegotiationItem,
    TargetEndingContent,
    TargetProposeContent,
)
from a2a_t.negotiation.generation.content_extractor import DefaultNegotiationContentExtractor
from a2a_t.negotiation.generation.json_schema_builder import build_extraction_schema
from a2a_t.negotiation.resources.reference import NegotiationReference

ZH_CN = "zh-CN"
EN_US = "en-US"


class ScriptedExtractionClient:
    """Scripted LLM client: returns the queued payloads in order, raises the queued failures.

    Once the queue is exhausted the last entry repeats, mirroring the Java
    ``ScriptedExtractionClient``. Every call records the request for later assertions.
    """

    def __init__(self, *entries: str | BaseException) -> None:
        self.script: list[str | BaseException] = list(entries)
        self.calls = 0
        self.last_messages: list[dict[str, str]] = []
        self.last_schema: dict[str, Any] = {}

    def structured(
        self,
        *,
        messages: list[dict[str, str]],
        json_schema: dict[str, Any],
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> LLMResponse:
        entry = self.script[min(self.calls, len(self.script) - 1)]
        self.calls += 1
        self.last_messages = messages
        self.last_schema = json_schema
        if isinstance(entry, BaseException):
            raise entry
        return LLMResponse(
            content=entry,
            model="scripted-model",
            usage={"prompt_tokens": 1, "completion_tokens": 1},
            metadata={},
        )


def reference(
    negotiation_type: NegotiationType | None, performative: NegotiationPerformative, language: str = ZH_CN
) -> NegotiationReference:
    """Build the reference of one extraction under test."""
    return NegotiationReference(negotiation_type, performative, language)


def extractor(llm: ScriptedExtractionClient | None, *, max_attempts: int = 3) -> DefaultNegotiationContentExtractor:
    """Build the extractor under test with the scripted client and the default attempt limit."""
    return DefaultNegotiationContentExtractor(llm, max_attempts=max_attempts)


def error_code(error: NegotiationGenerationError) -> str:
    """Return the plain string code of one negotiation generation failure."""
    return error.code_str


# --------------------------------------------------------------------------------------
# happy paths: every negotiation type and every from-text performative
# --------------------------------------------------------------------------------------


def test_extracts_information_propose_content() -> None:
    llm = ScriptedExtractionClient(
        '{"items":[{"name":"故障发生时间","value":"精确到分钟的时间点"},{"name":"受影响小区标识","value":null}],'
        '"relationship":"故障发生时间与受影响小区标识需逐小区对应"}'
    )

    content = extractor(llm).extract(
        "请提供故障发生时间与受影响小区标识。", reference(NegotiationType.INFORMATION, NegotiationPerformative.PROPOSE)
    )

    propose_content = isinstance(content, InformationProposeContent) and content
    assert propose_content is not False
    assert propose_content.items == [
        NegotiationItem("故障发生时间", "精确到分钟的时间点"),
        NegotiationItem("受影响小区标识", None),
    ]
    assert propose_content.relationship == "故障发生时间与受影响小区标识需逐小区对应"

    assert llm.calls == 1
    assert len(llm.last_messages) == 2
    assert llm.last_messages[0]["role"] == "system"
    assert llm.last_messages[1]["role"] == "user"
    assert "协商阶段：propose" in llm.last_messages[1]["content"]
    assert "请提供故障发生时间与受影响小区标识。" in llm.last_messages[1]["content"]
    assert llm.last_schema == build_extraction_schema(NegotiationType.INFORMATION, NegotiationPerformative.PROPOSE)


def test_extracts_target_propose_content() -> None:
    content = extractor(
        ScriptedExtractionClient(
            '{"target_negotiation_description":"请求将节能目标由30%调整为20%。",'
            '"intent_understanding":[{"name":"发起方理解","value":"对方希望降低节能力度"}],'
            '"alignment_and_clarification":null,'
            '"request_for_clarification":[{"name":"速率保障下限","value":null}]}'
        )
    ).extract("请求调整节能目标。", reference(NegotiationType.TARGET, NegotiationPerformative.PROPOSE, EN_US))

    assert isinstance(content, TargetProposeContent)
    assert content.target_negotiation_description == "请求将节能目标由30%调整为20%。"
    assert content.intent_understanding == [NegotiationItem("发起方理解", "对方希望降低节能力度")]
    assert content.alignment_and_clarification is None
    assert content.request_for_clarification == [NegotiationItem("速率保障下限", None)]


@pytest.mark.parametrize(
    ("payload", "expected_action", "expected_items_field"),
    [
        (
            '{"feasibility_negotiation_description":"请评估该节能目标能否达成。","action":"REQUEST_FEASIBILITY_EVALUATION",'
            '"contents_to_evaluate":[{"name":"评估对象","value":"停电8小时期间的速率保障"}],'
            '"infeasibility_details_and_proposal":null}',
            NegotiationAction.REQUEST_FEASIBILITY_EVALUATION,
            "contents_to_evaluate",
        ),
        (
            '{"feasibility_negotiation_description":"目标不可行，提出下调方案。","action":"PROPOSE_ALTERNATIVE_ON_FAILURE",'
            '"contents_to_evaluate":null,'
            '"infeasibility_details_and_proposal":[{"name":"替代提案","value":"下调至2Mbps"}]}',
            NegotiationAction.PROPOSE_ALTERNATIVE_ON_FAILURE,
            "infeasibility_details_and_proposal",
        ),
    ],
)
def test_extracts_feasibility_propose_content_for_both_actions(
    payload: str, expected_action: NegotiationAction, expected_items_field: str
) -> None:
    content = extractor(ScriptedExtractionClient(payload)).extract(
        "请评估。", reference(NegotiationType.FEASIBILITY, NegotiationPerformative.PROPOSE)
    )

    assert isinstance(content, FeasibilityProposeContent)
    assert content.action == expected_action
    items = getattr(content, expected_items_field)
    assert items is not None and len(items) == 1


def test_extracts_target_propose_confirm_request_content() -> None:
    content = extractor(
        ScriptedExtractionClient(
            '{"target_negotiation_description":"任务目标澄清完成，请答复<目标澄清后的确认请求>。",'
            '"intent_understanding":null,"alignment_and_clarification":null,"request_for_clarification":null,'
            '"target_confirm_request":"目标已经澄清，是否同意按照此目标继续执行？"}'
        )
    ).extract("目标已经澄清，请确认。", reference(NegotiationType.TARGET, NegotiationPerformative.PROPOSE))

    assert isinstance(content, TargetProposeContent)
    assert content.target_negotiation_description == "任务目标澄清完成，请答复<目标澄清后的确认请求>。"
    assert content.target_confirm_request == "目标已经澄清，是否同意按照此目标继续执行？"
    assert content.intent_understanding is None
    assert content.alignment_and_clarification is None
    assert content.request_for_clarification is None


def test_extracts_feasibility_propose_confirm_request_content() -> None:
    content = extractor(
        ScriptedExtractionClient(
            '{"feasibility_negotiation_description":"针对调整后的速率保障目标，可行性评估已完成，结论为可行，请答复<评估可行时的确认请求>。",'
            '"action":"REQUEST_FEASIBILITY_EVALUATION","contents_to_evaluate":null,'
            '"infeasibility_details_and_proposal":null,'
            '"feasibility_confirm_request":"评估目标可行，是否同意按照此目标继续执行？"}'
        )
    ).extract("评估目标可行，请确认。", reference(NegotiationType.FEASIBILITY, NegotiationPerformative.PROPOSE))

    assert isinstance(content, FeasibilityProposeContent)
    assert content.action == NegotiationAction.REQUEST_FEASIBILITY_EVALUATION
    assert content.feasibility_confirm_request == "评估目标可行，是否同意按照此目标继续执行？"
    assert content.contents_to_evaluate is None
    assert content.infeasibility_details_and_proposal is None


@pytest.mark.parametrize(
    ("payload", "expected_target_confirm", "expected_feasibility_confirm"),
    [
        (
            '{"target_negotiation_description":"描述","intent_understanding":null,'
            '"alignment_and_clarification":null,"request_for_clarification":null,'
            '"target_confirm_request":"请确认按此目标推进"}',
            "请确认按此目标推进",
            None,
        ),
        (
            '{"feasibility_negotiation_description":"描述","action":"REQUEST_FEASIBILITY_EVALUATION",'
            '"contents_to_evaluate":null,"infeasibility_details_and_proposal":null,'
            '"feasibility_confirm_request":"方案可行，望确认"}',
            None,
            "方案可行，望确认",
        ),
    ],
)
def test_passes_confirm_request_wording_through_leniently(
    payload: str, expected_target_confirm: str | None, expected_feasibility_confirm: str | None
) -> None:
    if expected_target_confirm is not None:
        content = extractor(ScriptedExtractionClient(payload)).extract(
            "文本", reference(NegotiationType.TARGET, NegotiationPerformative.PROPOSE)
        )
        assert isinstance(content, TargetProposeContent)
        assert content.target_confirm_request == expected_target_confirm
    else:
        content = extractor(ScriptedExtractionClient(payload)).extract(
            "文本", reference(NegotiationType.FEASIBILITY, NegotiationPerformative.PROPOSE)
        )
        assert isinstance(content, FeasibilityProposeContent)
        assert content.feasibility_confirm_request == expected_feasibility_confirm


@pytest.mark.parametrize(
    ("negotiation_type", "performative", "payload", "assert_content"),
    [
        (
            NegotiationType.INFORMATION,
            NegotiationPerformative.ACCEPT,
            '{"conclusion":"Accept","items":[{"name":"故障发生时间","value":"2026-08-19 10:30"}]}',
            lambda content: (
                isinstance(content, InformationEndingContent)
                and content.conclusion is NegotiationConclusion.ACCEPT
                and content.items == [NegotiationItem("故障发生时间", "2026-08-19 10:30")]
            ),
        ),
        (
            NegotiationType.TARGET,
            NegotiationPerformative.REJECT,
            '{"conclusion":"Reject","confirmed_intent":null,"failure_reason":"双方未达成一致。"}',
            lambda content: (
                isinstance(content, TargetEndingContent)
                and content.conclusion is NegotiationConclusion.REJECT
                and content.failure_reason == "双方未达成一致。"
            ),
        ),
        (
            NegotiationType.FEASIBILITY,
            NegotiationPerformative.ACCEPT,
            '{"conclusion":"Accept","feasibility_summary":"同意下调至2Mbps。"}',
            lambda content: (
                isinstance(content, InformationEndingContent) is False
                and type(content).__name__ == "FeasibilityEndingContent"
                and content.feasibility_summary == "同意下调至2Mbps。"
            ),
        ),
    ],
)
def test_extracts_ending_content_for_every_type_and_both_phases(
    negotiation_type: NegotiationType,
    performative: NegotiationPerformative,
    payload: str,
    assert_content: Any,
) -> None:
    content = extractor(ScriptedExtractionClient(payload)).extract(
        "同意提供。", reference(negotiation_type, performative)
    )

    assert assert_content(content)


def test_extracts_abort_content() -> None:
    content = extractor(ScriptedExtractionClient('{"termination_reason":"协商轮次已达到上限，终止协商。"}')).extract(
        "轮次达到上限，终止。", reference(None, NegotiationPerformative.ABORT)
    )

    assert isinstance(content, NegotiationAbortContent)
    assert content.termination_reason == "协商轮次已达到上限，终止协商。"


@pytest.mark.parametrize(
    ("performative", "expected_token"),
    [
        (NegotiationPerformative.PROPOSE, "propose"),
        (NegotiationPerformative.ACCEPT, "accept"),
        (NegotiationPerformative.REJECT, "reject"),
    ],
)
def test_phase_token_reaches_the_user_prompt(performative: NegotiationPerformative, expected_token: str) -> None:
    client = ScriptedExtractionClient('{"conclusion":"Accept","items":[]}')
    llm = reference(NegotiationType.INFORMATION, performative)

    try:
        extractor(client).extract("文本", llm)
    except NegotiationGenerationError:
        # The scripted conclusion only satisfies the accept phase; the messages are recorded either way.
        pass

    user_prompt = client.last_messages[1]["content"].replace("\r\n", "\n")
    token_line = user_prompt.split("\n", 1)[0]
    assert token_line == f"协商阶段：{expected_token}"


def test_abort_addresses_the_termination_specific_extraction_prompt() -> None:
    client = ScriptedExtractionClient('{"termination_reason":"超时终止。"}')

    extractor(client).extract("超时，终止。", reference(None, NegotiationPerformative.ABORT))

    assert "终止" in client.last_messages[1]["content"]
    assert client.last_schema == build_extraction_schema(None, NegotiationPerformative.ABORT)


def test_extraction_schema_carries_no_context_fields() -> None:
    client = ScriptedExtractionClient(
        '{"items":[{"name":"接入端口名称","value":"P533-珠江旧城-PTN3900-23-TPAEG24-1"}],"relationship":null}'
    )

    extractor(client).extract(
        "请提供接入端口名称。", reference(NegotiationType.INFORMATION, NegotiationPerformative.PROPOSE)
    )

    assert set(client.last_schema["properties"]) == {"items", "relationship"}


# --------------------------------------------------------------------------------------
# retry policy: exact call counts (port plan 7.3)
# --------------------------------------------------------------------------------------

#: The valid propose payload the scripted client succeeds with after its queued failures.
_INFORMATION_PROPOSE_PAYLOAD = (
    '{"items":[{"name":"接入端口名称","value":"P533-珠江旧城-PTN3900-23-TPAEG24-1"}],"relationship":null}'
)

#: One scripted failure entry per retryable code and the code it must surface.
RETRYABLE_FAILURES = [
    pytest.param(
        "llm.response_invalid",
        "  ",
        ErrorCatalog.LLM_RESPONSE_INVALID.value,
        {"step": "协商内容提取"},
        id="llm.response_invalid",
    ),
    pytest.param(
        "negotiation.content_extract_failed",
        '{"items":"不是数组"}',
        ErrorCatalog.NEGOTIATION_CONTENT_EXTRACT_FAILED.value,
        {"field": "items", "reason": "Field items must be an array of items"},
        id="negotiation.content_extract_failed",
    ),
    pytest.param(
        "llm.invocation_failed",
        RuntimeError("connection reset"),
        ErrorCatalog.LLM_INVOCATION_FAILED.value,
        {"provider": "ScriptedExtractionClient", "reason": "connection reset"},
        id="llm.invocation_failed",
    ),
]

#: One scripted failure entry per non-retryable code and the code it must surface.
NON_RETRYABLE_FAILURES = [
    pytest.param(
        '{"conclusion":"Reject","items":[]}',
        ErrorCatalog.NEGOTIATION_CONCLUSION_MISMATCH.value,
        NegotiationPerformative.ACCEPT,
        id="negotiation.conclusion_mismatch",
    ),
    pytest.param(
        '{"relationship":null}',
        ErrorCatalog.NEGOTIATION_FIELD_MISSING.value,
        NegotiationPerformative.PROPOSE,
        id="negotiation.field_missing",
    ),
    pytest.param(
        '{"target_negotiation_description":"描述","intent_understanding":[{"name":"理解","value":null}],'
        '"alignment_and_clarification":null,"request_for_clarification":null,'
        '"target_confirm_request":"请确认"}',
        ErrorCatalog.NEGOTIATION_INVALID_INPUT.value,
        NegotiationPerformative.PROPOSE,
        id="negotiation.invalid_input",
    ),
]


@pytest.mark.parametrize(("label", "failure_entry", "expected_code", "expected_facts"), RETRYABLE_FAILURES)
@pytest.mark.parametrize("success_at_attempt", [1, 2, 3])
def test_retryable_failure_is_retried_until_it_succeeds(
    label: str,
    failure_entry: str | BaseException,
    expected_code: str,
    expected_facts: dict[str, str] | None,
    success_at_attempt: int,
) -> None:
    llm = ScriptedExtractionClient(*([failure_entry] * (success_at_attempt - 1)), _INFORMATION_PROPOSE_PAYLOAD)

    content = extractor(llm).extract(
        "请提供接入端口名称。", reference(NegotiationType.INFORMATION, NegotiationPerformative.PROPOSE)
    )

    assert isinstance(content, InformationProposeContent)
    assert llm.calls == success_at_attempt, f"{label} must surface after exactly {success_at_attempt} calls"


@pytest.mark.parametrize(("label", "failure_entry", "expected_code", "expected_facts"), RETRYABLE_FAILURES)
def test_exhausted_retries_re_raise_the_original_code_and_facts(
    label: str,
    failure_entry: str | BaseException,
    expected_code: str,
    expected_facts: dict[str, str] | None,
) -> None:
    llm = ScriptedExtractionClient(failure_entry)

    with pytest.raises(NegotiationGenerationError) as exc_info:
        extractor(llm).extract(
            "请提供接入端口名称。", reference(NegotiationType.INFORMATION, NegotiationPerformative.PROPOSE)
        )

    assert exc_info.value.code_str == expected_code
    assert llm.calls == 3, f"{label} must exhaust exactly the configured attempt limit"
    if expected_facts is not None:
        assert exc_info.value.facts == expected_facts


@pytest.mark.parametrize(("label", "failure_entry", "expected_code", "expected_facts"), RETRYABLE_FAILURES)
@pytest.mark.parametrize(("max_attempts", "expected_calls"), [(1, 1), (2, 2)])
def test_attempt_limit_drives_the_exact_call_count(
    label: str,
    failure_entry: str | BaseException,
    expected_code: str,
    expected_facts: dict[str, str] | None,
    max_attempts: int,
    expected_calls: int,
) -> None:
    llm = ScriptedExtractionClient(failure_entry)

    with pytest.raises(NegotiationGenerationError) as exc_info:
        extractor(llm, max_attempts=max_attempts).extract(
            "请提供接入端口名称。", reference(NegotiationType.INFORMATION, NegotiationPerformative.PROPOSE)
        )

    assert exc_info.value.code_str == expected_code
    assert llm.calls == expected_calls


def test_clamped_config_value_drives_the_retry_loop() -> None:
    max_attempts = LlmRuntimeConfig.from_mapping({LLM_MAX_ATTEMPTS_KEY: "99"}).max_attempts
    assert max_attempts == 10
    llm = ScriptedExtractionClient(*([""] * 9), _INFORMATION_PROPOSE_PAYLOAD)

    content = DefaultNegotiationContentExtractor(llm, max_attempts=max_attempts).extract(
        "请提供接入端口名称。", reference(NegotiationType.INFORMATION, NegotiationPerformative.PROPOSE)
    )

    assert isinstance(content, InformationProposeContent)
    assert llm.calls == 10, "nine failed attempts plus one success must consume the clamped limit"


def test_fail_then_succeed_at_every_attempt_position_recovers() -> None:
    for failures_before_success in (0, 1, 2):
        llm = ScriptedExtractionClient(*(["  "] * failures_before_success), _INFORMATION_PROPOSE_PAYLOAD)

        content = extractor(llm).extract(
            "请提供接入端口名称。", reference(NegotiationType.INFORMATION, NegotiationPerformative.PROPOSE)
        )

        assert isinstance(content, InformationProposeContent)
        assert llm.calls == failures_before_success + 1


def test_retry_failure_then_success_mixed_codes() -> None:
    llm = ScriptedExtractionClient(
        "  ",
        RuntimeError("connection reset"),
        '{"items":"不是数组"}',
        _INFORMATION_PROPOSE_PAYLOAD,
    )

    content = DefaultNegotiationContentExtractor(llm, max_attempts=4).extract(
        "请提供接入端口名称。", reference(NegotiationType.INFORMATION, NegotiationPerformative.PROPOSE)
    )

    assert isinstance(content, InformationProposeContent)
    assert llm.calls == 4, "three failures of three different retryable codes then one success"


@pytest.mark.parametrize(("failure_payload", "expected_code", "performative"), NON_RETRYABLE_FAILURES)
def test_non_retryable_failure_stops_after_exactly_one_call(
    failure_payload: str, expected_code: str, performative: NegotiationPerformative
) -> None:
    llm = ScriptedExtractionClient(failure_payload)
    negotiation_type = (
        NegotiationType.INFORMATION
        if expected_code != ErrorCatalog.NEGOTIATION_INVALID_INPUT.value
        else NegotiationType.TARGET
    )

    with pytest.raises(NegotiationGenerationError) as exc_info:
        extractor(llm).extract("请提供接入端口名称。", reference(negotiation_type, performative))

    assert exc_info.value.code_str == expected_code
    assert llm.calls == 1, "non-retryable failures must not be attempted again"


def test_retry_logs_the_step_and_attempt_limit(caplog: pytest.LogCaptureFixture) -> None:
    llm = ScriptedExtractionClient("", "", _INFORMATION_PROPOSE_PAYLOAD)

    with caplog.at_level("WARNING", logger="a2a_t.negotiation.generation.content_extractor"):
        extractor(llm).extract(
            "请提供接入端口名称。", reference(NegotiationType.INFORMATION, NegotiationPerformative.PROPOSE)
        )

    retries = [record.message for record in caplog.records if record.message.startswith("negotiation_llm_retry ")]
    assert len(retries) == 2
    assert all("step=negotiation_content_extract" in message for message in retries)
    assert all("max_attempts=3" in message for message in retries)
    assert "attempt=1" in retries[0]
    assert "attempt=2" in retries[1]
    assert not [record for record in caplog.records if "negotiation_llm_retry_exhausted" in record.message]


def test_exhaustion_logs_the_exhaustion_event(caplog: pytest.LogCaptureFixture) -> None:
    llm = ScriptedExtractionClient("")

    with caplog.at_level("WARNING", logger="a2a_t.negotiation.generation.content_extractor"):
        with pytest.raises(NegotiationGenerationError):
            extractor(llm).extract(
                "请提供接入端口名称。", reference(NegotiationType.INFORMATION, NegotiationPerformative.PROPOSE)
            )

    exhausted = [
        record.message for record in caplog.records if record.message.startswith("negotiation_llm_retry_exhausted")
    ]
    assert len(exhausted) == 1
    assert "step=negotiation_content_extract" in exhausted[0]
    assert "max_attempts=3" in exhausted[0]
    assert "code=llm.response_invalid" in exhausted[0]


# --------------------------------------------------------------------------------------
# failure-code mapping of the response contract and the content shape
# --------------------------------------------------------------------------------------


def test_maps_transport_failures_to_the_invocation_failed_code() -> None:
    with pytest.raises(NegotiationGenerationError) as exc_info:
        extractor(ScriptedExtractionClient(RuntimeError("connection reset"))).extract(
            "文本", reference(NegotiationType.INFORMATION, NegotiationPerformative.PROPOSE)
        )

    assert exc_info.value.code_str == ErrorCatalog.LLM_INVOCATION_FAILED.value
    assert exc_info.value.facts["provider"] == "ScriptedExtractionClient"
    assert exc_info.value.facts["reason"] == "connection reset"
    assert isinstance(exc_info.value.__cause__, RuntimeError)


def test_maps_missing_llm_client_to_the_not_configured_code() -> None:
    with pytest.raises(NegotiationGenerationError) as exc_info:
        extractor(None).extract("文本", reference(NegotiationType.INFORMATION, NegotiationPerformative.PROPOSE))

    assert exc_info.value.code_str == ErrorCatalog.LLM_NOT_CONFIGURED.value


# --------------------------------------------------------------------------------------
# extraction prompt resource misses: the Java orchestrator's extractContent catch point
# --------------------------------------------------------------------------------------


def test_missing_extraction_prompt_maps_to_the_template_not_found_code() -> None:
    llm = ScriptedExtractionClient(_INFORMATION_PROPOSE_PAYLOAD)

    with pytest.raises(NegotiationGenerationError) as exc_info:
        extractor(llm).extract(
            "请提供接入端口名称。",
            reference(NegotiationType.INFORMATION, NegotiationPerformative.PROPOSE, "fr-FR"),
        )

    assert exc_info.value.code_str == ErrorCatalog.TEMPLATE_NOT_FOUND.value
    assert exc_info.value.facts == {
        "template_uri": "Negotiation-T/information-negotiation/propose/v1",
        "language": "fr-FR",
    }
    assert isinstance(exc_info.value.__cause__, A2ATError)
    assert llm.calls == 0, "a missing extraction prompt must fail before the LLM is touched at all"


def test_resource_lookup_miss_of_an_injected_builder_maps_to_template_not_found() -> None:
    llm = ScriptedExtractionClient(_INFORMATION_PROPOSE_PAYLOAD)

    def missing_prompt_builder(
        prompt_category: str, language: str, tokens: dict[str, str | None] | None
    ) -> list[dict[str, str]]:
        raise ResourceNotFoundError(
            "Negotiation prompt resource does not exist.", f"prompts/{prompt_category}/{language}/system.md"
        )

    with pytest.raises(NegotiationGenerationError) as exc_info:
        DefaultNegotiationContentExtractor(llm, message_builder=missing_prompt_builder).extract(
            "请提供接入端口名称。", reference(NegotiationType.INFORMATION, NegotiationPerformative.PROPOSE)
        )

    assert exc_info.value.code_str == ErrorCatalog.TEMPLATE_NOT_FOUND.value
    assert exc_info.value.facts["template_uri"] == "Negotiation-T/information-negotiation/propose/v1"
    assert llm.calls == 0


def test_other_builder_failures_propagate_raw_without_translation() -> None:
    llm = ScriptedExtractionClient(_INFORMATION_PROPOSE_PAYLOAD)

    def failing_builder(
        prompt_category: str, language: str, tokens: dict[str, str | None] | None
    ) -> list[dict[str, str]]:
        raise A2ATError("Failed to read negotiation prompt resource.")

    with pytest.raises(A2ATError) as exc_info:
        DefaultNegotiationContentExtractor(llm, message_builder=failing_builder).extract(
            "请提供接入端口名称。", reference(NegotiationType.INFORMATION, NegotiationPerformative.PROPOSE)
        )

    assert not isinstance(exc_info.value, NegotiationGenerationError)
    assert exc_info.value.code_str == ErrorCatalog.INFRA_INTERNAL_ERROR.value
    assert llm.calls == 0


@pytest.mark.parametrize(
    ("payload", "expected_code"),
    [
        ("这不是 JSON", ErrorCatalog.LLM_RESPONSE_INVALID.value),
        ('["not", "an", "object"]', ErrorCatalog.LLM_RESPONSE_INVALID.value),
        ("  ", ErrorCatalog.LLM_RESPONSE_INVALID.value),
    ],
)
def test_maps_response_contract_violations(payload: str, expected_code: str) -> None:
    with pytest.raises(NegotiationGenerationError) as exc_info:
        extractor(ScriptedExtractionClient(payload)).extract(
            "文本", reference(NegotiationType.INFORMATION, NegotiationPerformative.PROPOSE)
        )

    assert exc_info.value.code_str == expected_code


def test_response_invalid_step_fact_follows_the_language() -> None:
    with pytest.raises(NegotiationGenerationError) as zh_error:
        extractor(ScriptedExtractionClient("不是 JSON")).extract(
            "文本", reference(NegotiationType.INFORMATION, NegotiationPerformative.PROPOSE, ZH_CN)
        )
    with pytest.raises(NegotiationGenerationError) as en_error:
        extractor(ScriptedExtractionClient("not JSON")).extract(
            "text", reference(NegotiationType.INFORMATION, NegotiationPerformative.PROPOSE, EN_US)
        )

    assert zh_error.value.facts["step"] == "协商内容提取"
    assert en_error.value.facts["step"] == "negotiation content extraction"


def test_maps_wrong_shape_failures_with_the_field_fact() -> None:
    with pytest.raises(NegotiationGenerationError) as exc_info:
        extractor(ScriptedExtractionClient('{"items":"不是数组"}')).extract(
            "文本", reference(NegotiationType.INFORMATION, NegotiationPerformative.PROPOSE)
        )

    assert exc_info.value.code_str == ErrorCatalog.NEGOTIATION_CONTENT_EXTRACT_FAILED.value
    assert exc_info.value.facts["field"] == "items"
    assert "Field items must be an array of items" in exc_info.value.facts["reason"]


@pytest.mark.parametrize(
    ("payload", "expected_message_part"),
    [
        ('{"items":[{"name":"","value":null}]}', "Field items contained an item without a name"),
        ('{"items":"不是数组"}', "Field items must be an array of items"),
        ('{"items":[1,2]}', "Field items must contain item objects"),
        ('{"items":[{"value":null}]}', "Field items contained an item without a name"),
        ('{"items":[{"name":"x","value":3}]}', "Field items contained an item whose value is not a string"),
    ],
)
def test_maps_shape_failures_of_every_kind(payload: str, expected_message_part: str) -> None:
    with pytest.raises(NegotiationGenerationError) as exc_info:
        extractor(ScriptedExtractionClient(payload)).extract(
            "文本", reference(NegotiationType.INFORMATION, NegotiationPerformative.PROPOSE)
        )

    assert exc_info.value.code_str == ErrorCatalog.NEGOTIATION_CONTENT_EXTRACT_FAILED.value
    assert expected_message_part in str(exc_info.value)


@pytest.mark.parametrize(
    ("payload", "negotiation_type", "performative", "expected_field"),
    [
        ('{"relationship":null}', NegotiationType.INFORMATION, NegotiationPerformative.PROPOSE, "items"),
        (
            '{"intent_understanding":null,"alignment_and_clarification":null,"request_for_clarification":null}',
            NegotiationType.TARGET,
            NegotiationPerformative.PROPOSE,
            "target_negotiation_description",
        ),
        ('{"items":[]}', NegotiationType.INFORMATION, NegotiationPerformative.ACCEPT, "conclusion"),
        (
            '{"conclusion":"Accept","confirmed_intent":null,"failure_reason":null}',
            NegotiationType.TARGET,
            NegotiationPerformative.ACCEPT,
            "confirmed_intent",
        ),
        (
            '{"conclusion":"Reject","confirmed_intent":null,"failure_reason":""}',
            NegotiationType.TARGET,
            NegotiationPerformative.REJECT,
            "failure_reason",
        ),
        (
            '{"conclusion":"Accept","feasibility_summary":""}',
            NegotiationType.FEASIBILITY,
            NegotiationPerformative.ACCEPT,
            "feasibility_summary",
        ),
        (
            '{"termination_reason":""}',
            None,
            NegotiationPerformative.ABORT,
            "termination_reason",
        ),
    ],
)
def test_maps_missing_required_fields_to_the_field_missing_code(
    payload: str,
    negotiation_type: NegotiationType | None,
    performative: NegotiationPerformative,
    expected_field: str,
) -> None:
    with pytest.raises(NegotiationGenerationError) as exc_info:
        extractor(ScriptedExtractionClient(payload)).extract("文本", reference(negotiation_type, performative))

    assert exc_info.value.code_str == ErrorCatalog.NEGOTIATION_FIELD_MISSING.value
    assert expected_field in str(exc_info.value)


def test_empty_information_propose_items_are_a_retryable_extraction_miss() -> None:
    """An empty extraction of the inbound items is a retryable miss, not a client field omission."""
    with pytest.raises(NegotiationGenerationError) as exc_info:
        extractor(ScriptedExtractionClient('{"items":[]}')).extract(
            "请补充缺失信息。", reference(NegotiationType.INFORMATION, NegotiationPerformative.PROPOSE)
        )

    assert exc_info.value.code_str == ErrorCatalog.NEGOTIATION_CONTENT_EXTRACT_FAILED.value
    assert "requested items" in str(exc_info.value)


def test_rejects_empty_information_ending_items_before_template_rendering() -> None:
    with pytest.raises(NegotiationGenerationError) as exc_info:
        extractor(ScriptedExtractionClient('{"conclusion":"Reject","items":[]}')).extract(
            "拒绝，资源查询服务正在检修。", reference(NegotiationType.INFORMATION, NegotiationPerformative.REJECT)
        )

    assert exc_info.value.code_str == ErrorCatalog.NEGOTIATION_FIELD_MISSING.value
    assert "result content" in str(exc_info.value)


@pytest.mark.parametrize(
    ("payload", "performative", "expected_facts"),
    [
        (
            '{"conclusion":"Reject","items":[]}',
            NegotiationPerformative.ACCEPT,
            {"expected": "Accept", "actual": "Reject"},
        ),
        (
            '{"conclusion":"Accept","feasibility_summary":"同意。"}',
            NegotiationPerformative.REJECT,
            {"expected": "Reject", "actual": "Accept"},
        ),
        (
            '{"conclusion":"Abort","feasibility_summary":"终止。"}',
            NegotiationPerformative.ACCEPT,
            {"expected": "Accept", "actual": "Abort"},
        ),
    ],
)
def test_maps_conclusion_phase_mismatches_to_the_conclusion_mismatch_code(
    payload: str, performative: NegotiationPerformative, expected_facts: dict[str, str]
) -> None:
    negotiation_type = NegotiationType.FEASIBILITY if "feasibility_summary" in payload else NegotiationType.INFORMATION

    with pytest.raises(NegotiationGenerationError) as exc_info:
        extractor(ScriptedExtractionClient(payload)).extract("文本", reference(negotiation_type, performative))

    assert exc_info.value.code_str == ErrorCatalog.NEGOTIATION_CONCLUSION_MISMATCH.value
    assert exc_info.value.facts == expected_facts


@pytest.mark.parametrize(
    ("payload", "expected_code", "expected_reason_part"),
    [
        (
            '{"feasibility_negotiation_description":"描述","contents_to_evaluate":[]}',
            ErrorCatalog.NEGOTIATION_INVALID_INPUT.value,
            "produced no action",
        ),
        (
            '{"feasibility_negotiation_description":"描述","action":"REQUEST_FEASIBILITY_EVALUATION",'
            '"contents_to_evaluate":[],"infeasibility_details_and_proposal":null}',
            ErrorCatalog.NEGOTIATION_INVALID_INPUT.value,
            "extracted no contents to evaluate",
        ),
        (
            '{"feasibility_negotiation_description":"描述","action":"PROPOSE_ALTERNATIVE_ON_FAILURE",'
            '"contents_to_evaluate":null,"infeasibility_details_and_proposal":[]}',
            ErrorCatalog.NEGOTIATION_INVALID_INPUT.value,
            "extracted no infeasibility details",
        ),
        (
            '{"feasibility_negotiation_description":"描述","action":"REQUEST_FEASIBILITY_EVALUATION",'
            '"contents_to_evaluate":[{"name":"对象","value":null}],"infeasibility_details_and_proposal":null,'
            '"feasibility_confirm_request":"请确认"}',
            ErrorCatalog.NEGOTIATION_INVALID_INPUT.value,
            "extracted together with the contents to evaluate or infeasibility details and proposal sections",
        ),
        (
            '{"feasibility_negotiation_description":"描述","action":"PROPOSE_ALTERNATIVE_ON_FAILURE",'
            '"contents_to_evaluate":null,"infeasibility_details_and_proposal":null,'
            '"feasibility_confirm_request":"请确认"}',
            ErrorCatalog.NEGOTIATION_INVALID_INPUT.value,
            "requires the REQUEST_FEASIBILITY_EVALUATION action but the extracted action was "
            "PROPOSE_ALTERNATIVE_ON_FAILURE",
        ),
        (
            '{"feasibility_negotiation_description":"描述","action":"NOT_AN_ACTION",'
            '"contents_to_evaluate":[{"name":"对象","value":null}],"infeasibility_details_and_proposal":null}',
            ErrorCatalog.NEGOTIATION_CONTENT_EXTRACT_FAILED.value,
            "action must be one of the two action names",
        ),
        (
            '{"feasibility_negotiation_description":"描述","action":3,'
            '"contents_to_evaluate":[{"name":"对象","value":null}],"infeasibility_details_and_proposal":null}',
            ErrorCatalog.NEGOTIATION_CONTENT_EXTRACT_FAILED.value,
            "action must be one of the two action names",
        ),
    ],
)
def test_maps_feasibility_action_problems_to_their_codes(
    payload: str, expected_code: str, expected_reason_part: str
) -> None:
    with pytest.raises(NegotiationGenerationError) as exc_info:
        extractor(ScriptedExtractionClient(payload)).extract(
            "文本", reference(NegotiationType.FEASIBILITY, NegotiationPerformative.PROPOSE)
        )

    assert exc_info.value.code_str == expected_code
    assert expected_reason_part in exc_info.value.facts["reason"]


def test_maps_target_confirm_request_mutual_exclusion_to_the_invalid_input_code() -> None:
    with pytest.raises(NegotiationGenerationError) as exc_info:
        extractor(
            ScriptedExtractionClient(
                '{"target_negotiation_description":"描述","intent_understanding":[{"name":"理解","value":null}],'
                '"alignment_and_clarification":null,"request_for_clarification":null,'
                '"target_confirm_request":"请确认"}'
            )
        ).extract("文本", reference(NegotiationType.TARGET, NegotiationPerformative.PROPOSE))

    assert exc_info.value.code_str == ErrorCatalog.NEGOTIATION_INVALID_INPUT.value
    assert exc_info.value.facts["reason"] == (
        "Target confirm request extracted together with the intent understanding, alignment and "
        "clarification or clarification request sections; a confirm-request round carries only the "
        "summary and the confirm request."
    )


def test_rejects_blank_input_text_and_none_reference() -> None:
    negotiation_extractor = extractor(ScriptedExtractionClient("{}"))

    with pytest.raises(NegotiationGenerationError) as exc_info:
        negotiation_extractor.extract("  ", reference(NegotiationType.INFORMATION, NegotiationPerformative.PROPOSE))

    assert exc_info.value.code_str == ErrorCatalog.NEGOTIATION_INVALID_INPUT.value
    assert "must not be blank" in exc_info.value.facts["reason"]

    with pytest.raises(TypeError, match="Negotiation reference must not be null."):
        negotiation_extractor.extract("文本", None)  # type: ignore[arg-type]


# --------------------------------------------------------------------------------------
# input-length gate: the third D5 access point, before any LLM call
# --------------------------------------------------------------------------------------


def test_oversized_text_fails_before_the_first_llm_call() -> None:
    llm = ScriptedExtractionClient(_INFORMATION_PROPOSE_PAYLOAD)
    limit = InputLimitConfig(max_text_chars=8)
    text = "请提供接入端口名称，文本长度超过限制。"
    negotiated = DefaultNegotiationContentExtractor(llm, input_limit=limit)

    with pytest.raises(NegotiationGenerationError) as exc_info:
        negotiated.extract(text, reference(NegotiationType.INFORMATION, NegotiationPerformative.PROPOSE))

    assert exc_info.value.code_str == ErrorCatalog.INPUT_TEXT_TOO_LONG.value
    assert exc_info.value.facts == {"actual_length": str(len(text)), "max_chars": "8"}
    assert llm.calls == 0, "the input-length gate must fail before the LLM is touched at all"


def test_text_one_over_the_default_limit_is_rejected_before_any_llm_call() -> None:
    llm = ScriptedExtractionClient(_INFORMATION_PROPOSE_PAYLOAD)
    text = "a" * (DEFAULT_MAX_TEXT_CHARS + 1)

    with pytest.raises(NegotiationGenerationError) as exc_info:
        DefaultNegotiationContentExtractor(llm).extract(
            text, reference(NegotiationType.INFORMATION, NegotiationPerformative.PROPOSE)
        )

    assert exc_info.value.code_str == ErrorCatalog.INPUT_TEXT_TOO_LONG.value
    assert str(DEFAULT_MAX_TEXT_CHARS + 1) in str(exc_info.value), "the message must state the actual length"
    assert str(DEFAULT_MAX_TEXT_CHARS) in str(exc_info.value), "the message must state the limit"
    assert llm.calls == 0, "an oversized text must never reach the LLM"


def test_text_at_exactly_the_default_limit_passes_the_gate() -> None:
    llm = ScriptedExtractionClient(_INFORMATION_PROPOSE_PAYLOAD)
    text = "a" * DEFAULT_MAX_TEXT_CHARS

    content = DefaultNegotiationContentExtractor(llm).extract(
        text, reference(NegotiationType.INFORMATION, NegotiationPerformative.PROPOSE)
    )

    assert isinstance(content, InformationProposeContent)
    assert llm.calls == 1, "a text at exactly the limit must reach the LLM"


def test_default_input_limit_allows_the_regular_length() -> None:
    llm = ScriptedExtractionClient(_INFORMATION_PROPOSE_PAYLOAD)

    content = DefaultNegotiationContentExtractor(llm).extract(
        "请提供接入端口名称。", reference(NegotiationType.INFORMATION, NegotiationPerformative.PROPOSE)
    )

    assert isinstance(content, InformationProposeContent)
    assert llm.calls == 1


def test_none_text_is_never_too_long_but_is_blank() -> None:
    llm = ScriptedExtractionClient(_INFORMATION_PROPOSE_PAYLOAD)

    with pytest.raises(NegotiationGenerationError) as exc_info:
        DefaultNegotiationContentExtractor(llm).extract(
            None, reference(NegotiationType.INFORMATION, NegotiationPerformative.PROPOSE)
        )

    assert exc_info.value.code_str == ErrorCatalog.NEGOTIATION_INVALID_INPUT.value
    assert llm.calls == 0


def test_length_gate_applies_to_the_abort_performative_too() -> None:
    llm = ScriptedExtractionClient('{"termination_reason":"终止。"}')

    with pytest.raises(NegotiationGenerationError) as exc_info:
        DefaultNegotiationContentExtractor(llm, input_limit=InputLimitConfig(max_text_chars=2)).extract(
            "超时终止协商", reference(None, NegotiationPerformative.ABORT)
        )

    assert exc_info.value.code_str == ErrorCatalog.INPUT_TEXT_TOO_LONG.value
    assert llm.calls == 0


def test_max_attempts_below_one_is_rejected() -> None:
    with pytest.raises(ValueError, match="max_attempts must be at least 1"):
        DefaultNegotiationContentExtractor(ScriptedExtractionClient("{}"), max_attempts=0)


def test_max_attempts_defaults_to_the_java_default() -> None:
    negotiated = DefaultNegotiationContentExtractor(ScriptedExtractionClient("{}"))

    assert negotiated.max_attempts == 3
