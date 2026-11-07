"""Client persistence: the write store and the read-only query helpers.

``protocol.client_store`` keeps its historical import surface; the implementation is split into
:mod:`protocol.client_store.store` (writes) and :mod:`protocol.client_store.reader` (UI queries).
"""

from __future__ import annotations

from protocol.client_store.reader import (
    count_client_runs,
    read_client_run_detail,
    read_client_run_summaries,
    read_conversation_detail,
    read_conversation_summaries,
    read_run_metrics,
    read_run_metrics_rows,
)
from protocol.client_store.store import (
    NEGOTIATION_DIRECTION_INBOUND,
    NEGOTIATION_DIRECTION_OUTBOUND,
    RUN_STATUS_FAILED,
    RUN_STATUS_FINISHED,
    RUN_STATUS_INTERRUPTED,
    RUN_STATUS_RUNNING,
    RUN_STATUSES,
    SUMMARY_LIMIT,
    TURN_ROLE_ASSISTANT,
    TURN_ROLE_SYSTEM,
    TURN_ROLE_USER,
    ClientRunRecord,
    ClientStore,
    ConversationRecord,
    NegotiationEventRecord,
    RunAlreadyActiveError,
    SqliteClientStore,
    TaskSnapshotRecord,
    TurnRecord,
    now_iso,
)

__all__ = [
    "NEGOTIATION_DIRECTION_INBOUND",
    "NEGOTIATION_DIRECTION_OUTBOUND",
    "RUN_STATUSES",
    "RUN_STATUS_FAILED",
    "RUN_STATUS_FINISHED",
    "RUN_STATUS_INTERRUPTED",
    "RUN_STATUS_RUNNING",
    "SUMMARY_LIMIT",
    "TURN_ROLE_ASSISTANT",
    "TURN_ROLE_SYSTEM",
    "TURN_ROLE_USER",
    "ClientRunRecord",
    "ClientStore",
    "ConversationRecord",
    "NegotiationEventRecord",
    "RunAlreadyActiveError",
    "SqliteClientStore",
    "TaskSnapshotRecord",
    "TurnRecord",
    "count_client_runs",
    "now_iso",
    "read_client_run_detail",
    "read_client_run_summaries",
    "read_conversation_detail",
    "read_conversation_summaries",
    "read_run_metrics",
    "read_run_metrics_rows",
]
