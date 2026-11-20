"""Tests of the reloadable OSP case catalog."""

from __future__ import annotations

from pathlib import Path

import pytest
from eval.osp.catalog import CaseCatalog, CatalogReloadError
from eval.osp.expectations import negotiation_message_expectation, slot_error_expectation


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


# ---------------------------------------------------------------------------
# Hand-authored overlay
# ---------------------------------------------------------------------------


def _overlay_case_document(
    case_id: int,
    *,
    validate: str = "blocked",
    a2at_rounds: int = 1,
) -> str:
    """Return a case document with the fields the overlay validator checks."""
    return f"""
id: {case_id}
name: Case {case_id}
dimension: dimension-a
operation: Modify
tags: [tag-a]
expect:
  protocols:
    a2a-t:
      validate: {validate}
      task_t: |-
        ## Operation Type
        Modify
      negotiation:
        expected: false
        expected_rounds: {a2at_rounds}
        kinds: null
        rounds: []
    a2a:
      negotiation:
        expected: false
        expected_rounds: 0
        kinds: null
        rounds: []
"""


def _write_overlay_case(cases_dir: Path, case_id: int, **kwargs: object) -> None:
    (cases_dir / f"case-{case_id}.yaml").write_text(
        _overlay_case_document(case_id, **kwargs), encoding="utf-8"
    )


def _write_overlay(path: Path, body: str) -> None:
    path.write_text(body, encoding="utf-8")


def _overlay_fixture(tmp_path: Path) -> tuple[Path, Path]:
    """Return (cases_dir, overlay_path) with the overlay outside the case directory."""
    cases_dir = tmp_path / "cases"
    cases_dir.mkdir()
    return cases_dir, tmp_path / "overlay.yaml"


def test_the_overlay_is_merged_at_load_time(tmp_path: Path) -> None:
    cases_dir, overlay = _overlay_fixture(tmp_path)
    _write_overlay_case(cases_dir, 1)
    _write_overlay(
        overlay,
        """
schema: osp-expectations-overlay/v1
cases:
  - id: 1
    a2a-t:
      slot_errors:
        mode: any
        expect:
          - code: slot.constraint_violated
      negotiation:
        messages:
          - - |-
              ## Feasibility Negotiation
              Reduce the target to 2Mbps.
""",
    )

    catalog = CaseCatalog(cases_dir, overlay_path=overlay)

    case = catalog.get(1)
    assert case is not None
    a2at = case["expect"]["protocols"]["a2a-t"]
    assert a2at["slot_errors"]["mode"] == "any"
    assert a2at["negotiation"]["messages"][0][0].startswith("## Feasibility")
    # The generated case file is untouched: the overlay is the hand-authored source.
    assert "slot_errors" not in (cases_dir / "case-1.yaml").read_text(encoding="utf-8")
    assert slot_error_expectation(case) is not None
    assert negotiation_message_expectation(case, "a2a-t") is not None


def test_the_overlay_is_part_of_the_catalog_version(tmp_path: Path) -> None:
    cases_dir, overlay = _overlay_fixture(tmp_path)
    _write_overlay_case(cases_dir, 1)
    _write_overlay(overlay, "schema: osp-expectations-overlay/v1\ncases: []\n")
    catalog = CaseCatalog(cases_dir, overlay_path=overlay)
    before = catalog.version

    _write_overlay(
        overlay,
        """
schema: osp-expectations-overlay/v1
cases:
  - id: 1
    a2a-t:
      slot_errors: [slot.constraint_violated]
""",
    )
    outcome = catalog.reload()

    assert outcome.version != before
    assert catalog.version == outcome.version


def test_an_overlay_on_a_non_blocked_case_is_rejected(tmp_path: Path) -> None:
    cases_dir, overlay = _overlay_fixture(tmp_path)
    _write_overlay_case(cases_dir, 1, validate="pass")
    _write_overlay(
        overlay,
        """
schema: osp-expectations-overlay/v1
cases:
  - id: 1
    a2a-t:
      slot_errors: [slot.constraint_violated]
""",
    )

    with pytest.raises(ValueError, match="non-blocked"):
        CaseCatalog(cases_dir, overlay_path=overlay)


def test_an_overlay_naming_an_unknown_case_is_rejected(tmp_path: Path) -> None:
    cases_dir, overlay = _overlay_fixture(tmp_path)
    _write_overlay_case(cases_dir, 1)
    _write_overlay(
        overlay,
        """
schema: osp-expectations-overlay/v1
cases:
  - id: 99
    a2a-t:
      slot_errors: [slot.constraint_violated]
""",
    )

    with pytest.raises(ValueError, match="unknown cases"):
        CaseCatalog(cases_dir, overlay_path=overlay)


def test_an_overlay_may_only_set_the_annotated_blocks(tmp_path: Path) -> None:
    cases_dir, overlay = _overlay_fixture(tmp_path)
    _write_overlay_case(cases_dir, 1)
    _write_overlay(
        overlay,
        """
schema: osp-expectations-overlay/v1
cases:
  - id: 1
    a2a-t:
      validate: pass
""",
    )

    with pytest.raises(ValueError, match="may only set"):
        CaseCatalog(cases_dir, overlay_path=overlay)


def test_a_failed_overlay_reload_keeps_the_previous_snapshot(tmp_path: Path) -> None:
    cases_dir, overlay = _overlay_fixture(tmp_path)
    _write_overlay_case(cases_dir, 1)
    _write_overlay(overlay, "schema: osp-expectations-overlay/v1\ncases: []\n")
    catalog = CaseCatalog(cases_dir, overlay_path=overlay)
    _write_overlay(overlay, "cases:\n  - id: 42\n    a2a-t:\n      slot_errors: [x]\n")

    with pytest.raises(CatalogReloadError):
        catalog.reload()

    assert catalog.get(1) is not None


def test_a_missing_overlay_is_ignored(tmp_path: Path) -> None:
    cases_dir, _overlay = _overlay_fixture(tmp_path)
    _write_overlay_case(cases_dir, 1)

    catalog = CaseCatalog(cases_dir, overlay_path=tmp_path / "absent.yaml")

    assert catalog.count == 1
    assert "slot_errors" not in catalog.get(1)["expect"]["protocols"]["a2a-t"]


def test_a_live_annotation_overlay_is_merged_and_versioned(tmp_path: Path) -> None:
    """The annotation database is a live source; no export file is involved."""
    cases_dir, _overlay = _overlay_fixture(tmp_path)
    _write_overlay_case(cases_dir, 1)
    token = {"value": "v1"}
    blocks: dict[int, dict[str, dict[str, object]]] = {
        1: {"a2a-t": {"slot_errors": ["slot.constraint_violated"]}}
    }
    catalog = CaseCatalog(
        cases_dir,
        overlay_path=tmp_path / "absent.yaml",
        annotation_overlay=lambda: blocks,
        annotation_token=lambda: token["value"],
    )

    case = catalog.get(1)
    assert case is not None
    assert case["expect"]["protocols"]["a2a-t"]["slot_errors"] == ["slot.constraint_violated"]
    first = catalog.version

    token["value"] = "v2"
    outcome = catalog.reload()

    assert outcome.version != first
    assert catalog.version == outcome.version


def test_a_broken_live_annotation_overlay_is_skipped_not_fatal(tmp_path: Path) -> None:
    """A stale confirmed block must not take the tester's whole catalog down."""
    cases_dir, _overlay = _overlay_fixture(tmp_path)
    _write_overlay_case(cases_dir, 1)
    catalog = CaseCatalog(
        cases_dir,
        overlay_path=tmp_path / "absent.yaml",
        annotation_overlay=lambda: {
            1: {"a2a-t": {"slot_errors": ["slot.constraint_violated"]}},
            99: {"a2a-t": {"slot_errors": ["x"]}},
        },
    )

    assert catalog.count == 1
    assert catalog.get(1)["expect"]["protocols"]["a2a-t"]["slot_errors"] == [
        "slot.constraint_violated"
    ]
