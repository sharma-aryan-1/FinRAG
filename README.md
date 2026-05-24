# FinRAG

An agentic RAG system over SEC 10-K filings. Hybrid retrieval, cross-encoder reranking, structured-data fusion via DuckDB, and a split-pane citation UI, built from the ground up to demonstrate production ML engineering.

> Currently mid-build: Days 1 and 2 of the 5-day plan are complete. The agent layer (LangGraph + Claude + tool use), evaluation harness, and edge-inference variant are still ahead. See [Roadmap](#roadmap).

---

## What's working today

- **End-to-end retrieval pipeline** over ~4,000 chunks of SEC 10-K filings (Apple, Tesla, JPMorgan × FY2022–2024)
- **Three-stage retrieval funnel**: BM25 + dense (Cohere `embed-english-v3.0`) fused via Reciprocal Rank Fusion, then narrowed by Cohere Rerank v3 cross-encoder
- **Structured-data side**: SEC XBRL Company Facts API → DuckDB `financial_facts` table with canonicalized line items across filers. Enables one-line SQL for cross-company and multi-year financial queries
- **Split-pane web UI**: Next.js 14 chat with a live citation viewer that renders both narrative passages and HTML tables, with click-through to the original filing on SEC.gov
- **Citation-clean by construction**: every retrieved chunk carries full provenance (ticker, fiscal year, section, accession number, SEC URL), no joins needed at query time
- **CI-ready smoke harness**: 8 canary cases covering filter compliance, score sanity, cross-company retrieval; runs in <2s

---

## Architecture

```
                  ~4,011 chunks (Qdrant `finrag_chunks`)
                       │
        ┌──────────────┴──────────────┐
        ▼                             ▼
   BM25 top-50                Dense top-50
   (rank_bm25,                (Cohere embed-v3,
    in-process)                Qdrant ANN)
        │                             │
        └─────────────┬───────────────┘
                      ▼
              RRF fusion (k=60)
                      │
                      ▼
              Fused top-50 with hydration
                      │
                      ▼
             Cohere Rerank v3
             cross-encoder, relevance ∈ [0, 1]
                      │
                      ▼
                  Top-5 final
                      │
                      ├──► FastAPI /query (CORS-enabled)
                      │           │
                      │           ▼
                      │     Next.js UI
                      │     split-pane chat + citation
                      │
                      ▼ (parallel structured store)
        DuckDB financial_facts
        (canonicalized via SEC XBRL Company Facts API)
```

---

## Tech stack

| Layer | Choice | Why |
|---|---|---|
| Embeddings | Cohere `embed-english-v3.0`, 1024-dim | Asymmetric retrieval, separate document/query encoders. Production-grade for finance text. |
| Vector store | Qdrant v1.18 (Docker) | Native hybrid roadmap, payload filtering, fast Rust core, free self-host |
| Lexical retrieval | `rank_bm25` (BM25Okapi) | Catches exact-match signal that dense embeddings lose (years, tickers, GAAP jargon) |
| Fusion | Reciprocal Rank Fusion, k=60 | Calibration-free combination of incompatible score scales |
| Reranking | Cohere Rerank v3 | Cross-encoder over fused top-50 → top-K; fixes entity-disambiguation failures |
| Structured store | DuckDB | Columnar, embedded, real SQL which is ideal for financial-statement queries |
| Document parsing | Unstructured.io | Section-aware chunking with table preservation |
| Backend | FastAPI (Python 3.11, uv-managed) | Async-first, Pydantic-validated, plays well with the ML ecosystem |
| Frontend | Next.js 14 + Tailwind | App Router, TypeScript, split-pane chat + citation viewer |
| Data source | SEC EDGAR + XBRL Company Facts API | Free, structured, comprehensive |

---

## Performance

| Metric | Value |
|---|---|
| `/query` latency (steady-state) | ~550 ms |
| Latency breakdown | Embed 150ms · Dense 50ms · BM25 20ms · RRF <1ms · Hydration 30ms · Rerank 300ms |
| Corpus size | 9 filings, ~4,000 retrievable chunks, ~600 structured fact rows |
| Cost to embed full corpus | ~$0.09 |
| Cost per query at runtime | ~$0.002 (rerank-dominated) |

---

## Quick start

### Prerequisites

- Docker Desktop (for Qdrant)
- Python 3.11 + [uv](https://github.com/astral-sh/uv)
- Node 18+ (Next.js dev server)
- API keys for **Anthropic** (Day 3+) and **Cohere** (now)

### Setup

```bash
# 1. Clone and configure
git clone <repo-url> FinRAG
cd FinRAG
cp .env.example .env       # then fill in COHERE_API_KEY and ANTHROPIC_API_KEY

# 2. Start Qdrant
docker compose -f infra/docker-compose.yaml up -d

# 3. Backend deps
cd backend
uv sync

# 4. Frontend deps
cd ../frontend
npm install
```

### Build the data layer

```bash
cd backend

# Download 9 SEC 10-K filings (~80MB total)
uv run python -m finrag.ingestion.edgar

# Parse + chunk
uv run python -m finrag.ingestion.parse

# Embed and upload to Qdrant (~15min on Cohere trial key)
uv run python -m finrag.ingestion.embed

# Build the BM25 index
uv run python -c "from finrag.retrieval.lexical import build_index; build_index()"

# Populate DuckDB from SEC XBRL
uv run python -m finrag.ingestion.facts

# Sanity check should print 8/8 cases passing
uv run python -m finrag.eval.smoke
```

### Run the app

```bash
# Terminal 1: backend
cd backend
uv run uvicorn finrag.main:app --reload --port 8000

# Terminal 2: frontend
cd frontend
npm run dev
```

Open **http://localhost:3000** in your browser. Ask a question like *"How did Apple's services revenue change in 2023?"* and click any citation to view the full source passage.

---

## Project layout

```
FinRAG/
├── docs/
│   ├── day1.md                          # Day 1 reference (dense retrieval foundation)
│   └── day2.md                          # Day 2 reference (full funnel + DuckDB + UI)
│
├── infra/
│   └── docker-compose.yaml              # Qdrant
│
├── backend/                             # Python + FastAPI
│   ├── pyproject.toml                   # uv-managed
│   └── src/finrag/
│       ├── config.py                    # pydantic-settings
│       ├── main.py                      # FastAPI app: /health, /query
│       ├── ingestion/
│       │   ├── edgar.py                 # SEC scraper (handles paginated filers)
│       │   ├── parse.py                 # Unstructured.io → chunks
│       │   ├── embed.py                 # Cohere embed-v3 → Qdrant
│       │   └── facts.py                 # SEC XBRL → DuckDB
│       ├── retrieval/
│       │   ├── vector.py                # Dense (Qdrant) + helpers
│       │   ├── lexical.py               # BM25 inverted index
│       │   ├── hybrid.py                # RRF fusion
│       │   └── rerank.py                # Cohere Rerank v3
│       └── eval/
│           └── smoke.py                 # 8-case canary harness
│
├── frontend/                            # Next.js 14 + TypeScript + Tailwind
│   └── src/
│       ├── app/                         # App Router pages
│       ├── components/                  # ChatPane, ChunkCard, CitationViewer
│       └── lib/                         # api.ts, types.ts
│
└── data/                                # gitignored; populated by ingestion scripts
    ├── raw/                             # downloaded 10-K HTML + metadata
    ├── processed/                       # *.jsonl chunks
    ├── bm25_index.pkl                   # ~25 MB pickle
    └── duckdb/finrag.duckdb             # structured facts
```

---

## Roadmap

| Day | Status | Scope |
|---|---|---|
| 1 | Done | EDGAR scraper, document parsing, chunking, Cohere embeddings → Qdrant, dense `/query` endpoint, smoke harness |
| 2 | Done | BM25 + RRF hybrid retrieval, Cohere Rerank v3, DuckDB structured-data side from SEC XBRL, Next.js split-pane UI |
| 3 | Next | LangGraph agent (rewrite → route → retrieve → tool-loop → synthesize), Claude Sonnet 4.5 integration, SQL/calculator/citation tools, streamed agent trace |
| 4 | Planned | Ragas + custom evaluation across 4 difficulty tiers, Llama 3.2 3B Q4_K_M edge variant, benchmark table |
| 5 | Planned | Deploy (Vercel + Railway), README polish, demo recording |

See [`docs/day1.md`](./docs/day1.md) and [`docs/day2.md`](./docs/day2.md) for in-depth coverage of decisions made, bugs encountered, and concepts learned at each stage.

---

## What this project is designed to demonstrate

- **Retrieval-quality engineering**, not just "RAG with cosine similarity", every stage of the funnel (BM25, dense, RRF, rerank) is in there because it solves a specific class of query that the others fail on.
- **Modal-split data architecture**: text in Qdrant for semantic retrieval, tables in DuckDB for SQL. Same source documents, two representations, citation-clean across both. This is what enterprise AI looks like in practice, most portfolio RAG projects skip it.
- **Production discipline**: typed config, deterministic chunk IDs, idempotent ingestion, exponential-backoff retry on rate limits, payload-filterable retrieval, smoke harness with score floors calibrated per stage. The kind of system that survives contact with reality, not just a demo.
- **Eval-first thinking**, Day 4 will land Ragas + a tier-stratified question set. The eval set is the spec; the code is the means.

---

## License

Personal portfolio project. SEC filings are public domain.
