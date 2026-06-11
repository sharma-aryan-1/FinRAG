"""The agent's LangGraph nodes.

Flow: rewrite_query → route → (retrieve?) → agent(tool-loop+synthesis) → END.

Design note: the handoff listed tool-loop and synthesize as separate steps, but
with native function-calling they're one node by construction — the loop runs
until the model stops emitting function_calls and produces its final text, and
that terminal text *is* the synthesis. We still emit a distinct 'synthesize'
trace event so the frontend (Decision 18) can render it as its own step.
"""

from __future__ import annotations

from functools import lru_cache

from finrag.agent.state import AgentState
from finrag.ingestion.facts import corpus_companies, corpus_years
from finrag.llm import generate_text, run_tool_loop_stream, synthesize
from finrag.llm.base import ToolCall, format_chunks_for_prompt
from finrag.retrieval.rerank import rerank_search

MAX_TOOL_ITERS = 5  # hard cap so a confused model can't loop forever


@lru_cache(maxsize=1)
def _corpus_grounding() -> str:
    """Tell the model the exact known universe so vague references ('these three
    companies', 'all of them') resolve to real corpus members instead of the
    model guessing (it would otherwise pull in Microsoft/Google). Data-driven —
    reads the loaded DuckDB, so it can never drift from what's queryable."""
    companies = corpus_companies()
    if not companies:  # corpus not loaded — emit nothing rather than a wrong claim
        return ""
    lo, hi = corpus_years()
    span = f"fiscal years {lo}–{hi}" if lo and hi else "the available fiscal years"
    listing = "; ".join(f"{name} ({ticker})" for ticker, name in companies)
    return (
        f"\n\nKNOWN CORPUS — the dataset contains EXACTLY these {len(companies)} "
        f"companies, {span}: {listing}.\n"
        "When the question refers to the companies without naming them ('these "
        "companies', 'the three companies', 'all of them', 'each company'), it means "
        "exactly this set — resolve the reference to these names. Never introduce a "
        "company outside this set; if asked about one that isn't listed, say it is not "
        "in the corpus rather than answering from general knowledge."
    )


def _stream_writer():
    """LangGraph custom-stream writer when the graph is driven by
    `graph.stream(..., stream_mode=[..., "custom"])` (the /agent/stream SSE
    path); a no-op otherwise (plain invoke / direct call). The same agent node
    therefore serves both /agent and /agent/stream without branching."""
    try:
        from langgraph.config import get_stream_writer

        return get_stream_writer()
    except Exception:
        return lambda _data: None

# ── Prompts ────────────────────────────────────────────────────────────────
# rewrite + route merged into ONE call to save a request against the free-tier
# 5-req/min cap (the agent is call-heavy). The route hint is also tightened:
# segment-level figures (services/product revenue) live in narrative, not in
# our top-level XBRL facts, so they must route to vector — this fixes the
# earlier mis-route that answered "services revenue" with total revenue.
_PLAN_SYSTEM = """You prepare a question about SEC 10-K filings for retrieval. Do two things:

1. Rewrite it as a concise, self-contained search query: resolve vague references, and make the company and fiscal year explicit if implied.
2. Classify what it needs:
   - vector : qualitative/narrative content, OR segment-level figures like services/product/regional revenue (these live in the filing text, not the figures database)
   - sql    : precise TOP-LEVEL financials (total revenue, net income, total assets, margins, multi-year or cross-company comparisons)
   - both   : needs narrative AND exact top-level figures

Output EXACTLY two lines, nothing else:
QUERY: <rewritten query>
ROUTE: <vector|sql|both>"""

_AGENT_SYSTEM = """You are a financial analyst assistant answering questions about SEC 10-K filings.

You have tools:
- sql_query: get EXACT figures for TOP-LEVEL metrics only (total revenue, net income, total assets, margins). Prefer it for those over reading numbers from text. It does NOT have segment/product/regional figures (e.g. services revenue, iPhone revenue) — for those, read the value from the context chunks and cite [N]. If sql_query returns an error, fall back to the context.
- calculator: do arithmetic (growth rates, margins, ratios). Extract numbers, then compute — never do multi-digit math in your head.
- lookup_citation: re-fetch a chunk's full text by chunk_id if you need to quote it exactly. Pass the exact id shown as (id=...) in the chunk's header — never the [N] anchor.

Rules:
1. Ground every claim in the provided context chunks or tool results. If neither contains the answer, say so — do not use prior knowledge.
2. A figure must actually match what was asked. If a tool returns a number for a different metric than the question, do not report it — use the context instead.
3. Cite narrative facts from the context with [N], where N is the chunk index shown. Cite even when paraphrasing.
4. Quote exact figures; never round unless asked.
5. Be careful with fiscal vs calendar year (Apple's fiscal year ends in late September).
6. Be concise — match the question's scope.
"""


def _parse_plan(raw: str, fallback_query: str) -> tuple[str, str]:
    """Parse the two-line plan output into (rewritten_query, route)."""
    query, route = fallback_query, "both"
    for line in raw.splitlines():
        s = line.strip()
        low = s.lower()
        if low.startswith("query:"):
            query = s.split(":", 1)[1].strip() or fallback_query
        elif low.startswith("route:"):
            r = s.split(":", 1)[1].strip().lower()
            if "both" in r:
                route = "both"
            elif "sql" in r:
                route = "sql"
            elif "vector" in r:
                route = "vector"
    return query, route


def plan(state: AgentState) -> AgentState:
    """One call that both rewrites the query and routes it. Emits two trace
    events so the frontend still shows rewrite and route as distinct steps."""
    original = state["question"]
    # Ground the rewrite in the known corpus so "these 3 companies" expands to the
    # real names here, before retrieval and the agent ever see the query.
    raw = generate_text(_PLAN_SYSTEM + _corpus_grounding(), original, max_output_tokens=128)
    rewritten, decision = _parse_plan(raw, original)
    return {
        "rewritten_query": rewritten,
        "route": decision,
        "trace": [
            {
                "node": "plan",
                "type": "rewrite",
                "data": {"original": original, "rewritten": rewritten},
            },
            {"node": "plan", "type": "route", "data": {"route": decision}},
        ],
    }


def retrieve(state: AgentState) -> AgentState:
    """Vector retrieval via the Day-2 funnel. Reached only when the route
    includes vector (conditional edge in graph.py)."""
    chunks = rerank_search(question=state["rewritten_query"], top_k=8)
    return {
        "chunks": chunks,
        "trace": [
            {
                "node": "retrieve",
                "type": "retrieve",
                "data": {
                    "n_chunks": len(chunks),
                    "top": [
                        {"chunk_id": c.chunk_id, "ticker": c.ticker, "fy": c.fiscal_year}
                        for c in chunks[:3]
                    ],
                },
            }
        ],
    }


def agent(state: AgentState) -> AgentState:
    """Provider-neutral tool-calling loop (Claude tool_use or Gemini function
    calling, per llm_provider). Runs tools until a final text answer, surfacing
    each tool call in the trace (the SQL/args are the demo payload)."""
    chunks = state.get("chunks", [])
    context = (
        format_chunks_for_prompt(chunks)
        if chunks
        else "(no vector context retrieved — rely on tools)"
    )
    user_text = f"Question: {state['rewritten_query']}\n\nContext chunks:\n\n{context}"

    # Push live events to the SSE stream (no-op under plain /agent). Tokens are
    # the final answer forming; tool_call fires the instant a tool runs.
    writer = _stream_writer()

    def on_text(delta: str) -> None:
        writer({"type": "token", "text": delta})

    def on_tool_call(tc: ToolCall) -> None:
        writer(
            {
                "type": "tool_call",
                "node": "agent",
                "data": {"tool": tc.tool, "args": tc.args, "result": tc.result},
            }
        )

    result = run_tool_loop_stream(
        _AGENT_SYSTEM + _corpus_grounding(),
        user_text,
        # 1024 truncated detailed multi-company answers mid-sentence (e.g. a risk
        # comparison table got cut off). 4096 comfortably fits the longest answers
        # we produce while staying well under Sonnet's output limit.
        max_tokens=4096,
        max_iters=MAX_TOOL_ITERS,
        on_text=on_text,
        on_tool_call=on_tool_call,
    )

    trace: list[dict] = [
        {
            "node": "agent",
            "type": "tool_call",
            "data": {"tool": tc.tool, "args": tc.args, "result": tc.result},
        }
        for tc in result.tool_calls
    ]
    usage = {"input_tokens": result.input_tokens, "output_tokens": result.output_tokens}
    answer = result.answer

    # Reliability floor: if the tool-loop yields no answer (e.g. a backend that
    # intermittently botches a tool call), fall back to plain synthesis over the
    # retrieved chunks — the proven /answer path, no tool-calling involved.
    if not answer.strip() and chunks:
        fb = synthesize(state["rewritten_query"], chunks)
        answer = fb.answer
        usage["input_tokens"] += fb.input_tokens
        usage["output_tokens"] += fb.output_tokens
        trace.append(
            {
                "node": "agent",
                "type": "fallback",
                "data": {"reason": "tool-loop produced no answer; synthesized from retrieved chunks"},
            }
        )

    trace.append({"node": "agent", "type": "synthesize", "data": {"answer": answer}})
    return {"answer": answer, "usage": usage, "trace": trace}
