"""Export directory layout, hashing and manifest generation."""

from __future__ import annotations

import hashlib
import json
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

from . import __version__
from .models import RequestRecord, TraceBundle


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


def write_json(path: Path, value: Any) -> None:
    write_text(path, json.dumps(value, indent=2, ensure_ascii=False, sort_keys=False) + "\n")


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    write_text(path, "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _relative(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def collect_file_records(root: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        rel = _relative(path, root)
        if rel == "manifest.json":
            continue
        records.append({"path": rel, "bytes": path.stat().st_size, "sha256": sha256_file(path)})
    return records


def request_records(log: list[RequestRecord]) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    for record in log:
        entry = grouped.setdefault(
            record.endpoint,
            {"endpoint": record.endpoint, "status": record.status, "durationMs": 0, "pages": 0},
        )
        entry["status"] = record.status if record.status is not None else entry["status"]
        entry["durationMs"] += record.duration_ms
        entry["pages"] += 1
    return list(grouped.values())


def build_manifest(
    export_dir: Path,
    bundle: TraceBundle,
    exported_at: datetime,
    host_source: str,
) -> dict[str, Any]:
    summary = bundle.summary
    return {
        "tool": {"name": "langfuse-export", "version": __version__},
        "exportedAt": exported_at.isoformat(timespec="seconds"),
        "host": bundle.host,
        "hostSource": host_source,
        "apiVersion": bundle.api_version,
        "backend": bundle.backend,
        "network": bundle.network.to_dict(),
        "projectId": bundle.project_id,
        "projectName": bundle.project_name,
        "metadataTruncated": bundle.metadata_truncated,
        "truncated": bundle.truncated,
        "trace": summary.to_dict(),
        "requests": request_records(bundle.request_log),
        "warnings": list(bundle.warnings),
        "notices": list(bundle.notices),
        "files": collect_file_records(export_dir),
        "sessionExported": bool(bundle.session_rows),
    }


def make_zip(export_dir: Path) -> Path:
    archive = shutil.make_archive(
        str(export_dir.with_suffix("")),
        "zip",
        root_dir=str(export_dir.parent),
        base_dir=export_dir.name,
    )
    return Path(archive)
