"""Unified configuration of the RAN energy-saving ADK sample.

Values resolve as process environment > sample ``.env`` > defaults, the standard dotenv order. A
shell export therefore overrides the file, and the launcher writes the merged values into the
per-run ``.env`` so file readers (the SDK facade, helper scripts) see the same configuration. Model
construction lives in :mod:`shared.model_factory`, so this module stays a pure, injectable value
object.
"""

from __future__ import annotations

import math
import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from dotenv import dotenv_values

from shared.run_constants import LANGUAGE_ZH, SUPPORTED_LANGUAGES

#: Model modes.
MODEL_MODE_LIVE = "live"
MODEL_MODE_OFFLINE = "offline"
SUPPORTED_MODEL_MODES = (MODEL_MODE_LIVE, MODEL_MODE_OFFLINE)
ModelMode = Literal["live", "offline"]

#: Discovery modes.
DISCOVERY_REGISTRY = "registry"
DISCOVERY_DIRECT = "direct"
#: Direct A2A surface of a vendor NMS that publishes no AgentCard.
DISCOVERY_NMS = "nms"
SUPPORTED_DISCOVERY_MODES = (DISCOVERY_REGISTRY, DISCOVERY_DIRECT, DISCOVERY_NMS)

#: Transport modes.
TRANSPORT_SSE = "sse"
TRANSPORT_WEBHOOK = "webhook"
SUPPORTED_TRANSPORTS = (TRANSPORT_SSE, TRANSPORT_WEBHOOK)

#: Intent handling. ``processed`` puts the business intent layer before the protocol mapping;
#: ``raw`` keeps the previous direct-text behavior.
INTENT_MODE_PROCESSED = "processed"
INTENT_MODE_RAW = "raw"
SUPPORTED_INTENT_MODES = (INTENT_MODE_PROCESSED, INTENT_MODE_RAW)

#: Conversation fields of a pure A2A follow-up. The vendor may reject ``taskId``; the yellow-zone
#: run can switch to ``context`` without a code change.
PURE_FOLLOWUP_BOTH = "both"
PURE_FOLLOWUP_TASK = "task"
PURE_FOLLOWUP_CONTEXT = "context"
SUPPORTED_PURE_FOLLOWUPS = (PURE_FOLLOWUP_BOTH, PURE_FOLLOWUP_TASK, PURE_FOLLOWUP_CONTEXT)

#: ``.env`` keys.
MODEL_MODE_ENV_KEY = "A2AT_SAMPLE_MODEL_MODE"
LANGUAGE_ENV_KEY = "A2AT_LANGUAGE"
HOST_ENV_KEY = "A2AT_SAMPLE_HOST"
PORT_ENV_KEY = "A2AT_SAMPLE_PORT"
REGISTRY_HOST_ENV_KEY = "REGISTRY_CENTER_HOST"
REGISTRY_PORT_ENV_KEY = "REGISTRY_CENTER_PORT"
WEBHOOK_HOST_ENV_KEY = "A2AT_SAMPLE_WEBHOOK_HOST"
WEBHOOK_PORT_ENV_KEY = "A2AT_SAMPLE_WEBHOOK_PORT"
WEBHOOK_TOKEN_ENV_KEY = "A2AT_SAMPLE_WEBHOOK_TOKEN"
WEBHOOK_IDLE_TIMEOUT_ENV_KEY = "A2AT_SAMPLE_WEBHOOK_IDLE_TIMEOUT"
WEBHOOK_PUBLIC_URL_ENV_KEY = "A2AT_SAMPLE_WEBHOOK_PUBLIC_URL"
WEBHOOK_TLS_CERT_ENV_KEY = "A2AT_SAMPLE_WEBHOOK_TLS_CERT"
WEBHOOK_TLS_KEY_ENV_KEY = "A2AT_SAMPLE_WEBHOOK_TLS_KEY"
WEBHOOK_AUTH_CREDENTIALS_ENV_KEY = "A2AT_SAMPLE_WEBHOOK_AUTH_CREDENTIALS"
ADK_MODEL_PROVIDER_ENV_KEY = "ADK_MODEL_PROVIDER"
ADK_MODEL_NAME_ENV_KEY = "ADK_MODEL_NAME"
ADK_MODEL_API_BASE_ENV_KEY = "ADK_MODEL_API_BASE"
ADK_MODEL_API_KEY_ENV_KEY = "ADK_MODEL_API_KEY"
DISCOVERY_ENV_KEY = "A2AT_SAMPLE_DISCOVERY"
TRANSPORT_ENV_KEY = "A2AT_SAMPLE_TRANSPORT"
INTENT_MODE_ENV_KEY = "A2AT_SAMPLE_INTENT_MODE"
#: Directory of the run transcripts, NMS wire logs and the UI/SQLite history.
RUNS_DIR_ENV_KEY = "A2AT_SAMPLE_RUNS_DIR"
#: Which conversation fields a pure A2A follow-up carries (``both`` / ``task`` / ``context``).
PURE_FOLLOWUP_ENV_KEY = "A2AT_SAMPLE_PURE_FOLLOWUP"
HTTP_TIMEOUT_ENV_KEY = "A2AT_SAMPLE_HTTP_TIMEOUT_SECONDS"
LLM_TIMEOUT_ENV_KEY = "A2AT_SAMPLE_LLM_TIMEOUT_SECONDS"
LLM_MAX_ATTEMPTS_ENV_KEY = "A2AT_SAMPLE_LLM_MAX_ATTEMPTS"
TASK_DB_ENV_KEY = "A2AT_SAMPLE_TASK_DB"
CLIENT_DB_ENV_KEY = "A2AT_SAMPLE_CLIENT_DB"
CLIENT_MEMORY_TURNS_ENV_KEY = "A2AT_SAMPLE_CLIENT_MEMORY_TURNS"
ADK_SESSION_RETENTION_DAYS_ENV_KEY = "A2AT_SAMPLE_ADK_SESSION_RETENTION_DAYS"
GATEWAY_HOST_ENV_KEY = "A2AT_SAMPLE_GATEWAY_HOST"
GATEWAY_PORT_ENV_KEY = "A2AT_SAMPLE_GATEWAY_PORT"
GATEWAY_REGISTER_ENV_KEY = "A2AT_SAMPLE_GATEWAY_REGISTER"
GATEWAY_SCENARIO_ENV_KEY = "A2AT_SAMPLE_GATEWAY_SCENARIO"
GATEWAY_TASK_DB_ENV_KEY = "A2AT_SAMPLE_GATEWAY_TASK_DB"
AUTH_TOKEN_ENV_KEY = "A2AT_SAMPLE_AUTH_TOKEN"
SERVER_AUTH_TOKEN_ENV_KEY = "A2AT_SAMPLE_SERVER_AUTH_TOKEN"

#: Langfuse observability of the client run (see :mod:`shared.langfuse_tracing`).
LANGFUSE_PUBLIC_KEY_ENV_KEY = "LANGFUSE_PUBLIC_KEY"
LANGFUSE_SECRET_KEY_ENV_KEY = "LANGFUSE_SECRET_KEY"
LANGFUSE_BASE_URL_ENV_KEY = "LANGFUSE_BASE_URL"
LANGFUSE_ENABLED_ENV_KEY = "A2AT_SAMPLE_LANGFUSE_ENABLED"
LANGFUSE_CAPTURE_CONTENT_ENV_KEY = "A2AT_SAMPLE_LANGFUSE_CAPTURE_CONTENT"
LANGFUSE_ENVIRONMENT_ENV_KEY = "A2AT_SAMPLE_LANGFUSE_ENVIRONMENT"

#: Vendor NMS integration (``A2AT_SAMPLE_DISCOVERY=nms``).
#: ``A2AT_SAMPLE_NMS_HOST`` is the single value most deployments set; the two full URLs are
#: derived from it with the standard 910 ports, and the explicit keys below override them.
NMS_HOST_ENV_KEY = "A2AT_SAMPLE_NMS_HOST"
NMS_SCHEME_ENV_KEY = "A2AT_SAMPLE_NMS_SCHEME"
NMS_A2A_PORT_ENV_KEY = "A2AT_SAMPLE_NMS_A2A_PORT"
NMS_TOKEN_PORT_ENV_KEY = "A2AT_SAMPLE_NMS_TOKEN_PORT"
NMS_A2A_BASE_ENV_KEY = "A2AT_SAMPLE_NMS_A2A_BASE"
NMS_TOKEN_URL_ENV_KEY = "A2AT_SAMPLE_NMS_TOKEN_URL"
NMS_TOKEN_USER_ENV_KEY = "A2AT_SAMPLE_NMS_TOKEN_USER"
NMS_TOKEN_VALUE_ENV_KEY = "A2AT_SAMPLE_NMS_TOKEN_VALUE"
NMS_VERIFY_TLS_ENV_KEY = "A2AT_SAMPLE_NMS_VERIFY_TLS"
NMS_IDLE_TIMEOUT_ENV_KEY = "A2AT_SAMPLE_NMS_IDLE_TIMEOUT_SECONDS"
NMS_READ_AFTER_SEND_ENV_KEY = "A2AT_SAMPLE_NMS_READ_AFTER_SEND"
NMS_REQUEST_TIMEOUT_ENV_KEY = "A2AT_SAMPLE_NMS_REQUEST_TIMEOUT_SECONDS"
#: Region the vendor NMS serves. When set, ``--nms`` overrides the scenario region with it.
NMS_REGION_ENV_KEY = "A2AT_SAMPLE_NMS_REGION"
#: Comma-separated site names the vendor NMS serves. ``--nms`` overrides the scenario site list.
NMS_SITES_ENV_KEY = "A2AT_SAMPLE_NMS_SITES"
#: Whether the raw NMS exchange is written to ``runs/<runId>/wire.jsonl``.
NMS_WIRE_LOG_ENV_KEY = "A2AT_SAMPLE_NMS_WIRE_LOG"
PLANNER_MODEL_ENV_KEY = "A2AT_SAMPLE_PLANNER_MODEL"
PLANNER_API_BASE_ENV_KEY = "A2AT_SAMPLE_PLANNER_API_BASE"
PLANNER_API_KEY_ENV_KEY = "A2AT_SAMPLE_PLANNER_API_KEY"
PLANNER_TIMEOUT_ENV_KEY = "A2AT_SAMPLE_PLANNER_TIMEOUT_SECONDS"
PLANNER_MAX_STEPS_ENV_KEY = "A2AT_SAMPLE_PLANNER_MAX_STEPS"

#: Defaults matching ``env.example``.
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8000
DEFAULT_REGISTRY_HOST = "127.0.0.1"
DEFAULT_REGISTRY_PORT = 5001
DEFAULT_WEBHOOK_PORT = 9000
DEFAULT_WEBHOOK_TOKEN = "demo-webhook-token"

#: Client HTTP budget of one A2A request (``ClientCallContext.timeout`` and the httpx client).
#: A live-mode server gate may perform several LLM calls before sending any event, so the budget
#: must exceed the server-side worst case (LLM timeout × calls; the sample disables transport
#: retries). 300s covers two 120s gate calls plus ADK agent turns.
DEFAULT_HTTP_TIMEOUT_SECONDS = 300.0

#: Backstop idle timeout of a webhook phase; it must not end a phase before the client budget, so
#: it follows the HTTP budget by default.
DEFAULT_WEBHOOK_IDLE_TIMEOUT = int(DEFAULT_HTTP_TIMEOUT_SECONDS)

#: Live LLM budget of one call (SDK structured calls and ADK LiteLlm turns). A local live smoke
#: observed a 58s validation and a >90s accept validation; 120s leaves headroom for slower
#: endpoints while the 300s client budget still covers the gate plus ADK agent turns.
DEFAULT_LLM_TIMEOUT_SECONDS = 120.0

#: Live SDK LLM attempt limit per retryable step (the SDK accepts 1..10).
DEFAULT_LLM_MAX_ATTEMPTS = 1

#: Inclusive bounds of ``A2AT_SAMPLE_LLM_MAX_ATTEMPTS`` (mirrors the SDK's clamping bounds).
LLM_MAX_ATTEMPTS_LOWER_BOUND = 1
LLM_MAX_ATTEMPTS_UPPER_BOUND = 10

#: Recent conversation turns injected into the next client run.
DEFAULT_CLIENT_MEMORY_TURNS = 6

#: Days the client keeps a decision-agent session as an audit record before the sweep removes it.
DEFAULT_ADK_SESSION_RETENTION_DAYS = 7

#: A2A gateway defaults (the client agent's inbound A2A surface).
DEFAULT_GATEWAY_PORT = 8001
DEFAULT_GATEWAY_SCENARIO = "information"

#: Vendor NMS defaults. The A2A stream sends heartbeat comments every 15 s; the client treats a
#: 45 s window without a ``data:`` frame as idle and switches to task polling. The window is wider
#: than one heartbeat because heartbeats do not count as progress.
DEFAULT_NMS_IDLE_TIMEOUT_SECONDS = 45.0
DEFAULT_NMS_REQUEST_TIMEOUT_SECONDS = 120.0
DEFAULT_NMS_POLL_INTERVAL_SECONDS = 1.0

#: Region and sites of the 910 test environment. They are the defaults of an ``nms`` run so the
#: sample never sends the neutral demo region to the vendor; the two keys override them.
DEFAULT_NMS_REGION = "A2ATestRegion"
DEFAULT_NMS_SITES: tuple[str, ...] = ("NRES_XCS_6102937", "LTE_26441_LNR_OCL")

#: Standard vendor NMS surface, used to derive both URLs from ``A2AT_SAMPLE_NMS_HOST``.
DEFAULT_NMS_SCHEME = "https"
DEFAULT_NMS_A2A_PORT = 31127
DEFAULT_NMS_TOKEN_PORT = 27417
DEFAULT_NMS_A2A_PATH = "/a2a/json"
DEFAULT_NMS_TOKEN_PATH = "/rest/plat/smapp/v1/sessions"

#: LangGraph planner defaults (the server's post-accept execution agent).
DEFAULT_PLANNER_MAX_STEPS = 12
DEFAULT_ADK_MODEL_PROVIDER = "litellm"
DEFAULT_ADK_MODEL_NAME = "openai/mass/volcengine/deepseek-v4.1-flash"
DEFAULT_ADK_MODEL_API_BASE = "http://127.0.0.1:31943/v1"
DEFAULT_ADK_MODEL_API_KEY = "not-needed"

#: Langfuse Cloud endpoint and environment label of the client observability export.
DEFAULT_LANGFUSE_BASE_URL = "https://us.cloud.langfuse.com"
DEFAULT_LANGFUSE_ENVIRONMENT = "demo"

#: Sample root (the directory that owns ``runs/``). Relative runs directories resolve here.
SAMPLE_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RUNS_DIR = SAMPLE_ROOT / "runs"


def _resolved_dir(values: dict[str, str | None], key: str, default: Path) -> str:
    """Resolve one directory key; a relative value is relative to the sample root."""
    raw_value = _env_value(values, key, str(default))
    path = Path(raw_value).expanduser()
    if not path.is_absolute():
        path = SAMPLE_ROOT / path
    return str(path.resolve())


def _pure_followup(values: dict[str, str | None]) -> str:
    """Resolve the pure follow-up field selection, rejecting an unsupported value."""
    value = _env_value(values, PURE_FOLLOWUP_ENV_KEY, PURE_FOLLOWUP_BOTH).lower()
    if value not in SUPPORTED_PURE_FOLLOWUPS:
        raise ValueError(
            f"Unsupported {PURE_FOLLOWUP_ENV_KEY}: {value}; "
            f"expected one of {SUPPORTED_PURE_FOLLOWUPS}"
        )
    return value


#: Prefixes of the keys the sample owns. A shell export with one of these prefixes overrides the
#: source ``.env``; the launcher also merges them into the per-run ``.env`` so every file reader
#: (the SDK facade, helper scripts) sees the same effective value.
SAMPLE_ENV_PREFIXES = ("A2AT_", "ADK_", "LANGFUSE_", "REGISTRY_", "OTEL_")


def merge_sample_process_env(values: Mapping[str, str]) -> dict[str, str]:
    """Overlay every sample-prefixed process variable on a ``.env`` mapping.

    The process environment wins over the file, matching the standard dotenv semantics and the
    sample's precedence rule: CLI arguments > shell environment > per-run ``.env`` > source
    ``.env`` > defaults. Keys outside the sample prefixes (``PATH`` and friends) are never copied.
    """
    merged = dict(values)
    for key, value in os.environ.items():
        if value.strip() and key.startswith(SAMPLE_ENV_PREFIXES):
            merged[key] = value
    return merged


def _env_value(values: dict[str, str | None], key: str, default: str) -> str:
    """Resolve one key: process environment, then the ``.env`` values, then a default."""
    for candidate in (os.environ.get(key), values.get(key)):
        if candidate is not None and str(candidate).strip():
            return str(candidate).strip()
    return default


def _env_int(values: dict[str, str | None], key: str, default: int) -> int:
    """Resolve one integer key, raising a readable error on invalid input."""
    raw_value = _env_value(values, key, str(default))
    try:
        return int(raw_value)
    except ValueError as exc:
        raise ValueError(f"Invalid integer value for {key}: {raw_value}") from exc


def _env_float(values: dict[str, str | None], key: str, default: float) -> float:
    """Resolve one float key, raising a readable error on invalid input."""
    raw_value = _env_value(values, key, str(default))
    try:
        return float(raw_value)
    except ValueError as exc:
        raise ValueError(f"Invalid numeric value for {key}: {raw_value}") from exc


def _positive_seconds(values: dict[str, str | None], key: str, default: float) -> float:
    """Resolve one strictly positive, finite duration in seconds."""
    value = _env_float(values, key, default)
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"Invalid duration for {key}: {value:g}; expected a positive number of seconds")
    return value


def _non_negative_int(values: dict[str, str | None], key: str, default: int) -> int:
    """Resolve one non-negative integer key, raising a readable error on invalid input."""
    value = _env_int(values, key, default)
    if value < 0:
        raise ValueError(f"Invalid value for {key}: {value}; expected a non-negative integer")
    return value


def _positive_int(values: dict[str, str | None], key: str, default: int) -> int:
    """Resolve one strictly positive integer key."""
    value = _env_int(values, key, default)
    if value < 1:
        raise ValueError(f"Invalid value for {key}: {value}; expected a positive integer")
    return value


def _llm_max_attempts(values: dict[str, str | None]) -> int:
    """Resolve the live LLM attempt limit, validated against the inclusive SDK bounds."""
    value = _env_int(values, LLM_MAX_ATTEMPTS_ENV_KEY, DEFAULT_LLM_MAX_ATTEMPTS)
    if not LLM_MAX_ATTEMPTS_LOWER_BOUND <= value <= LLM_MAX_ATTEMPTS_UPPER_BOUND:
        raise ValueError(
            f"Invalid value for {LLM_MAX_ATTEMPTS_ENV_KEY}: {value}; expected "
            f"{LLM_MAX_ATTEMPTS_LOWER_BOUND}..{LLM_MAX_ATTEMPTS_UPPER_BOUND}"
        )
    return value


def _env_bool(values: dict[str, str | None], key: str, default: bool) -> bool:
    """Resolve one boolean key (``1/true/yes/on`` and ``0/false/no/off``)."""
    raw_value = _env_value(values, key, "true" if default else "false").lower()
    if raw_value in ("1", "true", "yes", "on"):
        return True
    if raw_value in ("0", "false", "no", "off"):
        return False
    raise ValueError(f"Invalid boolean value for {key}: {raw_value}")


def _env_optional_bool(values: dict[str, str | None], key: str) -> bool | None:
    """Resolve a tri-state key: unset means "auto", otherwise a validated boolean."""
    raw_value = os.environ.get(key)
    if raw_value is None or not str(raw_value).strip():
        raw_value = values.get(key)
    if raw_value is None or not str(raw_value).strip():
        return None
    return _env_bool(values, key, True)


def _nms_sites(values: dict[str, str | None], default: tuple[str, ...]) -> tuple[str, ...]:
    """Resolve the comma-separated site names the vendor NMS serves."""
    raw_value = _env_value(values, NMS_SITES_ENV_KEY, ",".join(default))
    return tuple(item.strip() for item in raw_value.split(",") if item.strip())


def _webhook_auth_credentials(values: dict[str, str | None]) -> str:
    """Resolve the receiver's Basic-auth credentials (``user:password``), empty when unset."""
    raw_value = _env_value(values, WEBHOOK_AUTH_CREDENTIALS_ENV_KEY, "")
    if raw_value and ":" not in raw_value:
        raise ValueError(f"Invalid value for {WEBHOOK_AUTH_CREDENTIALS_ENV_KEY}; expected user:password")
    return raw_value


def _optional_absolute_path(values: dict[str, str | None], key: str) -> str | None:
    """Resolve an optional absolute path key, rejecting relative values."""
    raw_value = _env_value(values, key, "")
    if not raw_value:
        return None
    path = Path(raw_value).expanduser()
    if not path.is_absolute():
        raise ValueError(f"Invalid value for {key}: {raw_value}; expected an absolute path")
    return str(path)


@dataclass(frozen=True)
class SampleConfig:
    """Resolved sample configuration.

    Attributes:
        model_mode: ``live`` (LiteLlm) or ``offline`` (scripted LLM + fake ADK model).
        language: Process-level language (``en-US`` / ``zh-CN``).
        host: Sample bind host.
        port: Sample bind port.
        registry_url: Registry center base URL.
        webhook_host: Webhook receiver bind host; use a reachable LAN address for a real NMS.
        webhook_port: Webhook receiver bind port (``0`` selects an ephemeral port).
        webhook_token: Push token checked by the webhook receiver.
        webhook_idle_timeout: Webhook idle backstop timeout in seconds.
        webhook_public_url: callback URL registered with the server instead of the bind address
            (the real NMS requires https and reaches the client through a gateway address).
        webhook_tls_cert: PEM certificate of the receiver's TLS listener; empty serves plain http.
        webhook_tls_key: private key matching ``webhook_tls_cert``.
        webhook_auth_credentials: ``user:password`` the pushed requests must carry as Basic auth;
            empty accepts any caller with the push token.
        adk_model_provider: ADK model provider name (``litellm``).
        adk_model_name: LiteLlm model id.
        adk_model_api_base: OpenAI-compatible endpoint of the ADK model.
        adk_model_api_key: API key of the ADK model endpoint.
        discovery: Discovery mode (``registry`` / ``direct``).
        transport: Event transport (``sse`` / ``webhook``).
        intent_mode: Intent handling (``processed`` / ``raw``).
        http_timeout_seconds: Client HTTP budget of one A2A request, in seconds.
        llm_timeout_seconds: Live LLM budget of one call (SDK structured call or ADK turn), in seconds.
        llm_max_attempts: Live SDK LLM attempt limit per retryable step.
        task_db_path: Absolute SQLite path of the persisted A2A Task store; ``None`` keeps the
            in-memory store (tests and ephemeral runs).
        client_db_path: Absolute SQLite path of the client conversation/run store; ``None`` keeps
            the client state in memory only.
        client_memory_turns: Recent conversation turns injected into the next run's prompts.
        adk_session_retention_days: Days a decision-agent session is kept as an audit record before
            the sweep removes it.
        gateway_host: bind host of the optional inbound A2A gateway.
        gateway_port: bind port of the optional inbound A2A gateway.
        gateway_register: whether the gateway registers its AgentCard in the registry center.
        gateway_scenario: scenario used for an inbound gateway request.
        runs_dir: directory of run transcripts, NMS wire logs and the UI history.
        pure_followup: conversation fields of a pure A2A follow-up (``both`` / ``task`` / ``context``).
        gateway_task_db_path: Absolute SQLite path of the inbound gateway Task store and its push
            notification registrations; ``None`` keeps both in memory.
        auth_token: bearer token required by the Web UI API and the inbound gateway; empty keeps
            both open (loopback-only deployments).
        server_auth_token: bearer token the client presents to the A2A server and the server
            requires on every route except its AgentCard and health endpoints; empty keeps the
            server open (loopback-only deployments).
        nms_a2a_base: full A2A surface URL of a vendor NMS (``.../a2a/json``); required by the
            ``nms`` discovery mode and used as the static AgentCard interface.
        nms_token_url: session/token endpoint of the vendor NMS.
        nms_token_user: northbound user name presented to the token endpoint.
        nms_token_value: password presented to the token endpoint; never logged or traced.
        nms_verify_tls: whether the NMS calls verify TLS certificates; ``False`` by default because
            the test environment uses a self-signed certificate.
        nms_idle_timeout: seconds without a ``data:`` frame before the client leaves the SSE stream
            and polls the task instead. Heartbeats do not reset this window.
        nms_read_after_send: whether a negotiation response keeps reading its stream for the next
            proposal (the vendor opens a new negotiation id on the same context id).
        nms_request_timeout: per-request budget of one NMS A2A call.
        nms_poll_interval: seconds between task polls while waiting for a terminal state.
        nms_region: region the vendor NMS serves; when set, ``--nms`` overrides the scenario
            region with it.
        nms_sites: site names the vendor NMS serves; when set, ``--nms`` replaces the scenario site
            list with it.
        nms_wire_log: whether the raw NMS exchange is written to ``wire.jsonl``.
        planner_model: LangChain model id of the server's LangGraph planner.
        planner_api_base: OpenAI-compatible endpoint of the planner.
        planner_api_key: API key of the planner endpoint.
        planner_timeout_seconds: per-call planner budget.
        planner_max_steps: LangGraph recursion limit of one execution.
        langfuse_public_key: Langfuse public key; with the secret key it enables the client export.
        langfuse_secret_key: Langfuse secret key; never logged or traced.
        langfuse_base_url: Langfuse Cloud or self-hosted base URL.
        langfuse_enabled: explicit on/off switch; ``None`` enables when both keys are present.
        langfuse_capture_content: whether prompt/response text is sent, or only metadata.
        langfuse_environment: Langfuse environment label (``demo`` by default).
    """

    model_mode: ModelMode
    language: str
    host: str
    port: int
    registry_url: str
    webhook_port: int
    webhook_token: str
    webhook_idle_timeout: int
    adk_model_provider: str
    adk_model_name: str
    adk_model_api_base: str
    adk_model_api_key: str
    discovery: str = DISCOVERY_REGISTRY
    transport: str = TRANSPORT_SSE
    intent_mode: str = INTENT_MODE_PROCESSED
    webhook_host: str = DEFAULT_HOST
    webhook_public_url: str = ""
    webhook_tls_cert: str = ""
    webhook_tls_key: str = ""
    webhook_auth_credentials: str = ""
    http_timeout_seconds: float = DEFAULT_HTTP_TIMEOUT_SECONDS
    llm_timeout_seconds: float = DEFAULT_LLM_TIMEOUT_SECONDS
    llm_max_attempts: int = DEFAULT_LLM_MAX_ATTEMPTS
    task_db_path: str | None = None
    client_db_path: str | None = None
    client_memory_turns: int = DEFAULT_CLIENT_MEMORY_TURNS
    adk_session_retention_days: int = DEFAULT_ADK_SESSION_RETENTION_DAYS
    gateway_host: str = DEFAULT_HOST
    gateway_port: int = DEFAULT_GATEWAY_PORT
    gateway_register: bool = False
    gateway_scenario: str = DEFAULT_GATEWAY_SCENARIO
    runs_dir: str = str(DEFAULT_RUNS_DIR)
    pure_followup: str = PURE_FOLLOWUP_BOTH
    gateway_task_db_path: str | None = None
    auth_token: str = ""
    server_auth_token: str = ""
    nms_a2a_base: str = ""
    nms_token_url: str = ""
    nms_token_user: str = ""
    nms_token_value: str = ""
    nms_verify_tls: bool = False
    nms_idle_timeout: float = DEFAULT_NMS_IDLE_TIMEOUT_SECONDS
    nms_read_after_send: bool = True
    nms_request_timeout: float = DEFAULT_NMS_REQUEST_TIMEOUT_SECONDS
    nms_poll_interval: float = DEFAULT_NMS_POLL_INTERVAL_SECONDS
    nms_region: str = ""
    nms_sites: tuple[str, ...] = ()
    nms_wire_log: bool = True
    planner_model: str = ""
    planner_api_base: str = ""
    planner_api_key: str = ""
    planner_timeout_seconds: float = DEFAULT_LLM_TIMEOUT_SECONDS
    planner_max_steps: int = DEFAULT_PLANNER_MAX_STEPS
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""
    langfuse_base_url: str = DEFAULT_LANGFUSE_BASE_URL
    langfuse_enabled: bool | None = None
    langfuse_capture_content: bool = True
    langfuse_environment: str = DEFAULT_LANGFUSE_ENVIRONMENT

    @property
    def is_offline(self) -> bool:
        """Return whether the sample runs with the scripted LLM and the fake ADK model."""
        return self.model_mode == MODEL_MODE_OFFLINE

    @property
    def langfuse_configured(self) -> bool:
        """Return whether the client Langfuse export is enabled and has both keys."""
        if self.langfuse_enabled is False:
            return False
        return bool(self.langfuse_public_key and self.langfuse_secret_key)

    @property
    def model_label(self) -> str:
        """Return the model-mode label shown by the CLI and the Web UI."""
        return "offline (scripted)" if self.is_offline else f"live ({self.adk_model_name})"


def load_config(*, env_path: Path | None = None) -> SampleConfig:
    """Load the sample configuration from ``.env`` (defaults to ``$CWD/.env``).

    A process environment variable always wins over the file value, then the file value wins over
    the built-in default. This is what makes ``export ADK_MODEL_NAME=...`` in a launcher script
    effective even when the source ``.env`` names another model.

    Raises:
        ValueError: when ``A2AT_SAMPLE_MODEL_MODE``, ``A2AT_LANGUAGE``, a port, a timeout, the LLM
            attempt limit, a discovery / transport / intent-mode key, or ``A2AT_SAMPLE_TASK_DB``
            carries an unsupported value.
    """
    resolved = env_path or Path.cwd() / ".env"
    values: dict[str, str | None] = dict(dotenv_values(resolved)) if resolved.exists() else {}

    model_mode = _env_value(values, MODEL_MODE_ENV_KEY, MODEL_MODE_OFFLINE)
    if model_mode not in SUPPORTED_MODEL_MODES:
        raise ValueError(f"Unsupported {MODEL_MODE_ENV_KEY}: {model_mode}; expected one of {SUPPORTED_MODEL_MODES}")

    language = _env_value(values, LANGUAGE_ENV_KEY, LANGUAGE_ZH)
    if language not in SUPPORTED_LANGUAGES:
        raise ValueError(f"Unsupported {LANGUAGE_ENV_KEY}: {language}; expected one of {SUPPORTED_LANGUAGES}")

    discovery = _env_value(values, DISCOVERY_ENV_KEY, DISCOVERY_REGISTRY)
    if discovery not in SUPPORTED_DISCOVERY_MODES:
        raise ValueError(f"Unsupported {DISCOVERY_ENV_KEY}: {discovery}; expected one of {SUPPORTED_DISCOVERY_MODES}")

    transport = _env_value(values, TRANSPORT_ENV_KEY, TRANSPORT_SSE)
    if transport not in SUPPORTED_TRANSPORTS:
        raise ValueError(f"Unsupported {TRANSPORT_ENV_KEY}: {transport}; expected one of {SUPPORTED_TRANSPORTS}")

    intent_mode = _env_value(values, INTENT_MODE_ENV_KEY, INTENT_MODE_PROCESSED)
    if intent_mode not in SUPPORTED_INTENT_MODES:
        raise ValueError(
            f"Unsupported {INTENT_MODE_ENV_KEY}: {intent_mode}; "
            f"expected one of {SUPPORTED_INTENT_MODES}"
        )

    nms_host = _env_value(values, NMS_HOST_ENV_KEY, "").strip()
    nms_scheme = _env_value(values, NMS_SCHEME_ENV_KEY, DEFAULT_NMS_SCHEME).strip().lower()
    if nms_scheme not in ("http", "https"):
        raise ValueError(f"Unsupported {NMS_SCHEME_ENV_KEY}: {nms_scheme}; expected http or https")
    nms_a2a_port = _env_int(values, NMS_A2A_PORT_ENV_KEY, DEFAULT_NMS_A2A_PORT)
    nms_token_port = _env_int(values, NMS_TOKEN_PORT_ENV_KEY, DEFAULT_NMS_TOKEN_PORT)
    for key, port in (
        (NMS_A2A_PORT_ENV_KEY, nms_a2a_port),
        (NMS_TOKEN_PORT_ENV_KEY, nms_token_port),
    ):
        if not 1 <= port <= 65535:
            raise ValueError(f"Invalid {key}: {port}; expected a port in 1..65535")
    nms_a2a_base = _env_value(values, NMS_A2A_BASE_ENV_KEY, "")
    nms_token_url = _env_value(values, NMS_TOKEN_URL_ENV_KEY, "")
    if nms_host and not nms_a2a_base:
        nms_a2a_base = f"{nms_scheme}://{nms_host}:{nms_a2a_port}{DEFAULT_NMS_A2A_PATH}"
    if nms_host and not nms_token_url:
        nms_token_url = f"{nms_scheme}://{nms_host}:{nms_token_port}{DEFAULT_NMS_TOKEN_PATH}"
    nms_token_user = _env_value(values, NMS_TOKEN_USER_ENV_KEY, "")
    nms_token_value = _env_value(values, NMS_TOKEN_VALUE_ENV_KEY, "")
    if discovery == DISCOVERY_NMS:
        missing = [
            key
            for key, value in (
                (f"{NMS_HOST_ENV_KEY} (or {NMS_A2A_BASE_ENV_KEY})", nms_a2a_base),
                (f"{NMS_HOST_ENV_KEY} (or {NMS_TOKEN_URL_ENV_KEY})", nms_token_url),
                (NMS_TOKEN_USER_ENV_KEY, nms_token_user),
                (NMS_TOKEN_VALUE_ENV_KEY, nms_token_value),
            )
            if not value
        ]
        if missing:
            raise ValueError(f"NMS discovery requires {', '.join(missing)}; set them in the .env used by the run")

    registry_host = _env_value(values, REGISTRY_HOST_ENV_KEY, DEFAULT_REGISTRY_HOST)
    registry_port = _env_int(values, REGISTRY_PORT_ENV_KEY, DEFAULT_REGISTRY_PORT)

    # The scenario catalog is supplied by the composition root, so config only carries the name:
    # validating it against the demo scenarios here would couple configuration to demo assets.
    gateway_scenario = _env_value(values, GATEWAY_SCENARIO_ENV_KEY, DEFAULT_GATEWAY_SCENARIO)

    adk_model_name = _env_value(values, ADK_MODEL_NAME_ENV_KEY, DEFAULT_ADK_MODEL_NAME)
    adk_model_api_base = _env_value(values, ADK_MODEL_API_BASE_ENV_KEY, DEFAULT_ADK_MODEL_API_BASE)
    adk_model_api_key = _env_value(values, ADK_MODEL_API_KEY_ENV_KEY, DEFAULT_ADK_MODEL_API_KEY)
    # The ADK model id is a LiteLLM id ("openai/..."); ChatOpenAI takes the bare model id. The
    # planner keys are independent so a deployment can point the server at a different model.
    planner_model_default = adk_model_name.removeprefix("openai/")

    return SampleConfig(
        model_mode=model_mode,
        language=language,
        host=_env_value(values, HOST_ENV_KEY, DEFAULT_HOST),
        port=_env_int(values, PORT_ENV_KEY, DEFAULT_PORT),
        registry_url=f"http://{registry_host}:{registry_port}",
        webhook_host=_env_value(values, WEBHOOK_HOST_ENV_KEY, DEFAULT_HOST),
        webhook_port=_env_int(values, WEBHOOK_PORT_ENV_KEY, DEFAULT_WEBHOOK_PORT),
        webhook_token=_env_value(values, WEBHOOK_TOKEN_ENV_KEY, DEFAULT_WEBHOOK_TOKEN),
        webhook_idle_timeout=_env_int(values, WEBHOOK_IDLE_TIMEOUT_ENV_KEY, DEFAULT_WEBHOOK_IDLE_TIMEOUT),
        webhook_public_url=_env_value(values, WEBHOOK_PUBLIC_URL_ENV_KEY, ""),
        webhook_tls_cert=_env_value(values, WEBHOOK_TLS_CERT_ENV_KEY, ""),
        webhook_tls_key=_env_value(values, WEBHOOK_TLS_KEY_ENV_KEY, ""),
        webhook_auth_credentials=_webhook_auth_credentials(values),
        adk_model_provider=_env_value(values, ADK_MODEL_PROVIDER_ENV_KEY, DEFAULT_ADK_MODEL_PROVIDER),
        adk_model_name=adk_model_name,
        adk_model_api_base=adk_model_api_base,
        adk_model_api_key=adk_model_api_key,
        discovery=discovery,
        transport=transport,
        intent_mode=intent_mode,
        http_timeout_seconds=_positive_seconds(values, HTTP_TIMEOUT_ENV_KEY, DEFAULT_HTTP_TIMEOUT_SECONDS),
        llm_timeout_seconds=_positive_seconds(values, LLM_TIMEOUT_ENV_KEY, DEFAULT_LLM_TIMEOUT_SECONDS),
        llm_max_attempts=_llm_max_attempts(values),
        task_db_path=_optional_absolute_path(values, TASK_DB_ENV_KEY),
        client_db_path=_optional_absolute_path(values, CLIENT_DB_ENV_KEY),
        client_memory_turns=_non_negative_int(values, CLIENT_MEMORY_TURNS_ENV_KEY, DEFAULT_CLIENT_MEMORY_TURNS),
        adk_session_retention_days=_positive_int(
            values, ADK_SESSION_RETENTION_DAYS_ENV_KEY, DEFAULT_ADK_SESSION_RETENTION_DAYS
        ),
        gateway_host=_env_value(values, GATEWAY_HOST_ENV_KEY, DEFAULT_HOST),
        gateway_port=_env_int(values, GATEWAY_PORT_ENV_KEY, DEFAULT_GATEWAY_PORT),
        gateway_register=_env_bool(values, GATEWAY_REGISTER_ENV_KEY, False),
        gateway_scenario=gateway_scenario,
        runs_dir=_resolved_dir(values, RUNS_DIR_ENV_KEY, DEFAULT_RUNS_DIR),
        pure_followup=_pure_followup(values),
        gateway_task_db_path=_optional_absolute_path(values, GATEWAY_TASK_DB_ENV_KEY),
        auth_token=_env_value(values, AUTH_TOKEN_ENV_KEY, "").strip(),
        server_auth_token=_env_value(values, SERVER_AUTH_TOKEN_ENV_KEY, "").strip(),
        nms_a2a_base=nms_a2a_base.rstrip("/"),
        nms_token_url=nms_token_url,
        nms_token_user=nms_token_user,
        nms_token_value=nms_token_value,
        nms_verify_tls=_env_bool(values, NMS_VERIFY_TLS_ENV_KEY, False),
        nms_idle_timeout=_positive_seconds(values, NMS_IDLE_TIMEOUT_ENV_KEY, DEFAULT_NMS_IDLE_TIMEOUT_SECONDS),
        nms_read_after_send=_env_bool(values, NMS_READ_AFTER_SEND_ENV_KEY, True),
        nms_request_timeout=_positive_seconds(values, NMS_REQUEST_TIMEOUT_ENV_KEY, DEFAULT_NMS_REQUEST_TIMEOUT_SECONDS),
        nms_region=_env_value(
            values,
            NMS_REGION_ENV_KEY,
            DEFAULT_NMS_REGION if discovery == DISCOVERY_NMS else "",
        ).strip(),
        nms_sites=_nms_sites(values, DEFAULT_NMS_SITES if discovery == DISCOVERY_NMS else ()),
        nms_wire_log=_env_bool(values, NMS_WIRE_LOG_ENV_KEY, True),
        planner_model=_env_value(values, PLANNER_MODEL_ENV_KEY, planner_model_default),
        planner_api_base=_env_value(values, PLANNER_API_BASE_ENV_KEY, adk_model_api_base),
        planner_api_key=_env_value(values, PLANNER_API_KEY_ENV_KEY, adk_model_api_key),
        planner_timeout_seconds=_positive_seconds(values, PLANNER_TIMEOUT_ENV_KEY, DEFAULT_LLM_TIMEOUT_SECONDS),
        planner_max_steps=_positive_int(values, PLANNER_MAX_STEPS_ENV_KEY, DEFAULT_PLANNER_MAX_STEPS),
        langfuse_public_key=_env_value(values, LANGFUSE_PUBLIC_KEY_ENV_KEY, "").strip(),
        langfuse_secret_key=_env_value(values, LANGFUSE_SECRET_KEY_ENV_KEY, "").strip(),
        langfuse_base_url=_env_value(values, LANGFUSE_BASE_URL_ENV_KEY, DEFAULT_LANGFUSE_BASE_URL).rstrip("/"),
        langfuse_enabled=_env_optional_bool(values, LANGFUSE_ENABLED_ENV_KEY),
        langfuse_capture_content=_env_bool(values, LANGFUSE_CAPTURE_CONTENT_ENV_KEY, True),
        langfuse_environment=_env_value(
            values, LANGFUSE_ENVIRONMENT_ENV_KEY, DEFAULT_LANGFUSE_ENVIRONMENT
        ),
    )
