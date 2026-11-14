"""Tests for the deterministic OSP AI report skeleton."""

from __future__ import annotations

from pathlib import Path

from eval.osp.ai.report import (
    MARKER_ANALYSIS,
    MARKER_BLUE_FIX,
    MARKER_CLEANUP,
    MARKER_CONCLUSION,
    MARKER_ENVIRONMENT,
    MARKER_FAILURES,
    MARKER_FIELD_CONFIRM,
    render_report,
    write_report,
)
from eval.osp.ai.types import ArtifactSet, FailureRecord, RunIdentity


def _record(
    case_id: int,
    protocol: str,
    stage: str,
    *,
    name: str,
    wire: Path,
    client_state: Path,
    batch: Path,
) -> FailureRecord:
    """Build one failure record with every artifact the report must render."""
    return FailureRecord(
        case_id=case_id,
        name=name,
        protocol=protocol,
        operation="Create",
        item_status="failed",
        stage=stage,
        stage_status="failed",
        message=f"阶段失败 {case_id}",
        expected={"Task Context": {"rate": "2Mbps"}},
        actual={"Task Context": {"rate": "10Mbps"}},
        first_diff={
            "path": "Task Context.rate",
            "expected": "2Mbps",
            "actual": "10Mbps",
        },
        signals={"validationCode": "content.param_missing", "negotiationRounds": 0},
        run=RunIdentity(
            run_id=f"run-{case_id}",
            trace_id="trace-1",
            task_id="task-1",
            context_id="ctx-1",
        ),
        baseline=None,
        artifacts=ArtifactSet(wire=wire, client_state=client_state, batch=batch),
    )


def _fixture(tmp_path: Path) -> tuple[dict[str, object], dict[str, object], list[FailureRecord]]:
    """Return a manifest, a batch and two out-of-order failure records."""
    manifest: dict[str, object] = {
        "batchId": "batch-x",
        "gitHead": "abc123",
        "worktree": "/repo/worktree",
        "gitDirty": True,
        "model": "deepseek",
        "modelMode": "live",
        "protocol": "both",
        "decisionMode": "live",
        "ports": {"ui": 18080, "webhook": 19000},
        "runsDir": "/runs-ai",
        "caseIds": [12, 3],
        "stopAfter": "send",
        "concurrency": 1,
        "nmsEndpoint": "https://nms.example/a2a/json",
        "generatedAt": "2026-11-10T00:00:00Z",
    }
    batch: dict[str, object] = {
        "batchId": "batch-x",
        "status": "finished",
        "protocols": ["a2a-t", "a2a"],
        "caseIds": [12, 3],
        "stopAfter": "send",
        "summary": {
            "total": 2,
            "passed": 0,
            "failed": 2,
            "stageFailed": 2,
            "stageWaiting": 0,
        },
        "items": [
            {"caseId": 12, "protocol": "a2a-t", "status": "failed"},
            {"caseId": 3, "protocol": "a2a", "status": "failed"},
        ],
    }
    records = [
        _record(
            12,
            "a2a-t",
            "send",
            name="后一条",
            wire=tmp_path / "runs" / "run-12" / "wire.jsonl",
            client_state=tmp_path / "runs" / "client-state.sqlite3",
            batch=tmp_path / "runs" / "tester" / "batch-x.json",
        ),
        _record(
            3,
            "a2a",
            "negotiate",
            name="前一条",
            wire=tmp_path / "runs" / "run-3" / "wire.jsonl",
            client_state=tmp_path / "runs" / "client-state.sqlite3",
            batch=tmp_path / "runs" / "tester" / "batch-x.json",
        ),
    ]
    return manifest, batch, records


def test_report_has_every_section_and_marker(tmp_path: Path) -> None:
    """All machine and AI sections of design section 6.4 are present."""
    manifest, batch, records = _fixture(tmp_path)
    text = render_report(manifest=manifest, batch=batch, records=records)

    assert text.startswith("# OSP AI 跑批报告 batch-x\n")
    for header in (
        "## 0. 结论",
        "## 1. 环境",
        "## 2. 失败清单",
        "## 3. 逐条分析",
        "## 4. 需要蓝区跟改",
        "## 5. 待现场确认",
        "## 6. 清理结果",
        "## 附：关键证据摘录",
    ):
        assert header in text
    for marker in (
        MARKER_CONCLUSION,
        MARKER_ENVIRONMENT,
        MARKER_FAILURES,
        MARKER_ANALYSIS,
        MARKER_BLUE_FIX,
        MARKER_FIELD_CONFIRM,
        MARKER_CLEANUP,
    ):
        assert marker in text


def test_report_renders_environment_counts_and_matrix(tmp_path: Path) -> None:
    """Machine facts, summary counts and the case/protocol matrix are rendered."""
    manifest, batch, records = _fixture(tmp_path)
    text = render_report(manifest=manifest, batch=batch, records=records)

    assert "| commit | abc123 |" in text
    assert "| dirty | true |" in text
    assert "| model | deepseek |" in text
    assert "| ui=18080, webhook=19000 |" in text
    assert "| cases | 12, 3 |" in text
    assert "| stopAfter | send |" in text
    assert "| concurrency | 1 |" in text
    assert "| total | 2 |" in text
    assert "| passed | 0 |" in text
    assert "**用例 / 协议矩阵**" in text
    assert "| 用例 | a2a-t | a2a |" in text
    assert "| #3 | — | failed |" in text
    assert "| #12 | failed | — |" in text


def test_report_lists_failures_sorted_with_artifacts(tmp_path: Path) -> None:
    """Failure rows are sorted by case/protocol/stage and carry every artifact path."""
    manifest, batch, records = _fixture(tmp_path)
    text = render_report(manifest=manifest, batch=batch, records=records)

    first = text.index("**#3 前一条")
    second = text.index("**#12 后一条")
    assert first < second
    assert "**失败阶段分布**：negotiate=1, send=1" in text

    wire_3 = str(tmp_path / "runs" / "run-3" / "wire.jsonl")
    wire_12 = str(tmp_path / "runs" / "run-12" / "wire.jsonl")
    assert wire_3 in text
    assert wire_12 in text
    assert str(tmp_path / "runs" / "tester" / "batch-x.json") in text
    assert "serverTrace=null" in text
    assert "Task Context.rate" in text
    assert "content.param_missing" in text
    assert "runId=run-3, traceId=trace-1, taskId=task-1, contextId=ctx-1" in text


def test_report_appendix_is_bounded_and_redacted(tmp_path: Path) -> None:
    """Provided excerpts are inlined, capped per excerpt and stripped of credentials."""
    manifest, batch, records = _fixture(tmp_path)
    excerpts = {
        "#3/a2a/negotiate/wire": "Authorization: Bearer super-secret-token\n"
        + "\n".join(f"line {index}" for index in range(12)),
    }
    text = render_report(
        manifest=manifest,
        batch=batch,
        records=records,
        excerpts=excerpts,
        detail_limit=5,
    )

    assert "### #3/a2a/negotiate/wire" in text
    assert "line 0" in text
    assert "line 3" in text
    assert "line 4" not in text
    assert "… (truncated)" in text
    assert "super-secret-token" not in text
    assert "Bearer ***" in text
    assert "> 机器生成，已脱敏；每段最多 5 行。" in text


def test_report_is_deterministic_and_redacts_environment(tmp_path: Path) -> None:
    """Two renders of the same inputs are byte-identical; credentials never leak."""
    manifest, batch, records = _fixture(tmp_path)
    manifest["model"] = "Bearer model-token"

    first = render_report(manifest=manifest, batch=batch, records=records)
    second = render_report(manifest=manifest, batch=batch, records=records)

    assert first == second
    assert "model-token" not in first
    assert "Bearer ***" in first


def test_report_cleanup_section_uses_manifest_or_falls_back(tmp_path: Path) -> None:
    """Cleanup facts are rendered when present; otherwise the down-command note is used."""
    manifest, batch, records = _fixture(tmp_path)
    fallback = render_report(manifest=manifest, batch=batch, records=records)
    assert "见 instance down 输出" in fallback

    manifest["cleanup"] = {"activeTasks": 0, "portsReleased": True}
    rendered = render_report(manifest=manifest, batch=batch, records=records)
    assert "```json" in rendered
    assert "activeTasks" in rendered
    assert "见 instance down 输出" not in rendered


def test_write_report_creates_parents_and_uses_lf(tmp_path: Path) -> None:
    """The writer creates nested directories and writes UTF-8 with LF endings."""
    target = tmp_path / "docs-local" / "osp-ai" / "2026-11-10-batch-x.md"
    write_report(target, "# 报告\n\n内容\n")

    assert target.is_file()
    raw = target.read_bytes()
    assert b"\r\n" not in raw
    assert target.read_text(encoding="utf-8") == "# 报告\n\n内容\n"
