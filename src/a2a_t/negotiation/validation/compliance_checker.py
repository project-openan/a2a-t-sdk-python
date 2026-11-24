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

"""Rule-level compliance check of the negotiation context (port of Java
``DefaultNegotiationComplianceChecker``, ``NegotiationComplianceChecker``, ``NegotiationRuleCheckResult``
and ``NegotiationRuleCheckerAdapter``).

The checker is deterministic and never calls an LLM. It validates only the strong constraints of the
negotiation context carried alongside the message in the A2A-T metadata: the id must be a UUID of 36
characters in 8-4-4-4-12 hexadecimal form and the round must not exceed the round budget. The
positive-integer shape of the round fields is already guaranteed by the
:class:`~a2a_t.core.metadata.NegotiationContext` constructor. Nothing else is inferred or checked
here — type inference, conclusion values, ending-section presence and conditional-section
exclusivity belong to the semantic validation step.

Rule table (each rule -> its catalog code and facts):

====================  ====================================  ===========================
rule                  code                                  facts
====================  ====================================  ===========================
id not a 8-4-4-4-12   ``negotiation.invalid_context_id``    ``actual``
hexadecimal UUID
round > max_rounds    ``negotiation.round_exceeded``        ``round``, ``max_rounds``
====================  ====================================  ===========================

The adapter bridges one checker plus the context of a single call to the core
:class:`~a2a_t.core.validation_pipeline.RuleChecker` contract consumed by the shared
:class:`~a2a_t.core.validation_pipeline.ValidationPipeline`: a ``None`` context is reported as the
message not being a negotiation message (``negotiation.invalid_input`` with the ``reason`` fact), a
rule violation surfaces as ``negotiation.rule_violation`` carrying the structured rule errors, and a
passing check returns the context parameters ``id``, ``round`` and ``maxRounds`` the deterministic
merge later gives precedence to.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Final, Protocol

from a2a_t.core.errors.catalog import ErrorCatalog
from a2a_t.core.errors.exceptions import (
    ContentValidationError,
    SlotValidationError,
)
from a2a_t.core.errors.messages import render
from a2a_t.core.metadata import NegotiationContext

__all__ = [
    "DefaultNegotiationComplianceChecker",
    "NegotiationComplianceChecker",
    "NegotiationRuleCheckResult",
    "NegotiationRuleCheckerAdapter",
]

_LOGGER = logging.getLogger(__name__)

#: Shape every negotiation context id must have: 8-4-4-4-12 hexadecimal groups (Java ``UUID_PATTERN``).
_UUID_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)

#: Slot name of the context-id rule.
_ID_SLOT: Final[str] = "id"

#: Slot name of the round-budget rule.
_ROUND_SLOT: Final[str] = "round"

#: ``reason`` fact of the missing-context failure, English (Java ``MISSING_CONTEXT_REASON_EN``).
_MISSING_CONTEXT_REASON_EN: Final[str] = "the negotiation context is missing (the message is not a negotiation message)"

#: ``reason`` fact of the missing-context failure, zh-CN (Java ``MISSING_CONTEXT_REASON_ZH``).
_MISSING_CONTEXT_REASON_ZH: Final[str] = "缺少协商上下文(该报文不是协商报文)"


@dataclass(frozen=True)
class NegotiationRuleCheckResult:
    """Outcome of the rule-level compliance check of a negotiation message.

    The checker only validates the negotiation context carried alongside the message, so this result
    carries exactly two components: the overall pass flag and the structured rule errors. Nothing
    else is inferred or checked here.

    Attributes:
        passed: ``True`` only when the negotiation context satisfies every context rule.
        errors: structured rule errors of the negotiation context; empty when every rule passes.
    """

    passed: bool
    errors: tuple[SlotValidationError, ...] = ()

    def __post_init__(self) -> None:
        """Normalize the error sequence.

        Raises:
            TypeError: when the errors sequence is ``None`` (Java ``NullPointerException`` parity).
        """
        if self.errors is None:
            raise TypeError("Negotiation rule check errors must not be null.")
        object.__setattr__(self, "errors", tuple(self.errors))


class NegotiationComplianceChecker(Protocol):
    """Rule-level compliance checker for negotiation messages.

    The checker is deterministic and never calls an LLM. It validates only the strong constraints of
    the negotiation context carried alongside the message; type inference, conclusion values,
    ending-section presence and conditional-section exclusivity are deliberately not checked here —
    they belong to the semantic validation step.
    """

    def check(self, context: NegotiationContext) -> NegotiationRuleCheckResult:
        """Run the rule-level compliance check of one negotiation context.

        Args:
            context: negotiation context carried alongside the message in the A2A-T metadata.

        Returns:
            rule check outcome carrying the pass flag and the structured errors.

        Raises:
            TypeError: when the context is ``None`` (Java ``NullPointerException`` parity).
        """
        ...


class DefaultNegotiationComplianceChecker:
    """Default rule-level compliance checker for negotiation messages.

    Error messages are rendered from the :class:`~a2a_t.core.errors.catalog.ErrorCatalog` message
    templates in the configured language.
    """

    def __init__(self, language: str | None = None) -> None:
        """Create the default checker rendering messages in one language.

        Args:
            language: message language, for example ``zh-CN``; ``None`` falls back to ``en-US``.
        """
        self._language = language

    @property
    def language(self) -> str | None:
        """Language the rule failure messages are rendered in."""
        return self._language

    def check(self, context: NegotiationContext) -> NegotiationRuleCheckResult:
        """Run the rule-level compliance check of one negotiation context.

        Args:
            context: negotiation context carried alongside the message in the A2A-T metadata.

        Returns:
            rule check outcome with no errors when every context rule holds, otherwise the context
            rule errors.

        Raises:
            TypeError: when the context is ``None`` (Java ``NullPointerException`` parity).
        """
        if context is None:
            raise TypeError("context")
        errors: list[SlotValidationError] = []
        self._collect_id_errors(context.id, errors)
        if context.round > context.max_rounds:
            facts = _round_facts(context)
            errors.append(
                SlotValidationError(
                    _ROUND_SLOT,
                    ErrorCatalog.NEGOTIATION_ROUND_EXCEEDED.value,
                    render(ErrorCatalog.NEGOTIATION_ROUND_EXCEEDED, facts, self._language),
                    facts,
                )
            )
        return _log_result(NegotiationRuleCheckResult(not errors, tuple(errors)))

    def _collect_id_errors(self, id: str, errors: list[SlotValidationError]) -> None:
        """Append the context-id rule error when the id does not match the UUID shape."""
        if not _UUID_PATTERN.match(id):
            facts = {"actual": id}
            errors.append(
                SlotValidationError(
                    _ID_SLOT,
                    ErrorCatalog.NEGOTIATION_INVALID_CONTEXT_ID.value,
                    render(ErrorCatalog.NEGOTIATION_INVALID_CONTEXT_ID, facts, self._language),
                    facts,
                )
            )


def _round_facts(context: NegotiationContext) -> dict[str, str]:
    """Build the fact values of the round-budget rule."""
    return {"round": str(context.round), "max_rounds": str(context.max_rounds)}


def _log_result(result: NegotiationRuleCheckResult) -> NegotiationRuleCheckResult:
    """Emit the rule-check completion event of one check outcome."""
    if not result.errors:
        _LOGGER.debug("negotiation_rule_checks_completed passed=%s error_count=0", result.passed)
    else:
        _LOGGER.warning("negotiation_rule_checks_completed passed=%s error_count=%s", result.passed, len(result.errors))
    return result


class NegotiationRuleCheckerAdapter:
    """Adapter bridging one compliance checker to the core ``RuleChecker`` contract for one call.

    The adapter holds a :class:`NegotiationComplianceChecker` and the negotiation context carried
    alongside the message in the A2A-T metadata, delegating the ``check`` call to the context-aware
    checker. A ``None`` context is reported as the message not being a negotiation message. The
    resulting :class:`NegotiationRuleCheckResult` is converted into either the context parameters
    (``id``, ``round``, ``maxRounds``) or a
    :class:`~a2a_t.core.errors.exceptions.ContentValidationError` carrying the catalog codes
    ``negotiation.invalid_input`` (no negotiation context) or ``negotiation.rule_violation`` (context
    rule violation) with messages rendered in the configured language.
    """

    def __init__(
        self,
        checker: NegotiationComplianceChecker,
        context: NegotiationContext | None,
        language: str | None = None,
    ) -> None:
        """Create an adapter for one compliance checker, negotiation context and message language.

        Args:
            checker: compliance checker validating the negotiation context.
            context: negotiation context carried alongside the message; ``None`` is reported as the
                message not being a negotiation message.
            language: message language, for example ``zh-CN``; ``None`` falls back to ``en-US``.

        Raises:
            TypeError: when the checker is ``None`` (Java ``NullPointerException`` parity).
        """
        if checker is None:
            raise TypeError("checker")
        self._checker = checker
        self._context = context
        self._language = language

    def check(self, prompt: str) -> dict[str, object]:
        """Run the rule-level gate of one validation call.

        Args:
            prompt: rendered negotiation message text (unused; the gate validates the context).

        Returns:
            the context parameters ``id``, ``round`` and ``maxRounds``.

        Raises:
            ContentValidationError: with ``negotiation.invalid_input`` when the context is ``None``
                (the message is not a negotiation message), or with ``negotiation.rule_violation``
                carrying the structured rule errors when the context violates a rule.
        """
        if self._context is None:
            facts = {"reason": _missing_context_reason(self._language)}
            raise ContentValidationError(
                ErrorCatalog.NEGOTIATION_INVALID_INPUT,
                facts,
                language=self._language,
            )
        result = self._checker.check(self._context)
        if not result.passed:
            message = (
                render(ErrorCatalog.NEGOTIATION_RULE_VIOLATION, None, self._language)
                if not result.errors
                else result.errors[0].message
            )
            raise ContentValidationError(
                ErrorCatalog.NEGOTIATION_RULE_VIOLATION,
                message=message,
                language=self._language,
                errors=result.errors,
            )
        return {"id": self._context.id, "round": self._context.round, "maxRounds": self._context.max_rounds}


def _missing_context_reason(language: str | None) -> str:
    """Return the missing-context reason fact in the reference language."""
    return (
        _MISSING_CONTEXT_REASON_ZH if language is not None and language.startswith("zh") else _MISSING_CONTEXT_REASON_EN
    )
