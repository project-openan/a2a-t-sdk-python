"""Filesystem layout of the sample and of the AI evaluation artifacts."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from eval.osp.ai.types import ArtifactSet


def sample_root() -> Path:
    """Return the ``ran-energy-saving`` sample root."""
    return Path(__file__).resolve().parents[3]


def batch_json_path(runs_dir: Path, batch_id: str) -> Path:
    """Return the persisted tester batch document of one batch."""
    return Path(runs_dir) / "tester" / f"{batch_id}.json"


def ai_state_dir(runs_dir: Path, batch_id: str) -> Path:
    """Return the directory holding the triage, manifest and report inputs of one batch."""
    return Path(runs_dir) / "ai-eval" / batch_id


def failures_path(runs_dir: Path, batch_id: str) -> Path:
    """Return the ``failures.jsonl`` path of one batch."""
    return ai_state_dir(runs_dir, batch_id) / "failures.jsonl"


def manifest_path(runs_dir: Path, batch_id: str) -> Path:
    """Return the ``manifest.json`` path of one batch."""
    return ai_state_dir(runs_dir, batch_id) / "manifest.json"


def run_artifacts(runs_dir: Path, run_id: str | None, *, batch: Path | None = None) -> ArtifactSet:
    """Return the artifacts that may exist for one run.

    ``server-trace.jsonl`` is only written when a local RAN server runs; in the real-NMS topology
    the file is absent. The client store and the wire log are always the evidence floor.
    """
    root = Path(runs_dir)
    if not run_id:
        return ArtifactSet(client_state=root / "client-state.sqlite3", batch=batch)
    run_dir = root / run_id
    return ArtifactSet(
        wire=_existing_or_none(run_dir / "wire.jsonl"),
        wire_oss=_existing_or_none(run_dir / "wire-oss.jsonl"),
        wire_error=_existing_or_none(run_dir / "wire-error.jsonl"),
        server_trace=_existing_or_none(run_dir / "server-trace.jsonl"),
        client_state=_existing_or_none(root / "client-state.sqlite3"),
        oss_task_db=_existing_or_none(root / "oss-task-status.sqlite3"),
        batch=batch,
    )


def report_path(docs_local: Path, batch_id: str, *, day: date | None = None) -> Path:
    """Return the self-contained Markdown report path of one batch."""
    target = Path(docs_local) / "osp-ai"
    return target / f"{(day or date.today()).isoformat()}-{batch_id}.md"


def _existing_or_none(path: Path) -> Path | None:
    """Return ``path`` when it exists, else ``None`` (the report marks it as absent)."""
    return path if path.exists() else None
