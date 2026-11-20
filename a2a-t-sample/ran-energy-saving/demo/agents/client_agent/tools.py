"""ADK function tool of the RAN energy-saving client decision agent.

The client owns no network inventory. Region, site and cell names, and whether a goal is feasible,
are the server's authority, so the agent gets no tool that could answer them: a fabricated
``unknown_cell`` result would be a client-side judgment the client is not allowed to make.

The one tool exposes the client's own acceptance policy (throughput floor and mutable fields) so
the model can decide within the limits the operator configured, rather than guessing them.
"""

from __future__ import annotations

from collections.abc import Callable

from protocol.client_policy import ClientPolicy


def make_get_client_policy(policy: ClientPolicy) -> Callable[[], dict[str, object]]:
    """Build the ``get_client_policy`` ADK tool for one run's policy.

    Args:
        policy: Acceptance limits resolved from the sample configuration.

    Returns:
        A zero-argument tool returning the policy's JSON-serializable view.
    """

    def get_client_policy() -> dict[str, object]:
        """Return the client's own negotiation acceptance policy.

        Call this when the proposal's alternative or the client's tolerance decides the answer.
        It reports the lowest guaranteed throughput the client accepts without a human
        (``min_throughput_mbps``; null means no floor), the Task-T fields a negotiation may change,
        the fields the server owns, and the rules that follow from them.

        Returns:
            ``{"min_throughput_mbps", "mutable_fields", "entity_fields", "rules"}``.
        """
        return policy.view()

    return get_client_policy


__all__ = ["make_get_client_policy"]
