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

"""Strict loaded-corpus models of the negotiation test corpus.

Port of the Java ``a2a-t-corpus`` record types (``NegotiationCase``, ``Expectation``,
``ContextSpec``, ``Contract``, ``LlmScript``/``LlmScriptStep``, ``ScenarioCase``, ``LiveCase``,
``LiveExpectation``, ``LoadedCorpus``, ``NegotiationApi``, ``LlmFailMarker``): every record of
the corpus JSON is loaded into exactly one frozen dataclass here, mirroring the Java record
tree field for field — snake_case fields, the same nested shapes (``LlmScriptStep.Payload`` /
``LlmScriptStep.Fail``, ``PromptSource.Golden`` / ``PromptSource.Text`` / ``PromptSource.FromStep``,
``ScenarioCase.ScenarioStep`` / ``ScenarioCase.ExpectFlow``, ``Expectation.SlotError`` /
``Expectation.Metadata``) and the same compact-constructor validation: the non-null record
components reject ``None`` with ``TypeError`` (the ``Objects.requireNonNull`` counterpart) and
the value constraints (``ContextSpec`` round limits, non-empty scenario steps) reject bad values
with ``ValueError`` (the ``IllegalArgumentException`` counterpart) — programming errors stay
outside the corpus error channel, which is :class:`tests.corpus.loader.CorpusLoadException`.

Like the Java records these dataclasses copy their collections on construction, so a loaded
corpus cannot be mutated through an aliased list or map; the collections themselves stay plain
Python ``list``/``dict`` objects (unhashable when non-empty), matching the convention of
:mod:`a2a_t.negotiation.content.models`. The loader guarantees the invariants documented per
field; constructing one of these dataclasses directly (the engine tests do) bypasses only the
corpus-format checks, never the constructor checks.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, StrEnum
from pathlib import Path
from typing import Any

__all__ = [
    "Contract",
    "ContextSpec",
    "Expectation",
    "Family",
    "LiveCase",
    "LiveExpectation",
    "LoadedCorpus",
    "LlmFailMarker",
    "LlmScript",
    "LlmScriptStep",
    "NegotiationApi",
    "NegotiationCase",
    "PromptSource",
    "ScenarioCase",
]


class Family(StrEnum):
    """The four API families of the negotiation content service plus the closed-loop task family.

    Referenced as :attr:`NegotiationApi.family`; defined at module level because a class nested
    in an :class:`~enum.Enum` body would itself become an enum member.
    """

    FROM_TEXT = "from-text"
    FROM_DATA = "from-data"
    VALIDATE = "validate"
    TASK = "task"


class NegotiationApi(Enum):
    """The fifteen APIs the corpus exercises, referenced by their production method name.

    The twelve :class:`~a2a_t.negotiation.generation.NegotiationContentService` APIs plus the
    three task-API facade methods of the closed loop. The corpus references an API by its
    ``json_name``; the later case engine dispatches on this enum, so a misspelled API name
    fails at corpus load time instead of silently skipping a case.
    """

    GENERATE_PROPOSE_FROM_TEXT = ("generateProposeFromText", Family.FROM_TEXT)
    GENERATE_ACCEPT_FROM_TEXT = ("generateAcceptFromText", Family.FROM_TEXT)
    GENERATE_REJECT_FROM_TEXT = ("generateRejectFromText", Family.FROM_TEXT)
    GENERATE_ABORT_FROM_TEXT = ("generateAbortFromText", Family.FROM_TEXT)
    GENERATE_PROPOSE_FROM_DATA = ("generateProposeFromData", Family.FROM_DATA)
    GENERATE_ACCEPT_FROM_DATA = ("generateAcceptFromData", Family.FROM_DATA)
    GENERATE_REJECT_FROM_DATA = ("generateRejectFromData", Family.FROM_DATA)
    GENERATE_ABORT_FROM_DATA = ("generateAbortFromData", Family.FROM_DATA)
    VALIDATE_PROPOSE_PROMPT_AND_DATA_FILLING = ("validateProposePromptAndDataFilling", Family.VALIDATE)
    VALIDATE_ACCEPT_PROMPT_AND_DATA_FILLING = ("validateAcceptPromptAndDataFilling", Family.VALIDATE)
    VALIDATE_REJECT_PROMPT_AND_DATA_FILLING = ("validateRejectPromptAndDataFilling", Family.VALIDATE)
    VALIDATE_ABORT_PROMPT_AND_DATA_FILLING = ("validateAbortPromptAndDataFilling", Family.VALIDATE)
    GENERATE_TASK_PROMPT_FROM_TEXT = ("generateTaskPromptFromText", Family.TASK)
    GENERATE_TASK_PROMPT_FROM_DATA_WITH_SCHEMA = ("generateTaskPromptFromDataWithSchema", Family.TASK)
    VALIDATE_TASK_PROMPT_AND_DATA_FILLING = ("validateTaskPromptAndDataFilling", Family.TASK)

    @property
    def json_name(self) -> str:
        """Corpus JSON name of this API, which equals the production method name."""
        return self.value[0]

    @property
    def family(self) -> Family:
        """API family this API belongs to (from-text, from-data, validate or task)."""
        return self.value[1]

    @classmethod
    def from_json_name(cls, json_name: str | None) -> NegotiationApi | None:
        """Resolve a corpus JSON name into an API, or ``None`` when it matches none."""
        for api in cls:
            if api.value[0] == json_name:
                return api
        return None


class LlmFailMarker(StrEnum):
    """The six scripted LLM failure markers of a corpus script step.

    A ``$fail`` marker replaces one scripted LLM answer with a failure the later scripted client
    replays: infrastructure failures, degenerate responses or the assertion failure that proves
    a zero-call run. The marker names are part of the corpus format contract.
    """

    RUNTIME_EXCEPTION = "runtime-exception"
    LLM_ERROR = "llm-error"
    NULL_RESPONSE = "null-response"
    BLANK_CONTENT = "blank-content"
    NON_JSON = "non-json"
    ASSERTION = "assertion"

    @classmethod
    def from_json_name(cls, json_name: str | None) -> LlmFailMarker | None:
        """Resolve a corpus JSON name into a failure marker, or ``None`` when it matches none."""
        for marker in cls:
            if marker.value == json_name:
                return marker
        return None


class Contract(Enum):
    """Registry of the behavior contract names a corpus expectation can reference.

    The registry defines all twelve contracts at once with their P0/P1 level, so it doubles as
    the readable expectation list of the corpus-generation workflow. The engine implements the
    four P0 contracts; referencing a P1 contract is an explicit engine failure ("not yet lit")
    rather than a silent pass, so a corpus author always knows what is actually asserted.
    """

    CONCLUSION_LITERAL_PRESENT = ("conclusionLiteralPresent", True)
    CONTEXT_KEYS_IN_MERGED_PARAMS = ("contextKeysInMergedParams", True)
    NO_LLM_LEAK_IN_USER_MESSAGE = ("noLlmLeakInUserMessage", True)
    METADATA_TRIPLE_SHAPE = ("metadataTripleShape", True)
    NO_RENDER_SLOT_LEAK = ("noRenderSlotLeak", False)
    NO_CONTEXT_IN_RENDERED_TEXT = ("noContextInRenderedText", False)
    TEMPLATE_URI_ECHO = ("templateUriEcho", False)
    ERROR_CODE_IN_MESSAGE = ("errorCodeInMessage", False)
    ZERO_LLM_CALLS_ON_RULE_GATE = ("zeroLlmCallsOnRuleGate", False)
    ERROR_CODE_DETERMINISM = ("errorCodeDeterminism", False)
    ROUND_MONOTONIC = ("roundMonotonic", False)
    CONTEXT_IMMUTABILITY = ("contextImmutability", False)

    @property
    def json_name(self) -> str:
        """Corpus JSON name of this contract."""
        return self.value[0]

    @property
    def is_p0(self) -> bool:
        """Whether this contract is a P0 contract the engine asserts today."""
        return self.value[1]

    @classmethod
    def from_json_name(cls, json_name: str | None) -> Contract | None:
        """Resolve a corpus JSON name into a contract, or ``None`` when it matches none."""
        for contract in cls:
            if contract.value[0] == json_name:
                return contract
        return None


@dataclass(frozen=True, slots=True)
class ContextSpec:
    """Negotiation context of one corpus record, inline in the corpus JSON.

    The engine converts this spec into a :class:`~a2a_t.core.metadata.NegotiationContext`; a
    JSON ``null`` context (the null-context probes of the validate family) stays a ``None``
    reference on the loaded case instead of a spec.
    """

    id: str
    round: int
    max_rounds: int

    def __post_init__(self) -> None:
        if self.id is None or not self.id.strip():
            raise ValueError("The context id must not be blank.")
        if self.round < 1 or self.max_rounds < 1:
            raise ValueError("The context round and maxRounds must be at least 1.")


class LlmScriptStep:
    """One scripted answer of an LLM script.

    After loader resolution every step is either a literal payload text (an inline corpus string
    or a resolved ``responses/`` reference) or a failure marker. The tri-state of the corpus
    JSON — ``$ref`` object, ``$fail`` object, literal string — collapses into exactly these two
    shapes.
    """

    @dataclass(frozen=True, slots=True)
    class Payload:
        """A scripted answer carrying a literal payload text."""

        json: str

    @dataclass(frozen=True, slots=True)
    class Fail:
        """A scripted answer that fails with the given marker."""

        marker: LlmFailMarker


@dataclass(frozen=True, slots=True)
class LlmScript:
    """The scripted LLM behavior of one corpus record.

    ``max_attempts`` is a builder-level retry limit (``None`` for the builder default; clamping
    assertions stay in the handwritten suites). The steps are consumed strictly step by step; an
    exhausted script fails the run instead of repeating the last answer.
    """

    steps: list[LlmScriptStep.Payload | LlmScriptStep.Fail]
    max_attempts: int | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "steps", list(self.steps))


class PromptSource:
    """The prompt input of one validate-family corpus record.

    Exactly one variant applies per record: a golden fixture name resolved per language by the
    engine, an inline prompt text, or the number of the scenario step whose generated prompt
    text feeds this validation step.
    """

    @dataclass(frozen=True, slots=True)
    class Golden:
        """The prompt is the golden fixture of the given name, resolved per language."""

        golden: str

    @dataclass(frozen=True, slots=True)
    class Text:
        """The prompt is the given inline text."""

        text: str

    @dataclass(frozen=True, slots=True)
    class FromStep:
        """The prompt is the prompt text generated by an earlier scenario step."""

        step: int


@dataclass(frozen=True, slots=True)
class Expectation:
    """The validated expectation block of one corpus record.

    The success and failure shapes are a discriminated union in the corpus JSON; on the loaded
    record both live on one object and the loader guarantees the field combinations that the
    outcome allows: a failure expectation always carries an exception name or an error code, and
    success-only fields never appear on a failure expectation or the other way round. The
    task-family block adds the three success-only fields :attr:`prompt_text_contains`,
    :attr:`missing_params` and :attr:`params_from_step`.
    """

    success: bool
    exception: str | None
    code: str | None
    message_contains: list[str]
    slot_errors: list[SlotError]
    llm_calls: int | None
    prompt_text_equals_golden: str | None
    metadata: Metadata | None
    params: dict[str, Any]
    contracts: list[str]
    differential: bool
    prompt_text_contains: list[str] = field(default_factory=list)
    missing_params: list[str] | None = None
    params_from_step: int | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "message_contains", list(self.message_contains))
        object.__setattr__(self, "slot_errors", list(self.slot_errors))
        object.__setattr__(self, "params", dict(self.params))
        object.__setattr__(self, "contracts", list(self.contracts))
        object.__setattr__(self, "prompt_text_contains", list(self.prompt_text_contains))
        if self.missing_params is not None:
            object.__setattr__(self, "missing_params", list(self.missing_params))

    @dataclass(frozen=True, slots=True)
    class SlotError:
        """One expected slot error as a slot and code pair."""

        slot: str
        code: str

    @dataclass(frozen=True, slots=True)
    class Metadata:
        """The expected metadata entries of a success expectation."""

        template_uri_echo: str | None
        context_echo: bool | None


@dataclass(frozen=True, slots=True)
class NegotiationCase:
    """One corpus case record expanded for exactly one language.

    The loader expands every corpus record once per entry of its ``languages`` array, appending
    ``/<language>`` to the id; references are already resolved, so the later case engine never
    sees a ``$ref``. A scenario step is also carried as a :class:`NegotiationCase` with the
    derived id ``<scenarioId>/<language>#step-<n>``.
    """

    id: str
    base_id: str
    source_file: str
    api: NegotiationApi
    language: str
    tags: list[str]
    expect: Expectation
    priority: str | None = None
    summary: str | None = None
    context: ContextSpec | None = None
    template_uri: str | None = None
    input_text: str | None = None
    input_data: dict[str, Any] | None = None
    llm: LlmScript | None = None
    prompt: PromptSource.Golden | PromptSource.Text | PromptSource.FromStep | None = None
    schema: dict[str, Any] | None = None
    inject: str | None = None

    def __post_init__(self) -> None:
        for name, value in (
            ("id", self.id),
            ("base_id", self.base_id),
            ("source_file", self.source_file),
            ("api", self.api),
            ("language", self.language),
            ("expect", self.expect),
        ):
            if value is None:
                raise TypeError(f"{type(self).__name__} requires a non-null '{name}'.")
        object.__setattr__(self, "tags", list(self.tags))

    def error_prefix(self) -> str:
        """JSON path prefix for failure messages of this case, naming file and expanded id."""
        return f"{self.source_file} [{self.id}]"


@dataclass(frozen=True, slots=True)
class ScenarioCase:
    """One scenario record expanded for exactly one language.

    A scenario is a multi-step multi-API interaction over the content layer. Every step is a
    full case record (carried as :attr:`ScenarioCase.ScenarioStep.case_data`); the scenario adds
    the step ordering, the acting role, the role descriptions of the closed loop and the
    flow-level expectation.
    """

    id: str
    base_id: str
    source_file: str
    language: str
    roles: list[str]
    steps: list[ScenarioStep]
    summary: str | None = None
    expect_flow: ExpectFlow | None = None
    roles_desc: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for name, value in (
            ("id", self.id),
            ("base_id", self.base_id),
            ("source_file", self.source_file),
            ("language", self.language),
            ("steps", self.steps),
        ):
            if value is None:
                raise TypeError(f"{type(self).__name__} requires a non-null '{name}'.")
        if not self.steps:
            raise ValueError("The scenario must carry at least one step.")
        object.__setattr__(self, "roles", list(self.roles))
        object.__setattr__(self, "steps", list(self.steps))
        object.__setattr__(self, "roles_desc", dict(self.roles_desc))

    def describe_role(self, role: str | None) -> str:
        """Role semantics rendering of one role for failure messages and the run summary."""
        if role is None:
            return "(no role)"
        description = self.roles_desc.get(role)
        return role if description is None else f"{role}={description}"

    def describe_roles(self) -> str:
        """Role semantics line of the whole scenario, or an empty string when it declares none."""
        if not self.roles and not self.roles_desc:
            return ""
        ordered: dict[str, str] = {role: self.roles_desc.get(role, "") for role in self.roles}
        for role, description in self.roles_desc.items():
            ordered.setdefault(role, "")
        return "roles: " + ", ".join(
            role if not description else f"{role}={description}" for role, description in ordered.items()
        )

    @dataclass(frozen=True, slots=True)
    class ScenarioStep:
        """One step of a scenario: the consecutive number, the acting role and the full case."""

        step: int
        role: str | None
        case_data: NegotiationCase

        def __post_init__(self) -> None:
            if self.step < 1:
                raise ValueError("The step number must be at least 1.")
            if self.case_data is None:
                raise TypeError("ScenarioStep requires a non-null 'case_data'.")

    @dataclass(frozen=True, slots=True)
    class ExpectFlow:
        """The flow-level expectation of a scenario."""

        terminal_condition: str | None = None
        rounds_used: int | None = None
        distinct_messages: bool | None = None
        missing_params_filled: int | None = None


@dataclass(frozen=True, slots=True)
class LiveExpectation:
    """The validated live expectation block of one :class:`LiveCase`.

    The live family asserts a key-field subset against the output of a real LLM instead of the
    full offline equivalence: ``params_contains`` is a subset check, ``params_absent`` names the
    slots that must be null or missing, and ``max_llm_calls`` is an upper bound rather than the
    exact call count of the offline :attr:`Expectation.llm_calls` — a real model may trigger
    pipeline-internal retries.
    """

    success: bool
    params_contains: dict[str, Any]
    params_absent: list[str]
    prompt_text_contains: list[str]
    scenario_code: str | None = None
    max_llm_calls: int | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "params_contains", dict(self.params_contains))
        object.__setattr__(self, "params_absent", list(self.params_absent))
        object.__setattr__(self, "prompt_text_contains", list(self.prompt_text_contains))


@dataclass(frozen=True, slots=True)
class LiveCase:
    """One live-LLM corpus case record expanded for exactly one language.

    The sibling of :class:`NegotiationCase` that runs against a real LLM endpoint instead of a
    scripted one: no ``llm`` script, no ``inject`` hook and no typed ``input_data``, and the
    looser :class:`LiveExpectation` instead of the offline expectation block. Live records land
    in :attr:`LoadedCorpus.live_cases`, never in the offline cases list.
    """

    id: str
    base_id: str
    source_file: str
    api: NegotiationApi
    language: str
    tags: list[str]
    live_expect: LiveExpectation
    priority: str | None = None
    summary: str | None = None
    context: ContextSpec | None = None
    template_uri: str | None = None
    input_text: str | None = None
    prompt: PromptSource.Golden | PromptSource.Text | PromptSource.FromStep | None = None
    schema: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        for name, value in (
            ("id", self.id),
            ("base_id", self.base_id),
            ("source_file", self.source_file),
            ("api", self.api),
            ("language", self.language),
            ("live_expect", self.live_expect),
        ):
            if value is None:
                raise TypeError(f"{type(self).__name__} requires a non-null '{name}'.")
        object.__setattr__(self, "tags", list(self.tags))

    def error_prefix(self) -> str:
        """JSON path prefix for failure messages of this case, naming file and expanded id."""
        return f"{self.source_file} [{self.id}]"


@dataclass(frozen=True, slots=True)
class LoadedCorpus:
    """The whole negotiation test corpus loaded from one corpus root.

    The loader fails fast on any format violation, so a successfully loaded corpus is internally
    consistent: ids are globally unique, every ``$ref`` is resolved, every expectation block is
    complete and every record is expanded per language.
    """

    root: Path
    cases: list[NegotiationCase]
    scenarios: list[ScenarioCase]
    live_cases: list[LiveCase]
    shared_responses: dict[str, str]
    shared_schemas: dict[str, Any]

    def __post_init__(self) -> None:
        object.__setattr__(self, "cases", list(self.cases))
        object.__setattr__(self, "scenarios", list(self.scenarios))
        object.__setattr__(self, "live_cases", list(self.live_cases))
        object.__setattr__(self, "shared_responses", dict(self.shared_responses))
        object.__setattr__(self, "shared_schemas", dict(self.shared_schemas))
