"""The single resource access layer for the A2A-T prompt resource tree (D31).

Port of the Java ``PromptResourceAccess`` factory and its two access implementations, with one
deliberate divergence required by port decision D31 (which overrides D13): negotiation resources
are routed like every other business resource instead of being fixed to the package. Both the
prompt pipeline and the negotiation pipeline consume this layer, and no other module may assemble
resource paths or own resource caches — the Java ``DefaultNegotiationTemplateLoader`` built a
second, parallel loader next to this one, and that mistake is not ported.

Routing table (selected by ``A2AT_PROMPT_SOURCE_TYPE``, values ``packaged`` | ``local_file``; the
default is ``packaged`` since the 1.1.0 release flip — D10 step 2, mirroring the Java
``classpath`` default; the pre-1.1.0 ``local_file`` default is restored by setting the variable):

- ``templates/**`` — routed (including ``templates/Negotiation-T/**``, locally overridable).
- ``slots/**`` — routed.
- ``scenarios/**`` — routed.
- ``negotiation-vocabulary/**`` — routed (Java 1.1.0 keeps it classpath-fixed; D31 routes it).
- ``prompts/**`` — always packaged: the LLM instruction prompts are an SDK contract, and a local
  copy under the root is ignored with a WARN listing the ignored directories (the Java
  ``CustomRootPromptsIgnored`` semantics).
- ``errors/**`` — always packaged: the SDK error-message contract (same ignore semantics).

``local_file`` mode serves the routed categories local-first with a built-in fallback (the port of
the Java ADR 0005 overlay): a resource missing from the local snapshot falls back to the packaged
copy, so a custom root only needs to carry the files it actually overrides. Each resource path that
is served from the package this way warns once
(``prompt_resource_builtin_fallback path=... source=packaged``); a resource missing both locally
and in the package fails with the plain not-found error and never warns.

``local_file`` mode requires the local root to exist and be a directory (a clear config error
otherwise), and the whole root is captured ONCE as a frozen snapshot when the access object is
created, so later file edits are invisible until the SDK is restarted (D9). ``packaged`` reads are
cached at module level and a missing resource is never cached. Configuring a local root while
running ``packaged`` mode warns that the root is ignored — except when it points at the packaged
tree itself, which the Python config resolver fills in as the default and which therefore carries
no user intent.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Final

from a2a_t.config.errors import ConfigError
from a2a_t.config.models import PromptRuntimeConfig
from a2a_t.core.errors.catalog import ErrorCatalog
from a2a_t.core.errors.exceptions import A2ATBusinessError, A2ATError, ResourceNotFoundError
from a2a_t.core.errors.messages import render
from a2a_t.core.prompt_resource_key import PromptResourceKey
from a2a_t.core.template_uri import TemplateUri

from . import json_source, packaged_access, template_source, vocabulary
from .builtin_fallback import BuiltinFallbackReader
from .json_source import ResourceReader
from .local_file_access import LocalResourceSnapshot
from .models import SOURCE_LOCAL, SOURCE_PACKAGED, ScenarioDefinition
from .packaged_access import PackagedResourceReader
from .vocabulary import Vocabulary

__all__ = [
    "LOCAL_FILE_SOURCE_TYPE",
    "PACKAGED_SOURCE_TYPE",
    "BuiltinFallbackReader",
    "LocalFilePromptResourceAccess",
    "PackagedPromptResourceAccess",
    "PromptResourceAccess",
    "create",
]

logger = logging.getLogger(__name__)

#: Source-type selector value loading routed resources from the packaged prompt resource tree.
PACKAGED_SOURCE_TYPE: Final[str] = "packaged"

#: Source-type selector value loading routed resources from the configured local root.
LOCAL_FILE_SOURCE_TYPE: Final[str] = "local_file"

#: Categories that are always packaged regardless of the source type (SDK contracts).
_PACKAGED_FIXED_CATEGORIES: Final[tuple[str, ...]] = ("prompts", "errors")

_LOCAL_ROOT_DIR_KEY: Final[str] = "A2AT_PROMPT_RESOURCE_LOCAL_ROOT_DIR"

#: Category of the template tree every extension publishes its templates under.
_TEMPLATE_CATEGORY: Final[str] = "templates"

#: File name of one template payload inside the template tree.
_TEMPLATE_FILE_NAME: Final[str] = "template.md"


def create(config: PromptRuntimeConfig) -> PromptResourceAccess:
    """Create the resource access object for one prompt runtime configuration.

    Args:
        config: prompt runtime configuration carrying ``source_type`` and ``local_root_dir``.

    Returns:
        the access object routing every resource category per the D31 routing table.

    Raises:
        ConfigError: when the source type is unsupported, or ``local_file`` mode is selected
            without a local root that exists and is a directory.
    """
    source_type = config.source_type
    if source_type == PACKAGED_SOURCE_TYPE:
        _warn_ignored_local_root(config.local_root_dir)
        return PackagedPromptResourceAccess()
    if source_type == LOCAL_FILE_SOURCE_TYPE:
        root_dir = _require_local_root(config.local_root_dir)
        if not _points_at_packaged_root(str(root_dir)):
            # The Python config resolver fills the packaged root in as the default local root,
            # which carries no user intent — only a genuinely configured root is worth a warning.
            _warn_ignored_local_directories(root_dir)
        return LocalFilePromptResourceAccess(root_dir)
    raise ConfigError(
        f"Unsupported prompt source type: {source_type!r}; "
        f"expected '{PACKAGED_SOURCE_TYPE}' or '{LOCAL_FILE_SOURCE_TYPE}'."
    )


class PromptResourceAccess(ABC):
    """Routing facade over the prompt resource tree; see the module docstring for the table.

    The class is the port's counterpart of the Java ``PromptResourceAccess`` interface: it exposes
    one method per resource family, hides which source serves each family, and is the only seam the
    prompt and negotiation pipelines are allowed to read resources through.
    """

    _routed_reader: ResourceReader
    _packaged_reader: PackagedResourceReader

    def __init__(self) -> None:
        """Wire the packaged reader every mode needs for the package-fixed categories."""
        self._packaged_reader = PackagedResourceReader()

    @abstractmethod
    def packaged(self) -> bool:
        """Return whether routed resources are served from the packaged tree.

        Returns:
            ``True`` in packaged mode, ``False`` when a local root serves the routed categories.
        """

    @abstractmethod
    def local_root_dir(self) -> Path | None:
        """Return the local root serving routed resources, or ``None`` in packaged mode.

        Returns:
            the configured local root directory, or ``None`` in packaged mode.
        """

    def load_scenarios(self, language: str) -> list[ScenarioDefinition]:
        """Load the scenario catalog of one language (routed).

        Args:
            language: locale identifier such as ``zh-CN`` or ``en-US``.

        Returns:
            the scenario definitions of the language.

        Raises:
            A2ATError: carrying ``infra.resource_read_failed`` when the catalog is missing,
                unreadable or malformed.
        """
        return json_source.load_scenarios(self._routed_reader, language)

    def template_text(self, template_uri: str | TemplateUri, language: str) -> str:
        """Load one template's markdown text (routed; negotiation templates included).

        Args:
            template_uri: template URI (raw string or typed) such as
                ``Task-T/network-layer/ran-energy-saving/v1`` or
                ``Negotiation-T/common/abort/v1``, or a bare scenario code such as
                ``ran-energy-saving``.
            language: locale identifier such as ``zh-CN`` or ``en-US``.

        Returns:
            the template markdown text.

        Raises:
            ValueError: when the identifier or the language is malformed (including path
                traversal attempts).
            A2ATBusinessError: carrying ``template.not_found`` with the ``template_uri`` and
                ``language`` facts when no template matches.
            A2ATError: carrying ``template.load_failed`` with the ``resource_path`` fact when the
                template resource cannot be read from its configured source.
        """
        identifier = template_source.identifier_str(template_uri)
        try:
            return template_source.load_template_text(self._routed_reader, template_uri, language)
        except ResourceNotFoundError as error:
            raise A2ATBusinessError(
                ErrorCatalog.TEMPLATE_NOT_FOUND,
                {"template_uri": identifier, "language": language},
            ) from error
        except (OSError, UnicodeDecodeError) as error:
            raise _load_failed(identifier) from error

    def template_entries(self) -> dict[str, str]:
        """Enumerate every template file of the routed ``templates/`` tree (D31).

        The directory-driven enumeration consumed by the template catalog: the whole routed
        templates tree — every extension directory it contains, negotiation templates included —
        is walked so extensions added later are discovered instead of being listed (Java
        ``PromptTemplateCatalog`` directory walking). In ``local_file`` mode the enumeration is the
        built-in overlay: the packaged templates union the locally captured ones, a local file
        winning over its packaged counterpart (Java ADR 0005 overlay semantics). The caller
        captures the returned mapping once into its own frozen snapshot; this read family has no
        module-level cache of its own.

        Returns:
            a mapping of templates-category-relative path (forward slashes, such as
            ``Negotiation-T/common/abort/v1/zh-CN/template.md``) to the template text.
        """
        return {path: text for path, (text, _origin) in self.template_entries_with_origins().items()}

    def template_entries_with_origins(self) -> dict[str, tuple[str, str]]:
        """Return the overlay template enumeration with the effective origin per path.

        One single walk of the routed tree serves both :meth:`template_entries` and the origin
        view. In packaged mode every entry originates from the package. In ``local_file`` mode the
        overlay gives locally captured files the :data:`~a2a_t.common.prompt_resources.models.
        SOURCE_LOCAL` origin and the packaged copies the
        :data:`~a2a_t.common.prompt_resources.models.SOURCE_PACKAGED` origin (the per-template
        origin the Java ``PromptTemplateCatalog`` carries in every ``PromptTemplate`` record).

        Returns:
            a mapping of templates-category-relative paths (the same keys as
            :meth:`template_entries`) to ``(text, origin)`` pairs.

        Note:
            the default derives one uniform origin from :meth:`packaged`, which is only correct
            for single-source access objects; an access that mixes local and packaged entries must
            override this method to keep the origins truthful.
        """
        uniform = SOURCE_PACKAGED if self.packaged() else SOURCE_LOCAL
        return {
            path: (text, uniform)
            for path, text in self._routed_reader.category_files(_TEMPLATE_CATEGORY, _TEMPLATE_FILE_NAME).items()
        }

    def slot_schema(self, template_uri: str | TemplateUri, language: str) -> dict[str, Any]:
        """Load one template's slot schema document (routed).

        Args:
            template_uri: template URI (raw string or typed), or a bare scenario code.
            language: locale identifier such as ``zh-CN`` or ``en-US``.

        Returns:
            the parsed slot schema document (a JSON object).

        Raises:
            ValueError: when the identifier or the language is malformed (including path
                traversal attempts).
            A2ATBusinessError: carrying ``slot.schema_not_found`` with the ``template_uri`` and
                ``language`` facts when no slot schema matches.
            A2ATError: carrying ``infra.resource_read_failed`` when the schema payload cannot be
                read or parsed.
        """
        identifier = template_source.identifier_str(template_uri)
        try:
            return template_source.load_slot_schema(self._routed_reader, template_uri, language)
        except ResourceNotFoundError as error:
            raise A2ATBusinessError(
                ErrorCatalog.SLOT_SCHEMA_NOT_FOUND,
                {"template_uri": identifier, "language": language},
            ) from error
        except (OSError, UnicodeDecodeError) as error:
            raise json_source.read_failed(identifier, language, cause=error) from error

    def load_prompt(self, category: str, language: str, file_name: str) -> str:
        """Load one LLM instruction prompt (package-fixed: a local copy is ignored with a WARN).

        Args:
            category: prompt action name, such as ``slot_extraction``.
            language: locale identifier such as ``zh-CN`` or ``en-US``.
            file_name: prompt file name, such as ``system.md`` or ``user.md``.

        Returns:
            the prompt text.

        Raises:
            ValueError: when any component is blank or not a simple path segment.
            A2ATError: carrying ``infra.resource_read_failed`` when the prompt resource is missing
                from the package or cannot be read.
        """
        key = PromptResourceKey.prompt(category, language, file_name)
        relative_path = key.relative_path()
        try:
            return self._packaged_reader.read_text(key)
        except ResourceNotFoundError as error:
            raise json_source.read_failed(
                relative_path,
                language,
                detail="The prompt resource does not exist in the packaged prompt resources.",
            ) from error
        except (OSError, UnicodeDecodeError) as error:
            raise json_source.read_failed(relative_path, language, cause=error) from error

    def load_vocabulary(self, language: str) -> Vocabulary:
        """Load the negotiation vocabulary of one language (routed; D31).

        Args:
            language: locale identifier such as ``zh-CN`` or ``en-US``.

        Returns:
            the vocabulary holding the validated text constants of the language.

        Raises:
            ValueError: when the language is not a non-blank simple path segment.
            A2ATError: carrying ``infra.resource_read_failed`` when the vocabulary is missing,
                unreadable, malformed or drifts from the canonical key set.
        """
        return vocabulary.load_vocabulary(self._routed_reader, language)

    def load_errors(self, language: str) -> dict[str, str]:
        """Load the error message catalog of one language (package-fixed: SDK contract).

        Args:
            language: locale identifier such as ``zh-CN`` or ``en-US``.

        Returns:
            the flat code-to-template mapping of the language.

        Raises:
            A2ATError: carrying ``infra.resource_read_failed`` when the catalog is missing,
                unreadable or not a flat code-to-template object.
        """
        return json_source.load_error_messages(self._packaged_reader, language)


class PackagedPromptResourceAccess(PromptResourceAccess):
    """Access serving every routed category from the packaged prompt resource tree."""

    def __init__(self) -> None:
        """Route every category to the module-level cached packaged reads."""
        super().__init__()
        self._routed_reader = PackagedResourceReader()

    def packaged(self) -> bool:
        """Return whether routed resources are served from the packaged tree.

        Returns:
            ``True``: routed resources come from the package.
        """
        return True

    def local_root_dir(self) -> Path | None:
        """Return the local root serving routed resources, or ``None`` in packaged mode.

        Returns:
            ``None``: packaged resources have no local root directory.
        """
        return None


class LocalFilePromptResourceAccess(PromptResourceAccess):
    """Access serving routed categories from one local root, frozen at construction (D9).

    The whole local root is captured once as a :class:`LocalResourceSnapshot` when this object is
    created; runtime reads never touch the filesystem, so local file changes only take effect after
    the SDK is restarted. The routed reads run through a
    :class:`~a2a_t.common.prompt_resources.builtin_fallback.BuiltinFallbackReader`: a resource
    missing from the snapshot falls back to the packaged copy with a one-time warning per resource
    path (Java ADR 0005 overlay), so a custom root only needs the files it overrides. The
    package-fixed categories (``prompts/``, ``errors/``) are still served from the package
    directly.
    """

    def __init__(self, root_dir: Path) -> None:
        """Capture the frozen snapshot of one local root and wire the built-in fallback.

        Args:
            root_dir: local prompt resource root; must exist and be a directory.
        """
        super().__init__()
        self._root_dir = root_dir
        self._snapshot = LocalResourceSnapshot.capture(root_dir)
        self._fallback_reader = BuiltinFallbackReader(self._snapshot, self._packaged_reader)
        self._routed_reader = self._fallback_reader

    def packaged(self) -> bool:
        """Return whether routed resources are served from the packaged tree.

        Returns:
            ``False``: routed resources come from the local root.
        """
        return False

    def local_root_dir(self) -> Path | None:
        """Return the local root serving routed resources, or ``None`` in packaged mode.

        Returns:
            the local root captured as the frozen snapshot at construction.
        """
        return self._root_dir

    def template_entries_with_origins(self) -> dict[str, tuple[str, str]]:
        """Return the overlay template enumeration with the per-path origin (single walk).

        Returns:
            a mapping of templates-category-relative paths to ``(text, origin)`` pairs, the origin
            being :data:`~a2a_t.common.prompt_resources.models.SOURCE_LOCAL` for the locally
            captured files and :data:`~a2a_t.common.prompt_resources.models.SOURCE_PACKAGED` for
            the packaged fallback copies.
        """
        return self._fallback_reader.category_files_with_origin(_TEMPLATE_CATEGORY, _TEMPLATE_FILE_NAME)

    @property
    def snapshot(self) -> LocalResourceSnapshot:
        """The frozen snapshot captured at construction (exposed for diagnostics and tests).

        Returns:
            the immutable snapshot of the routed categories of the local root.
        """
        return self._snapshot


def _require_local_root(local_root_dir: str | None) -> Path:
    """Require one configured local root to exist and be a directory."""
    if local_root_dir is None or not local_root_dir.strip():
        raise ConfigError(
            f"Prompt resource local root directory is required for sourceType "
            f"'{LOCAL_FILE_SOURCE_TYPE}' but is not set; configure {_LOCAL_ROOT_DIR_KEY} to the "
            "local prompt resource root."
        )
    root_dir = Path(local_root_dir)
    if not root_dir.is_dir():
        raise ConfigError(
            f"Prompt resource local root directory configured via {_LOCAL_ROOT_DIR_KEY} does not "
            f"exist or is not a directory: {root_dir}"
        )
    return root_dir


def _warn_ignored_local_root(local_root_dir: str | None) -> None:
    """Warn that one configured local root is ignored in packaged mode."""
    if not local_root_dir or not local_root_dir.strip():
        return
    if _points_at_packaged_root(local_root_dir):
        # The Python config resolver fills the packaged root in as the default value, which carries
        # no user intent — only a genuinely configured root is worth a warning.
        return
    logger.warning(
        "prompt_resource_local_root_ignored root=%s source=%s reason=source_type_is_packaged",
        local_root_dir,
        PACKAGED_SOURCE_TYPE,
    )


def _warn_ignored_local_directories(root_dir: Path) -> None:
    """Warn about package-fixed categories shadowed by local copies (CustomRootPromptsIgnored)."""
    ignored = [category for category in _PACKAGED_FIXED_CATEGORIES if (root_dir / category).is_dir()]
    if ignored:
        logger.warning(
            "prompt_resource_local_directories_ignored root=%s directories=[%s] reason=packaged_fixed",
            root_dir,
            ", ".join(ignored),
        )


def _points_at_packaged_root(local_root_dir: str) -> bool:
    """Return whether one local root path points at the packaged prompt resource tree itself."""
    packaged_root = packaged_access.prompt_resources_root()
    if packaged_root is None:
        return False
    try:
        return Path(local_root_dir).resolve() == packaged_root.resolve()
    except OSError:
        return False


def _load_failed(resource_path: str) -> A2ATError:
    """Create a ``template.load_failed`` failure for one template resource path."""
    return A2ATError(
        render(ErrorCatalog.TEMPLATE_LOAD_FAILED, {"resource_path": resource_path}),
        code=ErrorCatalog.TEMPLATE_LOAD_FAILED,
    )
