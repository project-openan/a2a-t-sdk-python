"""Tests for ``tools/template_lint.py`` — the P0 CI gate ported from the Java repository's 582-line linter.

The fixtures build a minimal but fully valid ``prompt_resources`` root: Task-T and Authorization-T
template/slot pairs, the complete Negotiation-T matrix, both negotiation vocabularies, both error
message catalogs, and the closed prompt family set including the two Python-only whitelist families
(D11). Every ported rule is then exercised by mutating that root — a rule that never fails on a
deliberately broken fixture would be a fake-green gate.

Error-code contract tests pin the catalog with a small synthetic table (via ``strict_catalog``) so
they stay deterministic regardless of the real ``a2a_t.core.errors.catalog`` contents; the degraded
behavior (catalog unavailable) is pinned separately via ``degraded_catalog``.

The bundled ``src/a2a_t/prompt_resources`` root is synced to the Java 1.1.0 resources (P2) with one
deliberate, Python-only divergence: the slot-extraction system prompt carries the generic
one-fragment-one-slot / missing-value rules added for the Task-T conversion fixes. Java sync is
deferred, so byte-for-byte equality with the Java repository is temporarily not expected. The tests
below pin that the current bundle passes the strict gate with the real catalog — neither errors nor
warnings. The temporary P0 stale-resource whitelist was deleted together with the original sync.
"""

from __future__ import annotations

import enum
import importlib.util
import json
import shutil
import subprocess
import sys
import types
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).parents[1]
LINTER_PATH = REPO_ROOT / "tools" / "template_lint.py"
BUNDLED_ROOT = REPO_ROOT / "src" / "a2a_t" / "prompt_resources"

_SPEC = importlib.util.spec_from_file_location("template_lint_under_test", LINTER_PATH)
assert _SPEC is not None and _SPEC.loader is not None
linter = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = linter
_SPEC.loader.exec_module(linter)


def _real_catalog_available() -> bool:
    try:
        return importlib.util.find_spec("a2a_t.core.errors.catalog") is not None
    except (ImportError, ValueError):
        return False


REAL_CATALOG_AVAILABLE = _real_catalog_available()

# Synthetic error catalog driving the strict-mode fixtures (mirrors the shape of the P1 ErrorCatalog:
# 'domain.semantic' codes plus declared fact parameter names).
CATALOG_FACTS: dict[str, list[str]] = {
    "content.param_missing": ["field"],
    "input.text_too_long": ["actual_length", "max_length"],
}

TASK_TEMPLATE = "## Task Description\n{{task_description}}\n\n## Expected Output\n{{expected_output}}\n"
TASK_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"task_description": {"type": "string"}, "expected_output": {"type": "string"}},
    "required": ["task_description"],
}
NOTIFICATION_TEMPLATE = (
    "## Subscription Description\n{{subscription_description}}\n\n## Expected Output\n{{expected_output}}\n"
)
NOTIFICATION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"subscription_description": {"type": "string"}, "expected_output": {"type": "string"}},
    "required": ["subscription_description"],
}
AUTHORIZATION_TEMPLATE = (
    "## Authorization Policy Operation Type\n{{operation_type}}\n\n## Expected Output\n{{expected_output}}\n"
)
AUTHORIZATION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"operation_type": {"type": "string"}, "expected_output": {"type": "string"}},
    "required": ["operation_type"],
}
ABORT_TEMPLATE = "<!-- common abort -->\n## Negotiation Result\nAbort\n\n## Negotiation Termination Reason\n{{termination_reason}} (required)\nRequirement:\nReason text.\n"

# Prompt body carrying only code references that are valid for the synthetic catalog (or allowlisted).
PROMPT_BODY = (
    "You are a validation assistant.\n"
    'Return JSON with a code such as {"code": "content.param_missing"}.\n'
    'Alternative codes are pipe-joined: "code": "content.param_missing|input.text_too_long".\n'
    "Section names like `section.info_static` are not error codes.\n"
)


def error_templates() -> dict[str, str]:
    return {
        "content.param_missing": "Field {field} is missing.",
        "input.text_too_long": "Length {actual_length} exceeds the maximum of {max_length}.",
    }


def real_catalog_error_templates() -> dict[str, str]:
    """Builds error templates covering the real catalog (CLI strict-gate green).

    The CLI run cannot inject a synthetic catalog, so the root used by CLI
    pass-path tests must satisfy the real code table. Each template mentions
    every declared fact parameter so the placeholder contract holds.
    """
    from a2a_t.core.errors.catalog import ErrorCatalog

    return {
        member.value: " ".join([f"Error {member.name}."] + [f"{{{fact}}}" for fact in member.fact_parameters])
        for member in ErrorCatalog
    }


def negotiation_section_keys() -> tuple[set[str], set[str]]:
    sections: set[str] = set(linter.NEGOTIATION_STATIC_SECTIONS)
    for keys in linter.NEGOTIATION_PROFILES.values():
        sections.update(keys)
    return sections, sections - linter.NEGOTIATION_STATIC_SECTIONS


def vocabulary(language: str) -> dict[str, str]:
    sections, slots = negotiation_section_keys()
    data = {f"section.{key}": f"{language}-section-{key}" for key in sorted(sections)}
    data.update({f"slot.{key}": f"{language}-slot-{key}" for key in sorted(slots)})
    # Keys beyond the profile matrix are tolerated (the Java vocabulary carries termination_reason too).
    data["section.termination_reason"] = f"{language}-section-termination_reason"
    data["slot.termination_reason"] = f"{language}-slot-termination_reason"
    return data


def section_title(language: str, key: str) -> str:
    return vocabulary(language)[f"section.{key}"]


def slot_name(language: str, key: str) -> str:
    return vocabulary(language)[f"slot.{key}"]


def negotiation_template(type_segment: str, phase_segment: str, language: str) -> str:
    profile = linter.NEGOTIATION_PROFILES[(type_segment, phase_segment)]
    data = vocabulary(language)
    marker_suffix = "（必填）" if language == "zh-CN" else " (required)"
    requirements_label = "要求：" if language == "zh-CN" else "Requirement:"
    lines = [f"<!-- {type_segment}/{phase_segment}/{language} -->", ""]
    for key in profile:
        lines.append(f"## {data[f'section.{key}']}")
        if key in linter.NEGOTIATION_STATIC_SECTIONS:
            lines.extend([f"Static body of {key}.", ""])
            continue
        lines.append(f"{{{{{data[f'slot.{key}']}}}}}{marker_suffix}")
        lines.append(requirements_label)
        lines.append(f"Body of {key}.")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def write_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def negotiation_path(root: Path, type_segment: str, phase_segment: str, language: str) -> Path:
    return root / "templates" / "Negotiation-T" / type_segment / phase_segment / "v1" / language / "template.md"


def build_resource_root(root: Path) -> Path:
    """Builds a fully green prompt_resources root for the ported rules."""
    task_dir = root / "templates" / "Task-T" / "network-layer" / "example" / "v1" / "en-US"
    task_dir.mkdir(parents=True)
    (task_dir / "template.md").write_text(TASK_TEMPLATE, encoding="utf-8")
    write_json(root / "slots" / "Task-T" / "network-layer" / "example" / "v1" / "en-US" / "slot.json", TASK_SCHEMA)
    authorization_dir = root / "templates" / "Authorization-T" / "example" / "v1" / "en-US"
    authorization_dir.mkdir(parents=True)
    (authorization_dir / "template.md").write_text(AUTHORIZATION_TEMPLATE, encoding="utf-8")
    write_json(root / "slots" / "Authorization-T" / "example" / "v1" / "en-US" / "slot.json", AUTHORIZATION_SCHEMA)
    for type_segment in linter.NEGOTIATION_TYPE_SEGMENTS:
        for phase_segment in linter.NEGOTIATION_PHASE_SEGMENTS:
            for language in linter.NEGOTIATION_LANGUAGES:
                path = negotiation_path(root, type_segment, phase_segment, language)
                path.parent.mkdir(parents=True)
                path.write_text(negotiation_template(type_segment, phase_segment, language), encoding="utf-8")
    for language in linter.NEGOTIATION_LANGUAGES:
        abort_path = root / "templates" / "Negotiation-T" / "common" / "abort" / "v1" / language / "template.md"
        abort_path.parent.mkdir(parents=True)
        abort_path.write_text(ABORT_TEMPLATE, encoding="utf-8")
        write_json(root / "negotiation-vocabulary" / language / "vocabulary.json", vocabulary(language))
        write_json(root / "errors" / language / "errors.json", error_templates())
    for family in (*linter.PROMPT_FAMILIES, *linter.PROMPT_FAMILY_WHITELIST):
        for language in linter.NEGOTIATION_LANGUAGES:
            for file_name in linter.PROMPT_FILES:
                path = root / "prompts" / family / language / file_name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(PROMPT_BODY, encoding="utf-8")
    return root


def lint_errors(root: Path) -> list[Any]:
    errors, _warnings = linter.lint_resource_root(root)
    return errors


def lint_rules(root: Path) -> set[str]:
    return {item.rule for item in lint_errors(root)}


def findings_with_rule(root: Path, rule: str) -> list[Any]:
    return [item for item in lint_errors(root) if item.rule == rule]


def rule_messages(root: Path, rule: str) -> str:
    return " ".join(item.message for item in findings_with_rule(root, rule))


# --- file mutation helpers (each asserts its target exists: a silent no-op mutation is a fake-green test) ---


def replace_text(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    assert old in text, f"mutation target {old!r} not found in {path}"
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def remove_section(path: Path, title: str) -> None:
    lines = path.read_text(encoding="utf-8").splitlines()
    header = f"## {title}"
    assert header in lines, f"section {title!r} not found in {path}"
    out: list[str] = []
    skipping = False
    for ln in lines:
        if ln.startswith("## "):
            skipping = ln == header
        if not skipping:
            out.append(ln)
    path.write_text("\n".join(out).rstrip() + "\n", encoding="utf-8")


def remove_body_line(path: Path, title: str, target: str) -> None:
    lines = path.read_text(encoding="utf-8").splitlines()
    out: list[str] = []
    inside = False
    removed = False
    for ln in lines:
        if ln.startswith("## "):
            inside = ln == f"## {title}"
            out.append(ln)
        elif inside and not removed and ln == target:
            removed = True
        else:
            out.append(ln)
    assert removed, f"line {target!r} not found in section {title!r} of {path}"
    path.write_text("\n".join(out) + "\n", encoding="utf-8")


def insert_after_header(path: Path, title: str, line: str) -> None:
    lines = path.read_text(encoding="utf-8").splitlines()
    header = f"## {title}"
    assert header in lines, f"section {title!r} not found in {path}"
    lines.insert(lines.index(header) + 1, line)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def append_section(path: Path, header: str, body: str) -> None:
    path.write_text(path.read_text(encoding="utf-8").rstrip() + "\n\n" + header + "\n" + body, encoding="utf-8")


def duplicate_section(path: Path, title: str) -> None:
    lines = path.read_text(encoding="utf-8").splitlines()
    header = f"## {title}"
    assert header in lines, f"section {title!r} not found in {path}"
    start = lines.index(header)
    end = start + 1
    while end < len(lines) and not lines[end].startswith("## "):
        end += 1
    lines[end:end] = ["", *lines[start:end]]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def reverse_sections(path: Path) -> None:
    lines = path.read_text(encoding="utf-8").splitlines()
    preamble: list[str] = []
    blocks: list[list[str]] = []
    for ln in lines:
        if ln.startswith("## "):
            blocks.append([ln])
        elif blocks:
            blocks[-1].append(ln)
        else:
            preamble.append(ln)
    assert len(blocks) >= 2, f"fewer than two sections in {path}"
    rewritten = [*preamble, *(line for block in reversed(blocks) for line in block)]
    path.write_text("\n".join(rewritten).rstrip() + "\n", encoding="utf-8")


def write_pair(tmp_path: Path, template: str, schema: object, name: str = "pair") -> tuple[Path, Path]:
    template_path = tmp_path / f"{name}-template.md"
    schema_path = tmp_path / f"{name}-slot.json"
    template_path.write_text(template, encoding="utf-8")
    schema_path.write_text(json.dumps(schema), encoding="utf-8")
    return template_path, schema_path


def pair_rules(tmp_path: Path, template: str, schema: object, name: str = "pair") -> set[str]:
    template_path, schema_path = write_pair(tmp_path, template, schema, name)
    return {item.rule for item in linter.lint_pair(template_path, schema_path)}


# --- fake catalog helpers (pin the load_error_catalog import contract for P1) ---


def make_catalog_enum(members: dict[str, list[str]], attribute: str = "fact_parameters") -> type[enum.Enum]:
    catalog = enum.Enum("ErrorCatalog", [(code, code) for code in members])
    for member, facts in zip(catalog, members.values()):
        setattr(member, attribute, list(facts))
    return catalog


def install_catalog_module(monkeypatch: pytest.MonkeyPatch, catalog: object | None) -> None:
    """Installs a fake ``a2a_t.core.errors.catalog`` module; ``None`` leaves ErrorCatalog unimportable."""
    module = types.ModuleType("a2a_t.core.errors.catalog")
    if catalog is not None:
        module.ErrorCatalog = catalog  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "a2a_t.core.errors.catalog", module)


class FakeMember:
    def __init__(self, value: str) -> None:
        self.value = value


@pytest.fixture
def strict_catalog(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(linter, "load_error_catalog", lambda: (dict(CATALOG_FACTS), []))


@pytest.fixture
def degraded_catalog(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(linter, "load_error_catalog", lambda: (None, []))


@pytest.fixture
def green_root(tmp_path: Path) -> Path:
    return build_resource_root(tmp_path / "prompt_resources")


# --- pinned contract tables ---


def test_prompt_whitelist_pins_the_python_only_families() -> None:
    assert linter.PROMPT_FAMILY_WHITELIST == ("clarification_negotiation", "fulfillment_negotiation")
    assert set(linter.PROMPT_FAMILY_WHITELIST).isdisjoint(linter.PROMPT_FAMILIES)
    assert len(linter.PROMPT_FAMILIES) == 9
    assert linter.PROMPT_LANGUAGES == ("zh-CN", "en-US")
    assert linter.PROMPT_FILES == ("system.md", "user.md")


def test_negotiation_profile_matrix_covers_every_type_and_phase() -> None:
    expected = {
        (type_segment, phase_segment)
        for type_segment in linter.NEGOTIATION_TYPE_SEGMENTS
        for phase_segment in linter.NEGOTIATION_PHASE_SEGMENTS
    }
    assert set(linter.NEGOTIATION_PROFILES) == expected
    assert all(linter.NEGOTIATION_PROFILES[key] for key in linter.NEGOTIATION_PROFILES)
    sections, _slots = negotiation_section_keys()
    assert linter.NEGOTIATION_STATIC_SECTIONS <= sections


@pytest.mark.parametrize(("alias", "expected"), sorted(linter.ALIASES.items()))
def test_canonical_heading_resolves_every_alias(alias: str, expected: str) -> None:
    assert linter.canonical_heading(alias) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("任务描述 (Task Description)", "Task Description"),
        ("Task Description (任务描述)", "Task Description"),
        ("任务目标 (Goal)", "Task Target"),
        ("Unheard Of Heading", "Unheard Of Heading"),
    ],
)
def test_canonical_heading_parenthesized_and_unknown_values(value: str, expected: str) -> None:
    assert linter.canonical_heading(value) == expected


# --- green root and degraded mode ---


def test_minimal_valid_root_passes_strict_gate(green_root: Path, strict_catalog: None) -> None:
    errors, warnings = linter.lint_resource_root(green_root)
    assert [str(item) for item in errors] == []
    assert warnings == []


def test_degraded_mode_skips_code_set_checks_with_single_p1_warning(green_root: Path, degraded_catalog: None) -> None:
    errors, warnings = linter.lint_resource_root(green_root)
    assert [str(item) for item in errors] == []
    assert len(warnings) == 1
    assert "a2a_t.core.errors.catalog" in warnings[0]
    assert "P1 TODO" in warnings[0]


# --- Task-T / Notification-T / Authorization-T template and slot rules (lint_pair) ---


@pytest.mark.parametrize(
    ("template", "schema"),
    [
        (TASK_TEMPLATE, TASK_SCHEMA),
        (NOTIFICATION_TEMPLATE, NOTIFICATION_SCHEMA),
        (AUTHORIZATION_TEMPLATE, AUTHORIZATION_SCHEMA),
    ],
    ids=["task", "notification", "authorization"],
)
def test_profile_templates_pass(tmp_path: Path, template: str, schema: object) -> None:
    assert pair_rules(tmp_path, template, schema) == set()


@pytest.mark.parametrize(
    ("template", "schema"),
    [
        (TASK_TEMPLATE, TASK_SCHEMA),
        (NOTIFICATION_TEMPLATE, NOTIFICATION_SCHEMA),
        (AUTHORIZATION_TEMPLATE, AUTHORIZATION_SCHEMA),
    ],
    ids=["task", "notification", "authorization"],
)
def test_instruction_required_missing_profile_anchor(tmp_path: Path, template: str, schema: object) -> None:
    anchor = (
        "## "
        + {
            "## Task Description": "Task Description",
            "## Subscription Description": "Subscription Description",
            "## Authorization Policy Operation Type": "Authorization Policy Operation Type",
        }[template.splitlines()[0]]
    )
    assert "instruction-required" in pair_rules(tmp_path, template.replace(anchor, "## Something Else"), schema)


@pytest.mark.parametrize(
    ("template", "schema", "foreign_heading"),
    [
        (TASK_TEMPLATE, TASK_SCHEMA, "## Subscription Description"),
        (NOTIFICATION_TEMPLATE, NOTIFICATION_SCHEMA, "## Task Type"),
        (AUTHORIZATION_TEMPLATE, AUTHORIZATION_SCHEMA, "## Constraints"),
        (TASK_TEMPLATE, TASK_SCHEMA, "## 完全未知标题"),
    ],
    ids=["task-has-notification", "notification-has-task", "authorization-has-task", "unknown-heading"],
)
def test_instruction_name_rejects_foreign_headings(
    tmp_path: Path, template: str, schema: object, foreign_heading: str
) -> None:
    assert "instruction-name" in pair_rules(tmp_path, template + foreign_heading + "\ntext\n", schema)


@pytest.mark.parametrize(
    "template",
    [
        "No headings at all, just prose about a task.\n",
        "### Task Description\n{{task_description}}\n",
    ],
    ids=["no-headings", "only-h3-headings"],
)
def test_instruction_missing_when_no_l0_heading(tmp_path: Path, template: str) -> None:
    rules = pair_rules(tmp_path, template, TASK_SCHEMA)
    assert "instruction-missing" in rules
    assert "instruction-required" in rules


def test_instruction_duplicate_repeated_heading(tmp_path: Path) -> None:
    template = TASK_TEMPLATE + "\n## Task Description\nagain\n"
    assert "instruction-duplicate" in pair_rules(tmp_path, template, TASK_SCHEMA)


@pytest.mark.parametrize(
    ("anchor", "alias"),
    [
        ("## 任务描述", "## 约束条件"),
        ("## 任务描述 (Task Description)", "## 预期输出 (Expected Output)"),
        ("## 订阅描述", "## 上报通知数据格式"),
        ("## 授权策略的操作类型", "## 动网操作的授权策略列表"),
        ("## 任务描述", "## 术语解释"),
        ("## 任务描述", "## 目标对象"),
    ],
    ids=[
        "task-alias",
        "task-alias-parens",
        "notification-alias",
        "authorization-alias",
        "terminology-alias",
        "object-alias",
    ],
)
def test_chinese_heading_aliases_are_canonicalized(tmp_path: Path, anchor: str, alias: str) -> None:
    assert pair_rules(tmp_path, anchor + "\ntext\n\n" + alias + "\ntext\n", {"properties": {}}) == set()


def test_slot_undefined_placeholder_missing_from_schema(tmp_path: Path) -> None:
    template = "## Task Description\n{{task_description}} and {{unknown_slot}}\n"
    assert "slot-undefined" in pair_rules(tmp_path, template, TASK_SCHEMA)


def test_slot_unused_schema_slot_without_placeholder(tmp_path: Path) -> None:
    schema = dict(TASK_SCHEMA)
    schema["properties"] = {**TASK_SCHEMA["properties"], "never_used": {"type": "string"}}
    assert "slot-unused" in pair_rules(tmp_path, TASK_TEMPLATE, schema)


def test_schema_json_unparseable_slot_file(tmp_path: Path) -> None:
    template_path = tmp_path / "t.md"
    schema_path = tmp_path / "s.json"
    template_path.write_text(TASK_TEMPLATE, encoding="utf-8")
    schema_path.write_text("{not json", encoding="utf-8")
    rules = {item.rule for item in linter.lint_pair(template_path, schema_path)}
    assert "schema-json" in rules


def test_schema_properties_missing(tmp_path: Path) -> None:
    assert "schema-properties" in pair_rules(tmp_path, TASK_TEMPLATE, {"type": "object"})


@pytest.mark.parametrize(
    "required",
    [
        "task_description",
        ["task_description", 42],
        ["not_a_property"],
    ],
    ids=["not-a-list", "non-string-member", "unknown-name"],
)
def test_schema_required_contract(tmp_path: Path, required: object) -> None:
    schema = {"type": "object", "properties": {"task_description": {"type": "string"}}, "required": required}
    assert "schema-required" in pair_rules(tmp_path, "## Task Description\n{{task_description}}\n", schema)


def test_template_read_error_when_template_is_a_directory(tmp_path: Path) -> None:
    template_path = tmp_path / "template.md"
    template_path.mkdir()
    schema_path = tmp_path / "slot.json"
    schema_path.write_text(json.dumps(TASK_SCHEMA), encoding="utf-8")
    rules = {item.rule for item in linter.lint_pair(template_path, schema_path)}
    assert "template-read" in rules


# --- resource root layout rules ---


def test_resource_root_missing_templates_directory(tmp_path: Path) -> None:
    (tmp_path / "slots").mkdir()
    assert lint_rules(tmp_path) == {"resource-root"}
    assert "Missing templates directory" in rule_messages(tmp_path, "resource-root")


def test_resource_root_missing_slots_directory(tmp_path: Path) -> None:
    (tmp_path / "templates").mkdir()
    assert lint_rules(tmp_path) == {"resource-root"}
    assert "Missing slots directory" in rule_messages(tmp_path, "resource-root")


def test_template_missing_paired_template(green_root: Path, strict_catalog: None) -> None:
    template = green_root / "templates" / "Task-T" / "network-layer" / "example" / "v1" / "en-US" / "template.md"
    template.unlink()
    assert lint_rules(green_root) == {"template-missing"}
    assert "Missing paired template" in rule_messages(green_root, "template-missing")


def test_task_template_read_error_via_root(green_root: Path, strict_catalog: None) -> None:
    template = green_root / "templates" / "Task-T" / "network-layer" / "example" / "v1" / "en-US" / "template.md"
    template.unlink()
    template.mkdir()
    # The directory matches the template glob (template-read) but fails is_file() (template-missing).
    assert lint_rules(green_root) == {"template-read", "template-missing"}


# --- Negotiation-T rules ---


@pytest.mark.parametrize(
    ("type_segment", "phase_segment", "key"),
    [
        ("information-negotiation", "propose", "info_static"),
        ("information-negotiation", "propose", "info_items"),
        ("information-negotiation", "accept-reject", "info_conclusion"),
        ("information-negotiation", "accept-reject", "info_result_content"),
        ("target-negotiation", "propose", "target"),
        ("target-negotiation", "propose", "target_confirm_request"),
        ("target-negotiation", "accept-reject", "target_conclusion"),
        ("feasibility-negotiation", "propose", "feasibility_infeasible"),
        ("feasibility-negotiation", "accept-reject", "feasibility_confirm"),
    ],
)
def test_negotiation_missing_required_section(
    green_root: Path, strict_catalog: None, type_segment: str, phase_segment: str, key: str
) -> None:
    for language in linter.NEGOTIATION_LANGUAGES:
        remove_section(
            negotiation_path(green_root, type_segment, phase_segment, language), section_title(language, key)
        )
    assert lint_rules(green_root) == {"negotiation-section-missing"}
    messages = rule_messages(green_root, "negotiation-section-missing")
    assert section_title("zh-CN", key) in messages
    assert section_title("en-US", key) in messages


def test_negotiation_no_sections_at_all(green_root: Path, strict_catalog: None) -> None:
    path = negotiation_path(green_root, "target-negotiation", "propose", "en-US")
    path.write_text("This template has no section headers.\n", encoding="utf-8")
    assert lint_rules(green_root) == {"negotiation-section-missing"}
    assert "must contain sections marked with '## '" in rule_messages(green_root, "negotiation-section-missing")


def test_negotiation_unrecognized_section_title(green_root: Path, strict_catalog: None) -> None:
    path = negotiation_path(green_root, "information-negotiation", "propose", "en-US")
    replace_text(path, f"## {section_title('en-US', 'info_items')}", "## Not A Real Section")
    assert "negotiation-section-name" in lint_rules(green_root)
    assert "not a recognized Negotiation-T section title" in rule_messages(green_root, "negotiation-section-name")


def test_negotiation_section_from_wrong_profile(green_root: Path, strict_catalog: None) -> None:
    path = negotiation_path(green_root, "target-negotiation", "propose", "en-US")
    foreign_title = section_title("en-US", "info_items")
    append_section(path, f"## {foreign_title}", "{{en-US-slot-info_items}} (required)\nRequirement:\nBody.\n")
    assert "negotiation-section-name" in lint_rules(green_root)
    messages = rule_messages(green_root, "negotiation-section-name")
    assert "not valid for the target-negotiation/propose" in messages


def test_negotiation_wrong_language_title(green_root: Path, strict_catalog: None) -> None:
    path = negotiation_path(green_root, "information-negotiation", "propose", "zh-CN")
    replace_text(path, f"## {section_title('zh-CN', 'info_items')}", f"## {section_title('en-US', 'info_items')}")
    assert "negotiation-section-name" in lint_rules(green_root)
    assert "is not the zh-CN title of section 'info_items'" in rule_messages(green_root, "negotiation-section-name")


def test_negotiation_duplicate_section(green_root: Path, strict_catalog: None) -> None:
    path = negotiation_path(green_root, "target-negotiation", "accept-reject", "en-US")
    duplicate_section(path, section_title("en-US", "target_conclusion"))
    assert "negotiation-section-duplicate" in lint_rules(green_root)
    assert "is repeated" in rule_messages(green_root, "negotiation-section-duplicate")


def test_negotiation_static_section_must_not_contain_slot_line(green_root: Path, strict_catalog: None) -> None:
    path = negotiation_path(green_root, "information-negotiation", "propose", "zh-CN")
    insert_after_header(
        path, section_title("zh-CN", "info_static"), f"{{{{{slot_name('zh-CN', 'info_items')}}}}}（必填）"
    )
    assert lint_rules(green_root) == {"negotiation-slot-structure"}
    assert "Static section" in rule_messages(green_root, "negotiation-slot-structure")


def test_negotiation_static_section_must_not_contain_requirements_line(green_root: Path, strict_catalog: None) -> None:
    path = negotiation_path(green_root, "information-negotiation", "propose", "zh-CN")
    insert_after_header(path, section_title("zh-CN", "info_static"), "要求：")
    assert lint_rules(green_root) == {"negotiation-requirements"}


def test_negotiation_slot_section_requires_standalone_marker_line(green_root: Path, strict_catalog: None) -> None:
    path = negotiation_path(green_root, "information-negotiation", "propose", "zh-CN")
    remove_body_line(path, section_title("zh-CN", "info_items"), f"{{{{{slot_name('zh-CN', 'info_items')}}}}}（必填）")
    rules = lint_rules(green_root)
    assert "negotiation-slot-structure" in rules
    assert "negotiation-alignment" in rules
    assert "must be followed by a standalone slot line" in rule_messages(green_root, "negotiation-slot-structure")


def test_negotiation_slot_section_requires_requirements_line(green_root: Path, strict_catalog: None) -> None:
    path = negotiation_path(green_root, "information-negotiation", "propose", "zh-CN")
    remove_body_line(path, section_title("zh-CN", "info_items"), "要求：")
    assert lint_rules(green_root) == {"negotiation-requirements"}
    assert "must contain a '要求：' line" in rule_messages(green_root, "negotiation-requirements")


@pytest.mark.parametrize(
    ("language", "good_suffix", "bad_suffix"),
    [("zh-CN", "（必填）", " (required)"), ("en-US", " (required)", "（必填）")],
    ids=["zh-marker-with-en-punctuation", "en-marker-with-zh-punctuation"],
)
def test_negotiation_slot_marker_language_punctuation(
    green_root: Path, strict_catalog: None, language: str, good_suffix: str, bad_suffix: str
) -> None:
    path = negotiation_path(green_root, "information-negotiation", "propose", language)
    replace_text(path, f"}}}}{good_suffix}", f"}}}}{bad_suffix}")
    assert lint_rules(green_root) == {"negotiation-slot-marker"}
    assert "must use" in rule_messages(green_root, "negotiation-slot-marker")


def test_negotiation_slot_name_must_match_vocabulary(green_root: Path, strict_catalog: None) -> None:
    path = negotiation_path(green_root, "information-negotiation", "propose", "en-US")
    replace_text(path, f"{{{{{slot_name('en-US', 'info_items')}}}}}", "{{wrong_slot_name}}")
    assert lint_rules(green_root) == {"negotiation-slot-name"}
    assert "must be '" in rule_messages(green_root, "negotiation-slot-name")


def test_negotiation_comment_header_must_be_well_formed(green_root: Path, strict_catalog: None) -> None:
    path = negotiation_path(green_root, "feasibility-negotiation", "propose", "en-US")
    path.write_text("<!-- broken comment\n" + path.read_text(encoding="utf-8"), encoding="utf-8")
    assert lint_rules(green_root) == {"negotiation-comment"}


@pytest.mark.parametrize(
    "relative",
    [
        "information-negotiation/propose/v1/zh-CN/template.md",
        "target-negotiation/propose/v1/en-US/template.md",
        "target-negotiation/accept-reject/v1/zh-CN/template.md",
        "feasibility-negotiation/propose/v1/en-US/template.md",
        "common/abort/v1/zh-CN/template.md",
    ],
)
def test_negotiation_file_set_missing_template(green_root: Path, strict_catalog: None, relative: str) -> None:
    (green_root / "templates" / "Negotiation-T" / relative).unlink()
    assert "negotiation-file-set" in lint_rules(green_root)
    assert "Missing Negotiation-T template" in rule_messages(green_root, "negotiation-file-set")


def test_negotiation_file_set_unexpected_location(green_root: Path, strict_catalog: None) -> None:
    source = negotiation_path(green_root, "information-negotiation", "propose", "en-US")
    target = (
        green_root
        / "templates"
        / "Negotiation-T"
        / "information-negotiation"
        / "propose"
        / "v2"
        / "en-US"
        / "template.md"
    )
    target.parent.mkdir(parents=True)
    shutil.copy(source, target)
    assert lint_rules(green_root) == {"negotiation-file-set"}
    assert "Unexpected Negotiation-T template location" in rule_messages(green_root, "negotiation-file-set")


def test_negotiation_file_set_missing_directory(green_root: Path, strict_catalog: None) -> None:
    shutil.rmtree(green_root / "templates" / "Negotiation-T")
    assert lint_rules(green_root) == {"negotiation-file-set"}
    assert "Missing Negotiation-T templates directory" in rule_messages(green_root, "negotiation-file-set")


def test_negotiation_template_read_error(green_root: Path, strict_catalog: None) -> None:
    path = negotiation_path(green_root, "feasibility-negotiation", "accept-reject", "en-US")
    path.unlink()
    path.mkdir()
    assert lint_rules(green_root) == {"negotiation-template-read"}


def test_negotiation_common_abort_content_is_not_content_linted(green_root: Path, strict_catalog: None) -> None:
    for language in linter.NEGOTIATION_LANGUAGES:
        path = green_root / "templates" / "Negotiation-T" / "common" / "abort" / "v1" / language / "template.md"
        path.write_text("garbage without any contract\n", encoding="utf-8")
    assert lint_rules(green_root) == set()


def test_negotiation_alignment_section_count(green_root: Path, strict_catalog: None) -> None:
    remove_section(
        negotiation_path(green_root, "target-negotiation", "propose", "en-US"),
        section_title("en-US", "target_clarification"),
    )
    rules = lint_rules(green_root)
    assert "negotiation-section-missing" in rules
    assert "negotiation-alignment" in rules
    assert "Section count differs" in rule_messages(green_root, "negotiation-alignment")


def test_negotiation_alignment_section_order(green_root: Path, strict_catalog: None) -> None:
    reverse_sections(negotiation_path(green_root, "target-negotiation", "propose", "en-US"))
    assert "negotiation-alignment" in lint_rules(green_root)
    assert "diverges from zh-CN template" in rule_messages(green_root, "negotiation-alignment")


@pytest.mark.parametrize("language", linter.NEGOTIATION_LANGUAGES)
def test_negotiation_alignment_marker_kind(green_root: Path, strict_catalog: None, language: str) -> None:
    path = negotiation_path(green_root, "information-negotiation", "propose", language)
    suffix = "（必填）" if language == "zh-CN" else " (required)"
    replacement = "（选填）" if language == "zh-CN" else " (optional)"
    replace_text(path, f"}}}}{suffix}", f"}}}}{replacement}")
    assert lint_rules(green_root) == {"negotiation-alignment"}
    assert "marker" in rule_messages(green_root, "negotiation-alignment")


# --- negotiation vocabulary rules ---


def test_vocabulary_missing_directory(green_root: Path, strict_catalog: None) -> None:
    shutil.rmtree(green_root / "negotiation-vocabulary")
    assert lint_rules(green_root) == {"negotiation-vocabulary"}
    assert "Missing negotiation-vocabulary directory" in rule_messages(green_root, "negotiation-vocabulary")


@pytest.mark.parametrize("language", linter.NEGOTIATION_LANGUAGES)
def test_vocabulary_missing_language_file(green_root: Path, strict_catalog: None, language: str) -> None:
    (green_root / "negotiation-vocabulary" / language / "vocabulary.json").unlink()
    assert lint_rules(green_root) == {"negotiation-vocabulary"}
    assert "Cannot load negotiation vocabulary" in rule_messages(green_root, "negotiation-vocabulary")


def test_vocabulary_unexpected_language_directory(green_root: Path, strict_catalog: None) -> None:
    (green_root / "negotiation-vocabulary" / "fr-FR").mkdir()
    assert lint_rules(green_root) == {"negotiation-vocabulary"}
    assert "Unexpected negotiation vocabulary language directory: fr-FR" in rule_messages(
        green_root, "negotiation-vocabulary"
    )


@pytest.mark.parametrize("language", linter.NEGOTIATION_LANGUAGES)
def test_vocabulary_duplicate_keys(green_root: Path, strict_catalog: None, language: str) -> None:
    path = green_root / "negotiation-vocabulary" / language / "vocabulary.json"
    text = path.read_text(encoding="utf-8")
    marker = '"section.info_static"'
    index = text.find(marker)
    snippet = text[index : text.find("\n", index)]
    path.write_text(text[:index] + snippet + "\n" + text[index:], encoding="utf-8")
    assert lint_rules(green_root) == {"negotiation-vocabulary"}
    assert "duplicate key 'section.info_static'" in rule_messages(green_root, "negotiation-vocabulary")


@pytest.mark.parametrize("language", linter.NEGOTIATION_LANGUAGES)
def test_vocabulary_blank_values(green_root: Path, strict_catalog: None, language: str) -> None:
    path = green_root / "negotiation-vocabulary" / language / "vocabulary.json"
    data = vocabulary(language)
    data["section.info_static"] = "   "
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    assert lint_rules(green_root) == {"negotiation-vocabulary-blank-value"}
    assert "Blank values are not allowed: section.info_static" in rule_messages(
        green_root, "negotiation-vocabulary-blank-value"
    )


@pytest.mark.parametrize(
    "data",
    [
        ["section.info_static"],
        {"section.info_static": {"nested": "value"}},
        {"section.info_static": 42},
    ],
    ids=["top-level-list", "nested-value", "non-string-value"],
)
def test_vocabulary_must_be_flat_string_object(green_root: Path, strict_catalog: None, data: object) -> None:
    path = green_root / "negotiation-vocabulary" / "en-US" / "vocabulary.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    assert lint_rules(green_root) == {"negotiation-vocabulary"}
    assert "must be a flat JSON object" in rule_messages(green_root, "negotiation-vocabulary")


def test_vocabulary_invalid_json(green_root: Path, strict_catalog: None) -> None:
    (green_root / "negotiation-vocabulary" / "en-US" / "vocabulary.json").write_text("{broken", encoding="utf-8")
    assert lint_rules(green_root) == {"negotiation-vocabulary"}
    assert "Cannot load negotiation vocabulary" in rule_messages(green_root, "negotiation-vocabulary")


@pytest.mark.parametrize("language", linter.NEGOTIATION_LANGUAGES)
def test_vocabulary_parity_extra_key_on_one_side(green_root: Path, strict_catalog: None, language: str) -> None:
    other = "en-US" if language == "zh-CN" else "zh-CN"
    path = green_root / "negotiation-vocabulary" / language / "vocabulary.json"
    data = vocabulary(language)
    del data["slot.termination_reason"]
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    assert lint_rules(green_root) == {"negotiation-vocabulary-parity"}
    assert f"missing in {language}: slot.termination_reason" in rule_messages(
        green_root, "negotiation-vocabulary-parity"
    )
    assert f"missing in {other}" not in rule_messages(green_root, "negotiation-vocabulary-parity")


@pytest.mark.parametrize(
    "key",
    ["section.info_static", "slot.info_items", "section.target_confirm_request", "slot.feasibility_confirm_request"],
)
def test_vocabulary_missing_required_key(green_root: Path, strict_catalog: None, key: str) -> None:
    for language in linter.NEGOTIATION_LANGUAGES:
        path = green_root / "negotiation-vocabulary" / language / "vocabulary.json"
        data = vocabulary(language)
        del data[key]
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    assert lint_rules(green_root) == {"negotiation-vocabulary-key"}
    assert f"Missing required key '{key}'" in rule_messages(green_root, "negotiation-vocabulary-key")


# --- error-code contract rules (strict gate with the synthetic catalog) ---


@pytest.mark.parametrize("language", linter.NEGOTIATION_LANGUAGES)
def test_error_template_missing_file(green_root: Path, strict_catalog: None, language: str) -> None:
    (green_root / "errors" / language / "errors.json").unlink()
    rules = lint_rules(green_root)
    assert "error-template" in rules
    # An unloadable catalog also fails the strict missing-code comparison for the whole code set.
    assert "error-template-missing" in rules
    assert "Cannot load error message templates" in rule_messages(green_root, "error-template")


@pytest.mark.parametrize(
    "content",
    ["{not json", '{"content.param_missing": {"nested": "value"}}', "[]"],
    ids=["bad-json", "non-flat", "top-level-list"],
)
def test_error_template_invalid_payload(green_root: Path, strict_catalog: None, content: str) -> None:
    (green_root / "errors" / "en-US" / "errors.json").write_text(content, encoding="utf-8")
    assert "error-template" in lint_rules(green_root)


def test_error_template_duplicate_keys(green_root: Path, strict_catalog: None) -> None:
    path = green_root / "errors" / "zh-CN" / "errors.json"
    text = path.read_text(encoding="utf-8")
    marker = '"content.param_missing"'
    index = text.find(marker)
    snippet = text[index : text.find("\n", index)]
    path.write_text(text[:index] + snippet + "\n" + text[index:], encoding="utf-8")
    assert "error-template" in lint_rules(green_root)
    assert "duplicate key 'content.param_missing'" in rule_messages(green_root, "error-template")


@pytest.mark.parametrize("language", linter.NEGOTIATION_LANGUAGES)
def test_error_template_blank_value(green_root: Path, strict_catalog: None, language: str) -> None:
    data = error_templates()
    data["content.param_missing"] = "  "
    write_json(green_root / "errors" / language / "errors.json", data)
    assert lint_rules(green_root) == {"error-template-blank-value", "error-template-missing"}


def test_error_template_unknown_code(green_root: Path, strict_catalog: None) -> None:
    data = error_templates()
    data["bogus.code"] = "Never declared in the catalog."
    write_json(green_root / "errors" / "en-US" / "errors.json", data)
    assert lint_rules(green_root) == {"error-template-unknown-code"}
    assert "Template code 'bogus.code' is not in the ErrorCatalog" in rule_messages(
        green_root, "error-template-unknown-code"
    )


@pytest.mark.parametrize("language", linter.NEGOTIATION_LANGUAGES)
@pytest.mark.parametrize("code", ["content.param_missing", "input.text_too_long"])
def test_error_template_missing_catalog_code(green_root: Path, strict_catalog: None, language: str, code: str) -> None:
    data = error_templates()
    del data[code]
    write_json(green_root / "errors" / language / "errors.json", data)
    assert lint_rules(green_root) == {"error-template-missing"}
    assert f"Missing {language} message template for catalog code '{code}'" in rule_messages(
        green_root, "error-template-missing"
    )


@pytest.mark.parametrize(
    ("template_value", "expected_rules"),
    [
        ("Field {field} is missing {wrong_placeholder}.", {"error-template-placeholder"}),
        ("The request omits the required field {field}.", set()),
    ],
    ids=["undeclared-placeholder", "declared-placeholder"],
)
def test_error_template_placeholder_must_be_declared_fact(
    green_root: Path, strict_catalog: None, template_value: str, expected_rules: set[str]
) -> None:
    data = error_templates()
    data["content.param_missing"] = template_value
    write_json(green_root / "errors" / "en-US" / "errors.json", data)
    assert lint_rules(green_root) == expected_rules
    if expected_rules:
        assert "not a declared fact parameter" in rule_messages(green_root, "error-template-placeholder")


@pytest.mark.parametrize(
    ("appended_line", "token"),
    [
        ('\n{"code": "bogus.code"}\n', "bogus.code"),
        ('\n{"code": "bogus_snake"}\n', "bogus_snake"),
        ("\nUse `bogus.code` for that failure.\n", "bogus.code"),
        ("\n- **bogus.code**: description\n", "bogus.code"),
        ("\n- bogus.code: description\n", "bogus.code"),
    ],
    ids=["json-value-dotted", "json-value-snake", "backtick", "list-definition-bold", "list-definition-plain"],
)
def test_prompt_error_code_unknown_tokens(
    green_root: Path, strict_catalog: None, appended_line: str, token: str
) -> None:
    path = green_root / "prompts" / "slot_extraction" / "en-US" / "system.md"
    path.write_text(path.read_text(encoding="utf-8") + appended_line, encoding="utf-8")
    assert lint_rules(green_root) == {"prompt-error-code"}
    assert f"Error code '{token}' is not part of the ErrorCatalog" in rule_messages(green_root, "prompt-error-code")


@pytest.mark.parametrize(
    "appended_line",
    [
        '\n{"code": "e.g"}\n',
        '\n{"code": "string"}\n',
        '\n{"code": "i.e"}\n',
        "\nUse `section.info_static` here.\n",
        "\n- section.info_items: description\n",
    ],
    ids=["json-e.g", "json-string", "json-i.e", "backtick-section-prefix", "list-section-prefix"],
)
def test_prompt_code_allowlist_tokens(green_root: Path, strict_catalog: None, appended_line: str) -> None:
    path = green_root / "prompts" / "slot_extraction" / "en-US" / "system.md"
    path.write_text(path.read_text(encoding="utf-8") + appended_line, encoding="utf-8")
    assert lint_rules(green_root) == set()


def test_prompt_backtick_tokens_without_dots_are_ignored(green_root: Path, strict_catalog: None) -> None:
    path = green_root / "prompts" / "slot_extraction" / "en-US" / "system.md"
    path.write_text(path.read_text(encoding="utf-8") + "\nUse `plainword` or `snake_case` freely.\n", encoding="utf-8")
    assert lint_rules(green_root) == set()


def test_prompt_json_value_pipe_splits_into_tokens(green_root: Path, strict_catalog: None) -> None:
    path = green_root / "prompts" / "slot_extraction" / "en-US" / "system.md"
    path.write_text(
        path.read_text(encoding="utf-8") + '\n"code": "content.param_missing|bogus.code"\n', encoding="utf-8"
    )
    assert lint_rules(green_root) == {"prompt-error-code"}
    messages = rule_messages(green_root, "prompt-error-code")
    assert "Error code 'bogus.code' is not part of the ErrorCatalog" in messages
    assert "content.param_missing" not in messages


# --- prompt closed file set (D11 whitelist) ---


def test_prompts_missing_directory(green_root: Path, strict_catalog: None) -> None:
    shutil.rmtree(green_root / "prompts")
    assert lint_rules(green_root) == {"prompt-file-set"}
    assert "Missing prompts directory" in rule_messages(green_root, "prompt-file-set")


def test_prompts_unexpected_regular_file(green_root: Path, strict_catalog: None) -> None:
    (green_root / "prompts" / "README.md").write_text("not a family\n", encoding="utf-8")
    assert lint_rules(green_root) == {"prompt-file-set"}
    assert "Unexpected file in prompts directory: README.md" in rule_messages(green_root, "prompt-file-set")


def test_prompts_unexpected_family_directory(green_root: Path, strict_catalog: None) -> None:
    (green_root / "prompts" / "rogue_family").mkdir()
    assert lint_rules(green_root) == {"prompt-file-set"}
    assert "Unexpected prompt family directory: rogue_family" in rule_messages(green_root, "prompt-file-set")


@pytest.mark.parametrize("family", sorted(linter.PROMPT_FAMILIES))
def test_prompts_missing_family_directory(green_root: Path, strict_catalog: None, family: str) -> None:
    shutil.rmtree(green_root / "prompts" / family)
    assert lint_rules(green_root) == {"prompt-file-set"}
    assert f"Missing prompt family directory: {family}" in rule_messages(green_root, "prompt-file-set")


@pytest.mark.parametrize("family", sorted(linter.PROMPT_FAMILY_WHITELIST))
@pytest.mark.parametrize("language", linter.NEGOTIATION_LANGUAGES)
@pytest.mark.parametrize("file_name", linter.PROMPT_FILES)
def test_prompts_missing_file(
    green_root: Path, strict_catalog: None, family: str, language: str, file_name: str
) -> None:
    (green_root / "prompts" / family / language / file_name).unlink()
    assert lint_rules(green_root) == {"prompt-file-set"}
    assert f"Missing prompt file of family '{family}' ({language}/{file_name})" in rule_messages(
        green_root, "prompt-file-set"
    )


@pytest.mark.parametrize("family", sorted(linter.PROMPT_FAMILY_WHITELIST), ids=lambda family: family)
def test_whitelist_families_are_allowed_but_optional(green_root: Path, strict_catalog: None, family: str) -> None:
    shutil.rmtree(green_root / "prompts" / family)
    assert lint_rules(green_root) == set()


# --- load_error_catalog import contract (D6; consumed by P1's a2a_t.core.errors.catalog) ---


@pytest.mark.parametrize("attribute", ["fact_parameters", "factParameters", "facts"])
def test_load_error_catalog_reads_fact_attribute(monkeypatch: pytest.MonkeyPatch, attribute: str) -> None:
    install_catalog_module(monkeypatch, make_catalog_enum(CATALOG_FACTS, attribute))
    catalog, errors = linter.load_error_catalog()
    assert catalog == CATALOG_FACTS
    assert errors == []


def test_load_error_catalog_without_fact_attribute_declares_no_facts(monkeypatch: pytest.MonkeyPatch) -> None:
    plain = enum.Enum("ErrorCatalog", [(code, code) for code in CATALOG_FACTS])
    install_catalog_module(monkeypatch, plain)
    catalog, errors = linter.load_error_catalog()
    assert catalog == {code: [] for code in CATALOG_FACTS}
    assert errors == []


def test_load_error_catalog_unimportable_module_degrades(monkeypatch: pytest.MonkeyPatch) -> None:
    install_catalog_module(monkeypatch, None)
    catalog, errors = linter.load_error_catalog()
    assert catalog is None
    assert errors == []


def test_load_error_catalog_broken_module_stays_loud(monkeypatch: pytest.MonkeyPatch) -> None:
    def explode(name: str) -> object:
        raise RuntimeError(f"broken catalog: {name}")

    module = types.ModuleType("a2a_t.core.errors.catalog")
    module.__getattr__ = explode  # type: ignore[method-assign]
    monkeypatch.setitem(sys.modules, "a2a_t.core.errors.catalog", module)
    catalog, errors = linter.load_error_catalog()
    assert catalog is None
    assert [item.rule for item in errors] == ["error-catalog"]
    assert "Cannot import a2a_t.core.errors.catalog" in errors[0].message


def test_load_error_catalog_duplicate_code_is_loud(monkeypatch: pytest.MonkeyPatch) -> None:
    install_catalog_module(monkeypatch, [FakeMember("a.b"), FakeMember("a.b")])
    catalog, errors = linter.load_error_catalog()
    assert catalog is None
    assert [item.rule for item in errors] == ["error-catalog"]
    assert "Duplicate catalog code 'a.b'" in errors[0].message


def test_load_error_catalog_empty_catalog_is_loud(monkeypatch: pytest.MonkeyPatch) -> None:
    install_catalog_module(monkeypatch, [])
    catalog, errors = linter.load_error_catalog()
    assert catalog is None
    assert [item.rule for item in errors] == ["error-catalog"]
    assert "exposes no codes" in errors[0].message


# --- current repository state (P2: bundled resources synced to Java 1.1.0) ---


def test_bundled_resources_lint_clean_after_the_p2_sync() -> None:
    # P2 synced the bundled root byte-for-byte from the Java 1.1.0 resources (cross-repo diff,
    # excluding the D11 whitelist families and errors/), so nothing stale remains: the strict gate
    # with the real catalog must report neither errors nor warnings. The P0 temporary
    # stale-resource whitelist was deleted together with this sync instead of being extended.
    errors, warnings = linter.lint_resource_root(BUNDLED_ROOT)
    assert [str(item) for item in errors] == []
    assert warnings == []


def run_cli(resource_root: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(LINTER_PATH), "--resource-root", str(resource_root)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=str(REPO_ROOT),
        check=False,
    )


def test_cli_passes_on_a_valid_root(tmp_path: Path) -> None:
    root = build_resource_root(tmp_path / "prompt_resources")
    # The CLI run cannot inject a synthetic catalog; swap in templates that
    # satisfy the real code table so the strict gate is green on this root.
    for language in linter.NEGOTIATION_LANGUAGES:
        write_json(root / "errors" / language / "errors.json", real_catalog_error_templates())
    proc = run_cli(root)
    assert proc.returncode == 0
    assert "A2A-T template lint passed" in proc.stdout


def test_cli_fails_on_a_broken_root(tmp_path: Path) -> None:
    root = build_resource_root(tmp_path / "prompt_resources")
    shutil.rmtree(root / "negotiation-vocabulary")
    proc = run_cli(root)
    assert proc.returncode == 1
    assert "template lint failed with" in proc.stderr
    assert "[negotiation-vocabulary]" in proc.stderr


def test_cli_passes_on_the_synced_bundled_resources() -> None:
    proc = run_cli(BUNDLED_ROOT)
    # The P2-synced bundle is the CI gate's own root (CI step 'Lint bundled A2A-T templates'):
    # exit 0 and no WARN line — the real catalog is importable and no stale finding remains.
    assert proc.returncode == 0
    assert "A2A-T template lint passed" in proc.stdout
    assert proc.stderr == ""


def test_linter_requires_the_resource_root_argument() -> None:
    proc = subprocess.run(
        [sys.executable, str(LINTER_PATH)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=str(REPO_ROOT),
        check=False,
    )
    assert proc.returncode != 0
    assert "--resource-root" in proc.stderr
