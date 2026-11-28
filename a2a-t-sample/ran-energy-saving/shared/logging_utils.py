"""Small text helpers shared by the transcript and the logs."""

from __future__ import annotations


def summarize_text(text: str, *, max_length: int = 80) -> str:
    """Collapse whitespace and truncate one text to ``max_length`` characters."""
    normalized = " ".join(str(text).split())
    if len(normalized) <= max_length:
        return normalized
    return f"{normalized[: max_length - 3]}..."
