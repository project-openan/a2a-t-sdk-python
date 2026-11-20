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
from a2a_t.core.errors.exceptions import A2ATError
from a2a_t.core.errors.exceptions import ConfigFileNotFoundError as _CoreConfigFileNotFoundError

__all__ = ["ConfigError", "ConfigFileNotFoundError"]


class ConfigError(A2ATError):
    """Base error for configuration loading (``infra.config_invalid``)."""

    def __init__(
        self,
        message: str,
        *,
        code: ErrorCatalog = ErrorCatalog.INFRA_CONFIG_INVALID,
        cause: BaseException | None = None,
    ) -> None:
        super().__init__(message, code=code, cause=cause)


# The file-not-found failure is the shared core exception: it renders its message from the
# ``infra.config_invalid`` template with the missing path as the ``key`` fact (Java parity), and
# re-exporting it keeps ``a2a_t.config.errors.ConfigFileNotFoundError`` importable for the legacy
# raise and catch sites.
ConfigFileNotFoundError = _CoreConfigFileNotFoundError
