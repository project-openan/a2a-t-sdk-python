"""A2A-T 的 Prompt 顶层包 / Top-level prompt package for A2A-T."""

from __future__ import annotations

from importlib import import_module

__all__ = [
    "PromptReference",
    "TaskPromptMetadata",
    "TaskPromptFormatError",
    "format_task_prompt",
    "parse_task_prompt_metadata",
    "TaskPromptRenderer",
    "TaskPromptRenderError",
]

_EXPORTS: dict[str, tuple[str, str]] = {
    "PromptReference": ("a2a_t.prompt.common.models", "PromptReference"),
    "TaskPromptMetadata": ("a2a_t.prompt.common.task_prompt_format", "TaskPromptMetadata"),
    "TaskPromptFormatError": ("a2a_t.prompt.common.task_prompt_format", "TaskPromptFormatError"),
    "format_task_prompt": ("a2a_t.prompt.common.task_prompt_format", "format_task_prompt"),
    "parse_task_prompt_metadata": ("a2a_t.prompt.common.task_prompt_format", "parse_task_prompt_metadata"),
    "TaskPromptRenderer": ("a2a_t.prompt.task_rendering", "TaskPromptRenderer"),
    "TaskPromptRenderError": ("a2a_t.prompt.task_rendering", "TaskPromptRenderError"),
}


def __getattr__(name: str):
    try:
        module_name, attr_name = _EXPORTS[name]
    except KeyError as error:
        raise AttributeError(f"module 'a2a_t.prompt' has no attribute {name!r}") from error

    value = getattr(import_module(module_name), attr_name)
    globals()[name] = value
    return value
