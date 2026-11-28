"""Message catalog of the tester console's server-side prose.

The tester page follows the process language (``--language`` / ``A2AT_LANGUAGE``).  This module
owns every runtime sentence the tester generates itself: stage explanations, verdict reasons and
API error bodies.  Corpus text, wire/trace evidence and protocol/runtime errors are *not* here:
they stay in their original language (D10).

The catalog is immutable and constructed once; callers pick a language through
:class:`TesterMessages`.  A batch keeps the language it was created in and reads its own messages,
so a batch opened under another process language is not re-rendered in a mixed state (D9).
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Final

from shared.run_constants import LANGUAGE_EN, LANGUAGE_ZH

_LOGGER = logging.getLogger("ran_energy_saving.tester.messages")

#: Every message key in both languages.  Both mappings must expose the same key set: the tests
#: enforce it, because a missing translation would silently fall back to the key name.
MESSAGE_CATALOG: Final[Mapping[str, Mapping[str, str]]] = {
    LANGUAGE_ZH: {
        # API errors
        "unknown_batch": "未知测试批次。",
        "unknown_case": "未知用例。",
        "batch_not_running": "该批次不在执行中。",
        "invalid_item_index": "用例下标无效。",
        "select_protocol": "至少选择一个协议。",
        "invalid_langfuse_columns": "langfuseColumns 只接受 auto、0 或 1。",
        "invalid_json_body": "请求体不是合法 JSON。",
        "json_body_not_object": "请求体必须是 JSON 对象。",
        "list_must_be_array": "{name} 必须是数组。",
        "list_must_be_int": "{name} 只能包含整数。",
        "list_must_not_be_empty": "{name} 不能为空。",
        "invalid_number": "数值参数格式不正确。",
        "concurrency_must_be_int": "并发必须是整数。",
        "concurrency_out_of_range": "并发必须在 {min} 到 {max} 之间。",
        "rounds_must_be_int": "轮次必须是整数。",
        "rounds_out_of_range": "轮次必须在 {min} 到 {max} 之间。",
        # Batch lifecycle errors
        "batch_already_run": "该批次已经执行过。",
        "batch_still_running": "该批次仍在执行中。",
        "no_failed_items": "没有失败、错误或取消的用例可以重跑。",
        "multi_round_keep_tasks": "多轮批次不支持保留任务。",
        "pure_no_sdk_stage": (
            "A2A 协议没有 SDK 阶段；请把执行到设为 send / negotiate / accepted / working / final。"
        ),
        "no_runnable_cases": "没有可执行的用例。",
        "scratch_empty": "临时请求不能为空。",
        "principles_load_failed": "无法加载通用协商原则：{error}",
        "persist_failed": "批次持久化失败：{error}",
        "batch_cancelled": "批次已取消。",
        "restart_incomplete": "UI 进程已重启，批次未执行完。",
        # Item lifecycle errors
        "item_already_finished": "该用例已经结束。",
        "item_still_running": "该用例还在执行中，不能重跑。",
        "item_not_run_yet": "该用例尚未执行，不能重跑。",
        "item_already_queued": "该用例已经在重跑队列中。",
        "unknown_case_id": "未知用例 {case_id}",
        "item_cancelled": "已取消。",
        # Code-generated item name
        "scratch_item_name": "临时请求",
        # Run level explanations
        "run_failed": "运行失败。",
        "scratch_timeout": "临时请求超时。",
        "scratch_recorded": "临时请求只记录结果。",
        "modify_baseline_create_failed": "Modify 基线任务创建失败。",
        "validate_no_evidence": "run 未产生校验证据。",
        # Stage messages
        "acceptance_unproven": (
            "NMS 只上报了 TASK_STATE_SUBMITTED，未按契约附带 “Task creation succeeded” 报文或结构化意图"
            "（网管侧缺陷）；受理尚未确认，继续观察。"
        ),
        "assertion_off": "用例关闭了该断言。",
        "negotiated_task_t": "期望 Task-T 是协商后的结果；SDK runner 只看到初始请求，因此只记录。",
        "negotiate_failed": "协商类型或报文与预期不一致。",
        "negotiate_rounds_warning": (
            "协商轮次与用例预期不同（用例记录的轮次仅作参考，类型与报文一致）；请人工确认结果。"
        ),
        "final_state_unpushed": "NMS 已确认任务成功，但未推送终态；无法观察最终结果，请人工确认。",
        "no_server_intent": "下游的 TASK_STATE_SUBMITTED 报文没有结构化意图，意图阶段只记录。",
        "a2a_proxy_server_intent": "A2A 非直连厂商链路返回的意图只记录，不据此判定。",
        "no_expected_intent": "用例没有期望意图，只记录。",
        "server_intent_mismatch": "服务端意图理解结果与期望不一致。",
        "intent_unjudged": "未判定，转人工复核。",
        "task_t_mismatch": "Task-T 与期望不一致（告警，不作为用例失败依据）。",
        "validation_mismatch": "拦截结论与期望不一致。",
        "no_expected_validation": "用例没有期望拦截结论，只记录。",
        "send_mismatch": "预期发送状态与观察结果不一致。",
        "final_mismatch": "最终状态与预期不一致。",
        "expected_no_send": "用例期望不发送",
        "expected_working_or_completed": "WORKING 或 COMPLETED",
        "run_ended_before_send": "run 在发送前结束：{final_state}",
        "task_state_is": "Task 状态为 {state}。",
        "run_finished_without_task": "run 已结束但没有 Task。",
        "wait_task_timeout": "等待 Task 超时。",
        "task_negotiating_input_required": "Task 仍在协商（INPUT_REQUIRED），尚未确认受理。",
        "no_acceptance_observed": "未观察到受理状态。",
        "expected_task_id": "非空",
        "expected_context_id": "非空",
        "expected_accepted_state": "已受理",
        "refused_completion": "Task 以 TASK_STATE_COMPLETED 结束，但 run 终态为 {final_state}。",
        "task_stuck_submitted": "{seconds}s 内 Task 仍停留在 SUBMITTED。",
        "task_negotiating": "Task 仍在协商（INPUT_REQUIRED）。",
        "completed_via_final_state": "通过终态 task_completed 观察到完成。",
        "run_final_state_is": "run 终态为 {final_state}。",
        "no_working_observed": "{seconds}s 内未观察到 WORKING。",
        "no_working_observed_short": "未观察到 WORKING。",
        # Follow-up / interception
        "followup_still_blocked": (
            "补充请求（blocked_followup.input）仍被拦截：原始请求被拦截是预期行为，补充请求必须通过校验。"
        ),
        "followup_not_sent": (
            "补充请求（blocked_followup.input）未发出：run 在发送前结束（{final_state}）；"
            "原始请求被拦截是预期行为。"
        ),
        "interception_missed": (
            "原请求未被拦截（{detail}），未启动 run，补充请求（blocked_followup.input）未执行；按记录处理。"
        ),
        "interception_miss_dispatched": (
            "原请求未被拦截（{detail}），已改按原请求下发；补充请求（blocked_followup.input）未执行；按记录处理。"
        ),
        "interception_detail": "实际校验结论：{actual}",
        "no_validation_conclusion": "没有校验结论",
        "followup_detail": "{message} 原因：{reason}",
        "followup_error_detail": "{message} 详情：{error}",
        # Modify baselines
        "baseline_modify_input_required": (
            "Modify 基线 Create 触发了协商（TASK_STATE_INPUT_REQUIRED），不是可续传状态；"
            "请检查 baseline.request：必须显式使用 A2ATestRegion，并采用手工验收那类"
            "不拦截、不协商的三段日计划。"
        ),
        "baseline_modify_unproven": (
            "Modify 基线 Create 未被确认受理：网管只上报 TASK_STATE_SUBMITTED、未按契约附带 "
            "“Task creation succeeded” 报文或结构化意图（网管侧缺陷）；任务若随后被取消，"
            "Modify 必然失败。"
        ),
        "baseline_modify_not_continuable": (
            "Modify 基线任务未到达可续传状态（当前 {state}）；任务已是终态时服务端会拒绝 Modify。"
        ),
        "baseline_pure_not_working": (
            "Modify 基线任务的纯 A2A 外层 Task 未停留在 WORKING 或下游绑定未建立"
            "（当前 {state}）；外层 WORKING 只代表 OSS Agent 已受理，"
            "下游 Create 仍可能失败（例如 Running task limit）。纯 A2A 只能续传真实存在且非终态的 "
            "Task，请清空 NMS 任务池后重试。"
        ),
        "baseline_state_unknown": "未观察到状态",
        # Verdict reason connectors
        "verdict_recorded": "{stage}: 只记录，未判定",
        "verdict_stage": "{stage}: {message}",
        "verdict_separator": "；",
        "verdict_needs_review": "转人工复核{suffix}",
        "verdict_needs_review_base": "转人工复核",
        "verdict_review_paths": "（{paths}）",
        # Export: worksheet names and headers
        "export_sheet_arbitration": "仲裁格式",
        "export_sheet_results": "用例结果",
        "export_sheet_stage_diffs": "阶段差异",
        "export_sheet_summary": "汇总",
        "export_sheet_wire": "交互报文",
        "export_sheet_rounds": "轮次汇总",
        "export_sheet_plan": "批次计划",
        "export_sheet_client_quality": "client 决策质量",
        "export_sheet_client_replies": "客户端答复参考",
        "export_header_trace_id": "traceId",
        "export_header_protocol": "协议",
        "export_header_case_id": "用例编号",
        "export_header_case_name": "用例名称",
        "export_header_task_type": "任务类型",
        "export_header_qa_id": "QA_ID",
        "export_header_uid": "UID",
        "export_header_designer": "设计人",
        "export_header_issue": "问题",
        "export_header_raw_request": "原始请求",
        "export_header_normalized_request": "标准化请求",
        "export_header_server_intent": "意图理解结果(结构化JSON)",
        "export_header_sdk_intent": "SDK抽取意图",
        "export_header_intent_source": "意图来源",
        "export_header_expected_intent": "期望意图",
        "export_header_intent_diff": "answer与reference差异点",
        "export_header_model_answer": "被测模型生成答案",
        "export_header_interaction": "交互报文",
        "export_header_trajectory": "仲裁人-EVAL-额外结果-执行轨迹",
        "export_header_task_state": "仲裁人-EVAL-额外结果-任务状态",
        "export_header_end_to_end": "仲裁人-EVAL-额外结果-端到端时延(ms)",
        "export_header_negotiation_rounds": "协商轮次",
        "export_header_negotiation_triggered": "协商触发",
        "export_header_negotiation_succeeded": "协商成功",
        "export_header_output_tokens": "仲裁人-EVAL-额外结果-输出token",
        "export_header_total_tokens": "仲裁人-EVAL-额外结果-总token",
        "export_header_execution_status": "执行状态",
        "export_header_auto_score": "自动评分",
        "export_header_final_score": "最终得分",
        "export_header_summary_result": "汇总结果",
        "export_header_verdict_reason": "判定原因",
        "export_header_arbitration_score": "仲裁得分",
        "export_header_manual_mark": "人工标记结果",
        "export_header_notes": "备注",
        "export_header_duration_ms": "耗时(ms)",
        "export_header_total_token_count": "Token 总量",
        "export_header_run_id": "Run ID",
        "export_header_baseline_status": "基线状态",
        "export_header_baseline_run_id": "基线 Run ID",
        "export_header_baseline_task_id": "基线 taskId",
        "export_header_baseline_state": "基线 state",
        "export_header_baseline_error": "基线错误",
        "export_header_transport": "传输",
        "export_header_discovery": "发现",
        "export_header_interception_validation": "拦截校验",
        "export_header_interception_followup": "拦截后补充信息",
        "export_header_stage_status": "{stage} 状态",
        "export_header_error": "错误信息",
        "export_header_round": "轮次",
        "export_header_stage": "阶段",
        "export_header_status": "状态",
        "export_header_field": "字段",
        "export_header_expected": "预期",
        "export_header_actual": "实际",
        "export_header_detail": "说明",
        "export_header_metric": "指标",
        "export_header_value": "值",
        "export_header_item": "项目",
        "export_header_frame": "帧号",
        "export_header_segment": "分段",
        "export_header_total": "总数",
        "export_header_passed": "通过",
        "export_header_pending": "待确认",
        "export_header_failed": "失败",
        "export_header_error_count": "错误",
        "export_header_cancelled": "取消",
        "export_header_stable_passes": "稳定通过",
        "export_header_comparable_cases": "可比用例",
        "export_header_negotiation_round": "协商轮次",
        "export_header_decision": "决策",
        "export_header_proposal": "提案",
        "export_header_quality_expected": "期望",
        "export_header_action": "动作",
        "export_header_structure": "结构",
        "export_header_review": "复核",
        "export_header_coverage": "覆盖",
        "export_header_quality": "质量",
        "export_header_human_review": "人工复核",
        "export_header_method": "判定方式",
        "export_header_reason": "理由",
        "export_header_recorded_replies": "记录答复",
        "export_plan_environment": "环境",
        "export_plan_negotiation_model": "协商模型",
        "export_plan_judge_model": "判定模型",
        "export_plan_principles_version": "协商原则版本",
        "export_plan_nms_version": "NMS 版本",
        "export_plan_stop_after": "执行到",
        "export_plan_rounds": "轮次",
        "export_round_column": "第 {round} 轮",
        # Export: notes and prose
        "export_yes": "是",
        "export_no": "否",
        "export_no_run_note": "本用例未启动 run。",
        "export_no_runs_dir_note": "导出时未提供 run 目录，无法读取交互报文。",
        "export_no_wire_note": "run {run_id} 无 wire 报文（未开启报文记录或文件已清理）。",
        "export_wire_header": (
            "run {run_id} | {frames} 帧 | wire.jsonl / wire-oss.jsonl / wire-error.jsonl"
        ),
        "export_wire_truncated_note": "…（已截断，完整逐帧报文见「交互报文」工作表）",
        "export_trajectory_truncated_note": "…（轨迹已截断，完整报文见「交互报文」列）",
        "export_no_answer_note": "未执行发送阶段",
        "export_no_server_intent_note": "下游未返回结构化意图",
        "export_no_server_intent_pure_note": "纯 A2A 未采集服务端意图",
        "export_no_intent_note": "本批次未采集意图",
        "export_rebuilt_trace_note": "traceparent 为重建值",
        "export_baseline_failed": "Modify 基线 Create 失败：{detail}",
        "export_baseline_failed_no_reason": "未记录原因",
        "export_scratch_item": "临时请求",
        "export_no_case_metadata": "本批次未采集用例元数据",
        "export_sdk_intent_missing": "SDK 抽取意图未采集",
        "export_langfuse_warning": "Langfuse 导出失败：{warning}",
        "export_note_separator": "；",
        "export_followup_skipped": "（未执行）{text}",
        "export_interception_stage_prefix": "拦截/",
        "export_cell_truncated": "…（已截断）",
        "export_label_passed": "通过",
        "export_label_failed": "失败",
        "export_label_partial": "待定",
        "export_label_error": "错误",
        "export_label_cancelled": "取消",
        # Export: intent diff and answer steps
        "export_diff_identical": "完全一致",
        "export_diff_missing": "缺失: {path} (ref值为 {value})",
        "export_diff_added": "新增: {path} (ans值为 {value})",
        "export_diff_changed": "值变更: {path} ({expected} → {actual})",
        "export_diff_expression": (
            "表达差异（归一化后一致）: {path} ({expected} → {actual})"
        ),
        "export_step_outbound": "发送{message_type}请求",
        "export_step_inbound": "接收{message_type}",
        "export_sse_blank_line": "(空行)",
        # Langfuse cells
        "langfuse_header_input_tokens": "输入token",
        "langfuse_header_cached_input_tokens": "输入缓存token",
        "langfuse_header_output_tokens": "输出token",
        "langfuse_header_total_tokens": "总token",
        "langfuse_header_negotiation_triggered": "是否触发协商",
        "langfuse_header_negotiation_latency": "协商时延(ms)",
        "langfuse_header_negotiation_rounds": "协商轮次",
        "langfuse_header_end_to_end_latency": "端到端时延(ms)",
        "langfuse_header_client_interceptions": "客户端拦截次数",
        "langfuse_note_input_tokens": "Langfuse tokens.input（Gross：含缓存；含 a2a-t-server 侧用量）",
        "langfuse_note_cached_input_tokens": "Langfuse tokens.cachedInput（输入 token 的子集，不重复相加）",
        "langfuse_note_output_tokens": "Langfuse tokens.output（客户端与服务端合计）",
        "langfuse_note_total_tokens": (
            "Langfuse tokens.total = input + output；与 sheet 中客户端口径的「总token」不同源"
        ),
        "langfuse_note_negotiation_triggered": (
            "服务端 INPUT_REQUIRED、客户端协商事件或 run 元数据任一存在即为「是」"
        ),
        "langfuse_note_negotiation_latency": (
            "Σ(S3 + S4 + S8 + S7)，只统计完整轮次；纯 A2A 用服务端 SendMessage 时长和"
        ),
        "langfuse_note_negotiation_rounds": (
            "Langfuse 协商轮次（服务端 INPUT_REQUIRED 与客户端事件取最大值）"
        ),
        "langfuse_note_end_to_end_latency": "taskCreatedAt − receivedAt；只到建任务，不是整条用例耗时",
        "langfuse_note_client_interceptions": (
            "每个「Task-T validation blocked」span 计一次；同一 run 纠正后重试不合并"
        ),
        "langfuse_unavailable": "Langfuse 指标未取到：{text}",
        "langfuse_no_credentials": "未配置 Langfuse 凭据",
        "langfuse_missing_trace_id": "缺少 Langfuse trace id",
        "langfuse_invalid_trace_id": "Langfuse trace id 格式无效",
        "langfuse_missing_run_id": "缺少 run id，无法定位 Langfuse 导出目录",
        "langfuse_cooldown": "Langfuse 限流冷却中（剩余 {seconds} 秒）",
        "langfuse_no_metrics": "导出结果缺少 metrics",
        "langfuse_runs_dir_missing": "导出时未提供 run 目录",
        "langfuse_timeout": "单条 trace {seconds:g} 秒内未完成",
    },
    LANGUAGE_EN: {
        # API errors
        "unknown_batch": "Unknown test batch.",
        "unknown_case": "Unknown test case.",
        "batch_not_running": "The test batch is not running.",
        "invalid_item_index": "Invalid test item index.",
        "select_protocol": "Select at least one protocol.",
        "invalid_langfuse_columns": "langfuseColumns accepts only auto, 0 or 1.",
        "invalid_json_body": "The request body is not valid JSON.",
        "json_body_not_object": "The request body must be a JSON object.",
        "list_must_be_array": "{name} must be an array.",
        "list_must_be_int": "{name} may contain only integers.",
        "list_must_not_be_empty": "{name} must not be empty.",
        "invalid_number": "A numeric parameter is malformed.",
        "concurrency_must_be_int": "Concurrency must be an integer.",
        "concurrency_out_of_range": "Concurrency must be between {min} and {max}.",
        "rounds_must_be_int": "Rounds must be an integer.",
        "rounds_out_of_range": "Rounds must be between {min} and {max}.",
        # Batch lifecycle errors
        "batch_already_run": "The test batch has already run.",
        "batch_still_running": "The test batch is still running.",
        "no_failed_items": "There are no failed, errored or cancelled items to retry.",
        "multi_round_keep_tasks": "A multi-round batch cannot keep tasks.",
        "pure_no_sdk_stage": (
            "The A2A protocol has no SDK stages; set stopAfter to "
            "send / negotiate / accepted / working / final."
        ),
        "no_runnable_cases": "There are no runnable cases.",
        "scratch_empty": "The ad-hoc request cannot be empty.",
        "principles_load_failed": "Unable to load the shared negotiation principles: {error}",
        "persist_failed": "Failed to persist the test batch: {error}",
        "batch_cancelled": "The test batch was cancelled.",
        "restart_incomplete": "The UI process restarted before the batch finished.",
        # Item lifecycle errors
        "item_already_finished": "The test item has already finished.",
        "item_still_running": "The test item is still running and cannot be rerun.",
        "item_not_run_yet": "The test item has not run yet and cannot be rerun.",
        "item_already_queued": "The test item is already queued for a rerun.",
        "unknown_case_id": "Unknown test case {case_id}",
        "item_cancelled": "Cancelled.",
        # Code-generated item name
        "scratch_item_name": "Ad-hoc request",
        # Run level explanations
        "run_failed": "The run failed.",
        "scratch_timeout": "The ad-hoc request timed out.",
        "scratch_recorded": "The ad-hoc request is recorded only.",
        "modify_baseline_create_failed": "The Modify baseline task could not be created.",
        "validate_no_evidence": "The run produced no validation evidence.",
        # Stage messages
        "acceptance_unproven": (
            'The NMS reported only TASK_STATE_SUBMITTED without the contractually required '
            '"Task creation succeeded" message or structured intent (vendor-side defect); '
            "acceptance is not confirmed, so observation continues."
        ),
        "assertion_off": "The case disabled this assertion.",
        "negotiated_task_t": (
            "The expected Task-T is the post-negotiation result; the SDK runner only sees the "
            "initial request, so this is record-only."
        ),
        "negotiate_failed": "The negotiation kind or message differs from the expectation.",
        "negotiate_rounds_warning": (
            "The negotiation rounds differ from the case expectation (the recorded round count "
            "is reference only and kind/messages match); confirm the result manually."
        ),
        "final_state_unpushed": (
            "The NMS confirmed the task succeeded but pushed no terminal state; the final result "
            "cannot be observed, so confirm it manually."
        ),
        "no_server_intent": (
            "The downstream TASK_STATE_SUBMITTED message carried no structured intent, so the "
            "intent stage is record-only."
        ),
        "a2a_proxy_server_intent": (
            "The intent returned on a non-direct A2A vendor hop is record-only and is not used "
            "for the verdict."
        ),
        "no_expected_intent": "The case has no expected intent, so this is record-only.",
        "server_intent_mismatch": "The server's intent reading differs from the expectation.",
        "intent_unjudged": "Not judged; needs human review.",
        "task_t_mismatch": (
            "Task-T differs from the expectation (warning; not a reason to fail the case)."
        ),
        "validation_mismatch": "The interception verdict differs from the expectation.",
        "no_expected_validation": (
            "The case has no expected interception verdict, so this is record-only."
        ),
        "send_mismatch": "The expected send status differs from the observed result.",
        "final_mismatch": "The final state differs from the expectation.",
        "expected_no_send": "The case expects no send",
        "expected_working_or_completed": "WORKING or COMPLETED",
        "run_ended_before_send": "The run ended before sending: {final_state}",
        "task_state_is": "The Task state is {state}.",
        "run_finished_without_task": "The run finished without a Task.",
        "wait_task_timeout": "Timed out waiting for a Task.",
        "task_negotiating_input_required": (
            "The Task is still negotiating (INPUT_REQUIRED); acceptance is not yet confirmed."
        ),
        "no_acceptance_observed": "No acceptance state was observed.",
        "expected_task_id": "not empty",
        "expected_context_id": "not empty",
        "expected_accepted_state": "accepted",
        "refused_completion": (
            "The Task ended as TASK_STATE_COMPLETED but the run's final state is {final_state}."
        ),
        "task_stuck_submitted": "The Task stayed at SUBMITTED for {seconds}s.",
        "task_negotiating": "The Task is still negotiating (INPUT_REQUIRED).",
        "completed_via_final_state": (
            "Completion was observed through the terminal state task_completed."
        ),
        "run_final_state_is": "The run's final state is {final_state}.",
        "no_working_observed": "WORKING was not observed within {seconds}s.",
        "no_working_observed_short": "WORKING was not observed.",
        # Follow-up / interception
        "followup_still_blocked": (
            "The follow-up request (blocked_followup.input) is still intercepted: the original "
            "request being intercepted is expected, but the follow-up must pass validation."
        ),
        "followup_not_sent": (
            "The follow-up request (blocked_followup.input) was not sent: the run ended before "
            "sending ({final_state}); the original request being intercepted is expected."
        ),
        "interception_missed": (
            "The original request was not intercepted ({detail}); no run was started, the "
            "follow-up request (blocked_followup.input) did not execute, and the miss is recorded."
        ),
        "interception_miss_dispatched": (
            "The original request was not intercepted ({detail}); the original request was "
            "dispatched instead, the follow-up request (blocked_followup.input) did not execute, "
            "and the miss is recorded."
        ),
        "interception_detail": "Actual validation verdict: {actual}",
        "no_validation_conclusion": "No validation conclusion",
        "followup_detail": "{message} Reason: {reason}",
        "followup_error_detail": "{message} Details: {error}",
        # Modify baselines
        "baseline_modify_input_required": (
            "The Modify baseline Create triggered a negotiation (TASK_STATE_INPUT_REQUIRED) and "
            "is not continuable; check baseline.request: it must use A2ATestRegion explicitly "
            "and a three-segment daily plan that is neither intercepted nor negotiated, as in "
            "manual acceptance."
        ),
        "baseline_modify_unproven": (
            "The Modify baseline Create was not confirmed accepted: the NMS reported only "
            'TASK_STATE_SUBMITTED without the contractually required "Task creation succeeded" '
            "message or structured intent (vendor-side defect); if the task is then cancelled, "
            "the Modify must fail."
        ),
        "baseline_modify_not_continuable": (
            "The Modify baseline task did not reach a continuable state (currently {state}); a "
            "terminal task is rejected by the server."
        ),
        "baseline_pure_not_working": (
            "The pure-A2A outer Task of the Modify baseline did not stay at WORKING, or the "
            "downstream binding was not created (currently {state}); an outer WORKING only means "
            "the OSS Agent accepted it, and the downstream Create can still fail (for example "
            "Running task limit). Pure A2A can only continue a real, non-terminal Task; clear "
            "the NMS task pool and retry."
        ),
        "baseline_state_unknown": "state not observed",
        # Verdict reason connectors
        "verdict_recorded": "{stage}: recorded, not judged",
        "verdict_stage": "{stage}: {message}",
        "verdict_separator": "; ",
        "verdict_needs_review": "Needs human review{suffix}",
        "verdict_needs_review_base": "Needs human review",
        "verdict_review_paths": " ({paths})",
        # Export: worksheet names and headers
        "export_sheet_arbitration": "Arbitration",
        "export_sheet_results": "Case results",
        "export_sheet_stage_diffs": "Stage diffs",
        "export_sheet_summary": "Summary",
        "export_sheet_wire": "Interaction messages",
        "export_sheet_rounds": "Round summary",
        "export_sheet_plan": "Batch plan",
        "export_sheet_client_quality": "Client decision quality",
        "export_sheet_client_replies": "Client replies reference",
        "export_header_trace_id": "traceId",
        "export_header_protocol": "Protocol",
        "export_header_case_id": "Case ID",
        "export_header_case_name": "Case name",
        "export_header_task_type": "Task type",
        "export_header_qa_id": "QA_ID",
        "export_header_uid": "UID",
        "export_header_designer": "Designer",
        "export_header_issue": "Issue",
        "export_header_raw_request": "Original request",
        "export_header_normalized_request": "Normalized request",
        "export_header_server_intent": "Intent reading (structured JSON)",
        "export_header_sdk_intent": "SDK-extracted intent",
        "export_header_intent_source": "Intent source",
        "export_header_expected_intent": "Expected intent",
        "export_header_intent_diff": "answer vs reference diff",
        "export_header_model_answer": "Model answer",
        "export_header_interaction": "Interaction messages",
        "export_header_trajectory": "Arbitrator-EVAL-extra-result-trajectory",
        "export_header_task_state": "Arbitrator-EVAL-extra-result-task state",
        "export_header_end_to_end": "Arbitrator-EVAL-extra-result-end-to-end latency(ms)",
        "export_header_negotiation_rounds": "Negotiation rounds",
        "export_header_negotiation_triggered": "Negotiation triggered",
        "export_header_negotiation_succeeded": "Negotiation succeeded",
        "export_header_output_tokens": "Arbitrator-EVAL-extra-result-output token",
        "export_header_total_tokens": "Arbitrator-EVAL-extra-result-total token",
        "export_header_execution_status": "Execution status",
        "export_header_auto_score": "Automatic score",
        "export_header_final_score": "Final score",
        "export_header_summary_result": "Summary result",
        "export_header_verdict_reason": "Verdict reason",
        "export_header_arbitration_score": "Arbitration score",
        "export_header_manual_mark": "Manual mark",
        "export_header_notes": "Notes",
        "export_header_duration_ms": "Duration(ms)",
        "export_header_total_token_count": "Total tokens",
        "export_header_run_id": "Run ID",
        "export_header_baseline_status": "Baseline status",
        "export_header_baseline_run_id": "Baseline Run ID",
        "export_header_baseline_task_id": "Baseline taskId",
        "export_header_baseline_state": "Baseline state",
        "export_header_baseline_error": "Baseline error",
        "export_header_transport": "Transport",
        "export_header_discovery": "Discovery",
        "export_header_interception_validation": "Interception validation",
        "export_header_interception_followup": "Follow-up after interception",
        "export_header_stage_status": "{stage} status",
        "export_header_error": "Error",
        "export_header_round": "Round",
        "export_header_stage": "Stage",
        "export_header_status": "Status",
        "export_header_field": "Field",
        "export_header_expected": "Expected",
        "export_header_actual": "Actual",
        "export_header_detail": "Detail",
        "export_header_metric": "Metric",
        "export_header_value": "Value",
        "export_header_item": "Item",
        "export_header_frame": "Frame",
        "export_header_segment": "Segment",
        "export_header_total": "Total",
        "export_header_passed": "Passed",
        "export_header_pending": "Pending",
        "export_header_failed": "Failed",
        "export_header_error_count": "Error",
        "export_header_cancelled": "Cancelled",
        "export_header_stable_passes": "Stable passes",
        "export_header_comparable_cases": "Comparable cases",
        "export_header_negotiation_round": "Negotiation round",
        "export_header_decision": "Decision",
        "export_header_proposal": "Proposal",
        "export_header_quality_expected": "Expected",
        "export_header_action": "Action",
        "export_header_structure": "Structure",
        "export_header_review": "Review",
        "export_header_coverage": "Coverage",
        "export_header_quality": "Quality",
        "export_header_human_review": "Human review",
        "export_header_method": "Method",
        "export_header_reason": "Reason",
        "export_header_recorded_replies": "Recorded replies",
        "export_plan_environment": "Environment",
        "export_plan_negotiation_model": "Negotiation model",
        "export_plan_judge_model": "Judge model",
        "export_plan_principles_version": "Principles version",
        "export_plan_nms_version": "NMS version",
        "export_plan_stop_after": "Stop after",
        "export_plan_rounds": "Rounds",
        "export_round_column": "Round {round}",
        # Export: notes and prose
        "export_yes": "yes",
        "export_no": "no",
        "export_no_run_note": "The case started no run.",
        "export_no_runs_dir_note": "The export was called without a runs directory.",
        "export_no_wire_note": (
            "run {run_id} has no wire messages (capture is off or the files were cleaned up)."
        ),
        "export_wire_header": (
            "run {run_id} | {frames} frames | wire.jsonl / wire-oss.jsonl / wire-error.jsonl"
        ),
        "export_wire_truncated_note": (
            "… (truncated; see the Interaction messages sheet for every frame)"
        ),
        "export_trajectory_truncated_note": (
            "… (trajectory truncated; see the Interaction messages column for the full frames)"
        ),
        "export_no_answer_note": "The send stage did not run",
        "export_no_server_intent_note": "The downstream returned no structured intent",
        "export_no_server_intent_pure_note": "Pure A2A collected no server intent",
        "export_no_intent_note": "This batch collected no intent",
        "export_rebuilt_trace_note": "traceparent was reconstructed",
        "export_baseline_failed": "Modify baseline Create failed: {detail}",
        "export_baseline_failed_no_reason": "no recorded reason",
        "export_scratch_item": "Ad-hoc request",
        "export_no_case_metadata": "This batch collected no case metadata",
        "export_sdk_intent_missing": "SDK-extracted intent was not collected",
        "export_langfuse_warning": "Langfuse export failed: {warning}",
        "export_note_separator": "; ",
        "export_followup_skipped": "(not executed) {text}",
        "export_interception_stage_prefix": "Interception/",
        "export_cell_truncated": "… (truncated)",
        "export_label_passed": "passed",
        "export_label_failed": "failed",
        "export_label_partial": "pending",
        "export_label_error": "error",
        "export_label_cancelled": "cancelled",
        # Export: intent diff and answer steps
        "export_diff_identical": "identical",
        "export_diff_missing": "missing: {path} (ref value {value})",
        "export_diff_added": "added: {path} (answer value {value})",
        "export_diff_changed": "changed: {path} ({expected} → {actual})",
        "export_diff_expression": (
            "expression difference (equal after normalization): {path} "
            "({expected} → {actual})"
        ),
        "export_step_outbound": "Sent {message_type} request",
        "export_step_inbound": "Received {message_type}",
        "export_sse_blank_line": "(blank line)",
        # Langfuse cells
        "langfuse_header_input_tokens": "input tokens",
        "langfuse_header_cached_input_tokens": "cached input tokens",
        "langfuse_header_output_tokens": "output tokens",
        "langfuse_header_total_tokens": "total tokens",
        "langfuse_header_negotiation_triggered": "negotiation triggered",
        "langfuse_header_negotiation_latency": "negotiation latency(ms)",
        "langfuse_header_negotiation_rounds": "negotiation rounds",
        "langfuse_header_end_to_end_latency": "end-to-end latency(ms)",
        "langfuse_header_client_interceptions": "client interceptions",
        "langfuse_note_input_tokens": (
            "Langfuse tokens.input (gross: includes cache; includes a2a-t-server usage)"
        ),
        "langfuse_note_cached_input_tokens": (
            "Langfuse tokens.cachedInput (a subset of input tokens, not added again)"
        ),
        "langfuse_note_output_tokens": (
            "Langfuse tokens.output (client and server combined)"
        ),
        "langfuse_note_total_tokens": (
            "Langfuse tokens.total = input + output; a different source from the "
            "client-side total tokens column"
        ),
        "langfuse_note_negotiation_triggered": (
            "yes when any of server INPUT_REQUIRED, a client negotiation event or run "
            "metadata exists"
        ),
        "langfuse_note_negotiation_latency": (
            "Σ(S3 + S4 + S8 + S7), complete rounds only; pure A2A uses the server "
            "SendMessage duration sum"
        ),
        "langfuse_note_negotiation_rounds": (
            "Langfuse negotiation rounds (the max of server INPUT_REQUIRED and client events)"
        ),
        "langfuse_note_end_to_end_latency": (
            "taskCreatedAt − receivedAt; task creation only, not the whole case duration"
        ),
        "langfuse_note_client_interceptions": (
            "one per 'Task-T validation blocked' span; a correction retry in the same run "
            "is not merged"
        ),
        "langfuse_unavailable": "Langfuse metrics unavailable: {text}",
        "langfuse_no_credentials": "Langfuse credentials are not configured",
        "langfuse_missing_trace_id": "Missing Langfuse trace id",
        "langfuse_invalid_trace_id": "Malformed Langfuse trace id",
        "langfuse_missing_run_id": (
            "Missing run id; cannot locate the Langfuse export directory"
        ),
        "langfuse_cooldown": "Langfuse rate-limit cooldown active ({seconds}s remaining)",
        "langfuse_no_metrics": "The export result has no metrics",
        "langfuse_runs_dir_missing": "the export was called without a runs directory",
        "langfuse_timeout": "one trace did not finish within {seconds:g}s",
    },
}


class TesterMessages:
    """Render one tester language's message catalog."""

    #: Not a pytest test class despite the ``Test`` prefix.
    __test__ = False

    def __init__(self, language: str = LANGUAGE_ZH) -> None:
        self._language = language if language in MESSAGE_CATALOG else LANGUAGE_ZH

    @property
    def language(self) -> str:
        """Return the language this message set renders in."""
        return self._language

    def get(self, key: str, **params: object) -> str:
        """Render ``key`` with ``params``.

        An unknown key returns the key itself (and logs a warning in production).  A template with
        missing parameters is returned unformatted rather than raising into the caller; every
        call site is expected to pass complete parameters.
        """
        template = MESSAGE_CATALOG[self._language].get(key)
        if template is None:
            _LOGGER.warning("tester message key %r is missing from %s", key, self._language)
            return key
        try:
            return template.format(**params)
        except (KeyError, IndexError, ValueError):
            _LOGGER.warning(
                "tester message %r could not format with %r", key, sorted(params), exc_info=True
            )
            return template


__all__ = ["MESSAGE_CATALOG", "TesterMessages"]
