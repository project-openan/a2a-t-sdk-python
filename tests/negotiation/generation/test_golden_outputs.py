"""Golden byte-fidelity tests of the deterministic from-data generation.

The expected texts are the verbatim golden corpus bytes of the Java repository
(``a2a-t-corpus/src/test/resources/golden/<lang>/*.md``, LF-normalized), and the inputs are the
corresponding ``from-data/happy.json`` corpus records — the P5 acceptance criterion "golden 输出
与 Java golden 逐字节一致（LF 归一后）" pinned repo-locally ahead of the P8 corpus stage. Every case
drives the real bundled templates through the packaged resource access (D31), so a template drift
or a renderer regression breaks the byte equality, not just a containment assertion.
"""

from __future__ import annotations

from collections.abc import Callable

import pytest

from a2a_t.common.prompt_resources.resource_access import PackagedPromptResourceAccess
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
    NegotiationItem,
    TargetEndingContent,
    TargetProposeContent,
    Vocabulary,
)
from a2a_t.negotiation.generation import (
    generate_abort,
    generate_ending,
    generate_feasibility_propose,
    generate_information_propose,
    generate_target_propose,
)

SESSION_ID = "3dbc13b5-bd57-4c2b-b503-24e381b6c8d3"

ACCESS = PackagedPromptResourceAccess()


def _context(round: int, performative: NegotiationPerformative) -> NegotiationContext:
    return NegotiationContext(SESSION_ID, round, 5, performative)


def _items(*pairs: tuple[str, str | None]) -> list[NegotiationItem]:
    return [NegotiationItem(name, value) for name, value in pairs]


#: One golden case: the render call of one generator and the exact expected text.
class _Case:
    __slots__ = ("id", "language", "call")

    def __init__(
        self,
        case_id: str,
        language: str,
        call: Callable[[], str],
    ) -> None:
        self.id = case_id
        self.language = language
        self.call = call


def _zh(language: str) -> Vocabulary:
    return Vocabulary.for_language(language)


def _template(uri: str, language: str) -> str:
    return ACCESS.template_text(uri, language)


def _case(case_id: str, language: str, call: Callable[[], str]) -> _Case:
    return _Case(case_id, language, call)


GOLDENS: list[_Case] = [
    _case(
        "abort",
        "zh-CN",
        lambda: generate_abort(
            _context(5, NegotiationPerformative.ABORT),
            NegotiationAbortContent("已达到协商轮次上限，本次协商确认终止。"),
            _template(NEGOTIATION_ABORT_URI, "zh-CN"),
            _zh("zh-CN"),
        ),
    ),
    _case(
        "abort",
        "en-US",
        lambda: generate_abort(
            _context(5, NegotiationPerformative.ABORT),
            NegotiationAbortContent(
                "The negotiation round limit is reached; this negotiation is confirmed as terminated."
            ),
            _template(NEGOTIATION_ABORT_URI, "en-US"),
            _zh("en-US"),
        ),
    ),
    _case(
        "information_propose",
        "zh-CN",
        lambda: generate_information_propose(
            _context(2, NegotiationPerformative.PROPOSE),
            InformationProposeContent(
                _items(
                    ("接入端口名称", "举例：P533-珠江旧城-PTN3900-23-TPA1EG24-1"),
                    ("投诉分类", "举例：专线质差"),
                    ("专线业务标识", None),
                ),
                "OR",
            ),
            _template(INFORMATION_NEGOTIATION_PROPOSE_URI, "zh-CN"),
            _zh("zh-CN"),
        ),
    ),
    _case(
        "information_propose",
        "en-US",
        lambda: generate_information_propose(
            _context(2, NegotiationPerformative.PROPOSE),
            InformationProposeContent(
                _items(
                    ("Access Port Name", "e.g. P533-Zhujiang Old Town-PTN3900-23-TPA1EG24-1"),
                    ("Complaint Category", "e.g. dedicated-line quality degradation"),
                    ("Private Line Service Identifier", None),
                ),
                "OR",
            ),
            _template(INFORMATION_NEGOTIATION_PROPOSE_URI, "en-US"),
            _zh("en-US"),
        ),
    ),
    _case(
        "information_accept",
        "zh-CN",
        lambda: generate_ending(
            _context(2, NegotiationPerformative.ACCEPT),
            InformationEndingContent(
                NegotiationConclusion.ACCEPT,
                _items(
                    ("接入端口名称", "P533-珠江旧城-PTN3900-23-TPA1EG24-1"),
                    ("投诉分类", "专线质差"),
                ),
            ),
            _template(INFORMATION_NEGOTIATION_ACCEPT_REJECT_URI, "zh-CN"),
            _zh("zh-CN"),
        ),
    ),
    _case(
        "target_propose",
        "zh-CN",
        lambda: generate_target_propose(
            _context(1, NegotiationPerformative.PROPOSE),
            TargetProposeContent(
                "专线质差投诉的修复目标协商意图见<意图理解陈述>；时延目标与完成时限仍存在待澄清问题，见<待澄清内容>；请澄清并确认。",
                _items(
                    ("修复意图", "在2026年5月15日前将深圳至广州专线的平均时延恢复至20ms以内"),
                    ("修复目标", "高峰时段丢包率不高于1%"),
                ),
                None,
                _items(
                    ("时延目标", "平均时延目标是恢复至20ms以内还是50ms以内"),
                    ("完成时限", "修复完成时限是48小时内还是72小时内"),
                ),
                None,
            ),
            _template(TARGET_NEGOTIATION_PROPOSE_URI, "zh-CN"),
            _zh("zh-CN"),
        ),
    ),
    _case(
        "target_propose",
        "en-US",
        lambda: generate_target_propose(
            _context(1, NegotiationPerformative.PROPOSE),
            TargetProposeContent(
                "The intent understanding of the dedicated-line quality degradation repair target is listed in "
                "<Intent Understanding Statement>; open questions remain about the latency target and the completion "
                "deadline, see <Content to Clarify>; please clarify and confirm.",
                _items(
                    (
                        "repair intent",
                        "restore the average latency of the Shenzhen-to-Guangzhou dedicated line to within 20ms before 2026-05-15",
                    ),
                    ("repair target", "peak-hour packet loss rate no higher than 1%"),
                ),
                None,
                _items(
                    ("latency target", "is the average latency target within 20ms or within 50ms"),
                    ("completion deadline", "is the repair deadline 48 hours or 72 hours"),
                ),
                None,
            ),
            _template(TARGET_NEGOTIATION_PROPOSE_URI, "en-US"),
            _zh("en-US"),
        ),
    ),
    _case(
        "target_propose_confirm",
        "zh-CN",
        lambda: generate_target_propose(
            _context(2, NegotiationPerformative.PROPOSE),
            TargetProposeContent(
                "任务目标澄清完成，请答复<目标澄清后的确认请求>。",
                None,
                None,
                None,
                "目标已经澄清，是否同意按照此目标继续执行？",
            ),
            _template(TARGET_NEGOTIATION_PROPOSE_URI, "zh-CN"),
            _zh("zh-CN"),
        ),
    ),
    _case(
        "target_accept",
        "zh-CN",
        lambda: generate_ending(
            _context(2, NegotiationPerformative.ACCEPT),
            TargetEndingContent(
                NegotiationConclusion.ACCEPT,
                "最终确认的意图：在2026年5月15日前将深圳至广州专线的平均时延恢复至20ms以内，高峰时段丢包率不高于1%。",
                None,
            ),
            _template(TARGET_NEGOTIATION_ACCEPT_REJECT_URI, "zh-CN"),
            _zh("zh-CN"),
        ),
    ),
    _case(
        "feasibility_propose",
        "zh-CN",
        lambda: generate_feasibility_propose(
            _context(1, NegotiationPerformative.PROPOSE),
            FeasibilityProposeContent(
                "请评估专线接入端口扩容方案能否在割接窗口内完成，见<待评估内容说明>；请评估。",
                NegotiationAction.REQUEST_FEASIBILITY_EVALUATION,
                _items(
                    ("扩容方案", "接入端口带宽由100Mbps扩容至1000Mbps"),
                    ("既有约束", "割接窗口仅限2026年5月30日02:00-06:00，业务中断不超过30分钟"),
                ),
                None,
                None,
            ),
            _template(FEASIBILITY_NEGOTIATION_PROPOSE_URI, "zh-CN"),
            _zh("zh-CN"),
        ),
    ),
    _case(
        "feasibility_propose_confirm",
        "zh-CN",
        lambda: generate_feasibility_propose(
            _context(2, NegotiationPerformative.PROPOSE),
            FeasibilityProposeContent(
                "针对调整后的速率保障目标，可行性评估已完成，结论为可行，请答复<评估可行时的确认请求>。",
                NegotiationAction.REQUEST_FEASIBILITY_EVALUATION,
                None,
                None,
                "评估目标可行，是否同意按照此目标继续执行？",
            ),
            _template(FEASIBILITY_NEGOTIATION_PROPOSE_URI, "zh-CN"),
            _zh("zh-CN"),
        ),
    ),
    _case(
        "feasibility_propose_confirm",
        "en-US",
        lambda: generate_feasibility_propose(
            _context(2, NegotiationPerformative.PROPOSE),
            FeasibilityProposeContent(
                "Regarding the adjusted rate guarantee target, the feasibility assessment has been completed and the "
                "conclusion is feasible. Please reply to <Feasible Evaluation Confirmation Request>.",
                NegotiationAction.REQUEST_FEASIBILITY_EVALUATION,
                None,
                None,
                "The target is assessed as feasible. Do you agree to proceed with this target?",
            ),
            _template(FEASIBILITY_NEGOTIATION_PROPOSE_URI, "en-US"),
            _zh("en-US"),
        ),
    ),
    _case(
        "feasibility_accept",
        "zh-CN",
        lambda: generate_ending(
            _context(2, NegotiationPerformative.ACCEPT),
            FeasibilityEndingContent(
                NegotiationConclusion.ACCEPT,
                "端口扩容方案可在割接窗口内完成，业务中断时长满足不超过30分钟的约束；本次可行性协商确认结束。",
            ),
            _template(FEASIBILITY_NEGOTIATION_ACCEPT_REJECT_URI, "zh-CN"),
            _zh("zh-CN"),
        ),
    ),
]

#: The verbatim Java golden bytes (LF-normalized), keyed by ``(case id, language)``.
EXPECTED: dict[tuple[str, str], str] = {
    ("abort", "zh-CN"): ("## 协商结果\nAbort\n\n## 协商终止原因\n已达到协商轮次上限，本次协商确认终止。"),
    ("abort", "en-US"): (
        "## Negotiation Result\n"
        "Abort\n"
        "\n"
        "## Negotiation Termination Reason\n"
        "The negotiation round limit is reached; this negotiation is confirmed as terminated."
    ),
    ("information_propose", "zh-CN"): (
        "## 信息协商\n"
        "请根据[所需信息项](#所需信息项)补充相关内容。\n"
        "\n"
        "## 所需信息项\n"
        "1. 接入端口名称：举例：P533-珠江旧城-PTN3900-23-TPA1EG24-1\n"
        "2. 投诉分类：举例：专线质差\n"
        "3. 专线业务标识\n"
        "缺失项之间的关系：OR"
    ),
    ("information_propose", "en-US"): (
        "## Information Negotiation\n"
        "Please supplement the relevant content based on [Required Information Items](#required-information-items).\n"
        "\n"
        "## Required Information Items\n"
        "1. Access Port Name: e.g. P533-Zhujiang Old Town-PTN3900-23-TPA1EG24-1\n"
        "2. Complaint Category: e.g. dedicated-line quality degradation\n"
        "3. Private Line Service Identifier\n"
        "Relationship between missing items: OR"
    ),
    ("information_accept", "zh-CN"): (
        "## 信息协商结果\n"
        "Accept\n"
        "\n"
        "## 信息协商结果内容\n"
        "1. 接入端口名称：P533-珠江旧城-PTN3900-23-TPA1EG24-1\n"
        "2. 投诉分类：专线质差"
    ),
    ("target_propose", "zh-CN"): (
        "## 目标协商\n"
        "专线质差投诉的修复目标协商意图见<意图理解陈述>；时延目标与完成时限仍存在待澄清问题，见<待澄清内容>；请澄清并确认。\n"
        "\n"
        "## 意图理解陈述\n"
        "1. 修复意图：在2026年5月15日前将深圳至广州专线的平均时延恢复至20ms以内\n"
        "2. 修复目标：高峰时段丢包率不高于1%\n"
        "\n"
        "## 待澄清内容\n"
        "1. 时延目标：平均时延目标是恢复至20ms以内还是50ms以内\n"
        "2. 完成时限：修复完成时限是48小时内还是72小时内"
    ),
    ("target_propose", "en-US"): (
        "## Target Negotiation\n"
        "The intent understanding of the dedicated-line quality degradation repair target is listed in <Intent "
        "Understanding Statement>; open questions remain about the latency target and the completion deadline, see "
        "<Content to Clarify>; please clarify and confirm.\n"
        "\n"
        "## Intent Understanding Statement\n"
        "1. repair intent: restore the average latency of the Shenzhen-to-Guangzhou dedicated line to within 20ms "
        "before 2026-05-15\n"
        "2. repair target: peak-hour packet loss rate no higher than 1%\n"
        "\n"
        "## Content to Clarify\n"
        "1. latency target: is the average latency target within 20ms or within 50ms\n"
        "2. completion deadline: is the repair deadline 48 hours or 72 hours"
    ),
    ("target_propose_confirm", "zh-CN"): (
        "## 目标协商\n"
        "任务目标澄清完成，请答复<目标澄清后的确认请求>。\n"
        "\n"
        "## 目标澄清后的确认请求\n"
        "目标已经澄清，是否同意按照此目标继续执行？"
    ),
    ("target_accept", "zh-CN"): (
        "## 目标协商结果\n"
        "Accept\n"
        "\n"
        "## 目标协商结果内容\n"
        "最终确认的意图：在2026年5月15日前将深圳至广州专线的平均时延恢复至20ms以内，高峰时段丢包率不高于1%。"
    ),
    ("feasibility_propose", "zh-CN"): (
        "## 可行性协商\n"
        "请评估专线接入端口扩容方案能否在割接窗口内完成，见<待评估内容说明>；请评估。\n"
        "\n"
        "## 待评估内容说明\n"
        "1. 扩容方案：接入端口带宽由100Mbps扩容至1000Mbps\n"
        "2. 既有约束：割接窗口仅限2026年5月30日02:00-06:00，业务中断不超过30分钟"
    ),
    ("feasibility_propose_confirm", "zh-CN"): (
        "## 可行性协商\n"
        "针对调整后的速率保障目标，可行性评估已完成，结论为可行，请答复<评估可行时的确认请求>。\n"
        "\n"
        "## 评估可行时的确认请求\n"
        "评估目标可行，是否同意按照此目标继续执行？"
    ),
    ("feasibility_propose_confirm", "en-US"): (
        "## Feasibility Negotiation\n"
        "Regarding the adjusted rate guarantee target, the feasibility assessment has been completed and the "
        "conclusion is feasible. Please reply to <Feasible Evaluation Confirmation Request>.\n"
        "\n"
        "## Feasible Evaluation Confirmation Request\n"
        "The target is assessed as feasible. Do you agree to proceed with this target?"
    ),
    ("feasibility_accept", "zh-CN"): (
        "## 可行性协商结果\n"
        "Accept\n"
        "\n"
        "## 可行性评估结果确认\n"
        "端口扩容方案可在割接窗口内完成，业务中断时长满足不超过30分钟的约束；本次可行性协商确认结束。"
    ),
}


@pytest.mark.parametrize(
    ("case",),
    [(case,) for case in GOLDENS],
    ids=[f"{case.id}-{case.language}" for case in GOLDENS],
)
def test_from_data_render_is_byte_identical_to_the_java_golden(case: _Case) -> None:
    assert case.call() == EXPECTED[(case.id, case.language)]


def test_every_pinned_case_carries_its_expected_text() -> None:
    assert {(case.id, case.language) for case in GOLDENS} == set(EXPECTED)


def test_the_golden_table_covers_all_five_generators() -> None:
    # One entry per generator family: abort, information propose / ending, target propose
    # (plain + confirm + ending) and feasibility propose (plain + confirm + ending).
    ids = {case.id for case in GOLDENS}

    assert {
        "abort",
        "information_propose",
        "information_accept",
        "target_propose",
        "target_propose_confirm",
        "target_accept",
        "feasibility_propose",
        "feasibility_propose_confirm",
        "feasibility_accept",
    } <= ids


def test_no_golden_text_ends_with_a_newline() -> None:
    assert all(not text.endswith("\n") for text in EXPECTED.values())
