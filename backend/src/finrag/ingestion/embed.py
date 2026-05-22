"""Embed parsed chunks with Cohere embed-v3 and upsert into Qdrant.

Pipeline:
    data/processed/*.jsonl  (Chunk objects)
        ──▶  Cohere embed-v3 (input_type=search_document)
        ──▶  Qdrant upsert into `finrag_chunks` collection

Idempotent: re-running overwrites existing points by ID (deterministic hash).
"""

from __future__ import annotations

import json
import time
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import TypeVar

import cohere
from cohere.errors import TooManyRequestsError
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, PointStruct, VectorParams

from finrag.config import settings
from finrag.ingestion.parse import PROCESSED_DIR, Chunk

# ── Constants ─────────────────────────────────────────────────────────────
# Cohere v3 is the asymmetric retrieval model. 1024-dim output, English.
# Use `embed-multilingual-v3.0` if you want cross-language support; same dim.
COHERE_MODEL = "embed-english-v3.0"
EMBED_DIM = 1024
COLLECTION_NAME = "finrag_chunks"

# Cohere caps `texts=[...]` at 96 per request. Larger requests get 400'd.
COHERE_BATCH_SIZE = 96
# Qdrant upsert is fine with much larger batches; 256 keeps memory bounded
# while amortizing the HTTP overhead.
QDRANT_BATCH_SIZE = 256

# Pacing for Cohere calls. Trial keys have two limits:
#   - 100 calls/min  (call-based)
#   - 100k tokens/min (token-based) ← this is the binding constraint at our chunk size
# At ~300 tokens/chunk × 96 chunks/batch ≈ 29k tokens/batch. Steady-state, that
# means we can do roughly 3 batches per rolling minute. 20s base sleep gives us
# margin; bursts above that get caught by the retry handler below.
COHERE_SLEEP_SECONDS = 20.0
COHERE_RETRY_INITIAL_BACKOFF_SECONDS = 30.0
COHERE_MAX_RETRIES = 5


# ── Helpers ───────────────────────────────────────────────────────────────
T = TypeVar("T")


def _batched(seq: Iterable[T], n: int) -> Iterator[list[T]]:
    """Yield lists of size `n` from `seq`. Last batch may be shorter."""
    buf: list[T] = []
    for x in seq:
        buf.append(x)
        if len(buf) == n:
            yield buf
            buf = []
    if buf:
        yield buf


def _read_all_chunks(processed_dir: Path) -> list[Chunk]:
    chunks: list[Chunk] = []
    for jsonl in sorted(processed_dir.glob("*.jsonl")):
        for line in jsonl.read_text(encoding="utf-8").splitlines():
            if line.strip():
                chunks.append(Chunk.model_validate_json(line))
    return chunks


def _ensure_collection(qdrant: QdrantClient, name: str, dim: int) -> None:
    """Create the collection if it doesn't exist. No-op otherwise.

    Note: we *don't* recreate the collection on schema mismatch — that would
    destroy data. If you change EMBED_DIM, delete the collection manually
    via the dashboard or `qdrant.delete_collection(name)`.
    """
    existing = {c.name for c in qdrant.get_collections().collections}
    if name in existing:
        return
    print(f"Creating Qdrant collection '{name}' (dim={dim}, distance=cosine)")
    qdrant.create_collection(
        collection_name=name,
        vectors_config=VectorParams(size=dim, distance=Distance.COSINE),
    )


def _embed_batch(co: cohere.ClientV2, texts: list[str]) -> list[list[float]]:
    """Embed a batch of texts with the document-side encoder.

    The `input_type="search_document"` here is the asymmetric-retrieval flag.
    The matching `search_query` lives in the /query endpoint (Decision 7).
    Mixing the two destroys retrieval quality silently.
    """
    response = co.embed(
        texts=texts,
        model=COHERE_MODEL,
        input_type="search_document",
        embedding_types=["float"],
    )
    # V2 response shape: response.embeddings.float_ is list[list[float]]
    return response.embeddings.float_


def _embed_batch_with_retry(
    co: cohere.ClientV2, texts: list[str]
) -> list[list[float]]:
    """Wrap _embed_batch with exponential backoff on 429 rate-limit errors.

    Trial keys can hit either the call limit or the token limit; both surface
    as TooManyRequestsError. We don't bother distinguishing — the right
    response is the same: wait, then retry.
    """
    backoff = COHERE_RETRY_INITIAL_BACKOFF_SECONDS
    for attempt in range(1, COHERE_MAX_RETRIES + 1):
        try:
            return _embed_batch(co, texts)
        except TooManyRequestsError:
            if attempt == COHERE_MAX_RETRIES:
                raise
            print(
                f"  ⚠ rate-limited; sleeping {backoff:.0f}s "
                f"(retry {attempt}/{COHERE_MAX_RETRIES - 1})"
            )
            time.sleep(backoff)
            backoff *= 2
    # Unreachable — loop either returns or raises.
    raise RuntimeError("retry loop exited without resolving")


def _chunk_to_point(chunk: Chunk, vector: list[float]) -> PointStruct:
    """Convert a Chunk + its vector into a Qdrant point.

    Point ID: convert our 16-hex chunk_id to uint64. Qdrant only accepts
    UUID or unsigned int IDs, not arbitrary strings. The hex→int conversion
    preserves determinism (same chunk → same ID across runs).
    """
    point_id = int(chunk.chunk_id, 16)
    return PointStruct(
        id=point_id,
        vector=vector,
        payload=chunk.model_dump(),
    )


# ── Core ──────────────────────────────────────────────────────────────────
def embed_and_upsert(chunks: list[Chunk]) -> int:
    """Embed all chunks and upsert into Qdrant. Returns count of points written."""
    co = cohere.ClientV2(api_key=settings.cohere_api_key)
    qdrant = QdrantClient(url=settings.qdrant_url, api_key=settings.qdrant_api_key)
    _ensure_collection(qdrant, COLLECTION_NAME, EMBED_DIM)

    qdrant_buf: list[PointStruct] = []
    total_written = 0

    for batch_idx, batch in enumerate(_batched(chunks, COHERE_BATCH_SIZE), start=1):
        texts = [c.text for c in batch]
        print(f"  ↳ embed  batch {batch_idx}  ({len(batch)} chunks)")
        vectors = _embed_batch_with_retry(co, texts)

        if len(vectors) != len(batch):
            # Cohere should always return one vector per input; fail loud if not.
            raise RuntimeError(
                f"Cohere returned {len(vectors)} vectors for {len(batch)} inputs"
            )

        for chunk, vector in zip(batch, vectors):
            qdrant_buf.append(_chunk_to_point(chunk, vector))

        if len(qdrant_buf) >= QDRANT_BATCH_SIZE:
            qdrant.upsert(collection_name=COLLECTION_NAME, points=qdrant_buf)
            total_written += len(qdrant_buf)
            print(f"  ↳ upsert {len(qdrant_buf):4d} points  (total {total_written})")
            qdrant_buf = []

        time.sleep(COHERE_SLEEP_SECONDS)

    # Flush remaining points (final partial batch)
    if qdrant_buf:
        qdrant.upsert(collection_name=COLLECTION_NAME, points=qdrant_buf)
        total_written += len(qdrant_buf)
        print(f"  ↳ upsert {len(qdrant_buf):4d} points  (total {total_written}, tail)")

    return total_written


# ── CLI ───────────────────────────────────────────────────────────────────
def main() -> None:
    chunks = _read_all_chunks(PROCESSED_DIR)
    print(f"Loaded {len(chunks)} chunks from {PROCESSED_DIR}\n")
    if not chunks:
        print("No chunks found. Run `finrag.ingestion.parse` first.")
        return

    written = embed_and_upsert(chunks)

    # Verify final state via Qdrant's own count
    qdrant = QdrantClient(url=settings.qdrant_url, api_key=settings.qdrant_api_key)
    info = qdrant.get_collection(COLLECTION_NAME)
    print(
        f"\nDone. {written} points written this run. "
        f"Collection '{COLLECTION_NAME}' contains {info.points_count} total."
    )


if __name__ == "__main__":
    main()
