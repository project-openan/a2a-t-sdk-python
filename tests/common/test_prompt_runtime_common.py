"""Tests for the shared prompt runtime components built from configuration.

The components builder now wires the single D31 resource access object (plus the stateless
JSON-schema validator); the routing, frozen-snapshot and custom-root warning behavior itself is
covered by ``tests/common/prompt_resources`` — these tests pin what the builder assembles.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from a2a_t.common.prompt_resources import (
    LocalFilePromptResourceAccess,
    PackagedPromptResourceAccess,
    PromptResourceAccess,
)
from a2a_t.common.prompt_runtime import PromptRuntimeComponents, PromptRuntimeComponentsBuilder
from a2a_t.config.models import A2ATConfig, PromptRuntimeConfig
from a2a_t.prompt.validation.json_schema_slot_validator import JsonSchemaSlotValidator

ACCESS_LOGGER = "a2a_t.common.prompt_resources.resource_access"


def _config(local_root_dir: str, *, language: str = "en-US", source_type: str = "local_file") -> A2ATConfig:
    return A2ATConfig(
        prompt=PromptRuntimeConfig(language=language, source_type=source_type, local_root_dir=local_root_dir),
    )


def test_components_builder_creates_the_resource_access_from_the_config(tmp_path: Path) -> None:
    components = PromptRuntimeComponentsBuilder().build(config=_config(str(tmp_path)))

    assert isinstance(components, PromptRuntimeComponents)
    assert isinstance(components.resource_access, LocalFilePromptResourceAccess)
    assert components.resource_access.local_root_dir() == tmp_path
    assert isinstance(components.json_schema_slot_validator, JsonSchemaSlotValidator)


def test_components_builder_creates_packaged_access_for_the_packaged_source_type() -> None:
    components = PromptRuntimeComponentsBuilder().build(config=_config("", source_type="packaged"))

    assert isinstance(components.resource_access, PackagedPromptResourceAccess)


def test_components_builder_reuses_an_injected_resource_access(tmp_path: Path) -> None:
    injected = PackagedPromptResourceAccess()

    components = PromptRuntimeComponentsBuilder().build(
        config=_config(str(tmp_path)),
        resource_access=injected,
    )

    assert components.resource_access is injected


def test_components_builder_requires_an_existing_local_root(tmp_path: Path) -> None:
    from a2a_t.config.errors import ConfigError

    missing = tmp_path / "does-not-exist"
    with pytest.raises(ConfigError):
        PromptRuntimeComponentsBuilder().build(config=_config(str(missing)))


def test_components_builder_loads_packaged_prompts_even_when_custom_root_has_no_prompts(tmp_path: Path) -> None:
    components = PromptRuntimeComponentsBuilder().build(config=_config(str(tmp_path)))

    access = components.resource_access
    assert isinstance(access, PromptResourceAccess)
    system_prompt = access.load_prompt("scenario_recognition", "en-US", "system.md")
    user_prompt = access.load_prompt("scenario_recognition", "en-US", "user.md")

    assert system_prompt.strip()
    assert user_prompt.strip()


def test_components_builder_warns_when_custom_root_contains_prompts_directory(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    prompts_dir = tmp_path / "prompts" / "scenario_recognition" / "en-US"
    prompts_dir.mkdir(parents=True)
    (prompts_dir / "system.md").write_text("custom system", encoding="utf-8")

    with caplog.at_level(logging.WARNING, logger=ACCESS_LOGGER):
        components = PromptRuntimeComponentsBuilder().build(config=_config(str(tmp_path)))

    warnings = [record.getMessage() for record in caplog.records if record.levelno == logging.WARNING]
    assert any("prompt_resource_local_directories_ignored" in message for message in warnings), warnings
    # The local copy is ignored: the packaged SDK contract is still served.
    assert components.resource_access.load_prompt("scenario_recognition", "en-US", "system.md") != "custom system"


def test_components_builder_does_not_warn_when_local_root_is_the_packaged_root(
    caplog: pytest.LogCaptureFixture,
) -> None:
    from a2a_t.config.models import PromptRuntimeConfig as _PromptRuntimeConfig

    # The pre-1.1.0 default combination, kept explicit after the D10 step-2 flip: local_file mode
    # whose resolved default root IS the packaged tree carries no user intent, so no warning fires.
    config = A2ATConfig(
        prompt=_PromptRuntimeConfig(source_type="local_file"),
    )

    with caplog.at_level(logging.WARNING, logger=ACCESS_LOGGER):
        PromptRuntimeComponentsBuilder().build(config=config)

    warnings = [record.getMessage() for record in caplog.records if record.levelno == logging.WARNING]
    assert not any("prompt_resource_local_directories_ignored" in message for message in warnings), warnings
