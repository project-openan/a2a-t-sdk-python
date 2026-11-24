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

"""Entry point for the mock registry center (uvicorn + Starlette)."""

from __future__ import annotations

import logging
import os
from pathlib import Path

import uvicorn
from dotenv import dotenv_values
from shared.logging_setup import configure_logging

from demo.registry.registry_routes import build_registry_app

_LOGGER = logging.getLogger("ran_energy_saving.registry")


def resolve_registry_bind(*, env_path: Path | None = None) -> tuple[str, int]:
    resolved_env_path = env_path or Path.cwd() / ".env"
    env_values = dotenv_values(resolved_env_path) if resolved_env_path.exists() else {}
    host = str(
        os.environ.get("REGISTRY_CENTER_HOST")
        or env_values.get("REGISTRY_CENTER_HOST")
        or "127.0.0.1"
    )
    raw_port = (
        os.environ.get("REGISTRY_CENTER_PORT")
        or env_values.get("REGISTRY_CENTER_PORT")
        or "5001"
    )
    try:
        port = int(str(raw_port))
    except ValueError as exc:
        raise ValueError(f"Invalid REGISTRY_CENTER_PORT: {raw_port}") from exc
    return host, port


def main() -> None:
    """Start the mock registry center uvicorn server."""
    configure_logging()
    env_path = Path.cwd() / ".env"
    host, port = resolve_registry_bind(env_path=env_path)
    _LOGGER.info("registry starting: host=%s port=%s", host, port)
    app = build_registry_app()
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    main()
