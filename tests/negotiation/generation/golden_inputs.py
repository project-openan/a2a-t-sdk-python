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

"""Fixed inputs of the golden fixture set of the negotiation content layer.

Port of Java ``a2a-t-corpus`` ``golden/GoldenInputs.java`` (the negotiation-module-local copy is
``NegotiationGoldenCases``): every golden fixture is rendered from exactly one
:class:`GoldenCase` carrying a fixed negotiation context, fixed typed content and the built-in
template URI of its negotiation type and performative. The fixture data lives in the private-line
complaint diagnosis business domain: the information fixtures carry the access port name and
complaint category of the OMC workbench complaint loop, the target fixtures negotiate the latency
repair target, and the feasibility fixtures assess the port expansion. The typed content is
language-dependent — zh-CN fixtures carry Chinese business data, en-US fixtures the English
business form — so each language renders its own golden fixture with real business data in that
language.

The fixture data deliberately exercises the known rendering pitfalls: a non-null relationship with
an appended line and a null-value item (information propose), the round-driven conditional sections
with a non-empty clarification list (target propose, round 1), the action-driven conditional section
(feasibility propose, evaluation request action), the confirm-request category rendering only the
summary and the fixed confirm request (target and feasibility propose confirm), the accept and
reject conclusion literals, the feasibility summary rendered into the vocabulary-exception slot of
the feasibility result confirmation section, and the common abort template rendering the
termination reason.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from a2a_t.core.metadata import MetadataContent, NegotiationContext, NegotiationPerformative
from a2a_t.core.standard_templates import (
    FEASIBILITY_NEGOTIATION_ACCEPT_REJECT_URI,
    FEASIBILITY_NEGOTIATION_PROPOSE_URI,
    INFORMATION_NEGOTIATION_ACCEPT_REJECT_URI,
    INFORMATION_NEGOTIATION_PROPOSE_URI,
    NEGOTIATION_ABORT_URI,
    TARGET_NEGOTIATION_ACCEPT_REJECT_URI,
    TARGET_NEGOTIATION_PROPOSE_URI,
)
from a2a_t.core.template_uri import TemplateUri
from a2a_t.negotiation.content import (
    FeasibilityEndingContent,
    FeasibilityProposeContent,
    InformationEndingContent,
    InformationProposeContent,
    NegotiationAbortContent,
    NegotiationAbortData,
    NegotiationAction,
    NegotiationConclusion,
    NegotiationContent,
    NegotiationEndingData,
    NegotiationItem,
    NegotiationProposeData,
    TargetEndingContent,
    TargetProposeContent,
)
from a2a_t.negotiation.generation import NegotiationGenerationOrchestrator
from a2a_t.negotiation.generation.builder import builder

#: Fixed negotiation session id shared by every golden fixture.
SESSION_ID = "3dbc13b5-bd57-4c2b-b503-24e381b6c8d3"

#: Language with bundled zh-CN negotiation resources.
ZH_CN = "zh-CN"

#: Language with bundled en-US negotiation resources.
EN_US = "en-US"

#: Both languages covered by the golden fixture set, in a fixed order.
LANGUAGES = (ZH_CN, EN_US)

#: Root of the committed golden fixture files (LF-normalized copies of the Java corpus fixtures).
GOLDEN_ROOT = Path(__file__).parents[2] / "resources" / "negotiation-cases" / "golden"


def default_context(performative: NegotiationPerformative) -> NegotiationContext:
    """Return the default fixture context: round 2 of at most 5 rounds, stamped with the performative."""
    return NegotiationContext(SESSION_ID, 2, 5, performative)


def first_round_context(performative: NegotiationPerformative) -> NegotiationContext:
    """Return the first-round fixture context used by the target propose fixture."""
    return NegotiationContext(SESSION_ID, 1, 5, performative)


def _items(*pairs: tuple[str, str | None]) -> list[NegotiationItem]:
    return [NegotiationItem(name, value) for name, value in pairs]


def _information_propose(language: str) -> NegotiationContent:
    """Information propose fixture: the OMC asks the workbench for the missing access port name and
    complaint category, plus an optional null-value private line service identifier."""
    if language == EN_US:
        return InformationProposeContent(
            _items(
                ("Access Port Name", "e.g. P533-Zhujiang Old Town-PTN3900-23-TPA1EG24-1"),
                ("Complaint Category", "e.g. dedicated-line quality degradation"),
                ("Private Line Service Identifier", None),
            ),
            "OR",
        )
    return InformationProposeContent(
        _items(
            ("接入端口名称", "举例：P533-珠江旧城-PTN3900-23-TPA1EG24-1"),
            ("投诉分类", "举例：专线质差"),
            ("专线业务标识", None),
        ),
        "OR",
    )


def _target_propose(language: str) -> NegotiationContent:
    """Target propose fixture of round 1: the workbench and the OMC clarify the latency repair
    target and the completion deadline of the quality degradation complaint."""
    if language == EN_US:
        return TargetProposeContent(
            "The intent understanding of the dedicated-line quality degradation repair target is"
            " listed in <Intent Understanding Statement>; open questions remain about the"
            " latency target and the completion deadline, see <Content to Clarify>; please"
            " clarify and confirm.",
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
        )
    return TargetProposeContent(
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
    )


def _feasibility_propose(language: str) -> NegotiationContent:
    """Feasibility propose fixture requesting a feasibility evaluation of the access port bandwidth
    expansion against the cutover window constraint."""
    if language == EN_US:
        return FeasibilityProposeContent(
            "Please assess whether the dedicated-line access port expansion can be completed within"
            " the cutover window, see <Under Evaluation Description>; please assess.",
            NegotiationAction.REQUEST_FEASIBILITY_EVALUATION,
            _items(
                ("expansion plan", "expand the access port bandwidth from 100Mbps to 1000Mbps"),
                (
                    "existing constraint",
                    "the cutover window is limited to 02:00-06:00 on 2026-05-30 with"
                    " service interruption no longer than 30 minutes",
                ),
            ),
            None,
            None,
        )
    return FeasibilityProposeContent(
        "请评估专线接入端口扩容方案能否在割接窗口内完成，见<待评估内容说明>；请评估。",
        NegotiationAction.REQUEST_FEASIBILITY_EVALUATION,
        _items(
            ("扩容方案", "接入端口带宽由100Mbps扩容至1000Mbps"),
            ("既有约束", "割接窗口仅限2026年5月30日02:00-06:00，业务中断不超过30分钟"),
        ),
        None,
        None,
    )


def _target_propose_confirm(language: str) -> NegotiationContent:
    """Target propose confirm-request fixture of a later round: the clarification is complete, so
    the message carries only the summary and the fixed confirm request."""
    if language == EN_US:
        return TargetProposeContent(
            "The clarification of the task target has been completed. Please reply to <Target"
            " Clarification Confirmation Request>.",
            None,
            None,
            None,
            "The target has been clarified. Do you agree to proceed with this target?",
        )
    return TargetProposeContent(
        "任务目标澄清完成，请答复<目标澄清后的确认请求>。",
        None,
        None,
        None,
        "目标已经澄清，是否同意按照此目标继续执行？",
    )


def _feasibility_propose_confirm(language: str) -> NegotiationContent:
    """Feasibility propose confirm-request fixture of the "assessed as feasible and requesting
    confirmation" category: the assessment is complete."""
    if language == EN_US:
        return FeasibilityProposeContent(
            "Regarding the adjusted rate guarantee target, the feasibility assessment has been"
            " completed and the conclusion is feasible. Please reply to <Feasible Evaluation"
            " Confirmation Request>.",
            NegotiationAction.REQUEST_FEASIBILITY_EVALUATION,
            None,
            None,
            "The target is assessed as feasible. Do you agree to proceed with this target?",
        )
    return FeasibilityProposeContent(
        "针对调整后的速率保障目标，可行性评估已完成，结论为可行，请答复<评估可行时的确认请求>。",
        NegotiationAction.REQUEST_FEASIBILITY_EVALUATION,
        None,
        None,
        "评估目标可行，是否同意按照此目标继续执行？",
    )


def _information_accept(language: str) -> NegotiationContent:
    """Information accept fixture delivering the access port name and the complaint category."""
    if language == EN_US:
        return InformationEndingContent(
            NegotiationConclusion.ACCEPT,
            _items(
                ("Access Port Name", "P533-Zhujiang Old Town-PTN3900-23-TPA1EG24-1"),
                ("Complaint Category", "dedicated-line quality degradation"),
            ),
        )
    return InformationEndingContent(
        NegotiationConclusion.ACCEPT,
        _items(
            ("接入端口名称", "P533-珠江旧城-PTN3900-23-TPA1EG24-1"),
            ("投诉分类", "专线质差"),
        ),
    )


def _target_accept(language: str) -> NegotiationContent:
    """Target accept fixture carrying the finally confirmed latency repair target."""
    if language == EN_US:
        return TargetEndingContent(
            NegotiationConclusion.ACCEPT,
            "The finally confirmed intent: restore the average latency of the"
            " Shenzhen-to-Guangzhou dedicated line to within 20ms before 2026-05-15, with a"
            " peak-hour packet loss rate no higher than 1%.",
            None,
        )
    return TargetEndingContent(
        NegotiationConclusion.ACCEPT,
        "最终确认的意图：在2026年5月15日前将深圳至广州专线的平均时延恢复至20ms以内，高峰时段丢包率不高于1%。",
        None,
    )


def _feasibility_accept(language: str) -> NegotiationContent:
    """Feasibility accept fixture confirming a positive port expansion assessment in the exception slot."""
    if language == EN_US:
        return FeasibilityEndingContent(
            NegotiationConclusion.ACCEPT,
            "The port expansion can be completed within the cutover window and satisfies the"
            " 30-minute interruption constraint; this negotiation is confirmed as concluded.",
        )
    return FeasibilityEndingContent(
        NegotiationConclusion.ACCEPT,
        "端口扩容方案可在割接窗口内完成，业务中断时长满足不超过30分钟的约束；本次可行性协商确认结束。",
    )


def _information_reject(language: str) -> NegotiationContent:
    """Information reject fixture stating that the access port name cannot be provided."""
    if language == EN_US:
        return InformationEndingContent(
            NegotiationConclusion.REJECT,
            _items(
                (
                    "Access Port Name",
                    "cannot be provided because the port inventory is temporarily unavailable on the workbench side",
                )
            ),
        )
    return InformationEndingContent(
        NegotiationConclusion.REJECT,
        _items(("接入端口名称", "无法提供，工作台侧端口资源台账暂不可查")),
    )


def _target_reject(language: str) -> NegotiationContent:
    """Target reject fixture carrying the failure reason of the unclarified latency target."""
    if language == EN_US:
        return TargetEndingContent(
            NegotiationConclusion.REJECT,
            None,
            "The latency target cannot be clarified in full because the fiber cutover window is not confirmed yet.",
        )
    return TargetEndingContent(NegotiationConclusion.REJECT, None, "因光缆割接窗口尚未确认，时延目标未能完全澄清。")


def _feasibility_reject(language: str) -> NegotiationContent:
    """Feasibility reject fixture confirming a negative port expansion assessment in the exception slot."""
    if language == EN_US:
        return FeasibilityEndingContent(
            NegotiationConclusion.REJECT,
            "The port expansion cannot be completed within the designated cutover window because"
            " of insufficient board slots in the aggregation room; this negotiation is"
            " confirmed as concluded.",
        )
    return FeasibilityEndingContent(
        NegotiationConclusion.REJECT,
        "受汇聚机房板卡槽位不足限制，端口扩容无法在指定割接窗口内完成；本次可行性协商确认结束。",
    )


def _abort(language: str) -> NegotiationContent:
    """Common abort fixture terminating the negotiation at the round limit."""
    if language == EN_US:
        return NegotiationAbortContent(
            "The negotiation round limit is reached; this negotiation is confirmed as terminated."
        )
    return NegotiationAbortContent("已达到协商轮次上限，本次协商确认终止。")


class GoldenCase:
    """One golden fixture case: one negotiation type, performative, fixed context, fixed content and
    its template URI."""

    __slots__ = (
        "name",
        "performative",
        "template_uri",
        "file_name",
        "_context_factory",
        "_content_factory",
    )

    def __init__(
        self,
        name: str,
        performative: NegotiationPerformative,
        template_uri: str,
        file_name: str,
        context_factory: Callable[[NegotiationPerformative], NegotiationContext],
        content_factory: Callable[[str], NegotiationContent],
    ) -> None:
        self.name = name
        self.performative = performative
        self.template_uri = template_uri
        self.file_name = file_name
        self._context_factory = context_factory
        self._content_factory = content_factory

    @property
    def template(self) -> TemplateUri:
        """The built-in template URI addressed by this fixture as a typed value."""
        return TemplateUri.parse(self.template_uri) or TemplateUri.of("Negotiation-T", "unknown", "unknown")

    def context(self) -> NegotiationContext:
        """The fixed negotiation context of this fixture: the fixture context stamped with the
        fixture performative (the pipeline stamps the performative of the addressed template onto
        the emitted context, so the fixed context compares equal to the emitted one)."""
        return self._context_factory(self.performative)

    def content(self, language: str) -> NegotiationContent:
        """The fixed typed content of this fixture in the given language."""
        return self._content_factory(language)

    def golden_resource_path(self, language: str) -> Path:
        """The path of the committed golden fixture file of this case for one language."""
        return GOLDEN_ROOT / language / self.file_name

    def generate(self, orchestrator: NegotiationGenerationOrchestrator, language: str) -> MetadataContent:
        """Generate this fixture through one orchestrator, using the from-data method of the
        fixture performative."""
        if self.performative is NegotiationPerformative.PROPOSE:
            return orchestrator.generate_propose_from_data(
                NegotiationProposeData(self.context(), self.content(language)), self.template
            )
        if self.performative is NegotiationPerformative.ACCEPT:
            return orchestrator.generate_accept_from_data(
                NegotiationEndingData(self.context(), self.content(language)), self.template
            )
        if self.performative is NegotiationPerformative.REJECT:
            return orchestrator.generate_reject_from_data(
                NegotiationEndingData(self.context(), self.content(language)), self.template
            )
        return orchestrator.generate_abort_from_data(
            NegotiationAbortData(self.context(), self.content(language)), self.template
        )


#: Every golden fixture case in the fixed Java enum order.
GOLDEN_CASES: tuple[GoldenCase, ...] = (
    GoldenCase(
        "INFORMATION_PROPOSE",
        NegotiationPerformative.PROPOSE,
        INFORMATION_NEGOTIATION_PROPOSE_URI,
        "information_propose.md",
        default_context,
        _information_propose,
    ),
    GoldenCase(
        "TARGET_PROPOSE",
        NegotiationPerformative.PROPOSE,
        TARGET_NEGOTIATION_PROPOSE_URI,
        "target_propose.md",
        first_round_context,
        _target_propose,
    ),
    GoldenCase(
        "FEASIBILITY_PROPOSE",
        NegotiationPerformative.PROPOSE,
        FEASIBILITY_NEGOTIATION_PROPOSE_URI,
        "feasibility_propose.md",
        default_context,
        _feasibility_propose,
    ),
    GoldenCase(
        "TARGET_PROPOSE_CONFIRM",
        NegotiationPerformative.PROPOSE,
        TARGET_NEGOTIATION_PROPOSE_URI,
        "target_propose_confirm.md",
        default_context,
        _target_propose_confirm,
    ),
    GoldenCase(
        "FEASIBILITY_PROPOSE_CONFIRM",
        NegotiationPerformative.PROPOSE,
        FEASIBILITY_NEGOTIATION_PROPOSE_URI,
        "feasibility_propose_confirm.md",
        default_context,
        _feasibility_propose_confirm,
    ),
    GoldenCase(
        "INFORMATION_ACCEPT",
        NegotiationPerformative.ACCEPT,
        INFORMATION_NEGOTIATION_ACCEPT_REJECT_URI,
        "information_accept.md",
        default_context,
        _information_accept,
    ),
    GoldenCase(
        "TARGET_ACCEPT",
        NegotiationPerformative.ACCEPT,
        TARGET_NEGOTIATION_ACCEPT_REJECT_URI,
        "target_accept.md",
        default_context,
        _target_accept,
    ),
    GoldenCase(
        "FEASIBILITY_ACCEPT",
        NegotiationPerformative.ACCEPT,
        FEASIBILITY_NEGOTIATION_ACCEPT_REJECT_URI,
        "feasibility_accept.md",
        default_context,
        _feasibility_accept,
    ),
    GoldenCase(
        "INFORMATION_REJECT",
        NegotiationPerformative.REJECT,
        INFORMATION_NEGOTIATION_ACCEPT_REJECT_URI,
        "information_reject.md",
        default_context,
        _information_reject,
    ),
    GoldenCase(
        "TARGET_REJECT",
        NegotiationPerformative.REJECT,
        TARGET_NEGOTIATION_ACCEPT_REJECT_URI,
        "target_reject.md",
        default_context,
        _target_reject,
    ),
    GoldenCase(
        "FEASIBILITY_REJECT",
        NegotiationPerformative.REJECT,
        FEASIBILITY_NEGOTIATION_ACCEPT_REJECT_URI,
        "feasibility_reject.md",
        default_context,
        _feasibility_reject,
    ),
    GoldenCase(
        "ABORT",
        NegotiationPerformative.ABORT,
        NEGOTIATION_ABORT_URI,
        "abort.md",
        default_context,
        _abort,
    ),
)


def read_golden_fixture(golden_case: GoldenCase, language: str) -> str:
    """Read one committed golden fixture file, CRLF→LF-normalized (Java 3bdacb2 parity)."""
    return golden_case.golden_resource_path(language).read_text(encoding="utf-8").replace("\r\n", "\n")


def orchestrator(language: str) -> NegotiationGenerationOrchestrator:
    """Build the orchestrator wired with the built-in resources of one fixture language, no LLM."""
    configuration = builder()
    configuration.language = language
    return configuration.build()
