# Copyright (c) 2026 Huawei Technologies Co., Ltd.
# All Rights Reserved.
#
# SPDX-License-Identifier: Apache-2.0
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.

"""Mock registry center routes: in-memory AgentCard register/query via Starlette."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

_cards: dict[tuple[str, str], dict[str, Any]] = {}


def store_card(card: dict[str, Any]) -> None:
    organization = str(card.get("provider", {}).get("organization", ""))
    name = str(card.get("name", ""))
    _cards[(organization, name)] = deepcopy(card)


def get_card(organization: str, name: str) -> dict[str, Any] | None:
    card = _cards.get((organization, name))
    return deepcopy(card) if card is not None else None


def clear_cards() -> None:
    _cards.clear()


async def register_agentcards(request: Request) -> JSONResponse:
    body = await request.json()
    agent_cards = body.get("agentCards", [])
    for card in agent_cards:
        if isinstance(card, dict):
            store_card(card)
    return JSONResponse(
        {"status": "success", "message": "Agent card registered successfully"},
        status_code=201,
    )


async def query_agentcard_by_name_org(request: Request) -> JSONResponse:
    organization = request.path_params["organization"]
    name = request.path_params["name"]
    card = get_card(organization, name)
    if card is None:
        return JSONResponse(
            {"errors": {"error": [{"errorMessage": "Agent not found"}]}},
            status_code=404,
        )
    return JSONResponse({"agentCards": [card]}, status_code=200)


def build_registry_app() -> Starlette:
    """Build the Starlette app with POST (register) and GET (query) routes for agent cards."""
    routes = [
        Route(
            path="/rest/v1/registry-center/agent-cards",
            endpoint=register_agentcards,
            methods=["POST"],
        ),
        Route(
            path="/rest/v1/registry-center/agent-cards/{organization}/{name}",
            endpoint=query_agentcard_by_name_org,
            methods=["GET"],
        ),
    ]
    return Starlette(routes=routes)
