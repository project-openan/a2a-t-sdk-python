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


def test_truncation_not_applied_within_limit() -> None:
    from a2a_t.observability.payload import extract_payload

    config = _config(extract_request=True, payload_log_max_length=4096)
    payload = extract_payload("tiny", config, is_request=True)
    assert payload is not None
    assert "[truncated]" not in payload


def test_extract_returns_raw_serialization_only() -> None:
    """M3: extract_payload 只负责序列化——截断与脱敏统一收敛到 prepare_payload
    （每通道一次、先脱敏后截断），跨截断边界的 secret 不再因先截断而逃逸脱敏。"""
    from a2a_t.observability.payload import extract_payload

    config = _config(
        extract_request=True,
        payload_log_max_length=10,
        payload_redactor=lambda s: s.replace("0", "X"),
    )
    payload = extract_payload("0123456789abcdef", config, is_request=True)
    assert payload == '{"raw": "0123456789abcdef"}'


def test_prepare_payload_redacts_before_truncation() -> None:
    """M3 安全顺序：先脱敏后截断——截断只删字符不会暴露新内容；secret 跨越
    max_length 边界被截成两半后，redactor 的完整模式无法命中，旧实现（先截断）
    会让部分明文 secret 存活并进入日志/span 属性。"""
    from a2a_t.observability.payload import prepare_payload

    secret = "sk-abcdef0123456789"
    config = _config(payload_log_max_length=25, payload_redactor=lambda s: s.replace(secret, "***"))
    prepared = prepare_payload("x" * 18 + secret + "y" * 10, config)
    assert "sk-a" not in prepared
    assert prepared.endswith("[truncated]")


def test_prepare_payload_truncation_appends_suffix() -> None:
    from a2a_t.observability.payload import prepare_payload

    config = _config(payload_log_max_length=10)
    assert prepare_payload("0123456789abcdef", config) == "0123456789[truncated]"


def test_prepare_payload_within_limit_redacted_only() -> None:
    from a2a_t.observability.payload import prepare_payload

    config = _config(payload_log_max_length=4096, payload_redactor=lambda s: s.replace("secret", "***"))
    assert prepare_payload("has-secret-inside", config) == "has-***-inside"


def test_prepare_payload_redactor_failure_yields_placeholder() -> None:
    from a2a_t.observability.payload import prepare_payload

    def boom(_payload: str) -> str:
        raise RuntimeError("boom")

    config = _config(payload_redactor=boom)
    assert prepare_payload("anything", config) == "[redaction-failed]"


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
