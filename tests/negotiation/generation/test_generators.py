"""Tests of the five function-style negotiation generators and their validation rules.

Port of the Java ``NegotiationGeneratorsTest`` (plus the generator-level rows of
``AbortMessageGenerationTest`` / ``ConclusionLiteralAndExceptionSlotTest`` /
``ConditionalSectionRenderingTest`` / ``RelationshipLineRenderingTest``). Every valid render goes
through the REAL bundled templates loaded via the packaged resource access (D31), so the
assertions double as golden-ish snapshots of the packaged template bytes. Every failure cell pins
the exact catalog code and facts — ``negotiation.content_invalid`` for blank required fields,
``negotiation.invalid_input`` (through the shared D14 function, ``combined`` wording) for the
confirm-request mutual exclusion, and ``TypeError``/``ValueError`` for the programming errors.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

import pytest

from a2a_t.common.prompt_resources.resource_access import PackagedPromptResourceAccess
from a2a_t.core.errors.catalog import ErrorCatalog
from a2a_t.core.errors.exceptions import A2ATBusinessError, NegotiationGenerationError
from a2a_t.core.metadata import NegotiationContext, NegotiationPerformative
from a2a_t.core.standard_templates import (
    FEASIBILITY_NEGOTIATION_ACCEPT_REJECT_URI,
    FEASIBILITY_NEGOTIATION_PROPOSE_URI,
    INFORMATION_NEGOTIATION_ACCEPT_REJECT_URI,
    INFORMATION_NEGOTIATION_PROPOSE_URI,
    NEGOTIATION_ABORT_URI,
    TARGET_NEGOTIATION_ACCEPT_REJECT_URI,
    TARGET_NEGOTIATION_PROPOSE_URI,
)
from a2a_t.negotiation.content import (
    FeasibilityEndingContent,
    FeasibilityProposeContent,
    InformationEndingContent,
    InformationProposeContent,
    NegotiationAbortContent,
    NegotiationAction,
    NegotiationConclusion,
    NegotiationContent,
    NegotiationItem,
    TargetEndingContent,
    TargetProposeContent,
    Vocabulary,
)
from a2a_t.negotiation.generation.generators import (
    generate_abort,
    generate_ending,
    generate_feasibility_propose,
    generate_information_propose,
    generate_target_propose,
)

if TYPE_CHECKING:
    from a2a_t.negotiation.generation import NegotiationGenerator

SESSION_ID = "3dbc13b5-bd57-4c2b-b503-24e381b6c8d3"

ACCESS = PackagedPromptResourceAccess()
ZH = Vocabulary.for_language("zh-CN")
EN = Vocabulary.for_language("en-US")


def context(round: int, performative: NegotiationPerformative = NegotiationPerformative.PROPOSE) -> NegotiationContext:
    """Build one negotiation context of the given round."""
    return NegotiationContext(SESSION_ID, round, 5, performative)


def zh_template(uri: str) -> str:
    """Load one bundled zh-CN template through the packaged access."""
    return ACCESS.template_text(uri, "zh-CN")


def en_template(uri: str) -> str:
    """Load one bundled en-US template through the packaged access."""
    return ACCESS.template_text(uri, "en-US")


# ---------------------------------------------------------------------------
# Information propose
# ---------------------------------------------------------------------------


def test_information_propose_renders_items_and_static_section() -> None:
    content = InformationProposeContent(
        [NegotiationItem("故障发生时间", "精确到分钟的时间点"), NegotiationItem("受影响小区标识", "CGI 或小区名称")],
        None,
    )

    rendered = generate_information_propose(context(1), content, zh_template(INFORMATION_NEGOTIATION_PROPOSE_URI), ZH)

    assert "## 所需信息项\n1. 故障发生时间：精确到分钟的时间点\n2. 受影响小区标识：CGI 或小区名称" in rendered
    assert "## 信息协商\n请根据[所需信息项](#所需信息项)补充相关内容。" in rendered
    assert "要求：" not in rendered
    assert "缺失项之间的关系" not in rendered
    assert "{{" not in rendered


def test_information_propose_appends_relationship_line_and_omits_it_when_absent() -> None:
    with_relationship = InformationProposeContent(
        [NegotiationItem("故障发生时间", "精确到分钟的时间点"), NegotiationItem("受影响小区标识", "CGI 或小区名称")],
        "故障发生时间与受影响小区标识需逐小区对应",
    )

    rendered_zh = generate_information_propose(
        context(1), with_relationship, zh_template(INFORMATION_NEGOTIATION_PROPOSE_URI), ZH
    )

    assert "## 所需信息项\n1. 故障发生时间：精确到分钟的时间点\n2. 受影响小区标识：CGI 或小区名称" in rendered_zh
    assert rendered_zh.endswith("缺失项之间的关系：故障发生时间与受影响小区标识需逐小区对应")

    without_relationship = InformationProposeContent([NegotiationItem("故障发生时间", "精确到分钟的时间点")], None)

    rendered_without = generate_information_propose(
        context(1), without_relationship, zh_template(INFORMATION_NEGOTIATION_PROPOSE_URI), ZH
    )

    assert "缺失项之间的关系" not in rendered_without


def test_information_propose_renders_english_relationship_label_with_trailing_space() -> None:
    content = InformationProposeContent(
        [NegotiationItem("Failure time", "minute precision")], "failure time and cell identity must correspond per cell"
    )

    rendered = generate_information_propose(context(1), content, en_template(INFORMATION_NEGOTIATION_PROPOSE_URI), EN)

    assert rendered.endswith(
        "Relationship between missing items: failure time and cell identity must correspond per cell"
    )


@pytest.mark.parametrize("items", [None, []], ids=["none", "empty"])
def test_information_propose_rejects_empty_requested_items(items: list[NegotiationItem] | None) -> None:
    content = InformationProposeContent(items, None)

    with pytest.raises(NegotiationGenerationError) as info:
        generate_information_propose(context(1), content, zh_template(INFORMATION_NEGOTIATION_PROPOSE_URI), ZH)

    _assert_content_invalid(info.value, "items", "Information negotiation propose message requested items")


# ---------------------------------------------------------------------------
# Target propose: round-driven conditional sections
# ---------------------------------------------------------------------------


def test_target_first_round_renders_intent_section_and_drops_alignment_section() -> None:
    content = TargetProposeContent(
        "对无线节能优化任务的意图理解参见<意图理解陈述>，请澄清和确认。",
        [NegotiationItem("发起方理解", "对方希望在体验无损的前提下降低节能力度")],
        [NegotiationItem("对齐项", "不应在首轮渲染")],
        [NegotiationItem("节能时间范围", "需要澄清")],
        None,
    )

    rendered = generate_target_propose(context(1), content, zh_template(TARGET_NEGOTIATION_PROPOSE_URI), ZH)

    assert rendered.startswith("## 目标协商\n")
    assert "协商上下文" not in rendered, "the context section must not be rendered"
    assert "## 意图理解陈述\n1. 发起方理解：对方希望在体验无损的前提下降低节能力度" in rendered
    assert "## 理解对齐与疑问澄清" not in rendered
    assert "## 待澄清内容\n1. 节能时间范围：需要澄清" in rendered
    assert "## 目标协商\n对无线节能优化任务的意图理解参见<意图理解陈述>，请澄清和确认。" in rendered
    assert "要求：" not in rendered
    assert not rendered.endswith("\n")


def test_target_later_round_renders_alignment_section_and_drops_intent_section() -> None:
    content = TargetProposeContent(
        "已提供疑问澄清，请确认。",
        [NegotiationItem("意图项", "不应在非首轮渲染")],
        [NegotiationItem("确认结果", "节能时间范围确认为08:00~18:00")],
        None,
        None,
    )

    rendered = generate_target_propose(context(3), content, zh_template(TARGET_NEGOTIATION_PROPOSE_URI), ZH)

    assert "## 理解对齐与疑问澄清\n1. 确认结果：节能时间范围确认为08:00~18:00" in rendered
    assert "## 意图理解陈述" not in rendered
    assert "## 待澄清内容" not in rendered


@pytest.mark.parametrize("blank", [None, "", " "], ids=["none", "empty", "blank"])
def test_target_blank_description_is_rejected(blank: str | None) -> None:
    content = TargetProposeContent(blank, None, None, None, None)

    with pytest.raises(NegotiationGenerationError) as info:
        generate_target_propose(context(1), content, zh_template(TARGET_NEGOTIATION_PROPOSE_URI), ZH)

    _assert_content_invalid(info.value, "content.targetNegotiationDescription", "Target negotiation description")


# ---------------------------------------------------------------------------
# Target propose: confirm-request round (D14 shared validation, combined wording)
# ---------------------------------------------------------------------------


def test_target_confirm_request_renders_only_the_summary_and_confirm_request_sections() -> None:
    rendered = generate_target_propose(
        context(2),
        TargetProposeContent(
            "任务目标澄清完成，请答复<目标澄清后的确认请求>。",
            None,
            None,
            None,
            "目标已经澄清，是否同意按照此目标继续执行？",
        ),
        zh_template(TARGET_NEGOTIATION_PROPOSE_URI),
        ZH,
    )

    assert "## 目标协商\n任务目标澄清完成，请答复<目标澄清后的确认请求>。" in rendered
    assert "## 目标澄清后的确认请求\n目标已经澄清，是否同意按照此目标继续执行？" in rendered
    assert "## 意图理解陈述" not in rendered
    assert "## 理解对齐与疑问澄清" not in rendered
    assert "## 待澄清内容" not in rendered
    assert "要求：" not in rendered

    rendered_en = generate_target_propose(
        context(2),
        TargetProposeContent(
            "The clarification of the task target has been completed. Please reply to <Target Clarification Confirmation Request>.",
            None,
            None,
            None,
            "The target has been clarified. Do you agree to proceed with this target?",
        ),
        en_template(TARGET_NEGOTIATION_PROPOSE_URI),
        EN,
    )

    assert "## Target Negotiation\nThe clarification of the task target has been completed." in rendered_en
    assert (
        "## Target Clarification Confirmation Request\nThe target has been clarified. Do you agree to proceed with this target?"
        in rendered_en
    )
    assert "## Intent Understanding Statement" not in rendered_en
    assert "{{" not in rendered_en


@pytest.mark.parametrize(
    ("intent", "alignment", "clarification"),
    [
        ("intent", None, None),
        (None, "alignment", None),
        (None, None, "clarification"),
        ("intent", "alignment", "clarification"),
    ],
    ids=["intent", "alignment", "clarification", "all-three"],
)
def test_target_confirm_request_combined_with_any_conditional_section_fails(
    intent: str | None, alignment: str | None, clarification: str | None
) -> None:
    content = TargetProposeContent(
        "描述",
        _items_or_none(intent),
        _items_or_none(alignment),
        _items_or_none(clarification),
        "目标已经澄清，是否同意按照此目标继续执行？",
    )

    with pytest.raises(NegotiationGenerationError) as info:
        generate_target_propose(context(2), content, zh_template(TARGET_NEGOTIATION_PROPOSE_URI), ZH)

    _assert_invalid_input(
        info.value,
        "Target confirm request must not be combined with the intent understanding, alignment and "
        "clarification or clarification request sections; a confirm-request round carries only the "
        "summary and the confirm request.",
    )


@pytest.mark.parametrize("blank", [None, "", " "], ids=["none", "empty", "blank"])
def test_target_confirm_request_is_ignored_when_blank(blank: str | None) -> None:
    content = TargetProposeContent("描述。", [NegotiationItem("发起方理解", "理解")], None, None, blank)

    rendered = generate_target_propose(context(1), content, zh_template(TARGET_NEGOTIATION_PROPOSE_URI), ZH)

    assert "## 目标澄清后的确认请求" not in rendered
    assert "## 意图理解陈述\n1. 发起方理解：理解" in rendered


# ---------------------------------------------------------------------------
# Feasibility propose: action-driven conditional sections
# ---------------------------------------------------------------------------


def test_feasibility_request_action_renders_exactly_the_evaluation_section() -> None:
    content = FeasibilityProposeContent(
        "请协助评估该节能目标能否达成。",
        NegotiationAction.REQUEST_FEASIBILITY_EVALUATION,
        [NegotiationItem("评估对象", "停电8小时期间核心用户的速率保障")],
        [NegotiationItem("不应出现", "值")],
        None,
    )

    rendered = generate_feasibility_propose(context(1), content, zh_template(FEASIBILITY_NEGOTIATION_PROPOSE_URI), ZH)

    assert "## 可行性协商\n请协助评估该节能目标能否达成。" in rendered
    assert "## 待评估内容说明\n1. 评估对象：停电8小时期间核心用户的速率保障" in rendered
    assert "## 评估不可行时的详情和提案" not in rendered
    assert "要求：" not in rendered


def test_feasibility_alternative_action_renders_exactly_the_infeasibility_section() -> None:
    content = FeasibilityProposeContent(
        "当前速率目标不可行，提出下调方案。",
        NegotiationAction.PROPOSE_ALTERNATIVE_ON_FAILURE,
        [NegotiationItem("不应出现", "值")],
        [
            NegotiationItem("不可行原因", "蓄电池仅能支撑8小时2Mbps的保障能力"),
            NegotiationItem("替代提案", "停电期间将速率保障目标下调至2Mbps"),
        ],
        None,
    )

    rendered = generate_feasibility_propose(context(2), content, zh_template(FEASIBILITY_NEGOTIATION_PROPOSE_URI), ZH)

    assert "## 可行性协商\n当前速率目标不可行，提出下调方案。" in rendered
    assert "## 评估不可行时的详情和提案\n1. 不可行原因：蓄电池仅能支撑8小时2Mbps的保障能力" in rendered
    assert "2. 替代提案：停电期间将速率保障目标下调至2Mbps" in rendered
    assert "## 待评估内容说明" not in rendered


def test_feasibility_null_action_is_rejected_before_the_confirm_gate() -> None:
    # The Java null check runs before the confirm gate, so a null action is reported even when the
    # confirm request is absent (FromDataProgrammingErrorMatrixTest row "feasibility propose without action").
    content = FeasibilityProposeContent("描述", None, [NegotiationItem("名称", "值")], None, None)

    with pytest.raises(TypeError, match="action must not be null"):
        generate_feasibility_propose(context(1), content, zh_template(FEASIBILITY_NEGOTIATION_PROPOSE_URI), ZH)


@pytest.mark.parametrize("blank", [None, "", " "], ids=["none", "empty", "blank"])
def test_feasibility_blank_description_is_rejected(blank: str | None) -> None:
    content = FeasibilityProposeContent(
        blank,
        NegotiationAction.REQUEST_FEASIBILITY_EVALUATION,
        [NegotiationItem("名称", "值")],
        None,
        None,
    )

    with pytest.raises(NegotiationGenerationError) as info:
        generate_feasibility_propose(context(1), content, zh_template(FEASIBILITY_NEGOTIATION_PROPOSE_URI), ZH)

    _assert_content_invalid(
        info.value, "content.feasibilityNegotiationDescription", "Feasibility negotiation description"
    )


@pytest.mark.parametrize("contents", [None, []], ids=["none", "empty"])
def test_feasibility_evaluation_request_with_empty_contents_is_rejected(
    contents: list[NegotiationItem] | None,
) -> None:
    content = FeasibilityProposeContent("描述", NegotiationAction.REQUEST_FEASIBILITY_EVALUATION, contents, None, None)

    with pytest.raises(NegotiationGenerationError) as info:
        generate_feasibility_propose(context(1), content, zh_template(FEASIBILITY_NEGOTIATION_PROPOSE_URI), ZH)

    _assert_content_invalid(
        info.value, "content.contentsToEvaluate", "Contents to evaluate of a feasibility evaluation request"
    )


@pytest.mark.parametrize("infeasibility", [None, []], ids=["none", "empty"])
def test_feasibility_alternative_with_empty_details_is_rejected(
    infeasibility: list[NegotiationItem] | None,
) -> None:
    content = FeasibilityProposeContent(
        "描述", NegotiationAction.PROPOSE_ALTERNATIVE_ON_FAILURE, None, infeasibility, None
    )

    with pytest.raises(NegotiationGenerationError) as info:
        generate_feasibility_propose(context(1), content, zh_template(FEASIBILITY_NEGOTIATION_PROPOSE_URI), ZH)

    _assert_content_invalid(
        info.value,
        "content.infeasibilityDetailsAndProposal",
        "Infeasibility details and proposal of an alternative proposal",
    )


def test_feasibility_confirm_request_renders_only_the_summary_and_confirm_request_sections() -> None:
    rendered = generate_feasibility_propose(
        context(2),
        FeasibilityProposeContent(
            "针对调整后的速率保障目标，可行性评估已完成，结论为可行，请答复<评估可行时的确认请求>。",
            NegotiationAction.REQUEST_FEASIBILITY_EVALUATION,
            None,
            None,
            "评估目标可行，是否同意按照此目标继续执行？",
        ),
        zh_template(FEASIBILITY_NEGOTIATION_PROPOSE_URI),
        ZH,
    )

    assert (
        "## 可行性协商\n针对调整后的速率保障目标，可行性评估已完成，结论为可行，请答复<评估可行时的确认请求>。"
        in rendered
    )
    assert "## 评估可行时的确认请求\n评估目标可行，是否同意按照此目标继续执行？" in rendered
    assert "## 待评估内容说明" not in rendered
    assert "## 评估不可行时的详情和提案" not in rendered
    assert "要求：" not in rendered

    rendered_en = generate_feasibility_propose(
        context(2),
        FeasibilityProposeContent(
            "Regarding the adjusted rate guarantee target, the feasibility assessment has been completed and the "
            "conclusion is feasible. Please reply to <Feasible Evaluation Confirmation Request>.",
            NegotiationAction.REQUEST_FEASIBILITY_EVALUATION,
            None,
            None,
            "The target is assessed as feasible. Do you agree to proceed with this target?",
        ),
        en_template(FEASIBILITY_NEGOTIATION_PROPOSE_URI),
        EN,
    )

    assert (
        "## Feasible Evaluation Confirmation Request\nThe target is assessed as feasible. "
        "Do you agree to proceed with this target?" in rendered_en
    )
    assert "## Under Evaluation Description" not in rendered_en


@pytest.mark.parametrize(
    ("contents", "infeasibility"),
    [("contents", None), (None, "infeasibility"), ("contents", "infeasibility")],
    ids=["contents-only", "infeasibility-only", "both"],
)
def test_feasibility_confirm_request_combined_with_any_conditional_section_fails(
    contents: str | None, infeasibility: str | None
) -> None:
    content = FeasibilityProposeContent(
        "描述",
        NegotiationAction.REQUEST_FEASIBILITY_EVALUATION,
        _items_or_none(contents),
        _items_or_none(infeasibility),
        "评估目标可行，是否同意按照此目标继续执行？",
    )

    with pytest.raises(NegotiationGenerationError) as info:
        generate_feasibility_propose(context(2), content, zh_template(FEASIBILITY_NEGOTIATION_PROPOSE_URI), ZH)

    _assert_invalid_input(
        info.value,
        "Feasibility confirm request must not be combined with the contents to evaluate or "
        "infeasibility details and proposal sections; a confirm-request round carries only the "
        "summary and the confirm request.",
    )


def test_feasibility_confirm_request_with_the_wrong_action_fails() -> None:
    content = FeasibilityProposeContent(
        "描述",
        NegotiationAction.PROPOSE_ALTERNATIVE_ON_FAILURE,
        None,
        None,
        "评估目标可行，是否同意按照此目标继续执行？",
    )

    with pytest.raises(NegotiationGenerationError) as info:
        generate_feasibility_propose(context(2), content, zh_template(FEASIBILITY_NEGOTIATION_PROPOSE_URI), ZH)

    _assert_invalid_input(
        info.value,
        "Feasibility confirm request requires the REQUEST_FEASIBILITY_EVALUATION action but the "
        "content carries PROPOSE_ALTERNATIVE_ON_FAILURE.",
    )


# ---------------------------------------------------------------------------
# Ending family (shared generator)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("language", ["zh-CN", "en-US"])
@pytest.mark.parametrize("conclusion", [NegotiationConclusion.ACCEPT, NegotiationConclusion.REJECT])
def test_ending_generators_render_conclusion_literals(language: str, conclusion: NegotiationConclusion) -> None:
    vocabulary = ZH if language == "zh-CN" else EN
    literal = conclusion.literal
    info_conclusion = "## 信息协商结果" if language == "zh-CN" else "## Information Negotiation Result"
    info_content = "## 信息协商结果内容" if language == "zh-CN" else "## Information Negotiation Result Content"
    target_conclusion = "## 目标协商结果" if language == "zh-CN" else "## Target Negotiation Result"
    target_content = "## 目标协商结果内容" if language == "zh-CN" else "## Target Negotiation Result Content"
    feasibility_conclusion = "## 可行性协商结果" if language == "zh-CN" else "## Feasibility Negotiation Result"
    feasibility_content = (
        "## 可行性评估结果确认" if language == "zh-CN" else "## Feasibility Assessment Result Confirmation"
    )

    info_rendered = generate_ending(
        context(2, NegotiationPerformative.ACCEPT),
        InformationEndingContent(conclusion, [NegotiationItem("故障发生时间", "2026-08-19 10:30")]),
        ACCESS.template_text(INFORMATION_NEGOTIATION_ACCEPT_REJECT_URI, language),
        vocabulary,
    )
    other_literal = "Reject" if conclusion is NegotiationConclusion.ACCEPT else "Accept"
    list_colon = "：" if language == "zh-CN" else ": "
    assert f"{info_conclusion}\n{literal}" in info_rendered
    assert f"\n{other_literal}" not in info_rendered
    assert f"{info_content}\n1. 故障发生时间{list_colon}2026-08-19 10:30" in info_rendered

    target_result = (
        "双方就速率保障目标达成一致。"
        if conclusion is NegotiationConclusion.ACCEPT
        else "双方未就速率保障下限达成一致。"
    )
    target_ending = (
        TargetEndingContent(conclusion, target_result, None)
        if conclusion is NegotiationConclusion.ACCEPT
        else TargetEndingContent(conclusion, None, target_result)
    )
    target_rendered = generate_ending(
        context(2, NegotiationPerformative.REJECT),
        target_ending,
        ACCESS.template_text(TARGET_NEGOTIATION_ACCEPT_REJECT_URI, language),
        vocabulary,
    )
    assert f"{target_conclusion}\n{literal}" in target_rendered
    assert f"\n{other_literal}" not in target_rendered
    assert f"{target_content}\n{target_result}" in target_rendered

    feasibility_rendered = generate_ending(
        context(2),
        FeasibilityEndingContent(conclusion, "同意将速率保障目标由5Mbps下调至2Mbps。"),
        ACCESS.template_text(FEASIBILITY_NEGOTIATION_ACCEPT_REJECT_URI, language),
        vocabulary,
    )
    assert f"{feasibility_conclusion}\n{literal}" in feasibility_rendered
    assert f"\n{other_literal}" not in feasibility_rendered
    assert f"{feasibility_content}\n同意将速率保障目标由5Mbps下调至2Mbps。" in feasibility_rendered


@pytest.mark.parametrize("items", [None, []], ids=["none", "empty"])
def test_information_ending_rejects_empty_result_items(items: list[NegotiationItem] | None) -> None:
    content = InformationEndingContent(NegotiationConclusion.REJECT, items)

    with pytest.raises(NegotiationGenerationError) as info:
        generate_ending(context(2), content, zh_template(INFORMATION_NEGOTIATION_ACCEPT_REJECT_URI), ZH)

    _assert_content_invalid(info.value, "items", "Information negotiation terminal message result content")


@pytest.mark.parametrize("blank", [None, "", " "], ids=["none", "empty", "blank"])
def test_feasibility_ending_requires_the_summary(blank: str | None) -> None:
    content = FeasibilityEndingContent(NegotiationConclusion.ACCEPT, blank)

    with pytest.raises(NegotiationGenerationError) as info:
        generate_ending(context(1), content, zh_template(FEASIBILITY_NEGOTIATION_ACCEPT_REJECT_URI), ZH)

    _assert_content_invalid(
        info.value, "content.feasibilitySummary", "Feasibility summary of a terminal feasibility negotiation message"
    )


def test_feasibility_summary_uses_the_exception_slot_under_its_own_section_title() -> None:
    # The summary slot key (slot.feasibility_confirm -> 评估结果确认) differs from its section
    # title (可行性评估结果确认); the raw slot name must never surface as a section title.
    rendered = generate_ending(
        context(2),
        FeasibilityEndingContent(NegotiationConclusion.ACCEPT, "同意将速率保障目标由5Mbps下调至2Mbps。"),
        zh_template(FEASIBILITY_NEGOTIATION_ACCEPT_REJECT_URI),
        ZH,
    )

    assert "## 可行性评估结果确认\n同意将速率保障目标由5Mbps下调至2Mbps。" in rendered
    assert "## 评估结果确认" not in rendered
    assert rendered.count("同意将速率保障目标由5Mbps下调至2Mbps。") == 1


@pytest.mark.parametrize(
    ("conclusion", "field"),
    [
        (NegotiationConclusion.ACCEPT, "content.confirmedIntent"),
        (NegotiationConclusion.REJECT, "content.failureReason"),
    ],
    ids=["accept-without-intent", "reject-without-reason"],
)
def test_target_ending_requires_the_field_matching_the_conclusion(
    conclusion: NegotiationConclusion, field: str
) -> None:
    content = (
        TargetEndingContent(conclusion, None, "失败原因")
        if conclusion is NegotiationConclusion.ACCEPT
        else TargetEndingContent(conclusion, "  ", None)
    )

    with pytest.raises(NegotiationGenerationError) as info:
        generate_ending(context(1), content, zh_template(TARGET_NEGOTIATION_ACCEPT_REJECT_URI), ZH)

    assert info.value.code is ErrorCatalog.NEGOTIATION_CONTENT_INVALID
    assert info.value.facts["field"] == field


@pytest.mark.parametrize("language", ["zh-CN", "en-US"])
@pytest.mark.parametrize(
    ("conclusion", "field", "description"),
    [
        (
            NegotiationConclusion.ACCEPT,
            "content.confirmedIntent",
            "Confirmed intent of an accepting target negotiation message",
        ),
        (
            NegotiationConclusion.REJECT,
            "content.failureReason",
            "Failure reason of a rejecting target negotiation message",
        ),
    ],
    ids=["accept", "reject"],
)
def test_target_ending_blank_field_fails_with_the_exact_facts(
    language: str, conclusion: NegotiationConclusion, field: str, description: str
) -> None:
    vocabulary = ZH if language == "zh-CN" else EN
    content = (
        TargetEndingContent(conclusion, " ", None)
        if conclusion is NegotiationConclusion.ACCEPT
        else TargetEndingContent(conclusion, None, " ")
    )

    with pytest.raises(NegotiationGenerationError) as info:
        generate_ending(
            context(1), content, ACCESS.template_text(TARGET_NEGOTIATION_ACCEPT_REJECT_URI, language), vocabulary
        )

    _assert_content_invalid(info.value, field, description)


@pytest.mark.parametrize(
    "content",
    [
        InformationEndingContent(NegotiationConclusion.ABORT, []),
        TargetEndingContent(NegotiationConclusion.ABORT, "意图", None),
        FeasibilityEndingContent(NegotiationConclusion.ABORT, "结论"),
    ],
    ids=["information", "target", "feasibility"],
)
def test_abort_conclusion_is_rejected_by_every_ending_family(content: object) -> None:
    with pytest.raises(ValueError) as info:
        generate_ending(context(1), content, zh_template(INFORMATION_NEGOTIATION_ACCEPT_REJECT_URI), ZH)

    assert "Typed negotiation templates carry no Abort conclusion slot" in str(info.value)


def test_null_conclusion_is_rejected() -> None:
    content = InformationEndingContent(None, [])  # type: ignore[arg-type]

    with pytest.raises(TypeError, match="conclusion must not be null"):
        generate_ending(context(1), content, zh_template(INFORMATION_NEGOTIATION_ACCEPT_REJECT_URI), ZH)


# ---------------------------------------------------------------------------
# Abort generator
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("language", ["zh-CN", "en-US"])
def test_abort_keeps_the_fixed_abort_conclusion_section_and_renders_the_termination_reason(language: str) -> None:
    vocabulary = ZH if language == "zh-CN" else EN
    reason = "达到协商轮次上限，本次协商确认结束。" if language == "zh-CN" else "Reached the negotiation round limit."
    result_section = "## 协商结果" if language == "zh-CN" else "## Negotiation Result"
    reason_section = "## 协商终止原因" if language == "zh-CN" else "## Negotiation Termination Reason"

    rendered = generate_abort(
        context(5, NegotiationPerformative.ABORT),
        NegotiationAbortContent(reason),
        ACCESS.template_text(NEGOTIATION_ABORT_URI, language),
        vocabulary,
    )

    assert f"{result_section}\nAbort" in rendered
    assert f"{reason_section}\n{reason}" in rendered
    assert "{{" not in rendered
    assert not rendered.endswith("\n")


@pytest.mark.parametrize("blank", [None, "", " "], ids=["none", "empty", "blank"])
def test_abort_blank_termination_reason_is_rejected(blank: str | None) -> None:
    content = NegotiationAbortContent(blank)

    with pytest.raises(NegotiationGenerationError) as info:
        generate_abort(context(5, NegotiationPerformative.ABORT), content, zh_template(NEGOTIATION_ABORT_URI), ZH)

    _assert_content_invalid(
        info.value, "content.terminationReason", "Termination reason of an abort negotiation message"
    )


# ---------------------------------------------------------------------------
# Wrong runtime type (programming errors, outside the error tree)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("generator", "wrong_content", "expected_type"),
    [
        (generate_information_propose, "target", "InformationProposeContent"),
        (generate_target_propose, "information", "TargetProposeContent"),
        (generate_feasibility_propose, "target", "FeasibilityProposeContent"),
        (generate_abort, "target", "NegotiationAbortContent"),
    ],
    ids=["information-propose", "target-propose", "feasibility-propose", "abort"],
)
def test_generator_rejects_content_of_another_runtime_type(
    generator: NegotiationGenerator, wrong_content: str, expected_type: str
) -> None:
    content = {
        "target": TargetProposeContent("描述", None, None, None, None),
        "information": InformationProposeContent([NegotiationItem("名称", "值")], None),
    }[wrong_content]

    with pytest.raises(ValueError) as info:
        generator(context(1), content, "## 板块\n静态内容。", ZH)

    assert f"requires content of type {expected_type}" in str(info.value)
    assert not isinstance(info.value, A2ATBusinessError)


def test_ending_generator_rejects_content_of_another_runtime_type() -> None:
    content = TargetProposeContent("描述", None, None, None, None)

    with pytest.raises(ValueError) as info:
        generate_ending(context(1), content, "## 板块\n静态内容。", ZH)

    assert (
        "Ending generator requires content of type InformationEndingContent, TargetEndingContent or "
        "FeasibilityEndingContent but received TargetProposeContent" in str(info.value)
    )


@pytest.mark.parametrize("generator_name", ["information-propose", "target-propose", "feasibility-propose", "abort"])
def test_generator_rejects_none_content(generator_name: str) -> None:
    generator: NegotiationGenerator = {
        "information-propose": generate_information_propose,
        "target-propose": generate_target_propose,
        "feasibility-propose": generate_feasibility_propose,
        "abort": generate_abort,
    }[generator_name]

    with pytest.raises(ValueError, match="but received None"):
        generator(context(1), None, "## 板块\n静态内容。", ZH)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Language matrix of the conditional sections (port of ConditionalSectionRenderingTest)
# ---------------------------------------------------------------------------

_TITLES = {
    "zh-CN": {
        "evaluate": "## 待评估内容说明",
        "infeasible": "## 评估不可行时的详情和提案",
        "intent": "## 意图理解陈述",
        "alignment": "## 理解对齐与疑问澄清",
        "clarification": "## 待澄清内容",
        "target_confirm": "## 目标澄清后的确认请求",
        "feasibility_confirm": "## 评估可行时的确认请求",
    },
    "en-US": {
        "evaluate": "## Under Evaluation Description",
        "infeasible": "## Infeasible Evaluation Details and Proposal",
        "intent": "## Intent Understanding Statement",
        "alignment": "## Understanding Alignment and Clarification",
        "clarification": "## Content to Clarify",
        "target_confirm": "## Target Clarification Confirmation Request",
        "feasibility_confirm": "## Feasible Evaluation Confirmation Request",
    },
}


def _title(language: str, key: str) -> str:
    return _TITLES[language][key]


@pytest.mark.parametrize("language", ["zh-CN", "en-US"])
def test_evaluation_request_renders_only_the_contents_to_evaluate_section(language: str) -> None:
    vocabulary = ZH if language == "zh-CN" else EN
    rendered = generate_feasibility_propose(
        context(1),
        FeasibilityProposeContent(
            "Please assess the adjusted rate target.",
            NegotiationAction.REQUEST_FEASIBILITY_EVALUATION,
            [NegotiationItem("adjusted target", "rate lowered to 2Mbps")],
            None,
            None,
        ),
        ACCESS.template_text(FEASIBILITY_NEGOTIATION_PROPOSE_URI, language),
        vocabulary,
    )

    assert _title(language, "evaluate") in rendered
    assert _title(language, "infeasible") not in rendered


@pytest.mark.parametrize("language", ["zh-CN", "en-US"])
def test_alternative_proposal_renders_only_the_infeasibility_section(language: str) -> None:
    vocabulary = ZH if language == "zh-CN" else EN
    rendered = generate_feasibility_propose(
        context(2),
        FeasibilityProposeContent(
            "The rate target is infeasible; a proposal follows.",
            NegotiationAction.PROPOSE_ALTERNATIVE_ON_FAILURE,
            None,
            [NegotiationItem("proposal", "lower the rate guarantee target to 2Mbps")],
            None,
        ),
        ACCESS.template_text(FEASIBILITY_NEGOTIATION_PROPOSE_URI, language),
        vocabulary,
    )

    assert _title(language, "infeasible") in rendered
    assert _title(language, "evaluate") not in rendered


@pytest.mark.parametrize("language", ["zh-CN", "en-US"])
def test_first_round_renders_the_intent_section_without_the_alignment_section(language: str) -> None:
    vocabulary = ZH if language == "zh-CN" else EN
    rendered = generate_target_propose(
        context(1),
        TargetProposeContent(
            "Clarify the intent of the ran-energy-saving task.",
            [NegotiationItem("task intent", "ran-energy-saving optimization")],
            None,
            None,
            None,
        ),
        ACCESS.template_text(TARGET_NEGOTIATION_PROPOSE_URI, language),
        vocabulary,
    )

    assert _title(language, "intent") in rendered
    assert _title(language, "alignment") not in rendered


@pytest.mark.parametrize("language", ["zh-CN", "en-US"])
def test_later_rounds_render_the_alignment_section_without_the_intent_section(language: str) -> None:
    vocabulary = ZH if language == "zh-CN" else EN
    rendered = generate_target_propose(
        context(3),
        TargetProposeContent(
            "Clarify the intent of the ran-energy-saving task.",
            None,
            [NegotiationItem("task intent", "confirmed as correct")],
            None,
            None,
        ),
        ACCESS.template_text(TARGET_NEGOTIATION_PROPOSE_URI, language),
        vocabulary,
    )

    assert _title(language, "alignment") in rendered
    assert _title(language, "intent") not in rendered


@pytest.mark.parametrize("language", ["zh-CN", "en-US"])
def test_an_empty_clarification_list_drops_the_clarification_section(language: str) -> None:
    vocabulary = ZH if language == "zh-CN" else EN
    without_clarification = generate_target_propose(
        context(1),
        TargetProposeContent(
            "Confirm the understood intent of the ran-energy-saving task.",
            [NegotiationItem("task intent", "ran-energy-saving optimization")],
            None,
            None,
            None,
        ),
        ACCESS.template_text(TARGET_NEGOTIATION_PROPOSE_URI, language),
        vocabulary,
    )
    with_clarification = generate_target_propose(
        context(1),
        TargetProposeContent(
            "Confirm the understood intent of the ran-energy-saving task.",
            [NegotiationItem("task intent", "ran-energy-saving optimization")],
            None,
            [NegotiationItem("area", "which site is covered")],
            None,
        ),
        ACCESS.template_text(TARGET_NEGOTIATION_PROPOSE_URI, language),
        vocabulary,
    )

    assert _title(language, "clarification") not in without_clarification
    assert _title(language, "clarification") in with_clarification


@pytest.mark.parametrize("language", ["zh-CN", "en-US"])
@pytest.mark.parametrize(
    ("factory", "confirm_title_key", "confirm_wording"),
    [
        (
            lambda confirm: TargetProposeContent(
                "The clarification of the task target has been completed. Please reply to <Target"
                " Clarification Confirmation Request>.",
                None,
                None,
                None,
                confirm,
            ),
            "target_confirm",
            {
                "zh-CN": "目标已经澄清，是否同意按照此目标继续执行？",
                "en-US": "The target has been clarified. Do you agree to proceed with this target?",
            },
        ),
        (
            lambda confirm: FeasibilityProposeContent(
                "Regarding the adjusted rate guarantee target, the feasibility assessment has been"
                " completed and the conclusion is feasible. Please reply to <Feasible Evaluation"
                " Confirmation Request>.",
                NegotiationAction.REQUEST_FEASIBILITY_EVALUATION,
                None,
                None,
                confirm,
            ),
            "feasibility_confirm",
            {
                "zh-CN": "评估目标可行，是否同意按照此目标继续执行？",
                "en-US": "The target is assessed as feasible. Do you agree to proceed with this target?",
            },
        ),
    ],
    ids=["target", "feasibility"],
)
def test_a_confirm_request_round_renders_only_the_confirm_section(
    language: str,
    factory: Callable[[str], NegotiationContent],
    confirm_title_key: str,
    confirm_wording: dict[str, str],
) -> None:
    generator = generate_target_propose if confirm_title_key == "target_confirm" else generate_feasibility_propose
    template_uri = (
        TARGET_NEGOTIATION_PROPOSE_URI if confirm_title_key == "target_confirm" else FEASIBILITY_NEGOTIATION_PROPOSE_URI
    )

    rendered = generator(
        context(2),
        factory(confirm_wording[language]),
        ACCESS.template_text(template_uri, language),
        ZH if language == "zh-CN" else EN,
    )

    assert _title(language, confirm_title_key) in rendered
    assert confirm_wording[language] in rendered
    for title_key in ("intent", "alignment", "clarification", "evaluate", "infeasible"):
        assert _title(language, title_key) not in rendered


# ---------------------------------------------------------------------------
# Relationship line and list punctuation follow the message language
# (port of RelationshipLineRenderingTest)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("language", "label", "expected_relationship_line", "expected_item_line", "relationship"),
    [
        ("zh-CN", "缺失项之间的关系", "缺失项之间的关系：二选一", "1. 节能区域：松山湖", "二选一"),
        (
            "en-US",
            "Relationship between missing items",
            "Relationship between missing items: either-or",
            "1. area: Songshan Lake",
            "either-or",
        ),
    ],
    ids=["zh-CN", "en-US"],
)
def test_the_relationship_line_uses_the_language_label_and_colon(
    language: str,
    label: str,
    expected_relationship_line: str,
    expected_item_line: str,
    relationship: str,
) -> None:
    vocabulary = ZH if language == "zh-CN" else EN
    chinese = language == "zh-CN"
    item = NegotiationItem("节能区域" if chinese else "area", "松山湖" if chinese else "Songshan Lake")

    with_relationship = generate_information_propose(
        context(2),
        InformationProposeContent([item], relationship),
        ACCESS.template_text(INFORMATION_NEGOTIATION_PROPOSE_URI, language),
        vocabulary,
    )
    without_relationship = generate_information_propose(
        context(2),
        InformationProposeContent([item], None),
        ACCESS.template_text(INFORMATION_NEGOTIATION_PROPOSE_URI, language),
        vocabulary,
    )

    assert expected_relationship_line in with_relationship
    assert expected_item_line in with_relationship
    assert expected_item_line in without_relationship
    assert label not in without_relationship
    # The other language's label never leaks into the rendered message.
    other_label = "Relationship between missing items" if chinese else "缺失项之间的关系"
    assert other_label not in with_relationship


@pytest.mark.parametrize(
    ("language", "expected_line", "forbidden_line"),
    [
        ("zh-CN", "1. 节能区域：松山湖", "1. 节能区域: 松山湖"),
        ("en-US", "1. area: Songshan Lake", "1. area：Songshan Lake"),
    ],
    ids=["zh-CN-full-width-colon", "en-US-colon-space"],
)
def test_the_numbered_item_lines_use_the_list_colon_of_the_language(
    language: str, expected_line: str, forbidden_line: str
) -> None:
    chinese = language == "zh-CN"
    rendered = generate_information_propose(
        context(2),
        InformationProposeContent(
            [NegotiationItem("节能区域" if chinese else "area", "松山湖" if chinese else "Songshan Lake")], None
        ),
        ACCESS.template_text(INFORMATION_NEGOTIATION_PROPOSE_URI, language),
        ZH if chinese else EN,
    )

    assert expected_line in rendered
    assert forbidden_line not in rendered


# ---------------------------------------------------------------------------
# Conclusion literal and feasibility exception slot (port of
# ConclusionLiteralAndExceptionSlotTest, generator-level rows)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("language", ["zh-CN", "en-US"])
@pytest.mark.parametrize("conclusion", [NegotiationConclusion.ACCEPT, NegotiationConclusion.REJECT])
def test_the_feasibility_summary_never_surfaces_the_raw_slot_name(
    language: str, conclusion: NegotiationConclusion
) -> None:
    summary = "The adjusted target is achievable; this negotiation is confirmed as concluded."
    section_title = "## 可行性评估结果确认" if language == "zh-CN" else "## Feasibility Assessment Result Confirmation"
    raw_slot_line = "## 评估结果确认\n" if language == "zh-CN" else "## evaluation_result_confirmation\n"
    performative = (
        NegotiationPerformative.ACCEPT if conclusion is NegotiationConclusion.ACCEPT else NegotiationPerformative.REJECT
    )

    rendered = generate_ending(
        context(2, performative),
        FeasibilityEndingContent(conclusion, summary),
        ACCESS.template_text(FEASIBILITY_NEGOTIATION_ACCEPT_REJECT_URI, language),
        ZH if language == "zh-CN" else EN,
    )

    assert f"{section_title}\n{summary}" in rendered
    assert raw_slot_line not in rendered, "the raw slot name must not surface as a section title"
    assert rendered.count(summary) == 1, "the summary is carried verbatim as the only body line"
    assert f"\n{conclusion.literal}" in rendered


def _items_or_none(name: str | None) -> list[NegotiationItem] | None:
    """Build a one-item list for a section name, or ``None`` for a ``None`` name."""
    return None if name is None else [NegotiationItem(name, "值")]


def _assert_content_invalid(error: NegotiationGenerationError, field: str, description: str) -> None:
    """Assert the exact ``negotiation.content_invalid`` failure contract of one required field."""
    assert isinstance(error, NegotiationGenerationError)
    assert isinstance(error, A2ATBusinessError)
    assert error.code is ErrorCatalog.NEGOTIATION_CONTENT_INVALID
    assert error.code_str == "negotiation.content_invalid"
    assert error.facts["field"] == field
    assert error.facts["reason"] == f"{description} must not be blank." or error.facts["reason"] == (
        f"{description} must contain at least one item."
    )
    assert field in str(error)


def _assert_invalid_input(error: NegotiationGenerationError, reason: str) -> None:
    """Assert the exact ``negotiation.invalid_input`` failure contract of one reason sentence."""
    assert error.code is ErrorCatalog.NEGOTIATION_INVALID_INPUT
    assert error.code_str == "negotiation.invalid_input"
    assert error.facts == {"reason": reason}
    assert reason in str(error)
