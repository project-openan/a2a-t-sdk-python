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

"""Caller-provided parameter schema for ``validate_task_prompt_and_data_filling``.

The schema is shared with the client's negotiation round (which regenerates the filled Task-T prompt
from it) and therefore lives in ``common.task_schema``; this module re-exports it so the server-side
imports stay unchanged.
"""

from __future__ import annotations

from common.task_schema import TASK_PARAM_SCHEMA

__all__ = ["TASK_PARAM_SCHEMA"]
