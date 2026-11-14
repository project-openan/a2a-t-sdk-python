"""Integration tests for the OSP AI evaluation command line."""

from __future__ import annotations

import json
from pathlib import Path

from eval.osp.ai import cli


def _write_profile(tmp_path: Path) -> Path:
    """Write a minimal profile that needs no vendor NMS."""
    path = tmp_path / "osp-ai.env"
    path.write_text(
        "\n".join(
            [
                "A2AT_OSP_AI_NMS=0",
                f"A2AT_OSP_AI_RUN_DIR={tmp_path / 'launcher'}",
                f"A2AT_OSP_AI_RUNS_DIR={tmp_path / 'runs-ai'}",
                "A2AT_OSP_AI_UI_PORT=18180",
                "A2AT_OSP_AI_OSS_PORT=18181",
                "A2AT_OSP_AI_MIE_PORT=18182",
                "A2AT_OSP_AI_WEBHOOK_PORT=19100",
                "A2AT_OSP_AI_OSS_WEBHOOK_PORT=19101",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    return path


def _write_batch(tmp_path: Path) -> tuple[Path, str]:
    """Write one finished batch with a failing validate stage."""
    batch_id = "batch-test"
    target = tmp_path / "runs-ai" / "tester" / f"{batch_id}.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    document = {
        "schema": "osp-tester/v1",
        "batchId": batch_id,
        "status": "finished",
        "caseIds": [1],
        "protocols": ["a2a-t"],
        "stopAfter": "working",
        "decisionMode": "live",
        "summary": {"total": 1, "failed": 1},
        "items": [
            {
                "caseId": 1,
                "name": "速率保障目标",
                "protocol": "a2a-t",
                "status": "failed",
                "runId": "run-1",
                "traceId": "trace-1",
                "refs": {"operation": "Create"},
                "answer": {"taskId": "task-1"},
                "baseline": None,
                "stages": {
                    "validate": {
                        "stage": "validate",
                        "status": "failed",
                        "message": "blocked by content.param_missing",
                        "expected": "pass",
                        "actual": "blocked",
                        "diffs": [
                            {"path": "Task Target", "expected": "A", "actual": "B", "reason": ""}
                        ],
                    }
                },
            }
        ],
    }
    target.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
    return target, batch_id


def test_profile_init_writes_a_template(tmp_path: Path) -> None:
    target = tmp_path / "profile.env"
    assert cli.main(["profile", "init", "--path", str(target)]) == 0
    text = target.read_text(encoding="utf-8")
    assert "A2AT_OSP_AI_UI_PORT=18080" in text
    assert "A2AT_OSP_AI_WEBHOOK_PORT=19000" in text
    assert "A2AT_SAMPLE_NMS_TOKEN_VALUE=" in text


def test_triage_writes_failures_from_a_batch_file(tmp_path: Path) -> None:
    profile = _write_profile(tmp_path)
    _, batch_id = _write_batch(tmp_path)
    out = tmp_path / "failures.jsonl"
    code = cli.main(
        [
            "triage",
            batch_id,
            "--profile",
            str(profile),
            "--out",
            str(out),
        ]
    )
    assert code == 0
    lines = out.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["caseId"] == 1
    assert record["stage"] == "validate"
    assert record["signals"]["validationCode"] == "content.param_missing"
    assert record["firstDiff"]["path"] == "Task Target"
    assert record["artifacts"]["batch"].endswith(f"{batch_id}.json")


def test_report_renders_a_self_contained_markdown(tmp_path: Path) -> None:
    profile = _write_profile(tmp_path)
    _, batch_id = _write_batch(tmp_path)
    out = tmp_path / "report.md"
    code = cli.main(
        [
            "report",
            batch_id,
            "--profile",
            str(profile),
            "--out",
            str(out),
        ]
    )
    assert code == 0
    text = out.read_text(encoding="utf-8")
    assert f"# OSP AI 跑批报告 {batch_id}" in text
    assert "## 2. 失败清单" in text
    assert "content.param_missing" in text
    assert "AI 填" in text
    assert "| cases | 1 |" in text
    assert "| stopAfter | working |" in text


def test_triage_accepts_an_external_batch_file_and_runs_dir(tmp_path: Path) -> None:
    profile = _write_profile(tmp_path)
    batch_path, _ = _write_batch(tmp_path)
    out = tmp_path / "external.jsonl"
    code = cli.main(
        [
            "triage",
            "ignored-id",
            "--profile",
            str(profile),
            "--batch-file",
            str(batch_path),
            "--runs-dir",
            str(tmp_path / "runs-ai"),
            "--out",
            str(out),
        ]
    )
    assert code == 0
    assert out.is_file()


def test_install_skill_copies_the_runbook(tmp_path: Path) -> None:
    target = tmp_path / "skills" / "osp-ai-eval" / "SKILL.md"
    assert cli.main(["install-skill", "--target", str(target)]) == 0
    text = target.read_text(encoding="utf-8")
    assert text.startswith("---\nname: osp-ai-eval\n")
    assert "## 0. 硬规则" in text


def test_batch_run_rejects_a_pure_a2a_sdk_stage(tmp_path: Path) -> None:
    profile = _write_profile(tmp_path)
    code = cli.main(
        [
            "batch",
            "run",
            "--profile",
            str(profile),
            "--cases",
            "1",
            "--protocols",
            "a2a",
            "--stop-after",
            "validate",
        ]
    )
    assert code == 2
