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

"""Strict loader of the negotiation test corpus.

Port of the Java ``a2a-t-corpus`` ``NegotiationCaseLoader``: the corpus lives under one root
directory (the shipped one is ``tests/resources/negotiation-cases``), case files and scenario
files are JSON arrays of records, and the ``shared/`` directory carries the two shared reference
files ``llm-responses.json`` (named payload texts, addressed through the ``responses/`` prefix)
and ``schemas.json`` (named JSON Schema variants, addressed through the ``schemas/`` prefix).
The ``live/`` subdirectory carries the live-LLM family bound as
:class:`~tests.corpus.models.LiveCase`; live records never mix into the offline cases list.

Loading is two-layered and fails fast — before any case runs — on every format violation (D21):

* every record is first validated against ``corpus-schema.json`` with :mod:`jsonschema`, so an
  unknown key, a wrong JSON type, an unknown enum value or an incomplete expectation block is an
  error, never a silently ignored typo (the schema layer is skipped when the root carries no
  usable ``corpus-schema.json``, for hand-built inline corpora of the engine tests);
* the record is then bound into the strict raw dataclasses of this module, where an unknown
  constructor keyword or a wrong field shape fails as well — the second layer stands in for
  Java's strict Jackson ``FAIL_ON_UNKNOWN_PROPERTIES`` binding.

Every error names the corpus file, the offending record id and the JSON path of the defect:
``from-text/retry.json [FT-RETRY-01] $[0].expect.expection: ...``. Beyond the two strict layers
the loader rejects dangling, out-of-scope, nested or circular ``$ref`` references, duplicate
record ids (before and after the language expansion), incomplete expectation blocks, scenario
steps that are not numbered consecutively from 1, and the live-family phase-1 restrictions
(the ``LIVE-`` id prefix, exactly zh-CN, the two task APIs, an inline prompt text only).

On success every record is expanded once per entry of its ``languages`` array, the expanded id
appending ``/<language>``, and references are resolved into literal payload texts and schema
nodes, so the later case engine never sees a reference.
"""

from __future__ import annotations

import copy
import json
import re
import types
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Union, get_args, get_origin, get_type_hints

from jsonschema.protocols import Validator
from jsonschema.validators import validator_for

from tests.corpus.models import (
    ContextSpec,
    Expectation,
    LiveCase,
    LiveExpectation,
    LlmFailMarker,
    LlmScript,
    LlmScriptStep,
    LoadedCorpus,
    NegotiationApi,
    NegotiationCase,
    PromptSource,
    ScenarioCase,
)

__all__ = [
    "CorpusLoadException",
    "LIVE_APIS",
    "LIVE_DEFAULT_MAX_LLM_CALLS",
    "LIVE_DIR_PREFIX",
    "LIVE_ID_PREFIX",
    "LIVE_LANGUAGES",
    "RESPONSES_PREFIX",
    "SCHEMAS_PREFIX",
    "SUPPORTED_LANGUAGES",
    "SUPPORTED_PRIORITIES",
    "SUPPORTED_TERMINAL_CONDITIONS",
    "load",
]

SUPPORTED_LANGUAGES = frozenset({"zh-CN", "en-US"})
SUPPORTED_PRIORITIES = frozenset({"P0", "P1", "P2"})
SUPPORTED_TERMINAL_CONDITIONS = frozenset({"accept", "reject", "abort", "exhausted"})

RESPONSES_PREFIX = "responses/"
SCHEMAS_PREFIX = "schemas/"

RESPONSES_FILE = "shared/llm-responses.json"
SCHEMAS_FILE = "shared/schemas.json"

#: Source-file prefix that routes a record file into the live-LLM family bindings.
LIVE_DIR_PREFIX = "live/"

#: The id prefix every live record must carry, so the two families cannot collide on ids.
LIVE_ID_PREFIX = "LIVE-"

#: Live phase 1 covers zh-CN only; the language expansion itself stays generic.
LIVE_LANGUAGES = ("zh-CN",)

#: Live phase 1 covers the two TASK APIs.
LIVE_APIS = frozenset(
    {NegotiationApi.GENERATE_TASK_PROMPT_FROM_TEXT, NegotiationApi.VALIDATE_TASK_PROMPT_AND_DATA_FILLING}
)

#: Default of the live LLM call upper bound when a live record omits it.
LIVE_DEFAULT_MAX_LLM_CALLS = 4

#: Name of the corpus format definition sitting next to the records; skipped by the record walk.
SCHEMA_FILE = "corpus-schema.json"

_KNOWN_INJECT_HOOKS = ("failingTemplateLoader", "failingSemanticValidator")

_UNEXPECTED_KEYWORD = re.compile(r"unexpected keyword argument '([^']+)'")


class CorpusLoadException(RuntimeError):
    """Signals a defect in the negotiation test corpus.

    An unknown key, a schema violation, a dangling or circular ``$ref``, a duplicate id, an
    incomplete expectation block or any other format violation. The loader fails fast — before
    any case runs — and the message always names the offending corpus file, the offending record
    id (when already readable) and the JSON path of the defect.
    """


# ------------------------------------------------------------------ raw JSON records
#
# The private binding targets of the strict second layer: one dataclass per JSON record shape,
# every field optional except the record id, mirroring the raw record tree of the Java loader.
# An unknown key fails the dataclass constructor, a wrong JSON type fails the shape check of
# :func:`_bind` — together they stand in for the strict Jackson binding of the Java loader.


@dataclass(frozen=True, slots=True)
class RawCase:
    id: str
    api: str | None = None
    languages: list[str] | None = None
    priority: str | None = None
    tags: list[str] | None = None
    summary: str | None = None
    context: dict[str, Any] | None = None
    templateUri: str | None = None
    input: dict[str, Any] | None = None
    llm: dict[str, Any] | None = None
    prompt: dict[str, Any] | None = None
    schema: Any = None
    inject: str | None = None
    expect: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class RawLiveCase:
    id: str
    api: str | None = None
    languages: list[str] | None = None
    priority: str | None = None
    tags: list[str] | None = None
    summary: str | None = None
    context: dict[str, Any] | None = None
    templateUri: str | None = None
    input: dict[str, Any] | None = None
    prompt: dict[str, Any] | None = None
    schema: Any = None
    expect: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class RawLiveInput:
    text: dict[str, str] | None = None


@dataclass(frozen=True, slots=True)
class RawLiveExpect:
    success: bool | None = None
    scenarioCode: str | None = None
    paramsContains: Any = None
    paramsAbsent: list[str] | None = None
    promptTextContains: list[str] | None = None
    maxLlmCalls: int | None = None


@dataclass(frozen=True, slots=True)
class RawScenario:
    id: str
    summary: str | None = None
    languages: list[str] | None = None
    roles: list[str] | None = None
    rolesDesc: dict[str, str] | None = None
    steps: list[dict[str, Any]] | None = None
    expectFlow: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class RawStep:
    step: int | None = None
    role: str | None = None
    api: str | None = None
    context: dict[str, Any] | None = None
    templateUri: str | None = None
    input: dict[str, Any] | None = None
    llm: dict[str, Any] | None = None
    prompt: dict[str, Any] | None = None
    schema: Any = None
    inject: str | None = None
    expect: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class RawContext:
    id: str | None = None
    round: int | None = None
    maxRounds: int | None = None


@dataclass(frozen=True, slots=True)
class RawInput:
    text: dict[str, str] | None = None
    data: Any = None


@dataclass(frozen=True, slots=True)
class RawLlm:
    maxAttempts: int | None = None
    script: list[Any] | None = None


@dataclass(frozen=True, slots=True)
class RawPrompt:
    golden: str | None = None
    text: str | None = None
    fromStep: int | None = None


@dataclass(frozen=True, slots=True)
class RawExpect:
    outcome: str | None = None
    exception: str | None = None
    code: str | None = None
    messageContains: list[str] | None = None
    slotErrors: list[dict[str, Any]] | None = None
    llmCalls: int | None = None
    promptTextEqualsGolden: str | None = None
    metadata: dict[str, Any] | None = None
    params: Any = None
    contracts: list[str] | None = None
    differential: bool | None = None
    promptTextContains: list[str] | None = None
    missingParams: list[str] | None = None
    paramsFromStep: int | None = None


@dataclass(frozen=True, slots=True)
class RawSlotError:
    slot: str | None = None
    code: str | None = None


@dataclass(frozen=True, slots=True)
class RawMetadata:
    templateUriEcho: str | None = None
    contextEcho: bool | None = None


@dataclass(frozen=True, slots=True)
class RawExpectFlow:
    terminalCondition: str | None = None
    roundsUsed: int | None = None
    distinctMessages: bool | None = None
    missingParamsFilled: int | None = None


@dataclass(frozen=True, slots=True)
class _SharedRefs:
    """The two shared reference files, already validated."""

    responses: dict[str, str]
    schemas: dict[str, Any]


@dataclass(frozen=True, slots=True)
class _ResolvedFields:
    """One record (or scenario step) with every reference already resolved."""

    api: NegotiationApi
    context: ContextSpec | None
    template_uri: str | None
    input_text: dict[str, str]
    input_data: dict[str, Any] | None
    llm: LlmScript | None
    prompt: PromptSource.Golden | PromptSource.Text | PromptSource.FromStep | None
    schema: dict[str, Any] | None
    inject: str | None
    expect: Expectation


@dataclass(slots=True)
class _LoadState:
    """Mutable bookkeeping of one :func:`load` run."""

    shared: _SharedRefs
    validators: dict[str, Validator] | None
    seen_base_ids: dict[str, str] = field(default_factory=dict)
    seen_expanded_ids: dict[str, str] = field(default_factory=dict)
    cases: list[NegotiationCase] = field(default_factory=list)
    scenarios: list[ScenarioCase] = field(default_factory=list)
    live_cases: list[LiveCase] = field(default_factory=list)


# ------------------------------------------------------------------ public entry point


def load(corpus_root: Path | str) -> LoadedCorpus:
    """Load the whole corpus from a corpus root directory.

    Every ``.json`` file is loaded except ``corpus-schema.json`` and the ``shared/`` reference
    files; records are validated against the corpus schema (when the root carries a usable one),
    bound strictly and expanded per language.

    :param corpus_root: existing corpus root directory
    :returns: the loaded corpus with all cases, scenarios and live cases expanded per language
    :raises CorpusLoadException: when any corpus record violates the format, or the root is not a
        directory
    """
    root = Path(corpus_root)
    if not root.is_dir():
        raise CorpusLoadException(f"The corpus root is not an existing directory: {root}")
    validators = _load_validators(root)
    state = _LoadState(shared=_load_shared(root / "shared", validators), validators=validators)
    for file in _list_corpus_files(root):
        _parse_file(state, file.relative_to(root).as_posix(), file)
    return LoadedCorpus(
        root=root,
        cases=state.cases,
        scenarios=state.scenarios,
        live_cases=state.live_cases,
        shared_responses=state.shared.responses,
        shared_schemas=state.shared.schemas,
    )


# ------------------------------------------------------------------ corpus schema layer

#: Sub-schema address of every corpus file kind inside ``corpus-schema.json``.
_KIND_REFS = {
    "case": "#/$defs/case",
    "scenario": "#/$defs/scenario",
    "live": "#/$defs/liveCase",
    "responses": "#/$defs/sharedResponsesFile",
    "schemas": "#/$defs/sharedSchemasFile",
}


def _load_validators(root: Path) -> dict[str, Validator] | None:
    """Build the per-kind schema validators from the root's ``corpus-schema.json``.

    Returns ``None`` when the file is absent, unparseable or does not carry the expected
    ``$defs`` entries (a stub schema next to a hand-built corpus), which disables the schema
    layer — the strict binding layer still runs, mirroring the Java loader that never enforced
    the schema file.
    """
    schema_file = root / SCHEMA_FILE
    if not schema_file.is_file():
        return None
    try:
        document = _read_document(schema_file)
    except CorpusLoadException:
        return None
    if not isinstance(document, dict):
        return None
    defs = document.get("$defs")
    if not isinstance(defs, dict) or any(ref.split("/")[-1] not in defs for ref in _KIND_REFS.values()):
        return None
    repaired = _repair_schema(document)
    validator_class = validator_for(repaired)
    return {kind: validator_class({"$ref": ref, "$defs": repaired["$defs"]}) for kind, ref in _KIND_REFS.items()}


def _repair_schema(document: dict[str, Any]) -> dict[str, Any]:
    """Apply the documented in-memory repairs to the shipped ``corpus-schema.json``.

    The Java corpus never enforced the schema file, so it carries three latent self-contradictions
    with the shipped corpus (each repair stays minimal and is pinned by a loader test):

    * ``$defs/context`` rejects the JSON ``null`` context that its own description documents as
      the null-context probe form — the def's ``type`` widens to ``["object", "null"]`` (the
      object keywords apply to objects only, so a null context passes and a malformed object
      still fails with its own precise defect);
    * the ``schema`` property of ``$defs/case`` and ``$defs/scenarioStep`` uses ``oneOf`` where a
      ``{"$ref": ...}`` object is valid under both branches (a JSON object *and* a schema
      reference), while ``$defs/liveCase`` already uses ``anyOf`` for the identical construct —
      the ``oneOf`` becomes ``anyOf``;
    * ``$defs/sharedSchemasFile`` limits property names to lower case, but the shipped
      ``shared/schemas.json`` carries the drift-probe names ``noType`` and ``nestedArray`` — the
      pattern admits upper case letters as well.
    """
    repaired = copy.deepcopy(document)
    defs = repaired.get("$defs", {})
    context = defs.get("context")
    if isinstance(context, dict) and context.get("type") == "object":
        widened_context = dict(context)
        widened_context["type"] = ["object", "null"]
        defs["context"] = widened_context
    for name in ("case", "scenarioStep"):
        properties = defs.get(name, {}).get("properties") if isinstance(defs.get(name), dict) else None
        schema_property = properties.get("schema") if isinstance(properties, dict) else None
        if isinstance(schema_property, dict) and "oneOf" in schema_property:
            widened = dict(schema_property)
            widened["anyOf"] = widened.pop("oneOf")
            properties["schema"] = widened
    shared_schemas = defs.get("sharedSchemasFile")
    if isinstance(shared_schemas, dict) and isinstance(shared_schemas.get("propertyNames"), dict):
        shared_schemas["propertyNames"]["pattern"] = "^[a-zA-Z0-9_.]+$"
    return repaired


def _schema_failure(
    validator: Validator, record: dict[str, Any], file: str, record_id: str | None, record_path: str
) -> CorpusLoadException | None:
    """Render the first schema violation as a corpus error.

    The shallowest leaf of the error tree is reported, with one override: an unknown-key violation
    (``additionalProperties``) outranks the other leaves. A discriminated-union branch — the
    expectation block is a ``oneOf`` of the success and failure shapes — answers a typo'd key
    with two errors, the unknown key itself and the sibling branch's spurious ``required``
    complaint, and naming the unknown key always names the actual defect.
    """
    leaves: list[Any] = []
    stack = list(validator.iter_errors(record))
    while stack:
        error = stack.pop()
        if error.context:
            stack.extend(error.context)
        else:
            leaves.append(error)
    if not leaves:
        return None
    leaves.sort(key=_leaf_sort_key)
    chosen = leaves[0]
    path = record_path + _relative_json_path(chosen)
    message = chosen.message
    if chosen.validator == "additionalProperties":
        offending = re.findall(r"'([^']+)'", message)
        if offending:
            path = f"{path}.{offending[0]}"
    return _error(file, record_id, path, message)


def _leaf_sort_key(error: Any) -> tuple[int, int, str, str]:
    """Order schema-error leaves: unknown keys first, then shallowest, then path and message."""
    unknown_key = 0 if error.validator == "additionalProperties" else 1
    return (unknown_key, len(error.absolute_path), _render_json_path(error.absolute_path), error.message)


def _render_json_path(segments: tuple[Any, ...]) -> str:
    rendered = "$"
    for segment in segments:
        rendered += f"[{segment}]" if isinstance(segment, int) else f".{segment}"
    return rendered


def _relative_json_path(error: Any) -> str:
    """JSON path of a schema error relative to its record, with the leading ``$`` stripped."""
    json_path = error.json_path
    return "" if json_path == "$" else json_path[1:]


# ------------------------------------------------------------------ file traversal


def _list_corpus_files(root: Path) -> list[Path]:
    """Every ``.json`` record file under the root, except the shared files and the schema."""
    shared_dir = root / "shared"
    files = [
        path
        for path in root.rglob("*.json")
        if path.is_file() and shared_dir not in path.parents and path.name != SCHEMA_FILE
    ]
    return sorted(files, key=lambda path: path.relative_to(root).as_posix())


def _read_document(file: Path) -> Any:
    """Read one corpus JSON file: UTF-8, CRLF normalized to LF, parsed strictly."""
    text = file.read_text(encoding="utf-8").replace("\r\n", "\n")
    return json.loads(text)


def _parse_file(state: _LoadState, file: str, path: Path) -> None:
    try:
        root = _read_document(path)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as cause:
        raise CorpusLoadException(f"{file}: failed to read the corpus file") from cause
    if not isinstance(root, list):
        raise _error(file, None, "$", "expected a JSON array of corpus records")
    for index, node in enumerate(root):
        record_path = f"$[{index}]"
        if not isinstance(node, dict):
            raise _error(file, None, record_path, "expected a JSON object record")
        record_id = node.get("id")
        if not isinstance(record_id, str):
            raise _error(file, None, f"{record_path}.id", "missing required field 'id'")
        kind = "live" if file.startswith(LIVE_DIR_PREFIX) else ("scenario" if "steps" in node else "case")
        if state.validators is not None:
            failure = _schema_failure(state.validators[kind], node, file, record_id, record_path)
            if failure is not None:
                raise failure
        if kind == "live":
            _parse_live_case(state, file, record_id, record_path, node)
        elif kind == "scenario":
            _parse_scenario(state, file, record_id, record_path, node)
        else:
            _parse_case(state, file, record_id, record_path, node)


# ------------------------------------------------------------------ shared reference files


def _load_shared(shared_dir: Path, validators: dict[str, Validator] | None) -> _SharedRefs:
    """Load and validate the two shared reference files (an absent file loads empty)."""
    responses = _load_shared_file(shared_dir / "llm-responses.json", "responses", validators)
    schemas = _load_shared_file(shared_dir / "schemas.json", "schemas", validators)
    return _SharedRefs(responses=responses, schemas=schemas)


def _load_shared_file(file: Path, kind: str, validators: dict[str, Validator] | None) -> dict[str, Any]:
    name = file.name
    if not file.is_file():
        return {}
    try:
        document = _read_document(file)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as cause:
        raise CorpusLoadException(f"Failed to read the shared corpus file: {file}") from cause
    if not isinstance(document, dict):
        raise _error(f"shared/{name}", None, "$", "expected a JSON object mapping names to shared values")
    if validators is not None:
        failure = _schema_failure(validators[kind], document, f"shared/{name}", None, "$")
        if failure is not None:
            raise failure
    for key, value in document.items():
        if kind == "responses" and not isinstance(value, str):
            raise _error(RESPONSES_FILE, None, f"$.{key}", "every shared response payload must be a JSON string")
        if kind == "schemas" and not isinstance(value, dict):
            raise _error(SCHEMAS_FILE, None, f"$.{key}", "every shared schema variant must be a JSON object")
    return document


# ------------------------------------------------------------------ record parsing


def _parse_case(state: _LoadState, file: str, record_id: str, path: str, node: dict[str, Any]) -> None:
    """Parse one offline case record into one expanded :class:`NegotiationCase` per language."""
    raw = _bind(node, RawCase, file, record_id, path)
    languages = _validate_languages(raw.languages, file, record_id, path)
    _claim_base_id(record_id, file, path, state.seen_base_ids)
    priority = _validate_priority(raw.priority, file, record_id, path)
    tags = list(raw.tags) if raw.tags is not None else []
    resolved = _resolve_case_fields(
        state,
        file,
        record_id,
        path,
        languages,
        raw.api,
        raw.context,
        raw.templateUri,
        raw.input,
        raw.llm,
        raw.prompt,
        raw.schema,
        raw.inject,
        raw.expect,
    )
    for language in languages:
        expanded_id = f"{record_id}/{language}"
        _claim_expanded_id(expanded_id, file, record_id, path, state.seen_expanded_ids)
        state.cases.append(
            NegotiationCase(
                id=expanded_id,
                base_id=record_id,
                source_file=file,
                api=resolved.api,
                language=language,
                priority=priority,
                tags=tags,
                summary=raw.summary,
                context=resolved.context,
                template_uri=resolved.template_uri,
                input_text=resolved.input_text.get(language),
                input_data=resolved.input_data,
                llm=resolved.llm,
                prompt=resolved.prompt,
                schema=resolved.schema,
                inject=resolved.inject,
                expect=resolved.expect,
            )
        )


def _parse_scenario(state: _LoadState, file: str, record_id: str, path: str, node: dict[str, Any]) -> None:
    """Parse one scenario record into one expanded :class:`ScenarioCase` per language."""
    raw = _bind(node, RawScenario, file, record_id, path)
    languages = _validate_languages(raw.languages, file, record_id, path)
    _claim_base_id(record_id, file, path, state.seen_base_ids)
    roles = list(raw.roles) if raw.roles is not None else []
    if any(not isinstance(role, str) or not role.strip() for role in roles):
        raise _error(file, record_id, f"{path}.roles", "role names must not be blank")
    roles_desc = _validate_roles_desc(raw.rolesDesc, roles, file, record_id, path)
    if not raw.steps:
        raise _error(file, record_id, f"{path}.steps", "missing required field 'steps' (at least one step)")
    bound_steps = [
        _bind(step_node, RawStep, file, record_id, f"{path}.steps[{index}]")
        for index, step_node in enumerate(raw.steps)
    ]
    for index, step in enumerate(bound_steps):
        if step.step is None or step.step != index + 1:
            raise _error(
                file,
                record_id,
                f"{path}.steps[{index}].step",
                f"scenario steps must be numbered consecutively from 1, but step {index + 1} carries '{step.step}'",
            )
    expect_flow = _build_expect_flow(
        _bind(raw.expectFlow, RawExpectFlow, file, record_id, f"{path}.expectFlow")
        if raw.expectFlow is not None
        else None,
        file,
        record_id,
        path,
    )
    for language in languages:
        expanded_id = f"{record_id}/{language}"
        _claim_expanded_id(expanded_id, file, record_id, path, state.seen_expanded_ids)
        steps: list[ScenarioCase.ScenarioStep] = []
        for raw_step in bound_steps:
            resolved = _resolve_case_fields(
                state,
                file,
                record_id,
                f"{path}.steps[{raw_step.step - 1}]",
                languages,
                raw_step.api,
                raw_step.context,
                raw_step.templateUri,
                raw_step.input,
                raw_step.llm,
                raw_step.prompt,
                raw_step.schema,
                raw_step.inject,
                raw_step.expect,
            )
            step_case = NegotiationCase(
                id=f"{expanded_id}#step-{raw_step.step}",
                base_id=record_id,
                source_file=file,
                api=resolved.api,
                language=language,
                tags=[],
                expect=resolved.expect,
                priority=None,
                summary=None,
                context=resolved.context,
                template_uri=resolved.template_uri,
                input_text=resolved.input_text.get(language),
                input_data=resolved.input_data,
                llm=resolved.llm,
                prompt=resolved.prompt,
                schema=resolved.schema,
                inject=resolved.inject,
            )
            steps.append(ScenarioCase.ScenarioStep(step=raw_step.step, role=raw_step.role, case_data=step_case))
        state.scenarios.append(
            ScenarioCase(
                id=expanded_id,
                base_id=record_id,
                source_file=file,
                language=language,
                roles=roles,
                steps=steps,
                summary=raw.summary,
                expect_flow=expect_flow,
                roles_desc=roles_desc,
            )
        )


def _parse_live_case(state: _LoadState, file: str, record_id: str, path: str, node: dict[str, Any]) -> None:
    """Parse one live-LLM record with its dedicated binding and the phase-1 restrictions.

    The live bindings share the base fields with the offline family minus ``llm``, ``inject``
    and the typed input data, plus the live-only expectation block, so the two families' keys
    never collide; live records land in ``live_cases``, never in the offline ``cases`` list.
    """
    if not record_id.startswith(LIVE_ID_PREFIX):
        raise _error(
            file,
            record_id,
            f"{path}.id",
            f"a live record id must carry the '{LIVE_ID_PREFIX}' prefix but is '{record_id}'",
        )
    raw = _bind(node, RawLiveCase, file, record_id, path)
    languages = _validate_languages(raw.languages, file, record_id, path)
    if languages != list(LIVE_LANGUAGES):
        raise _error(
            file,
            record_id,
            f"{path}.languages",
            f"live phase 1 records must declare exactly the languages {_render_list(LIVE_LANGUAGES)} "
            f"but declare {_render_list(languages)}",
        )
    _claim_base_id(record_id, file, path, state.seen_base_ids)
    priority = _validate_priority(raw.priority, file, record_id, path)
    tags = list(raw.tags) if raw.tags is not None else []
    api = NegotiationApi.from_json_name(raw.api)
    if api is None:
        raise _error(file, record_id, f"{path}.api", f"unknown api '{raw.api}' (known apis: {_known_api_names()})")
    if api not in LIVE_APIS:
        raise _error(
            file,
            record_id,
            f"{path}.api",
            f"live phase 1 supports only {_live_api_names()} but the record declares '{raw.api}'",
        )
    context_spec = _validate_context(
        _bind(raw.context, RawContext, file, record_id, f"{path}.context") if raw.context is not None else None,
        file,
        record_id,
        path,
    )
    if raw.templateUri is not None and not raw.templateUri.strip():
        raise _error(file, record_id, f"{path}.templateUri", "the template URI must not be blank")
    input_text: dict[str, str] = {}
    if raw.input is not None:
        raw_input = _bind(raw.input, RawLiveInput, file, record_id, f"{path}.input")
        if raw_input.text is not None:
            input_text = raw_input.text
            for language in languages:
                if language not in input_text:
                    raise _error(
                        file,
                        record_id,
                        f"{path}.input.text",
                        f"missing the '{language}' text entry (the input must cover every language of the record)",
                    )
    if api is NegotiationApi.GENERATE_TASK_PROMPT_FROM_TEXT and not input_text:
        raise _error(
            file,
            record_id,
            f"{path}.input.text",
            "a live generateTaskPromptFromText record requires an input.text "
            "(the engine has no other source of the natural-language input)",
        )
    prompt_source = _build_prompt_source(
        _bind(raw.prompt, RawPrompt, file, record_id, f"{path}.prompt") if raw.prompt is not None else None,
        file,
        record_id,
        path,
    )
    if prompt_source is not None and not isinstance(prompt_source, PromptSource.Text):
        # The live engine only reads an inline prompt text; a golden or fromStep source would
        # load fine and then misfire at run time, so the loader rejects it up front, mirroring
        # the offline fail-fast philosophy.
        declared = "golden" if isinstance(prompt_source, PromptSource.Golden) else "fromStep"
        raise _error(
            file,
            record_id,
            f"{path}.prompt",
            f"a live record supports only the inline prompt.text but declares {declared}",
        )
    schema_value = _resolve_schema(state, raw.schema, file, record_id, path)
    live_expect = _build_live_expectation(
        _bind(raw.expect, RawLiveExpect, file, record_id, f"{path}.expect") if raw.expect is not None else None,
        file,
        record_id,
        path,
    )
    if api is NegotiationApi.VALIDATE_TASK_PROMPT_AND_DATA_FILLING and live_expect.prompt_text_contains:
        raise _error(
            file,
            record_id,
            f"{path}.expect.promptTextContains",
            "promptTextContains judges the generated prompt of the generate API; a validate record "
            "has no generated prompt to assert fragments of",
        )
    for language in languages:
        expanded_id = f"{record_id}/{language}"
        _claim_expanded_id(expanded_id, file, record_id, path, state.seen_expanded_ids)
        state.live_cases.append(
            LiveCase(
                id=expanded_id,
                base_id=record_id,
                source_file=file,
                api=api,
                language=language,
                priority=priority,
                tags=tags,
                summary=raw.summary,
                context=context_spec,
                template_uri=raw.templateUri,
                input_text=input_text.get(language),
                prompt=prompt_source,
                schema=schema_value,
                live_expect=live_expect,
            )
        )


def _validate_roles_desc(
    roles_desc: dict[str, str] | None, roles: list[str], file: str, record_id: str, path: str
) -> dict[str, str]:
    """Validate the role descriptions of the closed loop: keys and values non-blank, keys declared.

    Every key must name a declared role, so a typo'd role fails at load time instead of silently
    never showing up in a failure message.
    """
    if roles_desc is None:
        return {}
    validated: dict[str, str] = {}
    for role, description in roles_desc.items():
        if not isinstance(role, str) or not role.strip():
            raise _error(file, record_id, f"{path}.rolesDesc", "role names must not be blank")
        if not isinstance(description, str) or not description.strip():
            raise _error(file, record_id, f"{path}.rolesDesc", f"the description of role '{role}' must not be blank")
        if role not in roles:
            raise _error(
                file,
                record_id,
                f"{path}.rolesDesc",
                f"the described role '{role}' is not one of the declared roles {_render_list(roles)}",
            )
        validated[role] = description
    return dict(validated)


# ------------------------------------------------------------------ field resolution


def _resolve_case_fields(
    state: _LoadState,
    file: str,
    record_id: str,
    path: str,
    languages: list[str],
    api_name: str | None,
    context_node: dict[str, Any] | None,
    template_uri: str | None,
    input_node: dict[str, Any] | None,
    llm_node: dict[str, Any] | None,
    prompt_node: dict[str, Any] | None,
    schema_node: dict[str, Any] | None,
    inject: str | None,
    expect_node: dict[str, Any] | None,
) -> _ResolvedFields:
    """Validate and resolve every case field of one record or scenario step."""
    api = NegotiationApi.from_json_name(api_name)
    if api is None:
        raise _error(file, record_id, f"{path}.api", f"unknown api '{api_name}' (known apis: {_known_api_names()})")
    context_spec = _validate_context(
        _bind(context_node, RawContext, file, record_id, f"{path}.context") if context_node is not None else None,
        file,
        record_id,
        path,
    )
    if template_uri is not None and not template_uri.strip():
        raise _error(file, record_id, f"{path}.templateUri", "the template URI must not be blank")
    input_text: dict[str, str] = {}
    input_data: dict[str, Any] | None = None
    if input_node is not None:
        raw_input = _bind(input_node, RawInput, file, record_id, f"{path}.input")
        if raw_input.text is not None:
            input_text = raw_input.text
            for language in languages:
                if language not in input_text:
                    raise _error(
                        file,
                        record_id,
                        f"{path}.input.text",
                        f"missing the '{language}' text entry (the input must cover every language of the record)",
                    )
        if raw_input.data is not None:
            if not isinstance(raw_input.data, dict):
                raise _error(file, record_id, f"{path}.input.data", "the typed input data must be a JSON object")
            input_data = raw_input.data
    llm_script: LlmScript | None = None
    if llm_node is not None:
        raw_llm = _bind(llm_node, RawLlm, file, record_id, f"{path}.llm")
        if raw_llm.maxAttempts is not None and raw_llm.maxAttempts < 1:
            raise _error(file, record_id, f"{path}.llm.maxAttempts", "maxAttempts must be at least 1")
        if not raw_llm.script:
            raise _error(file, record_id, f"{path}.llm.script", "missing required field 'script' (at least one step)")
        llm_script = LlmScript(
            steps=_resolve_script(raw_llm.script, state, file, record_id, f"{path}.llm.script"),
            max_attempts=raw_llm.maxAttempts,
        )
    prompt_source = _build_prompt_source(
        _bind(prompt_node, RawPrompt, file, record_id, f"{path}.prompt") if prompt_node is not None else None,
        file,
        record_id,
        path,
    )
    schema_value = _resolve_schema(state, schema_node, file, record_id, path)
    if inject is not None and inject not in _KNOWN_INJECT_HOOKS:
        raise _error(
            file,
            record_id,
            f"{path}.inject",
            f"unknown inject hook '{inject}' (known hooks: {', '.join(_KNOWN_INJECT_HOOKS)})",
        )
    expectation = _build_expectation(
        _bind(expect_node, RawExpect, file, record_id, f"{path}.expect") if expect_node is not None else None,
        file,
        record_id,
        path,
    )
    return _ResolvedFields(
        api=api,
        context=context_spec,
        template_uri=template_uri,
        input_text=input_text,
        input_data=input_data,
        llm=llm_script,
        prompt=prompt_source,
        schema=schema_value,
        inject=inject,
        expect=expectation,
    )


def _validate_languages(languages: list[str] | None, file: str, record_id: str, path: str) -> list[str]:
    if not languages:
        raise _error(file, record_id, f"{path}.languages", "missing required field 'languages' (at least one language)")
    seen: set[str] = set()
    for language in languages:
        if not isinstance(language, str) or language not in SUPPORTED_LANGUAGES:
            raise _error(
                file,
                record_id,
                f"{path}.languages",
                f"unsupported language '{language}' (supported languages: zh-CN, en-US)",
            )
        if language in seen:
            raise _error(file, record_id, f"{path}.languages", f"duplicate language '{language}'")
        seen.add(language)
    return list(languages)


def _validate_priority(priority: str | None, file: str, record_id: str, path: str) -> str | None:
    if priority is not None and priority not in SUPPORTED_PRIORITIES:
        raise _error(
            file, record_id, f"{path}.priority", f"unknown priority '{priority}' (known priorities: P0, P1, P2)"
        )
    return priority


def _validate_context(context: RawContext | None, file: str, record_id: str, path: str) -> ContextSpec | None:
    if context is None:
        return None
    if context.id is None or not context.id.strip():
        raise _error(file, record_id, f"{path}.context.id", "the context id must not be blank")
    if context.round is None:
        raise _error(file, record_id, f"{path}.context.round", "missing required field 'round'")
    if context.maxRounds is None:
        raise _error(file, record_id, f"{path}.context.maxRounds", "missing required field 'maxRounds'")
    if context.round < 1 or context.maxRounds < 1:
        raise _error(file, record_id, f"{path}.context", "the context round and maxRounds must be at least 1")
    return ContextSpec(id=context.id, round=context.round, max_rounds=context.maxRounds)


def _build_prompt_source(
    prompt: RawPrompt | None, file: str, record_id: str, path: str
) -> PromptSource.Golden | PromptSource.Text | PromptSource.FromStep | None:
    if prompt is None:
        return None
    declared = sum(value is not None for value in (prompt.golden, prompt.text, prompt.fromStep))
    if declared != 1:
        raise _error(
            file,
            record_id,
            f"{path}.prompt",
            f"the prompt must declare exactly one of golden, text or fromStep but declares {declared}",
        )
    if prompt.golden is not None:
        if not prompt.golden.strip():
            raise _error(file, record_id, f"{path}.prompt.golden", "the golden fixture name must not be blank")
        return PromptSource.Golden(prompt.golden)
    if prompt.text is not None:
        return PromptSource.Text(prompt.text)
    if prompt.fromStep is None or prompt.fromStep < 1:
        raise _error(file, record_id, f"{path}.prompt.fromStep", "fromStep must be at least 1")
    return PromptSource.FromStep(prompt.fromStep)


def _build_expectation(expect: RawExpect | None, file: str, record_id: str, path: str) -> Expectation:
    if expect is None:
        raise _error(file, record_id, f"{path}.expect", "missing required field 'expect'")
    if expect.outcome == "success":
        success = True
    elif expect.outcome == "failure":
        success = False
    else:
        raise _error(
            file,
            record_id,
            f"{path}.expect.outcome",
            f"unknown outcome '{expect.outcome}' (expected 'success' or 'failure')",
        )
    if success:
        if any(
            value is not None for value in (expect.exception, expect.code, expect.messageContains, expect.slotErrors)
        ):
            raise _error(
                file,
                record_id,
                f"{path}.expect",
                "a success expectation must not carry failure-only fields "
                "(exception, code, messageContains, slotErrors)",
            )
    else:
        if expect.exception is None and expect.code is None:
            raise _error(
                file,
                record_id,
                f"{path}.expect",
                "a failure expectation must name the expected exception or the expected error code",
            )
        if any(
            value is not None
            for value in (
                expect.promptTextEqualsGolden,
                expect.metadata,
                expect.params,
                expect.differential,
                expect.promptTextContains,
                expect.missingParams,
                expect.paramsFromStep,
            )
        ):
            raise _error(
                file,
                record_id,
                f"{path}.expect",
                "a failure expectation must not carry success-only fields (promptTextEqualsGolden, "
                "metadata, params, differential, promptTextContains, missingParams, paramsFromStep)",
            )
    slot_errors: list[Expectation.SlotError] = []
    if expect.slotErrors is not None:
        for index, slot_error_node in enumerate(expect.slotErrors):
            slot_error = _bind(slot_error_node, RawSlotError, file, record_id, f"{path}.expect.slotErrors[{index}]")
            if slot_error.slot is None or not slot_error.slot.strip():
                raise _error(
                    file, record_id, f"{path}.expect.slotErrors[{index}].slot", "the slot name must not be blank"
                )
            if slot_error.code is None or not slot_error.code.strip():
                raise _error(
                    file,
                    record_id,
                    f"{path}.expect.slotErrors[{index}].code",
                    "the slot error code must not be blank",
                )
            slot_errors.append(Expectation.SlotError(slot=slot_error.slot, code=slot_error.code))
    params: dict[str, Any] = {}
    if expect.params is not None:
        if not isinstance(expect.params, dict):
            raise _error(file, record_id, f"{path}.expect.params", "the expected params must be a JSON object")
        params = expect.params
        for key, value in params.items():
            if value is None:
                raise _error(
                    file,
                    record_id,
                    f"{path}.expect.params",
                    f"the expected param '{key}' carries a JSON null; a missing parameter belongs "
                    "into missingParams, not into params",
                )
    message_contains = list(expect.messageContains) if expect.messageContains is not None else []
    if message_contains and any(not isinstance(entry, str) or not entry.strip() for entry in message_contains):
        raise _error(file, record_id, f"{path}.expect.messageContains", "messageContains entries must not be blank")
    contracts = list(expect.contracts) if expect.contracts is not None else []
    if contracts and any(not isinstance(entry, str) or not entry.strip() for entry in contracts):
        raise _error(file, record_id, f"{path}.expect.contracts", "contract names must not be blank")
    prompt_text_contains = list(expect.promptTextContains) if expect.promptTextContains is not None else []
    if prompt_text_contains and any(not isinstance(entry, str) or not entry.strip() for entry in prompt_text_contains):
        raise _error(
            file, record_id, f"{path}.expect.promptTextContains", "promptTextContains entries must not be blank"
        )
    missing_params = list(expect.missingParams) if expect.missingParams is not None else None
    if missing_params is not None and any(not isinstance(entry, str) or not entry.strip() for entry in missing_params):
        raise _error(file, record_id, f"{path}.expect.missingParams", "missingParams entries must not be blank")
    if expect.paramsFromStep is not None and expect.paramsFromStep < 1:
        raise _error(file, record_id, f"{path}.expect.paramsFromStep", "paramsFromStep must be at least 1")
    metadata = None
    if expect.metadata is not None:
        raw_metadata = _bind(expect.metadata, RawMetadata, file, record_id, f"{path}.expect.metadata")
        metadata = Expectation.Metadata(
            template_uri_echo=raw_metadata.templateUriEcho, context_echo=raw_metadata.contextEcho
        )
    return Expectation(
        success=success,
        exception=expect.exception,
        code=expect.code,
        message_contains=message_contains,
        slot_errors=slot_errors,
        llm_calls=expect.llmCalls,
        prompt_text_equals_golden=expect.promptTextEqualsGolden,
        metadata=metadata,
        params=params,
        contracts=contracts,
        differential=expect.differential is True,
        prompt_text_contains=prompt_text_contains,
        missing_params=missing_params,
        params_from_step=expect.paramsFromStep,
    )


def _build_live_expectation(expect: RawLiveExpect | None, file: str, record_id: str, path: str) -> LiveExpectation:
    if expect is None:
        raise _error(file, record_id, f"{path}.expect", "missing required field 'expect'")
    if expect.success is None:
        raise _error(file, record_id, f"{path}.expect.success", "missing required field 'success'")
    params_contains: dict[str, Any] = {}
    if expect.paramsContains is not None:
        if not isinstance(expect.paramsContains, dict):
            raise _error(file, record_id, f"{path}.expect.paramsContains", "paramsContains must be a JSON object")
        params_contains = expect.paramsContains
        for key, value in params_contains.items():
            if value is None:
                raise _error(
                    file,
                    record_id,
                    f"{path}.expect.paramsContains",
                    f"the expected param '{key}' carries a JSON null; a missing parameter belongs "
                    "into paramsAbsent, not into paramsContains",
                )
    params_absent = list(expect.paramsAbsent) if expect.paramsAbsent is not None else []
    if params_absent and any(not isinstance(entry, str) or not entry.strip() for entry in params_absent):
        raise _error(file, record_id, f"{path}.expect.paramsAbsent", "paramsAbsent entries must not be blank")
    prompt_text_contains = list(expect.promptTextContains) if expect.promptTextContains is not None else []
    if prompt_text_contains and any(not isinstance(entry, str) or not entry.strip() for entry in prompt_text_contains):
        raise _error(
            file, record_id, f"{path}.expect.promptTextContains", "promptTextContains entries must not be blank"
        )
    if expect.maxLlmCalls is not None and expect.maxLlmCalls < 1:
        raise _error(file, record_id, f"{path}.expect.maxLlmCalls", "maxLlmCalls must be at least 1")
    return LiveExpectation(
        success=expect.success,
        scenario_code=expect.scenarioCode,
        params_contains=params_contains,
        params_absent=params_absent,
        prompt_text_contains=prompt_text_contains,
        max_llm_calls=LIVE_DEFAULT_MAX_LLM_CALLS if expect.maxLlmCalls is None else expect.maxLlmCalls,
    )


def _build_expect_flow(
    expect_flow: RawExpectFlow | None, file: str, record_id: str, path: str
) -> ScenarioCase.ExpectFlow | None:
    if expect_flow is None:
        return None
    if expect_flow.terminalCondition is not None and expect_flow.terminalCondition not in SUPPORTED_TERMINAL_CONDITIONS:
        raise _error(
            file,
            record_id,
            f"{path}.expectFlow.terminalCondition",
            f"unknown terminal condition '{expect_flow.terminalCondition}' "
            "(known conditions: accept, reject, abort, exhausted)",
        )
    if expect_flow.roundsUsed is not None and expect_flow.roundsUsed < 1:
        raise _error(file, record_id, f"{path}.expectFlow.roundsUsed", "roundsUsed must be at least 1")
    if expect_flow.missingParamsFilled is not None and expect_flow.missingParamsFilled < 1:
        raise _error(
            file, record_id, f"{path}.expectFlow.missingParamsFilled", "missingParamsFilled must be at least 1"
        )
    return ScenarioCase.ExpectFlow(
        terminal_condition=expect_flow.terminalCondition,
        rounds_used=expect_flow.roundsUsed,
        distinct_messages=expect_flow.distinctMessages,
        missing_params_filled=expect_flow.missingParamsFilled,
    )


# ------------------------------------------------------------------ $ref and $fail resolution


def _resolve_script(
    script: list[Any], state: _LoadState, file: str, record_id: str, path: str
) -> list[LlmScriptStep.Payload | LlmScriptStep.Fail]:
    """Resolve every script step into a literal payload or a failure marker."""
    steps: list[LlmScriptStep.Payload | LlmScriptStep.Fail] = []
    for index, step_node in enumerate(script):
        step_path = f"{path}[{index}]"
        if isinstance(step_node, str):
            steps.append(LlmScriptStep.Payload(step_node))
        elif isinstance(step_node, dict):
            if len(step_node) == 1 and "$fail" in step_node:
                steps.append(
                    LlmScriptStep.Fail(_parse_fail_marker(step_node["$fail"], file, record_id, f"{step_path}.$fail"))
                )
            elif len(step_node) == 1 and "$ref" in step_node:
                payload = _resolve_shared_ref(
                    step_node["$ref"], state, file, record_id, f"{step_path}.$ref", True, set()
                )
                if not isinstance(payload, str):
                    raise _error(
                        file, record_id, f"{step_path}.$ref", "the referenced response payload must be a JSON string"
                    )
                steps.append(LlmScriptStep.Payload(payload))
            else:
                raise _error(
                    file,
                    record_id,
                    step_path,
                    'a script step must be a literal JSON string, a {"$ref": ...} object or a {"$fail": ...} object',
                )
        else:
            raise _error(
                file,
                record_id,
                step_path,
                'a script step must be a literal JSON string, a {"$ref": ...} object or a {"$fail": ...} object',
            )
    return steps


def _parse_fail_marker(node: Any, file: str, record_id: str, path: str) -> LlmFailMarker:
    if not isinstance(node, str):
        raise _error(file, record_id, path, "the $fail marker must be a string")
    marker = LlmFailMarker.from_json_name(node)
    if marker is None:
        raise _error(
            file, record_id, path, f"unknown $fail marker '{node}' (known markers: {_known_fail_marker_names()})"
        )
    return marker


def _resolve_schema(state: _LoadState, schema: Any, file: str, record_id: str, path: str) -> dict[str, Any] | None:
    if schema is None:
        return None
    if not isinstance(schema, dict):
        raise _error(file, record_id, f"{path}.schema", "the schema must be a JSON object or a $ref object")
    if len(schema) == 1 and "$ref" in schema:
        ref_node = schema["$ref"]
        if not isinstance(ref_node, str):
            raise _error(file, record_id, f"{path}.schema.$ref", "the $ref must be a string")
        resolved = _resolve_shared_ref(ref_node, state, file, record_id, f"{path}.schema.$ref", False, set())
        if not isinstance(resolved, dict):
            raise _error(file, record_id, f"{path}.schema.$ref", "the referenced schema must be a JSON object")
        _reject_nested_refs(resolved, file, record_id, f"{path}.schema")
        return resolved
    _reject_nested_refs(schema, file, record_id, f"{path}.schema")
    return schema


def _resolve_shared_ref(
    ref_node: Any,
    state: _LoadState,
    file: str,
    record_id: str,
    path: str,
    response_scope: bool,
    visited: set[str],
) -> Any:
    if not isinstance(ref_node, str):
        raise _error(file, record_id, path, "the $ref must be a string")
    ref = ref_node
    prefix = RESPONSES_PREFIX if response_scope else SCHEMAS_PREFIX
    other_prefix = SCHEMAS_PREFIX if response_scope else RESPONSES_PREFIX
    shared_file = RESPONSES_FILE if response_scope else SCHEMAS_FILE
    if ref.startswith(other_prefix):
        raise _error(
            file,
            record_id,
            path,
            f"$ref '{ref}' is out of scope: it must address {shared_file} through the {prefix} prefix",
        )
    if not ref.startswith(prefix):
        raise _error(
            file,
            record_id,
            path,
            "$ref '" + ref + "' is out of scope: it must address shared/llm-responses.json through the"
            " responses/ prefix or shared/schemas.json through the schemas/ prefix",
        )
    if ref in visited:
        raise _error(file, record_id, path, f"circular $ref chain detected at '{ref}'")
    visited.add(ref)
    key = ref[len(prefix) :]
    if not key:
        raise _error(file, record_id, path, f"$ref '{ref}' carries no name after the {prefix} prefix")
    value = (state.shared.responses if response_scope else state.shared.schemas).get(key)
    if value is None:
        raise _error(file, record_id, path, f"dangling $ref '{ref}': '{key}' is not defined in {shared_file}")
    if isinstance(value, dict) and len(value) == 1 and "$ref" in value:
        inner_ref = value["$ref"]
        if inner_ref in visited:
            raise _error(file, record_id, path, f"circular $ref chain '{ref}' -> '{inner_ref}'")
        raise _error(
            file,
            record_id,
            path,
            f"nested $ref '{inner_ref}' inside '{ref}' is not allowed: "
            "shared values are literal and resolve one level only",
        )
    return value


def _reject_nested_refs(node: Any, file: str, record_id: str, path: str) -> None:
    """Reject a ``$ref`` object nested inside an inline schema: references resolve one level only."""
    if isinstance(node, dict):
        if len(node) == 1 and "$ref" in node:
            raise _error(
                file,
                record_id,
                path,
                f"nested $ref '{node['$ref']}' is not allowed: references resolve one level only",
            )
        for key, value in node.items():
            _reject_nested_refs(value, file, record_id, f"{path}.{key}")
    elif isinstance(node, list):
        for index, item in enumerate(node):
            _reject_nested_refs(item, file, record_id, f"{path}[{index}]")


# ------------------------------------------------------------------ id bookkeeping


def _claim_base_id(record_id: str, file: str, path: str, seen_base_ids: dict[str, str]) -> None:
    if record_id in seen_base_ids:
        raise _error(
            file,
            record_id,
            path,
            f"duplicate id '{record_id}': the id is the primary key of the corpus "
            f"and is also defined in {seen_base_ids[record_id]}",
        )
    seen_base_ids[record_id] = file


def _claim_expanded_id(
    expanded_id: str, file: str, record_id: str, path: str, seen_expanded_ids: dict[str, str]
) -> None:
    if expanded_id in seen_expanded_ids:
        raise _error(
            file,
            record_id,
            path,
            f"duplicate expanded id '{expanded_id}': the record is also expanded in {seen_expanded_ids[expanded_id]}",
        )
    seen_expanded_ids[expanded_id] = file


# ------------------------------------------------------------------ strict binding

_JSON_TYPE_NAMES: dict[type, str] = {
    str: "string",
    bool: "boolean",
    int: "integer",
    float: "number",
    list: "array",
    dict: "object",
}

_TYPE_HINTS: dict[type, dict[str, tuple[type, ...] | None]] = {}


def _json_types_of(record_type: type) -> dict[str, tuple[type, ...] | None]:
    """JSON value types accepted per field of a raw record, derived from its type hints."""
    cached = _TYPE_HINTS.get(record_type)
    if cached is None:
        cached = {field_name: _runtime_types(hint) for field_name, hint in get_type_hints(record_type).items()}
        _TYPE_HINTS[record_type] = cached
    return cached


def _runtime_types(annotation: Any) -> tuple[type, ...] | None:
    """Runtime JSON types an annotation accepts, or ``None`` when it is unconstrained."""
    if annotation is Any:
        return None
    origin = get_origin(annotation)
    if origin in (Union, types.UnionType):
        collected: list[type] = []
        for arg in get_args(annotation):
            if arg is type(None):
                continue
            runtime = _runtime_types(arg)
            if runtime is None:
                return None
            collected.extend(runtime)
        return tuple(collected) or None
    if annotation in _JSON_TYPE_NAMES:
        return (annotation,)
    if origin is dict:
        return (dict,)
    if origin is list:
        return (list,)
    return None


def _bind(node: Any, record_type: type, file: str, record_id: str | None, path: str) -> Any:
    """Bind one JSON object into a raw record, failing on unknown keys and wrong field shapes.

    This is the strict second layer of the loader (D21): the schema layer has already validated
    the record when a corpus schema is present; this binding still rejects an unknown property —
    a typo is an error, never a silent skip — and a field of the wrong JSON type.
    """
    if not isinstance(node, dict):
        raise _error(file, record_id, path, "malformed record: expected a JSON object")
    json_types = _json_types_of(record_type)
    for key, value in node.items():
        expected = json_types.get(key)
        if expected is not None and value is not None and not isinstance(value, expected):
            names = " or ".join(_JSON_TYPE_NAMES[accepted] for accepted in expected)
            raise _error(file, record_id, f"{path}.{key}", f"malformed record: '{key}' must be a JSON {names}")
    try:
        return record_type(**node)
    except TypeError as cause:
        offending = _UNEXPECTED_KEYWORD.search(str(cause))
        if offending is None:
            raise _error(file, record_id, path, f"malformed record: {cause}") from cause
        property_name = offending.group(1)
        raise _error(file, record_id, f"{path}.{property_name}", f"unknown property '{property_name}'") from cause


# ------------------------------------------------------------------ error rendering


def _error(file: str, record_id: str | None, path: str, message: str) -> CorpusLoadException:
    """Build a corpus error naming the corpus file, the offending record id and the JSON path."""
    return CorpusLoadException(f"{file}{'' if record_id is None else f' [{record_id}]'} {path}: {message}")


def _render_list(values: Any) -> str:
    return f"[{', '.join(values)}]"


def _known_api_names() -> str:
    return ", ".join(api.json_name for api in NegotiationApi)


def _live_api_names() -> str:
    return " and ".join(api.json_name for api in NegotiationApi if api in LIVE_APIS)


def _known_fail_marker_names() -> str:
    return ", ".join(marker.value for marker in LlmFailMarker)
