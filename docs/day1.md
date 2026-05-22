# FinRAG — Day 1 Reference

A self-contained record of everything we built, decided, and broke on Day 1. Designed to be reviewed standalone — without the chat history — and to seed resume/interview material once the project is done.

---

## 1. At a glance

**FinRAG** is an end-to-end agentic RAG system over SEC 10-K filings. Day 1 ships the **dense retrieval foundation**: from raw HTML on disk to a filterable HTTP endpoint with provenance-rich citations.

| Day-1 deliverable | Status |
|---|---|
| EDGAR scraper (handles paginated filers like JPM) | ✓ |
| HTML → structured chunks (`unstructured.io`, per-section) | ✓ |
| Tables emitted as their own chunks (HTML preserved) | ✓ |
| Cohere embed-v3 ingestion → Qdrant | ✓ |
| FastAPI `/query` endpoint, dense search + payload filters | ✓ |
| Smoke harness (8 canary cases) | ✓ 8/8 |

| Metric | Value |
|---|---|
| Filings ingested | 9 (AAPL/TSLA/JPM × FY2022/23/24) |
| Chunks indexed | ~4,011 |
| Embedding model | `embed-english-v3.0`, 1024-dim, cosine |
| Vector store | Qdrant v1.18 (Docker, persistent volume) |
| `/query` latency | ~100ms |
| Cost to embed full corpus | ~$0.09 |

---

## 2. System architecture (Day 1 state)

```
                 ┌───────────────────────────────────────────────┐
                 │                Ingestion (offline)            │
                 │                                               │
                 │  SEC EDGAR ──► scraper ──► data/raw/*.htm     │
                 │                            + metadata.json    │
                 │                                               │
                 │  parse.py ──► partition_html                  │
                 │              ──► section-bucketed chunks      │
                 │              + table chunks (HTML preserved)  │
                 │              ──► data/processed/*.jsonl       │
                 │                                               │
                 │  embed.py ──► Cohere embed-v3 (search_doc)    │
                 │              ──► Qdrant `finrag_chunks`       │
                 │                  4011 points, 1024-dim cosine │
                 └───────────────────────────────────────────────┘

                 ┌───────────────────────────────────────────────┐
                 │                Retrieval (online)             │
                 │                                               │
                 │  POST /query                                  │
                 │     { question, top_k, ticker?, fy?, type? }  │
                 │                       │                       │
                 │  Cohere embed-v3 ◄────┘                       │
                 │  (search_query)                               │
                 │       │                                       │
                 │       ▼                                       │
                 │  Qdrant nearest-neighbor + payload filter     │
                 │       │                                       │
                 │       ▼                                       │
                 │  Top-k chunks with full citation metadata     │
                 └───────────────────────────────────────────────┘
```

---

## 3. Repository layout (final state of Day 1)

```
FinRAG/
├── PLAN.md                          # original project plan
├── README.md                        # populated end of Day 5
├── .env                             # secrets, gitignored
├── .env.example                     # placeholders, committed
├── .gitignore                       # patterns flush-left (whitespace matters!)
│
├── infra/
│   └── docker-compose.yaml          # Qdrant v1.18 with named volume
│
├── backend/
│   ├── pyproject.toml               # uv-managed
│   ├── uv.lock                      # locked dep tree
│   ├── .python-version              # 3.11
│   └── src/finrag/
│       ├── config.py                # pydantic-settings, REPO_ROOT-anchored .env
│       ├── main.py                  # FastAPI app: /health + /query
│       ├── ingestion/
│       │   ├── edgar.py             # SEC scraper with pagination
│       │   ├── parse.py             # unstructured + chunking
│       │   └── embed.py             # Cohere + Qdrant upsert
│       ├── retrieval/
│       │   └── vector.py            # dense search + filter builder
│       └── eval/
│           └── smoke.py             # 8 canary cases
│
└── data/
    ├── raw/                         # 9 filing dirs, gitignored
    ├── processed/                   # JSONL chunks, gitignored
    └── duckdb/                      # Day 2 territory, empty
```

---

## 4. Data journey — one chunk's story

Following one passage from Apple's FY2023 10-K through the full pipeline.

| Stage | What the chunk looks like |
|---|---|
| **Raw HTML** | Buried inside ~30 MB of `<div>` markup at `data/raw/AAPL_2023/filing.htm` |
| **After partition** | A `NarrativeText` element with `.text = "Services revenue increased $7.1 billion or 9%..."`, preceded by a `Title` element `"Services"` |
| **After chunking** | A `Chunk` object with text + `section_title="Item 7..."` + ticker/FY/SEC URL provenance + deterministic 16-hex `chunk_id` |
| **In JSONL** | One line in `data/processed/AAPL_2023.jsonl` |
| **After embedding** | A 1024-dim vector + full Chunk fields as Qdrant payload, point ID `int(chunk_id, 16)` |
| **At query time** | Top-1 result for "How did Apple's services revenue change in 2023?", score 0.725 |

The lesson, in one line: **every stage preserves provenance. No fact ever loses its source.**

---

## 5. Decisions — concise summaries

### Decision 1 — Repo skeleton

- **`src/` layout** (`backend/src/finrag/`) not flat — forces tests to import the installed package, mirrors what end-users would see.
- `.gitignore` patterns must be **flush-left** (leading whitespace is part of the pattern; this bit us later).
- `data/raw/`, `data/processed/`, `data/duckdb/` all gitignored; `.gitkeep` files committed to preserve structure.
- `infra/` is its own top-level dir, not under `backend/` — orchestration belongs to the system, not one service.

### Decision 2 — Backend project with uv

- `uv init --package --name finrag --python 3.11`
- One source of truth for config: `pydantic-settings.BaseSettings` reading `.env` via an **absolute path** (`Path(__file__).parents[3] / ".env"`). Don't rely on CWD.
- `pyproject.toml` adds: `fastapi[standard]`, `pydantic-settings`, `qdrant-client`, `cohere`, `unstructured[pdf]`, `sec-edgar-downloader`, `httpx`. Dev: `ruff`, `pytest`.
- `ruff` config: line-length 100, target py311, rule sets `E, F, I, B, UP, N`.

### Decision 3 — Qdrant via docker-compose

```yaml
services:
  qdrant:
    image: qdrant/qdrant:v1.18.0          # version-pin; never use :latest
    ports: ["6333:6333", "6334:6334"]     # REST + gRPC
    volumes:
      - qdrant_storage:/qdrant/storage    # NAMED volume — survives container recreate
    healthcheck: ...                      # TCP probe on 6333
    restart: unless-stopped
volumes:
  qdrant_storage:
```

- Named volume > bind mount on Windows (no perms issues, better I/O).
- `docker compose down` keeps data; `docker compose down -v` destroys it. Memorize this distinction.
- Qdrant dashboard at `http://localhost:6333/dashboard`.

### Decision 4 — EDGAR scraper

- Mandatory `User-Agent: First Last email@example.com`. Without it, SEC returns 403 silently.
- Two endpoints used:
  - `https://www.sec.gov/files/company_tickers.json` — full ticker→CIK map (one cheap fetch).
  - `https://data.sec.gov/submissions/CIK{padded}.json` — filings history.
- **`filings.recent` is capped at ~1000 entries.** High-volume filers (JPM, banks) overflow into a `filings.files` pagination array — must walk these for older 10-Ks.
- Idempotency: directory-per-filing layout, skip if both `filing.htm` and `metadata.json` exist.
- Metadata schema captures the distinction between `filing_date` (when submitted) and `period_of_report` (fiscal year-end). Don't conflate.

### Decision 5 — Parsing + chunking

The most consequential Day-1 choice. We picked **structural / hierarchical chunking** via `unstructured.io`.

```python
elements = partition_html(filename=str(htm_path))
# Walk once: bucket by Title, peel out Tables to their own stream
# Then chunk_by_title PER SECTION (not globally):
composite_chunks = chunk_by_title(
    section["elements"],
    max_characters=1500,          # hard ceiling under Cohere's ~512-token cap
    new_after_n_chars=1200,       # soft target ~80% of max
    combine_text_under_n_chars=200,  # absorb stub sections
    overlap=150,                  # only kicks in on mid-section splits
)
```

Why per-section, not document-wide: guarantees no chunk crosses a section boundary; gives clean `section_title` attribution. Tables go to their own stream — embedded as their HTML (`metadata.text_as_html`) to preserve cell/column structure.

Deterministic chunk IDs (SHA-256 of `ticker|fy|position|text`, 16 hex chars) → re-ingestion *overwrites* in Qdrant rather than duplicating.

Output: `data/processed/{TICKER}_{FY}.jsonl`, one Chunk per line.

### Decision 6 — Embedding + Qdrant

**The non-obvious thing**: Cohere v3 is an **asymmetric retrieval model**. Two encoders, two `input_type`s:

```python
co.embed(
    texts=texts,
    model="embed-english-v3.0",
    input_type="search_document",  # ← indexing side
    embedding_types=["float"],
)
```

The matching `search_query` lives in the query endpoint. Mismatching them silently degrades retrieval — no error, just worse results.

Other mechanics:
- 1024-dim, cosine distance.
- Cohere caps batches at **96 texts/request**.
- Trial keys are limited to **100k tokens/min** — burst-tolerant retry with exponential backoff (start 30s, double) handles 429s gracefully.
- Qdrant accepts only UUID or unsigned-int point IDs. `int(chunk_id_hex, 16)` → uint64, preserves determinism.
- Full Chunk model dumped into the Qdrant payload — every later retrieval gets full provenance with no joins.

### Decision 7 — FastAPI `/query`

Minimal Day-1 endpoint, deliberately retrieval-only (no LLM, no agent, no rerank).

```python
POST /query
{
  "question": "How did Apple's services revenue change in 2023?",
  "top_k": 5,
  "ticker": "AAPL",        # optional
  "fiscal_year": 2023,     # optional
  "chunk_type": "table"    # optional
}
```

- `@lru_cache(maxsize=1)` on client factories — single Cohere and Qdrant client per process.
- Query embedding uses `input_type="search_query"` (the other half of v3's asymmetric pair).
- Qdrant payload filtering via `Filter(must=[FieldCondition(...)])` — same store, scoped retrieval.
- Pydantic request validation rejects empty/over-long questions, bounds `top_k` to [1, 50].

### Decision 8 — Smoke harness

8 canary cases in `finrag.eval.smoke` covering:

1. Basic narrative retrieval per company
2. Filter compliance (ticker + fiscal_year + chunk_type)
3. Cross-company retrieval
4. Multi-year topic continuity

Pass criteria are **structural**, not quality-based:
- Non-empty result
- Top score ≥ 0.35 (anti-mismatch floor for Cohere v3 cosine)
- Every returned chunk satisfies the active filter

Persists `data/smoke_results.json` for future diffing. Exits 0 / 1 — CI-ready.

**Smoke ≠ eval.** Smoke catches plumbing regressions in 30 seconds. Eval (Day 4) measures whether answers are *good* using Ragas + golden answers + tiered difficulty.

---

## 6. Bugs encountered — and what to remember

The "tutorials don't go long enough to hit these" list.

| # | What broke | Why | Lesson |
|---|---|---|---|
| 1 | `.env` not found despite existing | `pydantic-settings` reads `.env` relative to CWD; we ran from various dirs | Always resolve config paths to an absolute repo root |
| 2 | `httpx` GET to `data.sec.gov` returned 404 | I set `Host: www.sec.gov` as a client default; it overrode every request | Never hardcode `Host`. Let HTTP clients derive it per-URL |
| 3 | JPM 10-Ks not found in `filings.recent` | JPM files ~500 submissions/year; `recent` capped at ~1000 | High-volume filers need pagination walk over `filings.files` |
| 4 | Cohere returned 401 "Incorrect API key" | Trial key was placeholder/invalid | When debugging API auth, hit the service with a minimal isolated call first |
| 5 | Cohere returned 429 mid-run | Hit the 100k-tokens/min trial limit | Wrap API calls with exponential-backoff retry, not just `sleep` |
| 6 | Qdrant client/server version skew, silent empty results | Client v1.18 talking to server v1.13 — `query_points` request format mismatch | Keep Qdrant client and server within one minor version |
| 7 | `/query` returned empty even after fixes | `lru_cache` held a dead `QdrantClient` from before Docker restart | Restart dependent processes when their dependencies restart |
| 8 | `.gitignore` had no effect | Every pattern had 2 spaces of leading indent — Git treated them as literal patterns | `.gitignore` patterns must be flush-left; whitespace is significant |
| 9 | Filters with `ticker` or `chunk_type` returned empty | Swagger UI sends `""` for unset string fields; `""` is not `None`, my filter built `ticker == ""` | At API boundaries, treat falsy values as absent (`if v:` not `if v is not None:`) |

---

## 7. Concepts internalized

The five frames from the conceptual walkthrough, distilled to one paragraph each.

### Frame 1 — Why RAG exists

LLMs are frozen (knowledge cutoff), confident (no built-in "I don't know"), and ungrounded (no citations). **Fine-tune for behavior, retrieve for knowledge.** Retrieval and generation are architecturally separate — that separation is what makes citations possible. Without it, parametric knowledge erases the link to its source.

### Frame 2 — Retrieval is the bottleneck

Dense embeddings miss exact tokens (years, tickers, GAAP terms). BM25 misses paraphrases ("revenue" vs "net sales"). **Hybrid + RRF + cross-encoder rerank** is the funnel: BM25 + dense (fast, lossy) widen the net; cross-encoder rerank (slow, accurate) finishes the cut. Each stage trades speed for accuracy at a different point.

### Frame 3 — Why agents

Fixed RAG pipelines handle Tier-1 factual questions. Multi-document, computational, or cross-company questions (Tiers 2–4) need an interpreter that can take multiple hops, call tools (SQL, calculator), and decide its own path. **The LLM writes a tiny program; the runtime executes it.** State-machine frameworks (LangGraph) give you constraints + observability that a free-form tool loop can't.

### Frame 4 — Structured + unstructured fusion

10-Ks are half tables. Don't pretend they're prose — extract them to a real database and query with SQL. **A canonical normalized fact table** (one row per `company × fiscal_year × period × line_item`) makes cross-company and multi-year queries one-liners. Text-to-SQL is risky (hallucinated schema, semantic drift) but the mitigations are architectural (pass the schema, log the query, eval the path).

### Frame 5 — Eval-first thinking

The eval set is the spec. **Write the questions before the retriever.** Ragas gives a four-quadrant diagnostic (faithfulness, answer relevancy, context precision, context recall) — the *pattern* of scores tells you which subsystem is broken. Custom domain harnesses catch what Ragas can't (arithmetic correctness, citation accuracy, fiscal calendar). Tiered eval — explicitly showing where your system breaks — is more credible than uniform numbers.

---

## 8. Resume / interview material

### Pre-tested resume bullets (Day-1 only — expand after Day 5)

- **Built end-to-end RAG ingestion pipeline** over SEC 10-K filings: SEC EDGAR scraper (with pagination handling for high-volume filers), structural HTML parsing via Unstructured.io, section-aware chunking with per-section boundaries, and tables emitted separately preserving HTML structure for downstream SQL extraction.

- **Indexed ~4,000 chunks into Qdrant** with Cohere `embed-english-v3.0` (1024-dim, cosine). Correctly handled v3's asymmetric retrieval (`search_document` vs `search_query`), implemented deterministic SHA-256 chunk IDs for idempotent re-ingestion, and added exponential-backoff retry on rate-limited batches.

- **Designed full chunk provenance** so every retrievable unit carries ticker, fiscal year, accession number, SEC URL, and section title — enabling clickable citations and payload-filtered retrieval without joining external stores.

- **Shipped FastAPI `/query` endpoint** with Pydantic-validated request schema, optional payload filters (ticker / fiscal_year / chunk_type), and sub-100ms median latency.

- **Built an 8-case structural smoke harness** that runs in 30 seconds and exits with a CI-compatible status code; persists JSON results for diffing against prior runs to catch regressions in plumbing without conflating them with retrieval-quality changes.

### Interview questions to prepare for

| Question | What to say |
|---|---|
| "Why did you choose hybrid retrieval?" | Dense embeddings lose exact-match signal on numbers, years, and jargon — critical in finance. BM25 handles those. RRF fuses without score calibration. |
| "Why Qdrant and not pgvector / Pinecone / Chroma?" | OSS + free self-host, native hybrid in roadmap, payload filtering, fast Rust core. Pinecone is closed. Chroma less performant at scale. pgvector only attractive if Postgres already in stack. |
| "How do you cite a number from a table?" | Tables are stored both as Qdrant chunks (for retrieval) and (Day 2) as normalized rows in DuckDB. SQL result rows reference back to the source chunk via `source_chunk_id`, which carries the same citation envelope as text chunks. |
| "What did you do about Cohere's input_type?" | v3 is asymmetric — different encoder for documents vs queries. Indexing uses `search_document`; the query endpoint uses `search_query`. Mismatching silently degrades retrieval, which is why I'd lead with this in a code review. |
| "What's the biggest risk in your pipeline?" | Text-to-SQL semantic drift (Day 2+). Mitigations: always pass the schema in the prompt, log the SQL to the agent trace, eval the SQL against expected output, run with a read-only DB connection. |
| "How would you evaluate this?" | Ragas for four-quadrant diagnostic (faithfulness, relevancy, context precision, context recall). Custom harness for computational correctness and citation accuracy. Tiered eval set (factual / multi-doc / computational / cross-company) — score the *gradient*, not just the absolute. |

### Glossary — terms you should be able to define on the spot

| Term | One-line definition |
|---|---|
| **Embedding** | Mapping text to a vector in a learned space where semantic similarity ≈ geometric proximity. |
| **Bi-encoder** | Encodes query and document independently; fast but lossy. (Cohere embed-v3 is one.) |
| **Cross-encoder** | Encodes (query, document) pair together; accurate but slow. (Cohere Rerank v3 is one.) |
| **BM25** | TF-IDF-style lexical retrieval scoring; weights rare matching tokens highly. |
| **Reciprocal Rank Fusion (RRF)** | `score(d) = Σ 1/(k + rank_r(d))` — combines ranked lists from multiple retrievers without score calibration. |
| **Asymmetric retrieval** | A model with separate query and document encoders. Cohere v3 is asymmetric; OpenAI ada-002 is symmetric. |
| **HNSW** | Hierarchical Navigable Small World — the ANN index Qdrant uses by default. |
| **Faithfulness (Ragas)** | Fraction of claims in the answer that are supported by the retrieved context. |
| **Context precision / recall** | Whether retrieved chunks are relevant (precision) and whether the relevant chunks were retrieved (recall). |
| **Lost-in-the-middle** | LLMs over-weight context at the start and end of the prompt; middle content gets neglected. Argues against stuffing too many chunks. |

---

## 9. What's deliberately not done yet

| Capability | Day |
|---|---|
| BM25 + RRF hybrid retrieval | 2 |
| Cohere Rerank v3 (cross-encoder final stage) | 2 |
| DuckDB structured-data side (tables → normalized rows → SQL tool) | 2 |
| Next.js frontend with split-pane chat + citation viewer | 2 |
| LangGraph agent: rewrite → route → retrieve → tool-loop → synthesize | 3 |
| Tools: SQL query, calculator, citation lookup | 3 |
| Streamed agent trace to frontend | 3 |
| Ragas + custom evaluation harness, 30–50 question set across 4 tiers | 4 |
| Llama 3.2 3B Q4_K_M edge variant + benchmark table | 4 |
| Deployment (Vercel + Railway/Fly.io) + 90s demo | 5 |

---

## 10. Day-2 preview

The high-leverage moves, in order:

1. **BM25 over the existing chunks** — `rank-bm25`, in-memory, no new infra.
2. **Reciprocal Rank Fusion** combining BM25 ranks with Qdrant ranks. 10 lines of code, measurable lift on year/ticker queries.
3. **Cohere Rerank v3** as the final stage over fused top-50 → top-5. Adds the explicit Cohere hiring signal.
4. **DuckDB extraction** — parse table chunks into a normalized `financial_facts(company, fiscal_year, period, line_item, value, unit, source_chunk_id)` table. This is Frame 4 made concrete.
5. **Next.js scaffold** with split-pane chat + citation viewer.

After Day 2, retrieval quality should jump significantly and the structured-data differentiator becomes visible. The smoke harness will tell us instantly whether any of those changes regressed plumbing.
