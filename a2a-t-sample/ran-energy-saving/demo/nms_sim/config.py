"""Simulator configuration and fidelity profile."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Final

#: Fidelity profiles.
FIDELITY_STRICT: Final[str] = "strict"
FIDELITY_FAST: Final[str] = "fast"
SUPPORTED_FIDELITIES: Final[tuple[str, ...]] = (FIDELITY_STRICT, FIDELITY_FAST)

#: Environment keys read by the standalone simulator entry point.
FIDELITY_ENV_KEY: Final[str] = "A2AT_SAMPLE_NMS_SIM_FIDELITY"
HOST_ENV_KEY: Final[str] = "A2AT_SAMPLE_NMS_SIM_HOST"
PORT_ENV_KEY: Final[str] = "A2AT_SAMPLE_NMS_SIM_PORT"
#: Local rehearsal aid: a newly created Task stays WORKING instead of completing, so the
#: presentation's Modify picker has a target. Continuing that Task completes it normally.
HOLD_WORKING_ENV_KEY: Final[str] = "A2AT_SAMPLE_NMS_SIM_HOLD_WORKING"

#: Test region the real 910 environment accepts.
DEFAULT_REGION: Final[str] = "A2ATestRegion"
#: Test activation window the real 910 environment accepts.
DEFAULT_WINDOW: Final[str] = "00:00~06:00"
#: Maximum energy-saving rate of the simulator cell.
DEFAULT_MAX_SAVING_RATE: Final[float] = 35.0
#: Task list size kept by the NMS (the real one keeps the latest 50).
DEFAULT_TASK_LIMIT: Final[int] = 50
#: Default token endpoint credentials of the test environment.
DEFAULT_TOKEN_USER: Final[str] = "snbitestDefaultNorth"
DEFAULT_TOKEN_VALUE: Final[str] = "Changeme_123"


@dataclass(frozen=True)
class SimConfig:
    """Resolved simulator settings.

    Attributes:
        fidelity: ``strict`` reproduces the streaming quirks; ``fast`` finishes immediately.
        heartbeat_seconds: Interval of the ``: heartbeat`` comments in strict mode.
        working_delay: Seconds between ``SUBMITTED`` and ``WORKING``.
        completion_delay: Seconds between ``WORKING`` and ``COMPLETED``.
        token_ttl_seconds: Session lifetime advertised by the token endpoint.
        region: Region the simulator serves.
        window: Activation window the simulator accepts.
        max_saving_rate: Maximum energy-saving rate of the simulator cell.
        token_user: Accepted northbound user.
        token_value: Accepted password.
        task_limit: Number of tasks kept in the list.
        hold_working: Keep a newly created Task WORKING so a Modify can target it. Continuing
            an existing Task still completes it.
    """

    fidelity: str = FIDELITY_STRICT
    heartbeat_seconds: float = 15.0
    working_delay: float = 2.0
    completion_delay: float = 4.0
    token_ttl_seconds: int = 1800
    region: str = DEFAULT_REGION
    window: str = DEFAULT_WINDOW
    max_saving_rate: float = DEFAULT_MAX_SAVING_RATE
    token_user: str = DEFAULT_TOKEN_USER
    token_value: str = DEFAULT_TOKEN_VALUE
    task_limit: int = DEFAULT_TASK_LIMIT
    hold_working: bool = False

    @property
    def strict(self) -> bool:
        """Return whether the strict fidelity profile is active."""
        return self.fidelity != FIDELITY_FAST

    @classmethod
    def from_env(cls) -> SimConfig:
        """Build the settings from the process environment.

        Raises:
            ValueError: when the fidelity profile is unknown.
        """
        fidelity = os.environ.get(FIDELITY_ENV_KEY, FIDELITY_STRICT).strip().lower()
        if fidelity not in SUPPORTED_FIDELITIES:
            raise ValueError(f"Unsupported {FIDELITY_ENV_KEY}: {fidelity}; expected one of {SUPPORTED_FIDELITIES}")
        hold_working = os.environ.get(HOLD_WORKING_ENV_KEY, "").strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
        profile = cls(fidelity=fidelity, hold_working=hold_working)
        if profile.strict:
            return profile
        # The fast profile keeps the wire contract but removes the wall-clock delays.
        return cls(
            fidelity=fidelity,
            heartbeat_seconds=0.0,
            working_delay=0.0,
            completion_delay=0.0,
            hold_working=hold_working,
        )
