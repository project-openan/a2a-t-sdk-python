from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
PACKAGE_ENV_PATH = PROJECT_ROOT / "package_data" / ".env"
PROMPT_RESOURCE_ROOT = PROJECT_ROOT / "package_data" / "prompt_resources"
EVAL_ROOT = PROJECT_ROOT / "package_data" / "eval"
DOC_ROOT = PROJECT_ROOT / "docs" / "eval"
TMP_ENV_PATH = PROJECT_ROOT / ".tmp_server_prompt_validation_eval.env"

if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from a2a_t.common.prompt_runtime import PromptRuntimeComponentsBuilder
from a2a_t.config.models import A2ATConfig
from a2a_t.llm.client import LLMClient
from a2a_t.prompt.analysis import ScenarioRecognizer, ScenarioResolutionOrchestrator, SlotExtractor
from a2a_t.prompt.common.models import PromptReference
from a2a_t.server.a2at_server import A2ATServer


SUPPORTED_SCENARIOS = ("energy_saving", "subscribe_incident", "fault_diagnosis")

CASE_TYPE_LABELS = {
    "positive_complete": "正样本-信息完整",
    "positive_partial": "正样本-信息缺失",
    "negative_target_outside": "负样本-目标场景外",
    "negative_other_supported_scenario": "负样本-列表内其他场景",
    "negative_invalid_value": "负样本-槽位值非法",
}

SEMANTIC_VARIANT_LABELS = {
    "goal_expression": "目标表达",
    "object_expression": "对象表达",
    "context_background": "上下文表达",
    "constraint_expression": "约束表达",
    "constraint_value": "约束值非法",
    "topic_expression": "主题表达",
    "condition_expression": "条件表达",
    "condition_value": "条件值非法",
    "report_format": "上报格式",
    "instruction_purity": "指令纯度",
    "semantic_mismatch": "语义冲突",
    "cross_scenario_pollution": "跨场景污染",
}


def replace_or_append_env_var(lines: list[str], key: str, value: str) -> list[str]:
    prefix = f"{key}="
    updated: list[str] = []
    replaced = False
    for line in lines:
        if line.startswith(prefix):
            updated.append(f"{prefix}{value}")
            replaced = True
        else:
            updated.append(line)
    if not replaced:
        updated.append(f"{prefix}{value}")
    return updated


def build_env() -> Path:
    lines = PACKAGE_ENV_PATH.read_text(encoding="utf-8").splitlines()
    overrides = {
        "A2AT_LANGUAGE": "zh-CN",
        "A2AT_PROMPT_SOURCE_TYPE": "local_file",
        "A2AT_PROMPT_RESOURCE_LOCAL_ROOT_DIR": str(PROMPT_RESOURCE_ROOT),
    }
    for key, value in overrides.items():
        lines = replace_or_append_env_var(lines, key, value)
    TMP_ENV_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return TMP_ENV_PATH


def cleanup_env(env_path: Path) -> None:
    env_path.unlink(missing_ok=True)


def default_dataset_path(scenario_code: str) -> Path:
    return EVAL_ROOT / f"server_prompt_validation_{scenario_code}_cases.json"


def default_report_json_path(scenario_code: str) -> Path:
    return DOC_ROOT / f"server_prompt_validation_{scenario_code}_report.json"


def default_report_md_path(scenario_code: str) -> Path:
    return DOC_ROOT / f"server_prompt_validation_{scenario_code}_report.md"


def load_cases(dataset_path: Path, limit: int | None) -> list[dict[str, Any]]:
    cases = json.loads(dataset_path.read_text(encoding="utf-8"))
    if not isinstance(cases, list):
        raise ValueError("Dataset must be a JSON array.")
    return cases[:limit] if limit is not None else cases


def extract_failure(payload: dict[str, Any]) -> tuple[str | None, str | None, str | None]:
    failure = payload.get("failure")
    if not isinstance(failure, dict):
        return None, None, None
    code = failure.get("code")
    stage = failure.get("stage")
    message = failure.get("message")
    return (
        code if isinstance(code, str) else None,
        stage if isinstance(stage, str) else None,
        message if isinstance(message, str) else None,
    )


def build_prompt_analysis_runtime(
    env_path: Path,
) -> tuple[ScenarioResolutionOrchestrator, SlotExtractor, Any]:
    config = A2ATConfig.load(env_path)
    llm_client = LLMClient(env_path=env_path)
    runtime_components = PromptRuntimeComponentsBuilder().build(config=config)
    recognizer = ScenarioRecognizer(llm_client=llm_client)
    resolver = ScenarioResolutionOrchestrator(
        config=config.prompt,
        scenario_loader=runtime_components.scenario_loader,
        prompt_resource_loader=runtime_components.prompt_resource_loader,
        scenario_recognizer=recognizer,
    )
    extractor = SlotExtractor(llm_client=llm_client)
    return resolver, extractor, runtime_components


def normalize_slot_value(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        value = str(value)
    normalized = value.strip()
    return normalized or None


def normalize_slots(raw_slots: object) -> dict[str, str | None] | None:
    if not isinstance(raw_slots, dict):
        return None
    return {str(slot_name): normalize_slot_value(slot_value) for slot_name, slot_value in raw_slots.items()}


def serialize_slot_errors(raw_slot_errors: object) -> list[dict[str, str]]:
    if not isinstance(raw_slot_errors, list):
        return []
    serialized: list[dict[str, str]] = []
    for item in raw_slot_errors:
        if hasattr(item, "to_dict"):
            payload = item.to_dict()
            if isinstance(payload, dict):
                serialized.append({str(key): str(value) for key, value in payload.items()})
                continue
        if isinstance(item, dict):
            serialized.append({str(key): str(value) for key, value in item.items()})
    return serialized


def resolve_scenario(
    resolver: ScenarioResolutionOrchestrator,
    processed_prompt_text: str,
) -> dict[str, Any]:
    resolution = resolver.resolve(processed_prompt_text)
    if resolution.success and resolution.reference is not None:
        return {
            "recognized_scenario_code": resolution.reference.scenario_code,
            "reference": resolution.reference,
            "failure_code": None,
            "failure_stage": None,
            "failure_message": None,
        }
    failure = resolution.failure
    if failure is None:
        return {
            "recognized_scenario_code": None,
            "reference": None,
            "failure_code": None,
            "failure_stage": None,
            "failure_message": None,
        }
    return {
        "recognized_scenario_code": None,
        "reference": None,
        "failure_code": failure.code,
        "failure_stage": failure.stage,
        "failure_message": failure.message,
    }


def extract_slots_for_reference(
    *,
    processed_prompt_text: str,
    reference: PromptReference,
    extractor: SlotExtractor,
    runtime_components: Any,
) -> tuple[dict[str, str | None] | None, list[dict[str, str]], str | None]:
    try:
        template_text = runtime_components.template_loader.load(reference=reference)
        slot_schema = runtime_components.slot_schema_loader.load(reference=reference)
        slot_prompts = runtime_components.prompt_resource_loader.load(
            analysis_action="slot_extraction",
            language=reference.language,
        )
        extraction_result = extractor.extract(
            normalized_input=processed_prompt_text,
            reference=reference,
            template_text=template_text,
            slot_schema=slot_schema,
            system_prompt=slot_prompts.system_prompt,
            user_prompt=slot_prompts.user_prompt,
        )
    except Exception as error:
        return None, [], f"{type(error).__name__}: {error}"

    return normalize_slots(extraction_result.slots), serialize_slot_errors(extraction_result.slot_errors), None


def compare_slots(
    expected_slots: dict[str, str | None] | None,
    actual_slots: dict[str, str | None] | None,
) -> dict[str, Any]:
    if expected_slots is None:
        return {
            "slot_contract_required": False,
            "slot_value_check_executed": False,
            "slot_contract_matched": None,
            "slot_total_field_count": 0,
            "slot_matched_field_count": 0,
            "slot_missing_fields": [],
            "slot_mismatched_fields": [],
            "slot_unexpected_fields": [],
        }

    normalized_expected = normalize_slots(expected_slots) or {}
    normalized_actual = normalize_slots(actual_slots)
    if normalized_actual is None:
        return {
            "slot_contract_required": True,
            "slot_value_check_executed": False,
            "slot_contract_matched": False,
            "slot_total_field_count": len(normalized_expected),
            "slot_matched_field_count": 0,
            "slot_missing_fields": sorted(normalized_expected.keys()),
            "slot_mismatched_fields": [],
            "slot_unexpected_fields": [],
        }

    matched_field_count = 0
    missing_fields: list[str] = []
    mismatched_fields: list[dict[str, str | None]] = []
    for slot_name in sorted(normalized_expected.keys()):
        if slot_name not in normalized_actual:
            missing_fields.append(slot_name)
            continue
        expected_value = normalized_expected.get(slot_name)
        actual_value = normalized_actual.get(slot_name)
        if expected_value == actual_value:
            matched_field_count += 1
            continue
        mismatched_fields.append(
            {
                "slot_name": slot_name,
                "expected_value": expected_value,
                "actual_value": actual_value,
            }
        )

    unexpected_fields = sorted(slot_name for slot_name in normalized_actual.keys() if slot_name not in normalized_expected)
    return {
        "slot_contract_required": True,
        "slot_value_check_executed": True,
        "slot_contract_matched": not missing_fields and not mismatched_fields and not unexpected_fields,
        "slot_total_field_count": len(normalized_expected),
        "slot_matched_field_count": matched_field_count,
        "slot_missing_fields": missing_fields,
        "slot_mismatched_fields": mismatched_fields,
        "slot_unexpected_fields": unexpected_fields,
    }


def run_case(
    server: A2ATServer,
    resolver: ScenarioResolutionOrchestrator,
    extractor: SlotExtractor,
    runtime_components: Any,
    case: dict[str, Any],
) -> dict[str, Any]:
    processed_prompt_text = str(case["processed_prompt_text"])
    payload = server.check_task_prompt(processed_prompt_text=processed_prompt_text)
    expected = case["expected_result"]
    should_pass = bool(expected["should_pass"])
    expected_recognized_scenario_code = expected.get("expected_recognized_scenario_code")
    expected_slots = normalize_slots(expected.get("expected_slots"))
    actual_success = payload.get("success") is True
    actual_failure_code, actual_failure_stage, actual_failure_message = extract_failure(payload)

    scenario_resolution = resolve_scenario(resolver, processed_prompt_text)
    actual_recognized_scenario_code = scenario_resolution["recognized_scenario_code"]
    actual_recognition_failure_code = scenario_resolution["failure_code"]
    actual_recognition_failure_stage = scenario_resolution["failure_stage"]
    actual_recognition_failure_message = scenario_resolution["failure_message"]
    reference = scenario_resolution["reference"]

    actual_slots: dict[str, str | None] | None = None
    actual_slot_errors: list[dict[str, str]] = []
    actual_slot_extraction_error: str | None = None
    if reference is not None and expected_slots is not None:
        actual_slots, actual_slot_errors, actual_slot_extraction_error = extract_slots_for_reference(
            processed_prompt_text=processed_prompt_text,
            reference=reference,
            extractor=extractor,
            runtime_components=runtime_components,
        )

    slot_comparison = compare_slots(expected_slots, actual_slots)

    recognition_matched = actual_recognized_scenario_code == expected_recognized_scenario_code
    if expected_recognized_scenario_code is None:
        recognition_matched = actual_recognized_scenario_code is None

    failure_contract_required = not should_pass and (
        expected.get("expected_failure_code") is not None or expected.get("expected_failure_stage") is not None
    )
    failure_contract_matched: bool | None = None
    if failure_contract_required:
        failure_contract_matched = (
            actual_failure_code == expected.get("expected_failure_code")
            and actual_failure_stage == expected.get("expected_failure_stage")
        )

    api_expectation_matched = actual_success == should_pass
    if failure_contract_required:
        api_expectation_matched = api_expectation_matched and bool(failure_contract_matched)

    matched_expectation = api_expectation_matched and recognition_matched
    if slot_comparison["slot_contract_matched"] is not None:
        matched_expectation = matched_expectation and bool(slot_comparison["slot_contract_matched"])

    return {
        "id": case["id"],
        "case_type": case["tags"]["case_type"],
        "semantic_variant": case["tags"]["semantic_variant"],
        "completeness_level": case["tags"]["completeness_level"],
        "target_scenario_code": case["tags"].get("target_scenario_code"),
        "source_scenario_code": case["tags"].get("source_scenario_code"),
        "scenario_description": case["scenario_description"],
        "processed_prompt_text": processed_prompt_text,
        "should_pass": should_pass,
        "actual_success": actual_success,
        "expected_recognized_scenario_code": expected_recognized_scenario_code,
        "actual_recognized_scenario_code": actual_recognized_scenario_code,
        "recognition_matched": recognition_matched,
        "expected_failure_code": expected.get("expected_failure_code"),
        "expected_failure_stage": expected.get("expected_failure_stage"),
        "expected_slots": expected_slots,
        "actual_failure_code": actual_failure_code,
        "actual_failure_stage": actual_failure_stage,
        "actual_failure_message": actual_failure_message,
        "actual_recognition_failure_code": actual_recognition_failure_code,
        "actual_recognition_failure_stage": actual_recognition_failure_stage,
        "actual_recognition_failure_message": actual_recognition_failure_message,
        "actual_slots": actual_slots,
        "actual_slot_errors": actual_slot_errors,
        "actual_slot_extraction_error": actual_slot_extraction_error,
        **slot_comparison,
        "failure_contract_required": failure_contract_required,
        "failure_contract_matched": failure_contract_matched,
        "api_expectation_matched": api_expectation_matched,
        "matched_expectation": matched_expectation,
        "payload": payload,
    }


def ratio(numerator: int, denominator: int) -> float | None:
    if denominator == 0:
        return None
    return round(numerator / denominator, 4)


def format_percent(value: float | None) -> str:
    if value is None:
        return "N/A"
    return f"{value * 100:.2f}%"


def summarize_results(scenario_code: str, results: list[dict[str, Any]]) -> dict[str, Any]:
    expected_api_success_cases = [item for item in results if item["should_pass"]]
    expected_api_failure_cases = [item for item in results if not item["should_pass"]]
    failure_contract_cases = [item for item in results if item["failure_contract_required"]]
    slot_contract_cases = [item for item in results if item["slot_contract_required"]]
    slot_value_checked_cases = [item for item in slot_contract_cases if item["slot_value_check_executed"]]

    case_type_buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    semantic_buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    completeness_buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)

    for result in results:
        case_type_buckets[str(result["case_type"])].append(result)
        completeness_buckets[str(result["completeness_level"])].append(result)
        for label in result["semantic_variant"]:
            semantic_buckets[str(label)].append(result)

    def bucket_summary(bucket: list[dict[str, Any]]) -> dict[str, Any]:
        failure_contract_bucket = [item for item in bucket if item["failure_contract_required"]]
        slot_contract_bucket = [item for item in bucket if item["slot_contract_required"]]
        slot_value_checked_bucket = [item for item in slot_contract_bucket if item["slot_value_check_executed"]]
        return {
            "count": len(bucket),
            "combined_expectation_accuracy": ratio(sum(1 for item in bucket if item["matched_expectation"]), len(bucket)),
            "api_expectation_accuracy": ratio(sum(1 for item in bucket if item["api_expectation_matched"]), len(bucket)),
            "scenario_recognition_accuracy": ratio(sum(1 for item in bucket if item["recognition_matched"]), len(bucket)),
            "pass_rate": ratio(sum(1 for item in bucket if item["actual_success"]), len(bucket)),
            "failure_contract_accuracy": ratio(
                sum(1 for item in failure_contract_bucket if item["failure_contract_matched"]),
                len(failure_contract_bucket),
            ),
            "slot_contract_case_count": len(slot_contract_bucket),
            "slot_value_checked_case_count": len(slot_value_checked_bucket),
            "slot_exact_rate": ratio(
                sum(1 for item in slot_contract_bucket if item["slot_contract_matched"]),
                len(slot_contract_bucket),
            ),
            "slot_field_hit_rate": ratio(
                sum(int(item["slot_matched_field_count"]) for item in slot_value_checked_bucket),
                sum(int(item["slot_total_field_count"]) for item in slot_value_checked_bucket),
            ),
        }

    by_case_type = {key: bucket_summary(bucket) for key, bucket in sorted(case_type_buckets.items())}
    by_semantic_variant = {key: bucket_summary(bucket) for key, bucket in sorted(semantic_buckets.items())}
    by_completeness = {key: bucket_summary(bucket) for key, bucket in sorted(completeness_buckets.items())}

    failure_samples = []
    for result in results:
        if not result["matched_expectation"]:
            failure_samples.append(
                {
                    "id": result["id"],
                    "case_type": result["case_type"],
                    "should_pass": result["should_pass"],
                    "actual_success": result["actual_success"],
                    "expected_recognized_scenario_code": result["expected_recognized_scenario_code"],
                    "actual_recognized_scenario_code": result["actual_recognized_scenario_code"],
                    "expected_failure_code": result["expected_failure_code"],
                    "expected_failure_stage": result["expected_failure_stage"],
                    "actual_failure_code": result["actual_failure_code"],
                    "actual_failure_stage": result["actual_failure_stage"],
                    "actual_failure_message": result["actual_failure_message"],
                    "actual_recognition_failure_code": result["actual_recognition_failure_code"],
                    "actual_recognition_failure_stage": result["actual_recognition_failure_stage"],
                    "actual_recognition_failure_message": result["actual_recognition_failure_message"],
                    "expected_slots": result["expected_slots"],
                    "actual_slots": result["actual_slots"],
                    "actual_slot_errors": result["actual_slot_errors"],
                    "actual_slot_extraction_error": result["actual_slot_extraction_error"],
                    "slot_contract_required": result["slot_contract_required"],
                    "slot_value_check_executed": result["slot_value_check_executed"],
                    "slot_contract_matched": result["slot_contract_matched"],
                    "slot_missing_fields": result["slot_missing_fields"],
                    "slot_mismatched_fields": result["slot_mismatched_fields"],
                    "slot_unexpected_fields": result["slot_unexpected_fields"],
                    "processed_prompt_text": result["processed_prompt_text"],
                }
            )
        if len(failure_samples) >= 15:
            break

    success_cases = [result for result in results if result["matched_expectation"]]
    success_samples = [
        {
            "id": result["id"],
            "case_type": result["case_type"],
            "should_pass": result["should_pass"],
            "actual_success": result["actual_success"],
            "recognition_matched": result["recognition_matched"],
            "matched_expectation": result["matched_expectation"],
            "slot_contract_matched": result["slot_contract_matched"],
            "target_scenario_code": result["target_scenario_code"],
            "source_scenario_code": result["source_scenario_code"],
        }
        for result in success_cases
    ][:15]

    success_by_case_type = dict(sorted(Counter(str(item["case_type"]) for item in success_cases).items()))
    success_slot_exact_count = sum(1 for item in success_cases if item["slot_contract_matched"] is True)

    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "scenario_code": scenario_code,
        "summary": {
            "total_cases": len(results),
            "api_success_expected_cases": len(expected_api_success_cases),
            "api_failure_expected_cases": len(expected_api_failure_cases),
            "combined_expectation_accuracy": ratio(
                sum(1 for item in results if item["matched_expectation"]),
                len(results),
            ),
            "api_expectation_accuracy": ratio(
                sum(1 for item in results if item["api_expectation_matched"]),
                len(results),
            ),
            "scenario_recognition_accuracy": ratio(
                sum(1 for item in results if item["recognition_matched"]),
                len(results),
            ),
            "api_success_case_pass_rate": ratio(
                sum(1 for item in expected_api_success_cases if item["actual_success"]),
                len(expected_api_success_cases),
            ),
            "api_failure_case_rejection_rate": ratio(
                sum(1 for item in expected_api_failure_cases if not item["actual_success"]),
                len(expected_api_failure_cases),
            ),
            "failure_contract_accuracy": ratio(
                sum(1 for item in failure_contract_cases if item["failure_contract_matched"]),
                len(failure_contract_cases),
            ),
            "slot_contract_case_count": len(slot_contract_cases),
            "slot_value_checked_case_count": len(slot_value_checked_cases),
            "slot_exact_rate": ratio(
                sum(1 for item in slot_contract_cases if item["slot_contract_matched"]),
                len(slot_contract_cases),
            ),
            "slot_field_hit_rate": ratio(
                sum(int(item["slot_matched_field_count"]) for item in slot_value_checked_cases),
                sum(int(item["slot_total_field_count"]) for item in slot_value_checked_cases),
            ),
            "success_case_count": len(success_cases),
            "success_slot_exact_count": success_slot_exact_count,
            "success_case_type_distribution": success_by_case_type,
        },
        "by_case_type": by_case_type,
        "by_semantic_variant": by_semantic_variant,
        "by_completeness_level": by_completeness,
        "failure_samples": failure_samples,
        "success_samples": success_samples,
        "results": results,
    }


def render_markdown(report: dict[str, Any]) -> str:
    summary = report["summary"]
    lines = [
        f"# {report['scenario_code']} 服务端 prompt 校验评估报告",
        "",
        f"生成时间：{report['generated_at']}",
        "",
        "## 总览",
        "",
        f"- 用例总数：{summary['total_cases']}",
        f"- 预期 API 通过用例数：{summary['api_success_expected_cases']}",
        f"- 预期 API 拒绝用例数：{summary['api_failure_expected_cases']}",
        f"- 综合正确率：{format_percent(summary['combined_expectation_accuracy'])}",
        f"- API 结果正确率：{format_percent(summary['api_expectation_accuracy'])}",
        f"- 场景识别正确率：{format_percent(summary['scenario_recognition_accuracy'])}",
        f"- 槽位完全命中率：{format_percent(summary['slot_exact_rate'])}",
        f"- 槽位字段命中率：{format_percent(summary['slot_field_hit_rate'])}",
        f"- 预期 API 通过用例的实际通过率：{format_percent(summary['api_success_case_pass_rate'])}",
        f"- 预期 API 拒绝用例的实际拦截率：{format_percent(summary['api_failure_case_rejection_rate'])}",
        f"- 失败码/阶段契约正确率：{format_percent(summary['failure_contract_accuracy'])}",
        f"- 需要校验 expected_slots 的用例数：{summary['slot_contract_case_count']}",
        f"- 实际完成槽位比对的用例数：{summary['slot_value_checked_case_count']}",
        f"- 正确用例数：{summary['success_case_count']}",
        "",
    ]

    if report.get("success_samples"):
        lines.extend(["## 正确样例", ""])
        lines.append(f"- 各 case_type 正确分布：`{summary['success_case_type_distribution']}`")
        lines.append(f"- 其中槽位也完全命中的正确用例数：`{summary['success_slot_exact_count']}`")
        lines.append("")
        for sample in report["success_samples"]:
            lines.append(
                f"- {sample['id']}: `API={sample['actual_success']}` `识别={sample['recognition_matched']}` `槽位={sample['slot_contract_matched']}` `综合={sample['matched_expectation']}`"
            )
        lines.append("")

    def display_bucket_key(section: str, key: str) -> str:
        if section == "case_type":
            return CASE_TYPE_LABELS.get(key, key)
        if section == "semantic_variant":
            return SEMANTIC_VARIANT_LABELS.get(key, key)
        return key

    def render_bucket(title: str, payload: dict[str, Any], *, section: str) -> None:
        lines.extend(
            [
                f"## {title}",
                "",
                "| 分组 | 数量 | 综合正确率 | API正确率 | 场景识别正确率 | 槽位完全命中率 | 槽位字段命中率 | 实际通过率 | 失败契约正确率 |",
                "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
            ]
        )
        for key, metrics in payload.items():
            lines.append(
                "| `{}` | {} | {} | {} | {} | {} | {} | {} | {} |".format(
                    display_bucket_key(section, key),
                    metrics["count"],
                    format_percent(metrics["combined_expectation_accuracy"]),
                    format_percent(metrics["api_expectation_accuracy"]),
                    format_percent(metrics["scenario_recognition_accuracy"]),
                    format_percent(metrics["slot_exact_rate"]),
                    format_percent(metrics["slot_field_hit_rate"]),
                    format_percent(metrics["pass_rate"]),
                    format_percent(metrics["failure_contract_accuracy"]),
                )
            )
        lines.append("")

    render_bucket("按用例类型统计", report["by_case_type"], section="case_type")
    render_bucket("按语义变体统计", report["by_semantic_variant"], section="semantic_variant")
    render_bucket("按完整度统计", report["by_completeness_level"], section="completeness")

    lines.extend(["## 失败样例", ""])
    for failure in report["failure_samples"]:
        lines.extend(
            [
                f"### {failure['id']}",
                "",
                f"- 用例类型：`{CASE_TYPE_LABELS.get(failure['case_type'], failure['case_type'])}`",
                f"- 预期 API 通过：`{failure['should_pass']}`",
                f"- 实际 API 通过：`{failure['actual_success']}`",
                f"- 预期识别场景：`{failure['expected_recognized_scenario_code']}`",
                f"- 实际识别场景：`{failure['actual_recognized_scenario_code']}`",
                f"- 预期失败码：`{failure['expected_failure_code']}`",
                f"- 预期失败阶段：`{failure['expected_failure_stage']}`",
                f"- 实际失败码：`{failure['actual_failure_code']}`",
                f"- 实际失败阶段：`{failure['actual_failure_stage']}`",
                f"- 场景识别失败码：`{failure['actual_recognition_failure_code']}`",
                f"- 场景识别失败阶段：`{failure['actual_recognition_failure_stage']}`",
                f"- expected_slots 校验是否要求：`{failure['slot_contract_required']}`",
                f"- expected_slots 是否完成比对：`{failure['slot_value_check_executed']}`",
                f"- 槽位是否完全命中：`{failure['slot_contract_matched']}`",
                "",
                "**API 失败信息**",
                "",
                str(failure["actual_failure_message"]),
                "",
                "**场景识别失败信息**",
                "",
                str(failure["actual_recognition_failure_message"]),
                "",
                "**processed_prompt_text**",
                "",
                "```text",
                str(failure["processed_prompt_text"]),
                "```",
                "",
                "**expected_slots**",
                "",
                "```json",
                json.dumps(failure["expected_slots"], ensure_ascii=False, indent=2),
                "```",
                "",
                "**actual_slots**",
                "",
                "```json",
                json.dumps(failure["actual_slots"], ensure_ascii=False, indent=2),
                "```",
                "",
                f"- 槽位缺失字段：`{failure['slot_missing_fields']}`",
                f"- 槽位不匹配字段：`{failure['slot_mismatched_fields']}`",
                f"- 槽位意外字段：`{failure['slot_unexpected_fields']}`",
                f"- 槽位提取错误：`{failure['actual_slot_extraction_error']}`",
                f"- 槽位提取上游错误：`{failure['actual_slot_errors']}`",
                "",
            ]
        )
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description="执行服务端 prompt 校验评估用例并输出统计结果。")
    parser.add_argument("--scenario", choices=SUPPORTED_SCENARIOS, required=True)
    parser.add_argument("--dataset", type=Path, default=None)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--out-json", type=Path, default=None)
    parser.add_argument("--out-md", type=Path, default=None)
    parser.add_argument("--progress-interval", type=int, default=10, help="每处理多少条用例输出一次进度。")
    args = parser.parse_args()

    dataset = args.dataset or default_dataset_path(args.scenario)
    out_json = args.out_json or default_report_json_path(args.scenario)
    out_md = args.out_md or default_report_md_path(args.scenario)

    cases = load_cases(dataset, args.limit)
    env_path = build_env()
    started_at = perf_counter()
    try:
        server = A2ATServer(env_path=env_path)
        resolver, extractor, runtime_components = build_prompt_analysis_runtime(env_path)
        results = []
        progress_interval = max(args.progress_interval, 1)
        print(
            f"[start] scenario={args.scenario} total_cases={len(cases)} "
            f"progress_interval={progress_interval} dataset={dataset}",
            flush=True,
        )
        for index, case in enumerate(cases, start=1):
            result = run_case(server, resolver, extractor, runtime_components, case)
            results.append(result)
            if index % progress_interval == 0 or index == len(cases):
                elapsed_seconds = round(perf_counter() - started_at, 1)
                matched_count = sum(1 for item in results if item["matched_expectation"])
                recognition_matched_count = sum(1 for item in results if item["recognition_matched"])
                slot_exact_count = sum(1 for item in results if item["slot_contract_matched"] is True)
                print(
                    f"[progress] processed={index}/{len(cases)} "
                    f"combined_matched={matched_count} recognition_matched={recognition_matched_count} "
                    f"slot_exact_matched={slot_exact_count} "
                    f"last_case={case['id']} elapsed_seconds={elapsed_seconds}",
                    flush=True,
                )
    finally:
        cleanup_env(env_path)

    report = summarize_results(args.scenario, results)
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    out_md.write_text(render_markdown(report), encoding="utf-8")
    total_elapsed_seconds = round(perf_counter() - started_at, 1)
    print(f"[done] json_report={out_json}", flush=True)
    print(f"[done] markdown_report={out_md}", flush=True)
    print(
        "[summary] total_cases={} combined_accuracy={} api_accuracy={} recognition_accuracy={} slot_exact_rate={} slot_field_hit_rate={} elapsed_seconds={}".format(
            report["summary"]["total_cases"],
            format_percent(report["summary"]["combined_expectation_accuracy"]),
            format_percent(report["summary"]["api_expectation_accuracy"]),
            format_percent(report["summary"]["scenario_recognition_accuracy"]),
            format_percent(report["summary"]["slot_exact_rate"]),
            format_percent(report["summary"]["slot_field_hit_rate"]),
            total_elapsed_seconds,
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()
