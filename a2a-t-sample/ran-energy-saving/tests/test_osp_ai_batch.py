"""Tests of the headless tester batch driver (``eval.osp.ai.batch``)."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any, cast

import pytest
from eval.osp.ai import batch as batch_module
from eval.osp.ai.batch import (
    BatchError,
    BatchSnapshot,
    create,
    fetch,
    is_terminal,
    load_file,
    run,
    start,
    ui_restart_cancelled,
    validate_selection,
    wait,
)
from eval.osp.ai.http import JsonClient
from eval.osp.ai.types import Profile


def make_profile(tmp_path: Path, *, owner: str = "osp-ai", ui_port: int = 18080) -> Profile:
    """Return a minimal profile whose UI URL points at a fake tester."""
    return Profile(
        name="osp-ai",
        sample_root=tmp_path,
        source_path=None,
        run_dir=tmp_path / "launcher",
        runs_dir=tmp_path / "runs-ai",
        ui_host="127.0.0.1",
        ui_port=ui_port,
        oss_port=18081,
        mie_port=18082,
        webhook_host="127.0.0.1",
        webhook_port=19000,
        oss_webhook_port=19001,
        webhook_public_url="https://example.test:19000/webhook",
        oss_webhook_public_url="https://example.test:19001/webhook",
        language="en-US",
        model_mode="live",
        protocol="both",
        decision_mode="live",
        approval="auto",
        owner=owner,
        nms=True,
        nms_sim=False,
        nms_sim_port=18083,
        env={},
    )


def snapshot_document(
    status: str,
    *,
    batch_id: str = "batch-1",
    summary: Mapping[str, int] | None = None,
    items: list[Mapping[str, object]] | None = None,
) -> dict[str, object]:
    """Return one server-shaped batch document."""
    return {
        "schema": "osp-tester/v3",
        "batchId": batch_id,
        "status": status,
        "summary": dict(summary or {"total": 1, "pending": 1}),
        "items": list(items or []),
        "stopAfter": "send",
        "ownerId": "osp-ai",
    }


class FakeClient:
    """Replay one ``(status, body)`` per request and record every call."""

    def __init__(self, *responses: tuple[int, object]) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[str, str, object]] = []

    def get_json(self, path: str) -> tuple[int, object]:
        """Return the next scripted response for a GET."""
        return self._next("GET", path, None)

    def post_json(self, path: str, payload: Mapping[str, Any] | None = None) -> tuple[int, object]:
        """Return the next scripted response for a POST."""
        return self._next("POST", path, payload)

    def _next(self, method: str, path: str, payload: object) -> tuple[int, object]:
        self.calls.append((method, path, payload))
        if not self.responses:
            raise AssertionError(f"unexpected {method} {path}")
        return self.responses.pop(0)


class RepeatingClient:
    """Always answer with the same document and count the requests."""

    def __init__(self, body: object) -> None:
        self.body = body
        self.requests: list[str] = []

    def get_json(self, path: str) -> tuple[int, object]:
        """Return the fixed document."""
        self.requests.append(path)
        return 200, self.body

    def post_json(self, path: str, payload: Mapping[str, Any] | None = None) -> tuple[int, object]:
        """Return the fixed document."""
        self.requests.append(path)
        return 200, self.body


class FakeClock:
    """Deterministic replacement of the ``time`` module used by :func:`wait`."""

    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        """Return the fake monotonic clock."""
        return self.now

    def sleep(self, seconds: float) -> None:
        """Advance the fake clock and remember the requested delay."""
        self.sleeps.append(seconds)
        self.now += seconds


def as_client(fake: object) -> JsonClient:
    """Type a fake client as the real client for the annotated API."""
    return cast(JsonClient, fake)


def test_create_start_wait_happy_path(tmp_path: Path) -> None:
    """The driver reports draft, running and finally the finished snapshot."""
    profile = make_profile(tmp_path)
    fake = FakeClient(
        (201, snapshot_document("draft")),
        (200, snapshot_document("running")),
        (200, snapshot_document("running")),
        (200, snapshot_document("running", summary={"total": 1, "running": 1})),
        (200, snapshot_document("finished", summary={"total": 1, "passed": 1})),
    )

    created = create(
        profile,
        case_ids=[1, 20],
        protocols=["a2a-t"],
        stop_after="send",
        approval="auto",
        timeout_seconds=300.0,
        working_timeout_seconds=60.0,
        decision_mode="scripted",
        keep_tasks=True,
        client=as_client(fake),
    )
    assert created.batch_id == "batch-1"
    assert created.status == "draft"
    assert created.summary == {"total": 1, "pending": 1}

    method, path, payload = fake.calls[0]
    assert (method, path) == ("POST", "/api/tester/batches")
    assert payload == {
        "caseIds": [1, 20],
        "protocols": ["a2a-t"],
        "stopAfter": "send",
        "approval": "auto",
        "timeoutSeconds": 300.0,
        "workingTimeoutSeconds": 60.0,
        "decisionMode": "scripted",
        "keepTasks": True,
    }

    started = start(profile, created.batch_id, client=as_client(fake))
    assert started.status == "running"
    assert fake.calls[1][:2] == ("POST", "/api/tester/batches/batch-1/start")

    ticks: list[BatchSnapshot] = []
    done = wait(
        profile,
        started.batch_id,
        timeout_seconds=60.0,
        poll_seconds=0.01,
        on_tick=ticks.append,
        client=as_client(fake),
        sleep=lambda _seconds: None,
    )
    assert done.status == "finished"
    assert done.summary == {"total": 1, "passed": 1}
    assert [tick.status for tick in ticks] == ["running", "running", "finished"]
    assert fake.calls[2] == ("GET", "/api/tester/batches/batch-1", None)


def test_default_client_sends_the_owner_cookie(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A batch created without an explicit client uses the profile's owner cookie."""
    recorded: list[object] = []

    class RecordingClient:
        def __init__(
            self,
            base_url: str,
            *,
            cookie: Mapping[str, str] | None = None,
            timeout: float = 0.0,
        ) -> None:
            self.base_url = base_url
            self.cookie = cookie
            self.timeout = timeout
            recorded.append(self)

        def post_json(self, path: str, payload: Mapping[str, Any] | None = None) -> tuple[int, object]:
            return 201, snapshot_document("draft")

        def get_json(self, path: str) -> tuple[int, object]:
            return 200, snapshot_document("finished")

    monkeypatch.setattr(batch_module, "JsonClient", RecordingClient)

    create(make_profile(tmp_path), case_ids=[1], protocols=["a2a-t"], stop_after="final")
    create(make_profile(tmp_path, owner="alice"), case_ids=[1], protocols=["a2a"], stop_after="final")

    first, second = cast(list[Any], recorded)
    assert (first.base_url, first.cookie) == ("http://127.0.0.1:18080", {"a2at_tester_client": "osp-ai"})
    assert (second.base_url, second.cookie) == ("http://127.0.0.1:18080", {"a2at_tester_client": "alice"})


def test_non_2xx_raises_with_the_server_message(tmp_path: Path) -> None:
    """404 names the ownership rule; every other status carries the server error."""
    profile = make_profile(tmp_path)

    not_found = FakeClient((404, {"error": "未知测试批次。", "code": "unknown_batch"}))
    with pytest.raises(BatchError) as fetch_error:
        fetch(profile, "batch-9", client=as_client(not_found))
    message = str(fetch_error.value)
    assert "未知测试批次。" in message
    assert "not owned by this profile" in message
    assert fetch_error.value.status == 404

    conflict = FakeClient((409, {"error": "该批次已经执行过。", "code": "batch_not_startable"}))
    with pytest.raises(BatchError) as start_error:
        start(profile, "batch-1", client=as_client(conflict))
    assert "该批次已经执行过。" in str(start_error.value)
    assert "HTTP 409" in str(start_error.value)

    broken = FakeClient((500, None))
    with pytest.raises(BatchError) as server_error:
        fetch(profile, "batch-1", client=as_client(broken))
    assert "HTTP 500" in str(server_error.value)


def test_create_rejects_an_invalid_selection_before_any_request(tmp_path: Path) -> None:
    """The local check keeps a rejected selection from looking like an infrastructure failure."""

    class NeverCalled:
        def get_json(self, path: str) -> tuple[int, object]:
            raise AssertionError(path)

        def post_json(self, path: str, payload: Mapping[str, Any] | None = None) -> tuple[int, object]:
            raise AssertionError(path)

    with pytest.raises(ValueError):
        create(
            make_profile(tmp_path),
            case_ids=[1],
            protocols=["a2a"],
            stop_after="validate",
            client=as_client(NeverCalled()),
        )


@pytest.mark.parametrize(
    ("protocols", "stop_after"),
    [
        pytest.param(["a2a-t"], "intent", id="a2at-sdk-stage"),
        pytest.param(["a2a-t"], "final", id="a2at-final"),
        pytest.param(["a2a"], "send", id="pure-send"),
        pytest.param(["a2a"], "working", id="pure-working"),
        pytest.param(["a2a", "a2a-t"], "working", id="both-working"),
    ],
)
def test_validate_selection_accepts_valid_pairs(protocols: list[str], stop_after: str) -> None:
    """The accepted matrix mirrors the server: A2A-T may use every stage."""
    validate_selection(protocols, stop_after)


@pytest.mark.parametrize(
    ("protocols", "stop_after"),
    [
        pytest.param([], "final", id="no-protocol"),
        pytest.param(["http"], "final", id="unknown-protocol"),
        pytest.param(["a2a"], "intent", id="pure-intent"),
        pytest.param(["a2a"], "task_t", id="pure-task-t"),
        pytest.param(["a2a"], "validate", id="pure-validate"),
        pytest.param(["a2a", "a2a-t"], "validate", id="both-validate"),
        pytest.param(["a2a-t"], "post", id="unknown-stage"),
        pytest.param(["a2a-t"], "", id="empty-stage"),
    ],
)
def test_validate_selection_rejects_invalid_pairs(protocols: list[str], stop_after: str) -> None:
    """Pure A2A has no SDK stage and unknown values are refused locally."""
    with pytest.raises(ValueError):
        validate_selection(protocols, stop_after)


def test_is_terminal_only_finished_or_cancelled() -> None:
    """A draft or running batch never ends the poll loop."""
    assert is_terminal("finished")
    assert is_terminal("cancelled")
    assert not is_terminal("draft")
    assert not is_terminal("running")
    assert not is_terminal("")


def test_ui_restart_cancelled_detection(tmp_path: Path) -> None:
    """A UI-restart cancellation is returned, not raised, and flagged for the operator."""
    profile = make_profile(tmp_path)
    restart_item = {"caseId": 1, "status": "cancelled", "error": "UI 进程已重启，批次未执行完。"}
    other_item = {"caseId": 2, "status": "failed", "error": "run 在发送前结束"}

    fake = FakeClient((200, snapshot_document("cancelled", items=[restart_item, other_item])))
    done = wait(profile, "batch-1", client=as_client(fake), sleep=lambda _seconds: None)
    assert done.status == "cancelled"
    assert ui_restart_cancelled(done) is True
    assert done.raw["items"] == [restart_item, other_item]

    unrelated = BatchSnapshot(
        batch_id="batch-2",
        status="cancelled",
        summary={},
        items=({"caseId": 2, "status": "cancelled", "error": "用户取消"},),
        raw={},
    )
    assert ui_restart_cancelled(unrelated) is False

    still_running = BatchSnapshot(
        batch_id="batch-3",
        status="running",
        summary={},
        items=(restart_item,),
        raw={},
    )
    assert ui_restart_cancelled(still_running) is False


def test_wait_timeout_reports_the_last_status_and_summary(tmp_path: Path) -> None:
    """A zero budget polls once, then fails with the last machine facts."""
    profile = make_profile(tmp_path)
    fake = RepeatingClient(snapshot_document("running", summary={"total": 3, "passed": 1, "running": 2}))

    with pytest.raises(BatchError) as timeout_error:
        wait(profile, "batch-1", timeout_seconds=0.0, client=as_client(fake), sleep=lambda _seconds: None)

    message = str(timeout_error.value)
    assert "did not reach a terminal state" in message
    assert "'running'" in message
    assert "'passed': 1" in message
    assert len(fake.requests) == 1


def test_wait_polling_uses_the_deadline_and_sleep(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Polling runs at ``poll_seconds`` until the budget is spent, without touching the wall clock."""
    profile = make_profile(tmp_path)
    clock = FakeClock()
    monkeypatch.setattr(batch_module, "time", clock)
    fake = RepeatingClient(snapshot_document("running"))

    with pytest.raises(BatchError):
        wait(
            profile,
            "batch-1",
            timeout_seconds=2.0,
            poll_seconds=1.0,
            client=as_client(fake),
            sleep=clock.sleep,
        )

    assert len(fake.requests) == 3
    assert clock.sleeps == [1.0, 1.0]


def test_wait_rejects_a_non_positive_poll_interval(tmp_path: Path) -> None:
    """A zero poll interval would spin; refuse it before the first request."""
    with pytest.raises(ValueError):
        wait(make_profile(tmp_path), "batch-1", poll_seconds=0.0, client=as_client(RepeatingClient({})))


def test_run_creates_starts_and_polls(tmp_path: Path) -> None:
    """``run`` chains the three calls and forwards the camelCase body."""
    profile = make_profile(tmp_path)
    fake = FakeClient(
        (201, snapshot_document("draft")),
        (200, snapshot_document("running")),
        (200, snapshot_document("finished", summary={"total": 1, "passed": 1})),
    )

    done = run(
        profile,
        case_ids=[7],
        protocols=["a2a"],
        stop_after="send",
        keep_tasks=False,
        client=as_client(fake),
        poll_seconds=0.01,
        sleep=lambda _seconds: None,
    )

    assert done.status == "finished"
    assert [call[0] for call in fake.calls] == ["POST", "POST", "GET"]
    assert fake.calls[0][2] == {
        "caseIds": [7],
        "protocols": ["a2a"],
        "stopAfter": "send",
        "approval": None,
        "timeoutSeconds": None,
        "workingTimeoutSeconds": None,
        "decisionMode": None,
        "keepTasks": False,
    }


def test_run_rejects_unknown_options_before_any_request(tmp_path: Path) -> None:
    """A typo in a CLI option must not silently create a batch."""

    class NeverCalled:
        def get_json(self, path: str) -> tuple[int, object]:
            raise AssertionError(path)

        def post_json(self, path: str, payload: Mapping[str, Any] | None = None) -> tuple[int, object]:
            raise AssertionError(path)

    with pytest.raises(TypeError):
        run(
            make_profile(tmp_path),
            case_ids=[7],
            protocols=["a2a-t"],
            stop_after="final",
            wait_timeout=10,
            client=as_client(NeverCalled()),
        )


def test_snapshot_to_dict_keeps_the_server_document(tmp_path: Path) -> None:
    """The promoted fields win, and unpromoted server fields survive."""
    document = snapshot_document("running", summary={"total": 2, "running": 1})
    document["currentIndex"] = 0
    snapshot = fetch(
        make_profile(tmp_path), "batch-1", client=as_client(FakeClient((200, document)))
    )

    payload = snapshot.to_dict()
    assert payload["batchId"] == "batch-1"
    assert payload["status"] == "running"
    assert payload["summary"] == {"total": 2, "running": 1}
    assert payload["currentIndex"] == 0


def test_load_file_reads_a_persisted_batch(tmp_path: Path) -> None:
    """A valid ``osp-tester`` document is returned as a mapping."""
    path = tmp_path / "tester" / "batch-1.json"
    path.parent.mkdir()
    path.write_text(json.dumps(snapshot_document("finished")), encoding="utf-8")

    loaded = load_file(path)
    assert loaded["batchId"] == "batch-1"
    assert loaded["schema"] == "osp-tester/v3"


@pytest.mark.parametrize(
    "content",
    [
        pytest.param("{not json", id="invalid-json"),
        pytest.param("[1, 2, 3]", id="not-an-object"),
        pytest.param(json.dumps({"schema": "other/v1"}), id="wrong-schema"),
        pytest.param(json.dumps({"batchId": "batch-1"}), id="missing-schema"),
    ],
)
def test_load_file_rejects_a_document_it_does_not_understand(tmp_path: Path, content: str) -> None:
    """Triage must fail loudly instead of analysing an unrelated JSON file."""
    path = tmp_path / "batch.json"
    path.write_text(content, encoding="utf-8")
    with pytest.raises(BatchError):
        load_file(path)


def test_load_file_rejects_a_missing_file(tmp_path: Path) -> None:
    """An absent batch file is a ``BatchError``, not an ``OSError`` for the caller to guess at."""
    with pytest.raises(BatchError):
        load_file(tmp_path / "absent.json")
