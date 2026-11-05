"""Standalone Web UI server, launched as a managed child by :mod:`demo.run_demo`.

Serving the UI in its own process (instead of calling ``uvicorn.run`` inside the launcher) puts its
PID into the launcher state, so ``--stop`` and an abruptly killed launcher can no longer leave the
UI holding the port.

Usage (normally invoked by the launcher, not by hand):
    python -m demo.web.serve --env-file <run/.env> --port 8080 --scenario night --mode auto
"""

from __future__ import annotations

import argparse
import os
import socket
from pathlib import Path

from protocol.approval_gate import DEFAULT_APPROVAL_TIMEOUT_SECONDS
from shared.run_constants import (
    BRAND_ORANGE,
    DEMO_PROTOCOLS,
    PROTOCOL_A2A_T,
    SUPPORTED_BRANDS,
)


def _build_parser() -> argparse.ArgumentParser:
    """Build the argument parser shared with the launcher."""
    from protocol.client_runtime import SUPPORTED_MODES
    from shared.config import SUPPORTED_DISCOVERY_MODES, SUPPORTED_TRANSPORTS

    from demo.scenario import SUPPORTED_SCENARIO_NAMES

    parser = argparse.ArgumentParser(description="Web UI server of the RAN energy-saving ADK sample.")
    parser.add_argument("--env-file", type=Path, required=True, help="per-run .env written by the launcher")
    parser.add_argument("--host", default="127.0.0.1", help="bind host")
    parser.add_argument("--port", type=int, required=True, help="bind port")
    parser.add_argument("--scenario", required=True, choices=list(SUPPORTED_SCENARIO_NAMES))
    parser.add_argument(
        "--mode",
        required=True,
        choices=[mode for mode in SUPPORTED_MODES if mode != "scripted"],
    )
    parser.add_argument("--transport", required=True, choices=list(SUPPORTED_TRANSPORTS))
    parser.add_argument(
        "--discovery",
        default=None,
        choices=list(SUPPORTED_DISCOVERY_MODES),
        help="run discovery default; the process .env value is used when omitted",
    )
    parser.add_argument(
        "--ui-mode",
        default="presentation",
        choices=("presentation", "explorer"),
    )
    parser.add_argument(
        "--brand",
        default=BRAND_ORANGE,
        choices=list(SUPPORTED_BRANDS),
        help="presentation color theme (default: orange)",
    )
    parser.add_argument("--server-url", default="", help="A2A server base URL used by the MIE approval")
    parser.add_argument("--approval-gate", action="store_true")
    parser.add_argument(
        "--approval-timeout", type=float, default=DEFAULT_APPROVAL_TIMEOUT_SECONDS
    )
    parser.add_argument("--allow-insecure-bind", action="store_true")
    parser.add_argument("--protocol", default=PROTOCOL_A2A_T, choices=list(DEMO_PROTOCOLS))
    parser.add_argument("--mie-url", default=None)
    return parser


def run_ui(
    *,
    env_path: Path,
    ui_port: int,
    scenario: str,
    mode: str,
    transport: str,
    discovery: str | None = None,
    ui_mode: str = "presentation",
    brand: str = BRAND_ORANGE,
    host: str = "127.0.0.1",
    server_url: str = "",
    approval_gate: bool = False,
    approval_timeout: float = DEFAULT_APPROVAL_TIMEOUT_SECONDS,
    allow_insecure_bind: bool = False,
    protocol: str = PROTOCOL_A2A_T,
    mie_url: str | None = None,
) -> int:
    """Serve the Web UI until interrupted, returning a process exit code."""
    import uvicorn
    from protocol.client_store import SqliteClientStore
    from shared.config import load_config
    from shared.http_security import is_loopback
    from shared.logging_setup import configure_logging

    from demo.catalog import DemoRunCatalog
    from demo.web.app import create_app

    configure_logging()
    config = load_config(env_path=env_path)
    client_store = (
        SqliteClientStore(config.client_db_path)
        if config.client_db_path is not None
        else None
    )
    try:
        app = create_app(
            config,
            env_path=env_path,
            catalog=DemoRunCatalog.from_config(config),
            client_store=client_store,
            memory_turns=config.client_memory_turns,
            default_scenario=scenario,
            default_mode=mode,
            default_transport=transport,
            default_discovery=discovery,
            bind_host=host,
            ui_mode=ui_mode,
            default_brand=brand,
            server_url=server_url or None,
            approval_gate=approval_gate,
            approval_timeout_seconds=approval_timeout,
            allow_insecure_bind=allow_insecure_bind,
            protocol=protocol,
            mie_url=mie_url,
            # The server trace emitter flushes in batches; aggregate the persisted metrics after
            # the same short window the SSE stream uses to forward its last spans.
            metrics_grace_seconds=2.0,
        )
    except ValueError as exc:
        print(f"[ui] {exc}")
        return 2
    trace_enabled = bool(os.environ.get("A2AT_SAMPLE_TRACE_TOKEN", "").strip())
    print(
        f"[ui] Web UI on http://{host}:{ui_port} (ui_mode={ui_mode}) "
        f"(language={config.language}, model_mode={config.model_mode} [{config.model_label}], "
        f"provider={config.adk_model_provider}, "
        f"defaults: scenario={scenario}, mode={mode}, transport={transport}, "
        f"approval_gate={approval_gate}, "
        f"server trace ingestion: {'enabled' if trace_enabled else 'disabled (no local token)'})"
    )
    if not is_loopback(host):
        try:
            lan_ip = socket.gethostbyname(socket.gethostname())
        except OSError:
            lan_ip = "<your-lan-ip>"
        if config.auth_token:
            print(f"[ui] share on the LAN: http://{lan_ip}:{ui_port}/?token=<A2AT_SAMPLE_AUTH_TOKEN>")
        else:
            print(
                "[ui] WARNING: the Web UI is reachable on the LAN without a bearer token; "
                "anyone who can reach the port can start runs. Use only on a trusted network. "
                f"Share: http://{lan_ip}:{ui_port}/"
            )
    try:
        uvicorn.run(app, host=host, port=ui_port, log_level="info")
    except KeyboardInterrupt:
        print("\n[ui] Web UI stopped")
        return 130
    return 0


def main(argv: list[str] | None = None) -> int:
    """Parse the launcher arguments and serve the UI."""
    args = _build_parser().parse_args(argv)
    return run_ui(
        env_path=args.env_file,
        ui_port=args.port,
        host=args.host,
        scenario=args.scenario,
        mode=args.mode,
        transport=args.transport,
        discovery=args.discovery,
        ui_mode=args.ui_mode,
        brand=args.brand,
        server_url=args.server_url,
        approval_gate=args.approval_gate,
        approval_timeout=args.approval_timeout,
        allow_insecure_bind=args.allow_insecure_bind,
        protocol=args.protocol,
        mie_url=args.mie_url,
    )


if __name__ == "__main__":
    raise SystemExit(main())
