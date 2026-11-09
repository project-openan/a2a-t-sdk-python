"""Contract tests of the generated OSP evaluation cases.

The 51 YAML files are generated from an internal workbook export; this test protects the
committed contract (counts, filenames, schema keys and the deliberate assertion switches)
without depending on the internal source JSON.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
import yaml

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
    r"^(?P<id>\d{2})-(?P<name>.+)-\[(?P<validate>拦截|不拦截\+术语|不拦截|未填写)\]-"
    r"\[(?P<operation>Create|Modify)\]\.yaml$"
)

A2AT_NEGOTIATION_IDS = frozenset({32, 34, 35, 36, 37, 38})
PARTIAL_IDS = frozenset({40, 46})
ISSUE_IDS = frozenset({20, 21, 28, 34, 40, 46})

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


def test_51_cases_with_ids_1_to_51(cases: dict[int, dict[str, Any]]) -> None:
    assert sorted(cases) == list(range(1, 52))


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


def test_a2a_negotiation_is_expected_for_every_case(cases: dict[int, dict[str, Any]]) -> None:
    for case_id, document in cases.items():
        protocol = document["expect"]["protocols"]["a2a"]
        negotiation = protocol["negotiation"]
        assert negotiation["expected"] is True, case_id
        assert negotiation["kinds"] is None
        assert negotiation["expected_rounds"] == len(negotiation["rounds"])
        assert negotiation["expected_rounds"] >= 1
        assert protocol["final_state"] is None
        assert document["assert"]["a2a"]["final_state"] is False


def test_a2a_t_negotiation_only_for_the_six_cases(cases: dict[int, dict[str, Any]]) -> None:
    for case_id, document in cases.items():
        negotiation = document["expect"]["protocols"]["a2a-t"]["negotiation"]
        expected = case_id in A2AT_NEGOTIATION_IDS
        assert negotiation["expected"] is expected, case_id
        assert negotiation["expected_rounds"] == (1 if expected else 0)
        assert len(negotiation["rounds"]) == (1 if expected else 0)
        if expected:
            assert negotiation["kinds"]
        else:
            assert negotiation["kinds"] == []


def test_conflict_assert_switches(cases: dict[int, dict[str, Any]]) -> None:
    for case_id in PARTIAL_IDS:
        document = cases[case_id]
        assert isinstance(document["known_issue"], str) and document["known_issue"].strip()
        assert "needs-confirmation" in document["tags"]
    for case_id in (40, 46):
        assert cases[case_id]["assert"]["intent"] is False
        assert cases[case_id]["assert"]["a2a-t"]["task_t"] is False
        assert cases[case_id]["assert"]["a2a-t"]["validate"] is True
    for case_id in (49, 50, 51):
        # Promoted to fully judged cases in the 2026-11-09 source table.
        protocol = cases[case_id]["expect"]["protocols"]["a2a-t"]
        assert protocol["validate"] == "pass_with_terminology"
        assert cases[case_id]["known_issue"] is None
        assert "needs-confirmation" not in cases[case_id]["tags"]
        assert cases[case_id]["assert"]["a2a-t"]["validate"] is True
        assert cases[case_id]["assert"]["a2a-t"]["send"] is True
        assert cases[case_id]["assert"]["a2a-t"]["final_state"] is True
    for case_id in ISSUE_IDS:
        assert cases[case_id]["known_issue"], case_id


def test_index_matches_cases(cases: dict[int, dict[str, Any]]) -> None:
    index = _load(INDEX_PATH)
    assert index["schema"] == "osp-eval-index/v1"
    assert index["count"] == 51
    entries = index["cases"]
    assert len(entries) == 51
    assert sorted(entry["id"] for entry in entries) == list(range(1, 52))
    for entry in entries:
        document = cases[entry["id"]]
        assert entry["file"] == document["_path"].name
        assert entry["operation"] == document["operation"]
        assert entry["validate"] == document["expect"]["protocols"]["a2a-t"]["validate"]


def test_top_level_contract(cases: dict[int, dict[str, Any]]) -> None:
    for case_id, document in cases.items():
        assert document["schema"] == "osp-eval-case/v1"
        assert document["id"] == case_id
        assert document["dimension"] == "D1 意图完备无歧义"
        assert document["user_input"].strip()
        assert document["assert"]["intent"] is (case_id not in {40, 46})
        assert document["source"]["file"] == "OSP-Unified Evaluation Set_v.01.xlsx"
        assert document["source"]["sheet"] == "A2A-T_原始数据"
        assert document["source"]["row"] == case_id + 1
        runner = document["runner"]
        assert runner["scenario"] == "night"
        assert runner["language"] == "en-US"
        assert runner["approval"] == "approve"
        assert runner["timeout_seconds"] == 300
        assert runner["pre_send_validation"] == "always"
        issue = document["known_issue"]
        assert issue is None or (isinstance(issue, str) and issue.strip())
