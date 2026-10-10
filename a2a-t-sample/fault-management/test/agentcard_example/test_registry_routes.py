from __future__ import annotations

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = PROJECT_ROOT / "src"

if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from agentcard_example.registry_routes import build_registry_app, clear_cards, get_card, store_card
from starlette.testclient import TestClient


class RegistryDataTest(unittest.TestCase):
    def setUp(self) -> None:
        clear_cards()

    def test_store_and_get_card(self) -> None:
        card = {"name": "Test Agent", "provider": {"organization": "SampleOrg"}}
        store_card(card)
        result = get_card("SampleOrg", "Test Agent")
        self.assertIsNotNone(result)
        self.assertEqual(result["name"], "Test Agent")

    def test_get_missing_card_returns_none(self) -> None:
        result = get_card("UnknownOrg", "Missing Agent")
        self.assertIsNone(result)

    def test_store_card_overwrites_duplicate(self) -> None:
        card_v1 = {"name": "Agent", "provider": {"organization": "Org"}, "version": "1.0.0"}
        card_v2 = {"name": "Agent", "provider": {"organization": "Org"}, "version": "2.0.0"}
        store_card(card_v1)
        store_card(card_v2)
        result = get_card("Org", "Agent")
        self.assertEqual(result["version"], "2.0.0")


class RegistryRoutesTest(unittest.TestCase):
    def setUp(self) -> None:
        clear_cards()
        self.app = build_registry_app()
        self.client = TestClient(self.app)

    def tearDown(self) -> None:
        clear_cards()

    def test_register_then_query_returns_card(self) -> None:
        card = {
            "name": "Subscribe Incident Agent",
            "version": "1.0.0",
            "provider": {"organization": "SampleOrg"},
            "supportedInterfaces": [{"protocolBinding": "HTTP+JSON", "url": "http://127.0.0.1:8000"}],
        }
        response = self.client.post(
            "/rest/v1/registry-center/agent-cards",
            json={"agentCards": [card]},
        )
        self.assertEqual(response.status_code, 201)
        body = response.json()
        self.assertEqual(body["status"], "success")

        query_response = self.client.get(
            "/rest/v1/registry-center/agent-cards/SampleOrg/Subscribe%20Incident%20Agent"
        )
        self.assertEqual(query_response.status_code, 200)
        cards = query_response.json()["agentCards"]
        self.assertEqual(len(cards), 1)
        self.assertEqual(cards[0]["name"], "Subscribe Incident Agent")

    def test_query_unregistered_returns_404(self) -> None:
        response = self.client.get(
            "/rest/v1/registry-center/agent-cards/UnknownOrg/Missing"
        )
        self.assertEqual(response.status_code, 404)

    def test_register_multiple_cards(self) -> None:
        cards = [
            {"name": "Agent A", "provider": {"organization": "Org1"}},
            {"name": "Agent B", "provider": {"organization": "Org2"}},
        ]
        response = self.client.post(
            "/rest/v1/registry-center/agent-cards",
            json={"agentCards": cards},
        )
        self.assertEqual(response.status_code, 201)

        r1 = self.client.get("/rest/v1/registry-center/agent-cards/Org1/Agent%20A")
        self.assertEqual(r1.status_code, 200)
        r2 = self.client.get("/rest/v1/registry-center/agent-cards/Org2/Agent%20B")
        self.assertEqual(r2.status_code, 200)


if __name__ == "__main__":
    unittest.main()
