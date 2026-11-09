"""Per-case negotiation scripts derived from the OSP YAML.

The workbook stores two different kinds of reply:

* A2A-T (Q column) — a rendered Task-T negotiation answer; it is turned into the structured
  ``NegotiationDecision`` the A2A-T client already knows how to send.
* A2A (P column) — the OSS user's plain-text answer to the intermediary hop; it is replayed as the
  pure-A2A ``INPUT_REQUIRED`` answer.

Keeping the parsing here means the case files stay data and the protocol layer stays unchanged.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from shared.models import NegotiationDecision

#: Headings the workbook uses inside one Task-T negotiation reply.
_TARGET = "Target Negotiation Result"
_FEASIBILITY = "Feasibility Negotiation Result"
_INFORMATION = "Information Negotiation Result"


def a2at_decisions(case: Mapping[str, object]) -> list[NegotiationDecision]:
    """Turn the A2A-T Q-column replies of one case into ordered client decisions.

    A single workbook reply can carry both a target and a feasibility section; the order of the
    sections is preserved because the server sends its proposals in that order. The current corpus
    has no such reply (id 34 was refreshed to a feasibility-only answer), but the vendor can still
    return one.
    """
    decisions: list[NegotiationDecision] = []
    for text in _negotiation_rounds(case, "a2a-t"):
        if _TARGET in text:
            detail = _section_detail(text, _TARGET)
            decisions.append(
                NegotiationDecision(
                    action="accept",
                    rationale=detail or "Accept the target negotiation.",
                    confirmed_intent=detail or "Accept the target negotiation.",
                )
            )
        if _FEASIBILITY in text:
            detail = _section_detail(text, _FEASIBILITY)
            decisions.append(
                NegotiationDecision(
                    action="accept",
                    rationale=detail or "Accept the feasibility negotiation.",
                    feasibility_summary=detail or "Accept the feasibility negotiation.",
                )
            )
        if _INFORMATION in text:
            detail = _section_detail(text, _INFORMATION)
            decisions.append(
                NegotiationDecision(
                    action="accept",
                    rationale=detail or "Accept the information negotiation.",
                )
            )
    return decisions


def pure_answers(case: Mapping[str, object]) -> list[str]:
    """Return the A2A P-column answers of one case, in order."""
    return _negotiation_rounds(case, "a2a")


def _negotiation_rounds(case: Mapping[str, object], protocol: str) -> list[str]:
    expect = case.get("expect")
    if not isinstance(expect, Mapping):
        raise ValueError("case has no expect block")
    protocols = expect.get("protocols")
    if not isinstance(protocols, Mapping):
        raise ValueError("case has no protocol expectations")
    block = protocols.get(protocol)
    if not isinstance(block, Mapping):
        raise ValueError(f"case has no {protocol} expectations")
    negotiation = block.get("negotiation")
    if not isinstance(negotiation, Mapping):
        raise ValueError(f"case has no {protocol} negotiation block")
    rounds = negotiation.get("rounds")
    if not isinstance(rounds, Sequence) or isinstance(rounds, str | bytes):
        raise ValueError(f"case has no {protocol} negotiation rounds")
    return [str(round_text) for round_text in rounds]


def _section_detail(text: str, heading: str) -> str:
    """Return the prose under ``## <heading> ... in Detail`` (or the heading's first section)."""
    detail = _collect_section(text, heading, require_detail=True)
    return detail or _collect_section(text, heading, require_detail=False)


def _collect_section(text: str, heading: str, *, require_detail: bool) -> str:
    collected: list[str] = []
    active = False
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if line.startswith("## "):
            title = line[3:].strip()
            matches = heading.lower() in title.lower()
            is_detail = "detail" in title.lower()
            active = matches and (is_detail or not require_detail)
            continue
        if not active or not line:
            continue
        if line.lower() == "accept":
            continue
        collected.append(line)
    return " ".join(collected).strip()
