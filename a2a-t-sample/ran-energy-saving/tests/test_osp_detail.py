"""Tests of the Markdown detail report of failing OSP cases."""

from __future__ import annotations

from typing import Any

from eval.osp.detail import render_failures


def _report() -> dict[str, Any]:
    return {
        "schema": "osp-eval-run/v1",
        "startedAt": "2026-11-15T10:00:00Z",
        "stopAfter": "validate",
        "summary": {
            "total": 2,
            "passed": 1,
            "failed": 1,
            "error": 0,
            "stages": {
                "intent": {"passed": 2, "failed": 0, "recorded": 0, "error": 0},
                "task_t": {"passed": 1, "failed": 1, "recorded": 0, "error": 0},
                "validate": {"passed": 2, "failed": 0, "recorded": 0, "error": 0},
            },
        },
        "cases": [
            {"id": 1, "name": "passing row", "passed": True, "stages": {}},
            {
                "id": 2,
                "name": "failing row",
                "passed": False,
                "error": None,
                "stages": {
                    "intent": {"stage": "intent", "status": "passed", "message": ""},
                    "task_t": {
                        "stage": "task_t",
                        "status": "failed",
                        "message": "",
                        "diffs": [
                            {
                                "path": "task_t.Task Target",
                                "expected": "",
                                "actual": "Conservative",
                                "reason": "changed",
                            }
                        ],
                    },
                    "validate": {"stage": "validate", "status": "passed", "message": ""},
                },
            },
        ],
    }


def _cases() -> dict[int, dict[str, Any]]:
    return {
        2: {
            "id": 2,
            "name": "failing row",
            "operation": "Create",
            "dimension": "D2 专业术语/行业黑话",
            "user_input": "Implement energy saving in A2ATestRegion region.",
            "expect": {
                "protocols": {
                    "a2a-t": {"validate": "pass_with_terminology", "validate_label": "不拦截，携带术语上下文信息"}
                }
            },
        }
    }


def test_render_lists_only_failing_cases_with_their_diffs() -> None:
    text = render_failures(_report(), _cases())

    assert "## 2. failing row" in text
    assert "passing row" not in text
    assert "Implement energy saving in A2ATestRegion region." in text
    assert "- operation: `Create`" in text
    assert "- expected validation: `pass_with_terminology` (不拦截，携带术语上下文信息)" in text
    assert "| task_t | failed |" in text
    assert "| `task_t.Task Target` | `` | `Conservative` |" in text
    assert "- `task_t`: 1 passed, 1 failed, 0 recorded, 0 error" in text


def test_render_includes_the_interception_segment() -> None:
    report = _report()
    report["cases"][1]["interception"] = {
        "passed": False,
        "stages": {
            "validate": {
                "stage": "validate",
                "status": "failed",
                "message": "slot.not_provided",
                "diffs": [
                    {"path": "validate.verdict", "expected": "blocked", "actual": "pass", "reason": "changed"}
                ],
            }
        },
    }

    text = render_failures(report, _cases())

    assert "### interception (original request)" in text
    assert "| validate | failed | slot.not_provided |" in text
    assert "| `validate.verdict` | `blocked` | `pass` |" in text


def test_render_returns_nothing_when_every_case_passed() -> None:
    report = _report()
    report["cases"] = [{"id": 1, "name": "passing row", "passed": True, "stages": {}}]

    assert render_failures(report, {}) == ""
