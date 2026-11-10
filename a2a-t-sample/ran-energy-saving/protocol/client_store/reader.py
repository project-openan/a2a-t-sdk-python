"""Read-only query helpers used by the Web UI and external consoles."""

from __future__ import annotations

import builtins
import logging
import sqlite3
from pathlib import Path

from shared.sqlite import readonly_connection

from protocol.client_store.store import _load_json, _truncate

_LOGGER = logging.getLogger("ran_energy_saving.client_store")


def _readonly_connection(path: str | Path) -> sqlite3.Connection | None:
    """Open one read-only connection, ``None`` when the database is missing."""
    return readonly_connection(path)


def read_client_run_summaries(
    path: str | Path,
    limit: int = 50,
    *,
    offset: int = 0,
    protocol: str | None = None,
    status: str | None = None,
    source_kind: str | None = None,
    scenario: str | None = None,
    query: str | None = None,
    trace_id: str | None = None,
) -> builtins.list[dict[str, object]]:
    """Return UI rows for the newest client runs (never creates the database).

    The optional filters support the internal run console. ``query`` matches the request, run id
    and conversation id as a case-insensitive substring; every other filter is an exact match,
    including ``trace_id`` (the vendor-facing id used by the observe page).
    """
    connection = _readonly_connection(path)
    if connection is None:
        return []
    resolved_limit = max(1, min(limit, 500))
    resolved_offset = max(0, offset)
    clauses, params = _run_filters(
        protocol=protocol,
        status=status,
        source_kind=source_kind,
        scenario=scenario,
        query=query,
        trace_id=trace_id,
    )
    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    try:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            f"""
            SELECT r.run_id, r.conversation_id, r.scenario, r.mode, r.status, r.final_state,
                   r.protocol, r.trace_id, r.chain_id, r.parent_run_id, r.user_request, r.error,
                   r.source_kind, r.source_json, r.server_task_id, r.server_context_id,
                   r.downstream_task_id, r.downstream_context_id, r.downstream_hop,
                   r.created_at, r.updated_at, r.finished_at,
                   m.duration_ms, m.total_tokens, m.prompt_tokens, m.completion_tokens,
                   m.llm_calls, m.token_available,
                   (SELECT COUNT(*) FROM turns t WHERE t.conversation_id = r.conversation_id)
                       AS turn_count
            FROM runs r
            LEFT JOIN run_metrics m ON m.run_id = r.run_id
            {where}
            ORDER BY r.created_at DESC, r.run_id DESC
            LIMIT ? OFFSET ?
            """,
            (*params, resolved_limit, resolved_offset),
        ).fetchall()
        return [
            {
                "runId": str(row["run_id"]),
                "conversationId": row["conversation_id"],
                "scenario": str(row["scenario"]),
                "mode": str(row["mode"]),
                "protocol": str(row["protocol"]),
                "traceId": str(row["trace_id"] or ""),
                "chainId": str(row["chain_id"] or ""),
                "parentRunId": str(row["parent_run_id"] or ""),
                "serverTaskId": row["server_task_id"],
                "serverContextId": row["server_context_id"],
                "status": str(row["status"]),
                "finalState": row["final_state"],
                "userRequest": _truncate(str(row["user_request"])),
                "error": row["error"],
                "sourceKind": str(row["source_kind"] or ""),
                "sourceChannel": _source_channel(row["source_json"]),
                "caseId": _source_text(row["source_json"], "caseId"),
                "batchId": _source_text(row["source_json"], "batchId"),
                "downstreamTaskId": row["downstream_task_id"],
                "downstreamContextId": row["downstream_context_id"],
                "downstreamHop": str(row["downstream_hop"] or ""),
                "createdAt": str(row["created_at"]),
                "updatedAt": str(row["updated_at"]),
                "finishedAt": row["finished_at"],
                "turnCount": int(row["turn_count"] or 0),
                "durationMs": row["duration_ms"],
                "totalTokens": row["total_tokens"],
                "promptTokens": row["prompt_tokens"],
                "completionTokens": row["completion_tokens"],
                "llmCalls": row["llm_calls"],
                "tokenAvailable": bool(row["token_available"] or 0),
            }
            for row in rows
        ]
    except sqlite3.Error as exc:
        _LOGGER.warning("client run summary read failed: %s", exc)
        return []
    finally:
        connection.close()


def count_client_runs(
    path: str | Path,
    *,
    protocol: str | None = None,
    status: str | None = None,
    source_kind: str | None = None,
    scenario: str | None = None,
    query: str | None = None,
    trace_id: str | None = None,
) -> int:
    """Return how many runs match the console filters (never creates the database)."""
    connection = _readonly_connection(path)
    if connection is None:
        return 0
    clauses, params = _run_filters(
        protocol=protocol,
        status=status,
        source_kind=source_kind,
        scenario=scenario,
        query=query,
        trace_id=trace_id,
    )
    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    try:
        connection.row_factory = sqlite3.Row
        row = connection.execute(
            f"SELECT COUNT(*) AS total FROM runs r{where}",
            tuple(params),
        ).fetchone()
        return int(row["total"] or 0) if row is not None else 0
    except sqlite3.Error as exc:
        _LOGGER.warning("client run count failed: %s", exc)
        return 0
    finally:
        connection.close()


def _run_filters(
    *,
    protocol: str | None,
    status: str | None,
    source_kind: str | None,
    scenario: str | None,
    query: str | None,
    trace_id: str | None,
) -> tuple[builtins.list[str], builtins.list[object]]:
    """Build the WHERE clauses and parameters shared by the run list and its count."""
    clauses: builtins.list[str] = []
    params: builtins.list[object] = []
    if protocol:
        clauses.append("r.protocol=?")
        params.append(protocol)
    if status:
        clauses.append("r.status=?")
        params.append(status)
    if source_kind:
        clauses.append("r.source_kind=?")
        params.append(source_kind)
    if scenario:
        clauses.append("r.scenario=?")
        params.append(scenario)
    if query:
        needle = f"%{query}%"
        clauses.append(
            "(r.user_request LIKE ? OR r.run_id LIKE ? OR r.conversation_id LIKE ?"
            " OR r.server_task_id LIKE ? OR r.server_context_id LIKE ?"
            " OR r.downstream_task_id LIKE ? OR r.downstream_context_id LIKE ?)"
        )
        params.extend((needle, needle, needle, needle, needle, needle, needle))
    if trace_id:
        clauses.append("r.trace_id=?")
        params.append(trace_id)
    return clauses, params


def _source_channel(raw: object) -> str:
    """Return the channel label of one persisted source document."""
    source = _load_json(raw) if isinstance(raw, str) else {}
    channel = source.get("channel")
    return str(channel) if isinstance(channel, str) else ""


def _source_text(raw: object, key: str) -> str:
    """Return one string field of a persisted source document (tester case/batch ids)."""
    source = _load_json(raw) if isinstance(raw, str) else {}
    value = source.get(key)
    return str(value) if isinstance(value, str) else ""


def read_client_run_detail(path: str | Path, run_id: str) -> dict[str, object] | None:
    """Return one run with its turns, negotiation events and Task snapshot."""
    connection = _readonly_connection(path)
    if connection is None:
        return None
    try:
        connection.row_factory = sqlite3.Row
        run = connection.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone()
        if run is None:
            return None
        turns = connection.execute(
            "SELECT * FROM turns WHERE conversation_id=? ORDER BY seq ASC",
            (run["conversation_id"],),
        ).fetchall()
        events = connection.execute(
            "SELECT * FROM negotiation_events WHERE run_id=? ORDER BY seq ASC",
            (run_id,),
        ).fetchall()
        snapshot = connection.execute(
            "SELECT * FROM task_snapshots WHERE run_id=?",
            (run_id,),
        ).fetchone()
        return {
            "runId": str(run["run_id"]),
            "conversationId": run["conversation_id"],
            "turnId": run["turn_id"],
            "scenario": str(run["scenario"]),
            "mode": str(run["mode"]),
            "transport": str(run["transport"]),
            "discovery": str(run["discovery"]),
            "protocol": str(run["protocol"]),
            "userRequest": str(run["user_request"]),
            "status": str(run["status"]),
            "finalState": run["final_state"],
            "serverContextId": run["server_context_id"],
            "serverTaskId": run["server_task_id"],
            "downstreamTaskId": run["downstream_task_id"],
            "downstreamContextId": run["downstream_context_id"],
            "downstreamHop": str(run["downstream_hop"] or ""),
            "error": run["error"],
            "idempotencyKey": str(run["idempotency_key"]),
            "sourceKind": str(run["source_kind"] or ""),
            "source": _load_json(run["source_json"]),
            "transcriptJson": run["transcript_json"],
            "transcriptMarkdown": run["transcript_markdown"],
            "traceJson": run["trace_json"],
            "createdAt": str(run["created_at"]),
            "updatedAt": str(run["updated_at"]),
            "finishedAt": run["finished_at"],
            "turns": [
                {
                    "turnId": str(row["turn_id"]),
                    "seq": int(row["seq"]),
                    "role": str(row["role"]),
                    "content": str(row["content"]),
                    "runId": row["run_id"],
                    "createdAt": str(row["created_at"]),
                }
                for row in turns
            ],
            "negotiationEvents": [
                {
                    "eventId": str(row["event_id"]),
                    "seq": int(row["seq"]),
                    "negotiationId": row["negotiation_id"],
                    "kind": row["kind"],
                    "round": int(row["round"]) if row["round"] is not None else None,
                    "performative": row["performative"],
                    "direction": str(row["direction"]),
                    "actor": str(row["actor"]),
                    "content": _load_json(row["content_json"]),
                    "decisionAction": row["decision_action"],
                    "rationale": row["rationale"],
                    # Absent only on a database written before the migration; such a row is A2A-T.
                    "protocol": row["protocol"] if "protocol" in row.keys() else None,
                    "createdAt": str(row["created_at"]),
                }
                for row in events
            ],
            "taskSnapshot": (
                {
                    "taskId": snapshot["task_id"],
                    "contextId": snapshot["context_id"],
                    "state": snapshot["state"],
                    "updatedAt": str(snapshot["updated_at"]),
                    "payload": _load_json(snapshot["payload_json"]),
                }
                if snapshot is not None
                else None
            ),
        }
    except sqlite3.Error as exc:
        _LOGGER.warning("client run detail read failed: %s", exc)
        return None
    finally:
        connection.close()


def read_run_metrics(path: str | Path, run_id: str) -> dict[str, object] | None:
    """Return the persisted metrics document of one run, ``None`` when absent."""
    connection = _readonly_connection(path)
    if connection is None:
        return None
    try:
        connection.row_factory = sqlite3.Row
        row = connection.execute(
            "SELECT metric_json, created_at, updated_at FROM run_metrics WHERE run_id=?",
            (run_id,),
        ).fetchone()
    except sqlite3.Error as exc:
        _LOGGER.warning("run metrics read failed: %s", exc)
        return None
    finally:
        connection.close()
    if row is None:
        return None
    metrics = _load_json(row["metric_json"])
    metrics.setdefault("createdAt", str(row["created_at"]))
    metrics["updatedAt"] = str(row["updated_at"])
    return metrics


def read_run_metrics_rows(
    path: str | Path,
    *,
    protocol: str | None = None,
    scenario: str | None = None,
    limit: int = 500,
) -> builtins.list[dict[str, object]]:
    """Return persisted metrics rows, newest first, optionally filtered."""
    connection = _readonly_connection(path)
    if connection is None:
        return []
    resolved_limit = max(1, min(limit, 5000))
    clauses: list[str] = []
    params: list[object] = []
    if protocol:
        clauses.append("protocol=?")
        params.append(protocol)
    if scenario:
        clauses.append("scenario=?")
        params.append(scenario)
    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    params.append(resolved_limit)
    try:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            f"""
            SELECT metric_json, created_at, updated_at
              FROM run_metrics
              {where}
             ORDER BY created_at DESC, run_id DESC
             LIMIT ?
            """,
            tuple(params),
        ).fetchall()
    except sqlite3.Error as exc:
        _LOGGER.warning("run metrics list failed: %s", exc)
        return []
    finally:
        connection.close()
    results: builtins.list[dict[str, object]] = []
    for row in rows:
        metrics = _load_json(row["metric_json"])
        metrics.setdefault("createdAt", str(row["created_at"]))
        metrics["updatedAt"] = str(row["updated_at"])
        results.append(metrics)
    return results


def read_conversation_summaries(
    path: str | Path,
    limit: int = 50,
) -> builtins.list[dict[str, object]]:
    """Return UI rows for the newest conversations."""
    connection = _readonly_connection(path)
    if connection is None:
        return []
    resolved_limit = max(1, min(limit, 500))
    try:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            """
            SELECT c.conversation_id, c.external_id, c.user_id, c.title, c.created_at,
                   c.updated_at,
                   (SELECT COUNT(*) FROM turns t WHERE t.conversation_id = c.conversation_id)
                       AS turn_count,
                   (SELECT COUNT(*) FROM runs r WHERE r.conversation_id = c.conversation_id)
                       AS run_count
            FROM conversations c
            ORDER BY c.updated_at DESC, c.conversation_id DESC
            LIMIT ?
            """,
            (resolved_limit,),
        ).fetchall()
        return [
            {
                "conversationId": str(row["conversation_id"]),
                "externalId": str(row["external_id"]),
                "userId": str(row["user_id"]),
                "title": str(row["title"]),
                "createdAt": str(row["created_at"]),
                "updatedAt": str(row["updated_at"]),
                "turnCount": int(row["turn_count"] or 0),
                "runCount": int(row["run_count"] or 0),
            }
            for row in rows
        ]
    except sqlite3.Error as exc:
        _LOGGER.warning("conversation summary read failed: %s", exc)
        return []
    finally:
        connection.close()


def read_conversation_detail(
    path: str | Path,
    conversation_id: str,
) -> dict[str, object] | None:
    """Return one conversation with its turns and runs."""
    connection = _readonly_connection(path)
    if connection is None:
        return None
    try:
        connection.row_factory = sqlite3.Row
        conversation = connection.execute(
            "SELECT * FROM conversations WHERE conversation_id=?",
            (conversation_id,),
        ).fetchone()
        if conversation is None:
            return None
        turns = connection.execute(
            "SELECT * FROM turns WHERE conversation_id=? ORDER BY seq ASC",
            (conversation_id,),
        ).fetchall()
        runs = connection.execute(
            "SELECT * FROM runs WHERE conversation_id=? ORDER BY created_at DESC",
            (conversation_id,),
        ).fetchall()
        return {
            "conversationId": str(conversation["conversation_id"]),
            "externalId": str(conversation["external_id"]),
            "userId": str(conversation["user_id"]),
            "title": str(conversation["title"]),
            "createdAt": str(conversation["created_at"]),
            "updatedAt": str(conversation["updated_at"]),
            "metadata": _load_json(conversation["metadata_json"]),
            "turns": [
                {
                    "turnId": str(row["turn_id"]),
                    "seq": int(row["seq"]),
                    "role": str(row["role"]),
                    "content": str(row["content"]),
                    "runId": row["run_id"],
                    "createdAt": str(row["created_at"]),
                }
                for row in turns
            ],
            "runs": [
                {
                    "runId": str(row["run_id"]),
                    "scenario": str(row["scenario"]),
                    "status": str(row["status"]),
                    "finalState": row["final_state"],
                    "createdAt": str(row["created_at"]),
                    "finishedAt": row["finished_at"],
                }
                for row in runs
            ],
        }
    except sqlite3.Error as exc:
        _LOGGER.warning("conversation detail read failed: %s", exc)
        return None
    finally:
        connection.close()
