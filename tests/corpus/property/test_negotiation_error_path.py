"""Error-path property layer (port of Java ``NegotiationErrorPathPropertyTest``, design §8.3).

Strategies specifically designed to trigger one error code each: blank free text, a conclusion that
contradicts the addressed phase, a payload missing one required field, or a context round above
the budget. Every property asserts the exact public error code plus the exact LLM call count —
0 when the failure precedes the LLM step, 1 when the mapped failure is non-retryable.
"""

from __future__ import annotations

from collections.abc import Callable
from enum import Enum
from typing import Any

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from a2a_t.core.errors.catalog import ErrorCatalog
from a2a_t.core.errors.exceptions import (
    NegotiationGenerationError,
    NegotiationParamExtractionError,
)
from a2a_t.core.metadata import NegotiationContext, NegotiationPerformative
from a2a_t.negotiation.generation import NegotiationContentService
from tests.corpus.llm_stub import ScriptedNegotiationLlmClient
from tests.corpus.property.arbitraries import any_session_id, contexts, languages
from tests.corpus.property.harness import (
    json_payload,
    object_schema,
    scripted,
    service,
    template_uri,
)

INFORMATION_PROPOSE_URI = "Negotiation-T/information-negotiation/propose/v1"
TARGET_PROPOSE_URI = "Negotiation-T/target-negotiation/propose/v1"
FEASIBILITY_PROPOSE_URI = "Negotiation-T/feasibility-negotiation/propose/v1"
INFORMATION_ACCEPT_REJECT_URI = "Negotiation-T/information-negotiation/accept-reject/v1"
TARGET_ACCEPT_REJECT_URI = "Negotiation-T/target-negotiation/accept-reject/v1"
COMMON_ABORT_URI = "Negotiation-T/common/abort/v1"

FLAT_SCHEMA: dict[str, object] = object_schema({})

#: The three negotiation type segments of the accept-reject URIs.
NEGOTIATION_TYPES: tuple[str, ...] = (
    "information-negotiation",
    "target-negotiation",
    "feasibility-negotiation",
)


# ------------------------------------------------------------------ missing-field matrix


class MissingRequiredField(Enum):
    """One required extraction field dropped from an otherwise valid payload, with its API."""

    INFORMATION_PROPOSE_ITEMS = "information-propose-items"
    TARGET_PROPOSE_DESCRIPTION = "target-propose-description"
    FEASIBILITY_PROPOSE_DESCRIPTION = "feasibility-propose-description"
    ENDING_CONCLUSION = "ending-conclusion"
    TARGET_ACCEPT_CONFIRMED_INTENT = "target-accept-confirmed-intent"
    ABORT_TERMINATION_REASON = "abort-termination-reason"

    @property
    def payload(self) -> dict[str, Any]:
        """The valid payload of this field's API with this field dropped."""
        return _PAYLOADS[self]()

    def invoke(self, wired: NegotiationContentService, context: NegotiationContext) -> Any:
        """Run this field's API on the payload-missing input."""
        return _INVOKERS[self](wired, context)


def _without_key(payload: dict[str, Any], key: str) -> dict[str, Any]:
    """Copy one payload without the given key."""
    reduced = dict(payload)
    reduced.pop(key, None)
    return reduced


def _valid_information_propose_payload() -> dict[str, Any]:
    """A valid information propose extraction payload."""
    return {
        "items": [{"name": "access_port", "value": "P533-Zhujiang Old Town-PTN3900-23-TPA1EG24-1"}],
        "relationship": None,
    }


def _valid_target_propose_payload() -> dict[str, Any]:
    """A valid target propose extraction payload."""
    return {
        "target_negotiation_description": "Latency repair target of the quality degradation complaint",
        "intent_understanding": [],
        "alignment_and_clarification": [],
        "request_for_clarification": [],
    }


def _valid_feasibility_propose_payload() -> dict[str, Any]:
    """A valid feasibility propose extraction payload."""
    return {
        "feasibility_negotiation_description": "Access port expansion feasibility within the cutover window",
        "action": "REQUEST_FEASIBILITY_EVALUATION",
        "contents_to_evaluate": [{"name": "latency_target", "value": "within 20ms"}],
        "infeasibility_details_and_proposal": [],
    }


def _valid_information_ending_payload() -> dict[str, Any]:
    """A valid information ending extraction payload."""
    return {
        "conclusion": "Accept",
        "items": [{"name": "access_port", "value": "P533-Zhujiang Old Town-PTN3900-23-TPA1EG24-1"}],
    }


def _valid_target_ending_payload() -> dict[str, Any]:
    """A valid target ending extraction payload."""
    return {
        "conclusion": "Accept",
        "confirmed_intent": "Confirmed latency repair intent within 20ms",
        "failure_reason": None,
    }


def _valid_abort_payload() -> dict[str, Any]:
    """A valid abort extraction payload."""
    return {"termination_reason": "The negotiation round limit is reached."}


def _payload_information_propose_items() -> dict[str, Any]:
    """The information propose payload without its required items."""
    return _without_key(_valid_information_propose_payload(), "items")


def _payload_target_propose_description() -> dict[str, Any]:
    """The target propose payload without its required description."""
    return _without_key(_valid_target_propose_payload(), "target_negotiation_description")


def _payload_feasibility_propose_description() -> dict[str, Any]:
    """The feasibility propose payload without its required description."""
    return _without_key(_valid_feasibility_propose_payload(), "feasibility_negotiation_description")


def _payload_ending_conclusion() -> dict[str, Any]:
    """The information ending payload without its conclusion."""
    return _without_key(_valid_information_ending_payload(), "conclusion")


def _payload_target_accept_confirmed_intent() -> dict[str, Any]:
    """The target ending payload without its accept-phase confirmed intent."""
    return _without_key(_valid_target_ending_payload(), "confirmed_intent")


def _payload_abort_termination_reason() -> dict[str, Any]:
    """The abort payload without its termination reason."""
    return _without_key(_valid_abort_payload(), "termination_reason")


#: The payload builder of each missing-field entry.
_PAYLOADS: dict[MissingRequiredField, Callable[[], dict[str, Any]]] = {
    MissingRequiredField.INFORMATION_PROPOSE_ITEMS: _payload_information_propose_items,
    MissingRequiredField.TARGET_PROPOSE_DESCRIPTION: _payload_target_propose_description,
    MissingRequiredField.FEASIBILITY_PROPOSE_DESCRIPTION: _payload_feasibility_propose_description,
    MissingRequiredField.ENDING_CONCLUSION: _payload_ending_conclusion,
    MissingRequiredField.TARGET_ACCEPT_CONFIRMED_INTENT: _payload_target_accept_confirmed_intent,
    MissingRequiredField.ABORT_TERMINATION_REASON: _payload_abort_termination_reason,
}


def _invoke_information_propose(wired: NegotiationContentService, context: NegotiationContext) -> Any:
    """Run the information propose from-text API."""
    return wired.generate_propose_from_text(
        "Please provide the missing information.", context, template_uri(INFORMATION_PROPOSE_URI)
    )


def _invoke_target_propose(wired: NegotiationContentService, context: NegotiationContext) -> Any:
    """Run the target propose from-text API."""
    return wired.generate_propose_from_text(
        "I want to negotiate the coverage target.", context, template_uri(TARGET_PROPOSE_URI)
    )


def _invoke_feasibility_propose(wired: NegotiationContentService, context: NegotiationContext) -> Any:
    """Run the feasibility propose from-text API."""
    return wired.generate_propose_from_text(
        "Please evaluate whether the upgrade is feasible.", context, template_uri(FEASIBILITY_PROPOSE_URI)
    )


def _invoke_information_accept(wired: NegotiationContentService, context: NegotiationContext) -> Any:
    """Run the information accept from-text API."""
    return wired.generate_accept_from_text(
        "I accept the proposed information set.", context, template_uri(INFORMATION_ACCEPT_REJECT_URI)
    )


def _invoke_target_accept(wired: NegotiationContentService, context: NegotiationContext) -> Any:
    """Run the target accept from-text API."""
    return wired.generate_accept_from_text(
        "I confirm the negotiated intent.", context, template_uri(TARGET_ACCEPT_REJECT_URI)
    )


def _invoke_abort(wired: NegotiationContentService, context: NegotiationContext) -> Any:
    """Run the abort from-text API."""
    return wired.generate_abort_from_text("The round limit is reached.", context, template_uri(COMMON_ABORT_URI))


#: The API invocation of each missing-field entry.
_INVOKERS: dict[MissingRequiredField, Callable[[NegotiationContentService, NegotiationContext], Any]] = {
    MissingRequiredField.INFORMATION_PROPOSE_ITEMS: _invoke_information_propose,
    MissingRequiredField.TARGET_PROPOSE_DESCRIPTION: _invoke_target_propose,
    MissingRequiredField.FEASIBILITY_PROPOSE_DESCRIPTION: _invoke_feasibility_propose,
    MissingRequiredField.ENDING_CONCLUSION: _invoke_information_accept,
    MissingRequiredField.TARGET_ACCEPT_CONFIRMED_INTENT: _invoke_target_accept,
    MissingRequiredField.ABORT_TERMINATION_REASON: _invoke_abort,
}


def _ending_payload(conclusion: str) -> str:
    """Serialize one target ending payload carrying the given conclusion."""
    payload = dict(_valid_target_ending_payload())
    payload["conclusion"] = conclusion
    return json_payload(payload)


@settings(max_examples=100)
@given(language=languages(), context=contexts(), text=st.text(alphabet=" \t\r\n", max_size=12))
def test_blank_text_fails_with_invalid_input(
    language: str,
    context: NegotiationContext,
    text: str,
) -> None:
    """A blank free-text input fails both from-text legs with ``negotiation.invalid_input``, 0 calls."""
    llm = ScriptedNegotiationLlmClient.assertion_only()
    wired = service(language, llm)
    with pytest.raises(NegotiationGenerationError) as propose:
        wired.generate_propose_from_text(text, context, template_uri(INFORMATION_PROPOSE_URI))
    assert propose.value.code == ErrorCatalog.NEGOTIATION_INVALID_INPUT
    with pytest.raises(NegotiationGenerationError) as abort:
        wired.generate_abort_from_text(text, context, template_uri(COMMON_ABORT_URI))
    assert abort.value.code == ErrorCatalog.NEGOTIATION_INVALID_INPUT
    assert llm.call_count == 0


@settings(max_examples=100)
@given(
    language=languages(),
    context=contexts(),
    type_segment=st.sampled_from(NEGOTIATION_TYPES),
    swapped=st.booleans(),
)
def test_conclusion_mismatching_the_phase_fails_with_conclusion_mismatch(
    language: str,
    context: NegotiationContext,
    type_segment: str,
    swapped: bool,
) -> None:
    """An accept call receiving a Reject payload (and vice versa) fails with the mismatch code."""
    uri = template_uri(f"Negotiation-T/{type_segment}/accept-reject/v1")
    # An accept call receives a Reject payload and vice versa; all other required fields present.
    conclusion = "Reject" if swapped else "Accept"
    llm = scripted(_ending_payload(conclusion))
    wired = service(language, llm)
    if swapped:
        with pytest.raises(NegotiationGenerationError) as excinfo:
            wired.generate_accept_from_text("I must refuse the current offer.", context, uri)
    else:
        with pytest.raises(NegotiationGenerationError) as excinfo:
            wired.generate_reject_from_text("I cannot agree with the current offer.", context, uri)
    assert excinfo.value.code == ErrorCatalog.NEGOTIATION_CONCLUSION_MISMATCH
    assert llm.call_count == 1


@settings(max_examples=100)
@given(language=languages(), context=contexts(), field=st.sampled_from(tuple(MissingRequiredField)))
def test_payload_missing_one_required_field_fails_with_slot_missing(
    language: str,
    context: NegotiationContext,
    field: MissingRequiredField,
) -> None:
    """One dropped required extraction field fails with ``negotiation.field_missing``, 1 call."""
    llm = scripted(json_payload(field.payload))
    wired = service(language, llm)
    with pytest.raises(NegotiationGenerationError) as excinfo:
        field.invoke(wired, context)
    assert excinfo.value.code == ErrorCatalog.NEGOTIATION_FIELD_MISSING
    assert llm.call_count == 1


@settings(max_examples=100)
@given(
    language=languages(),
    max_rounds=st.integers(min_value=1, max_value=10),
    overshoot=st.integers(min_value=1, max_value=20),
)
def test_round_above_the_budget_fails_with_rule_violation(
    language: str,
    max_rounds: int,
    overshoot: int,
) -> None:
    """An over-budget incoming context fails both validate legs with ``negotiation.rule_violation``."""
    # Each leg validates the message of one performative, so its context carries that performative.
    propose_context = NegotiationContext(
        any_session_id(), max_rounds + overshoot, max_rounds, NegotiationPerformative.PROPOSE
    )
    abort_context = NegotiationContext(
        any_session_id(), max_rounds + overshoot, max_rounds, NegotiationPerformative.ABORT
    )
    llm = ScriptedNegotiationLlmClient.assertion_only()
    wired = service(language, llm)
    with pytest.raises(NegotiationParamExtractionError) as propose:
        wired.validate_propose_prompt_and_data_filling(
            "Rendered negotiation message text.",
            propose_context,
            FLAT_SCHEMA,
            template_uri(INFORMATION_PROPOSE_URI),
        )
    assert propose.value.code == ErrorCatalog.NEGOTIATION_RULE_VIOLATION
    with pytest.raises(NegotiationParamExtractionError) as abort:
        wired.validate_abort_prompt_and_data_filling(
            "Rendered negotiation message text.",
            abort_context,
            FLAT_SCHEMA,
            template_uri(COMMON_ABORT_URI),
        )
    assert abort.value.code == ErrorCatalog.NEGOTIATION_RULE_VIOLATION
    assert llm.call_count == 0
