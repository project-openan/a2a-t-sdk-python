"""Contract tests for the Langfuse observe CSV table (design 2026-11-09 §5)."""

from __future__ import annotations

import csv
import io

from demo.web.observe.table import (
    FIELD_NOTES,
    MAIN_COLUMNS,
    ROUNDS_COLUMNS,
    build_row,
    fields_csv,
    main_csv,
    rounds_csv,
)


def test_main_columns_follow_the_contract_order() -> None:
    assert MAIN_COLUMNS == (
        "traceId",
        "runId",
        "caseId",
        "batchId",
        "chainId",
        "protocol",
        "scenario",
        "runMode",
        "mode",
        "modelMode",
        "sourceKind",
        "receivedAt",
        "sdkCalledAt",
        "requestSentAt",
        "taskCreatedAt",
        "prepareMs",
        "sdkMs",
        "dispatchMs",
        "endToEndMs",
        "input",
        "cachedInput",
        "output",
        "total",
        "client.input",
        "client.cachedInput",
        "client.output",
        "client.total",
        "server.input",
        "server.cachedInput",
        "server.output",
        "server.total",
        "cacheHitRate",
        "inputExcludingCached",
        "negotiationTriggered",
        "rounds",
        "negotiationLatencyMs",
        "serverDurationMs",
        "observations",
        "runObservations",
        "clientObservations",
        "serverObservations",
        "generations",
        "toolCalls",
        "warnings",
        "exportStatus",
        "status",
        "finalState",
        "passed",
        "accuracy",
        "startedAt",
        "finishedAt",
    )


def test_rounds_columns_keep_the_prefix_and_append_pure_a2a_fields() -> None:
    assert ROUNDS_COLUMNS == (
        "traceId",
        "round",
        "proposeAt",
        "notifiedAt",
        "notifiedAtSource",
        "negotiationStartedAt",
        "negotiationStartedAtSource",
        "clock",
        "decisionAt",
        "sdkConstructedAt",
        "replySentAt",
        "negotiationType",
        "hop",
    )


def test_failed_export_keeps_identity_and_warnings_only() -> None:
    row = build_row(
        "abc123",
        "not_found",
        None,
        ["trace not found or no data yet", "second reason"],
        {
            "traceId": "abc123",
            "runId": "run-1",
            "caseId": "case-1",
            "batchId": "batch-1",
            "chainId": "chain-1",
            "status": "finished",
        },
    )

    assert row["traceId"] == "abc123"
    assert row["runId"] == "run-1"
    assert row["caseId"] == "case-1"
    assert row["batchId"] == "batch-1"
    assert row["chainId"] == "chain-1"
    assert row["warnings"] == "trace not found or no data yet; second reason"
    assert row["exportStatus"] == "not_found"
    filled = {"traceId", "runId", "caseId", "batchId", "chainId", "warnings", "exportStatus"}
    assert {column: value for column, value in row.items() if column not in filled} == {
        column: "" for column in MAIN_COLUMNS if column not in filled
    }


def _ok_metrics() -> dict[str, object]:
    return {
        "traceId": "t1",
        "runId": "r1",
        "caseId": "c1",
        "batchId": None,
        "chainId": "ch1",
        "protocol": "a2a-t",
        "scenario": "rate",
        "runMode": "live",
        "mode": "auto",
        "modelMode": "live",
        "sourceKind": "presentation",
        "timeline": {
            "receivedAt": "2026-11-09T10:00:00.000Z",
            "sdkCalledAt": "2026-11-09T10:00:00.250Z",
            "requestSentAt": "2026-11-09T10:00:00.500Z",
            "taskCreatedAt": "2026-11-09T10:00:01.000Z",
        },
        "latency": {"endToEndMs": 1000},
        "tokens": {
            "input": 1000,
            "cachedInput": 250,
            "output": 100,
            "total": 1100,
            "bySide": {
                "client": {"input": 600, "cachedInput": 100, "output": 50, "total": 650},
                "server": {"input": 400, "cachedInput": 150, "output": 50, "total": 450},
            },
        },
        "negotiation": {
            "triggered": False,
            "rounds": 0,
            "latencyMs": None,
            "serverDurationMs": None,
            "roundsDetail": [],
        },
        "counts": {
            "observations": 12,
            "runObservations": 10,
            "clientObservations": 7,
            "serverObservations": 3,
            "generations": 2,
            "toolCalls": 1,
        },
        "warnings": [],
    }


def test_ok_row_maps_identity_timeline_tokens_and_counts() -> None:
    row = build_row("t1", "ok", _ok_metrics(), [], None)

    assert row["traceId"] == "t1"
    assert row["runId"] == "r1"
    assert row["caseId"] == "c1"
    assert row["batchId"] == ""
    assert row["chainId"] == "ch1"
    assert row["protocol"] == "a2a-t"
    assert row["scenario"] == "rate"
    assert row["runMode"] == "live"
    assert row["mode"] == "auto"
    assert row["modelMode"] == "live"
    assert row["sourceKind"] == "presentation"

    assert row["receivedAt"] == "2026-11-09T10:00:00.000Z"
    assert row["sdkCalledAt"] == "2026-11-09T10:00:00.250Z"
    assert row["requestSentAt"] == "2026-11-09T10:00:00.500Z"
    assert row["taskCreatedAt"] == "2026-11-09T10:00:01.000Z"
    assert row["prepareMs"] == "250"
    assert row["sdkMs"] == "250"
    assert row["dispatchMs"] == "500"
    assert row["endToEndMs"] == "1000"

    assert row["input"] == "1000"
    assert row["cachedInput"] == "250"
    assert row["output"] == "100"
    assert row["total"] == "1100"
    assert row["client.input"] == "600"
    assert row["client.cachedInput"] == "100"
    assert row["client.output"] == "50"
    assert row["client.total"] == "650"
    assert row["server.input"] == "400"
    assert row["server.cachedInput"] == "150"
    assert row["server.output"] == "50"
    assert row["server.total"] == "450"
    assert row["cacheHitRate"] == "0.2500"
    assert row["inputExcludingCached"] == "750"

    assert row["negotiationTriggered"] == "false"
    assert row["rounds"] == "0"
    assert row["negotiationLatencyMs"] == ""
    assert row["serverDurationMs"] == ""
    assert row["observations"] == "12"
    assert row["runObservations"] == "10"
    assert row["clientObservations"] == "7"
    assert row["serverObservations"] == "3"
    assert row["generations"] == "2"
    assert row["toolCalls"] == "1"
    assert row["warnings"] == ""
    assert row["exportStatus"] == "ok"
    assert row["status"] == ""
    assert row["finalState"] == ""
    assert row["passed"] == ""
    assert row["accuracy"] == ""
    assert row["startedAt"] == ""
    assert row["finishedAt"] == ""


def test_derived_latency_missing_or_unparsable_endpoints_leave_column_blank() -> None:
    metrics = _ok_metrics()
    metrics["timeline"] = {
        "receivedAt": "2026-11-09T10:00:00.000Z",
        "sdkCalledAt": "2026-99-99T10:00:00Z",
        "requestSentAt": None,
        "taskCreatedAt": "2026-11-09T10:00:01.000Z",
    }
    metrics["latency"] = {}

    row = build_row("t1", "ok", metrics, ["upstream"], None)

    assert row["prepareMs"] == ""
    assert row["sdkMs"] == ""
    assert row["dispatchMs"] == ""
    assert row["endToEndMs"] == ""
    assert row["warnings"] == (
        "upstream; "
        "stage latency unavailable: prepareMs; "
        "stage latency unavailable: sdkMs; "
        "stage latency unavailable: dispatchMs; "
        "stage latency unavailable: endToEndMs"
    )


def test_cache_hit_rate_zero_denominator_and_clamped_input_excluding_cached() -> None:
    zero = _ok_metrics()
    zero["tokens"] = {
        "input": 0,
        "cachedInput": 0,
        "output": 0,
        "total": 0,
        "bySide": {},
    }
    zero_row = build_row("t1", "ok", zero, [], None)
    assert zero_row["cacheHitRate"] == ""
    assert zero_row["inputExcludingCached"] == "0"
    assert zero_row["warnings"] == ""

    inflated = _ok_metrics()
    inflated["tokens"] = {
        "input": 100,
        "cachedInput": 150,
        "output": 10,
        "total": 260,
        "bySide": {},
    }
    inflated_row = build_row("t1", "ok", inflated, ["upstream"], None)
    assert inflated_row["cacheHitRate"] == "1.5000"
    assert inflated_row["inputExcludingCached"] == "0"
    assert inflated_row["warnings"] == (
        "upstream; cachedInput exceeds input; inputExcludingCached clamped to 0"
    )


def test_missing_tokens_counts_as_zero_but_missing_by_side_stays_blank() -> None:
    # Upstream always emits the top-level four token counters, so a missing object is
    # treated as "all zero"; bySide sides are only rendered when that side exists.
    metrics = _ok_metrics()
    del metrics["tokens"]

    row = build_row("t1", "ok", metrics, [], None)

    assert (row["input"], row["cachedInput"], row["output"], row["total"]) == (
        "0",
        "0",
        "0",
        "0",
    )
    assert row["cacheHitRate"] == ""
    assert row["inputExcludingCached"] == "0"
    assert row["client.input"] == ""
    assert row["client.cachedInput"] == ""
    assert row["client.output"] == ""
    assert row["client.total"] == ""
    assert row["server.input"] == ""
    assert row["server.cachedInput"] == ""
    assert row["server.output"] == ""
    assert row["server.total"] == ""


def test_zero_by_side_buckets_render_as_zero() -> None:
    # Upstream 0.4.3 always emits both sides, using zeros when a side had no usage
    # (e.g. pure A2A has no server LLM). A present side dict must print 0, not blank.
    metrics = _ok_metrics()
    metrics["tokens"] = {
        "input": 10,
        "cachedInput": 0,
        "output": 5,
        "total": 15,
        "bySide": {
            "client": {"input": 0, "cachedInput": 0, "output": 0, "total": 0},
            "server": {"input": 0, "cachedInput": 0, "output": 0, "total": 0},
        },
    }

    row = build_row("t1", "ok", metrics, [], None)

    assert (row["client.input"], row["client.cachedInput"], row["client.output"], row["client.total"]) == (
        "0",
        "0",
        "0",
        "0",
    )
    assert (row["server.input"], row["server.cachedInput"], row["server.output"], row["server.total"]) == (
        "0",
        "0",
        "0",
        "0",
    )


def test_missing_by_side_block_stays_blank_for_old_traces() -> None:
    # Old traces (before 0.4.3) may have no bySide bucket at all: keep it blank.
    metrics = _ok_metrics()
    metrics["tokens"] = {"input": 10, "cachedInput": 0, "output": 5, "total": 15}

    row = build_row("t1", "ok", metrics, [], None)

    for column in ("client.input", "client.cachedInput", "client.output", "client.total"):
        assert row[column] == ""
    for column in ("server.input", "server.cachedInput", "server.output", "server.total"):
        assert row[column] == ""


def test_by_side_missing_side_stays_blank_and_present_side_defaults_to_zero() -> None:
    metrics = _ok_metrics()
    metrics["tokens"] = {
        "input": 10,
        "cachedInput": 0,
        "output": 5,
        "total": 15,
        "bySide": {"client": {"input": 10}},
    }

    row = build_row("t1", "ok", metrics, [], None)

    assert row["client.input"] == "10"
    assert row["client.cachedInput"] == "0"
    assert row["client.output"] == "0"
    assert row["client.total"] == "0"
    assert row["server.input"] == ""
    assert row["server.output"] == ""


def test_ok_row_joins_local_run_columns() -> None:
    row = build_row(
        "t1",
        "ok",
        _ok_metrics(),
        [],
        {
            "status": "finished",
            "finalState": "task_completed",
            "passed": True,
            "accuracy": 0.5,
            "startedAt": "2026-11-09T10:00:00.000Z",
            "finishedAt": "2026-11-09T10:02:00.000Z",
        },
    )

    assert row["status"] == "finished"
    assert row["finalState"] == "task_completed"
    assert row["passed"] == "true"
    assert row["accuracy"] == "0.5000"
    assert row["startedAt"] == "2026-11-09T10:00:00.000Z"
    assert row["finishedAt"] == "2026-11-09T10:02:00.000Z"


def test_local_run_false_and_string_accuracy_render_as_given() -> None:
    row = build_row(
        "t1",
        "ok",
        _ok_metrics(),
        [],
        {"passed": False, "accuracy": "3/5"},
    )

    assert row["passed"] == "false"
    assert row["accuracy"] == "3/5"

    string_flag = build_row("t1", "ok", _ok_metrics(), [], {"passed": "TRUE"})
    assert string_flag["passed"] == "true"


def test_main_csv_uses_contract_header_and_rfc4180_quoting() -> None:
    warning = "bad, value; still one cell"
    failed = build_row("t1", "error", None, [warning], None)
    sparse = {"traceId": "t2"}

    text = main_csv([failed, sparse])

    assert text.startswith(",".join(MAIN_COLUMNS) + "\r\n")
    assert text.endswith("\r\n")
    assert "\n" not in text.replace("\r\n", "")
    assert '"bad, value; still one cell"' in text

    parsed = list(csv.reader(io.StringIO(text)))
    assert parsed[0] == list(MAIN_COLUMNS)
    assert parsed[1][MAIN_COLUMNS.index("warnings")] == warning
    assert parsed[1][MAIN_COLUMNS.index("exportStatus")] == "error"
    assert len(parsed[2]) == len(MAIN_COLUMNS)
    assert parsed[2][MAIN_COLUMNS.index("traceId")] == "t2"
    assert parsed[2][MAIN_COLUMNS.index("runId")] == ""


def test_main_csv_with_no_rows_is_just_the_header() -> None:
    assert main_csv([]) == ",".join(MAIN_COLUMNS) + "\r\n"


def test_rounds_csv_returns_none_when_no_trace_has_round_detail() -> None:
    assert rounds_csv([]) is None
    assert rounds_csv([("t1", _ok_metrics())]) is None


def test_rounds_csv_expands_every_round_detail_row() -> None:
    metrics = _ok_metrics()
    metrics["negotiation"] = {
        "triggered": True,
        "rounds": 2,
        "latencyMs": 400,
        "serverDurationMs": 120,
        "roundsDetail": [
            {
                "round": 0,
                "proposeAt": "2026-11-09T10:00:01.000Z",
                "notifiedAt": "2026-11-09T10:00:01.000Z",
                "notifiedAtSource": "propose",
                "negotiationStartedAt": "2026-11-09T10:00:02.000Z",
                "negotiationStartedAtSource": "client:negotiation_started",
                "clock": "client",
                "decisionAt": "2026-11-09T10:00:02.200Z",
                "sdkConstructedAt": "2026-11-09T10:00:02.100Z",
                "replySentAt": "2026-11-09T10:00:02.300Z",
                "negotiationType": "target",
                "hop": "oss",
            },
            {
                "round": 1,
                "proposeAt": "2026-11-09T10:00:03.000Z",
                "clock": "server",
            },
        ],
    }
    empty = _ok_metrics()

    text = rounds_csv([("t1", metrics), ("t2", empty)])

    assert text is not None
    assert text.startswith(",".join(ROUNDS_COLUMNS) + "\r\n")
    parsed = list(csv.reader(io.StringIO(text)))
    assert parsed[0] == list(ROUNDS_COLUMNS)
    assert len(parsed) == 3
    first = dict(zip(ROUNDS_COLUMNS, parsed[1], strict=True))
    assert first["traceId"] == "t1"
    assert first["round"] == "0"
    assert first["proposeAt"] == "2026-11-09T10:00:01.000Z"
    assert first["notifiedAtSource"] == "propose"
    assert first["sdkConstructedAt"] == "2026-11-09T10:00:02.100Z"
    assert first["negotiationType"] == "target"
    assert first["hop"] == "oss"
    second = dict(zip(ROUNDS_COLUMNS, parsed[2], strict=True))
    assert second["traceId"] == "t1"
    assert second["round"] == "1"
    assert second["clock"] == "server"
    assert second["decisionAt"] == ""
    assert second["sdkConstructedAt"] == ""
    assert second["replySentAt"] == ""
    assert second["negotiationType"] == ""
    assert second["hop"] == ""


def test_field_notes_cover_every_main_and_rounds_column() -> None:
    covered = {note.column for note in FIELD_NOTES}
    assert set(MAIN_COLUMNS) <= covered
    assert set(ROUNDS_COLUMNS) <= covered


def test_fields_csv_renders_the_header_and_every_note_in_order() -> None:
    parsed = list(csv.reader(io.StringIO(fields_csv())))

    assert parsed[0] == ["column", "group", "meaningZh", "source", "caveat"]
    assert len(parsed) == len(FIELD_NOTES) + 1
    for note, row in zip(FIELD_NOTES, parsed[1:], strict=True):
        assert row == [note.column, note.group, note.meaning_zh, note.source, note.caveat]


def test_field_notes_document_the_d8_token_semantics() -> None:
    notes = {note.column: note for note in FIELD_NOTES}

    assert "包含缓存" in notes["input"].meaning_zh
    assert "README" in notes["input"].caveat
    assert "错误" in notes["input"].caveat
    assert "子集" in notes["cachedInput"].meaning_zh
    assert "input − cachedInput" in notes["inputExcludingCached"].meaning_zh
    assert "通常等于 input + output" in notes["total"].meaning_zh
    assert "gen_ai.usage" in notes["server.input"].source
    assert "去重" in notes["server.input"].source
    assert "clock=server" in notes["negotiationLatencyMs"].caveat
    assert "不计入" in notes["negotiationLatencyMs"].caveat
    assert "statusUpdate" in notes["prepareMs"].caveat
    assert "空值 = 数据缺失，不猜测填充" in notes["input"].caveat


def test_field_notes_document_pure_a2a_rounds_and_zero_bucket_semantics() -> None:
    notes = {note.column: note for note in FIELD_NOTES}

    negotiation_type = notes["negotiationType"]
    assert "information" in negotiation_type.meaning_zh
    assert "target" in negotiation_type.meaning_zh
    assert "feasibility" in negotiation_type.meaning_zh
    assert "unknown" in negotiation_type.meaning_zh
    assert "metadata.pure.negotiationKind" in negotiation_type.source
    assert "phase" in negotiation_type.source
    assert "留空" in negotiation_type.caveat  # missing -> blank

    hop = notes["hop"]
    assert "OSS" in hop.meaning_zh
    assert "content" in hop.caveat

    assert "0.4.3" in notes["client.input"].caveat
    assert "0" in notes["client.input"].caveat
    assert "旧 trace" in notes["client.input"].caveat
    assert "0.4.3" in notes["server.input"].caveat
    assert "0" in notes["server.input"].caveat
    assert "旧 trace" in notes["server.input"].caveat


def test_end_to_end_uses_metrics_latency_instead_of_recomputing() -> None:
    metrics = _ok_metrics()
    metrics["latency"] = {"endToEndMs": 7777}

    row = build_row("t1", "ok", metrics, [], None)

    assert row["endToEndMs"] == "7777"
    assert row["prepareMs"] == "250"
