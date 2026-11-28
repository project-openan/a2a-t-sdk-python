"""Live OSP evaluation of the A2A-T SDK Task-T and interception behavior.

This module is opt-in: the default suite skips it, so CI and every offline run stay deterministic
and never contact a model endpoint. Set ``A2AT_OSP_LIVE=1`` and configure the live model to run it.

Configuration, in order of precedence:

* ``A2AT_OSP_LIVE_ENV`` — path to a sample ``.env`` carrying the live model settings
  (``A2AT_SAMPLE_MODEL_MODE=live`` plus ``A2AT_LLM_MODEL`` / ``A2AT_LLM_BASE_URL`` /
  ``A2AT_LLM_API_KEY`` and the ``ADK_MODEL_*`` mirrors when ADK runs too).
* otherwise the shell environment, captured when this module is imported. The sample's
  ``conftest`` clears ``A2AT_*`` / ``ADK_*`` for every test, so the captured values are restored
  before the client is built.

``A2AT_OSP_LIVE_IDS`` restricts the run to a comma-separated id list (for example ``1,11,42``).

Each case runs the SDK-owned stages ``intent`` -> ``task_t`` -> ``validate`` through
:func:`eval.osp.runner.run_case` and asserts every stage the case marks as judged. A failure
message lists the per-stage status and the first diffs, so a yellow-zone run needs no extra
tooling to explain itself::

    A2AT_OSP_LIVE=1 A2AT_LLM_MODEL=mass/qwen3.8-flash \
    A2AT_LLM_BASE_URL=http://127.0.0.1:31943/v1 A2AT_LLM_API_KEY=not-needed \
    pytest tests/test_osp_live_eval.py -q

The JSON report of the same run is available through ``python -m eval.osp.runner --all
--model-mode live``; see ``eval/osp/README.md``.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest
from eval.osp.runner import CaseOutcome, build_client_with_env, load_cases, run_case, select_cases
from shared.config import SAMPLE_ENV_PREFIXES

#: Captured at import: pytest imports the module during collection, before the sample conftest
#: clears the process environment for each test.
_ENV_AT_IMPORT: dict[str, str] = {
    key: value for key, value in os.environ.items() if key.startswith(SAMPLE_ENV_PREFIXES)
}

_ENABLED: bool = os.environ.get("A2AT_OSP_LIVE", "").strip().lower() in {"1", "true", "yes", "on"}

pytestmark = pytest.mark.skipif(
    not _ENABLED,
    reason="live OSP evaluation is opt-in: set A2AT_OSP_LIVE=1 and configure the live model",
)


def _selected_ids() -> list[int]:
    """Return the case ids this run covers, from ``A2AT_OSP_LIVE_IDS`` when set."""
    value = os.environ.get("A2AT_OSP_LIVE_IDS", "")
    return [int(part) for part in value.split(",") if part.strip()]


def _case_params() -> list[Any]:
    """Return the parametrization of the shipped corpus (one row per case)."""
    if not _ENABLED:
        return []
    cases = select_cases(load_cases(), _selected_ids())
    return [pytest.param(case, id=f"case-{case['id']}") for case in cases]


@pytest.fixture(scope="module")
def live_client() -> Any:
    """Build one SDK client from the live configuration shared by every case."""
    if not _ENABLED:
        pytest.skip("live OSP evaluation is disabled")
    env_at_import = dict(_ENV_AT_IMPORT)
    os.environ.update(env_at_import)
    env_path = os.environ.get("A2AT_OSP_LIVE_ENV", "").strip()
    try:
        client, _env_dir = build_client_with_env(Path(env_path) if env_path else None)
        return client
    finally:
        # Nothing else in this module reads the environment; leave the process as the sample
        # conftest expects it.
        for key in env_at_import:
            os.environ.pop(key, None)


def _format_outcome(outcome: CaseOutcome) -> str:
    """Render the judged stages and their first diffs for a readable assertion message."""
    lines: list[str] = []
    for stage in (*outcome.stages, *(outcome.interception or ())):
        lines.append(f"{stage.stage}: {stage.status}")
        if stage.message:
            lines.append(f"  message: {stage.message}")
        for diff in stage.diffs[:5]:
            lines.append(
                f"  {diff.path}: expected={diff.expected!r} actual={diff.actual!r}"
            )
    if outcome.error:
        lines.append(f"error: {outcome.error}")
    return "\n".join(lines)


@pytest.mark.slow
@pytest.mark.parametrize("case", _case_params())
def test_osp_case_matches_the_tester_expectation(case: dict[str, Any], live_client: Any) -> None:
    """The SDK Task-T and the validation verdict match the case's tester expectation."""
    outcome = run_case(case, live_client, stop_after="validate")
    assert outcome.passed, (
        f"case {outcome.case_id} ({outcome.name}) did not match the corpus expectation\n"
        f"{_format_outcome(outcome)}"
    )
