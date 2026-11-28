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
    },
}


class TesterMessages:
    """Render one tester language's message catalog."""

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
