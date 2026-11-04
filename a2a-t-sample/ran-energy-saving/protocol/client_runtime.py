"""Client runtime assembly of the RAN energy-saving sample.

:func:`build_client_runtime` wires one run end to end:

* it builds a **per-run** A2A-T SDK ``.env`` from the sample ``.env`` (the SDK reads its
  configuration from a file, never from process environment variables) and never mutates the source
  file;
* it registers the scripted SDK provider before constructing ``A2ATClient`` in offline mode;
* it discovers the server with :func:`protocol.discovery.discover_agent` (registry by default, or
  the direct AgentCard);
* it creates the a2a-sdk client with streaming enabled for SSE and disabled for webhook, owns the
  embedded webhook receiver and its queue, and selects the configured decision provider;
* :meth:`ClientRuntime.run` executes :func:`protocol.client_flow.run_client_flow` with the optional
  live observers used by the Web UI and returns its :class:`~protocol.client_flow.ClientFlowResult`;
* :meth:`ClientRuntime.aclose` stops the receiver and closes every HTTP client and temporary file.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Final

import httpx
from a2a.client import ClientConfig, create_client
from a2a.utils.constants import TransportProtocol
from a2a_t.client.a2at_client import A2ATClient
from a2a_t.core.metadata import NegotiationContext
from dotenv import dotenv_values
from shared.agent_card import AGENT_CARD_NAME, AGENT_CARD_ORGANIZATION
from shared.config import (
    DISCOVERY_NMS,
    INTENT_MODE_PROCESSED,
    SUPPORTED_TRANSPORTS,
    TRANSPORT_SSE,
    TRANSPORT_WEBHOOK,
    SampleConfig,
)
from shared.model_factory import build_traced_adk_model
from shared.models import NegotiationProposeView
from shared.run_constants import (
    FINAL_STATE_INTENT_NOT_CONFIRMED,
    MAX_ROUNDS,
    PROTOCOL_A2A_T,
    PROTOCOL_PLAIN,
    PROTOCOL_PURE,
    SUPPORTED_PROTOCOLS,
)
from shared.sdk_env import SdkEnvFile, prepare_sdk_env
from shared.trace import (
    PHASE_TASK_PROMPT,
    TraceRecorder,
    current_trace,
    set_global_sink,
    trace_scope,
)
from shared.traced_llm import register_traced_providers
from shared.webhook_receiver import WebhookReceiver

from protocol.client_flow import (
    ClientFlowResult,
    ClientTransport,
    DecisionMadeCallback,
    SseClientTransport,
    WebhookClientTransport,
    build_webhook_receiver,
    run_client_flow,
)
from protocol.decision_provider import (
    AdkDecisionProvider,
    ConfirmingDecisionProvider,
    ManualDecisionProvider,
    NegotiationDecisionProvider,
    ScriptedDecisionProvider,
)
from protocol.discovery import DiscoveredAgent, discover_agent
from protocol.intent import (
    IntentProcessor,
    OperatorRequest,
    ProcessedIntent,
    RawIntentProcessor,
)
from protocol.nms_client import NmsClient, NmsConfig, NmsSseTransport
from protocol.plain_client_flow import run_plain_client_flow
from protocol.pure_client_flow import run_pure_client_flow
from protocol.run_spec import DEFAULT_CLIENT_POLICY, ClientPolicy, RunCatalog, RunSpec
from protocol.transcript import DEFAULT_RUNS_DIR

_LOGGER = logging.getLogger("ran_energy_saving.client_runtime")

#: Client decision modes.
MODE_AUTO: Final[str] = "auto"
MODE_MANUAL: Final[str] = "manual"
MODE_SCRIPTED: Final[str] = "scripted"
MODE_CONFIRM: Final[str] = "confirm"
SUPPORTED_MODES: Final[tuple[str, ...]] = (MODE_AUTO, MODE_MANUAL, MODE_SCRIPTED, MODE_CONFIRM)

#: Discovery key of the direct server base URL.
DIRECT_SERVER_URL_ENV_KEY: Final[str] = "A2AT_SAMPLE_DIRECT_SERVER_URL"

#: Prefix of the temporary SDK ``.env`` directories.
_SDK_ENV_PREFIX: Final[str] = "a2at-ran-client-env-"


class ClientRuntimeError(RuntimeError):
    """Raised when the client runtime cannot be assembled."""


def _env_values(env_path: Path | None) -> dict[str, str]:
    """Read the source ``.env`` values as plain strings (empty when the file is missing)."""
    if env_path is None or not env_path.exists():
        return {}
    return {
        str(key): str(value) for key, value in dotenv_values(env_path).items() if key is not None and value is not None
    }


def prepare_client_sdk_env(
    config: SampleConfig,
    env_path: Path | None = None,
) -> SdkEnvFile:
    """Build a per-run SDK ``.env`` without mutating the caller's file.

    The builder lives in :mod:`shared.sdk_env` so the server runtime can use it without importing
    this module; this wrapper keeps the historical client entry point and its directory prefix.

    Args:
        config: resolved sample configuration.
        env_path: source ``.env``; defaults to ``$CWD/.env``.

    Returns:
        The generated file and the temporary directory owning it.
    """
    return prepare_sdk_env(config, env_path, prefix=_SDK_ENV_PREFIX)


def _direct_base_url(config: SampleConfig, env_path: Path | None) -> str:
    """Return the direct-discovery server URL (configured value or the sample bind address)."""
    configured = _env_values(env_path).get(DIRECT_SERVER_URL_ENV_KEY, "").strip()
    return configured or f"http://{config.host}:{config.port}"


def _server_auth_headers(config: SampleConfig) -> dict[str, str]:
    """Return the bearer header of the client-to-server A2A calls, empty when open."""
    token = config.server_auth_token.strip()
    return {"Authorization": f"Bearer {token}"} if token else {}


def _effective_policy(
    policy: ClientPolicy,
    config: SampleConfig,
    request_text: str,
    language: str,
) -> ClientPolicy:
    """Return the policy a run uses.

    A real NMS run honors the operator's request text (region, window and throughput), so the
    scenario's canned ``initial_data`` cannot override it. When ``A2AT_SAMPLE_NMS_REGION`` is set
    and the request text does not already name that region, the scenario's structured values are
    kept and only the region is replaced: the vendor rejects the demo region, and the scenario
    text is already a complete request. An explicit request that names the configured region wins,
    so an operator can still choose windows or rates. Other modes keep the scenario policy
    unchanged.
    """
    if config.discovery != DISCOVERY_NMS:
        return policy
    region = config.nms_region.strip()
    if region:
        base_filled = policy.filled_params

        def filled_params(
            kind: str,
            language_code: str,
            *,
            base: Callable[[str, str], dict[str, object]] = base_filled,
            region_value: str = region,
        ) -> dict[str, object]:
            params = dict(base(kind, language_code))
            params["region"] = region_value
            return params

        policy = replace(policy, filled_params=filled_params)
        if region.lower() not in request_text.lower():
            initial = policy.initial_data(language)
            if initial is not None:
                values, schema = initial
                overridden = {**values, "region": region}
                return replace(
                    policy,
                    initial_data=lambda _language, values=overridden, schema=schema: (
                        values,
                        schema,
                    ),
                )
    if request_text.strip():
        return replace(policy, initial_data=DEFAULT_CLIENT_POLICY.initial_data)
    return policy


def _emit_intent_span(
    *,
    trace_sink: Callable[[dict[str, object]], None] | None,
    intent: ProcessedIntent,
    mode: str,
    scenario: str,
) -> None:
    """Publish the business intent as one Run Explorer trace row."""
    if trace_sink is None:
        return
    recorder = TraceRecorder(current_trace().run_id or "", id_prefix="intent")
    span = recorder.emit(
        kind="intent_processed",
        phase=PHASE_TASK_PROMPT,
        title="Intent processed",
        actor="client",
        side="client",
        direction="internal",
        detail={
            "mode": mode,
            "scenario": scenario,
            "source": intent.source,
            "rawRequest": intent.raw_request,
            "normalizedText": intent.normalized_text,
            "intent": intent.to_dict(),
            "missingFields": list(intent.missing_fields),
            "conflicts": list(intent.conflicts),
            "warnings": list(intent.warnings),
        },
    )
    trace_sink(span.to_dict())


def _decision_provider(
    config: SampleConfig,
    run_spec: RunSpec,
    mode: str,
    *,
    model: Any | None = None,
    client_agent_factory: Callable[[Any], Any] | None = None,
) -> NegotiationDecisionProvider:
    """Build the decision provider of one client mode.

    The offline model is supplied by the composition root; a scripted provider replays the run
    spec's decisions. In offline ``auto`` mode the model serves the same scripted decisions, so the
    ADK runner stays in the loop and still produces counter/reject/abort decisions.

    Raises:
        ClientRuntimeError: when the mode is not supported.
    """
    if mode == MODE_MANUAL:
        return ManualDecisionProvider()
    if mode == MODE_SCRIPTED:
        return ScriptedDecisionProvider(run_spec.scripted_decisions)
    if mode in (MODE_AUTO, MODE_CONFIRM):
        # The traced wrapper keeps the ADK decision turns visible in the Run Explorer. The original
        # request is carried into every decision prompt: target/feasibility proposals describe the
        # conflict but not the concrete throughput target, so the model cannot resolve them alone.
        provider = AdkDecisionProvider(
            model=build_traced_adk_model(config, model=model),
            # The demo supplies a scripted decision model whose script covers the run's expected
            # proposals. An unexpected proposal (for example a live validator that opens the wrong
            # negotiation kind) has no scripted answer; falling back to accept keeps the run alive
            # instead of failing it with an unparseable decision.
            offline=config.is_offline or model is not None,
            user_request=run_spec.user_request,
            session_db_path=config.client_db_path,
            agent_factory=client_agent_factory,
            default_region=(config.nms_region if config.discovery == DISCOVERY_NMS else None),
        )
        if mode == MODE_CONFIRM:
            return ConfirmingDecisionProvider(provider)
        return provider
    raise ClientRuntimeError(f"Unsupported client mode: {mode}; expected one of {SUPPORTED_MODES}")


@dataclass
class ClientRuntime:
    """Assembled client runtime of one run.

    Attributes:
        config: resolved sample configuration.
        run_spec: the named run being executed (catalog entry).
        mode: decision mode (``auto`` / ``manual`` / ``scripted``).
        protocol: interaction protocol (``a2a-t`` / ``plain`` / ``pure``).
        transport_name: event transport (``sse`` / ``webhook``).
        prompt_client: A2A-T SDK prompt facade; ``None`` in pure mode.
        a2a_client: a2a-sdk client used by the transports; ``None`` in NMS mode.
        transport: normalized-event transport.
        provider: negotiation decision provider; ``None`` in pure mode.
        discovered: discovered server endpoint.
        sdk_env: generated SDK ``.env`` owned by this runtime; ``None`` in pure mode.
        manual_provider: manual provider when ``mode == "manual"``, else ``None``.
        nms_client: raw vendor NMS client; ``None`` outside NMS mode.
        pure_answer: callback answering one pure ``INPUT_REQUIRED`` event.
        intent_processor: business intent understanding step; ``None`` falls back to raw text.
    """

    config: SampleConfig
    run_spec: RunSpec
    mode: str
    protocol: str
    transport_name: str
    prompt_client: A2ATClient | None
    a2a_client: Any | None
    transport: ClientTransport
    provider: NegotiationDecisionProvider | None
    discovered: DiscoveredAgent
    sdk_env: SdkEnvFile | None
    manual_provider: Any | None
    _httpx_client: httpx.AsyncClient | None
    nms_client: NmsClient | None = None
    _webhook_receiver: WebhookReceiver | None = None
    _closed: bool = False
    trace_sink: Callable[[dict[str, object]], None] | None = None
    pure_answer: Callable[[dict[str, object]], str] | None = None
    intent_processor: IntentProcessor | None = None

    def set_recommendation_sink(
        self,
        sink: Callable[[Any, Any, Any], None] | None,
    ) -> None:
        """Install the confirmation recommendation sink when the provider supports one.

        Args:
            sink: callback receiving ``(decision, propose, session)`` before the human wait.
        """
        setter = getattr(self.provider, "set_recommendation_sink", None)
        if callable(setter):
            setter(sink)

    def _scenario_raw_request(self) -> str:
        """Return the operator-facing request of the scenario, when one is defined."""
        if self.run_spec.intent is not None:
            return self.run_spec.intent.raw_request
        return self.run_spec.user_request

    def _raw_mode_request(self, custom_request: str) -> str:
        """Return the request used by the raw (no intent processing) mode."""
        return custom_request or self.run_spec.user_request

    async def _process_intent(self, raw_text: str, *, custom: bool) -> ProcessedIntent:
        """Run the business intent layer, falling back to the scenario intent or raw text."""
        processor = self.intent_processor
        if processor is not None:
            return await processor.process(
                OperatorRequest(
                    text=raw_text,
                    language=self.config.language,
                    scenario=self.run_spec.name,
                    source="client",
                    custom=custom,
                )
            )
        if not custom and self.run_spec.intent is not None:
            return self.run_spec.intent
        return await RawIntentProcessor().process(
            OperatorRequest(
                text=raw_text,
                language=self.config.language,
                scenario=self.run_spec.name,
                source="client",
                custom=custom,
            )
        )

    async def run(
        self,
        *,
        event_sink: Callable[[dict[str, object]], None] | None = None,
        on_decision_requested: Callable[[NegotiationProposeView, NegotiationContext], None] | None = None,
        on_decision_made: DecisionMadeCallback | None = None,
        on_intent_ready: Callable[[str], Awaitable[bool]] | None = None,
        trace_sink: Callable[[dict[str, object]], None] | None = None,
        run_id: str | None = None,
        user_request: str | None = None,
        conversation_context: str | None = None,
    ) -> ClientFlowResult:
        """Run the full client flow of the configured scenario.

        The optional trace sink is attached to the process-wide producer hook for the duration of
        the run, and the run id is put into the trace context so the client spans, the LLM/tool
        spans and the ``sampleRunId`` message metadata all agree.

        Args:
            event_sink: Optional live observer of the normalized events (Web UI timeline).
            on_decision_requested: Optional observer invoked before asking the provider.
            on_decision_made: Optional observer invoked immediately after the provider returns,
                before the response is sent.
            on_intent_ready: Optional async approval gate invoked before the Task-T is sent.
            trace_sink: Optional observer of synthetic Run Explorer spans (LLM/tool producers
                included); defaults to the sink the runtime was built with.
            run_id: Optional Run Explorer run id (the Web UI and the CLI pass theirs).
            user_request: Optional natural-language request entered in the Web UI; ``None`` uses
                the scenario's canned request.
            conversation_context: Optional recent-turn block injected into the decision prompt
                and Task-T generation so a follow-up can refer to an earlier run.

        Returns:
            The collected events, decisions and terminal state.
        """
        if self._closed:
            raise ClientRuntimeError("The client runtime is already closed.")
        if self.nms_client is not None:
            self.nms_client.set_wire_path(DEFAULT_RUNS_DIR / run_id / "wire.jsonl" if run_id else None)
        sink = trace_sink if trace_sink is not None else self.trace_sink
        # A run-level sink is restored to the construction-time sink after the run; the
        # construction-time sink stays attached until ``aclose``.
        temporary_sink = trace_sink is not None and trace_sink is not self.trace_sink
        if sink is not None:
            set_global_sink(sink)
        try:
            with trace_scope(run_id=run_id, role="client"):
                custom_request = (
                    user_request.strip()
                    if isinstance(user_request, str) and user_request.strip()
                    else ""
                )
                processed = self.config.intent_mode == INTENT_MODE_PROCESSED
                raw_text = custom_request or self._scenario_raw_request()
                if self.protocol == PROTOCOL_PURE:
                    # Pure A2A uses standard Task states only: the OSS Agent owns the business
                    # intent layer, so the engineer client sends the raw request to it.
                    return await run_pure_client_flow(
                        transport=self.transport,
                        user_request=raw_text if processed else self._raw_mode_request(custom_request),
                        answer=self.pure_answer,
                        max_rounds=MAX_ROUNDS,
                        call_timeout_seconds=self.config.http_timeout_seconds,
                        event_sink=event_sink,
                        trace_sink=sink,
                        run_id=run_id,
                    )
                intent: ProcessedIntent | None = None
                if processed:
                    intent = await self._process_intent(raw_text, custom=bool(custom_request))
                    request_text = intent.normalized_text
                    _emit_intent_span(
                        trace_sink=sink,
                        intent=intent,
                        mode=self.config.intent_mode,
                        scenario=self.run_spec.name,
                    )
                else:
                    request_text = self._raw_mode_request(custom_request)
                if self.protocol == PROTOCOL_PLAIN:
                    # Plain A2A has no Task-T, no intent gate and no negotiation: one message in,
                    # task events out.
                    return await run_plain_client_flow(
                        transport=self.transport,
                        user_request=request_text,
                        call_timeout_seconds=self.config.http_timeout_seconds,
                        event_sink=event_sink,
                        trace_sink=sink,
                    )
                set_context = getattr(self.provider, "set_conversation_context", None)
                if callable(set_context):
                    set_context(conversation_context)
                set_request = getattr(self.provider, "set_user_request", None)
                if callable(set_request):
                    set_request(request_text)
                policy = _effective_policy(
                    self.run_spec.policy,
                    self.config,
                    request_text,
                    self.config.language,
                )
                if processed:
                    # The intent layer already contains every business value. Task-T must now be
                    # extracted from the normalized text, not rendered from a structured shortcut.
                    policy = replace(policy, initial_data=DEFAULT_CLIENT_POLICY.initial_data)
                flow_on_intent_ready = on_intent_ready
                if processed and on_intent_ready is not None:
                    # Approval 1 belongs to the business intent, before the protocol mapping.
                    if not await on_intent_ready(request_text):
                        blocked = ClientFlowResult()
                        blocked.final_state = FINAL_STATE_INTENT_NOT_CONFIRMED
                        return blocked
                    flow_on_intent_ready = None
                return await run_client_flow(
                    prompt_client=self.prompt_client,
                    transport=self.transport,
                    user_request=request_text,
                    provider=self.provider,
                    language=self.config.language,
                    policy=policy,
                    event_sink=event_sink,
                    on_decision_requested=on_decision_requested,
                    on_decision_made=on_decision_made,
                    on_intent_ready=flow_on_intent_ready,
                    trace_sink=sink,
                    call_timeout_seconds=self.config.http_timeout_seconds,
                    conversation_context=conversation_context,
                    single_extension_per_message=(self.config.discovery == DISCOVERY_NMS),
                    forced_region=(self.config.nms_region if self.config.discovery == DISCOVERY_NMS else None),
                )
        finally:
            if temporary_sink:
                set_global_sink(self.trace_sink)

    async def aclose(self) -> None:
        """Stop the webhook receiver, close the HTTP clients and remove the temporary SDK env."""
        if self._closed:
            return
        self._closed = True
        if self._webhook_receiver is not None:
            await self._webhook_receiver.stop()
        close_client = getattr(self.a2a_client, "close", None)
        if callable(close_client):
            await close_client()
        if self.nms_client is not None:
            await self.nms_client.aclose()
        if self._httpx_client is not None:
            await self._httpx_client.aclose()
        if self.trace_sink is not None:
            set_global_sink(None)
        if self.sdk_env is not None:
            shutil.rmtree(self.sdk_env.directory, ignore_errors=True)


async def build_client_runtime(
    config: SampleConfig,
    scenario: str,
    mode: str = MODE_AUTO,
    transport: str = TRANSPORT_SSE,
    *,
    protocol: str = PROTOCOL_A2A_T,
    catalog: RunCatalog,
    env_path: Path | None = None,
    trace_sink: Callable[[dict[str, object]], None] | None = None,
    offline_model_factory: Callable[[RunSpec], Any] | None = None,
    install_offline_provider: Callable[[], None] | None = None,
    client_agent_factory: Callable[[Any], Any] | None = None,
    pure_answer: Callable[[dict[str, object]], str] | None = None,
    intent_processor_factory: Callable[[SampleConfig, RunSpec], IntentProcessor] | None = None,
) -> ClientRuntime:
    """Assemble the client runtime of one run.

    Args:
        config: resolved sample configuration (language and model mode are process-level).
        scenario: run name resolved through ``catalog``.
        mode: decision mode (``auto`` / ``manual`` / ``scripted``).
        transport: event transport (``sse`` / ``webhook``).
        protocol: interaction protocol (``a2a-t`` / ``plain`` / ``pure``).
        catalog: run catalog supplied by the composition root.
        env_path: source ``.env`` used for discovery and for the per-run SDK ``.env``.
        trace_sink: optional Run Explorer sink attached **before** the SDK and ADK clients are
            built, so their LLM/tool producers are traced from the first call.
        offline_model_factory: builder of the deterministic ADK model; required in offline mode.
        install_offline_provider: optional installer of the deterministic SDK LLM routing.
        client_agent_factory: optional builder of the ADK decision agent from its model.
        pure_answer: callback answering one pure ``INPUT_REQUIRED`` event. Pure mode skips the
            A2A-T SDK facade and the negotiation decision provider entirely.
        intent_processor_factory: optional builder of the business intent layer; when omitted,
            processed mode falls back to the run's built-in intent or raw text.

    Returns:
        The assembled runtime; the caller owns it and must call :meth:`ClientRuntime.aclose`.

    Raises:
        ClientRuntimeError: on an unsupported mode/transport, a missing offline factory, or when
            the runtime cannot assemble.
        KeyError: when the run name is unknown to the catalog.
        DiscoveryError: when no usable server endpoint is found.
    """
    if mode not in SUPPORTED_MODES:
        raise ClientRuntimeError(f"Unsupported client mode: {mode}; expected one of {SUPPORTED_MODES}")
    if transport not in SUPPORTED_TRANSPORTS:
        raise ClientRuntimeError(f"Unsupported transport: {transport}; expected one of {SUPPORTED_TRANSPORTS}")
    if protocol not in SUPPORTED_PROTOCOLS:
        raise ClientRuntimeError(f"Unsupported protocol: {protocol}; expected one of {SUPPORTED_PROTOCOLS}")

    run_spec = catalog.spec(scenario, language=config.language)
    intent_processor = (
        intent_processor_factory(config, run_spec)
        if intent_processor_factory is not None
        else None
    )
    register_traced_providers()
    if config.is_offline:
        if offline_model_factory is None:
            raise ClientRuntimeError(
                "Offline mode needs a deterministic model factory; configure live mode or pass "
                "offline_model_factory from the composition root."
            )
        if install_offline_provider is not None:
            install_offline_provider()
    if trace_sink is not None:
        set_global_sink(trace_sink)
    if protocol == PROTOCOL_PURE:
        # A pure A2A client does not need the A2A-T SDK facade or its generated .env.
        sdk_env: SdkEnvFile | None = None
        prompt_client: A2ATClient | None = None
    else:
        sdk_env = prepare_client_sdk_env(config, env_path)
        prompt_client = A2ATClient(env_path=sdk_env.path, logger=None)

    discovered = await discover_agent(
        discovery=config.discovery,
        organization=AGENT_CARD_ORGANIZATION,
        name=AGENT_CARD_NAME,
        registry_center_url=config.registry_url,
        direct_base_url=_direct_base_url(config, env_path),
        nms_a2a_base=config.nms_a2a_base,
        env_path=env_path,
    )
    _LOGGER.info("agent discovered: url=%s discovery=%s", discovered.url, config.discovery)

    nms_client: NmsClient | None = None
    a2a_client: Any | None = None
    httpx_client: httpx.AsyncClient | None = None
    receiver: WebhookReceiver | None = None
    if config.discovery == DISCOVERY_NMS:
        # The vendor NMS has no AgentCard and no a2a-sdk-compatible transport: the raw client owns
        # authentication, the never-closing SSE stream and task polling.
        nms_client = NmsClient(NmsConfig.from_config(config))
        flow_transport: ClientTransport = NmsSseTransport(
            nms_client,
            protocol=protocol,
            request_timeout=config.nms_request_timeout,
        )
    else:
        # One budget for both the transport and the per-call context (the SDK applies the context
        # timeout to every request, overriding the httpx client default).
        httpx_client = httpx.AsyncClient(
            timeout=config.http_timeout_seconds,
            trust_env=False,
            headers=_server_auth_headers(config),
        )
        client_config = ClientConfig(
            streaming=transport == TRANSPORT_SSE,
            httpx_client=httpx_client,
            supported_protocol_bindings=[TransportProtocol.HTTP_JSON],
            use_client_preference=True,
        )
        try:
            a2a_client = await create_client(discovered.agent_card, client_config=client_config)
            if transport == TRANSPORT_WEBHOOK:
                queue: asyncio.Queue[dict[str, object]] = asyncio.Queue()
                receiver = build_webhook_receiver(
                    token=config.webhook_token,
                    queue=queue,
                    port=config.webhook_port,
                )
                await receiver.start()
                _LOGGER.info("webhook mode: push_url=%s", receiver.url)
                flow_transport = WebhookClientTransport(
                    a2a_client,
                    queue=queue,
                    push_url=receiver.url,
                    push_token=config.webhook_token,
                    idle_timeout=float(config.webhook_idle_timeout),
                )
            else:
                flow_transport = SseClientTransport(a2a_client)
        except Exception:
            await httpx_client.aclose()
            if sdk_env is not None:
                shutil.rmtree(sdk_env.directory, ignore_errors=True)
            raise

    if protocol == PROTOCOL_PURE:
        provider: NegotiationDecisionProvider | None = None
    else:
        offline_model = offline_model_factory(run_spec) if offline_model_factory is not None else None
        provider = _decision_provider(
            config,
            run_spec,
            mode,
            model=offline_model,
            client_agent_factory=client_agent_factory,
        )
    return ClientRuntime(
        config=config,
        run_spec=run_spec,
        mode=mode,
        protocol=protocol,
        transport_name=transport,
        prompt_client=prompt_client,
        a2a_client=a2a_client,
        transport=flow_transport,
        provider=provider,
        discovered=discovered,
        sdk_env=sdk_env,
        manual_provider=(
            provider
            if protocol == PROTOCOL_A2A_T and isinstance(provider, (ManualDecisionProvider, ConfirmingDecisionProvider))
            else None
        ),
        _httpx_client=httpx_client,
        nms_client=nms_client,
        _webhook_receiver=receiver,
        trace_sink=trace_sink,
        pure_answer=pure_answer,
        intent_processor=intent_processor,
    )
