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

"""Verify A2A-T observability spans flow into Langfuse cloud."""
import asyncio
import os

# 1. Langfuse init (reads LANGFUSE_* env vars; never hard-code credentials)
if not os.environ.get("LANGFUSE_PUBLIC_KEY") or not os.environ.get("LANGFUSE_SECRET_KEY"):
    raise SystemExit("Set LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY (optionally LANGFUSE_BASE_URL).")
os.environ.setdefault("LANGFUSE_BASE_URL", "https://us.cloud.langfuse.com")

from langfuse import Langfuse

lf = Langfuse()
print("Langfuse initialized, base_url:", os.environ.get("LANGFUSE_BASE_URL"))

# Langfuse v5 sets a global TracerProvider — our setup() will detect it and skip
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
