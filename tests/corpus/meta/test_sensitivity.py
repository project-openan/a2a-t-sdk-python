"""Sensitivity self-test of the corpus engines (port of Java ``CorpusSensitivitySelfTest``).

The meta-meta layer guards against the deadliest failure mode of a data-driven suite: an
always-green rubber-stamp engine whose bug lets every case pass silently (design §8.6, Q16). Every
class of expectation assertion is proven sensitive twice:

* on **inline sample cases** (the Java port): each sample runs green with its true expectation,
  then one flipped expectation value must make the :class:`~tests.corpus.engine.CaseEngine` red,
  with the case id and the JSON path of the flipped expectation in the failure message —
  outcome, error code, ``llmCalls`` ± 1, the golden fixture name, the merged parameter map, the
  slot-error bag (dropped and added), the task-family missing-parameter set and the
  ``promptTextContains`` fragments. The samples are built inline and never touch the corpus files;
* on a **representative sample of shipped corpus cases** (the port's mutation matrix): the same
  mutation operators applied to real records — flip outcome, swap the error code, drop or add a
  slot error, ``llmCalls`` ± 1, tamper a merged parameter — where each sampled case is first
  proven green with its shipped expectation (so the mutation is never vacuous) and then proven red
  through the engine directly, which is exactly what the suites would report.

The final guard, ``test_the_engines_never_crash_on_any_corpus_record``, walks the whole loaded
corpus: the engines may report red (an :class:`AssertionError` is the engine correctly rejecting a
mismatch — that is what the suites turn into test failures), but they must never crash with an
unexpected exception. A crash is an engine bug; a red assertion is the engine working.
"""

from __future__ import annotations

import dataclasses
import json
from typing import Final

import pytest

from tests.corpus.conftest import SESSION_ID, expanded_cases
from tests.corpus.engine import CaseEngine, ScenarioEngine
from tests.corpus.models import (
    ContextSpec,
    Expectation,
    LlmFailMarker,
    LlmScript,
    LlmScriptStep,
    LoadedCorpus,
    NegotiationApi,
    NegotiationCase,
    PromptSource,
)

#: The only language the inline samples need (the sensitivity is per assertion, not per language).
ZH_CN: Final[str] = "zh-CN"

INFORMATION_PROPOSE_URI: Final[str] = "Negotiation-T/information-negotiation/propose/v1"

INFORMATION_ACCEPT_REJECT_URI: Final[str] = "Negotiation-T/information-negotiation/accept-reject/v1"

PRIVATE_LINE_COMPLAINT_URI: Final[str] = "Task-T/network-layer/private-line-complaint/v1"

#: The workbench raw complaint of the closed loop (样例步骤1): no port name and no complaint category.
COMPLAINT_TEXT: Final[str] = (
    "深圳访问广州的专线从5月11号早上8点半开始响应时延从平均12ms骤升至320ms，柜面和手机银行的交易接口频繁报"
    "“连接超时”。OSS侧事件流水号：event-id-20260511-09013。"
)

#: Slot-extraction payload of the closed loop's step 1: the task object carries what the raw text
#: names (the circuit) but no port name, so the rendered prompt stays portless.
TASK_SLOTS_OBJECT_PORTLESS: Final[str] = (
    '{"slots": {"任务对象": "深圳访问广州的专线", "任务上下文": "投诉分类：待补充；问题发生时间：2026-05-11T08:21:46Z；'
    'OSS侧事件流水号：event-id-20260511-09013；投诉详情：深圳访问广州的响应时延从平均12ms骤升至320ms"},'
    ' "slot_errors": []}'
)

#: Semantic payload of the closed loop's step 2: the port name and the complaint category are missing.
TASK_SEMANTIC_MISSING_PARAMS: Final[str] = (
    '{"semantic_verdict":true,"errors":[],"params":{"accessPort":null,"bizScenario":null,'
    '"faultTime":"2026-05-11T08:21:46Z","eventSerialNo":"event-id-20260511-09013"}}'
)

#: The rendered task prompt the OMC receives (样例步骤1, shortened): the negotiation-causing message.
TASK_PROMPT_MISSING_PARAMS: Final[str] = (
    "## 任务类型(Task Type)\n"
    "传输专线业务投诉诊断\n"
    "\n"
    "## 任务对象(Task Object)\n"
    "接入端口名称：\n"
    "\n"
    "## 任务上下文(Task Context)\n"
    "1. 投诉分类：\n"
    '2. 问题发生时间： "2026-05-11T08:21:46Z"\n'
    '3. OSS侧事件流水号："event-id-20260511-09013"\n'
    '4. 投诉详情："深圳访问广州的响应时延从平均12ms骤升至320ms"\n'
)

#: The task parameter schema of the closed loop (server-side keys, dictionary §10).
TASK_PARAM_SCHEMA: Final[dict[str, object]] = json.loads(
    '{"type":"object","properties":{"accessPort":{"type":"string"},"bizScenario":'
    '{"type":"string"},"faultTime":{"type":"string"},"eventSerialNo":'
    '{"type":"string"}},"required":["accessPort","bizScenario"]}'
)

#: Extraction payload mapping to the typed content of the information accept golden fixture.
ACCEPT_PAYLOAD: Final[str] = (
    '{"conclusion":"Accept","items":[{"name":"接入端口名称","value":"P533-珠江旧城'
    '-PTN3900-23-TPA1EG24-1"},{"name":"投诉分类","value":"专线质差"}]}'
)

#: Extraction payload whose mapped content lacks every required slot (fails with
#: ``negotiation.field_missing``).
SLOTS_MISSING_PAYLOAD: Final[str] = '{"relationship":null}'

#: Semantic verdict payload of a successful validation carrying one business parameter.
SEMANTIC_ACCEPT_PAYLOAD_WITH_PARAMS: Final[str] = (
    '{"semantic_verdict":true,"negotiation_type":"information","errors":[],"params":'
    '{"accessPort":"P533-珠江旧城-PTN3900-23-TPA1EG24-1"}}'
)

#: Semantic verdict payload rejecting the message with two slot errors.
SEMANTIC_REJECT_PAYLOAD_TWO_ERRORS: Final[str] = (
    '{"semantic_verdict":false,"negotiation_type":"information","errors":[{"slot_name":'
    '"accessPort","code":"negotiation.field_missing","facts":{"field":'
    '"接入端口名称"}},{"slot_name":"latency_target","code":'
    '"negotiation.constraint_conflict","facts":{"section_label":"时延目标",'
    '"reason":"latency target is out of range"}}],"params":{}}'
)

#: The flat caller schema of the inline validate samples.
SAMPLE_SCHEMA: Final[dict[str, object]] = json.loads('{"type":"object","properties":{"accessPort":{"type":"string"}}}')

#: The two content-layer codes the swap mutation alternates between.
_SWAP_PRIMARY_CODE: Final[str] = "negotiation.field_missing"
_SWAP_ALTERNATIVE_CODE: Final[str] = "negotiation.invalid_input"

#: Sentinel the mutation matrix writes into a merged parameter value.
_TAMPERED_PARAM_VALUE: Final[str] = "tampered-by-the-sensitivity-meta-test"

#: Mutation operators of the corpus-sample matrix, mapped to the JSON path they must break.
_MUTATION_JSON_PATHS: Final[dict[str, str]] = {
    "flip_outcome": "$.expect.outcome",
    "swap_code": "$.expect.code",
    "drop_slot_error": "$.expect.slotErrors",
    "add_slot_error": "$.expect.slotErrors",
    "llm_calls_plus_one": "$.expect.llmCalls",
    "llm_calls_minus_one": "$.expect.llmCalls",
    "tamper_params": "$.expect.params",
}

#: How many applicable records each family directory contributes to each mutation of the matrix.
_SAMPLE_PER_FAMILY: Final[int] = 2

#: The offline family directories the mutation matrix samples from.
_SAMPLED_DIRECTORIES: Final[tuple[str, ...]] = ("from-text", "from-data", "validate")


# ------------------------------------------------------------------ flipped expectations must be red


def test_flipped_error_code_must_fail_the_engine() -> None:
    """A swapped error code fails with the case id and the ``$.expect.code`` JSON path."""
    engine = CaseEngine()
    engine.run(_from_text_case("FT-SENS-CODE", SLOTS_MISSING_PAYLOAD, _failed("negotiation.field_missing", 1)))
    flipped = _from_text_case("FT-SENS-CODE-FLIP", SLOTS_MISSING_PAYLOAD, _failed("negotiation.invalid_input", 1))
    with pytest.raises(AssertionError) as excinfo:
        engine.run(flipped)
    message = str(excinfo.value)
    assert "FT-SENS-CODE-FLIP" in message and "$.expect.code" in message, (
        f"a swapped error code must fail with the case id and the JSON path but was: {message}"
    )


def test_flipped_llm_call_count_must_fail_the_engine() -> None:
    """``llmCalls`` off by one in either direction fails on the ``$.expect.llmCalls`` JSON path."""
    engine = CaseEngine()
    engine.run(_from_text_case("FT-SENS-CALLS", ACCEPT_PAYLOAD, _ok(1, "information_accept")))
    too_high = _from_text_case("FT-SENS-CALLS-HIGH", ACCEPT_PAYLOAD, _ok(2, "information_accept"))
    with pytest.raises(AssertionError) as excinfo:
        engine.run(too_high)
    assert "FT-SENS-CALLS-HIGH" in str(excinfo.value) and "$.expect.llmCalls" in str(excinfo.value)
    too_low = _from_text_case("FT-SENS-CALLS-LOW", ACCEPT_PAYLOAD, _ok(0, "information_accept"))
    with pytest.raises(AssertionError) as excinfo:
        engine.run(too_low)
    assert "FT-SENS-CALLS-LOW" in str(excinfo.value) and "$.expect.llmCalls" in str(excinfo.value)


def test_flipped_golden_name_must_fail_the_engine() -> None:
    """A swapped golden fixture fails on the ``$.expect.promptTextEqualsGolden`` JSON path."""
    engine = CaseEngine()
    engine.run(_from_text_case("FT-SENS-GOLDEN", ACCEPT_PAYLOAD, _ok(1, "information_accept")))
    flipped = _from_text_case("FT-SENS-GOLDEN-FLIP", ACCEPT_PAYLOAD, _ok(1, "information_reject"))
    with pytest.raises(AssertionError) as excinfo:
        engine.run(flipped)
    message = str(excinfo.value)
    assert "FT-SENS-GOLDEN-FLIP" in message and "$.expect.promptTextEqualsGolden" in message, (
        f"a swapped golden fixture must fail but was: {message}"
    )


def test_tampered_params_must_fail_the_engine() -> None:
    """A tampered merged-parameter value fails on ``$.expect.params`` naming the tampered value."""
    engine = CaseEngine()
    engine.run(
        _validate_case(
            "VAL-SENS-PARAMS",
            {"id": SESSION_ID, "round": 2, "maxRounds": 5, "accessPort": "P533-珠江旧城-PTN3900-23-TPA1EG24-1"},
        )
    )
    tampered = _validate_case(
        "VAL-SENS-PARAMS-FLIP",
        {"id": SESSION_ID, "round": 2, "maxRounds": 5, "accessPort": "P781-珠江新城-PTN7900-23-TPA1EG24-17"},
    )
    with pytest.raises(AssertionError) as excinfo:
        engine.run(tampered)
    message = str(excinfo.value)
    assert (
        "VAL-SENS-PARAMS-FLIP" in message
        and "$.expect.params" in message
        and "P781-珠江新城-PTN7900-23-TPA1EG24-17" in message
    ), f"a tampered params value must fail but was: {message}"


def test_flipped_outcome_must_fail_the_engine() -> None:
    """A success run expected to fail and a failing run expected to succeed are both red."""
    engine = CaseEngine()
    success_read_as_failure = _from_text_case(
        "FT-SENS-OUTCOME-SF", ACCEPT_PAYLOAD, _failed("negotiation.invalid_input", 1)
    )
    with pytest.raises(AssertionError) as excinfo:
        engine.run(success_read_as_failure)
    assert "FT-SENS-OUTCOME-SF" in str(excinfo.value) and "$.expect.outcome" in str(excinfo.value)
    failure_read_as_success = _from_text_case("FT-SENS-OUTCOME-FS", None, _ok(1, "information_accept"))
    with pytest.raises(AssertionError) as excinfo:
        engine.run(failure_read_as_success)
    assert "FT-SENS-OUTCOME-FS" in str(excinfo.value) and "$.expect.outcome" in str(excinfo.value)


def test_flipped_slot_errors_must_fail_the_engine() -> None:
    """A dropped and an added slot error both fail on the ``$.expect.slotErrors`` JSON path."""
    engine = CaseEngine()
    both = [
        Expectation.SlotError("accessPort", "negotiation.field_missing"),
        Expectation.SlotError("latency_target", "negotiation.constraint_conflict"),
    ]
    engine.run(_validate_case("VAL-SENS-SLOTS", None, both))
    removed = _validate_case(
        "VAL-SENS-SLOTS-DEL", None, [Expectation.SlotError("accessPort", "negotiation.field_missing")]
    )
    with pytest.raises(AssertionError) as excinfo:
        engine.run(removed)
    assert "VAL-SENS-SLOTS-DEL" in str(excinfo.value) and "$.expect.slotErrors" in str(excinfo.value)
    added = both + [Expectation.SlotError("round", "negotiation.round_exceeded")]
    case_added = _validate_case("VAL-SENS-SLOTS-ADD", None, added)
    with pytest.raises(AssertionError) as excinfo:
        engine.run(case_added)
    assert "VAL-SENS-SLOTS-ADD" in str(excinfo.value) and "$.expect.slotErrors" in str(excinfo.value)


# ------------------------------------------------------------------ task-family expectations must be sensitive


def test_flipped_missing_params_must_fail_the_engine() -> None:
    """A shrunken missing-parameter set fails on ``$.expect.missingParams`` naming the lost slot."""
    engine = CaseEngine()
    true_expectation = _task_validate_case(
        "VAL-SENS-MISSING",
        _task_ok(1, None, ["accessPort", "bizScenario"], {"faultTime": "2026-05-11T08:21:46Z"}),
        TASK_SEMANTIC_MISSING_PARAMS,
    )
    engine.run(true_expectation)
    flipped = _task_validate_case(
        "VAL-SENS-MISSING-FLIP",
        _task_ok(1, None, ["accessPort"], {"faultTime": "2026-05-11T08:21:46Z"}),
        TASK_SEMANTIC_MISSING_PARAMS,
    )
    with pytest.raises(AssertionError) as excinfo:
        engine.run(flipped)
    message = str(excinfo.value)
    assert "VAL-SENS-MISSING-FLIP" in message and "$.expect.missingParams" in message and "bizScenario" in message, (
        f"a shrunken missing-parameter set must fail but was: {message}"
    )


def test_flipped_prompt_text_contains_must_fail_the_engine() -> None:
    """A wrong structural fragment fails on the ``$.expect.promptTextContains`` JSON path."""
    engine = CaseEngine()
    true_expectation = _task_from_text_case("VAL-SENS-HEADERS", _task_ok(1, ["## 任务类型"], None, {}))
    engine.run(true_expectation)
    flipped = _task_from_text_case("VAL-SENS-HEADERS-FLIP", _task_ok(1, ["## 所需信息项"], None, {}))
    with pytest.raises(AssertionError) as excinfo:
        engine.run(flipped)
    message = str(excinfo.value)
    assert "VAL-SENS-HEADERS-FLIP" in message and "$.expect.promptTextContains" in message, (
        f"a wrong structural fragment must fail but was: {message}"
    )


# ------------------------------------------------------------------ mutation matrix over shipped corpus cases


def _mutated(mutation: str, case: NegotiationCase) -> NegotiationCase | None:
    """Apply one mutation operator to a shipped case, or ``None`` when it does not apply."""
    expect = case.expect
    if mutation == "flip_outcome":
        return dataclasses.replace(case, expect=dataclasses.replace(expect, success=not expect.success))
    if mutation == "swap_code":
        if expect.success or expect.code is None:
            return None
        swapped = _SWAP_ALTERNATIVE_CODE if expect.code == _SWAP_PRIMARY_CODE else _SWAP_PRIMARY_CODE
        return dataclasses.replace(case, expect=dataclasses.replace(expect, code=swapped))
    if mutation == "drop_slot_error":
        # An empty expected bag means "no assertion" (the engine and the Java original both skip
        # it), so dropping the only slot error of a record is vacuous — only a bag of two or more
        # proves the drop is felt, exactly like the Java inline sample removes one of two.
        if expect.success or len(expect.slot_errors) < 2:
            return None
        return dataclasses.replace(case, expect=dataclasses.replace(expect, slot_errors=list(expect.slot_errors[:-1])))
    if mutation == "add_slot_error":
        if expect.success or not expect.slot_errors:
            return None
        added = list(expect.slot_errors) + [Expectation.SlotError("tampered_slot", "negotiation.field_missing")]
        return dataclasses.replace(case, expect=dataclasses.replace(expect, slot_errors=added))
    if mutation in ("llm_calls_plus_one", "llm_calls_minus_one"):
        if expect.llm_calls is None:
            return None
        mutated_calls = expect.llm_calls + (1 if mutation == "llm_calls_plus_one" else -1)
        if mutated_calls < 0:
            return None
        return dataclasses.replace(case, expect=dataclasses.replace(expect, llm_calls=mutated_calls))
    if mutation == "tamper_params":
        if not expect.success or not expect.params:
            return None
        key = next(iter(expect.params))
        if expect.params[key] == _TAMPERED_PARAM_VALUE:
            return None
        tampered = dict(expect.params)
        tampered[key] = _TAMPERED_PARAM_VALUE
        return dataclasses.replace(case, expect=dataclasses.replace(expect, params=tampered))
    raise ValueError(f"unknown mutation {mutation}")


def _sampled_offline_cases() -> list[NegotiationCase]:
    """Every expanded offline case of the three sampled family directories."""
    return [
        *expanded_cases("from-text"),
        *expanded_cases("from-data"),
        *expanded_cases("validate"),
    ]


def _build_mutation_matrix() -> list[tuple[str, NegotiationCase]]:
    """Collect the mutation matrix: up to two applicable records per family per mutation."""
    sampled = _sampled_offline_cases()
    matrix: list[tuple[str, NegotiationCase]] = []
    for mutation in _MUTATION_JSON_PATHS:
        per_family: dict[str, int] = {}
        for case in sampled:
            family = case.source_file.split("/", 1)[0]
            if per_family.get(family, 0) >= _SAMPLE_PER_FAMILY:
                continue
            if _mutated(mutation, case) is None:
                continue
            matrix.append((mutation, case))
            per_family[family] = per_family.get(family, 0) + 1
    assert matrix, "the mutation matrix must sample at least one shipped corpus case per operator"
    return matrix


#: The (mutation, case) pairs of the matrix: the first applicable records of each family directory.
_MUTATION_MATRIX: Final[list[tuple[str, NegotiationCase]]] = _build_mutation_matrix()


@pytest.mark.parametrize(
    ["mutation", "case"],
    _MUTATION_MATRIX,
    ids=[f"{mutation}::{case.id}" for mutation, case in _MUTATION_MATRIX],
)
def test_mutated_expectations_of_shipped_cases_fail_the_engine(
    mutation: str,
    case: NegotiationCase,
) -> None:
    """Every mutation operator goes red on real corpus records, each first proven green.

    The sampled case runs green with its shipped expectation (the baseline — a mutation of a
    record that is already red would be vacuous), then the mutated expectation must fail with the
    mutated case id and the JSON path the operator targets, which is exactly the failure the
    suites would report.
    """
    engine = CaseEngine()
    engine.run(case)
    mutated = _mutated(mutation, case)
    assert mutated is not None, f"the mutation {mutation} is not applicable to {case.id}"
    with pytest.raises(AssertionError) as excinfo:
        engine.run(mutated)
    message = str(excinfo.value)
    assert mutated.id in message and _MUTATION_JSON_PATHS[mutation] in message, (
        f"the {mutation} mutation of {case.id} must fail with the case id and "
        f"{_MUTATION_JSON_PATHS[mutation]} but was: {message}"
    )


def test_the_mutation_matrix_covers_every_operator_and_family() -> None:
    """The matrix exercises all seven operators in every family where an operator applies.

    An operator only goes red where the corpus offers a record it can mutate — the slot-error
    bag exists only in the validate family, so ``drop_slot_error``/``add_slot_error`` sample from
    there alone while ``flip_outcome`` must reach all three family directories.
    """
    operators = {mutation for mutation, _ in _MUTATION_MATRIX}
    assert operators == set(_MUTATION_JSON_PATHS), "every mutation operator must be sampled"
    for mutation in _MUTATION_JSON_PATHS:
        contributing = {
            case.source_file.split("/", 1)[0] for operator, case in _MUTATION_MATRIX if operator == mutation
        }
        applicable = {
            case.source_file.split("/", 1)[0]
            for case in _sampled_offline_cases()
            if _mutated(mutation, case) is not None
        }
        assert contributing == applicable, (
            f"the {mutation} mutation must be sampled in every family that offers an applicable "
            f"record: contributing {sorted(contributing)}, applicable {sorted(applicable)}"
        )


# ------------------------------------------------------------------ engine crash guard


def test_the_engines_never_crash_on_any_corpus_record(loaded_corpus: LoadedCorpus) -> None:
    """The engines may report red, but must never crash with an unexpected exception.

    An :class:`AssertionError` is the engine rejecting a mismatch (what the suites turn into test
    failures); any other exception escaping one corpus record is an engine bug.
    """
    case_engine = CaseEngine()
    for test_case in loaded_corpus.cases:
        try:
            case_engine.run(test_case)
        except AssertionError:
            continue  # red is the engine working, not a crash
        except Exception as crash:  # noqa: BLE001 - reported as the engine bug it is
            pytest.fail(f"the case engine crashed on {test_case.error_prefix()}: {crash}")
    scenario_engine = ScenarioEngine()
    for scenario in loaded_corpus.scenarios:
        try:
            scenario_engine.run_scenario(scenario)
        except AssertionError:
            continue  # see above: red is not a crash
        except Exception as crash:  # noqa: BLE001 - reported as the engine bug it is
            pytest.fail(f"the scenario engine crashed on {scenario.id}: {crash}")


# ------------------------------------------------------------------ inline sample cases


def _from_text_case(case_id: str, payload: str | None, expect: Expectation) -> NegotiationCase:
    """Build the inline from-text accept sample: one scripted payload, or one failure marker."""
    steps: list[LlmScriptStep.Payload | LlmScriptStep.Fail] = (
        [LlmScriptStep.Fail(LlmFailMarker.NON_JSON)] if payload is None else [LlmScriptStep.Payload(payload)]
    )
    return NegotiationCase(
        id=f"{case_id}/zh-CN",
        base_id=case_id,
        source_file="from-text/sensitivity-probes.json",
        api=NegotiationApi.GENERATE_ACCEPT_FROM_TEXT,
        language=ZH_CN,
        tags=[],
        expect=expect,
        context=ContextSpec(SESSION_ID, 2, 5),
        template_uri=INFORMATION_ACCEPT_REJECT_URI,
        input_text="请接受。",
        llm=LlmScript(steps),
    )


def _validate_case(
    case_id: str,
    params: dict[str, object] | None,
    slot_errors: list[Expectation.SlotError] | None = None,
) -> NegotiationCase:
    """Build the inline validate sample: a passing merge or a semantic rejection with slot errors."""
    if params is None:
        expect = Expectation(
            success=False,
            exception=None,
            code="negotiation.semantic_rejected",
            message_contains=[],
            slot_errors=[] if slot_errors is None else slot_errors,
            llm_calls=1,
            prompt_text_equals_golden=None,
            metadata=None,
            params={},
            contracts=[],
            differential=False,
        )
        payload = SEMANTIC_REJECT_PAYLOAD_TWO_ERRORS
    else:
        expect = Expectation(
            success=True,
            exception=None,
            code=None,
            message_contains=[],
            slot_errors=[],
            llm_calls=1,
            prompt_text_equals_golden=None,
            metadata=None,
            params=params,
            contracts=[],
            differential=False,
        )
        payload = SEMANTIC_ACCEPT_PAYLOAD_WITH_PARAMS
    return NegotiationCase(
        id=f"{case_id}/zh-CN",
        base_id=case_id,
        source_file="validate/sensitivity-probes.json",
        api=NegotiationApi.VALIDATE_PROPOSE_PROMPT_AND_DATA_FILLING,
        language=ZH_CN,
        tags=[],
        expect=expect,
        context=ContextSpec(SESSION_ID, 2, 5),
        template_uri=INFORMATION_PROPOSE_URI,
        llm=LlmScript([LlmScriptStep.Payload(payload)]),
        prompt=PromptSource.Golden("information_propose"),
        schema=SAMPLE_SCHEMA,
    )


def _task_from_text_case(case_id: str, expect: Expectation) -> NegotiationCase:
    """Build the inline task from-text sample of the closed loop's step 1."""
    return NegotiationCase(
        id=f"{case_id}/zh-CN",
        base_id=case_id,
        source_file="task/sensitivity-probes.json",
        api=NegotiationApi.GENERATE_TASK_PROMPT_FROM_TEXT,
        language=ZH_CN,
        tags=[],
        expect=expect,
        template_uri=PRIVATE_LINE_COMPLAINT_URI,
        input_text=COMPLAINT_TEXT,
        llm=LlmScript([LlmScriptStep.Payload(TASK_SLOTS_OBJECT_PORTLESS)]),
    )


def _task_validate_case(case_id: str, expect: Expectation, semantic_payload: str) -> NegotiationCase:
    """Build the inline task validate sample of the closed loop's step 2."""
    return NegotiationCase(
        id=f"{case_id}/zh-CN",
        base_id=case_id,
        source_file="task/sensitivity-probes.json",
        api=NegotiationApi.VALIDATE_TASK_PROMPT_AND_DATA_FILLING,
        language=ZH_CN,
        tags=[],
        expect=expect,
        template_uri=PRIVATE_LINE_COMPLAINT_URI,
        llm=LlmScript([LlmScriptStep.Payload(semantic_payload)]),
        prompt=PromptSource.Text(TASK_PROMPT_MISSING_PARAMS),
        schema=TASK_PARAM_SCHEMA,
    )


def _ok(llm_calls: int | None, prompt_text_equals_golden: str | None) -> Expectation:
    """Build a success expectation with the golden name the golden leg asserts."""
    return Expectation(
        success=True,
        exception=None,
        code=None,
        message_contains=[],
        slot_errors=[],
        llm_calls=llm_calls,
        prompt_text_equals_golden=prompt_text_equals_golden,
        metadata=None,
        params={},
        contracts=[],
        differential=False,
    )


def _task_ok(
    llm_calls: int | None,
    prompt_text_contains: list[str] | None,
    missing_params: list[str] | None,
    params: dict[str, object],
) -> Expectation:
    """Build a task-family success expectation with the prompt fragments and the missing set."""
    return Expectation(
        success=True,
        exception=None,
        code=None,
        message_contains=[],
        slot_errors=[],
        llm_calls=llm_calls,
        prompt_text_equals_golden=None,
        metadata=None,
        params=params,
        contracts=[],
        differential=False,
        prompt_text_contains=[] if prompt_text_contains is None else prompt_text_contains,
        missing_params=missing_params,
    )


def _failed(
    code: str | None,
    llm_calls: int | None,
    params: dict[str, object] | None = None,
    slot_errors: list[Expectation.SlotError] | None = None,
) -> Expectation:
    """Build a failure expectation carrying the error code the code leg asserts."""
    return Expectation(
        success=False,
        exception=None,
        code=code,
        message_contains=[],
        slot_errors=[] if slot_errors is None else slot_errors,
        llm_calls=llm_calls,
        prompt_text_equals_golden=None,
        metadata=None,
        params={} if params is None else params,
        contracts=[],
        differential=False,
    )
