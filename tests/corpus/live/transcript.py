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

"""Transcript writer of the live corpus (port of the Java ``LiveTranscript``; design document §5).

Every run of the live suite leaves its full evidence under ``.live-corpus/<yyyyMMdd-HHmmss>/`` of
the repository root, so a live run can be judged — and its prompts recalibrated — after the fact.
The whole tree is gitignored and therefore never committed.

A run accumulates its :class:`~tests.corpus.live.results.LiveCaseResult` verdicts in memory;
:meth:`LiveTranscriptRun.write` flushes once at the end into two pretty-printed files:
``transcript.json`` (the JSON array of case verdicts, Java camelCase field names so the export
tool and existing readers keep working) and ``summary.json`` (the aggregated
:class:`~tests.corpus.live.results.LiveRunSummary`, including the M5 schema-adherence statistic).
The run directory is created by :func:`create_run` — evaluated when the suite's module fixtures
are set up, per the design — with a second-precision timestamp and a uniquifying suffix when two
runs start within the same second.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from threading import Lock
from typing import Final

from tests.corpus.live.results import LiveCaseResult, LiveRunSummary

__all__ = ["LiveTranscript", "LiveTranscriptRun"]

#: Directory-name pattern of one run, second-precision as the design specifies.
_RUN_DIRECTORY_FORMAT: Final[str] = "%Y%m%d-%H%M%S"

#: Name of the case-verdict array file of one run.
TRANSCRIPT_FILE: Final[str] = "transcript.json"

#: Name of the aggregate file of one run.
SUMMARY_FILE: Final[str] = "summary.json"

#: Default transcript root of the configured runs (gitignored, never committed).
DEFAULT_ROOT: Final[Path] = Path(__file__).resolve().parents[3] / ".live-corpus"


class LiveTranscript:
    """Factory of live transcript runs (the Java static-utility class counterpart)."""

    @staticmethod
    def create_run(root_dir: Path | None = None) -> LiveTranscriptRun:
        """Create a run directory under the given root (default: ``.live-corpus`` of the repo).

        Args:
            root_dir: root directory the timestamped run directory is created under; ``None``
                resolves the default repository-root ``.live-corpus`` directory.

        Returns:
            handle of the new run, with its timestamped directory already created.

        Raises:
            OSError: when the run directory cannot be created.
        """
        root = DEFAULT_ROOT if root_dir is None else root_dir
        stamp = datetime.now().strftime(_RUN_DIRECTORY_FORMAT)
        directory = root / stamp
        uniquifier = 1
        while directory.exists():
            directory = root / f"{stamp}-{uniquifier}"
            uniquifier += 1
        directory.mkdir(parents=True, exist_ok=False)
        return LiveTranscriptRun(directory)


class LiveTranscriptRun:
    """Handle of one live transcript run: it accumulates the case verdicts of one suite execution
    and flushes them once."""

    __slots__ = ("_directory", "_cases", "_lock")

    def __init__(self, directory: Path) -> None:
        """Carry the already-created run directory the verdicts are flushed into."""
        self._directory = directory
        self._cases: list[LiveCaseResult] = []
        self._lock = Lock()

    @property
    def directory(self) -> Path:
        """Timestamped run directory, already created."""
        return self._directory

    def append_case(self, result: LiveCaseResult) -> None:
        """Append one case verdict to the run.

        Raises:
            TypeError: when ``result`` is ``None``.
        """
        if result is None:
            raise TypeError("result")
        with self._lock:
            self._cases.append(result)

    def summary(self) -> LiveRunSummary:
        """Return the aggregate of the cases appended so far."""
        with self._lock:
            return _summarize(self._cases)

    def write(self) -> Path:
        """Flush the run into its directory: the pretty-printed JSON array of case verdicts as
        ``transcript.json`` and the aggregate as ``summary.json``.

        Returns:
            path of the written ``transcript.json``.

        Raises:
            OSError: when either file cannot be written.
        """
        with self._lock:
            cases = list(self._cases)
            summary = _summarize(cases)
            transcript_file = self._directory / TRANSCRIPT_FILE
            transcript_file.write_text(
                json.dumps(
                    [result.to_json() for result in cases],
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
                newline="\n",
            )
            (self._directory / SUMMARY_FILE).write_text(
                json.dumps(summary.to_json(), ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
                newline="\n",
            )
            return transcript_file


def _summarize(cases: list[LiveCaseResult]) -> LiveRunSummary:
    """Aggregate the case verdicts into the run summary."""
    pass_count = 0
    fail_count = 0
    skip_count = 0
    error_count = 0
    total_llm_calls = 0
    total_prompt_tokens = 0
    total_completion_tokens = 0
    schema_parse_failure_count = 0
    for result in cases:
        if result.outcome.value == "PASS":
            pass_count += 1
        elif result.outcome.value == "FAIL":
            fail_count += 1
        elif result.outcome.value == "SKIP":
            skip_count += 1
        else:
            error_count += 1
        for call in result.llm_calls:
            total_llm_calls += 1
            total_prompt_tokens += call.usage.get("prompt_tokens") or 0
            total_completion_tokens += call.usage.get("completion_tokens") or 0
            if not call.failed and not _content_is_json_object(call.content):
                schema_parse_failure_count += 1
    return LiveRunSummary(
        len(cases),
        pass_count,
        fail_count,
        skip_count,
        error_count,
        total_llm_calls,
        total_prompt_tokens,
        total_completion_tokens,
        schema_parse_failure_count,
    )


def _content_is_json_object(content: str | None) -> bool:
    """Return whether an answered content is a JSON object.

    The ``None`` content of a failed call does not count, only a real non-object answer does (the
    M5 schema-adherence statistic).
    """
    try:
        return isinstance(json.loads(content or ""), dict)
    except (ValueError, TypeError):
        return False
