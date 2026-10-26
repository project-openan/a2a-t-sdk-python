"""Facade API symmetry guard (port of Java ``NegotiationFacadeApiSymmetryTest``).

The Java sample module guards the negotiation content-layer API surface of the two high-level
facades with reflection: the client and the server facade expose the negotiation generation, query
and validation APIs as one symmetric surface — identical method names, parameter types and return
types — and the guard fails when one facade drifts from the other. This port replaces the
reflection sweep with ``inspect.signature`` parametrized tests (plan section 三、P7): for every
guarded method the two facades must agree on the parameter names, their order, kinds, annotations
and defaults, and on the return annotation.

The intentional asymmetries of the Java contract are pinned as explicit skips carrying the Java
reason: the seven prompt-side generation APIs exist on the client only (the client generates
prompts, the server checks them), while task-prompt compliance and the three extension content
validators exist on the server only (``checkTaskPrompt`` and
``validate{Task,Notification,Auth}PromptAndDataFilling`` have no client counterpart in Java). A
companion test pins each documented asymmetry as fact, so a skip reason cannot silently rot when
one facade later gains the counterpart. The retired negotiation query methods
(``get_negotiation_prompts`` / ``get_negotiation_prompt``) must stay absent from both facades,
mirroring the Java guard.

The output symmetry of the Java ``NegotiationFacadeOutputSymmetryTest`` is ported here as well:
both facades built from one config produce, for the identical golden fixture inputs, identical
:class:`~a2a_t.core.metadata.MetadataContent` records and identical metadata maps — the direct
proof that the client and the server wire the same negotiation content service from one config.
"""

from __future__ import annotations

import inspect
import re
import sys
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"

if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from a2a_t.client.a2at_client import A2ATClient
from a2a_t.core.metadata import MetadataContent, NegotiationPerformative
from a2a_t.negotiation.content import (
    NegotiationAbortData,
    NegotiationEndingData,
    NegotiationProposeData,
)
from a2a_t.server.a2at_server import A2ATServer
from tests.negotiation.generation.golden_inputs import (
    GOLDEN_CASES,
    LANGUAGES,
    GoldenCase,
)

#: The twelve negotiation content methods both facades expose as one symmetric surface.
NEGOTIATION_API_METHODS: tuple[str, ...] = (
    "generate_negotiation_propose_prompt_from_data",
    "generate_negotiation_accept_prompt_from_data",
    "generate_negotiation_reject_prompt_from_data",
    "generate_negotiation_abort_prompt_from_data",
    "generate_negotiation_propose_prompt_from_text",
    "generate_negotiation_accept_prompt_from_text",
    "generate_negotiation_reject_prompt_from_text",
    "generate_negotiation_abort_prompt_from_text",
    "validate_propose_prompt_and_data_filling",
    "validate_accept_prompt_and_data_filling",
    "validate_reject_prompt_and_data_filling",
    "validate_abort_prompt_and_data_filling",
)

#: The retired negotiation-scoped query methods the Java guard keeps absent from both facades.
REMOVED_NEGOTIATION_QUERY_METHODS: tuple[str, ...] = ("get_negotiation_prompts", "get_negotiation_prompt")

#: The extension-agnostic query methods both facades expose with one signature.
CROSS_EXTENSION_QUERY_METHODS: tuple[str, ...] = ("get_prompts", "get_prompt")

#: Name shape of the negotiation content API family, used to catch family drift beyond the closed
#: list (a new ``generate_negotiation_*`` or negotiation ``validate_*_prompt_and_data_filling``
#: method added to one facade only breaks the closed-set assertion below).
_NEGOTIATION_API_NAME_PATTERN = re.compile(
    r"generate_negotiation_\w+_prompt_from_(?:data|text)"
    r"|validate_(?:propose|accept|reject|abort)_prompt_and_data_filling"
)

#: Client-only methods of the Java contract: the prompt-side generation APIs. Java ``A2ATClient``
#: owns ``generateTaskPrompt`` and the six fromText/fromDataWithSchema variants; the server facade
#: has no counterpart (it owns the checking side instead). Skipped in the client-to-server sweep.
_CLIENT_ONLY_PROMPT_SIDE_METHODS: dict[str, str] = {
    "generate_task_prompt": (
        "client-only in the Java contract: scenario-recognition task prompt generation "
        "(the server facade owns check_task_prompt instead)"
    ),
    "generate_task_prompt_from_text": (
        "client-only prompt-side API in the Java contract (Java A2ATClient generateTaskPromptFromText)"
    ),
    "generate_task_prompt_from_data_with_schema": (
        "client-only prompt-side API in the Java contract (Java A2ATClient generateTaskPromptFromDataWithSchema)"
    ),
    "generate_auth_prompt_from_text": (
        "client-only prompt-side API in the Java contract (Java A2ATClient generateAuthPromptFromText)"
    ),
    "generate_auth_prompt_from_data_with_schema": (
        "client-only prompt-side API in the Java contract (Java A2ATClient generateAuthPromptFromDataWithSchema)"
    ),
    "generate_notification_prompt_from_text": (
        "client-only prompt-side API in the Java contract (Java A2ATClient generateNotificationPromptFromText)"
    ),
    "generate_notification_prompt_from_data_with_schema": (
        "client-only prompt-side API in the Java contract "
        "(Java A2ATClient generateNotificationPromptFromDataWithSchema)"
    ),
}

#: Server-only methods of the Java contract: task-prompt compliance and the three extension
#: content validators. Java ``A2ATServer`` owns ``checkTaskPrompt`` and
#: ``validate{Task,Notification,Auth}PromptAndDataFilling``; the client facade has no counterpart
#: (it owns the generation side instead). Skipped in the server-to-client sweep.
_SERVER_ONLY_VALIDATION_METHODS: dict[str, str] = {
    "check_task_prompt": (
        "server-only in the Java contract: task prompt compliance (the client facade generates "
        "task prompts instead of checking them)"
    ),
    "validate_task_prompt_and_data_filling": (
        "server-only extension content validator in the Java contract "
        "(Java A2ATServer validateTaskPromptAndDataFilling)"
    ),
    "validate_notification_prompt_and_data_filling": (
        "server-only extension content validator in the Java contract "
        "(Java A2ATServer validateNotificationPromptAndDataFilling)"
    ),
    "validate_auth_prompt_and_data_filling": (
        "server-only extension content validator in the Java contract "
        "(Java A2ATServer validateAuthPromptAndDataFilling)"
    ),
}

#: Both facades under guard, in a fixed order.
_FACADES: tuple[tuple[str, type], ...] = (("A2ATClient", A2ATClient), ("A2ATServer", A2ATServer))


# --------------------------------------------------------------------------------------
# Signature comparison helpers
# --------------------------------------------------------------------------------------


def _public_methods(facade: type) -> list[str]:
    """Return the sorted public method names declared on one facade."""
    return sorted(name for name, member in inspect.getmembers(facade, inspect.isfunction) if not name.startswith("_"))


def _assert_symmetric_signatures(source: type, target: type, method: str) -> None:
    """Assert one method exists on both facades with the identical spelled signature.

    Both facade modules evaluate annotations lazily (``from __future__ import annotations``), so
    the annotations compare as their source text; parameter names and order, kinds, defaults and
    the return annotation are compared one by one.
    """
    source_method = getattr(source, method, None)
    assert callable(source_method), f"{source.__name__}.{method} is missing"
    target_method = getattr(target, method, None)
    assert callable(target_method), f"{target.__name__}.{method} is missing"
    source_signature = inspect.signature(source_method)
    target_signature = inspect.signature(target_method)
    assert list(source_signature.parameters) == list(target_signature.parameters), (
        f"{source.__name__}.{method} and {target.__name__}.{method} disagree on the parameter names or their order"
    )
    for name, source_parameter in source_signature.parameters.items():
        target_parameter = target_signature.parameters[name]
        assert source_parameter.kind is target_parameter.kind, (
            f"{source.__name__}.{method} and {target.__name__}.{method} disagree on the kind of parameter {name!r}"
        )
        assert source_parameter.annotation == target_parameter.annotation, (
            f"{source.__name__}.{method} and {target.__name__}.{method} "
            f"disagree on the annotation of parameter {name!r} "
            f"({source_parameter.annotation!r} vs {target_parameter.annotation!r})"
        )
        assert source_parameter.default == target_parameter.default, (
            f"{source.__name__}.{method} and {target.__name__}.{method} disagree on the default of parameter {name!r}"
        )
    assert source_signature.return_annotation == target_signature.return_annotation, (
        f"{source.__name__}.{method} and {target.__name__}.{method} "
        f"disagree on the return annotation "
        f"({source_signature.return_annotation!r} vs {target_signature.return_annotation!r})"
    )


def _surface_params(facade: type, only_method_reasons: dict[str, str]) -> list[Any]:
    """Build the parametrize list of one facade's public methods, skipping the documented
    one-sided methods with the Java contract reason."""
    params: list[Any] = []
    for method in _public_methods(facade):
        reason = only_method_reasons.get(method)
        if reason is None:
            params.append(method)
        else:
            params.append(pytest.param(method, marks=pytest.mark.skip(reason=reason)))
    return params


# --------------------------------------------------------------------------------------
# The twelve negotiation content methods (Java bothFacadesExposeTheSameTwelveNegotiationApiMethods)
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize("method", NEGOTIATION_API_METHODS)
def test_negotiation_api_method_is_symmetric(method: str) -> None:
    """Every negotiation content method exists on both facades with the identical signature."""
    _assert_symmetric_signatures(A2ATClient, A2ATServer, method)


@pytest.mark.parametrize("facade_name,facade", _FACADES)
def test_negotiation_api_family_is_exactly_the_twelve_methods(facade_name: str, facade: type) -> None:
    """The negotiation content API family present on one facade is exactly the closed set of twelve.

    This is the drift guard beyond the closed list: a negotiation-family method added to only one
    facade (or a renamed one) breaks the closed-set equality here, in both directions.
    """
    family = {method for method in _public_methods(facade) if _NEGOTIATION_API_NAME_PATTERN.fullmatch(method)}
    assert family == set(NEGOTIATION_API_METHODS), (
        f"{facade_name} negotiation content API family drifted from the closed set of twelve methods"
    )


# --------------------------------------------------------------------------------------
# The retired negotiation query methods (Java neitherFacadeExposesTheRemovedNegotiationQueryMethods)
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize("facade_name,facade", _FACADES)
@pytest.mark.parametrize("method", REMOVED_NEGOTIATION_QUERY_METHODS)
def test_removed_negotiation_query_method_is_absent(facade_name: str, facade: type, method: str) -> None:
    """Neither facade exposes the removed negotiation-scoped query methods."""
    assert not hasattr(facade, method), (
        f"{facade_name} must not expose the removed {method} query method; "
        "use the extension-agnostic get_prompts / get_prompt instead"
    )


# --------------------------------------------------------------------------------------
# The cross-extension query methods (Java bothFacadesExposeTheSameCrossExtensionQueryMethods)
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize("method", CROSS_EXTENSION_QUERY_METHODS)
def test_cross_extension_query_method_is_symmetric(method: str) -> None:
    """The extension-agnostic template queries exist on the client and match on the server."""
    assert callable(getattr(A2ATClient, method, None)), f"A2ATClient is missing {method}"
    _assert_symmetric_signatures(A2ATClient, A2ATServer, method)


def test_client_exposes_exactly_the_cross_extension_query_methods() -> None:
    """The client facade exposes both cross-extension query methods and no other one."""
    exposed = {method for method in _public_methods(A2ATClient) if method in CROSS_EXTENSION_QUERY_METHODS}
    assert exposed == set(CROSS_EXTENSION_QUERY_METHODS)


# --------------------------------------------------------------------------------------
# Whole public surface sweep with the documented intentional asymmetries
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize("method", _surface_params(A2ATClient, _CLIENT_ONLY_PROMPT_SIDE_METHODS))
def test_every_client_public_method_is_symmetric_on_the_server(method: str) -> None:
    """Every client facade public method exists on the server facade with the identical signature.

    The seven prompt-side generation APIs are the documented client-only exception of the Java
    contract and are skipped with their reason; every other client method — the twelve negotiation
    content methods and both template queries — must stay symmetric.
    """
    _assert_symmetric_signatures(A2ATClient, A2ATServer, method)


@pytest.mark.parametrize("method", _surface_params(A2ATServer, _SERVER_ONLY_VALIDATION_METHODS))
def test_every_server_public_method_is_symmetric_on_the_client(method: str) -> None:
    """Every server facade public method exists on the client facade with the identical signature.

    Task-prompt compliance and the three extension content validators are the documented
    server-only exception of the Java contract and are skipped with their reason; every other
    server method must stay symmetric.
    """
    _assert_symmetric_signatures(A2ATServer, A2ATClient, method)


@pytest.mark.parametrize("method", sorted(_CLIENT_ONLY_PROMPT_SIDE_METHODS))
def test_client_only_prompt_side_method_has_no_server_counterpart(method: str) -> None:
    """The documented client-only prompt-side methods really are absent on the server facade.

    This pins the skip table of the client-to-server sweep as fact: when the server facade later
    gains one of these methods, the skip turns stale and this test (or the sweep) fails, forcing
    the asymmetry table to be revisited.
    """
    assert not hasattr(A2ATServer, method)


@pytest.mark.parametrize("method", sorted(_SERVER_ONLY_VALIDATION_METHODS))
def test_server_only_validation_method_has_no_client_counterpart(method: str) -> None:
    """The documented server-only compliance and validation methods really are absent on the client."""
    assert not hasattr(A2ATClient, method)


# --------------------------------------------------------------------------------------
# Output symmetry across the two facades (Java NegotiationFacadeOutputSymmetryTest)
# --------------------------------------------------------------------------------------


def _write_env(tmp_path: Path, language: str) -> Path:
    """Write one facade env file selecting one language and the packaged resource source."""
    env_path = tmp_path / "symmetry.env"
    env_path.write_text(
        "\n".join(
            (
                f"A2AT_LANGUAGE={language}",
                "A2AT_PROMPT_SOURCE_TYPE=packaged",
                "A2AT_LLM_PROVIDER=openai",
                "A2AT_LLM_MODEL=test-model",
                "A2AT_LLM_BASE_URL=https://llm.example.test/v1",
                "A2AT_LLM_API_KEY=test-key",
                "",
            )
        ),
        encoding="utf-8",
    )
    return env_path


def _build_facades(env_path: Path) -> tuple[A2ATClient, A2ATServer]:
    """Build both facades from one config, with the LLM client factory patched to a stand-in."""
    with (
        patch("a2a_t.client.a2at_client.LLMClientFactory.create", return_value=object()),
        patch("a2a_t.server.a2at_server.LLMClientFactory.create", return_value=object()),
    ):
        return A2ATClient(env_path=env_path), A2ATServer(env_path=env_path)


def _fixture_data(golden_case: GoldenCase, language: str) -> Any:
    """Build the typed fixture input of one golden case, fresh per call."""
    context = golden_case.context()
    content = golden_case.content(language)
    if golden_case.performative is NegotiationPerformative.PROPOSE:
        return NegotiationProposeData(context, content)
    if golden_case.performative in (NegotiationPerformative.ACCEPT, NegotiationPerformative.REJECT):
        return NegotiationEndingData(context, content)
    return NegotiationAbortData(context, content)


def _from_data_call(
    facade: A2ATClient | A2ATServer,
    golden_case: GoldenCase,
    language: str,
) -> MetadataContent:
    """Generate one golden fixture through one facade using the from-data method of its performative."""
    data = _fixture_data(golden_case, language)
    performative = golden_case.performative
    if performative is NegotiationPerformative.PROPOSE:
        return facade.generate_negotiation_propose_prompt_from_data(data, golden_case.template_uri)
    if performative is NegotiationPerformative.ACCEPT:
        return facade.generate_negotiation_accept_prompt_from_data(data, golden_case.template_uri)
    if performative is NegotiationPerformative.REJECT:
        return facade.generate_negotiation_reject_prompt_from_data(data, golden_case.template_uri)
    return facade.generate_negotiation_abort_prompt_from_data(data, golden_case.template_uri)


@pytest.mark.parametrize("language", LANGUAGES)
@pytest.mark.parametrize("golden_case", GOLDEN_CASES, ids=[case.name for case in GOLDEN_CASES])
def test_both_facades_generate_the_identical_negotiation_message(
    golden_case: GoldenCase, language: str, tmp_path: Path
) -> None:
    """Both facades built from one config produce identical outputs for identical inputs.

    The from-data legs are deterministic and never call the LLM, so the whole
    :class:`~a2a_t.core.metadata.MetadataContent` record and the metadata map built from it must
    be equal on both sides — the direct proof that the client and the server wire the same
    negotiation content service from the same config and the same built-in resources.
    """
    client, server = _build_facades(_write_env(tmp_path, language))

    client_result = _from_data_call(client, golden_case, language)
    server_result = _from_data_call(server, golden_case, language)

    assert client_result == server_result
    assert client_result.build_metadata_content() == server_result.build_metadata_content()


@pytest.mark.parametrize("language", LANGUAGES)
def test_both_facades_list_the_same_template_catalog(language: str, tmp_path: Path) -> None:
    """Both facades built from one config answer the template queries with the same catalog."""
    client, server = _build_facades(_write_env(tmp_path, language))

    client_prompts = client.get_prompts()
    server_prompts = server.get_prompts()

    assert [prompt.template_uri.uri for prompt in client_prompts] == [
        prompt.template_uri.uri for prompt in server_prompts
    ]
    assert client_prompts == server_prompts
    assert client.get_prompt("Negotiation-T/common/abort/v1") == server.get_prompt("Negotiation-T/common/abort/v1")
