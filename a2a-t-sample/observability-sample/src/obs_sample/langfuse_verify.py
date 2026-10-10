"""Verify A2A-T observability spans flow into Langfuse cloud."""
import asyncio
import os

# 1. Langfuse init (reads LANGFUSE_* env vars or explicit params)
os.environ["LANGFUSE_PUBLIC_KEY"] = "pk-lf-661d8d9f-d5e1-4bd4-ae9f-68c7f1c93b8a"
os.environ["LANGFUSE_SECRET_KEY"] = "sk-lf-ff9d6ca0-4eb8-4288-9839-eca9e5a12a8f"
os.environ["LANGFUSE_BASE_URL"] = "https://us.cloud.langfuse.com"

from langfuse import Langfuse

lf = Langfuse()
print("Langfuse initialized, base_url:", os.environ.get("LANGFUSE_BASE_URL"))

# Langfuse v4 sets a global TracerProvider — our setup() will detect it and skip
from opentelemetry import trace  # noqa: E402

tp = trace.get_tracer_provider()
print(f"TracerProvider after Langfuse init: {type(tp).__name__}")

# 2. A2A-T observability decorators (should use Langfuse's provider)
from a2a_t.observability.client.transport_decorator import (  # noqa: E402
    A2ATClientTransportDecorator,
)


# Simulate: create a decorator around a fake inner transport
class FakeInner:
    async def send_message(self, request, *, context=None):
        return "fake-response"

    async def send_message_streaming(self, request, *, context=None):
        yield "fake-event-1"
        yield "fake-event-2"

inner = FakeInner()
decorator = A2ATClientTransportDecorator(inner)

# Simulate a send_message call
class FakeRequest:
    message = None

async def test():
    try:
        result = await decorator.send_message(FakeRequest(), context=None)
        print("decorator send_message:", result)
    except Exception as e:
        print(f"decorator call: {type(e).__name__}: {e}")

asyncio.run(test())

# 3. Flush Langfuse
lf.flush()
print("Langfuse flushed — check the Langfuse dashboard for spans")
