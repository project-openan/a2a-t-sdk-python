from __future__ import annotations

import argparse
import json
from collections import Counter
from copy import deepcopy
from pathlib import Path
from typing import Any

import generate_server_prompt_validation_eval_assets_legacy as legacy


PROJECT_ROOT = Path(__file__).resolve().parents[1]
EVAL_ROOT = PROJECT_ROOT / "package_data" / "eval"
DOC_ROOT = PROJECT_ROOT / "docs" / "eval"

SUPPORTED_SCENARIOS = legacy.SUPPORTED_SCENARIOS

CASE_TYPE_ORDER = (
    "positive_complete",
    "positive_partial",
    "negative_target_outside",
    "negative_other_supported_scenario",
    "negative_invalid_value",
)
CASE_ID_PREFIX = {
    "positive_complete": "pc",
    "positive_partial": "pp",
    "negative_target_outside": "nt",
    "negative_other_supported_scenario": "no",
    "negative_invalid_value": "nv",
}
CASE_TYPE_LABELS = {
    "positive_complete": "正样本-信息完整",
    "positive_partial": "正样本-信息缺失",
    "negative_target_outside": "负样本-目标场景外",
    "negative_other_supported_scenario": "负样本-列表内其他场景",
    "negative_invalid_value": "负样本-槽位值非法",
}
DEFAULT_CASE_TYPE_COUNTS = {
    "energy_saving": 20,
    "subscribe_incident": 20,
    "fault_diagnosis": 150,
}

DEFAULT_CASE_TYPE_RATIOS = {
    "positive_complete": 0.25,
    "positive_partial": 0.25,
    "negative_target_outside": 0.20,
    "negative_other_supported_scenario": 0.10,
    "negative_invalid_value": 0.20,
}


def allocate_default_case_type_counts(total: int) -> dict[str, int]:
    raw_counts = {case_type: total * DEFAULT_CASE_TYPE_RATIOS[case_type] for case_type in CASE_TYPE_ORDER}
    resolved = {case_type: int(raw_counts[case_type]) for case_type in CASE_TYPE_ORDER}
    remainder = total - sum(resolved.values())
    if remainder > 0:
        ranked_case_types = sorted(
            CASE_TYPE_ORDER,
            key=lambda case_type: (raw_counts[case_type] - resolved[case_type], -CASE_TYPE_ORDER.index(case_type)),
            reverse=True,
        )
        for case_type in ranked_case_types[:remainder]:
            resolved[case_type] += 1
    return resolved


def resolve_case_type_counts(scenario_code: str, args: argparse.Namespace) -> dict[str, int]:
    defaults = allocate_default_case_type_counts(DEFAULT_CASE_TYPE_COUNTS[scenario_code])
    values = {
        "positive_complete": args.positive_complete_count,
        "positive_partial": args.positive_partial_count,
        "negative_target_outside": args.negative_target_outside_count,
        "negative_other_supported_scenario": args.negative_other_supported_scenario_count,
        "negative_invalid_value": args.negative_invalid_value_count,
    }
    resolved: dict[str, int] = {}
    for case_type in CASE_TYPE_ORDER:
        value = values[case_type]
        resolved[case_type] = defaults[case_type] if value is None else value
        if resolved[case_type] < 0:
            raise ValueError(f"{case_type} count must be non-negative.")
    return resolved


def build_legacy_case_pool(
    scenario_code: str,
    *,
    positive_complete: int = 0,
    positive_partial: int = 0,
    negative_unrecognized_prompt: int = 0,
    negative_invalid_value: int = 0,
    negative_cross_scenario_pollution: int = 0,
) -> list[dict[str, object]]:
    return legacy.build_cases(
        scenario_code,
        {
            "positive_complete": positive_complete,
            "positive_partial": positive_partial,
            "negative_unrecognized_prompt": negative_unrecognized_prompt,
            "negative_invalid_value": negative_invalid_value,
            "negative_cross_scenario_pollution": negative_cross_scenario_pollution,
        },
    )


def eval_case_id(scenario_code: str, case_type: str, index: int) -> str:
    scenario_prefix = {
        "energy_saving": "es",
        "subscribe_incident": "si",
        "fault_diagnosis": "fd",
    }[scenario_code]
    return f"{scenario_prefix}_{CASE_ID_PREFIX[case_type]}_{index:03d}"


def distribute_evenly(total: int, bucket_count: int) -> list[int]:
    base = total // bucket_count
    remainder = total % bucket_count
    return [base + (1 if index < remainder else 0) for index in range(bucket_count)]


def adapt_case(
    case: dict[str, object],
    *,
    target_scenario_code: str,
    case_type: str,
    index: int,
    expected_api_success: bool,
    expected_failure_code: str | None,
    expected_failure_stage: str | None,
    expected_recognized_scenario_code: str | None,
    scenario_description: str,
    source_scenario_code: str | None = None,
) -> dict[str, object]:
    adapted = deepcopy(case)
    adapted["id"] = eval_case_id(target_scenario_code, case_type, index)
    adapted["scenario_description"] = scenario_description
    expected_result = deepcopy(adapted.get("expected_result", {}))
    expected_slots = expected_result.get("expected_slots")
    normalized_expected_slots = deepcopy(expected_slots) if isinstance(expected_slots, dict) else None
    adapted["expected_result"] = {
        "should_pass": expected_api_success,
        "expected_failure_code": expected_failure_code,
        "expected_failure_stage": expected_failure_stage,
        "expected_recognized_scenario_code": expected_recognized_scenario_code,
        "expected_slots": normalized_expected_slots,
    }
    tags = dict(adapted.get("tags", {}))
    tags["case_type"] = case_type
    tags["target_scenario_code"] = target_scenario_code
    if source_scenario_code is not None:
        tags["source_scenario_code"] = source_scenario_code
    adapted["tags"] = tags
    return adapted


def build_other_supported_scenario_cases(target_scenario_code: str, count: int) -> list[dict[str, object]]:
    if count == 0:
        return []
    source_scenarios = [scenario_code for scenario_code in SUPPORTED_SCENARIOS if scenario_code != target_scenario_code]
    complete_total = (count + 1) // 2
    partial_total = count - complete_total
    complete_distribution = distribute_evenly(complete_total, len(source_scenarios))
    partial_distribution = distribute_evenly(partial_total, len(source_scenarios))

    cases: list[dict[str, object]] = []
    index = 1
    for source_scenario_code, complete_count, partial_count in zip(
        source_scenarios,
        complete_distribution,
        partial_distribution,
        strict=True,
    ):
        pool = build_legacy_case_pool(
            source_scenario_code,
            positive_complete=complete_count,
            positive_partial=partial_count,
        )
        for case in pool:
            cases.append(
                adapt_case(
                    case,
                    target_scenario_code=target_scenario_code,
                    case_type="negative_other_supported_scenario",
                    index=index,
                    expected_api_success=True,
                    expected_failure_code=None,
                    expected_failure_stage=None,
                    expected_recognized_scenario_code=source_scenario_code,
                    scenario_description=f"合法的 {source_scenario_code} 场景 processed prompt，应被识别为列表内其他支持场景。",
                    source_scenario_code=source_scenario_code,
                )
            )
            index += 1
    return cases


def build_eval_cases(scenario_code: str, counts: dict[str, int]) -> list[dict[str, object]]:
    legacy_pool = build_legacy_case_pool(
        scenario_code,
        positive_complete=counts["positive_complete"],
        positive_partial=counts["positive_partial"],
        negative_unrecognized_prompt=counts["negative_target_outside"],
        negative_invalid_value=counts["negative_invalid_value"],
    )

    mapping = {
        "positive_complete": (
            "positive_complete",
            True,
            None,
            None,
            scenario_code,
            "当前目标场景的完整合法 prompt，应通过校验并识别为目标场景。",
        ),
        "positive_partial": (
            "positive_partial",
            True,
            None,
            None,
            scenario_code,
            "当前目标场景的信息缺失 prompt，仍应通过校验并识别为目标场景。",
        ),
        "negative_unrecognized_prompt": (
            "negative_target_outside",
            False,
            "processed_prompt_parse_error",
            "prompt_parse",
            None,
            "不属于目标场景，且不属于支持场景列表，预期场景识别失败。",
        ),
        "negative_invalid_value": (
            "negative_invalid_value",
            False,
            "slot_validation_error",
            "slot_validation",
            scenario_code,
            "属于目标场景，但槽位值非法，预期在槽位校验阶段失败。",
        ),
    }

    cases: list[dict[str, object]] = []
    index_by_type = {case_type: 1 for case_type in CASE_TYPE_ORDER}
    for case in legacy_pool:
        legacy_case_type = str(case["tags"]["case_type"])
        if legacy_case_type not in mapping:
            continue
        mapped_case_type, should_pass, failure_code, failure_stage, recognized_scenario_code, description = mapping[
            legacy_case_type
        ]
        cases.append(
            adapt_case(
                case,
                target_scenario_code=scenario_code,
                case_type=mapped_case_type,
                index=index_by_type[mapped_case_type],
                expected_api_success=should_pass,
                expected_failure_code=failure_code,
                expected_failure_stage=failure_stage,
                expected_recognized_scenario_code=recognized_scenario_code,
                scenario_description=description,
            )
        )
        index_by_type[mapped_case_type] += 1

    cases.extend(build_other_supported_scenario_cases(scenario_code, counts["negative_other_supported_scenario"]))
    return cases


def render_markdown(scenario_code: str, cases: list[dict[str, Any]]) -> str:
    counts = Counter(str(case["tags"]["case_type"]) for case in cases)
    lines = [
        f"# {legacy.SCENARIO_TITLES[scenario_code]}",
        "",
        f"用例总数：{len(cases)}",
        "",
        "## 分布情况",
        "",
    ]
    for case_type in CASE_TYPE_ORDER:
        lines.append(f"- `{CASE_TYPE_LABELS[case_type]}`：{counts.get(case_type, 0)}")

    lines.extend(["", "## 用例预览", ""])
    for case_type in CASE_TYPE_ORDER:
        bucket = [case for case in cases if case["tags"]["case_type"] == case_type]
        if not bucket:
            continue
        lines.extend([f"### {CASE_TYPE_LABELS[case_type]}", ""])
        for case in bucket:
            lines.extend(
                [
                    f"#### {case['id']}",
                    "",
                    f"- 完整度：`{case['tags']['completeness_level']}`",
                    f"- 预期 API 通过：`{case['expected_result']['should_pass']}`",
                    f"- 预期识别场景：`{case['expected_result'].get('expected_recognized_scenario_code')}`",
                    f"- 预期失败码：`{case['expected_result']['expected_failure_code']}`",
                    f"- 预期失败阶段：`{case['expected_result']['expected_failure_stage']}`",
                    f"- 场景说明：{case['scenario_description']}",
                    "",
                    "```text",
                    str(case["processed_prompt_text"]),
                    "```",
                    "",
                ]
            )
    return "\n".join(lines) + "\n"


def dataset_path(scenario_code: str) -> Path:
    return EVAL_ROOT / f"server_prompt_validation_{scenario_code}_cases.json"


def markdown_path(scenario_code: str) -> Path:
    return DOC_ROOT / f"server_prompt_validation_{scenario_code}_cases.md"


def generate_for_scenario(scenario_code: str, counts: dict[str, int]) -> None:
    cases = build_eval_cases(scenario_code, counts)
    case_ids = [str(case["id"]) for case in cases]
    if len(case_ids) != len(set(case_ids)):
        raise ValueError(f"Duplicate case ids detected for scenario {scenario_code}")

    data_path = dataset_path(scenario_code)
    md_path = markdown_path(scenario_code)
    data_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.parent.mkdir(parents=True, exist_ok=True)
    data_path.write_text(json.dumps(cases, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    md_path.write_text(render_markdown(scenario_code, cases), encoding="utf-8")
    count_summary = ", ".join(f"{case_type}={counts[case_type]}" for case_type in CASE_TYPE_ORDER)
    print(f"[distribution] scenario={scenario_code} total_cases={len(cases)} {count_summary}")
    print(f"已生成 {scenario_code} 用例：{data_path}")
    print(f"已生成 {scenario_code} 预览文档：{md_path}")


def main() -> None:
    parser = argparse.ArgumentParser(description="生成服务端 prompt 校验评估数据集。")
    parser.add_argument(
        "--scenario",
        choices=[*SUPPORTED_SCENARIOS, "all"],
        default="all",
        help="选择要生成的场景，默认生成全部场景。",
    )
    parser.add_argument("--positive-complete-count", type=int, default=None, help="每个场景的正样本-信息完整数量。")
    parser.add_argument("--positive-partial-count", type=int, default=None, help="每个场景的正样本-信息缺失数量。")
    parser.add_argument("--negative-target-outside-count", type=int, default=None, help="每个场景的负样本-目标场景外数量。")
    parser.add_argument(
        "--negative-other-supported-scenario-count",
        type=int,
        default=None,
        help="每个场景的负样本-列表内其他场景数量。",
    )
    parser.add_argument("--negative-invalid-value-count", type=int, default=None, help="每个场景的负样本-槽位值非法数量。")
    args = parser.parse_args()

    scenarios = SUPPORTED_SCENARIOS if args.scenario == "all" else (args.scenario,)
    for scenario_code in scenarios:
        generate_for_scenario(scenario_code, resolve_case_type_counts(scenario_code, args))


if __name__ == "__main__":
    main()
