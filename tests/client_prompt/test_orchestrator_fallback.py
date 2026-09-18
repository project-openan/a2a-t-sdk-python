"""End-to-end prompt generation over a real local resource root (D31 access layer).

Replaces the interim loader-fallback tests: ``local_file`` mode serves routed resources from the
frozen snapshot of the configured root with the built-in packaged fallback (Java ADR 0005 overlay)
— a resource missing locally is served from the package with a one-time warning — while the
instruction prompts always come from the package. A resource missing both locally and in the
package still fails fast with the catalog-coded generation failure.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = PROJECT_ROOT / "src"

if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))


from a2a_t.client.prompt_generation.prompt_generation_orchestrator import PromptGenerationOrchestrator
from a2a_t.common.prompt_resources import create
from a2a_t.config.models import PromptRuntimeConfig
from a2a_t.core.errors.catalog import ErrorCatalog
from a2a_t.core.errors.exceptions import A2ATError
from a2a_t.llm.models import LLMResponse
from a2a_t.prompt.analysis import ScenarioRecognizer, ScenarioResolutionOrchestrator, SlotExtractor
from tests.support import FakePromptResourceAccess

_SCENARIO_ENTRY = {
    "scenario_code": "ran-energy-saving",
    "scenario_name": "Energy Saving",
    "description": "Used for energy saving analysis.",
    "example": "Analyze site power usage and suggest optimization.",
}

_SLOT_JSON_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "properties": {"site": {"type": "string", "examples": ["Site A"]}},
    "required": ["site"],
}


class FakeSequencedLLMClient:
    def __init__(self, response_texts: list[str]) -> None:
        self._response_texts = list(response_texts)
        self.calls: list[dict[str, object]] = []

    def structured(
        self, *, messages: list[dict[str, str]], json_schema: dict[str, object], **kwargs: object
    ) -> LLMResponse:
        self.calls.append({"messages": messages, "json_schema": json_schema, "kwargs": kwargs})
        return LLMResponse(
            content=self._response_texts.pop(0),
            model="fake-model",
            usage={},
            metadata={},
        )


def _write_resource_file(root: Path, relative_path: str, content: str) -> None:
    path = root / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _write_english_business_resources(root: Path) -> None:
    _write_resource_file(
        root,
        "scenarios/en-US/scenarios.json",
        json.dumps({"scenarios": [_SCENARIO_ENTRY]}, ensure_ascii=True),
    )
    _write_resource_file(
        root,
        "templates/Task-T/network-layer/ran-energy-saving/v1/en-US/template.md",
        "Site: {site}\nNotes: {additional_notes}",
    )
    _write_resource_file(
        root,
        "slots/Task-T/network-layer/ran-energy-saving/v1/en-US/slot.json",
        json.dumps(_SLOT_JSON_SCHEMA, ensure_ascii=True),
    )


def _build_orchestrator(root: Path, *, language: str, llm_client: Any) -> PromptGenerationOrchestrator:
    access = create(PromptRuntimeConfig(language=language, source_type="local_file", local_root_dir=str(root)))
    return PromptGenerationOrchestrator(
        config=PromptRuntimeConfig(language=language),
        resource_access=access,
        scenario_resolver=ScenarioResolutionOrchestrator(
            config=PromptRuntimeConfig(language=language),
            resource_access=access,
            scenario_recognizer=ScenarioRecognizer(llm_client=llm_client),
        ),
        slot_extractor=SlotExtractor(llm_client=llm_client),
    )


def test_generate_falls_back_to_the_packaged_resources_when_requested_language_resources_are_missing(
    tmp_path: Path,
) -> None:
    _write_english_business_resources(tmp_path)
    llm_client = FakeSequencedLLMClient(
        [
            '{"matched": true, "scenario_code": "ran-energy-saving", "error_message": null}',
            '{"slots": {"site": "Site A", "additional_notes": null}, "slot_errors": []}',
        ]
    )

    orchestrator = _build_orchestrator(tmp_path, language="zh-CN", llm_client=llm_client)

    result = orchestrator.generate("Analyze Site A.")

    assert result.success is True
    assert result.failure is None
    # The packaged zh-CN copy is served (its section headers are in the prompt), not the local
    # en-US template.
    assert "操作类型" in result.prompt_text
    assert "Site:" not in result.prompt_text


def test_generate_returns_prompt_resource_load_error_when_packaged_prompts_are_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_english_business_resources(tmp_path)
    llm_client = FakeSequencedLLMClient(
        [
            '{"matched": true, "scenario_code": "ran-energy-saving", "error_message": null}',
            '{"slots": {"site": "Site A", "additional_notes": null}, "slot_errors": []}',
        ]
    )

    def raising_read_text(self: object, key: Any) -> str:
        if key.relative_path().startswith("prompt_resources/prompts/"):
            raise A2ATError(f"Failed to read resource '{key.relative_path()}'.")
        return _packaged_read_text(self, key)

    from a2a_t.common.prompt_resources.packaged_access import PackagedResourceReader

    _packaged_read_text = PackagedResourceReader.read_text
    monkeypatch.setattr(PackagedResourceReader, "read_text", raising_read_text)

    orchestrator = _build_orchestrator(tmp_path, language="en-US", llm_client=llm_client)

    result = orchestrator.generate("Analyze Site A.")

    assert result.success is False
    assert result.failure is not None
    assert result.failure.code == ErrorCatalog.TEMPLATE_LOAD_FAILED.value
    assert result.failure.stage == "preparation"
    assert result.failure.message == (
        "Failed to read template resource 'prompt_resources/prompts/scenario_recognition/en-US/system.md'"
    )


def test_generate_uses_the_routed_local_business_resources_and_packaged_prompts(tmp_path: Path) -> None:
    _write_english_business_resources(tmp_path)
    _write_resource_file(
        tmp_path,
        "templates/Task-T/network-layer/ran-energy-saving/v1/en-US/template.md",
        "LOCAL Site: {site}",
    )
    llm_client = FakeSequencedLLMClient(
        [
            '{"matched": true, "scenario_code": "ran-energy-saving", "error_message": null}',
            '{"slots": {"site": "Site A", "additional_notes": null}, "slot_errors": []}',
        ]
    )

    orchestrator = _build_orchestrator(tmp_path, language="en-US", llm_client=llm_client)

    result = orchestrator.generate("Analyze Site A.")

    assert result.success is True
    assert result.prompt_text == "LOCAL Site: Site A"
    # The instruction prompts still come from the packaged SDK contract, never the local root.
    system_messages = [
        message
        for call in llm_client.calls
        for message in call["messages"]  # type: ignore[index,union-attr]
        if message["role"] == "system"
    ]
    assert len(system_messages) == 2
    from a2a_t.common.prompt_resources import PackagedPromptResourceAccess

    packaged = PackagedPromptResourceAccess()
    assert system_messages[0]["content"] == packaged.load_prompt("scenario_recognition", "en-US", "system.md")
    assert system_messages[1]["content"] == packaged.load_prompt("slot_extraction", "en-US", "system.md")


def test_generate_falls_back_to_the_packaged_template_when_the_local_template_is_missing(tmp_path: Path) -> None:
    _write_resource_file(
        tmp_path,
        "scenarios/en-US/scenarios.json",
        json.dumps({"scenarios": [_SCENARIO_ENTRY]}, ensure_ascii=True),
    )
    _write_resource_file(
        tmp_path,
        "slots/Task-T/network-layer/ran-energy-saving/v1/en-US/slot.json",
        json.dumps(_SLOT_JSON_SCHEMA, ensure_ascii=True),
    )
    llm_client = FakeSequencedLLMClient(
        [
            '{"matched": true, "scenario_code": "ran-energy-saving", "error_message": null}',
            '{"slots": {"site": "Site A", "additional_notes": null}, "slot_errors": []}',
        ]
    )

    orchestrator = _build_orchestrator(tmp_path, language="en-US", llm_client=llm_client)

    result = orchestrator.generate("Analyze Site A.")

    assert result.success is True
    assert result.failure is None
    # The packaged en-US copy is served (its section headers are in the prompt), not a local template.
    assert "Operation Type" in result.prompt_text
    assert "Site:" not in result.prompt_text


def test_generate_still_fails_when_the_template_is_missing_everywhere(tmp_path: Path) -> None:
    """A template missing everywhere keeps the fail-fast contract."""
    _write_resource_file(
        tmp_path,
        "scenarios/en-US/scenarios.json",
        json.dumps(
            {
                "scenarios": [
                    {
                        "scenario_code": "ghost-scenario",
                        "scenario_name": "Ghost Scenario",
                        "description": "A scenario unknown to the packaged resources.",
                        "example": "Trigger the ghost scenario.",
                    }
                ]
            },
            ensure_ascii=True,
        ),
    )
    _write_resource_file(
        tmp_path,
        "slots/Task-T/network-layer/ghost-scenario/v1/en-US/slot.json",
        json.dumps(_SLOT_JSON_SCHEMA, ensure_ascii=True),
    )
    llm_client = FakeSequencedLLMClient(['{"matched": true, "scenario_code": "ghost-scenario", "error_message": null}'])

    orchestrator = _build_orchestrator(tmp_path, language="en-US", llm_client=llm_client)

    result = orchestrator.generate("Analyze Site A.")

    assert result.success is False
    assert result.failure is not None
    assert result.failure.code == ErrorCatalog.TEMPLATE_NOT_FOUND.value
    assert result.failure.stage == "preparation"


def test_fake_access_stays_usable_for_pipeline_wiring_checks() -> None:
    """The shared fake mirrors the access surface the pipeline depends on."""
    access = FakePromptResourceAccess(
        template_text="Site: {site}",
        slot_json_schema=_SLOT_JSON_SCHEMA,
        system_prompt="system",
        user_prompt="user",
    )

    assert access.template_text("ran-energy-saving", "en-US") == "Site: {site}"
    assert access.slot_schema("ran-energy-saving", "en-US")["required"] == ["site"]
    assert access.load_prompt("slot_extraction", "en-US", "system.md") == "system"
    assert access.load_prompt("slot_extraction", "en-US", "user.md") == "user"
