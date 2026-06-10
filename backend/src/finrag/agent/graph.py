"""Wire the nodes into a LangGraph state machine.

    START → plan ──(vector|both)──→ retrieve ─┐
                  └──────(sql)────────────────┤
                                              ↓
                                agent(tool-loop) → END

`plan` does rewrite+route in one call (free-tier request budget). The
conditional edge is the one branch: sql-only questions skip vector retrieval
and go straight to the tool-loop (the agent calls sql_query itself);
vector/both questions pre-fetch chunks first.
"""

from __future__ import annotations

from functools import lru_cache

from langgraph.graph import END, START, StateGraph

from finrag.agent import nodes
from finrag.agent.state import AgentState


def _after_route(state: AgentState) -> str:
    return "retrieve" if state.get("route") in ("vector", "both") else "agent"


def build_graph():
    g = StateGraph(AgentState)
    g.add_node("plan", nodes.plan)
    g.add_node("retrieve", nodes.retrieve)
    g.add_node("agent", nodes.agent)

    g.add_edge(START, "plan")
    g.add_conditional_edges(
        "plan", _after_route, {"retrieve": "retrieve", "agent": "agent"}
    )
    g.add_edge("retrieve", "agent")
    g.add_edge("agent", END)
    return g.compile()


@lru_cache(maxsize=1)
def get_agent():
    """Compiled graph, built once per process (compilation is non-trivial)."""
    return build_graph()


def run_agent(question: str) -> AgentState:
    return get_agent().invoke({"question": question, "trace": []})


if __name__ == "__main__":
    import json

    final = run_agent("How did Apple's services revenue change in fiscal 2023, and by what percent?")
    print("ROUTE :", final.get("route"))
    print("ANSWER:", final.get("answer"))
    print("USAGE :", final.get("usage"))
    print("TRACE :")
    for step in final.get("trace", []):
        print("  -", json.dumps(step)[:200])
