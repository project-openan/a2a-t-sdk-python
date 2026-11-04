"""Agent discovery of the RAN energy-saving client.

Two modes (design §7.1):

* ``registry`` (default): query the registry center by ``(organization, name)`` and use the first
  HTTP interface of the first returned card;
* ``direct``: read ``/.well-known/agent-card.json`` from the server base URL.

Both modes return a :class:`DiscoveredAgent` carrying the parsed ``AgentCard`` and the endpoint URL
the plain a2a-sdk client must call.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

import httpx
from a2a.types import AgentCard
from a2a.utils.constants import TransportProtocol
from a2a_t.core.metadata import NEGOTIATION_T_EXTENSION_URI, TASK_T_EXTENSION_URI
from google.protobuf.json_format import ParseDict, ParseError
from shared.config import DISCOVERY_DIRECT, DISCOVERY_NMS, DISCOVERY_REGISTRY
from shared.registry_client import query_by_name_org

#: Well-known path of the direct AgentCard endpoint.
AGENT_CARD_WELL_KNOWN_PATH: Final[str] = "/.well-known/agent-card.json"

#: Interface protocol bindings a plain HTTP JSON client can use.
_HTTP_BINDINGS: Final[tuple[str, ...]] = (TransportProtocol.HTTP_JSON.value, "")


class DiscoveryError(RuntimeError):
    """Raised when no usable agent endpoint can be discovered."""


@dataclass(frozen=True)
class DiscoveredAgent:
    """One discovered server endpoint.

    Attributes:
        agent_card: Parsed AgentCard advertised by the server.
        url: Base URL of the HTTP JSON interface to call with the a2a-sdk client.
    """

    agent_card: AgentCard
    url: str


def parse_agent_card(payload: Mapping[str, object]) -> AgentCard:
    """Parse one AgentCard JSON mapping.

    Raises:
        DiscoveryError: when the payload does not match the AgentCard schema.
    """
    try:
        card = AgentCard()
        ParseDict(dict(payload), card)
        return card
    except (TypeError, ValueError, ParseError) as exc:
        raise DiscoveryError(f"Invalid AgentCard payload: {exc}") from exc


def agent_card_url(card: AgentCard) -> str | None:
    """Return the first usable HTTP JSON interface URL of one card, ``None`` when it has none."""
    for interface in card.supported_interfaces:
        if interface.url and interface.protocol_binding in _HTTP_BINDINGS:
            return interface.url.rstrip("/")
    return None


def discovered_from_card(card: AgentCard) -> DiscoveredAgent:
    """Wrap one card into a :class:`DiscoveredAgent`.

    Raises:
        DiscoveryError: when the card advertises no usable HTTP interface.
    """
    url = agent_card_url(card)
    if url is None:
        raise DiscoveryError(f"Agent card '{card.name}' advertises no HTTP JSON interface")
    return DiscoveredAgent(agent_card=card, url=url)


async def fetch_agent_card(
    base_url: str,
    *,
    timeout_seconds: float = 30.0,
    http_client: Any | None = None,
) -> AgentCard:
    """Fetch and parse the AgentCard served at ``<base_url>/.well-known/agent-card.json``.

    Raises:
        DiscoveryError: on a transport failure, a non-200 response, or an invalid card.
    """
    uri = f"{base_url.rstrip('/')}{AGENT_CARD_WELL_KNOWN_PATH}"
    owns_client = http_client is None
    client = http_client or httpx.AsyncClient(timeout=timeout_seconds, trust_env=False)
    try:
        response = await client.get(uri)
        if response.status_code != 200:
            raise DiscoveryError(f"AgentCard request failed with status {response.status_code}: {uri}")
        payload = response.json()
    except DiscoveryError:
        raise
    except Exception as exc:
        raise DiscoveryError(f"AgentCard request failed for {uri}: {exc}") from exc
    finally:
        if owns_client:
            await client.aclose()
    if not isinstance(payload, Mapping):
        raise DiscoveryError(f"AgentCard response is not a JSON object: {uri}")
    return parse_agent_card(payload)


async def discover_from_registry(
    *,
    organization: str,
    name: str,
    registry_center_url: str | None = None,
    env_path: Path | None = None,
    timeout_seconds: float = 30.0,
    http_client: Any | None = None,
) -> DiscoveredAgent:
    """Discover the agent through the registry center.

    Raises:
        DiscoveryError: when the registry has no card with a usable HTTP interface.
    """
    data = await query_by_name_org(
        organization=organization,
        name=name,
        env_path=env_path,
        registry_center_url=registry_center_url,
        timeout_seconds=timeout_seconds,
        http_client=http_client,
    )
    cards = data.get("agentCards") if isinstance(data, Mapping) else None
    if not isinstance(cards, list):
        raise DiscoveryError("The registry response carries no agentCards list")
    interface_errors: list[str] = []
    for raw_card in cards:
        if not isinstance(raw_card, Mapping):
            continue
        try:
            card = parse_agent_card(raw_card)
            return discovered_from_card(card)
        except DiscoveryError as exc:
            interface_errors.append(str(exc))
    detail = "; ".join(interface_errors) if interface_errors else "no agent cards returned"
    raise DiscoveryError(f"No usable AgentCard for '{organization}/{name}': {detail}")


def nms_agent_card(base_url: str, *, name: str = "RAN Energy Saving Agent") -> AgentCard:
    """Build the static AgentCard of a vendor NMS that publishes no ``/.well-known`` card.

    The URL must be the full A2A surface (``.../a2a/json``), not the host root: the a2a-sdk
    client appends ``/message:stream`` to this value.

    Args:
        base_url: Full A2A JSON surface of the vendor NMS.
        name: Card name shown in the discovery trace.

    Returns:
        A parsed AgentCard declaring streaming and the two A2A-T extensions.
    """
    payload: dict[str, object] = {
        "name": name,
        "description": ("Vendor NMS energy-saving agent reached through its A2A HTTP JSON surface."),
        "version": "1.0.0",
        "capabilities": {
            "streaming": True,
            "pushNotifications": False,
            "extensions": [
                {
                    "uri": TASK_T_EXTENSION_URI,
                    "description": "Structured Task-T prompt requests.",
                    "required": False,
                },
                {
                    "uri": NEGOTIATION_T_EXTENSION_URI,
                    "description": "Structured Negotiation-T requests.",
                    "required": False,
                },
            ],
        },
        "defaultInputModes": ["application/json", "text/plain"],
        "defaultOutputModes": ["application/json", "text/plain"],
        "skills": [
            {
                "id": "ran-energy-saving",
                "name": "RAN energy saving",
                "description": "Apply and report energy-saving plans on RAN cells.",
                "tags": ["energy-saving", "RAN"],
            }
        ],
        "supportedInterfaces": [
            {
                "url": base_url.rstrip("/"),
                "protocolBinding": TransportProtocol.HTTP_JSON.value,
                "protocolVersion": "1.0",
            }
        ],
    }
    return parse_agent_card(payload)


async def discover_agent(
    *,
    discovery: str = DISCOVERY_REGISTRY,
    organization: str,
    name: str,
    registry_center_url: str | None = None,
    direct_base_url: str | None = None,
    nms_a2a_base: str | None = None,
    env_path: Path | None = None,
    timeout_seconds: float = 30.0,
    http_client: Any | None = None,
) -> DiscoveredAgent:
    """Discover the server endpoint in the configured mode.

    Args:
        discovery: ``registry`` (default), ``direct``, or ``nms``.
        organization: Provider organization the registry lookup uses.
        name: Agent card name the registry lookup uses.
        registry_center_url: Registry base URL; defaults to the sample configuration.
        direct_base_url: Server base URL used by the direct mode.
        nms_a2a_base: Full A2A surface of a vendor NMS used by the ``nms`` mode.
        env_path: Optional ``.env`` path for the registry client.
        timeout_seconds: HTTP timeout.
        http_client: Optional injected HTTP client (tests).

    Raises:
        ValueError: when ``discovery`` is not a supported mode or its required URL is missing.
        DiscoveryError: when no usable endpoint is found.
    """
    if discovery == DISCOVERY_NMS:
        if not nms_a2a_base:
            raise ValueError("NMS discovery requires nms_a2a_base")
        return discovered_from_card(nms_agent_card(nms_a2a_base))
    if discovery == DISCOVERY_DIRECT:
        if not direct_base_url:
            raise ValueError("Direct discovery requires direct_base_url")
        card = await fetch_agent_card(direct_base_url, timeout_seconds=timeout_seconds, http_client=http_client)
        return discovered_from_card(card)
    if discovery != DISCOVERY_REGISTRY:
        raise ValueError(f"Unsupported discovery mode: {discovery}")
    return await discover_from_registry(
        organization=organization,
        name=name,
        registry_center_url=registry_center_url,
        env_path=env_path,
        timeout_seconds=timeout_seconds,
        http_client=http_client,
    )
