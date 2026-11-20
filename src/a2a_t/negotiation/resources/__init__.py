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

"""Negotiation resource domain types.

Port of the Java ``a2a-t-negotiation`` ``resources`` package reduced to its domain type: template
loading is owned by the common resource access layer (D31), so this package carries only
:class:`NegotiationReference` and its addressing validation — no IO, cache or path assembly.
"""

from __future__ import annotations

from .reference import COMMON_TYPE_SEGMENT, NegotiationReference, uri_segment_of

__all__ = ["COMMON_TYPE_SEGMENT", "NegotiationReference", "uri_segment_of"]
