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

"""Generic JSON prompt-resource loading with strict parsing, plus the routed read seam (D31).

Port of the Java ``PromptResourceJsonParser`` and ``ResourceReadErrors`` utilities, generalized for
every JSON resource of the prompt resource tree: slot schemas (via
:mod:`~a2a_t.common.prompt_resources.template_source`), scenario catalogs, the negotiation
vocabulary (via :mod:`~a2a_t.common.prompt_resources.vocabulary`) and the error message catalogs.
Parsing is strict where the Java parsers were lenient by accident: the document root must be a JSON
object and duplicate object keys fail fast through an ``object_pairs_hook``, because a duplicated
key in a resource file is a resource defect that must surface at load time, not a value to silently
overwrite.

This module also hosts :class:`ResourceReader`, the read seam routed by
:mod:`~a2a_t.common.prompt_resources.resource_access`. The packaged reader
(:class:`~a2a_t.common.prompt_resources.packaged_access.PackagedResourceReader`) and the local
snapshot (:class:`~a2a_t.common.prompt_resources.local_file_access.LocalResourceSnapshot`) both
satisfy it, which is what keeps the generic loaders source-agnostic — the single resource access
layer required by decision D31.
"""

from __future__ import annotations

import json
from typing import Any, Final, Protocol, runtime_checkable

from a2a_t.core.errors.catalog import ErrorCatalog
from a2a_t.core.errors.exceptions import A2ATError, ResourceNotFoundError
from a2a_t.core.errors.messages import render
from a2a_t.core.prompt_resource_key import PromptResourceKey

from .models import ScenarioDefinition

__all__ = [
    "DuplicateJsonKeyError",
    "ResourceReader",
    "error_catalog_key",
    "load_error_messages",
    "load_scenarios",
    "parse_json_document",
    "parse_json_object",
    "read_failed",
    "scenario_catalog_key",
]

_SCENARIO_FIELDS: Final[tuple[str, ...]] = ("scenario_code", "scenario_name", "description", "example")


@runtime_checkable
class ResourceReader(Protocol):
    """Read seam the resource access layer routes for one source.

    Implementations: the packaged reader (module-level frozen cache, missing never cached) and the
    local snapshot (captured once at construction, D9). Both expose the same three operations so the
    generic loaders never care which source is active.
    """

    def read_text(self, key: PromptResourceKey) -> str:
        """Read one UTF-8 text resource.

        Args:
            key: resource key identifying the file under ``prompt_resources/``.

        Returns:
            the text payload of the resource.

        Raises:
            ResourceNotFoundError: when the resource does not exist for this source.
        """
        ...

    def category_types(self, category: str) -> tuple[str, ...]:
        """Return the first-level directory names available under one routed category.

        Args:
            category: category directory under ``prompt_resources/``, such as ``templates``.

        Returns:
            the sorted directory names; empty when the category does not exist.
        """
        ...

    def category_files(self, category: str, file_name: str) -> dict[str, str]:
        """Return every file of one routed category matching a file name, walking the whole subtree.

        Args:
            category: category directory under ``prompt_resources/``, such as ``templates``.
            file_name: file name of the payloads to collect, such as ``template.md``.

        Returns:
            a mapping of category-relative path (forward slashes) to the UTF-8 text payload.
        """
        ...


class DuplicateJsonKeyError(ValueError):
    """Raised when one JSON document defines the same object key twice.

    Attributes:
        key: the JSON object key that appeared more than once.
    """

    def __init__(self, key: str, origin: str | None = None) -> None:
        """Remember the duplicated key and the resource it occurred in.

        Args:
            key: the JSON object key that appeared more than once.
            origin: resource path the document was parsed from, when known.
        """
        self.key = key
        message = f"Duplicate JSON object key '{key}'"
        if origin:
            message += f" in resource '{origin}'"
        super().__init__(message + ".")


def parse_json_document(text: str, *, origin: str) -> Any:
    """Parse one JSON document with duplicate-key detection.

    Args:
        text: raw JSON payload of the resource.
        origin: resource path used in failure messages.

    Returns:
        the parsed document (object, array or scalar).

    Raises:
        DuplicateJsonKeyError: when the document defines one object key twice.
        ValueError: when the document is not valid JSON.
    """
    try:
        return json.loads(text, object_pairs_hook=_reject_duplicate_keys)
    except DuplicateJsonKeyError as error:
        raise DuplicateJsonKeyError(error.key, origin) from error
    except json.JSONDecodeError as error:
        raise ValueError(
            f"Prompt resource '{origin}' is not valid JSON: {error.msg} (line {error.lineno}, column {error.colno})."
        ) from error


def parse_json_object(text: str, *, origin: str) -> dict[str, Any]:
    """Parse one JSON document that must carry an object at the root.

    Args:
        text: raw JSON payload of the resource.
        origin: resource path used in failure messages.

    Returns:
        the parsed object.

    Raises:
        ValueError: when the document is not valid JSON or the root is not an object.
    """
    document = parse_json_document(text, origin=origin)
    if not isinstance(document, dict):
        raise ValueError(
            f"Prompt resource '{origin}' must carry a JSON object at the root but was {type(document).__name__}."
        )
    return document


def read_failed(
    resource_path: str,
    language: str | None = None,
    cause: BaseException | None = None,
    *,
    detail: str | None = None,
) -> A2ATError:
    """Create an ``infra.resource_read_failed`` failure for one resource path.

    Port of the Java ``ResourceReadErrors.readFailed`` factory: the message is rendered from the
    code's template with ``resource_path`` as the only fact, and an optional detail sentence is
    appended to carry the specific failure mode. Without an explicit detail the cause's message
    becomes the detail, so a wrapped parse failure stays visible in the raised error.

    Args:
        resource_path: path of the resource that could not be read.
        language: language used to render the failure message; ``None`` falls back to ``en-US``.
        cause: root cause, if any.
        detail: optional sentence describing the specific failure mode.

    Returns:
        infra failure carrying the ``infra.resource_read_failed`` code.
    """
    message = render(ErrorCatalog.INFRA_RESOURCE_READ_FAILED, {"resource_path": resource_path}, language)
    if detail is None and cause is not None:
        detail = str(cause)
    if detail:
        message = f"{message} {detail}"
    return A2ATError(message, code=ErrorCatalog.INFRA_RESOURCE_READ_FAILED, cause=cause)


def scenario_catalog_key(language: str) -> PromptResourceKey:
    """Return the resource key of one language's scenario catalog.

    Args:
        language: locale identifier such as ``zh-CN`` or ``en-US``.

    Returns:
        the resource key addressing ``scenarios/<language>/scenarios.json``.
    """
    return PromptResourceKey.scenario(language, "scenarios.json")


def error_catalog_key(language: str) -> PromptResourceKey:
    """Return the resource key of one language's error message catalog.

    Args:
        language: locale identifier such as ``zh-CN`` or ``en-US``.

    Returns:
        the resource key addressing ``errors/<language>/errors.json``.
    """
    return PromptResourceKey("errors", (), language, "errors.json")


def load_scenarios(reader: ResourceReader, language: str) -> list[ScenarioDefinition]:
    """Load the scenario catalog of one language from the routed source.

    Args:
        reader: routed reader resolving the catalog.
        language: locale identifier such as ``zh-CN`` or ``en-US``.

    Returns:
        the scenario definitions of the language; unknown entry fields are ignored (Java
        ``@JsonIgnoreProperties`` parity).

    Raises:
        A2ATError: carrying ``infra.resource_read_failed`` when the catalog is missing, unreadable
            or malformed.
    """
    key = scenario_catalog_key(language)
    relative_path = key.relative_path()
    try:
        text = reader.read_text(key)
    except ResourceNotFoundError as error:
        raise read_failed(
            relative_path, language, detail="The scenario catalog does not exist for the configured language."
        ) from error
    except (OSError, UnicodeDecodeError) as error:
        raise read_failed(relative_path, language, cause=error) from error
    try:
        document = parse_json_object(text, origin=relative_path)
        raw_scenarios = document.get("scenarios")
        if not isinstance(raw_scenarios, list):
            raise ValueError(f"Prompt resource '{relative_path}' must carry a 'scenarios' array.")
        return [_scenario_definition(item, origin=relative_path) for item in raw_scenarios]
    except ValueError as error:
        raise read_failed(relative_path, language, cause=error) from error


def load_error_messages(reader: ResourceReader, language: str) -> dict[str, str]:
    """Load the error message catalog of one language from the packaged source.

    Args:
        reader: reader resolving the catalog (always the packaged reader — ``errors/**`` is
            package-fixed by the D31 routing table).
        language: locale identifier such as ``zh-CN`` or ``en-US``.

    Returns:
        the flat code-to-template mapping of the language.

    Raises:
        A2ATError: carrying ``infra.resource_read_failed`` when the catalog is missing, unreadable
            or not a flat code-to-template object.
    """
    key = error_catalog_key(language)
    relative_path = key.relative_path()
    try:
        text = reader.read_text(key)
    except ResourceNotFoundError as error:
        raise read_failed(
            relative_path, language, detail="The error message catalog does not exist for the configured language."
        ) from error
    except (OSError, UnicodeDecodeError) as error:
        raise read_failed(relative_path, language, cause=error) from error
    try:
        document = parse_json_object(text, origin=relative_path)
    except ValueError as error:
        raise read_failed(relative_path, language, cause=error) from error
    invalid = sorted(key_name for key_name, value in document.items() if not isinstance(value, str))
    if invalid:
        raise read_failed(
            relative_path,
            language,
            detail=f"The error message catalog must map codes to templates but has non-string values for {invalid}.",
        )
    return {key_name: value for key_name, value in document.items() if isinstance(value, str)}


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """Build one object while rejecting duplicated keys (the strict ``object_pairs_hook``)."""
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise DuplicateJsonKeyError(key)
        result[key] = value
    return result


def _scenario_definition(item: Any, *, origin: str) -> ScenarioDefinition:
    """Project one raw scenario entry onto the shared scenario model."""
    if not isinstance(item, dict):
        raise ValueError(f"Prompt resource '{origin}' must list scenario objects.")
    missing = [field for field in _SCENARIO_FIELDS if field not in item]
    if missing:
        raise ValueError(f"Prompt resource '{origin}' has a scenario entry missing the fields {missing}.")
    return ScenarioDefinition(
        scenario_code=str(item["scenario_code"]),
        scenario_name=str(item["scenario_name"]),
        description=str(item["description"]),
        example=str(item["example"]),
    )
