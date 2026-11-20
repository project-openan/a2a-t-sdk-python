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

"""Transcript and summary reporting of the corpus (port of the Java ``TranscriptWriter`` and
``ResultAggregator``).

The transcript of one scenario is written to ``output_result_<flow>.json`` — each run fully
overwrites the file, so a filtered run reflects exactly the executed cases; the default
destination is the scenario directory itself. The run summary aggregates accuracy (95% target
line), latency percentiles and token totals into ``summary_report_<flow>.json`` plus a console
table, both written under ``<corpus>/.corpus`` by default.
"""

from __future__ import annotations

import json
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any

from engine.discover import corpus_root
from engine.engine import CaseResult

__all__ = ["ResultAggregator", "TranscriptWriter"]

#: 95%+ accuracy target required by the accuracy verification directive.
ACCURACY_TARGET: float = 0.95


class TranscriptWriter:
    """Writes the execution transcript of one scenario to ``output_result_<flow>.json``."""

    @staticmethod
    def write_scenario_output(
        flow_type: str,
        scenario_name: str,
        default_scenario_dir: Path,
        results: list[CaseResult],
        output_dir_override: Path | None = None,
    ) -> Path:
        """Write the results of one scenario; returns the written file."""
        output_dir = TranscriptWriter._output_dir(scenario_name, default_scenario_dir, output_dir_override)
        output_dir.mkdir(parents=True, exist_ok=True)
        file = output_dir / f"output_result_{flow_type}.json"
        records = [TranscriptWriter._to_record(result) for result in results]
        file.write_text(json.dumps(records, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return file

    @staticmethod
    def _output_dir(
        scenario_name: str,
        default_scenario_dir: Path,
        output_dir_override: Path | None,
    ) -> Path:
        if output_dir_override is not None:
            return output_dir_override.resolve() / scenario_name
        return default_scenario_dir

    @staticmethod
    def _to_record(result: CaseResult) -> dict[str, Any]:
        record: dict[str, Any] = {
            "id": result.input_case.id,
            "caseDimension": result.input_case.case_dimension,
            "caseDesc": result.input_case.case_desc,
            "caseCategory": result.input_case.case_category,
            "totalTime": result.total_duration_ms,
            "totalInputToken": result.total_input_token,
            "totalOutputToken": result.total_output_token,
            "totalToken": result.total_input_token + result.total_output_token,
            "result": result.verdict,
        }
        if result.fail_reason:
            record["failReason"] = result.fail_reason
        interactions: list[dict[str, Any]] = []
        for interaction in result.interactions:
            interactions.append(
                {
                    "step": str(interaction.step),
                    "type": interaction.type,
                    "result": interaction.result,
                    "duration_ms": interaction.duration_ms,
                    "request": interaction.request,
                    "response": interaction.response,
                    "inputToken": interaction.input_token,
                    "outputToken": interaction.output_token,
                }
            )
        record["output"] = {"interactions": interactions}
        return record


class ResultAggregator:
    """Aggregates one run into accuracy / latency / token statistics and writes the summary."""

    @staticmethod
    def write_summary(
        flow_type: str,
        results: list[CaseResult],
        summary_dir_override: Path | None = None,
    ) -> Path:
        """Compute and persist the run summary; returns the written file."""
        summary = ResultAggregator.summarize(results)
        directory = summary_dir_override or corpus_root() / ".corpus"
        directory.mkdir(parents=True, exist_ok=True)
        file = directory / f"summary_report_{flow_type}.json"
        file.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        ResultAggregator._print_table(flow_type, summary)
        return file

    @staticmethod
    def print_case_line(result: CaseResult) -> None:
        """Print one case result as it completes; failures print their full interaction trace."""
        fail = "" if not result.fail_reason else f" | fail: {result.fail_reason}"
        print(
            f"[case] {result.scenario}/{result.input_case.id} => {result.verdict} | "
            f"{result.total_duration_ms}ms | tokens "
            f"{result.total_input_token + result.total_output_token}{fail}"
        )
        if result.verdict == "success":
            return
        for interaction in result.interactions:
            detail = ""
            code = interaction.response.get("errorCode")
            message = interaction.response.get("message")
            if code is not None or message is not None:
                detail = f" | {code or ''} {_abbreviate(str(message), 160)}"
            print(
                f"    s{interaction.step:<4d} {interaction.type:<30s} {interaction.result:<8s} "
                f"{interaction.duration_ms:6d}ms in={interaction.input_token} "
                f"out={interaction.output_token}{detail}"
            )

    @staticmethod
    def summarize(results: list[CaseResult]) -> dict[str, Any]:
        """Compute the full run summary structure."""
        return {
            "caseTotal": len(results),
            "accuracyTarget": ACCURACY_TARGET,
            "rows": ResultAggregator._case_rows(results),
            "apiStepRows": ResultAggregator._api_step_rows(results),
            "durationMs": ResultAggregator._percentile_stats(
                [interaction.duration_ms for result in results for interaction in result.interactions
                 if interaction.type != "LLM"]
            ),
            "tokens": ResultAggregator._token_stats(results),
        }

    @staticmethod
    def _case_rows(results: list[CaseResult]) -> list[dict[str, Any]]:
        buckets: dict[tuple[str, str], _Bucket] = {}
        for result in results:
            _count_case(buckets, "overall", "overall", result)
            _count_case(buckets, "scenario", result.scenario, result)
            _count_case(buckets, "dimension", result.input_case.case_dimension, result)
            _count_case(buckets, "category", result.input_case.case_category, result)
        return _bucket_rows(list(buckets.values()))

    @staticmethod
    def _api_step_rows(results: list[CaseResult]) -> list[dict[str, Any]]:
        buckets: dict[tuple[str, str], _Bucket] = {}
        for result in results:
            for interaction in result.interactions:
                if interaction.type == "LLM" or interaction.result == "skipped":
                    continue
                bucket = buckets.setdefault(("api", interaction.type), _Bucket("api", interaction.type))
                bucket.count(interaction.result == "success")
        return _bucket_rows(sorted(buckets.values(), key=lambda bucket: (bucket.scope, bucket.key)))

    @staticmethod
    def _percentile_stats(durations: list[int]) -> dict[str, Any]:
        stats: dict[str, Any] = {"count": len(durations)}
        if durations:
            sorted_durations = sorted(durations)
            size = len(sorted_durations)
            stats["p50"] = sorted_durations[min(size - 1, size * 50 // 100)]
            stats["p95"] = sorted_durations[min(size - 1, size * 95 // 100)]
            stats["max"] = sorted_durations[size - 1]
        return stats

    @staticmethod
    def _token_stats(results: list[CaseResult]) -> dict[str, Any]:
        input_tokens = sum(result.total_input_token for result in results)
        output_tokens = sum(result.total_output_token for result in results)
        return {
            "totalInputToken": input_tokens,
            "totalOutputToken": output_tokens,
            "totalToken": input_tokens + output_tokens,
            "meanInputTokenPerCase": round(input_tokens / len(results)) if results else 0,
            "meanOutputTokenPerCase": round(output_tokens / len(results)) if results else 0,
        }

    @staticmethod
    def _print_table(flow_type: str, summary: dict[str, Any]) -> None:
        print()
        print(
            f"===== a2a-t-corpus run summary ({flow_type}, target "
            f"{ACCURACY_TARGET * 100:.0f}%)====="
        )
        print(f"{'scope':<12s} {'key':<30s} {'total':>6s} {'success':>8s} {'accuracy':>9s} {'meets':>8s}")
        for row in summary["rows"]:
            print(
                f"{row['scope']:<12s} {_abbreviate(str(row['key']), 30):<30s} "
                f"{row['total']:6d} {row['success']:8d} {row['accuracy']:9.2f}% "
                f"{'YES' if row['meetsTarget'] else 'NO':>8s}"
            )
        tokens = summary["tokens"]
        print(
            f"Token totals: input={tokens['totalInputToken']} output={tokens['totalOutputToken']} "
            f"total={tokens['totalToken']}"
        )


class _Bucket:
    __slots__ = ("scope", "key", "total", "success")

    def __init__(self, scope: str, key: str) -> None:
        self.scope = scope
        self.key = key
        self.total = 0
        self.success = 0

    def count(self, passed: bool) -> None:
        """Count one verdict (True = success)."""
        self.total += 1
        if passed:
            self.success += 1


def _count_case(
    buckets: dict[tuple[str, str], _Bucket],
    scope: str,
    key: str,
    result: CaseResult,
) -> None:
    bucket = buckets.setdefault((scope, key), _Bucket(scope, key))
    bucket.count(result.verdict == "success")


def _bucket_rows(buckets: list[_Bucket]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for bucket in sorted(buckets, key=lambda item: (item.scope, item.key)):
        accuracy = _percentage(bucket.success, bucket.total)
        rows.append(
            {
                "scope": bucket.scope,
                "key": bucket.key,
                "total": bucket.total,
                "success": bucket.success,
                "failure": bucket.total - bucket.success,
                "accuracy": accuracy,
                # Accuracy rows carry percent values, so the target line compares against
                # ACCURACY_TARGET * 100 (the Java original compared the percentage against the
                # 0.95 fraction, marking every non-empty bucket as meeting the target).
                "meetsTarget": bool(bucket.total > 0 and accuracy >= ACCURACY_TARGET * 100),
            }
        )
    return rows


def _percentage(success: int, total: int) -> float:
    """Return the rounded two-decimal percentage (Java ``RoundingMode.HALF_UP`` parity)."""
    if total == 0:
        return 0.0
    value = Decimal(success * 100) / Decimal(total)
    return float(value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def _abbreviate(value: str, max_length: int) -> str:
    return value if len(value) <= max_length else value[: max_length - 1] + "…"
