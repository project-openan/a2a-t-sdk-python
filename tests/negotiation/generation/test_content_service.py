"""Tests of the negotiation content service facade (port of the Java ``NegotiationContentService``
surface contract and the facade-level template URI boundary).

The service is a thin typing over the orchestrator with no behavior of its own: every method
delegates to exactly one orchestrator method, the twelve-method surface is pinned by name and
signature, the raw string template URI boundary parses fail-fast (D16, the Java facades'
``parseTemplateUri`` helper), and ``build_orchestrator`` wires the unified SDK config.
"""

from __future__ import annotations

import inspect
from typing import Any

import pytest

from a2a_t.config.models import (
    A2ATConfig,
    LlmRuntimeConfig,
    PromptRuntimeConfig,
)
from a2a_t.core.errors.exceptions import NegotiationGenerationError
from a2a_t.core.errors.input_limit import InputLimitConfig
from a2a_t.core.metadata import NegotiationContext, NegotiationPerformative
from a2a_t.core.standard_templates import INFORMATION_NEGOTIATION_PROPOSE, INFORMATION_NEGOTIATION_PROPOSE_URI
from a2a_t.core.validation_pipeline import FilledParamData
from a2a_t.negotiation.content import InformationProposeContent, NegotiationItem
from a2a_t.negotiation.content.models import NegotiationProposeData
from a2a_t.negotiation.generation import NegotiationContentService
from a2a_t.negotiation.generation.builder import builder

UUID = "3dbc13b5-bd57-4c2b-b503-24e381b6c8d3"

#: The eight generation methods and the four validation methods of the facade, in declaration order.
GENERATE_METHODS = (
    "generate_propose_from_data",
    "generate_accept_from_data",
    "generate_reject_from_data",
    "generate_abort_from_data",
    "generate_propose_from_text",
    "generate_accept_from_text",
    "generate_reject_from_text",
    "generate_abort_from_text",
)

VALIDATE_METHODS = (
    "validate_propose_prompt_and_data_filling",
    "validate_accept_prompt_and_data_filling",
    "validate_reject_prompt_and_data_filling",
    "validate_abort_prompt_and_data_filling",
)


class RecordingOrchestrator:
    """Recording stand-in of the orchestrator: every method returns a marker and records its call."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...]]] = []

    def __getattr__(self, name: str) -> Any:
        def record(*args: Any) -> str:
            self.calls.append((name, args))
            return f"{name}:ok"

        return record


def service() -> tuple[NegotiationContentService, RecordingOrchestrator]:
    """Build the service under test over a recording orchestrator."""
    recording = RecordingOrchestrator()
    return NegotiationContentService(recording), recording


def config(*, language: str = "zh-CN", max_attempts: int = 3, max_text_chars: int = 16384) -> A2ATConfig:
    """Build one unified SDK config for the wiring tests."""
    return A2ATConfig(
        prompt=PromptRuntimeConfig(language=language),
        input_limits=InputLimitConfig(max_text_chars=max_text_chars),
        llm=LlmRuntimeConfig(max_attempts=max_attempts),
    )


def propose_data() -> NegotiationProposeData:
    return NegotiationProposeData(
        NegotiationContext(UUID, 1, 5, NegotiationPerformative.PROPOSE),
        InformationProposeContent([NegotiationItem("节能区域", "松山湖")], None),
    )


def test_rejects_a_null_orchestrator() -> None:
    with pytest.raises(TypeError, match="Negotiation orchestrator must not be null."):
        NegotiationContentService(None)


@pytest.mark.parametrize(
    "method",
    (*GENERATE_METHODS, *VALIDATE_METHODS),
)
def test_every_service_method_delegates_to_exactly_one_orchestrator_method(method: str) -> None:
    wired, recording = service()

    if method in GENERATE_METHODS[:4]:
        result = getattr(wired, method)(propose_data(), INFORMATION_NEGOTIATION_PROPOSE_URI)
    elif method in GENERATE_METHODS[4:]:
        result = getattr(wired, method)(
            "请提供节能区域。",
            NegotiationContext(UUID, 1, 5, NegotiationPerformative.PROPOSE),
            INFORMATION_NEGOTIATION_PROPOSE_URI,
        )
    else:
        result = getattr(wired, method)(
            "## 所需信息项\n1. 区域\n",
            NegotiationContext(UUID, 1, 5, NegotiationPerformative.PROPOSE),
            {"type": "object"},
            INFORMATION_NEGOTIATION_PROPOSE_URI,
        )

    assert result == f"{method}:ok"
    assert [name for name, _ in recording.calls] == [method]
    assert recording.calls[0][1][-1] == INFORMATION_NEGOTIATION_PROPOSE, (
        "the raw string URI must be normalized to the typed form before the delegation"
    )


@pytest.mark.parametrize("method", VALIDATE_METHODS)
def test_every_validate_method_has_exactly_the_full_four_argument_signature(method: str) -> None:
    """Port of the Java signature contract test: the negotiation context, the parameter schema and
    the template URI are mandatory parts of the full signature of each of the four methods, so no
    reduced-arity overload can silently appear later."""
    signature = inspect.signature(getattr(NegotiationContentService, method))

    assert list(signature.parameters) == ["self", "prompt", "context", "schema", "template_uri"]


@pytest.mark.parametrize(
    "method",
    GENERATE_METHODS[:4],
)
def test_the_from_data_methods_have_the_two_argument_signature(method: str) -> None:
    signature = inspect.signature(getattr(NegotiationContentService, method))

    assert list(signature.parameters) == ["self", "data", "template_uri"]


@pytest.mark.parametrize("method", GENERATE_METHODS[4:])
def test_the_from_text_methods_have_the_three_argument_signature(method: str) -> None:
    signature = inspect.signature(getattr(NegotiationContentService, method))

    assert list(signature.parameters) == ["self", "text", "context", "template_uri"]


def test_the_service_declares_exactly_the_twelve_negotiation_methods() -> None:
    methods = {
        name
        for name, _ in inspect.getmembers(NegotiationContentService, predicate=inspect.isfunction)
        if not name.startswith("_") and name != "build_orchestrator"
    }
    assert methods == {*GENERATE_METHODS, *VALIDATE_METHODS}


# ----------------------------------------------------------------------
# Template URI boundary (D16)
# ----------------------------------------------------------------------


def test_an_unparseable_template_uri_is_rejected_fail_fast() -> None:
    wired, recording = service()

    with pytest.raises(ValueError, match="Unparseable template URI: not-a-valid-uri"):
        wired.generate_propose_from_data(propose_data(), "not-a-valid-uri")

    assert recording.calls == []


def test_a_null_template_uri_is_a_programming_error() -> None:
    wired, _ = service()

    with pytest.raises(TypeError, match="Template URI must not be null."):
        wired.generate_propose_from_data(propose_data(), None)


@pytest.mark.parametrize("template_uri", ["", "   "])
def test_a_blank_template_uri_is_rejected_fail_fast(template_uri: str) -> None:
    wired, _ = service()

    with pytest.raises(ValueError, match="Unparseable template URI"):
        wired.generate_propose_from_data(propose_data(), template_uri)


def test_a_typed_template_uri_is_the_accepted_dual_spelling() -> None:
    wired, recording = service()

    result = wired.generate_propose_from_data(propose_data(), INFORMATION_NEGOTIATION_PROPOSE)

    assert result == "generate_propose_from_data:ok"
    assert recording.calls[0][1][1] is INFORMATION_NEGOTIATION_PROPOSE


# ----------------------------------------------------------------------
# Default orchestrator wiring from the unified SDK config
# ----------------------------------------------------------------------


def test_build_orchestrator_wires_language_attempts_and_limit_from_the_config() -> None:
    from tests.negotiation.generation.test_orchestrator import ScriptedClient

    llm = ScriptedClient(RuntimeError("LLM endpoint unavailable."))
    orchestrator = NegotiationContentService.build_orchestrator(config(max_attempts=2, max_text_chars=10), llm)

    assert orchestrator.language == "zh-CN"
    with pytest.raises(NegotiationGenerationError) as generation_failure:
        orchestrator.generate_propose_from_text(
            "01234567890",
            NegotiationContext(UUID, 1, 5, NegotiationPerformative.PROPOSE),
            INFORMATION_NEGOTIATION_PROPOSE,
        )
    assert generation_failure.value.code.value == "input.text_too_long", "the config limit must drive the gate"
    assert llm.calls == 0

    with pytest.raises(NegotiationGenerationError) as extraction_failure:
        orchestrator.generate_propose_from_text(
            "请提供区域。",
            NegotiationContext(UUID, 1, 5, NegotiationPerformative.PROPOSE),
            INFORMATION_NEGOTIATION_PROPOSE,
        )
    assert extraction_failure.value.code.value == "llm.invocation_failed"
    assert llm.calls == 2, "the config attempt limit must drive the retry chain"


def test_build_orchestrator_works_without_an_llm_client_for_the_deterministic_leg() -> None:
    orchestrator = NegotiationContentService.build_orchestrator(config(), None)

    result = orchestrator.generate_propose_from_data(propose_data(), INFORMATION_NEGOTIATION_PROPOSE)

    assert result.template_uri == INFORMATION_NEGOTIATION_PROPOSE_URI
    assert "所需信息项" in result.prompt_text
    assert result.negotiation_context == NegotiationContext(UUID, 1, 5, NegotiationPerformative.PROPOSE)


def test_the_service_over_a_built_orchestrator_fills_parameters_through_the_seam() -> None:
    """The service validation leg returns the filled parameter data of the injected extractor."""

    class FixedExtractor:
        def extract(
            self,
            prompt: str | None,
            context: NegotiationContext | None,
            schema: Any,
            reference: Any,
        ) -> FilledParamData:
            return FilledParamData({"id": UUID, "round": 1, "region": "松山湖"})

    configuration = builder()
    configuration.language = "zh-CN"
    configuration.param_extractor = FixedExtractor()
    wired = NegotiationContentService(configuration.build())

    filled = wired.validate_propose_prompt_and_data_filling(
        "## 所需信息项\n1. 区域\n",
        NegotiationContext(UUID, 1, 5, NegotiationPerformative.PROPOSE),
        {"type": "object", "properties": {"region": {"type": "string"}}},
        INFORMATION_NEGOTIATION_PROPOSE_URI,
    )

    assert filled.data == {"id": UUID, "round": 1, "region": "松山湖"}
