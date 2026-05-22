"""Dense vector retrieval over the Qdrant `finrag_chunks` collection.

This is the minimal Day-1 retriever: embed the query with Cohere v3
(`search_query` side of the asymmetric pair) and run a single nearest-
neighbor search with optional payload filtering. Hybrid (BM25 + dense)
and reranking come on Day 2.
"""

from __future__ import annotations

from functools import lru_cache

import cohere
from pydantic import BaseModel
from qdrant_client import QdrantClient
from qdrant_client.models import (
    FieldCondition,
    Filter,
    MatchValue,
)

from finrag.config import settings
from finrag.ingestion.embed import COHERE_MODEL, COLLECTION_NAME


# ── Public response model ─────────────────────────────────────────────────
class RetrievedChunk(BaseModel):
    """One chunk surfaced by the retriever, with its similarity score.

    All payload fields from the Chunk we indexed are copied through — so the
    caller (or eventually, the agent/frontend) has everything it needs to
    render a citation without joining back against another store.
    """

    chunk_id: str
    score: float
    text: str
    chunk_type: str
    section_title: str | None
    ticker: str
    company_name: str
    fiscal_year: int
    period_of_report: str
    accession_number: str
    sec_url: str


# ── Clients (one per process, cached) ─────────────────────────────────────
# lru_cache on a no-arg function is the canonical "singleton per process"
# pattern for FastAPI. Avoids re-creating TLS connections on every request.
@lru_cache(maxsize=1)
def get_cohere_client() -> cohere.ClientV2:
    return cohere.ClientV2(api_key=settings.cohere_api_key)


@lru_cache(maxsize=1)
def get_qdrant_client() -> QdrantClient:
    return QdrantClient(
        url=settings.qdrant_url,
        api_key=settings.qdrant_api_key,
    )


# ── Query embedding ───────────────────────────────────────────────────────
def embed_query(text: str) -> list[float]:
    """Embed a user query using the query-side encoder.

    The matching `search_document` lives in ingestion/embed.py. Mismatching
    these two silently degrades retrieval quality — there's no error, just
    worse results. See Decision 6's notes on asymmetric retrieval.
    """
    co = get_cohere_client()
    response = co.embed(
        texts=[text],
        model=COHERE_MODEL,
        input_type="search_query",
        embedding_types=["float"],
    )
    return response.embeddings.float_[0]


# ── Filter builder ────────────────────────────────────────────────────────
def _build_filter(
    ticker: str | None = None,
    fiscal_year: int | None = None,
    chunk_type: str | None = None,
) -> Filter | None:
    """Translate simple kwarg filters into Qdrant's filter grammar.

    Treats falsy values (None, "", 0) as "not provided" — important because
    JSON clients (notably Swagger UI) often send "" for unset string fields
    instead of omitting them, and we don't want to filter for ticker == "".
    """
    conditions: list[FieldCondition] = []
    if ticker:
        conditions.append(
            FieldCondition(key="ticker", match=MatchValue(value=ticker))
        )
    if fiscal_year:
        conditions.append(
            FieldCondition(key="fiscal_year", match=MatchValue(value=fiscal_year))
        )
    if chunk_type:
        conditions.append(
            FieldCondition(key="chunk_type", match=MatchValue(value=chunk_type))
        )
    return Filter(must=conditions) if conditions else None


# ── Retrieval ─────────────────────────────────────────────────────────────
def search(
    question: str,
    top_k: int = 5,
    ticker: str | None = None,
    fiscal_year: int | None = None,
    chunk_type: str | None = None,
) -> list[RetrievedChunk]:
    """End-to-end: embed the question, search Qdrant, return chunks + scores."""
    qdrant = get_qdrant_client()
    query_vector = embed_query(question)
    query_filter = _build_filter(ticker, fiscal_year, chunk_type)

    # query_points is the modern Qdrant API (replaces deprecated `search`).
    # `with_payload=True` is the default but worth being explicit — without
    # it, you get only point IDs and scores, and lose all citation info.
    response = qdrant.query_points(
        collection_name=COLLECTION_NAME,
        query=query_vector,
        query_filter=query_filter,
        limit=top_k,
        with_payload=True,
    )

    return [
        RetrievedChunk(
            chunk_id=point.payload["chunk_id"],
            score=point.score,
            text=point.payload["text"],
            chunk_type=point.payload["chunk_type"],
            section_title=point.payload.get("section_title"),
            ticker=point.payload["ticker"],
            company_name=point.payload["company_name"],
            fiscal_year=point.payload["fiscal_year"],
            period_of_report=point.payload["period_of_report"],
            accession_number=point.payload["accession_number"],
            sec_url=point.payload["sec_url"],
        )
        for point in response.points
    ]
