"""Blocked-case follow-up requests (the OSP "A2A-T 拦截后补充信息" column).

A case whose A2A-T request is expected to be intercepted may carry a replacement request. The
tester records the interception first, then replays the replacement through the case's own
expectations, so the item does not stop at the blocked state and the intercepted request is never
dispatched. The replacement is a *follow-up to a held gate*: when the original request was not
intercepted, the case has already failed and the replacement must not run (see
:func:`followup_allowed`).

Two derived documents are needed and neither the SDK runner nor the tester should duplicate the
merge rules:

* :func:`interception_case` -- the original request with the intent/Task-T assertions disabled.
  Only the ``blocked`` validation is judged; the observed intent and Task-T are evidence.
* :func:`followup_case` -- the replacement request with ``validate: pass``, ``send: true`` and
  ``final_state: task_completed``; the case's intent, Task-T and negotiation expectations already
  describe the replacement request.

Both functions return deep copies; the catalog's case document is never mutated.
"""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from typing import Any, Final

#: Top-level case key holding the replacement request.
FOLLOWUP_KEY: Final[str] = "blocked_followup"
#: Protocol block every derived document looks at.
A2A_T_PATH: Final[tuple[str, ...]] = ("expect", "protocols", "a2a-t")


#: Observed validation verdict that proves the original request was intercepted.
BLOCKING_VERDICT: Final[str] = "blocked"
#: Observed verdict of a validation that could not run; it proves neither a block nor a pass.
UNAVAILABLE_VERDICT: Final[str] = "unavailable"
#: Message of a missed interception whose validation is recorded instead of judged.
MISSED_INTERCEPTION_NOTE: Final[str] = (
    "原请求未被拦截：补充请求（blocked_followup.input）未执行，本用例按记录处理。"
)


def followup_allowed(verdict: str | None) -> bool:
    """Return whether the replacement request of a blocked case may run.

    The replacement is a follow-up to a *held* gate. Only an observed ``pass`` proves the gate was
    bypassed: that case has nothing left to judge and the replacement must not run.

    A block whose slot errors differ from the case is still a block: that case fails on its slot
    expectation, but it never dispatched anything, so the replacement still runs. ``unavailable``
    and ``None`` are runs without a usable verdict (a validation that could not run, a batch that
    stops at ``intent`` / ``task_t``, or an errored validation call); neither proves a bypass, so
    the two-segment behaviour is kept.
    """
    return verdict is None or verdict in {BLOCKING_VERDICT, UNAVAILABLE_VERDICT}


def followup_input(case: Mapping[str, Any]) -> str | None:
    """Return the replacement request of one case, or ``None`` when absent or blank."""
    block = case.get(FOLLOWUP_KEY)
    if not isinstance(block, Mapping):
        return None
    text = block.get("input")
    if not isinstance(text, str) or not text.strip():
        return None
    return text


def has_followup(case: Mapping[str, Any]) -> bool:
    """Return whether one case carries a usable replacement request."""
    return followup_input(case) is not None


def is_blocked_case(case: Mapping[str, Any]) -> bool:
    """Return whether the case expects the A2A-T request to be intercepted."""
    return _read_block(case).get("validate") == "blocked"


def interception_case(case: Mapping[str, Any]) -> dict[str, Any]:
    """Return the case that records the interception instead of judging the blocked request."""
    derived = deepcopy(dict(case))
    assertions = _mapping(derived.get("assert"))
    assertions["intent"] = False
    protocol_assertions = _mapping(assertions.get("a2a-t"))
    protocol_assertions["task_t"] = False
    protocol_assertions["validate"] = True
    assertions["a2a-t"] = protocol_assertions
    derived["assert"] = assertions
    return derived


def original_dispatch_case(case: Mapping[str, Any]) -> dict[str, Any]:
    """Return the case that dispatches the original request after a missed interception.

    A case whose interception did not hold still has a request worth observing downstream, but
    every corpus expectation describes the *replacement* request. The derived document keeps the
    original ``user_input``, declares the send that is about to happen (so the acceptance stages
    are observed instead of skipped as "the case expects no send"), and turns every downstream
    assertion off: the run records evidence and only the interception segment judges the case.
    """
    derived = deepcopy(dict(case))
    assertions = _mapping(derived.get("assert"))
    assertions["intent"] = False
    protocol_assertions = _mapping(assertions.get("a2a-t"))
    for key in ("send", "task_t", "validate", "negotiation", "final_state"):
        protocol_assertions[key] = False
    assertions["a2a-t"] = protocol_assertions
    derived["assert"] = assertions
    _block(derived)["send"] = True
    return derived


def followup_case(case: Mapping[str, Any]) -> dict[str, Any]:
    """Return the case that sends the replacement request and judges its expectations.

    ``blocked_followup.expect`` and ``blocked_followup.assert`` are optional partial overrides
    merged into the derived document (for example a negotiation round the replacement triggers).
    """
    text = followup_input(case)
    if text is None:
        raise ValueError(f"case has no {FOLLOWUP_KEY}.input")
    if not is_blocked_case(case):
        raise ValueError(f"{FOLLOWUP_KEY} is only valid for a blocked A2A-T case")
    derived = deepcopy(dict(case))
    derived["user_input"] = text
    block = _block(derived)
    block["validate"] = "pass"
    block["send"] = True
    block["final_state"] = "task_completed"
    block["final_state_derived"] = True
    raw = case[FOLLOWUP_KEY]
    if isinstance(raw, Mapping):
        overrides = raw.get("expect")
        if isinstance(overrides, Mapping):
            _merge(_ensure_mapping(derived, "expect"), overrides)
        assertions = raw.get("assert")
        if isinstance(assertions, Mapping):
            _merge(_ensure_mapping(derived, "assert"), assertions)
    return derived


def _block(case: dict[str, Any]) -> dict[str, Any]:
    """Return the mutable A2A-T protocol block of one derived case, creating it when missing."""
    value: dict[str, Any] = _ensure_mapping(case, A2A_T_PATH[0])
    for key in A2A_T_PATH[1:]:
        value = _ensure_mapping(value, key)
    return value


def _read_block(case: Mapping[str, Any]) -> Mapping[str, Any]:
    """Return the A2A-T protocol block of one case without creating it."""
    value: object = case
    for key in A2A_T_PATH:
        value = value.get(key) if isinstance(value, Mapping) else None
    return value if isinstance(value, Mapping) else {}


def _ensure_mapping(container: dict[str, Any], key: str) -> dict[str, Any]:
    """Return ``container[key]`` as a mutable mapping, replacing a missing/non-mapping value."""
    value = container.get(key)
    if not isinstance(value, dict):
        value = _mapping(value)
        container[key] = value
    return value


def _mapping(value: object) -> dict[str, Any]:
    """Return a mutable copy of one mapping value (empty when it is not a mapping)."""
    return dict(value) if isinstance(value, Mapping) else {}


def _merge(base: dict[str, Any], override: Mapping[str, Any]) -> None:
    """Deep-merge ``override`` into ``base``; nested mappings merge recursively."""
    for key, value in override.items():
        current = base.get(key)
        if isinstance(value, Mapping) and isinstance(current, dict):
            _merge(current, value)
        else:
            base[key] = deepcopy(value)
