"""Offline runner for the OSP energy-saving evaluation cases.

The runner executes the SDK-owned stages of a case without starting the Web UI or contacting a
network:

``intent``
    Generate the Task-T from the operator text, then extract the 11-key structured intent with
    :data:`eval.osp.intent_schema.OSP_INTENT_SCHEMA`.
``task_t``
    Compare the generated Task-T with the expected one section by section.
``validate``
    Run the sample's Task-T content validation and map it to ``blocked`` / ``pass``.

``send``, ``negotiate`` and ``final`` need the full runtime and are deliberately outside this
runner; use the presentation, the tester console or the benchmark for those stages.

Usage::

    PYTHONPATH=. python -m eval.osp.runner --all
    PYTHONPATH=. python -m eval.osp.runner --ids 1,40,46,49 --stop-after validate --verbose
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final, Protocol

import yaml
from a2a_t.core.errors.exceptions import ContentValidationError, PromptGenerationError
from protocol.client_flow import _classify_validation_error
from protocol.messages import TASK_TEMPLATE_URI
from shared.task_schema import TASK_PARAM_SCHEMA

from eval.osp.compare import Diff, compare_intent, compare_task_t, time_period_hours
from eval.osp.followup import followup_case, has_followup, interception_case, is_blocked_case
from eval.osp.intent_schema import OSP_INTENT_SCHEMA

#: Stages this runner can execute, in pipeline order.
STAGE_ORDER: Final[tuple[str, ...]] = ("intent", "task_t", "validate")

#: Status values of one stage.
STAGE_PASSED: Final[str] = "passed"
STAGE_FAILED: Final[str] = "failed"
STAGE_RECORDED: Final[str] = "recorded"
STAGE_ERROR: Final[str] = "error"

#: The 24 hourly intervals of a full-day energy-saving period. ``OSP_INTENT_SCHEMA`` declares this
#: as the default for an area-level request that names no time range.
_FULL_DAY_TIME: Final[str] = "&".join(
    f"{hour:02d}:00:00-{hour:02d}:59:59" for hour in range(24)
)

#: The case directory shipped next to this module.
DEFAULT_CASES_DIR: Final[Path] = Path(__file__).resolve().parent / "cases"

#: The sample root (``a2a-t-sample/ran-energy-saving``).
SAMPLE_ROOT: Final[Path] = Path(__file__).resolve().parents[2]

#: Default report directory.
DEFAULT_REPORT_DIR: Final[Path] = SAMPLE_ROOT / "runs" / "osp-eval"


class EvalPromptClient(Protocol):
    """The SDK surface the runner needs; the real implementation is ``A2ATClient``."""

    def generate_task_prompt_from_text(self, text: str, template_uri: str) -> object: ...

    def validate_task_prompt_and_data_filling(
        self,
        prompt: str,
        schema: Mapping[str, object],
        template_uri: str,
    ) -> object: ...


@dataclass(frozen=True)
class StageOutcome:
    """Result of one stage of one case."""

    stage: str
    status: str
    message: str = ""
    expected: object = None
    actual: object = None
    diffs: tuple[Diff, ...] = ()

    def to_dict(self) -> dict[str, object]:
        """Return the JSON-friendly representation used by the report."""
        return {
            "stage": self.stage,
            "status": self.status,
            "message": self.message,
            "expected": self.expected,
            "actual": self.actual,
            "diffs": [diff.to_dict() for diff in self.diffs],
        }


@dataclass(frozen=True)
class CaseOutcome:
    """Result of one case.

    A case whose A2A-T request is expected to be intercepted carries two segments: the
    ``interception`` records the original request (intent and Task-T are evidence; only the
    ``blocked`` validation is judged) and ``stages`` is the follow-up request that carries the
    case's real expectations. A case without a follow-up has ``interception`` set to ``None`` and
    ``stages`` is its only segment.
    """

    case_id: int
    name: str
    stages: tuple[StageOutcome, ...]
    passed: bool
    error: str | None = None
    interception: tuple[StageOutcome, ...] | None = None

    def to_dict(self) -> dict[str, object]:
        """Return the JSON-friendly representation used by the report."""
        result: dict[str, object] = {
            "id": self.case_id,
            "name": self.name,
            "passed": self.passed,
            "error": self.error,
            "stages": {stage.stage: stage.to_dict() for stage in self.stages},
        }
        if self.interception is not None:
            result["interception"] = {
                "passed": all(
                    stage.status in {STAGE_PASSED, STAGE_RECORDED}
                    for stage in self.interception
                ),
                "stages": {stage.stage: stage.to_dict() for stage in self.interception},
            }
        return result


def load_cases(cases_dir: Path = DEFAULT_CASES_DIR) -> list[dict[str, Any]]:
    """Load every case YAML in the directory, sorted by id."""
    cases: list[dict[str, Any]] = []
    for path in sorted(cases_dir.glob("*.yaml")):
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(document, dict):
            raise ValueError(f"{path}: not a YAML mapping")
        case_id = document.get("id")
        if not isinstance(case_id, int):
            raise ValueError(f"{path}: missing integer id")
        document["_path"] = path
        cases.append(document)
    cases.sort(key=lambda item: item["id"])
    return cases


def select_cases(cases: Sequence[dict[str, Any]], ids: Sequence[int]) -> list[dict[str, Any]]:
    """Return the selected cases; an empty id list means every case."""
    if not ids:
        return list(cases)
    wanted = set(ids)
    selected = [case for case in cases if case["id"] in wanted]
    missing = sorted(wanted - {case["id"] for case in selected})
    if missing:
        raise ValueError(f"unknown case ids: {missing}")
    return selected


def run_case(
    case: Mapping[str, Any],
    client: EvalPromptClient,
    *,
    stop_after: str = "validate",
) -> CaseOutcome:
    """Execute the stages of one case up to and including ``stop_after``.

    A blocked case with a ``blocked_followup`` request runs twice, matching the case contract:
    the interception segment judges only that the original request is blocked, and the follow-up
    segment replays the replacement request against the case's intent, Task-T and validation
    expectations. Without the second segment the replacement expectations would be compared with
    the incomplete original request, which reports a model error for a request the operator never
    meant to send.
    """
    if stop_after not in STAGE_ORDER:
        raise ValueError(f"unsupported stop_after {stop_after!r}; expected one of {STAGE_ORDER}")
    if not (is_blocked_case(case) and has_followup(case)):
        return _run_segment(case, client, stop_after=stop_after)

    interception = _run_segment(interception_case(case), client, stop_after=stop_after)
    followup = _run_segment(followup_case(case), client, stop_after=stop_after)
    return CaseOutcome(
        case_id=followup.case_id,
        name=followup.name,
        stages=followup.stages,
        passed=interception.passed and followup.passed,
        error=followup.error or interception.error,
        interception=interception.stages,
    )


def _run_segment(
    case: Mapping[str, Any],
    client: EvalPromptClient,
    *,
    stop_after: str = "validate",
) -> CaseOutcome:
    """Execute one request segment of a case up to and including ``stop_after``."""
    stages = STAGE_ORDER[: STAGE_ORDER.index(stop_after) + 1]
    case_id = int(case["id"])
    name = str(case["name"])

    try:
        task_t = _generate_task_t(case, client)
    except PromptGenerationError as exc:
        code = str(getattr(exc, "code_str", ""))
        if code.startswith("slot."):
            return _blocked_before_task_t(case, stages, exc)
        message = f"{type(exc).__name__}: {exc}"
        return _error_outcome(case_id, name, stages, message)
    except Exception as exc:  # noqa: BLE001 - one bad case must not stop the batch
        return _error_outcome(case_id, name, stages, f"{type(exc).__name__}: {exc}")

    outcomes: list[StageOutcome] = []
    if "intent" in stages:
        outcomes.append(_run_intent(case, client, task_t))
    if "task_t" in stages:
        outcomes.append(_run_task_t(case, task_t))
    if "validate" in stages:
        outcomes.append(_run_validate(case, client, task_t))

    passed = all(outcome.status in {STAGE_PASSED, STAGE_RECORDED} for outcome in outcomes)
    return CaseOutcome(case_id=case_id, name=name, stages=tuple(outcomes), passed=passed)


def _error_outcome(
    case_id: int,
    name: str,
    stages: Sequence[str],
    message: str,
) -> CaseOutcome:
    return CaseOutcome(
        case_id=case_id,
        name=name,
        stages=tuple(
            StageOutcome(stage=stage, status=STAGE_ERROR, message=message) for stage in stages
        ),
        passed=False,
        error=message,
    )


def _blocked_before_task_t(
    case: Mapping[str, Any],
    stages: Sequence[str],
    error: PromptGenerationError,
) -> CaseOutcome:
    """Handle a ``slot.*`` generation failure: the request is blocked before any Task-T exists.

    The OSP validation column is the expected outcome in this situation.  The intent and Task-T
    stages cannot be observed, so they are recorded rather than turned into a false failure.
    """
    case_id = int(case["id"])
    name = str(case["name"])
    expected_validate = case["expect"]["protocols"]["a2a-t"]["validate"]
    message = f"{getattr(error, 'code_str', 'slot.*')}: {error}"
    outcomes: list[StageOutcome] = []
    for stage in stages:
        if stage == "validate":
            if not _assert_flag(case, "a2a-t", "validate") or expected_validate is None:
                status = STAGE_RECORDED
            elif expected_validate == "blocked":
                status = STAGE_PASSED
            else:
                status = STAGE_FAILED
            outcomes.append(
                StageOutcome(
                    stage=stage,
                    status=status,
                    message=message,
                    expected=expected_validate,
                    actual="blocked",
                )
            )
            continue
        if stage == "intent":
            recorded = not _assert_flag(case, "intent")
            expected = case["expect"]["intent"]
        else:
            recorded = not _assert_flag(case, "a2a-t", "task_t")
            expected = case["expect"]["protocols"]["a2a-t"]["task_t"]
        status = (
            STAGE_RECORDED
            if recorded or expected_validate in {"blocked", None}
            else STAGE_ERROR
        )
        outcomes.append(
            StageOutcome(
                stage=stage,
                status=status,
                message=message,
                expected=expected,
                actual="<blocked before Task-T>",
            )
        )
    passed = all(outcome.status in {STAGE_PASSED, STAGE_RECORDED} for outcome in outcomes)
    return CaseOutcome(case_id=case_id, name=name, stages=tuple(outcomes), passed=passed)


def _generate_task_t(case: Mapping[str, Any], client: EvalPromptClient) -> str:
    content = client.generate_task_prompt_from_text(str(case["user_input"]), TASK_TEMPLATE_URI)
    text = getattr(content, "prompt_text", None)
    if not isinstance(text, str) or not text.strip():
        raise RuntimeError("the SDK returned no Task-T prompt text")
    return text


def _run_intent(
    case: Mapping[str, Any],
    client: EvalPromptClient,
    task_t: str,
) -> StageOutcome:
    expected = case["expect"]["intent"]
    note = ""
    try:
        filled = client.validate_task_prompt_and_data_filling(
            task_t, OSP_INTENT_SCHEMA, TASK_TEMPLATE_URI
        )
        actual = getattr(filled, "data", None)
    except ContentValidationError as exc:
        params = getattr(exc, "params", None)
        actual = dict(params) if isinstance(params, Mapping) else {}
        note = f"intent extraction reported {exc.code_str}"
    except Exception as exc:  # noqa: BLE001 - the stage reports the failure
        return StageOutcome(
            stage="intent",
            status=STAGE_ERROR,
            message=f"{type(exc).__name__}: {exc}",
            expected=expected,
        )
    if not isinstance(actual, Mapping):
        actual = {}
    actual, applied_defaults = _apply_intent_defaults(actual)
    if applied_defaults:
        note = _append_note(note, f"schema defaults: {', '.join(applied_defaults)}")
    diffs = compare_intent(expected, actual)
    negotiation = case["expect"]["protocols"]["a2a-t"]["negotiation"]
    post_negotiation = bool(negotiation.get("expected"))
    recorded = not _assert_flag(case, "intent") or post_negotiation
    if recorded:
        status = STAGE_RECORDED
        note = _append_note(
            note,
            "expected intent is post-negotiation; the offline runner sees the initial request"
            if post_negotiation
            else "record-only",
        )
    else:
        status = STAGE_PASSED if not diffs else STAGE_FAILED
    return StageOutcome(
        stage="intent",
        status=status,
        message=note,
        expected=expected,
        actual=dict(actual),
        diffs=tuple(diffs),
    )


def _apply_intent_defaults(
    actual: Mapping[str, object],
) -> tuple[dict[str, object], tuple[str, ...]]:
    """Fill the defaults ``OSP_INTENT_SCHEMA`` declares but the model may leave null.

    The schema states that an unmentioned RAT is ``ALL`` and that an area-level request without a
    time range covers the full day. Those are schema semantics, not guesses about the operator's
    text, so they are applied here instead of trusting every model turn to repeat them. When the
    zone targets carry their own effective periods, their union is the energy-saving window and
    overrides whatever the model wrote. A site-level request (one that carries
    ``Scenario-based energy saving``) keeps its empty time: the outage window belongs to the
    scenario period there.
    """
    data = dict(actual)
    applied: list[str] = []
    rat = data.get("Energy saving cell RAT")
    if rat is None or (isinstance(rat, str) and not rat.strip()):
        data["Energy saving cell RAT"] = "ALL"
        applied.append("Energy saving cell RAT=ALL")
    time = data.get("Energy saving time")
    if not data.get("Scenario-based energy saving"):
        # An area-level request whose zone targets already carry their own effective periods has
        # its real energy-saving window there; the model may still have written the full day, so
        # the target union wins. Only a request with no range at all covers the whole day.
        derived = _time_from_zone_targets(data.get("Guaranteed throughput target by zone"))
        if derived is not None:
            if data.get("Energy saving time") != derived:
                data["Energy saving time"] = derived
                applied.append("Energy saving time=zone target periods")
        elif time is None or (isinstance(time, str) and not time.strip()):
            data["Energy saving time"] = _FULL_DAY_TIME
            applied.append("Energy saving time=full day")
    return data, tuple(applied)


def _time_from_zone_targets(targets: object) -> str | None:
    """Return the union of the hourly periods of area-level zone targets.

    The intent extractor may leave ``Energy saving time`` empty while correctly filling a
    time-segmented zone target; the schema says the rate goal is guaranteed only inside the
    energy-saving window, so the target's own effective periods are that window. Returns ``None``
    when no hourly period can be read, so the caller falls back to the full-day default.
    """
    if not isinstance(targets, (list, tuple)):
        return None
    hours: set[int] = set()
    for entry in targets:
        if not isinstance(entry, Mapping):
            continue
        hours.update(time_period_hours(entry.get("Effective time period")))
    if not hours:
        return None
    return "&".join(f"{hour:02d}:00:00-{hour:02d}:59:59" for hour in sorted(hours))


def _run_task_t(case: Mapping[str, Any], task_t: str) -> StageOutcome:
    protocol = case["expect"]["protocols"]["a2a-t"]
    expected = protocol["task_t"]
    diffs = compare_task_t(expected, task_t)
    recorded = not _assert_flag(case, "a2a-t", "task_t")
    if recorded:
        status = STAGE_RECORDED
        message = "record-only"
    else:
        status = STAGE_PASSED if not diffs else STAGE_FAILED
        message = ""
    return StageOutcome(
        stage="task_t",
        status=status,
        message=message,
        expected=expected,
        actual=task_t,
        diffs=tuple(diffs),
    )


def _run_validate(
    case: Mapping[str, Any],
    client: EvalPromptClient,
    task_t: str,
) -> StageOutcome:
    protocol = case["expect"]["protocols"]["a2a-t"]
    expected = protocol["validate"]
    recorded = not _assert_flag(case, "a2a-t", "validate")
    actual, note, error = _actual_validation(client, task_t)
    if error is not None:
        return StageOutcome(
            stage="validate",
            status=STAGE_ERROR,
            message=error,
            expected=expected,
            actual=actual,
        )
    if recorded:
        return StageOutcome(
            stage="validate",
            status=STAGE_RECORDED,
            message=_append_note(note, "record-only"),
            expected=expected,
            actual=actual,
        )
    passed = _validation_matches(expected, actual)
    if expected == "pass_with_terminology" and actual == "pass":
        note = _append_note(note, "terminology context is a soft expectation")
    return StageOutcome(
        stage="validate",
        status=STAGE_PASSED if passed else STAGE_FAILED,
        message=note,
        expected=expected,
        actual=actual,
    )


def _actual_validation(
    client: EvalPromptClient,
    task_t: str,
) -> tuple[str | None, str, str | None]:
    try:
        client.validate_task_prompt_and_data_filling(task_t, TASK_PARAM_SCHEMA, TASK_TEMPLATE_URI)
    except ContentValidationError as exc:
        verdict, _kind = _classify_validation_error(exc)
        if verdict == "unavailable":
            return "unavailable", f"validation unavailable: {exc.code_str}", None
        if verdict == "blocked":
            return "blocked", f"blocked by {exc.code_str}", None
        return "pass", f"negotiable ({exc.code_str}); the request is still sent", None
    except Exception as exc:  # noqa: BLE001 - reported as a stage error
        return None, "", f"{type(exc).__name__}: {exc}"
    return "pass", "", None


def _validation_matches(expected: object, actual: object) -> bool:
    if expected == "pass_with_terminology":
        return actual in {"pass", "pass_with_terminology"}
    return expected == actual


def _assert_flag(case: Mapping[str, Any], *path: str) -> bool:
    value: object = case.get("assert")
    for key in path:
        if not isinstance(value, Mapping):
            return True
        value = value.get(key)
    return True if value is None else bool(value)


def _append_note(note: str, extra: str) -> str:
    return f"{note}; {extra}" if note else extra


def build_client(
    env_path: Path | None = None,
    *,
    model_mode: str | None = None,
) -> EvalPromptClient:
    """Build the real SDK client from the sample configuration.

    Offline mode registers the demo's deterministic SDK provider, because the SDK facade resolves
    ``scripted_traced`` only after that registration (the same step the demo runtime performs).
    """
    from a2a_t.client.a2at_client import A2ATClient
    from protocol.client_runtime import prepare_client_sdk_env
    from shared.config import load_config

    config = load_config(env_path=env_path)
    if model_mode is not None and model_mode != config.model_mode:
        from dataclasses import replace

        config = replace(config, model_mode=model_mode)
    if config.is_offline:
        from demo.composition import install_offline_provider

        install_offline_provider()
    sdk_env = prepare_client_sdk_env(config, env_path)
    return A2ATClient(env_path=sdk_env.path, logger=None)


def build_report(
    outcomes: Sequence[CaseOutcome],
    *,
    stop_after: str,
    started_at: str,
) -> dict[str, object]:
    """Build the JSON report of one run."""
    counts = {
        "total": len(outcomes),
        "passed": sum(1 for outcome in outcomes if outcome.passed),
        "failed": sum(1 for outcome in outcomes if not outcome.passed and outcome.error is None),
        "error": sum(1 for outcome in outcomes if outcome.error is not None),
    }
    recorded = sum(
        1
        for outcome in outcomes
        for stage in (*outcome.stages, *(outcome.interception or ()))
        if stage.status == STAGE_RECORDED
    )
    counts["recordedStages"] = recorded
    counts["stageErrors"] = sum(
        1
        for outcome in outcomes
        for stage in (*outcome.stages, *(outcome.interception or ()))
        if stage.status == STAGE_ERROR
    )
    return {
        "schema": "osp-eval-run/v1",
        "startedAt": started_at,
        "finishedAt": _now_iso(),
        "stopAfter": stop_after,
        "cases": [outcome.to_dict() for outcome in outcomes],
        "summary": counts,
    }


def _print_outcome(outcome: CaseOutcome, *, verbose: bool) -> None:
    label = "PASS" if outcome.passed else "FAIL"
    stage_text = " ".join(f"{stage.stage}={stage.status}" for stage in outcome.stages)
    if outcome.interception is not None:
        intercepted = " ".join(
            f"{stage.stage}={stage.status}" for stage in outcome.interception
        )
        stage_text = f"{stage_text}  [interception: {intercepted}]"
    display_name = " ".join(outcome.name.split())
    print(f"[{outcome.case_id:>3}] {label}  {stage_text}  {display_name}")
    if outcome.error:
        print(f"      error: {outcome.error}")
    if not verbose:
        return
    for stage in (*outcome.stages, *(outcome.interception or ())):
        if stage.message:
            print(f"      {stage.stage}: {stage.message}")
        for diff in stage.diffs:
            print(f"      {diff.path}: expected={diff.expected!r} actual={diff.actual!r}")


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases-dir", type=Path, default=DEFAULT_CASES_DIR)
    parser.add_argument(
        "--ids",
        default="",
        help="comma-separated case ids; empty (with --all) runs every case",
    )
    parser.add_argument("--all", action="store_true", help="run every case (the default)")
    parser.add_argument(
        "--stop-after",
        choices=STAGE_ORDER,
        default="validate",
        help="last SDK stage to execute; send/negotiate/final need the full runtime",
    )
    parser.add_argument("--env", type=Path, default=None, help="sample .env used to build the SDK client")
    parser.add_argument(
        "--model-mode",
        choices=("live", "offline"),
        default=None,
        help="override A2AT_SAMPLE_MODEL_MODE for this run",
    )
    parser.add_argument("--out", type=Path, default=None, help="JSON report path")
    parser.add_argument("--no-write", action="store_true", help="do not write a JSON report")
    parser.add_argument("--verbose", action="store_true", help="print every diff")
    return parser.parse_args(argv)


def _parse_ids(value: str) -> list[int]:
    if not value.strip():
        return []
    try:
        return [int(part.strip()) for part in value.split(",") if part.strip()]
    except ValueError as exc:
        raise SystemExit(f"invalid --ids: {value!r}") from exc


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        cases = select_cases(load_cases(args.cases_dir), _parse_ids(args.ids))
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if not cases:
        print("error: no cases selected", file=sys.stderr)
        return 2

    client = build_client(args.env, model_mode=args.model_mode)
    started_at = _now_iso()
    outcomes: list[CaseOutcome] = []
    for case in cases:
        outcome = run_case(case, client, stop_after=args.stop_after)
        outcomes.append(outcome)
        _print_outcome(outcome, verbose=args.verbose)

    report = build_report(outcomes, stop_after=args.stop_after, started_at=started_at)
    if not args.no_write:
        target = args.out or (DEFAULT_REPORT_DIR / f"{started_at.replace(':', '')}.json")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(report, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"report: {target}")

    summary = report["summary"]
    if not isinstance(summary, Mapping):
        return 1
    return 0 if summary.get("failed") == 0 and summary.get("error") == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
