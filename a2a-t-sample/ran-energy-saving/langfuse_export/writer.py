"""Export directory layout, hashing and manifest generation."""

from __future__ import annotations

import hashlib
import json
import math
import shutil
import zipfile
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


_XLSX_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
_XLSX_REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_XLSX_PKG_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"


def _xlsx_col(index: int) -> str:
    """Return the Excel column name for a 1-based index (1 -> A, 27 -> AA)."""
    name = ""
    while index > 0:
        index, remainder = divmod(index - 1, 26)
        name = chr(65 + remainder) + name
    return name


def _xlsx_escape(value: str) -> str:
    return (
        value.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _xlsx_cell(ref: str, value: Any) -> str:
    if value is None or value == "":
        return f'<c r="{ref}"/>'
    if isinstance(value, bool):
        value = "true" if value else "false"
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
            return f'<c r="{ref}"/>'
        return f'<c r="{ref}"><v>{value}</v></c>'
    text = _xlsx_escape(str(value))
    return f'<c r="{ref}" t="inlineStr"><is><t xml:space="preserve">{text}</t></is></c>'


def _sheet_xml(headers: list[str], rows: list[list[Any]]) -> str:
    lines: list[str] = []
    for row_index, values in enumerate([headers, *rows], start=1):
        cells = "".join(
            _xlsx_cell(f"{_xlsx_col(col_index)}{row_index}", value)
            for col_index, value in enumerate(values, start=1)
        )
        lines.append(f'<row r="{row_index}">{cells}</row>')
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<worksheet xmlns="{_XLSX_NS}"><sheetData>{"".join(lines)}</sheetData></worksheet>'
    )


def write_xlsx(path: Path, headers: list[str], rows: list[list[Any]], sheet_name: str = "metrics") -> None:
    """Write a minimal single-sheet .xlsx (inline strings, no styling, stdlib only)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    safe_sheet = _xlsx_escape(sheet_name or "metrics").replace("/", "_").replace("\\", "_")[:31]
    content_types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/xl/workbook.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
        '<Override PartName="/xl/worksheets/sheet1.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        "</Types>"
    )
    root_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<Relationships xmlns="{_XLSX_PKG_REL_NS}">'
        '<Relationship Id="rId1" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
        'Target="xl/workbook.xml"/>'
        "</Relationships>"
    )
    workbook = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<workbook xmlns="{_XLSX_NS}" xmlns:r="{_XLSX_REL_NS}">'
        f'<sheets><sheet name="{safe_sheet}" sheetId="1" r:id="rId1"/></sheets></workbook>'
    )
    workbook_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<Relationships xmlns="{_XLSX_PKG_REL_NS}">'
        '<Relationship Id="rId1" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
        'Target="worksheets/sheet1.xml"/>'
        "</Relationships>"
    )
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", content_types)
        archive.writestr("_rels/.rels", root_rels)
        archive.writestr("xl/workbook.xml", workbook)
        archive.writestr("xl/_rels/workbook.xml.rels", workbook_rels)
        archive.writestr("xl/worksheets/sheet1.xml", _sheet_xml(headers, rows))


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
