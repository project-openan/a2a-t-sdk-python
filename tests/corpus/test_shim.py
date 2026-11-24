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

"""Meta tests of the D22 Java exception-name shim table.

The corpus JSON files are frozen by policy and carry the *Java* exception class names in
``expect.exception``, so the engine translates each expected name through
:mod:`tests.corpus.shim` instead of the fixtures. These tests pin the two load-bearing properties
of that translation together:

* **completeness** — every Java exception name the shipped corpus names resolves through the
  table (scanned from the corpus JSON files at collection time, so a newly added case naming an
  unmapped Java name fails here first, not as a silent pass in the engine);
* **resolvability** — every table entry either carries a real Python exception class of the SDK's
  own tree, or is a deliberately collapsed Java type (``python_type is None``) that can only be
  asserted through its paired error-code expectation and carries a note saying why.

An unknown name must raise :class:`ValueError` — the engine error channel — never match nothing.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from tests.corpus.conftest import CORPUS_ROOT
from tests.corpus.engine import CaseEngine
from tests.corpus.models import (
    Expectation,
    LlmFailMarker,
    LlmScript,
    LlmScriptStep,
    NegotiationApi,
    NegotiationCase,
)
from tests.corpus.shim import (
    JAVA_EXCEPTION_SHIMS,
    JavaExceptionShim,
    java_exception_names,
    resolve_java_exception,
)

#: Names a corpus expectation must never name: the deprecated Java negotiation demo state machine.
_DEPRECATED_JAVA_NAMES = ("NegotiationStateException",)


def corpus_exception_names() -> tuple[str, ...]:
    """Collect every ``expect.exception`` Java class name of the shipped corpus, in discovery order."""
    names: list[str] = []
    for path in sorted(CORPUS_ROOT.glob("**/*.json")):
        for record in json.loads(path.read_text(encoding="utf-8")):
            name = _expectation_field(record, "exception")
            if name is not None and name not in names:
                names.append(name)
        for scenario in record.get("steps", []) if isinstance(record, dict) else []:
            name = _expectation_field(scenario, "exception")
            if name is not None and name not in names:
                names.append(name)
    return tuple(names)


def _expectation_field(record: Any, field: str) -> str | None:
    """Read one field of a record's expectation block, tolerating the scenario step shape."""
    if not isinstance(record, dict):
        return None
    expect = record.get("expect")
    if isinstance(expect, dict) and isinstance(expect.get(field), str):
        return expect[field]
    return None


#: Every Java exception name the shipped corpus names (collected at collection time).
CORPUS_EXCEPTION_NAMES: tuple[str, ...] = corpus_exception_names()


class TestShimCompleteness:
    """Every Java name a corpus expectation can name resolves through the table."""

    def test_the_corpus_names_java_exceptions_at_all(self) -> None:
        assert CORPUS_EXCEPTION_NAMES, "the corpus scan found no expect.exception names; fix the scan"

    @pytest.mark.parametrize("java_name", CORPUS_EXCEPTION_NAMES, ids=CORPUS_EXCEPTION_NAMES)
    def test_every_corpus_java_name_has_a_mapping(self, java_name: str) -> None:
        assert resolve_java_exception(java_name).java_name == java_name

    @pytest.mark.parametrize(
        ("java_name", "expected_type_name"),
        [
            ("NegotiationGenerationException", "NegotiationGenerationError"),
            ("NegotiationParamExtractionException", "NegotiationParamExtractionError"),
            ("IllegalArgumentException", "ValueError"),
            ("NullPointerException", "TypeError"),
        ],
        ids=["generation", "param-extraction", "iae-parity", "npe-parity"],
    )
    def test_the_corpus_names_map_to_their_python_carriers(self, java_name: str, expected_type_name: str) -> None:
        shim = resolve_java_exception(java_name)

        assert shim.python_type is not None, f"{java_name} must carry a Python exception type"
        assert shim.python_type.__name__ == expected_type_name

    def test_the_table_covers_the_hand_built_engine_test_names(self) -> None:
        for java_name in ("ContentValidationException", "PromptGenerationException"):
            assert resolve_java_exception(java_name).python_type is not None

    def test_the_deprecated_java_demo_names_stay_absent(self) -> None:
        assert not set(_DEPRECATED_JAVA_NAMES) & set(java_exception_names()), (
            "the deprecated Java negotiation demo names code that was never ported and can never surface"
        )


#: The two stdlib parity carriers of the Java programming errors: NPE → TypeError, IAE/ISE →
#: ValueError — deliberately kept outside the coded business tree (D22, the plan's §7.5).
_PARITY_CARRIERS: tuple[type[BaseException], ...] = (TypeError, ValueError)

#: Java exceptions that extend plain ``RuntimeException`` in the Java tree too: internal pipeline
#: failures the public APIs translate, never surface raw. Python mirrors that hierarchy choice
#: (``NegotiationValidationError(RuntimeError)``), so these are the only non-coded carriers.
_INTERNAL_PIPELINE_CARRIERS: frozenset[str] = frozenset({"NegotiationValidationException"})


class TestShimResolvability:
    """Every table entry resolves in the Python exception tree or is a documented collapse."""

    @pytest.mark.parametrize("shim", JAVA_EXCEPTION_SHIMS, ids=[shim.java_name for shim in JAVA_EXCEPTION_SHIMS])
    def test_every_entry_resolves_in_the_sdk_exception_tree(self, shim: JavaExceptionShim) -> None:
        if shim.python_type is None:
            assert shim.note, f"{shim.java_name} is collapsed and must carry a note explaining the code-only assertion"
            return
        assert issubclass(shim.python_type, BaseException), (
            f"{shim.java_name} must map onto an exception class, not {shim.python_type!r}"
        )
        module = shim.python_type.__module__
        assert module.startswith("a2a_t.") or shim.python_type in _PARITY_CARRIERS, (
            f"{shim.java_name} must map onto the SDK's own exception tree or one of the two stdlib "
            f"parity carriers, but points at {module}.{shim.python_type.__name__}"
        )

    def test_the_coded_entries_are_business_errors_or_documented_exceptions(self) -> None:
        """Every entry maps onto an A2ATError member, a parity carrier or an internal carrier."""
        from a2a_t.core.errors.exceptions import A2ATError

        for shim in JAVA_EXCEPTION_SHIMS:
            if shim.python_type is None or shim.python_type in _PARITY_CARRIERS:
                continue
            if shim.java_name in _INTERNAL_PIPELINE_CARRIERS:
                assert "nternal" in shim.note, (
                    f"{shim.java_name} is not an A2ATError member, so its note must document the "
                    "internal-pipeline role of the carrier"
                )
                continue
            assert issubclass(shim.python_type, A2ATError), (
                f"{shim.java_name} maps onto {shim.python_type.__name__} which is neither an A2ATError "
                "subtree member, one of the two stdlib parity carriers nor a registered internal "
                "pipeline carrier"
            )

    def test_the_table_names_are_unique(self) -> None:
        names = [shim.java_name for shim in JAVA_EXCEPTION_SHIMS]

        assert len(names) == len(set(names))

    def test_the_collapsed_entries_name_a_paired_code_assertion(self) -> None:
        collapsed = [shim for shim in JAVA_EXCEPTION_SHIMS if shim.python_type is None]

        assert collapsed, "the table must keep the collapsed Java types documented, not drop them"
        for shim in collapsed:
            assert "code" in shim.note, f"{shim.java_name} must document the code-only assertion"


class TestUnknownJavaNameIsAnEngineError:
    """An unknown Java name fails loudly — never a silent pass (D22)."""

    @pytest.mark.parametrize(
        "java_name",
        ["NotARealJavaException", "IllegalArgumentException ", "", "negotiationgenerationexception"],
        ids=["typo", "whitespace", "empty", "wrong-case"],
    )
    def test_resolving_an_unknown_name_raises(self, java_name: str) -> None:
        with pytest.raises(ValueError, match="Unknown Java exception name"):
            resolve_java_exception(java_name)

    def test_the_engine_surfaces_the_unknown_name_as_an_engine_error(self) -> None:
        test_case = _failing_case(exception="NotARealJavaException", code=None)

        with pytest.raises(ValueError, match="NotARealJavaException"):
            CaseEngine().run(test_case)

    def test_the_engine_rejects_a_collapsed_name_without_a_paired_code(self) -> None:
        test_case = _failing_case(exception="NegotiationRenderException", code=None)

        with pytest.raises(ValueError, match="NegotiationRenderException"):
            CaseEngine().run(test_case)


def _failing_case(*, exception: str | None, code: str | None) -> NegotiationCase:
    """Build one from-text case whose run fails with ``llm.response_invalid`` after three attempts.

    The failure is real production behavior (the extraction leg retries the unparseable scripted
    answer up to the builder default of three attempts), so the engine reaches the exception-name
    comparison of the expectation — the shim seam under test.
    """
    return NegotiationCase(
        id="SHIM-01/zh-CN",
        base_id="SHIM-01",
        source_file="inline/shim.json",
        api=NegotiationApi.GENERATE_ACCEPT_FROM_TEXT,
        language="zh-CN",
        tags=[],
        expect=Expectation(
            success=False,
            exception=exception,
            code=code,
            message_contains=[],
            slot_errors=[],
            llm_calls=3,
            prompt_text_equals_golden=None,
            metadata=None,
            params={},
            contracts=[],
            differential=False,
        ),
        priority="P0",
        summary=None,
        context=None,
        template_uri="Negotiation-T/information-negotiation/accept-reject/v1",
        input_text="请接受。",
        input_data=None,
        llm=LlmScript(
            [
                LlmScriptStep.Fail(LlmFailMarker.NON_JSON),
                LlmScriptStep.Fail(LlmFailMarker.NON_JSON),
                LlmScriptStep.Fail(LlmFailMarker.NON_JSON),
            ]
        ),
        prompt=None,
        schema=None,
        inject=None,
    )
