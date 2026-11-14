r"""LangGraph execution graph of the RAN NMS agent.

The graph is a minimal ReAct loop built from LangGraph primitives rather than the deprecated
``create_react_agent`` helper:

```
START -> agent --(tool calls)--> tools -> agent
             \--(no tool calls)--> END
```

``agent`` invokes the planner model with the system prompt and the accumulated messages; ``tools``
runs the guarded RAN NMS tools. Each accepted Task builds its own graph (the tools close over a
per-execution :class:`~agents.server_agent.langgraph_tools.PlanGuard`), so no shared state leaks
between concurrent Tasks.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Annotated, Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import BaseMessage, SystemMessage
from langchain_core.tools import BaseTool
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.graph.state import CompiledStateGraph
from langgraph.prebuilt import ToolNode
from typing_extensions import TypedDict


class ExecutionState(TypedDict):
    """Messages exchanged by the ReAct loop."""

    messages: Annotated[list[BaseMessage], add_messages]


def build_execution_graph(
    *,
    model: BaseChatModel,
    tools: Sequence[BaseTool],
    system_prompt: str,
) -> CompiledStateGraph:
    """Build one compiled ReAct graph.

    Args:
        model: planner chat model.
        tools: guarded tools of this execution.
        system_prompt: instructions prepended to every planner turn.

    Returns:
        The compiled graph; invoke it with ``{"messages": [HumanMessage(...)]}``.
    """
    bound_model = model.bind_tools(list(tools))
    tool_node = ToolNode(list(tools))

    async def agent_node(state: ExecutionState) -> dict[str, Any]:
        """Invoke the planner with the system prompt and the accumulated messages."""
        messages = [SystemMessage(content=system_prompt), *state["messages"]]
        response = await bound_model.ainvoke(messages)
        return {"messages": [response]}

    def route(state: ExecutionState) -> str:
        """Route to the tool node while the last model turn requests tools."""
        last = state["messages"][-1]
        return "tools" if getattr(last, "tool_calls", None) else END

    # LangGraph's state bound is a Protocol over TypedDict internals; ty does not yet match a
    # TypedDict against it, although the runtime does.
    builder = StateGraph(ExecutionState)  # ty: ignore[invalid-argument-type]
    builder.add_node("agent", agent_node)
    builder.add_node("tools", tool_node)
    builder.add_edge(START, "agent")
    builder.add_conditional_edges("agent", route, {"tools": "tools", END: END})
    builder.add_edge("tools", "agent")
    return builder.compile()


__all__ = ["build_execution_graph"]
