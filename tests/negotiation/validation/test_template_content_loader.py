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

"""Direct tests of the template loading gate of the validation pipeline.

The loader is the D31 replacement of the Java builder's inline lambda over the negotiation template
loader: a template miss surfaced by the common resource access layer — the coded
``template.not_found`` / ``template.load_failed`` business failures or a raw
:class:`~a2a_t.core.errors.exceptions.ResourceNotFoundError` of an injected access implementation —
is translated to a :class:`~a2a_t.core.errors.exceptions.ResourceNotFoundError` carrying the
reference's template URI, which the shared pipeline maps to the ``template.not_found`` catalog code.
Every other business failure passes through unchanged.
"""

from __future__ import annotations

from typing import Any

import pytest

from a2a_t.core.errors.catalog import ErrorCatalog
from a2a_t.core.errors.exceptions import A2ATBusinessError, ResourceNotFoundError
from a2a_t.core.metadata import NegotiationPerformative
from a2a_t.negotiation.content.enums import NegotiationType
from a2a_t.negotiation.resources.reference import NegotiationReference
from a2a_t.negotiation.validation.param_extractor import NegotiationTemplateContentLoader

REFERENCE = NegotiationReference(NegotiationType.INFORMATION, NegotiationPerformative.PROPOSE, "zh-CN")

TEMPLATE_BODY = "# template body\n"


class FakeAccess:
    """Resource access fake returning one body or raising one scripted failure."""

    def __init__(self, failure: BaseException | None = None) -> None:
        self.failure = failure
        self.calls: list[tuple[str, str]] = []

    def template_text(self, template_uri: Any, language: str) -> str:
        self.calls.append((str(template_uri), language))
        if self.failure is not None:
            raise self.failure
        return TEMPLATE_BODY


def test_loads_the_template_body_of_the_reference() -> None:
    access = FakeAccess()

    body = NegotiationTemplateContentLoader(access).load(REFERENCE)

    assert body == TEMPLATE_BODY
    assert access.calls == [(REFERENCE.uri, "zh-CN")]


@pytest.mark.parametrize(
    ("code", "facts"),
    [
        (ErrorCatalog.TEMPLATE_NOT_FOUND, {"template_uri": REFERENCE.uri, "language": "zh-CN"}),
        (ErrorCatalog.TEMPLATE_LOAD_FAILED, {"resource_path": "templates/Negotiation-T"}),
    ],
    ids=["template-not-found", "template-load-failed"],
)
def test_template_misses_translate_to_resource_not_found_carrying_the_reference_uri(
    code: ErrorCatalog, facts: dict[str, str]
) -> None:
    access = FakeAccess(failure=A2ATBusinessError(code, facts))

    with pytest.raises(ResourceNotFoundError) as excinfo:
        NegotiationTemplateContentLoader(access).load(REFERENCE)

    assert excinfo.value.resource_path == REFERENCE.uri
    assert excinfo.value.__cause__ is access.failure


def test_raw_resource_misses_are_retagged_with_the_reference_uri() -> None:
    access = FakeAccess(failure=ResourceNotFoundError("missing", "some/other/path"))

    with pytest.raises(ResourceNotFoundError) as excinfo:
        NegotiationTemplateContentLoader(access).load(REFERENCE)

    assert excinfo.value.resource_path == REFERENCE.uri


def test_unrelated_business_failures_pass_through_unchanged() -> None:
    access = FakeAccess(failure=A2ATBusinessError(ErrorCatalog.INFRA_RESOURCE_READ_FAILED, {"resource_path": "x"}))

    with pytest.raises(A2ATBusinessError) as excinfo:
        NegotiationTemplateContentLoader(access).load(REFERENCE)

    assert excinfo.value.code is ErrorCatalog.INFRA_RESOURCE_READ_FAILED


def test_null_access_is_rejected() -> None:
    with pytest.raises(TypeError):
        NegotiationTemplateContentLoader(None)  # type: ignore[arg-type]
