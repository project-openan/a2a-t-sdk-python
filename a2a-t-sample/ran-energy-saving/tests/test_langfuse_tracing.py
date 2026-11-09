"""Tests for the Langfuse export of one client run's Run Explorer spans."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from shared.config import MODEL_MODE_LIVE, SampleConfig, load_config
from shared.langfuse_tracing import (
    LANGFUSE_SCOPE_NAME,
    LangfuseRunMetadata,
    LangfuseSpanExporter,
    create_langfuse_exporter,
)


class _FakeClient:
    """Minimal Langfuse client double recording flush/URL calls."""

    def __init__(self) -> None:
        self.flushed = 0
        self.flush_error: Exception | None = None

    def flush(self) -> None:
        self.flushed += 1
        if self.flush_error is not None:
            raise self.flush_error

    def get_trace_url(self, *, trace_id: str | None = None) -> str:
        return f"https://langfuse.example/project/p1/traces/{trace_id}"


class _Runtime:
    """Runtime double with a real OTel tracer and an in-memory exporter."""

    def __init__(self) -> None:
        self.spans = InMemorySpanExporter()
        self.provider = TracerProvider()
        self.provider.add_span_processor(SimpleSpanProcessor(self.spans))
        self.tracer = self.provider.get_tracer(LANGFUSE_SCOPE_NAME)
        self.client = _FakeClient()


def _metadata() -> LangfuseRunMetadata:
    return LangfuseRunMetadata(
        run_id="run-1",
        scenario="night",
        conversation_id="conv-1",
        user_id="user-1",
        language="en-US",
        mode="auto",
        protocol="a2a-t",
        transport="sse",
        discovery="registry",
        intent_mode="processed",
        model_mode="live",
        model_label="live (m)",
    )


def _exporter(
    offline_config: SampleConfig,
    *,
    capture: bool = True,
    span_level: str = "full",
    parent_trace_id: str | None = None,
) -> tuple[LangfuseSpanExporter, _Runtime]:
    runtime = _Runtime()
    config = replace(
        offline_config,
        model_mode=MODEL_MODE_LIVE,
        langfuse_public_key="pk",
        langfuse_secret_key="sk",
        langfuse_capture_content=capture,
        trace_span_level=span_level,
    )
    return (
        LangfuseSpanExporter(
            runtime=runtime,
            config=config,
            metadata=_metadata(),
            parent_trace_id=parent_trace_id,
        ),
        runtime,
    )


def _llm_span(**overrides: object) -> dict[str, object]:
    span: dict[str, object] = {
        "kind": "llm",
        "phase": "negotiation",
        "title": "LLM call · negotiation_propose_extraction",
        "status": "ok",
        "startedAt": "2026-11-05T10:00:00.000Z",
        "endedAt": "2026-11-05T10:00:01.000Z",
        "durationMs": 1000,
        "model": "deepseek-v4.1-flash",
        "tokens": {"prompt": 30, "completion": 20, "total": 50},
        "spanId": "llm_0001",
        "round": 1,
        "detail": {
            "request": {"messages": [{"role": "user", "content": "propose"}]},
            "response": {"rawContent": '{"ok":true}', "parsed": {"ok": True}},
        },
    }
    span.update(overrides)
    return span


def _by_name(runtime: _Runtime) -> dict[str, Any]:
    return {span.name: span for span in runtime.spans.get_finished_spans()}


def test_exporter_is_disabled_without_keys(tmp_path) -> None:
    env = tmp_path / ".env"
    env.write_text("A2AT_SAMPLE_MODEL_MODE=live\n", encoding="utf-8")

    assert create_langfuse_exporter(load_config(env_path=env), _metadata()) is None


def test_exporter_is_disabled_in_offline_mode(tmp_path) -> None:
    env = tmp_path / ".env"
    env.write_text(
        "\n".join(
            [
                "A2AT_SAMPLE_MODEL_MODE=offline",
                "LANGFUSE_PUBLIC_KEY=pk",
                "LANGFUSE_SECRET_KEY=sk",
            ]
        ),
        encoding="utf-8",
    )

    assert create_langfuse_exporter(load_config(env_path=env), _metadata()) is None


def test_exporter_can_be_forced_off(tmp_path) -> None:
    env = tmp_path / ".env"
    env.write_text(
        "\n".join(
            [
                "A2AT_SAMPLE_MODEL_MODE=live",
                "LANGFUSE_PUBLIC_KEY=pk",
                "LANGFUSE_SECRET_KEY=sk",
                "A2AT_SAMPLE_LANGFUSE_ENABLED=0",
            ]
        ),
        encoding="utf-8",
    )

    config = load_config(env_path=env)
    assert config.langfuse_configured is False
    assert create_langfuse_exporter(config, _metadata()) is None


def test_llm_span_becomes_a_generation_with_usage(offline_config: SampleConfig) -> None:
    exporter, runtime = _exporter(offline_config)

    exporter(_llm_span())

    spans = _by_name(runtime)
    generation = next(span for span in spans.values() if span.name == "LLM call · negotiation_propose_extraction")
    attributes = generation.attributes
    assert attributes["langfuse.observation.type"] == "generation"
    assert attributes["langfuse.observation.model.name"] == "deepseek-v4.1-flash"
    assert json.loads(attributes["langfuse.observation.usage_details"]) == {
        "input": 30,
        "output": 20,
        "total": 50,
    }
    assert json.loads(attributes["langfuse.observation.input"]) == [
        {"role": "user", "content": "propose"}
    ]
    assert attributes["langfuse.observation.output"] == '{"ok":true}'
    # The recorded timestamps are replayed exactly.
    started = int(datetime(2026, 11, 5, 10, 0, 0, tzinfo=UTC).timestamp() * 1_000_000_000)
    assert generation.start_time == started
    assert generation.end_time == started + 1_000_000_000
    assert generation.parent is not None


def test_round_spans_group_negotiation_observations(offline_config: SampleConfig) -> None:
    exporter, runtime = _exporter(offline_config)

    exporter(_llm_span(spanId="llm_0001", round=1))
    exporter(_llm_span(spanId="llm_0002", round=2, title="LLM call · accept"))
    exporter.finish(result=None, error=None)

    spans = _by_name(runtime)
    round_one = spans["round 1"]
    round_two = spans["round 2"]
    first = spans["LLM call · negotiation_propose_extraction"]
    second = spans["LLM call · accept"]
    assert first.parent.span_id == round_one.context.span_id
    assert second.parent.span_id == round_two.context.span_id
    assert round_one.parent.span_id == round_two.parent.span_id  # both under the negotiation phase


def test_capture_content_off_only_sends_metadata(offline_config: SampleConfig) -> None:
    exporter, runtime = _exporter(offline_config, capture=False)

    exporter(_llm_span())

    spans = _by_name(runtime)
    generation = spans["LLM call · negotiation_propose_extraction"]
    assert "langfuse.observation.input" not in generation.attributes
    assert "langfuse.observation.output" not in generation.attributes
    assert generation.attributes["langfuse.observation.model.name"] == "deepseek-v4.1-flash"


def test_tool_and_intent_spans_keep_their_payloads(offline_config: SampleConfig) -> None:
    exporter, runtime = _exporter(offline_config)

    exporter(
        {
            "kind": "tool",
            "phase": "execution",
            "title": "Tool call · get_cell",
            "startedAt": "2026-11-05T10:00:00.000Z",
            "endedAt": "2026-11-05T10:00:00.500Z",
            "detail": {"toolName": "get_cell", "args": {"cell": "c1"}, "result": {"rate": 30}},
        }
    )
    exporter(
        {
            "kind": "intent_processed",
            "phase": "task_prompt",
            "title": "Intent processed",
            "startedAt": "2026-11-05T10:00:00.000Z",
            "endedAt": "2026-11-05T10:00:00.200Z",
            "detail": {"rawRequest": "save power", "normalizedText": "Create baseline ..."},
        }
    )

    spans = _by_name(runtime)
    tool = spans["Tool call · get_cell"]
    assert tool.attributes["langfuse.observation.type"] == "tool"
    assert json.loads(tool.attributes["langfuse.observation.input"]) == {"cell": "c1"}
    intent = spans["Intent processed"]
    assert intent.attributes["langfuse.observation.input"] == "save power"
    assert intent.attributes["langfuse.observation.output"] == "Create baseline ..."


def test_finish_flushes_and_exposes_the_trace_url(offline_config: SampleConfig) -> None:
    exporter, runtime = _exporter(offline_config)

    exporter(_llm_span())
    exporter.finish(result=None, error=None)
    snapshot = exporter.snapshot(resolve_url=True)

    assert runtime.client.flushed == 1
    assert snapshot["status"] == "exported"
    assert snapshot["url"] == (
        f"https://us.cloud.langfuse.com/project/p1/traces/{snapshot['traceId']}"
    )
    assert snapshot["exportedSpans"] == 1
    exporter.finish(result=None, error=None)
    assert runtime.client.flushed == 1  # idempotent


class _FlakyTracer:
    """Delegate the root span, then fail on the next observation."""

    def __init__(self, inner: Any) -> None:
        self._inner = inner
        self._calls = 0

    def start_span(self, *args: Any, **kwargs: Any) -> Any:
        self._calls += 1
        if self._calls > 1:
            raise RuntimeError("tracer unavailable")
        return self._inner.start_span(*args, **kwargs)


def test_export_failure_never_raises(offline_config: SampleConfig) -> None:
    exporter, runtime = _exporter(offline_config)
    runtime.tracer = _FlakyTracer(runtime.tracer)

    exporter(_llm_span())

    snapshot = exporter.snapshot()
    assert snapshot["warning"] is not None
    assert snapshot["exportedSpans"] == 0


def test_flush_failure_is_reported(offline_config: SampleConfig) -> None:
    exporter, runtime = _exporter(offline_config)
    runtime.client.flush_error = RuntimeError("network down")

    exporter.finish(result=None, error=None)

    snapshot = exporter.snapshot()
    assert snapshot["status"] == "failed"
    assert "network down" in str(snapshot["warning"])


@pytest.mark.parametrize(
    ("tokens", "expected"),
    [
        ({"prompt": 1, "completion": 2}, {"input": 1, "output": 2}),
        (
            {"prompt": 793, "cached": 768, "completion": 2, "total": 795},
            {"input": 793, "input_cached": 768, "output": 2, "total": 795},
        ),
        ({"prompt": 11, "cached": None, "completion": 2}, {"input": 11, "output": 2}),
        ({"prompt": None, "completion": None, "total": 3}, {"total": 3}),
        # LangGraph planner shape: without normalization Langfuse would keep only ``total``.
        (
            {"input_tokens": 10, "output_tokens": 4, "total_tokens": 14},
            {"input": 10, "output": 4, "total": 14},
        ),
        # DeepSeek native cache counter.
        ({"prompt": 10, "prompt_cache_hit_tokens": 6, "completion": 2}, {"input": 10, "input_cached": 6, "output": 2}),
        # LangChain nested cache counter.
        (
            {"input_tokens": 10, "input_token_details": {"cache_read": 7}, "output_tokens": 1},
            {"input": 10, "input_cached": 7, "output": 1},
        ),
        ({"estimated": True, "label": "n/a (scripted)"}, None),
    ],
)
def test_usage_details_tolerates_scripted_tokens(
    offline_config: SampleConfig, tokens: dict[str, object], expected: dict[str, int] | None
) -> None:
    exporter, runtime = _exporter(offline_config)

    exporter(_llm_span(tokens=tokens))

    generation = _by_name(runtime)["LLM call · negotiation_propose_extraction"]
    attribute = generation.attributes.get("langfuse.observation.usage_details")
    assert (json.loads(attribute) if attribute is not None else None) == expected


def test_missing_langfuse_package_is_a_noop(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    env = tmp_path / ".env"
    env.write_text(
        "\n".join(
            [
                "A2AT_SAMPLE_MODEL_MODE=live",
                "LANGFUSE_PUBLIC_KEY=pk",
                "LANGFUSE_SECRET_KEY=sk",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr("shared.langfuse_tracing.LANGFUSE_AVAILABLE", False)

    assert create_langfuse_exporter(load_config(env_path=env), _metadata()) is None


def test_the_trace_url_uses_the_browser_facing_ui_base(offline_config: SampleConfig) -> None:
    """Reporting may go through a proxy; the reported link must stay on the UI address."""
    exporter, _runtime = _exporter(
        replace(offline_config, langfuse_ui_base_url="https://langfuse-ui.example/relay")
    )

    exporter(_llm_span())
    exporter.finish(result=None, error=None)
    snapshot = exporter.snapshot(resolve_url=True)

    assert snapshot["url"] == (
        f"https://langfuse-ui.example/relay/project/p1/traces/{snapshot['traceId']}"
    )


def test_exporter_joins_the_current_otel_parent(offline_config: SampleConfig) -> None:
    """An incoming traceparent on the UI request makes the run join the caller's trace."""
    from opentelemetry import context as otel_context
    from opentelemetry import trace as otel_trace
    from opentelemetry.sdk.trace import TracerProvider

    parent_provider = TracerProvider()
    parent = parent_provider.get_tracer("external-caller").start_span("external request")
    token = otel_context.attach(otel_trace.set_span_in_context(parent))
    try:
        exporter, runtime = _exporter(offline_config)
        exporter.finish()
    finally:
        otel_context.detach(token)
        parent.end()

    root = _by_name(runtime)["run · night · a2a-t"]
    assert f"{root.context.trace_id:032x}" == f"{parent.get_span_context().trace_id:032x}"


def _reduced_run_spans() -> list[dict[str, object]]:
    """A small run with the status noise the reduced levels are meant to remove."""
    return [
        {"kind": "run_started", "phase": "dispatch", "title": "Run started", "detail": {}},
        {"kind": "runtime_ready", "phase": "dispatch", "title": "Client runtime ready", "detail": {}},
        {"kind": "message_sent", "phase": "task_prompt", "title": "Pure A2A message sent", "detail": {}},
        {
            "kind": "status",
            "phase": "execution",
            "title": "WORKING",
            "detail": {"event": {"kind": "status", "state": "TASK_STATE_WORKING"}},
        },
        {"kind": "tool", "phase": "execution", "title": "NMS token", "detail": {}},
        {
            "kind": "status",
            "phase": "execution",
            "title": "COMPLETED",
            "detail": {"event": {"kind": "status", "state": "TASK_STATE_COMPLETED"}},
        },
        {"kind": "finished", "phase": "done", "title": "Run finished", "detail": {}},
    ]


def _exported_names(offline_config: SampleConfig, level: str) -> list[str]:
    exporter, runtime = _exporter(offline_config, span_level=level)
    for span in _reduced_run_spans():
        exporter(span)
    exporter.finish()
    return [span.name for span in runtime.spans.get_finished_spans()]


def test_full_span_level_keeps_every_observation(offline_config: SampleConfig) -> None:
    names = _exported_names(offline_config, "full")

    assert "Client runtime ready" in names
    assert "WORKING" in names
    assert "phase · execution" in names


def test_phase_span_level_keeps_only_the_osp_carriers(offline_config: SampleConfig) -> None:
    names = _exported_names(offline_config, "phase")

    assert "Pure A2A message sent" in names
    assert "NMS token" in names
    assert "Run finished" in names
    assert "Run started" not in names
    assert "COMPLETED" not in names
    assert "Client runtime ready" not in names
    assert "WORKING" not in names
    assert not any(name.startswith("phase ·") for name in names)


def test_phase_level_keeps_every_round_and_hides_transport(offline_config: SampleConfig) -> None:
    exporter, runtime = _exporter(offline_config, span_level="phase")
    for round_number in (1, 2):
        exporter(
            {
                "kind": "propose",
                "phase": "negotiation",
                "title": "## Information Negotiation Please supplement ...",
                "round": round_number,
                "detail": {"negotiationType": "information"},
            }
        )
        exporter({"kind": "counter", "phase": "negotiation", "title": "counter", "round": round_number})
        exporter(
            {
                "kind": "message_sent",
                "phase": "negotiation",
                "title": f"Counter proposal sent · R{round_number}",
                "round": round_number,
            }
        )
    exporter({"kind": "transport", "phase": "execution", "title": "NMS task polled"})
    exporter({"kind": "approval", "phase": "execution", "title": "Approval requested"})
    exporter.finish()

    names = [span.name for span in runtime.spans.get_finished_spans()]
    assert "Propose · information · R1" in names
    assert "Propose · information · R2" in names
    assert "Decision · counter · R1" in names
    assert "Decision · counter · R2" in names
    assert "Approval requested" in names
    assert "NMS task polled" not in names


def test_phase_level_exports_the_decision_request_as_the_oss_notification(
    offline_config: SampleConfig,
) -> None:
    """AK: the moment the OSS is asked to handle a proposal must reach the dashboard."""
    exporter, runtime = _exporter(offline_config, span_level="phase")
    exporter(
        {
            "kind": "decision_requested",
            "phase": "negotiation",
            "title": "Decision requested · target R1",
            "round": 1,
            "detail": {"negotiationKind": "target", "content": "Set the target to 10 Mbps."},
        }
    )
    exporter.finish()

    names = [span.name for span in runtime.spans.get_finished_spans()]
    assert "Decision requested · target R1" in names


def test_phase_level_backfills_the_last_status_without_a_terminal_state(
    offline_config: SampleConfig,
) -> None:
    exporter, runtime = _exporter(offline_config, span_level="phase")
    exporter(
        {
            "kind": "status",
            "phase": "execution",
            "title": "WORKING",
            "detail": {"event": {"state": "TASK_STATE_WORKING"}},
        }
    )
    exporter.finish()

    assert "WORKING" in [span.name for span in runtime.spans.get_finished_spans()]


def test_minimal_span_level_keeps_only_llm_tool_and_result(offline_config: SampleConfig) -> None:
    names = _exported_names(offline_config, "minimal")

    assert "NMS token" in names
    assert "Run finished" in names
    assert "Run started" not in names
    assert "COMPLETED" not in names


def test_trace_metadata_carries_the_osp_case_and_the_round_count(
    offline_config: SampleConfig,
) -> None:
    runtime = _Runtime()
    config = replace(
        offline_config,
        model_mode=MODEL_MODE_LIVE,
        langfuse_public_key="pk",
        langfuse_secret_key="sk",
        trace_span_level="phase",
    )
    exporter = LangfuseSpanExporter(
        runtime=runtime,
        config=config,
        metadata=replace(
            _metadata(),
            source_kind="tester",
            case_id="32",
            case_name="10Mbps",
            batch_id="batch-1",
            received_at="2026-11-07T00:00:00.000Z",
            user_request="reduce the throughput target",
        ),
    )
    exporter({"kind": "propose", "phase": "negotiation", "title": "x", "round": 2, "detail": {}})
    exporter.finish()

    root = next(span for span in runtime.spans.get_finished_spans() if span.name.startswith("run ·"))
    assert root.attributes["langfuse.trace.metadata.caseId"] == "32"
    assert root.attributes["langfuse.trace.metadata.sourceKind"] == "tester"
    assert root.attributes["langfuse.trace.metadata.receivedAt"] == "2026-11-07T00:00:00.000Z"
    assert root.attributes["langfuse.trace.metadata.rounds"] == "2"
    assert root.attributes["langfuse.trace.metadata.negotiated"] == "true"


def test_a_correction_chain_is_one_langfuse_session(offline_config: SampleConfig) -> None:
    """A retry opens its own trace but stays under the parent attempt's Langfuse session."""
    config = replace(
        offline_config,
        model_mode=MODEL_MODE_LIVE,
        langfuse_public_key="pk",
        langfuse_secret_key="sk",
    )
    first_runtime = _Runtime()
    retry_runtime = _Runtime()
    first = LangfuseSpanExporter(
        runtime=first_runtime,
        config=config,
        metadata=replace(_metadata(), chain_id="chain-1"),
    )
    retry = LangfuseSpanExporter(
        runtime=retry_runtime,
        config=config,
        metadata=replace(
            _metadata(),
            run_id="run-2",
            conversation_id="conv-2",
            chain_id="chain-1",
            parent_run_id="run-1",
        ),
    )
    first.finish()
    retry.finish()

    first_root = next(
        span for span in first_runtime.spans.get_finished_spans() if span.name.startswith("run ·")
    )
    retry_root = next(
        span for span in retry_runtime.spans.get_finished_spans() if span.name.startswith("run ·")
    )
    assert first_root.attributes["session.id"] == "chain-1"
    assert retry_root.attributes["session.id"] == "chain-1"
    assert retry_root.attributes["langfuse.trace.metadata.chainId"] == "chain-1"
    assert retry_root.attributes["langfuse.trace.metadata.parentRunId"] == "run-1"
    # Each attempt keeps a trace of its own: one root per trace, grouped by the session.
    assert first_root.context.trace_id != retry_root.context.trace_id


def test_exporter_uses_the_explicit_run_trace_id(offline_config: SampleConfig) -> None:
    trace_id = "c" * 32
    exporter, runtime = _exporter(offline_config, parent_trace_id=trace_id)

    exporter.finish()

    root = _by_name(runtime)["run · night · a2a-t"]
    assert f"{root.context.trace_id:032x}" == trace_id


def test_the_run_trace_id_wins_over_a_stale_current_otel_parent(offline_config: SampleConfig) -> None:
    """Another request's span being current must not steal the run's Langfuse trace."""
    from opentelemetry.trace import NonRecordingSpan, SpanContext, TraceFlags, use_span

    stale = SpanContext(
        trace_id=int("f" * 32, 16),
        span_id=int("1" * 16, 16),
        is_remote=False,
        trace_flags=TraceFlags(TraceFlags.SAMPLED),
    )
    with use_span(NonRecordingSpan(stale)):
        exporter, runtime = _exporter(offline_config, parent_trace_id="c" * 32)
        exporter.finish()

    root = _by_name(runtime)["run · night · a2a-t"]
    assert f"{root.context.trace_id:032x}" == "c" * 32


def test_retries_with_the_same_trace_id_share_one_langfuse_trace(
    offline_config: SampleConfig,
) -> None:
    trace_id = "d" * 32
    first, first_runtime = _exporter(offline_config, parent_trace_id=trace_id)
    second, second_runtime = _exporter(offline_config, parent_trace_id=trace_id)
    first.finish()
    second.finish()

    first_root = _by_name(first_runtime)["run · night · a2a-t"]
    second_root = _by_name(second_runtime)["run · night · a2a-t"]
    assert first_root.context.trace_id == second_root.context.trace_id
