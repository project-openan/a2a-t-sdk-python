"""Load and validate the shared client negotiation principles.

The principles are one test-side asset shared by every case in a batch. They are frozen per
batch: the tester resolves a version when the batch is created and records it on every item, so
an A2A-T and an A2A batch can only be compared when they used the same version.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

import yaml

#: Schema of the committed principles document.
SCHEMA = "osp-negotiation-principles/v1"

#: The committed asset, next to this module.
DEFAULT_PATH = Path(__file__).with_name("negotiation-principles.yaml")


@dataclass(frozen=True)
class NegotiationPrinciples:
    """One frozen version of the shared client decision principles."""

    version: str
    text: str
    sha256: str


def load_principles(
    version: str | None = None,
    *,
    path: Path | None = None,
) -> NegotiationPrinciples:
    """Load the shared principles, optionally requiring one version.

    Args:
        version: expected version; ``None`` accepts the version the file declares.
        path: asset path; defaults to the committed document.

    Raises:
        FileNotFoundError: when the asset does not exist.
        ValueError: when the schema, version or body is missing or mismatched.
    """
    asset = path or DEFAULT_PATH
    if not asset.exists():
        raise FileNotFoundError(f"negotiation principles not found: {asset}")
    document = yaml.safe_load(asset.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise ValueError(f"negotiation principles must be a mapping: {asset}")
    schema = str(document.get("schema") or "").strip()
    if schema != SCHEMA:
        raise ValueError(f"unsupported negotiation principles schema: {schema!r}")
    declared = str(document.get("version") or "").strip()
    if not declared:
        raise ValueError(f"negotiation principles carry no version: {asset}")
    if version is not None and version.strip() != declared:
        raise ValueError(
            f"negotiation principles version mismatch: requested {version!r}, file has {declared!r}"
        )
    text = str(document.get("principles") or "").strip()
    if not text:
        raise ValueError(f"negotiation principles carry no body: {asset}")
    return NegotiationPrinciples(
        version=declared,
        text=text,
        sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
    )


def available_versions(*, path: Path | None = None) -> tuple[str, ...]:
    """Return the versions a caller may request from the committed asset."""
    return (load_principles(path=path).version,)
