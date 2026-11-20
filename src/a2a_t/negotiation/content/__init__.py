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

"""Negotiation content model: typed contents, enums, vocabulary and shared content validation.

Port of the Java ``a2a-t-negotiation`` ``content`` package. Zero LLM, pure data plus validation:
the frozen dataclasses mirror the Java records as unvalidated carriers, the enums pin the
negotiation type / feasibility action / three-value conclusion sets, the domain vocabulary wraps
the common resource access layer (D31), and :mod:`confirm_request` hosts the single shared
confirm-request mutual-exclusion validation both generation call sites consume (D14).
"""

from __future__ import annotations

from .confirm_request import ConfirmRequestStyle, has_items, present_text, validate_confirm_request
from .enums import NegotiationAction, NegotiationConclusion, NegotiationType
from .models import (
    FeasibilityEndingContent,
    FeasibilityProposeContent,
    InformationEndingContent,
    InformationProposeContent,
    NegotiationAbortContent,
    NegotiationAbortData,
    NegotiationContent,
    NegotiationEndingContent,
    NegotiationEndingData,
    NegotiationItem,
    NegotiationProposeContent,
    NegotiationProposeData,
    TargetEndingContent,
    TargetProposeContent,
)
from .vocabulary import CANONICAL_KEYS, Vocabulary

__all__ = [
    "CANONICAL_KEYS",
    "ConfirmRequestStyle",
    "FeasibilityEndingContent",
    "FeasibilityProposeContent",
    "InformationEndingContent",
    "InformationProposeContent",
    "NegotiationAbortContent",
    "NegotiationAbortData",
    "NegotiationAction",
    "NegotiationConclusion",
    "NegotiationContent",
    "NegotiationEndingContent",
    "NegotiationEndingData",
    "NegotiationItem",
    "NegotiationProposeContent",
    "NegotiationProposeData",
    "NegotiationType",
    "TargetEndingContent",
    "TargetProposeContent",
    "Vocabulary",
    "has_items",
    "present_text",
    "validate_confirm_request",
]
