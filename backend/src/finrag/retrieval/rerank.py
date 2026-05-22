"""Final-stage reranking via Cohere Rerank v3 (cross-encoder).

The reranker fixes the failure modes hybrid retrieval can't:
  - Lexical accidents (BM25 dragging an entity-mismatched chunk to the top
    because keyword co-occurrence happens to be high)
  - Query-document interaction blindness (bi-encoders can't see "Apple" in
    the query and "Tesla" in the candidate at the same time)

Flow:
    hybrid_search(top_k=N_CANDIDATES) ──▶ list of RetrievedChunk
                                              │
                                              ▼
                                  Cohere Rerank v3
                                  (rerank-english-v3.0)
                                              │
                                              ▼
                                  Reordered, top_k chosen
                                  score = relevance_score (0–1)
"""

from __future__ import annotations

import time

from cohere.errors import TooManyRequestsError

from finrag.retrieval.hybrid import hybrid_search
from finrag.retrieval.vector import RetrievedChunk, get_cohere_client

RERANK_MODEL = "rerank-english-v3.0"

# Default candidate-pool size fed to the reranker. 50 is the sweet spot:
# wide enough that the reranker can rescue chunks ranked 20+ by hybrid,
# narrow enough to stay under Rerank v3's pricing tier (1 search unit per
# 100 docs) and latency budget (~200–400ms at 50 docs).
DEFAULT_CANDIDATES = 50

# Trial-key safety net — same pattern as ingestion/embed.py
RERANK_RETRY_INITIAL_BACKOFF_SECONDS = 5.0
RERANK_MAX_RETRIES = 4


def _rerank_with_retry(
    query: str, documents: list[str], top_n: int
) -> list[tuple[int, float]]:
    """Call Cohere Rerank v3 with exponential-backoff retry on 429s.

    Returns list of (original_index, relevance_score) in reranker order.
    """
    co = get_cohere_client()
    backoff = RERANK_RETRY_INITIAL_BACKOFF_SECONDS

    for attempt in range(1, RERANK_MAX_RETRIES + 1):
        try:
            response = co.rerank(
                model=RERANK_MODEL,
                query=query,
                documents=documents,
                top_n=top_n,
            )
            return [(r.index, r.relevance_score) for r in response.results]
        except TooManyRequestsError:
            if attempt == RERANK_MAX_RETRIES:
                raise
            print(
                f"  ⚠ rerank rate-limited; sleeping {backoff:.0f}s "
                f"(retry {attempt}/{RERANK_MAX_RETRIES - 1})"
            )
            time.sleep(backoff)
            backoff *= 2
    raise RuntimeError("rerank retry loop exited without resolving")


def rerank_search(
    question: str,
    top_k: int = 5,
    ticker: str | None = None,
    fiscal_year: int | None = None,
    chunk_type: str | None = None,
    candidates: int = DEFAULT_CANDIDATES,
) -> list[RetrievedChunk]:
    """Hybrid retrieval + cross-encoder rerank. The user-facing default.

    The `score` field on returned chunks is the rerank relevance_score
    (0–1, semantically meaningful) — *not* the RRF score from the hybrid
    stage. You can compare these across queries: 0.85 means "strongly
    relevant" no matter what was asked.
    """
    # 1. Get a wide candidate pool from hybrid fusion. Pass the same filters
    #    through — scoping should happen before rerank, not after.
    pool = hybrid_search(
        question=question,
        top_k=candidates,
        ticker=ticker,
        fiscal_year=fiscal_year,
        chunk_type=chunk_type,
    )
    if not pool:
        return []

    # If we have fewer candidates than top_k, no reranking is meaningful —
    # just return what we have.
    if len(pool) <= top_k:
        return pool

    # 2. Cross-encoder rerank
    documents = [c.text for c in pool]
    ranked = _rerank_with_retry(
        query=question,
        documents=documents,
        top_n=top_k,
    )

    # 3. Reassemble in rerank order with the rerank score replacing RRF
    return [
        pool[orig_idx].model_copy(update={"score": rel_score})
        for orig_idx, rel_score in ranked
    ]


# ── CLI ───────────────────────────────────────────────────────────────────
def main() -> None:
    """Side-by-side: hybrid (RRF only) vs hybrid+rerank, same queries."""
    queries = [
        "How did Apple's services revenue change in 2023?",
        "Tesla R&D expense fiscal 2023",
        "SG&A expense",
        "JPMorgan net interest income",
        "supply chain risk",
    ]
    for q in queries:
        print(f"\n=== {q!r} ===")
        print("  Hybrid (RRF only) top-3:")
        for c in hybrid_search(q, top_k=3):
            print(
                f"    {c.ticker} FY{c.fiscal_year}  rrf={c.score:.4f}  | {c.text[:60]}"
            )
        print("  Hybrid + Rerank v3 top-3:")
        for c in rerank_search(q, top_k=3):
            print(
                f"    {c.ticker} FY{c.fiscal_year}  rel={c.score:.3f}  | {c.text[:60]}"
            )


if __name__ == "__main__":
    main()
