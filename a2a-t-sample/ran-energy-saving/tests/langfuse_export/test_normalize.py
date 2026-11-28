from datetime import timezone
from decimal import Decimal

from langfuse_export.normalize import (
    build_tree,
    derive_summary,
    derive_usage,
    detect_metadata_truncation,
    extract_media_refs,
    normalize_scores,
)

TRACE = "9f2c1a7de4b34c0e8a5f2d1c6b9e3a70"


def make_row(**kwargs):
    base = {
        "id": "obs_1",
        "traceId": TRACE,
        "parentObservationId": None,
        "type": "SPAN",
        "name": "root",
        "startTime": "2026-11-06T22:14:02.100Z",
        "endTime": "2026-11-06T22:14:02.110Z",
        "latency": 0.01,
        "level": "DEFAULT",
        "usageDetails": {},
        "metadata": {},
    }
    base.update(kwargs)
    return base


def test_usage_prefers_top_level_fields():
    usage = derive_usage(
        {"inputUsage": 7, "outputUsage": 3, "totalUsage": 10, "usageDetails": {"input": 1, "output": 1, "total": 2}}
    )
    assert (usage.input, usage.output, usage.total) == (7, 3, 10)


def test_usage_falls_back_to_details():
    usage = derive_usage({"usageDetails": {"input": 5, "output": 2}})
    assert (usage.input, usage.output, usage.total) == (5, 2, 7)


def test_usage_total_prefers_input_plus_output():
    # real API rows occasionally report total != input + output
    usage = derive_usage({"inputUsage": 8810, "outputUsage": 233, "totalUsage": 5843})
    assert usage.total == 9043


def test_usage_cost_is_decimal():
    usage = derive_usage({"totalCost": 0.0041, "costDetails": {"input": 0.0024}})
    assert usage.cost_total == Decimal("0.0041")
    assert usage.cost_details["input"] == Decimal("0.0024")


def test_orphans_become_roots_and_warn():
    rows = [
        make_row(id="a", parentObservationId="missing"),
        make_row(id="b", parentObservationId="also-missing"),
    ]
    roots, warnings = build_tree(rows, TRACE)
    assert len(roots) == 2
    assert any("multiple physical roots" in w for w in warnings)


def test_app_root_with_physical_parent_warns():
    rows = [
        make_row(id="root"),
        make_row(id="child", parentObservationId="root", isRootObservation=True),
    ]
    roots, warnings = build_tree(rows, TRACE)
    assert len(roots) == 1
    assert any("app root has physical parent" in w for w in warnings)


def test_shell_row_is_skipped():
    rows = [make_row(id=f"t-{TRACE}", type="SPAN", name="")]
    roots, warnings = build_tree(rows, TRACE)
    assert roots == []
    assert any("shell row" in w for w in warnings)


def test_conflicting_trace_name_warns():
    rows = [
        make_row(id="a", traceName="one", startTime="2026-11-06T22:14:00.000Z"),
        make_row(id="b", traceName="two", startTime="2026-11-06T22:14:01.000Z"),
    ]
    roots, _ = build_tree(rows, TRACE)
    summary, warnings = derive_summary(roots, roots, 0, TRACE)
    assert summary.name == "one"
    assert any("conflicting trace traceName" in w for w in warnings)


def test_trace_name_falls_back_to_root():
    rows = [make_row(id="a", name="my-root")]
    roots, _ = build_tree(rows, TRACE)
    summary, _ = derive_summary(roots, roots, 0, TRACE)
    assert summary.name == "my-root"


def test_missing_cost_warns():
    rows = [make_row(id="a")]
    roots, _ = build_tree(rows, TRACE)
    summary, warnings = derive_summary(roots, roots, 0, TRACE)
    assert summary.total_cost is None
    assert any("no cost data" in w for w in warnings)


def test_latency_derived_from_times_when_missing():
    rows = [make_row(id="a", latency=None)]
    roots, _ = build_tree(rows, TRACE)
    summary, _ = derive_summary(roots, roots, 0, TRACE)
    assert summary.latency_ms == 10


def test_extract_media_refs_nested():
    token = "@@@langfuseMedia:type=image/png|id=med_abc|source=base64_data_uri@@@"
    refs = extract_media_refs("metadata", {"a": [{"b": token}]})
    assert len(refs) == 1
    assert refs[0].media_id == "med_abc"
    assert refs[0].mime_type == "image/png"


def test_metadata_truncation_detection():
    # Only a 200/203-char string ending in "..." is treated as truncated.
    assert detect_metadata_truncation([{"metadata": {"x": "y" * 197 + "..."}}]) is True
    assert detect_metadata_truncation([{"metadata": {"x": "y" * 200 + "..."}}]) is True
    assert detect_metadata_truncation([{"metadata": {"x": "y" * 200}}]) is False
    assert detect_metadata_truncation([{"metadata": {"x": "y" * 4214}}]) is False


def test_scores_normalization_subjects():
    scores = normalize_scores(
        [
            {"id": "s1", "name": "a", "value": 1, "traceId": TRACE},
            {"id": "s2", "name": "b", "value": True, "observationId": "obs_1"},
            {"id": "s3", "name": "c", "value": "x", "subject": {"kind": "session", "id": "sess"}},
        ]
    )
    kinds = [score.subject_kind for score in scores]
    assert kinds == ["trace", "observation", "session"]
