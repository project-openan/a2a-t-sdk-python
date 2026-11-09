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


#: The manual baseline Task-T the testers use: a complete, non-negotiating Create.
BASELINE_SCHEDULE = (
    "guarantee 2Mbps from 00:00 to 07:00, 10Mbps from 07:00 to 18:00 "
    "and 20Mbps from 18:00 to 24:00"
)


def test_every_modify_case_pairs_a_normal_create_baseline(
    cases: dict[int, dict[str, Any]],
) -> None:
    """A Modify continues a Task that must already exist.

    The corpus does not say where that Task came from, so each Modify row states its paired Create
    explicitly. The baseline is the Create Task-T the testers use manually: a complete plan the
    vendor accepts without a pre-task negotiation. The region must be written as ``A2ATestRegion``;
    any other region either fails validation or opens an information negotiation.

    A per-site high-rate day plan is not used. Widening Create 8/9's 10Mbps guarantee to the whole
    day makes the baseline itself infeasible: the vendor answers a high rate over a long window
    with a feasibility negotiation, so no continuable Task is created and the Modify never runs.
    """
    for case_id, document in cases.items():
        if document["operation"] != "Modify":
            assert "baseline" not in document, case_id
            continue
        baseline = document.get("baseline")
        assert isinstance(baseline, dict), case_id
        request = baseline.get("request")
        assert isinstance(request, str) and request.strip(), case_id
        assert request.startswith("Create an energy saving task for A2ATestRegion"), case_id
        assert BASELINE_SCHEDULE in request, case_id
        assert "Modify" not in request, case_id
        # Prose only: the scripted provider rewrites an explicit window list into the Task Context
        # and content validation then rejects the baseline.
        assert "{" not in request and "}" not in request, case_id
        # The region is part of the request, never inherited from the environment.
        assert "Region A" not in request and "Region Z" not in request, case_id
        lowered = request.lower()
        for marker in ("outage", "停电", "background event"):
            assert marker not in lowered, f"{case_id}: baseline carries the new event"


def test_every_modify_baseline_passes_the_sdk_pipeline(
    cases: dict[int, dict[str, Any]],
) -> None:
    """The baseline is a real Create: the SDK must generate and validate its Task-T.

    The baseline runs on the ``night`` scenario, so its canned Task-T is the default plan; the
    test keeps every baseline request on the path that renders and validates that plan.
    """
    from eval.osp.runner import build_client
    from protocol.messages import TASK_TEMPLATE_URI
    from shared.task_schema import TASK_PARAM_SCHEMA

    client = build_client(model_mode="offline")
    for case_id, document in cases.items():
        if document["operation"] != "Modify":
            continue
        request = document["baseline"]["request"]
        generated = client.generate_task_prompt_from_text(request, TASK_TEMPLATE_URI)
        text = getattr(generated, "prompt_text", "")
        assert isinstance(text, str) and text.strip(), case_id
        client.validate_task_prompt_and_data_filling(text, TASK_PARAM_SCHEMA, TASK_TEMPLATE_URI)


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
        assert entry["tags"] == document["tags"]


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
