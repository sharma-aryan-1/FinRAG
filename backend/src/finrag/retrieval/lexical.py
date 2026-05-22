"""BM25 lexical retrieval over the same chunks indexed in Qdrant.

Why this exists: dense embeddings (Cohere v3) lose exact-match signal on
years, tickers, GAAP terminology, and dollar amounts — exactly the tokens
that matter most in financial documents. BM25 catches these.

This module is *not* a full retriever. It returns (chunk_id, score) pairs.
The fusion + hydration into full RetrievedChunk objects happens in
retrieval.hybrid (Decision 10).

Pipeline:
    build:  data/processed/*.jsonl  ──▶  tokenize  ──▶  BM25Okapi
                                                          │
                                                          ▼
                                          pickled to data/bm25_index.pkl
    load:   pickle load → ready to search
"""

from __future__ import annotations

import pickle
import re
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from rank_bm25 import BM25Okapi

from finrag.ingestion.parse import PROCESSED_DIR, Chunk

# parse.py → ingestion/ → finrag/ → src/ → backend/ → ROOT
REPO_ROOT = Path(__file__).resolve().parents[4]
INDEX_PATH = REPO_ROOT / "data" / "bm25_index.pkl"

# Token regex: any run of alphanumeric chars including underscores. Drops
# punctuation, splits on whitespace + symbols. Lowercased before splitting.
# Critical: this exact function is also called on queries — the same vocab
# must be used on both sides or no terms will match.
_TOKEN_RE = re.compile(r"\w+")


def tokenize(text: str) -> list[str]:
    """Lowercase + simple word tokenization.

    The same function runs on chunk text at index time and on user queries
    at search time. Don't tweak one side without the other.
    """
    return _TOKEN_RE.findall(text.lower())


# ── On-disk format ────────────────────────────────────────────────────────
@dataclass
class _BM25Bundle:
    """What we pickle. Separated so we can version the schema later.

    `chunks` is a list of (id, ticker, fiscal_year, chunk_type) tuples — the
    minimal payload we need for post-search filtering and lookups. The full
    chunk text/metadata lives in Qdrant; storing it twice would double disk
    use and risk drift between stores.
    """

    bm25: BM25Okapi
    # Parallel arrays — index `i` in `bm25` corresponds to chunks[i].
    # We use a tuple-list rather than a dict because BM25Okapi indexes by
    # position, not by chunk_id.
    chunk_ids: list[str]
    tickers: list[str]
    fiscal_years: list[int]
    chunk_types: list[str]


# Pin __module__ so pickle records the dotted path "finrag.retrieval.lexical"
# instead of "__main__" when this file is run via `python -m`. Without this,
# the pickle is only loadable from the same entrypoint that built it.
_BM25Bundle.__module__ = "finrag.retrieval.lexical"


# ── Build ─────────────────────────────────────────────────────────────────
def _load_all_chunks(processed_dir: Path) -> list[Chunk]:
    chunks: list[Chunk] = []
    for jsonl in sorted(processed_dir.glob("*.jsonl")):
        for line in jsonl.read_text(encoding="utf-8").splitlines():
            if line.strip():
                chunks.append(Chunk.model_validate_json(line))
    return chunks


def build_index(processed_dir: Path = PROCESSED_DIR) -> _BM25Bundle:
    """Build a BM25 index from all chunks in processed_dir and persist it."""
    chunks = _load_all_chunks(processed_dir)
    if not chunks:
        raise RuntimeError(f"No chunks found in {processed_dir}")

    print(f"Tokenizing {len(chunks)} chunks…")
    tokenized = [tokenize(c.text) for c in chunks]

    print("Building BM25Okapi index…")
    # k1=1.5, b=0.75 are BM25's standard defaults. The rank_bm25 library
    # exposes these as kwargs; leave them at defaults unless we have a
    # specific reason — these are well-calibrated for English text and
    # any tuning we'd do should be eval-driven, not guess-driven.
    bm25 = BM25Okapi(tokenized)

    bundle = _BM25Bundle(
        bm25=bm25,
        chunk_ids=[c.chunk_id for c in chunks],
        tickers=[c.ticker for c in chunks],
        fiscal_years=[c.fiscal_year for c in chunks],
        chunk_types=[c.chunk_type for c in chunks],
    )

    INDEX_PATH.parent.mkdir(parents=True, exist_ok=True)
    with INDEX_PATH.open("wb") as f:
        pickle.dump(bundle, f, protocol=pickle.HIGHEST_PROTOCOL)
    print(f"Wrote {INDEX_PATH} ({INDEX_PATH.stat().st_size / 1024:.0f} KB)")

    return bundle


# ── Load + search ────────────────────────────────────────────────────────
_cached_bundle: _BM25Bundle | None = None


def load_index() -> _BM25Bundle:
    """Load the pickled index from disk, caching in-process.

    Module-level cache (not lru_cache) because the underlying BM25Okapi
    object is heavyweight (~30 MB at our scale) — we want exactly one in
    memory regardless of how many callers ask for it.
    """
    global _cached_bundle
    if _cached_bundle is None:
        if not INDEX_PATH.exists():
            raise FileNotFoundError(
                f"BM25 index not found at {INDEX_PATH}. "
                "Run `uv run python -m finrag.retrieval.lexical` to build it."
            )
        with INDEX_PATH.open("rb") as f:
            _cached_bundle = pickle.load(f)
    return _cached_bundle


def search(
    query: str,
    top_k: int = 50,
    ticker: str | None = None,
    fiscal_year: int | None = None,
    chunk_type: str | None = None,
) -> list[tuple[str, float]]:
    """Return ranked (chunk_id, score) tuples for a query.

    Filtering is post-ranking: we ask BM25 for top-N (where N > top_k to
    leave headroom after filtering), then drop chunks that don't match.
    This is fine at our scale (~4k chunks); at 1M+ you'd want a filter-
    aware index structure or pre-shard by ticker.
    """
    bundle = load_index()
    tokens = tokenize(query)
    if not tokens:
        return []

    # get_scores returns one score per indexed document, in index order
    scores = bundle.bm25.get_scores(tokens)

    # Build candidate list — over-fetch to allow for filter attrition.
    # 4x is a heuristic; if filters are tight (e.g. one ticker × one year),
    # we may want more — but unbounded over-fetch defeats the purpose.
    candidate_count = top_k * 4 if (ticker or fiscal_year or chunk_type) else top_k
    candidate_count = min(candidate_count, len(scores))

    # argpartition is O(n) vs argsort's O(n log n) — meaningful at scale.
    # We get the top-K unordered, then sort just those K.
    top_indices = np.argpartition(-scores, candidate_count - 1)[:candidate_count]
    # Sort the candidates by descending score
    top_indices = top_indices[np.argsort(-scores[top_indices])]

    results: list[tuple[str, float]] = []
    for i in top_indices:
        if ticker and bundle.tickers[i] != ticker:
            continue
        if fiscal_year and bundle.fiscal_years[i] != fiscal_year:
            continue
        if chunk_type and bundle.chunk_types[i] != chunk_type:
            continue
        results.append((bundle.chunk_ids[i], float(scores[i])))
        if len(results) >= top_k:
            break

    return results


# ── CLI ───────────────────────────────────────────────────────────────────
def main() -> None:
    build_index()

    # Sanity check: run a couple of test queries
    print("\nSanity-check queries:")
    for q in [
        "services revenue 2023",
        "iPhone net sales",
        "SG&A expense",
        "Tesla R&D",
    ]:
        results = search(q, top_k=3)
        print(f"\n  Q: {q!r}")
        for chunk_id, score in results:
            print(f"    {chunk_id}  score={score:.3f}")


if __name__ == "__main__":
    main()
