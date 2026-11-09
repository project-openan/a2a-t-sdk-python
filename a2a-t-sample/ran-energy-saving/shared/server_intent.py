"""Extract the structured Task payload a downstream returns when it accepts a Task.

The A2A Task state is the machine-readable submission signal (:data:`shared.run_constants.
TASK_STATE_SUBMITTED`): the localized copy of that status must never decide whether the Task was
created. The copy *does* carry data, though: the vendor NMS and the simulator append their own
structured understanding of the Task-T right after it, e.g. ``Task creation succeeded{"Operation
type":"Create",...}``. This module reads that payload as evidence only; callers must already know
the state is ``TASK_STATE_SUBMITTED``.
"""

from __future__ import annotations

import json
from typing import Final

#: Key every Task payload carries. It scopes the scan: an unrelated JSON object quoted in the
#: status copy (an error body, a diagnostic) is not the downstream's Task understanding.
OPERATION_KEY: Final[str] = "Operation type"

_DECODER: Final[json.JSONDecoder] = json.JSONDecoder()


def parse_server_intent(text: object) -> dict[str, object] | None:
    """Return the structured Task payload embedded in one submission status message.

    The localized prose before the payload differs per language and per operation (create or
    modify), so the payload is located by scanning for a decodable JSON object instead of matching
    the copy. A value that is not text, contains no such object, or decodes to a document without
    :data:`OPERATION_KEY` yields ``None``.

    Args:
        text: status message text of a ``TASK_STATE_SUBMITTED`` event.

    Returns:
        The decoded payload, or ``None`` when the message carries none.
    """
    if not isinstance(text, str):
        return None
    start = text.find("{")
    while start >= 0:
        try:
            payload, _end = _DECODER.raw_decode(text, start)
        except ValueError:
            start = text.find("{", start + 1)
            continue
        if isinstance(payload, dict) and OPERATION_KEY in payload:
            return {str(key): value for key, value in payload.items()}
        start = text.find("{", start + 1)
    return None


__all__ = ["OPERATION_KEY", "parse_server_intent"]
