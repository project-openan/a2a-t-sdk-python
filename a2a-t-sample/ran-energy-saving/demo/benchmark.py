"""Benchmark the A2A-T and pure A2A modes on the same scenarios.

The protocol is selected when the demo process starts, so the benchmark runs one demo stack per
protocol, drives the Web UI API, approves the human gate after a fixed delay, and reads each run's
metrics from ``/api/runs/{id}/metrics``. The result is a JSON/CSV/HTML report with token use,
latency and scenario accuracy per protocol and scenario.

Usage::

    uv run python -m demo.benchmark --model-mode live --repeats 3
    uv run python -m demo.benchmark --protocols a2a --scenarios information --repeats 1
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import html
import json
import os
import socket
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

import httpx
from protocol.metrics_summary import summarize_metrics, summarize_protocols
from shared.config import SUPPORTED_MODEL_MODES
from shared.run_constants import (
    DEMO_PROTOCOLS,
    LANGUAGE_ZH,
    PROTOCOL_A2A_NAME,
    PROTOCOL_A2A_T,
    PROTOCOL_PURE,
    SUPPORTED_LANGUAGES,
    normalize_protocol_name,
)

from demo.scenario import PURE_PROTOCOL_SCENARIOS, SCENARIO_INFORMATION, SCENARIO_TARGET

CASE_DIR: Final[Path] = Path(__file__).resolve().parent.parent
DEFAULT_OUT_DIR: Final[Path] = CASE_DIR / "runs" / "benchmarks"

#: Ports of the demo stack; the benchmark refuses to start when one is already in use.
STACK_PORTS: Final[tuple[int, ...]] = (5001, 8000, 8001, 8002)

#: Default scenarios compared by the benchmark (the pure mode supports all three).
DEFAULT_SCENARIOS: Final[tuple[str, ...]] = (
    SCENARIO_INFORMATION,
    SCENARIO_TARGET,
    "night",
)


@dataclass
class RunResult:
    """One benchmark run: metrics plus a failure reason when the run could not complete."""

    protocol: str
    scenario: str
    metrics: dict[str, object] | None = None
    error: str | None = None


@dataclass
class BenchmarkOptions:
    """Resolved benchmark configuration."""

    protocols: tuple[str, ...]
    scenarios: tuple[str, ...]
    repeats: int
    model_mode: str
    language: str
    approval_delay: float
    timeout: float
    out_dir: Path
    ui_port: int | None = None
    results: list[RunResult] = field(default_factory=list)


def _free_port() -> int:
    """Return one free loopback TCP port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _port_open(port: int, host: str = "127.0.0.1") -> bool:
    """Return whether a TCP port accepts a connection."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.2)
        return sock.connect_ex((host, port)) == 0


def _check_ports() -> None:
    """Fail fast when another demo stack still holds a port."""
    busy = [port for port in STACK_PORTS if _port_open(port)]
    if busy:
        raise SystemExit(
            f"[benchmark] ports already in use: {busy}. Stop the running demo first "
            "(uv run python -m demo.run_demo --stop)."
        )


async def _wait_ui(client: httpx.AsyncClient, timeout: float) -> None:
    """Wait until the Web UI answers its meta endpoint."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            response = await client.get("/api/meta")
            if response.status_code == 200:
                return
        except httpx.HTTPError:
            pass
        await asyncio.sleep(0.3)
    raise RuntimeError("the demo Web UI did not start in time")


async def _start_stack(
    options: BenchmarkOptions,
    protocol: str,
    scenario: str,
    log_path: Path,
) -> tuple[subprocess.Popen[str], httpx.AsyncClient, str]:
    """Start one demo stack and return its process, HTTP client and base URL."""
    _check_ports()
    ui_port = options.ui_port or _free_port()
    command = [
        sys.executable,
        "-m",
        "demo.run_demo",
        "--protocol",
        protocol,
        "--ui",
        "--ui-port",
        str(ui_port),
        "--scenario",
        scenario,
        "--language",
        options.language,
        "--model-mode",
        options.model_mode,
        "--approval-gate",
        "--approval-timeout",
        str(max(30.0, options.timeout)),
        "--keep-alive",
    ]
    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    env["PYTHONPATH"] = str(CASE_DIR) + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    log_handle = log_path.open("w", encoding="utf-8")
    process = subprocess.Popen(  # noqa: S603 - fixed local command, no shell
        command,
        cwd=CASE_DIR,
        stdout=log_handle,
        stderr=subprocess.STDOUT,
        env=env,
    )
    base_url = f"http://127.0.0.1:{ui_port}"
    client = httpx.AsyncClient(base_url=base_url, timeout=30.0, trust_env=False)
    try:
        await _wait_ui(client, options.timeout)
    except Exception:
        await client.aclose()
        process.terminate()
        log_handle.close()
        await asyncio.to_thread(
            subprocess.run,  # noqa: S603 - fixed local command, no shell
            [sys.executable, "-m", "demo.run_demo", "--stop"],
            cwd=CASE_DIR,
            capture_output=True,
            text=True,
            timeout=30,
        )
        raise
    return process, client, base_url


async def _stop_stack(process: subprocess.Popen[str], client: httpx.AsyncClient) -> None:
    """Stop the UI process and the background demo processes it left behind."""
    await client.aclose()
    if process.poll() is None:
        process.terminate()
        try:
            await asyncio.to_thread(process.wait, 10)
        except subprocess.TimeoutExpired:
            process.kill()
    stop = await asyncio.to_thread(
        subprocess.run,  # noqa: S603 - fixed local command, no shell
        [sys.executable, "-m", "demo.run_demo", "--stop"],
        cwd=CASE_DIR,
        capture_output=True,
        text=True,
        timeout=30,
    )
    del stop


async def _iter_sse(response: httpx.Response):
    """Yield decoded SSE ``data:`` JSON objects from one streaming response."""
    buffer = ""
    async for chunk in response.aiter_text():
        buffer += chunk.replace("\r\n", "\n")
        while "\n\n" in buffer:
            block, buffer = buffer.split("\n\n", 1)
            for line in block.split("\n"):
                if line.startswith("data: "):
                    try:
                        yield json.loads(line[6:])
                    except json.JSONDecodeError:
                        continue


async def _approve_mie(
    client: httpx.AsyncClient,
    task_id: str,
    delay: float,
) -> None:
    """Approve one pure-mode MIE task after the simulated engineer delay."""
    await asyncio.sleep(delay)
    try:
        await client.post(f"/api/mie/approvals/{task_id}", json={"approved": True})
    except httpx.HTTPError:
        pass


async def _run_once(
    client: httpx.AsyncClient,
    *,
    protocol: str,
    scenario: str,
    approval_delay: float,
    timeout: float,
) -> dict[str, object]:
    """Start one run, auto-approve its gate, wait for completion and return its metrics."""
    started = await client.post(
        "/api/runs",
        json={"scenario": scenario, "protocol": protocol, "mode": "auto"},
    )
    if started.status_code >= 400:
        raise RuntimeError(f"start failed: {started.status_code} {started.text}")
    run_id = str(started.json()["runId"])
    finished = asyncio.Event()
    approval_tasks: set[asyncio.Task[None]] = set()
    approved = False

    async def approve_a2at() -> None:
        nonlocal approved
        if approved:
            return
        approved = True
        await asyncio.sleep(approval_delay)
        try:
            await client.post(f"/api/runs/{run_id}/approvals", json={"approved": True})
        except httpx.HTTPError:
            pass

    async def read_mie() -> None:
        async with client.stream("GET", "/api/mie/events") as response:
            response.raise_for_status()
            async for event in _iter_sse(response):
                if event.get("type") == "approval_requested":
                    task = asyncio.create_task(_approve_mie(client, str(event.get("taskId") or ""), approval_delay))
                    approval_tasks.add(task)
                    task.add_done_callback(approval_tasks.discard)

    mie_task: asyncio.Task[None] | None = None
    if protocol == PROTOCOL_PURE:
        mie_task = asyncio.create_task(read_mie())

    try:
        async with client.stream("GET", f"/api/runs/{run_id}/events") as response:
            response.raise_for_status()
            async for frame in _iter_sse(response):
                event = frame.get("event")
                if (
                    protocol == PROTOCOL_A2A_T
                    and frame.get("type") == "event"
                    and isinstance(event, dict)
                    and event.get("state") == "TASK_STATE_INPUT_REQUIRED"
                ):
                    task = asyncio.create_task(approve_a2at())
                    approval_tasks.add(task)
                    task.add_done_callback(approval_tasks.discard)
                if frame.get("type") == "finished":
                    finished.set()
                    break
    finally:
        if mie_task is not None:
            mie_task.cancel()
            try:
                await mie_task
            except (asyncio.CancelledError, httpx.HTTPError):
                pass
        if approval_tasks:
            await asyncio.gather(*tuple(approval_tasks), return_exceptions=True)

    if not finished.is_set():
        raise RuntimeError("the run stream ended before a finished frame")

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        metrics_response = await client.get(f"/api/runs/{run_id}/metrics")
        if metrics_response.status_code == 200:
            metrics = metrics_response.json()
            if metrics.get("traceFinal") is True:
                return metrics
        await asyncio.sleep(0.3)
    raise RuntimeError("the run finished but its metrics did not become final")


async def run_benchmark(options: BenchmarkOptions) -> list[RunResult]:
    """Run every protocol/scenario/repeat combination."""
    results: list[RunResult] = []
    options.out_dir.mkdir(parents=True, exist_ok=True)
    for protocol in options.protocols:
        scenarios = [name for name in options.scenarios if protocol != PROTOCOL_PURE or name in PURE_PROTOCOL_SCENARIOS]
        if not scenarios:
            continue
        log_path = options.out_dir / f"{protocol}.log"
        process, client, _ = await _start_stack(options, protocol, scenarios[0], log_path)
        try:
            for scenario in scenarios:
                for repeat in range(options.repeats):
                    label = f"{protocol}/{scenario} #{repeat + 1}"
                    print(f"[benchmark] running {label}", flush=True)
                    try:
                        metrics = await asyncio.wait_for(
                            _run_once(
                                client,
                                protocol=protocol,
                                scenario=scenario,
                                approval_delay=options.approval_delay,
                                timeout=options.timeout,
                            ),
                            timeout=options.timeout + 30.0,
                        )
                        results.append(RunResult(protocol=protocol, scenario=scenario, metrics=metrics))
                        print(
                            f"[benchmark] done {label}: "
                            f"passed={metrics.get('accuracy', {}).get('passed')} "
                            f"tokens={metrics.get('tokens', {}).get('total')}",
                            flush=True,
                        )
                    except Exception as exc:  # noqa: BLE001 - one failed run must not stop the matrix
                        results.append(RunResult(protocol=protocol, scenario=scenario, error=str(exc)))
                        print(f"[benchmark] failed {label}: {exc}", flush=True)
        finally:
            await _stop_stack(process, client)
    return results


def _write_report(options: BenchmarkOptions, results: list[RunResult]) -> Path:
    """Write the JSON, per-run JSONL, summary CSV and standalone HTML report."""
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    out_dir = options.out_dir / stamp
    out_dir.mkdir(parents=True, exist_ok=True)
    rows = [result.metrics for result in results if result.metrics is not None]
    (out_dir / "metrics.json").write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    with (out_dir / "metrics.jsonl").open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    summary = summarize_metrics(rows)
    protocols = summarize_protocols(rows)
    _write_summary_csv(out_dir / "summary.csv", summary)
    (out_dir / "summary.json").write_text(
        json.dumps({"groups": summary, "protocols": protocols}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (out_dir / "report.html").write_text(_render_html(options, rows, summary, protocols, results), encoding="utf-8")
    return out_dir


def _write_summary_csv(path: Path, summary: list[dict[str, object]]) -> None:
    """Write the grouped summary as CSV."""
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "protocol",
                "scenario",
                "runs",
                "successRate",
                "accuracy",
                "avgRounds",
                "avgPromptTokens",
                "avgCompletionTokens",
                "avgTotalTokens",
                "avgCachedTokens",
                "cacheHitRate",
                "avgModelMs",
                "avgTotalMs",
                "p95TotalMs",
                "avgApprovalWaitMs",
            ]
        )
        for row in summary:
            tokens = row.get("tokens") if isinstance(row.get("tokens"), dict) else {}
            latency = row.get("latency") if isinstance(row.get("latency"), dict) else {}
            writer.writerow(
                [
                    row.get("protocol"),
                    row.get("scenario"),
                    row.get("runs"),
                    row.get("successRate"),
                    row.get("accuracy"),
                    row.get("avgRounds"),
                    (tokens.get("prompt") or {}).get("avg"),
                    (tokens.get("completion") or {}).get("avg"),
                    (tokens.get("total") or {}).get("avg"),
                    (tokens.get("cached") or {}).get("avg"),
                    row.get("cacheHitRate"),
                    (latency.get("modelMs") or {}).get("avg"),
                    (latency.get("totalMs") or {}).get("avg"),
                    (latency.get("totalMs") or {}).get("p95"),
                    (latency.get("approvalWaitMs") or {}).get("avg"),
                ]
            )


def _render_html(
    options: BenchmarkOptions,
    rows: list[dict[str, object]],
    summary: list[dict[str, object]],
    protocols: list[dict[str, object]],
    results: list[RunResult],
) -> str:
    """Render one self-contained HTML report."""

    def cell(value: object, digits: int = 1) -> str:
        if value is None:
            return "—"
        if isinstance(value, float):
            return f"{value:.{digits}f}"
        return html.escape(str(value))

    def rate(value: object) -> str:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return "—"
        return f"{value * 100:.1f}%"

    def table(headers: list[str], body: list[str]) -> str:
        return (
            "<table><thead><tr>"
            + "".join(f"<th>{html.escape(h)}</th>" for h in headers)
            + "</tr></thead><tbody>"
            + "".join(body)
            + "</tbody></table>"
        )

    group_rows = [
        "<tr>"
        + "".join(
            f"<td>{cell(value)}</td>"
            for value in (
                row.get("protocol"),
                row.get("scenario"),
                row.get("runs"),
                row.get("successRate"),
                row.get("accuracy"),
                row.get("avgRounds"),
                (row.get("tokens") or {}).get("prompt", {}).get("avg"),
                (row.get("tokens") or {}).get("completion", {}).get("avg"),
                (row.get("tokens") or {}).get("total", {}).get("avg"),
                (row.get("tokens") or {}).get("cached", {}).get("avg"),
                rate(row.get("cacheHitRate")),
                (row.get("latency") or {}).get("modelMs", {}).get("avg"),
                (row.get("latency") or {}).get("totalMs", {}).get("avg"),
                (row.get("latency") or {}).get("totalMs", {}).get("p95"),
                (row.get("latency") or {}).get("approvalWaitMs", {}).get("avg"),
            )
        )
        + "</tr>"
        for row in summary
    ]
    protocol_rows = [
        "<tr>"
        + "".join(
            f"<td>{cell(value)}</td>"
            for value in (
                row.get("protocol"),
                row.get("runs"),
                row.get("successRate"),
                row.get("accuracy"),
                (row.get("tokens") or {}).get("total", {}).get("avg"),
                rate(row.get("cacheHitRate")),
                (row.get("latency") or {}).get("modelMs", {}).get("avg"),
                (row.get("latency") or {}).get("totalMs", {}).get("avg"),
            )
        )
        + "</tr>"
        for row in protocols
    ]
    run_rows = [
        "<tr>"
        + "".join(
            f"<td>{cell(value)}</td>"
            for value in (
                row.get("runId"),
                row.get("protocol"),
                row.get("scenario"),
                row.get("finalState"),
                (row.get("accuracy") or {}).get("score"),
                (row.get("tokens") or {}).get("total"),
                (row.get("tokens") or {}).get("cached"),
                rate((row.get("tokens") or {}).get("cacheHitRate")),
                (row.get("latency") or {}).get("modelMs"),
                (row.get("latency") or {}).get("totalMs"),
                (row.get("negotiation") or {}).get("rounds"),
            )
        )
        + "</tr>"
        for row in rows
    ]
    failed = [
        f"<li>{html.escape(result.protocol)}/{html.escape(result.scenario)}: {html.escape(result.error or '')}</li>"
        for result in results
        if result.error
    ]
    overview_table = table(
        [
            "Protocol",
            "Runs",
            "Success",
            "Accuracy",
            "Avg total tokens",
            "Cache hit",
            "Avg model ms",
            "Avg total ms",
        ],
        protocol_rows,
    )
    group_table = table(
        [
            "Protocol",
            "Scenario",
            "Runs",
            "Success",
            "Accuracy",
            "Rounds",
            "Prompt",
            "Completion",
            "Total",
            "Cached",
            "Cache hit",
            "Model ms",
            "Total ms",
            "P95 ms",
            "Approval ms",
        ],
        group_rows,
    )
    runs_table = table(
        [
            "Run",
            "Protocol",
            "Scenario",
            "Final state",
            "Accuracy",
            "Tokens",
            "Cached",
            "Cache hit",
            "Model ms",
            "Total ms",
            "Rounds",
        ],
        run_rows,
    )
    return f"""<!doctype html>
<html lang="{html.escape(options.language)}"><head><meta charset="utf-8">
<title>RAN energy saving protocol benchmark</title>
<style>
body{{font:14px/1.5 "Segoe UI","Microsoft YaHei",system-ui,sans-serif;margin:24px;color:#1d1d1f;background:#f6f6f4}}
h1{{font-size:20px}} h2{{font-size:15px;margin-top:24px}}
table{{border-collapse:collapse;width:100%;background:#fff;margin-top:8px}}
th,td{{border-bottom:1px solid #e2e2df;padding:6px 9px;text-align:left;white-space:nowrap}}
th{{font-size:12px;color:#6b6b70}} .meta{{color:#6b6b70}}
</style></head><body>
<h1>RAN energy saving · protocol benchmark</h1>
<p class="meta">{html.escape(options.model_mode)} · {options.repeats} repeat(s) ·
scenarios: {html.escape(", ".join(options.scenarios))} ·
approval delay: {options.approval_delay:.1f}s</p>
<h2>Protocol overview</h2>
{overview_table}
<h2>Protocol × scenario</h2>
{group_table}
<h2>Runs</h2>
{runs_table}
{("<h2>Failures</h2><ul>" + "".join(failed) + "</ul>") if failed else ""}
</body></html>"""


def _parse_args(argv: list[str] | None = None) -> BenchmarkOptions:
    """Parse the benchmark command line."""
    parser = argparse.ArgumentParser(description="Compare the A2A-T and A2A modes.")
    parser.add_argument(
        "--protocols",
        default=",".join((PROTOCOL_A2A_T, PROTOCOL_A2A_NAME)),
        help="comma-separated protocols: a2a-t, a2a",
    )
    parser.add_argument("--scenarios", default=",".join(DEFAULT_SCENARIOS))
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--model-mode", default="offline", choices=list(SUPPORTED_MODEL_MODES))
    parser.add_argument("--language", default=LANGUAGE_ZH, choices=list(SUPPORTED_LANGUAGES))
    parser.add_argument("--approval-delay", type=float, default=0.3)
    parser.add_argument("--timeout", type=float, default=300.0)
    parser.add_argument("--out", default=str(DEFAULT_OUT_DIR))
    args = parser.parse_args(argv)
    protocols = tuple(normalize_protocol_name(part.strip()) for part in args.protocols.split(",") if part.strip())
    unknown = [name for name in protocols if name not in DEMO_PROTOCOLS]
    if unknown:
        parser.error(f"unsupported protocols: {unknown}; expected {PROTOCOL_A2A_T} or {PROTOCOL_A2A_NAME}")
    scenarios = tuple(part.strip() for part in args.scenarios.split(",") if part.strip())
    if not scenarios:
        parser.error("at least one scenario is required")
    if args.repeats < 1:
        parser.error("--repeats must be at least 1")
    return BenchmarkOptions(
        protocols=protocols,
        scenarios=scenarios,
        repeats=args.repeats,
        model_mode=args.model_mode,
        language=args.language,
        approval_delay=max(0.0, args.approval_delay),
        timeout=max(30.0, args.timeout),
        out_dir=Path(args.out).expanduser().resolve(),
    )


def main(argv: list[str] | None = None) -> int:
    """Run the benchmark and print the output directory."""
    options = _parse_args(argv)
    results = asyncio.run(run_benchmark(options))
    out_dir = _write_report(options, results)
    failures = sum(1 for result in results if result.error)
    print(f"[benchmark] results: {out_dir}")
    print(f"[benchmark] runs={len(results)} failures={failures}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
