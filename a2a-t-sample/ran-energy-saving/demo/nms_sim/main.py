"""Standalone entry point of the NMS simulator."""

from __future__ import annotations

import logging
import os

import uvicorn
from shared.logging_setup import configure_logging

from demo.nms_sim.app import build_sim_app
from demo.nms_sim.config import HOST_ENV_KEY, PORT_ENV_KEY, SimConfig

_LOGGER = logging.getLogger("ran_energy_saving.nms_sim")


def main() -> None:
    """Start the simulator with uvicorn."""
    configure_logging()
    config = SimConfig.from_env()
    host = os.environ.get(HOST_ENV_KEY, "127.0.0.1").strip() or "127.0.0.1"
    port = int(os.environ.get(PORT_ENV_KEY, "18080"))
    _LOGGER.info(
        "NMS simulator starting: host=%s port=%s fidelity=%s transport=stream+webhook "
        "carrier=%s kinds=%s region=%s window=%s hold_working=%s max_rounds=%s task_shape=%s",
        host,
        port,
        config.fidelity,
        config.negotiation_carrier,
        ",".join(config.negotiation_kinds),
        config.region,
        config.window,
        config.hold_working,
        config.max_rounds,
        config.task_shape,
    )
    uvicorn.run(build_sim_app(config), host=host, port=port)


if __name__ == "__main__":
    main()
