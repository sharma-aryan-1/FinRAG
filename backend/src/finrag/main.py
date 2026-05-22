from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from finrag.config import settings
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

    The full Day-2 funnel. score field is the rerank relevance_score (0–1,
    semantically meaningful: 0.85+ = strongly relevant). Agent and LLM
    synthesis arrive in Day 3.
    """
    chunks = rerank_search(
        question=req.question,
        top_k=req.top_k,
        ticker=req.ticker,
        fiscal_year=req.fiscal_year,
        chunk_type=req.chunk_type,
    )
    return QueryResponse(question=req.question, chunks=chunks)
