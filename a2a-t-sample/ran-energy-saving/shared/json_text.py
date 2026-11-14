"""Extraction of JSON objects from model output that is not *only* JSON.

Some OpenAI-compatible gateways ignore ``response_format`` (or a model wraps its answer in a
markdown fence), so a contract-compliant object can arrive as
``"```json\\n{...}\\n```"`` or surrounded by prose. The SDK and the ADK decision agent both need
the object itself; these helpers scan for the first balanced object instead of parsing the whole
string. Nothing here accepts invalid JSON: a candidate still has to decode.
"""

from __future__ import annotations

import json
from collections.abc import Iterator


def iter_json_objects(text: str) -> Iterator[tuple[dict[str, object], str]]:
    """Yield ``(object, raw_text)`` for every JSON object embedded in ``text``, in reading order.

    The scanner decodes from each ``{`` and resumes after a successful object, so a stray brace in
    prose costs one attempt rather than the whole response.
    """
    decoder = json.JSONDecoder()
    index = text.find("{")
    while index != -1:
        try:
            value, end = decoder.raw_decode(text, index)
        except ValueError:
            index = text.find("{", index + 1)
            continue
        if isinstance(value, dict):
            yield value, text[index:end]
        index = text.find("{", end)


def first_json_object_text(text: str) -> str:
    """Return the first embedded JSON object as text, or the stripped input when there is none."""
    for _value, raw in iter_json_objects(text):
        return raw
    return text.strip()
