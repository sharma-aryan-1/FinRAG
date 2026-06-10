"""LangGraph state for the FinRAG agent.

The state is a TypedDict threaded through every node; each node returns a
partial dict that LangGraph merges in. `trace` uses an additive reducer so
every node *appends* its step (rather than overwriting) — that accumulated
list is what Decision 18's frontend renders as the agent's visible reasoning.
"""

from __future__ import annotations

import operator
from typing import Annotated, Any, TypedDict

from finrag.retrieval.vector import RetrievedChunk


class AgentState(TypedDict, total=False):
    question: str                 # raw user question
    rewritten_query: str          # normalized, self-contained query
    route: str                    # "vector" | "sql" | "both"
    chunks: list[RetrievedChunk]  # vector context (empty for sql-only routes)
    answer: str                   # final grounded answer
    usage: dict[str, int]         # token totals across all agent LLM calls
    # Additive: nodes append step records; the reducer concatenates them.
    trace: Annotated[list[dict[str, Any]], operator.add]
