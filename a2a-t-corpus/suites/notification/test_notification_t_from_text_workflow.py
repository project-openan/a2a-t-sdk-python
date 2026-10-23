"""Notification-T natural-language workflow suite.

For every scenario directory under ``a2a-t-corpus/suites/notification/resources/``, runs the recorded
SDK API flow of each case in ``input_case_from_text.json`` against a real LLM.

Default API flow (also shown in the case files): ``generateNotificationPromptFromText`` →
``validateNotificationPromptAndDataFilling``.

Filters: ``--corpus-scenario`` (glob, comma-separated) and ``--case-filter`` (glob on case id).
"""

from __future__ import annotations

import pytest
from engine.constants import FLOW_FROM_TEXT, INPUT_CASE_FROM_TEXT, NOTIFICATION_API_NAMES
from engine.suite import run_case

EXTENSION_FOLDER = "notification"
INPUT_CASE_FILE = INPUT_CASE_FROM_TEXT
FLOW_TYPE = FLOW_FROM_TEXT
RUNTIME_KIND = "notification"
API_NAMES = NOTIFICATION_API_NAMES


@pytest.mark.live
def test_notification_t_from_text_workflow(scenario, case, workflow_engine, workflow_context):  # noqa: ANN001
    """Execute one discovered from-text case and compare it against its expectations."""
    run_case(workflow_engine, scenario, case, workflow_context)
