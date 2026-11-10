"""Simulator configuration and fidelity profile."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Final

#: Fidelity profiles.
FIDELITY_STRICT: Final[str] = "strict"
FIDELITY_FAST: Final[str] = "fast"
SUPPORTED_FIDELITIES: Final[tuple[str, ...]] = (FIDELITY_STRICT, FIDELITY_FAST)

#: Negotiation carriers: a standalone ``Message`` or a Task in ``INPUT_REQUIRED``.
CARRIER_MESSAGE: Final[str] = "message"
CARRIER_TASK: Final[str] = "task"
SUPPORTED_CARRIERS: Final[tuple[str, ...]] = (CARRIER_MESSAGE, CARRIER_TASK)

#: Task document shapes: the vendor's nested shape, or the extra top-level fields.
TASK_SHAPE_STANDARD: Final[str] = "standard"
TASK_SHAPE_TOPLEVEL: Final[str] = "toplevel"
SUPPORTED_TASK_SHAPES: Final[tuple[str, ...]] = (TASK_SHAPE_STANDARD, TASK_SHAPE_TOPLEVEL)

#: Plain-A2A failure policies.
PURE_FAILURE_COMPLETED: Final[str] = "completed"
PURE_FAILURE_RETRY: Final[str] = "retry"
PURE_FAILURE_REJECTED: Final[str] = "rejected"
SUPPORTED_PURE_FAILURES: Final[tuple[str, ...]] = (
    PURE_FAILURE_COMPLETED,
    PURE_FAILURE_RETRY,
    PURE_FAILURE_REJECTED,
)

#: Plain-A2A question styles. ``issue`` renders the business issue it found; ``confirm`` asks the
#: vendor's fixed sentence (``Not feasible, please confirm``) on every new exchange, which is how
#: the real NMS negotiates and what the OSP P-column answers reply to.
PURE_QUESTION_ISSUE: Final[str] = "issue"
PURE_QUESTION_CONFIRM: Final[str] = "confirm"
SUPPORTED_PURE_QUESTIONS: Final[tuple[str, ...]] = (PURE_QUESTION_ISSUE, PURE_QUESTION_CONFIRM)
PURE_QUESTION_ENV_KEY: Final[str] = "A2AT_SAMPLE_NMS_SIM_PURE_QUESTION"

#: Window acceptance policies.
WINDOW_POLICY_EXACT: Final[str] = "exact"
WINDOW_POLICY_ANY: Final[str] = "any"
SUPPORTED_WINDOW_POLICIES: Final[tuple[str, ...]] = (WINDOW_POLICY_EXACT, WINDOW_POLICY_ANY)

#: Whether a request must state a guaranteed rate. The vendor accepts an area-level request
#: without one (default no-disruption mode), and the OSP corpus contains such cases; the strict
#: default keeps the historical behavior for demos and benchmarks.
RATE_POLICY_REQUIRED: Final[str] = "required"
RATE_POLICY_OPTIONAL: Final[str] = "optional"
SUPPORTED_RATE_POLICIES: Final[tuple[str, ...]] = (RATE_POLICY_REQUIRED, RATE_POLICY_OPTIONAL)
RATE_POLICY_ENV_KEY: Final[str] = "A2AT_SAMPLE_NMS_SIM_RATE_POLICY"
#: Acceptance rehearsal: propose a feasibility negotiation when a power outage lasts longer than
#: four hours *and* the guaranteed rate exceeds 2 Mbps (``shared.energy_rules`` holds the rule).
#: The vendor's feasibility dialogue covers long outages; short outages (<= 4h) stay at zero
#: rounds, which is what the OSP corpus expects.
LONG_OUTAGE_FEASIBILITY_ENV_KEY: Final[str] = "A2AT_SAMPLE_NMS_SIM_LONG_OUTAGE_FEASIBILITY"

#: Environment keys read by the standalone simulator entry point.
FIDELITY_ENV_KEY: Final[str] = "A2AT_SAMPLE_NMS_SIM_FIDELITY"
HOST_ENV_KEY: Final[str] = "A2AT_SAMPLE_NMS_SIM_HOST"
PORT_ENV_KEY: Final[str] = "A2AT_SAMPLE_NMS_SIM_PORT"
#: Local rehearsal aid: a newly created Task stays WORKING instead of completing, so the
#: presentation's Modify picker has a target. Continuing that Task completes it normally.
HOLD_WORKING_ENV_KEY: Final[str] = "A2AT_SAMPLE_NMS_SIM_HOLD_WORKING"
#: Reproduce the vendor's old cancel defect: ``:cancel`` always answers HTTP 503.
CANCEL_UNSUPPORTED_ENV_KEY: Final[str] = "A2AT_SAMPLE_NMS_SIM_CANCEL_UNSUPPORTED"
#: Business constraints the simulator enforces.
REGION_ENV_KEY: Final[str] = "A2AT_SAMPLE_NMS_SIM_REGION"
WINDOW_ENV_KEY: Final[str] = "A2AT_SAMPLE_NMS_SIM_WINDOW"
MAX_SAVING_RATE_ENV_KEY: Final[str] = "A2AT_SAMPLE_NMS_SIM_MAX_SAVING_RATE"
SITES_ENV_KEY: Final[str] = "A2AT_SAMPLE_NMS_SIM_SITES"
#: Negotiation shape: ``task`` (the real NMS) or ``message`` (the older local demo path).
NEGOTIATION_CARRIER_ENV_KEY: Final[str] = "A2AT_SAMPLE_NMS_SIM_NEGOTIATION_CARRIER"
#: Comma-separated kinds the simulator may propose: information,target,feasibility.
NEGOTIATION_KINDS_ENV_KEY: Final[str] = "A2AT_SAMPLE_NMS_SIM_NEGOTIATION_KINDS"
MAX_ROUNDS_ENV_KEY: Final[str] = "A2AT_SAMPLE_NMS_SIM_MAX_ROUNDS"
#: ``standard`` keeps the vendor's nested shape; ``toplevel`` adds top-level state/timestamp.
TASK_SHAPE_ENV_KEY: Final[str] = "A2AT_SAMPLE_NMS_SIM_TASK_SHAPE"
#: What a plain-A2A request does when a follow-up still cannot be served. The vendor answers
#: ``completed`` (a refusal dressed as success); ``retry`` asks again; ``rejected`` ends the Task.
PURE_FAILURE_ENV_KEY: Final[str] = "A2AT_SAMPLE_NMS_SIM_PURE_FAILURE"
#: ``exact`` accepts only the configured ``window`` (the historical demo constraint); ``any``
#: accepts every parsed window, which is what the vendor does and what the OSP cases need.
WINDOW_POLICY_ENV_KEY: Final[str] = "A2AT_SAMPLE_NMS_SIM_WINDOW_POLICY"

#: Test region the real 910 environment accepts.
DEFAULT_REGION: Final[str] = "A2ATestRegion"
#: Test activation window the real 910 environment accepts.
DEFAULT_WINDOW: Final[str] = "00:00~06:00"
#: Maximum energy-saving rate of the simulator cell.
DEFAULT_MAX_SAVING_RATE: Final[float] = 35.0
#: Negotiation round budget before the simulator aborts.
DEFAULT_MAX_ROUNDS: Final[int] = 5
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
        sites: Site names shown in the report text only.
        token_user: Accepted northbound user.
        token_value: Accepted password.
        task_limit: Number of tasks kept in the list.
        hold_working: Keep a newly created Task WORKING so a Modify can target it. Continuing
            an existing Task still completes it.
        cancel_supported: Accept ``:cancel`` and move the Task to ``TASK_STATE_CANCELED``.
            ``False`` reproduces the vendor's older HTTP 503 answer.
        negotiation_carrier: ``task`` carries proposals in ``INPUT_REQUIRED`` status messages,
            ``message`` carries them as standalone messages.
        negotiation_kinds: Negotiation kinds the simulator may propose.
        max_rounds: Round budget before the simulator answers ``ABORT``.
        task_shape: ``toplevel`` adds top-level ``state``/``timestamp`` to Task documents.
        pure_failure: What a plain-A2A follow-up does when it still cannot be served:
            ``completed`` (vendor refusal dressed as success), ``retry`` or ``rejected``.
        window_policy: ``exact`` accepts only ``window``; ``any`` accepts every parsed window.
        pure_question: ``issue`` renders the business issue it found; ``confirm`` asks the vendor's
            fixed ``Not feasible, please confirm`` on every new pure exchange.
    """

    fidelity: str = FIDELITY_STRICT
    heartbeat_seconds: float = 15.0
    working_delay: float = 2.0
    completion_delay: float = 4.0
    token_ttl_seconds: int = 1800
    region: str = DEFAULT_REGION
    window: str = DEFAULT_WINDOW
    max_saving_rate: float = DEFAULT_MAX_SAVING_RATE
    sites: tuple[str, ...] = ()
    token_user: str = DEFAULT_TOKEN_USER
    token_value: str = DEFAULT_TOKEN_VALUE
    task_limit: int = DEFAULT_TASK_LIMIT
    hold_working: bool = False
    cancel_supported: bool = True
    negotiation_carrier: str = CARRIER_TASK
    negotiation_kinds: tuple[str, ...] = ("information", "target", "feasibility")
    max_rounds: int = DEFAULT_MAX_ROUNDS
    task_shape: str = TASK_SHAPE_STANDARD
    pure_failure: str = PURE_FAILURE_COMPLETED
    window_policy: str = WINDOW_POLICY_EXACT
    rate_policy: str = RATE_POLICY_REQUIRED
    long_outage_feasibility: bool = False
    pure_question: str = PURE_QUESTION_ISSUE

    def __post_init__(self) -> None:
        """Apply the fast profile to settings built directly, not only through ``from_env``.

        ``fast`` is documented as "finishes immediately"; the environment path already zeroes the
        delays, but a direct ``SimConfig(fidelity=FIDELITY_FAST)`` used to keep the strict 2 s + 4 s
        wall-clock delays. Normalizing here keeps both construction paths identical.

        A ``confirm`` dialogue may need several rounds (the OSP outage cases concede 10→5→2 Mbps);
        the vendor's default ``completed`` refusal would stop after the first answer, so the
        replay profile asks again until the round budget is spent.
        """
        if self.fidelity == FIDELITY_FAST:
            object.__setattr__(self, "heartbeat_seconds", 0.0)
            object.__setattr__(self, "working_delay", 0.0)
            object.__setattr__(self, "completion_delay", 0.0)
        if self.pure_question == PURE_QUESTION_CONFIRM and self.pure_failure == PURE_FAILURE_COMPLETED:
            object.__setattr__(self, "pure_failure", PURE_FAILURE_RETRY)

    @property
    def strict(self) -> bool:
        """Return whether the strict fidelity profile is active."""
        return self.fidelity != FIDELITY_FAST

    @classmethod
    def from_env(cls) -> SimConfig:
        """Build the settings from the process environment.

        Raises:
            ValueError: when a profile, carrier, kind, task shape or round budget is invalid.
        """
        fidelity = os.environ.get(FIDELITY_ENV_KEY, FIDELITY_STRICT).strip().lower()
        if fidelity not in SUPPORTED_FIDELITIES:
            raise ValueError(f"Unsupported {FIDELITY_ENV_KEY}: {fidelity}; expected one of {SUPPORTED_FIDELITIES}")
        carrier = os.environ.get(NEGOTIATION_CARRIER_ENV_KEY, CARRIER_TASK).strip().lower()
        if carrier not in SUPPORTED_CARRIERS:
            raise ValueError(
                f"Unsupported {NEGOTIATION_CARRIER_ENV_KEY}: {carrier}; "
                f"expected one of {SUPPORTED_CARRIERS}"
            )
        task_shape = os.environ.get(TASK_SHAPE_ENV_KEY, TASK_SHAPE_STANDARD).strip().lower()
        if task_shape not in SUPPORTED_TASK_SHAPES:
            raise ValueError(
                f"Unsupported {TASK_SHAPE_ENV_KEY}: {task_shape}; expected one of {SUPPORTED_TASK_SHAPES}"
            )
        pure_failure = os.environ.get(PURE_FAILURE_ENV_KEY, PURE_FAILURE_COMPLETED).strip().lower()
        if pure_failure not in SUPPORTED_PURE_FAILURES:
            raise ValueError(
                f"Unsupported {PURE_FAILURE_ENV_KEY}: {pure_failure}; "
                f"expected one of {SUPPORTED_PURE_FAILURES}"
            )
        window_policy = os.environ.get(WINDOW_POLICY_ENV_KEY, WINDOW_POLICY_EXACT).strip().lower()
        if window_policy not in SUPPORTED_WINDOW_POLICIES:
            raise ValueError(
                f"Unsupported {WINDOW_POLICY_ENV_KEY}: {window_policy}; "
                f"expected one of {SUPPORTED_WINDOW_POLICIES}"
            )
        rate_policy = os.environ.get(RATE_POLICY_ENV_KEY, RATE_POLICY_REQUIRED).strip().lower()
        if rate_policy not in SUPPORTED_RATE_POLICIES:
            raise ValueError(
                f"Unsupported {RATE_POLICY_ENV_KEY}: {rate_policy}; "
                f"expected one of {SUPPORTED_RATE_POLICIES}"
            )
        pure_question = os.environ.get(PURE_QUESTION_ENV_KEY, PURE_QUESTION_ISSUE).strip().lower()
        if pure_question not in SUPPORTED_PURE_QUESTIONS:
            raise ValueError(
                f"Unsupported {PURE_QUESTION_ENV_KEY}: {pure_question}; "
                f"expected one of {SUPPORTED_PURE_QUESTIONS}"
            )
        kinds = _parse_kinds(os.environ.get(NEGOTIATION_KINDS_ENV_KEY, ""))
        try:
            max_rounds = int(os.environ.get(MAX_ROUNDS_ENV_KEY, str(DEFAULT_MAX_ROUNDS)).strip())
        except ValueError as exc:
            raise ValueError(f"Invalid {MAX_ROUNDS_ENV_KEY}: {os.environ.get(MAX_ROUNDS_ENV_KEY)!r}") from exc
        if max_rounds < 1:
            raise ValueError(f"{MAX_ROUNDS_ENV_KEY} must be at least 1")
        try:
            max_saving_rate = float(
                os.environ.get(MAX_SAVING_RATE_ENV_KEY, str(DEFAULT_MAX_SAVING_RATE)).strip()
            )
        except ValueError as exc:
            raise ValueError(
                f"Invalid {MAX_SAVING_RATE_ENV_KEY}: {os.environ.get(MAX_SAVING_RATE_ENV_KEY)!r}"
            ) from exc
        profile = cls(
            fidelity=fidelity,
            region=os.environ.get(REGION_ENV_KEY, DEFAULT_REGION).strip() or DEFAULT_REGION,
            window=os.environ.get(WINDOW_ENV_KEY, DEFAULT_WINDOW).strip() or DEFAULT_WINDOW,
            max_saving_rate=max_saving_rate,
            sites=_parse_sites(os.environ.get(SITES_ENV_KEY, "")),
            hold_working=_truthy(os.environ.get(HOLD_WORKING_ENV_KEY, "")),
            cancel_supported=not _truthy(os.environ.get(CANCEL_UNSUPPORTED_ENV_KEY, "")),
            negotiation_carrier=carrier,
            negotiation_kinds=kinds,
            max_rounds=max_rounds,
            task_shape=task_shape,
            pure_failure=pure_failure,
            window_policy=window_policy,
            rate_policy=rate_policy,
            long_outage_feasibility=_truthy(os.environ.get(LONG_OUTAGE_FEASIBILITY_ENV_KEY, "")),
            pure_question=pure_question,
        )
        # The fast profile keeps the wire contract but removes the wall-clock delays; the dataclass
        # applies that normalization in ``__post_init__``, so ``profile`` is ready as built.
        return profile


def _truthy(value: str) -> bool:
    """Return whether one environment value means "on"."""
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _parse_kinds(value: str) -> tuple[str, ...]:
    """Parse the negotiation-kind list; an empty value means all kinds."""
    if not value.strip():
        return ("information", "target", "feasibility")
    kinds = tuple(item.strip().lower() for item in value.split(",") if item.strip())
    unknown = [kind for kind in kinds if kind not in {"information", "target", "feasibility"}]
    if unknown:
        raise ValueError(f"Unsupported negotiation kind(s) in {NEGOTIATION_KINDS_ENV_KEY}: {', '.join(unknown)}")
    return kinds


def _parse_sites(value: str) -> tuple[str, ...]:
    """Parse the comma-separated site list."""
    return tuple(item.strip() for item in value.split(",") if item.strip())
