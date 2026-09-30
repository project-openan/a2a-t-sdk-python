from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from a2a_t.observability.attributes import (
    extension_name_from_uri,
    extract_negotiation_attributes,
    extract_request_attributes,
    normalize_metadata,
)


class _FakeValue:
    """Structural stand-in for google.protobuf.Value."""

    def __init__(self, which: str, value: Any) -> None:
        self._which = which
        self._value = value

    def WhichOneof(self, name: str) -> str | None:  # noqa: N802 - mirrors protobuf API
        return self._which if name == "kind" else None

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"):
            raise AttributeError(name)
        if name in ("string_value", "number_value", "bool_value"):
            return self._value if name == self._which else None
        if name in ("struct_value", "list_value"):
            return self._value if name == self._which else None
        raise AttributeError(name)


class _FakeListValue:
    def __init__(self, values: list[_FakeValue]) -> None:
        self.values = values


def _wrap(value: Any) -> _FakeValue:
    if isinstance(value, dict):
        return _FakeValue("struct_value", _FakeStruct({k: _wrap(v) for k, v in value.items()}))
    if isinstance(value, list):
        return _FakeValue("list_value", _FakeListValue([_wrap(v) for v in value]))
    if isinstance(value, bool):
        return _FakeValue("bool_value", value)
    if isinstance(value, (int, float)):
        return _FakeValue("number_value", value)
    return _FakeValue("string_value", value)


class _FakeStruct:
    def __init__(self, fields: dict[str, _FakeValue]) -> None:
        self.fields = fields


def _struct(data: dict[str, Any]) -> _FakeStruct:
    return _FakeStruct({k: _wrap(v) for k, v in data.items()})


class _FakeMessage:
    def __init__(self, metadata: Any, task_id: str = "", context_id: str = "") -> None:
        self.metadata = metadata
        self.task_id = task_id
        self.context_id = context_id


class _FakeRequest:
    def __init__(self, message: _FakeMessage) -> None:
        self.message = message


def test_extension_name_from_uri() -> None:
    assert extension_name_from_uri(
        "https://projects.tmforum.org/a2aproject/telecommunication/extensions/Task-T/v1"
    ) == "Task-T"
    assert extension_name_from_uri(
        "https://projects.tmforum.org/a2aproject/telecommunication/extensions/Negotiation-T/NL/v1"
    ) == "Negotiation-T"
    assert extension_name_from_uri("https://example.com/other") is None
    assert extension_name_from_uri("") is None


def test_normalize_metadata_dict_struct_none() -> None:
    assert normalize_metadata({"a": 1}) == {"a": 1}
    assert normalize_metadata(None) == {}
    struct = _struct(
        {"negotiationContext": {"id": "N001", "round": 2, "maxRounds": 5, "performative": "PROPOSE"}}
    )
    assert normalize_metadata(struct) == {
        "negotiationContext": {"id": "N001", "round": 2, "maxRounds": 5, "performative": "PROPOSE"}
    }


class _MappingStruct(Mapping):
    """Mimics real google.protobuf.Struct: registers as a Mapping, converts
    scalars on access, but leaves nested containers as raw Struct-like objects."""

    def __init__(self, fields: dict[str, _FakeValue]) -> None:
        self._fields = fields

    def __getitem__(self, key: str) -> Any:
        value = self._fields[key]
        if value._which in ("struct_value", "list_value"):  # noqa: SLF001 - same-module test double
            return getattr(value, value._which)  # noqa: SLF001 - same-module test double
        return value._value  # noqa: SLF001 - same-module test double

    def __iter__(self) -> Any:
        return iter(self._fields)

    def __len__(self) -> int:
        return len(self._fields)


def _wrap_mapping(value: Any) -> _FakeValue:
    """Like _wrap but nested dicts become Mapping-shaped (real protobuf behavior)."""
    if isinstance(value, dict):
        return _FakeValue("struct_value", _MappingStruct({k: _wrap_mapping(v) for k, v in value.items()}))
    return _wrap(value)


def test_normalize_metadata_mapping_struct_deep_converts() -> None:
    struct = _MappingStruct(
        {
            "plain": _wrap("x"),
            "negotiationContext": _wrap_mapping({"id": "N001", "round": 2, "performative": "ACCEPT"}),
            "items": _wrap([1, "two"]),
        }
    )
    assert normalize_metadata(struct) == {
        "plain": "x",
        "negotiationContext": {"id": "N001", "round": 2, "performative": "ACCEPT"},
        "items": [1, "two"],
    }
    assert extract_negotiation_attributes(normalize_metadata(struct))["gen_ai.agent.a2at.negotiation.id"] == "N001"


def test_extract_negotiation_attributes() -> None:
    propose = {"negotiationContext": {"id": "N001", "round": 2, "maxRounds": 5, "performative": "PROPOSE"}}
    assert extract_negotiation_attributes(propose) == {
        "gen_ai.agent.a2at.negotiation.id": "N001",
        "gen_ai.agent.a2at.negotiation.round": 2,
        "gen_ai.agent.a2at.negotiation.max_rounds": 5,
        "gen_ai.agent.a2at.negotiation.performative": "PROPOSE",
    }
    terminal = {"negotiationContext": {"id": "N001", "round": 3, "maxRounds": 5, "performative": "ACCEPT"}}
    assert extract_negotiation_attributes(terminal)["gen_ai.agent.a2at.negotiation.total_rounds"] == 3
    assert extract_negotiation_attributes({}) == {}


def test_negotiation_float_and_bool_wire_numbers() -> None:
    metadata = {"negotiationContext": {"id": "N002", "round": 2.0, "maxRounds": 5.0, "performative": "ACCEPT"}}
    attrs = extract_negotiation_attributes(metadata)
    assert attrs["gen_ai.agent.a2at.negotiation.round"] == 2
    assert attrs["gen_ai.agent.a2at.negotiation.max_rounds"] == 5
    assert attrs["gen_ai.agent.a2at.negotiation.total_rounds"] == 2
    boolean = {"negotiationContext": {"id": "N003", "round": True, "maxRounds": 5, "performative": "PROPOSE"}}
    attrs2 = extract_negotiation_attributes(boolean)
    assert "gen_ai.agent.a2at.negotiation.round" not in attrs2
    assert "gen_ai.agent.a2at.negotiation.total_rounds" not in attrs2


def test_extract_request_attributes_full() -> None:
    request = _FakeRequest(
        _FakeMessage(
            metadata=_struct(
                {
                    "https://projects.tmforum.org/a2aproject/telecommunication/extensions/Notification-T/NL/v1": "prompt",
                    "templateUri": "x",
                    "negotiationContext": {"id": "N001", "round": 1, "maxRounds": 5, "performative": "PROPOSE"},
                }
            ),
            task_id="T1",
            context_id="C1",
        )
    )
    attrs = extract_request_attributes(request, method="send_message")
    assert attrs["gen_ai.operation.name"] == "send_message"
    assert attrs["gen_ai.conversation.id"] == "C1"
    assert attrs["gen_ai.agent.a2at.task.id"] == "T1"
    assert attrs["gen_ai.agent.a2at.extension.name"] == "Notification-T"
    assert attrs["gen_ai.agent.a2at.negotiation.id"] == "N001"


def test_extract_request_attributes_plain() -> None:
    request = _FakeRequest(_FakeMessage(metadata=None))
    attrs = extract_request_attributes(request, method="send_message")
    assert attrs == {"gen_ai.operation.name": "send_message"}


def test_extract_request_attributes_push_url() -> None:
    class _FakeConfig:
        url = "https://hook.example.com/cb"

    class _FakePushRequest:
        push_notification_config = _FakeConfig()

    attrs = extract_request_attributes(_FakePushRequest(), method="create_task_push_notification_config")
    assert attrs["gen_ai.agent.a2at.push.notification.url"] == "https://hook.example.com/cb"


def test_extract_never_raises() -> None:
    class _Broken:
        @property
        def message(self) -> Any:
            raise RuntimeError("boom")

    assert extract_request_attributes(_Broken(), method="send_message") == {
        "gen_ai.operation.name": "send_message"
    }


def test_genai_standard_constants() -> None:
    from a2a_t.observability.attributes import (
        ATTR_GEN_AI_PROVIDER_NAME,
        ATTR_GEN_AI_REQUEST_MODEL,
        ATTR_GEN_AI_RESPONSE_MODEL,
        ATTR_GEN_AI_TOKEN_TYPE,
        ATTR_GEN_AI_USAGE_INPUT_TOKENS,
        ATTR_GEN_AI_USAGE_OUTPUT_TOKENS,
    )

    assert ATTR_GEN_AI_USAGE_INPUT_TOKENS == "gen_ai.usage.input_tokens"
    assert ATTR_GEN_AI_USAGE_OUTPUT_TOKENS == "gen_ai.usage.output_tokens"
    assert ATTR_GEN_AI_REQUEST_MODEL == "gen_ai.request.model"
    assert ATTR_GEN_AI_RESPONSE_MODEL == "gen_ai.response.model"
    assert ATTR_GEN_AI_TOKEN_TYPE == "gen_ai.token.type"
    assert ATTR_GEN_AI_PROVIDER_NAME == "gen_ai.provider.name"


def test_default_regex_constants() -> None:
    from a2a_t.observability.attributes import (
        DEFAULT_AUTHORIZATION_POLICY_OPERATION_TYPE_REGEX,
        DEFAULT_NOTIFICATION_TOPIC_REGEX,
        DEFAULT_TASK_TYPE_REGEX,
    )

    assert DEFAULT_TASK_TYPE_REGEX is not None
    assert DEFAULT_NOTIFICATION_TOPIC_REGEX is not None
    assert DEFAULT_AUTHORIZATION_POLICY_OPERATION_TYPE_REGEX is not None
