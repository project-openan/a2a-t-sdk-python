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

"""Suite support of the corpus (the Python counterpart of the Java ``CorpusWorkFlowSuite`` glue).

One :class:`WorkflowRunContext` collects the executed case results of one workflow suite module
and, on teardown, writes the per-scenario transcripts and the run summary — the Python analog of
the Java ``@AfterAll`` wiring.
"""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path

from engine.discover import Scenario
from engine.engine import CaseResult, WorkflowEngine
from engine.loader import InputCase
from engine.report import ResultAggregator, TranscriptWriter

__all__ = ["WorkflowRunContext", "run_case"]


class WorkflowRunContext:
    """Collects the results of one workflow suite module and writes its transcript on teardown."""

    def __init__(self, flow_type: str) -> None:
        self._flow_type = flow_type
        self._results: list[CaseResult] = []
        self._scenario_dirs: dict[str, Path] = {}

    def record(self, scenario: Scenario, result: CaseResult) -> None:
        """Append one executed case."""
        self._results.append(result)
        self._scenario_dirs[scenario.name] = scenario.source_dir

    def write_outputs(self, *, output_dir_override: Path | None, summary_dir_override: Path | None) -> None:
        """Write the per-scenario transcripts and the run summary of the executed cases."""
        grouped: dict[str, list[CaseResult]] = defaultdict(list)
        for result in self._results:
            grouped[result.scenario].append(result)
        for scenario_name, results in grouped.items():
            result_file = TranscriptWriter.write_scenario_output(
                self._flow_type,
                scenario_name,
                self._scenario_dirs[scenario_name],
                results,
                output_dir_override,
            )
            print(f"Output: {result_file}")
        summary_file = ResultAggregator.write_summary(
            self._flow_type, self._results, summary_dir_override
        )
        print(f"Summary: {summary_file}")


def run_case(
    engine: WorkflowEngine,
    scenario: Scenario,
    input_case: InputCase,
    context: WorkflowRunContext,
) -> None:
    """Execute one case and assert its verdict; the transcript row prints as it completes."""
    result = engine.run(scenario.name, input_case)
    context.record(scenario, result)
    ResultAggregator.print_case_line(result)
    assert result.verdict == "success", (
        result.fail_reason or f"case verdict={result.verdict}, see the output JSON"
    )
