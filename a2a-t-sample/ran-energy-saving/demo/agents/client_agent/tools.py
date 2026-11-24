"""ADK function tool of the RAN energy-saving client decision agent.

The client owns no network inventory. Region, site and cell names, and whether a goal is feasible,
are the server's authority, so the agent gets no tool that could answer them: a fabricated
``unknown_cell`` result would be a client-side judgment the client is not allowed to make.

The acceptance policy is static for a whole run, so it is stated in the decision prompt instead of
being fetched through a tool: a model that does not converge used to call such a tool dozens of
times in one turn.
"""

from __future__ import annotations

from collections.abc import Callable

from protocol.decision_provider import current_decision_basis
from shared.condition_checker import ConditionChecker


def make_check_client_condition(
    checker: ConditionChecker,
) -> Callable[[str, str], object]:
    """Build the ``check_client_condition`` ADK tool for one run's condition checker.

    Args:
        checker: client-side checker backed by the independent judge.

    Returns:
        A tool that answers any candidate value (not only the current proposal) from the
        decision basis in force for this task.
    """

    async def check_client_condition(
        question: str,
        candidate_value: str,
    ) -> dict[str, object]:
        """Ask whether one candidate value is acceptable under the client's own conditions.

        Use this for any value you are considering, including a value the current proposal does
        not contain. Answer ``yes`` / ``no`` / ``unknown`` with a short reason. The call is
        decision evidence; it does not itself change the negotiation.

        Returns:
            ``{"allowed": "yes|no|unknown", "reason": "..."}``.
        """
        answer = await checker.check(
            question,
            candidate_value,
            basis=current_decision_basis(),
        )
        return answer.to_dict()

    return check_client_condition


__all__ = ["make_check_client_condition"]
