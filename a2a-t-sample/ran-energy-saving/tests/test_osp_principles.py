"""Contract tests of the shared negotiation principles asset."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
import yaml
from eval.osp.principles import NegotiationPrinciples, available_versions, load_principles

OSP_DIR = Path(__file__).resolve().parents[1] / "eval" / "osp"
PRINCIPLES_PATH = OSP_DIR / "negotiation-principles.yaml"


def _write(tmp_path: Path, document: object) -> Path:
    path = tmp_path / "principles.yaml"
    path.write_text(yaml.safe_dump(document, allow_unicode=True), encoding="utf-8")
    return path


def _document(**overrides: object) -> dict[str, object]:
    document: dict[str, object] = {
        "schema": "osp-negotiation-principles/v1",
        "version": "2026-11-23.1",
        "principles": "Information: supply the requested items.\nFeasibility: accept the recommendation.",
    }
    document.update(overrides)
    return document


def test_the_committed_asset_loads() -> None:
    principles = load_principles()
    assert isinstance(principles, NegotiationPrinciples)
    assert principles.version
    assert principles.text.strip()
    assert principles.sha256 == hashlib.sha256(principles.text.encode("utf-8")).hexdigest()
    assert principles.version in available_versions()


def test_a_requested_version_must_match_the_file(tmp_path: Path) -> None:
    path = _write(tmp_path, _document())
    assert load_principles("2026-11-23.1", path=path).version == "2026-11-23.1"
    with pytest.raises(ValueError):
        load_principles("2026-11-24.1", path=path)


def test_an_empty_asset_is_rejected(tmp_path: Path) -> None:
    path = _write(tmp_path, _document(principles="   "))
    with pytest.raises(ValueError):
        load_principles(path=path)


def test_an_unknown_schema_is_rejected(tmp_path: Path) -> None:
    path = _write(tmp_path, _document(schema="osp-negotiation-principles/v2"))
    with pytest.raises(ValueError):
        load_principles(path=path)


def test_a_missing_file_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_principles(path=tmp_path / "absent.yaml")
