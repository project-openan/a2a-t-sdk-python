"""Excel import and export of the tester console's test cases.

The workbook is the only test-case input: one row is one run. The parser accepts the bilingual
headers written by :func:`write_template`, validates every row on its own (a bad row never blocks
the rest of the sheet), and resolves the sample-level expectation defaults.

``scenario`` is optional. Business testers usually write a user request only; the sample still
needs a scenario profile to build the client runtime, so an omitted scenario falls back to
``night`` and no negotiation assertion is derived from it. An explicit scenario is treated as a
test profile: its final state, negotiation kind and round count become the defaults of that row.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from io import BytesIO
from typing import Final

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet
from shared.run_constants import (
    FINAL_STATE_INCOMPLETE,
    FINAL_STATE_INTENT_NOT_CONFIRMED,
    FINAL_STATE_NEGOTIATION_ABORTED,
    FINAL_STATE_NEGOTIATION_ENDED,
    FINAL_STATE_TASK_COMPLETED,
    FINAL_STATE_TASK_FAILED,
    FINAL_STATE_TASK_REJECTED,
    LANGUAGE_EN,
    LANGUAGE_ZH,
    PROTOCOL_A2A_T,
    PROTOCOL_PURE,
)

from demo.scenario import SCENARIOS

#: Scenario used when the sheet does not name one. It is a profile, not a required business input.
DEFAULT_SCENARIO: Final[str] = "night"

#: Approval automation values.
APPROVAL_AUTO: Final[str] = "auto"
APPROVAL_APPROVE: Final[str] = "approve"
APPROVAL_REJECT: Final[str] = "reject"
APPROVAL_VALUES: Final[tuple[str, ...]] = (
    APPROVAL_AUTO,
    APPROVAL_APPROVE,
    APPROVAL_REJECT,
)

#: Header aliases, normalized by :func:`_header_key`. The first alias is the canonical label.
COLUMN_ALIASES: Final[Mapping[str, tuple[str, ...]]] = {
    "case_id": ("用例编号", "Case ID", "编号", "id"),
    "name": ("用例名称", "Name", "名称", "title"),
    "enabled": ("是否执行", "Enabled", "执行", "run"),
    "protocol": ("协议", "Protocol"),
    "scenario": ("场景", "Scenario", "场景配置", "测试配置"),
    "language": ("语言", "Language", "lang"),
    "user_request": ("用户请求", "User Request", "自然语言请求", "request"),
    "expected_final_state": (
        "预期最终状态",
        "Expected Final State",
        "预期状态",
        "expected final",
    ),
    "expected_kind": ("预期协商类型", "Expected Kind", "预期类型"),
    "expected_rounds": ("预期协商轮次", "Expected Rounds", "预期轮次"),
    "approval": ("审批结果", "Approval", "审批"),
    "timeout_seconds": ("超时秒数", "Timeout Seconds", "超时", "timeout"),
    "note": ("备注", "Note", "说明"),
}

#: Columns written by the template, in display order.
TEMPLATE_COLUMNS: Final[tuple[tuple[str, str], ...]] = (
    ("用例编号", "Case ID"),
    ("用例名称", "Name"),
    ("是否执行", "Enabled"),
    ("协议", "Protocol"),
    ("场景", "Scenario"),
    ("语言", "Language"),
    ("用户请求", "User Request"),
    ("预期最终状态", "Expected Final State"),
    ("预期协商类型", "Expected Kind"),
    ("预期协商轮次", "Expected Rounds"),
    ("审批结果", "Approval"),
    ("超时秒数", "Timeout Seconds"),
    ("备注", "Note"),
)

_FINAL_STATE_ALIASES: Final[Mapping[str, str]] = {
    "task_completed": FINAL_STATE_TASK_COMPLETED,
    "completed": FINAL_STATE_TASK_COMPLETED,
    "完成": FINAL_STATE_TASK_COMPLETED,
    "任务完成": FINAL_STATE_TASK_COMPLETED,
    "no_task_negotiation_ended": FINAL_STATE_NEGOTIATION_ENDED,
    "negotiation_ended": FINAL_STATE_NEGOTIATION_ENDED,
    "协商结束": FINAL_STATE_NEGOTIATION_ENDED,
    "no_task_negotiation_aborted": FINAL_STATE_NEGOTIATION_ABORTED,
    "negotiation_aborted": FINAL_STATE_NEGOTIATION_ABORTED,
    "协商中止": FINAL_STATE_NEGOTIATION_ABORTED,
    "intent_not_confirmed": FINAL_STATE_INTENT_NOT_CONFIRMED,
    "意图未确认": FINAL_STATE_INTENT_NOT_CONFIRMED,
    "task_rejected": FINAL_STATE_TASK_REJECTED,
    "任务拒绝": FINAL_STATE_TASK_REJECTED,
    "task_failed": FINAL_STATE_TASK_FAILED,
    "任务失败": FINAL_STATE_TASK_FAILED,
    "incomplete": FINAL_STATE_INCOMPLETE,
    "不完整": FINAL_STATE_INCOMPLETE,
}

_KIND_ALIASES: Final[Mapping[str, str]] = {
    "none": "none",
    "无": "none",
    "information": "information",
    "信息": "information",
    "target": "target",
    "目标": "target",
    "feasibility": "feasibility",
    "可行性": "feasibility",
}

_APPROVAL_ALIASES: Final[Mapping[str, str]] = {
    "auto": APPROVAL_AUTO,
    "自动": APPROVAL_AUTO,
    "approve": APPROVAL_APPROVE,
    "approved": APPROVAL_APPROVE,
    "通过": APPROVAL_APPROVE,
    "同意": APPROVAL_APPROVE,
    "reject": APPROVAL_REJECT,
    "rejected": APPROVAL_REJECT,
    "拒绝": APPROVAL_REJECT,
}

_FALSE_VALUES: Final[frozenset[str]] = frozenset({"n", "no", "false", "0", "否", "不", "跳过", "skip"})


@dataclass
class TestCase:
    """One parsed row of the test workbook.

    Attributes:
        case_id: Tester-visible identifier; unique within a sheet.
        name: Optional display title.
        enabled: Whether the row is executed; a disabled row is reported as skipped.
        protocol: ``a2a-t`` or ``a2a`` (standard A2A).
        scenario: Scenario profile used to build the runtime.
        scenario_explicit: Whether the sheet named the scenario (and therefore supplied the
            expectation defaults).
        language: ``zh-CN`` or ``en-US``.
        user_request: Business request; empty uses the scenario's canned request.
        expected_final_state: Final state the run must reach; always set.
        expected_kind: Negotiation kind to assert, ``"none"`` for no negotiation, or ``""`` for
            no assertion.
        expected_rounds: Negotiation round to assert, or ``None`` for no assertion.
        approval: ``auto``, ``approve`` or ``reject`` automation of a paused approval gate.
        timeout_seconds: Whole-case wall-clock budget.
        note: Free text preserved from the sheet.
        errors: Row-level validation errors; any error makes the row ineligible to run.
    """

    case_id: str
    name: str
    enabled: bool
    protocol: str
    scenario: str
    scenario_explicit: bool
    language: str
    user_request: str
    expected_final_state: str
    expected_kind: str
    expected_rounds: int | None
    approval: str
    timeout_seconds: float
    note: str
    errors: list[str] = field(default_factory=list)

    @property
    def runnable(self) -> bool:
        """Whether this row may start a run."""
        return self.enabled and not self.errors

    def to_dict(self) -> dict[str, object]:
        """Return the JSON representation used by the tester page."""
        return {
            "caseId": self.case_id,
            "name": self.name,
            "enabled": self.enabled,
            "protocol": self.protocol,
            "scenario": self.scenario,
            "scenarioExplicit": self.scenario_explicit,
            "language": self.language,
            "userRequest": self.user_request,
            "expectedFinalState": self.expected_final_state,
            "expectedKind": self.expected_kind,
            "expectedRounds": self.expected_rounds,
            "approval": self.approval,
            "timeoutSeconds": self.timeout_seconds,
            "note": self.note,
            "errors": list(self.errors),
            "runnable": self.runnable,
        }


def _header_key(value: object) -> str:
    """Normalize one header cell to a comparable key."""
    text = str(value or "").strip()
    return re.sub(r"[\s_\-:/（）()]+", "", text).lower()


_ALIAS_LOOKUP: Final[dict[str, str]] = {
    _header_key(alias): canonical for canonical, aliases in COLUMN_ALIASES.items() for alias in aliases
}
# The template writes "Chinese English" headers; accept those exact labels too.
for _canonical, (_zh, _en) in zip(COLUMN_ALIASES, TEMPLATE_COLUMNS, strict=True):
    _ALIAS_LOOKUP[_header_key(f"{_zh} {_en}")] = _canonical


def _cell_text(value: object) -> str:
    """Return one cell as trimmed text (Excel numbers become text without a trailing ``.0``)."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def _normalize_protocol(value: str) -> str:
    """Map an Excel protocol label to ``a2a-t`` / ``a2a``; return ``""`` when unknown."""
    compact = re.sub(r"[\s_\-]+", "", value).lower()
    if compact in {"a2at", "a2a-t", "a2atextension"}:
        return PROTOCOL_A2A_T
    if compact in {"a2a", "pure", "standarda2a", "标准a2a"}:
        return PROTOCOL_PURE
    return ""


def _normalize_language(value: str, default: str) -> str:
    """Map an Excel language label to ``zh-CN`` / ``en-US``."""
    compact = value.strip().lower()
    if not compact:
        return default
    if compact in {"zh", "zh-cn", "cn", "chinese", "中文", "汉语"}:
        return LANGUAGE_ZH
    if compact in {"en", "en-us", "english", "英文", "英语"}:
        return LANGUAGE_EN
    return ""


def _normalize_final_state(value: str) -> str:
    """Map a final-state label to its constant; return ``""`` when unknown."""
    compact = value.strip().lower().replace(" ", "_")
    return _FINAL_STATE_ALIASES.get(compact, "")


def _normalize_kind(value: str) -> str:
    """Map a negotiation-kind label; return ``""`` when unknown."""
    return _KIND_ALIASES.get(value.strip().lower(), "")


def _normalize_approval(value: str) -> str:
    """Map an approval label; return ``""`` when unknown."""
    return _APPROVAL_ALIASES.get(value.strip().lower(), "")


def _parse_enabled(value: str) -> bool:
    """Parse the enabled flag; an empty cell means enabled."""
    return value.strip().lower() not in _FALSE_VALUES


def _scenario_defaults(name: str) -> tuple[str, str, int | None]:
    """Return the expectation defaults of an explicit scenario profile."""
    spec = SCENARIOS.get(name)
    if spec is None:
        return "", "", None
    rounds = max(
        (step.round for step in spec.expected_sequence if step.round is not None),
        default=0,
    )
    return spec.expected_final_state, spec.expected_kind, rounds


def _parse_row(
    values: Mapping[str, object],
    *,
    row_number: int,
    protocols: Sequence[str],
    scenarios_by_protocol: Mapping[str, Sequence[str]],
    default_language: str,
    seen_ids: set[str],
) -> TestCase:
    """Build and validate one test case from a header-keyed row."""
    errors: list[str] = []
    case_id = _cell_text(values.get("case_id"))
    if not case_id:
        errors.append("用例编号不能为空。")
    elif case_id in seen_ids:
        errors.append(f"用例编号重复：{case_id}。")
    seen_ids.add(case_id)

    protocol_text = _cell_text(values.get("protocol"))
    protocol = _normalize_protocol(protocol_text)
    if not protocol:
        errors.append(f"协议必须为 A2A-T 或 A2A，当前为 {protocol_text or '空'}。")
    elif protocol not in protocols:
        errors.append(f"当前页面未启用协议 {protocol_text}。")

    scenario_text = _cell_text(values.get("scenario"))
    scenario_explicit = bool(scenario_text)
    scenario = scenario_text or DEFAULT_SCENARIO
    if scenario not in SCENARIOS:
        errors.append(f"未知场景配置：{scenario}。")
    elif protocol and protocol in scenarios_by_protocol and scenario not in scenarios_by_protocol[protocol]:
        errors.append(f"协议 {protocol_text} 不支持场景 {scenario}。")

    language_text = _cell_text(values.get("language"))
    language = _normalize_language(language_text, default_language)
    if not language:
        errors.append(f"语言必须为 zh 或 en，当前为 {language_text}。")

    final_state_text = _cell_text(values.get("expected_final_state"))
    expected_final_state = _normalize_final_state(final_state_text) if final_state_text else ""
    if final_state_text and not expected_final_state:
        errors.append(f"未知预期最终状态：{final_state_text}。")

    kind_text = _cell_text(values.get("expected_kind"))
    expected_kind = _normalize_kind(kind_text) if kind_text else ""
    if kind_text and not expected_kind:
        errors.append(f"未知预期协商类型：{kind_text}。")

    rounds_text = _cell_text(values.get("expected_rounds"))
    expected_rounds: int | None = None
    if rounds_text:
        try:
            expected_rounds = int(float(rounds_text))
            if expected_rounds < 0:
                raise ValueError
        except ValueError:
            errors.append(f"预期协商轮次必须为非负整数，当前为 {rounds_text}。")

    approval_text = _cell_text(values.get("approval"))
    # The console is unattended by default: an empty cell answers a paused gate with approval.
    # ``auto`` is the explicit opt-out that leaves the gate to a human (or the timeout).
    approval = _normalize_approval(approval_text) if approval_text else APPROVAL_APPROVE
    if approval_text and not approval:
        errors.append(f"未知审批结果：{approval_text}。")

    timeout_text = _cell_text(values.get("timeout_seconds"))
    timeout_seconds = 300.0
    if timeout_text:
        try:
            timeout_seconds = float(timeout_text)
            if timeout_seconds <= 0:
                raise ValueError
        except ValueError:
            errors.append(f"超时秒数必须为正数，当前为 {timeout_text}。")

    if scenario_explicit:
        scenario_final, scenario_kind, scenario_rounds = _scenario_defaults(scenario)
        if not expected_final_state:
            expected_final_state = scenario_final
        if not expected_kind:
            expected_kind = scenario_kind
        if expected_rounds is None:
            expected_rounds = scenario_rounds
    elif not expected_final_state:
        # A business request without a named profile has no negotiation expectation; the only
        # safe assertion is that the task itself completes.
        expected_final_state = FINAL_STATE_TASK_COMPLETED
    if not expected_final_state:
        errors.append("预期最终状态不能为空。")

    return TestCase(
        case_id=case_id or f"row-{row_number}",
        name=_cell_text(values.get("name")),
        enabled=_parse_enabled(_cell_text(values.get("enabled"))),
        protocol=protocol,
        scenario=scenario,
        scenario_explicit=scenario_explicit,
        language=language,
        user_request=_cell_text(values.get("user_request")),
        expected_final_state=expected_final_state,
        expected_kind=expected_kind,
        expected_rounds=expected_rounds,
        approval=approval,
        timeout_seconds=timeout_seconds,
        note=_cell_text(values.get("note")),
        errors=errors,
    )


def parse_test_cases(
    data: bytes,
    *,
    protocols: Sequence[str],
    scenarios_by_protocol: Mapping[str, Sequence[str]],
    default_language: str,
) -> list[TestCase]:
    """Parse one ``.xlsx`` workbook into validated test cases.

    Args:
        data: Raw workbook bytes.
        protocols: Protocols the page exposes.
        scenarios_by_protocol: Scenario names each protocol accepts.
        default_language: Process language used when the row leaves the column empty.

    Returns:
        The parsed rows in sheet order; rows with errors are included but not runnable.

    Raises:
        ValueError: when the file is not a readable workbook or has no recognizable header row.
    """
    try:
        workbook = load_workbook(BytesIO(data), read_only=True, data_only=True)
    except Exception as exc:  # noqa: BLE001 - openpyxl raises several zip/XML exception types
        raise ValueError("无法读取 Excel 文件，请确认上传的是 .xlsx 工作簿。") from exc
    try:
        sheet = workbook.worksheets[0]
        rows = list(sheet.iter_rows(values_only=True))
    finally:
        workbook.close()
    if not rows:
        raise ValueError("Excel 文件为空。")

    header = [_header_key(cell) for cell in rows[0]]
    keys = [_ALIAS_LOOKUP.get(cell, "") for cell in header]
    if "case_id" not in keys:
        raise ValueError("表头缺少“用例编号 / Case ID”列。")

    seen_ids: set[str] = set()
    cases: list[TestCase] = []
    for index, row in enumerate(rows[1:], start=2):
        if not any(_cell_text(cell) for cell in row):
            continue
        values = {key: row[position] if position < len(row) else None for position, key in enumerate(keys) if key}
        cases.append(
            _parse_row(
                values,
                row_number=index,
                protocols=protocols,
                scenarios_by_protocol=scenarios_by_protocol,
                default_language=default_language,
                seen_ids=seen_ids,
            )
        )
    if not cases:
        raise ValueError("Excel 中没有测试用例行。")
    return cases


def write_template() -> bytes:
    """Build the downloadable test-case template with two example rows."""
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "测试用例"
    header_fill = PatternFill("solid", fgColor="FFF1E4")
    for column, (zh, en) in enumerate(TEMPLATE_COLUMNS, start=1):
        cell = sheet.cell(row=1, column=column, value=f"{zh} {en}")
        cell.font = Font(bold=True)
        cell.fill = header_fill
        cell.alignment = Alignment(vertical="center", wrap_text=True)
        sheet.column_dimensions[get_column_letter(column)].width = max(14, len(zh) * 2 + 6)
    examples = (
        (
            "TC-001",
            "完整请求 · A2A-T",
            "Y",
            "A2A-T",
            "night",
            "zh",
            "",
            "task_completed",
            "none",
            0,
            "approve",
            300,
            "使用 night 场景自带的完整请求。",
        ),
        (
            "TC-002",
            "完整请求 · A2A",
            "Y",
            "A2A",
            "night",
            "zh",
            "",
            "task_completed",
            "none",
            0,
            "approve",
            300,
            "标准 A2A 三跳，经 OSS Agent。",
        ),
        (
            "TC-003",
            "仅业务请求",
            "Y",
            "A2A-T",
            "",
            "zh",
            "在区域 A 开启节能，保障速率为：00:00–17:30 10 Mbps，17:30–23:00 20 Mbps，23:00–24:00 10 Mbps。",
            "task_completed",
            "",
            "",
            "approve",
            300,
            "场景留空：只按业务请求测试，不校验协商类型。",
        ),
    )
    for row_number, example in enumerate(examples, start=2):
        for column, value in enumerate(example, start=1):
            sheet.cell(row=row_number, column=column, value=value)

    notes = workbook.create_sheet("说明")
    notes.append(["列", "说明"])
    notes.append(["场景", "可选。留空时只按用户请求执行，不校验协商类型/轮次；填写时作为测试配置模板。"])
    notes.append(["预期最终状态", "留空时：填写场景则取场景默认值；未填写场景则为 task_completed。"])
    notes.append(["预期协商类型", "none / information / target / feasibility；留空不校验（填写场景时取场景默认值）。"])
    notes.append(
        [
            "审批结果",
            "approve（默认）自动通过；reject 自动拒绝；auto 不自动应答，等待人工处理或超时。",
        ]
    )
    notes.append(["协议", "A2A-T 使用 Task-T/Negotiation-T；A2A 使用标准 Task 状态。"])
    notes.column_dimensions["A"].width = 20
    notes.column_dimensions["B"].width = 90

    buffer = BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def write_results(batch: Mapping[str, object]) -> bytes:
    """Build the result workbook of one finished or running batch.

    The workbook has three sheets: the case verdicts, the per-case metrics (duration, tokens and
    latency) and a batch-level summary. Scripted offline LLM spans report tokens as unavailable
    instead of inventing numbers.
    """
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "测试结果"
    headers = (
        "用例编号",
        "用例名称",
        "执行状态",
        "协议",
        "场景",
        "用户请求",
        "预期最终状态",
        "实际最终状态",
        "预期协商",
        "实际协商",
        "耗时(ms)",
        "开始时间",
        "结束时间",
        "Token 总量",
        "Prompt Token",
        "Completion Token",
        "LLM 调用",
        "模型耗时(ms)",
        "工具耗时(ms)",
        "审批等待(ms)",
        "Token 状态",
        "Run ID",
        "错误信息",
        "备注",
    )
    sheet.append(list(headers))
    _style_header(sheet)
    cases = batch.get("cases", [])
    results = batch.get("results", [])
    pairs: list[tuple[Mapping[str, object], Mapping[str, object]]] = []
    if isinstance(cases, list) and isinstance(results, list):
        for case, result in zip(cases, results, strict=False):
            if isinstance(case, Mapping) and isinstance(result, Mapping):
                pairs.append((case, result))
    for case, result in pairs:
        tokens = _tokens_of(result)
        latency = _latency_of(result)
        sheet.append(
            [
                case.get("caseId", ""),
                case.get("name", ""),
                result.get("status", ""),
                _protocol_label(case.get("protocol")),
                case.get("scenario", ""),
                case.get("userRequest", ""),
                case.get("expectedFinalState", ""),
                result.get("finalState", ""),
                _expected_summary(case),
                _actual_summary(result),
                result.get("durationMs", ""),
                result.get("startedAt", ""),
                result.get("finishedAt", ""),
                _token_metric(tokens, "total"),
                _token_metric(tokens, "prompt"),
                _token_metric(tokens, "completion"),
                tokens.get("llmCalls", ""),
                latency.get("modelMs", ""),
                latency.get("toolMs", ""),
                latency.get("approvalWaitMs", ""),
                _token_status(tokens),
                result.get("runId", ""),
                result.get("error", ""),
                case.get("note", ""),
            ]
        )
    _set_widths(
        sheet,
        (16, 24, 12, 10, 14, 52, 20, 24, 18, 18, 12, 22, 22, 12, 12, 14, 10, 14, 14, 14, 14, 38, 40, 30),
    )

    metrics_sheet = workbook.create_sheet("指标")
    metrics_headers = (
        "用例编号",
        "协议",
        "场景",
        "状态",
        "实际最终状态",
        "实际协商",
        "耗时(ms)",
        "Token 总量",
        "Prompt",
        "Completion",
        "Reasoning",
        "LLM 调用",
        "客户端 Token",
        "服务端 Token",
        "模型耗时(ms)",
        "工具耗时(ms)",
        "审批等待(ms)",
        "Token 状态",
        "错误信息",
    )
    metrics_sheet.append(list(metrics_headers))
    _style_header(metrics_sheet)
    for case, result in pairs:
        tokens = _tokens_of(result)
        latency = _latency_of(result)
        metrics_sheet.append(
            [
                case.get("caseId", ""),
                _protocol_label(case.get("protocol")),
                case.get("scenario", ""),
                result.get("status", ""),
                result.get("finalState", ""),
                _actual_summary(result),
                result.get("durationMs", ""),
                _token_metric(tokens, "total"),
                _token_metric(tokens, "prompt"),
                _token_metric(tokens, "completion"),
                _token_metric(tokens, "reasoning"),
                tokens.get("llmCalls", ""),
                _side_tokens(tokens, "client"),
                _side_tokens(tokens, "server"),
                latency.get("modelMs", ""),
                latency.get("toolMs", ""),
                latency.get("approvalWaitMs", ""),
                _token_status(tokens),
                result.get("error", ""),
            ]
        )
    _set_widths(metrics_sheet, (16, 10, 14, 12, 20, 18, 12, 12, 12, 14, 12, 10, 14, 14, 14, 14, 14, 12, 40))

    summary_sheet = workbook.create_sheet("汇总")
    summary_sheet.append(["指标", "值"])
    _style_header(summary_sheet)
    for label, value in _batch_summary(pairs):
        summary_sheet.append([label, value])
    _set_widths(summary_sheet, (28, 24))

    buffer = BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def _style_header(sheet: Worksheet) -> None:
    """Apply the shared header fill to the first row of one sheet."""
    for cell in sheet[1]:
        cell.font = Font(bold=True)
        cell.fill = PatternFill("solid", fgColor="FFF1E4")
        cell.alignment = Alignment(vertical="center", wrap_text=True)
    sheet.freeze_panes = "A2"


def _set_widths(sheet: Worksheet, widths: Sequence[int]) -> None:
    """Set column widths and wrap the body cells of one sheet."""
    for index, width in enumerate(widths, start=1):
        sheet.column_dimensions[get_column_letter(index)].width = width
    for row in sheet.iter_rows(min_row=2):
        for cell in row:
            cell.alignment = Alignment(vertical="top", wrap_text=True)


def _tokens_of(result: Mapping[str, object]) -> Mapping[str, object]:
    """Return the token section of one case result."""
    metrics = result.get("metrics")
    if not isinstance(metrics, Mapping):
        return {}
    tokens = metrics.get("tokens")
    return tokens if isinstance(tokens, Mapping) else {}


def _latency_of(result: Mapping[str, object]) -> Mapping[str, object]:
    """Return the latency section of one case result."""
    metrics = result.get("metrics")
    if not isinstance(metrics, Mapping):
        return {}
    latency = metrics.get("latency")
    return latency if isinstance(latency, Mapping) else {}


def _token_metric(tokens: Mapping[str, object], field: str) -> object:
    """Return one token count only when the provider actually reported usage."""
    if tokens.get("available") is not True:
        return ""
    return tokens.get(field, "")


def _side_tokens(tokens: Mapping[str, object], side: str) -> object:
    """Return the reported total of one side (client/server), when available."""
    if tokens.get("available") is not True:
        return ""
    by_side = tokens.get("bySide")
    if not isinstance(by_side, Mapping):
        return ""
    bucket = by_side.get(side)
    if not isinstance(bucket, Mapping):
        return ""
    return bucket.get("total", "")


def _token_status(tokens: Mapping[str, object]) -> str:
    """Explain why token counts are missing or how they were obtained."""
    if not tokens:
        return "无数据"
    if tokens.get("available") is True:
        return "可用"
    if tokens.get("scripted") is True:
        return "离线脚本（无真实用量）"
    if tokens.get("estimated") is True:
        return "估算"
    return "不可用"


def _batch_summary(pairs: Sequence[tuple[Mapping[str, object], Mapping[str, object]]]) -> list[tuple[str, object]]:
    """Build the aggregate sheet of one batch."""
    counts: dict[str, int] = {}
    total_duration = 0
    total_tokens = 0
    total_llm_calls = 0
    model_ms = 0.0
    tool_ms = 0.0
    approval_ms = 0.0
    tokens_available = False
    for _, result in pairs:
        status = str(result.get("status") or "")
        counts[status] = counts.get(status, 0) + 1
        duration = result.get("durationMs")
        if isinstance(duration, (int, float)):
            total_duration += int(duration)
        tokens = _tokens_of(result)
        latency = _latency_of(result)
        calls = tokens.get("llmCalls")
        if isinstance(calls, int):
            total_llm_calls += calls
        for latency_field, target in (
            ("modelMs", "model"),
            ("toolMs", "tool"),
            ("approvalWaitMs", "approval"),
        ):
            value = latency.get(latency_field)
            if isinstance(value, (int, float)):
                if target == "model":
                    model_ms += float(value)
                elif target == "tool":
                    tool_ms += float(value)
                else:
                    approval_ms += float(value)
        if tokens.get("available") is True:
            tokens_available = True
            value = tokens.get("total")
            if isinstance(value, int):
                total_tokens += value
    return [
        ("用例总数", len(pairs)),
        ("通过", counts.get("passed", 0)),
        ("失败", counts.get("failed", 0)),
        ("错误", counts.get("error", 0)),
        ("取消", counts.get("cancelled", 0)),
        ("跳过", counts.get("skipped", 0)),
        ("无效", counts.get("invalid", 0)),
        ("总耗时(ms)", total_duration),
        ("总模型耗时(ms)", round(model_ms, 3)),
        ("总工具耗时(ms)", round(tool_ms, 3)),
        ("总审批等待(ms)", round(approval_ms, 3)),
        ("LLM 调用次数", total_llm_calls),
        ("Token 总量", total_tokens if tokens_available else "不可用"),
        ("Token 统计", "可用" if tokens_available else "离线脚本或未上报"),
    ]


def _protocol_label(value: object) -> str:
    """Render the user-facing protocol name in reports."""
    protocol = str(value or "")
    if protocol == PROTOCOL_A2A_T:
        return "A2A-T"
    if protocol == PROTOCOL_PURE:
        return "A2A"
    return protocol


def _expected_summary(case: Mapping[str, object]) -> str:
    """Render the expectation columns of one exported row."""
    kind = str(case.get("expectedKind") or "-")
    rounds = case.get("expectedRounds")
    return f"{kind}/{rounds if rounds is not None else '-'}"


def _actual_summary(result: Mapping[str, object]) -> str:
    """Render the actual negotiation columns of one exported row."""
    kind = str(result.get("actualKind") or "-")
    rounds = result.get("actualRounds")
    return f"{kind}/{rounds if rounds is not None else '-'}"
