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

from __future__ import annotations

from a2a_t.core.errors.catalog import ErrorCatalog
from a2a_t.core.errors.exceptions import A2ATBusinessError


class TaskPromptRenderError(A2ATBusinessError):
    """Raised when a task prompt cannot be rendered.

    Java parity (``TaskPromptRenderException``): carries the ``template.render_failed`` code with
    the concrete rendering problem as the ``reason`` fact. The template URI is not known to the
    renderer and is supplied by the orchestrator that catches this error.
    """

    def __init__(self, message: str) -> None:
        super().__init__(ErrorCatalog.TEMPLATE_RENDER_FAILED, {"reason": message}, message=message)
