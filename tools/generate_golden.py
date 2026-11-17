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

"""Regeneration helper for the golden fixture set of the negotiation content layer.

Python port of the Java ``a2a-t-corpus`` ``golden/GoldenFixtureGeneratorTest`` generation path,
promoted from a disabled test to a CLI tool (D23): the tool writes the 24 golden fixture files
(12 type/performative combinations, x 2 languages) under ``tests/resources/negotiation-cases/golden/``
by rendering the fixed ``tests/negotiation/generation/golden_inputs.py`` inputs through an
orchestrator wired with the real built-in resources — no LLM client is involved.

Golden fixtures are locked by the parity suite
(``tests/negotiation/generation/test_golden_parity.py``) and may only change together with a
reviewed template or vocabulary revision. Without arguments the tool rewrites the fixture files
(LF, no BOM); review the diff before committing it. With ``--check`` nothing is written and the
exit code reports drift instead (0 = fresh, 1 = stale), so the tool doubles as a local freshness
gate the way the Java repo's CI once used it.

Usage:
  uv run python tools/generate_golden.py           # regenerate the fixtures
  uv run python tools/generate_golden.py --check   # compare without writing
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tests.negotiation.generation.golden_inputs import (  # noqa: E402
    GOLDEN_CASES,
    GOLDEN_ROOT,
    LANGUAGES,
    GoldenCase,
    orchestrator,
)


def render_fixture(golden_case: GoldenCase, language: str) -> str:
    """Render one golden fixture through the real built-in resources of its language."""
    result = golden_case.generate(orchestrator(language), language)
    if result.prompt_text is None:  # pragma: no cover - the from-data pipeline always renders
        raise SystemExit(f"the from-data pipeline produced no prompt text for {golden_case.name}/{language}")
    return result.prompt_text


def normalized(text: str) -> str:
    """CRLF→LF-normalize one fixture text (the Java 3bdacb2 Windows lesson)."""
    return text.replace("\r\n", "\n")


def check_fixtures() -> list[str]:
    """Compare the rendered fixtures against the committed files without writing.

    Returns:
        the drift report, one entry per mismatching, missing or stale fixture file.
    """
    drift: list[str] = []
    for language in LANGUAGES:
        rendered = {
            golden_case.file_name: normalized(render_fixture(golden_case, language)) for golden_case in GOLDEN_CASES
        }
        expected_files = set(rendered)
        committed_files = {path.name for path in (GOLDEN_ROOT / language).glob("*.md")}
        for file_name, text in sorted(rendered.items()):
            committed = GOLDEN_ROOT / language / file_name
            if not committed.is_file():
                drift.append(f"missing fixture {committed.relative_to(REPO_ROOT)}")
                continue
            if normalized(committed.read_text(encoding="utf-8")) != text:
                drift.append(f"stale fixture {committed.relative_to(REPO_ROOT)} (regenerate and review)")
        for file_name in sorted(committed_files - expected_files):
            drift.append(f"stale fixture {GOLDEN_ROOT / language / file_name} matches no golden case; remove it")
    return drift


def write_fixtures() -> int:
    """Rewrite every fixture file with its freshly rendered text (LF, no BOM)."""
    written = 0
    for language in LANGUAGES:
        for golden_case in GOLDEN_CASES:
            target = GOLDEN_ROOT / language / golden_case.file_name
            target.parent.mkdir(parents=True, exist_ok=True)
            text = normalized(render_fixture(golden_case, language))
            if target.is_file() and normalized(target.read_text(encoding="utf-8")) == text:
                continue
            with open(target, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(text)
            written += 1
            print(f"wrote {target.relative_to(REPO_ROOT)}")
    print(f"golden fixtures are fresh: {len(GOLDEN_CASES) * len(LANGUAGES)} files, {written} rewritten")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--check",
        action="store_true",
        help="Compare the rendered fixtures against the committed files instead of writing; exit 1 on drift.",
    )
    args = parser.parse_args(argv)

    if not args.check:
        return write_fixtures()

    drift = check_fixtures()
    if drift:
        for entry in drift:
            print(entry, file=sys.stderr)
        print(
            f"golden fixtures are stale ({len(drift)} drift); regenerate with: uv run python tools/generate_golden.py",
            file=sys.stderr,
        )
        return 1
    print(
        f"golden fixtures are fresh: {GOLDEN_ROOT.relative_to(REPO_ROOT)} ({len(GOLDEN_CASES) * len(LANGUAGES)} files)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
