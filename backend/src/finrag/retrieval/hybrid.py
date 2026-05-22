"""Hybrid retrieval: dense (Qdrant) + lexical (BM25) fused via RRF.

Why RRF rather than score normalization: BM25 scores and cosine similarities
are on different scales with different distributions. Normalizing them risks
arbitrary calibration choices. RRF discards raw scores and uses *ranks*,
which makes the fusion calibration-free and notably robust.

Formula (Cormack, Clarke, Buettcher 2009):
    RRF_score(d) = Σ over rankers r:  1 / (k + rank_r(d))
    rank is 1-based; k=60 is the paper's value and works in practice.
"""

from __future__ import annotations

from finrag.retrieval import lexical
from finrag.retrieval.vector import (
    RetrievedChunk,
    payload_to_chunk,
    retrieve_by_chunk_ids,
    search as dense_search,
)

# Constants
RRF_K = 60       # smoothing — paper default, do not tune without eval
DEFAULT_K_EACH = 50  # candidates per retriever before fusion


def _rrf_fuse(
    ranked_lists: list[list[str]], k: int = RRF_K
) -> dict[str, float]:
    """Compute RRF scores given multiple ranked lists of chunk_ids.

    Each list should be in retrieval-order (best first). A chunk_id absent
    from a list contributes 0 from that ranker.
    """
    scores: dict[str, float] = {}
    for ranks in ranked_lists:
        for position, chunk_id in enumerate(ranks, start=1):
            scores[chunk_id] = scores.get(chunk_id, 0.0) + 1.0 / (k + position)
    return scores


def hybrid_search(
    question: str,
    top_k: int = 5,
    ticker: str | None = None,
    fiscal_year: int | None = None,
    chunk_type: str | None = None,
    k_each: int = DEFAULT_K_EACH,
) -> list[RetrievedChunk]:
    """End-to-end hybrid retrieval.

    Steps:
      1. Run dense and BM25 in parallel-ish (sequential here; both fast).
      2. RRF-fuse the two rank orderings into a single score per chunk_id.
      3. Take top_k by fused score.
      4. Hydrate any chunk_ids that came only from BM25 by batch-fetching
         their payloads from Qdrant.
      5. Return RetrievedChunk objects with `score` = the RRF fused score.

    The score field is now the RRF score, not raw cosine or BM25. RRF scores
    are small (typically 0.01-0.05 for top results) — don't compare them to
    Day-1 cosine scores; they're on different scales.
    """
    # 1. Candidates from each retriever, both filter-aware so the candidate
    #    pool already respects the user's scoping.
    dense_chunks = dense_search(
        question=question,
        top_k=k_each,
        ticker=ticker,
        fiscal_year=fiscal_year,
        chunk_type=chunk_type,
    )
    bm25_results = lexical.search(
        query=question,
        top_k=k_each,
        ticker=ticker,
        fiscal_year=fiscal_year,
        chunk_type=chunk_type,
    )

    dense_ids = [c.chunk_id for c in dense_chunks]
    bm25_ids = [cid for cid, _ in bm25_results]

    # 2. RRF fuse
    rrf_scores = _rrf_fuse([dense_ids, bm25_ids])

    # 3. Top-K by fused score
    top_ids = sorted(rrf_scores, key=rrf_scores.get, reverse=True)[:top_k]

    # 4. Hydrate. Dense gave us full payloads; for BM25-only chunks, batch
    #    fetch from Qdrant.
    dense_map = {c.chunk_id: c for c in dense_chunks}
    missing_ids = [cid for cid in top_ids if cid not in dense_map]
    extra_payloads = retrieve_by_chunk_ids(missing_ids)

    # 5. Build final list, score = RRF score
    results: list[RetrievedChunk] = []
    for cid in top_ids:
        rrf_score = rrf_scores[cid]
        if cid in dense_map:
            # Reuse the dense RetrievedChunk; just swap the score for the
            # fused one. model_copy keeps the object immutable-ish.
            results.append(dense_map[cid].model_copy(update={"score": rrf_score}))
        elif cid in extra_payloads:
            results.append(payload_to_chunk(extra_payloads[cid], rrf_score))
        else:
            # Should not happen — a fused id with no source. Defensive skip.
            continue
    return results


# ── CLI ───────────────────────────────────────────────────────────────────
def main() -> None:
    """Compare dense-only, BM25-only, and hybrid on a few canary queries."""
    queries = [
        "How did Apple's services revenue change in 2023?",
        "Tesla R&D expense fiscal 2023",
        "SG&A expense",
        "supply chain risk",
    ]
    for q in queries:
        print(f"\n=== {q!r} ===")
        print("  Dense top-3:")
        for c in dense_search(q, top_k=3):
            print(f"    {c.ticker} FY{c.fiscal_year}  score={c.score:.3f}  | {c.text[:60]}")
        print("  BM25 top-3:")
        for cid, score in lexical.search(q, top_k=3):
            print(f"    {cid}  score={score:.3f}")
        print("  Hybrid top-3:")
        for c in hybrid_search(q, top_k=3):
            print(f"    {c.ticker} FY{c.fiscal_year}  rrf={c.score:.4f}  | {c.text[:60]}")


if __name__ == "__main__":
    main()
