"""Contract tests of the generated OSP evaluation cases.

The 100 YAML files are generated from an internal workbook export
(``docs-local/cases-all.yaml``, not committed); this test protects the committed contract
(counts, filenames, schema keys and the deliberate assertion switches) without depending on the
internal source.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
import yaml
from eval.osp.expectations import negotiation_message_expectation, slot_error_expectation

OSP_DIR = Path(__file__).resolve().parents[1] / "eval" / "osp"
CASES_DIR = OSP_DIR / "cases"
INDEX_PATH = OSP_DIR / "index.yaml"

INTENT_KEYS = (
    "Operation type",
    "Energy saving cell RAT",
    "Energy saving aggressiveness",
    "Energy saving region",
    "Whether Experience-Preserving Energy Saving is Required",
    "Special Requirements for Carrier Shutdown Frequencies",
    "Energy saving time",
    "Frequencies of carriers that can be shut down",
    "Gain target",
    "Guaranteed throughput target by zone",
    "Scenario-based energy saving",
)

TASK_T_SECTIONS = (
    "## Operation Type",
    "## Task Type",
    "## Task Description",
    "## Task Object",
    "## Task Target",
    "## Task Context",
    "## Terminology Explanation",
    "## Expected Output",
)

FILENAME_PATTERN = re.compile(
    r"^(?P<id>\d{2,3})-(?P<name>.+)-\[(?P<validate>拦截|不拦截\+术语|不拦截|未填写)\]-"
    r"\[(?P<operation>Create|Modify)\]\.yaml$"
)

#: Cases whose A2A-T request is expected to open a negotiation.
A2AT_NEGOTIATION_IDS = frozenset({26, 28, 29, 30, 31, 32, 50, 51, 88, 89})

#: Cases the pure-A2A path is expected to complete without a negotiation.
A2A_NO_NEGOTIATION_IDS = frozenset({45, 46, 47, 48, 49, 53, 54, 55, 56, 57, 69})

DIMENSIONS = frozenset(
    {
        "D1 意图完备且清晰",
        "D2 专业术语/行业黑话",
        "D3 意图模糊",
        "D4 意图不完备",
        "D5 意图存在噪声",
        "D6 意图存在值越界",
        "D7 混合",
    }
)

SOURCE_FILE = "OSP-Unified Evaluation Set_v.01_拆分_A2ATestRegion.xlsx"

VALIDATE_LABELS: dict[str | None, str] = {
    "blocked": "拦截",
    "pass": "不拦截",
    "pass_with_terminology": "不拦截，携带术语上下文信息",
    None: "未填写",
}

FILENAME_VALIDATE_LABELS: dict[str | None, str] = {
    "blocked": "拦截",
    "pass": "不拦截",
    "pass_with_terminology": "不拦截+术语",
    None: "未填写",
}


def _load(path: Path) -> dict[str, Any]:
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(document, dict), f"{path} is not a YAML mapping"
    return document


@pytest.fixture(scope="module")
def cases() -> dict[int, dict[str, Any]]:
    """Load every case once, keyed by id."""
    result: dict[int, dict[str, Any]] = {}
    for path in sorted(CASES_DIR.glob("*.yaml")):
        document = _load(path)
        case_id = document["id"]
        assert isinstance(case_id, int)
        assert case_id not in result, f"duplicate id {case_id}"
        document["_path"] = path
        result[case_id] = document
    return result


def test_100_cases_with_ids_1_to_100(cases: dict[int, dict[str, Any]]) -> None:
    assert sorted(cases) == list(range(1, 101))


def test_filenames_follow_the_contract(cases: dict[int, dict[str, Any]]) -> None:
    seen: set[str] = set()
    for case_id, document in cases.items():
        path: Path = document["_path"]
        match = FILENAME_PATTERN.match(path.name)
        assert match is not None, path.name
        assert int(match["id"]) == case_id
        assert match["operation"] == document["operation"]
        assert match["validate"] == FILENAME_VALIDATE_LABELS[
            document["expect"]["protocols"]["a2a-t"]["validate"]
        ]
        assert path.name not in seen
        seen.add(path.name)


def test_intent_has_the_eleven_keys(cases: dict[int, dict[str, Any]]) -> None:
    for case_id, document in cases.items():
        intent = document["expect"]["intent"]
        assert list(intent) == list(INTENT_KEYS), case_id
        assert document["operation"] == intent["Operation type"]
        assert document["operation"] in {"Create", "Modify"}


def test_task_t_carries_all_eight_sections(cases: dict[int, dict[str, Any]]) -> None:
    for case_id, document in cases.items():
        task_t = document["expect"]["protocols"]["a2a-t"]["task_t"]
        for section in TASK_T_SECTIONS:
            assert section in task_t, f"case {case_id}: missing {section}"


def test_validate_maps_to_send_and_final_state(cases: dict[int, dict[str, Any]]) -> None:
    for case_id, document in cases.items():
        protocol = document["expect"]["protocols"]["a2a-t"]
        validate = protocol["validate"]
        assert validate in VALIDATE_LABELS, case_id
        assert protocol["validate_label"] == VALIDATE_LABELS[validate]
        if validate == "blocked":
            assert protocol["send"] is False
            assert protocol["final_state"] == "input_invalid"
        elif validate in {"pass", "pass_with_terminology"}:
            assert protocol["send"] is True
            assert protocol["final_state"] == "task_completed"
        else:
            assert protocol["send"] is None
            assert protocol["final_state"] is None
        assert protocol["final_state_derived"] is (validate is not None)


def test_a2a_negotiation_matches_its_rounds(cases: dict[int, dict[str, Any]]) -> None:
    """``expected`` is a per-case fact; the round count must agree with the recorded rounds."""
    for case_id, document in cases.items():
        protocol = document["expect"]["protocols"]["a2a"]
        negotiation = protocol["negotiation"]
        assert negotiation["expected"] is (case_id not in A2A_NO_NEGOTIATION_IDS), case_id
        assert negotiation["expected_rounds"] == len(negotiation["rounds"])
        if negotiation["expected"]:
            assert negotiation["expected_rounds"] >= 1, case_id
        else:
            assert negotiation["expected_rounds"] == 0, case_id
        assert protocol["final_state"] is None
        assert document["assert"]["a2a"]["final_state"] is False


def test_a2a_t_negotiation_only_for_the_pinned_cases(cases: dict[int, dict[str, Any]]) -> None:
    for case_id, document in cases.items():
        negotiation = document["expect"]["protocols"]["a2a-t"]["negotiation"]
        expected = case_id in A2AT_NEGOTIATION_IDS
        assert negotiation["expected"] is expected, case_id
        assert negotiation["expected_rounds"] == (1 if expected else 0)
        assert len(negotiation["rounds"]) == (1 if expected else 0)
        if expected:
            assert negotiation["kinds"]
        else:
            assert not negotiation["kinds"]


def test_the_optional_expectations_parse(cases: dict[int, dict[str, Any]]) -> None:
    """A case may state the expected slot errors and the expected negotiation messages.

    Both blocks are optional, so this test pins their shape rather than their presence: a
    malformed block must fail here instead of at run time, and the slot-error block may only
    describe an intercepted request.
    """
    for case_id, document in cases.items():
        protocol = document["expect"]["protocols"]["a2a-t"]
        slot_errors = protocol.get("slot_errors")
        if slot_errors is not None:
            assert protocol["validate"] == "blocked", case_id
        slot_error_expectation(document)
        for name in ("a2a-t", "a2a"):
            expectation = negotiation_message_expectation(document, name)
            if expectation is not None:
                assert expectation.rounds, case_id


def test_a_case_without_the_optional_expectations_keeps_the_verdict_only(cases: dict[int, dict[str, Any]]) -> None:
    """The blocks stay optional: most cases still judge the verdict and the round count alone."""
    assert any(
        "slot_errors" not in case["expect"]["protocols"]["a2a-t"] for case in cases.values()
    )
    assert any(
        "messages" not in case["expect"]["protocols"]["a2a-t"].get("negotiation", {})
        for case in cases.values()
    )


def test_assert_switches(cases: dict[int, dict[str, Any]]) -> None:
    """The corpus judges intent, Task-T, validation, send, negotiation and final state."""
    for case_id, document in cases.items():
        switches = document["assert"]
        assert switches["intent"] is True, case_id
        assert switches["a2a-t"] == {
            "validate": True,
            "task_t": True,
            "send": True,
            "negotiation": True,
            "final_state": True,
        }, case_id
        assert switches["a2a"] == {"negotiation": True, "final_state": False}, case_id


def test_index_matches_cases(cases: dict[int, dict[str, Any]]) -> None:
    index = _load(INDEX_PATH)
    assert index["schema"] == "osp-eval-index/v1"
    assert index["source"]["file"] == SOURCE_FILE
    assert index["count"] == 100
    entries = index["cases"]
    assert len(entries) == 100
    assert sorted(entry["id"] for entry in entries) == list(range(1, 101))
    for entry in entries:
        document = cases[entry["id"]]
        protocols = document["expect"]["protocols"]
        assert entry["file"] == f"cases/{document['_path'].name}"
        assert entry["operation"] == document["operation"]
        assert entry["validate"] == protocols["a2a-t"]["validate"]
        assert entry["tags"] == document["tags"]
        assert entry["source_row"] == document["source"]["row"]
        assert entry["negotiation"] == {
            "a2a-t": protocols["a2a-t"]["negotiation"]["expected_rounds"] > 0,
            "a2a": protocols["a2a"]["negotiation"]["expected_rounds"] > 0,
        }
        assert entry["blocked_followup"] == ("blocked_followup" in document)


def test_top_level_contract(cases: dict[int, dict[str, Any]]) -> None:
    for case_id, document in cases.items():
        assert document["schema"] == "osp-eval-case/v1"
        assert document["id"] == case_id
        assert document["dimension"] in DIMENSIONS, case_id
        assert document["user_input"].strip()
        assert document["source"]["file"] == SOURCE_FILE
        assert document["source"]["sheet"] == "A2A-T_原始数据"
        row = document["source"]["row"]
        assert isinstance(row, int) and row >= 2
        runner = document["runner"]
        assert runner["scenario"] == "night"
        assert runner["language"] == "en-US"
        assert runner["approval"] == "approve"
        assert runner["timeout_seconds"] == 300
        issue = document["known_issue"]
        assert issue is None or (isinstance(issue, str) and issue.strip())
