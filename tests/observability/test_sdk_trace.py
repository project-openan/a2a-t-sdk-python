from __future__ import annotations

import asyncio
import inspect
from collections.abc import Iterator
from typing import Any

import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from a2a_t.observability.sdk_trace import trace_facade


@pytest.fixture()
def exporter() -> Iterator[InMemorySpanExporter]:
    exporter = InMemorySpanExporter()
    trace.set_tracer_provider(TracerProvider())
    trace.get_tracer_provider().add_span_processor(SimpleSpanProcessor(exporter))
    return exporter


class _SyncFacade:
    def generate_task_prompt(self, text: str) -> str:
        return "processed:" + text

    def _private(self) -> str:
        return "private"


class _AsyncFacade:
    async def generate_task_prompt(self, text: str) -> str:
        return "processed:" + text


def test_wraps_sync_methods_with_span(exporter: InMemorySpanExporter) -> None:
    facade = trace_facade(_SyncFacade())
    assert facade.generate_task_prompt("hi") == "processed:hi"
    spans = {s.name: s for s in exporter.get_finished_spans()}
    assert "a2at.sdk.custom.generate_task_prompt" in spans
    assert spans["a2at.sdk.custom.generate_task_prompt"].attributes == {}  # 零自定义属性
    assert "_private" not in spans


def test_role_guessing_and_override(exporter: InMemorySpanExporter) -> None:
    class ClientLike:
        def run(self) -> None: ...

    trace_facade(ClientLike()).run()

    class ServerLike:
        def run(self) -> None: ...

    trace_facade(ServerLike()).run()

    class Plain:
        def run(self) -> None: ...

    trace_facade(Plain(), role="orchestrator").run()
    names = {s.name for s in exporter.get_finished_spans()}
    assert "a2at.sdk.client.run" in names
    assert "a2at.sdk.server.run" in names
    assert "a2at.sdk.orchestrator.run" in names


async def test_wraps_async_methods_awaited(exporter: InMemorySpanExporter) -> None:
    facade = trace_facade(_AsyncFacade())
    assert inspect.iscoroutinefunction(facade.generate_task_prompt)
    result = await facade.generate_task_prompt("hi")
    assert result == "processed:hi"
    assert "a2at.sdk.custom.generate_task_prompt" in {s.name for s in exporter.get_finished_spans()}


def test_exception_recorded_and_raised(exporter: InMemorySpanExporter) -> None:
    class _Boom:
        def run(self) -> None:
            raise ValueError("boom")

    with pytest.raises(ValueError):
        trace_facade(_Boom()).run()
    from opentelemetry.trace import StatusCode

    span = exporter.get_finished_spans()[-1]
    assert span.name == "a2at.sdk.custom.run"
    assert span.status.status_code == StatusCode.ERROR


def test_methods_filter_and_original_preserved(exporter: InMemorySpanExporter) -> None:
    facade: Any = trace_facade(_SyncFacade(), methods=["generate_task_prompt"])
    assert facade.generate_task_prompt.__name__ == "generate_task_prompt"  # functools.wraps
    # 类未被修改（实例级包装）
    assert "_SyncFacade_wrapped" not in str(type(facade))


async def test_wraps_asyncgen_methods_span_covers_iteration(exporter: InMemorySpanExporter) -> None:
    """M6: async generator 方法（流式 facade 的高概率形态）的 span 必须覆盖整个迭代——
    inspect.iscoroutinefunction 对 asyncgen 函数返回 False，旧逻辑落入 sync 包装：
    调用只创建生成器对象，span 在任何迭代发生前就瞬时结束且无任何报错。"""
    gate = asyncio.Event()

    class StreamFacade:
        async def stream(self) -> Any:
            yield "a"
            await gate.wait()
            yield "b"

    facade = trace_facade(StreamFacade())
    gen = facade.stream()
    assert await gen.__anext__() == "a"
    # 迭代中途：span 仍在记录（尚未结束；get_finished_spans 返回 tuple）
    assert not exporter.get_finished_spans()
    gate.set()
    assert [item async for item in gen] == ["b"]
    assert [span.name for span in exporter.get_finished_spans()] == ["a2at.sdk.custom.stream"]
