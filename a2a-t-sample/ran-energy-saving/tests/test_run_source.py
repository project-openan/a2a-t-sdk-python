"""Tests of the normalized request-origin document."""

from __future__ import annotations

from shared.run_source import RUN_SOURCE_KINDS, normalize_run_source


def test_a_known_kind_and_timestamp_are_kept() -> None:
    source = normalize_run_source(
        {
            "kind": "tester",
            "channel": "tester-console",
            "caseId": "TC-001",
            "empty": "",
            "missing": None,
        }
    )

    assert source["kind"] == "tester"
    assert source["channel"] == "tester-console"
    assert source["caseId"] == "TC-001"
    assert "empty" not in source
    assert "missing" not in source
    assert "receivedAt" in source


def test_an_unknown_kind_falls_back_to_api() -> None:
    assert normalize_run_source({"kind": "made-up"})["kind"] == "api"


def test_no_source_defaults_to_api() -> None:
    source = normalize_run_source(None)

    assert source["kind"] == "api"
    assert source["receivedAt"]


def test_every_declared_kind_is_accepted() -> None:
    for kind in RUN_SOURCE_KINDS:
        assert normalize_run_source({"kind": kind})["kind"] == kind
