import json

import pytest

from fix_support import FakeBackend, make_row, set_env, wire_cli_backend, workdir
from langfuse_export import cli
from langfuse_export.metrics import build_metrics

TRACE = "0123456789abcdef0123456789abcdef"


def _row(name, start, kind=None, phase=None, side="client", type_="SPAN", **meta):
    metadata = {}
    if kind is not None:
        metadata["kind"] = kind
    if phase is not None:
        metadata["phase"] = phase
    if side is not None:
        metadata["side"] = side
    metadata.setdefault("runId", TRACE)
    metadata.update(meta)
    row = make_row(id=name.replace(" ", "_"), name=name, type=type_, metadata=metadata)
    row["startTime"] = start
    row["endTime"] = start
    row["runId"] = TRACE
    return row


def _opened(received="2026-11-08T07:00:00.000Z", **extra):
    row = make_row(
        id="trace_opened",
        name="trace opened",
        type="EVENT",
        metadata={
            "runId": TRACE,
            "receivedAt": received,
            "protocol": "a2a-t",
            "scenario": "outage",
            "runMode": "create",
            "mode": "confirm",
            "modelMode": "live",
            "sourceKind": "presentation",
            "chainId": "conv-test",
            **extra,
        },
    )
    row["startTime"] = received
    row["traceName"] = "outage · a2a-t"
    row["userId"] = "test-user"
    row["sessionId"] = "conv-test"
    return row


def _generation(name, start, input_tokens, cached, output, side="client", stage=None, phase="execution"):
    usage = {"input": input_tokens, "input_cached": cached, "output": output, "total": input_tokens + output}
    row = make_row(
        id=name.replace(" ", "_"),
        name=name,
        type="GENERATION",
        usageDetails=usage,
        metadata={
            "kind": "llm",
            "phase": phase,
            "side": side,
            "runId": TRACE,
            **({"stage": stage} if stage else {}),
        },
    )
    row["startTime"] = start
    row["endTime"] = start
    return row


def _a2a_rows():
    rows = [
        _opened(),
        _generation(
            "LLM call · task_t_slot_extraction", "2026-11-08T07:00:01.000Z", 100, 20, 50, stage="task_t_slot_extraction"
        ),
        _row("Task-T prompt rendered", "2026-11-08T07:00:01.000Z", kind="task_prompt_rendered", phase="task_prompt"),
        _row("Task-T sent", "2026-11-08T07:00:02.000Z", kind="message_sent", phase="task_prompt"),
        _row("Propose · create · R1", "2026-11-08T07:00:03.000Z", kind="propose", phase="negotiation", round="1"),
        _row(
            "Negotiation started · R1",
            "2026-11-08T07:00:04.000Z",
            kind="negotiation_started",
            phase="negotiation",
            round="1",
        ),
        _row("Decision · accept · R1", "2026-11-08T07:00:06.000Z", kind="accept", phase="negotiation", round="1"),
        _generation(
            "LLM call · negotiation_accept_extraction",
            "2026-11-08T07:00:06.100Z",
            200,
            100,
            80,
            stage="negotiation_accept_extraction",
            phase="negotiation",
        ),
        _row("Accept sent · R1", "2026-11-08T07:00:07.000Z", kind="message_sent", phase="negotiation", round="1"),
        _row("Decision · reject · R2", "2026-11-08T07:00:08.000Z", kind="reject", phase="negotiation", round="2"),
        _row("Task created", "2026-11-08T07:00:10.000Z", kind="task_created", phase="execution"),
    ]
    return rows


def _retry_row(name, start, run_id, kind=None, phase=None, input_tokens=0, output_tokens=0):
    row = _row(name, start, kind=kind, phase=phase, runId=run_id)
    row["runId"] = run_id
    if input_tokens or output_tokens:
        row["usageDetails"] = {
            "input": input_tokens,
            "input_cached": 0,
            "output": output_tokens,
            "total": input_tokens + output_tokens,
        }
    return row


def test_a_shared_correction_trace_selects_the_sent_retry():
    """The blocked attempt owns the trace id; the export must not report it as the run."""
    retry_id = "f" * 32
    blocked = _opened()
    blocked_generation = _retry_row(
        "LLM call · blocked_slot_extraction",
        "2026-11-08T07:00:01.000Z",
        TRACE,
        kind="llm",
        input_tokens=1000,
        output_tokens=500,
    )
    retry_sent = _retry_row(
        "Task-T sent",
        "2026-11-08T07:01:02.000Z",
        retry_id,
        kind="message_sent",
        phase="task_prompt",
    )
    retry_created = _retry_row(
        "Task created",
        "2026-11-08T07:01:10.000Z",
        retry_id,
        kind="task_created",
        phase="execution",
    )
    retry_generation = _retry_row(
        "LLM call · retry_slot_extraction",
        "2026-11-08T07:01:01.000Z",
        retry_id,
        kind="llm",
        input_tokens=10,
        output_tokens=5,
    )

    metrics = build_metrics(
        [blocked, blocked_generation, retry_sent, retry_created, retry_generation],
        trace_id=TRACE,
    )

    assert metrics["traceId"] == TRACE
    assert metrics["runId"] == retry_id
    assert metrics["attemptCount"] == 2
    assert set(metrics["attemptRunIds"]) == {TRACE, retry_id}
    assert metrics["tokens"]["input"] == 10
    assert metrics["tokens"]["output"] == 5
    assert metrics["timeline"]["requestSentAt"] == "2026-11-08T07:01:02.000Z"
    assert metrics["timeline"]["taskCreatedAt"] == "2026-11-08T07:01:10.000Z"

    blocked_metrics = build_metrics(
        [blocked, blocked_generation, retry_sent, retry_created, retry_generation],
        trace_id=TRACE,
        run_id=TRACE,
    )
    assert blocked_metrics["runId"] == TRACE
    assert blocked_metrics["tokens"]["input"] == 1000


def test_metrics_timeline_tokens_and_negotiation():
    metrics = build_metrics(_a2a_rows(), trace_id=TRACE)
    assert metrics["traceId"] == TRACE
    assert metrics["runId"] == TRACE
    assert metrics["protocol"] == "a2a-t"
    assert metrics["scenario"] == "outage"
    assert metrics["runMode"] == "create"
    assert metrics["chainId"] == "conv-test"
    assert metrics["trace"]["name"] == "outage · a2a-t"

    timeline = metrics["timeline"]
    assert timeline["receivedAt"] == "2026-11-08T07:00:00.000Z"
    assert timeline["sdkCalledAt"] == "2026-11-08T07:00:01.000Z"
    assert timeline["requestSentAt"] == "2026-11-08T07:00:02.000Z"
    assert timeline["taskCreatedAt"] == "2026-11-08T07:00:10.000Z"
    assert metrics["latency"]["endToEndMs"] == 10000

    tokens = metrics["tokens"]
    assert tokens["input"] == 300
    assert tokens["cachedInput"] == 120
    assert tokens["output"] == 130
    assert tokens["total"] == 430
    assert tokens["bySide"]["client"]["input"] == 300

    negotiation = metrics["negotiation"]
    assert negotiation["triggered"] is True
    assert negotiation["rounds"] == 2
    # round1: decision 06 - started 04 = 2000ms; round2 has no negotiation_started -> not counted
    assert negotiation["latencyMs"] == 2000
    assert negotiation["roundsDetail"][0]["proposeAt"] == "2026-11-08T07:00:03.000Z"
    assert negotiation["roundsDetail"][0]["sdkConstructedAt"] == "2026-11-08T07:00:06.100Z"
    assert negotiation["roundsDetail"][0]["replySentAt"] == "2026-11-08T07:00:07.000Z"

    counts = metrics["counts"]
    assert counts["observations"] == 11
    assert counts["runObservations"] == 11
    assert counts["clientObservations"] == 10
    assert counts["serverObservations"] == 0
    assert counts["generations"] == 2
    assert metrics["warnings"] == []


def test_metrics_counts_remote_server_tokens_but_not_its_timeline():
    server = _generation("LLM call · planner", "2026-11-08T06:55:00.000Z", 999, 0, 1, side="server", phase="execution")
    server["runId"] = None
    server["metadata"].pop("runId", None)
    rows = _a2a_rows() + [server]
    metrics = build_metrics(rows, trace_id=TRACE)
    # The remote span must not move the run timeline...
    assert metrics["timeline"]["receivedAt"] == "2026-11-08T07:00:00.000Z"
    # ...but its tokens are real RAN-side consumption and stay visible per side.
    assert metrics["tokens"]["bySide"]["server"] == {"input": 999, "cachedInput": 0, "output": 1, "total": 1000}
    assert metrics["tokens"]["input"] == 1299
    assert metrics["tokens"]["output"] == 131
    assert metrics["tokens"]["total"] == 1430
    assert metrics["counts"]["observations"] == 12


def test_metrics_reads_server_tokens_from_otel_attributes_only():
    server = make_row(
        id="send_message",
        name="SendMessage",
        type="AGENT",
        metadata={
            "scope.name": "a2a-t-server",
            "resourceAttributes.service.name": "a2a-t-server",
            "attributes.gen_ai.usage.input_tokens": "138253",
            "attributes.gen_ai.usage.output_tokens": "1407",
        },
    )
    server["startTime"] = "2026-11-08T06:55:00.000Z"
    rows = _a2a_rows() + [server]
    metrics = build_metrics(rows, trace_id=TRACE)
    assert metrics["tokens"]["bySide"]["server"] == {
        "input": 138253,
        "cachedInput": 0,
        "output": 1407,
        "total": 139660,
    }
    assert metrics["tokens"]["input"] == 138553
    assert metrics["tokens"]["output"] == 1537
    assert metrics["tokens"]["total"] == 140090


def test_metrics_missing_values_are_null_with_warnings():
    rows = [_generation("LLM call · adk_agent", "2026-11-08T07:00:01.000Z", 10, 0, 5)]
    rows[0]["runId"] = TRACE
    metrics = build_metrics(rows, trace_id=TRACE)
    assert metrics["timeline"]["receivedAt"] is None
    assert metrics["timeline"]["taskCreatedAt"] is None
    assert metrics["latency"]["endToEndMs"] is None
    assert metrics["negotiation"]["rounds"] == 0
    assert any("end-to-end" in warning for warning in metrics["warnings"])


def test_metrics_falls_back_to_summary_latency():
    class Summary:
        latency_ms = 1234

    rows = [_generation("LLM call · adk_agent", "2026-11-08T07:00:01.000Z", 10, 0, 5)]
    metrics = build_metrics(rows, trace_id=TRACE, summary=Summary())
    assert metrics["latency"]["endToEndMs"] == 1234


def test_metrics_cli_writes_metrics_json_and_manifest(monkeypatch, capsys):
    backend = FakeBackend(rows=_a2a_rows())
    wire_cli_backend(monkeypatch, backend)
    with workdir("metrics_cli") as tmpdir:
        set_env(monkeypatch, "https://example.test")
        out = tmpdir / "out"
        code = cli.main([TRACE, "--out", str(out), "--no-media", "--metrics", "--json"])
        assert code == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["metrics"]["negotiation"]["rounds"] == 2
        assert payload["metrics"]["tokens"]["total"] == 430
        (export,) = list(out.glob("*"))
        metrics = json.loads((export / "metrics.json").read_text(encoding="utf-8"))
        assert metrics["traceId"] == TRACE
        assert metrics["negotiation"]["rounds"] == 2
        manifest = json.loads((export / "manifest.json").read_text(encoding="utf-8"))
        assert "metrics.json" in [entry["path"] for entry in manifest["files"]]


def test_metrics_not_written_without_flag(monkeypatch):
    backend = FakeBackend(rows=_a2a_rows())
    wire_cli_backend(monkeypatch, backend)
    with workdir("metrics_cli_off") as tmpdir:
        set_env(monkeypatch, "https://example.test")
        out = tmpdir / "out"
        code = cli.main([TRACE, "--out", str(out), "--no-media"])
        assert code == 0
        (export,) = list(out.glob("*"))
        assert not (export / "metrics.json").exists()


def test_metrics_prefers_summary_trace_attrs_and_chain_id_fallback():
    class Summary:
        name = "test · a2a-t"
        user_id = "test-user"
        session_id = "conv-summary"
        environment = "default"
        release = None
        latency_ms = 999

    opened = _opened()
    opened["metadata"].pop("chainId", None)  # no explicit chainId -> fall back to sessionId
    rows = [
        opened,
        _row("Run finished", "2026-11-08T07:00:02.000Z", kind="finished", phase="done"),
    ]
    rows[1]["traceName"] = "Run finished"
    metrics = build_metrics(rows, trace_id=TRACE, summary=Summary())
    assert metrics["trace"]["name"] == "test · a2a-t"
    assert metrics["trace"]["sessionId"] == "conv-summary"
    assert metrics["chainId"] == "conv-summary"


def test_metrics_counts_split_server_rows_but_keep_their_timestamps():
    rows = [
        _opened(),
        _row("Task created", "2026-11-08T07:00:05.000Z", kind="task_created", phase="execution", side="server"),
        _row("status", "2026-11-08T07:00:06.000Z", kind="status", phase="execution", side="server"),
        _row("Task-T prompt rendered", "2026-11-08T07:00:01.000Z", kind="task_prompt_rendered", phase="task_prompt"),
    ]
    metrics = build_metrics(rows, trace_id=TRACE)
    assert metrics["timeline"]["taskCreatedAt"] == "2026-11-08T07:00:05.000Z"
    assert metrics["counts"]["runObservations"] == 4
    assert metrics["counts"]["clientObservations"] == 1
    assert metrics["counts"]["serverObservations"] == 2


def test_metrics_token_definition_a_cached_is_not_added_to_input_or_total():
    rows = [_generation("LLM call · adk_agent", "2026-11-08T07:00:01.000Z", 631, 630, 2035)]
    metrics = build_metrics(rows, trace_id=TRACE)
    assert metrics["tokens"]["input"] == 631  # not 1261
    assert metrics["tokens"]["cachedInput"] == 630
    assert metrics["tokens"]["output"] == 2035
    assert metrics["tokens"]["total"] == 2666  # 631 + 2035, cache not added
    assert metrics["tokens"]["bySide"]["client"]["total"] == 2666


def test_metrics_dedupes_parent_span_that_repeats_child_generation_tokens():
    parent = make_row(
        id="send_message",
        name="SendMessage",
        type="AGENT",
        metadata={
            "scope.name": "a2a-t-server",
            "attributes.gen_ai.usage.input_tokens": "130812",
            "attributes.gen_ai.usage.output_tokens": "1500",
        },
    )
    child = make_row(
        id="send_message_negotiation",
        name="SendMessage-negotiation",
        type="GENERATION",
        metadata={"scope.name": "a2a-t-server"},
        usageDetails={"input": 130812, "output": 1500, "total": 132312},
    )
    child["parentObservationId"] = "send_message"
    rows = [_opened(), parent, child]
    metrics = build_metrics(rows, trace_id=TRACE)
    assert metrics["tokens"]["bySide"]["server"] == {
        "input": 130812,
        "cachedInput": 0,
        "output": 1500,
        "total": 132312,
    }
    assert metrics["tokens"]["input"] == 130812  # server counted once


def test_metrics_maps_resolution_message_sent_to_reply_sent():
    rows = _a2a_rows() + [
        _row("Accept sent · R1", "2026-11-08T07:00:07.500Z", kind="message_sent", phase="resolution", round="1"),
    ]
    metrics = build_metrics(rows, trace_id=TRACE)
    detail = metrics["negotiation"]["roundsDetail"][0]
    assert detail["replySentAt"] == "2026-11-08T07:00:07.000Z"  # first message_sent wins


def test_metrics_fills_ao_from_server_send_message_but_keeps_latency_null():
    server = make_row(
        id="send_message_negotiation",
        name="SendMessage-negotiation",
        type="GENERATION",
        metadata={
            "scope.name": "a2a-t-server",
            "attributes.gen_ai.agent.a2at.negotiation.performative": "PROPOSE",
            "attributes.gen_ai.agent.a2at.negotiation.round": "1",
            "attributes.gen_ai.agent.a2at.negotiation.total_duration_ms": "51125",
        },
    )
    server["startTime"] = "2026-11-08T06:44:33.633Z"
    # Remove both client starts (negotiation_started and propose) so AO has to fall
    # back to the server span: P5 only prefers a client propose when one exists.
    rows = [
        row for row in _a2a_rows() if (row.get("metadata") or {}).get("kind") not in {"negotiation_started", "propose"}
    ]
    rows.append(server)
    metrics = build_metrics(rows, trace_id=TRACE)
    detail = metrics["negotiation"]["roundsDetail"][0]
    assert detail["negotiationStartedAt"] == "2026-11-08T06:44:33.633Z"
    assert detail["clock"] == "server"
    assert detail["negotiationStartedAtSource"].startswith("server:")
    # Client decision is on another clock; subtracting would fabricate a latency.
    assert metrics["negotiation"]["latencyMs"] is None
    assert metrics["negotiation"]["serverDurationMs"] == 51125
    assert any("server clock" in warning for warning in metrics["warnings"])


def test_metrics_pure_a2a_round_uses_the_client_propose_as_start():
    rows = [
        _opened(),
        _row("Propose · create · R1", "2026-11-08T07:00:03.000Z", kind="propose", phase="negotiation", round="1"),
        _row("Decision · accept · R1", "2026-11-08T07:00:05.000Z", kind="accept", phase="negotiation", round="1"),
    ]
    metrics = build_metrics(rows, trace_id=TRACE)
    detail = metrics["negotiation"]["roundsDetail"][0]
    assert detail["negotiationStartedAt"] == "2026-11-08T07:00:03.000Z"
    assert detail["negotiationStartedAt"] == detail["proposeAt"]
    assert detail["negotiationStartedAtSource"] == "client:propose"
    assert detail["clock"] == "client"
    assert metrics["negotiation"]["latencyMs"] == 2000
    assert not any("server clock" in warning for warning in metrics["warnings"])


def test_metrics_client_propose_wins_over_the_server_fallback():
    server = make_row(
        id="send_message_negotiation",
        name="SendMessage-negotiation",
        type="GENERATION",
        metadata={
            "scope.name": "a2a-t-server",
            "attributes.gen_ai.agent.a2at.negotiation.performative": "PROPOSE",
            "attributes.gen_ai.agent.a2at.negotiation.round": "1",
            "attributes.gen_ai.agent.a2at.negotiation.total_duration_ms": "51125",
        },
    )
    server["startTime"] = "2026-11-08T06:44:33.633Z"
    rows = [
        _opened(),
        _row("Propose · create · R1", "2026-11-08T07:00:03.000Z", kind="propose", phase="negotiation", round="1"),
        _row("Decision · accept · R1", "2026-11-08T07:00:05.000Z", kind="accept", phase="negotiation", round="1"),
        server,
    ]
    metrics = build_metrics(rows, trace_id=TRACE)
    detail = metrics["negotiation"]["roundsDetail"][0]
    # The client propose fills AO before the server fallback gets a chance to.
    assert detail["negotiationStartedAt"] == "2026-11-08T07:00:03.000Z"
    assert detail["negotiationStartedAtSource"] == "client:propose"
    assert detail["clock"] == "client"
    assert metrics["negotiation"]["latencyMs"] == 2000
    assert metrics["negotiation"]["serverDurationMs"] == 51125
    assert not any("server clock" in warning for warning in metrics["warnings"])


def test_metrics_client_negotiation_started_still_wins_over_propose():
    metrics = build_metrics(_a2a_rows(), trace_id=TRACE)
    detail = metrics["negotiation"]["roundsDetail"][0]
    assert detail["negotiationStartedAt"] == "2026-11-08T07:00:04.000Z"
    assert detail["negotiationStartedAtSource"] == "client:negotiation_started"
    assert detail["clock"] == "client"
    assert metrics["negotiation"]["latencyMs"] == 2000  # 06 - 04, not 06 - 03


def test_metrics_round_detail_carries_negotiation_type_and_hop():
    rows = [
        _opened(),
        _row(
            "Propose · create · R1",
            "2026-11-08T07:00:03.000Z",
            kind="propose",
            phase="negotiation",
            round="1",
            detail={"negotiationType": "information", "hop": "oss"},
        ),
        _row("Decision · accept · R1", "2026-11-08T07:00:05.000Z", kind="accept", phase="negotiation", round="1"),
    ]
    metrics = build_metrics(rows, trace_id=TRACE)
    detail = metrics["negotiation"]["roundsDetail"][0]
    assert detail["negotiationType"] == "information"
    assert detail["hop"] == "oss"


def test_metrics_round_detail_defaults_negotiation_type_and_hop_to_none():
    metrics = build_metrics(_a2a_rows(), trace_id=TRACE)
    detail = metrics["negotiation"]["roundsDetail"][0]
    assert detail["negotiationType"] is None
    assert detail["hop"] is None


def test_metrics_round_detail_keeps_negotiation_type_without_hop():
    rows = [
        _opened(),
        _row(
            "Propose · create · R1",
            "2026-11-08T07:00:03.000Z",
            kind="propose",
            phase="negotiation",
            round="1",
            detail={"negotiationType": "target"},
        ),
    ]
    metrics = build_metrics(rows, trace_id=TRACE)
    detail = metrics["negotiation"]["roundsDetail"][0]
    assert detail["negotiationType"] == "target"
    assert detail["hop"] is None


def test_metrics_round_detail_reads_flattened_langfuse_detail_keys():
    """Langfuse v2 flattens nested attributes: real rows expose `detail.negotiationType`."""
    rows = [
        _opened(),
        _row(
            "Propose · information · R1",
            "2026-11-08T07:00:03.000Z",
            kind="propose",
            phase="negotiation",
            round="1",
            **{"detail.negotiationType": "information", "detail.hop": "oss"},
        ),
        _row("Decision · accept · R1", "2026-11-08T07:00:05.000Z", kind="accept", phase="negotiation", round="1"),
    ]
    metrics = build_metrics(rows, trace_id=TRACE)
    detail = metrics["negotiation"]["roundsDetail"][0]
    assert detail["negotiationType"] == "information"
    assert detail["hop"] == "oss"


def test_metrics_notified_at_uses_decision_requested_when_available():
    rows = _a2a_rows() + [
        _row(
            "Decision requested · information · R1",
            "2026-11-08T07:00:06.000Z",
            kind="decision_requested",
            phase="negotiation",
            round="1",
        ),
    ]
    metrics = build_metrics(rows, trace_id=TRACE)
    detail = metrics["negotiation"]["roundsDetail"][0]
    assert detail["notifiedAt"] == "2026-11-08T07:00:06.000Z"
    assert detail["notifiedAtSource"] == "decision_requested"


def test_metrics_notified_at_falls_back_to_propose():
    # _a2a_rows already carries the client propose span for round 1.
    metrics = build_metrics(_a2a_rows(), trace_id=TRACE)
    detail = metrics["negotiation"]["roundsDetail"][0]
    assert detail["proposeAt"] is not None
    assert detail["notifiedAt"] == detail["proposeAt"]
    assert detail["notifiedAtSource"] == "propose"


def test_metrics_dedupes_identical_usage_on_sibling_server_generations():
    parent = make_row(
        id="send_message",
        name="SendMessage",
        type="AGENT",
        metadata={
            "scope.name": "a2a-t-server",
            "attributes.gen_ai.usage.input_tokens": "1000",
            "attributes.gen_ai.usage.output_tokens": "200",
        },
    )
    children = []
    for name in ("SendMessage-negotiation", "SendMessage-pushNotification"):
        child = make_row(
            id=name,
            name=name,
            type="GENERATION",
            metadata={"scope.name": "a2a-t-server"},
            usageDetails={"input": 1000, "output": 200, "total": 1200},
        )
        child["parentObservationId"] = "send_message"
        children.append(child)
    rows = [_opened(), parent, *children]
    metrics = build_metrics(rows, trace_id=TRACE)
    assert metrics["tokens"]["bySide"]["server"] == {
        "input": 1000,
        "cachedInput": 0,
        "output": 200,
        "total": 1200,
    }


def test_metrics_synthesizes_round_from_run_metadata_and_server_span():
    run = make_row(
        id="run_root",
        name="run · outage · a2a-t",
        type="SPAN",
        metadata={
            "runId": TRACE,
            "receivedAt": "2026-11-09T06:14:59.046Z",
            "rounds": "1",
            "negotiated": "True",
        },
    )
    run["startTime"] = "2026-11-09T06:14:59.047Z"
    server = make_row(
        id="send_message_negotiation",
        name="SendMessage-negotiation",
        type="GENERATION",
        metadata={
            "scope.name": "a2a-t-server",
            "attributes.gen_ai.agent.a2at.negotiation.performative": "PROPOSE",
            "attributes.gen_ai.agent.a2at.negotiation.round": "1",
        },
    )
    server["startTime"] = "2026-11-09T06:02:17.756Z"
    metrics = build_metrics([run, server], trace_id=TRACE)
    negotiation = metrics["negotiation"]
    assert negotiation["triggered"] is True
    assert negotiation["rounds"] == 1
    detail = negotiation["roundsDetail"][0]
    assert detail["round"] == 1
    assert detail["negotiationStartedAt"] == "2026-11-09T06:02:17.756Z"
    assert detail["clock"] == "server"
    assert negotiation["latencyMs"] is None


def test_metrics_ignores_status_rows_when_picking_server_negotiation_start():
    run = make_row(
        id="run_root",
        name="run · outage · a2a-t",
        type="SPAN",
        metadata={"runId": TRACE, "rounds": "1", "negotiated": "True"},
    )
    run["startTime"] = "2026-11-09T06:14:59.047Z"
    status = make_row(
        id="status_delivery",
        name="## Feasibility Negotiation\nThe assessment has been completed.",
        type="SPAN",
        metadata={"runId": TRACE, "kind": "status", "side": "server", "round": "1"},
    )
    status["startTime"] = "2026-11-09T06:16:04.905Z"
    server = make_row(
        id="send_message_negotiation",
        name="SendMessage-negotiation",
        type="GENERATION",
        metadata={
            "scope.name": "a2a-t-server",
            "attributes.gen_ai.agent.a2at.negotiation.performative": "PROPOSE",
            "attributes.gen_ai.agent.a2at.negotiation.round": "1",
        },
    )
    server["startTime"] = "2026-11-09T06:02:17.756Z"
    metrics = build_metrics([run, status, server], trace_id=TRACE)
    detail = metrics["negotiation"]["roundsDetail"][0]
    assert detail["negotiationStartedAt"] == "2026-11-09T06:02:17.756Z"
    assert detail["negotiationStartedAtSource"] == "server:SendMessage-negotiation"


def test_metrics_without_usage_reports_zero_side_buckets():
    rows = [
        _opened(),
        _row("Task created", "2026-11-09T07:31:10.468Z", kind="task_created", phase="execution"),
        _row("Run finished", "2026-11-09T07:31:19.152Z", kind="finished", phase="done"),
    ]
    metrics = build_metrics(rows, trace_id=TRACE)
    assert metrics["tokens"]["input"] == 0
    assert metrics["tokens"]["total"] == 0
    assert metrics["tokens"]["bySide"]["client"] == {
        "input": 0,
        "cachedInput": 0,
        "output": 0,
        "total": 0,
    }
    assert metrics["tokens"]["bySide"]["server"] == {
        "input": 0,
        "cachedInput": 0,
        "output": 0,
        "total": 0,
    }
    assert any("no token usage" in warning for warning in metrics["warnings"])
