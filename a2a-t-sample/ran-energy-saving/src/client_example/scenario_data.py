"""Sample scenario input data for the ran-energy-saving e2e flow.

The client uses the template-directed ``generate_task_prompt_from_text`` API against the
``Task-T/network-layer/ran-energy-saving/v1`` template, so no scenario recognition is needed:
the natural-language input below directly drives the slot extraction.

The prompt input is selected by the configured language (``A2AT_LANGUAGE`` in ``.env``), so the
input language always matches the mock/template language tree (``zh-CN`` / ``en-US``).
"""

from __future__ import annotations

from pathlib import Path

from dotenv import dotenv_values

#: Task-T template addressed by this case.
ENERGY_SAVING_TEMPLATE_URI = "Task-T/network-layer/ran-energy-saving/v1"

#: Chinese natural-language task request used for prompt generation.
NATURAL_LANGUAGE_PROMPT_INPUT_ZH = (
    "在东莞松山湖管委会创建无线网络节能任务：节能的小区制式：NR；"
    "需要节能的时间范围（北京时间，UTC+8）：起始时间00:00:00，结束时间12:00:00；"
    "能耗目标：总功耗降低30%"
)

#: English natural-language task request used for prompt generation.
NATURAL_LANGUAGE_PROMPT_INPUT_EN = (
    "Create a RAN energy saving task in the Songshanhu Administration Committee area: "
    "cell RAT for energy saving: NR; energy saving time range (Beijing time, UTC+8): "
    "start time 00:00:00, end time 12:00:00; energy consumption goal: reduce total power "
    "consumption by 30%"
)


def resolve_language(*, env_path: Path | None = None) -> str:
    """Resolve the configured ``A2AT_LANGUAGE`` (defaults to ``zh-CN``)."""
    resolved = env_path or Path.cwd() / ".env"
    values = dotenv_values(resolved) if resolved.exists() else {}
    return str(values.get("A2AT_LANGUAGE", "")).strip() or "zh-CN"


def build_prompt_input(*, env_path: Path | None = None) -> str:
    """Return the prompt generation input matching the configured language."""
    if resolve_language(env_path=env_path) == "en-US":
        return NATURAL_LANGUAGE_PROMPT_INPUT_EN
    return NATURAL_LANGUAGE_PROMPT_INPUT_ZH


def build_task_request() -> dict[str, object]:
    """Build the sample Task-T request input (scenario name + agent query)."""
    return {
        "scenario": "create ran energy saving task",
        "agent_card_query": {
            "name": "RAN Energy Saving Agent",
            "organization": "Huawei",
        },
    }
