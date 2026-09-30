from __future__ import annotations

import json


def _config(**kwargs):
    from a2a_t.observability.config import A2ATObservabilityConfig

    return A2ATObservabilityConfig(**kwargs)


def _proto_message():
    from google.protobuf import descriptor_pb2

    msg = descriptor_pb2.FieldDescriptorProto(name="type_name", number=6, type=11, type_name="t")
    return msg


def test_returns_none_when_extract_request_disabled() -> None:
    from a2a_t.observability.payload import extract_payload

    config = _config(extract_request=False, extract_response=True)
    assert extract_payload(_proto_message(), config, is_request=True) is None


def test_returns_none_when_extract_response_disabled() -> None:
    from a2a_t.observability.payload import extract_payload

    config = _config(extract_request=True, extract_response=False)
    assert extract_payload(_proto_message(), config, is_request=False) is None


def test_env_switch_respected(monkeypatch) -> None:
    from a2a_t.observability.payload import extract_payload

    monkeypatch.setenv("A2AT_EXTRACT_REQUEST", "true")
    monkeypatch.delenv("A2AT_EXTRACT_RESPONSE", raising=False)
    config = _config()
    assert extract_payload(_proto_message(), config, is_request=True) is not None
    assert extract_payload(_proto_message(), config, is_request=False) is None


def test_protobuf_message_serialized_to_json() -> None:
    from a2a_t.observability.payload import extract_payload

    config = _config(extract_request=True)
    payload = extract_payload(_proto_message(), config, is_request=True)
    assert payload is not None
    parsed = json.loads(payload)
    assert parsed == {"name": "type_name", "number": 6, "type": "TYPE_MESSAGE", "type_name": "t"}


def test_snake_case_field_names_preserved() -> None:
    from a2a_t.observability.payload import extract_payload

    config = _config(extract_request=True)
    payload = extract_payload(_proto_message(), config, is_request=True)
    assert payload is not None
    assert "type_name" in json.loads(payload)


def test_non_protobuf_falls_back_to_raw() -> None:
    from a2a_t.observability.payload import extract_payload

    config = _config(extract_request=True)
    payload = extract_payload("plain-event", config, is_request=True)
    assert payload is not None
    assert json.loads(payload) == {"raw": "plain-event"}


def test_truncation_appends_suffix() -> None:
    from a2a_t.observability.payload import extract_payload

    config = _config(extract_request=True, payload_log_max_length=10)
    payload = extract_payload("0123456789abcdef", config, is_request=True)
    assert payload == '{"raw": "0' + "[truncated]"


def test_truncation_not_applied_within_limit() -> None:
    from a2a_t.observability.payload import extract_payload

    config = _config(extract_request=True, payload_log_max_length=4096)
    payload = extract_payload("tiny", config, is_request=True)
    assert payload is not None
    assert "[truncated]" not in payload


def test_redactor_applied() -> None:
    from a2a_t.observability.payload import extract_payload

    config = _config(extract_request=True, payload_redactor=lambda s: s.replace("secret", "***"))
    payload = extract_payload({"raw": "has-secret-inside"}, config, is_request=True)
    assert payload is not None
    assert "secret" not in payload
    assert "***" in payload


def test_redactor_failure_yields_placeholder() -> None:
    from a2a_t.observability.payload import extract_payload

    def boom(_payload: str) -> str:
        raise RuntimeError("boom")

    config = _config(extract_request=True, payload_redactor=boom)
    payload = extract_payload("anything", config, is_request=True)
    assert payload == "[redaction-failed]"


def test_ensure_ascii_false_keeps_unicode() -> None:
    from a2a_t.observability.payload import extract_payload

    config = _config(extract_request=True)
    payload = extract_payload("任务类型", config, is_request=True)
    assert payload is not None
    assert "任务类型" in payload


def test_never_raises_on_failing_str() -> None:
    from a2a_t.observability.payload import extract_payload

    class Exploding:
        def __str__(self) -> str:
            raise ValueError("nope")

    config = _config(extract_request=True)
    assert extract_payload(Exploding(), config, is_request=True) is None


def test_truncation_before_redaction() -> None:
    from a2a_t.observability.payload import extract_payload

    seen: list[str] = []

    def redactor(payload: str) -> str:
        seen.append(payload)
        return payload

    config = _config(extract_request=True, payload_log_max_length=5, payload_redactor=redactor)
    extract_payload("0123456789", config, is_request=True)
    assert seen == ['{"raw[truncated]']
