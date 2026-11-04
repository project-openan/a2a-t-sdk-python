"""Task-T / plain-text intent parsing and business validation of the simulator.

The parser accepts the sample's rendered Task-T (English headings, with optional bilingual
parentheses) and the vendor's Chinese L0 template. It validates the same business constraints the
910 test environment enforces: the test region, the activation window, a time-segmented throughput
target and the cell's maximum saving rate.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final

from a2a_t.core.metadata import NEGOTIATION_T_EXTENSION_URI, TASK_T_EXTENSION_URI

from demo.nms_sim.config import SimConfig

#: Header names the vendor accepts (A2A 1.0 and the older Java SDK spelling).
EXTENSION_HEADERS: Final[tuple[str, ...]] = ("a2a-extensions", "x-a2a-extensions")

#: Exact 422 body of the vendor when the header and the metadata do not pair.
MISSING_EXTENSION_METADATA: Final[dict[str, object]] = {
    "error": {
        "code": 422,
        "status": "INVALID_ARGUMENT",
        "message": ("Field 'message.metadata' must contain a -T extension key declared in 'A2A-Extensions' header"),
    }
}

_WINDOW_PATTERN: Final[re.Pattern[str]] = re.compile(r"(\d{1,2}:\d{2})\s*[~\-–]\s*(\d{1,2}:\d{2})")
_THROUGHPUT_PATTERN: Final[re.Pattern[str]] = re.compile(r"(\d+(?:\.\d+)?)\s*Mbps", re.IGNORECASE)
_PERCENT_PATTERN: Final[re.Pattern[str]] = re.compile(r"(\d+(?:\.\d+)?)\s*%")
_REGION_PREFIXES: Final[tuple[str, ...]] = (
    "the area is",
    "region:",
    "区域：",
    "区域:",
)


@dataclass(frozen=True)
class ParsedIntent:
    """Validated business intent of one A2A message.

    Attributes:
        operation: ``Create`` or ``Modify``.
        region: Region the NMS will act on.
        window: Activation window in ``HH:MM~HH:MM`` form.
        rate_mbps: First guaranteed throughput value, in Mbps.
        source: The Task-T prompt or the message text the values came from.
    """

    operation: str
    region: str
    window: str
    rate_mbps: float
    source: str


class SimRequestError(Exception):
    """A request the simulator rejects with a JSON HTTP error."""

    def __init__(self, status_code: int, payload: dict[str, object]) -> None:
        self.status_code = status_code
        self.payload = payload
        super().__init__(str(payload))


class SimNegotiationRequiredError(Exception):
    """A request the simulator answers with an information-negotation message."""

    def __init__(self, text: str) -> None:
        self.text = text
        super().__init__(text)


def parse_message_request(
    body: Mapping[str, object],
    headers: Mapping[str, str],
    config: SimConfig,
) -> ParsedIntent:
    """Parse and validate one ``message:stream`` request.

    Args:
        body: Parsed JSON request body.
        headers: Request headers (case-insensitive keys are expected; see :func:`extension_header`).
        config: simulator business constraints.

    Raises:
        SimRequestError: on a malformed body or the extension-header/metadata mismatch.
        SimNegotiationRequiredError: when the business content is incomplete or not served.
    """
    message = body.get("message")
    if not isinstance(message, Mapping):
        raise SimRequestError(400, {"error": {"code": 400, "message": "message is required"}})
    metadata = message.get("metadata")
    metadata_map = metadata if isinstance(metadata, Mapping) else {}
    extension_header = extension_header_value(headers)
    declared = {item.strip() for item in extension_header.split(",") if item.strip()}
    metadata_keys = {str(key) for key in metadata_map}
    if extension_header and not (declared & metadata_keys):
        # The vendor requires at least one declared ``-T`` key, not specifically Task-T: an
        # accept carries Negotiation-T only.
        raise SimRequestError(422, dict(MISSING_EXTENSION_METADATA))
    prompt = metadata_map.get(TASK_T_EXTENSION_URI)
    prompt_text = prompt if isinstance(prompt, str) else ""
    negotiation = metadata_map.get(NEGOTIATION_T_EXTENSION_URI)
    negotiation_text = negotiation if isinstance(negotiation, str) else ""
    text = _first_text(message)
    # An accept carries only Negotiation-T; its refilled values live in that metadata body.
    source = prompt_text or negotiation_text or text
    if not source.strip():
        raise SimNegotiationRequiredError(_negotiation_text(["Task content"]))

    operation = _operation(source)
    region = _region(source)
    windows = _windows(source)
    rate = _throughput(source)

    if not region:
        raise SimNegotiationRequiredError(
            _negotiation_text(
                ["Energy saving region"],
                "The energy-saving region is missing. Please specify the region explicitly.",
            )
        )
    if config.region.lower() not in region.lower():
        raise SimNegotiationRequiredError(
            _negotiation_text(
                ["Energy saving region"],
                f"The region '{region}' is not served. Available test region: {config.region}.",
            )
        )
    if not windows:
        raise SimNegotiationRequiredError(
            _negotiation_text(
                ["Energy saving time"],
                "The energy-saving activation time is missing. Please provide an explicit time "
                f"range such as {config.window}.",
            )
        )
    if config.window not in windows:
        raise SimNegotiationRequiredError(
            _negotiation_text(
                ["Energy saving time"],
                f"The activation time {', '.join(windows)} is not available. Available test window: {config.window}.",
            )
        )
    if rate is None:
        raise SimNegotiationRequiredError(
            _negotiation_text(
                ["Time-segmented guaranteed throughput target"],
                "missing explicit time-segmented rate goals",
            )
        )
    percent = _percent(source)
    if percent is not None and percent > config.max_saving_rate:
        raise SimNegotiationRequiredError(
            _negotiation_text(
                ["Energy saving rate"],
                f"The requested {percent:g}% exceeds the {config.max_saving_rate:g}% maximum.",
            )
        )
    return ParsedIntent(
        operation=operation,
        region=config.region,
        window=config.window,
        rate_mbps=rate,
        source=source,
    )


def extension_header_value(headers: Mapping[str, str]) -> str:
    """Return the first non-empty extension header of one request."""
    normalized = {key.lower(): value for key, value in headers.items()}
    for name in EXTENSION_HEADERS:
        value = normalized.get(name, "").strip()
        if value:
            return value
    return ""


def declares_negotiation(headers: Mapping[str, str]) -> bool:
    """Return whether the request declares the Negotiation-T extension.

    The vendor only returns structured negotiation metadata when the request declares it; the
    simulator reproduces that behavior so the root-cause path is exercised locally.
    """
    return NEGOTIATION_T_EXTENSION_URI in extension_header_value(headers)


def _first_text(message: Mapping[str, object]) -> str:
    """Return the first text part of one message."""
    parts = message.get("parts")
    if isinstance(parts, list) and parts:
        first = parts[0]
        if isinstance(first, Mapping) and isinstance(first.get("text"), str):
            return str(first["text"])
    return ""


def _section(text: str, *names: str) -> str:
    """Return the body of the first ``##`` heading containing one of ``names``."""
    lines = text.splitlines()
    for index, line in enumerate(lines):
        stripped = line.strip()
        if not stripped.startswith("##"):
            continue
        if not any(name.lower() in stripped.lower() for name in names):
            continue
        body: list[str] = []
        for following in lines[index + 1 :]:
            if following.strip().startswith("##"):
                break
            body.append(following)
        return "\n".join(body).strip()
    return ""


def _operation(text: str) -> str:
    """Return ``Create`` or ``Modify`` (default ``Create``)."""
    section = _section(text, "Operation Type", "操作类型")
    haystack = section or text
    return "Modify" if re.search(r"\bmodify\b|修改", haystack, re.IGNORECASE) else "Create"


def _region(text: str) -> str:
    """Extract the region name from the Task Object or a ``Region:`` line."""
    section = _section(text, "Task Object", "任务对象")
    candidate = section
    if not candidate:
        match = re.search(r"(?:区域|region)\s*[:：]\s*([^\n]+)", text, re.IGNORECASE)
        candidate = match.group(1) if match else ""
    if not candidate:
        match = re.search(r"\bA2ATestRegion\b", text)
        candidate = match.group(0) if match else ""
    line = next((item.strip() for item in candidate.splitlines() if item.strip()), "")
    lowered = line.lower()
    for prefix in _REGION_PREFIXES:
        if lowered.startswith(prefix):
            line = line[len(prefix) :].strip()
            break
    return line.strip(" .。\t")


def _windows(text: str) -> tuple[str, ...]:
    """Return every ``HH:MM~HH:MM`` window found in the text, canonicalized with ``~``."""
    return tuple(f"{_clock(start)}~{_clock(end)}" for start, end in _WINDOW_PATTERN.findall(text))


def _throughput(text: str) -> float | None:
    """Return the first guaranteed throughput value in Mbps."""
    match = _THROUGHPUT_PATTERN.search(text)
    return float(match.group(1)) if match else None


def _percent(text: str) -> float | None:
    """Return the first percentage value in the text, if any."""
    match = _PERCENT_PATTERN.search(text)
    return float(match.group(1)) if match else None


def _clock(value: str) -> str:
    """Normalize ``H:MM`` to ``HH:MM``."""
    hour, minute = value.split(":", 1)
    return f"{int(hour):02d}:{minute}"


def _negotiation_text(items: list[str], detail: str = "") -> str:
    """Render the vendor-style information-negotation message body."""
    lines = [
        "## Information Negotiation",
        "",
        "Please supplement the relevant content based on <Required Information Items>.",
        "",
        "## Required Information Items",
    ]
    lines.extend(f"{index}. {item}" for index, item in enumerate(items, 1))
    if detail:
        lines.extend(["", detail])
    return "\n".join(lines)
