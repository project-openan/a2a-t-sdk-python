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

"""inputCsvToJson - convert the case design table (CSV) to corpus input JSON, and back.

Subcommands (mutually exclusive):
  --template                write the case-design template CSV (with one example row) to --out
  --reverse                 convert an input_case_*.json back to the design table (CSV)

CSV contract: one row per case; `stepN_*` column pairs map 1:1 to the input/expected
array elements, so any step count is supported. All files are UTF-8; the template
ships with a BOM so Excel opens it directly.

Validation (--no-validate to disable) mirrors the Java case loader: id uniqueness
inside the file, non-empty metadata columns, parseable JSON fragments for args and
per-step expected data, and api/expect presence per step.
"""

import argparse
import csv
import json
import re
import sys
from pathlib import Path

FIXED_COLUMNS = ["id", "caseDimension", "caseDesc", "caseCategory"]
STEP_PREFIX_RE = re.compile(r"^(?P<kind>step(?P<num>\d+)_(?P<field>api|args|expect_result|expect_data|expect_errCode|expect_errMessageContains))$")
UTF8_BOM = "\ufeff"


def parse_step_columns(header):
    """Maps column name -> (step number, field) for all stepN_* columns."""
    steps = []
    for column in header:
        match = STEP_PREFIX_RE.match(column)
        if match:
            steps.append((int(match.group("num")), match.group("field"), column))
    return sorted(steps, key=lambda item: (item[0], item[1]))


def fail(message):
    print(f"[error] {message}", file=sys.stderr)
    sys.exit(1)


def load_rows(path):
    with open(path, "r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def parse_json_fragment(column, row_id, allow_empty=False):
    text = (column or "").strip()
    if not text:
        if allow_empty:
            return {}
        fail(f"case {row_id}: missing JSON fragment")
    try:
        value = json.loads(text)
    except json.JSONDecodeError as error:
        fail(f"case {row_id}: invalid JSON fragment: {error}")
    if not allow_empty and not isinstance(value, dict):
        fail(f"case {row_id}: JSON fragment must be an object: {text}")
    return value


def rows_to_cases(rows, requested_scenario=None):
    if not rows:
        fail("no data rows in CSV")
    step_columns = parse_step_columns(list(rows[0].keys()))
    cases = []
    seen_ids = set()
    for row in rows:
        row_id = (row.get("id") or "").strip()
        if not row_id:
            fail("found a data row without id")
        if row_id in seen_ids:
            fail(f"duplicate case id: {row_id}")
        seen_ids.add(row_id)
        if not row.get("caseDimension", "").strip():
            fail(f"case {row_id}: missing caseDimension")
        if not row.get("caseDesc", "").strip():
            fail(f"case {row_id}: missing caseDesc")
        if not row.get("caseCategory", "").strip():
            fail(f"case {row_id}: missing caseCategory")

        scenario = (row.get("scenario") or "").strip() or requested_scenario
        if scenario:
            row["scenario"] = scenario
        else:
            del row["scenario"]

        max_step = max((num for num, _, _ in step_columns), default=0)
        input_steps = []
        expected_steps = []
        for num in range(1, max_step + 1):
            api = (row.get(f"step{num}_api") or "").strip()
            args = (row.get(f"step{num}_args") or "").strip()
            result = (row.get(f"step{num}_expect_result") or "").strip()
            if not api and not args and not result:
                continue
            if not api:
                fail(f"case {row_id}: step{num} missing api")
            if result not in ("success", "error"):
                fail(f"case {row_id}: step{num}_expect_result must be success or error: {result}")
            input_steps.append({
                "api": api,
                "args": parse_json_fragment(args, row_id) if args else {},
            })
            expected_step = {
                "result": result,
                "data": parse_json_fragment(row.get(f"step{num}_expect_data"), row_id, allow_empty=True),
                "error": {
                    "errCode": (row.get(f"step{num}_expect_errCode") or "").strip(),
                    "errMessage": (row.get(f"step{num}_expect_errMessageContains") or "").strip(),
                },
            }
            contains = (row.get(f"step{num}_expect_errMessageContains") or "").strip()
            if contains:
                expected_step["error"]["errMessageContains"] = contains
            expected_steps.append(expected_step)
        if not input_steps:
            fail(f"case {row_id}: at least one step is required")
        if len(input_steps) != len(expected_steps):
            fail(f"case {row_id}: input/expected step count mismatch")

        case = {
            "id": row_id,
            "caseDimension": row.get("caseDimension").strip(),
            "caseDesc": row.get("caseDesc").strip(),
            "caseCategory": row.get("caseCategory").strip(),
            "input": input_steps,
            "expected": expected_steps,
        }
        cases.append((scenario, case))
    return cases


def write_template(path):
    header = FIXED_COLUMNS + ["scenario"]
    for num in (1, 2, 3):
        header += [
            f"step{num}_api", f"step{num}_args",
            f"step{num}_expect_result", f"step{num}_expect_data",
            f"step{num}_expect_errCode", f"step{num}_expect_errMessageContains",
        ]
    example = {
        "id": "TC00000001",
        "caseDimension": "01-意图清晰参数完整",
        "caseDesc": "示例：专线业务中断标准投诉（可删除本行）",
        "caseCategory": "正常",
        "scenario": "private-line-complaint",
        "step1_api": "generateTaskPromptFromText",
        "step1_args": '{"text":"发生专线业务中断，接入端口名称为P781-……","templateUri":"Task-T/network-layer/private-line-complaint/v1"}',
        "step1_expect_result": "success",
        "step1_expect_data": "{}",
        "step1_expect_errCode": "",
        "step1_expect_errMessageContains": "",
        "step2_api": "validateTaskPromptAndDataFilling",
        "step2_args": '{"schema":{"$schema":"https://json-schema.org/draft/2020-12/schema","type":"object","properties":{"accessPort":{"type":"string"}},"required":["accessPort"]},"templateUri":"Task-T/network-layer/private-line-complaint/v1","promptText":{"$fromStep":1,"$field":"promptText"}}',
        "step2_expect_result": "success",
        "step2_expect_data": '{"accessPort":"P781-……"}',
        "step2_expect_errCode": "",
        "step2_expect_errMessageContains": "",
        "step3_api": "",
        "step3_args": "",
        "step3_expect_result": "",
        "step3_expect_data": "",
        "step3_expect_errCode": "",
        "step3_expect_errMessageContains": "",
    }
    with open(path, "w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=header)
        writer.writeheader()
        writer.writerow(example)
    print(f"Wrote case design template: {path}")


def cases_to_json(cases, api_names=None):
    out = []
    for _, case in cases:
        serialized = {
            "id": case["id"],
            "caseDimension": case["caseDimension"],
            "caseDesc": case["caseDesc"],
            "caseCategory": case["caseCategory"],
            "input": case["input"],
            "expected": case["expected"],
        }
        out.append(serialized)
    return out


def json_to_rows(cases):
    rows = []
    step_columns = build_step_columns(cases)
    header = FIXED_COLUMNS + ["scenario"] + step_columns
    for case in cases:
        row = {key: case.get(key, "") for key in FIXED_COLUMNS}
        row["scenario"] = case.get("scenario", "")
        for num in range(1, len(case.get("input", [])) + 1):
            step = case["input"][num - 1]
            expected = case["expected"][num - 1] if num - 1 < len(case.get("expected", [])) else {}
            row[f"step{num}_api"] = step.get("api", "")
            row[f"step{num}_args"] = json.dumps(step.get("args", {}), ensure_ascii=False)
            row[f"step{num}_expect_result"] = expected.get("result", "")
            row[f"step{num}_expect_data"] = json.dumps(expected.get("data", {}) or {}, ensure_ascii=False)
            error = expected.get("error") or {}
            row[f"step{num}_expect_errCode"] = error.get("errCode", "")
            row[f"step{num}_expect_errMessageContains"] = error.get("errMessageContains", "")
        for column in step_columns:
            row.setdefault(column, "")
        rows.append(row)
    return header, rows


def build_step_columns(cases):
    max_steps = max((len(case.get("input", [])) for case in cases), default=0)
    columns = []
    for num in range(1, max_steps + 1):
        columns += [
            f"step{num}_api", f"step{num}_args",
            f"step{num}_expect_result", f"step{num}_expect_data",
            f"step{num}_expect_errCode", f"step{num}_expect_errMessageContains",
        ]
    return columns


def write_csv(path, header, rows):
    with open(path, "w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=header)
        writer.writeheader()
        writer.writerows(rows)
    print(f"Wrote CSV: {path}")


def main():
    parser = argparse.ArgumentParser(description="Case design CSV <-> corpus input JSON converter")
    parser.add_argument("--template", action="store_true", help="write the design template CSV (with one example row) to --out")
    parser.add_argument("--reverse", action="store_true", help="reverse direction: input JSON -> design CSV")
    parser.add_argument("--csv", help="input CSV (default command) or output CSV (--reverse)")
    parser.add_argument("--out", required=True, help="output file")
    parser.add_argument("--scenario", help="target scenario name (written to the scenario column; inferred from the output file name when absent)")
    parser.add_argument("--no-validate", action="store_true", help="skip structural validation of the produced JSON")
    args = parser.parse_args()

    if args.template:
        write_template(args.out)
        return

    if args.reverse:
        if not args.csv:
            fail("--reverse requires --csv pointing at an input_case_*.json")
        with open(args.csv, "r", encoding="utf-8-sig") as handle:
            cases = json.load(handle)
        header, rows = json_to_rows(cases)
        write_csv(args.out, header, rows)
        return

    if not args.csv:
        fail("--csv pointing at the design table is required (or use --template to generate one)")
    rows = load_rows(args.csv)
    scenario = args.scenario
    if not scenario:
        scenario = Path(args.out).stem.replace("input_case_", "") or None
    cases = rows_to_cases(rows, scenario)

    payload = cases_to_json(cases)
    if not args.no_validate:
        validate_payload(payload)
    with open(args.out, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    print(f"Wrote case JSON: {args.out} ({len(payload)} cases)")


def validate_payload(payload):
    if not isinstance(payload, list) or not payload:
        fail("validation failed: the top level must be a case array")
    for case in payload:
        case_id = case.get("id")
        for key in FIXED_COLUMNS:
            if not str(case.get(key, "")).strip():
                fail(f"validation failed: case {case_id} missing {key}")
        if not isinstance(case.get("input"), list) or not case["input"]:
            fail(f"validation failed: case {case_id} input must be a non-empty array")
        if not isinstance(case.get("expected"), list) or len(case["expected"]) != len(case["input"]):
            fail(f"validation failed: case {case_id} expected must be the same length as input")
        for i, step in enumerate(case["input"], start=1):
            if not isinstance(step, dict) or not str(step.get("api", "")).strip():
                fail(f"validation failed: case {case_id} step {i} missing api")
            expected = case["expected"][i - 1]
            if expected.get("result") not in ("success", "error"):
                fail(f"validation failed: case {case_id} step {i} expected.result invalid")
            if expected.get("result") == "error" and not str(
                    (expected.get("error") or {}).get("errCode", "")).strip():
                fail(f"validation failed: case {case_id} step {i} error expectation missing errCode")


if __name__ == "__main__":
    main()