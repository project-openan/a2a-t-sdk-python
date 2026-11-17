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

from __future__ import annotations

from collections.abc import Mapping

from .sectioned_renderer import collapse_sections


class TaskPromptRenderer:
    """Render task prompt templates into plain prompt bodies (collapse policy).

    Thin orchestrator-facing adapter over :func:`collapse_sections`: the task prompt
    pipeline keeps the section scaffolding and collapses standalone slot lines to their
    slot values (Java ``TaskPromptRenderer``). The ``scenario_code`` / ``language`` /
    ``description`` arguments exist for orchestrator wiring only — they are not part of
    the rendering grammar, mirroring the Java renderer which does not receive them
    either.
    """

    def render(
        self,
        *,
        template_text: str,
        slots: Mapping[str, str | None],
        scenario_code: str,
        language: str,
        description: str,
    ) -> str:
        """Render a processed task prompt body from a template and extracted slots."""
        return collapse_sections(template_text, slots)
