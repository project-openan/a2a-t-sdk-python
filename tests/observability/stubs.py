"""Structural stubs of a2a-python objects (spec appendix B contract) — no a2a import."""

from __future__ import annotations

from typing import Any


class FakeEnumValue:
    def __init__(self, name: str) -> None:
        self.name = name


class FakeEnumType:
    def __init__(self, mapping: dict[int, FakeEnumValue]) -> None:
        self.values_by_number = mapping


class FakeFieldDescriptor:
    def __init__(self, enum_type: FakeEnumType) -> None:
        self.enum_type = enum_type


class FakeDescriptor:
    def __init__(self, fields: dict[str, FakeFieldDescriptor]) -> None:
        self.fields_by_name = fields


class FakeStatus:
    _next_state = 100

    def __init__(self, state_name: str) -> None:
        FakeStatus._next_state += 1
        self.state = FakeStatus._next_state
        self.DESCRIPTOR = FakeDescriptor(
            {"state": FakeFieldDescriptor(FakeEnumType({self.state: FakeEnumValue(state_name)}))}
        )


class FakeStatusEvent:
    def __init__(self, task_id: str, state_name: str, final: bool = False) -> None:
        self.task_id = task_id
        self.status = FakeStatus(state_name)
        self.final = final


class FakeArtifact:
    def __init__(self) -> None:
        self.artifact_id = "A1"
        self.name = "faultManagement.Incident"


class FakeArtifactEvent:
    def __init__(self, task_id: str) -> None:
        self.task_id = task_id
        self.artifact = FakeArtifact()


class FakeMessage:
    def __init__(self, metadata: Any, task_id: str = "", context_id: str = "") -> None:
        self.metadata = metadata
        self.task_id = task_id
        self.context_id = context_id
        self.parts: list[Any] = []
        self.role = "ROLE_USER"


class FakeSendRequest:
    def __init__(self, message: FakeMessage) -> None:
        self.message = message


class FakeStreamResponse:
    """Structural StreamResponse: oneof field name + inner event."""

    def __init__(self, field: str, value: Any) -> None:
        object.__setattr__(self, "_field", field)
        object.__setattr__(self, "_value", value)

    def HasField(self, name: str) -> bool:  # noqa: N802 - mirrors protobuf API
        return name == object.__getattribute__(self, "_field")

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"):
            raise AttributeError(name)
        return object.__getattribute__(self, "_value") if name == object.__getattribute__(self, "_field") else None


class FakeContext:
    def __init__(self) -> None:
        self.state: dict[str, Any] = {}
        self.service_parameters: dict[str, str] = {}


class FakeBeforeArgs:
    def __init__(self, input_obj: Any, method: str, context: FakeContext | None = None) -> None:
        self.input = input_obj
        self.method = method
        self.agent_card = None
        self.context = context
        self.early_return = None


class FakeAfterArgs:
    def __init__(self, result: Any, method: str, context: FakeContext | None = None) -> None:
        self.result = result
        self.method = method
        self.agent_card = None
        self.context = context
        self.early_return = False


class FakeEventQueue:
    def __init__(self) -> None:
        self.events: list[Any] = []

    async def enqueue_event(self, event: Any) -> None:
        self.events.append(event)

    async def close(self) -> None: ...


class FakeServerCallContext:
    def __init__(self, headers: dict[str, str] | None = None) -> None:
        self.state: dict[str, Any] = {"headers": dict(headers or {})}
        self.requested_extensions: set[str] = set()
        self.tenant = ""


class FakeRequestContext:
    def __init__(self, message: FakeMessage, headers: dict[str, str] | None = None) -> None:
        self.call_context = FakeServerCallContext(headers)
        self.message = message


class FakeClientTransport:
    """Structural ClientTransport stub: all 12 ABC methods (a2a-sdk 1.1.2 shape).

    Simple RPC methods return ``result``; ``send_message_streaming`` / ``subscribe``
    are async generators yielding ``events`` (faithful to the ABC's asyncgen
    contract). ``error`` raises on every method; ``calls`` records invocations.
    """

    def __init__(
        self,
        result: Any = None,
        events: list[Any] | None = None,
        error: BaseException | None = None,
    ) -> None:
        self.result = result
        self.events: list[Any] = list(events or [])
        self.error = error
        self.calls: list[tuple[str, Any, Any]] = []

    async def _canned(self, name: str, request: Any, context: Any) -> Any:
        self.calls.append((name, request, context))
        if self.error is not None:
            raise self.error
        return self.result

    async def send_message(self, request: Any, *, context: Any = None) -> Any:
        return await self._canned("send_message", request, context)

    async def send_message_streaming(self, request: Any, *, context: Any = None) -> Any:
        self.calls.append(("send_message_streaming", request, context))
        if self.error is not None:
            raise self.error
        for event in self.events:
            yield event

    async def subscribe(self, request: Any, *, context: Any = None) -> Any:
        self.calls.append(("subscribe", request, context))
        if self.error is not None:
            raise self.error
        for event in self.events:
            yield event

    async def get_task(self, request: Any, *, context: Any = None) -> Any:
        return await self._canned("get_task", request, context)

    async def list_tasks(self, request: Any, *, context: Any = None) -> Any:
        return await self._canned("list_tasks", request, context)

    async def cancel_task(self, request: Any, *, context: Any = None) -> Any:
        return await self._canned("cancel_task", request, context)

    async def create_task_push_notification_config(self, request: Any, *, context: Any = None) -> Any:
        return await self._canned("create_task_push_notification_config", request, context)

    async def get_task_push_notification_config(self, request: Any, *, context: Any = None) -> Any:
        return await self._canned("get_task_push_notification_config", request, context)

    async def list_task_push_notification_configs(self, request: Any, *, context: Any = None) -> Any:
        return await self._canned("list_task_push_notification_configs", request, context)

    async def delete_task_push_notification_config(self, request: Any, *, context: Any = None) -> Any:
        return await self._canned("delete_task_push_notification_config", request, context)

    async def get_extended_agent_card(self, request: Any, *, context: Any = None) -> Any:
        return await self._canned("get_extended_agent_card", request, context)

    async def close(self) -> None:
        self.calls.append(("close", None, None))


class FakeRequestHandler:
    """Structural RequestHandler stub: all 11 ABC methods (a2a-sdk 1.1.2 shape).

    Simple RPC methods return ``result``; ``on_message_send_stream`` /
    ``on_subscribe_to_task`` are async generators yielding ``events``.
    """

    def __init__(
        self,
        result: Any = None,
        events: list[Any] | None = None,
        error: BaseException | None = None,
    ) -> None:
        self.result = result
        self.events: list[Any] = list(events or [])
        self.error = error
        self.calls: list[tuple[str, Any, Any]] = []
        self.agent_executor: Any = None
        self._push_sender: Any = None

    async def _canned(self, name: str, params: Any, context: Any) -> Any:
        self.calls.append((name, params, context))
        if self.error is not None:
            raise self.error
        return self.result

    async def on_message_send(self, params: Any, context: Any) -> Any:
        return await self._canned("on_message_send", params, context)

    async def on_message_send_stream(self, params: Any, context: Any) -> Any:
        self.calls.append(("on_message_send_stream", params, context))
        if self.error is not None:
            raise self.error
        for event in self.events:
            yield event

    async def on_subscribe_to_task(self, params: Any, context: Any) -> Any:
        self.calls.append(("on_subscribe_to_task", params, context))
        if self.error is not None:
            raise self.error
        for event in self.events:
            yield event

    async def on_get_task(self, params: Any, context: Any) -> Any:
        return await self._canned("on_get_task", params, context)

    async def on_list_tasks(self, params: Any, context: Any) -> Any:
        return await self._canned("on_list_tasks", params, context)

    async def on_cancel_task(self, params: Any, context: Any) -> Any:
        return await self._canned("on_cancel_task", params, context)

    async def on_create_task_push_notification_config(self, params: Any, context: Any) -> Any:
        return await self._canned("on_create_task_push_notification_config", params, context)

    async def on_get_task_push_notification_config(self, params: Any, context: Any) -> Any:
        return await self._canned("on_get_task_push_notification_config", params, context)

    async def on_list_task_push_notification_configs(self, params: Any, context: Any) -> Any:
        return await self._canned("on_list_task_push_notification_configs", params, context)

    async def on_delete_task_push_notification_config(self, params: Any, context: Any) -> Any:
        return await self._canned("on_delete_task_push_notification_config", params, context)

    async def on_get_extended_agent_card(self, params: Any, context: Any) -> Any:
        return await self._canned("on_get_extended_agent_card", params, context)


def make_send_request(metadata: Any, task_id: str = "T1", context_id: str = "C1") -> FakeSendRequest:
    return FakeSendRequest(FakeMessage(metadata=metadata, task_id=task_id, context_id=context_id))


def make_status_event(task_id: str, state_name: str, final: bool = False) -> FakeStatusEvent:
    return FakeStatusEvent(task_id, state_name, final)


def make_artifact_event(task_id: str) -> FakeArtifactEvent:
    return FakeArtifactEvent(task_id)


def make_final_event(task_id: str, state_name: str = "TASK_STATE_COMPLETED") -> FakeStatusEvent:
    return FakeStatusEvent(task_id, state_name, final=True)
