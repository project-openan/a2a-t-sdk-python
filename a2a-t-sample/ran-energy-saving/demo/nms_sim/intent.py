"""Task-T / plain-text intent parsing and business validation of the simulator.

The parser accepts the sample's rendered Task-T (English headings, with optional bilingual
parentheses) and the vendor's Chinese L0 template. It extracts the same business constraints the
910 test environment enforces (test region, activation window, time-segmented throughput target
and the cell's maximum saving rate); :mod:`demo.nms_sim.negotiation` decides how a violation is
reported to the client.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final

from a2a_t.core.metadata import NEGOTIATION_T_EXTENSION_URI, TASK_T_EXTENSION_URI
from shared import energy_rules

from demo.nms_sim.config import (
    RATE_POLICY_OPTIONAL,
    WINDOW_POLICY_ANY,
    WINDOW_POLICY_EXACT,
    SimConfig,
)
from demo.nms_sim.negotiation import (
    KIND_FEASIBILITY,
    KIND_INFORMATION,
    KIND_TARGET,
    NegotiationIssue,
)

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

#: Throughput assumed when an accept carries no new rate value.
DEFAULT_ACCEPT_RATE_MBPS: Final[float] = 30.0

#: Activation window assumed when the Task-T omits the optional energy-saving period.
FULL_DAY_WINDOW: Final[str] = "00:00~24:00"

_WINDOW_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"(\d{1,2}:\d{2})(?::\d{2})?\s*(?:[~\-–]|to|until|至)\s*(\d{1,2}:\d{2})(?::\d{2})?",
    re.IGNORECASE,
)
#: The Task-T rendering ``start time "00:00:00", end time "22:00:00"`` has no direct separator
#: between the two clocks, so the pair above cannot see it.
_START_END_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"start time\s*\"?(\d{1,2}:\d{2})(?::\d{2})?\"?\s*,\s*"
    r"end time\s*\"?(\d{1,2}:\d{2})(?::\d{2})?\"?",
    re.IGNORECASE,
)
_THROUGHPUT_PATTERN: Final[re.Pattern[str]] = re.compile(r"(\d+(?:\.\d+)?)\s*Mbps", re.IGNORECASE)
_PERCENT_PATTERN: Final[re.Pattern[str]] = re.compile(r"(\d+(?:\.\d+)?)\s*%")
#: A saving rate can be written without ``%`` ("energy saving rate: 40").
_SAVING_RATE_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"(?:energy[-\s]*saving\s*rate|节电率|节能率)[^\d]{0,20}(\d+(?:\.\d+)?)",
    re.IGNORECASE,
)
_REGION_PREFIXES: Final[tuple[str, ...]] = (
    "the area is",
    "the region is",
    "region:",
    "区域：",
    "区域:",
)
#: Plain-text answers that name the region: "The region is X.", "region: X", "the area is X".
_REGION_IS_PATTERN: Final[re.Pattern[str]] = re.compile(
    # ``\b`` keeps "region:" from matching inside the mixed-case region id "A2ATestRegion:".
    r"(?:the\s+)?(?:energy[-\s]*saving\s+)?\b(?:region|area|zone)(?:\s+name)?\s*(?:is|=|:)\s*"
    r"([^\n,;.。；]+)",
    re.IGNORECASE,
)
#: "in the A2ATestRegion area" / "in A2ATestRegion region".
_REGION_IN_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"\bin\s+(?:the\s+)?([A-Za-z0-9_\-]+)\s+(?:region|area|zone)\b",
    re.IGNORECASE,
)
#: "an energy saving task for TestRegion with ...".
_REGION_FOR_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"\bfor\s+(?:the\s+)?([A-Za-z][A-Za-z0-9_\-]*)\s+(?:with|region|area)\b",
    re.IGNORECASE,
)
#: "Create an energy saving task for A2ATestRegion: ..." -- the prose baseline the tester seeds
#: and the vendor accepts. The colon keeps this from matching "for each time period".
_REGION_FOR_COLON_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"\bfor\s+(?:the\s+)?([A-Za-z][A-Za-z0-9_\-]*)\s*:",
    re.IGNORECASE,
)
#: Words that describe a region instead of naming it; removed before equality comparison.
_REGION_GENERIC_WORDS: Final[frozenset[str]] = frozenset(
    {"the", "area", "region", "zone", "of", "in", "at"}
)


@dataclass(frozen=True)
class ExtractedContent:
    """Business values read from one Task-T prompt or negotiation reply.

    Attributes:
        operation: ``Create``/``Modify`` when the text names one, else ``None``.
        region: Region the NMS will act on (empty when absent).
        windows: Activation windows found in the text, canonicalized with ``~``.
        rate_mbps: First guaranteed throughput value in Mbps, when present.
        percent: First percentage value in the text, when present.
        source: The text the values came from.
    """

    operation: str | None
    region: str
    windows: tuple[str, ...]
    rate_mbps: float | None
    percent: float | None
    source: str

    def merged_with(self, baseline: ExtractedContent | None) -> ExtractedContent:
        """Return this reply's values over the original request's values.

        A negotiation reply often answers only the asked item (an information accept names the
        missing region and nothing else), so the original request supplies the untouched fields.
        """
        if baseline is None:
            return self
        return ExtractedContent(
            operation=self.operation or baseline.operation,
            region=self.region or baseline.region,
            windows=self.windows or baseline.windows,
            rate_mbps=self.rate_mbps if self.rate_mbps is not None else baseline.rate_mbps,
            percent=self.percent if self.percent is not None else baseline.percent,
            source=_join_sources(baseline.source, self.source),
        )


@dataclass(frozen=True)
class ParsedIntent:
    """Validated business intent of one A2A message.

    Attributes:
        operation: ``Create`` or ``Modify``.
        region: Region the NMS will act on.
        window: Activation window in ``HH:MM~HH:MM`` form.
        rate_mbps: First guaranteed throughput value, in Mbps.
        sites: Site names shown in the report text only.
        source: The Task-T prompt or the message text the values came from.
    """

    operation: str
    region: str
    window: str
    rate_mbps: float
    sites: tuple[str, ...]
    source: str


class SimRequestError(Exception):
    """A request the simulator rejects with a JSON HTTP error."""

    def __init__(self, status_code: int, payload: dict[str, object]) -> None:
        self.status_code = status_code
        self.payload = payload
        super().__init__(str(payload))


class SimNegotiationRequiredError(Exception):
    """A request the simulator answers with a Negotiation-T proposal."""

    def __init__(self, issue: NegotiationIssue) -> None:
        self.issue = issue
        super().__init__(issue.body())

    @property
    def text(self) -> str:
        """Return the rendered Negotiation-T body."""
        return self.issue.body()


def read_message_request(
    body: Mapping[str, object],
    headers: Mapping[str, str],
) -> ExtractedContent:
    """Validate the request envelope and read the business content without judging it.

    Raises:
        SimRequestError: on a malformed body or the extension-header/metadata mismatch.
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
    return extract_content(source)


def extract_content(source: str) -> ExtractedContent:
    """Read the business values of one Task-T prompt or negotiation reply body."""
    return ExtractedContent(
        operation=_operation(source),
        region=_region(source),
        windows=_windows(source),
        rate_mbps=_throughput(source),
        percent=_percent(source),
        source=source,
    )


def validate_content(content: ExtractedContent, config: SimConfig) -> NegotiationIssue | None:
    """Return the first business violation of one request, or ``None`` when it is acceptable.

    The order is the vendor's own: missing/unknown data first (information), then an unavailable
    window (feasibility), then an unattainable goal (target). One kind per call, so one proposal
    per round.
    """
    if not content.source.strip() and not content.region and not content.windows and content.rate_mbps is None:
        return NegotiationIssue(KIND_INFORMATION, ("Task content",), "")
    if not content.region:
        return NegotiationIssue(
            KIND_INFORMATION,
            ("Energy saving region",),
            "The energy-saving region is missing. Please specify the region explicitly.",
        )
    if normalize_region(content.region) != normalize_region(config.region):
        # The vendor's region check reports only that the name is not recognized and never names
        # a candidate: its region_check.py deliberately drops the best match so the proposal
        # cannot read as a recommendation. The simulator must not hand the client a hint the real
        # network never gives.
        return NegotiationIssue(
            KIND_INFORMATION,
            ("Energy saving region",),
            f'The region name "{content.region}" is not recognized. '
            "Please provide the correct region name.",
        )
    if not content.windows and config.window_policy != WINDOW_POLICY_ANY:
        # The Task-T template makes the activation period optional (full-period default). Under
        # the vendor-like ``any`` policy an omitted period is accepted as the full day instead of
        # opening an information round the client cannot answer.
        return NegotiationIssue(
            KIND_INFORMATION,
            ("Energy saving time",),
            "The energy-saving activation time is missing. Please provide an explicit time "
            f"range such as {config.window}.",
        )
    if config.window_policy == WINDOW_POLICY_EXACT and config.window not in content.windows:
        return NegotiationIssue(
            KIND_FEASIBILITY,
            ("Energy saving time",),
            f"The activation time {', '.join(content.windows)} is not available. "
            f"Available test window: {config.window}.",
        )
    if content.rate_mbps is None and config.rate_policy != RATE_POLICY_OPTIONAL:
        return NegotiationIssue(
            KIND_INFORMATION,
            ("Time-segmented guaranteed throughput target",),
            "missing explicit time-segmented rate goals",
        )
    if config.long_outage_feasibility:
        # The vendor's rule is strict on both sides: the outage must exceed four hours AND a
        # guaranteed rate must exceed 2 Mbps. Measuring the outage envelope (not its distinct
        # hours) keeps a gapped outage as long as the vendor sees it.
        outage_hours = energy_rules.outage_duration_hours(content.source)
        rates = [float(value) for value in _THROUGHPUT_PATTERN.findall(content.source)]
        if energy_rules.is_outage_infeasible(outage_hours, rates):
            return NegotiationIssue(
                KIND_FEASIBILITY,
                ("Guaranteed throughput target",),
                f"The {outage_hours:g}-hour power outage with a rate above "
                f"{energy_rules.FEASIBILITY_RATE_MBPS:g}Mbps exceeds the "
                f"{energy_rules.FEASIBILITY_OUTAGE_HOURS}-hour feasibility limit. Reduce the "
                "guaranteed throughput target for the outage period.",
            )
    if content.percent is not None and content.percent > config.max_saving_rate:
        return NegotiationIssue(
            KIND_TARGET,
            ("Energy saving rate",),
            f"The requested {content.percent:g}% exceeds the {config.max_saving_rate:g}% maximum.",
        )
    return None


def select_issue(content: ExtractedContent, config: SimConfig) -> NegotiationIssue | None:
    """Return the issue to propose for one request, honouring the configured kinds.

    Missing information is never suppressed: without the region/window/rate the request cannot be
    executed at all, so a disabled kind falls back to ``information`` instead of silently
    accepting an invalid request.
    """
    issue = validate_content(content, config)
    if issue is None or issue.kind in config.negotiation_kinds or issue.kind == KIND_INFORMATION:
        return issue
    return NegotiationIssue(KIND_INFORMATION, issue.items, issue.detail)


def accepted_intent(
    content: ExtractedContent,
    baseline: ExtractedContent | None,
    config: SimConfig,
) -> ParsedIntent:
    """Build the intent of an accepted negotiation.

    An accept completes the request even when its terse body names only the answered item, so
    untouched fields come from the original request and, failing that, from the served defaults.
    """
    merged = content.merged_with(baseline)
    return ParsedIntent(
        operation=merged.operation or "Create",
        # Echo what the request asked for; the report must not claim a different region or window.
        region=merged.region or config.region,
        window=(
            merged.windows[0]
            if merged.windows
            else FULL_DAY_WINDOW
            if config.window_policy == WINDOW_POLICY_ANY
            else config.window
        ),
        rate_mbps=merged.rate_mbps if merged.rate_mbps is not None else DEFAULT_ACCEPT_RATE_MBPS,
        sites=config.sites,
        source=merged.source,
    )


def parse_message_request(
    body: Mapping[str, object],
    headers: Mapping[str, str],
    config: SimConfig,
) -> ParsedIntent:
    """Parse and validate one request that is expected to be executable.

    Raises:
        SimRequestError: on a malformed body or the extension-header/metadata mismatch.
        SimNegotiationRequiredError: when the business content is incomplete or not served.
    """
    content = read_message_request(body, headers)
    issue = select_issue(content, config)
    if issue is not None:
        raise SimNegotiationRequiredError(issue)
    return ParsedIntent(
        operation=content.operation or "Create",
        # Echo the requested values instead of the configured defaults, so the report and the
        # acceptance evidence describe the plan the operator actually asked for.
        region=content.region or config.region,
        window=(
            content.windows[0]
            if content.windows
            else FULL_DAY_WINDOW
            if config.window_policy == WINDOW_POLICY_ANY
            else config.window
        ),
        rate_mbps=content.rate_mbps if content.rate_mbps is not None else DEFAULT_ACCEPT_RATE_MBPS,
        sites=config.sites,
        source=content.source,
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


def message_text(message: Mapping[str, object]) -> str:
    """Return the first text part of one message (public helper for the pure path)."""
    return _first_text(message)


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


def _operation(text: str) -> str | None:
    """Return ``Create``/``Modify`` when the text names one, else ``None``."""
    section = _section(text, "Operation Type", "操作类型")
    haystack = section or text
    if re.search(r"\bmodify\b|修改", haystack, re.IGNORECASE):
        return "Modify"
    if re.search(r"\bcreate\b|创建", haystack, re.IGNORECASE):
        return "Create"
    return None


def normalize_region(value: str) -> str:
    """Normalize one region name for equality comparison.

    Case, punctuation and descriptive words are removed, so ``"A2ATestRegion area"`` equals
    ``"A2ATestRegion"`` while ``"A2ATestRegion2"`` does not. Substring matching would accept the
    latter, which is exactly the region mistake the vendor rejects.
    """
    text = unicodedata.normalize("NFKC", value).casefold()
    text = re.sub(r"[^\w]+", " ", text)
    return " ".join(
        word for word in text.split() if word and word not in _REGION_GENERIC_WORDS
    )


def _region(text: str) -> str:
    """Return the region the text names, preferring the latest explicit answer.

    A pure-A2A conversation accumulates the original request and every follow-up, so the last
    explicit answer wins. A Task-T prompt still reads its ``## Task Object`` section first; plain
    requests fall back to the vendor phrasings ("The region is X.", "in X area", "for X with") and
    finally to the configured region literal.
    """
    section = _section(text, "Task Object", "任务对象")
    if section:
        line = next((item.strip() for item in section.splitlines() if item.strip()), "")
        return _strip_region_line(line)
    for pattern in (
        _REGION_IS_PATTERN,
        _REGION_IN_PATTERN,
        _REGION_FOR_PATTERN,
        _REGION_FOR_COLON_PATTERN,
    ):
        matches = pattern.findall(text)
        if matches:
            return _strip_region_line(matches[-1])
    match = re.search(r"\bA2ATestRegion\b", text)
    return _strip_region_line(match.group(0)) if match else ""


def _strip_region_line(line: str) -> str:
    """Strip a leading region prefix and trailing sentence punctuation."""
    line = line.strip()
    lowered = line.lower()
    for prefix in _REGION_PREFIXES:
        if lowered.startswith(prefix):
            line = line[len(prefix) :].strip()
            break
    return line.strip(" .。\t")


def _windows(text: str) -> tuple[str, ...]:
    """Return every ``HH:MM~HH:MM`` window found in the text, canonicalized with ``~``."""
    pairs = _WINDOW_PATTERN.findall(text) + _START_END_PATTERN.findall(text)
    return tuple(f"{_clock(start)}~{_clock(end)}" for start, end in pairs)


def _throughput(text: str) -> float | None:
    """Return the first guaranteed throughput value in Mbps."""
    match = _THROUGHPUT_PATTERN.search(text)
    return float(match.group(1)) if match else None


def _percent(text: str) -> float | None:
    """Return the first percentage/saving-rate value in the text, if any."""
    match = _PERCENT_PATTERN.search(text) or _SAVING_RATE_PATTERN.search(text)
    return float(match.group(1)) if match else None


def _clock(value: str) -> str:
    """Normalize ``H:MM`` to ``HH:MM``."""
    hour, minute = value.split(":", 1)
    return f"{int(hour):02d}:{minute}"


def _join_sources(baseline: str, reply: str) -> str:
    """Join the original request and one reply for the report text."""
    if not reply.strip():
        return baseline
    if not baseline.strip():
        return reply
    return f"{baseline}\n{reply}"
