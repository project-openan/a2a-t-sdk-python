from __future__ import annotations

import json
from collections import Counter
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATASET_PATH = PROJECT_ROOT / "package_data" / "eval" / "energy_saving_cases.json"
MARKDOWN_PATH = PROJECT_ROOT / "docs" / "eval" / "energy_saving_cases.md"

CASE_TYPE_COUNTS = {
    "positive_complete": 24,
    "positive_partial": 16,
    "positive_recognized_but_slot_risky": 16,
    "negative_near_intent": 12,
    "negative_out_of_scope_request": 12,
    "negative_ambiguous": 10,
}

CASE_TYPE_LABELS = {
    "positive_complete": "正样本-信息完整",
    "positive_partial": "正样本-信息不完整",
    "positive_recognized_but_slot_risky": "正样本-高风险槽位提取",
    "negative_near_intent": "负样本-近邻意图",
    "negative_out_of_scope_request": "负样本-列表外任务",
    "negative_ambiguous": "负样本-意图模糊",
}

AREAS = [
    "松山湖管委会",
    "福田保税区",
    "南山科技园北区",
    "前海桂湾片区",
    "龙岗大运中心周边",
    "宝安机场东片区",
    "苏州工业园区金鸡湖片区",
    "杭州未来科技城核心区",
    "武汉光谷软件园",
    "成都天府三街商务区",
]

COORDINATE_AREAS = [
    "坐标(112.91, 22.92)周边区域",
    "坐标(113.95, 22.54)周边区域",
    "坐标(120.74, 31.30)周边区域",
    "坐标(104.07, 30.57)周边区域",
]

BACKGROUND_CONTEXTS = [
    "最近夜间话务长期偏低，但白天业务量波动较大",
    "节假日零点后的负荷下降比较明显，存在节能空间",
    "近期能耗考核压力较大，需要先做一轮区域节能优化",
    "过去两周低谷期资源利用率持续偏低，希望减少空耗",
    "该区域白天用户感知要求高，夜间则可以适度节能",
    "当前片区正在做降本专项，优先处理夜间低负荷小区",
]

SCENE_NOTES = [
    "商业区场景禁止1300频点做容量层",
    "居民区场景22:00后不要关闭基础覆盖层",
    "铁路场景不要启用深度休眠",
    "景区场景在节假日晚高峰前后不要做激进切换",
    "校园区场景考试周期间保持体验无损",
    "黑森林场景00:00-06:00不要开通休眠",
]

OUT_OF_SCOPE_TASKS = [
    "容量扩容建议",
    "KPI异常分析",
    "日志消息订阅",
    "巡检工单生成",
    "参数一致性检查",
    "配置变更公告订阅",
    "设备资产盘点",
    "告警聚类分析",
]

REQUEST_PREFIXES = [
    "请帮我",
    "麻烦你",
    "需要你",
    "请",
    "帮我",
]

COMPLETE_STYLE_BUILDERS = [
    lambda *, goal, target, context, constraints, suffix: (
        f"{REQUEST_PREFIXES[0]}给{target}创建一个节能任务，目标是{goal}。"
        f"背景是：{context}。约束：{constraints}。{suffix}"
    ),
    lambda *, goal, target, context, constraints, suffix: (
        f"需要你为{target}生成无线能效优化请求，要求{goal}，"
        f"当前情况是{context}，并满足这些限制：{constraints}。{suffix}"
    ),
    lambda *, goal, target, context, constraints, suffix: (
        f"帮我给{target}出一个节能方案，{goal}。{context}。"
        f"执行时请遵守：{constraints}。{suffix}"
    ),
    lambda *, goal, target, context, constraints, suffix: (
        f"{target}这边想做一条节能任务，目标定为{goal}，"
        f"补充背景：{context}；约束条件包括{constraints}。{suffix}"
    ),
]

PARTIAL_STYLE_BUILDERS = [
    lambda *, target, detail, suffix: f"请给{target}做节能任务，{detail}。{suffix}",
    lambda *, target, detail, suffix: f"需要为{target}生成能效优化请求，{detail}。{suffix}",
    lambda *, target, detail, suffix: f"帮我针对{target}做节能，{detail}。{suffix}",
    lambda *, target, detail, suffix: f"{target}这边想做节能优化，{detail}。{suffix}",
]

NEAR_INTENT_PATTERNS = [
    "把下面这段节能任务描述翻译成英文，不要生成任务：{detail}。",
    "帮我把这段节能请求润色得正式一点，不要创建任务：{detail}。",
    "解释一下这段节能要求是什么意思，重点讲约束条件：{detail}。",
    "总结这段节能需求的关键信息，不要输出任务：{detail}。",
]

AMBIGUOUS_PATTERNS = [
    "先做个节能方案看看。",
    "这个片区省点电。",
    "晚上低谷期优化一下能耗。",
    "给我搞个节能任务。",
    "这边先节能一下。",
]

NOISE_SUFFIXES = [
    "描述写正式一点",
    "按网管任务口径组织内容",
    "不要漏掉关键限制",
    "输出尽量简洁",
    "顺手把表述规范一下",
]


def alias_group(value: str) -> list[str]:
    mapping = {
        "体验无损": ["体验无损", "无损体验"],
        "允许关闭": ["允许关闭", "可关断"],
        "不可关断": ["不可关断", "不允许关闭", "不可关闭"],
        "全部": ["全部", "所有"],
        "总功耗降低": ["总功耗降低", "能耗降低"],
        "下行吞吐率不低于": ["下行吞吐率不低于", "速率保障不低于"],
    }
    return mapping.get(value, [value])


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
            "expected_scenario_code": "energy_saving" if should_recognize else None,
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


def choose_target(index: int) -> str:
    if index % 5 == 4:
        return COORDINATE_AREAS[(index // 5) % len(COORDINATE_AREAS)]
    return AREAS[index % len(AREAS)]


def choose_goal(index: int) -> tuple[str, list[list[str]]]:
    energy_pct = 18 + (index % 6) * 4
    speed = 40 + (index % 5) * 5
    text = f"总功耗降低{energy_pct}%，下行吞吐率不低于{speed}Mbps"
    groups = [
        [f"总功耗降低{energy_pct}%", f"能耗降低{energy_pct}%"],
        [f"下行吞吐率不低于{speed}Mbps", f"速率保障不低于{speed}Mbps"],
    ]
    return text, groups


def choose_context(index: int) -> tuple[str, list[list[str]]]:
    text = BACKGROUND_CONTEXTS[index % len(BACKGROUND_CONTEXTS)]
    if "节假日" in text:
        group = ["节假日", "零点后"]
    elif "能耗考核" in text:
        group = ["能耗考核", "节能优化"]
    elif "资源利用率" in text:
        group = ["资源利用率", "空耗"]
    elif "用户感知" in text:
        group = ["用户感知", "夜间"]
    elif "降本" in text:
        group = ["降本", "夜间低负荷"]
    elif "夜间" in text:
        group = ["夜间", "低谷期"]
    else:
        group = [text]
    return text, [group]


def choose_constraints(index: int) -> tuple[str, list[list[str]]]:
    cell_type = ["室外", "室内", "所有"][index % 3]
    rat = ["NR", "LTE", "全部"][index % 3]
    base_freq = str(1300 + (index % 4) * 175)
    capacity_freq = str(2450 + (index % 4) * 120)
    aggressiveness = ["激进节能", "保守节能"][index % 2]
    lossless = ["体验无损", "允许轻微波动但不要明显影响体验"][index % 2]
    start_hour = 21 + (index % 3)
    end_hour = 5 + (index % 2)
    scene_note = SCENE_NOTES[index % len(SCENE_NOTES)]
    carrier_rule = ["允许关闭1650频点载波", "1650频点不可关断"][index % 2]
    if cell_type == "所有" and rat == "全部":
        object_phrase = "针对全量小区"
    elif cell_type == "所有":
        object_phrase = f"仅针对{rat}小区"
    elif rat == "全部":
        object_phrase = f"仅针对{cell_type}小区"
    else:
        object_phrase = f"仅针对{cell_type}{rat}小区"

    constraint_text = (
        f"{object_phrase}，{base_freq}频点作为基础层，{capacity_freq}频点作为容量层，"
        f"采用{aggressiveness}，{lossless}，执行时间为每天{start_hour:02d}:00:00至{end_hour:02d}:00:00，"
        f"{scene_note}，{carrier_rule}"
    )
    groups = [
        alias_group(cell_type),
        alias_group(rat),
        [base_freq],
        [capacity_freq],
        [aggressiveness],
        alias_group("体验无损") if lossless == "体验无损" else ["轻微波动", "不要明显影响体验"],
        [f"{start_hour:02d}:00:00"],
        [f"{end_hour:02d}:00:00"],
        [scene_note.split("场景")[0]],
        alias_group("允许关闭") if "允许关闭" in carrier_rule else alias_group("不可关断"),
    ]
    return constraint_text, groups


def flatten_slot_groups(expected_slots: dict[str, list[list[str]]]) -> list[list[str]]:
    groups: list[list[str]] = []
    for slot_name in ("任务目标", "任务对象", "任务上下文", "约束条件"):
        groups.extend(expected_slots.get(slot_name, []))
    return groups


def build_positive_complete(index: int) -> dict[str, object]:
    target = choose_target(index)
    goal_text, goal_groups = choose_goal(index)
    context_text, context_groups = choose_context(index)
    constraint_text, constraint_groups = choose_constraints(index)
    suffix = NOISE_SUFFIXES[index % len(NOISE_SUFFIXES)]
    input_text = COMPLETE_STYLE_BUILDERS[index % len(COMPLETE_STYLE_BUILDERS)](
        goal=goal_text,
        target=target,
        context=context_text,
        constraints=constraint_text,
        suffix=suffix,
    )
    expected_slots = {
        "任务目标": goal_groups,
        "任务对象": [[target]],
        "任务上下文": context_groups,
        "约束条件": constraint_groups,
    }
    return build_case(
        case_id=f"es_pc_{index + 1:03d}",
        input_text=input_text,
        scenario_description="完整正样本，节能目标、区域对象、背景和约束条件同时出现。",
        should_recognize=True,
        expected_slots=expected_slots,
        allowed_missing_slots=[],
        failure_type=None,
        must_include_points=flatten_slot_groups(expected_slots),
        preferred_keywords=[target, "节能", "能效优化"],
        must_not_include_points=[],
        case_type="positive_complete",
        semantic_variant=[
            "goal_expression",
            "object_expression",
            "context_background",
            "constraint_cell_type",
            "constraint_rat",
            "constraint_frequency",
            "constraint_time_window",
            "constraint_scene_specific",
            "constraint_carrier_shutdown",
        ],
        completeness_level="L4",
    )


def build_positive_partial(index: int) -> dict[str, object]:
    target = choose_target(index)
    goal_text, goal_groups = choose_goal(index)
    context_text, context_groups = choose_context(index)
    constraint_text, constraint_groups = choose_constraints(index)
    variant = index % 4

    if variant == 0:
        detail = "先按这个区域做节能优化，目标和细约束你先按常规节能任务组织"
        expected_slots = {
            "任务目标": [],
            "任务对象": [[target]],
            "任务上下文": [],
            "约束条件": [],
        }
        allowed_missing = ["任务目标", "任务上下文", "约束条件"]
        semantic_variant = ["object_expression"]
        completeness = "L1"
    elif variant == 1:
        detail = f"目标是{goal_text}，其他限制先不补"
        expected_slots = {
            "任务目标": goal_groups,
            "任务对象": [[target]],
            "任务上下文": [],
            "约束条件": [],
        }
        allowed_missing = ["任务上下文", "约束条件"]
        semantic_variant = ["goal_expression", "object_expression"]
        completeness = "L2"
    elif variant == 2:
        detail = f"背景是{context_text}，执行约束先按{constraint_text}"
        expected_slots = {
            "任务目标": [],
            "任务对象": [[target]],
            "任务上下文": context_groups,
            "约束条件": constraint_groups[:4],
        }
        allowed_missing = ["任务目标"]
        semantic_variant = [
            "object_expression",
            "context_background",
            "constraint_cell_type",
            "constraint_rat",
            "constraint_frequency",
        ]
        completeness = "L2"
    else:
        detail = f"目标先定为{goal_text}，背景是{context_text}"
        expected_slots = {
            "任务目标": goal_groups,
            "任务对象": [[target]],
            "任务上下文": context_groups,
            "约束条件": [],
        }
        allowed_missing = ["约束条件"]
        semantic_variant = ["goal_expression", "object_expression", "context_background"]
        completeness = "L3"

    suffix = NOISE_SUFFIXES[index % len(NOISE_SUFFIXES)]
    input_text = PARTIAL_STYLE_BUILDERS[index % len(PARTIAL_STYLE_BUILDERS)](
        target=target,
        detail=detail,
        suffix=suffix,
    )
    return build_case(
        case_id=f"es_pp_{index + 1:03d}",
        input_text=input_text,
        scenario_description="正样本但信息不完整，测试场景可识别且缺省槽位可接受。",
        should_recognize=True,
        expected_slots=expected_slots,
        allowed_missing_slots=allowed_missing,
        failure_type=None,
        must_include_points=flatten_slot_groups(expected_slots),
        preferred_keywords=[target, "节能"],
        must_not_include_points=[],
        case_type="positive_partial",
        semantic_variant=semantic_variant,
        completeness_level=completeness,
    )


def build_positive_risky(index: int) -> dict[str, object]:
    target = choose_target(index)
    goal_text, goal_groups = choose_goal(index)
    context_text, context_groups = choose_context(index)
    constraint_text, constraint_groups = choose_constraints(index)
    extra_context = BACKGROUND_CONTEXTS[(index + 2) % len(BACKGROUND_CONTEXTS)]
    input_text = (
        f"{target}最近准备做一轮节能优化，主目标还是{goal_text}。"
        f"已知情况是：{context_text}；另外补充一点，{extra_context}。"
        f"执行时请按这些限制处理：{constraint_text}。"
        f"如果需要保留背景可以简要带一句，但不要改写成分析报告，{NOISE_SUFFIXES[index % len(NOISE_SUFFIXES)]}。"
    )
    expected_slots = {
        "任务目标": goal_groups,
        "任务对象": [[target]],
        "任务上下文": context_groups + [[extra_context.split("，")[0]]],
        "约束条件": constraint_groups,
    }
    return build_case(
        case_id=f"es_pr_{index + 1:03d}",
        input_text=input_text,
        scenario_description="意图明确但句子较长且含噪声，容易识别成功但槽位提取不完整。",
        should_recognize=True,
        expected_slots=expected_slots,
        allowed_missing_slots=[],
        failure_type=None,
        must_include_points=flatten_slot_groups(expected_slots),
        preferred_keywords=[target, "节能优化", "约束条件"],
        must_not_include_points=["翻译", "润色"],
        case_type="positive_recognized_but_slot_risky",
        semantic_variant=[
            "goal_expression",
            "object_expression",
            "context_background",
            "constraint_cell_type",
            "constraint_rat",
            "constraint_frequency",
            "constraint_time_window",
            "constraint_scene_specific",
            "constraint_carrier_shutdown",
            "noise_interference",
        ],
        completeness_level="L4",
    )


def build_negative_near_intent(index: int) -> dict[str, object]:
    target = choose_target(index)
    goal_text, _ = choose_goal(index)
    context_text, _ = choose_context(index)
    constraint_text, _ = choose_constraints(index)
    detail = f"{target}做节能，目标是{goal_text}，背景是{context_text}，约束是{constraint_text}"
    input_text = NEAR_INTENT_PATTERNS[index % len(NEAR_INTENT_PATTERNS)].format(detail=detail)
    return build_case(
        case_id=f"es_nn_{index + 1:03d}",
        input_text=input_text,
        scenario_description="接近节能任务，但主意图是翻译、润色、解释或总结，不应生成任务。",
        should_recognize=False,
        expected_slots={
            "任务目标": [],
            "任务对象": [],
            "任务上下文": [],
            "约束条件": [],
        },
        allowed_missing_slots=["任务目标", "任务对象", "任务上下文", "约束条件"],
        failure_type="near_intent_non_generation",
        must_include_points=[],
        preferred_keywords=[target, "节能"],
        must_not_include_points=[],
        case_type="negative_near_intent",
        semantic_variant=["instruction_purity", "goal_expression", "object_expression"],
        completeness_level="L2",
    )


def build_negative_out_of_scope_request(index: int) -> dict[str, object]:
    target = choose_target(index)
    task_name = OUT_OF_SCOPE_TASKS[index % len(OUT_OF_SCOPE_TASKS)]
    if index % 4 == 0:
        input_text = f"请给{target}创建一个{task_name}任务，这不是incident订阅、节能任务或专线投诉诊断。"
    elif index % 4 == 1:
        input_text = f"需要为{target}输出{task_name}请求，重点关注晚高峰问题，这不属于当前场景列表里的任务。"
    elif index % 4 == 2:
        input_text = f"帮我处理{target}的{task_name}，按网管任务格式组织，但不要识别成incident订阅、节能任务或专线投诉诊断。"
    else:
        input_text = f"{target}这边想做{task_name}，如果涉及优化也不是能效优化场景。"
    return build_case(
        case_id=f"es_no_{index + 1:03d}",
        input_text=input_text,
        scenario_description="任务创建意图明确，但任务本身不在当前支持场景列表中，应拒答而不是强行映射。",
        should_recognize=False,
        expected_slots={
            "任务目标": [],
            "任务对象": [],
            "任务上下文": [],
            "约束条件": [],
        },
        allowed_missing_slots=["任务目标", "任务对象", "任务上下文", "约束条件"],
        failure_type="out_of_scope_request",
        must_include_points=[],
        preferred_keywords=[task_name],
        must_not_include_points=[],
        case_type="negative_out_of_scope_request",
        semantic_variant=["instruction_purity", "object_expression"],
        completeness_level="L1",
    )


def build_negative_ambiguous(index: int) -> dict[str, object]:
    input_text = AMBIGUOUS_PATTERNS[index % len(AMBIGUOUS_PATTERNS)]
    if index % 3 == 0:
        input_text = input_text[:-1] + "，你先看着办。"
    elif index % 3 == 1:
        input_text = input_text[:-1] + "，具体条件后面再说。"
    return build_case(
        case_id=f"es_na_{index + 1:03d}",
        input_text=input_text,
        scenario_description="出现节能相关词，但没有明确对象或任务边界，测试是否会过度猜测为节能任务。",
        should_recognize=False,
        expected_slots={
            "任务目标": [],
            "任务对象": [],
            "任务上下文": [],
            "约束条件": [],
        },
        allowed_missing_slots=["任务目标", "任务对象", "任务上下文", "约束条件"],
        failure_type="ambiguous_input",
        must_include_points=[],
        preferred_keywords=["节能"],
        must_not_include_points=[],
        case_type="negative_ambiguous",
        semantic_variant=["instruction_purity", "noise_interference"],
        completeness_level="L1",
    )


def build_cases() -> list[dict[str, object]]:
    cases: list[dict[str, object]] = []
    builders = {
        "positive_complete": build_positive_complete,
        "positive_partial": build_positive_partial,
        "positive_recognized_but_slot_risky": build_positive_risky,
        "negative_near_intent": build_negative_near_intent,
        "negative_out_of_scope_request": build_negative_out_of_scope_request,
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
        "# Energy Saving 评测用例",
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
    print(f"已生成 {len(cases)} 条 energy_saving 用例：{DATASET_PATH}")
    print(f"已生成用例预览文档：{MARKDOWN_PATH}")


if __name__ == "__main__":
    main()
