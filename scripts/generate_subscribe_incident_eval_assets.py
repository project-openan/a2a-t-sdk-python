from __future__ import annotations

import json
from collections import Counter
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATASET_PATH = PROJECT_ROOT / "package_data" / "eval" / "subscribe_incident_cases.json"
MARKDOWN_PATH = PROJECT_ROOT / "docs" / "eval" / "subscribe_incident_cases.md"

CASE_TYPE_COUNTS = {
    "positive_complete": 50,
    "positive_partial": 30,
    "positive_recognized_but_slot_risky": 25,
    "negative_near_intent": 20,
    "negative_non_incident_subscription": 20,
    "negative_ambiguous": 15,
}

CASE_TYPE_LABELS = {
    "positive_complete": "正样本-信息完整",
    "positive_partial": "正样本-信息不完整",
    "positive_recognized_but_slot_risky": "正样本-高风险槽位提取",
    "negative_near_intent": "负样本-近邻意图",
    "negative_non_incident_subscription": "负样本-非Incident订阅",
    "negative_ambiguous": "负样本-意图模糊",
}

TARGETS = [
    "基站设备",
    "无线网元",
    "站点设备",
    "接入侧网元",
    "汇聚节点",
    "OLT设备",
    "核心网网元",
    "园区站点",
    "传输设备",
    "室分设备",
]

TOPIC_VARIANTS = [
    "Incident",
    "Incident事件",
    "故障Incident",
    "智能故障Incident",
]

FAULTS = [
    "光纤中断",
    "单板故障",
    "光模块故障",
    "尾纤故障",
    "主控板异常",
    "电源模块告警",
    "链路抖动",
    "端口异常",
    "风扇故障",
    "温度过高",
]

SEVERITY_PATTERNS = [
    ("严重和高", ["严重", "高"]),
    ("高和中", ["高", "中"]),
    ("严重和中", ["严重", "中"]),
    ("critical和major", ["critical", "major"]),
    ("严重和major", ["严重", "major"]),
]

REPORT_VARIANTS = [
    ("通过DataPart上报Incident数据", ["DataPart", "Incident"]),
    ("通过TextPart上报Incident通知", ["TextPart", "Incident"]),
    ("用DataPart承载Incident数据进行上报", ["DataPart", "Incident"]),
    ("按DataPart格式上报Incident消息", ["DataPart", "Incident"]),
]

REQUEST_PREFIXES = [
    "请帮我",
    "麻烦帮我",
    "需要你",
    "请",
    "帮我",
]

STYLE_BUILDERS = [
    lambda *, target, topic, condition, report, suffix: f"{REQUEST_PREFIXES[0]}订阅{target}的{topic}，{condition}，{report}{suffix}",
    lambda *, target, topic, condition, report, suffix: f"需要创建一个{target}{topic}订阅任务，{condition}，{report}{suffix}",
    lambda *, target, topic, condition, report, suffix: f"我们想接收{target}侧的{topic}通知，{condition}，{report}{suffix}",
    lambda *, target, topic, condition, report, suffix: f"帮我盯一下{target}这边的{topic}，{condition}，{report}{suffix}",
    lambda *, target, topic, condition, report, suffix: f"最近{target}故障偏多，请订阅相关{topic}，{condition}，{report}{suffix}",
]

PARTIAL_STYLE_BUILDERS = [
    lambda *, target, topic, detail, suffix: f"请订阅{target}的{topic}，{detail}{suffix}",
    lambda *, target, topic, detail, suffix: f"需要给{target}创建{topic}订阅，{detail}{suffix}",
    lambda *, target, topic, detail, suffix: f"我们想接收{target}相关的{topic}，{detail}{suffix}",
    lambda *, target, topic, detail, suffix: f"帮我配置{target}的{topic}订阅，{detail}{suffix}",
]

NEAR_INTENT_PATTERNS = [
    "请把下面这段{topic}订阅说明翻成英文：{detail}。",
    "解释一下如何订阅{target}的{topic}，重点说明{detail}。",
    "把这段{topic}订阅需求改写得更正式一些：{detail}。",
    "帮我总结这条{topic}订阅规则，不要生成任务请求：{detail}。",
    "检查下面这段{topic}订阅描述是否通顺：{detail}。",
]

NON_INCIDENT_SUBJECTS = [
    "性能事件",
    "状态变更通知",
    "日志消息",
    "KPI异常",
    "巡检结果",
    "工单通知",
    "链路质量事件",
    "容量预警",
    "能耗告警",
    "配置变更消息",
]

AMBIGUOUS_PATTERNS = [
    "帮我配个Incident规则。",
    "基站那边的Incident先关注一下。",
    "建一个incident订阅。",
    "Incident这块你先处理一下。",
    "把故障订阅配上。",
]

NOISE_SUFFIXES = [
    "，描述写正式一点。",
    "，按网管侧常用说法组织内容。",
    "，不要漏掉关键限制。",
    "，输出尽量简洁。",
    "，顺便帮我表述规范一点。",
]

COMPLETE_BATCH_NOTES = [
    "，这条给白班值守使用。",
    "，这条给夜间巡检使用。",
    "，这条用于现场运维订阅。",
    "，这条给区域监控席位使用。",
    "，这条先按当前站点范围生效。",
]

RISKY_BATCH_NOTES = [
    " 当前是日常值守场景。",
    " 当前是割接观察场景。",
    " 当前是告警复盘后的补订阅场景。",
    " 当前是区域集中监控场景。",
    " 当前是节假日保障场景。",
]


def alias_group(value: str) -> list[str]:
    mapping = {
        "Incident": ["Incident", "incident"],
        "DataPart": ["DataPart", "datapart"],
        "TextPart": ["TextPart", "textpart"],
        "严重": ["严重", "critical"],
        "高": ["高", "major"],
        "critical": ["critical", "严重"],
        "major": ["major", "高"],
        "中": ["中", "medium"],
    }
    return mapping.get(value, [value])


def pair_faults(index: int) -> list[str]:
    first = FAULTS[index % len(FAULTS)]
    second = FAULTS[(index + 3) % len(FAULTS)]
    if first == second:
        second = FAULTS[(index + 4) % len(FAULTS)]
    return [first, second]


def triple_faults(index: int) -> list[str]:
    values = [FAULTS[(index + offset) % len(FAULTS)] for offset in (0, 3, 6)]
    return list(dict.fromkeys(values))


def choose_topic(index: int) -> str:
    return TOPIC_VARIANTS[index % len(TOPIC_VARIANTS)]


def choose_target(index: int) -> str:
    return TARGETS[index % len(TARGETS)]


def choose_severity(index: int) -> tuple[str, list[str]]:
    return SEVERITY_PATTERNS[index % len(SEVERITY_PATTERNS)]


def choose_report(index: int) -> tuple[str, list[str]]:
    return REPORT_VARIANTS[index % len(REPORT_VARIANTS)]


def positive_prompt_groups(topic: str, severities: list[str], faults: list[str], report_terms: list[str] | None) -> list[list[str]]:
    groups: list[list[str]] = [alias_group("Incident")]
    groups.extend(alias_group(severity) for severity in severities)
    groups.extend(alias_group(fault) for fault in faults)
    if report_terms:
        groups.extend(alias_group(term) for term in report_terms)
    return groups


def build_case(
    *,
    case_id: str,
    input_text: str,
    scenario_description: str,
    should_recognize: bool,
    expected_slots: dict[str, list[list[str]]],
    allowed_missing_slots: list[str],
    failure_type: str | None,
    must_include_points: list[list[str]],
    preferred_keywords: list[str],
    must_not_include_points: list[str],
    case_type: str,
    semantic_variant: list[str],
    completeness_level: str,
) -> dict[str, object]:
    return {
        "id": case_id,
        "input": input_text,
        "scenario_description": scenario_description,
        "expected_result": {
            "should_recognize": should_recognize,
            "expected_scenario_code": "subscribe_incident" if should_recognize else None,
            "expected_slots": expected_slots,
            "allowed_missing_slots": allowed_missing_slots,
            "failure_type": failure_type,
        },
        "expected_prompt_effect": {
            "must_include_points": must_include_points,
            "preferred_keywords": preferred_keywords,
            "must_not_include_points": must_not_include_points,
        },
        "tags": {
            "case_type": case_type,
            "semantic_variant": semantic_variant,
            "completeness_level": completeness_level,
        },
    }


def build_positive_complete(index: int) -> dict[str, object]:
    target = choose_target(index)
    topic = choose_topic(index)
    faults = pair_faults(index)
    severity_text, severities = choose_severity(index)
    report_text, report_terms = choose_report(index)
    condition = f"关注{faults[0]}和{faults[1]}，级别限定为{severity_text}"
    suffix = NOISE_SUFFIXES[index % len(NOISE_SUFFIXES)] if index % 5 == 0 else "。"
    batch_note = COMPLETE_BATCH_NOTES[(index // len(TARGETS)) % len(COMPLETE_BATCH_NOTES)]
    input_text = STYLE_BUILDERS[index % len(STYLE_BUILDERS)](
        target=target,
        topic=topic,
        condition=condition,
        report=report_text,
        suffix=suffix,
    )
    input_text = input_text.rstrip("。") + batch_note
    expected_slots = {
        "通知主题": [alias_group("Incident")],
        "订阅条件": [alias_group(severity) for severity in severities] + [alias_group(fault) for fault in faults],
        "上报通知数据格式": [alias_group(term) for term in report_terms],
    }
    return build_case(
        case_id=f"si_pc_{index + 1:03d}",
        input_text=input_text,
        scenario_description="完整正样本，主题、故障名、级别和上报格式同时出现。",
        should_recognize=True,
        expected_slots=expected_slots,
        allowed_missing_slots=[],
        failure_type=None,
        must_include_points=positive_prompt_groups(topic, severities, faults, report_terms),
        preferred_keywords=["Incident", *faults, *report_terms],
        must_not_include_points=[],
        case_type="positive_complete",
        semantic_variant=[
            "topic_expression",
            "condition_fault_name",
            "condition_severity",
            "condition_combination",
            "report_format",
            "subscription_target_context",
        ],
        completeness_level="L3" if index % 3 else "L4",
    )


def build_positive_partial(index: int) -> dict[str, object]:
    target = choose_target(index)
    topic = choose_topic(index)
    faults = pair_faults(index)
    severity_text, severities = choose_severity(index)
    variant = index % 3
    if variant == 0:
        detail = "先只订阅这个主题，暂时不用限定级别和上报格式。"
        expected_condition: list[list[str]] = []
        completeness = "L1"
    elif variant == 1:
        detail = f"重点看{faults[0]}，其他限制先不加。"
        expected_condition = [alias_group(faults[0])]
        completeness = "L2"
    else:
        detail = f"只限制级别为{severity_text}，先不要指定上报格式。"
        expected_condition = [alias_group(severity) for severity in severities]
        completeness = "L2"
    suffix = NOISE_SUFFIXES[index % len(NOISE_SUFFIXES)] if index % 4 == 0 else ""
    input_text = PARTIAL_STYLE_BUILDERS[index % len(PARTIAL_STYLE_BUILDERS)](
        target=target,
        topic=topic,
        detail=detail,
        suffix=suffix,
    )
    expected_slots = {
        "通知主题": [alias_group("Incident")],
        "订阅条件": expected_condition,
        "上报通知数据格式": [],
    }
    semantic_variant = ["topic_expression", "subscription_target_context"]
    if expected_condition:
        semantic_variant.append("condition_fault_name" if variant == 1 else "condition_severity")
    return build_case(
        case_id=f"si_pp_{index + 1:03d}",
        input_text=input_text,
        scenario_description="正样本但信息不完整，测试可识别但较宽泛的输入。",
        should_recognize=True,
        expected_slots=expected_slots,
        allowed_missing_slots=["订阅条件", "上报通知数据格式"] if variant == 0 else ["上报通知数据格式"],
        failure_type=None,
        must_include_points=[alias_group("Incident"), *expected_condition],
        preferred_keywords=["Incident", *faults, *severities],
        must_not_include_points=[],
        case_type="positive_partial",
        semantic_variant=semantic_variant,
        completeness_level=completeness,
    )


def build_positive_risky(index: int) -> dict[str, object]:
    target = choose_target(index)
    topic = choose_topic(index)
    faults = triple_faults(index)
    severity_text, severities = choose_severity(index)
    report_text, report_terms = choose_report(index)
    background = (
        f"最近{target}在晚高峰时段波动明显，前面已经连续出现过{faults[0]}和{faults[1]}，"
        f"现在我想把{topic}订阅补上，不是所有故障都要，只看{severity_text}，"
        f"另外把{faults[2]}也纳入条件，{report_text}。"
    )
    if index % 2 == 0:
        background += " 如果描述里要带背景可以简短带一句，但不要改成分析任务。"
    else:
        background += " 输出保持成订阅请求，不要写成说明文。"
    background += RISKY_BATCH_NOTES[(index // len(TARGETS)) % len(RISKY_BATCH_NOTES)]
    expected_slots = {
        "通知主题": [alias_group("Incident")],
        "订阅条件": [alias_group(severity) for severity in severities] + [alias_group(fault) for fault in faults],
        "上报通知数据格式": [alias_group(term) for term in report_terms],
    }
    return build_case(
        case_id=f"si_pr_{index + 1:03d}",
        input_text=background,
        scenario_description="意图明确但句子较长且有噪声，容易识别成功但抽槽位不完整。",
        should_recognize=True,
        expected_slots=expected_slots,
        allowed_missing_slots=[],
        failure_type=None,
        must_include_points=positive_prompt_groups(topic, severities, faults, report_terms),
        preferred_keywords=["Incident", *faults, *report_terms],
        must_not_include_points=["分析任务", "说明文"],
        case_type="positive_recognized_but_slot_risky",
        semantic_variant=[
            "topic_expression",
            "condition_fault_name",
            "condition_severity",
            "condition_combination",
            "report_format",
            "subscription_target_context",
            "noise_interference",
        ],
        completeness_level="L4",
    )


def build_negative_near_intent(index: int) -> dict[str, object]:
    target = choose_target(index)
    topic = choose_topic(index)
    faults = pair_faults(index)
    severity_text, _ = choose_severity(index)
    detail = f"{target}的{topic}，关注{faults[0]}和{faults[1]}，级别是{severity_text}"
    pattern = NEAR_INTENT_PATTERNS[index % len(NEAR_INTENT_PATTERNS)]
    input_text = pattern.format(topic=topic, target=target, detail=detail)
    return build_case(
        case_id=f"si_nn_{index + 1:03d}",
        input_text=input_text,
        scenario_description="接近incident订阅，但主意图是翻译、解释、改写或总结，不应生成订阅prompt。",
        should_recognize=False,
        expected_slots={
            "通知主题": [],
            "订阅条件": [],
            "上报通知数据格式": [],
        },
        allowed_missing_slots=["通知主题", "订阅条件", "上报通知数据格式"],
        failure_type="near_intent_non_generation",
        must_include_points=[],
        preferred_keywords=["Incident", *faults],
        must_not_include_points=[],
        case_type="negative_near_intent",
        semantic_variant=["topic_expression", "condition_fault_name", "condition_severity", "instruction_purity"],
        completeness_level="L2",
    )


def build_negative_non_incident_subscription(index: int) -> dict[str, object]:
    target = choose_target(index)
    subject = NON_INCIDENT_SUBJECTS[index % len(NON_INCIDENT_SUBJECTS)]
    suffix = "，不要订阅Incident。"
    if index % 4 == 0:
        input_text = f"请订阅{target}的{subject}{suffix}"
    elif index % 4 == 1:
        input_text = f"需要给{target}配置{subject}订阅，重点看夜间波动，不要转成Incident任务。"
    elif index % 4 == 2:
        input_text = f"帮我订阅{target}的{subject}，按DataPart上报即可，但不是Incident。"
    else:
        input_text = f"我们想接收{target}的{subject}通知，别识别成Incident订阅。"
    return build_case(
        case_id=f"si_ni_{index + 1:03d}",
        input_text=input_text,
        scenario_description="订阅意图明确，但订阅对象不是incident，不应识别为subscribe_incident。",
        should_recognize=False,
        expected_slots={
            "通知主题": [],
            "订阅条件": [],
            "上报通知数据格式": [],
        },
        allowed_missing_slots=["通知主题", "订阅条件", "上报通知数据格式"],
        failure_type="non_incident_subscription",
        must_include_points=[],
        preferred_keywords=[subject],
        must_not_include_points=[],
        case_type="negative_non_incident_subscription",
        semantic_variant=["subscription_target_context", "instruction_purity"],
        completeness_level="L1",
    )


def build_negative_ambiguous(index: int) -> dict[str, object]:
    input_text = AMBIGUOUS_PATTERNS[index % len(AMBIGUOUS_PATTERNS)]
    if index % 3 == 0:
        input_text = input_text[:-1] + "，先处理一下。"
    elif index % 3 == 1:
        input_text = input_text[:-1] + "，具体你看着配。"
    return build_case(
        case_id=f"si_na_{index + 1:03d}",
        input_text=input_text,
        scenario_description="意图太短或边界模糊，测试模型是否会过度猜测成incident订阅。",
        should_recognize=False,
        expected_slots={
            "通知主题": [],
            "订阅条件": [],
            "上报通知数据格式": [],
        },
        allowed_missing_slots=["通知主题", "订阅条件", "上报通知数据格式"],
        failure_type="ambiguous_input",
        must_include_points=[],
        preferred_keywords=["Incident"],
        must_not_include_points=[],
        case_type="negative_ambiguous",
        semantic_variant=["topic_expression", "instruction_purity", "noise_interference"],
        completeness_level="L1",
    )


def build_cases() -> list[dict[str, object]]:
    cases: list[dict[str, object]] = []
    builders = {
        "positive_complete": build_positive_complete,
        "positive_partial": build_positive_partial,
        "positive_recognized_but_slot_risky": build_positive_risky,
        "negative_near_intent": build_negative_near_intent,
        "negative_non_incident_subscription": build_negative_non_incident_subscription,
        "negative_ambiguous": build_negative_ambiguous,
    }
    for case_type, count in CASE_TYPE_COUNTS.items():
        builder = builders[case_type]
        for index in range(count):
            cases.append(builder(index))

    inputs = [str(case["input"]) for case in cases]
    if len(inputs) != len(set(inputs)):
        raise ValueError("Generated duplicate case inputs; adjust the variation rules.")
    return cases


def render_markdown(cases: list[dict[str, object]]) -> str:
    lines = [
        "# Incident订阅评测用例",
        "",
        f"用例总数：{len(cases)}",
        "",
        "## 分布情况",
        "",
    ]
    counts = Counter(str(case["tags"]["case_type"]) for case in cases)
    for case_type in CASE_TYPE_COUNTS:
        lines.append(f"- `{CASE_TYPE_LABELS[case_type]}`：{counts[case_type]}")
    lines.extend(["", "## 用例预览", ""])

    for case_type in CASE_TYPE_COUNTS:
        lines.append(f"### {CASE_TYPE_LABELS[case_type]}")
        lines.append("")
        lines.append("| 用例ID | 完整度 | 输入 | 场景说明 |")
        lines.append("| --- | --- | --- | --- |")
        for case in cases:
            tags = case["tags"]
            if tags["case_type"] != case_type:
                continue
            case_id = str(case["id"])
            completeness = str(tags["completeness_level"])
            input_text = str(case["input"]).replace("|", "\\|")
            description = str(case["scenario_description"]).replace("|", "\\|")
            lines.append(f"| `{case_id}` | `{completeness}` | {input_text} | {description} |")
        lines.append("")
    return "\n".join(lines) + "\n"


def main() -> None:
    cases = build_cases()
    DATASET_PATH.parent.mkdir(parents=True, exist_ok=True)
    MARKDOWN_PATH.parent.mkdir(parents=True, exist_ok=True)
    DATASET_PATH.write_text(json.dumps(cases, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    MARKDOWN_PATH.write_text(render_markdown(cases), encoding="utf-8")
    print(f"已生成 {len(cases)} 条用例：{DATASET_PATH}")
    print(f"已生成用例预览文档：{MARKDOWN_PATH}")


if __name__ == "__main__":
    main()
