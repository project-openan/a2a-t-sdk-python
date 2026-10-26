"""Real-wiring assemblers of the corpus engine (port of the Java ``TaskApiAssembler`` and
``TypedInputAssembler``).

The corpus harness drives exactly the production wiring the facades drive internally, so a change
of the production assembly cannot drift past the corpus:

* :class:`TaskApiAssembler` assembles the three closed-loop task APIs through the real facade
  builders — ``generate_task_prompt_from_text`` / ``generate_task_prompt_from_data_with_schema``
  run through the client-side prompt generation orchestrator built by the same builder call the
  :class:`~a2a_t.client.a2at_client.A2ATClient` facade constructor makes, and
  ``validate_task_prompt_and_data_filling`` runs through the task content validator built by the
  same builder function the :class:`~a2a_t.server.a2at_server.A2ATServer` facade constructor uses.
  A minimal packaged-source ``.env`` (written once per language and retry limit into a temporary
  directory) is loaded through :meth:`a2a_t.config.models.A2ATConfig.load`, and the scripted LLM
  client is injected through the builders' ``llm_client`` seam — the Python advantage over the
  Java reflection-based harness: the injection is explicit.

* :func:`assemble_typed_input` converts the corpus ``input.data`` JSON onto the typed content
  records of :mod:`a2a_t.negotiation.content`. The corpus carries the typed content in the same
  snake_case shape the LLM extraction produces, so this assembler only converts — it never invents
  or reconstructs content. Deliberate performative-content contradictions (an ``Accept`` conclusion
  fed to the reject API) pass through unvalidated so the production from-data validation surfaces
  them. A malformed ``input.data`` node is a corpus authoring defect, not a production behavior:
  the assembler fails with a :class:`RuntimeError` naming the offending field, which no corpus
  expectation can match by accident (the programming-error carriers ``ValueError``/``TypeError``
  are deliberately avoided here because the corpus *does* expect those for real production
  programming errors).
"""

from __future__ import annotations

import tempfile
from collections.abc import Mapping
from pathlib import Path
from threading import Lock
from typing import Any, Final

from a2a_t.client.prompt_generation.prompt_generation_orchestrator_builder import (
    PromptGenerationOrchestratorBuilder,
)
from a2a_t.common.prompt_resources.models import ScenarioDefinition
from a2a_t.common.prompt_resources.resource_access import (
    PackagedPromptResourceAccess,
    PromptResourceAccess,
)
from a2a_t.common.prompt_resources.vocabulary import Vocabulary
from a2a_t.config.models import DEFAULT_LLM_MAX_ATTEMPTS, A2ATConfig
from a2a_t.core.errors.exceptions import ResourceNotFoundError
from a2a_t.core.metadata import MetadataContent, NegotiationContext, NegotiationPerformative
from a2a_t.core.standard_templates import TASK_EXTENSION_NAME
from a2a_t.core.template_uri import TemplateUri
from a2a_t.core.validation_pipeline import FilledParamData
from a2a_t.llm.provider import LLMClient
from a2a_t.negotiation.content.enums import NegotiationAction, NegotiationConclusion, NegotiationType
from a2a_t.negotiation.content.models import (
    FeasibilityEndingContent,
    FeasibilityProposeContent,
    InformationEndingContent,
    InformationProposeContent,
    NegotiationAbortContent,
    NegotiationAbortData,
    NegotiationEndingContent,
    NegotiationEndingData,
    NegotiationItem,
    NegotiationProposeContent,
    NegotiationProposeData,
    TargetEndingContent,
    TargetProposeContent,
)
from a2a_t.negotiation.resources.reference import NegotiationReference, uri_segment_of
from a2a_t.server.prompt_compliance.content_validator import build_content_validators

__all__ = [
    "DEFAULT_TASK_MAX_ATTEMPTS",
    "INJECT_FAILING_SEMANTIC_VALIDATOR",
    "INJECT_FAILING_TEMPLATE_LOADER",
    "INJECT_HOOKS",
    "TaskApiAssembler",
    "assemble_typed_input",
    "failing_semantic_validator",
    "failing_template_loader_access",
]

#: Name of the harness injection hook that makes every template load fail (generation matrix).
INJECT_FAILING_TEMPLATE_LOADER: Final[str] = "failingTemplateLoader"

#: Name of the harness injection hook that makes every semantic validation call fail (validate
#: matrix: the pipeline maps the resource failure onto the public ``template.not_found`` code).
INJECT_FAILING_SEMANTIC_VALIDATOR: Final[str] = "failingSemanticValidator"

#: Registry of the two harness injection hooks the engine wires onto real builder seams.
INJECT_HOOKS: Final[tuple[str, ...]] = (INJECT_FAILING_TEMPLATE_LOADER, INJECT_FAILING_SEMANTIC_VALIDATOR)

#: Default retry limit the task API assembly falls back to (the builder default of 3).
DEFAULT_TASK_MAX_ATTEMPTS: Final[int] = DEFAULT_LLM_MAX_ATTEMPTS

#: Written minimal-env cache, keyed by ``<language>/<maxAttempts>`` (Java ``ENV_FILES`` parity).
_ENV_FILES: dict[str, Path] = {}
_ENV_FILES_LOCK = Lock()

#: Sentinel distinguishing a missing JSON field from a present JSON ``null`` (Java ``MissingNode``).
_MISSING: Final[object] = object()


# --------------------------------------------------------------------------- task API assembly


class TaskApiAssembler:
    """Real-builder assembly of the three closed-loop task APIs (Q21).

    The same LLM client instance feeds the client-side and the server-side components, so an
    ``llmCalls`` expectation counts the whole closed loop.
    """

    def __init__(self, language: str, max_attempts: int, llm_client: LLMClient) -> None:
        """Assemble the task API wiring for one language.

        Args:
            language: language of the generated and validated task prompts, such as ``zh-CN``.
            max_attempts: retry limit of the LLM steps, mirroring the builder default of 3.
            llm_client: LLM client injected at the same seam the facade builders inject their real
                client.
        """
        self._init(_minimal_env_for(language, max_attempts), llm_client)

    @classmethod
    def from_env_path(cls, env_path: Path, llm_client: LLMClient) -> TaskApiAssembler:
        """Assemble the task API wiring around an explicit ``.env`` file — the seam of the live family.

        The live harness hands in its env bridge (real test-endpoint values plus the explicit
        stability knobs and the pipeline retry limit) instead of the scripted minimal env.

        Args:
            env_path: the ``.env`` file the config is loaded from, carrying the language and retry
                limit.
            llm_client: LLM client injected at the same seam the facade builders inject their real
                client.

        Returns:
            the task API assembly of the env file's language.
        """
        assembler = cls.__new__(cls)
        assembler._init(env_path, llm_client)
        return assembler

    def _init(self, env_path: Path, llm_client: LLMClient) -> None:
        """Wire the client-side orchestrator and the server-side task validator from one config."""
        config = A2ATConfig.load(env_path)
        self._prompt_generation = PromptGenerationOrchestratorBuilder().build(
            config=config,
            llm_client=llm_client,
        )
        self._task_validator = build_content_validators(config=config, llm_client=llm_client)[TASK_EXTENSION_NAME]

    def generate_task_prompt_from_text(self, text: str, template_uri: TemplateUri) -> MetadataContent:
        """Generate a task prompt with metadata from natural-language input.

        Args:
            text: natural-language task input.
            template_uri: template URI identifying the target task template.

        Returns:
            metadata content carrying the resolved template URI, rendered prompt text and Task-T
            extension URI.
        """
        return self._prompt_generation.generate_task_prompt_from_text(text, template_uri)

    def generate_task_prompt_from_data_with_schema(
        self,
        data: Mapping[str, object],
        schema: Mapping[str, object],
        template_uri: TemplateUri,
    ) -> MetadataContent:
        """Generate a task prompt with metadata from structured input and a data schema.

        Args:
            data: structured task input as a string-to-object mapping.
            schema: data schema mapping describing the meaning of each input field.
            template_uri: template URI identifying the target task template.

        Returns:
            metadata content carrying the resolved template URI, rendered prompt text and Task-T
            extension URI.
        """
        return self._prompt_generation.generate_task_prompt_from_data_with_schema(data, schema, template_uri)

    def validate_task_prompt_and_data_filling(
        self,
        prompt: str,
        schema: Mapping[str, object],
        template_uri: TemplateUri,
    ) -> FilledParamData:
        """Validate a task prompt and extract its filled parameters through the real server wiring.

        A schema slot the prompt misses surfaces as a ``None``-valued entry of the returned
        parameter data — that set of ``None``-valued keys is the missing-parameter set the
        negotiation loop then fills.

        Args:
            prompt: rendered task prompt text to validate.
            schema: caller-provided parameter JSON schema describing the parameters to extract.
            template_uri: template URI declaring the expected task template.

        Returns:
            filled parameter data carrying the extracted parameters; ``None`` values mark missing
            parameters.
        """
        return self._task_validator.validate(prompt, schema, template_uri)


def _minimal_env_for(language: str, max_attempts: int) -> Path:
    """Write (once per language and retry limit) the minimal packaged-source ``.env``.

    Follows the facade-test minimal-env precedent: the LLM entries are inert because the scripted
    client is injected at the builders' LLM seam.

    Args:
        language: language of the corpus case.
        max_attempts: retry limit of the LLM steps from the case's LLM script.

    Returns:
        path of the written ``.env`` file.
    """
    key = f"{language}/{max_attempts}"
    with _ENV_FILES_LOCK:
        cached = _ENV_FILES.get(key)
        if cached is not None:
            return cached
        env_file = Path(tempfile.mkdtemp(prefix="a2at-corpus-task-env")) / "client.env"
        env_file.write_text(
            "\n".join(
                (
                    f"A2AT_LANGUAGE={language}",
                    "A2AT_PROMPT_SOURCE_TYPE=packaged",
                    "A2AT_PROMPT_RESOURCE_LOCAL_ROOT_DIR=",
                    "A2AT_LLM_PROVIDER=openai",
                    "A2AT_LLM_MODEL=scripted-model",
                    "A2AT_LLM_BASE_URL=https://llm.example.test/v1",
                    "A2AT_LLM_API_KEY=corpus-scripted",
                    f"A2AT_LLM_MAX_ATTEMPTS={max_attempts}",
                    "",
                )
            ),
            encoding="utf-8",
            newline="\n",
        )
        _ENV_FILES[key] = env_file
        return env_file


class _FailingTemplateLoaderAccess(PromptResourceAccess):
    """Resource access whose every template load fails (the ``failingTemplateLoader`` hook).

    The D31 replacement of the Java ``NegotiationTemplateLoader`` injection point: everything else
    delegates to the packaged access — the vocabulary still loads at builder time — while
    ``template_text`` replays the template-not-found failure the generation matrix asserts.
    """

    def __init__(self) -> None:
        """Wrap the packaged access and fail only its template loads."""
        super().__init__()
        self._delegate = PackagedPromptResourceAccess()

    def packaged(self) -> bool:
        """Return whether routed resources are served from the packaged tree."""
        return self._delegate.packaged()

    def local_root_dir(self) -> Path | None:
        """Return the local root serving routed resources, or ``None`` in packaged mode."""
        return self._delegate.local_root_dir()

    def template_text(self, template_uri: str | TemplateUri, language: str) -> str:
        """Fail every template load with the resource-not-found failure of the Java hook."""
        raise ResourceNotFoundError("Negotiation template does not exist.", _uri_string(template_uri))

    def load_scenarios(self, language: str) -> list[ScenarioDefinition]:
        """Delegate scenario loading to the packaged access."""
        return self._delegate.load_scenarios(language)

    def template_entries(self) -> dict[str, str]:
        """Delegate template enumeration to the packaged access."""
        return self._delegate.template_entries()

    def slot_schema(self, template_uri: str | TemplateUri, language: str) -> dict[str, Any]:
        """Delegate slot-schema loading to the packaged access."""
        return self._delegate.slot_schema(template_uri, language)

    def load_prompt(self, category: str, language: str, file_name: str) -> str:
        """Delegate prompt loading to the packaged access."""
        return self._delegate.load_prompt(category, language, file_name)

    def load_vocabulary(self, language: str) -> Vocabulary:
        """Delegate vocabulary loading to the packaged access."""
        return self._delegate.load_vocabulary(language)

    def load_errors(self, language: str) -> dict[str, str]:
        """Delegate error-template loading to the packaged access."""
        return self._delegate.load_errors(language)


class _FailingSemanticValidator:
    """Semantic validator failing every call with a resource-not-found failure.

    Replays the semantic validator of a language without bundled semantic validation prompt
    resources: the pipeline maps the resource failure onto the public ``template.not_found`` code
    without bubbling the raw exception (the validate-family leg of the template-resolution matrix;
    mirrors the hand-written pipeline suite).
    """

    def validate(
        self,
        prompt: str,
        schema: Mapping[str, object],
        reference: NegotiationReference,
        template_content: str,
    ) -> Any:
        """Fail every semantic validation call.

        Raises:
            ResourceNotFoundError: carrying the missing prompt-resource path of the reference's
                language.
        """
        raise ResourceNotFoundError(
            "Negotiation semantic validation prompt resource does not exist for language "
            f"{reference.language}; set A2AT_LANGUAGE to a language with bundled prompt resources "
            "(zh-CN or en-US).",
            f"prompt_resources/prompts/negotiation_semantic_validation/{reference.language}/system.md",
        )


#: Exposed seam constructors for the engine's inject-hook wiring (real builder injection points).
failing_template_loader_access = _FailingTemplateLoaderAccess
failing_semantic_validator = _FailingSemanticValidator


def _uri_string(template_uri: str | TemplateUri) -> str:
    """Return the raw spelling of one template URI argument."""
    return template_uri if isinstance(template_uri, str) else template_uri.uri


# --------------------------------------------------------------------------- typed input assembly


def assemble_typed_input(
    data: Mapping[str, object],
    context: NegotiationContext | None,
    template_uri: TemplateUri | None,
    performative: NegotiationPerformative,
    language: str,
) -> NegotiationProposeData | NegotiationEndingData | NegotiationAbortData:
    """Assemble the typed API input of one from-data call.

    Args:
        data: typed content in the snake_case corpus shape.
        context: negotiation context of the case, or ``None`` for the null-context probes.
        template_uri: template URI addressed by the case, or ``None`` for the null-URI probes.
        performative: performative of the addressed generation method.
        language: language of the case.

    Returns:
        a :class:`NegotiationProposeData`, :class:`NegotiationEndingData` or
        :class:`NegotiationAbortData`.

    Raises:
        RuntimeError: when ``data`` is ``None`` or malformed (a corpus authoring defect).
        ValueError: when the template URI is missing or does not address a negotiation template of
            the expected performative (the type the typed content needs cannot be resolved).
    """
    if data is None:
        raise RuntimeError("input.data: the typed input data is required")
    negotiation_type = _resolve_type(template_uri, performative, language)
    if performative is NegotiationPerformative.PROPOSE:
        return NegotiationProposeData(context, _propose_content(data, negotiation_type))
    if performative in (NegotiationPerformative.ACCEPT, NegotiationPerformative.REJECT):
        return NegotiationEndingData(context, _ending_content(data, negotiation_type))
    return NegotiationAbortData(context, NegotiationAbortContent(_required_text(data, "termination_reason")))


def _propose_content(data: Mapping[str, object], negotiation_type: NegotiationType | None) -> NegotiationProposeContent:
    """Map one propose payload onto the typed propose content of the negotiation type."""
    if negotiation_type is None:
        raise _authoring("the abort performative carries no typed propose content")
    if negotiation_type is NegotiationType.INFORMATION:
        return InformationProposeContent(_required_items(data, "items"), _optional_text(data, "relationship"))
    if negotiation_type is NegotiationType.TARGET:
        return TargetProposeContent(
            _required_text(data, "target_negotiation_description"),
            _optional_items(data, "intent_understanding"),
            _optional_items(data, "alignment_and_clarification"),
            _optional_items(data, "request_for_clarification"),
            _optional_text(data, "target_confirm_request"),
        )
    return FeasibilityProposeContent(
        _required_text(data, "feasibility_negotiation_description"),
        _action(data),
        _optional_items(data, "contents_to_evaluate"),
        _optional_items(data, "infeasibility_details_and_proposal"),
        _optional_text(data, "feasibility_confirm_request"),
    )


def _ending_content(data: Mapping[str, object], negotiation_type: NegotiationType | None) -> NegotiationEndingContent:
    """Map one ending payload onto the typed ending content of the negotiation type."""
    if negotiation_type is None:
        raise _authoring("the abort performative carries no typed ending content")
    conclusion = _conclusion(data)
    if negotiation_type is NegotiationType.INFORMATION:
        return InformationEndingContent(conclusion, _required_items(data, "items"))
    if negotiation_type is NegotiationType.TARGET:
        return TargetEndingContent(
            conclusion, _optional_text(data, "confirmed_intent"), _optional_text(data, "failure_reason")
        )
    return FeasibilityEndingContent(conclusion, _required_text(data, "feasibility_summary"))


def _resolve_type(
    template_uri: TemplateUri | None, performative: NegotiationPerformative, language: str
) -> NegotiationType | None:
    """Resolve the negotiation type the addressed template declares for the performative."""
    if template_uri is None:
        raise ValueError("input.data: the template URI is missing but the typed input data needs the negotiation type")
    reference = NegotiationReference.from_template_uri(template_uri, performative, language)
    if reference is None:
        raise ValueError(
            "Template URI does not address a negotiation template of the expected performative "
            f"{performative} ({uri_segment_of(performative)}): {template_uri.uri}."
        )
    return reference.type


# --------------------------------------------------------------------------- field readers


def _conclusion(data: Mapping[str, object]) -> NegotiationConclusion:
    """Read the conclusion literal of one ending payload."""
    node = data.get("conclusion", _MISSING)
    if not isinstance(node, str):
        raise _authoring("conclusion must be the Accept or Reject literal")
    for candidate in NegotiationConclusion:
        if candidate.value == node:
            return candidate
    raise _authoring(f"conclusion must be the Accept or Reject literal but was '{node}'")


def _action(data: Mapping[str, object]) -> NegotiationAction:
    """Read the feasibility action enum name of one propose payload."""
    node = data.get("action", _MISSING)
    if not isinstance(node, str):
        raise _authoring("action must be one of the two feasibility action names")
    try:
        return NegotiationAction[node]
    except KeyError:
        raise _authoring(f"action must be one of the two feasibility action names but was '{node}'") from None


def _required_text(data: Mapping[str, object], field_name: str) -> str:
    """Read one required non-blank string field."""
    node = data.get(field_name, _MISSING)
    if not isinstance(node, str) or not node.strip():
        raise _authoring(f"{field_name} must be a non-blank string")
    return node


def _optional_text(data: Mapping[str, object], field_name: str) -> str | None:
    """Read one optional string field, ``None`` when missing or JSON ``null``."""
    node = data.get(field_name, _MISSING)
    if node is _MISSING or node is None:
        return None
    if not isinstance(node, str):
        raise _authoring(f"{field_name} must be a string or null")
    return node


def _required_items(data: Mapping[str, object], field_name: str) -> list[NegotiationItem]:
    """Read one required item-list field."""
    node = data.get(field_name, _MISSING)
    if node is _MISSING or node is None:
        raise _authoring(f"{field_name} must be an array of items")
    return _items(node, field_name)


def _optional_items(data: Mapping[str, object], field_name: str) -> list[NegotiationItem] | None:
    """Read one optional item-list field, ``None`` when missing or JSON ``null``."""
    node = data.get(field_name, _MISSING)
    if node is _MISSING or node is None:
        return None
    return _items(node, field_name)


def _items(node: object, field_name: str) -> list[NegotiationItem]:
    """Read one JSON array of ``{name, value}`` entries as typed negotiation items."""
    if not isinstance(node, list):
        raise _authoring(f"{field_name} must be an array of items")
    items: list[NegotiationItem] = []
    for entry in node:
        if not isinstance(entry, dict):
            raise _authoring(f"{field_name} must be an array of items")
        name = entry.get("name", _MISSING)
        if not isinstance(name, str) or not name.strip():
            raise _authoring(f"{field_name} contained an item without a name")
        value = entry.get("value", _MISSING)
        if value is not _MISSING and value is not None and not isinstance(value, str):
            raise _authoring(f"{field_name} contained an item whose value is not a string or null")
        items.append(NegotiationItem(name, value if isinstance(value, str) else None))
    return items


def _authoring(message: str) -> RuntimeError:
    """Build the corpus-authoring failure of one malformed ``input.data`` node."""
    return RuntimeError(f"input.data: {message}")
