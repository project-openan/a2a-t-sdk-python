#!/usr/bin/env python3
# Copyright (c) 2026 Huawei Technologies Co., Ltd.
# All Rights Reserved.
#
# SPDX-License-Identifier: Apache-2.0
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.

"""Static CI gate for A2A-T prompt templates, their slot JSON Schemas, Negotiation-T templates,
and the file-driven negotiation vocabulary.

Python port of the Java repository's ``tools/template_lint.py`` (1.1.0, 582 lines). Per D6 the error-code
catalog is not parsed from source files: the linter imports ``a2a_t.core.errors.catalog.ErrorCatalog``
directly. While that module is not importable the code-set checks degrade to a single warning (see
``CATALOG_PENDING_WARNING``); every structural check stays active so the gate never fake-greens.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TypeAlias

HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
SLOT = re.compile(r"{{\s*([^{}\s]+)\s*}}")
TASK = {
    "Task Description",
    "Task Type",
    "Task Target",
    "Task Object",
    "Task Context",
    "Constraints",
    "Expected Output",
    "Operation Type",
    "Terminology Explanation",
}
NOTIFICATION = {
    "Subscription Description",
    "Notification Topic",
    "Subscribe Condition",
    "Notification Data Format",
    "Expected Output",
}
AUTHORIZATION = {
    "Authorization Policy Operation Type",
    "Authorization Policy Operation Description",
    "Network Operation Authorization Policy List",
    "Expected Output",
    "Terminology Explanation",
}
ALIASES = {
    "任务描述": "Task Description",
    "任务类型": "Task Type",
    "任务目标": "Task Target",
    "任务对象": "Task Object",
    "目标对象": "Task Object",
    "任务上下文": "Task Context",
    "约束条件": "Constraints",
    "预期输出": "Expected Output",
    "操作类型": "Operation Type",
    "术语解释": "Terminology Explanation",
    "订阅描述": "Subscription Description",
    "通知主题": "Notification Topic",
    "订阅条件": "Subscribe Condition",
    "通知数据格式": "Notification Data Format",
    "上报通知数据格式": "Notification Data Format",
    "授权策略的操作类型": "Authorization Policy Operation Type",
    "授权策略的操作描述": "Authorization Policy Operation Description",
    "动网操作的授权策略列表": "Network Operation Authorization Policy List",
}

NEGOTIATION_TYPE_SEGMENTS = ("information-negotiation", "target-negotiation", "feasibility-negotiation")
NEGOTIATION_PHASE_SEGMENTS = ("propose", "accept-reject")
NEGOTIATION_LANGUAGES = ("zh-CN", "en-US")
NEGOTIATION_STATIC_SECTIONS = {"info_static"}
NEGOTIATION_PROFILES: dict[tuple[str, str], tuple[str, ...]] = {
    ("information-negotiation", "propose"): ("info_static", "info_items"),
    ("information-negotiation", "accept-reject"): ("info_conclusion", "info_result_content"),
    ("target-negotiation", "propose"): (
        "target",
        "target_intent",
        "target_alignment",
        "target_clarification",
        "target_confirm_request",
    ),
    ("target-negotiation", "accept-reject"): ("target_conclusion", "target_result_content"),
    ("feasibility-negotiation", "propose"): (
        "feasibility",
        "feasibility_evaluate",
        "feasibility_infeasible",
        "feasibility_confirm_request",
    ),
    ("feasibility-negotiation", "accept-reject"): ("feasibility_conclusion", "feasibility_confirm"),
}
NEGOTIATION_MARKER = re.compile(
    r"^\{\{(?P<slot>[^{}]+)\}\}(?:(?P<zh>（(?P<zh_kind>必填|选填)）)| \((?P<en_kind>required|optional)\))\s*$"
)

ERROR_TEMPLATE_LANGUAGES = ("zh-CN", "en-US")
# Error codes are layered 'domain.semantic' tokens; plain snake_case words are only trusted in "code": "..." values.
ERROR_CODE_TOKEN = re.compile(r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+$")
ERROR_TEMPLATE_PLACEHOLDER = re.compile(r"\{([a-zA-Z][a-zA-Z0-9_]*)\}")
PROMPT_JSON_CODE_VALUE = re.compile(r'"code"\s*:\s*"([^"]*)"')
PROMPT_LIST_CODE_DEFINITION = re.compile(r"^\s*[-*]\s+(?:\*\*)?([a-z][a-z0-9_.]*)(?:\*\*)?\s*[：:]\s")
PROMPT_BACKTICK_CODE = re.compile(r"`([a-z][a-z0-9_.]+)`")
# Tokens that look like error codes but are known not to be codes.
PROMPT_CODE_ALLOWLIST = {"string", "e.g", "i.e"}
PROMPT_CODE_ALLOWLIST_PREFIXES = ("section.",)

# D6: the catalog is imported, not parsed from source. P1 lands src/a2a_t/core/errors/catalog.py.
CATALOG_MODULE = "a2a_t.core.errors.catalog"
CATALOG_SOURCE = "src/a2a_t/core/errors/catalog.py"
CATALOG_FACT_ATTRIBUTES = ("fact_parameters", "factParameters", "facts")
CATALOG_PENDING_WARNING = (
    f"{CATALOG_MODULE}.ErrorCatalog is not available yet (P1 TODO: land {CATALOG_SOURCE}); "
    "error-code contract checks (error-template-missing, error-template-unknown-code, "
    "error-template-placeholder, prompt-error-code) are skipped and the code-set gate is NOT strict."
)

# Closed set of prompt families. PROMPT_FAMILY_WHITELIST covers the Python-only clarification and
# fulfillment prompts kept until the Java side evaluates them for a two-way sync (D11).
PROMPT_FAMILIES = (
    "abort_negotiation",
    "content_validation",
    "feasibility_negotiation",
    "information_negotiation",
    "negotiation_semantic_validation",
    "scenario_recognition",
    "semantic_validation",
    "slot_extraction",
    "target_negotiation",
)
PROMPT_FAMILY_WHITELIST = ("clarification_negotiation", "fulfillment_negotiation")
PROMPT_LANGUAGES = ("zh-CN", "en-US")
PROMPT_FILES = ("system.md", "user.md")


@dataclass(frozen=True)
class LintError:
    path: Path
    line: int
    rule: str
    message: str

    def __str__(self) -> str:
        return f"{self.path}:{self.line}: [{self.rule}] {self.message}"


@dataclass(frozen=True)
class Section:
    """One '## ' section of a Negotiation-T template with its body lines as (line number, text) pairs."""

    title: str
    line: int
    body: list[tuple[int, str]]


SectionShape: TypeAlias = tuple[str, bool | None, bool, int]


def error(path: Path, line: int, rule: str, message: str) -> LintError:
    return LintError(path, line, rule, message)


def canonical_heading(value: str) -> str:
    value = value.strip()
    if value in TASK | NOTIFICATION:
        return value
    match = re.fullmatch(r"(.+?)\s*\(([^)]+)\)", value)
    for candidate in (value,) if match is None else (match.group(1).strip(), match.group(2).strip()):
        if candidate in ALIASES:
            return ALIASES[candidate]
        if candidate in TASK | NOTIFICATION:
            return candidate
    return value


def schema_slots(path: Path, errors: list[LintError]) -> set[str]:
    try:
        schema = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        errors.append(error(path, 1, "schema-json", f"Cannot load JSON Schema: {exc}"))
        return set()
    properties = schema.get("properties")
    if not isinstance(properties, dict):
        errors.append(error(path, 1, "schema-properties", "Schema must define an object 'properties'."))
        return set()
    names = set(properties)
    required = schema.get("required", [])
    if not isinstance(required, list) or not all(isinstance(name, str) for name in required):
        errors.append(error(path, 1, "schema-required", "Schema 'required' must be an array of strings."))
    else:
        for name in required:
            if name not in names:
                errors.append(error(path, 1, "schema-required", f"Required slot '{name}' is not in properties."))
    return names


def lint_pair(template_path: Path, schema_path: Path) -> list[LintError]:
    errors: list[LintError] = []
    slots = schema_slots(schema_path, errors)
    try:
        lines = template_path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        return [*errors, error(template_path, 1, "template-read", f"Cannot read template: {exc}")]
    headings: list[tuple[int, str]] = []
    placeholders: list[tuple[int, str]] = []
    for line_no, line in enumerate(lines, 1):
        match = HEADING.match(line)
        if match:
            if len(match.group(1)) == 2:
                headings.append((line_no, canonical_heading(match.group(2))))
        placeholders.extend((line_no, name) for name in SLOT.findall(line))
    names = [name for _, name in headings]
    if "Authorization Policy Operation Type" in names:
        profile = "authorization"
        allowed = AUTHORIZATION
        required = {"Authorization Policy Operation Type"}
    elif "Subscription Description" in names:
        profile = "notification"
        allowed = NOTIFICATION
        required = {"Subscription Description"}
    else:
        profile = "task"
        allowed = TASK
        required = {"Task Description"}
    if not headings:
        errors.append(
            error(template_path, 1, "instruction-missing", "Template must contain L0 instructions marked with '##'.")
        )
    for line_no, name in headings:
        if name not in allowed:
            errors.append(
                error(template_path, line_no, "instruction-name", f"'{name}' is not valid for the {profile} profile.")
            )
    for name in sorted(required - set(names)):
        errors.append(error(template_path, 1, "instruction-required", f"Missing required instruction '{name}'."))
    for name in set(names):
        occurrences = [line_no for line_no, value in headings if value == name]
        if len(occurrences) > 1:
            errors.append(
                error(template_path, occurrences[1], "instruction-duplicate", f"Instruction '{name}' is repeated.")
            )
    used: set[str] = set()
    for line_no, name in placeholders:
        if name in used:
            continue
        used.add(name)
        if name not in slots:
            errors.append(
                error(
                    template_path,
                    line_no,
                    "slot-undefined",
                    f"Placeholder '{{{{{name}}}}}' is missing from {schema_path.name}.",
                )
            )
    for name in sorted(slots - used):
        errors.append(error(schema_path, 1, "slot-unused", f"Schema slot '{name}' has no template placeholder."))
    return errors


def _reject_duplicate_keys(pairs: list[tuple[str, str]]) -> dict[str, str]:
    seen: set[str] = set()
    for key, _ in pairs:
        if key in seen:
            raise ValueError(f"duplicate key '{key}'")
        seen.add(key)
    return dict(pairs)


def load_negotiation_vocabularies(resource_root: Path) -> tuple[dict[str, dict[str, str]], list[LintError]]:
    """Loads negotiation-vocabulary/{lang}/vocabulary.json for both languages from the resource root.

    The vocabulary files are the single source of the Negotiation-T section titles and slot marker names; the tables
    are no longer hardcoded here. Both language files must exist, parse into a flat string-to-string object without
    duplicate keys or blank values, expose identical key sets, and carry every section.*/slot.* key the template
    profiles reference.
    """
    vocabulary_root = resource_root / "negotiation-vocabulary"
    if not vocabulary_root.is_dir():
        return {}, [error(vocabulary_root, 1, "negotiation-vocabulary", "Missing negotiation-vocabulary directory.")]
    errors: list[LintError] = []
    for entry in sorted(vocabulary_root.iterdir()):
        if entry.is_dir() and entry.name not in NEGOTIATION_LANGUAGES:
            errors.append(
                error(
                    entry,
                    1,
                    "negotiation-vocabulary",
                    f"Unexpected negotiation vocabulary language directory: {entry.name}.",
                )
            )
    vocabularies: dict[str, dict[str, str]] = {}
    for language in NEGOTIATION_LANGUAGES:
        path = vocabulary_root / language / "vocabulary.json"
        try:
            data = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_reject_duplicate_keys)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
            errors.append(error(path, 1, "negotiation-vocabulary", f"Cannot load negotiation vocabulary: {exc}"))
            continue
        if not isinstance(data, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in data.items()):
            errors.append(
                error(
                    path, 1, "negotiation-vocabulary", "Vocabulary must be a flat JSON object mapping keys to strings."
                )
            )
            continue
        blank_keys = sorted(key for key, value in data.items() if not value.strip())
        if blank_keys:
            errors.append(
                error(
                    path,
                    1,
                    "negotiation-vocabulary-blank-value",
                    f"Blank values are not allowed: {', '.join(blank_keys)}.",
                )
            )
            continue
        vocabularies[language] = data
    if len(vocabularies) == len(NEGOTIATION_LANGUAGES):
        zh_keys, en_keys = set(vocabularies["zh-CN"]), set(vocabularies["en-US"])
        if zh_keys != en_keys:
            details = []
            missing_in_en = sorted(zh_keys - en_keys)
            missing_in_zh = sorted(en_keys - zh_keys)
            if missing_in_en:
                details.append("missing in en-US: " + ", ".join(missing_in_en))
            if missing_in_zh:
                details.append("missing in zh-CN: " + ", ".join(missing_in_zh))
            errors.append(
                error(
                    vocabulary_root / "en-US" / "vocabulary.json",
                    1,
                    "negotiation-vocabulary-parity",
                    f"zh-CN and en-US vocabulary key sets differ ({'; '.join(details)}).",
                )
            )
    required_section_keys = set(NEGOTIATION_STATIC_SECTIONS)
    for keys in NEGOTIATION_PROFILES.values():
        required_section_keys.update(keys)
    required_slot_keys = required_section_keys - NEGOTIATION_STATIC_SECTIONS
    for language in list(vocabularies):
        path = vocabulary_root / language / "vocabulary.json"
        vocabulary = vocabularies[language]
        usable = True
        for section_key in sorted(required_section_keys):
            if f"section.{section_key}" not in vocabulary:
                errors.append(
                    error(path, 1, "negotiation-vocabulary-key", f"Missing required key 'section.{section_key}'.")
                )
                usable = False
        for slot_key in sorted(required_slot_keys):
            if f"slot.{slot_key}" not in vocabulary:
                errors.append(error(path, 1, "negotiation-vocabulary-key", f"Missing required key 'slot.{slot_key}'."))
                usable = False
        if not usable:
            del vocabularies[language]
    return vocabularies, errors


def negotiation_title_lookup(vocabularies: dict[str, dict[str, str]]) -> dict[str, tuple[str, str]]:
    lookup: dict[str, tuple[str, str]] = {}
    for language in NEGOTIATION_LANGUAGES:
        for key, value in vocabularies.get(language, {}).items():
            if key.startswith("section."):
                lookup.setdefault(value, (key[len("section.") :], language))
    return lookup


def negotiation_section_title(vocabulary: dict[str, str], key: str) -> str:
    return vocabulary[f"section.{key}"]


def negotiation_slot_name(vocabulary: dict[str, str], key: str) -> str:
    return vocabulary[f"slot.{key}"]


def negotiation_requirements_label(language: str) -> str:
    return "要求：" if language == "zh-CN" else "Requirement:"


def parse_negotiation_sections(lines: list[str]) -> list[Section]:
    sections: list[Section] = []
    current: Section | None = None
    for line_no, line in enumerate(lines, 1):
        heading = re.match(r"^## (.+?)\s*$", line)
        if heading:
            current = Section(title=heading.group(1), line=line_no, body=[])
            sections.append(current)
        elif current is not None:
            current.body.append((line_no, line))
    return sections


def lint_negotiation_file(
    path: Path,
    language: str,
    type_segment: str,
    phase_segment: str,
    vocabulary: dict[str, str],
    title_lookup: dict[str, tuple[str, str]],
) -> tuple[list[LintError], list[SectionShape] | None]:
    errors: list[LintError] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        return [error(path, 1, "negotiation-template-read", f"Cannot read template: {exc}")], None
    profile = NEGOTIATION_PROFILES[(type_segment, phase_segment)]
    requirements_label = negotiation_requirements_label(language)
    first_line = next((line for line in lines if line.strip()), "")
    if first_line.startswith("<!--"):
        if not first_line.rstrip().endswith("-->"):
            errors.append(
                error(path, 1, "negotiation-comment", "HTML comment header must be well-formed '<!-- ... -->'.")
            )
    sections = parse_negotiation_sections(lines)
    if not sections:
        errors.append(
            error(path, 1, "negotiation-section-missing", "Template must contain sections marked with '## '.")
        )
        return errors, None
    shape: list[SectionShape] = []
    seen: set[str] = set()
    for section in sections:
        title, line_no, body = section.title, section.line, section.body
        lookup = title_lookup.get(title)
        if lookup is None:
            errors.append(
                error(
                    path,
                    line_no,
                    "negotiation-section-name",
                    f"'{title}' is not a recognized Negotiation-T section title.",
                )
            )
            continue
        key, title_language = lookup
        if key not in profile:
            errors.append(
                error(
                    path,
                    line_no,
                    "negotiation-section-name",
                    f"'{title}' is not valid for the {type_segment}/{phase_segment} Negotiation-T template.",
                )
            )
            continue
        if title_language != language:
            errors.append(
                error(
                    path,
                    line_no,
                    "negotiation-section-name",
                    f"Section title '{title}' is not the {language} title of section '{key}'.",
                )
            )
            continue
        if key in seen:
            errors.append(error(path, line_no, "negotiation-section-duplicate", f"Section '{title}' is repeated."))
            continue
        seen.add(key)
        if key in NEGOTIATION_STATIC_SECTIONS:
            if any(NEGOTIATION_MARKER.match(text) for _, text in body):
                errors.append(
                    error(
                        path,
                        line_no,
                        "negotiation-slot-structure",
                        f"Static section '{title}' must not contain a slot line.",
                    )
                )
            if any(text.rstrip() == requirements_label for _, text in body):
                errors.append(
                    error(
                        path,
                        line_no,
                        "negotiation-requirements",
                        f"Static section '{title}' must not contain a '{requirements_label}' line.",
                    )
                )
            shape.append((key, None, True, line_no))
            continue
        marker_line = next(((no, text) for no, text in body if text.strip()), None)
        marker = NEGOTIATION_MARKER.match(marker_line[1]) if marker_line else None
        if marker is None:
            errors.append(
                error(
                    path,
                    line_no,
                    "negotiation-slot-structure",
                    f"Slot section '{title}' must be followed by a standalone slot line "
                    f"such as '{{{{slot}}}}（必填）'.",
                )
            )
            shape.append((key, None, True, line_no))
            continue
        marker_line_no = marker_line[0]
        marker_language = "zh-CN" if marker.group("zh") is not None else "en-US"
        required = (
            marker.group("zh_kind") == "必填" if marker_language == "zh-CN" else marker.group("en_kind") == "required"
        )
        if marker_language != language:
            errors.append(
                error(
                    path,
                    marker_line_no,
                    "negotiation-slot-marker",
                    f"Slot marker in {language} template must use {language} punctuation "
                    f"(full-width for zh-CN, ' (required)'/' (optional)' for en-US).",
                )
            )
        expected_slot = negotiation_slot_name(vocabulary, key)
        if marker.group("slot") != expected_slot:
            errors.append(
                error(
                    path,
                    marker_line_no,
                    "negotiation-slot-name",
                    f"Slot name '{{{{{marker.group('slot')}}}}}' must be '{expected_slot}'.",
                )
            )
        if not any(text.rstrip() == requirements_label for _, text in body):
            errors.append(
                error(
                    path,
                    line_no,
                    "negotiation-requirements",
                    f"Slot section '{title}' must contain a '{requirements_label}' line.",
                )
            )
        shape.append((key, required, marker.group("slot") == expected_slot, line_no))
    for key in profile:
        if key not in seen:
            errors.append(
                error(
                    path,
                    1,
                    "negotiation-section-missing",
                    f"Missing required section '{negotiation_section_title(vocabulary, key)}'.",
                )
            )
    return errors, shape


def lint_negotiation_alignment(
    zh_path: Path, en_path: Path, zh_shape: list[SectionShape], en_shape: list[SectionShape]
) -> list[LintError]:
    errors: list[LintError] = []
    if len(zh_shape) != len(en_shape):
        errors.append(
            error(
                en_path,
                1,
                "negotiation-alignment",
                f"Section count differs from zh-CN counterpart: {len(en_shape)} vs {len(zh_shape)}.",
            )
        )
    for index, (zh_entry, en_entry) in enumerate(zip(zh_shape, en_shape)):
        zh_key, zh_required, _, _ = zh_entry
        en_key, en_required, _, en_line = en_entry
        differences = []
        if zh_key != en_key:
            differences.append(f"section '{en_key}' vs zh-CN '{zh_key}'")
        if zh_required != en_required:
            if en_required is None:
                differences.append("missing slot marker (zh-CN has one)")
            elif zh_required is None:
                differences.append("has slot marker while zh-CN does not")
            else:
                differences.append(
                    f"marker {'required' if en_required else 'optional'} "
                    f"vs zh-CN {'required' if zh_required else 'optional'}"
                )
        if differences:
            errors.append(
                error(
                    en_path,
                    en_line,
                    "negotiation-alignment",
                    f"Section {index + 1} diverges from zh-CN template ({'; '.join(differences)}).",
                )
            )
    return errors


def lint_negotiation(templates_dir: Path, vocabularies: dict[str, dict[str, str]]) -> list[LintError]:
    negotiation_root = templates_dir / "Negotiation-T"
    if not negotiation_root.is_dir():
        return [error(negotiation_root, 1, "negotiation-file-set", "Missing Negotiation-T templates directory.")]
    expected = {
        Path(type_segment) / phase_segment / "v1" / language / "template.md"
        for type_segment in NEGOTIATION_TYPE_SEGMENTS
        for phase_segment in NEGOTIATION_PHASE_SEGMENTS
        for language in NEGOTIATION_LANGUAGES
    } | {Path("common") / "abort" / "v1" / language / "template.md" for language in NEGOTIATION_LANGUAGES}
    errors: list[LintError] = []
    shapes: dict[tuple[str, str, str], list[SectionShape] | None] = {}
    found: set[Path] = set()
    title_lookup = negotiation_title_lookup(vocabularies)
    for path in sorted(negotiation_root.rglob("template.md")):
        relative = path.relative_to(negotiation_root)
        if relative not in expected:
            errors.append(
                error(path, 1, "negotiation-file-set", f"Unexpected Negotiation-T template location: {relative}")
            )
            continue
        found.add(relative)
        parts = relative.parts
        if parts[0] == "common":
            continue
        type_segment, phase_segment, _, language, _ = parts
        vocabulary = vocabularies.get(language)
        if vocabulary is None:
            # The vocabulary errors were already reported; without a usable vocabulary the per-file checks cannot run.
            continue
        file_errors, shape = lint_negotiation_file(
            path, language, type_segment, phase_segment, vocabulary, title_lookup
        )
        errors.extend(file_errors)
        shapes[(type_segment, phase_segment, language)] = shape
    for relative in sorted(expected - found):
        errors.append(
            error(
                negotiation_root / relative,
                1,
                "negotiation-file-set",
                f"Missing Negotiation-T template: {negotiation_root / relative}",
            )
        )
    for type_segment in NEGOTIATION_TYPE_SEGMENTS:
        for phase_segment in NEGOTIATION_PHASE_SEGMENTS:
            zh_shape = shapes.get((type_segment, phase_segment, "zh-CN"))
            en_shape = shapes.get((type_segment, phase_segment, "en-US"))
            if zh_shape is None or en_shape is None:
                continue
            base = negotiation_root / type_segment / phase_segment / "v1"
            errors.extend(
                lint_negotiation_alignment(
                    base / "zh-CN" / "template.md", base / "en-US" / "template.md", zh_shape, en_shape
                )
            )
    return errors


def load_error_catalog() -> tuple[dict[str, list[str]] | None, list[LintError]]:
    """Imports the runtime ErrorCatalog (D6) and derives an ordered {code: fact parameter names} table.

    Returns ``(None, [])`` while P1 has not landed the catalog module yet, so the caller can skip the
    code-set checks with a single warning. Any other import failure is reported as an error instead,
    so a broken catalog keeps the gate red instead of silently degrading.
    """
    try:
        from a2a_t.core.errors.catalog import ErrorCatalog
    except ImportError:
        return None, []
    except Exception as exc:  # pragma: no cover - defensive: a broken catalog must stay loud, not crash CI
        return None, [error(Path(CATALOG_SOURCE), 1, "error-catalog", f"Cannot import {CATALOG_MODULE}: {exc}")]
    catalog: dict[str, list[str]] = {}
    for member in ErrorCatalog:
        code = str(getattr(member, "value", member))
        facts: list[str] = []
        for attribute in CATALOG_FACT_ATTRIBUTES:
            raw = getattr(member, attribute, None)
            if isinstance(raw, (list, tuple)) and all(isinstance(item, str) for item in raw):
                facts = list(raw)
                break
        if code in catalog:
            return None, [error(Path(CATALOG_SOURCE), 1, "error-catalog", f"Duplicate catalog code '{code}'.")]
        catalog[code] = facts
    if not catalog:
        return None, [error(Path(CATALOG_SOURCE), 1, "error-catalog", "The imported ErrorCatalog exposes no codes.")]
    return catalog, []


def load_error_templates(resource_root: Path, language: str) -> tuple[dict[str, str], list[LintError]]:
    """Loads errors/{language}/errors.json as a flat code-to-template object."""
    path = resource_root / "errors" / language / "errors.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_reject_duplicate_keys)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        return {}, [error(path, 1, "error-template", f"Cannot load error message templates: {exc}")]
    if not isinstance(data, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in data.items()):
        return {}, [
            error(
                path,
                1,
                "error-template",
                "Error message templates must be a flat JSON object mapping codes to strings.",
            )
        ]
    blank = sorted(key for key, value in data.items() if not value.strip())
    if blank:
        return {}, [
            error(path, 1, "error-template-blank-value", f"Blank templates are not allowed: {', '.join(blank)}.")
        ]
    return data, []


def allowed_prompt_code_token(token: str) -> bool:
    """Returns whether one token is a known non-code token (allowlist with optional prefixes)."""
    return token in PROMPT_CODE_ALLOWLIST or token.startswith(PROMPT_CODE_ALLOWLIST_PREFIXES)


def lint_prompt_error_codes(prompts_dir: Path, catalog: set[str]) -> list[LintError]:
    """Verifies every error code token quoted in the prompt resources is part of the ErrorCatalog.

    Tokens are collected from three code-bearing positions: values of "code": "..." JSON keys (snake_case trusted
    there), backtick-quoted tokens, and list-leading definitions such as '- content.param_missing: ...' (dotted
    tokens only in the latter two, because snake_case words in those positions are usually field names).
    """
    errors: list[LintError] = []
    for path in sorted(prompts_dir.rglob("*.md")):
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError as exc:
            errors.append(error(path, 1, "prompt-error-code", f"Cannot read prompt: {exc}"))
            continue
        tokens: dict[str, int] = {}
        for line_no, line in enumerate(lines, 1):
            for value in PROMPT_JSON_CODE_VALUE.findall(line):
                for raw_token in value.split("|"):
                    token = raw_token.strip()
                    if token and (ERROR_CODE_TOKEN.fullmatch(token) or re.fullmatch(r"[a-z][a-z0-9_]*", token)):
                        tokens.setdefault(token, line_no)
            match = PROMPT_LIST_CODE_DEFINITION.match(line)
            if match and "." in match.group(1):
                tokens.setdefault(match.group(1), line_no)
            for token in PROMPT_BACKTICK_CODE.findall(line):
                if "." in token:
                    tokens.setdefault(token, line_no)
        for token, line_no in sorted(tokens.items()):
            if token not in catalog and not allowed_prompt_code_token(token):
                errors.append(
                    error(path, line_no, "prompt-error-code", f"Error code '{token}' is not part of the ErrorCatalog.")
                )
    return errors


def lint_prompt_file_set(prompts_dir: Path) -> list[LintError]:
    """Verifies the prompts directory is the closed set of known prompt families (D11 whitelist included).

    Java 1.1.0 only checks that the prompts directory exists; the closed-file-set enforcement is a Python-only
    addition so the two Python-specific families are an explicit, reviewed part of the contract.
    """
    if not prompts_dir.is_dir():
        return [error(prompts_dir, 1, "prompt-file-set", "Missing prompts directory.")]
    allowed = set(PROMPT_FAMILIES) | set(PROMPT_FAMILY_WHITELIST)
    errors: list[LintError] = []
    present: set[str] = set()
    for entry in sorted(prompts_dir.iterdir()):
        if not entry.is_dir():
            errors.append(error(entry, 1, "prompt-file-set", f"Unexpected file in prompts directory: {entry.name}"))
            continue
        present.add(entry.name)
        if entry.name not in allowed:
            errors.append(error(entry, 1, "prompt-file-set", f"Unexpected prompt family directory: {entry.name}."))
            continue
        for language in PROMPT_LANGUAGES:
            for file_name in PROMPT_FILES:
                if not (entry / language / file_name).is_file():
                    errors.append(
                        error(
                            entry / language / file_name,
                            1,
                            "prompt-file-set",
                            f"Missing prompt file of family '{entry.name}' ({language}/{file_name}).",
                        )
                    )
    for family in sorted(set(PROMPT_FAMILIES) - present):
        errors.append(error(prompts_dir / family, 1, "prompt-file-set", f"Missing prompt family directory: {family}."))
    return errors


def lint_error_codes(resource_root: Path, catalog: dict[str, list[str]] | None) -> list[LintError]:
    """Verifies the catalog, its message templates, and the prompt code sets stay consistent.

    ``catalog is None`` means the P1 ErrorCatalog is not importable yet: the structural template checks still
    run, while every code-set comparison is skipped (see CATALOG_PENDING_WARNING).
    """
    errors: list[LintError] = []
    templates: dict[str, dict[str, str]] = {}
    for language in ERROR_TEMPLATE_LANGUAGES:
        data, template_errors = load_error_templates(resource_root, language)
        errors.extend(template_errors)
        templates[language] = data
        if catalog is not None:
            for code in sorted(set(data) - set(catalog)):
                path = resource_root / "errors" / language / "errors.json"
                errors.append(
                    error(path, 1, "error-template-unknown-code", f"Template code '{code}' is not in the ErrorCatalog.")
                )
    if catalog is None:
        return errors
    for language, data in templates.items():
        path = resource_root / "errors" / language / "errors.json"
        for code in sorted(set(catalog) - set(data)):
            errors.append(
                error(
                    path, 1, "error-template-missing", f"Missing {language} message template for catalog code '{code}'."
                )
            )
        for code, facts in catalog.items():
            template = data.get(code)
            if template is None:
                continue
            for placeholder in sorted(set(ERROR_TEMPLATE_PLACEHOLDER.findall(template))):
                if placeholder not in facts:
                    errors.append(
                        error(
                            path,
                            1,
                            "error-template-placeholder",
                            f"Placeholder '{{{placeholder}}}' of code '{code}' ({language}) "
                            f"is not a declared fact parameter.",
                        )
                    )
    prompts_dir = resource_root / "prompts"
    if prompts_dir.is_dir():
        errors.extend(lint_prompt_error_codes(prompts_dir, set(catalog)))
    return errors


def lint_resource_root(resource_root: Path) -> tuple[list[LintError], list[str]]:
    """Lints one prompt_resources root and returns ``(errors, warnings)``.

    Warnings never change the exit code; the current one is the pending catalog degradation emitted
    while ``a2a_t.core.errors.catalog`` is not importable.
    """
    warnings: list[str] = []
    templates, slots = resource_root / "templates", resource_root / "slots"
    if not templates.is_dir():
        return [error(templates, 1, "resource-root", "Missing templates directory.")], warnings
    if not slots.is_dir():
        return [error(slots, 1, "resource-root", "Missing slots directory.")], warnings
    errors: list[LintError] = []
    # Layouts: <type>/network-layer/<scenario>/v1/<lang> for Task-T/Notification-T,
    # and <type>/<scenario>/v1/<lang> for Authorization-T.
    template_globs = ("*/network-layer/*/v1/*/template.md", "*/*/v1/*/template.md")
    for pattern in template_globs:
        for template_path in sorted(templates.glob(pattern)):
            schema_path = slots / template_path.relative_to(templates).parent / "slot.json"
            if schema_path.is_file():
                errors.extend(lint_pair(template_path, schema_path))
    for pattern in template_globs:
        for schema_path in sorted(slots.glob(pattern.replace("template.md", "slot.json"))):
            template_path = templates / schema_path.relative_to(slots).parent / "template.md"
            if not template_path.is_file():
                errors.append(error(schema_path, 1, "template-missing", f"Missing paired template: {template_path}"))
    vocabularies, vocabulary_errors = load_negotiation_vocabularies(resource_root)
    errors.extend(vocabulary_errors)
    errors.extend(lint_negotiation(templates, vocabularies))
    catalog, catalog_errors = load_error_catalog()
    errors.extend(catalog_errors)
    if catalog is None and not catalog_errors:
        warnings.append(CATALOG_PENDING_WARNING)
    errors.extend(lint_error_codes(resource_root, catalog))
    errors.extend(lint_prompt_file_set(resource_root / "prompts"))
    return errors, warnings


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--resource-root", type=Path, required=True, help="Path to the prompt_resources root.")
    arguments = parser.parse_args()
    errors, warnings = lint_resource_root(arguments.resource_root)
    for warning in warnings:
        print(f"WARN: {warning}", file=sys.stderr)
    for lint_error in errors:
        print(lint_error, file=sys.stderr)
    if errors:
        print(f"A2A-T template lint failed with {len(errors)} error(s).", file=sys.stderr)
        return 1
    print(f"A2A-T template lint passed: {arguments.resource_root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
