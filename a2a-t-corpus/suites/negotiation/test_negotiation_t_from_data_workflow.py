# Copyright (c) 2026 Huawei Technologies Co., Ltd.
# All Rights Reserved.
#
# SPDX-License-Identifier: Apache-2.0
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.

"""Negotiation-T structured-input workflow suite (port of the Java
``NegotiationTFromDataWorkFlowTest``).

For every scenario directory under ``a2a-t-corpus/negotiation/resources/``, runs the recorded SDK
API flow of each case in ``input_case_from_data.json`` against a real LLM (the from-data
generation leg is deterministic; the validation leg uses the real semantic pipeline).

Default API flow (also shown in the case files): one ``generateNegotiation*PromptFromData`` step
followed by its matching ``validate*PromptAndDataFilling`` step; the concrete performative
(propose / accept / reject / abort) is chosen per case.

Filters: ``--corpus-scenario`` and ``--case-filter``, see
:mod:`test_negotiation_t_from_text_workflow`.
"""

from __future__ import annotations

import pytest
from engine.constants import (
    FLOW_FROM_DATA,
    INPUT_CASE_FROM_DATA,
    NEGOTIATION_API_NAMES,
)
from engine.suite import run_case

EXTENSION_FOLDER = "negotiation"
INPUT_CASE_FILE = INPUT_CASE_FROM_DATA
FLOW_TYPE = FLOW_FROM_DATA
RUNTIME_KIND = "negotiation"
API_NAMES = NEGOTIATION_API_NAMES


@pytest.mark.live
def test_negotiation_t_from_data_workflow(scenario, case, workflow_engine, workflow_context):  # noqa: ANN001
    """Execute one discovered from-data negotiation case and compare it against its expectations."""
    run_case(workflow_engine, scenario, case, workflow_context)
