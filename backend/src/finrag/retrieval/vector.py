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
from finrag.ingestion.embed import COHERE_MODEL, COLLECTION_NAME, make_qdrant_client


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
    # Embedded (on-disk) or remote, decided by settings.qdrant_path — see
    # make_qdrant_client. lru_cache makes this the single client per worker that
    # embedded mode requires.
    return make_qdrant_client()


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


# ── Payload → RetrievedChunk ──────────────────────────────────────────────
def payload_to_chunk(payload: dict, score: float) -> RetrievedChunk:
    """Convert a Qdrant payload + score into a RetrievedChunk.

    Shared by dense search and the hybrid retriever's hydration step — keeps
    the mapping in one place so adding a field to the model means editing
    one function, not three.
    """
    return RetrievedChunk(
        chunk_id=payload["chunk_id"],
        score=score,
        text=payload["text"],
        chunk_type=payload["chunk_type"],
        section_title=payload.get("section_title"),
        ticker=payload["ticker"],
        company_name=payload["company_name"],
        fiscal_year=payload["fiscal_year"],
        period_of_report=payload["period_of_report"],
        accession_number=payload["accession_number"],
        sec_url=payload["sec_url"],
    )


# ── Retrieval ─────────────────────────────────────────────────────────────
def search(
    question: str,
    top_k: int = 5,
    ticker: str | None = None,
    fiscal_year: int | None = None,
    chunk_type: str | None = None,
) -> list[RetrievedChunk]:
    """Dense-only retrieval: embed the question, search Qdrant, return chunks.

    Kept available for direct testing and the eval harness's comparison runs.
    The user-facing /query endpoint uses hybrid_search instead.
    """
    qdrant = get_qdrant_client()
    query_vector = embed_query(question)
    query_filter = _build_filter(ticker, fiscal_year, chunk_type)

    response = qdrant.query_points(
        collection_name=COLLECTION_NAME,
        query=query_vector,
        query_filter=query_filter,
        limit=top_k,
        with_payload=True,
    )
    return [payload_to_chunk(p.payload, p.score) for p in response.points]


def retrieve_by_chunk_ids(chunk_ids: list[str]) -> dict[str, dict]:
    """Batch-fetch payloads by chunk_id (used by hybrid hydration).

    Returns a dict {chunk_id: payload}. Qdrant stores point IDs as uint64
    (the hex chunk_id converted), so we convert on the way in and dereference
    via the payload's own chunk_id field on the way out.

    Ids that aren't valid 16-hex chunk_ids (e.g. a value the agent hallucinated
    like 'chunk_5') are silently dropped rather than raising — a malformed id is
    just a miss, so callers see it as not-found, not a crash.
    """
    if not chunk_ids:
        return {}
    qdrant = get_qdrant_client()
    point_ids: list[int] = []
    for cid in chunk_ids:
        try:
            point_ids.append(int(cid, 16))
        except ValueError:
            continue  # not a hex chunk_id → treat as not-found
    if not point_ids:
        return {}
    points = qdrant.retrieve(
        collection_name=COLLECTION_NAME,
        ids=point_ids,
        with_payload=True,
    )
    return {p.payload["chunk_id"]: p.payload for p in points}
