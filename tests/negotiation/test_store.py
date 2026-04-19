from __future__ import annotations

import sys
from pathlib import Path
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = PROJECT_ROOT / "src"

if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))


class InMemoryNegotiationStateStoreTest(unittest.TestCase):
    def test_store_saves_gets_deletes_records(self) -> None:
        from a2a_t.negotiation.common.enums import NegotiationRole, NegotiationStatus, NegotiationType
        from a2a_t.negotiation.common.models import NegotiationContext, NegotiationRecord
        from a2a_t.negotiation.store.in_memory import InMemoryNegotiationStateStore

        store = InMemoryNegotiationStateStore()
        record = NegotiationRecord(
            context=NegotiationContext(
                negotiation_type=NegotiationType.FULFILLMENT,
                negotiation_id="neg-store",
                role=NegotiationRole.SERVER,
                round=1,
                status=NegotiationStatus.IN_PROGRESS,
                extra={},
            ),
            last_message="message",
            last_receive_result=None,
            last_continue_result=None,
            last_task_prompt=None,
            created_at="2026-04-18T00:00:00Z",
            updated_at="2026-04-18T00:00:00Z",
        )

        store.save(record)

        self.assertIs(store.get("neg-store"), record)

        store.delete("neg-store")

        self.assertIsNone(store.get("neg-store"))

    def test_cleanup_expired_returns_true(self) -> None:
        from a2a_t.negotiation.store.in_memory import InMemoryNegotiationStateStore

        store = InMemoryNegotiationStateStore()

        self.assertTrue(store.cleanup_expired())
