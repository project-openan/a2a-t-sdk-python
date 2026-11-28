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
        _generation("LLM call · task_t_slot_extraction", "2026-11-08T07:00:01.000Z", 100, 20, 50, stage="task_t_slot_extraction"),
        _row("Task-T prompt rendered", "2026-11-08T07:00:01.000Z", kind="task_prompt_rendered", phase="task_prompt"),
        _row("Task-T sent", "2026-11-08T07:00:02.000Z", kind="message_sent", phase="task_prompt"),
        _row("Propose · create · R1", "2026-11-08T07:00:03.000Z", kind="propose", phase="negotiation", round="1"),
        _row("Negotiation started · R1", "2026-11-08T07:00:04.000Z", kind="negotiation_started", phase="negotiation", round="1"),
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
    assert tokens["input"] == 420        # gross: 300 non-cached + 120 cached
    assert tokens["cachedInput"] == 120
    assert tokens["output"] == 130
    assert tokens["total"] == 550
    assert tokens["bySide"]["client"]["input"] == 420

    negotiation = metrics["negotiation"]
    assert negotiation["triggered"] is True
    assert negotiation["rounds"] == 2
    # round1: decision 06 - started 04 = 2000ms; round2 has no negotiation_started -> not counted
    assert negotiation["latencyMs"] is None  # no S3/S4/S7 server data in this fixture
    assert negotiation["roundsDetail"][0]["proposeAt"] == "2026-11-08T07:00:03.000Z"
    assert negotiation["roundsDetail"][0]["sdkConstructedAt"] == "2026-11-08T07:00:06.100Z"
    assert negotiation["roundsDetail"][0]["replySentAt"] == "2026-11-08T07:00:07.000Z"

    counts = metrics["counts"]
    assert counts["observations"] == 11
    assert counts["runObservations"] == 11
    assert counts["clientObservations"] == 10
    assert counts["serverObservations"] == 0
    assert counts["generations"] == 2
    assert "negotiation rounds found but latency could not be computed" in metrics["warnings"]


def test_metrics_counts_remote_server_tokens_but_not_its_timeline():
    server = _generation(
        "LLM call · planner", "2026-11-08T06:55:00.000Z", 999, 0, 1, side="server", phase="execution"
    )
    server["runId"] = None
    server["metadata"].pop("runId", None)
    rows = _a2a_rows() + [server]
    metrics = build_metrics(rows, trace_id=TRACE)
    # The remote span must not move the run timeline...
    assert metrics["timeline"]["receivedAt"] == "2026-11-08T07:00:00.000Z"
    # ...but its tokens are real RAN-side consumption and stay visible per side.
    assert metrics["tokens"]["bySide"]["server"] == {"input": 999, "cachedInput": 0, "output": 1, "total": 1000}
    assert metrics["tokens"]["input"] == 1419
    assert metrics["tokens"]["output"] == 131
    assert metrics["tokens"]["total"] == 1550
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
    assert metrics["tokens"]["input"] == 138673
    assert metrics["tokens"]["output"] == 1537
    assert metrics["tokens"]["total"] == 140210


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
        assert payload["metrics"]["tokens"]["total"] == 550
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


def test_metrics_gross_input_includes_cached_tokens():
    rows = [_generation("LLM call · adk_agent", "2026-11-08T07:00:01.000Z", 631, 630, 2035)]
    metrics = build_metrics(rows, trace_id=TRACE)
    assert metrics["tokens"]["input"] == 1261         # 631 non-cached + 630 cached
    assert metrics["tokens"]["cachedInput"] == 630
    assert metrics["tokens"]["output"] == 2035
    assert metrics["tokens"]["total"] == 3296         # 1261 + 2035
    assert metrics["tokens"]["bySide"]["client"]["total"] == 3296


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
    # Remove the client negotiation_started row so AO has to fall back to the server span.
    rows = [row for row in _a2a_rows() if (row.get("metadata") or {}).get("kind") != "negotiation_started"]
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


def test_metrics_notified_at_uses_decision_requested_when_available():
    rows = _a2a_rows() + [
        _row("Decision requested · information · R1", "2026-11-08T07:00:06.000Z",
             kind="decision_requested", phase="negotiation", round="1"),
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
        "input": 0, "cachedInput": 0, "output": 0, "total": 0,
    }
    assert metrics["tokens"]["bySide"]["server"] == {
        "input": 0, "cachedInput": 0, "output": 0, "total": 0,
    }
    assert any("no token usage" in warning for warning in metrics["warnings"])


def test_decision_round_mode_pairs_propose_with_following_decision():
    # Raw API order is newest-first; the proposal carries no round metadata.
    rows = [
        _row("Decision · accept · R1", "2026-11-10T02:20:21.736Z", kind="accept", phase="resolution", round="1"),
        _row("Propose · information", "2026-11-10T02:18:51.276Z", kind="propose", phase="negotiation"),
        _opened("2026-11-10T02:17:43.591Z"),
    ]
    legacy = build_metrics(rows, trace_id=TRACE)
    assert legacy["negotiation"]["rounds"] == 1  # overridden by the S-timing round model

    decision = build_metrics(rows, trace_id=TRACE, round_mode="decision")
    negotiation = decision["negotiation"]
    assert negotiation["roundMode"] == "decision"
    assert negotiation["rounds"] == 1
    entry = negotiation["roundsDetail"][0]
    assert entry["proposeAt"] == "2026-11-10T02:18:51.276Z"
    assert entry["decisionAt"] == "2026-11-10T02:20:21.736Z"


def test_decision_round_mode_keeps_two_decision_rounds():
    rows = [
        _row("Decision · accept · R2", "2026-11-10T02:25:53.564Z", kind="accept", phase="resolution", round="2"),
        _row("Propose · information", "2026-11-10T02:23:05.775Z", kind="propose", phase="negotiation"),
        _row("Decision · accept · R1", "2026-11-10T02:22:36.596Z", kind="accept", phase="resolution", round="1"),
        _row("Propose · information", "2026-11-10T02:21:06.898Z", kind="propose", phase="negotiation"),
        _opened("2026-11-10T02:20:17.901Z"),
    ]
    decision = build_metrics(rows, trace_id=TRACE, round_mode="decision")
    negotiation = decision["negotiation"]
    assert negotiation["rounds"] == 2
    first, second = negotiation["roundsDetail"]
    assert (first["proposeAt"], first["decisionAt"]) == (
        "2026-11-10T02:21:06.898Z",
        "2026-11-10T02:22:36.596Z",
    )
    assert (second["proposeAt"], second["decisionAt"]) == (
        "2026-11-10T02:23:05.775Z",
        "2026-11-10T02:25:53.564Z",
    )


def test_decision_round_mode_attaches_client_negotiation_started():
    rows = [
        _row("Decision · accept · R1", "2026-11-10T02:20:21.736Z", kind="accept", phase="resolution", round="1"),
        _row("Negotiation started", "2026-11-10T02:18:00.000Z", kind="negotiation_started", phase="negotiation"),
        _opened("2026-11-10T02:17:43.591Z"),
    ]
    decision = build_metrics(rows, trace_id=TRACE, round_mode="decision")
    entry = decision["negotiation"]["roundsDetail"][0]
    assert entry["negotiationStartedAt"] == "2026-11-10T02:18:00.000Z"
    assert entry["clock"] == "client"
    assert decision["negotiation"]["latencyMs"] is None


def test_metrics_reads_server_cache_read_tokens_with_definition_a():
    server = make_row(
        id="send_message",
        name="SendMessage",
        type="AGENT",
        metadata={
            "scope.name": "a2a-t-server",
            "attributes.gen_ai.usage.input_tokens": "260884",
            "attributes.gen_ai.usage.cache_read.input_tokens": "242048",
            "attributes.gen_ai.usage.cache_write.input_tokens": "18836",
            "attributes.gen_ai.usage.output_tokens": "3023",
        },
    )
    rows = [_opened(), server]
    metrics = build_metrics(rows, trace_id=TRACE)
    side = metrics["tokens"]["bySide"]["server"]
    assert side["input"] == 260884         # gross: cache_read + cache_write
    assert side["cachedInput"] == 242048
    assert side["output"] == 3023
    assert side["total"] == 263907         # input + output
    assert metrics["tokens"]["input"] == 260884
    assert metrics["tokens"]["cachedInput"] == 242048
    assert metrics["tokens"]["total"] == 263907


def test_metrics_issue6_s_timings_and_negotiation_latency():
    opened = _opened("2026-11-11T09:50:01.968Z")
    run = make_row(
        id="retry_run",
        name="run · night · a2a-t",
        type="SPAN",
        metadata={"runId": "b1b92414b5374363a94cd5c451c450b2", "rounds": "1", "negotiated": "True"},
    )
    run["startTime"] = "2026-11-11T09:50:18.665Z"
    run["endTime"] = "2026-11-11T10:34:45.308Z"

    def span(name, start, end, kind=None, side="client", **meta):
        row = make_row(id=name, name=name, type="SPAN", metadata={"runId": "b1b92414b5374363a94cd5c451c450b2", "side": side, **({"kind": kind} if kind else {}), **meta})
        row["startTime"] = start
        row["endTime"] = end
        return row

    rows = [
        opened,
        run,
        span("Task-T validation passed", "2026-11-11T09:50:23.703Z", "2026-11-11T09:50:23.703Z", kind="task_validation"),
        span("Propose · information · R1", "2026-11-11T09:51:41.934Z", "2026-11-11T09:51:41.934Z", kind="propose", side="server", round="1"),
        span("Decision requested · information R1", "2026-11-11T09:51:41.935Z", "2026-11-11T09:51:41.935Z", kind="decision_requested", round="1"),
        span("Decision · accept · R1", "2026-11-11T09:51:50.009Z", "2026-11-11T09:51:50.010Z", kind="accept", round="1"),
        span("Accept sent", "2026-11-11T09:51:51.017Z", "2026-11-11T09:51:51.017Z", kind="message_sent", phase="resolution", round="1"),
        span("Propose · information · R1", "2026-11-11T09:52:12.381Z", "2026-11-11T09:52:12.381Z", kind="propose", side="server", round="1"),
        span("Decision requested · information R1", "2026-11-11T09:57:12.398Z", "2026-11-11T09:57:12.398Z", kind="decision_requested", round="1"),
    ]
    for idx, (start, end) in enumerate(
        [
            ("2026-11-11T09:36:37.955Z", "2026-11-11T09:38:01.746Z"),
            ("2026-11-11T09:38:01.596Z", "2026-11-11T09:38:28.017Z"),
        ]
    ):
        send = make_row(
            id=f"send_{idx}",
            name="SendMessage",
            type="AGENT",
            metadata={
                "scope.name": "a2a-t-server",
                **(
                    {"attributes.gen_ai.agent.a2a.response": {"status": {"state": "TASK_STATE_INPUT_REQUIRED"}}}
                    if idx == 0
                    else {}
                ),
            },
        )
        send["startTime"] = start
        send["endTime"] = end
        rows.append(send)
        neg = make_row(id=f"neg_{idx}", name="SendMessage-negotiation", type="GENERATION", metadata={"scope.name": "a2a-t-server"})
        neg["startTime"] = start
        neg["endTime"] = end
        neg["parentObservationId"] = f"send_{idx}"
        rows.append(neg)

    metrics = build_metrics(rows, trace_id=TRACE)
    negotiation = metrics["negotiation"]
    assert negotiation["s1Ms"] == 5038  # retry run start → first passed validation
    assert negotiation["rounds"] == 2
    first, second = negotiation["roundsDetail"]
    assert first["s3Ms"] == 83791
    assert first["s4RawMs"] == 8074
    assert first["s4Ms"] == 0  # only instantaneous tool/decision spans inside
    assert first["s7RawMs"] == 1008
    assert first["s7Ms"] == 1  # the 1 ms accept span itself
    assert first["negotiationLatencyMs"] == 83792
    assert second["s3Ms"] == 26421
    assert second["s4Ms"] is None
    assert second["negotiationLatencyMs"] is None
    assert negotiation["latencyMs"] == 83792


def test_metrics_excludes_agent_llm_from_client_tokens():
    rows = [
        _opened(),
        _generation("LLM call · task_t_slot_extraction", "2026-11-12T00:00:01.000Z", 1000, 200, 100, stage="task_t_slot_extraction"),
        _generation("LLM call · client_decision_agent", "2026-11-12T00:00:02.000Z", 500, 300, 50, stage="client_decision_agent"),
        _generation("LLM call · adk_agent", "2026-11-12T00:00:03.000Z", 400, 100, 40, stage="adk_agent"),
    ]
    metrics = build_metrics(rows, trace_id=TRACE)
    client = metrics["tokens"]["bySide"]["client"]
    assert client["input"] == 1200
    assert client["cachedInput"] == 200
    assert client["output"] == 100
    assert client["total"] == 1300
    excluded = metrics["tokens"]["excludedClient"]
    assert excluded["input"] == 1300
    assert excluded["cachedInput"] == 400
    assert excluded["output"] == 90
    assert excluded["total"] == 1390


def test_metrics_pure_latency_sums_server_send_message_durations():
    rows = [_opened(protocol="pure")]
    for idx, (start, end) in enumerate(
        [
            ("2026-11-11T20:00:00.000Z", "2026-11-11T20:00:01.000Z"),
            ("2026-11-11T20:00:02.000Z", "2026-11-11T20:00:04.000Z"),
        ]
    ):
        row = make_row(
            id=f"sm_{idx}",
            name="SendMessage",
            type="AGENT",
            metadata={
                "runId": TRACE,
                "scope.name": "a2a-t-server",
                **(
                    {"attributes.gen_ai.agent.a2a.response": {"status": {"state": "TASK_STATE_INPUT_REQUIRED"}}}
                    if idx == 0
                    else {}
                ),
            },
        )
        row["startTime"] = start
        row["endTime"] = end
        rows.append(row)
    metrics = build_metrics(rows, trace_id=TRACE)
    negotiation = metrics["negotiation"]
    assert negotiation["latencyMs"] == 3000
    assert negotiation["latencySource"] == "pure:sum(SendMessage durations)"
    assert negotiation["sendMessageDurationsMs"] == [1000, 2000]


def test_metrics_s7_includes_negotiation_accept_extraction():
    rows = [
        _opened(protocol="a2a-t"),
        _row("Decision requested · information R1", "2026-11-12T00:00:00.000Z", kind="decision_requested", round="1"),
        _row("Decision · accept · R1", "2026-11-12T00:00:00.500Z", kind="accept", round="1"),
        _row("Accept sent", "2026-11-12T00:00:02.000Z", kind="message_sent", phase="resolution", round="1"),
    ]
    llm = make_row(
        id="accept_extraction",
        name="LLM call · negotiation_accept_extraction",
        type="GENERATION",
        metadata={
            "runId": TRACE,
            "kind": "llm",
            "side": "client",
            "stage": "negotiation_accept_extraction",
        },
    )
    llm["startTime"] = "2026-11-12T00:00:00.600Z"
    llm["endTime"] = "2026-11-12T00:00:01.000Z"
    rows.append(llm)
    metrics = build_metrics(rows, trace_id=TRACE)
    assert metrics["negotiation"]["roundsDetail"][0]["s7Ms"] == 400


def test_metrics_negotiation_requires_input_required_response():
    rows = [_opened(protocol="pure")]
    send = make_row(
        id="sm_task",
        name="SendMessage",
        type="AGENT",
        metadata={
            "runId": TRACE,
            "scope.name": "a2a-t-server",
            "attributes.gen_ai.agent.a2a.response": {"status": {"state": "TASK_STATE_SUBMITTED"}},
        },
    )
    send["startTime"] = "2026-11-12T00:00:01.000Z"
    send["endTime"] = "2026-11-12T00:00:05.000Z"
    rows.append(send)
    metrics = build_metrics(rows, trace_id=TRACE)
    negotiation = metrics["negotiation"]
    assert negotiation["triggered"] is False
    assert negotiation["rounds"] == 0
    assert negotiation["latencyMs"] is None
    assert negotiation["inputRequiredRounds"] == 0


def test_metrics_end_to_end_falls_back_to_task_succeeded_status():
    rows = [
        _opened(received="2026-11-12T00:00:00.000Z"),
        _row(
            "Task modification succeeded",
            "2026-11-12T00:00:30.000Z",
            kind="status",
            side="server",
        ),
    ]
    metrics = build_metrics(rows, trace_id=TRACE)
    assert metrics["latency"]["endToEndMs"] == 30000
    assert metrics["timeline"]["taskCreatedAt"] == "2026-11-12T00:00:30.000Z"


def test_metrics_negotiation_falls_back_to_client_accept_without_input_required():
    rows = [
        _opened(protocol="pure", negotiated="True", rounds="1"),
        _row("Decision · accept · R1", "2026-11-12T00:00:02.000Z", kind="accept", round="1"),
    ]
    send = make_row(
        id="sm_task",
        name="SendMessage",
        type="AGENT",
        metadata={
            "runId": TRACE,
            "scope.name": "a2a-t-server",
            "attributes.gen_ai.agent.a2a.response": {"status": {"state": "TASK_STATE_WORKING"}},
        },
    )
    send["startTime"] = "2026-11-12T00:00:01.000Z"
    send["endTime"] = "2026-11-12T00:00:05.000Z"
    rows.append(send)
    metrics = build_metrics(rows, trace_id=TRACE)
    negotiation = metrics["negotiation"]
    assert negotiation["triggered"] is True
    assert negotiation["rounds"] == 1
    assert negotiation["latencyMs"] == 4000

def test_metrics_counts_client_interceptions_per_blocked_validation():
    rows = [
        _opened(),
        _row(
            "Task-T validation blocked",
            "2026-11-12T00:00:02.000Z",
            kind="task_validation",
            **{"detail.verdict": "blocked", "detail.blocking": True, "detail.reason": "negotiation.semantic_rejected"},
        ),
        _row("Task-T validation passed", "2026-11-12T00:00:03.000Z", kind="task_validation"),
    ]
    metrics = build_metrics(rows, trace_id=TRACE)
    assert metrics["counts"]["clientInterceptions"] == 1
    assert metrics["clientInterception"] == {
        "count": 1,
        "blockedRuns": 1,
        "runIds": [TRACE],
        "reasons": ["negotiation.semantic_rejected"],
        "corrected": False,
    }


def test_metrics_client_interception_tracks_retry_runs_and_correction():
    other = "fedcba9876543210fedcba9876543210"
    rows = [
        _opened(),
        _row(
            "Task-T validation blocked",
            "2026-11-12T00:00:02.000Z",
            kind="task_validation",
            **{"detail.verdict": "blocked", "detail.reason": "slot.constraint_violated"},
        ),
        _row("Request corrected", "2026-11-12T00:00:02.500Z", kind="correction_applied"),
        _row(
            "Task-T validation blocked",
            "2026-11-12T00:00:05.000Z",
            kind="task_validation",
            runId=other,
            **{"detail.reason": "negotiation.semantic_rejected"},
        ),
    ]
    interception = build_metrics(rows, trace_id=TRACE)["clientInterception"]
    assert interception["count"] == 2
    assert interception["blockedRuns"] == 2
    assert interception["runIds"] == [TRACE, other]
    assert interception["reasons"] == ["slot.constraint_violated", "negotiation.semantic_rejected"]
    assert interception["corrected"] is True


def test_metrics_client_interception_zero_when_never_blocked():
    metrics = build_metrics(_a2a_rows(), trace_id=TRACE)
    assert metrics["counts"]["clientInterceptions"] == 0
    assert metrics["clientInterception"] == {
        "count": 0,
        "blockedRuns": 0,
        "runIds": [],
        "reasons": [],
        "corrected": False,
    }


def test_metrics_counts_send_message_not_its_push_notification_child():
    """Issue #15: the parent SendMessage already aggregates the nested generation."""
    parent = make_row(
        id="send_message",
        name="SendMessage",
        type="AGENT",
        metadata={
            "scope.name": "a2a-t-server",
            "attributes.gen_ai.usage.input_tokens": "241706",
            "attributes.gen_ai.usage.output_tokens": "909",
            "attributes.gen_ai.usage.cache_read.input_tokens": "227584",
        },
    )
    child = make_row(
        id="push_notification",
        name="SendMessage-pushNotification",
        type="GENERATION",
        metadata={"scope.name": "a2a-t-server"},
        usageDetails={"input": 248405, "output": 957, "total": 249362},
    )
    child["parentObservationId"] = "send_message"
    metrics = build_metrics([_opened(), parent, child], trace_id=TRACE)
    assert metrics["tokens"]["bySide"]["server"] == {
        "input": 241706,
        "cachedInput": 227584,
        "output": 909,
        "total": 242615,
    }
    assert metrics["tokens"]["input"] == 241706


def test_metrics_keeps_push_notification_when_parent_reports_no_usage():
    parent = make_row(id="send_message", name="SendMessage", type="AGENT",
                      metadata={"scope.name": "a2a-t-server"})
    child = make_row(
        id="push_notification",
        name="SendMessage-pushNotification",
        type="GENERATION",
        metadata={"scope.name": "a2a-t-server"},
        usageDetails={"input": 1000, "output": 100, "total": 1100},
    )
    child["parentObservationId"] = "send_message"
    metrics = build_metrics([_opened(), parent, child], trace_id=TRACE)
    assert metrics["tokens"]["bySide"]["server"] == {
        "input": 1000,
        "cachedInput": 0,
        "output": 100,
        "total": 1100,
    }



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

def test_metrics_client_negotiation_started_still_wins_over_propose():
    metrics = build_metrics(_a2a_rows(), trace_id=TRACE)
    detail = metrics["negotiation"]["roundsDetail"][0]
    assert detail["negotiationStartedAt"] == "2026-11-08T07:00:04.000Z"
    assert detail["negotiationStartedAtSource"] == "client:negotiation_started"
    assert detail["clock"] == "client"
    # Negotiation latency now comes from the S timings; this trace has no server spans.
    assert metrics["negotiation"]["latencyMs"] is None

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

def test_decision_round_mode_carries_negotiation_type_and_hop():
    rows = [
        _row(
            "Propose · information",
            "2026-11-10T02:18:51.276Z",
            kind="propose",
            phase="negotiation",
            detail={"negotiationType": "information", "hop": "oss"},
        ),
        _row("Decision · accept · R1", "2026-11-10T02:20:21.736Z", kind="accept", phase="resolution", round="1"),
        _opened("2026-11-10T02:17:43.591Z"),
    ]
    decision = build_metrics(rows, trace_id=TRACE, round_mode="decision")
    negotiation = decision["negotiation"]
    assert negotiation["roundMode"] == "decision"
    assert negotiation["rounds"] == 1
    entry = negotiation["roundsDetail"][0]
    assert entry["negotiationType"] == "information"
    assert entry["hop"] == "oss"
    assert entry["proposeAt"] == "2026-11-10T02:18:51.276Z"
    assert entry["decisionAt"] == "2026-11-10T02:20:21.736Z"

def test_decision_round_mode_tolerates_a_missing_start_time():
    # A row without startTime must not be compared to a datetime (P9b): the proposal
    # falls back to the run's single rounds bucket instead of crashing the export.
    rows = [
        _row("Propose · information", None, kind="propose", phase="negotiation"),
        _row("Decision · accept · R1", "2026-11-10T02:20:21.736Z", kind="accept", phase="resolution", round="1"),
        _opened("2026-11-10T02:17:43.591Z"),
    ]
    decision = build_metrics(rows, trace_id=TRACE, round_mode="decision")
    negotiation = decision["negotiation"]
    assert negotiation["roundMode"] == "decision"
    assert negotiation["rounds"] == 1
    entry = negotiation["roundsDetail"][0]
    assert entry["proposeAt"] is None
    assert entry["decisionAt"] == "2026-11-10T02:20:21.736Z"

def test_decision_round_mode_reads_flattened_langfuse_detail_keys():
    """Real rows flatten nested attributes: decision mode must read `detail.*` too (P9a)."""
    rows = [
        _opened("2026-11-10T02:17:43.591Z"),
        _row(
            "Propose · information",
            "2026-11-10T02:18:51.276Z",
            kind="propose",
            phase="negotiation",
            **{"detail.negotiationType": "information", "detail.hop": "oss"},
        ),
        _row("Decision · accept · R1", "2026-11-10T02:20:21.736Z", kind="accept", phase="resolution", round="1"),
    ]
    decision = build_metrics(rows, trace_id=TRACE, round_mode="decision")
    detail = decision["negotiation"]["roundsDetail"][0]
    assert detail["negotiationType"] == "information"
    assert detail["hop"] == "oss"
