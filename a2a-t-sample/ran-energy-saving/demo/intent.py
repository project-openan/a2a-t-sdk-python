"""Business intent understanding of the RAN energy-saving demonstration.

The sample keeps the operator-facing request short and lets this layer produce the canonical text
that the A2A client actually sends. Known scenarios have a deterministic built-in intent (which
preserves their deliberate gaps and conflicts); a free-text request uses the live model, and an
offline free-text request falls back to the raw text with a warning.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Mapping
from dataclasses import replace
from typing import Final

import httpx
from protocol.intent import (
    INTENT_SOURCE_MODEL,
    INTENT_SOURCE_SCENARIO,
    IntentProcessor,
    OperatorRequest,
    ProcessedIntent,
)
from protocol.run_spec import RunSpec
from shared.config import DISCOVERY_NMS, SampleConfig
from shared.run_constants import LANGUAGE_EN, LANGUAGE_ZH

from demo.scenario import (
    PRESENTATION_REQUESTS,
    SCENARIO_ABORT,
    SCENARIO_FEASIBILITY,
    SCENARIO_INFORMATION,
    SCENARIO_NIGHT,
    SCENARIO_OUTAGE,
    SCENARIO_REJECT,
    SCENARIO_TARGET,
    SCENARIO_UNKNOWN_AREA,
    SUPPORTED_SCENARIO_NAMES,
    complete_initial_params,
    follow_up_initial_params,
    get_scenario,
    resolve_modify_request,
)

_LOGGER = logging.getLogger("ran_energy_saving.intent")

#: Overlapping windows of the target/abort scenarios, kept in their original notation.
_TARGET_WINDOWS: Final[dict[str, str]] = {
    LANGUAGE_EN: (
        "{00:00~07:00,2Mbps}, {07:00~09:00,10Mbps}, {09:00~17:30,10Mbps}, {17:30~23:00,20Mbps}, {20:00~00:00,10Mbps}"
    ),
    LANGUAGE_ZH: (
        "{00:00~07:00,2Mbps}、{07:00~09:00,10Mbps}、{09:00~17:30,10Mbps}、{17:30~23:00,20Mbps}、{20:00~00:00,10Mbps}"
    ),
}

#: Feasibility scenario values: the outage and the two site names are part of the request.
_FEASIBILITY_WINDOWS: Final[dict[str, str]] = {
    LANGUAGE_EN: "{00:00~06:00,2Mbps}, {06:00~18:00,10Mbps}",
    LANGUAGE_ZH: "{00:00~06:00,2Mbps}、{06:00~18:00,10Mbps}",
}
_FEASIBILITY_SITES: Final[dict[str, tuple[str, ...]]] = {
    LANGUAGE_EN: ("Station 1", "Station 2"),
    LANGUAGE_ZH: ("站点1", "站点2"),
}
_FEASIBILITY_EVENT: Final[dict[str, str]] = {
    LANGUAGE_EN: "power outage from 00:00 to 18:00",
    LANGUAGE_ZH: "00:00~18:00 停电",
}

#: Scenario surface text used as the built-in raw operator request.
_RAW_OVERRIDES: Final[dict[str, dict[str, str]]] = {
    SCENARIO_NIGHT: {
        LANGUAGE_EN: "Start energy saving in Region A.",
        LANGUAGE_ZH: "在区域 A 开启节能。",
    },
}

#: Generic site tokens a raw request may use for the n-th site, per language. The presentation
#: copy says "Site 1" while the structured intent says "Station 1"; both must follow the profile.
_SITE_TOKENS: Final[dict[str, tuple[str, ...]]] = {
    LANGUAGE_EN: ("Station {n}", "station {n}", "Site {n}", "site {n}"),
    LANGUAGE_ZH: ("站点名称{n}", "站点 {n}", "站点{n}"),
}


def _localized(value: Mapping[str, str], language: str) -> str:
    """Return one localized value, falling back to Chinese."""
    return value.get(language, value[LANGUAGE_ZH])


def _split_windows(value: object) -> tuple[str, ...]:
    """Split one guaranteed-throughput string into its individual ``{...}`` window tokens."""
    if not isinstance(value, str) or not value.strip():
        return ()
    # A window itself contains a comma ("{HH:MM~HH:MM,NMbps}"), so split the braces rather than
    # every comma; the fallback keeps units that omit braces working.
    braced = re.findall(r"\{[^}]+\}", value)
    if braced:
        return tuple(part.strip() for part in braced)
    return tuple(part.strip() for part in re.split(r"[,、]", value) if part.strip())


def _split_sites(value: object) -> tuple[str, ...]:
    """Split one site-list string into its individual site names."""
    if not isinstance(value, str) or not value.strip():
        return ()
    return tuple(part.strip().strip('"') for part in re.split(r"[,、]", value) if part.strip())


def _base_params(language: str) -> dict[str, object]:
    """Return the complete base fields shared by every Create scenario."""
    return dict(complete_initial_params(SCENARIO_NIGHT, language) or {})


def build_scenario_intent(
    name: str,
    language: str = LANGUAGE_ZH,
    *,
    region: str = "",
    sites: tuple[str, ...] = (),
) -> ProcessedIntent:
    """Build the deterministic intent of one known scenario.

    The normalized text is the scenario's existing rich request, so the A2A-T extraction path and
    its offline fixtures stay stable. The raw text is the shorter request an operator would type.
    ``region``/``sites`` apply the vendor profile to every surface of the intent, including the raw
    request, so a real-NMS run never shows the neutral demo name next to a vendor Task-T.
    """
    scenario = get_scenario(name)
    params = _base_params(language)
    raw = PRESENTATION_REQUESTS.get(name, {}).get(language) or scenario.user_request[language]
    raw = _RAW_OVERRIDES.get(name, {}).get(language, raw)
    normalized = scenario.user_request[language]

    windows = _split_windows(params.get("guaranteedThroughputTarget"))
    sites = _split_sites(params.get("siteList"))
    background = _text_or_none(params.get("backgroundEvent"))
    missing: tuple[str, ...] = ()
    conflicts: tuple[str, ...] = ()

    if name in (SCENARIO_INFORMATION, SCENARIO_REJECT):
        # The throughput target is missing on purpose; the server must still open the information
        # negotiation. Do not repair it here.
        windows = ()
        missing = ("guaranteedThroughputTarget",)
    elif name in (SCENARIO_TARGET, SCENARIO_ABORT):
        windows = _split_windows(_localized(_TARGET_WINDOWS, language))
        conflicts = ("overlapping throughput windows",)
    elif name == SCENARIO_UNKNOWN_AREA:
        unknown = complete_initial_params(SCENARIO_UNKNOWN_AREA, language) or {}
        params.update(unknown)
        windows = _split_windows(unknown.get("guaranteedThroughputTarget"))
    elif name == SCENARIO_FEASIBILITY:
        windows = _split_windows(_localized(_FEASIBILITY_WINDOWS, language))
        sites = _FEASIBILITY_SITES.get(language, _FEASIBILITY_SITES[LANGUAGE_ZH])
        background = _localized(_FEASIBILITY_EVENT, language)
    elif name == SCENARIO_OUTAGE:
        # Baseline phase of the two-phase run: the planned outage is introduced by Modify later.
        outage = complete_initial_params(SCENARIO_OUTAGE, language) or {}
        windows = _split_windows(outage.get("guaranteedThroughputTarget"))
        sites = _split_sites(outage.get("siteList"))
        # The scenario's own rich request describes the Modify phase. The initial Task-T is the
        # baseline Create, so render its canonical text from the baseline fields instead.
        normalized = render_normalized_text(
            operation="Create",
            region=str(params.get("region") or ""),
            sites=sites,
            windows=windows,
            goal=str(params.get("energySavingGoal") or ""),
            description=str(params.get("task_description") or ""),
            expected=str(params.get("expectedOutput") or ""),
            background=None,
            language=language,
        )

    intent = ProcessedIntent(
        raw_request=raw,
        normalized_text=normalized,
        operation_type=str(params.get("operationType") or ""),
        region=str(params.get("region") or ""),
        sites=sites,
        windows=windows,
        energy_saving_goal=str(params.get("energySavingGoal") or ""),
        task_description=str(params.get("task_description") or ""),
        expected_output=str(params.get("expectedOutput") or ""),
        background_event=background,
        missing_fields=missing,
        conflicts=conflicts,
        source=INTENT_SOURCE_SCENARIO,
        plain_text=render_plain_text(
            operation=str(params.get("operationType") or ""),
            region=str(params.get("region") or ""),
            sites=sites,
            windows=windows,
            goal=str(params.get("energySavingGoal") or ""),
            expected=str(params.get("expectedOutput") or ""),
            background=background,
            language=language,
        ),
    )
    return _with_forced_context(intent, region, sites, language)


def build_modify_intent(
    language: str = LANGUAGE_ZH,
    *,
    region: str = "",
    sites: tuple[str, ...] = (),
) -> ProcessedIntent:
    """Build the deterministic intent of a Modify-only run against an existing Task.

    The target Task already exists on the NMS, so the request has no Create phase. The business
    values are the planned-outage Modify values of the ``outage`` scenario.
    """
    params = follow_up_initial_params(SCENARIO_OUTAGE, language) or {}
    windows = _split_windows(params.get("guaranteedThroughputTarget"))
    resolved_sites = _split_sites(params.get("siteList"))
    background = _text_or_none(params.get("backgroundEvent"))
    normalized = render_normalized_text(
        operation="Modify",
        region=str(params.get("region") or ""),
        sites=resolved_sites,
        windows=windows,
        goal=str(params.get("energySavingGoal") or ""),
        description=str(params.get("task_description") or ""),
        expected=str(params.get("expectedOutput") or ""),
        background=background,
        language=language,
    )
    intent = ProcessedIntent(
        raw_request=resolve_modify_request(language),
        normalized_text=normalized,
        plain_text=resolve_modify_request(language),
        operation_type="Modify",
        region=str(params.get("region") or ""),
        sites=resolved_sites,
        windows=windows,
        energy_saving_goal=str(params.get("energySavingGoal") or ""),
        task_description=str(params.get("task_description") or ""),
        expected_output=str(params.get("expectedOutput") or ""),
        background_event=background,
        source=INTENT_SOURCE_SCENARIO,
    )
    return _with_forced_context(intent, region, sites, language)


def build_all_scenario_intents(
    language: str = LANGUAGE_ZH,
    *,
    region: str = "",
    sites: tuple[str, ...] = (),
) -> dict[str, ProcessedIntent]:
    """Return the built-in intent of every scenario, keyed by scenario name."""
    return {
        name: build_scenario_intent(name, language, region=region, sites=sites) for name in SUPPORTED_SCENARIO_NAMES
    }


def _text_or_none(value: object) -> str | None:
    """Return a non-empty string, ``None`` otherwise."""
    return value if isinstance(value, str) and value.strip() else None


def apply_region_profile_text(
    text: str,
    *,
    old_region: str,
    region: str,
    old_sites: tuple[str, ...] = (),
    sites: tuple[str, ...] = (),
    language: str = LANGUAGE_EN,
) -> str:
    """Replace the neutral demo region/site names with the configured vendor profile.

    The raw request and the normalized text must change together. Otherwise the UI shows the
    operator asking for ``Region A`` while the Task-T on the wire names ``A2ATestRegion``.
    """
    if not text:
        return text
    updated = text
    if old_region and region and old_region != region:
        updated = updated.replace(old_region, region)
    if old_sites and sites and old_sites != sites:
        tokens = _SITE_TOKENS.get(language, _SITE_TOKENS[LANGUAGE_EN])
        for index, (old_site, new_site) in enumerate(zip(old_sites, sites), start=1):
            if old_site and old_site != new_site:
                updated = updated.replace(old_site, new_site)
            for token in sorted(tokens, key=len, reverse=True):
                updated = updated.replace(token.format(n=index), new_site)
    return updated


def _with_forced_context(
    intent: ProcessedIntent,
    region: str,
    sites: tuple[str, ...],
    language: str = LANGUAGE_EN,
) -> ProcessedIntent:
    """Apply the configured NMS region and site list to one intent.

    The vendor serves exactly one region and a fixed set of sites; the neutral demo names must
    never reach it. A scenario that carries no site list stays region-level and gains none. The
    raw request is rewritten too: an NMS run presents one coherent request, not a neutral operator
    sentence next to a vendor Task-T.
    """
    new_region = region or intent.region
    new_sites = sites if sites and intent.sites else intent.sites
    if new_region == intent.region and new_sites == intent.sites:
        return intent
    raw = apply_region_profile_text(
        intent.raw_request,
        old_region=intent.region,
        region=new_region,
        old_sites=intent.sites,
        sites=new_sites,
        language=language,
    )
    normalized = apply_region_profile_text(
        intent.normalized_text,
        old_region=intent.region,
        region=new_region,
        old_sites=intent.sites,
        sites=new_sites,
        language=language,
    )
    plain = apply_region_profile_text(
        intent.plain_text,
        old_region=intent.region,
        region=new_region,
        old_sites=intent.sites,
        sites=new_sites,
        language=language,
    )
    missing = (
        tuple(item for item in intent.missing_fields if item != "region")
        if region and region != intent.region
        else intent.missing_fields
    )
    return replace(
        intent,
        raw_request=raw,
        region=new_region,
        sites=new_sites,
        normalized_text=normalized,
        plain_text=plain,
        missing_fields=missing,
    )


class DemoIntentProcessor:
    """Sample intent processor: built-in scenarios plus a live-model fallback for free text."""

    def __init__(
        self,
        *,
        language: str,
        built_in: Mapping[str, ProcessedIntent] | None = None,
        extractor: DemoIntentExtractor | None = None,
        forced_region: str = "",
        forced_sites: tuple[str, ...] = (),
    ) -> None:
        """Create one processor.

        Args:
            language: process-level language.
            built_in: scenario intents keyed by scenario name.
            extractor: live-model extractor; ``None`` in offline mode.
            forced_region: NMS region applied to the normalized intent.
            forced_sites: NMS sites applied to the normalized intent's site list.
        """
        self._language = language
        # Keep both surfaces: the CLI can send the neutral scenario request, while the Web UI of a
        # vendor run sends the profiled one. Matching either must find the same built-in intent.
        self._built_in: dict[str, tuple[ProcessedIntent, ProcessedIntent]] = {
            name: (intent, _with_forced_context(intent, forced_region, forced_sites, language))
            for name, intent in (built_in or build_all_scenario_intents(language)).items()
        }
        self._extractor = extractor
        self._forced_region = forced_region
        self._forced_sites = forced_sites

    async def process(self, request: OperatorRequest) -> ProcessedIntent:
        """Understand one operator request."""
        built_in = self._matching_built_in(request)
        if built_in is not None:
            return _with_forced_context(built_in, self._forced_region, self._forced_sites, self._language)
        if self._extractor is None:
            return ProcessedIntent.raw(
                request.text,
                reason="No live model is configured for free-text intent extraction.",
            )
        try:
            intent = await self._extractor.extract(request)
        except Exception as exc:  # noqa: BLE001 - the intent layer must not break the run
            _LOGGER.warning("intent extraction failed; falling back to raw text: %s", exc)
            return ProcessedIntent.raw(
                request.text,
                reason=f"Intent extraction failed: {type(exc).__name__}: {exc}",
            )
        return _with_forced_context(intent, self._forced_region, self._forced_sites, self._language)

    def _matching_built_in(self, request: OperatorRequest) -> ProcessedIntent | None:
        """Return the built-in intent when the request matches a canned scenario request."""
        if request.custom:
            return None
        text = request.text.strip()
        candidates = (
            [self._built_in[request.scenario]]
            if request.scenario and request.scenario in self._built_in
            else list(self._built_in.values())
        )
        for neutral, profiled in candidates:
            surfaces = (
                neutral.raw_request.strip(),
                neutral.normalized_text.strip(),
                profiled.raw_request.strip(),
                profiled.normalized_text.strip(),
            )
            if text and text in surfaces:
                return profiled
        return None


class DemoIntentExtractor:
    """Live-model extraction of a free-text operator request into the business fields."""

    def __init__(self, config: SampleConfig) -> None:
        """Create one extractor from the live model configuration."""
        self._api_base = config.adk_model_api_base.rstrip("/")
        self._model = config.adk_model_name.removeprefix("openai/")
        self._api_key = config.adk_model_api_key
        self._timeout = config.llm_timeout_seconds

    async def extract(self, request: OperatorRequest) -> ProcessedIntent:
        """Call the OpenAI-compatible endpoint and parse its JSON answer."""
        payload = {
            "model": self._model,
            "temperature": 0,
            "messages": [
                {"role": "system", "content": _EXTRACTION_SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": (
                        f"Language: {request.language}\n"
                        f"Scenario: {request.scenario or '-'}\n"
                        f"Operator request:\n{request.text}"
                    ),
                },
            ],
        }
        headers = {"content-type": "application/json"}
        if self._api_key:
            headers["authorization"] = f"Bearer {self._api_key}"
        async with httpx.AsyncClient(timeout=self._timeout, trust_env=False) as client:
            response = await client.post(
                f"{self._api_base}/chat/completions",
                json=payload,
                headers=headers,
            )
            response.raise_for_status()
            body = response.json()
        content = _message_content(body)
        fields = _parse_json_object(content)
        return _intent_from_fields(request.text, fields, request.language)


def build_demo_intent_processor(
    config: SampleConfig,
    run_spec: RunSpec | None = None,
) -> IntentProcessor:
    """Build the intent processor of one demo run, or of the OSS Agent when ``run_spec`` is None."""
    extractor = None if config.is_offline else DemoIntentExtractor(config)
    forced_region = config.nms_region if config.discovery == DISCOVERY_NMS else ""
    forced_sites = config.nms_sites if config.discovery == DISCOVERY_NMS else ()
    built_in = build_all_scenario_intents(config.language)
    if run_spec is not None and run_spec.intent is not None:
        built_in[run_spec.name] = run_spec.intent
    return DemoIntentProcessor(
        language=config.language,
        built_in=built_in,
        extractor=extractor,
        forced_region=forced_region,
        forced_sites=forced_sites,
    )


_EXTRACTION_SYSTEM_PROMPT: Final[str] = """\
You extract a RAN energy-saving business intent from an operator request.
Return one JSON object with exactly these keys:
{
  "operation_type": "Create or Modify",
  "region": "region name or empty string",
  "sites": ["site names"],
  "windows": ["guaranteed throughput windows in {HH:MM~HH:MM,NMbps} form"],
  "energy_saving_goal": "goal or empty string",
  "task_description": "what the task should do",
  "expected_output": "expected output",
  "background_event": "optional event or null",
  "missing_fields": ["fields the request does not provide"],
  "conflicts": ["conflicts you detect, for example overlapping windows"]
}
Do not invent missing values and do not repair conflicts. Keep the operator's region even when it
looks unknown. Return JSON only, without prose or code fences.
"""


def _message_content(body: object) -> str:
    """Return the assistant content of one OpenAI-compatible response."""
    if not isinstance(body, Mapping):
        raise ValueError("The model response is not a JSON object")
    choices = body.get("choices")
    if not isinstance(choices, list) or not choices:
        raise ValueError("The model response has no choices")
    first = choices[0]
    if not isinstance(first, Mapping):
        raise ValueError("The model choice is not an object")
    message = first.get("message")
    if not isinstance(message, Mapping):
        raise ValueError("The model choice has no message")
    content = message.get("content")
    if not isinstance(content, str) or not content.strip():
        raise ValueError("The model message has no text content")
    return content


def _parse_json_object(content: str) -> dict[str, object]:
    """Parse a JSON object, tolerating a fenced code block around it."""
    text = content.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z0-9_-]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Intent extraction returned invalid JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise ValueError("Intent extraction did not return a JSON object")
    return {str(key): value for key, value in parsed.items()}


def _intent_from_fields(raw: str, fields: Mapping[str, object], language: str) -> ProcessedIntent:
    """Build a ProcessedIntent from a model JSON object."""
    operation = _string(fields.get("operation_type")) or "Create"
    region = _string(fields.get("region"))
    sites = _string_list(fields.get("sites"))
    windows = _string_list(fields.get("windows"))
    goal = _string(fields.get("energy_saving_goal"))
    description = _string(fields.get("task_description"))
    expected = _string(fields.get("expected_output"))
    background = _string(fields.get("background_event")) or None
    missing = _string_list(fields.get("missing_fields"))
    conflicts = _string_list(fields.get("conflicts"))
    normalized = render_normalized_text(
        operation=operation,
        region=region,
        sites=sites,
        windows=windows,
        goal=goal,
        description=description,
        expected=expected,
        background=background,
        language=language,
    )
    plain = render_plain_text(
        operation=operation,
        region=region,
        sites=sites,
        windows=windows,
        goal=goal,
        expected=expected,
        background=background,
        language=language,
    )
    return ProcessedIntent(
        raw_request=raw,
        normalized_text=normalized,
        plain_text=plain,
        operation_type=operation,
        region=region,
        sites=sites,
        windows=windows,
        energy_saving_goal=goal,
        task_description=description,
        expected_output=expected,
        background_event=background,
        missing_fields=missing,
        conflicts=conflicts,
        source=INTENT_SOURCE_MODEL,
    )


def render_normalized_text(
    *,
    operation: str,
    region: str,
    sites: tuple[str, ...],
    windows: tuple[str, ...],
    goal: str,
    description: str,
    expected: str,
    background: str | None,
    language: str,
) -> str:
    """Render the canonical normalized text from the business fields."""
    if language == LANGUAGE_ZH:
        return _render_zh(operation, region, sites, windows, goal, description, expected, background)
    return _render_en(operation, region, sites, windows, goal, description, expected, background)


def render_plain_text(
    *,
    operation: str,
    region: str,
    sites: tuple[str, ...],
    windows: tuple[str, ...],
    goal: str,
    expected: str,
    background: str | None,
    language: str,
) -> str:
    """Render the natural-language request of one standard A2A hop.

    A2A must not carry the Task-T section layout: the same business values are rendered as a
    plain request instead, so the protocol comparison keeps two different request forms. The
    throughput windows keep the ``{HH:MM~HH:MM,NMbps}`` notation because the RAN agent's pure
    target policy and the NMS simulator parse that form.
    """
    if language == LANGUAGE_ZH:
        return _render_plain_zh(operation, region, sites, windows, goal, expected, background)
    return _render_plain_en(operation, region, sites, windows, goal, expected, background)


def _render_plain_en(
    operation: str,
    region: str,
    sites: tuple[str, ...],
    windows: tuple[str, ...],
    goal: str,
    expected: str,
    background: str | None,
) -> str:
    """Render the English A2A request without Task-T headings."""
    verb = (
        "Modify the running RAN energy saving task"
        if operation.strip().lower() == "modify"
        else "Create a RAN energy saving task"
    )
    parts = [f"{verb} in {region}." if region else f"{verb} in the target area."]
    if sites:
        parts.append(f"Site list 1: [{', '.join(sites)}].")
    if windows:
        parts.append(f"The time-segmented guaranteed throughput target is {', '.join(windows)}.")
    if goal:
        parts.append(_with_period(f"The energy saving goal is {goal}", language=LANGUAGE_EN))
    if background:
        parts.append(_with_period(f"The background event is {background}", language=LANGUAGE_EN))
    if expected:
        parts.append(_with_period(f"The expected output is {expected}", language=LANGUAGE_EN))
    return " ".join(parts)


def _render_plain_zh(
    operation: str,
    region: str,
    sites: tuple[str, ...],
    windows: tuple[str, ...],
    goal: str,
    expected: str,
    background: str | None,
) -> str:
    """Render the Chinese A2A request without Task-T headings."""
    verb = "修改运行中的" if operation.strip().lower() == "modify" else "创建"
    parts = [f"在{region or '目标区域'}{verb}无线网络节能任务。"]
    if sites:
        parts.append(f"站点列表1为[{'、'.join(sites)}]。")
    if windows:
        parts.append(f"分时段保障速率目标为 {'、'.join(windows)}。")
    if goal:
        parts.append(_with_period(f"节能目标为{goal}", language=LANGUAGE_ZH))
    if background:
        parts.append(_with_period(f"背景事件为{background}", language=LANGUAGE_ZH))
    if expected:
        parts.append(_with_period(f"预期输出为{expected}", language=LANGUAGE_ZH))
    return "".join(parts)


def _with_period(text: str, *, language: str) -> str:
    """Append the terminal punctuation of one plain sentence when it is missing."""
    stripped = text.strip()
    if not stripped:
        return stripped
    endings = "。！？" if language == LANGUAGE_ZH else ".!?"
    return stripped if stripped[-1] in endings else f"{stripped}{'。' if language == LANGUAGE_ZH else '.'}"


def _render_en(
    operation: str,
    region: str,
    sites: tuple[str, ...],
    windows: tuple[str, ...],
    goal: str,
    description: str,
    expected: str,
    background: str | None,
) -> str:
    """Render the English normalized text in the Task-T template's section order."""
    lines = [
        "## Operation Type",
        operation or "Create",
        "",
        "## Task Description",
        description or "RAN energy saving task based on the task object and task context.",
        "",
        "## Task Object",
    ]
    lines.append(f"The area is {region}." if region else "The area is not provided.")
    if sites:
        lines.append(f"Site list 1: [{', '.join(sites)}].")
    lines.extend(
        # No goal means no goal: never invent a "maximize" policy the NMS would execute. The
        # neutral placeholder keeps the Task-T section present while ``missingFields`` still
        # reports the omission.
        ["", "## Task Target", goal or "Not provided."]
    )
    lines.extend(["", "## Task Context"])
    if windows:
        lines.append(f"The time-segmented guaranteed throughput target is {', '.join(windows)}.")
    if background:
        lines.append(f"The background event is {background}.")
    if not windows and not background:
        lines.append("The task context is not further constrained.")
    lines.extend(["", "## Expected Output", expected or "The intent report of the task."])
    return "\n".join(lines).strip() + "\n"


def _render_zh(
    operation: str,
    region: str,
    sites: tuple[str, ...],
    windows: tuple[str, ...],
    goal: str,
    description: str,
    expected: str,
    background: str | None,
) -> str:
    """Render the Chinese normalized text in the Task-T template's section order."""
    lines = [
        "## 操作类型(Operation Type)",
        operation or "Create",
        "",
        "## 任务描述(Task Description)",
        description or "基于任务对象和任务上下文的无线网络节能任务。",
        "",
        "## 任务对象(Task Object)",
    ]
    lines.append(f"区域为 {region}。" if region else "未提供区域。")
    if sites:
        lines.append(f"站点列表1为[{', '.join(sites)}]。")
    lines.extend(["", "## 任务目标(Task Target)", goal or "未提供。"])
    lines.extend(["", "## 任务上下文(Task Context)"])
    if windows:
        lines.append(f"分时段保障速率目标为 {'、'.join(windows)}。")
    if background:
        lines.append(f"背景事件为 {background}。")
    if not windows and not background:
        lines.append("任务上下文没有更多约束。")
    lines.extend(["", "## 预期输出(Expected Output)", expected or "无线网络节能任务的意图报告。"])
    return "\n".join(lines).strip() + "\n"


def _string(value: object) -> str:
    """Return a stripped string, ``""`` otherwise."""
    return value.strip() if isinstance(value, str) else ""


def _string_list(value: object) -> tuple[str, ...]:
    """Return a tuple of non-empty strings from a JSON array."""
    if not isinstance(value, list):
        return ()
    return tuple(item.strip() for item in value if isinstance(item, str) and item.strip())


__all__ = [
    "DemoIntentExtractor",
    "DemoIntentProcessor",
    "apply_region_profile_text",
    "build_all_scenario_intents",
    "build_demo_intent_processor",
    "build_modify_intent",
    "build_scenario_intent",
    "render_normalized_text",
    "render_plain_text",
]
