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

"""Shared plumbing of the hypothesis property layer (port of Java ``PropertyHarness``).

Production-wired service assembly through the real builder, JSON payload construction and the
scripted-LLM seam reused from the corpus testdata package. Mirroring the
:class:`~tests.corpus.engine.CaseEngine` discipline, everything except the LLM client is production
assembly; the property layer only decides inputs and expected invariants.

The failing-template-loader assembly reuses the corpus engine's
``failingTemplateLoader`` hook wiring (the D31 resource-access seam), so the property layer and the
corpus engine cannot drift apart on how a template miss is injected.
"""

from __future__ import annotations

import json
from typing import Mapping

from a2a_t.core.template_uri import TemplateUri
from a2a_t.llm.provider import LLMClient
from a2a_t.negotiation.generation import NegotiationContentService, NegotiationGenerationOrchestrator
from a2a_t.negotiation.generation.builder import NegotiationGenerationOrchestratorBuilder
from tests.corpus.assemblers import failing_template_loader_access
from tests.corpus.llm_stub import ScriptedNegotiationLlmClient
from tests.corpus.models import LlmFailMarker, LlmScriptStep

__all__ = [
    "MAX_ATTEMPTS",
    "failing",
    "json_payload",
    "object_schema",
    "scripted",
    "semantic_verdict",
    "service",
    "service_with_failing_template_loader",
    "template_uri",
    "type_schema",
]

#: Default attempt limit of the property runs; the retry partition property depends on this exact
#: value (a failure is retried to the limit if and only if its code is retryable).
MAX_ATTEMPTS: int = 3


def service(language: str, llm_client: LLMClient) -> NegotiationContentService:
    """Assemble the production negotiation content service for one property run.

    Args:
        language: message language such as ``zh-CN``.
        llm_client: scripted LLM client of the run.

    Returns:
        production-wired negotiation content service.
    """
    return NegotiationContentService(_orchestrator(language, llm_client))


def service_with_failing_template_loader(language: str, llm_client: LLMClient) -> NegotiationContentService:
    """Assemble the production service with a resource access whose every template load fails.

    The property-layer stand-in of the corpus ``inject: failingTemplateLoader`` hook, wired onto
    the same D31 builder seam the engine uses.

    Args:
        language: message language.
        llm_client: scripted LLM client of the run.

    Returns:
        production-wired service whose template loads always miss.
    """
    builder = NegotiationGenerationOrchestratorBuilder(language=language, llm_client=llm_client)
    builder.max_attempts = MAX_ATTEMPTS
    builder.resource_access = failing_template_loader_access()
    return NegotiationContentService(builder.build())


def _orchestrator(language: str, llm_client: LLMClient) -> NegotiationGenerationOrchestrator:
    """Build the real production orchestrator of one property run through the real builder.

    Args:
        language: message language.
        llm_client: scripted LLM client of the run.

    Returns:
        the built production orchestrator.
    """
    builder = NegotiationGenerationOrchestratorBuilder(language=language, llm_client=llm_client)
    builder.max_attempts = MAX_ATTEMPTS
    return builder.build()


def template_uri(raw: str) -> TemplateUri:
    """Parse one raw template URI string.

    Args:
        raw: template URI such as ``Negotiation-T/information-negotiation/propose/v1``.

    Returns:
        parsed template URI.

    Raises:
        ValueError: when the raw URI is unparseable.
    """
    parsed = TemplateUri.parse(raw)
    if parsed is None:
        raise ValueError(f"Unparseable template URI: {raw}")
    return parsed


def json_payload(value: object) -> str:
    """Serialize one value into the JSON payload text of a scripted LLM step.

    Args:
        value: payload value.

    Returns:
        JSON text.
    """
    return json.dumps(value, ensure_ascii=False)


def semantic_verdict(negotiation_type: str | None, params: Mapping[str, object]) -> str:
    """Build the semantic-validation payload of an accepting verdict.

    Args:
        negotiation_type: negotiation type name the verdict reports, or ``None`` for the
            type-independent abort phase.
        params: extracted parameters the scripted validator returns.

    Returns:
        JSON payload text of the scripted semantic-validation answer.
    """
    return json_payload(
        {
            "semantic_verdict": True,
            "negotiation_type": negotiation_type,
            "errors": [],
            "params": dict(params),
        }
    )


def scripted(payload_json: str) -> ScriptedNegotiationLlmClient:
    """Create a scripted client answering exactly one payload step.

    Args:
        payload_json: payload text of the single answer.

    Returns:
        strictly consuming scripted client.
    """
    return ScriptedNegotiationLlmClient([LlmScriptStep.Payload(payload_json)])


def failing(marker: LlmFailMarker) -> ScriptedNegotiationLlmClient:
    """Create a scripted client replaying one failure marker for every attempt of the run.

    Args:
        marker: failure marker to replay.

    Returns:
        strictly consuming scripted client that always fails.
    """
    return ScriptedNegotiationLlmClient([LlmScriptStep.Fail(marker)] * MAX_ATTEMPTS)


def object_schema(properties: Mapping[str, object]) -> dict[str, object]:
    """Build a flat object JSON Schema over the given properties.

    Args:
        properties: per-key type schemas keyed by parameter name.

    Returns:
        caller parameter schema of a validate run.
    """
    return {"type": "object", "properties": dict(properties)}


def type_schema(value: object) -> dict[str, object]:
    """Derive the JSON Schema type of one parameter value.

    Args:
        value: parameter value generated by a property.

    Returns:
        single-key type schema.
    """
    if isinstance(value, bool):  # bool first: bool is an int subclass in Python
        return {"type": "boolean"}
    if isinstance(value, str):
        return {"type": "string"}
    if isinstance(value, float):
        return {"type": "number"}
    return {"type": "integer"}
