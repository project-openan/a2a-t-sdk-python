"""Tests of the structured Task payload a downstream returns on submission."""

from __future__ import annotations

import json

from shared.server_intent import parse_server_intent

#: The create payload the vendor NMS returns after ``Task creation succeeded``.
_CREATE = {
    "Operation type": "Create",
    "Energy saving region": "A2ATestRegion",
    "Guaranteed throughput target by zone": [
        {"Guaranteed throughput target": "10Mbps", "Effective time period": "00:00:00-00:59:59"}
    ],
    "Energy saving time": "",
    "Gain target": "",
    "Energy saving cell RAT": "ALL",
    "Frequencies of carriers that can be shut down": [],
    "Scenario-based energy saving": [],
    "Energy saving aggressiveness": "",
    "Whether Experience-Preserving Energy Saving is Required": "",
    "Special Requirements for Carrier Shutdown Frequencies": "",
}


def test_the_create_payload_after_the_localized_copy_is_parsed() -> None:
    text = f"Task creation succeeded{json.dumps(_CREATE, ensure_ascii=False, separators=(',', ':'))}"

    assert parse_server_intent(text) == _CREATE


def test_the_modify_payload_is_parsed_the_same_way() -> None:
    payload = {"Operation type": "Modify", "Energy saving region": "A2ATestRegion"}
    text = f"Task modification succeeded {json.dumps(payload, separators=(',', ':'))}"

    assert parse_server_intent(text) == payload


def test_a_brace_in_the_prose_before_the_payload_does_not_stop_the_scan() -> None:
    payload = {"Operation type": "Create", "Energy saving region": "A2ATestRegion"}
    text = f"Task creation succeeded for {{A2ATestRegion}}{json.dumps(payload, separators=(',', ':'))}"

    assert parse_server_intent(text) == payload


def test_text_without_a_payload_is_ignored() -> None:
    assert parse_server_intent("Task creation succeeded") is None
    assert parse_server_intent("") is None
    assert parse_server_intent(None) is None
    assert parse_server_intent({"Operation type": "Create"}) is None


def test_a_json_document_that_is_not_a_task_payload_is_ignored() -> None:
    assert parse_server_intent("TASK_STATE_SUBMITTED {not json}") is None
    assert parse_server_intent('TASK_STATE_SUBMITTED {"error": "boom"}') is None
    assert parse_server_intent('TASK_STATE_SUBMITTED ["Create"]') is None
