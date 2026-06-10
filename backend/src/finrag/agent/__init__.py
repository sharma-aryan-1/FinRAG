"""FinRAG agent — LangGraph state machine over the Decision-15 tools.

Public surface: `run_agent(question)` returns the final AgentState
(answer + route + chunks + trace + usage). `main.py`'s /agent endpoint and the
graph's own __main__ are the two callers.
"""

from __future__ import annotations

from finrag.agent.graph import build_graph, get_agent, run_agent
from finrag.agent.state import AgentState

__all__ = ["run_agent", "get_agent", "build_graph", "AgentState"]
