# FinRAG

**[▶ Live demo](https://finrag-front.vercel.app/)** &nbsp;·&nbsp; built by [Aryan Sharma](https://www.linkedin.com/in/sharmaaryan25/)

An agentic RAG system over SEC 10-K filings. Hybrid retrieval, cross-encoder reranking, structured-data fusion via DuckDB, and a split-pane citation UI, built from the ground up to demonstrate production ML engineering.

> The live demo runs the agent on **Claude Haiku 4.5** behind cost guardrails (per-IP rate limit + a global daily question cap) on a free Hugging Face Space, so a public URL can't run up the bill. It sleeps after extended idle; the first request wakes it (~tens of seconds). For unlimited use and the full Sonnet-grade eval numbers, run it locally (below).

> All five days of the build are complete: retrieval, the structured-data side, the agent layer (LangGraph + Claude tool use + streamed trace UI), a tier-stratified **evaluation harness** with an LLM-judge, and a local **edge-inference variant** (Llama 3.2 3B) benchmarked against the cloud agent. See [What the eval proves](#what-the-eval-proves) and the [Roadmap](#roadmap).

---

## What's working today

- **End-to-end retrieval pipeline** over ~4,000 chunks of SEC 10-K filings (Apple, Tesla, JPMorgan × FY2022–2024)
- **Three-stage retrieval funnel**: BM25 + dense (Cohere `embed-english-v3.0`) fused via Reciprocal Rank Fusion, then narrowed by Cohere Rerank v3 cross-encoder
- **Structured-data side**: SEC XBRL Company Facts API → DuckDB `financial_facts` table with canonicalized line items across filers. Enables one-line SQL for cross-company and multi-year financial queries
- **Split-pane web UI**: Next.js 14 chat with a live citation viewer that renders both narrative passages and HTML tables, with click-through to the original filing on SEC.gov
- **Citation-clean by construction**: every retrieved chunk carries full provenance (ticker, fiscal year, section, accession number, SEC URL), no joins needed at query time
- **LangGraph agent** (`/agent`): plans and routes each question (semantic retrieval vs structured SQL), runs a Claude Sonnet 4.6 native tool-loop over three tools (`sql_query`, `calculator`, `lookup_citation`), and synthesizes a grounded, cited answer
- **Streamed agent trace** (`/agent/stream`, Server-Sent Events): node-level reasoning milestones *and* token-by-token answer streaming, rendered as a live trace UI (with the agent's generated SQL inline)
- **Provider seam**: synthesis/agent backend is swappable between Anthropic Claude, Google Gemini, **and a local Llama 3.2 3B** (Ollama) behind neutral types. All three are wired for evaluation A/B, selected live by one setting
- **Tier-stratified eval harness** (`eval/harness.py`): 30 questions × 4 difficulty tiers (factual / narrative / multihop / honesty), ground truth derived live from the `financial_facts` table, scored by deterministic checks (number / citation / refusal) **plus** an LLM-judge (faithfulness / relevance / context-precision). Closed an eval→improve loop that caught and fixed a real faithfulness defect
- **Edge variant**: the same agent runs on a local 3B model with zero cloud cost, and the benchmark surfaced a counterintuitive finding about *why* the agentic architecture matters for small models (see [below](#what-the-eval-proves))
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
| Agent orchestration | LangGraph | Typed state machine: plan → route → retrieve → tool-loop → synthesize, with streamed trace (no LangChain) |
| LLM (synthesis + agent) | Anthropic Claude Sonnet 4.6 (`claude-sonnet-4-6`) | Reliable native tool use; Gemini 2.5 Flash-Lite wired as the swappable alternate |
| Document parsing | Unstructured.io | Section-aware chunking with table preservation |
| Backend | FastAPI (Python 3.11, uv-managed) | Async-first, Pydantic-validated, plays well with the ML ecosystem |
| Frontend | Next.js 14 + Tailwind + react-markdown | App Router, TypeScript, split-pane chat + live agent-trace UI |
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

## What the eval proves

"I built RAG" is common. "I built RAG and can show you its faithfulness, citation-validity, and honesty numbers, across difficulty tiers, with a cost/quality provider comparison, and a defect the eval caught and I fixed" is the production-RAG signal. The harness (`finrag.eval.harness`) runs 30 questions across four tiers and scores each with both deterministic checks and a Claude LLM-judge. Full method and analysis in [`docs/day4.md`](./docs/day4.md) and [`docs/day5.md`](./docs/day5.md).

**Cloud baseline (Claude Sonnet 4.6, 30 cases, 0 errors, ~$0.39/run):**

| Metric | Value |
|---|---|
| Exact-match accuracy (factual + multihop + honesty refusal) | **1.00** |
| Citation validity (narrative) | **1.00** |
| Faithfulness (graded tiers) | **~0.92** |
| Answer relevance | **0.98** |
| Context precision (narrative top-8) | 0.55 |

The eval also closed a loop: the judge flagged factually-correct answers adding ungrounded flourish ("as disclosed in the 10-K", "record-setting profit"); a one-line "don't embellish" prompt rule lifted multihop faithfulness 0.88 → 0.97 with accuracy unchanged. **The eval found a real defect and proved the fix.**

**Edge variant (local Llama 3.2 3B via Ollama, $0 generation cost), and the finding it produced:**

The interesting result is an A/B between the full agentic tool-loop and a degraded synthesis-only mode on the same 3B model. It **inverts the usual assumption** that small models can't handle tools:

| Metric (3B local) | Agentic tool-loop | Synthesis-only |
|---|---|---|
| Factual accuracy | **0.80** | **0.00** |
| Honesty / refusal | 0.80 | 1.00 |
| Faithfulness | 0.84 | 0.47 |
| Overall accuracy (n=30) | **0.59** | 0.23 |

A 3B model can't read an exact 12-digit figure out of prose, but it *can* reliably **invoke** a `sql_query` tool, so the deterministic SQL layer supplies the precision the model lacks. **The agentic architecture is what makes a small edge model viable, not a tax on it.** Strip the tools away "to simplify," and factual accuracy collapses from 0.80 to zero. (A bonus deployment finding: a 4GB consumer GPU can't safely co-host even a 3B model with a desktop session; CPU inference is the stable edge path. See `docs/day5.md`.)

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

Open **http://localhost:3000** in your browser. Ask a question like *"How did Apple's services revenue change in 2023?"* and watch the agent's trace stream live: query rewrite, route decision, retrieval, any tool calls (with the generated SQL inline), and the token-by-token answer. Then click any citation to view the full source passage.

---

## Project layout

```
FinRAG/
├── docs/
│   ├── day1.md                          # Day 1 reference (dense retrieval foundation)
│   ├── day2.md                          # Day 2 reference (full funnel + DuckDB + UI)
│   ├── day3.md                          # Day 3 reference (agent + tools + streaming + trace UI)
│   ├── day4.md                          # Day 4 reference (eval harness + judge + provider A/B)
│   ├── day5.md                          # Day 5 reference (local edge variant + tool-loop A/B)
│   └── demo.md                          # scripted demo runbook (3 questions + what to show)
│
├── infra/
│   └── docker-compose.yaml              # Qdrant
│
├── backend/                             # Python + FastAPI
│   ├── pyproject.toml                   # uv-managed
│   └── src/finrag/
│       ├── config.py                    # pydantic-settings (llm_provider seam)
│       ├── main.py                      # FastAPI: /health, /query, /answer, /agent, /agent/stream
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
│       ├── llm/                         # provider seam: base, dispatchers, claude.py, gemini.py, local.py
│       ├── tools/                       # sql_query, calculator, lookup_citation (+ registry)
│       ├── agent/                       # LangGraph: state, nodes, graph
│       └── eval/
│           ├── smoke.py                 # 8-case retrieval canary harness
│           ├── dataset.py               # 30-Q tiered eval set, DB-derived ground truth
│           ├── metrics.py               # deterministic + LLM-judge scorers
│           └── harness.py               # provider-parametrized runner + per-tier report
│
├── frontend/                            # Next.js 14 + TypeScript + Tailwind
│   └── src/
│       ├── app/                         # App Router (page.tsx drives streamAgent)
│       ├── components/                  # ChatPane, ChunkCard, CitationViewer, AgentTrace, AgentAnswer
│       └── lib/                         # api.ts (streamAgent SSE client), types.ts
│
└── data/                                # gitignored; populated by ingestion scripts
    ├── raw/                             # downloaded 10-K HTML + metadata
    ├── processed/                       # *.jsonl chunks
    ├── bm25_index.pkl                   # ~3.4 MB pickle
    └── duckdb/finrag.duckdb             # structured facts
```

---

## Roadmap

| Day | Status | Scope |
|---|---|---|
| 1 | Done | EDGAR scraper, document parsing, chunking, Cohere embeddings → Qdrant, dense `/query` endpoint, smoke harness |
| 2 | Done | BM25 + RRF hybrid retrieval, Cohere Rerank v3, DuckDB structured-data side from SEC XBRL, Next.js split-pane UI |
| 3 | Done | LangGraph agent (plan/route → retrieve → tool-loop → synthesize), Claude Sonnet 4.6 + provider seam, SQL/calculator/citation tools, SSE streaming, live agent-trace UI |
| 4 | Done | Hand-rolled (no-Ragas) evaluation harness across 4 difficulty tiers, deterministic + LLM-judge scoring, Claude-vs-Gemini A/B, closed eval→improve loop |
| 5 | Done | Local Llama 3.2 3B edge variant behind the provider seam, tool-loop vs synthesis-only A/B, CPU-pinned deployment finding |

See [`docs/day1.md`](./docs/day1.md) … [`docs/day5.md`](./docs/day5.md) for in-depth coverage of decisions made, bugs encountered, and concepts learned at each stage.

---

## What this project is designed to demonstrate

- **Retrieval-quality engineering**, not just "RAG with cosine similarity", every stage of the funnel (BM25, dense, RRF, rerank) is in there because it solves a specific class of query that the others fail on.
- **Modal-split data architecture**: text in Qdrant for semantic retrieval, tables in DuckDB for SQL. Same source documents, two representations, citation-clean across both. This is what enterprise AI looks like in practice, most portfolio RAG projects skip it.
- **Production discipline**: typed config, deterministic chunk IDs, idempotent ingestion, exponential-backoff retry on rate limits, payload-filterable retrieval, smoke harness with score floors calibrated per stage. The kind of system that survives contact with reality, not just a demo.
- **Eval-first thinking**: a tier-stratified question set with ground truth derived live from the data, scored by deterministic checks plus an LLM-judge, hand-rolled rather than reaching for Ragas, to keep the dependency tree lean and the scoring legible. The eval set is the spec; the code is the means.

---

## License

Personal portfolio project. SEC filings are public domain.
