"""Generality tests of the declarative ``x-a2at-checks`` rules in the sample overlay.

The rules block two kinds of incomplete input: a site reference with no concrete name, and an
outage request that states no guaranteed rate. Both are phrased broadly on purpose: an operator
who paraphrases ("some other sites", "etc.", "several sites") must still be blocked, and a
concrete list must never be.

The evaluator below mirrors the SDK contract documented on
``a2a_t.client.prompt_generation.prompt_generation_orchestrator._declarative_check_errors``:
``when`` is searched in the candidate(s), ``require`` must appear in the same candidate, and
``source`` selects the extracted slot, the operator text, or both.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

_OVERLAY = (
    Path(__file__).resolve().parents[1]
    / "prompt_resources/slots/Task-T/network-layer/ran-energy-saving/v1"
)


def _checks(language: str) -> list[Mapping[str, Any]]:
    data = json.loads((_OVERLAY / language / "slot.json").read_text(encoding="utf-8"))
    return list(data["x-a2at-checks"])


def _site_check(language: str) -> Mapping[str, Any]:
    checks = _checks(language)
    return next(
        check
        for check in checks
        if check.get("source") == "input" and "handful" in str(check.get("when", ""))
    )


def _time_check(language: str) -> Mapping[str, Any]:
    checks = _checks(language)
    return next(
        check
        for check in checks
        if check.get("source") == "input" and "busy" in str(check.get("when", ""))
    )


def _rate_check(language: str) -> Mapping[str, Any]:
    checks = _checks(language)
    return next(
        check
        for check in checks
        if check.get("source") == "slot" and "outage" in str(check.get("when", ""))
    )


def _violates(check: Mapping[str, Any], user_input: str, slot_value: str) -> bool:
    source = check.get("source")
    if source == "input":
        candidates = [user_input]
    elif source == "slot":
        candidates = [slot_value]
    else:
        candidates = [slot_value, user_input]
    when = str(check.get("when"))
    require = check.get("require")
    for candidate in candidates:
        if not candidate or not candidate.strip():
            continue
        if re.search(when, candidate, re.IGNORECASE) is None:
            continue
        if isinstance(require, str) and re.search(require, candidate, re.IGNORECASE) is not None:
            continue
        return True
    return False


@pytest.mark.parametrize(
    "phrase",
    [
        "LTE_26441_LNR_OCL, NRES_XCS_6102937, and a few others",
        "LTE_26441_LNR_OCL and a couple of others",
        "LTE_26441_LNR_OCL and some other sites",
        "LTE_26441_LNR_OCL and several sites",
        "LTE_26441_LNR_OCL and others",
        "LTE_26441_LNR_OCL, NRES_XCS_6102937, etc.",
        "LTE_26441_LNR_OCL 及其他站点",
        "LTE_26441_LNR_OCL 及少数其他站点",
        "LTE_26441_LNR_OCL 等其他站点",
        "LTE_26441_LNR_OCL、NRES_XCS_6102937、其余站点",
    ],
)
def test_a_site_reference_without_a_concrete_name_is_blocked(phrase: str) -> None:
    check = _site_check("en-US")

    assert _violates(check, f"Modify the energy saving task of {phrase} in A2ATestRegion.", "")


@pytest.mark.parametrize(
    "phrase",
    [
        "LTE_26441_LNR_OCL, NRES_XCS_6102937, and LNR_KPI_7745",
        "the two sites LTE_26441_LNR_OCL and NRES_XCS_6102937",
        "sites LTE_26441_LNR_OCL and NRES_XCS_6102937",
        "site 1 and site 2",
    ],
)
def test_a_concrete_site_list_is_not_blocked(phrase: str) -> None:
    check = _site_check("en-US")

    assert not _violates(check, f"Modify the energy saving task of {phrase} in A2ATestRegion.", "")


@pytest.mark.parametrize(
    "slot_value",
    [
        "Background event: power outage from 08:00 to 18:00.",
        "停电 08:00-18:00，保障速率目标待定。",
        "Power outage 00:00-05:00, keep the rest unchanged.",
    ],
)
def test_an_outage_without_a_rate_value_is_blocked(slot_value: str) -> None:
    check = _rate_check("en-US")

    assert _violates(check, "Modify the guaranteed throughput target during the outage.", slot_value)


@pytest.mark.parametrize(
    "slot_value",
    [
        "Site list: [\"LTE_A\", \"NRES_B\"], Time-segmented rate guarantee target: "
        "{08:00-18:00, 10Mbps}, Background event: power outage from 08:00 to 18:00.",
        "Background event: power outage from 00:00 to 05:00; Rate guarantee target: 2Mbps.",
        "Time-segmented rate guarantee goal: {00:00~22:00,2Mbps}.",
    ],
)
def test_an_outage_with_a_rate_value_is_not_blocked(slot_value: str) -> None:
    check = _rate_check("en-US")

    assert not _violates(check, "Modify the guaranteed throughput target.", slot_value)


def test_the_chinese_overlay_carries_the_same_two_rules() -> None:
    site = _site_check("zh-CN")
    rate = _rate_check("zh-CN")

    assert _violates(site, "修改 LTE_A 及其他站点的节能任务。", "")
    assert not _violates(site, "修改 LTE_A 和 NRES_B 的节能任务。", "")
    assert _violates(rate, "在停电期间修改保障吞吐目标。", "停电 08:00-18:00。")
    assert not _violates(rate, "在停电期间修改保障吞吐目标。", "停电 08:00-18:00，保障速率 10Mbps。")


@pytest.mark.parametrize(
    "user_input",
    [
        "Set the guaranteed throughput as follows: idle period: 2M, regular period: 10Mbps, "
        "Busy period: 20Mbps.",
        "Guarantee 10Mbps during busy hours and 2Mbps during off-peak.",
        "在忙时保障 10Mbps，闲时保障 2Mbps。",
        "高峰时段保障 20Mbps，低谷时段保障 2Mbps。",
    ],
)
def test_a_time_label_without_explicit_hours_is_blocked(user_input: str) -> None:
    check = _time_check("en-US")

    assert _violates(check, user_input, "")


@pytest.mark.parametrize(
    "user_input",
    [
        "Set the guaranteed throughput as follows: 07:00~18:00 -> 10Mbps, 18:00~23:00 -> 20Mbps.",
        "During busy hours 08:00-12:00 guarantee 10Mbps.",
        "忙时 08:00-12:00 保障 10Mbps。",
    ],
)
def test_a_time_label_with_explicit_hours_is_not_blocked(user_input: str) -> None:
    check = _time_check("en-US")

    assert not _violates(check, user_input, "")
