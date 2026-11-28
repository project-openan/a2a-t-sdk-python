"""Per-case reference replies derived from the OSP YAML.

The A2A P column is the recorded plain-text answer to the intermediary hop. The tester no longer
replays it: it is exported next to the case so the client agent's own answer can be reviewed
against it. Keeping the parsing here means the case files stay data and the protocol layer stays
unchanged.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence


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
