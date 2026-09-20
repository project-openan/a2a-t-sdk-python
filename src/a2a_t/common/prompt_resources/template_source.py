"""Template-addressed resource loading by template URI and language (D31).

Both the prompt pipeline and the negotiation pipeline load template text through this module:
negotiation templates live in the same ``templates/Negotiation-T/<type>/<phase>/v1/<language>/``
tree, so they need no loader of their own. The Java ``DefaultNegotiationTemplateLoader`` — a second,
parallel resource loader with its own cache, path assembly and language validation — is deliberately
not ported; see port decision D31.

Template resources mirror the
:class:`~a2a_t.core.template_uri.TemplateUri` layout one-to-one: a full template URI resolves
directly to ``templates/<templateUri>/<language>/template.md``, and ``slots/`` mirrors the same
layout with ``slot.json``. A bare scenario code (no slash) is probed per template type — the known
types first, then the extension directories discovered under the category — trying the
``network-layer`` domain layout before the plain layout, so extensions bundled later, such as
``Authorization-T``, are loadable without extending a hardcoded list (Java
``ClasspathPromptTemplateLoader`` semantics).
"""

from __future__ import annotations

from typing import Any, Final, Iterator

from a2a_t.core.path_segments import require_simple_relative_path, require_simple_segment
from a2a_t.core.prompt_resource_key import PromptResourceKey
from a2a_t.core.standard_templates import (
    NEGOTIATION_EXTENSION_NAME,
    NETWORK_LAYER_SEGMENT,
    NOTIFICATION_EXTENSION_NAME,
    TASK_EXTENSION_NAME,
)
from a2a_t.core.template_uri import DEFAULT_TEMPLATE_VERSION, TemplateUri

from . import json_source
from .builtin_fallback import resolve_bare_code
from .json_source import ResourceReader

__all__ = [
    "KNOWN_SLOT_TYPES",
    "KNOWN_TEMPLATE_TYPES",
    "SLOT_SCHEMA_FILE_NAME",
    "TEMPLATE_FILE_NAME",
    "identifier_str",
    "load_slot_schema",
    "load_template_text",
]

#: File name of a template markdown payload.
TEMPLATE_FILE_NAME: Final[str] = "template.md"

#: File name of a slot schema payload.
SLOT_SCHEMA_FILE_NAME: Final[str] = "slot.json"

#: Template types probed first for bare scenario codes (Java ``KNOWN_TEMPLATE_TYPES``).
KNOWN_TEMPLATE_TYPES: Final[tuple[str, ...]] = (
    TASK_EXTENSION_NAME,
    NOTIFICATION_EXTENSION_NAME,
    NEGOTIATION_EXTENSION_NAME,
)

#: Slot types probed first for bare scenario codes (Java ``KNOWN_SLOT_TYPES``).
KNOWN_SLOT_TYPES: Final[tuple[str, ...]] = (TASK_EXTENSION_NAME, NOTIFICATION_EXTENSION_NAME)

_TEMPLATES_CATEGORY: Final[str] = "templates"
_SLOTS_CATEGORY: Final[str] = "slots"


def identifier_str(template_uri: str | TemplateUri) -> str:
    """Return the raw template URI string of one template identifier.

    Args:
        template_uri: raw template URI or typed :class:`TemplateUri`.

    Returns:
        the URI string, used verbatim as the ``template_uri`` fact of catalog errors.
    """
    return template_uri.uri if isinstance(template_uri, TemplateUri) else template_uri


def load_template_text(reader: ResourceReader, template_uri: str | TemplateUri, language: str) -> str:
    """Load one template's markdown text from the routed source.

    Args:
        reader: routed reader resolving the template tree.
        template_uri: template URI (raw string or typed) such as
            ``Task-T/network-layer/ran-energy-saving/v1``, or a bare scenario code such as
            ``ran-energy-saving``.
        language: locale identifier such as ``zh-CN`` or ``en-US``.

    Returns:
        the template markdown text.

    Raises:
        ValueError: when the identifier or the language is not a simple relative path / segment.
        ResourceNotFoundError: when no template matches the identifier for the language.
    """
    identifier = _require_identifier(template_uri, language)
    _key, text = _load_routed_text(
        reader, _TEMPLATES_CATEGORY, identifier, language, TEMPLATE_FILE_NAME, KNOWN_TEMPLATE_TYPES
    )
    return text


def load_slot_schema(reader: ResourceReader, template_uri: str | TemplateUri, language: str) -> dict[str, Any]:
    """Load one template's slot schema document from the routed source.

    Args:
        reader: routed reader resolving the slot tree.
        template_uri: template URI (raw string or typed), or a bare scenario code.
        language: locale identifier such as ``zh-CN`` or ``en-US``.

    Returns:
        the parsed slot schema document (a JSON object).

    Raises:
        ValueError: when the identifier or the language is not a simple relative path / segment.
        ResourceNotFoundError: when no slot schema matches the identifier for the language.
        A2ATError: carrying ``infra.resource_read_failed`` when the schema payload is malformed.
    """
    identifier = _require_identifier(template_uri, language)
    key, text = _load_routed_text(
        reader, _SLOTS_CATEGORY, identifier, language, SLOT_SCHEMA_FILE_NAME, KNOWN_SLOT_TYPES
    )
    try:
        return json_source.parse_json_object(text, origin=key.relative_path())
    except ValueError as error:
        raise json_source.read_failed(key.relative_path(), language, cause=error) from error


def _require_identifier(template_uri: str | TemplateUri, language: str) -> str:
    """Validate one template identifier and language, returning the identifier string."""
    identifier = identifier_str(template_uri)
    require_simple_relative_path(identifier, "Prompt template scenario code")
    require_simple_segment(language, "Prompt template language")
    return identifier


def _load_routed_text(
    reader: ResourceReader,
    category: str,
    identifier: str,
    language: str,
    file_name: str,
    known_types: tuple[str, ...],
) -> tuple[PromptResourceKey, str]:
    """Resolve and read one template-addressed resource, probing bare codes across types.

    A slash-carrying identifier addresses the resource directly; a bare scenario code resolves in
    the Java two-phase order (:func:`~a2a_t.common.prompt_resources.builtin_fallback.resolve_bare_code`):
    the whole local snapshot is probed first — a local file wins whatever its type directory or
    layout — and only then the packaged candidates.
    """
    if "/" in identifier:
        key = _direct_key(category, identifier, language, file_name)
        return key, reader.read_text(key)
    keys = list(_bare_code_keys(reader, category, identifier, language, file_name, known_types))
    return resolve_bare_code(reader, keys, _probe_hint(category, identifier, language, file_name))


def _direct_key(category: str, identifier: str, language: str, file_name: str) -> PromptResourceKey:
    """Return the resource key of one slash-carrying identifier, parsed via the core URI type."""
    return PromptResourceKey(category, _uri_segments(identifier), language, file_name)


def _uri_segments(identifier: str) -> tuple[str, ...]:
    """Return the URI segments of one identifier, parsing full URIs via :class:`TemplateUri`."""
    parsed = TemplateUri.parse(identifier)
    if parsed is not None:
        return parsed.segments
    return tuple(identifier.strip().split("/"))


def _bare_code_keys(
    reader: ResourceReader,
    category: str,
    scenario_code: str,
    language: str,
    file_name: str,
    known_types: tuple[str, ...],
) -> Iterator[PromptResourceKey]:
    """Yield the candidate keys of one bare scenario code across the category's types."""
    for template_type in _probe_types(reader, category, known_types):
        yield PromptResourceKey(
            category,
            (template_type, NETWORK_LAYER_SEGMENT, scenario_code, DEFAULT_TEMPLATE_VERSION),
            language,
            file_name,
        )
        yield PromptResourceKey(category, (template_type, scenario_code, DEFAULT_TEMPLATE_VERSION), language, file_name)


def _probe_types(reader: ResourceReader, category: str, known_types: tuple[str, ...]) -> tuple[str, ...]:
    """Return the probe order for one category: known types first, then discovered extensions."""
    return tuple(dict.fromkeys((*known_types, *reader.category_types(category))))


def _probe_hint(category: str, identifier: str, language: str, file_name: str) -> str:
    """Return the Java-parity path hint used when no candidate matched a bare scenario code."""
    return (
        f"prompt_resources/{category}/*/{NETWORK_LAYER_SEGMENT}/{identifier}/{DEFAULT_TEMPLATE_VERSION}/"
        f"{language}/{file_name} (or the layout without the network-layer segment)"
    )
