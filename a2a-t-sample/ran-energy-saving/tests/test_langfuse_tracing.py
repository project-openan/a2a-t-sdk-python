"""Tests for the Langfuse export of one client run's Run Explorer spans."""

from __future__ import annotations

import io
import json
import urllib.error
from collections import OrderedDict, deque
from dataclasses import replace
from datetime import UTC, datetime
from email.message import Message
from types import SimpleNamespace
from typing import Any

import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor, SpanExportResult
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from shared.config import MODEL_MODE_LIVE, SampleConfig, load_config
from shared.langfuse_tracing import (
    _INGESTION_PROBES,
    LANGFUSE_SCOPE_NAME,
    LangfuseRunMetadata,
    LangfuseSpanExporter,
    _audit_for,
    _audited_exporter,
    _get_runtime,
    _instrument_otlp_client,
    _LangfuseRuntime,
    _max_accepted_bytes,
    _note_accepted_request,
    _note_traffic,
    _probe_ingestion,
    _record_batch,
    _remember_rejection_body,
    _request_body_size,
    _request_encoding,
    _reset_batch_diagnostics,
    _retrying_otlp_exporter,
    _take_batch_diagnostics,
    _traffic_last_minute,
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
    workflow_root_span_id: str | None = None,
    create_workflow_root: bool = False,
    metadata: LangfuseRunMetadata | None = None,
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
            metadata=metadata or _metadata(),
            parent_trace_id=parent_trace_id,
            workflow_root_span_id=workflow_root_span_id,
            create_workflow_root=create_workflow_root,
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


def _created_span(**overrides: object) -> dict[str, object]:
    """One ``task_created`` row: the acceptance signal of a Create/Modify run."""
    span: dict[str, object] = {
        "kind": "task_created",
        "phase": "execution",
        "title": "Task created",
        "status": "ok",
        "startedAt": "2026-11-05T10:00:02.000Z",
        "endedAt": "2026-11-05T10:00:02.000Z",
        "durationMs": 0,
        "spanId": "created_0001",
        "detail": {"state": "TASK_STATE_SUBMITTED"},
    }
    span.update(overrides)
    return span


def _by_name(runtime: _Runtime) -> dict[str, Any]:
    return {span.name: span for span in runtime.spans.get_finished_spans()}


def test_exporter_is_disabled_without_keys(tmp_path) -> None:
    env = tmp_path / ".env"
    env.write_text("A2AT_SAMPLE_MODEL_MODE=live\n", encoding="utf-8")

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
    assert json.loads(attributes["langfuse.observation.input"]) == [{"role": "user", "content": "propose"}]
    assert attributes["langfuse.observation.output"] == '{"ok":true}'
    # The recorded timestamps are replayed exactly.
    started = int(datetime(2026, 11, 5, 10, 0, 0, tzinfo=UTC).timestamp() * 1_000_000_000)
    assert generation.start_time == started
    assert generation.end_time == started + 1_000_000_000
    assert generation.parent is not None


def test_a_generation_falls_back_to_detail_model_and_usage(
    offline_config: SampleConfig,
) -> None:
    """Some producer shapes keep model/usage in ``detail.response`` only."""
    exporter, runtime = _exporter(offline_config)
    span = _llm_span()
    span.pop("model")
    span.pop("tokens")
    span["detail"] = {
        "response": {
            "model": "detail-model",
            "usage": {"prompt_tokens": 3, "completion_tokens": 4, "total_tokens": 7},
        }
    }

    exporter(span)

    generation = _by_name(runtime)["LLM call · negotiation_propose_extraction"]
    assert generation.attributes["langfuse.observation.model.name"] == "detail-model"
    assert json.loads(generation.attributes["langfuse.observation.usage_details"]) == {
        "input": 3,
        "output": 4,
        "total": 7,
    }


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
    assert snapshot["url"] == (f"https://us.cloud.langfuse.com/project/p1/traces/{snapshot['traceId']}")
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
    exporter, _runtime = _exporter(replace(offline_config, langfuse_ui_base_url="https://langfuse-ui.example/relay"))

    exporter(_llm_span())
    exporter.finish(result=None, error=None)
    snapshot = exporter.snapshot(resolve_url=True)

    assert snapshot["url"] == (f"https://langfuse-ui.example/relay/project/p1/traces/{snapshot['traceId']}")


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


def test_a_correction_keeps_the_original_request_and_names_the_dispatched_one(
    offline_config: SampleConfig,
) -> None:
    """The trace names the operator's request; the corrected text is a separate field."""
    metadata = replace(
        _metadata(),
        user_request="original request",
        request_role="blocked_followup",
    )
    exporter, runtime = _exporter(offline_config, span_level="phase", metadata=metadata)
    exporter(
        {
            "kind": "correction_applied",
            "phase": "task_prompt",
            "title": "Request corrected",
            "detail": {
                "attempt": 1,
                "originalRequest": "original request",
                "correctedRequest": "corrected request",
                "verdict": "blocked",
                "errors": [],
            },
        }
    )
    exporter.finish()

    spans = _by_name(runtime)
    root = spans["run · night · a2a-t"]
    attributes = dict(root.attributes)
    assert attributes["langfuse.trace.metadata.userRequest"] == "original request"
    assert attributes["langfuse.trace.metadata.requestRole"] == "blocked_followup"
    assert attributes["langfuse.trace.metadata.dispatchedRequest"] == "corrected request"

    correction = spans["Request corrected"]
    assert correction.attributes["langfuse.observation.input"] == "original request"
    assert correction.attributes["langfuse.observation.output"] == "corrected request"


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


def test_observations_hang_under_an_anchor_exported_before_the_root(
    offline_config: SampleConfig,
) -> None:
    """An unfinished run must still show a tree: the open root is exported only at finish."""
    exporter, runtime = _exporter(offline_config, span_level="phase")

    exporter(_llm_span())

    exported = runtime.spans.get_finished_spans()
    anchor = next(span for span in exported if span.name == "run opened")
    creation = exporter._creation_span
    observation = next(span for span in exported if span.name.startswith("LLM call"))
    # The anchor exists while the run is open, so it is the parent the backend can resolve.
    assert f"{anchor.context.span_id:016x}" == exporter.root_span_id
    assert creation is not None
    assert creation.parent is not None and creation.parent.span_id == anchor.context.span_id
    assert observation.parent is not None
    assert observation.parent.span_id == creation.get_span_context().span_id
    assert all(not span.name.startswith("run ·") for span in exported)


def test_the_creation_stage_closes_when_the_task_is_accepted(
    offline_config: SampleConfig,
) -> None:
    """The creation stage gets one closed span at SUBMITTED; later observations sit under it."""
    exporter, runtime = _exporter(offline_config, span_level="phase")

    exporter(_llm_span())
    exporter(_created_span())
    exporter(_llm_span(spanId="llm_0002", title="LLM call · after acceptance"))

    exported = runtime.spans.get_finished_spans()
    creation = next(span for span in exported if span.name.startswith("creation ·"))
    # Creation ends with the accepting row, not at run end.
    created = next(span for span in exported if span.name == "Task created")
    assert creation.end_time == created.end_time
    assert created.parent is not None
    assert created.parent.span_id == creation.context.span_id
    later = next(span for span in exported if span.name == "LLM call · after acceptance")
    assert later.parent is not None
    assert later.parent.span_id == creation.parent.span_id  # back under the anchor
    # The span names the statistics tools rely on are untouched.
    assert all(not span.name.startswith("run ·") for span in exported)


def test_a_minimal_export_still_closes_the_creation_stage(offline_config: SampleConfig) -> None:
    """A level that does not export the accepting row must still end the creation stage."""
    exporter, _runtime = _exporter(offline_config, span_level="minimal")

    exporter(
        {
            "kind": "status",
            "phase": "execution",
            "title": "Working",
            "status": "ok",
            "startedAt": "2026-11-05T10:00:03.000Z",
            "endedAt": "2026-11-05T10:00:03.000Z",
            "durationMs": 0,
            "spanId": "status_0001",
            "detail": {"event": {"state": "TASK_STATE_WORKING"}},
        }
    )

    assert exporter._creation_span is None


def test_a_vendor_request_span_is_exported_under_the_run_root(offline_config: SampleConfig) -> None:
    """The traceparent span-id must name a span that exists in the run's Langfuse trace."""
    exporter, runtime = _exporter(offline_config)

    span = exporter.start_vendor_span("NMS message:send", {"url": "https://nms/a2a/json/message:send"})
    context = span.get_span_context()
    span.end()

    exported = runtime.spans.get_finished_spans()
    vendor = next(item for item in exported if item.context.span_id == context.span_id)
    creation = exporter._creation_span
    assert f"{context.trace_id:032x}" == exporter.trace_id
    assert creation is not None
    assert vendor.parent is not None
    assert vendor.parent.span_id == creation.get_span_context().span_id
    assert vendor.attributes["url"] == "https://nms/a2a/json/message:send"


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

    first_root = next(span for span in first_runtime.spans.get_finished_spans() if span.name.startswith("run ·"))
    retry_root = next(span for span in retry_runtime.spans.get_finished_spans() if span.name.startswith("run ·"))
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


def test_the_workflow_parent_context_claims_the_trace() -> None:
    """The anchor context carries the SDK's root-suppression baggage and parent span id."""
    from opentelemetry import baggage, trace
    from shared.langfuse_tracing import (
        LANGFUSE_TRACE_ID_BAGGAGE_KEY,
        _workflow_parent_context,
    )

    trace_id = "a" * 32
    span_id = "b" * 16
    context = _workflow_parent_context(trace_id, span_id)

    assert baggage.get_baggage(LANGFUSE_TRACE_ID_BAGGAGE_KEY, context=context) == trace_id
    parent = trace.get_current_span(context=context).get_span_context()
    assert f"{parent.trace_id:032x}" == trace_id
    assert f"{parent.span_id:016x}" == span_id


def test_a_workflow_root_is_the_only_root_and_the_run_root_is_its_child(
    offline_config: SampleConfig,
) -> None:
    """The chain owns one neutral anchor; the attempt is a child of it."""
    trace_id = "e" * 32
    exporter, runtime = _exporter(
        offline_config,
        parent_trace_id=trace_id,
        create_workflow_root=True,
    )

    exporter.finish()

    spans = {span.name: span for span in runtime.spans.get_finished_spans()}
    anchor = spans["workflow · night · a2a-t"]
    run_root = spans["run · night · a2a-t"]
    opened = spans["trace opened"]
    assert f"{anchor.context.trace_id:032x}" == trace_id
    assert anchor.attributes["langfuse.internal.as_root"] is True
    assert anchor.attributes["session.id"] == "conv-1"
    assert "langfuse.trace.metadata.runId" not in anchor.attributes
    assert run_root.parent is not None
    assert f"{run_root.parent.span_id:016x}" == exporter.workflow_root_span_id
    assert run_root.attributes.get("langfuse.internal.as_root") is not True
    assert "langfuse.trace.name" not in run_root.attributes
    assert run_root.attributes["langfuse.observation.metadata.runId"] == "run-1"
    assert opened.attributes["langfuse.observation.metadata.runId"] == "run-1"
    assert exporter.snapshot()["workflowRootSpanId"] == exporter.workflow_root_span_id


def test_a_retry_attaches_to_the_workflow_anchor_from_another_process(
    offline_config: SampleConfig,
) -> None:
    """A retry adopts the persisted anchor without creating a second root."""
    trace_id = "f" * 32
    first, first_runtime = _exporter(
        offline_config,
        parent_trace_id=trace_id,
        create_workflow_root=True,
    )
    first.finish()
    anchor_id = first.workflow_root_span_id

    retry_metadata = replace(
        _metadata(),
        run_id="run-2",
        parent_run_id="run-1",
        chain_id="conv-1",
    )
    second, second_runtime = _exporter(
        offline_config,
        parent_trace_id=trace_id,
        workflow_root_span_id=anchor_id,
        metadata=retry_metadata,
    )
    second.finish()

    assert second.workflow_root_span_id == anchor_id
    retry_root = _by_name(second_runtime)["run · night · a2a-t"]
    assert f"{retry_root.context.trace_id:032x}" == trace_id
    assert retry_root.parent is not None
    assert f"{retry_root.parent.span_id:016x}" == anchor_id
    assert retry_root.attributes.get("langfuse.internal.as_root") is not True
    assert retry_root.attributes["langfuse.observation.metadata.runId"] == "run-2"
    assert "langfuse.trace.name" not in retry_root.attributes
    # No second workflow anchor was created by the retry.
    assert "workflow · night · a2a-t" not in _by_name(second_runtime)


def test_the_ingestion_probe_reports_a_suspended_project(offline_config, monkeypatch) -> None:
    """Langfuse's 403 is a project-level rejection; the probe must surface its message."""
    config = replace(
        offline_config,
        langfuse_public_key="pk",
        langfuse_secret_key="sk",
        langfuse_base_url="https://langfuse.example",
    )

    def deny(request: Any, timeout: float = 0) -> Any:
        del timeout
        raise urllib.error.HTTPError(
            request.full_url,
            403,
            "Forbidden",
            Message(),
            io.BytesIO(b'{"message":"Ingestion suspended: Usage threshold exceeded.","error":"ForbiddenError"}'),
        )

    monkeypatch.setattr("shared.langfuse_tracing.urllib.request.urlopen", deny)

    warning = _probe_ingestion(config)

    assert "HTTP 403" in warning
    assert "Ingestion suspended" in warning


def test_finish_probes_ingestion_with_a_real_runtime(offline_config, monkeypatch) -> None:
    """A healthy exporter looks finished even when the project rejects writes; the probe fixes that."""
    runtime_double = _Runtime()
    config = replace(
        offline_config,
        model_mode=MODEL_MODE_LIVE,
        langfuse_public_key="pk",
        langfuse_secret_key="sk",
        langfuse_base_url="https://langfuse.example",
    )
    real_runtime = _LangfuseRuntime(
        client=runtime_double.client,
        provider=runtime_double.provider,
        tracer=runtime_double.tracer,
    )
    _INGESTION_PROBES.clear()
    monkeypatch.setattr(
        "shared.langfuse_tracing._probe_ingestion",
        lambda config: "Langfuse ingestion rejected (HTTP 403): suspended",
    )
    try:
        exporter = LangfuseSpanExporter(
            runtime=real_runtime,
            config=config,
            metadata=_metadata(),
            parent_trace_id="e" * 32,
        )

        exporter.finish()

        assert "HTTP 403" in str(exporter.snapshot()["warning"])
        assert runtime_double.client.flushed == 1
    finally:
        _INGESTION_PROBES.clear()


def test_the_otlp_exporter_retries_rejected_batches(    live_sdk_provider: None,
) -> None:
    """429/5xx and a temporary 403 must be retried instead of dropping the whole batch.

    The 403 is the intermittent Cloud rejection observed in the field (403 interleaved with 200),
    not the persistent "ingestion suspended" answer: a suspended project just exhausts the bounded
    budget. 401 (bad key) stays out of the list.
    """
    config = replace(
        load_config(),
        langfuse_public_key="pk",
        langfuse_secret_key="sk",
        langfuse_base_url="https://us.cloud.langfuse.com",
    )

    exporter = _retrying_otlp_exporter(config)

    assert exporter is not None
    inner = exporter._inner  # type: ignore[attr-defined]
    transport = inner._client._transport  # type: ignore[attr-defined]
    session = transport._session  # type: ignore[attr-defined]
    retry = session.get_adapter("https://us.cloud.langfuse.com/api/public/otel/v1/traces").max_retries
    assert retry.total == 4
    assert 403 in retry.status_forcelist
    assert 429 in retry.status_forcelist
    assert 401 not in retry.status_forcelist
    assert retry.allowed_methods == frozenset({"POST"})
    assert retry.respect_retry_after_header is True


class _ExportedSpan:
    """Minimal readable span for the export-audit unit tests."""

    def __init__(self, name: str, trace_id: int, observation_type: str = "span") -> None:
        self.name = name
        self.context = SimpleNamespace(trace_id=trace_id)
        self.attributes = {"langfuse.observation.type": observation_type}


@pytest.fixture
def clean_export_audit(monkeypatch: pytest.MonkeyPatch) -> None:
    """Start every audit test from an empty per-trace registry."""
    monkeypatch.setattr("shared.langfuse_tracing._EXPORT_AUDITS", OrderedDict())


def test_a_rejected_batch_is_visible_on_the_run_snapshot(
    offline_config: SampleConfig, clean_export_audit: None
) -> None:
    """A 403 batch is dropped by OTel, so the exporter must count it, not say exported."""
    exporter, _runtime = _exporter(offline_config)
    trace_id = int(exporter.trace_id, 16)

    _record_batch(
        [
            _ExportedSpan("LLM · task_t_slot_extraction", trace_id, "generation"),
            _ExportedSpan("Propose · target · R1", trace_id),
        ],
        failed=True,
        status=403,
        reason='{"message":"Ingestion suspended: Usage threshold exceeded."}',
    )

    snapshot = exporter.snapshot()

    assert snapshot["status"] == "failed"
    assert snapshot["rejectedBatches"] == 1
    assert snapshot["lostSpans"] == 2
    assert snapshot["lostGenerations"] == 1
    assert snapshot["deliveredSpans"] == 0
    assert snapshot["lastRejectStatus"] == 403
    assert "Ingestion suspended" in str(snapshot["warning"])


def test_a_partially_rejected_trace_is_partial_not_exported(
    offline_config: SampleConfig, clean_export_audit: None
) -> None:
    exporter, _runtime = _exporter(offline_config)
    trace_id = int(exporter.trace_id, 16)

    _record_batch([_ExportedSpan("run · night · a2a-t", trace_id)], failed=False)
    _record_batch(
        [_ExportedSpan("LLM · task_t_client_validation", trace_id, "generation")],
        failed=True,
        status=429,
    )

    snapshot = exporter.snapshot()

    assert snapshot["status"] == "partial"
    assert snapshot["deliveredSpans"] == 1
    assert snapshot["lostSpans"] == 1
    assert snapshot["lostGenerations"] == 1
    assert snapshot["lastRejectStatus"] == 429


def test_the_export_audit_is_attributed_per_trace(
    offline_config: SampleConfig, clean_export_audit: None
) -> None:
    first, _first_runtime = _exporter(offline_config)
    second, _second_runtime = _exporter(offline_config)
    assert first.trace_id != second.trace_id

    _record_batch(
        [_ExportedSpan("LLM · slot extraction", int(first.trace_id, 16), "generation")],
        failed=True,
        status=403,
    )
    _record_batch([_ExportedSpan("run opened", int(second.trace_id, 16))], failed=False)

    assert first.snapshot()["lostSpans"] == 1
    assert second.snapshot()["lostSpans"] == 0
    assert second.snapshot()["status"] == "exporting"
    assert _audit_for(second.trace_id) is not None


class _InnerExporter:
    """Inner exporter double returning a fixed result."""

    def __init__(self, result: SpanExportResult) -> None:
        self.result = result
        self.batches = 0
        self.shutdown_calls = 0

    def export(self, spans: list[Any]) -> SpanExportResult:
        self.batches += 1
        return self.result

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        return True

    def shutdown(self) -> None:
        self.shutdown_calls += 1


def test_the_audited_exporter_forwards_the_result_and_records_the_loss(
    clean_export_audit: None,
) -> None:
    inner = _InnerExporter(SpanExportResult.FAILURE)
    wrapper = _audited_exporter(inner)
    trace_id = int("ab" * 16, 16)

    result = wrapper.export([_ExportedSpan("LLM · task_t_slot_extraction", trace_id, "generation")])

    assert result is SpanExportResult.FAILURE
    assert inner.batches == 1
    audit = _audit_for(f"{trace_id:032x}")
    assert audit is not None
    assert audit.lost_spans == 1
    assert audit.lost_generations == 1
    wrapper.shutdown()
    assert inner.shutdown_calls == 1


def test_the_otlp_client_status_is_captured() -> None:
    """The exporter only returns SUCCESS/FAILURE; the client result names the HTTP status."""
    class _Client:
        def export(self, data: bytes) -> Any:
            return SimpleNamespace(success=False, status_code=403, reason="Forbidden")

    exporter = SimpleNamespace(_client=_Client())
    _reset_batch_diagnostics()
    _instrument_otlp_client(exporter)

    result = exporter._client.export(b"{}")

    assert result.success is False
    assert _take_batch_diagnostics() == (403, "Forbidden")


def test_a_rejected_request_reports_its_wire_size_and_encoding() -> None:
    """A proxy that rejects by body size must leave the size in the log."""
    request = SimpleNamespace(body=b"x" * 4096, headers={"Content-Encoding": "gzip"})

    assert _request_body_size(request) == 4096
    assert _request_encoding(request) == "gzip"
    assert _request_body_size(SimpleNamespace(body=None, headers={})) == "unknown"
    assert _request_encoding(SimpleNamespace(body=b"", headers={})) == "none"


def test_the_rolling_traffic_window_sums_recent_requests(monkeypatch) -> None:
    """A per-time proxy quota is proved by comparing 1-way and 3-way traffic."""
    monkeypatch.setattr("shared.langfuse_tracing._OTLP_TRAFFIC", deque())

    _note_traffic(SimpleNamespace(body=b"x" * 1000, headers={}))
    _note_traffic(SimpleNamespace(body=b"x" * 500, headers={}))

    assert _traffic_last_minute() == (2, 1500)


def test_the_largest_accepted_request_tracks_only_successes(monkeypatch) -> None:
    """An accepted request above a rejection proves the size rule is not the cause."""
    monkeypatch.setattr("shared.langfuse_tracing._MAX_ACCEPTED_BYTES", 0)

    _note_accepted_request(SimpleNamespace(body=b"x" * 100, headers={}))
    _note_accepted_request(SimpleNamespace(body=b"x" * 50, headers={}))

    assert _max_accepted_bytes() == 100


def test_the_runtime_uses_the_configured_batch_size(
    monkeypatch: pytest.MonkeyPatch, live_sdk_provider: None
) -> None:
    """The batch size bounds the OTLP request body behind a size-limited egress proxy."""
    config = replace(load_config(), langfuse_flush_at=4)
    captured: dict[str, Any] = {}

    class _FakeLangfuse:
        def __init__(self, **kwargs: Any) -> None:
            captured.update(kwargs)

    monkeypatch.setattr("shared.langfuse_tracing.Langfuse", _FakeLangfuse)
    monkeypatch.setattr("shared.langfuse_tracing._retrying_otlp_exporter", lambda _config: None)
    monkeypatch.setattr("shared.langfuse_tracing._RUNTIME", None)

    _get_runtime(config)

    assert captured["flush_at"] == 4
    assert captured["flush_interval"] == 1.0


def test_a_rejection_body_is_paired_with_its_status() -> None:
    """The proxy/gateway body is what distinguishes quota from throttling."""
    _reset_batch_diagnostics()
    _remember_rejection_body(403, '{"message":"Ingestion suspended: Usage threshold exceeded."}')

    status, reason = _take_batch_diagnostics()

    assert status == 403
    assert "Ingestion suspended" in reason
