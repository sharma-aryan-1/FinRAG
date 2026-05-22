from fastapi import FastAPI
from pydantic import BaseModel, Field

from finrag.config import settings
from finrag.retrieval.vector import RetrievedChunk, search

app = FastAPI(title="FinRAG", version="0.1.0")


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


# ── Routes ────────────────────────────────────────────────────────────────
@app.get("/health")
def health() -> dict[str, str]:
    return {
        "status": "ok",
        "llm_mode": settings.llm_mode,
    }


@app.post("/query", response_model=QueryResponse)
def query(req: QueryRequest) -> QueryResponse:
    """Minimal Day-1 retrieval endpoint.

    Embeds the question (search_query side of Cohere v3) and runs a dense
    nearest-neighbor search against Qdrant. Returns top-k chunks with full
    citation metadata. No reranking, no LLM, no agent — those come later.
    """
    chunks = search(
        question=req.question,
        top_k=req.top_k,
        ticker=req.ticker,
        fiscal_year=req.fiscal_year,
        chunk_type=req.chunk_type,
    )
    return QueryResponse(question=req.question, chunks=chunks)
