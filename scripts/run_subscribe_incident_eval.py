from __future__ import annotations

import argparse
import json
import re
from collections import defaultdict
from datetime import datetime, UTC
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
PACKAGE_ENV_PATH = PROJECT_ROOT / "package_data" / ".env"
PROMPT_RESOURCE_ROOT = PROJECT_ROOT / "package_data" / "prompt_resources"
DATASET_PATH = PROJECT_ROOT / "package_data" / "eval" / "subscribe_incident_cases.json"
REPORT_JSON_PATH = PROJECT_ROOT / "docs" / "eval" / "subscribe_incident_eval_report.json"
REPORT_MD_PATH = PROJECT_ROOT / "docs" / "eval" / "subscribe_incident_eval_report.md"
TMP_ENV_PATH = PROJECT_ROOT / ".tmp_subscribe_incident_eval.env"

CASE_TYPE_LABELS = {
    "positive_complete": "正样本-信息完整",
    "positive_partial": "正样本-信息不完整",
    "positive_recognized_but_slot_risky": "正样本-高风险槽位提取",
    "negative_near_intent": "负样本-近邻意图",
    "negative_non_incident_subscription": "负样本-非Incident订阅",
    "negative_ambiguous": "负样本-意图模糊",
}

SEMANTIC_VARIANT_LABELS = {
    "topic_expression": "主题表达",
    "condition_fault_name": "故障名称条件",
    "condition_severity": "级别条件",
    "condition_combination": "组合条件",
    "report_format": "上报格式",
    "subscription_target_context": "订阅对象上下文",
    "noise_interference": "噪声干扰",
    "instruction_purity": "指令纯度",
}

if str(SRC_ROOT) not in __import__("sys").path:
    __import__("sys").path.insert(0, str(SRC_ROOT))

from a2a_t.client.a2at_client import A2ATClient


def normalize_text(text: str) -> str:
    lowered = text.casefold()
    lowered = re.sub(r"\s+", "", lowered)
    lowered = re.sub(r"[，。、“”‘’；;：:（）()【】\[\]《》<>、,.\-_/]", "", lowered)
    return lowered


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


def load_cases(dataset_path: Path, limit: int | None) -> list[dict[str, Any]]:
    cases = json.loads(dataset_path.read_text(encoding="utf-8"))
    if not isinstance(cases, list):
        raise ValueError("Dataset must be a JSON array.")
    return cases[:limit] if limit is not None else cases


def group_match(text: str, group: list[str]) -> bool:
    normalized_text = normalize_text(text)
    return any(normalize_text(candidate) in normalized_text for candidate in group)


def score_groups(text: str, groups: list[list[str]]) -> tuple[int, int, list[list[str]]]:
    hits = 0
    missing: list[list[str]] = []
    for group in groups:
        if group_match(text, group):
            hits += 1
        else:
            missing.append(group)
    return hits, len(groups), missing


def flatten_expected_slot_groups(case: dict[str, Any]) -> list[list[str]]:
    expected_slots = case["expected_result"]["expected_slots"]
    groups: list[list[str]] = []
    for slot_name in ("通知主题", "订阅条件", "上报通知数据格式"):
        raw_groups = expected_slots.get(slot_name, [])
        for group in raw_groups:
            groups.append(list(group))
    return groups


def failure_payload(error: Exception) -> dict[str, Any]:
    return {
        "success": False,
        "prompt_text": None,
        "failure": {
            "code": "exception",
            "message": f"{type(error).__name__}: {error}",
            "stage": None,
        },
    }


def run_case(client: A2ATClient, case: dict[str, Any]) -> dict[str, Any]:
    try:
        payload = client.generate_task_prompt(str(case["input"])).to_dict()
    except Exception as error:
        payload = failure_payload(error)

    prompt_text = payload.get("prompt_text")
    prompt_body = prompt_text if isinstance(prompt_text, str) else ""
    should_recognize = bool(case["expected_result"]["should_recognize"])
    recognized = payload.get("success") is True and bool(prompt_body.strip())
    recognition_correct = recognized if should_recognize else not recognized

    slot_groups = flatten_expected_slot_groups(case)
    slot_hits = slot_total = 0
    slot_missing: list[list[str]] = []
    prompt_groups = [list(group) for group in case["expected_prompt_effect"]["must_include_points"]]
    prompt_hits = prompt_total = 0
    prompt_missing: list[list[str]] = []
    forbidden_hits: list[str] = []

    if should_recognize and recognized:
        slot_hits, slot_total, slot_missing = score_groups(prompt_body, slot_groups)
        prompt_hits, prompt_total, prompt_missing = score_groups(prompt_body, prompt_groups)
        forbidden_hits = [
            phrase for phrase in case["expected_prompt_effect"]["must_not_include_points"] if normalize_text(phrase) in normalize_text(prompt_body)
        ]

    failure = payload.get("failure") if isinstance(payload.get("failure"), dict) else None
    return {
        "id": case["id"],
        "input": case["input"],
        "case_type": case["tags"]["case_type"],
        "semantic_variant": case["tags"]["semantic_variant"],
        "completeness_level": case["tags"]["completeness_level"],
        "should_recognize": should_recognize,
        "recognized": recognized,
        "recognition_correct": recognition_correct,
        "slot_hits": slot_hits,
        "slot_total": slot_total,
        "slot_exact": slot_total > 0 and slot_hits == slot_total,
        "slot_missing": slot_missing,
        "prompt_hits": prompt_hits,
        "prompt_total": prompt_total,
        "prompt_exact": prompt_total > 0 and prompt_hits == prompt_total and not forbidden_hits,
        "prompt_missing": prompt_missing,
        "forbidden_hits": forbidden_hits,
        "payload": payload,
        "prompt_text": prompt_body,
        "failure_code": failure.get("code") if failure else None,
        "failure_stage": failure.get("stage") if failure else None,
        "failure_message": failure.get("message") if failure else None,
    }


def ratio(numerator: int, denominator: int) -> float | None:
    if denominator == 0:
        return None
    return round(numerator / denominator, 4)


def summarize(results: list[dict[str, Any]]) -> dict[str, Any]:
    total_cases = len(results)
    positives = [result for result in results if result["should_recognize"]]
    negatives = [result for result in results if not result["should_recognize"]]
    recognized_positives = [result for result in positives if result["recognized"]]

    by_case_type: dict[str, dict[str, Any]] = {}
    case_type_buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    semantic_buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    completeness_buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)

    for result in results:
        case_type_buckets[str(result["case_type"])].append(result)
        completeness_buckets[str(result["completeness_level"])].append(result)
        for label in result["semantic_variant"]:
            semantic_buckets[str(label)].append(result)

    def bucket_summary(bucket: list[dict[str, Any]]) -> dict[str, Any]:
        recognized_positive_bucket = [item for item in bucket if item["should_recognize"] and item["recognized"]]
        slot_hits = sum(int(item["slot_hits"]) for item in recognized_positive_bucket)
        slot_total = sum(int(item["slot_total"]) for item in recognized_positive_bucket)
        prompt_hits = sum(int(item["prompt_hits"]) for item in recognized_positive_bucket)
        prompt_total = sum(int(item["prompt_total"]) for item in recognized_positive_bucket)
        return {
            "count": len(bucket),
            "recognition_accuracy": ratio(sum(1 for item in bucket if item["recognition_correct"]), len(bucket)),
            "recognized_positive_cases": len(recognized_positive_bucket),
            "slot_exact_rate": ratio(
                sum(1 for item in recognized_positive_bucket if item["slot_exact"]),
                len(recognized_positive_bucket),
            ),
            "slot_group_hit_rate": ratio(slot_hits, slot_total),
            "prompt_exact_rate": ratio(
                sum(1 for item in recognized_positive_bucket if item["prompt_exact"]),
                len(recognized_positive_bucket),
            ),
            "prompt_group_hit_rate": ratio(prompt_hits, prompt_total),
        }

    by_case_type = {key: bucket_summary(bucket) for key, bucket in sorted(case_type_buckets.items())}
    by_semantic_variant = {key: bucket_summary(bucket) for key, bucket in sorted(semantic_buckets.items())}
    by_completeness = {key: bucket_summary(bucket) for key, bucket in sorted(completeness_buckets.items())}

    total_slot_hits = sum(int(item["slot_hits"]) for item in recognized_positives)
    total_slot_groups = sum(int(item["slot_total"]) for item in recognized_positives)
    total_prompt_hits = sum(int(item["prompt_hits"]) for item in recognized_positives)
    total_prompt_groups = sum(int(item["prompt_total"]) for item in recognized_positives)

    failure_samples = []
    for result in results:
        if not result["recognition_correct"] or (result["should_recognize"] and result["recognized"] and (not result["slot_exact"] or not result["prompt_exact"])):
            failure_samples.append(
                {
                    "id": result["id"],
                    "case_type": result["case_type"],
                    "recognition_correct": result["recognition_correct"],
                    "slot_exact": result["slot_exact"],
                    "prompt_exact": result["prompt_exact"],
                    "failure_code": result["failure_code"],
                    "failure_stage": result["failure_stage"],
                    "failure_message": result["failure_message"],
                    "slot_missing": result["slot_missing"],
                    "prompt_missing": result["prompt_missing"],
                    "forbidden_hits": result["forbidden_hits"],
                    "input": result["input"],
                    "prompt_text": result["prompt_text"],
                }
            )
        if len(failure_samples) >= 15:
            break

    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "summary": {
            "total_cases": total_cases,
            "positive_cases": len(positives),
            "negative_cases": len(negatives),
            "overall_recognition_accuracy": ratio(sum(1 for item in results if item["recognition_correct"]), total_cases),
            "positive_case_recognition_accuracy": ratio(sum(1 for item in positives if item["recognized"]), len(positives)),
            "negative_case_false_positive_rate": ratio(sum(1 for item in negatives if item["recognized"]), len(negatives)),
            "recognized_positive_cases": len(recognized_positives),
            "slot_exact_rate_within_recognized_positive_cases": ratio(
                sum(1 for item in recognized_positives if item["slot_exact"]),
                len(recognized_positives),
            ),
            "slot_group_hit_rate_within_recognized_positive_cases": ratio(total_slot_hits, total_slot_groups),
            "prompt_exact_rate_within_recognized_positive_cases": ratio(
                sum(1 for item in recognized_positives if item["prompt_exact"]),
                len(recognized_positives),
            ),
            "prompt_group_hit_rate_within_recognized_positive_cases": ratio(total_prompt_hits, total_prompt_groups),
        },
        "by_case_type": by_case_type,
        "by_semantic_variant": by_semantic_variant,
        "by_completeness_level": by_completeness,
        "failure_samples": failure_samples,
        "results": results,
    }


def render_markdown(report: dict[str, Any]) -> str:
    summary = report["summary"]
    lines = [
        "# Incident订阅评测报告",
        "",
        f"生成时间：{report['generated_at']}",
        "",
        "## 总览",
        "",
        f"- 用例总数：{summary['total_cases']}",
        f"- 正样本数：{summary['positive_cases']}",
        f"- 负样本数：{summary['negative_cases']}",
        f"- 总体识别准确率：{summary['overall_recognition_accuracy']}",
        f"- 正样本识别准确率：{summary['positive_case_recognition_accuracy']}",
        f"- 负样本误识别率：{summary['negative_case_false_positive_rate']}",
        f"- 成功识别的正样本数：{summary['recognized_positive_cases']}",
        f"- 已识别正样本中的槽位完全命中率：{summary['slot_exact_rate_within_recognized_positive_cases']}",
        f"- 已识别正样本中的槽位组命中率：{summary['slot_group_hit_rate_within_recognized_positive_cases']}",
        f"- 已识别正样本中的Prompt完全命中率：{summary['prompt_exact_rate_within_recognized_positive_cases']}",
        f"- 已识别正样本中的Prompt关键组命中率：{summary['prompt_group_hit_rate_within_recognized_positive_cases']}",
        "",
    ]

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
                "| 分组 | 数量 | 识别准确率 | 槽位完全命中率 | 槽位组命中率 | Prompt完全命中率 | Prompt组命中率 |",
                "| --- | --- | --- | --- | --- | --- | --- |",
            ]
        )
        for key, metrics in payload.items():
            lines.append(
                "| `{}` | {} | {} | {} | {} | {} | {} |".format(
                    display_bucket_key(section, key),
                    metrics["count"],
                    metrics["recognition_accuracy"],
                    metrics["slot_exact_rate"],
                    metrics["slot_group_hit_rate"],
                    metrics["prompt_exact_rate"],
                    metrics["prompt_group_hit_rate"],
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
                f"- 识别是否正确：`{failure['recognition_correct']}`",
                f"- 槽位是否完全命中：`{failure['slot_exact']}`",
                f"- Prompt是否完全命中：`{failure['prompt_exact']}`",
                f"- 失败码：`{failure['failure_code']}`",
                f"- 失败阶段：`{failure['failure_stage']}`",
                f"- 缺失槽位组：`{failure['slot_missing']}`",
                f"- 缺失Prompt组：`{failure['prompt_missing']}`",
                f"- 命中的禁止项：`{failure['forbidden_hits']}`",
                "",
                "**输入**",
                "",
                failure["input"],
                "",
                "**失败信息**",
                "",
                str(failure["failure_message"]),
                "",
                "**生成的Prompt**",
                "",
                "```text",
                failure["prompt_text"] or "",
                "```",
                "",
            ]
        )
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description="执行 subscribe_incident 评测用例并输出统计结果。")
    parser.add_argument("--dataset", type=Path, default=DATASET_PATH)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--out-json", type=Path, default=REPORT_JSON_PATH)
    parser.add_argument("--out-md", type=Path, default=REPORT_MD_PATH)
    args = parser.parse_args()

    cases = load_cases(args.dataset, args.limit)
    env_path = build_env()
    try:
        client = A2ATClient(env_path=env_path)
        results = []
        for index, case in enumerate(cases, start=1):
            result = run_case(client, case)
            results.append(result)
            if index % 10 == 0 or index == len(cases):
                print(f"已处理 {index}/{len(cases)} 条用例")
    finally:
        cleanup_env(env_path)

    report = summarize(results)
    args.out_json.parent.mkdir(parents=True, exist_ok=True)
    args.out_md.parent.mkdir(parents=True, exist_ok=True)
    args.out_json.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    args.out_md.write_text(render_markdown(report), encoding="utf-8")
    print(f"已写出 JSON 报告：{args.out_json}")
    print(f"已写出 Markdown 报告：{args.out_md}")


if __name__ == "__main__":
    main()
