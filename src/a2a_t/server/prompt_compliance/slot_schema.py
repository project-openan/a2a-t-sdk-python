from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError, model_validator

from a2a_t.server.prompt_compliance.errors import SlotSchemaLoadError, SlotSchemaValidationError
from a2a_t.server.prompt_compliance.models import PromptIdentity, SlotSchemaConfig


class SlotPromptIdentity(BaseModel):
    """存储在 slot.json 中的 Prompt 身份 / Prompt identity stored in slot.json."""

    name: str
    language: str
    version: str


class SlotRange(BaseModel):
    """数值或长度类校验的范围约束 / Range constraint for numeric or length-based validations."""

    min: float | int | None = None
    max: float | int | None = None


class SlotDefinition(BaseModel):
    """单个已声明的槽位约束 / One declared slot constraint."""

    name: str
    required: bool = False
    type: Literal["string", "number", "integer", "boolean", "enum", "list"]
    allowed_values: list[Any] | None = None
    range: SlotRange | None = None
    pattern: str | None = None

    @model_validator(mode="after")
    def validate_semantics(self) -> "SlotDefinition":
        if self.type == "enum" and not self.allowed_values:
            raise ValueError("enum slot must define allowed_values")
        if self.allowed_values is not None and self.type != "enum":
            raise ValueError("allowed_values is only supported for enum slots")
        if self.pattern is not None and self.type not in {"string", "enum"}:
            raise ValueError("pattern is only supported for string or enum slots")
        return self


class DependencyCondition(BaseModel):
    """条件规则触发器 / Conditional rule trigger."""

    slot: str
    equals: Any


class SlotRule(BaseModel):
    """跨槽位规则定义 / Cross-slot rule definition."""

    type: Literal["dependency"]
    when: DependencyCondition
    requires: list[str] = Field(min_length=1)


class SlotSchema(BaseModel):
    """完整 slot.json 结构 / Full slot.json structure."""

    prompt_identity: SlotPromptIdentity
    slots: list[SlotDefinition]
    rules: list[SlotRule] = Field(default_factory=list)


class SlotSchemaResolver:
    """解析并加载镜像路径下的 slot.json 文件 / Resolve and load mirrored slot.json files."""

    def __init__(self, config: SlotSchemaConfig) -> None:
        self._config = config
        (Path(self._config.root_dir) / self._config.slot_root_name).mkdir(parents=True, exist_ok=True)

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

    def load(self, identity: PromptIdentity) -> SlotSchema:
        path = self.resolve_path(identity)
        if not path.exists():
            raise SlotSchemaLoadError(
                "Slot configuration file does not exist.",
                path=str(path),
                name=identity.name,
                language=identity.language,
                version=identity.version,
            )

        try:
            raw_data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as error:
            raise SlotSchemaValidationError("Slot configuration JSON is invalid.", path=str(path)) from error

        return self.build_slot_schema(raw_data)

    @staticmethod
    def build_slot_schema(raw_data: dict[str, Any]) -> SlotSchema:
        try:
            return SlotSchema.model_validate(raw_data)
        except ValidationError as error:
            raise SlotSchemaValidationError("Slot configuration is invalid.", errors=error.errors()) from error
