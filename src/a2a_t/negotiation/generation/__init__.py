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

"""Negotiation generation pipeline (port of the Java ``a2a-t-negotiation`` ``generation`` package).

Deterministic from-data generation (the five function-style generators, their ``(type,
performative)`` registry, the drop-policy render step, the item formatter and the LLM message-list
assembly) plus the single-step LLM content extraction of the from-text leg, the extraction schema
builder, the orchestrator assembling both legs with the error-translation catch points, its builder
and the 12-method :class:`~a2a_t.negotiation.generation.content_service.NegotiationContentService`
facade shared by the client and server facades.
"""

from __future__ import annotations

from .builder import NegotiationGenerationOrchestratorBuilder, builder
from .content_extractor import DefaultNegotiationContentExtractor, NegotiationContentExtractor
from .content_service import NegotiationContentService
from .generators import (
    GENERATOR_FUNCTIONS,
    NegotiationGenerator,
    generate_abort,
    generate_ending,
    generate_feasibility_propose,
    generate_information_propose,
    generate_target_propose,
    resolve,
)
from .item_formatter import format_items
from .message_builder import (
    TOKEN_INPUT,
    TOKEN_NEGOTIATION_TYPE,
    TOKEN_PHASE,
    TOKEN_SCHEMA,
    TOKEN_TEMPLATE_URI,
    build_messages,
)
from .orchestrator import NegotiationGenerationOrchestrator, NegotiationParamExtractor
from .prompt_renderer import NegotiationRenderError, render

__all__ = [
    "GENERATOR_FUNCTIONS",
    "DefaultNegotiationContentExtractor",
    "NegotiationContentExtractor",
    "NegotiationContentService",
    "NegotiationGenerationOrchestrator",
    "NegotiationGenerationOrchestratorBuilder",
    "NegotiationGenerator",
    "NegotiationParamExtractor",
    "NegotiationRenderError",
    "TOKEN_INPUT",
    "TOKEN_NEGOTIATION_TYPE",
    "TOKEN_PHASE",
    "TOKEN_SCHEMA",
    "TOKEN_TEMPLATE_URI",
    "build_messages",
    "builder",
    "format_items",
    "generate_abort",
    "generate_ending",
    "generate_feasibility_propose",
    "generate_information_propose",
    "generate_target_propose",
    "render",
    "resolve",
]
