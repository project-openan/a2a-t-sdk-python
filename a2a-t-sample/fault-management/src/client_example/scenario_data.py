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

"""Sample scenario input data for the fault-management e2e flow.

Contains the scenario tag, agent_card_query, subscription_filter, and
diagnosis_context used by the sample client. The prompt generation input is
selected based on the configured language (zh-CN / en-US), so the input
language matches `A2AT_LANGUAGE`.
"""

from __future__ import annotations

from pathlib import Path

from dotenv import dotenv_values

# Hard-coded natural language inputs used for prompt generation, one per
# supported language so the input language matches A2AT_LANGUAGE.
NATURAL_LANGUAGE_PROMPT_INPUT_ZH = (
    "请生成一个Incident事件订阅任务：通知主题为Incident，"
    "订阅条件为订阅级别为critical的ETH-LOS的故障，上报通知数据格式为DataPart"
)

NATURAL_LANGUAGE_PROMPT_INPUT_EN = (
    "Generate an Incident event subscription task: notification topic is Incident, "
    "subscription condition is a critical ETH-LOS fault, "
    "and the notification data format is DataPart"
)


def resolve_language(*, env_path: Path | None = None) -> str:
    """Resolve the configured A2AT_LANGUAGE (defaults to zh-CN)."""
    resolved = env_path or Path.cwd() / ".env"
    values = dotenv_values(resolved) if resolved.exists() else {}
    return str(values.get("A2AT_LANGUAGE", "")).strip() or "zh-CN"


def build_prompt_input(*, env_path: Path | None = None) -> str:
    """Return the prompt generation input matching the configured language."""
    if resolve_language(env_path=env_path) == "en-US":
        return NATURAL_LANGUAGE_PROMPT_INPUT_EN
    return NATURAL_LANGUAGE_PROMPT_INPUT_ZH


def build_subscription_request() -> dict[str, object]:
    """Build the sample subscription request input (scenario + agent query + filters)."""
    return {
        "scenario": "create incident subscription",
        "agent_card_query": {
            "name": "SPN Domain Agent",
            "organization": "Huawei",
        },
        "subscription_filter": {
            "alarm_type": "flash",
            "severity": "critical",
            "province": "fujian",
        },
        "diagnosis_context": {
            "domain": "spn",
            "source": "transport_workbench",
        },
    }
