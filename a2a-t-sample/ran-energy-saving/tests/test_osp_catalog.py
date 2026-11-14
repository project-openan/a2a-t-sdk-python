"""Tests of the reloadable OSP case catalog."""

from __future__ import annotations

from pathlib import Path

import pytest
from eval.osp.catalog import CaseCatalog, CatalogReloadError


def _case_document(case_id: int, name: str) -> str:
    """Return the minimal YAML document the catalog can summarize."""
    return f"""
id: {case_id}
name: {name}
dimension: dimension-a
operation: Create
tags: [tag-a]
expect:
  protocols:
    a2a-t:
      negotiation:
        expected_rounds: 0
    a2a:
      negotiation:
        expected_rounds: 0
"""


def _write_case(cases_dir: Path, case_id: int, name: str) -> Path:
    path = cases_dir / f"case-{case_id}.yaml"
    path.write_text(_case_document(case_id, name), encoding="utf-8")
    return path


def test_reload_picks_up_added_and_changed_cases(tmp_path: Path) -> None:
    _write_case(tmp_path, 1, "Case one")
    catalog = CaseCatalog(tmp_path)
    assert catalog.count == 1

    _write_case(tmp_path, 2, "Case two")
    _write_case(tmp_path, 1, "Case one renamed by a longer value")

    outcome = catalog.reload()

    assert catalog.count == 2
    first = catalog.get(1)
    assert first is not None
    assert first["name"] == "Case one renamed by a longer value"
    assert [summary.name for summary in catalog.summaries()] == [
        "Case one renamed by a longer value",
        "Case two",
    ]
    assert outcome.count == 2
    assert outcome.added == (2,)
    assert outcome.removed == ()
    assert outcome.changed_files == ("case-1.yaml",)
    assert outcome.version


def test_reload_removes_deleted_cases(tmp_path: Path) -> None:
    _write_case(tmp_path, 1, "Case one")
    _write_case(tmp_path, 2, "Case two")
    catalog = CaseCatalog(tmp_path)
    (tmp_path / "case-2.yaml").unlink()

    outcome = catalog.reload()

    assert catalog.count == 1
    assert catalog.get(2) is None
    assert outcome.removed == (2,)
    assert outcome.removed_files == ("case-2.yaml",)


def test_reload_without_changes_reports_no_diff(tmp_path: Path) -> None:
    _write_case(tmp_path, 1, "Case one")
    catalog = CaseCatalog(tmp_path)

    outcome = catalog.reload()

    assert outcome.count == 1
    assert outcome.added == ()
    assert outcome.removed == ()
    assert outcome.changed_files == ()
    assert outcome.removed_files == ()


def test_failed_reload_keeps_the_previous_snapshot(tmp_path: Path) -> None:
    _write_case(tmp_path, 1, "Case one")
    catalog = CaseCatalog(tmp_path)
    (tmp_path / "case-2.yaml").write_text("id: [unclosed", encoding="utf-8")

    with pytest.raises(CatalogReloadError):
        catalog.reload()

    assert catalog.count == 1
    kept = catalog.get(1)
    assert kept is not None
    assert kept["name"] == "Case one"
