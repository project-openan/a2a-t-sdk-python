"""Tests for the never-throw error message rendering (port of Java ``ErrorMessages``)."""

from __future__ import annotations

import json
import logging
import re
from importlib.resources import files as resource_files

import pytest

from a2a_t.core.errors import messages
from a2a_t.core.errors.catalog import ErrorCatalog
from a2a_t.core.errors.messages import DEFAULT_LANGUAGE, ErrorMessages, render, template

MESSAGES_LOGGER = "a2a_t.core.errors.messages"
TEMPLATE_LANGUAGES = ("en-US", "zh-CN")


def _bundled_templates(language: str) -> dict[str, str]:
    """Load one language's bundled errors.json directly from the package resources."""
    resource = resource_files("a2a_t") / "prompt_resources" / "errors" / language / "errors.json"
    assert resource.is_file(), f"missing bundled errors.json for {language}"
    return json.loads(resource.read_text(encoding="utf-8"))


@pytest.mark.parametrize("language", TEMPLATE_LANGUAGES)
def test_bundled_template_keys_match_the_catalog_exactly(language: str) -> None:
    assert set(_bundled_templates(language)) == {member.value for member in ErrorCatalog}


@pytest.mark.parametrize("language", TEMPLATE_LANGUAGES)
@pytest.mark.parametrize("member", list(ErrorCatalog))
def test_template_placeholders_are_covered_by_declared_fact_parameters(
    member: ErrorCatalog, language: str
) -> None:
    text = template(member, language)
    assert text is not None
    placeholders = set(re.findall(r"\{([a-zA-Z][a-zA-Z0-9_]*)\}", text))
    # Every rendered placeholder must be a declared fact parameter, but facts may carry more
    # context than the message renders (template.not_found keeps the configured language in
    # facts while the message only reports the miss).
    assert placeholders <= set(member.fact_parameters)


@pytest.mark.parametrize(
    ("code", "facts", "language", "expected"),
    [
        (
            ErrorCatalog.INPUT_TEXT_TOO_LONG,
            {"actual_length": 20000, "max_chars": 16384},
            None,
            "Input text length 20000 exceeds the maximum of 16384 (A2AT_INPUT_TEXT_MAX_CHARS)",
        ),
        (
            ErrorCatalog.INPUT_TEXT_TOO_LONG,
            {"actual_length": 20000, "max_chars": 16384},
            "zh-CN",
            "输入文本长度 20000 超过上限 16384(A2AT_INPUT_TEXT_MAX_CHARS)",
        ),
        (
            ErrorCatalog.NEGOTIATION_MUTUALLY_EXCLUSIVE_SECTIONS,
            {"sections": "rounds, deadline"},
            None,
            "Mutually exclusive sections appear together: rounds, deadline",
        ),
        (
            ErrorCatalog.CONTENT_ENTRY_FIELD_MISSING,
            {"section_label": "items", "index": 3, "field_label": "name"},
            None,
            "Entry 3 of 'items' is missing required field 'name'",
        ),
        (
            ErrorCatalog.LLM_INVOCATION_FAILED,
            {"provider": "openai", "reason": "timeout"},
            "zh-CN",
            "LLM 调用失败(提供方 openai):timeout",
        ),
        (
            ErrorCatalog.SCENARIO_NOT_MATCHED,
            {"reason": "no known scenario applies"},
            None,
            "The input does not match any known scenario: no known scenario applies",
        ),
    ],
)
def test_render_substitutes_fact_values(
    code: ErrorCatalog,
    facts: dict[str, object],
    language: str | None,
    expected: str,
) -> None:
    assert render(code, facts, language) == expected


def test_render_ignores_fact_keys_without_a_placeholder() -> None:
    assert render(ErrorCatalog.SCENARIO_NOT_MATCHED, {"reason": "no match", "extra": "ignored"}) == (
        "The input does not match any known scenario: no match"
    )


def test_render_without_facts_returns_the_raw_template() -> None:
    expected = "Entry {index} of '{section_label}' is missing required field '{field_label}'"
    assert render(ErrorCatalog.CONTENT_ENTRY_FIELD_MISSING) == expected
    assert render(ErrorCatalog.CONTENT_ENTRY_FIELD_MISSING, None) == expected
    assert render(ErrorCatalog.CONTENT_ENTRY_FIELD_MISSING, {}) == expected


def test_render_keeps_partial_and_null_fact_placeholders_literal() -> None:
    assert render(ErrorCatalog.CONTENT_ENTRY_FIELD_MISSING, {"section_label": "items"}) == (
        "Entry {index} of 'items' is missing required field '{field_label}'"
    )
    assert render(ErrorCatalog.CONTENT_ENTRY_FIELD_MISSING, {"section_label": None}) == (
        "Entry {index} of '{section_label}' is missing required field '{field_label}'"
    )


def test_render_falls_back_to_the_default_language() -> None:
    assert render(ErrorCatalog.SLOT_NOT_PROVIDED, {"slot_label": "round"}, "fr-FR") == (
        "'round' is not provided in the input."
    )
    assert template("slot.not_provided", "fr-FR") == template("slot.not_provided", DEFAULT_LANGUAGE)


@pytest.mark.parametrize("language", [None, "", "   "])
def test_render_blank_language_uses_the_default(language: str | None) -> None:
    assert render(ErrorCatalog.SLOT_NOT_PROVIDED, {"slot_label": "round"}, language) == (
        "'round' is not provided in the input."
    )


def test_render_trims_the_language_tag() -> None:
    assert render(ErrorCatalog.SLOT_NOT_PROVIDED, {"slot_label": "轮次"}, " zh-CN ") == "输入中未提供「轮次」。"


def test_render_returns_the_bare_code_when_no_template_exists(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING, logger=MESSAGES_LOGGER):
        assert render("not.in_catalog", {"reason": "x"}, "zh-CN") == "not.in_catalog"
    assert "No error message template found for code 'not.in_catalog'" in caplog.text


def test_render_accepts_catalog_members_and_plain_strings() -> None:
    facts = {"template_uri": "Task-T/v1/energy-saving", "language": "zh-CN"}
    assert render(ErrorCatalog.TEMPLATE_NOT_FOUND, facts) == render("template.not_found", facts)


def test_template_returns_the_raw_template_without_rendering() -> None:
    assert template("slot.not_provided") == "'{slot_label}' is not provided in the input."


def test_template_returns_none_without_any_template() -> None:
    assert template("not.in_catalog") is None


def test_error_messages_facade_delegates_to_the_module_functions() -> None:
    facts = {"actual_length": 5, "max_chars": 2}
    assert ErrorMessages.render(ErrorCatalog.INPUT_TEXT_TOO_LONG, facts) == render(
        ErrorCatalog.INPUT_TEXT_TOO_LONG, facts
    )
    assert ErrorMessages.template("slot.not_provided") == template("slot.not_provided")
    assert ErrorMessages.DEFAULT_LANGUAGE == DEFAULT_LANGUAGE == "en-US"


def test_loaded_language_is_cached_while_a_missing_language_is_not(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(messages, "_templates_by_language", {})
    calls: list[str] = []
    original_loader = messages._load_templates

    def counting_loader(language: str) -> dict[str, str] | None:
        calls.append(language)
        return original_loader(language)

    monkeypatch.setattr(messages, "_load_templates", counting_loader)

    assert render("template.not_found", None, "xx-XX") != "template.not_found"
    assert render("template.not_found", None, "xx-XX") != "template.not_found"
    assert calls.count("xx-XX") == 2, "a missing language file must be re-read on every call"

    assert render("slot.not_provided", None, "en-US") != "slot.not_provided"
    assert render("slot.not_provided", None, "en-US") != "slot.not_provided"
    assert calls.count("en-US") == 1, "a loaded language file is cached after the first read"


class _MissingResource:
    """Resource stub whose file does not exist."""

    def is_file(self) -> bool:
        return False


class _BrokenResource:
    """Resource stub that fails while being inspected."""

    def is_file(self) -> bool:
        raise OSError("resource read failed")


def test_missing_resource_file_degrades_to_the_bare_code(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setattr(messages, "_templates_by_language", {})
    monkeypatch.setattr(messages, "_resource_file", lambda language: _MissingResource())
    with caplog.at_level(logging.WARNING, logger=MESSAGES_LOGGER):
        assert render("template.not_found", None, "en-US") == "template.not_found"
    assert "errors/en-US/errors.json' not found in package 'a2a_t'" in caplog.text


def test_unreadable_resource_degrades_to_the_bare_code(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setattr(messages, "_templates_by_language", {})
    monkeypatch.setattr(messages, "_resource_file", lambda language: _BrokenResource())
    with caplog.at_level(logging.WARNING, logger=MESSAGES_LOGGER):
        assert render("template.not_found", None, "en-US") == "template.not_found"
    assert "Cannot load error message resource" in caplog.text


def test_malformed_resource_degrades_to_the_bare_code(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setattr(messages, "_templates_by_language", {})
    monkeypatch.setattr(
        messages,
        "_resource_file",
        lambda language: _StaticResource("{not json"),
    )
    with caplog.at_level(logging.WARNING, logger=MESSAGES_LOGGER):
        assert render("template.not_found", None, "en-US") == "template.not_found"
    assert "Cannot load error message resource" in caplog.text


class _StaticResource:
    """Resource stub serving one fixed text payload."""

    def __init__(self, text: str) -> None:
        self._text = text

    def is_file(self) -> bool:
        return True

    def read_text(self, encoding: str = "utf-8") -> str:
        return self._text


def test_format_unsafe_templates_fall_back_to_the_java_substitution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        messages,
        "_templates_by_language",
        {"xx-XX": {"weird.code": "positional {} attribute {a.b} plain {name}"}},
    )
    assert render("weird.code", {"name": "value"}, "xx-XX") == "positional {} attribute {a.b} plain value"
