"""Tests of the traced SDK LLM providers (``shared.traced_llm``)."""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any, cast

import pytest
from a2a_t.llm.errors import LLMRuntimeError
from a2a_t.llm.models import LLMClientConfig, LLMResponse
from a2a_t.llm.providers.openai import OpenAIClient
from demo.scripted_llm import ScriptedLLMClient
from demo.traced_scripted import TracedScriptedClient, register_traced_scripted_provider
from shared.adk_tracing import serialize_llm_response
from shared.trace import (
    PHASE_NEGOTIATION,
    PHASE_RESOLUTION,
    PHASE_TASK_PROMPT,
    emit_span,
    phase_for_stage,
    set_global_sink,
    trace_scope,
    trace_stage,
)
from shared.traced_llm import (
    SCRIPTED_TOKENS_LABEL,
    TRACED_OPENAI_PROVIDER,
    TRACED_SCRIPTED_PROVIDER,
    TracedOpenAIClient,
    _token_map,
    is_traced_provider_registered,
    register_traced_providers,
    sdk_provider_name,
)

#: Schema of the scripted semantic-validation call (routable without any scenario marker).
_VALIDATION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"semantic_verdict": {"type": "boolean"}, "negotiation_type": {"type": "string"}},
}


def _client_config(model: str = "scripted") -> LLMClientConfig:
    """Build one SDK LLM client configuration for the scripted provider."""
    return LLMClientConfig(
        provider="scripted",
        model=model,
        api_key="not-needed",
        base_url="http://127.0.0.1:9/v1",
        history_window=4,
        max_tokens=1024,
        temperature=0.0,
        timeout_seconds=5.0,
        session_max_total=10,
        session_max_per_provider=10,
    )


def _valid_messages() -> list[dict[str, str]]:
    """Build one routable user message for the scripted semantic-validation call."""
    return [{"role": "user", "content": "Validate the following negotiation message."}]


def _fake_openai_client(raw: SimpleNamespace) -> SimpleNamespace:
    """Build one fake ``OpenAI`` client whose completions call returns ``raw``."""
    return SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=lambda **_: raw))
    )


def _openai_response(content: str, *, response_id: str = "resp-1") -> SimpleNamespace:
    """Build one OpenAI-compatible raw response object."""
    return SimpleNamespace(
        id=response_id,
        model="test-model",
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content=content),
                finish_reason="stop",
            )
        ],
        usage=SimpleNamespace(prompt_tokens=120, completion_tokens=30, total_tokens=150),
    )


@pytest.fixture
def spans() -> list[dict[str, object]]:
    """Spans emitted by the producer hook during one test."""
    return []


@pytest.fixture(autouse=True)
def _sink(spans: list[dict[str, object]]) -> Any:
    """Attach one recording sink and clear it afterwards."""
    set_global_sink(spans.append)
    try:
        yield
    finally:
        set_global_sink(None)


def test_register_traced_providers_is_idempotent_and_selects_by_mode() -> None:
    register_traced_providers()
    register_traced_providers()
    register_traced_scripted_provider()
    register_traced_scripted_provider()

    assert is_traced_provider_registered(TRACED_OPENAI_PROVIDER)
    assert is_traced_provider_registered(TRACED_SCRIPTED_PROVIDER)
    assert sdk_provider_name(offline=True) == TRACED_SCRIPTED_PROVIDER
    assert sdk_provider_name(offline=False) == TRACED_OPENAI_PROVIDER


def test_traced_scripted_client_emits_request_response_and_scripted_tokens(
    english_language: None,
    spans: list[dict[str, object]],
) -> None:
    del english_language
    client = TracedScriptedClient(_client_config())

    with trace_scope(run_id="run-1", role="client"), trace_stage("negotiation_propose_validation"):
        response = client.structured(messages=_valid_messages(), json_schema=_VALIDATION_SCHEMA)

    # Behavior is unchanged: the scripted response is returned as-is.
    assert json.loads(response.content)["semantic_verdict"] is True
    assert client.calls == ["negotiation_semantic_validation"]

    assert len(spans) == 1
    span = spans[0]
    assert span["kind"] == "llm"
    assert span["runId"] == "run-1"
    assert span["side"] == "client"
    assert span["stage"] == "negotiation_propose_validation"
    assert span["phase"] == PHASE_NEGOTIATION
    assert span["status"] == "ok"
    assert str(span["spanId"]).startswith("llm_client_scripted_traced_")
    assert isinstance(span["durationMs"], float)
    tokens = span["tokens"]
    assert isinstance(tokens, dict)
    assert tokens["label"] == SCRIPTED_TOKENS_LABEL
    assert tokens["prompt"] is None and tokens["completion"] is None and tokens["total"] is None
    detail = span["detail"]
    assert isinstance(detail, dict)
    assert detail["provider"] == TRACED_SCRIPTED_PROVIDER
    request = detail["request"]
    assert isinstance(request, dict)
    assert request["messages"] == _valid_messages()
    assert request["jsonSchema"] == _VALIDATION_SCHEMA
    assert request["temperature"] is None and request["maxTokens"] is None
    response_detail = detail["response"]
    assert isinstance(response_detail, dict)
    assert json.loads(str(response_detail["rawContent"]))["semantic_verdict"] is True
    assert response_detail["parsed"] == json.loads(response.content)
    assert response_detail["parseError"] is None


def test_stage_falls_back_to_the_scripted_metadata_without_trace_stage(
    english_language: None,
    spans: list[dict[str, object]],
) -> None:
    del english_language
    client = TracedScriptedClient(_client_config())

    with trace_scope(run_id="run-1", role="server"):
        client.structured(messages=_valid_messages(), json_schema=_VALIDATION_SCHEMA)

    assert len(spans) == 1
    assert spans[0]["stage"] == "negotiation_semantic_validation"
    assert spans[0]["side"] == "server"
    assert spans[0]["phase"] == PHASE_NEGOTIATION


def test_traced_scripted_client_captures_an_error_and_reraises(
    english_language: None,
    spans: list[dict[str, object]],
) -> None:
    del english_language
    client = TracedScriptedClient(_client_config())

    with trace_scope(run_id="run-1", role="client"), trace_stage("task_t_slot_extraction"):
        with pytest.raises(ValueError):
            client.structured(
                messages=[{"role": "user", "content": "unroutable"}],
                json_schema={"type": "object", "properties": {"something_else": {}}},
            )

    assert len(spans) == 1
    span = spans[0]
    assert span["status"] == "error"
    assert span["phase"] == PHASE_TASK_PROMPT
    assert "ValueError" in str(span["error"])
    detail = span["detail"]
    assert isinstance(detail, dict)
    assert detail["request"]["messages"][0]["content"] == "unroutable"


def test_traced_openai_client_disables_http_retries() -> None:
    client = TracedOpenAIClient(_client_config("test-model"))

    raw_client = client._get_client()

    assert raw_client.max_retries == 0
    assert raw_client.timeout == 5.0


def test_traced_openai_client_captures_raw_content_and_usage(
    monkeypatch: pytest.MonkeyPatch,
    spans: list[dict[str, object]],
) -> None:
    client = TracedOpenAIClient(_client_config("test-model"))
    raw = _openai_response('{"semantic_verdict": true, "ok": 1}')
    monkeypatch.setattr(client, "_get_client", lambda: _fake_openai_client(raw))

    with trace_scope(run_id="run-2", role="client"), trace_stage("negotiation_accept_validation"):
        response = client.structured(
            messages=_valid_messages(),
            json_schema=_VALIDATION_SCHEMA,
            temperature=0.2,
            max_tokens=99,
        )

    assert response.content == '{"semantic_verdict": true, "ok": 1}'
    assert response.usage == {"prompt_tokens": 120, "completion_tokens": 30, "total_tokens": 150}

    assert len(spans) == 1
    span = spans[0]
    assert span["status"] == "ok"
    assert span["phase"] == PHASE_RESOLUTION
    assert span["model"] == "test-model"
    assert span["tokens"] == {
        "prompt": 120,
        "completion": 30,
        "total": 150,
        "cached": None,
        "estimated": False,
    }
    detail = span["detail"]
    assert isinstance(detail, dict)
    assert detail["request"]["temperature"] == 0.2
    assert detail["request"]["maxTokens"] == 99
    response_detail = detail["response"]
    assert response_detail["rawContent"] == '{"semantic_verdict": true, "ok": 1}'
    assert response_detail["parsed"] == {"semantic_verdict": True, "ok": 1}
    assert response_detail["responseId"] == "resp-1"
    assert response_detail["finishReason"] == "stop"


def test_traced_openai_client_accepts_a_fenced_json_object(
    monkeypatch: pytest.MonkeyPatch,
    spans: list[dict[str, object]],
) -> None:
    """Some gateways wrap the object in a fence despite ``response_format``."""
    fenced = '```json\n{"semantic_verdict": true, "ok": 1}\n```'
    client = TracedOpenAIClient(_client_config("test-model"))
    raw = _openai_response(fenced)
    monkeypatch.setattr(client, "_get_client", lambda: _fake_openai_client(raw))

    with trace_scope(run_id="run-fenced", role="client"), trace_stage("negotiation_accept_validation"):
        response = client.structured(messages=_valid_messages(), json_schema=_VALIDATION_SCHEMA)

    assert response.content == '{"semantic_verdict": true, "ok": 1}'
    detail = spans[0]["detail"]
    assert isinstance(detail, dict)
    response_detail = detail["response"]
    assert response_detail["rawContent"] == fenced
    assert response_detail["parsed"] == {"semantic_verdict": True, "ok": 1}
    assert response_detail["parseError"] is None


def test_traced_openai_client_stashes_the_raw_response_of_invalid_json(
    monkeypatch: pytest.MonkeyPatch,
    spans: list[dict[str, object]],
) -> None:
    client = TracedOpenAIClient(_client_config("test-model"))
    raw = _openai_response("not json")
    monkeypatch.setattr(client, "_get_client", lambda: _fake_openai_client(raw))

    with trace_scope(run_id="run-3", role="client"), trace_stage("client_decision_agent"):
        with pytest.raises(LLMRuntimeError):
            client.structured(messages=_valid_messages(), json_schema=_VALIDATION_SCHEMA)

    assert len(spans) == 1
    span = spans[0]
    assert span["status"] == "error"
    assert span["phase"] == PHASE_NEGOTIATION
    assert "invalid json" in str(span["error"])
    detail = span["detail"]
    assert isinstance(detail, dict)
    assert detail["response"]["rawContent"] == "not json"


def test_phase_mapping_covers_every_documented_stage_family() -> None:
    assert phase_for_stage("task_t_slot_extraction") == PHASE_TASK_PROMPT
    assert phase_for_stage("task_t_server_validation") == "task_validation"
    assert phase_for_stage("negotiation_propose_validation") == PHASE_NEGOTIATION
    assert phase_for_stage("negotiation_accept_extraction") == PHASE_RESOLUTION
    # Accept-time Task-T work happens after the decision: Story puts it in Resolution.
    assert phase_for_stage("task_t_slot_extraction_refill") == PHASE_RESOLUTION
    assert phase_for_stage("task_t_accept_validation") == PHASE_RESOLUTION
    assert phase_for_stage("client_decision_agent") == PHASE_NEGOTIATION
    assert phase_for_stage("server_execution_agent") == "execution"
    assert phase_for_stage(None) == PHASE_NEGOTIATION


def test_emit_span_accepts_a_traced_response_serialization() -> None:
    """The ADK serializer used by the decision span is ``None``-tolerant."""
    assert serialize_llm_response(None) is None


def test_underlying_scripted_client_is_used_by_the_factory(
    english_language: None,
    tmp_path: Any,
) -> None:
    """The traced provider is a real subclass of the scripted provider (drop-in)."""
    del english_language, tmp_path
    assert issubclass(TracedScriptedClient, ScriptedLLMClient)
    assert issubclass(TracedOpenAIClient, OpenAIClient)


def test_producer_hook_is_a_noop_without_a_sink(spans: list[dict[str, object]]) -> None:
    set_global_sink(None)
    emit_span({"spanId": "ignored", "kind": "llm"})
    assert spans == []


def _llm_response(usage: dict[str, object]) -> LLMResponse:
    """Build one provider response with the given raw usage map."""
    return LLMResponse(
        content="ok",
        model="deepseek-v4.1-flash-1017",
        usage=cast("dict[str, int]", usage),
        metadata={},
    )


def test_token_map_carries_the_cached_prefix_tokens() -> None:
    """The provider reports cache hits; dropping the field hides the saving."""
    response = _llm_response(
        {
            "prompt_tokens": 793,
            "completion_tokens": 2,
            "total_tokens": 795,
            "prompt_tokens_details": {"cached_tokens": 768},
        }
    )

    tokens = _token_map(response)

    assert tokens["cached"] == 768
    assert tokens["prompt"] == 793


def test_token_map_marks_an_unreported_cache_as_none() -> None:
    """``None`` (not ``0``) means the provider did not report the field."""
    response = _llm_response({"prompt_tokens": 11, "completion_tokens": 2, "total_tokens": 13})

    assert _token_map(response)["cached"] is None


def test_token_map_reads_the_deepseek_native_cache_field() -> None:
    response = _llm_response(
        {
            "prompt_tokens": 10,
            "completion_tokens": 1,
            "total_tokens": 11,
            "prompt_cache_hit_tokens": 8,
        }
    )

    assert _token_map(response)["cached"] == 8


def test_scripted_tokens_never_invent_a_cache_hit() -> None:
    response = LLMResponse(
        content="ok",
        model=TRACED_SCRIPTED_PROVIDER,
        usage=cast("dict[str, int]", {}),
        metadata={},
    )

    tokens = _token_map(response)

    assert tokens["cached"] is None
    assert tokens["scripted"] is True


def test_token_map_reads_the_sdk_normalized_cache_key() -> None:
    """The SDK provider preserves the cache count as a flat ``cached`` field."""
    response = _llm_response(
        {"prompt_tokens": 793, "completion_tokens": 2, "total_tokens": 795, "cached": 768}
    )

    assert _token_map(response)["cached"] == 768
