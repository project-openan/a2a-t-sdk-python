#!/usr/bin/env python3
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

"""Generates and freshness-checks INDEX.md for the A2A-T negotiation test corpus.

Python port of the Java repository's ``tools/corpus_index.py`` (1.1.0, 363 lines) with the corpus
root relocated to ``tests/resources/negotiation-cases/``. Scans every corpus JSON under the
negotiation-cases root (case files, scenario files and the shared payload/schema maps), renders a
machine-generated index -- top statistics, the API x category x expected-outcome coverage matrices
and one line per case -- and writes it to ``negotiation-cases/INDEX.md``.

Per D25 the freshness check stays a soft, local-only gate: the Java repo explicitly revoked the
CI freshness check, so this tool is never wired into CI as a hard gate. With ``--check`` the
rendered index is compared against the file on disk and the exit code reports drift (0 = fresh,
1 = stale) for local use only.

Usage:
  uv run python tools/corpus_index.py            # regenerate INDEX.md
  uv run python tools/corpus_index.py --check    # compare without writing; exit 1 on drift
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CORPUS_ROOT = REPO_ROOT / "tests" / "resources" / "negotiation-cases"
INDEX_NAME = "INDEX.md"
SCHEMA_NAME = "corpus-schema.json"

APIS = (
    "generateProposeFromText",
    "generateAcceptFromText",
    "generateRejectFromText",
    "generateAbortFromText",
    "generateProposeFromData",
    "generateAcceptFromData",
    "generateRejectFromData",
    "generateAbortFromData",
    "validateProposePromptAndDataFilling",
    "validateAcceptPromptAndDataFilling",
    "validateRejectPromptAndDataFilling",
    "validateAbortPromptAndDataFilling",
    # the three closed-loop task APIs (Q21): exercised as scenario steps, never as standalone case records
    "generateTaskPromptFromText",
    "generateTaskPromptFromDataWithSchema",
    "validateTaskPromptAndDataFilling",
)
FAMILY_ORDER = ("FT", "FD", "VAL", "SC")
FAMILY_LABELS = {"FT": "from-text", "FD": "from-data", "VAL": "validate", "SC": "scenarios"}


def error(path: Path, rule: str, message: str) -> str:
    """Render one corpus violation for the error channel."""
    return f"{path}: [{rule}] {message}"


class Case:
    """One offline case record of the corpus."""

    def __init__(self, path: Path, record: dict[str, Any]) -> None:
        self.path = path
        self.id: str = record["id"]
        self.api: str = record["api"]
        self.languages: list[str] = list(record.get("languages", []))
        self.priority: str = record.get("priority", "-")
        self.summary: str = record.get("summary", "")
        self.expect: dict[str, Any] = record["expect"]


class Scenario:
    """One scenario record of the corpus."""

    def __init__(self, path: Path, record: dict[str, Any]) -> None:
        self.path = path
        self.id: str = record["id"]
        self.languages: list[str] = list(record.get("languages", []))
        self.summary: str = record.get("summary", "")
        self.steps: list[dict[str, Any]] = list(record["steps"])
        self.expect_flow: dict[str, Any] = record.get("expectFlow", {})


def family_of(record_id: str, path: Path, errors: list[str]) -> str | None:
    """Classify one record id by its family prefix, or report a corpus-id violation."""
    family = record_id.split("-", 1)[0]
    if family not in FAMILY_LABELS:
        errors.append(error(path, "corpus-id", f"Record id '{record_id}' does not start with a known family prefix."))
        return None
    return family


def category_of(record_id: str) -> str:
    """The category (family + second id segment) one record id belongs to."""
    return "-".join(record_id.split("-")[:2])


def outcome_of(expect: dict[str, Any]) -> str:
    """The expected-outcome key of one expectation block."""
    if expect.get("outcome") == "success":
        return "success"
    if "code" in expect:
        return str(expect["code"])
    return f"exception:{expect.get('exception', '?')}"


def outcome_label(expect: dict[str, Any]) -> str:
    """The expected-outcome rendering of the per-record listing."""
    if expect.get("outcome") == "success":
        return "success"
    parts = []
    if "exception" in expect:
        parts.append(str(expect["exception"]))
    if "code" in expect:
        parts.append(str(expect["code"]))
    return "(" + ", ".join(parts) + ")" if parts else "(failure)"


def load_corpus(root: Path) -> tuple[list[Case], list[Scenario], dict[str, int], list[str]]:
    """Load every corpus JSON, classifying case files, scenario files and the shared maps."""
    cases: list[Case] = []
    scenarios: list[Scenario] = []
    shared = {"payloads": 0, "schemas": 0}
    errors: list[str] = []
    for path in sorted(root.rglob("*.json")):
        relative = path.relative_to(root).as_posix()
        if path.name == SCHEMA_NAME:
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            errors.append(error(path, "corpus-json", f"Cannot load corpus file: {exc}"))
            continue
        if relative.startswith("shared/"):
            if not isinstance(data, dict):
                errors.append(error(path, "corpus-shared", "Shared corpus files must be JSON objects."))
                continue
            if path.name == "llm-responses.json":
                shared["payloads"] = len(data)
            elif path.name == "schemas.json":
                shared["schemas"] = len(data)
            continue
        # The live family targets a real model and is not part of the offline coverage index.
        if relative.startswith("live/"):
            continue
        if not isinstance(data, list):
            errors.append(error(path, "corpus-file", "Corpus files must be JSON arrays of records."))
            continue
        for record in data:
            if not isinstance(record, dict):
                errors.append(error(path, "corpus-record", "Corpus records must be JSON objects."))
                continue
            record_id = record.get("id")
            if not isinstance(record_id, str):
                errors.append(error(path, "corpus-record", "Corpus record is missing its 'id'."))
                continue
            if family_of(record_id, path, errors) is None:
                continue
            if "steps" in record:
                scenarios.append(Scenario(path, record))
            else:
                cases.append(Case(path, record))
    return cases, scenarios, shared, errors


def category_sort_key(category: str) -> tuple[int, str]:
    """Sort key of one category: family order first, then the category name."""
    family = category.split("-", 1)[0]
    return (FAMILY_ORDER.index(family), category)


def render_flow(expect_flow: dict[str, Any]) -> str:
    """Render the flow expectation of one scenario."""
    if not expect_flow:
        return "-"
    parts = []
    if "terminalCondition" in expect_flow:
        parts.append(str(expect_flow["terminalCondition"]))
    if "roundsUsed" in expect_flow:
        parts.append(f"rounds={expect_flow['roundsUsed']}")
    if expect_flow.get("distinctMessages"):
        parts.append("distinct-messages")
    return ", ".join(parts) if parts else "-"


def render_index(root: Path) -> str:
    """Render the whole INDEX.md content of the corpus under the given root."""
    cases, scenarios, shared, errors = load_corpus(root)
    if errors:
        for item in errors:
            print(item, file=sys.stderr)
        raise SystemExit(1)

    # Coverage units: one base case record, or one scenario step, keyed by (api, category, outcome).
    units: list[tuple[str, str, str]] = []
    for case in cases:
        units.append((case.api, category_of(case.id), outcome_of(case.expect)))
    for scenario in scenarios:
        category = category_of(scenario.id)
        for step in scenario.steps:
            units.append((step["api"], category, outcome_of(step["expect"])))

    error_codes = sorted(
        {outcome for _, _, outcome in units if outcome != "success" and not outcome.startswith("exception:")}
    )
    exception_types = sorted(
        {outcome[len("exception:") :] for _, _, outcome in units if outcome.startswith("exception:")}
    )
    outcomes = ["success", *error_codes, "exception"]
    categories = sorted({category for _, category, _ in units}, key=category_sort_key)

    languages_expanded = sum(len(case.languages) for case in cases) + sum(
        len(scenario.languages) for scenario in scenarios
    )
    priority_counts: dict[str, int] = {priority: 0 for priority in ("P0", "P1", "P2")}
    for case in cases:
        if case.priority in priority_counts:
            priority_counts[case.priority] += 1

    lines: list[str] = []
    lines.append("<!-- AUTO-GENERATED by tools/corpus_index.py -- DO NOT EDIT BY HAND.")
    lines.append("     Regenerate with: uv run python tools/corpus_index.py")
    lines.append(
        "     Freshness check (soft gate, local only per D25): uv run python tools/corpus_index.py --check -->"
    )
    lines.append("")
    lines.append("# Negotiation Test Corpus Index")
    lines.append("")
    lines.append("Machine-generated view of the corpus under `tests/resources/negotiation-cases/`.")
    lines.append(
        "Coverage matrices count base records (one case record, or one scenario step under its scenario's category);"
    )
    lines.append("language expansion is listed per record and totaled in the statistics.")
    lines.append("")

    # Statistics.
    lines.append("## Statistics")
    lines.append("")
    lines.append("| Metric | Value |")
    lines.append("|---|---|")
    lines.append(f"| Case records | {len(cases)} |")
    lines.append(f"| Scenario records (steps) | {len(scenarios)} ({sum(len(s.steps) for s in scenarios)}) |")
    lines.append(f"| Base records total | {len(cases) + len(scenarios)} |")
    lines.append(f"| Language-expanded units | {languages_expanded} |")
    lines.append(f"| Shared LLM payloads | {shared['payloads']} |")
    lines.append(f"| Shared schema variants | {shared['schemas']} |")
    lines.append(f"| Error codes covered | {len(error_codes)} ({', '.join(error_codes)}) |")
    lines.append(
        f"| Exception-only failures | {sum(1 for _, _, o in units if o.startswith('exception:'))} "
        f"({', '.join(exception_types) or 'none'}) |"
    )
    lines.append("")
    lines.append("Priority distribution (case records; scenarios carry no priority):")
    lines.append("")
    lines.append("| Priority | Cases |")
    lines.append("|---|---|")
    for priority in ("P0", "P1", "P2"):
        lines.append(f"| {priority} | {priority_counts[priority]} |")
    lines.append("")

    # Family distribution.
    lines.append("## Family distribution")
    lines.append("")
    lines.append("| Family | Category | Corpus file | Records | Steps | Languages |")
    lines.append("|---|---|---|---|---|---|")
    family_records: dict[tuple[str, str], dict[str, Any]] = {}
    for case in cases:
        key = (category_of(case.id), case.path.relative_to(root).as_posix())
        entry = family_records.setdefault(key, {"records": 0, "steps": 0, "languages": set()})
        entry["records"] += 1
        entry["languages"].update(case.languages)
    for scenario in scenarios:
        key = (category_of(scenario.id), scenario.path.relative_to(root).as_posix())
        entry = family_records.setdefault(key, {"records": 0, "steps": 0, "languages": set()})
        entry["records"] += 1
        entry["steps"] += len(scenario.steps)
        entry["languages"].update(scenario.languages)
    for (category, file_name), entry in sorted(family_records.items(), key=lambda item: category_sort_key(item[0][0])):
        family = category.split("-", 1)[0]
        lines.append(
            f"| {FAMILY_LABELS[family]} ({family}) | {category} | {file_name} | {entry['records']} | "
            f"{entry['steps']} | {', '.join(sorted(entry['languages']))} |"
        )
    lines.append("")

    # API x category coverage matrix.
    lines.append("## API x category coverage")
    lines.append("")
    lines.append(
        "Cells count base records (case records plus scenario steps) per API and category;"
        " empty cells are coverage gaps."
    )
    lines.append("")
    lines.append("| Category | " + " | ".join(APIS) + " | Total |")
    lines.append("|---" * (len(APIS) + 2) + "|")
    for category in categories:
        counts = [sum(1 for api, cat, _ in units if cat == category and api == api_name) for api_name in APIS]
        total = sum(counts)
        cells = " | ".join(str(count) if count else "-" for count in counts)
        lines.append(f"| {category} | {cells} | {total} |")
    column_totals = [sum(1 for api, _, _ in units if api == api_name) for api_name in APIS]
    lines.append(
        "| **Total** | " + " | ".join(f"**{count}**" for count in column_totals) + f" | **{sum(column_totals)}** |"
    )
    lines.append("")

    # API x expected outcome coverage matrix.
    lines.append("## API x expected outcome coverage")
    lines.append("")
    lines.append(
        "Cells count base records per API and expectation; the `exception` column aggregates failures that assert only "
        "an exception type (failures carrying both an exception and a code are counted under their code)."
    )
    lines.append("")
    lines.append("| API | " + " | ".join(f"`{outcome}`" for outcome in outcomes) + " | Total |")
    lines.append("|---" * (len(outcomes) + 2) + "|")
    for api_name in APIS:
        counts = []
        for outcome in outcomes:
            if outcome == "exception":
                counts.append(sum(1 for api, _, o in units if api == api_name and o.startswith("exception:")))
            else:
                counts.append(sum(1 for api, _, o in units if api == api_name and o == outcome))
        cells = " | ".join(str(count) if count else "-" for count in counts)
        lines.append(f"| `{api_name}` | {cells} | {sum(counts)} |")
    lines.append("")

    # Per-record listing.
    lines.append("## Case index")
    lines.append("")
    by_file: dict[tuple[str, Path], list[Case]] = {}
    for case in cases:
        by_file.setdefault((category_of(case.id), case.path), []).append(case)
    for (category, path), file_cases in sorted(by_file.items(), key=lambda item: category_sort_key(item[0][0])):
        lines.append(f"### {category} -- {path.relative_to(root).as_posix()}")
        lines.append("")
        lines.append("| Id | API | Languages | Priority | Expectation | Summary |")
        lines.append("|---|---|---|---|---|---|")
        for case in file_cases:
            languages = ", ".join(sorted(case.languages))
            lines.append(
                f"| {case.id} | `{case.api}` | {languages} | {case.priority} | "
                f"{outcome_label(case.expect)} | {case.summary} |"
            )
        lines.append("")

    lines.append("## Scenario index")
    lines.append("")
    scenario_by_file: dict[tuple[str, Path], list[Scenario]] = {}
    for scenario in scenarios:
        scenario_by_file.setdefault((category_of(scenario.id), scenario.path), []).append(scenario)
    for (category, path), file_scenarios in sorted(
        scenario_by_file.items(), key=lambda item: category_sort_key(item[0][0])
    ):
        lines.append(f"### {category} -- {path.relative_to(root).as_posix()}")
        lines.append("")
        lines.append("| Id | Step APIs | Languages | Priority | Flow expectation | Step failures | Summary |")
        lines.append("|---|---|---|---|---|---|---|")
        for scenario in file_scenarios:
            apis = ", ".join(f"`{api}`" for api in dict.fromkeys(step["api"] for step in scenario.steps))
            languages = ", ".join(sorted(scenario.languages))
            failures: dict[str, int] = {}
            for step in scenario.steps:
                if step["expect"].get("outcome") == "failure":
                    label = outcome_label(step["expect"])
                    failures[label] = failures.get(label, 0) + 1
            failure_text = (
                ", ".join(f"{label} x{count}" if count > 1 else label for label, count in sorted(failures.items()))
                or "-"
            )
            lines.append(
                f"| {scenario.id} | {apis} | {languages} | - | {render_flow(scenario.expect_flow)} | "
                f"{failure_text} | {scenario.summary} |"
            )
        lines.append("")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    """CLI entry point: regenerate INDEX.md, or freshness-check it with ``--check``."""
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--corpus-root",
        type=Path,
        default=DEFAULT_CORPUS_ROOT,
        help="Negotiation corpus directory (default: the bundled test corpus).",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Compare the generated index against INDEX.md on disk instead of writing it; exit 1 on drift.",
    )
    args = parser.parse_args(argv)

    root = args.corpus_root.resolve()
    if not root.is_dir():
        print(f"Corpus root does not exist: {root}", file=sys.stderr)
        return 1
    content = render_index(root)
    index_path = root / INDEX_NAME

    if args.check:
        try:
            on_disk = index_path.read_text(encoding="utf-8")
        except OSError:
            print(f"{INDEX_NAME} is missing under {root}; run: uv run python tools/corpus_index.py", file=sys.stderr)
            return 1
        # Normalize line endings so a CRLF checkout never produces a false drift signal.
        if on_disk.replace("\r\n", "\n") != content:
            print(f"{index_path} is stale; regenerate it with: uv run python tools/corpus_index.py", file=sys.stderr)
            return 1
        print(f"Corpus index is fresh: {index_path}")
        return 0

    with open(index_path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(content)
    print(f"Wrote corpus index: {index_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
