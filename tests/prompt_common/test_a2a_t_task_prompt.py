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

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = PROJECT_ROOT / "src"

if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))


from a2a_t.prompt.common.models import PromptReference


class A2ATTaskPromptCommonTest(unittest.TestCase):
    def test_render_and_parse_a2a_t_task_prompt_share_one_protocol(self) -> None:
        from a2a_t.prompt.common.models import TaskPromptMetadata
        from a2a_t.prompt.common.task_prompt_format import (
            format_task_prompt,
            parse_task_prompt_metadata,
        )

        prompt_text = format_task_prompt(
            body="Site: Site A",
            metadata=TaskPromptMetadata(
                scenario_code="ran-energy-saving",
                language="en-US",
                description="Used for energy saving analysis.",
            ),
        )
        metadata = parse_task_prompt_metadata(prompt_text)

        self.assertEqual(
            metadata,
            TaskPromptMetadata(
                scenario_code="ran-energy-saving",
                language="en-US",
                description="Used for energy saving analysis.",
            ),
        )
        self.assertEqual(
            metadata.to_prompt_reference(),
            PromptReference(scenario_code="ran-energy-saving", language="en-US"),
        )

    def test_parse_rejects_missing_language(self) -> None:
        from a2a_t.prompt.common.errors import TaskPromptFormatError
        from a2a_t.prompt.common.task_prompt_format import parse_task_prompt_metadata

        with self.assertRaises(TaskPromptFormatError) as context:
            parse_task_prompt_metadata(
                "---\n"
                "scenario_code: ran-energy-saving\n"
                "description: Used for energy saving analysis.\n"
                "---\n\n"
                "Site: Site A"
            )

        self.assertEqual(context.exception.field, "language")

    def test_parse_rejects_missing_description(self) -> None:
        from a2a_t.prompt.common.errors import TaskPromptFormatError
        from a2a_t.prompt.common.task_prompt_format import parse_task_prompt_metadata

        with self.assertRaises(TaskPromptFormatError) as context:
            parse_task_prompt_metadata("---\nscenario_code: ran-energy-saving\nlanguage: en-US\n---\n\nSite: Site A")

        self.assertEqual(context.exception.field, "description")

    def test_parse_rejects_blank_language(self) -> None:
        from a2a_t.prompt.common.errors import TaskPromptFormatError
        from a2a_t.prompt.common.task_prompt_format import parse_task_prompt_metadata

        with self.assertRaises(TaskPromptFormatError) as context:
            parse_task_prompt_metadata(
                "---\n"
                "scenario_code: ran-energy-saving\n"
                "language:    \n"
                "description: Used for energy saving analysis.\n"
                "---\n\n"
                "Site: Site A"
            )

        self.assertEqual(context.exception.field, "language")

    def test_parse_rejects_blank_description(self) -> None:
        from a2a_t.prompt.common.errors import TaskPromptFormatError
        from a2a_t.prompt.common.task_prompt_format import parse_task_prompt_metadata

        with self.assertRaises(TaskPromptFormatError) as context:
            parse_task_prompt_metadata(
                "---\nscenario_code: ran-energy-saving\nlanguage: en-US\ndescription:    \n---\n\nSite: Site A"
            )

        self.assertEqual(context.exception.field, "description")


if __name__ == "__main__":
    unittest.main()
