from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, ValidationError, model_validator

from a2a_t.server.prompt_compliance.errors import SlotConfigLoadError, SlotConfigValidationError
from a2a_t.server.prompt_compliance.models import PromptIdentity, SlotSchemaConfig


class SlotPromptIdentity(BaseModel):
    """Prompt identity stored in slot.yaml."""

    name: str
    language: str
    version: str


class SlotRange(BaseModel):
    """Range constraint for numeric or length-based validations."""

    min: float | int | None = None
    max: float | int | None = None


class SlotDefinition(BaseModel):
    """One declared slot constraint."""

    name: str
    required: bool = False
    type: Literal["string", "number", "integer", "boolean", "enum", "list"]
    allowed_values: list[Any] | None = None
    range: SlotRange | None = None
    pattern: str | None = None

    @model_validator(mode="after")
    def validate_semantics(self) -> SlotDefinition:
        if self.type == "enum" and not self.allowed_values:
            raise ValueError("enum slot must define allowed_values")
        if self.allowed_values is not None and self.type != "enum":
            raise ValueError("allowed_values is only supported for enum slots")
        if self.pattern is not None and self.type not in {"string", "enum"}:
            raise ValueError("pattern is only supported for string or enum slots")
        return self


class DependencyCondition(BaseModel):
    """Conditional rule trigger."""

    slot: str
    equals: Any


class SlotRule(BaseModel):
    """Cross-slot rule definition."""

    type: Literal["dependency"]
    when: DependencyCondition
    requires: list[str] = Field(min_length=1)


class SlotConfig(BaseModel):
    """Full slot.yaml structure."""

    prompt_identity: SlotPromptIdentity
    slots: list[SlotDefinition]
    rules: list[SlotRule] = Field(default_factory=list)


class SlotConfigResolver:
    """Resolve and load mirrored slot.yaml files."""

    def __init__(self, config: SlotSchemaConfig) -> None:
        self._config = config

    def resolve_path(self, identity: PromptIdentity) -> Path:
        root_dir = Path(self._config.root_dir)
        return (
            root_dir
            / self._config.slot_root_name
            / identity.name
            / identity.version
            / identity.language
            / self._config.file_name
        )

    def load(self, identity: PromptIdentity) -> SlotConfig:
        path = self.resolve_path(identity)
        if not path.exists():
            raise SlotConfigLoadError(
                "Slot configuration file does not exist.",
                path=str(path),
                name=identity.name,
                language=identity.language,
                version=identity.version,
            )

        try:
            raw_data = yaml.safe_load(path.read_text(encoding="utf-8"))
        except yaml.YAMLError as error:
            raise SlotConfigValidationError("Slot configuration YAML is invalid.", path=str(path)) from error

        return self.build_slot_config(raw_data)

    @staticmethod
    def build_slot_config(raw_data: dict[str, Any]) -> SlotConfig:
        try:
            return SlotConfig.model_validate(raw_data)
        except ValidationError as error:
            raise SlotConfigValidationError("Slot configuration is invalid.", errors=error.errors()) from error
