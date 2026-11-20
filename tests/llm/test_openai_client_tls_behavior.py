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

# ruff: noqa: E402

"""Behavioral TLS tests for the OpenAI-compatible provider.

A local HTTPS server presents a self-signed certificate for ``localhost`` while the client
connects to ``127.0.0.1``, so the default path fails on both the certificate chain and the
hostname check. Mirrors the Java ``OpenAIClientTlsVerificationTest``.
"""

from __future__ import annotations

import json
import ssl
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import trustme

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = PROJECT_ROOT / "src"

if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from a2a_t.llm.errors import LLMRuntimeError
from a2a_t.llm.models import LLMClientConfig
from a2a_t.llm.providers.openai import OpenAIClient

_CHAT_COMPLETION = {
    "id": "chatcmpl_test",
    "object": "chat.completion",
    "created": 1,
    "model": "gpt-4o-mini",
    "choices": [
        {
            "index": 0,
            "message": {"role": "assistant", "content": '{"device_type":"router"}'},
            "finish_reason": "stop",
        }
    ],
    "usage": {"prompt_tokens": 7, "completion_tokens": 2, "total_tokens": 9},
}


class _Handler(BaseHTTPRequestHandler):
    requests = 0

    def do_POST(self) -> None:
        _Handler.requests += 1
        length = int(self.headers.get("Content-Length") or 0)
        self.rfile.read(length)
        body = json.dumps(_CHAT_COMPLETION).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args: object) -> None:
        pass


class SelfSignedHttpsServer:
    """Local HTTPS server whose certificate is not trusted and does not cover 127.0.0.1."""

    def __init__(self) -> None:
        _Handler.requests = 0
        self._server = HTTPServer(("127.0.0.1", 0), _Handler)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        authority = trustme.CA()
        authority.issue_cert("localhost").configure_cert(context)
        self._server.socket = context.wrap_socket(self._server.socket, server_side=True)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    @property
    def base_url(self) -> str:
        return f"https://127.0.0.1:{self._server.server_port}"

    @property
    def requests(self) -> int:
        return _Handler.requests

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)


def _config(base_url: str, ssl_verify: bool) -> LLMClientConfig:
    return LLMClientConfig(
        provider="openai",
        model="gpt-4o-mini",
        api_key="sk-test",
        base_url=f"{base_url}/v1",
        history_window=10,
        max_tokens=None,
        temperature=None,
        timeout_seconds=5.0,
        session_max_total=300,
        session_max_per_provider=100,
        ssl_verify=ssl_verify,
    )


def _has_ssl_error_in_cause_chain(error: BaseException) -> bool:
    seen: set[int] = set()
    current: BaseException | None = error
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, ssl.SSLError):
            return True
        current = current.__cause__ if current.__cause__ is not None else current.__context__
    return False


class OpenAIClientTlsBehaviorTest(unittest.TestCase):
    def test_ssl_verification_disabled_connects_to_self_signed_endpoint(self) -> None:
        server = SelfSignedHttpsServer()
        try:
            client = OpenAIClient(_config(server.base_url, ssl_verify=False))
            with self.assertLogs("a2a_t.llm.providers.openai", level="WARNING") as logs:
                response = client.structured(
                    messages=[{"role": "user", "content": "extract"}],
                    json_schema={"type": "object"},
                )
        finally:
            server.close()

        self.assertEqual(response.content, '{"device_type":"router"}')
        self.assertEqual(response.model, "gpt-4o-mini")
        self.assertEqual(server.requests, 1)
        self.assertEqual(len(logs.output), 1)
        self.assertIn("TLS certificate chain and hostname verification are disabled", logs.output[0])

    def test_ssl_verification_enabled_by_default_rejects_self_signed_endpoint(self) -> None:
        server = SelfSignedHttpsServer()
        try:
            client = OpenAIClient(_config(server.base_url, ssl_verify=True))
            with self.assertRaises(LLMRuntimeError) as ctx:
                client.structured(
                    messages=[{"role": "user", "content": "extract"}],
                    json_schema={"type": "object"},
                )
        finally:
            server.close()

        self.assertTrue(
            _has_ssl_error_in_cause_chain(ctx.exception),
            f"expected an SSL error in the cause chain, got: {ctx.exception.__cause__!r}",
        )
        self.assertEqual(server.requests, 0)


if __name__ == "__main__":
    unittest.main()
