"""Tests of the client-side Task-T content validation facade."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = PROJECT_ROOT / "src"

if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from a2a_t.client.a2at_client import A2ATClient
from a2a_t.core.standard_templates import ENERGY_SAVING_URI, TASK_EXTENSION_NAME
from a2a_t.core.template_uri import TemplateUri
from a2a_t.core.validation_pipeline import FilledParamData
from tests.support import TEST_ENV_PATH

_PARAM_SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {"region": {"type": "string"}},
    "required": ["region"],
}


class FakeTaskValidator:
    """Records delegation without running the LLM-backed pipeline."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, object, TemplateUri]] = []

    def validate(
        self,
        prompt: str,
        schema: object,
        template_uri: TemplateUri,
    ) -> FilledParamData:
        self.calls.append((prompt, schema, template_uri))
        return FilledParamData(data={"region": "Region A"})


def _client_with(validator: FakeTaskValidator) -> A2ATClient:
    client = A2ATClient(env_path=TEST_ENV_PATH)
    client._task_content_validator = validator
    return client


def test_client_validation_delegates_to_the_content_pipeline() -> None:
    validator = FakeTaskValidator()
    client = _client_with(validator)

    filled = client.validate_task_prompt_and_data_filling(
        "The area is Region A.", _PARAM_SCHEMA, ENERGY_SAVING_URI
    )

    assert filled.data == {"region": "Region A"}
    assert len(validator.calls) == 1
    prompt, schema, template_uri = validator.calls[0]
    assert prompt == "The area is Region A."
    assert schema is _PARAM_SCHEMA
    assert isinstance(template_uri, TemplateUri)
    assert template_uri.uri == ENERGY_SAVING_URI


def test_client_validation_rejects_a_missing_template_uri() -> None:
    client = _client_with(FakeTaskValidator())

    with pytest.raises(TypeError):
        client.validate_task_prompt_and_data_filling("prompt", _PARAM_SCHEMA, None)  # type: ignore[arg-type]


def test_client_validation_rejects_a_malformed_template_uri() -> None:
    client = _client_with(FakeTaskValidator())

    with pytest.raises(ValueError):
        client.validate_task_prompt_and_data_filling("prompt", _PARAM_SCHEMA, "not-a-uri")


def test_the_task_validator_is_built_once() -> None:
    validator = FakeTaskValidator()
    client = A2ATClient(env_path=TEST_ENV_PATH)

    with patch(
        "a2a_t.server.prompt_compliance.content_validator.build_content_validator",
        return_value=validator,
    ) as build:
        client.validate_task_prompt_and_data_filling("one", _PARAM_SCHEMA, ENERGY_SAVING_URI)
        client.validate_task_prompt_and_data_filling("two", _PARAM_SCHEMA, ENERGY_SAVING_URI)

    build.assert_called_once()
    assert build.call_args.kwargs["extension_name"] == TASK_EXTENSION_NAME
    assert len(validator.calls) == 2
