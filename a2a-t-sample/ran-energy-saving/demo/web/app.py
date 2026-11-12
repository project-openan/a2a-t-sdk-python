"""Lightweight Web UI of the RAN energy-saving client (design §10).

The Starlette app observes and steers the client flow, it never re-implements protocol logic:

* ``GET /`` serves the page selected by ``ui_mode`` (``presentation`` for an audience,
  ``explorer`` for protocol-level debugging) and ``/static`` its assets;
* ``POST /api/runs`` starts one background client run (one concurrent run, a second start is 409);
* ``GET /api/runs/{id}/events`` streams the normalized events, ``decision_requested`` prompts and
  the ``finished`` outcome as Server-Sent Events;
* ``POST /api/runs/{id}/decisions`` submits a :class:`~shared.models.NegotiationDecision` to the
  manual provider (404 for an unknown run, 409 when no decision is pending);
* ``GET /api/runs/{id}`` returns the status, result and transcript;
* ``POST /api/runs/{id}/cancel`` cancels the running Task (presentation UI), best effort on the
  remote side and always on the local run;
* ``POST /api/runs/{id}/trace`` ingests the cross-process server spans (local token, envelope
  validation, broadcast; re-sent producer spans are deduplicated by original ``spanId``);
* ``GET /api/runs/{id}/transcript.json`` / ``.md`` download the run transcript.

The language is process-level and read-only: it comes from the sample configuration and is exposed
through ``GET /api/meta`` (design D18).
"""

from __future__ import annotations

import asyncio
import csv
import io
import json
import logging
import os
import re
import ssl
import time
from collections.abc import AsyncGenerator, AsyncIterator, Mapping, Sequence
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx
from a2a_t.core.metadata import NegotiationContext, NegotiationPerformative
from protocol.approval_gate import DEFAULT_APPROVAL_TIMEOUT_SECONDS
from protocol.client_runtime import MODE_AUTO
from protocol.client_service import (
    STATUS_RUNNING,
    UI_MODES,
    ClientRuntimeLike,
    RunConflictError,
    RunManager,
    RunState,
    RuntimeFactory,
)
from protocol.client_store import (
    ClientStore,
    read_client_run_detail,
    read_client_run_summaries,
    read_conversation_detail,
    read_conversation_summaries,
    read_run_metrics,
    read_run_metrics_rows,
)
from protocol.manual_answer import missing_item_labels, ungrounded_answer_fields
from protocol.metrics_summary import summarize_metrics, summarize_protocols
from protocol.nms_client import NmsConfigError, NmsError
from protocol.run_spec import RunCatalog
from protocol.sqlite_task_store import read_task_summaries
from shared.config import (
    DISCOVERY_DIRECT,
    DISCOVERY_NMS,
    DISCOVERY_REGISTRY,
    PURE_HOP_NMS,
    SUPPORTED_DISCOVERY_MODES,
    SUPPORTED_INTENT_MODES,
    SUPPORTED_TRANSPORTS,
    TRANSPORT_SSE,
    TRANSPORT_WEBHOOK,
    SampleConfig,
)
from shared.http_security import (
    UI_TOKEN_COOKIE,
    BearerAuthMiddleware,
    install_health_routes,
    install_metrics_route,
    sqlite_ready,
    validate_bind_security,
)
from shared.models import NegotiationDecision
from shared.run_constants import (
    BRAND_ORANGE,
    DEMO_PROTOCOLS,
    PROTOCOL_A2A_T,
    PROTOCOL_PLAIN,
    PROTOCOL_PURE,
    SUPPORTED_BRANDS,
    SUPPORTED_PROTOCOLS,
)
from shared.run_source import normalize_run_source
from shared.trace import (
    PHASE_TASK_MODIFY,
    PHASES,
    SCHEMA_VERSION,
    set_global_sink,
)
from shared.trace_propagation import TracePropagationMiddleware
from sse_starlette.sse import EventSourceResponse
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.requests import Request
from starlette.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    Response,
    StreamingResponse,
)
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from demo.scenario import (
    PLAIN_PROTOCOL_SCENARIOS,
    PURE_PROTOCOL_SCENARIOS,
    resolve_example_request,
    resolve_modify_request,
)
from demo.web.nms_tasks import (
    ACTIVE_STATES,
    MODIFIABLE_STATES,
    NMS_SUBMITTED_STATE,
    NMS_WORKING_STATE,
    cancel_nms_task,
    list_active_tasks,
    list_modifiable_tasks,
    read_task,
)
from demo.web.observe.routes import build_observe_routes
from demo.web.runs_console import build_run_console_routes
from demo.web.tester import build_tester_routes
from demo.web.wire_records import read_wire_records

_LOGGER = logging.getLogger("ran_energy_saving.web")

#: Trace ingest path; it is exempt from the user bearer and authenticated by the trace token.
TRACE_INGEST_PATH_PATTERN: Final[re.Pattern[str]] = re.compile(r"/api/runs/[^/]+/trace")

#: Static directory of the Run Explorer page.
STATIC_DIR: Final[Path] = Path(__file__).resolve().parent / "static"

#: Static directory of the presentation page served to an audience.
PRESENTATION_DIR: Final[Path] = Path(__file__).resolve().parent / "presentation"

#: Static directory of the protocol comparison (`/comparison`) page.
COMPARISON_DIR: Final[Path] = Path(__file__).resolve().parent / "comparison"

#: Web UI variants: the audience-facing presentation and the technical Run Explorer.
WEB_UI_PRESENTATION: Final[str] = "presentation"
WEB_UI_EXPLORER: Final[str] = "explorer"
WEB_UI_MODES: Final[tuple[str, ...]] = (WEB_UI_PRESENTATION, WEB_UI_EXPLORER)

#: Seconds an SSE stream stays open after ``finished`` so late cross-process server spans arrive.
#:
#: The server trace emitter batches spans with a 0.5 s flush interval, so its last batch is posted
#: just after the client finishes. Closing the stream on ``finished`` would hide every server LLM
#: and tool span of a fresh run, so the handler forwards those frames during a short grace window.
FINISHED_GRACE_SECONDS: Final[float] = 2.0


def _resolve_default(
    value: str | None,
    allowed: tuple[str, ...],
    fallback: str,
    label: str,
) -> str:
    """Resolve one Web-UI selection default, rejecting an invalid explicit value.

    Raises:
        ValueError: when an explicit default is not one of ``allowed``.
    """
    if value is None:
        return fallback
    if value not in allowed:
        raise ValueError(f"Unsupported default {label}: {value}; expected one of {allowed}")
    return value


#: Seconds one forwarded approval request may take before the page is told the result is unknown.
APPROVAL_FORWARD_TIMEOUT_SECONDS: Final[float] = 10.0


def _approval_forward_client(*, verify_tls: bool = True) -> httpx.AsyncClient:
    """Build the HTTP client that forwards one local approval decision."""
    return httpx.AsyncClient(trust_env=False, timeout=APPROVAL_FORWARD_TIMEOUT_SECONDS, verify=verify_tls)


def _targets_the_nms(server_url: str | None, config: SampleConfig) -> bool:
    """Whether a configured server URL is the vendor NMS rather than a local A2A server."""
    if not server_url:
        return False
    base = (config.nms_a2a_base or "").rstrip("/")
    return bool(base) and server_url.rstrip("/") == base


def _is_tls_failure(exc: BaseException) -> bool:
    """Whether an HTTP error was caused by certificate verification (walk the cause chain)."""
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, ssl.SSLError):
            return True
        current = current.__cause__ or current.__context__
    return False


async def forward_approval_decision(
    server_url: str,
    *,
    run_id: str,
    approved: bool,
    headers: Mapping[str, str],
    verify_tls: bool,
    timeout: float = APPROVAL_FORWARD_TIMEOUT_SECONDS,
) -> tuple[int, dict[str, object]]:
    """Forward one approval decision and classify every failure instead of "unreachable".

    Returns:
        ``(status_code, body)`` for the page. Only a 2xx from the target is an acceptance: a
        redirect means the POST was not applied, so it must not read as success.
    """
    try:
        async with httpx.AsyncClient(trust_env=False, timeout=timeout, verify=verify_tls) as client:
            forwarded = await client.post(
                f"{server_url.rstrip('/')}/approvals",
                json={"runId": run_id, "approved": approved},
                headers=dict(headers),
            )
    except httpx.TimeoutException as exc:
        _LOGGER.warning("approval forward timed out: %s", exc)
        return 504, {
            "error": "The approval request timed out; the outcome is unknown. Refresh the run status.",
            "code": "approval_timeout",
        }
    except httpx.InvalidURL as exc:
        _LOGGER.warning("approval forward target is invalid: %s", exc)
        return 500, {
            "error": "The configured approval target is not a valid URL.",
            "code": "approval_target_invalid",
        }
    except httpx.HTTPError as exc:
        _LOGGER.warning("approval forward failed: %s", exc)
        if _is_tls_failure(exc):
            return 502, {
                "error": f"The approval target's TLS certificate was not accepted: {exc}",
                "code": "approval_tls_error",
            }
        return 502, {
            "error": f"The approval target could not be reached: {exc}",
            "code": "approval_connect_failed",
        }
    payload = await _forward_json_or_empty(forwarded)
    if 300 <= forwarded.status_code < 400:
        return 502, {
            "error": "The approval target redirected the request; nothing was applied.",
            "code": "approval_target_rejected",
        }
    if forwarded.status_code == 404:
        return 404, {
            "error": "The approval target has no /approvals route.",
            "code": "approval_target_not_found",
        }
    if forwarded.status_code >= 400:
        code = payload.get("code")
        if not isinstance(code, str) or not code:
            payload["code"] = "approval_target_rejected"
        return forwarded.status_code, payload
    return 200, {"accepted": True, "runId": run_id, "approved": approved}


async def _forward_json_or_empty(response: httpx.Response) -> dict[str, object]:
    """Return one forwarded JSON object, or an empty mapping."""
    try:
        value = response.json()
    except ValueError:
        return {}
    return dict(value) if isinstance(value, dict) else {}


async def _pure_run_mapping(client_store: ClientStore | None) -> dict[str, tuple[str, str]]:
    """Map each observed vendor Task id to the pure-A2A outer Task that owns it.

    The newest run wins: a Task can be continued by more than one run, and the latest attempt holds
    the outer Task the page should address.
    """
    if client_store is None:
        return {}
    try:
        runs = await client_store.list_runs(limit=500)
    except Exception:  # noqa: BLE001 - the picker must still show the NMS list
        _LOGGER.warning("could not read pure runs for the task picker", exc_info=True)
        return {}
    mapping: dict[str, tuple[str, str]] = {}
    for run in runs:
        # A direct standard-A2A run addresses the vendor Task itself; it has no outer Task to map.
        if (
            run.protocol != PROTOCOL_PURE
            or run.discovery == DISCOVERY_NMS
            or not run.downstream_task_id
            or not run.server_task_id
        ):
            continue
        mapping.setdefault(
            run.downstream_task_id,
            (run.server_task_id, run.server_context_id or ""),
        )
    return mapping


async def _annotate_server_tasks(
    client_store: ClientStore | None,
    rows: list[dict[str, object]],
) -> None:
    """Add the pure-A2A outer Task ids to the picker rows.

    In pure mode a picker row names the OSS Agent's downstream Task. The A2A peer the page may
    continue is the OSS outer Task, and every pure run that observed the downstream already
    recorded both identities in the client store. Joining them here keeps the vendor task id (used
    for the NMS state checks) next to the id the A2A message must carry.
    """
    if client_store is None or not rows:
        return
    mapping = await _pure_run_mapping(client_store)
    for row in rows:
        pair = mapping.get(str(row.get("taskId") or ""))
        if pair is not None:
            row["serverTaskId"], row["serverContextId"] = pair


def create_app(
    config: SampleConfig,
    *,
    runs_dir: Path | None = None,
    runtime_factory: RuntimeFactory | None = None,
    catalog: RunCatalog | None = None,
    env_path: Path | None = None,
    client_store: ClientStore | None = None,
    memory_turns: int = 6,
    default_scenario: str | None = None,
    default_mode: str | None = None,
    default_transport: str | None = None,
    default_discovery: str | None = None,
    finished_grace_seconds: float = FINISHED_GRACE_SECONDS,
    metrics_grace_seconds: float = 0.0,
    bind_host: str = "127.0.0.1",
    ui_mode: str = WEB_UI_PRESENTATION,
    default_brand: str = BRAND_ORANGE,
    server_url: str | None = None,
    approval_gate: bool = False,
    approval_timeout_seconds: float = DEFAULT_APPROVAL_TIMEOUT_SECONDS,
    allow_insecure_bind: bool = False,
    intent_review: bool | None = None,
    protocol: str = PROTOCOL_A2A_T,
    protocols: tuple[str, ...] | None = None,
    nms_enabled: bool | None = None,
    mie_url: str | None = None,
    tester_prompt_client: Any | None = None,
) -> Starlette:
    """Build the Starlette application of the Web UI.

    Args:
        config: process-level sample configuration (language and model mode are read-only).
        runs_dir: transcript directory; defaults to the sample ``runs/`` directory.
        runtime_factory: optional runtime builder (tests inject a deterministic double).
        catalog: run catalog of the deployment; defaults to the demo scenarios.
        env_path: source ``.env`` handed to the real client runtime.
        client_store: optional client persistence store (conversations, runs, negotiations).
        memory_turns: number of recent turns injected into the next run's prompts.
        default_scenario: scenario preselected by the page and used when a start body omits it;
            defaults to the first supported scenario.
        default_mode: decision mode preselected/used by default; defaults to ``auto``.
        default_transport: transport preselected/used by default; defaults to the process-level
            ``A2AT_SAMPLE_TRANSPORT`` value.
        default_discovery: discovery preselected/used by default; defaults to the process-level
            ``A2AT_SAMPLE_DISCOVERY`` value.
        finished_grace_seconds: seconds an event stream stays open after ``finished`` to forward
            the server spans that a cross-process run posts just after completion; ``0`` closes
            immediately (tests that do not exercise the race).
        metrics_grace_seconds: seconds a finished run waits before its per-run metrics are
            persisted, so the last batched server spans are included; ``0`` persists immediately
            (tests and callers that query live metrics only).
        bind_host: address the caller will bind; a non-loopback bind needs an auth token.
        ui_mode: ``presentation`` (audience-facing page) or ``explorer`` (technical Run
            Explorer); both serve the same API and event stream.
        default_brand: presentation color theme (``orange`` or ``huawei``); the page can switch it
            at runtime and remembers the choice in local storage.
        server_url: base URL of the A2A server, used by the presentation's approval route.
        approval_gate: whether the server pauses before applying a plan.
        approval_timeout_seconds: approval window reported to the page.
        allow_insecure_bind: explicit opt-in for a trusted-LAN bind without ``A2AT_SAMPLE_AUTH_TOKEN``;
            only the Web UI exposes this, never the A2A server.
        intent_review: whether confirm mode waits for approval 1; defaults to true only for the
            presentation view, because the explorer has no intent card.
        protocol: default interaction protocol of this UI process.
        protocols: interaction protocols the page may switch between; defaults to ``(protocol,)``.
            ``("a2a-t", "pure")`` exposes both request forms of the same agent.
        nms_enabled: whether the deployment talks to the vendor NMS; when ``None``, the process
            discovery mode decides. It selects each protocol's default hop (A2A-T direct to the
            NMS, standard A2A through the OSS Agent).
        mie_url: MIE Agent base URL; when set, the UI proxies its approval SSE/POST routes.
        tester_prompt_client: optional SDK prompt client injected into the tester's SDK stages
            (tests use a fake; production builds the real client lazily).

    Returns:
        The assembled Starlette application.

    Raises:
        ValueError: when an explicit default is not a supported value, or ``ui_mode`` is unknown.
    """
    if ui_mode not in WEB_UI_MODES:
        raise ValueError(f"Unsupported ui mode: {ui_mode}; expected one of {WEB_UI_MODES}")
    if default_brand not in SUPPORTED_BRANDS:
        raise ValueError(f"Unsupported presentation brand: {default_brand}; expected one of {SUPPORTED_BRANDS}")
    static_dir = PRESENTATION_DIR if ui_mode == WEB_UI_PRESENTATION else STATIC_DIR
    index_html = (static_dir / "index.html").read_text(encoding="utf-8")
    if ui_mode == WEB_UI_PRESENTATION:
        index_html = index_html.replace("__A2AT_DEFAULT_BRAND__", default_brand)
        # The page ships the Chinese template; let it hide that copy before the first paint when
        # the process language is different. The client applies the real copy after /api/meta.
        index_html = index_html.replace("__A2AT_LANGUAGE__", config.language)
    validate_bind_security(bind_host, config.auth_token, allow_insecure=allow_insecure_bind)
    if catalog is None:
        from demo.catalog import DemoRunCatalog

        catalog = DemoRunCatalog.from_config(config)
    resolved_catalog: RunCatalog = catalog
    scenario_names = resolved_catalog.names
    advertised_protocols = tuple(
        candidate for candidate in (protocols or (protocol,)) if candidate in DEMO_PROTOCOLS
    ) or (PROTOCOL_A2A_T,)
    resolved_protocol = protocol if protocol in advertised_protocols else advertised_protocols[0]

    def _scenarios_for(candidate: str) -> tuple[str, ...]:
        """Return the scenarios the page may offer for one advertised protocol."""
        if candidate == PROTOCOL_PURE:
            names = PURE_PROTOCOL_SCENARIOS
        elif candidate == PROTOCOL_PLAIN:
            names = PLAIN_PROTOCOL_SCENARIOS
        else:
            names = scenario_names
        return tuple(name for name in names if name in scenario_names)

    scenarios_by_protocol = {candidate: _scenarios_for(candidate) for candidate in advertised_protocols}
    active_scenarios = tuple(
        name for name in scenario_names if any(name in names for names in scenarios_by_protocol.values())
    )
    if not active_scenarios:
        active_scenarios = tuple(scenario_names)
    resolved_default_scenario = _resolve_default(default_scenario, active_scenarios, active_scenarios[0], "scenario")
    resolved_default_mode = _resolve_default(default_mode, UI_MODES, MODE_AUTO, "mode")
    config_transport = config.transport if config.transport in SUPPORTED_TRANSPORTS else SUPPORTED_TRANSPORTS[0]
    resolved_default_transport = _resolve_default(
        default_transport, SUPPORTED_TRANSPORTS, config_transport, "transport"
    )
    config_discovery = (
        config.discovery if config.discovery in SUPPORTED_DISCOVERY_MODES else SUPPORTED_DISCOVERY_MODES[0]
    )
    resolved_default_discovery = _resolve_default(
        default_discovery, SUPPORTED_DISCOVERY_MODES, config_discovery, "discovery"
    )
    # One agent serves both request forms, but the deployment decides the hop: A2A-T calls the
    # vendor NMS directly (or discovers the local RAN Agent), while standard A2A calls the vendor
    # NMS directly on a vendor deployment and the RAN Agent locally. `A2AT_SAMPLE_PURE_HOP=oss`
    # is the deprecated proxy opt-in; the hop is deployment-level and validated at startup.
    resolved_nms = (config.discovery == DISCOVERY_NMS) if nms_enabled is None else nms_enabled
    pure_hop_nms = config.pure_hop == PURE_HOP_NMS
    if pure_hop_nms and not resolved_nms:
        raise ValueError(
            "A2AT_SAMPLE_PURE_HOP=nms requires the nms discovery; a direct standard-A2A client "
            "has no OSS Agent to discover"
        )
    protocol_defaults: dict[str, dict[str, str]] = {
        PROTOCOL_A2A_T: {
            "discovery": DISCOVERY_NMS if resolved_nms else DISCOVERY_REGISTRY,
            # The launcher pins the NMS transport: the real vendor needs webhook, the local
            # simulator uses SSE. Taking it from the resolved default keeps `--nms-sim --ui`
            # from sending `message:send` to a simulator that only serves `message:stream`.
            "transport": resolved_default_transport,
        },
        PROTOCOL_PURE: {
            "discovery": DISCOVERY_NMS if pure_hop_nms else DISCOVERY_DIRECT,
            # The direct hop talks to the vendor with the deployment transport (webhook on the
            # real NMS, SSE on the simulator); the proxy hop keeps its historical SSE form.
            "transport": resolved_default_transport if pure_hop_nms else TRANSPORT_SSE,
        },
    }
    resolved_intent_mode = (
        config.intent_mode if config.intent_mode in SUPPORTED_INTENT_MODES else SUPPORTED_INTENT_MODES[0]
    )
    resolved_intent_review = ui_mode == WEB_UI_PRESENTATION if intent_review is None else intent_review

    async def default_factory(
        resolved_config: SampleConfig,
        scenario: str,
        mode: str,
        transport: str,
        discovery: str,
        *,
        protocol: str = PROTOCOL_A2A_T,
        run_id: str = "",
        scripted_decisions: Sequence[NegotiationDecision] | None = None,
        pure_answers: Sequence[str] | None = None,
    ) -> ClientRuntimeLike:
        """Build the real client runtime with the app's source ``.env``."""
        from demo.composition import build_demo_runtime

        return await build_demo_runtime(
            resolved_config,
            scenario,
            mode,
            transport,
            discovery,
            protocol=protocol,
            run_id=run_id,
            env_path=env_path,
            scripted_decisions=scripted_decisions,
            pure_answers=pure_answers,
        )

    resolved_runs_dir = runs_dir or Path(config.runs_dir)
    manager = RunManager(
        config=config,
        runtime_factory=runtime_factory or default_factory,
        catalog=resolved_catalog,
        runs_dir=resolved_runs_dir,
        store=client_store,
        memory_turns=memory_turns,
        intent_review=resolved_intent_review,
        metrics_grace_seconds=metrics_grace_seconds,
    )
    # Install the run-id router before any run is built. Uvicorn also runs the lifespan, but the
    # ASGI test client may not, and an in-process server already emits on its own thread.
    set_global_sink(manager.route_external_span)
    tester_routes, tester_batches = build_tester_routes(
        runs=manager,
        protocols=advertised_protocols,
        scenarios_by_protocol=scenarios_by_protocol,
        protocol_defaults=protocol_defaults,
        default_language=config.language,
        server_url=server_url,
        server_auth_token=config.server_auth_token,
        runs_dir=resolved_runs_dir,
        nms_enabled=resolved_nms,
        nms_sim=config.nms_sim,
        nms_sim_pure_question=config.nms_sim_pure_question,
        language=config.language,
        model_mode=config.model_mode,
        model_label=config.model_label,
        metrics_grace_seconds=metrics_grace_seconds,
        prompt_client=tester_prompt_client,
    )
    run_console_routes = build_run_console_routes(
        runs=manager,
        client_db_path=Path(config.client_db_path) if config.client_db_path else None,
        runs_dir=resolved_runs_dir,
        protocols=advertised_protocols,
        scenarios_by_protocol=scenarios_by_protocol,
        language=config.language,
        run_dir=Path(config.run_dir) if config.run_dir else None,
    )
    observe_routes = build_observe_routes(
        config=config,
        runs_dir=resolved_runs_dir,
        env_path=env_path,
        client_db_path=Path(config.client_db_path) if config.client_db_path else None,
    )

    @asynccontextmanager
    async def lifespan(app: Starlette) -> AsyncGenerator[None, None]:
        del app
        _LOGGER.info(
            "web ui starting: language=%s model_mode=%s client_db=%s",
            config.language,
            config.model_mode,
            config.client_db_path or "memory",
        )
        # A run whose remote Task is still executing survives the restart; only runs without one
        # are marked interrupted. This must be the first store operation of the process.
        recovered = await manager.recover_runs()
        if recovered:
            _LOGGER.info("recovered %d long-running run(s)", recovered)
        # Apply the decision-session retention window before the first run of this process.
        await manager.sweep_sessions()
        if config.discovery == DISCOVERY_NMS and config.transport == TRANSPORT_WEBHOOK:
            # Bind the process-level callback receiver now. A public URL pins the port, so a
            # collision can only fail; discovering it here reports it at startup instead of after
            # the operator has already approved a run. Later runs reuse this same instance.
            from protocol.shared_webhook import get_shared_webhook

            shared_webhook = await get_shared_webhook(config)
            _LOGGER.info("webhook receiver ready: push_url=%s", shared_webhook.url)
        # Producers that emit from another thread/task (e.g. an in-process server) cannot see a
        # run-local sink; route their spans by the run id they carry.
        set_global_sink(manager.route_external_span)
        try:
            yield
        finally:
            set_global_sink(None)
            await tester_batches.shutdown()
            await manager.shutdown()
            # The callback receiver is process-owned, not run-owned; release its port on shutdown.
            from protocol.shared_webhook import close_shared_webhooks

            await close_shared_webhooks()
            _LOGGER.info("web ui stopped")

    async def index(request: Request) -> Response:
        del request
        if ui_mode == WEB_UI_PRESENTATION:
            return HTMLResponse(index_html)
        return FileResponse(static_dir / "index.html")

    async def meta(request: Request) -> Response:
        del request
        return JSONResponse(
            {
                "language": config.language,
                "modelMode": config.model_mode,
                "modelLabel": config.model_label,
                "scenarios": list(active_scenarios),
                "scenarioRequests": {
                    name: _scenario_request(resolved_catalog, name, config.language) for name in active_scenarios
                },
                # The presentation can list running Tasks only when the client talks to the real
                # NMS; the local registry/direct server has no vendor task list endpoint.
                "nmsTasksEnabled": config.discovery == DISCOVERY_NMS,
                "modifyRequest": _modify_request(config, config.language),
                "modes": list(UI_MODES),
                "protocol": resolved_protocol,
                "protocols": list(advertised_protocols),
                "scenariosByProtocol": {name: list(names) for name, names in scenarios_by_protocol.items()},
                "protocolDefaults": protocol_defaults,
                "pureScenarios": list(PURE_PROTOCOL_SCENARIOS),
                "plainScenarios": list(PLAIN_PROTOCOL_SCENARIOS),
                "defaultProtocol": resolved_protocol,
                "mieEnabled": bool(mie_url) and PROTOCOL_PURE in advertised_protocols,
                "transports": list(SUPPORTED_TRANSPORTS),
                "discoveries": list(SUPPORTED_DISCOVERY_MODES),
                "defaultScenario": resolved_default_scenario,
                "defaultMode": resolved_default_mode,
                "defaultTransport": resolved_default_transport,
                "defaultDiscovery": resolved_default_discovery,
                "defaultIntentMode": resolved_intent_mode,
                "intentModes": list(SUPPORTED_INTENT_MODES),
                "uiMode": ui_mode,
                "defaultBrand": default_brand,
                "brands": list(SUPPORTED_BRANDS),
                "decisionMode": resolved_default_mode,
                "intentReview": resolved_intent_review and resolved_default_mode == "confirm",
                # The vendor NMS has no local approval route: never advertise a gate it cannot serve.
                "approvalGate": approval_gate and not resolved_nms,
                "approvalTimeoutSeconds": approval_timeout_seconds,
                "metricsEnabled": config.client_db_path is not None,
                # The UI must keep its stream open at least as long as the backend grace window,
                # otherwise the server's last trace batch is posted to a closed subscriber.
                "finishedGraceMs": int(round(finished_grace_seconds * 1000)),
            }
        )

    async def tasks(request: Request) -> Response:
        """Return the persisted A2A task summaries for the task-status table."""
        raw_limit = request.query_params.get("limit", "50")
        try:
            limit = int(raw_limit)
        except ValueError:
            return JSONResponse(
                {"error": "limit must be an integer.", "code": "limit_not_integer"},
                status_code=400,
            )
        if not 1 <= limit <= 500:
            return JSONResponse(
                {"error": "limit must be between 1 and 500.", "code": "limit_out_of_range"},
                status_code=400,
            )
        path = config.task_db_path
        enabled = path is not None and Path(path).is_file()
        if not enabled:
            return JSONResponse({"enabled": False, "tasks": []})
        rows = await asyncio.to_thread(read_task_summaries, path, limit)
        return JSONResponse({"enabled": True, "tasks": rows})

    async def nms_tasks(request: Request) -> Response:
        """Return the Tasks the Running-tasks picker may select.

        The list comes from the vendor NMS itself, not the local A2A task store: a Task can be
        modified while the NMS holds it in ``TASK_STATE_SUBMITTED`` or ``TASK_STATE_WORKING``,
        and any non-terminal Task, including ``TASK_STATE_INPUT_REQUIRED``, can be cancelled.
        ``state=active`` returns all three; ``state=working`` is kept for older callers and
        ``state=submitted`` returns only the newly accepted Tasks.
        """
        if config.discovery != DISCOVERY_NMS:
            return JSONResponse(
                {
                    "enabled": False,
                    "tasks": [],
                    "reason": "The task picker needs the real NMS discovery mode.",
                }
            )
        state = request.query_params.get("state", "modifiable").strip().lower()
        if state not in ("", "active", "modifiable", "working", "submitted"):
            return JSONResponse(
                {
                    "error": "state must be 'active', 'modifiable', 'working' or 'submitted'.",
                    "code": "unsupported_task_state",
                },
                status_code=400,
            )
        try:
            rows = await (list_active_tasks(config) if state == "active" else list_modifiable_tasks(config))
        except NmsConfigError as exc:
            return JSONResponse({"error": str(exc), "code": "nms_not_configured"}, status_code=503)
        except NmsError as exc:
            return JSONResponse(
                {"error": f"The NMS task list could not be read: {exc}", "code": "nms_unavailable"},
                status_code=502,
            )
        if state == "working":
            rows = [row for row in rows if row.get("state") == NMS_WORKING_STATE]
        elif state == "submitted":
            rows = [row for row in rows if row.get("state") == NMS_SUBMITTED_STATE]
        await _annotate_server_tasks(client_store, rows)
        return JSONResponse(
            {
                "enabled": True,
                "states": sorted(ACTIVE_STATES if state == "active" else MODIFIABLE_STATES),
                "tasks": rows,
            }
        )

    async def cancel_nms_task_route(request: Request) -> Response:
        """Cancel one NMS Task and stop the local runs that track it.

        The NMS cancel comes first: when the vendor answers the known HTTP 503, the Task keeps
        running and no local run is stopped (the operator still sees the warning). A successful
        cancel then stops every in-process run whose remote or target Task is this one.
        """
        if config.discovery != DISCOVERY_NMS:
            return JSONResponse(
                {
                    "error": "Cancelling an NMS Task needs the real NMS discovery mode.",
                    "code": "nms_discovery_required",
                },
                status_code=400,
            )
        task_id = request.path_params["task_id"]
        try:
            cancelled = await cancel_nms_task(config, task_id)
        except NmsConfigError as exc:
            return JSONResponse({"error": str(exc), "code": "nms_not_configured"}, status_code=503)
        except NmsError as exc:
            if "HTTP 404" in str(exc):
                return JSONResponse(
                    {"error": "The Task does not exist on the NMS.", "code": "task_missing"},
                    status_code=404,
                )
            return JSONResponse(
                {"error": f"The Task could not be cancelled: {exc}", "code": "nms_unavailable"},
                status_code=502,
            )
        if not cancelled:
            return JSONResponse(
                {
                    "taskId": task_id,
                    "cancelled": False,
                    "supported": False,
                    "reason": "nms_cancel_unsupported",
                    "stoppedRuns": [],
                }
            )
        stopped: list[str] = []
        for run in manager.active_runs_for_task(task_id):
            if await manager.cancel(run.run_id, remote=False):
                stopped.append(run.run_id)
        return JSONResponse(
            {
                "taskId": task_id,
                "cancelled": True,
                "supported": True,
                "stoppedRuns": stopped,
            }
        )

    def query_limit(request: Request) -> int | Response:
        """Parse and validate the ``limit`` query parameter."""
        raw_limit = request.query_params.get("limit", "50")
        try:
            limit = int(raw_limit)
        except ValueError:
            return JSONResponse(
                {"error": "limit must be an integer.", "code": "limit_not_integer"},
                status_code=400,
            )
        if not 1 <= limit <= 500:
            return JSONResponse(
                {"error": "limit must be between 1 and 500.", "code": "limit_out_of_range"},
                status_code=400,
            )
        return limit

    async def client_runs(request: Request) -> Response:
        """Return persisted client run summaries for the client-runs table."""
        limit = query_limit(request)
        if isinstance(limit, Response):
            return limit
        path = config.client_db_path
        enabled = path is not None and Path(path).is_file()
        if not enabled:
            return JSONResponse({"enabled": False, "runs": []})
        rows = await asyncio.to_thread(read_client_run_summaries, path, limit)
        return JSONResponse({"enabled": True, "runs": rows})

    async def run_wire(request: Request) -> Response:
        """Return the redacted NMS exchanges of one run, as the wire inspector shows them."""
        run_id = request.path_params["run_id"]
        root = runs_dir or Path(config.runs_dir)
        records = await asyncio.to_thread(read_wire_records, root, run_id)
        return JSONResponse({"runId": run_id, "records": records, "enabled": bool(records)})

    async def client_run_detail(request: Request) -> Response:
        """Return one persisted client run with turns and negotiation events."""
        path = config.client_db_path
        if path is None:
            return JSONResponse(
                {"error": "The client database is disabled.", "code": "client_db_disabled"},
                status_code=404,
            )
        detail = await asyncio.to_thread(read_client_run_detail, path, request.path_params["run_id"])
        if detail is None:
            return JSONResponse({"error": "Unknown run.", "code": "unknown_run"}, status_code=404)
        # The wire inspector shows what the client really sent. These records are written redacted
        # by the NMS client, so credentials arrive as "***" and need no second scrub.
        wire = await asyncio.to_thread(
            read_wire_records, runs_dir or Path(config.runs_dir), request.path_params["run_id"]
        )
        if wire:
            detail["wire"] = wire
        live = manager.get(request.path_params["run_id"])
        if live is not None:
            detail["live"] = live.snapshot()
        return JSONResponse(detail)

    async def client_conversations(request: Request) -> Response:
        """Return persisted client conversation summaries."""
        limit = query_limit(request)
        if isinstance(limit, Response):
            return limit
        path = config.client_db_path
        enabled = path is not None and Path(path).is_file()
        if not enabled:
            return JSONResponse({"enabled": False, "conversations": []})
        rows = await asyncio.to_thread(read_conversation_summaries, path, limit)
        return JSONResponse({"enabled": True, "conversations": rows})

    async def client_conversation_detail(request: Request) -> Response:
        """Return one conversation with its turns and runs."""
        path = config.client_db_path
        if path is None:
            return JSONResponse(
                {"error": "The client database is disabled.", "code": "client_db_disabled"},
                status_code=404,
            )
        detail = await asyncio.to_thread(read_conversation_detail, path, request.path_params["conversation_id"])
        if detail is None:
            return JSONResponse(
                {"error": "Unknown conversation.", "code": "unknown_conversation"},
                status_code=404,
            )
        return JSONResponse(detail)

    def metrics_filter(request: Request) -> tuple[str | None, str | None]:
        """Return the optional protocol/scenario filters of a metrics request."""
        protocol = (request.query_params.get("protocol") or "").strip() or None
        scenario = (request.query_params.get("scenario") or "").strip() or None
        return protocol, scenario

    def metrics_limit(request: Request) -> int:
        """Parse the metrics list limit (1..5000)."""
        try:
            return max(1, min(int(request.query_params.get("limit", "500")), 5000))
        except ValueError:
            return 500

    def stored_metrics(request: Request) -> list[dict[str, object]] | None:
        """Read persisted metrics rows; ``None`` when the client database is disabled."""
        path = config.client_db_path
        if path is None or not Path(path).is_file():
            return None
        protocol, scenario = metrics_filter(request)
        return read_run_metrics_rows(
            path,
            protocol=protocol,
            scenario=scenario,
            limit=metrics_limit(request),
        )

    async def run_metrics_detail(request: Request) -> Response:
        """Return one run's metrics: live while the process holds it, persisted otherwise."""
        run_id = request.path_params["run_id"]
        run = manager.get(run_id)
        metrics = manager.metrics_for(run_id)
        if metrics is not None and run is not None:
            elapsed = time.monotonic() - run.finished_at if run.finished_at is not None else 0.0
            metrics["traceFinal"] = run.finished() and elapsed >= manager.metrics_grace_seconds
            return JSONResponse(metrics)
        path = config.client_db_path
        stored = read_run_metrics(path, run_id) if path is not None else None
        if stored is None:
            return JSONResponse(
                {"error": "No metrics have been recorded for this run.", "code": "metrics_unknown"},
                status_code=404,
            )
        stored["traceFinal"] = True
        return JSONResponse(stored)

    async def metrics_runs(request: Request) -> Response:
        """Return the persisted per-run metrics rows."""
        rows = await asyncio.to_thread(stored_metrics, request)
        if rows is None:
            return JSONResponse({"enabled": False, "runs": []})
        return JSONResponse({"enabled": True, "runs": rows})

    async def metrics_summary(request: Request) -> Response:
        """Return protocol/scenario aggregates and the headline protocol comparison."""
        rows = await asyncio.to_thread(stored_metrics, request)
        if rows is None:
            return JSONResponse({"enabled": False, "groups": [], "protocols": [], "runs": 0})
        return JSONResponse(
            {
                "enabled": True,
                "runs": len(rows),
                "groups": summarize_metrics(rows),
                "protocols": summarize_protocols(rows),
            }
        )

    async def metrics_csv(request: Request) -> Response:
        """Download the persisted metrics as CSV (one row per run)."""
        rows = await asyncio.to_thread(stored_metrics, request)
        if rows is None:
            return JSONResponse({"enabled": False, "runs": []})
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow(
            [
                "runId",
                "protocol",
                "scenario",
                "mode",
                "modelMode",
                "status",
                "finalState",
                "passed",
                "accuracy",
                "rounds",
                "promptTokens",
                "completionTokens",
                "totalTokens",
                "cachedTokens",
                "cacheHitRate",
                "llmCalls",
                "durationMs",
                "modelMs",
                "approvalWaitMs",
                "startedAt",
                "finishedAt",
            ]
        )
        for row in rows:
            accuracy = row.get("accuracy") if isinstance(row.get("accuracy"), Mapping) else {}
            negotiation = row.get("negotiation") if isinstance(row.get("negotiation"), Mapping) else {}
            tokens = row.get("tokens") if isinstance(row.get("tokens"), Mapping) else {}
            latency = row.get("latency") if isinstance(row.get("latency"), Mapping) else {}
            writer.writerow(
                [
                    row.get("runId"),
                    row.get("protocol"),
                    row.get("scenario"),
                    row.get("mode"),
                    row.get("modelMode"),
                    row.get("status"),
                    row.get("finalState"),
                    accuracy.get("passed"),
                    accuracy.get("score"),
                    negotiation.get("rounds"),
                    tokens.get("prompt"),
                    tokens.get("completion"),
                    tokens.get("total"),
                    tokens.get("cached"),
                    tokens.get("cacheHitRate"),
                    tokens.get("llmCalls"),
                    latency.get("totalMs"),
                    latency.get("modelMs"),
                    latency.get("approvalWaitMs"),
                    row.get("startedAt"),
                    row.get("finishedAt"),
                ]
            )
        return Response(
            buffer.getvalue(),
            media_type="text/csv",
            headers={"Content-Disposition": 'attachment; filename="run-metrics.csv"'},
        )

    async def comparison_page(request: Request) -> Response:
        """Serve the protocol comparison page."""
        del request
        return FileResponse(COMPARISON_DIR / "index.html")

    async def start_run(request: Request) -> Response:
        try:
            body = await request.json()
        except ValueError:
            return JSONResponse(
                {"error": "The request body must be a JSON object.", "code": "body_not_object"},
                status_code=400,
            )
        if not isinstance(body, dict):
            return JSONResponse(
                {"error": "The request body must be a JSON object.", "code": "body_not_object"},
                status_code=400,
            )
        scenario = body.get("scenario", resolved_default_scenario)
        mode = body.get("mode", resolved_default_mode)
        protocol = body.get("protocol", resolved_protocol)
        protocol_key = str(protocol)
        defaults = protocol_defaults.get(protocol_key, {})
        # The page sends only the protocol; the backend owns each request form's hop so a stale
        # browser value can never send standard A2A through the A2A-T discovery path.
        transport = body.get("transport") or defaults.get("transport") or resolved_default_transport
        discovery = body.get("discovery") or defaults.get("discovery") or resolved_default_discovery
        intent_mode = body.get("intentMode", resolved_intent_mode)
        raw_user_request = body.get("userRequest")
        if raw_user_request is not None and not isinstance(raw_user_request, str):
            return JSONResponse(
                {"error": "The userRequest field must be a string."},
                status_code=400,
            )
        user_request = raw_user_request.strip() if isinstance(raw_user_request, str) else None
        optional_strings: dict[str, str | None] = {}
        for field_name in (
            "conversationId",
            "externalConversationId",
            "traceId",
            "chainId",
            "parentRunId",
            "userId",
            "idempotencyKey",
        ):
            raw_value = body.get(field_name)
            if raw_value is not None and not isinstance(raw_value, str):
                return JSONResponse(
                    {"error": f"The {field_name} field must be a string."},
                    status_code=400,
                )
            optional_strings[field_name] = (
                raw_value.strip() if isinstance(raw_value, str) and raw_value.strip() else None
            )
        target_task_id = body.get("targetTaskId")
        target_context_id = body.get("targetContextId")
        target_server_task_id = body.get("targetServerTaskId")
        target_server_context_id = body.get("targetServerContextId")
        for field_name, value in (
            ("targetTaskId", target_task_id),
            ("targetContextId", target_context_id),
            ("targetServerTaskId", target_server_task_id),
            ("targetServerContextId", target_server_context_id),
        ):
            if value is not None and not isinstance(value, str):
                return JSONResponse(
                    {"error": f"The {field_name} field must be a string."},
                    status_code=400,
                )
        resolved_target_task = target_task_id.strip() if isinstance(target_task_id, str) else ""
        resolved_target_context = target_context_id.strip() if isinstance(target_context_id, str) else ""
        resolved_server_task = (
            target_server_task_id.strip() if isinstance(target_server_task_id, str) else ""
        )
        resolved_server_context = (
            target_server_context_id.strip() if isinstance(target_server_context_id, str) else ""
        )
        if bool(resolved_target_task) != bool(resolved_target_context):
            return JSONResponse(
                {
                    "error": "targetTaskId and targetContextId must be provided together.",
                    "code": "target_pair_required",
                },
                status_code=400,
            )
        if bool(resolved_server_task) != bool(resolved_server_context):
            return JSONResponse(
                {
                    "error": "targetServerTaskId and targetServerContextId must be provided together.",
                    "code": "target_server_pair_required",
                },
                status_code=400,
            )
        if resolved_target_task:
            if protocol not in (PROTOCOL_A2A_T, PROTOCOL_PURE):
                return JSONResponse(
                    {
                        "error": "A Modify-only run requires A2A-T or pure A2A.",
                        "code": "target_protocol_unsupported",
                    },
                    status_code=400,
                )
            # The picker always reads the vendor NMS from this process, while a pure run's own hop
            # is the OSS Agent (direct). What matters is that the page can validate the vendor
            # Task; an A2A-T run additionally continues that same Task directly.
            if config.discovery != DISCOVERY_NMS or (
                protocol == PROTOCOL_A2A_T and discovery != DISCOVERY_NMS
            ):
                return JSONResponse(
                    {
                        "error": "The task picker is available only with NMS discovery.",
                        "code": "target_discovery_unsupported",
                    },
                    status_code=400,
                )
            try:
                task = await read_task(config, resolved_target_task)
            except NmsConfigError as exc:
                return JSONResponse({"error": str(exc), "code": "nms_not_configured"}, status_code=503)
            except NmsError as exc:
                return JSONResponse(
                    {
                        "error": f"The target Task could not be read: {exc}",
                        "code": "nms_unavailable",
                    },
                    status_code=502,
                )
            if task is None:
                return JSONResponse(
                    {"error": "The target Task does not exist on the NMS.", "code": "target_missing"},
                    status_code=409,
                )
            if task.get("state") not in MODIFIABLE_STATES:
                return JSONResponse(
                    {
                        "error": (
                            "The target Task is not in a modifiable state (SUBMITTED or WORKING); "
                            "refresh the task list and choose another one."
                        ),
                        "code": "target_not_modifiable",
                        "state": task.get("state"),
                    },
                    status_code=409,
                )
            task_context = task.get("contextId")
            if isinstance(task_context, str) and task_context and task_context != resolved_target_context:
                return JSONResponse(
                    {
                        "error": "The target Task context does not match the NMS Task.",
                        "code": "target_context_mismatch",
                    },
                    status_code=409,
                )
            if protocol == PROTOCOL_PURE and discovery == DISCOVERY_NMS:
                # A direct standard-A2A Modify addresses the vendor Task itself: targetTaskId is
                # the wire taskId, and there is no OSS outer Task to name.
                if resolved_server_task:
                    return JSONResponse(
                        {
                            "error": (
                                "A direct standard-A2A Modify addresses the vendor Task; drop "
                                "targetServerTaskId/targetServerContextId."
                            ),
                            "code": "target_server_not_used",
                        },
                        status_code=400,
                    )
            elif protocol == PROTOCOL_PURE:
                # The vendor Task is only the picker's identity. The message must continue the
                # A2A peer's outer Task, and only a pure run that observed this vendor Task knows
                # that id. Refuse rather than send a message that OSS would treat as a new Task.
                if not resolved_server_task:
                    return JSONResponse(
                        {
                            "error": (
                                "A pure-A2A Modify needs the A2A peer's Task and context "
                                "(targetServerTaskId/targetServerContextId)."
                            ),
                            "code": "target_server_pair_required",
                        },
                        status_code=400,
                    )
                mapping = await _pure_run_mapping(client_store)
                mapped = mapping.get(resolved_target_task)
                if mapped != (resolved_server_task, resolved_server_context):
                    return JSONResponse(
                        {
                            "error": (
                                "No pure-A2A run of this page owns the target Task; create the "
                                "Task from this page first or use the A2A-T request form."
                            ),
                            "code": "target_server_mapping_missing",
                        },
                        status_code=409,
                    )
        elif resolved_server_task:
            return JSONResponse(
                {
                    "error": "targetServerTaskId/targetServerContextId require targetTaskId.",
                    "code": "target_pair_required",
                },
                status_code=400,
            )
        requested_scenarios = scenarios_by_protocol.get(
            protocol_key,
            (
                PURE_PROTOCOL_SCENARIOS
                if protocol == PROTOCOL_PURE
                else PLAIN_PROTOCOL_SCENARIOS
                if protocol == PROTOCOL_PLAIN
                else scenario_names
            ),
        )
        if resolved_target_task and protocol == PROTOCOL_PURE and scenario in scenario_names:
            # A pure Modify carries the operator's text; the scenario only labels the run and
            # supplies the Modify example. It is not a request form the pure list curates.
            requested_scenarios = (scenario,)
        if scenario not in requested_scenarios:
            return JSONResponse(
                {
                    "error": (
                        f"Unsupported scenario for protocol {protocol}: {scenario}; "
                        f"expected one of {requested_scenarios}"
                    )
                },
                status_code=400,
            )
        if mode not in UI_MODES:
            return JSONResponse(
                {"error": f"Unsupported mode: {mode}; expected one of {UI_MODES}"},
                status_code=400,
            )
        if protocol not in SUPPORTED_PROTOCOLS:
            return JSONResponse(
                {"error": f"Unsupported protocol: {protocol}; expected one of {SUPPORTED_PROTOCOLS}"},
                status_code=400,
            )
        if transport not in SUPPORTED_TRANSPORTS:
            return JSONResponse(
                {"error": f"Unsupported transport: {transport}; expected one of {SUPPORTED_TRANSPORTS}"},
                status_code=400,
            )
        if discovery not in SUPPORTED_DISCOVERY_MODES:
            return JSONResponse(
                {"error": f"Unsupported discovery: {discovery}; expected one of {SUPPORTED_DISCOVERY_MODES}"},
                status_code=400,
            )
        if intent_mode not in SUPPORTED_INTENT_MODES:
            return JSONResponse(
                {"error": (f"Unsupported intent mode: {intent_mode}; expected one of {SUPPORTED_INTENT_MODES}")},
                status_code=400,
            )
        try:
            run = await manager.start(
                str(scenario),
                str(mode),
                str(transport),
                str(discovery),
                user_request,
                protocol=str(protocol),
                conversation_id=optional_strings["conversationId"],
                external_conversation_id=optional_strings["externalConversationId"],
                trace_id=optional_strings["traceId"],
                chain_id=optional_strings["chainId"],
                parent_run_id=optional_strings["parentRunId"],
                user_id=optional_strings["userId"] or "web-user",
                idempotency_key=optional_strings["idempotencyKey"],
                intent_mode=str(intent_mode),
                source=_request_source(request, body.get("source")),
                target_task_id=resolved_target_task or None,
                target_context_id=resolved_target_context or None,
                target_server_task_id=resolved_server_task or None,
                target_server_context_id=resolved_server_context or None,
            )
        except RunConflictError as exc:
            return JSONResponse({"error": str(exc), "code": "run_conflict"}, status_code=409)
        except ValueError as exc:
            return JSONResponse({"error": str(exc), "code": "invalid_run"}, status_code=400)
        reused = run.task is None
        return JSONResponse(
            {
                "runId": run.run_id,
                "conversationId": run.conversation_id,
                "traceId": run.trace_id,
                "chainId": run.chain_id,
                "parentRunId": run.parent_run_id or None,
                "deduplicated": reused,
                "protocol": run.protocol,
                "language": config.language,
                "modelMode": config.model_mode,
                "modelLabel": config.model_label,
                "discovery": discovery,
                "intentMode": run.intent_mode or str(intent_mode),
            },
            status_code=200 if reused else 202,
        )

    async def run_status(request: Request) -> Response:
        run = manager.get(request.path_params["run_id"])
        if run is None:
            return JSONResponse({"error": "Unknown run.", "code": "unknown_run"}, status_code=404)
        return JSONResponse(run.snapshot())

    async def cancel_run(request: Request) -> Response:
        """Stop one running task (presentation UI cancel button).

        The manager asks the remote transport to cancel its Task first (best effort; the vendor
        NMS is known to answer HTTP 503), then stops the local run. The run task still publishes
        its terminal ``finished`` frame, so every subscriber sees the cancellation.
        """
        run = manager.get(request.path_params["run_id"])
        if run is None:
            return JSONResponse({"error": "Unknown run.", "code": "unknown_run"}, status_code=404)
        if not await manager.cancel(run.run_id):
            return JSONResponse(
                {"error": "The run is already finished.", "code": "run_not_active"},
                status_code=409,
            )
        return JSONResponse({"runId": run.run_id, "status": "cancelled"}, status_code=202)

    async def run_events(request: Request) -> Response:
        run = manager.get(request.path_params["run_id"])
        if run is None:
            return JSONResponse({"error": "Unknown run.", "code": "unknown_run"}, status_code=404)
        snapshot, queue = run.subscribe()

        async def stream() -> AsyncIterator[dict[str, str]]:
            """Replay recorded messages, then forward live ones (including post-``finished`` spans).

            The stream does not return the moment ``finished`` is seen: the server process posts its
            last trace batch a flush interval later, and those spans belong to this run. A short
            grace window keeps the subscriber attached so the browser sees the server LLM/tool
            rows of a fresh run (design §8: reconnect must not lose events).
            """
            try:
                for message in snapshot:
                    yield _sse_data(message)
                deadline: float | None = None
                finished_at = run.finished_at
                if run.finished() and finished_at is not None:
                    deadline = finished_at + finished_grace_seconds
                while True:
                    # Frames broadcast between ``subscribe`` and the first iteration are already
                    # queued; drain them before the deadline can end the stream.
                    try:
                        message = queue.get_nowait()
                    except asyncio.QueueEmpty:
                        if deadline is not None:
                            remaining = deadline - time.monotonic()
                            if remaining <= 0:
                                return
                            try:
                                message = await asyncio.wait_for(queue.get(), timeout=remaining)
                            except TimeoutError:
                                return
                        else:
                            message = await queue.get()
                    yield _sse_data(message)
                    if message.get("type") == "finished":
                        finished_at = run.finished_at or time.monotonic()
                        deadline = finished_at + finished_grace_seconds
            finally:
                run.unsubscribe(queue)

        return EventSourceResponse(stream(), ping=15)

    async def submit_decision(request: Request) -> Response:
        run = manager.get(request.path_params["run_id"])
        if run is None:
            return JSONResponse({"error": "Unknown run.", "code": "unknown_run"}, status_code=404)
        try:
            body = await request.json()
            # ``answerSource``/``answerText`` are page provenance, not protocol fields: read them
            # before validation because the decision model ignores unknown keys. The text is the
            # operator's own wording; a pure A2A answer is sent verbatim downstream.
            source = body.get("answerSource") if isinstance(body, dict) else None
            answer_text = body.get("answerText") if isinstance(body, dict) else None
            decision = NegotiationDecision.model_validate(body)
        except (ValueError, TypeError):
            return JSONResponse(
                {
                    "error": "The body must be a NegotiationDecision JSON object.",
                    "code": "decision_body_invalid",
                },
                status_code=400,
            )
        provider = run.manual_provider
        if run.status != STATUS_RUNNING or not run.pending_decision or provider is None:
            return JSONResponse(
                {"error": "No manual decision is pending for this run."},
                status_code=409,
            )
        run.pending_decision = False
        run.pending_answer_source = source.strip() if isinstance(source, str) and source.strip() else None
        # OSP `AQ`: the answer moment is here, not when the grounded decision is recorded later.
        run.mark_decision_answered()
        set_answer_text = getattr(provider, "set_answer_text", None)
        if callable(set_answer_text):
            set_answer_text(answer_text if isinstance(answer_text, str) else None)
        provider.submit(decision)
        return JSONResponse({"accepted": True, "runId": run.run_id})

    async def parse_decision(request: Request) -> Response:
        """Structure one engineer's manual answer into a decision preview.

        The preview is not submitted: the page shows it and the engineer confirms through the
        normal decision endpoint. This keeps a model misread from silently changing the
        negotiation.
        """
        run = manager.get(request.path_params["run_id"])
        if run is None:
            return JSONResponse({"error": "Unknown run.", "code": "unknown_run"}, status_code=404)
        try:
            body = await request.json()
        except (ValueError, TypeError):
            return JSONResponse(
                {"error": "The body must be a JSON object.", "code": "body_not_object"},
                status_code=400,
            )
        text = body.get("text") if isinstance(body, dict) else None
        if not isinstance(text, str) or not text.strip():
            return JSONResponse(
                {"error": "The text field must be a non-empty string.", "code": "manual_text_invalid"},
                status_code=400,
            )
        if run.status != STATUS_RUNNING or not run.pending_decision or run.manual_provider is None:
            return JSONResponse(
                {"error": "No manual decision is pending for this run."},
                status_code=409,
            )
        structurer = run.manual_structurer
        if structurer is None:
            return JSONResponse(
                {
                    "error": "Structuring a manual answer requires a live decision model.",
                    "code": "manual_parse_unavailable",
                },
                status_code=422,
            )
        propose = run.proposals[-1] if run.proposals else None
        if propose is None:
            return JSONResponse(
                {"error": "No inbound proposal is available for this decision."},
                status_code=409,
            )
        session = NegotiationContext(
            id=propose.negotiation_id,
            round=propose.round,
            max_rounds=propose.max_rounds,
            performative=NegotiationPerformative(propose.performative),
        )
        try:
            decision = await structurer.structure_answer(propose, session, text.strip())
        except Exception as exc:  # noqa: BLE001 - a model failure must become a readable 422
            return JSONResponse(
                {
                    "error": f"Manual answer parsing failed: {type(exc).__name__}: {exc}",
                    "code": "manual_parse_failed",
                },
                status_code=422,
            )
        if decision is None:
            return JSONResponse(
                {
                    "error": (
                        "The answer could not be structured into a decision for this proposal; "
                        "rephrase it or send it as a free-text counter-proposal."
                    ),
                    "code": "manual_parse_failed",
                },
                status_code=422,
            )
        if run.protocol == PROTOCOL_PURE and propose.kind == "information":
            # A pure A2A answer is one plain sentence: an invented value would reach the vendor as
            # if the operator had typed it. Only accept values grounded in the answer text or in an
            # explicit page selection.
            selected = body.get("region")
            ungrounded = ungrounded_answer_fields(
                text,
                decision.filled_params,
                allow=(selected,) if isinstance(selected, str) and selected.strip() else (),
            )
            if ungrounded:
                return JSONResponse(
                    {
                        "error": (
                            "The structured answer contains values the operator did not supply: "
                            f"{', '.join(ungrounded)}. Rewrite the answer with the exact value."
                        ),
                        "code": "manual_answer_ungrounded",
                        "fields": list(ungrounded),
                    },
                    status_code=422,
                )
        return JSONResponse(
            {
                "decision": decision.model_dump(),
                "missingItems": list(missing_item_labels(propose.content)),
            }
        )

    async def submit_intent(request: Request) -> Response:
        """Resolve approval 1: the engineer confirms or rejects the structured intent."""
        run = manager.get(request.path_params["run_id"])
        if run is None:
            return JSONResponse({"error": "Unknown run.", "code": "unknown_run"}, status_code=404)
        try:
            body = await request.json()
        except ValueError:
            return JSONResponse(
                {"error": "The body must be a JSON object.", "code": "body_not_object"},
                status_code=400,
            )
        if not isinstance(body, dict) or not isinstance(body.get("approved"), bool):
            return JSONResponse(
                {
                    "error": "The approved field must be a boolean.",
                    "code": "approved_invalid",
                },
                status_code=400,
            )
        if run.status != STATUS_RUNNING or not run.resolve_intent(body["approved"]):
            return JSONResponse(
                {"error": "No intent review is pending for this run."},
                status_code=409,
            )
        return JSONResponse({"accepted": True, "runId": run.run_id})

    async def submit_approval(request: Request) -> Response:
        """Forward the engineer's change approval to the NMS approval gate."""
        run = manager.get(request.path_params["run_id"])
        if run is None:
            return JSONResponse({"error": "Unknown run.", "code": "unknown_run"}, status_code=404)
        try:
            body = await request.json()
        except ValueError:
            return JSONResponse(
                {"error": "The body must be a JSON object.", "code": "body_not_object"},
                status_code=400,
            )
        if not isinstance(body, dict) or not isinstance(body.get("approved"), bool):
            return JSONResponse(
                {
                    "error": "The approved field must be a boolean.",
                    "code": "approved_invalid",
                },
                status_code=400,
            )
        # A real NMS owns execution approval and exposes no /approvals route. Refuse before any
        # network call so the page gets a truthful reason instead of a 404/TLS "unreachable".
        if resolved_nms or _targets_the_nms(server_url, config):
            return JSONResponse(
                {
                    "error": (
                        "This run talks to the vendor NMS, which owns execution approval and has "
                        "no local /approvals route. Nothing was sent."
                    ),
                    "code": "approval_not_supported_in_nms",
                },
                status_code=409,
            )
        # The page's gate flag only controls whether it expects the MIE card. Forward whenever the
        # server URL is configured: a Web UI started separately from the A2A server must still be
        # able to resolve a gate the server is actually holding.
        if not server_url:
            return JSONResponse(
                {
                    "error": "This Web UI has no A2A server URL to forward the approval to.",
                    "code": "approval_forward_unconfigured",
                },
                status_code=404,
            )
        if run.status != STATUS_RUNNING:
            return JSONResponse(
                {"error": "The run is not running.", "code": "run_not_running"},
                status_code=409,
            )
        headers = {}
        if config.server_auth_token:
            headers["authorization"] = f"Bearer {config.server_auth_token}"
        status_code, payload = await forward_approval_decision(
            server_url,
            run_id=run.run_id,
            approved=body["approved"],
            headers=headers,
            verify_tls=config.nms_verify_tls,
        )
        return JSONResponse(payload, status_code=status_code)

    async def _json_or_empty(response: httpx.Response) -> dict[str, object]:
        """Return one forwarded JSON object, or an empty mapping."""
        try:
            value = response.json()
        except ValueError:
            return {}
        return dict(value) if isinstance(value, dict) else {}

    async def mie_events(request: Request) -> Response:
        """Proxy the MIE Agent approval SSE stream to the browser."""
        del request
        if not mie_url:
            return JSONResponse(
                {"error": "The MIE Agent is not configured.", "code": "mie_disabled"},
                status_code=404,
            )

        async def stream() -> AsyncIterator[bytes]:
            try:
                async with httpx.AsyncClient(timeout=None, trust_env=False) as client:
                    async with client.stream("GET", f"{mie_url.rstrip('/')}/approvals/events") as response:
                        response.raise_for_status()
                        async for chunk in response.aiter_bytes():
                            yield chunk
            except httpx.HTTPError as exc:
                _LOGGER.warning("MIE event proxy stopped: %s", exc)
                return

        return StreamingResponse(stream(), media_type="text/event-stream")

    async def mie_decide(request: Request) -> Response:
        """Forward one engineer decision to the MIE Agent."""
        if not mie_url:
            return JSONResponse(
                {"error": "The MIE Agent is not configured.", "code": "mie_disabled"},
                status_code=404,
            )
        try:
            body = await request.json()
        except ValueError:
            return JSONResponse(
                {"error": "The body must be a JSON object.", "code": "body_not_object"},
                status_code=400,
            )
        if not isinstance(body, dict) or not isinstance(body.get("approved"), bool):
            return JSONResponse(
                {"error": "The approved field must be a boolean.", "code": "approved_invalid"},
                status_code=400,
            )
        task_id = request.path_params["task_id"]
        try:
            async with httpx.AsyncClient(trust_env=False, timeout=10.0) as client:
                forwarded = await client.post(
                    f"{mie_url.rstrip('/')}/approvals/{task_id}",
                    json={"approved": body["approved"]},
                )
        except httpx.HTTPError as exc:
            _LOGGER.warning("MIE approval forward failed: %s", exc)
            return JSONResponse(
                {"error": "The MIE Agent is unreachable.", "code": "mie_unreachable"},
                status_code=502,
            )
        payload = await _json_or_empty(forwarded)
        return JSONResponse(payload, status_code=forwarded.status_code)

    async def _stored_transcript(request: Request, field_name: str) -> str | None:
        """Read one transcript path persisted with a finished run."""
        path = config.client_db_path
        if path is None:
            return None
        detail = await asyncio.to_thread(read_client_run_detail, path, request.path_params["run_id"])
        stored = detail.get(field_name) if detail is not None else None
        if not isinstance(stored, str) or not Path(stored).is_file():
            return None
        try:
            return await asyncio.to_thread(Path(stored).read_text, encoding="utf-8")
        except OSError:
            return None

    async def transcript_json(request: Request) -> Response:
        run = manager.get(request.path_params["run_id"])
        if run is not None and run.transcript is not None:
            text = run.transcript.to_json()
        else:
            text = await _stored_transcript(request, "transcriptJson")
        if text is None:
            return JSONResponse({"error": "Unknown run.", "code": "unknown_run"}, status_code=404)
        return Response(
            text,
            media_type="application/json",
            headers={"Content-Disposition": (f'attachment; filename="{request.path_params["run_id"]}.json"')},
        )

    async def transcript_markdown(request: Request) -> Response:
        run = manager.get(request.path_params["run_id"])
        if run is not None and run.transcript is not None:
            text = run.transcript.to_markdown()
        else:
            text = await _stored_transcript(request, "transcriptMarkdown")
        if text is None:
            return JSONResponse({"error": "Unknown run.", "code": "unknown_run"}, status_code=404)
        return Response(
            text,
            media_type="text/markdown",
            headers={"Content-Disposition": (f'attachment; filename="{request.path_params["run_id"]}.md"')},
        )

    async def run_span(request: Request) -> Response:
        run = manager.get(request.path_params["run_id"])
        if run is None:
            return JSONResponse({"error": "Unknown run.", "code": "unknown_run"}, status_code=404)
        span = run.spans.get(request.path_params["span_id"])
        if span is None:
            return JSONResponse({"error": "Unknown span.", "code": "unknown_span"}, status_code=404)
        return JSONResponse(span)

    async def run_trace_json(request: Request) -> Response:
        run_id = request.path_params["run_id"]
        run = manager.get(run_id)
        if run is not None:
            spans = sorted(run.spans.values(), key=_span_seq)
            return JSONResponse(
                {
                    "schemaVersion": SCHEMA_VERSION,
                    "runId": run.run_id,
                    "scenario": run.scenario,
                    "status": run.status,
                    "spans": spans,
                    "transcript": run.transcript.to_dict() if run.transcript is not None else None,
                }
            )
        persisted = await _persisted_trace(run_id)
        if persisted is not None:
            return JSONResponse(persisted)
        return JSONResponse(
            {"error": "trace 仅本次进程内可看", "code": "trace_not_available"},
            status_code=404,
        )

    async def _persisted_trace(run_id: str) -> dict[str, object] | None:
        """Rebuild a trace document from the persisted run/transcript of a previous process.

        The client store keeps no separate trace file, but the transcript JSON persists every trace
        span as a timeline entry. The reconstruction is best effort: a run record without a readable
        transcript still returns an (empty) trace document rather than pretending it is unavailable.
        """
        path = config.client_db_path
        if path is None:
            return None
        detail = await asyncio.to_thread(read_client_run_detail, path, run_id)
        if detail is None:
            return None
        transcript: dict[str, object] | None = None
        spans: list[dict[str, object]] = []
        transcript_path = detail.get("transcriptJson")
        if isinstance(transcript_path, str) and Path(transcript_path).is_file():
            try:
                text = await asyncio.to_thread(Path(transcript_path).read_text, encoding="utf-8")
                document = json.loads(text)
            except (OSError, ValueError):
                document = None
            if isinstance(document, dict):
                transcript = document
                events = document.get("events")
                if isinstance(events, list):
                    spans = [
                        _span_from_transcript_event(event)
                        for event in events
                        if isinstance(event, dict) and event.get("entryType") == "trace"
                    ]
        return {
            "schemaVersion": SCHEMA_VERSION,
            "runId": run_id,
            "scenario": detail.get("scenario"),
            "status": detail.get("status"),
            "spans": sorted(spans, key=_span_seq),
            "transcript": transcript,
            "persisted": True,
        }

    async def ingest_trace(request: Request) -> Response:
        run = manager.get(request.path_params["run_id"])
        if run is None:
            # A stale OSS Agent posts spans for a run that this UI instance does not own (for
            # example after a UI restart). Keep the reason in ui.log so the operator can tell it
            # apart from a token mismatch.
            _LOGGER.warning("trace ingest rejected: unknown run %s", request.path_params["run_id"])
            return JSONResponse({"error": "Unknown run.", "code": "unknown_run"}, status_code=404)
        if not _trace_token_ok(request):
            _LOGGER.warning("trace ingest rejected: invalid token")
            return JSONResponse(
                {"error": "Invalid trace token.", "code": "trace_token_invalid"},
                status_code=403,
            )
        try:
            body = await request.json()
        except ValueError:
            return JSONResponse(
                {"error": "The body must be a JSON span or span list.", "code": "trace_body_invalid"},
                status_code=400,
            )
        spans = _coerce_trace_payload(body)
        if spans is None:
            return JSONResponse(
                {"error": "The body must be a JSON span or span list.", "code": "trace_body_invalid"},
                status_code=400,
            )
        for index, span in enumerate(spans):
            problem = _validate_trace_span(span, run.run_id)
            if problem is not None:
                return JSONResponse(
                    {"error": f"span[{index}]: {problem}", "code": "span_invalid"},
                    status_code=400,
                )
        created = 0
        for span in spans:
            before = len(run.traces)
            run.record_trace(span)
            if len(run.traces) > before:
                created += 1
        response: dict[str, object] = {"accepted": created, "runId": run.run_id}
        if created < len(spans):
            response["deduplicated"] = len(spans) - created
        return JSONResponse(response, status_code=202)

    app = Starlette(
        routes=[
            Route("/", index),
            Route("/api/meta", meta),
            Route("/api/tasks", tasks),
            Route("/api/nms/tasks", nms_tasks),
            Route("/api/nms/tasks/{task_id}/cancel", cancel_nms_task_route, methods=["POST"]),
            Route("/api/client/runs", client_runs),
            Route("/api/client/runs/{run_id}", client_run_detail),
            Route("/api/client/conversations", client_conversations),
            Route("/api/client/conversations/{conversation_id}", client_conversation_detail),
            Route("/api/runs/{run_id}/wire", run_wire),
            Route("/api/runs/{run_id}/metrics", run_metrics_detail),
            Route("/api/metrics/runs", metrics_runs),
            Route("/api/metrics/summary", metrics_summary),
            Route("/api/metrics/export.csv", metrics_csv),
            Route("/comparison", comparison_page),
            Route("/api/runs", start_run, methods=["POST"]),
            Route("/api/runs/{run_id}", run_status),
            Route("/api/runs/{run_id}/cancel", cancel_run, methods=["POST"]),
            Route("/api/runs/{run_id}/events", run_events),
            Route("/api/runs/{run_id}/decisions", submit_decision, methods=["POST"]),
            Route("/api/runs/{run_id}/decisions/parse", parse_decision, methods=["POST"]),
            Route("/api/runs/{run_id}/intent", submit_intent, methods=["POST"]),
            Route("/api/runs/{run_id}/approvals", submit_approval, methods=["POST"]),
            Route("/api/mie/events", mie_events),
            Route("/api/mie/approvals/{task_id}", mie_decide, methods=["POST"]),
            Route("/api/runs/{run_id}/transcript.json", transcript_json),
            Route("/api/runs/{run_id}/transcript.md", transcript_markdown),
            Route("/api/runs/{run_id}/trace", ingest_trace, methods=["POST"]),
            Route("/api/runs/{run_id}/trace.json", run_trace_json),
            Route("/api/runs/{run_id}/spans/{span_id}", run_span),
            *tester_routes,
            *run_console_routes,
            *observe_routes,
            Mount("/static", app=StaticFiles(directory=static_dir), name="static"),
        ],
        lifespan=lifespan,
        middleware=(
            [
                Middleware(
                    BearerAuthMiddleware,
                    token=config.auth_token,
                    protected_prefixes=("/api/", "/metrics"),
                    # The trace ingest route carries the run-scoped local trace token instead of
                    # the user bearer; the handler still rejects a wrong or absent trace token.
                    public_path_patterns=(TRACE_INGEST_PATH_PATTERN,),
                    # The page copies ``?token=…`` into this cookie, so fetch and EventSource
                    # (which cannot set an Authorization header) carry the same token.
                    cookie_name=UI_TOKEN_COOKIE,
                )
            ]
            if config.auth_token
            else []
        )
        + [
            # An external caller that starts a run with a W3C ``traceparent`` makes that context
            # current, so ``create_langfuse_exporter`` joins the caller's trace instead of opening
            # an unrelated one. Browser requests carry no traceparent and are unaffected.
            Middleware(TracePropagationMiddleware)
        ],
    )
    install_health_routes(
        app,
        service="ran-energy-saving-web",
        checks={"client_store": lambda: sqlite_ready(config.client_db_path)},
    )
    install_metrics_route(app)
    app.state.run_manager = manager
    app.state.client_store = client_store
    app.state.config = config
    app.state.tester_batches = tester_batches
    return app


def _now_iso() -> str:
    """Return the current UTC time in ISO 8601 form (UI timestamps)."""
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _modify_request(config: SampleConfig, language: str) -> str:
    """Return the Modify request shown for a selected running Task, vendor-profiled."""
    from demo.catalog import profile_request_text

    region = config.nms_region if config.discovery == DISCOVERY_NMS else ""
    sites = config.nms_sites if config.discovery == DISCOVERY_NMS else ()
    return profile_request_text(
        resolve_modify_request(language),
        language=language,
        region=region,
        sites=sites,
    )


def _scenario_request(catalog: RunCatalog, name: str, language: str) -> str:
    """Return the request shown for one scenario.

    The catalog carries the vendor profile, so the page pre-fills exactly the text the client will
    send. The page never shows a neutral name that the wire later replaces.
    """
    try:
        return catalog.spec(name, language=language).user_request
    except KeyError:
        return resolve_example_request(name, language)


def _sse_data(message: dict[str, object]) -> dict[str, str]:
    """Render one SSE message payload."""
    return {"data": json.dumps(message, ensure_ascii=False)}


#: Default channel label of each run source kind.
_SOURCE_CHANNELS: Final[dict[str, str]] = {
    "presentation": "presentation-page",
    "explorer": "explorer-page",
    "tester": "tester-console",
    "gateway": "a2a-gateway",
    "cli": "cli",
    "api": "http-api",
}

#: Longest header value kept in a run source document.
_SOURCE_TEXT_LIMIT: Final[int] = 1000

#: Query keys stripped from a stored referer so a shared `?token=…` link never leaks.
_SOURCE_SECRET_KEYS: Final[frozenset[str]] = frozenset({"token", "auth", "authorization"})


def _redacted_url(value: str | None) -> str | None:
    """Return one URL with credential-like query keys removed and a bounded length."""
    if not value:
        return None
    try:
        parsed = urlsplit(value)
    except ValueError:
        return value[:_SOURCE_TEXT_LIMIT]
    if not parsed.query:
        return value[:_SOURCE_TEXT_LIMIT]
    pairs = [
        (key, item)
        for key, item in parse_qsl(parsed.query, keep_blank_values=True)
        if key.lower() not in _SOURCE_SECRET_KEYS
    ]
    return urlunsplit(parsed._replace(query=urlencode(pairs)))[:_SOURCE_TEXT_LIMIT]


def _request_source(request: Request, declared: object) -> dict[str, object]:
    """Build the run source document from the page declaration and HTTP metadata.

    The page declares its kind (``presentation`` / ``explorer``); the server adds the network
    origin so the internal console can tell a page run from a scripted API call. Header values are
    bounded, and a referer's ``token`` query parameter is removed before storage.
    """
    base: dict[str, object] = {}
    kind: object = None
    if isinstance(declared, Mapping):
        base = {str(key): value for key, value in declared.items() if isinstance(value, (str, int, float, bool))}
        kind = base.get("kind")
    elif isinstance(declared, str):
        kind = declared
    if kind is not None:
        base["kind"] = str(kind)
    headers = request.headers
    if request.client is not None and request.client.host:
        base["remoteAddress"] = request.client.host
    for field, header in (
        ("realIp", "x-real-ip"),
        ("forwardedFor", "x-forwarded-for"),
        ("userAgent", "user-agent"),
        ("origin", "origin"),
        ("acceptLanguage", "accept-language"),
        ("host", "host"),
    ):
        value = headers.get(header)
        if value:
            base[field] = value[:_SOURCE_TEXT_LIMIT]
    referer = _redacted_url(headers.get("referer"))
    if referer:
        base["referer"] = referer
    request_id = headers.get("x-request-id") or headers.get("x-correlation-id")
    if request_id:
        base["requestId"] = request_id[:_SOURCE_TEXT_LIMIT]
    base["entryPath"] = request.url.path
    normalized = normalize_run_source(base)
    normalized.setdefault("channel", _SOURCE_CHANNELS.get(str(normalized.get("kind")), "http-api"))
    return normalized


def _span_seq(span: Mapping[str, object]) -> int:
    """Return the ``seq`` of one span, ``0`` when absent."""
    value = span.get("seq")
    return value if isinstance(value, int) else 0


def _span_from_transcript_event(event: Mapping[str, object]) -> dict[str, object]:
    """Map one persisted transcript trace row back to the ``trace.json`` span shape."""
    return {
        "spanId": event.get("spanId"),
        "parentSpanId": event.get("parentSpanId"),
        "seq": event.get("seq"),
        "phase": event.get("phase"),
        "phaseStep": event.get("phaseStep"),
        "kind": event.get("kind"),
        "title": event.get("summary"),
        "actor": event.get("actor") or "server",
        "side": event.get("side") or "server",
        "direction": event.get("direction"),
        "status": event.get("status"),
        "round": event.get("round"),
        "contextId": event.get("contextId"),
        "taskId": event.get("taskId"),
        "detail": event.get("detail"),
        "startedAt": event.get("ts"),
        "durationMs": event.get("durationMs"),
    }


def _trace_token_ok(request: Request) -> bool:
    """Check the local trace token (``A2AT_SAMPLE_TRACE_TOKEN``), failing closed.

    The server process only posts server spans when ``run_demo.py --ui`` configured the same token
    for both processes (design §4/§7). Without a configured token the ingestion route is disabled
    instead of open, so a stray process cannot inject rows into a run timeline.
    """
    expected = os.environ.get("A2AT_SAMPLE_TRACE_TOKEN", "").strip()
    if not expected:
        return False
    supplied = request.headers.get("x-a2at-trace-token", "").strip()
    if supplied == expected:
        return True
    authorization = request.headers.get("authorization", "")
    return authorization == f"Bearer {expected}"


def _coerce_trace_payload(body: object) -> list[dict[str, object]] | None:
    """Return the spans of one JSON list/dict body, ``None`` when malformed."""
    if isinstance(body, list):
        if not all(isinstance(item, Mapping) for item in body):
            return None
        return [dict(item) for item in body if isinstance(item, Mapping)]
    if isinstance(body, Mapping):
        nested = body.get("spans")
        if isinstance(nested, list):
            return _coerce_trace_payload(nested)
        return [dict(body)]
    return None


def _validate_trace_span(span: Mapping[str, object], run_id: str) -> str | None:
    """Validate one ingested server span; return a readable problem, ``None`` when valid.

    The UI is the single owner of the ordering and of the phase vocabulary, so it rejects a
    malformed envelope early: an unknown phase or schema version, a non-string kind/title, a
    ``detail`` that is not an object, or a ``runId`` pointing at another run.
    """
    schema_version = span.get("schemaVersion")
    if schema_version is not None and schema_version != SCHEMA_VERSION:
        return f"unsupported schemaVersion {schema_version!r}"
    span_run_id = span.get("runId")
    if span_run_id not in (None, "") and span_run_id != run_id:
        return f"runId {span_run_id!r} does not match the run"
    phase = span.get("phase")
    # ``task_modify`` is an optional two-phase phase; it is not part of every run's phase set but
    # is a valid span phase once a Modify continuation starts.
    allowed_phases = (*PHASES, PHASE_TASK_MODIFY)
    if not isinstance(phase, str) or phase not in allowed_phases:
        return f"unknown phase {phase!r}; expected one of {allowed_phases}"
    kind = span.get("kind")
    if not isinstance(kind, str) or not kind.strip():
        return "kind must be a non-empty string"
    for field_name in ("spanId", "title", "stage", "actor", "side", "status"):
        value = span.get(field_name)
        if value is not None and not isinstance(value, str):
            return f"{field_name} must be a string"
    detail = span.get("detail")
    if detail is not None and not isinstance(detail, Mapping):
        return "detail must be an object"
    duration = span.get("durationMs")
    if duration is not None and not isinstance(duration, (int, float)):
        return "durationMs must be a number"
    return None


__all__ = [
    "RunConflictError",
    "RunManager",
    "RunState",
    "create_app",
]
