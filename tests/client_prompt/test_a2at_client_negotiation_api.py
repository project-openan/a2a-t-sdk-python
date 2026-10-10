"""Negotiation content API of the client facade (port of Java ``A2ATClientNegotiationApiTest``).

The suite pins the facade surface added in P7: the twelve negotiation content methods delegating
to the shared :class:`~a2a_t.negotiation.generation.content_service.NegotiationContentService`
plus the template queries, with the raw string template URI boundary failing fast (D16) exactly
like the service boundary. The from-data legs run against the real packaged templates with no LLM
(the factory is patched to a stand-in client), the from-text and validation legs run against a
scripted LLM client recording every structured call, and the golden fixtures pin the rendered
messages byte-for-byte. The deprecation shim round (D1) is pinned here too: the three legacy
state-machine facade methods warn, and so does the import of every old negotiation demo package.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path
from types import ModuleType
from typing import Any, Callable
from unittest.mock import patch

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = PROJECT_ROOT / "src"

if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from a2a_t.client.a2at_client import A2ATClient
from a2a_t.common.prompt_resources import SOURCE_PACKAGED, PromptTemplate
from a2a_t.core.metadata import MetadataContent, NegotiationContext, NegotiationPerformative
from a2a_t.core.standard_templates import (
    INFORMATION_NEGOTIATION_ACCEPT_REJECT_URI,
    INFORMATION_NEGOTIATION_PROPOSE,
    INFORMATION_NEGOTIATION_PROPOSE_URI,
    NEGOTIATION_ABORT_URI,
    NEGOTIATION_EXTENSION_NAME,
)
from a2a_t.core.template_uri import TemplateUri
from a2a_t.core.validation_pipeline import FilledParamData
from a2a_t.llm.models import LLMResponse
from a2a_t.negotiation.content import (
    InformationEndingContent,
    InformationProposeContent,
    NegotiationAbortContent,
    NegotiationAbortData,
    NegotiationConclusion,
    NegotiationEndingData,
    NegotiationItem,
    NegotiationProposeData,
)
from tests.negotiation.generation.golden_inputs import (
    GOLDEN_CASES,
    LANGUAGES,
    GoldenCase,
    read_golden_fixture,
)

SESSION_ID = "3dbc13b5-bd57-4c2b-b503-24e381b6c8d3"

#: The twelve negotiation content methods of the facade, in declaration order.
NEGOTIATION_API_METHODS = (
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

#: The six retired state-machine negotiation demo packages kept for one release (D1).
OLD_NEGOTIATION_PACKAGES = (
    "a2a_t.negotiation.common",
    "a2a_t.negotiation.types",
    "a2a_t.negotiation.store",
    "a2a_t.negotiation.runtime",
    "a2a_t.negotiation.handling",
    "a2a_t.negotiation.rendering",
)


class ScriptedLlmClient:
    """LLM boundary fake replaying scripted payloads and recording every structured call."""

    def __init__(self, *payloads: str) -> None:
        self.payloads = list(payloads)
        self.calls = 0
        self.last_messages: list[dict[str, str]] = []
        self.last_schema: dict[str, Any] = {}

    def structured(
        self,
        *,
        messages: list[dict[str, str]],
        json_schema: dict[str, Any],
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> LLMResponse:
        self.calls += 1
        self.last_messages = messages
        self.last_schema = json_schema
        payload = self.payloads[min(self.calls - 1, len(self.payloads) - 1)]
        return LLMResponse(
            content=payload, model="test-model", usage={"prompt_tokens": 1, "completion_tokens": 1}, metadata={}
        )


def write_env(tmp_path: Path, language: str) -> Path:
    """Write one facade env file selecting one language and the packaged resource source."""
    env_path = tmp_path / "client.env"
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


def build_client(env_path: Path, llm_client: object | None = None) -> A2ATClient:
    """Build one client facade whose LLM client factory call returns the given stand-in client."""
    with patch("a2a_t.client.a2at_client.LLMClientFactory.create", return_value=llm_client or object()):
        return A2ATClient(env_path=env_path)


def propose_data(round_: int = 2) -> NegotiationProposeData:
    """The information propose fixture input carrying one required item."""
    return NegotiationProposeData(
        NegotiationContext(SESSION_ID, round_, 5, NegotiationPerformative.PROPOSE),
        InformationProposeContent([NegotiationItem("节能区域", "松山湖")], None),
    )


def ending_data(conclusion: NegotiationConclusion) -> NegotiationEndingData:
    """The information ending fixture input carrying one delivered item."""
    performative = (
        NegotiationPerformative.ACCEPT if conclusion is NegotiationConclusion.ACCEPT else NegotiationPerformative.REJECT
    )
    return NegotiationEndingData(
        NegotiationContext(SESSION_ID, 2, 5, performative),
        InformationEndingContent(conclusion, [NegotiationItem("节能区域", "松山湖")]),
    )


def abort_data() -> NegotiationAbortData:
    """The common abort fixture input carrying the termination reason."""
    return NegotiationAbortData(
        NegotiationContext(SESSION_ID, 5, 5, NegotiationPerformative.ABORT),
        NegotiationAbortContent("已达到协商轮次上限，本次协商确认终止。"),
    )


# --------------------------------------------------------------------------------------
# from-data generation: the golden fixture set through the facade, byte-for-byte
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize("language", LANGUAGES)
@pytest.mark.parametrize("golden_case", GOLDEN_CASES, ids=[case.name for case in GOLDEN_CASES])
def test_from_data_generates_the_java_golden_message(golden_case: GoldenCase, language: str, tmp_path: Path) -> None:
    client = build_client(write_env(tmp_path, language))
    context = golden_case.context()
    content = golden_case.content(language)
    data: Any
    if golden_case.performative is NegotiationPerformative.PROPOSE:
        data = NegotiationProposeData(context, content)
        method = client.generate_negotiation_propose_prompt_from_data
    elif golden_case.performative is NegotiationPerformative.ACCEPT:
        data = NegotiationEndingData(context, content)
        method = client.generate_negotiation_accept_prompt_from_data
    elif golden_case.performative is NegotiationPerformative.REJECT:
        data = NegotiationEndingData(context, content)
        method = client.generate_negotiation_reject_prompt_from_data
    else:
        data = NegotiationAbortData(context, content)
        method = client.generate_negotiation_abort_prompt_from_data

    result = method(data, golden_case.template_uri)

    assert isinstance(result, MetadataContent)
    assert result.template_uri == golden_case.template_uri
    assert result.prompt_text == read_golden_fixture(golden_case, language)
    assert result.negotiation_context == context
    metadata = result.build_metadata_content()
    assert metadata[result.extension_uri] == result.prompt_text
    assert metadata["templateUri"] == golden_case.template_uri


@pytest.mark.parametrize("language", LANGUAGES)
def test_from_data_round_trip_metadata_carries_the_negotiation_context(language: str, tmp_path: Path) -> None:
    client = build_client(write_env(tmp_path, language))

    result = client.generate_negotiation_abort_prompt_from_data(abort_data(), NEGOTIATION_ABORT_URI)

    assert result.negotiation_context is not None
    assert result.negotiation_context.round == 5
    assert result.negotiation_context.performative is NegotiationPerformative.ABORT
    negotiation_context = result.build_metadata_content()["negotiationContext"]
    assert negotiation_context == {"id": SESSION_ID, "round": 5, "maxRounds": 5, "performative": "ABORT"}


# --------------------------------------------------------------------------------------
# from-text generation: one scripted LLM extraction call per performative
# --------------------------------------------------------------------------------------

_FROM_TEXT_CASES: tuple[tuple[str, str, Callable[[A2ATClient], Any], str], ...] = (
    (
        "propose",
        '{"items":[{"name":"接入端口名称","value":"举例：P533-珠江旧城-PTN3900-23-TPA1EG24-1"}],"relationship":null}',
        lambda client: client.generate_negotiation_propose_prompt_from_text(
            "请提供接入端口名称。",
            NegotiationContext(SESSION_ID, 1, 5, NegotiationPerformative.PROPOSE),
            INFORMATION_NEGOTIATION_PROPOSE_URI,
        ),
        "接入端口名称",
    ),
    (
        "accept",
        '{"conclusion":"Accept","items":[{"name":"接入端口名称","value":"P533-珠江旧城-PTN3900-23-TPA1EG24-1"}]}',
        lambda client: client.generate_negotiation_accept_prompt_from_text(
            "同意并接入端口名称。",
            NegotiationContext(SESSION_ID, 2, 5, NegotiationPerformative.ACCEPT),
            INFORMATION_NEGOTIATION_ACCEPT_REJECT_URI,
        ),
        "Accept",
    ),
    (
        "reject",
        '{"conclusion":"Reject","items":[{"name":"接入端口名称","value":"无法提供，工作台侧端口资源台账暂不可查"}]}',
        lambda client: client.generate_negotiation_reject_prompt_from_text(
            "无法提供接入端口名称。",
            NegotiationContext(SESSION_ID, 2, 5, NegotiationPerformative.REJECT),
            INFORMATION_NEGOTIATION_ACCEPT_REJECT_URI,
        ),
        "Reject",
    ),
    (
        "abort",
        '{"termination_reason":"已达到协商轮次上限，本次协商确认终止。"}',
        lambda client: client.generate_negotiation_abort_prompt_from_text(
            "已达到协商轮次上限，终止协商。",
            NegotiationContext(SESSION_ID, 5, 5, NegotiationPerformative.ABORT),
            NEGOTIATION_ABORT_URI,
        ),
        "已达到协商轮次上限，本次协商确认终止。",
    ),
)


@pytest.mark.parametrize(("name", "payload", "call", "expected_fragment"), _FROM_TEXT_CASES)
def test_from_text_generates_after_one_llm_extraction_call(
    name: str, payload: str, call: Callable[[A2ATClient], Any], expected_fragment: str, tmp_path: Path
) -> None:
    llm = ScriptedLlmClient(payload)
    client = build_client(write_env(tmp_path, "zh-CN"), llm)

    result = call(client)

    assert result.template_uri in (
        INFORMATION_NEGOTIATION_PROPOSE_URI,
        INFORMATION_NEGOTIATION_ACCEPT_REJECT_URI,
        NEGOTIATION_ABORT_URI,
    )
    assert expected_fragment in (result.prompt_text or "")
    assert llm.calls == 1
    assert llm.last_messages[0]["role"] == "system"
    assert "协商阶段：propose" in llm.last_messages[1]["content"] or name != "propose"


def test_from_text_rejects_a_wrong_family_template_uri(tmp_path: Path) -> None:
    client = build_client(write_env(tmp_path, "zh-CN"), ScriptedLlmClient("{}"))

    with pytest.raises(ValueError):
        client.generate_negotiation_propose_prompt_from_text(
            "请提供接入端口名称。",
            NegotiationContext(SESSION_ID, 1, 5, NegotiationPerformative.PROPOSE),
            NEGOTIATION_ABORT_URI,
        )


# --------------------------------------------------------------------------------------
# validation: the full chain over the message the generation leg actually rendered
# --------------------------------------------------------------------------------------

_VALIDATE_CASES: tuple[tuple[str, str, Callable[[A2ATClient], str], Any, str, Any], ...] = (
    (
        "propose",
        '{"semantic_verdict":true,"negotiation_type":"information","errors":[],"params":{"energyRegion":"松山湖"}}',
        lambda client: (
            client.generate_negotiation_propose_prompt_from_data(
                propose_data(), INFORMATION_NEGOTIATION_PROPOSE_URI
            ).prompt_text
            or ""
        ),
        NegotiationContext(SESSION_ID, 2, 5, NegotiationPerformative.PROPOSE),
        INFORMATION_NEGOTIATION_PROPOSE_URI,
        {"energyRegion": "松山湖"},
    ),
    (
        "accept",
        '{"semantic_verdict":true,"negotiation_type":"information","errors":[],"params":{"energyRegion":"松山湖"}}',
        lambda client: (
            client.generate_negotiation_accept_prompt_from_data(
                ending_data(NegotiationConclusion.ACCEPT), INFORMATION_NEGOTIATION_ACCEPT_REJECT_URI
            ).prompt_text
            or ""
        ),
        NegotiationContext(SESSION_ID, 2, 5, NegotiationPerformative.ACCEPT),
        INFORMATION_NEGOTIATION_ACCEPT_REJECT_URI,
        {"energyRegion": "松山湖"},
    ),
    (
        "reject",
        '{"semantic_verdict":true,"negotiation_type":"information","errors":[],"params":'
        '{"unavailableItem":"节能区域"}}',
        lambda client: (
            client.generate_negotiation_reject_prompt_from_data(
                ending_data(NegotiationConclusion.REJECT), INFORMATION_NEGOTIATION_ACCEPT_REJECT_URI
            ).prompt_text
            or ""
        ),
        NegotiationContext(SESSION_ID, 2, 5, NegotiationPerformative.REJECT),
        INFORMATION_NEGOTIATION_ACCEPT_REJECT_URI,
        {"unavailableItem": "节能区域"},
    ),
    (
        "abort",
        '{"semantic_verdict":true,"negotiation_type":null,"errors":[],"params":{"terminationReason":"超时"}}',
        lambda client: (
            client.generate_negotiation_abort_prompt_from_data(abort_data(), NEGOTIATION_ABORT_URI).prompt_text or ""
        ),
        NegotiationContext(SESSION_ID, 5, 5, NegotiationPerformative.ABORT),
        NEGOTIATION_ABORT_URI,
        {"terminationReason": "超时"},
    ),
)

_VALIDATE_METHODS: dict[str, Callable[..., Any]] = {
    "propose": "validate_propose_prompt_and_data_filling",
    "accept": "validate_accept_prompt_and_data_filling",
    "reject": "validate_reject_prompt_and_data_filling",
    "abort": "validate_abort_prompt_and_data_filling",
}

_VALIDATE_CONTEXTS: dict[str, NegotiationContext] = {case[0]: case[3] for case in _VALIDATE_CASES}


@pytest.mark.parametrize(("name", "payload", "message_builder", "context", "template_uri", "params"), _VALIDATE_CASES)
def test_validate_extracts_params_from_the_generated_message(
    name: str,
    payload: str,
    message_builder: Callable[[A2ATClient], str],
    context: NegotiationContext,
    template_uri: str,
    params: dict[str, str],
    tmp_path: Path,
) -> None:
    llm = ScriptedLlmClient(payload)
    client = build_client(write_env(tmp_path, "zh-CN"), llm)
    message = message_builder(client)
    schema = {"type": "object", "properties": {key: {"type": "string"} for key in params}}

    filled = getattr(client, _VALIDATE_METHODS[name])(message, context, schema, template_uri)

    assert isinstance(filled, FilledParamData)
    assert llm.calls == 1
    assert filled.data["id"] == SESSION_ID
    assert filled.data["round"] == context.round
    assert filled.data["maxRounds"] == 5
    for key, value in params.items():
        assert filled.data[key] == value


def test_validate_reports_a_null_context_as_not_being_a_negotiation_message(tmp_path: Path) -> None:
    client = build_client(write_env(tmp_path, "zh-CN"), ScriptedLlmClient("{}"))

    with pytest.raises(Exception) as excinfo:
        client.validate_propose_prompt_and_data_filling(
            "rendered negotiation message", None, {"type": "object"}, INFORMATION_NEGOTIATION_PROPOSE_URI
        )

    assert "negotiation.invalid_input" in str(excinfo.value) or excinfo.value.args


# --------------------------------------------------------------------------------------
# template URI boundary: malformed and wrong-family URIs fail fast on every method
# --------------------------------------------------------------------------------------

_MALFORMED_URIS = ("Task-T/only-one-segment", "  ", "a/b")


def _boundary_calls(client: A2ATClient) -> dict[str, Callable[..., Any]]:
    """One call closure per URI-carrying facade method, with the matching stand-in arguments."""
    propose_context = NegotiationContext(SESSION_ID, 1, 5, NegotiationPerformative.PROPOSE)
    abort_context = NegotiationContext(SESSION_ID, 5, 5, NegotiationPerformative.ABORT)
    return {
        "generate_negotiation_propose_prompt_from_data": lambda uri: (
            client.generate_negotiation_propose_prompt_from_data(  # noqa: E501
                propose_data(), uri
            )
        ),
        "generate_negotiation_accept_prompt_from_data": lambda uri: client.generate_negotiation_accept_prompt_from_data(  # noqa: E501
            ending_data(NegotiationConclusion.ACCEPT), uri
        ),
        "generate_negotiation_reject_prompt_from_data": lambda uri: client.generate_negotiation_reject_prompt_from_data(  # noqa: E501
            ending_data(NegotiationConclusion.REJECT), uri
        ),
        "generate_negotiation_abort_prompt_from_data": lambda uri: client.generate_negotiation_abort_prompt_from_data(
            abort_data(), uri
        ),
        "generate_negotiation_propose_prompt_from_text": lambda uri: (
            client.generate_negotiation_propose_prompt_from_text("请提供接入端口名称。", propose_context, uri)
        ),
        "generate_negotiation_accept_prompt_from_text": lambda uri: client.generate_negotiation_accept_prompt_from_text(
            "同意。", NegotiationContext(SESSION_ID, 2, 5, NegotiationPerformative.ACCEPT), uri
        ),
        "generate_negotiation_reject_prompt_from_text": lambda uri: client.generate_negotiation_reject_prompt_from_text(
            "无法提供。", NegotiationContext(SESSION_ID, 2, 5, NegotiationPerformative.REJECT), uri
        ),
        "generate_negotiation_abort_prompt_from_text": lambda uri: client.generate_negotiation_abort_prompt_from_text(
            "终止协商。", abort_context, uri
        ),
        "validate_propose_prompt_and_data_filling": lambda uri: client.validate_propose_prompt_and_data_filling(
            "message", propose_context, {"type": "object"}, uri
        ),
        "validate_accept_prompt_and_data_filling": lambda uri: client.validate_accept_prompt_and_data_filling(
            "message", NegotiationContext(SESSION_ID, 2, 5, NegotiationPerformative.ACCEPT), {"type": "object"}, uri
        ),
        "validate_reject_prompt_and_data_filling": lambda uri: client.validate_reject_prompt_and_data_filling(
            "message", NegotiationContext(SESSION_ID, 2, 5, NegotiationPerformative.REJECT), {"type": "object"}, uri
        ),
        "validate_abort_prompt_and_data_filling": lambda uri: client.validate_abort_prompt_and_data_filling(
            "message", abort_context, {"type": "object"}, uri
        ),
        "get_prompt": client.get_prompt,
    }


@pytest.mark.parametrize("method_name", (*NEGOTIATION_API_METHODS, "get_prompt"))
@pytest.mark.parametrize("malformed_uri", _MALFORMED_URIS, ids=["one-segment", "blank", "two-segments"])
def test_template_uri_boundary_rejects_malformed_uris(method_name: str, malformed_uri: str, tmp_path: Path) -> None:
    client = build_client(write_env(tmp_path, "zh-CN"))

    with pytest.raises(ValueError, match="Unparseable template URI"):
        _boundary_calls(client)[method_name](malformed_uri)


@pytest.mark.parametrize("method_name", (*NEGOTIATION_API_METHODS, "get_prompt"))
def test_template_uri_boundary_rejects_a_null_uri(method_name: str, tmp_path: Path) -> None:
    client = build_client(write_env(tmp_path, "zh-CN"))

    with pytest.raises(TypeError):
        _boundary_calls(client)[method_name](None)


_WRONG_FAMILY_CASES: tuple[tuple[str, str], ...] = (
    # (method, well-formed URI of the wrong family for that method)
    ("generate_negotiation_propose_prompt_from_data", INFORMATION_NEGOTIATION_ACCEPT_REJECT_URI),
    ("generate_negotiation_accept_prompt_from_data", NEGOTIATION_ABORT_URI),
    ("generate_negotiation_reject_prompt_from_data", INFORMATION_NEGOTIATION_PROPOSE_URI),
    ("generate_negotiation_abort_prompt_from_data", INFORMATION_NEGOTIATION_PROPOSE_URI),
    ("generate_negotiation_propose_prompt_from_text", NEGOTIATION_ABORT_URI),
    ("generate_negotiation_abort_prompt_from_text", INFORMATION_NEGOTIATION_PROPOSE_URI),
    ("validate_propose_prompt_and_data_filling", INFORMATION_NEGOTIATION_ACCEPT_REJECT_URI),
    ("validate_abort_prompt_and_data_filling", INFORMATION_NEGOTIATION_PROPOSE_URI),
)


@pytest.mark.parametrize(("method_name", "wrong_family_uri"), _WRONG_FAMILY_CASES)
def test_template_uri_boundary_rejects_wrong_family_uris(
    method_name: str, wrong_family_uri: str, tmp_path: Path
) -> None:
    client = build_client(write_env(tmp_path, "zh-CN"), ScriptedLlmClient("{}"))

    with pytest.raises(ValueError):
        _boundary_calls(client)[method_name](wrong_family_uri)


# --------------------------------------------------------------------------------------
# delegation wiring: every facade method calls the matching service method with the typed URI
# --------------------------------------------------------------------------------------


class RecordingContentService:
    """Stand-in of the negotiation content service recording every delegated call."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...]]] = []

    def __getattr__(self, name: str) -> Callable[..., str]:
        def record(*args: Any) -> str:
            self.calls.append((name, args))
            return f"{name}:ok"

        return record


_DELEGATION_CASES: tuple[tuple[str, str, tuple[Any, ...]], ...] = (
    (
        "generate_negotiation_propose_prompt_from_data",
        "generate_propose_from_data",
        (propose_data(), INFORMATION_NEGOTIATION_PROPOSE_URI),
    ),
    (
        "generate_negotiation_accept_prompt_from_data",
        "generate_accept_from_data",
        (ending_data(NegotiationConclusion.ACCEPT), INFORMATION_NEGOTIATION_ACCEPT_REJECT_URI),
    ),
    (
        "generate_negotiation_reject_prompt_from_data",
        "generate_reject_from_data",
        (ending_data(NegotiationConclusion.REJECT), INFORMATION_NEGOTIATION_ACCEPT_REJECT_URI),
    ),
    (
        "generate_negotiation_abort_prompt_from_data",
        "generate_abort_from_data",
        (abort_data(), NEGOTIATION_ABORT_URI),
    ),
    (
        "generate_negotiation_propose_prompt_from_text",
        "generate_propose_from_text",
        (
            "text",
            NegotiationContext(SESSION_ID, 1, 5, NegotiationPerformative.PROPOSE),
            INFORMATION_NEGOTIATION_PROPOSE_URI,
        ),
    ),
    (
        "generate_negotiation_accept_prompt_from_text",
        "generate_accept_from_text",
        (
            "text",
            NegotiationContext(SESSION_ID, 2, 5, NegotiationPerformative.ACCEPT),
            INFORMATION_NEGOTIATION_ACCEPT_REJECT_URI,
        ),
    ),
    (
        "generate_negotiation_reject_prompt_from_text",
        "generate_reject_from_text",
        (
            "text",
            NegotiationContext(SESSION_ID, 2, 5, NegotiationPerformative.REJECT),
            INFORMATION_NEGOTIATION_ACCEPT_REJECT_URI,
        ),
    ),
    (
        "generate_negotiation_abort_prompt_from_text",
        "generate_abort_from_text",
        ("text", NegotiationContext(SESSION_ID, 5, 5, NegotiationPerformative.ABORT), NEGOTIATION_ABORT_URI),
    ),
    (
        "validate_propose_prompt_and_data_filling",
        "validate_propose_prompt_and_data_filling",
        (
            "prompt",
            NegotiationContext(SESSION_ID, 1, 5, NegotiationPerformative.PROPOSE),
            {"type": "object"},
            INFORMATION_NEGOTIATION_PROPOSE_URI,
        ),
    ),
    (
        "validate_accept_prompt_and_data_filling",
        "validate_accept_prompt_and_data_filling",
        (
            "prompt",
            NegotiationContext(SESSION_ID, 2, 5, NegotiationPerformative.ACCEPT),
            {"type": "object"},
            INFORMATION_NEGOTIATION_ACCEPT_REJECT_URI,
        ),
    ),
    (
        "validate_reject_prompt_and_data_filling",
        "validate_reject_prompt_and_data_filling",
        (
            "prompt",
            NegotiationContext(SESSION_ID, 2, 5, NegotiationPerformative.REJECT),
            {"type": "object"},
            INFORMATION_NEGOTIATION_ACCEPT_REJECT_URI,
        ),
    ),
    (
        "validate_abort_prompt_and_data_filling",
        "validate_abort_prompt_and_data_filling",
        (
            "prompt",
            NegotiationContext(SESSION_ID, 5, 5, NegotiationPerformative.ABORT),
            {"type": "object"},
            NEGOTIATION_ABORT_URI,
        ),
    ),
)


@pytest.mark.parametrize(("facade_method", "service_method", "args"), _DELEGATION_CASES)
def test_every_negotiation_method_delegates_to_the_content_service(
    facade_method: str, service_method: str, args: tuple[Any, ...], tmp_path: Path
) -> None:
    client = build_client(write_env(tmp_path, "zh-CN"))
    recording = RecordingContentService()
    client._negotiation_content_service = recording

    result = getattr(client, facade_method)(*args)

    assert result == f"{service_method}:ok"
    assert recording.calls == [(service_method, (*args[:-1], TemplateUri.parse(args[-1])))]


class RecordingQueryService:
    """Stand-in of the template query service recording every delegated call."""

    def __init__(self) -> None:
        self.prompt_calls: list[TemplateUri] = []
        self.prompts_calls = 0

    def get_prompts(self) -> list[PromptTemplate]:
        self.prompts_calls += 1
        return []

    def get_prompt(self, template_uri: TemplateUri) -> PromptTemplate | None:
        self.prompt_calls.append(template_uri)
        return None


def test_template_queries_delegate_to_the_query_service(tmp_path: Path) -> None:
    client = build_client(write_env(tmp_path, "zh-CN"))
    recording = RecordingQueryService()
    client._template_query_service = recording

    assert client.get_prompts() == []
    assert client.get_prompt(INFORMATION_NEGOTIATION_PROPOSE_URI) is None

    assert recording.prompts_calls == 1
    assert recording.prompt_calls == [INFORMATION_NEGOTIATION_PROPOSE]


# --------------------------------------------------------------------------------------
# template queries over the packaged tree
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize("language", LANGUAGES)
def test_get_prompts_lists_every_bundled_template_sorted_by_uri(language: str, tmp_path: Path) -> None:
    client = build_client(write_env(tmp_path, language))

    prompts = client.get_prompts()

    assert len(prompts) == 13
    uris = [template.template_uri.uri for template in prompts]
    assert uris == sorted(uris)
    assert uris[0] == "Authorization-T/authorization-policy-management/v1"
    negotiation = [
        template for template in prompts if template.template_uri.extension_name == NEGOTIATION_EXTENSION_NAME
    ]
    assert len(negotiation) == 7
    assert all(template.source == SOURCE_PACKAGED for template in prompts)
    assert all(template.content for template in prompts)
    assert all(isinstance(template, PromptTemplate) for template in prompts)


@pytest.mark.parametrize("language", LANGUAGES)
@pytest.mark.parametrize(
    "template_uri",
    (
        INFORMATION_NEGOTIATION_PROPOSE_URI,
        NEGOTIATION_ABORT_URI,
        "Task-T/network-layer/ran-energy-saving/v1",
        "Authorization-T/authorization-policy-management/v1",
    ),
)
def test_get_prompt_loads_one_bundled_template(template_uri: str, language: str, tmp_path: Path) -> None:
    client = build_client(write_env(tmp_path, language))

    template = client.get_prompt(template_uri)

    assert template is not None
    assert template.template_uri == TemplateUri.parse(template_uri)
    assert template.source == SOURCE_PACKAGED
    assert template.content
    assert template.content.startswith("## ")


@pytest.mark.parametrize(
    "unknown_uri",
    (
        "Negotiation-T/information-negotiation/propose/v9",
        "Negotiation-T/unknown-negotiation/propose/v1",
        "Negotiation-T/information-negotiation/confirm/v1",
        "Task-T/network-layer/unknown-scenario/v1",
    ),
)
def test_get_prompt_answers_none_for_unknown_templates(unknown_uri: str, tmp_path: Path) -> None:
    client = build_client(write_env(tmp_path, "zh-CN"))

    assert client.get_prompt(unknown_uri) is None


# --------------------------------------------------------------------------------------
# deprecation shim round (D1)
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("method", "build_input"),
    (
        ("start_negotiation", lambda: object()),
        ("receive_negotiation", lambda: ("message", {})),
        ("continue_negotiation", lambda: object()),
    ),
)
def test_legacy_negotiation_methods_warn_and_still_delegate(
    method: str, build_input: Callable[[], Any], tmp_path: Path
) -> None:
    client = build_client(write_env(tmp_path, "zh-CN"))
    input = build_input()
    with patch.object(client._negotiation_orchestrator, method, return_value={"legacy": True}) as delegated:
        with pytest.warns(DeprecationWarning, match=f"A2ATClient\\.{method} is deprecated since 1\\.1\\.0"):

            def call() -> Any:
                return (
                    getattr(client, method)(input)
                    if method != "receive_negotiation"
                    else client.receive_negotiation(*input)
                )

            result = call()

    assert result == {"legacy": True}
    delegated.assert_called_once()


@pytest.mark.parametrize("package_name", OLD_NEGOTIATION_PACKAGES)
def test_importing_an_old_negotiation_package_warns(package_name: str) -> None:
    module: ModuleType = importlib.import_module(package_name)

    with pytest.warns(DeprecationWarning, match=f"{package_name} package is deprecated since 1\\.1\\.0"):
        importlib.reload(module)


def test_negotiation_api_surface_is_complete() -> None:
    for method in (*NEGOTIATION_API_METHODS, "get_prompts", "get_prompt"):
        assert callable(getattr(A2ATClient, method, None)), f"A2ATClient is missing {method}"
