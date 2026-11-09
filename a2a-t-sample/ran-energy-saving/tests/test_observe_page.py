"""Contract tests for the /observe Langfuse export page (design 2026-11-09 §4-§6.6).

The page is a build-free ES module: these tests pin the DOM contract, the API/download URLs and
the two pure helpers the polling logic depends on. No browser and no backend import is needed.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

OBSERVE_DIR = Path(__file__).resolve().parents[1] / "demo" / "web" / "observe"
INDEX = OBSERVE_DIR / "index.html"
STYLESHEET = OBSERVE_DIR / "app.css"
MODULE = OBSERVE_DIR / "main.js"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _node_binary() -> str:
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is not installed")
    return node


def test_page_assets_exist() -> None:
    for path in (INDEX, STYLESHEET, MODULE):
        assert path.is_file(), f"missing {path}"
        assert path.stat().st_size > 0, f"empty {path}"


def test_index_loads_local_assets_and_nav_links() -> None:
    html = _read(INDEX)
    assert '<link rel="stylesheet" href="/observe/app.css">' in html
    assert '<script type="module" src="/observe/main.js"></script>' in html
    for href, label in (("/", "演示页面"), ("/runs", "Run 控制台"), ("/tester", "Tester")):
        assert f'href="{href}"' in html, href
        assert label in html, label


def test_index_exposes_the_required_controls() -> None:
    html = _read(INDEX)
    for control in (
        "banner",
        "config-state",
        "config-host",
        "tls-hint",
        "trace-input",
        "full-export",
        "start-button",
        "progress",
        "progress-bar",
        "results-table",
        "results-head",
        "results-body",
        "legend",
        "legend-table",
        "download-metrics",
        "download-rounds",
        "download-fields",
        "download-table",
        "jobs-body",
    ):
        assert f'id="{control}"' in html, control
    assert "同时生成完整导出" in html
    assert "开始导出" in html
    assert "table.xlsx（AF–AW）" in html
    assert 'id="download-table" type="button" disabled' in html
    assert "字段说明" in html


def test_no_markup_injection_pathways() -> None:
    html = _read(INDEX)
    module = _read(MODULE)
    for source in (html, module):
        assert "innerHTML" not in source
        assert "document.write" not in source
    assert not re.search(r"\son[a-z]+\s*=", html, re.IGNORECASE)
    assert "onclick=" not in module.lower()
    assert "textContent" in module


def test_module_contract_for_api_fields_and_downloads() -> None:
    module = _read(MODULE)
    for url in (
        "/api/observe/meta",
        "/api/observe/fields",
        "/api/observe/runs?limit=50",
        "/api/observe/jobs?limit=20",
        "/api/observe/jobs",
        "?since=",
        "/metrics.csv",
        "/rounds.csv",
        "/fields.csv",
        "/table.xlsx",
        "/archives/",
    ):
        assert url in module, url
    assert "window.location.href" in module
    assert "mainColumns" in module
    assert "export function mergeResults" in module
    assert "export function pollIntervalMs" in module
    assert "export function statusLabel" in module
    assert "row-error" in module
    assert "exportStatus" in module


def test_node_syntax_check() -> None:
    node = _node_binary()
    result = subprocess.run(
        [node, "--check", str(MODULE)],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stderr or result.stdout


def test_node_helpers_dedupe_results_and_scale_polling(tmp_path: Path) -> None:
    node = _node_binary()
    shutil.copyfile(MODULE, tmp_path / "main.mjs")
    harness = tmp_path / "observe.test.mjs"
    harness.write_text(
        """
import assert from "node:assert/strict";
import { mergeResults, pollIntervalMs, statusLabel } from "./main.mjs";

const merged = mergeResults(
  [{ seq: 1, traceId: "a" }, { seq: 2, traceId: "b" }],
  [{ seq: 2, traceId: "b2" }, { seq: 3, traceId: "c" }],
);
assert.equal(merged.length, 3, "duplicate seq must collapse into one row");
assert.deepEqual(merged.map((row) => row.seq), [1, 2, 3], "rows stay ordered by seq");
assert.equal(merged.find((row) => row.seq === 2).traceId, "b2", "newest result wins");

assert.equal(pollIntervalMs(0), 1000);
assert.equal(pollIntervalMs(19), 1000);
assert.equal(pollIntervalMs(20), 2000);
assert.equal(pollIntervalMs(99), 2000);
assert.equal(pollIntervalMs(100), 5000);
assert.equal(pollIntervalMs(500), 5000);

assert.equal(statusLabel("not_found"), "未找到");
assert.equal(statusLabel("done"), "已完成");
console.log("observe helpers ok");
""".strip()
        + "\n",
        encoding="utf-8",
    )
    result = subprocess.run(
        [node, str(harness)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stderr or result.stdout
    assert "observe helpers ok" in result.stdout
