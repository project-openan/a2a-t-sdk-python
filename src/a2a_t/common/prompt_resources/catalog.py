"""Template catalog and query service (port of the Java ``prompt/resources/catalog`` package).

The Java package trio — ``TemplateDescriptions`` (the leading-HTML-comment description convention),
``PromptTemplateCatalog`` (directory-driven discovery over the ``templates/`` tree) and
``TemplateQueryService`` (the never-throwing query facade consumed by the client and the server
facade) — is ported here as one module. The catalog enumerates the routed ``templates/`` tree of
the configured source through the common resource access layer (D31): every extension directory
that appears under it — Task-T, Notification-T, Authorization-T, Negotiation-T and any extension
added later — is picked up without a hardcoded extension list, and a template file lives at
``templates/<extensionName>/<pathSegments>/<templateVersion>/<language>/template.md``, addressed by
the URI formed from the segments before the language.

One deliberate divergence from the Java catalog (D31, which overrides D13): Java keeps the
Negotiation-T tree classpath-fixed and unions it into the local business content in ``local_file``
mode, while this port routes the whole ``templates/`` tree — negotiation templates included —
through the single resource access layer, so both sources of the overlay come from one access
object. In ``local_file`` mode the effective set is the built-in templates union the locally
captured ones, with the local copy winning when the same path exists in both (the Java ADR 0005
overlay), and every template record carries its effective origin: ``local`` for locally captured
files, ``packaged`` for the built-in copies. The Negotiation-T closed-set filter of the Java
catalog is kept verbatim: a Negotiation-T template outside the closed set of seven shapes is
ignored with a warning.

The catalog captures its entries once, at construction (Java constructor snapshot): the packaged
tree is walked and the local root snapshotted by the access layer, and the frozen entry map is
never re-read afterwards (D9).
"""

from __future__ import annotations

import logging
from typing import Final

from a2a_t.config.models import PromptRuntimeConfig
from a2a_t.core.path_segments import is_simple_segment
from a2a_t.core.standard_templates import NEGOTIATION_EXTENSION_NAME
from a2a_t.core.template_uri import DEFAULT_TEMPLATE_VERSION, TemplateUri

from .models import SOURCE_LOCAL, SOURCE_PACKAGED, PromptTemplate
from .resource_access import create as create_resource_access

__all__ = [
    "PromptTemplateCatalog",
    "TemplateQueryService",
    "extract_description",
]

logger = logging.getLogger(__name__)

#: Hint logged when one template exists nowhere for the configured language.
_LANGUAGE_HINT: Final[str] = "set A2AT_LANGUAGE to a language with bundled templates (zh-CN or en-US)"

_COMMENT_OPEN: Final[str] = "<!--"

_COMMENT_CLOSE: Final[str] = "-->"

#: Minimum segment count of a catalogable template URI (extension + path + version).
_MINIMUM_URI_SEGMENTS: Final[int] = 3

#: Extension directory of the negotiation templates filtered to their closed set.
_NEGOTIATION_EXTENSION: Final[str] = NEGOTIATION_EXTENSION_NAME

#: Negotiation types of the closed set of catalogable negotiation templates.
_NEGOTIATION_TYPES: Final[frozenset[str]] = frozenset(
    {"information-negotiation", "target-negotiation", "feasibility-negotiation"}
)

#: Performative segments of the closed set of catalogable negotiation templates.
_NEGOTIATION_PERFORMATIVES: Final[frozenset[str]] = frozenset({"propose", "accept-reject"})

#: Type segment of the type-independent common negotiation templates.
_COMMON_TYPE_SEGMENT: Final[str] = "common"

#: Performative segment of the common abort negotiation template.
_ABORT_PERFORMATIVE_SEGMENT: Final[str] = "abort"

#: File name of one template payload inside the template tree.
_TEMPLATE_FILE_NAME: Final[str] = "template.md"


def extract_description(content: str) -> str:
    """Extract the template description from a leading HTML comment.

    Port of the Java ``TemplateDescriptions.extract`` convention shared by every prompt template of
    the resource tree: a template carries its description as a leading HTML comment on the first
    line; a template without such a comment reports an empty description.

    Args:
        content: full template text.

    Returns:
        the stripped comment text of the first line when it is an HTML comment, otherwise an empty
        string.
    """
    first_line_break = content.find("\n")
    first_line = content if first_line_break < 0 else content[:first_line_break]
    first_line = first_line.strip()
    if (
        first_line.startswith(_COMMENT_OPEN)
        and first_line.endswith(_COMMENT_CLOSE)
        and len(first_line) >= len(_COMMENT_OPEN) + len(_COMMENT_CLOSE)
    ):
        return first_line[len(_COMMENT_OPEN) : len(first_line) - len(_COMMENT_CLOSE)].strip()
    return ""


class PromptTemplateCatalog:
    """Directory-driven catalog over the prompt template tree of every A2A-T extension.

    The catalog captures the template entries of the configured source once, at construction, and
    never re-reads the resource tree afterwards, so a template added or removed after assembly is
    invisible to :meth:`load_all` and :meth:`load` until the SDK is restarted (D9). In
    ``local_file`` mode the captured set is the built-in overlay: the packaged templates union the
    locally captured ones with the local copy winning, and each entry keeps the origin it was
    served from. The shadowing is per path: a local override placed at a non-canonical layout (for
    example the plain ``Task-T/<code>/v1`` layout while the built-in lives under
    ``network-layer``) is listed as its own URI next to the packaged one, matching the Java
    catalog. Both query methods never throw: a template outside the catalogable URI shapes — or a
    Negotiation-T template outside the closed set of seven shapes — is skipped with a warning, and
    a template that exists nowhere for the language is answered with ``None``.
    """

    def __init__(self, language: str, source_type: str, local_root_dir: str | None = None) -> None:
        """Capture the template snapshot of one language and resource source.

        Args:
            language: locale identifier such as ``zh-CN`` or ``en-US``.
            source_type: resource source selector, ``packaged`` or ``local_file``.
            local_root_dir: local prompt resource root containing the ``templates/`` tree; required
                in ``local_file`` mode and ignored otherwise.

        Raises:
            ValueError: when the language is not a non-blank simple path segment (Java
                ``IllegalArgumentException`` parity).
            ConfigError: when the source type is unsupported, or ``local_file`` mode is selected
                without a local root that exists and is a directory (the access layer owns that
                validation in this port, D31).
        """
        if not is_simple_segment(language):
            raise ValueError(
                f"Prompt template catalog language must be a non-blank simple path segment but was {language}."
            )
        self._language = language
        access = create_resource_access(
            PromptRuntimeConfig(language=language, source_type=source_type, local_root_dir=local_root_dir)
        )
        self._source = SOURCE_PACKAGED if access.packaged() else SOURCE_LOCAL
        self._entries: dict[str, str] = {}
        self._origins: dict[str, str] = {}
        for path, (text, origin) in access.template_entries_with_origins().items():
            self._entries[path] = text
            self._origins[path] = origin

    @property
    def language(self) -> str:
        """The locale identifier the catalog was created for (used in log messages).

        Returns:
            locale identifier such as ``zh-CN`` or ``en-US``.
        """
        return self._language

    @property
    def source(self) -> str:
        """The dominant origin of the catalog's configured source (``packaged`` or ``local``).

        Returns:
            :data:`~a2a_t.common.prompt_resources.models.SOURCE_PACKAGED` in packaged mode;
            :data:`~a2a_t.common.prompt_resources.models.SOURCE_LOCAL` in ``local_file`` mode,
            where locally captured files win the built-in overlay. The per-template effective
            origin — including the packaged fallback copies a local root does not override — is
            carried by each :class:`PromptTemplate`'s ``source`` field.
        """
        return self._source

    def load_all(self) -> list[PromptTemplate]:
        """List every loadable template of the configured language across all extensions.

        This query never throws: templates that exist nowhere for the language are skipped and an
        empty list is returned when no template can be loaded at all. The result is sorted by
        template URI, which orders by extension first. Each record carries the effective origin of
        its path in the captured overlay.

        Returns:
            the loadable templates of the configured language, sorted by URI; empty when none can
            be loaded.
        """
        language_suffix = f"/{self._language}/{_TEMPLATE_FILE_NAME}"
        templates: list[PromptTemplate] = []
        for path, content in self._entries.items():
            if not path.endswith(language_suffix):
                continue
            uri = path[: len(path) - len(language_suffix)]
            if not _is_catalogable_uri(uri):
                logger.debug("prompt_template_skipped path=%s reason=not_a_template_uri", path)
                continue
            if _is_negotiation_uri(uri) and not _is_closed_set_negotiation_uri(uri):
                logger.warning("negotiation_template_outside_closed_set uri=%s", uri)
                continue
            template_uri = TemplateUri.parse(uri)
            if template_uri is None:
                # Unreachable given _is_catalogable_uri, kept as a defensive skip so the query
                # keeps its never-throw contract (Java raises IllegalStateException here).
                logger.warning("prompt_template_skipped uri=%s reason=unparseable", uri)
                continue
            templates.append(PromptTemplate(template_uri, extract_description(content), content, self._origin_of(path)))
        templates.sort(key=lambda template: template.template_uri.uri)
        logger.debug("prompt_templates_listed count=%d language=%s", len(templates), self._language)
        return templates

    def load(self, template_uri: TemplateUri) -> PromptTemplate | None:
        """Load one template of the configured language by its URI, regardless of the extension.

        In ``local_file`` mode the addressed template resolves against the captured overlay: the
        locally captured copy wins, and the packaged copy is the effective origin of a path the
        local root does not carry. A Negotiation-T template outside the closed set is answered with
        ``None``. This query never throws: a template that exists nowhere for the language yields
        ``None``.

        Args:
            template_uri: template URI such as ``Negotiation-T/information-negotiation/propose/v1``
                or ``Task-T/network-layer/ran-energy-saving/v1``.

        Returns:
            the addressed template, or ``None`` when no template exists for it in the configured
            language.

        Raises:
            TypeError: when the template URI is ``None``.
        """
        if template_uri is None:
            raise TypeError("templateUri")
        uri = template_uri.uri
        if template_uri.extension_name == _NEGOTIATION_EXTENSION and not _is_closed_set_negotiation_uri(uri):
            return None
        path = f"{uri}/{self._language}/{_TEMPLATE_FILE_NAME}"
        content = self._entries.get(path)
        if content is None:
            return None
        return PromptTemplate(template_uri, extract_description(content), content, self._origin_of(path))

    def _origin_of(self, path: str) -> str:
        """Return the captured origin of one template path, defaulting to the configured source."""
        return self._origins.get(path, self._source)


class TemplateQueryService:
    """Generic template query service consumed by the client and the server facade.

    Port of the Java ``TemplateQueryService``: the extension-agnostic template queries of the
    design document over one :class:`PromptTemplateCatalog`. Both queries never throw; a missing
    template is answered with ``None`` and an actionable warning log.
    """

    def __init__(self, template_catalog: PromptTemplateCatalog, language: str | None = None) -> None:
        """Create one service over a catalog.

        Args:
            template_catalog: directory-driven catalog over the template tree of every extension.
            language: locale identifier the catalog was created for; defaults to the catalog's own
                language and is only used in log messages.

        Raises:
            TypeError: when the catalog is ``None`` (Java ``NullPointerException`` parity).
        """
        if template_catalog is None:
            raise TypeError("Prompt template catalog must not be null.")
        self._template_catalog = template_catalog
        self._language = template_catalog.language if language is None else language

    @classmethod
    def from_config(cls, language: str, source_type: str, local_root_dir: str | None = None) -> TemplateQueryService:
        """Create one service for one language, one source type and an optional local root.

        The Python counterpart of the Java convenience constructor
        ``TemplateQueryService(language, sourceType, localRootDir)``.

        Args:
            language: locale identifier such as ``zh-CN`` or ``en-US``.
            source_type: resource source selector, ``packaged`` or ``local_file``.
            local_root_dir: local prompt resource root containing the ``templates/`` tree; required
                in ``local_file`` mode and ignored otherwise.

        Returns:
            the query service over the freshly captured catalog snapshot.
        """
        return cls(PromptTemplateCatalog(language, source_type, local_root_dir), language)

    def get_prompts(self) -> list[PromptTemplate]:
        """List every template available for the configured language across all A2A-T extensions.

        This query never throws: the extension directories are discovered from the configured
        resource tree itself, so templates of extensions added later are included automatically.
        Templates that exist nowhere for the language are skipped and an empty list is returned
        when no template can be loaded at all.

        Returns:
            the loadable templates of the configured language across all extensions, sorted by
            template URI; empty when none can be loaded.
        """
        return self._template_catalog.load_all()

    def get_prompt(self, template_uri: TemplateUri) -> PromptTemplate | None:
        """Load one template by its URI, regardless of the extension.

        This query never throws for a well-formed template URI: a missing template yields ``None``
        together with a warning log instead of a failure.

        Args:
            template_uri: typed template URI such as ``Negotiation-T/target-negotiation/propose/v1``
                or ``Task-T/network-layer/ran-energy-saving/v1``.

        Returns:
            the addressed template, or ``None`` when no template exists for it in the configured
            language.

        Raises:
            TypeError: when the template URI is ``None``.
        """
        if template_uri is None:
            raise TypeError("templateUri")
        template = self._template_catalog.load(template_uri)
        if template is None:
            logger.warning(
                "prompt_template_not_found uri=%s language=%s hint=%s",
                template_uri.uri,
                self._language,
                _LANGUAGE_HINT,
            )
        return template


def _is_negotiation_uri(uri: str) -> bool:
    """Return whether one template URI addresses the Negotiation-T extension."""
    slash = uri.find("/")
    return slash >= 0 and uri[:slash] == _NEGOTIATION_EXTENSION


def _is_closed_set_negotiation_uri(uri: str) -> bool:
    """Return whether one template URI is inside the closed set of seven negotiation shapes.

    The closed set is the three negotiation types' ``propose`` and ``accept-reject`` templates plus
    the common ``abort`` template, all at the default template version — the shapes the negotiation
    content pipeline addresses; any other Negotiation-T directory layout is not a usable template.
    """
    segments = uri.split("/")
    if len(segments) != 4:
        return False
    if segments[0] != _NEGOTIATION_EXTENSION or segments[3] != DEFAULT_TEMPLATE_VERSION:
        return False
    type_segment, performative_segment = segments[1], segments[2]
    if type_segment == _COMMON_TYPE_SEGMENT:
        return performative_segment == _ABORT_PERFORMATIVE_SEGMENT
    return type_segment in _NEGOTIATION_TYPES and performative_segment in _NEGOTIATION_PERFORMATIVES


def _is_catalogable_uri(uri: str) -> bool:
    """Return whether one template path segment sequence forms a catalogable template URI."""
    if not uri or not uri.strip():
        return False
    segments = uri.split("/")
    if len(segments) < _MINIMUM_URI_SEGMENTS:
        return False
    return all(is_simple_segment(segment) for segment in segments)
