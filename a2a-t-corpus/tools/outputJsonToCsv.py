#!/usr/bin/env python3
"""outputJsonToCsv - render corpus result JSON into one review-friendly CSV table (one row per case).

Every step exposes its API request and response verbatim plus every underlying LLM
request/response; the content is always complete, never truncated. `stepN_*` column
pairs extend with the case step count, and the API error code is surfaced per step.

Usage:
  python outputJsonToCsv.py --json output_result_from_text.json --out result.csv
  python outputJsonToCsv.py --json <dir>/<scenario>/output_result_from_text.json,<other>/output_result_from_text.json --out merged.csv
"""

import argparse
import csv
import json
import sys
from pathlib import Path

UTF8_BOM = "\ufeff"


def fail(message):
    print(f"[error] {message}", file=sys.stderr)
    sys.exit(1)


def load_results(paths):
    records = []
    for raw_path in paths:
        path = Path(raw_path)
        if not path.is_file():
            fail(f"result file not found: {path}")
        scenario = path.parent.name
        with open(path, "r", encoding="utf-8-sig") as handle:
            payload = json.load(handle)
        if not isinstance(payload, list):
            fail(f"{path} top level must be a result array")
        for record in payload:
            record["__scenario"] = scenario
            records.append(record)
    return records


def split_steps(interactions):
    """Splits the interleaved transcript into per-step groups {api, interaction, llm_calls}."""
    steps = []
    for interaction in interactions:
        if interaction.get("type") == "LLM":
            if steps:
                steps[-1]["llm_calls"].append(interaction)
            continue
        steps.append({"api": interaction.get("type", ""), "interaction": interaction, "llm_calls": []})
    return steps


def jsonize(value, pretty=True):
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, indent=2 if pretty else None)


def records_to_rows(records):
    all_rows = []
    max_steps = 0
    normalized = []
    for record in records:
        interactions = (record.get("output") or {}).get("interactions") or []
        steps = split_steps(interactions)
        normalized.append((record, steps))
        max_steps = max(max_steps, len(steps))
    for record, steps in normalized:
        row = {
            "scenario": record.get("__scenario", ""),
            "id": record.get("id", ""),
            "caseDimension": record.get("caseDimension", ""),
            "caseDesc": record.get("caseDesc", ""),
            "caseCategory": record.get("caseCategory", ""),
            "result": record.get("result", ""),
            "totalTime_ms": record.get("totalTime", 0),
            "totalInputToken": record.get("totalInputToken", 0),
            "totalOutputToken": record.get("totalOutputToken", 0),
            "totalToken": record.get("totalToken", 0),
        }
        for num, step in enumerate(steps, start=1):
            interaction = step["interaction"]
            prefix = f"step{num}_"
            row[prefix + "api"] = step["api"]
            row[prefix + "result"] = interaction.get("result", "")
            row[prefix + "duration_ms"] = interaction.get("duration_ms", 0)
            row[prefix + "inputToken"] = interaction.get("inputToken", 0)
            row[prefix + "outputToken"] = interaction.get("outputToken", 0)
            row[prefix + "api_errCode"] = (interaction.get("response") or {}).get("errorCode", "")
            row[prefix + "request"] = jsonize(interaction.get("request"))
            row[prefix + "response"] = jsonize(interaction.get("response"))
            row[prefix + "llm_requests"] = jsonize([call.get("request") for call in step["llm_calls"]])
            row[prefix + "llm_responses"] = jsonize([call.get("response") for call in step["llm_calls"]])
        row["fail_reason"] = record.get("failReason", "")
        row["__max_steps"] = len(steps)
        all_rows.append(row)
    return all_rows, max_steps


def build_header(max_steps):
    header = [
        "scenario", "id", "caseDimension", "caseDesc", "caseCategory",
        "result", "totalTime_ms", "totalInputToken", "totalOutputToken", "totalToken",
    ]
    for num in range(1, max_steps + 1):
        header += [
            f"step{num}_api", f"step{num}_result", f"step{num}_duration_ms",
            f"step{num}_inputToken", f"step{num}_outputToken", f"step{num}_api_errCode",
            f"step{num}_request", f"step{num}_response",
            f"step{num}_llm_requests", f"step{num}_llm_responses",
        ]
    header.append("fail_reason")
    return header


def main():
    parser = argparse.ArgumentParser(description="Corpus result JSON -> review CSV (one row per case with full per-step request/response)")
    parser.add_argument("--json", required=True, help="output_result_*.json; comma-separated to merge several scenarios")
    parser.add_argument("--out", required=True, help="output CSV")
    args = parser.parse_args()

    records = load_results([item.strip() for item in args.json.split(",") if item.strip()])
    rows, max_steps = records_to_rows(records)
    header = build_header(max_steps)
    with open(args.out, "w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=header)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key, "") for key in header})
    print(f"Wrote review table: {args.out} ({len(rows)} rows, {max_steps} step(s) max, complete request/response output)")


if __name__ == "__main__":
    main()