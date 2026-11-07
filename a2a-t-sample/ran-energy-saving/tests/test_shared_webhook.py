"""The webhook callback is a process resource; its event stream is per run."""

from __future__ import annotations

from dataclasses import replace

import httpx
from integration_helpers import free_tcp_port, offline_direct_config
from protocol.shared_webhook import (
    WebhookRouter,
    WebhookSubscription,
    close_shared_webhooks,
    get_shared_webhook,
)
from shared.config import SampleConfig


def _config() -> SampleConfig:
    """Return a direct-mode configuration asking the OS for a callback port."""
    return replace(
        offline_direct_config(free_tcp_port()),
        webhook_port=0,
        webhook_public_url="",
    )


def _status(task_id: str, context_id: str = "") -> dict[str, object]:
    """Return one status-update push for a task."""
    return {
        "statusUpdate": {
            "taskId": task_id,
            **({"contextId": context_id} if context_id else {}),
            "status": {"state": "TASK_STATE_WORKING"},
        }
    }


def _message(context_id: str) -> dict[str, object]:
    """Return one message push carrying only a context id."""
    return {"message": {"contextId": context_id, "messageId": "m-1", "parts": [{"text": "hi"}]}}


async def test_every_run_reuses_one_receiver_and_port() -> None:
    config = _config()
    first = await get_shared_webhook(config)
    try:
        second = await get_shared_webhook(config)

        assert second is first
        assert second.url == first.url
        assert first.receiver.port != 0
    finally:
        await close_shared_webhooks()


async def test_the_receiver_routes_a_real_push_to_the_owning_run() -> None:
    """End-to-end: the HTTP callback hands the push to the router, not to one shared queue."""
    config = _config()
    shared = await get_shared_webhook(config)
    first = shared.subscribe("run-1")
    second = shared.subscribe("run-2")
    first.bind(task_id="task-1", context_id="ctx-1")
    second.bind(task_id="task-2", context_id="ctx-2")
    try:
        async with httpx.AsyncClient(trust_env=False) as client:
            response = await client.post(
                f"http://127.0.0.1:{shared.receiver.port}/webhook",
                json=_status("task-2", "ctx-2"),
                headers={"X-A2A-Notification-Token": config.webhook_token},
            )
        assert response.status_code == 200
        assert first.queue.empty()
        assert len(_drain(second)) == 1
    finally:
        await close_shared_webhooks()


async def test_two_runs_bind_their_own_task_and_context_streams() -> None:
    """Concurrent webhook runs must not consume each other's pushes."""
    router = WebhookRouter()
    first = router.subscribe("run-1")
    second = router.subscribe("run-2")
    first.bind(task_id="task-1", context_id="ctx-1")
    second.bind(task_id="task-2", context_id="ctx-2")

    router.on_event(_status("task-2"))
    router.on_event(_message("ctx-1"))
    router.on_event({"task": {"id": "task-1", "contextId": "ctx-1"}})

    first_events = _drain(first)
    second_events = _drain(second)
    assert [next(iter(event)) for event in first_events] == ["message", "task"]
    assert [next(iter(event)) for event in second_events] == ["statusUpdate"]
    assert second_events[0]["statusUpdate"]["taskId"] == "task-2"  # type: ignore[index]
    assert first.queue.empty()
    assert second.queue.empty()


async def test_a_push_that_beats_the_rpc_reply_is_flushed_on_bind() -> None:
    """The server can push before the client has parsed the reply that names the task."""
    router = WebhookRouter()
    first = router.subscribe("run-1")
    second = router.subscribe("run-2")
    second.bind(task_id="task-2", context_id="ctx-2")

    router.on_event(_status("task-1", "ctx-1"))
    assert first.queue.empty()

    first.bind(task_id="task-1", context_id="ctx-1")

    assert len(_drain(first)) == 1
    assert second.queue.empty()


async def test_a_finished_runs_late_push_is_dropped_not_delivered_to_the_next_run() -> None:
    """A sole remaining run must not be contaminated by a push of the run that just finished."""
    router = WebhookRouter()
    finished = router.subscribe("old-run")
    finished.bind(task_id="old-task", context_id="old-ctx")
    finished.unsubscribe()
    current = router.subscribe("new-run")
    current.bind(task_id="new-task", context_id="new-ctx")

    router.on_event(_status("old-task", "old-ctx"))
    router.on_event(_status("new-task", "new-ctx"))

    events = _drain(current)
    assert len(events) == 1
    assert events[0]["statusUpdate"]["taskId"] == "new-task"  # type: ignore[index]


async def test_an_unknown_identity_is_not_guessed_into_the_sole_run() -> None:
    """An identity-bearing push must wait for its owner, even when only one run is active.

    The old fallback delivered any unmatched push to the sole subscription. A late push from a
    previous Task would then enter an unrelated run and corrupt its timeline.
    """
    router = WebhookRouter()
    current = router.subscribe("run-1")
    current.bind(task_id="task-1", context_id="ctx-1")

    router.on_event(_status("old-task", "old-ctx"))

    assert current.queue.empty()

    late_owner = router.subscribe("run-2")
    late_owner.bind(task_id="old-task", context_id="old-ctx")

    assert current.queue.empty()
    assert len(_drain(late_owner)) == 1


async def test_an_unkeyed_push_is_delivered_only_when_one_run_is_alone() -> None:
    """A server event without task/context is ambiguous once two runs share the receiver."""
    router = WebhookRouter()
    single = router.subscribe("run-1")
    router.on_event({"unknown": {}})
    assert len(_drain(single)) == 1

    router.subscribe("run-2")
    router.on_event({"unknown": {}})
    assert single.queue.empty()


async def test_repeated_scopes_keep_distinct_queues() -> None:
    """Two runs reporting the same id (copy/paste, replay) must not steal each other's stream."""
    router = WebhookRouter()
    first = router.subscribe("run-1")
    second = router.subscribe("run-1")

    assert second is not first
    assert not first.closed
    assert router.active_subscriptions == 2

    first.bind(task_id="task-1")
    second.bind(task_id="task-2")
    router.on_event(_status("task-2"))

    assert first.queue.empty()
    assert len(_drain(second)) == 1

    second.unsubscribe()
    assert router.active_subscriptions == 1


def _drain(subscription: WebhookSubscription) -> list[dict[str, object]]:
    """Pop every queued event of one subscription."""
    events: list[dict[str, object]] = []
    while not subscription.queue.empty():
        events.append(subscription.queue.get_nowait())
    return events
