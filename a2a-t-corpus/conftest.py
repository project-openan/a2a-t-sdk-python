"""Pytest outlets of the accuracy verification corpus (the Python counterpart of the Java
``CorpusWorkFlowSuite`` machinery).

- CLI filters: ``--corpus-scenario`` (scenario glob), ``--case-filter`` (case id glob) and
  ``--corpus-output-dir`` (transcript/summary redirect).
- ``workflow_runtime``: the shared module-selected SDK runtime (one session singleton per
  extension). It resolves lazily on the first executed workflow case, so collection and the
  CI-deselected runs never touch the LLM config — an unconfigured or invalid ``A2AT_LLM_*`` setup
  fails fast with an actionable error exactly when someone actually tries to run the live suites.
- ``pytest_generate_tests``: parametrizes the workflow suite modules over the discovered scenario
  cases (test id = ``<scenario>/<case id>``), the ``@TestFactory`` counterpart.
- ``A2AT_LANGUAGE`` (default ``zh-CN``) selects the prompt language and the case-file variant, for
  example ``input_case_from_text_en-US.json``; a language without any committed case file skips.

Suite modules declare four constants: ``EXTENSION_FOLDER``, ``INPUT_CASE_FILE``, ``FLOW_TYPE`` and
``RUNTIME_KIND`` (``"task"``, ``"negotiation"`` or ``"notification"``); ``API_NAMES`` defaults to the
Task-T set when omitted.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from engine.config import resolve_language
from engine.constants import DEFAULT_LANGUAGE, TASK_API_NAMES, localized_case_file
from engine.discover import ScenarioScanner
from engine.loader import CaseFileLoader

_WORKFLOW_FIXTURES = frozenset({"scenario", "case"})


def pytest_addoption(parser: pytest.Parser) -> None:
    """Register the corpus CLI filters used by workflow runs."""
    parser.addoption(
        "--corpus-scenario",
        default=None,
        help="scenario name glob (comma-separated); default runs every scenario",
    )
    parser.addoption(
        "--case-filter",
        default=None,
        help="case id glob inside the matched scenarios; default runs every case",
    )
    parser.addoption(
        "--corpus-output-dir",
        default=None,
        help="redirect transcripts and the summary into this directory",
    )


def _runtime_kind(request: pytest.FixtureRequest) -> str:
    """Read the suite's runtime kind from its module, defaulting to Task-T."""
    return getattr(request.module, "RUNTIME_KIND", "task")


@pytest.fixture(scope="session")
def corpus_language() -> str:
    """The configured prompt and case-file language, resolved before the LLM runtime loads."""
    return resolve_language()


@pytest.fixture(scope="session")
def task_workflow_runtime() -> Any:
    """The shared Task-T SDK runtime, assembled lazily on first use.

    Raises:
        RuntimeError: when the corpus LLM configuration is missing or invalid.
    """
    from engine.assembler import task_runtime

    return task_runtime()


@pytest.fixture(scope="session")
def negotiation_workflow_runtime() -> Any:
    """The shared Negotiation-T SDK runtime, assembled lazily on first use.

    Raises:
        RuntimeError: when the corpus LLM configuration is missing or invalid.
    """
    from engine.assembler import negotiation_runtime

    return negotiation_runtime()


@pytest.fixture(scope="session")
def notification_workflow_runtime() -> Any:
    """The shared Notification-T SDK runtime, assembled lazily on first use.

    Raises:
        RuntimeError: when the corpus LLM configuration is missing or invalid.
    """
    from engine.assembler import notification_runtime

    return notification_runtime()


@pytest.fixture(scope="module")
def workflow_runtime(request: pytest.FixtureRequest) -> Any:
    """The module-selected runtime singleton (one per extension, shared across its suites)."""
    kind = _runtime_kind(request)
    if kind == "negotiation":
        return request.getfixturevalue("negotiation_workflow_runtime")
    if kind == "notification":
        return request.getfixturevalue("notification_workflow_runtime")
    return request.getfixturevalue("task_workflow_runtime")


@pytest.fixture(scope="session")
def summary_output_dir(request: pytest.FixtureRequest) -> Path | None:
    """The configured run-output override (transcripts and summary), or ``None`` for the defaults."""
    raw = request.config.getoption("--corpus-output-dir")
    return None if not raw else Path(raw).resolve()


@pytest.fixture(scope="module")
def workflow_engine(request: pytest.FixtureRequest, workflow_runtime: Any) -> Any:
    """One workflow engine shared by every case of the suite module."""
    from engine.engine import WorkflowEngine

    kind = _runtime_kind(request)
    if kind == "negotiation":
        from engine.assembler import build_negotiation_registry

        registry = build_negotiation_registry(workflow_runtime)
    elif kind == "notification":
        from engine.assembler import build_notification_registry

        registry = build_notification_registry(workflow_runtime)
    else:
        from engine.assembler import build_task_registry

        registry = build_task_registry(workflow_runtime)
    return WorkflowEngine(registry, workflow_runtime.recorder)


@pytest.fixture(scope="module")
def workflow_context(
    request: pytest.FixtureRequest,
    summary_output_dir: Path | None,
    corpus_language: str,
) -> Any:
    """The result collector of one suite module; the transcript writes on teardown."""
    from engine.suite import WorkflowRunContext

    flow_type = getattr(request.module, "FLOW_TYPE", None)
    if not flow_type:
        pytest.fail("workflow suite modules must declare FLOW_TYPE", pytrace=False)
    run_flow_type = flow_type if corpus_language == DEFAULT_LANGUAGE else f"{flow_type}_{corpus_language}"
    context = WorkflowRunContext(run_flow_type)

    def finalize() -> None:
        context.write_outputs(output_dir_override=summary_output_dir, summary_dir_override=summary_output_dir)

    request.addfinalizer(finalize)
    return context


def pytest_generate_tests(metafunc: pytest.Metafunc) -> None:
    """Parametrize the workflow suite tests over the discovered scenario cases."""
    if not _WORKFLOW_FIXTURES.issubset(metafunc.fixturenames):
        return
    extension_folder = getattr(metafunc.module, "EXTENSION_FOLDER", None)
    base_flow_file = getattr(metafunc.module, "INPUT_CASE_FILE", None)
    if not extension_folder or not base_flow_file:
        return

    language = resolve_language()
    flow_file_name = localized_case_file(base_flow_file, language)
    scenario_filter = metafunc.config.getoption("--corpus-scenario")
    case_filter = metafunc.config.getoption("--case-filter")
    scenarios = ScenarioScanner.discover_optional(extension_folder, flow_file_name)
    if not scenarios:
        reason = (
            f"no {language} corpus case file found: expected "
            f"suites/{extension_folder}/resources/<scenario>/{flow_file_name}; add the "
            f"language-specific case file or set A2AT_LANGUAGE={DEFAULT_LANGUAGE}"
        )
        metafunc.parametrize(
            ("scenario", "case"),
            [pytest.param(None, None, marks=pytest.mark.skip(reason))],
            ids=["none"],
        )
        return
    scenarios = ScenarioScanner.filter_scenarios(scenarios, scenario_filter)
    loader = CaseFileLoader()
    parameters: list[tuple[Any, Any]] = []
    ids: list[str] = []
    for scenario in scenarios:
        for input_case in loader.load(
            scenario.input_file(flow_file_name),
            getattr(metafunc.module, "API_NAMES", TASK_API_NAMES),
        ):
            if not ScenarioScanner.matches_case_filter(case_filter, input_case.id):
                continue
            parameters.append((scenario, input_case))
            ids.append(f"{scenario.name}/{input_case.id}")
    if not parameters:
        metafunc.parametrize(
            ("scenario", "case"),
            [pytest.param(None, None, marks=pytest.mark.skip("no corpus case matched the filters"))],
            ids=["none"],
        )
        return
    metafunc.parametrize(("scenario", "case"), parameters, ids=ids)
