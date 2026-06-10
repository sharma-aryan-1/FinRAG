import json
from typing import Any

from fastapi import FastAPI
from fastapi.encoders import jsonable_encoder
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from finrag.agent import get_agent, run_agent
from finrag.config import settings
from finrag.llm import synthesize
from finrag.retrieval.rerank import rerank_search
from finrag.retrieval.vector import RetrievedChunk

app = FastAPI(title="FinRAG", version="0.1.0")

# Dev CORS: allow the Next.js dev server on :3000 to call us.
# In prod, lock allow_origins to the deployed frontend's exact URL.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


# ── Request / response models ─────────────────────────────────────────────
class QueryRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=1000)
    top_k: int = Field(default=5, ge=1, le=50)
    # Optional payload filters — agent (Day 3) will populate these dynamically
    ticker: str | None = None
    fiscal_year: int | None = None
    chunk_type: str | None = Field(
        default=None,
        description="Filter to 'narrative' or 'table' chunks only.",
    )


class QueryResponse(BaseModel):
    question: str
    chunks: list[RetrievedChunk]


class AnswerRequest(QueryRequest):
    """Same filter shape as QueryRequest; answer endpoint just adds synthesis."""

    pass


class AnswerUsage(BaseModel):
    """Surfaced so the frontend (and curious humans) can see prompt-caching
    is working: cache_read_input_tokens should be > 0 on the second+ call."""

    model: str
    input_tokens: int
    output_tokens: int
    cache_creation_input_tokens: int
    cache_read_input_tokens: int
    stop_reason: str


class AnswerResponse(BaseModel):
    question: str
    answer: str
    chunks: list[RetrievedChunk]
    usage: AnswerUsage


class AgentResponse(BaseModel):
    question: str
    answer: str
    route: str
    chunks: list[RetrievedChunk]
    # Ordered node/tool steps the agent took — drives the frontend trace UI.
    trace: list[dict[str, Any]]
    usage: dict[str, int]


# ── Routes ────────────────────────────────────────────────────────────────
@app.get("/health")
def health() -> dict[str, str]:
    return {
        "status": "ok",
        "llm_mode": settings.llm_mode,
    }


@app.post("/query", response_model=QueryResponse)
def query(req: QueryRequest) -> QueryResponse:
    """Three-stage retrieval: BM25 + dense (RRF-fused) → Cohere Rerank v3.

    Returns the raw retrieved chunks. Used by the eval harness and for
    inspecting retrieval quality in isolation.
    """
    chunks = rerank_search(
        question=req.question,
        top_k=req.top_k,
        ticker=req.ticker,
        fiscal_year=req.fiscal_year,
        chunk_type=req.chunk_type,
    )
    return QueryResponse(question=req.question, chunks=chunks)


@app.post("/answer", response_model=AnswerResponse)
def answer(req: AnswerRequest) -> AnswerResponse:
    """Retrieve top-K with the Day-2 funnel, then synthesize a grounded
    answer with Claude. Citations are returned as [N] inline references
    pointing into the `chunks` array (1-based).

    Day-3 agent + tool use (sql_query, calculator) lands in the next
    decision. This endpoint will remain available as the "retrieval-only
    synthesis" baseline so we can measure agent value-add against it.
    """
    chunks = rerank_search(
        question=req.question,
        top_k=req.top_k,
        ticker=req.ticker,
        fiscal_year=req.fiscal_year,
        chunk_type=req.chunk_type,
    )
    result = synthesize(req.question, chunks)
    return AnswerResponse(
        question=req.question,
        answer=result.answer,
        chunks=chunks,
        usage=AnswerUsage(
            model=result.model,
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            cache_creation_input_tokens=result.cache_creation_input_tokens,
            cache_read_input_tokens=result.cache_read_input_tokens,
            stop_reason=result.stop_reason,
        ),
    )


@app.post("/agent", response_model=AgentResponse)
def agent_endpoint(req: AnswerRequest) -> AgentResponse:
    """Run the LangGraph agent: plan → (retrieve) → tool-loop → synthesize.

    Unlike /answer (retrieval + plain synthesis), this routes the question,
    optionally pulls vector context, and lets the model call tools
    (sql_query, calculator, lookup_citation). Returns the answer plus the full
    node/tool `trace` for the frontend to render. /answer stays as the baseline.
    """
    final = run_agent(req.question)
    return AgentResponse(
        question=req.question,
        answer=final.get("answer", ""),
        route=final.get("route", ""),
        chunks=final.get("chunks", []),
        trace=final.get("trace", []),
        usage=final.get("usage", {}),
    )


def _sse(event: str, data: Any) -> str:
    """One Server-Sent-Events frame. jsonable_encoder handles RetrievedChunk
    (pydantic) and any dates/Decimals in tool results."""
    return f"event: {event}\ndata: {json.dumps(jsonable_encoder(data))}\n\n"


@app.post("/agent/stream")
def agent_stream(req: AnswerRequest) -> StreamingResponse:
    """Streaming twin of /agent (Server-Sent Events). The client watches the
    agent reason in real time:

        event: rewrite | route | retrieve   planning milestones (per graph node)
        event: tool_call                     each tool the moment it executes
        event: token                         final-answer text deltas as generated
        event: done                          full answer + route + chunks + usage + trace
        event: error                         message, if the run raises mid-stream

    Milestones come from LangGraph 'updates' (state deltas as each node finishes);
    tokens and tool_calls come from the agent node's custom stream writer. Both
    are pulled from one `graph.stream(stream_mode=["updates","custom"])` so they
    arrive interleaved in true execution order. tool_call/synthesize trace items
    are skipped in the 'updates' pass — they're already streamed live — but the
    'done' frame still carries the complete trace for the final render."""

    def event_gen():
        graph = get_agent()
        final: dict[str, Any] = {
            "question": req.question,
            "answer": "",
            "route": "",
            "chunks": [],
            "usage": {},
            "trace": [],
        }
        try:
            for mode, chunk in graph.stream(
                {"question": req.question, "trace": []},
                stream_mode=["updates", "custom"],
            ):
                if mode == "custom":
                    yield _sse(chunk.get("type", "custom"), chunk)
                    continue
                # mode == "updates": chunk is {node_name: state_delta}
                for _node, delta in chunk.items():
                    for ev in delta.get("trace", []):
                        if ev.get("type") in ("rewrite", "route", "retrieve", "fallback"):
                            yield _sse(ev["type"], ev)
                    for key in ("answer", "route", "chunks", "usage"):
                        if key in delta:
                            final[key] = delta[key]
                    final["trace"].extend(delta.get("trace", []))
            yield _sse("done", final)
        except Exception as e:  # don't 500 mid-stream — report and close cleanly
            yield _sse("error", {"message": str(e)})

    return StreamingResponse(
        event_gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
