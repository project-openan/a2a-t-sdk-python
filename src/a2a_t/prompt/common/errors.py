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


class PromptLoaderError(A2ATError):
    """Base class for prompt loading errors that carry source context.

    Part of the :class:`~a2a_t.core.errors.exceptions.A2ATError` tree. The class-level default
    code is ``infra.resource_read_failed``; orchestrator boundaries translate it to the
    pipeline-specific catalog code (``template.load_failed`` on the client,
    ``infra.resource_read_failed`` on the server).
    """

    def __init__(
        self,
        message: str,
        *,
        code: ErrorCatalog = ErrorCatalog.INFRA_RESOURCE_READ_FAILED,
        **context: object,
    ) -> None:
        super().__init__(message, code=code)
        self.context = context


class PromptSourceError(PromptLoaderError):
    """Raised when a prompt cannot be read from its configured source."""

    pass


class PromptConfigError(PromptLoaderError):
    """Raised when prompt loading configuration is invalid."""

    pass


class PromptFetchError(PromptLoaderError):
    """Raised when prompt content cannot be fetched from the source."""

    pass


class PromptParseError(PromptLoaderError):
    """Raised when prompt content cannot be parsed."""

    pass


class PromptMetadataError(PromptLoaderError):
    """Raised when prompt metadata is missing or malformed."""

    pass


class PromptCacheError(PromptLoaderError):
    """Raised when prompt cache access fails."""

    pass


class PromptConflictError(PromptLoaderError):
    """Raised when conflicting prompt content cannot be reconciled."""

    pass


class PromptVersionComparisonError(PromptLoaderError):
    """Raised when prompt versions cannot be compared safely."""

    pass


class PromptCatalogRegistryError(PromptLoaderError):
    """Raised when prompt catalog or registry resolution fails."""

    pass


class TaskPromptFormatError(ValueError):
    """Describe a task prompt front-matter formatting error.

    Deliberately outside the :class:`~a2a_t.core.errors.exceptions.A2ATError` tree: a malformed
    front matter is a caller-contract violation, which stays a :class:`ValueError` (the Java
    ``IllegalArgumentException`` counterpart, D3).
    """

    def __init__(self, message: str, *, field: str | None = None) -> None:
        super().__init__(message)
        self.field = field
