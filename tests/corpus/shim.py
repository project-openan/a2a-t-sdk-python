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

"""Java exception-name shim of the frozen corpus expectations (port decision D22).

The corpus JSON files are frozen by policy — they carry the *Java* exception class names
(``IllegalArgumentException``, ``NegotiationGenerationException``, ...) in
``expect.exception`` — while the Python SDK raises its own exception tree
(:mod:`a2a_t.core.errors.exceptions`). Mirroring the Java harness decision ("freeze the corpus,
translate in the engine"), this module is the single translation table: the engine resolves each
expected Java name into the Python exception type to assert instead.

Rules the engine and the meta test pin together:

* an unknown Java name is an **engine error**, never a silent pass — a typo'd expectation fails
  loudly instead of matching nothing;
* every table entry either resolves to a real Python exception class, or is a *collapsed* Java
  type whose Python carrier no longer exists as an exception (``python_type is None``) — those can
  only be asserted through the paired ``expect.code`` entry, and an expectation naming them
  without a code is an engine error too;
* the Java stdlib programming errors map onto the deliberate Python parity carriers
  (``NullPointerException`` → :class:`TypeError`, ``IllegalArgumentException`` /
  ``IllegalStateException`` → :class:`ValueError`), which the port keeps outside the coded
  business tree.

The table also covers the Java names the hand-written engine tests use
(``ContentValidationException``, ``PromptGenerationException``), so inline cases and corpus cases
assert through one seam. Names of the deprecated Java negotiation demo (``NegotiationStateException``)
and of the sample-internal exceptions are intentionally absent: they name code that was never
ported and can therefore never surface.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from a2a_t.core.errors.exceptions import (
    A2ATBusinessError,
    A2ATError,
    A2ATParamExtractionError,
    ConfigFileNotFoundError,
    ContentValidationError,
    NegotiationGenerationError,
    NegotiationParamExtractionError,
    PromptGenerationError,
    ResourceNotFoundError,
)
from a2a_t.llm.errors import LLMConfigError, LLMError, LLMRuntimeError
from a2a_t.negotiation.validation.semantic_validator import NegotiationValidationError
from a2a_t.prompt.analysis.errors import ScenarioRecognitionError
from a2a_t.prompt.task_rendering.errors import TaskPromptRenderError

__all__ = [
    "JAVA_EXCEPTION_SHIMS",
    "JavaExceptionShim",
    "java_exception_names",
    "resolve_java_exception",
]


@dataclass(frozen=True)
class JavaExceptionShim:
    """One mapping of a Java exception class name onto its Python assertion carrier.

    Attributes:
        java_name: Java simple class name as it appears in ``expect.exception``.
        python_type: Python exception class the engine asserts with ``isinstance``; ``None`` when
            the Java type was collapsed away in the Python tree and only the paired error-code
            expectation can assert the failure.
        note: why this mapping is the faithful one (for the meta test's failure messages).
    """

    java_name: str
    python_type: type[BaseException] | None
    note: str


#: The D22 shim table: every Java exception name a corpus or engine-test expectation can name.
JAVA_EXCEPTION_SHIMS: Final[tuple[JavaExceptionShim, ...]] = (
    # ---- Java stdlib programming errors: the deliberate Python parity carriers ----
    JavaExceptionShim(
        "NullPointerException",
        TypeError,
        "Java NPE parity: a None argument is a programming error raised as TypeError.",
    ),
    JavaExceptionShim(
        "IllegalArgumentException",
        ValueError,
        "Java IAE parity: a malformed argument is a programming error raised as ValueError.",
    ),
    JavaExceptionShim(
        "IllegalStateException",
        ValueError,
        "Wiring errors stay outside the coded business tree (builder parity: ValueError).",
    ),
    # ---- core exception tree (same names modulo the Exception/Error renaming) ----
    JavaExceptionShim("A2ATError", A2ATError, "Root of the coded tree, same name."),
    JavaExceptionShim(
        "A2ATBusinessException",
        A2ATBusinessError,
        "Business base; Java's Exception suffix became Error in Python.",
    ),
    JavaExceptionShim(
        "A2ATParamExtractionError",
        A2ATParamExtractionError,
        "Prompt-family parameter extraction failure, same name.",
    ),
    JavaExceptionShim(
        "ContentValidationException",
        ContentValidationError,
        "Content validation pipeline failure (task-family validate leg).",
    ),
    JavaExceptionShim(
        "PromptGenerationException",
        PromptGenerationError,
        "MetadataContent pipeline failure (task-family generation leg).",
    ),
    JavaExceptionShim(
        "ResourceNotFoundException",
        ResourceNotFoundError,
        "Resource resolution failure, same name modulo the suffix.",
    ),
    JavaExceptionShim(
        "ConfigFileNotFoundException",
        ConfigFileNotFoundError,
        "Missing configuration file, same name modulo the suffix.",
    ),
    # ---- negotiation exceptions ----
    JavaExceptionShim(
        "NegotiationProcessingException",
        A2ATBusinessError,
        "Java's intermediate negotiation base was collapsed; assert the business base.",
    ),
    JavaExceptionShim(
        "NegotiationGenerationException",
        NegotiationGenerationError,
        "Negotiation generation failure (from-data / from-text legs).",
    ),
    JavaExceptionShim(
        "NegotiationParamExtractionException",
        NegotiationParamExtractionError,
        "Negotiation validate-leg failure carrying the slot errors.",
    ),
    JavaExceptionShim(
        "NegotiationValidationException",
        NegotiationValidationError,
        "Internal semantic-validator contract failure, same name modulo the suffix.",
    ),
    JavaExceptionShim(
        "NegotiationRenderException",
        None,
        "Package-private Java internal, wrapped into template.render_failed by the orchestrator; "
        "assert through the paired error code only.",
    ),
    # ---- LLM exceptions (same names) ----
    JavaExceptionShim("LLMError", LLMError, "LLM base failure, same name."),
    JavaExceptionShim("LLMConfigError", LLMConfigError, "LLM configuration failure, same name."),
    JavaExceptionShim("LLMRuntimeError", LLMRuntimeError, "LLM runtime failure, same name."),
    # ---- prompt pipeline exceptions ----
    JavaExceptionShim(
        "ScenarioRecognitionException",
        ScenarioRecognitionError,
        "Scenario recognition failure (Java Exception suffix became Error).",
    ),
    JavaExceptionShim(
        "TaskPromptRenderException",
        TaskPromptRenderError,
        "Task prompt rendering failure (Java Exception suffix became Error).",
    ),
    JavaExceptionShim(
        "PromptComplianceCheckException",
        None,
        "The Python facade surfaces compliance failures as the Result failure dataclass (D3 "
        "dual-track); assert through the paired error code only.",
    ),
)


#: Java names the shim table resolves, keyed for the engine's lookup.
_SHIMS_BY_JAVA_NAME: Final[dict[str, JavaExceptionShim]] = {shim.java_name: shim for shim in JAVA_EXCEPTION_SHIMS}


def resolve_java_exception(java_name: str | None) -> JavaExceptionShim:
    """Resolve one expected Java exception class name into its shim entry.

    Args:
        java_name: Java simple class name from ``expect.exception``.

    Returns:
        the shim entry carrying the Python assertion carrier.

    Raises:
        ValueError: when the name is not in the table — an unknown Java exception name is an
            engine error, never a silent pass (D22).
    """
    shim = _SHIMS_BY_JAVA_NAME.get(java_name or "")
    if shim is None:
        raise ValueError(
            f"Unknown Java exception name {java_name!r}: the corpus expectation is frozen with "
            f"Java class names and this name matches none of the {len(JAVA_EXCEPTION_SHIMS)} "
            f"registered shim entries {java_exception_names()}."
        )
    return shim


def java_exception_names() -> tuple[str, ...]:
    """Return every Java exception name the shim table resolves, in table order."""
    return tuple(shim.java_name for shim in JAVA_EXCEPTION_SHIMS)
