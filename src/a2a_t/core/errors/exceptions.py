"""Exception tree of the A2A-T SDK error model.

Port of the Java 1.1.0 error classes: ``A2ATError`` is the single root for every SDK processing
failure; :class:`A2ATBusinessError` adds the catalog code and the fact values that produced the
rendered message. Programming errors (``None`` or malformed arguments) deliberately stay outside the
tree as :class:`TypeError` / :class:`ValueError` (Java ``NullPointerException`` /
``IllegalArgumentException``), and infrastructure failures stay plain :class:`A2ATError` (D3).

Codes are carried internally as :class:`~a2a_t.core.errors.catalog.ErrorCatalog` members and exposed
externally as plain strings through ``code_str`` (D4).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from a2a_t.core.errors.catalog import ErrorCatalog
from a2a_t.core.errors.messages import render

__all__ = [
    "A2ATBusinessError",
    "A2ATError",
    "A2ATParamExtractionError",
    "ConfigFileNotFoundError",
    "ContentValidationError",
    "NegotiationGenerationError",
    "NegotiationParamExtractionError",
    "PromptGenerationError",
    "ResourceNotFoundError",
    "SlotValidationError",
]


def _normalize_facts(facts: Mapping[str, object] | None) -> dict[str, str]:
    """Normalize fact values to plain strings (the facts entry contract is ``dict[str, str]``)."""
    if not facts:
        return {}
    return {key: value if isinstance(value, str) else str(value) for key, value in facts.items()}


class A2ATError(Exception):
    """Single root exception type for all A2A-T SDK processing failures.

    Every runtime, environment or data-processing failure raised by the SDK is part of this tree, so
    callers can catch this one root type and branch on the machine-readable error code returned by
    :attr:`code` / :attr:`code_str`, which is never ``None``. Caller contract violations (``None``,
    blank or otherwise malformed arguments) are programming errors raised as ``TypeError`` or
    ``ValueError`` and intentionally stay outside this tree.
    """

    def __init__(
        self,
        message: str,
        *,
        code: ErrorCatalog = ErrorCatalog.INFRA_INTERNAL_ERROR,
        cause: BaseException | None = None,
    ) -> None:
        """Create one root failure with its message, code and optional cause.

        Args:
            message: human-readable failure message; infrastructure failures pass a plain message,
                business failures pass one rendered from the code's template.
            code: catalog code of the failure; defaults to ``infra.internal_error``.
            cause: underlying exception to chain as ``__cause__``, when any.
        """
        super().__init__(message)
        self.code = code
        if cause is not None:
            self.__cause__ = cause
            self.__suppress_context__ = True

    @property
    def code_str(self) -> str:
        """The layered error code of this failure as a plain string (D4: the external carrier).

        Returns:
            the code string, for example ``negotiation.invalid_input``.
        """
        return str(self.code.value)


class A2ATBusinessError(A2ATError):
    """Base type for expected business failures raised by the A2A-T SDK.

    Business failures carry a machine-readable code from :class:`ErrorCatalog` and a message rendered
    from the code's message template; the structured fact values that produced the message travel in
    :attr:`facts`. Programming errors stay ``TypeError``/``ValueError``; infrastructure failures stay
    plain :class:`A2ATError`.

    The message is rendered at construction unless one is passed explicitly::

        raise A2ATBusinessError(ErrorCatalog.INPUT_TEXT_TOO_LONG, {"actual_length": 42, "max_chars": 16})
    """

    def __init__(
        self,
        code: ErrorCatalog,
        facts: Mapping[str, object] | None = None,
        *,
        language: str | None = None,
        message: str | None = None,
        cause: BaseException | None = None,
    ) -> None:
        """Create one business failure, rendering its message from the code's template.

        Args:
            code: catalog code of the failure.
            facts: fact values keyed by fact parameter name; non-string values are ``str()``-ized.
            language: message language for the rendered message; ``None`` uses the default
                language.
            message: pre-rendered message; when ``None``, the message is rendered from the code's
                template and the given facts.
            cause: underlying exception to chain as ``__cause__``, when any.
        """
        self.facts: dict[str, str] = _normalize_facts(facts)
        if message is None:
            message = render(code, self.facts, language)
        super().__init__(message, code=code, cause=cause)


@dataclass(frozen=True)
class SlotValidationError:
    """Structured validation error details for one named slot.

    Port of the Java ``core/model/SlotValidationError`` record. Instances travel inside
    parameter-extraction and content-validation failures so callers can inspect which slot failed,
    under which error code, and why, without parsing exception messages. The ``message`` is rendered
    by the SDK from the code's template; ``facts`` carries the structured values that produced it.

    Attributes:
        slot_name: name of the slot the error belongs to.
        code: layered error code of the error; a plain string ready for JSON wire and logs.
        message: human-readable message rendered from the code's bilingual template.
        facts: structured fact values that produced the message, keyed by fact parameter name;
            ``None`` when the failure carries no facts.
    """

    slot_name: str
    code: str
    message: str
    facts: dict[str, str] | None = None

    def __post_init__(self) -> None:
        """Normalize a catalog member passed as the code to its plain string form (D4)."""
        # D4: the code is the external string carrier, so a catalog member passed in is normalized
        # to its plain string form instead of serializing as "ErrorCatalog.X".
        if isinstance(self.code, ErrorCatalog):
            object.__setattr__(self, "code", self.code.value)


class PromptGenerationError(A2ATBusinessError):
    """Unified failure type for MetadataContent pipeline failures.

    Carries the per-slot validation errors of the failed parameters in :attr:`failed_parameters`
    (Java ``PromptGenerationException.failedParameters``).
    """

    def __init__(
        self,
        code: ErrorCatalog,
        facts: Mapping[str, object] | None = None,
        *,
        language: str | None = None,
        message: str | None = None,
        cause: BaseException | None = None,
        failed_parameters: Iterable[SlotValidationError] | None = None,
    ) -> None:
        """Create one prompt generation failure.

        Args:
            code: catalog code of the failure.
            facts: fact values keyed by fact parameter name; non-string values are ``str()``-ized.
            language: message language for the rendered message; ``None`` uses the default
                language.
            message: pre-rendered message; when ``None``, the message is rendered from the code's
                template and the given facts.
            cause: underlying exception to chain as ``__cause__``, when any.
            failed_parameters: per-slot validation errors of the failed parameters.
        """
        super().__init__(code, facts, language=language, message=message, cause=cause)
        self.failed_parameters: list[SlotValidationError] = list(failed_parameters or ())


class ContentValidationError(A2ATBusinessError):
    """Failure raised by the content validation pipeline.

    Carries the structured per-slot validation errors in :attr:`errors` and, when validation ran on
    an extraction, the partial extraction result in :attr:`params`. The semantic validator
    deliberately emits ``None`` values for slots it could not extract, so ``params`` and its values
    may be ``None``; insertion order is preserved (Java ``ContentValidationException.params``).
    """

    def __init__(
        self,
        code: ErrorCatalog,
        facts: Mapping[str, object] | None = None,
        *,
        language: str | None = None,
        message: str | None = None,
        cause: BaseException | None = None,
        errors: Iterable[SlotValidationError] | None = None,
        params: Mapping[str, object] | Sequence[object] | None = None,
    ) -> None:
        """Create one content validation failure.

        Args:
            code: catalog code of the failure.
            facts: fact values keyed by fact parameter name; non-string values are ``str()``-ized.
            language: message language for the rendered message; ``None`` uses the default
                language.
            message: pre-rendered message; when ``None``, the message is rendered from the code's
                template and the given facts.
            cause: underlying exception to chain as ``__cause__``, when any.
            errors: structured per-slot validation errors.
            params: partial extraction result, when validation ran on an extraction; may carry
                ``None`` values for the slots the semantic validator could not extract. An
                array-shaped caller schema yields a list of per-item objects, preserved as-is.
        """
        super().__init__(code, facts, language=language, message=message, cause=cause)
        self.errors: list[SlotValidationError] = list(errors or ())
        if params is None:
            self.params: dict[str, object] | list[object] = {}
        elif isinstance(params, Mapping):
            self.params = dict(params)
        else:
            self.params = list(params)


class A2ATParamExtractionError(A2ATBusinessError):
    """Shared failure type raised when validating a prompt and extracting parameters from it fails.

    The default code is ``slot.not_provided`` (Java default), matching the failure mode where a
    required slot is missing from the input. The structured per-slot details travel in
    :attr:`errors`.
    """

    def __init__(
        self,
        code: ErrorCatalog = ErrorCatalog.SLOT_NOT_PROVIDED,
        facts: Mapping[str, object] | None = None,
        *,
        language: str | None = None,
        message: str | None = None,
        cause: BaseException | None = None,
        errors: Iterable[SlotValidationError] | None = None,
    ) -> None:
        """Create one parameter extraction failure.

        Args:
            code: catalog code of the failure; defaults to ``slot.not_provided`` (the Java default).
            facts: fact values keyed by fact parameter name; non-string values are ``str()``-ized.
            language: message language for the rendered message; ``None`` uses the default
                language.
            message: pre-rendered message; when ``None``, the message is rendered from the code's
                template and the given facts.
            cause: underlying exception to chain as ``__cause__``, when any.
            errors: structured per-slot validation errors.
        """
        super().__init__(code, facts, language=language, message=message, cause=cause)
        self.errors: list[SlotValidationError] = list(errors or ())


class NegotiationGenerationError(A2ATBusinessError):
    """Raised when generating a negotiation message fails at runtime.

    Java parity: extends the business base through ``NegotiationProcessingException``; the Python
    tree collapses that intermediate base, so catch :class:`A2ATBusinessError` for full negotiation
    business-failure coverage.
    """


class NegotiationParamExtractionError(A2ATBusinessError):
    """Raised when validating a negotiation message and extracting its parameters fails.

    Java parity: extends the business base through ``NegotiationProcessingException``; the Python
    tree collapses that intermediate base (same as :class:`NegotiationGenerationError`). Carries
    the structured per-slot validation details in :attr:`errors` (Java
    ``NegotiationParamExtractionException.errors``); a message rendered upstream (for example by
    the shared validation pipeline) survives the wrap unchanged when passed explicitly, so the
    facts stay available to callers without re-rendering.
    """

    def __init__(
        self,
        code: ErrorCatalog,
        facts: Mapping[str, object] | None = None,
        *,
        language: str | None = None,
        message: str | None = None,
        cause: BaseException | None = None,
        errors: Iterable[SlotValidationError] | None = None,
    ) -> None:
        """Create one negotiation parameter extraction failure.

        Args:
            code: catalog code of the failure.
            facts: fact values keyed by fact parameter name; non-string values are ``str()``-ized.
            language: message language for the rendered message; ``None`` uses the default
                language.
            message: pre-rendered message; when ``None``, the message is rendered from the code's
                template and the given facts. A message rendered upstream (for example by the
                shared validation pipeline) survives the wrap unchanged, so the facts stay
                available to callers without re-rendering.
            cause: underlying exception to chain as ``__cause__``, when any.
            errors: structured per-slot validation errors.
        """
        super().__init__(code, facts, language=language, message=message, cause=cause)
        self.errors: list[SlotValidationError] = list(errors or ())


class ResourceNotFoundError(A2ATError):
    """Raised when a referenced SDK resource cannot be resolved.

    Java parity: ``ResourceNotFoundException`` extends ``A2ATError`` with the default
    ``infra.internal_error`` code; callers translate it to a more specific catalog code (for example
    ``template.load_failed``) at their boundary, mirroring the Java orchestrator catch points.
    """

    def __init__(
        self,
        message: str,
        resource_path: str,
        *,
        code: ErrorCatalog = ErrorCatalog.INFRA_INTERNAL_ERROR,
    ) -> None:
        """Create one resource resolution failure.

        Args:
            message: human-readable failure message.
            resource_path: path of the resource that could not be resolved, root-relative.
            code: catalog code of the failure; defaults to ``infra.internal_error`` — callers
                translate it to a more specific catalog code at their boundary.
        """
        super().__init__(message, code=code)
        self.resource_path = resource_path


class ConfigFileNotFoundError(A2ATError):
    """Raised when one required configuration file path does not exist.

    The message is rendered from the ``infra.config_invalid`` template with the missing path as the
    ``key`` fact, mirroring the Java constructor.
    """

    def __init__(self, path: Path) -> None:
        """Create one missing-config-file failure for the given path.

        The message is rendered from the ``infra.config_invalid`` template with the missing path as
        the ``key`` fact, mirroring the Java constructor.

        Args:
            path: the configuration file path that does not exist.
        """
        facts: dict[str, str] = {"key": str(path), "reason": "config file does not exist"}
        super().__init__(render(ErrorCatalog.INFRA_CONFIG_INVALID, facts), code=ErrorCatalog.INFRA_CONFIG_INVALID)
        self.path = path
