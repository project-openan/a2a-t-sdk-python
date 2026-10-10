"""Entry point for the mock registry center (uvicorn + Starlette)."""

from __future__ import annotations

import os
from pathlib import Path

import uvicorn
from dotenv import dotenv_values

from agentcard_example.registry_routes import build_registry_app


def resolve_registry_bind(*, env_path: Path | None = None) -> tuple[str, int]:
    resolved_env_path = env_path or Path.cwd() / ".env"
    env_values = dotenv_values(resolved_env_path) if resolved_env_path.exists() else {}
    host = str(
        env_values.get("REGISTRY_CENTER_HOST")
        or os.environ.get("REGISTRY_CENTER_HOST")
        or "127.0.0.1"
    )
    raw_port = (
        env_values.get("REGISTRY_CENTER_PORT")
        or os.environ.get("REGISTRY_CENTER_PORT")
        or "5001"
    )
    try:
        port = int(str(raw_port))
    except ValueError as exc:
        raise ValueError(f"Invalid REGISTRY_CENTER_PORT: {raw_port}") from exc
    return host, port


def main() -> None:
    """Start the mock registry center uvicorn server."""
    env_path = Path.cwd() / ".env"
    host, port = resolve_registry_bind(env_path=env_path)
    print(f"[registry] startup: host={host} port={port}")
    app = build_registry_app()
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    main()
