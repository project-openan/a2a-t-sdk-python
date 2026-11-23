"""Case-level client decision instructions.

A case may carry ``client_conditions``: natural-language acceptance boundaries or client-known
facts that only apply to this case. The field is optional and lives next to ``user_input``; it is
never part of ``expect`` and never drives an assertion.
"""

from __future__ import annotations

from collections.abc import Mapping

#: Top-level case field carrying the optional case instructions.
CLIENT_CONDITIONS_FIELD = "client_conditions"


def case_client_conditions(case: Mapping[str, object]) -> str:
    """Return one case's client instructions, or an empty string when it has none.

    Raises:
        ValueError: when the field is present but not a string.
    """
    value = case.get(CLIENT_CONDITIONS_FIELD)
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ValueError(
            f"client_conditions must be a string, got {type(value).__name__}"
        )
    return value.strip()


__all__ = ["CLIENT_CONDITIONS_FIELD", "case_client_conditions"]
