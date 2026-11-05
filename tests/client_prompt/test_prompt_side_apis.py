"""Tests of the template-directed client prompt APIs (Java ``fromText`` / ``fromDataWithSchema``).

Pins the six ``generate_{task,auth,notification}_prompt_from_{text,data_with_schema}`` methods of
the client prompt generation orchestrator against the Java
``DefaultClientPromptGenerationOrchestrator`` contract: the happy path renders one
``MetadataContent`` per extension family, the from-data leg threads the caller's data schema into
the extraction message, and every stage boundary surfaces the Java catch-point catalog code.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = PROJECT_ROOT / "src"

if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from a2a_t.client.prompt_generation.prompt_generation_orchestrator import PromptGenerationOrchestrator
from a2a_t.config.models import PromptRuntimeConfig
from a2a_t.core.errors.catalog import ErrorCatalog
from a2a_t.core.errors.exceptions import A2ATBusinessError, A2ATError, PromptGenerationError
from a2a_t.core.metadata import (
    AUTHORIZATION_T_EXTENSION_URI,
    NOTIFICATION_T_EXTENSION_URI,
    TASK_T_EXTENSION_URI,
)
from a2a_t.llm.errors import LLMConfigError, LLMRuntimeError
from a2a_t.prompt.analysis.errors import SlotExtractionError
from a2a_t.prompt.analysis.models import SlotExtractionResult
from a2a_t.prompt.common.models import PromptReference
from a2a_t.prompt.task_rendering.errors import TaskPromptRenderError
from a2a_t.prompt.validation.models import SlotValidationError
from tests.support import FakePromptResourceAccess

#: The three extension families and the facade entry points they select.
FAMILIES = (
    ("task", "generate_task_prompt_from_text", "generate_task_prompt_from_data_with_schema", TASK_T_EXTENSION_URI),
    (
        "auth",
        "generate_auth_prompt_from_text",
        "generate_auth_prompt_from_data_with_schema",
        AUTHORIZATION_T_EXTENSION_URI,
    ),
    (
        "notification",
        "generate_notification_prompt_from_text",
        "generate_notification_prompt_from_data_with_schema",
        NOTIFICATION_T_EXTENSION_URI,
    ),
)

_TEMPLATE_URI = "Task-T/network-layer/ran-energy-saving/v1"

_SLOT_JSON_SCHEMA: dict[str, object] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "properties": {
        "site": {
            "type": "string",
            "description": "Site name",
            "examples": ["Site A"],
        },
        "additional_notes": {
            "type": "string",
            "description": "Additional notes",
            "examples": ["Focus on power system"],
        },
    },
    "required": ["site"],
}

#: Catalog codes the metadata-content pipeline emits at its failure boundaries (Java parity).
METADATA_FAILURE_CODES = (
    ErrorCatalog.INPUT_TEXT_TOO_LONG,
    ErrorCatalog.TEMPLATE_NOT_FOUND,
    ErrorCatalog.TEMPLATE_LOAD_FAILED,
    ErrorCatalog.SLOT_SCHEMA_NOT_FOUND,
    ErrorCatalog.SLOT_NOT_PROVIDED,
    ErrorCatalog.SLOT_CONSTRAINT_VIOLATED,
    ErrorCatalog.SLOT_RULE_VIOLATION,
    ErrorCatalog.LLM_NOT_CONFIGURED,
    ErrorCatalog.LLM_INVOCATION_FAILED,
    ErrorCatalog.LLM_RESPONSE_INVALID,
    ErrorCatalog.TEMPLATE_RENDER_FAILED,
)


class RecordingSlotExtractor:
    """Fake slot extractor recording its keyword arguments (the injection seam of the tests)."""

    def __init__(self, result: SlotExtractionResult | BaseException) -> None:
        self._result = result
        self.last_kwargs: dict[str, Any] = {}
        self.call_count = 0
        self.last_raw_response_content: str | None = '{"slots": {}}'

    def extract(self, **kwargs: Any) -> SlotExtractionResult:
        self.call_count += 1
        self.last_kwargs = dict(kwargs)
        if isinstance(self._result, BaseException):
            raise self._result
        return self._result


def _build_orchestrator(
    *,
    slot_extractor: Any,
    resource_access: FakePromptResourceAccess | None = None,
) -> PromptGenerationOrchestrator:
    return PromptGenerationOrchestrator(
        config=PromptRuntimeConfig(language="en-US", source_type="packaged"),
        resource_access=resource_access
        or FakePromptResourceAccess(
            template_text="Site: {site}\nNotes: {additional_notes}",
            slot_json_schema=_SLOT_JSON_SCHEMA,
            system_prompt="Extract slots.",
            user_prompt="Return slots.",
        ),
        scenario_resolver=object(),
        slot_extractor=slot_extractor,
    )


def _success_extraction() -> SlotExtractionResult:
    return SlotExtractionResult(slots={"site": "Site A", "additional_notes": None}, slot_errors=[])


@pytest.mark.parametrize("entry", METADATA_FAILURE_CODES, ids=lambda entry: entry.value)
def test_metadata_failure_codes_are_closed_catalog_members(entry: ErrorCatalog) -> None:
    assert ErrorCatalog(entry.value) is entry


@pytest.mark.parametrize(
    ("family", "from_text_method", "from_data_method", "extension_uri"),
    FAMILIES,
    ids=[family[0] for family in FAMILIES],
)
def test_from_text_returns_metadata_content_for_every_family(
    family: str,
    from_text_method: str,
    from_data_method: str,
    extension_uri: str,
) -> None:
    extractor = RecordingSlotExtractor(_success_extraction())
    orchestrator = _build_orchestrator(slot_extractor=extractor)

    result = getattr(orchestrator, from_text_method)("Analyze Site A energy usage.", _TEMPLATE_URI)

    assert result.template_uri == _TEMPLATE_URI
    assert result.prompt_text == "Site: Site A\nNotes: "
    assert result.extension_uri == extension_uri
    assert result.negotiation_context is None
    assert extractor.call_count == 1
    assert extractor.last_kwargs["reference"] == PromptReference(scenario_code=_TEMPLATE_URI, language="en-US")


@pytest.mark.parametrize(
    ("family", "from_text_method", "from_data_method", "extension_uri"),
    FAMILIES,
    ids=[family[0] for family in FAMILIES],
)
def test_from_data_with_schema_returns_metadata_content_for_every_family(
    family: str,
    from_text_method: str,
    from_data_method: str,
    extension_uri: str,
) -> None:
    extractor = RecordingSlotExtractor(_success_extraction())
    orchestrator = _build_orchestrator(slot_extractor=extractor)

    result = getattr(orchestrator, from_data_method)({"site": "Site A"}, {"site": "string"}, _TEMPLATE_URI)

    assert result.template_uri == _TEMPLATE_URI
    assert result.prompt_text == "Site: Site A\nNotes: "
    assert result.extension_uri == extension_uri
    # The from-data leg threads the caller's data schema into the extraction step and stringifies
    # the structured input as the extraction text (Java ``String.valueOf``).
    assert extractor.last_kwargs["data_schema"] == {"site": "string"}
    assert extractor.last_kwargs["normalized_input"] == str({"site": "Site A"})


def test_from_text_passes_no_data_schema_by_default() -> None:
    extractor = RecordingSlotExtractor(_success_extraction())
    orchestrator = _build_orchestrator(slot_extractor=extractor)

    orchestrator.generate_task_prompt_from_text("Analyze Site A energy usage.", _TEMPLATE_URI)

    assert extractor.last_kwargs["data_schema"] is None


def test_generation_resources_are_loaded_through_the_access_layer() -> None:
    access = FakePromptResourceAccess(
        template_text="Site: {site}\nNotes: {additional_notes}",
        slot_json_schema=_SLOT_JSON_SCHEMA,
        system_prompt="Extract slots.",
        user_prompt="Return slots.",
    )
    extractor = RecordingSlotExtractor(_success_extraction())
    orchestrator = _build_orchestrator(slot_extractor=extractor, resource_access=access)

    orchestrator.generate_task_prompt_from_data_with_schema({"site": "Site A"}, {"site": "string"}, _TEMPLATE_URI)

    assert access.template_calls == [(_TEMPLATE_URI, "en-US")]
    assert extractor.last_kwargs["system_prompt"] == "Extract slots."
    assert extractor.last_kwargs["user_prompt"] == "Return slots."


@pytest.mark.parametrize(
    ("template_failure", "expected_code"),
    [
        (A2ATBusinessError(ErrorCatalog.TEMPLATE_NOT_FOUND, {"template_uri": _TEMPLATE_URI}), "template.not_found"),
        (A2ATError("template resource read failed", code=ErrorCatalog.TEMPLATE_LOAD_FAILED), "template.load_failed"),
    ],
    ids=["template-not-found", "template-load-failed"],
)
def test_template_load_failures_surface_the_java_catch_codes(
    template_failure: BaseException,
    expected_code: str,
) -> None:
    access = FakePromptResourceAccess(template_text=template_failure)
    orchestrator = _build_orchestrator(
        slot_extractor=RecordingSlotExtractor(_success_extraction()),
        resource_access=access,
    )

    with pytest.raises(PromptGenerationError) as exc_info:
        orchestrator.generate_task_prompt_from_text("Analyze Site A.", _TEMPLATE_URI)

    assert exc_info.value.code_str == expected_code


def test_missing_slot_schema_surfaces_schema_not_found() -> None:
    access = FakePromptResourceAccess(
        template_text="Site: {site}",
        slot_json_schema=A2ATBusinessError(ErrorCatalog.SLOT_SCHEMA_NOT_FOUND, {}),
    )
    orchestrator = _build_orchestrator(
        slot_extractor=RecordingSlotExtractor(_success_extraction()),
        resource_access=access,
    )

    with pytest.raises(PromptGenerationError) as exc_info:
        orchestrator.generate_task_prompt_from_text("Analyze Site A.", _TEMPLATE_URI)

    assert exc_info.value.code_str == "slot.schema_not_found"


@pytest.mark.parametrize(
    ("extractor_error", "expected_code"),
    [
        (LLMConfigError("LLM is not configured."), "llm.not_configured"),
        (LLMRuntimeError("LLM invocation failed."), "llm.invocation_failed"),
        (LLMRuntimeError("The model returned invalid json."), "llm.response_invalid"),
        (LLMRuntimeError("The model returned empty content."), "llm.response_invalid"),
        (SlotExtractionError("Slot extraction returned invalid JSON."), "llm.invocation_failed"),
    ],
    ids=["not-configured", "invocation-failed", "invalid-json", "empty-content", "analysis-error"],
)
def test_llm_step_failures_surface_the_java_catch_codes(
    extractor_error: BaseException,
    expected_code: str,
) -> None:
    orchestrator = _build_orchestrator(slot_extractor=RecordingSlotExtractor(extractor_error))

    with pytest.raises(PromptGenerationError) as exc_info:
        orchestrator.generate_task_prompt_from_text("Analyze Site A.", _TEMPLATE_URI)

    assert exc_info.value.code_str == expected_code


@pytest.mark.parametrize(
    ("reported_code", "expected_code"),
    [
        ("missing_input", "slot.not_provided"),
        ("slot.not_provided", "slot.not_provided"),
        ("invalid_value", "slot.constraint_violated"),
        ("slot.constraint_violated", "slot.constraint_violated"),
        ("slot.fabricated_value", "slot.rule_violation"),
        ("slot.rule_violation", "slot.rule_violation"),
        ("negotiation.rule_violation", "slot.rule_violation"),
        ("totally-unknown", "slot.rule_violation"),
    ],
    ids=[
        "legacy-missing-input",
        "catalog-not-provided",
        "legacy-invalid-value",
        "catalog-constraint-violated",
        "in-catalog-code-outside-the-extraction-contract",
        "catalog-rule-violation",
        "cross-domain-code",
        "unknown-code",
    ],
)
def test_extraction_slot_errors_map_to_the_catalog_codes(reported_code: str, expected_code: str) -> None:
    extraction = SlotExtractionResult(
        slots={"site": "Site A", "additional_notes": None},
        slot_errors=[SlotValidationError(slot_name="site", code=reported_code, message="extraction step message")],
    )
    orchestrator = _build_orchestrator(slot_extractor=RecordingSlotExtractor(extraction))

    with pytest.raises(PromptGenerationError) as exc_info:
        orchestrator.generate_task_prompt_from_text("Analyze Site A.", _TEMPLATE_URI)

    assert exc_info.value.code_str == expected_code
    assert exc_info.value.failed_parameters[0].slot_name == "site"
    assert exc_info.value.failed_parameters[0].facts == {"slot_label": "site"}
    # The message is the joined rendered catalog messages, never the raw extraction message.
    assert "extraction step message" not in str(exc_info.value)


def test_required_slot_missing_fails_with_not_provided_and_slot_label() -> None:
    extraction = SlotExtractionResult(slots={"site": None, "additional_notes": None}, slot_errors=[])
    orchestrator = _build_orchestrator(slot_extractor=RecordingSlotExtractor(extraction))

    with pytest.raises(PromptGenerationError) as exc_info:
        orchestrator.generate_task_prompt_from_text("Analyze Site A.", _TEMPLATE_URI)

    assert exc_info.value.code_str == "slot.not_provided"
    failed = exc_info.value.failed_parameters
    assert [error.slot_name for error in failed] == ["site"]
    # The slot label is the slot description when one exists (Java ``slotLabel``).
    assert failed[0].facts == {"slot_label": "Site name"}


def test_render_failure_surfaces_render_failed_with_template_uri_fact() -> None:
    class RaisingRenderer:
        def render(self, **kwargs: Any) -> str:
            raise TaskPromptRenderError("Unknown slot in template.")

    extractor = RecordingSlotExtractor(_success_extraction())
    orchestrator = PromptGenerationOrchestrator(
        config=PromptRuntimeConfig(language="en-US", source_type="packaged"),
        resource_access=FakePromptResourceAccess(
            template_text="Site: {site}",
            slot_json_schema=_SLOT_JSON_SCHEMA,
            system_prompt="Extract slots.",
            user_prompt="Return slots.",
        ),
        scenario_resolver=object(),
        slot_extractor=extractor,
        renderer=RaisingRenderer(),
    )

    with pytest.raises(PromptGenerationError) as exc_info:
        orchestrator.generate_task_prompt_from_text("Analyze Site A.", _TEMPLATE_URI)

    assert exc_info.value.code_str == "template.render_failed"
    assert exc_info.value.facts["template_uri"] == _TEMPLATE_URI
    assert "Unknown slot in template." in str(exc_info.value.facts.get("reason", ""))


def test_from_text_rejects_oversized_input_before_any_llm_call() -> None:
    extractor = RecordingSlotExtractor(_success_extraction())
    orchestrator = _build_orchestrator(slot_extractor=extractor)

    with pytest.raises(PromptGenerationError) as exc_info:
        orchestrator.generate_task_prompt_from_text("x" * 20000, _TEMPLATE_URI)

    assert exc_info.value.code_str == "input.text_too_long"
    assert extractor.call_count == 0


@pytest.mark.parametrize("method_name", [family[1] for family in FAMILIES], ids=[f[0] for f in FAMILIES])
def test_from_text_rejects_null_and_malformed_template_uri(method_name: str) -> None:
    orchestrator = _build_orchestrator(slot_extractor=RecordingSlotExtractor(_success_extraction()))

    with pytest.raises(TypeError, match="templateUri"):
        getattr(orchestrator, method_name)("text", None)
    with pytest.raises(ValueError, match="Unparseable template URI"):
        getattr(orchestrator, method_name)("text", "not-a-uri")


@pytest.mark.parametrize("method_name", [family[2] for family in FAMILIES], ids=[f[0] for f in FAMILIES])
def test_from_data_rejects_null_data_null_schema_and_empty_schema(method_name: str) -> None:
    orchestrator = _build_orchestrator(slot_extractor=RecordingSlotExtractor(_success_extraction()))

    with pytest.raises(TypeError, match="data"):
        getattr(orchestrator, method_name)(None, {"site": "string"}, _TEMPLATE_URI)
    with pytest.raises(TypeError, match="schema"):
        getattr(orchestrator, method_name)({"site": "Site A"}, None, _TEMPLATE_URI)
    with pytest.raises(ValueError, match="Data schema must not be empty"):
        getattr(orchestrator, method_name)({"site": "Site A"}, {}, _TEMPLATE_URI)


@pytest.mark.parametrize(
    ("method_name", "arguments"),
    [(family[1], (None, None)) for family in FAMILIES] + [(family[2], (None, None, None)) for family in FAMILIES],
    ids=[f"{f[0]}-from-text" for f in FAMILIES] + [f"{f[0]}-from-data" for f in FAMILIES],
)
def test_facade_checks_input_arguments_before_the_template_uri(method_name: str, arguments: tuple[object, ...]) -> None:
    """The facade rejects a null input before parsing the template URI (Java facade order).

    Java's ``A2ATClient`` runs ``Objects.requireNonNull(text/data/schema)`` before
    ``parseTemplateUri``, so a doubly-null call reports the input argument, not the URI.
    """
    from unittest.mock import patch

    from a2a_t.client.a2at_client import A2ATClient
    from a2a_t.llm.models import LLMClientConfig
    from tests.support import TEST_ENV_PATH

    llm_config = LLMClientConfig(
        provider="openai",
        model="gpt-4o-mini",
        api_key="sk-test",
        base_url=None,
        history_window=10,
        max_tokens=None,
        temperature=None,
        timeout_seconds=None,
        session_max_total=300,
        session_max_per_provider=100,
    )

    with (
        patch("a2a_t.client.a2at_client._default_env_path", return_value=TEST_ENV_PATH),
        patch("a2a_t.client.a2at_client.LLMConfigLoader.load", return_value=llm_config),
        patch("a2a_t.client.a2at_client.LLMClientFactory.create", return_value=object()),
        patch("a2a_t.client.a2at_client.PromptGenerationOrchestratorBuilder"),
    ):
        client = A2ATClient()

        with pytest.raises(TypeError, match="^(text|data)$"):
            getattr(client, method_name)(*arguments)


def test_from_data_with_real_extractor_and_mock_llm_renders_packaged_template() -> None:
    from a2a_t.common.prompt_resources import create
    from a2a_t.llm.models import LLMResponse
    from a2a_t.prompt.analysis import SlotExtractor
    from a2a_t.prompt.task_rendering import TaskPromptRenderer

    class MockLLM:
        def __init__(self, content: str) -> None:
            self._content = content
            self.calls: list[dict[str, object]] = []

        def structured(
            self,
            *,
            messages: list[dict[str, str]],
            json_schema: dict[str, object],
            temperature: float | None = None,
            max_tokens: int | None = None,
        ) -> LLMResponse:
            self.calls.append({"messages": messages, "json_schema": json_schema})
            return LLMResponse(content=self._content, model="mock", usage={}, metadata={})

    config = PromptRuntimeConfig(language="en-US", source_type="packaged")
    access = create(config)
    mock_llm = MockLLM(
        '{"slots": {"operation_type": "RAN energy saving", "task_description": "Analyze Site A", '
        '"task_object": "Site A", "task_target": "Reduce energy", "task_context": "Night window", '
        '"expected_output": "Report"}, "slot_errors": []}'
    )
    orchestrator = PromptGenerationOrchestrator(
        config=config,
        resource_access=access,
        scenario_resolver=object(),
        slot_extractor=SlotExtractor(llm_client=mock_llm),
        renderer=TaskPromptRenderer(),
    )

    result = orchestrator.generate_task_prompt_from_data_with_schema(
        {"operation_type": "RAN energy saving"},
        {"operation_type": "the operation type"},
        "Task-T/network-layer/ran-energy-saving/v1",
    )

    assert result.template_uri == "Task-T/network-layer/ran-energy-saving/v1"
    assert result.extension_uri == TASK_T_EXTENSION_URI
    assert "RAN energy saving" in (result.prompt_text or "")
    assert len(mock_llm.calls) == 1
    # The schema-guided leg appends the [data_schema] section to the extraction message.
    user_message = mock_llm.calls[0]["messages"][1]["content"]
    assert "[data_schema]" in user_message
    assert '"operation_type"' in user_message
