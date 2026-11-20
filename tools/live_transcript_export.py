#!/usr/bin/env python3
"""Exports a live-corpus transcript into a review-friendly report with inline replayable requests.

Python port of the Java repository's ``tools/live_transcript_export.py`` (1.1.0, 201 lines). Reads
``.live-corpus/<run>/transcript.json`` (+ ``summary.json`` when present) and writes a single
``export/report.md``: one section per case -- input, parsed params, every LLM call's full request
messages / schema / raw response -- and, per call, the exact OpenAI-compatible
``/v1/chat/completions`` request body OpenAIClient sent (one merged system message carrying the
JSON-mode instruction, the schema instruction and the recorded system prompt, ``response_format``
json_object, and the recorded messages), pretty-printed inline so it can be copied straight into
Postman for prompt tuning outside the test suite.

The replay body is rebuilt with the production
:func:`a2a_t.llm.providers.openai.build_structured_messages`, so the replay section cannot drift
from what the client actually sends (the Java copy keeps them in sync by hand).

Usage:
  uv run python tools/live_transcript_export.py <run-dir-or-transcript.json> [--out DIR]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from a2a_t.llm.providers.openai import build_structured_messages

# Temperature the live .env pins (LiveLlmEnvWriter, A2AT_LLM_TEMPERATURE=0); used when the
# per-call record carries none, which is the production default path.
DEFAULT_TEMPERATURE = 0.0

OUTCOME_ICONS = {"PASS": "✅", "FAIL": "❌", "ERROR": "💥", "SKIP": "⏭️"}


def load_run(source: Path) -> tuple[Path, list[dict[str, Any]], dict[str, Any] | None]:
    """Resolve the run directory, transcript cases and optional summary from a path."""
    if source.is_dir():
        run_dir = source
        transcript_path = run_dir / "transcript.json"
    else:
        transcript_path = source
        run_dir = transcript_path.parent
    if not transcript_path.is_file():
        raise SystemExit(f"transcript not found: {transcript_path}")
    cases = json.loads(transcript_path.read_text(encoding="utf-8"))
    summary_path = run_dir / "summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8")) if summary_path.is_file() else None
    return run_dir, cases, summary


def build_replay_request(call: dict[str, Any]) -> dict[str, Any]:
    """Rebuild the ``/v1/chat/completions`` body OpenAIClient sent for one recorded call."""
    recorded = [
        {
            "role": "system" if message.get("role", "user") == "system" else "user",
            "content": message.get("content", ""),
        }
        for message in call.get("messages") or []
    ]
    messages = build_structured_messages(recorded, call.get("jsonSchema") or {})
    request: dict[str, Any] = {
        "model": call.get("model"),
        "messages": messages,
        "response_format": {"type": "json_object"},
    }
    temperature = call.get("temperature")
    request["temperature"] = temperature if temperature is not None else DEFAULT_TEMPERATURE
    max_tokens = call.get("maxTokens")
    if max_tokens is not None:
        request["max_tokens"] = max_tokens
    return request


def render_messages(messages: list[dict[str, Any]]) -> str:
    """Render one call's pipeline messages as role-labeled fenced blocks.

    Four-backtick fences: message bodies routinely contain triple-backtick json blocks (the
    extraction prompts embed output-format examples), which would close a normal fence.
    """
    parts = []
    for message in messages:
        role = message.get("role", "user")
        parts.append(f"**{role}**\n\n````\n{message.get('content', '')}\n````")
    return "\n\n".join(parts)


def render_params(params: dict[str, Any] | None) -> str:
    """Render the parsed param map as a markdown table, null values shown as such."""
    if not params:
        return "_(none)_"
    rows = ["| slot | extracted value |", "| --- | --- |"]
    for key in sorted(params):
        value = params[key]
        rows.append(f"| `{key}` | {value if value is not None else '_null_'} |")
    return "\n".join(rows)


def render_report(cases: list[dict[str, Any]], summary: dict[str, Any] | None) -> str:
    """Render the whole run into one markdown document."""
    lines = ["# Live corpus run transcript", ""]
    if summary:
        lines += [
            "## Run summary",
            "",
            f"- cases: {summary.get('totalCases')} "
            f"(pass {summary.get('passCount')}, fail {summary.get('failCount')}, "
            f"error {summary.get('errorCount')}, skip {summary.get('skipCount')})",
            f"- LLM calls: {summary.get('totalLlmCalls')}"
            f" (prompt tokens {summary.get('totalPromptTokens')},"
            f" completion tokens {summary.get('totalCompletionTokens')})",
            f"- schema parse failures: {summary.get('schemaParseFailureCount')}",
            "",
            "_Replay a request in Postman: POST `$A2AT_TEST_LLM_BASE_URL/chat/completions`,"
            " header `Authorization: Bearer $A2AT_TEST_LLM_API_KEY`,"
            " body = the JSON of the call's replay section._",
            "",
        ]
    for case in cases:
        outcome = case.get("outcome", "?")
        lines += [
            f"## {OUTCOME_ICONS.get(outcome, '')} {case.get('caseId')} — {outcome}",
            "",
            f"- assertions: {case.get('assertionSummary') or '_(none recorded)_'}",
            f"- scenario: `{case.get('scenarioCode') or '_(none)'}`"
            + (f", duration {case.get('durationMs')} ms" if case.get("durationMs") is not None else ""),
            "",
            "### Input",
            "",
            "````",
            case.get("inputSummary") or "_(not recorded)_",
            "````",
            "",
            "### Extracted params",
            "",
            render_params(case.get("params")),
            "",
        ]
        for call_number, call in enumerate(case.get("llmCalls") or [], start=1):
            usage = call.get("usage") or {}
            lines += [
                f"### LLM call {call_number}",
                "",
                f"- model: `{call.get('model')}`"
                f", duration {call.get('durationMs')} ms"
                f", tokens: prompt {usage.get('prompt_tokens', '?')}"
                f" / completion {usage.get('completion_tokens', '?')}"
                + (f", **error: {call.get('error')}**" if call.get("error") else ""),
                "",
                "#### Request messages (as the pipeline sent them)",
                "",
                render_messages(call.get("messages") or []),
                "",
                "#### JSON schema",
                "",
                "```json",
                json.dumps(call.get("jsonSchema"), ensure_ascii=False, indent=2),
                "```",
                "",
                "#### Raw response",
                "",
                "````",
                call.get("content") or "_(no content)_",
                "````",
                "",
                "#### Replay request (copy into Postman)",
                "",
                "````json",
                json.dumps(build_replay_request(call), ensure_ascii=False, indent=2),
                "````",
                "",
            ]
        if case.get("failureDiff"):
            lines += ["### Failure diff", "", "````", case["failureDiff"], "````", ""]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    """CLI entry point: export one run's transcript into ``report.md``."""
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("run", help="run directory (containing transcript.json) or the transcript.json itself")
    parser.add_argument("--out", help="output directory (default: <run>/export)")
    args = parser.parse_args(argv)

    run_dir, cases, summary = load_run(Path(args.run))
    out_dir = Path(args.out) if args.out else run_dir / "export"
    out_dir.mkdir(parents=True, exist_ok=True)

    report = render_report(cases, summary)
    report_path = out_dir / "report.md"
    with open(report_path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(report)

    print(f"{len(cases)} cases, {sum(len(case.get('llmCalls') or []) for case in cases)} inline replay requests")
    print(f"report: {report_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
