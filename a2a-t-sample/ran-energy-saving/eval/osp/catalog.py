"""Read-only catalog of the OSP energy-saving evaluation cases.

The tester console lists the catalog, lets the operator filter it, and starts one batch item per
(case, protocol). The YAML files stay the source of truth; this module only projects the fields the
console needs and keeps the full document for execution.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from eval.osp.runner import DEFAULT_CASES_DIR, load_cases

PROTOCOL_A2A_T: Final[str] = "a2a-t"
PROTOCOL_A2A: Final[str] = "a2a"
PROTOCOLS: Final[tuple[str, ...]] = (PROTOCOL_A2A_T, PROTOCOL_A2A)


@dataclass(frozen=True)
class CaseSummary:
    """One catalog row shown by the tester console."""

    id: int
    name: str
    dimension: str
    operation: str
    tags: tuple[str, ...]
    validate: str | None
    validate_label: str
    protocols: tuple[str, ...]
    a2a_negotiation_rounds: int
    a2at_negotiation_rounds: int
    file: str

    def to_dict(self) -> dict[str, object]:
        """Return the JSON-friendly catalog row."""
        return {
            "id": self.id,
            "name": self.name,
            "dimension": self.dimension,
            "operation": self.operation,
            "tags": list(self.tags),
            "validate": self.validate,
            "validateLabel": self.validate_label,
            "protocols": list(self.protocols),
            "a2aNegotiationRounds": self.a2a_negotiation_rounds,
            "a2aTNegotiationRounds": self.a2at_negotiation_rounds,
            "file": self.file,
        }


class CaseCatalog:
    """Loaded OSP cases plus the filter the console applies."""

    def __init__(self, cases_dir: Path = DEFAULT_CASES_DIR) -> None:
        self._cases: dict[int, dict[str, Any]] = {
            int(document["id"]): document for document in load_cases(cases_dir)
        }
        self._summaries: dict[int, CaseSummary] = {
            case_id: self._summarize(document) for case_id, document in self._cases.items()
        }

    @property
    def count(self) -> int:
        """Number of loaded cases."""
        return len(self._cases)

    def get(self, case_id: int) -> dict[str, Any] | None:
        """Return one full case document, or ``None`` when unknown."""
        return self._cases.get(case_id)

    def summaries(self) -> list[CaseSummary]:
        """Return every catalog row, ordered by id."""
        return [self._summaries[case_id] for case_id in sorted(self._summaries)]

    def filter(
        self,
        *,
        query: str = "",
        dimensions: tuple[str, ...] = (),
        tags: tuple[str, ...] = (),
        operations: tuple[str, ...] = (),
        validates: tuple[str, ...] = (),
        protocol: str | None = None,
    ) -> list[CaseSummary]:
        """Return the rows matching every supplied filter (tags match any selected tag)."""
        text = query.strip().lower()
        selected_tags = set(tags)
        selected_validates = set(validates)
        result: list[CaseSummary] = []
        for summary in self.summaries():
            if text and text not in summary.name.lower() and text not in str(summary.id):
                continue
            if dimensions and summary.dimension not in dimensions:
                continue
            if selected_tags and not selected_tags.intersection(summary.tags):
                continue
            if operations and summary.operation not in operations:
                continue
            if selected_validates:
                marker = summary.validate or "unfilled"
                if marker not in selected_validates:
                    continue
            if protocol is not None and protocol not in summary.protocols:
                continue
            result.append(summary)
        return result

    @staticmethod
    def _summarize(document: dict[str, Any]) -> CaseSummary:
        protocols = document["expect"]["protocols"]
        a2at = protocols["a2a-t"]
        a2a = protocols["a2a"]
        path = document.get("_path")
        return CaseSummary(
            id=int(document["id"]),
            name=str(document["name"]),
            dimension=str(document.get("dimension") or ""),
            operation=str(document.get("operation") or ""),
            tags=tuple(str(tag) for tag in document.get("tags") or ()),
            validate=a2at.get("validate"),
            validate_label=str(a2at.get("validate_label") or ""),
            protocols=PROTOCOLS,
            a2a_negotiation_rounds=int(a2a["negotiation"]["expected_rounds"]),
            a2at_negotiation_rounds=int(a2at["negotiation"]["expected_rounds"]),
            file=path.name if isinstance(path, Path) else "",
        )
