"""The NMS execution agent a deployment plugs into the protocol shell.

``protocol.server_executor`` owns the A2A lifecycle, the artifact/status metadata and the tool
trace spans. The planner graph, its tools and its prompt belong to the NMS integration; a
deployment supplies them as a :class:`ServerAgentSpec` through a factory, so the core never imports
the demo agents.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from shared.config import SampleConfig


@dataclass(frozen=True)
class ServerAgentSpec:
    """One execution's planner graph and the metadata the shell needs to render it.

    Attributes:
        graph: Compiled LangGraph exposing ``astream``.
        model: Planner model used by the LLM trace spans.
        system_prompt: System prompt sent on every planner turn (trace detail).
        report_tool_name: Name of the tool whose successful result is the final report.
        initial_messages: Messages seeding the graph invocation.
        parse_tool_result: Parser of one tool result text into its JSON object.
    """

    graph: Any
    model: Any
    system_prompt: str
    report_tool_name: str
    initial_messages: Sequence[Any]
    parse_tool_result: Callable[[str], dict[str, object]]


class ServerAgentFactory(Protocol):
    """Builds the planner graph of one accepted task."""

    def __call__(
        self,
        config: SampleConfig | None,
        *,
        task_prompt: str,
        negotiation_context: Mapping[str, object] | None,
        approval_gate: Any | None = None,
        operation: str = "Create",
        existing_plan: Mapping[str, object] | None = None,
    ) -> ServerAgentSpec: ...
