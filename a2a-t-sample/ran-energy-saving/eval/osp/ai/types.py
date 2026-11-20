"""Value objects shared by the OSP AI evaluation commands."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping


@dataclass(frozen=True)
class Profile:
    """One isolated AI evaluation instance.

    Every field is resolved before the launcher is invoked. ``env`` holds the keys the sample
    reads from the process environment (webhook receivers, model endpoint, vendor NMS); the bind
    ports are *not* read from there and are passed to ``run_demo.py`` as explicit arguments.
    """

    name: str
    sample_root: Path
    source_path: Path | None
    run_dir: Path
    runs_dir: Path
    ui_host: str
    ui_port: int
    oss_port: int
    mie_port: int
    webhook_host: str
    webhook_port: int
    oss_webhook_port: int
    webhook_public_url: str
    oss_webhook_public_url: str
    language: str
    model_mode: str
    protocol: str
    decision_mode: str
    approval: str
    owner: str
    nms: bool
    nms_sim: bool
    nms_sim_port: int
    env: Mapping[str, str] = field(default_factory=dict)

    @property
    def ui_url(self) -> str:
        """Return the base URL of the Web UI and tester API."""
        return f"http://{self.ui_host}:{self.ui_port}"

    @property
    def bind_ports(self) -> tuple[tuple[str, int], ...]:
        """Return the loopback bind ports this instance owns, in report order."""
        return (
            ("ui", self.ui_port),
            ("oss", self.oss_port),
            ("mie", self.mie_port),
            ("webhook", self.webhook_port),
            ("oss-webhook", self.oss_webhook_port),
        )


@dataclass(frozen=True)
class RunIdentity:
    """The four identities that must never be conflated."""

    run_id: str | None = None
    trace_id: str | None = None
    task_id: str | None = None
    context_id: str | None = None

    def to_dict(self) -> dict[str, object]:
        """Return the JSON-friendly representation."""
        return {
            "runId": self.run_id,
            "traceId": self.trace_id,
            "taskId": self.task_id,
            "contextId": self.context_id,
        }


@dataclass(frozen=True)
class ArtifactSet:
    """Run artifacts an analyst may need, with ``None`` for what this topology does not write."""

    wire: Path | None = None
    wire_oss: Path | None = None
    wire_error: Path | None = None
    server_trace: Path | None = None
    client_state: Path | None = None
    oss_task_db: Path | None = None
    batch: Path | None = None

    def to_dict(self) -> dict[str, object]:
        """Return the JSON-friendly representation; paths stay absolute for the CLI."""
        return {
            "wire": _path_text(self.wire),
            "wireOss": _path_text(self.wire_oss),
            "wireError": _path_text(self.wire_error),
            "serverTrace": _path_text(self.server_trace),
            "clientState": _path_text(self.client_state),
            "ossTaskDb": _path_text(self.oss_task_db),
            "batch": _path_text(self.batch),
        }


@dataclass(frozen=True)
class FailureRecord:
    """One failing stage of one ``(case, protocol)`` tester item."""

    case_id: int
    name: str
    protocol: str
    operation: str
    item_status: str
    stage: str
    stage_status: str
    message: str
    expected: object
    actual: object
    first_diff: Mapping[str, object] | None
    signals: Mapping[str, object]
    run: RunIdentity
    baseline: Mapping[str, object] | None
    artifacts: ArtifactSet

    def to_dict(self) -> dict[str, object]:
        """Return the JSON-friendly representation written to ``failures.jsonl``."""
        return {
            "caseId": self.case_id,
            "name": self.name,
            "protocol": self.protocol,
            "operation": self.operation,
            "itemStatus": self.item_status,
            "stage": self.stage,
            "stageStatus": self.stage_status,
            "message": self.message,
            "expected": self.expected,
            "actual": self.actual,
            "firstDiff": dict(self.first_diff) if self.first_diff is not None else None,
            "signals": dict(self.signals),
            "run": self.run.to_dict(),
            "baseline": dict(self.baseline) if self.baseline is not None else None,
            "artifacts": self.artifacts.to_dict(),
        }


def _path_text(path: Path | None) -> str | None:
    """Return an absolute path string, or ``None``."""
    return str(path) if path is not None else None


def mapping_or_empty(value: object) -> dict[str, Any]:
    """Return a shallow copy of ``value`` when it is a mapping, else an empty dict."""
    return dict(value) if isinstance(value, Mapping) else {}
