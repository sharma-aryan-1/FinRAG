# FinRAG — Project Plan

> An enterprise-grade agentic RAG system for financial document intelligence. Ingests SEC 10-K filings, answers analyst-style questions with grounded citations, and supports both cloud and edge inference.

---

## 1. Project Goals

Build a portfolio-quality, end-to-end web application that demonstrates production ML engineering across four dimensions:

1. **Retrieval quality** — hybrid search with reranking, not naive vector search
2. **Agentic reasoning** — multi-step query handling with tool use (SQL, calculator, citation lookup)
3. **Structured + unstructured fusion** — financial tables extracted to SQL, narrative text retrieved via embeddings, agent decides which to use
4. **Evaluation rigor** — quantitative metrics (faithfulness, retrieval recall, answer accuracy) reported in the README
5. **Edge deployment story** — quantized local LLM variant as a feature flag, with a benchmark table

Target audience: ML/data science recruiters at Cohere, C3 AI, webAI, and Experian.

---

## 2. High-Level Architecture

```
┌─────────────────────────────────────────────────────┐
│  Next.js Frontend (Vercel)                          │
│  - Chat UI with streaming                           │
│  - Document upload + filing browser                 │
│  - Citation viewer (split-pane)                     │
│  - Eval dashboard, retrieval inspector              │
└──────────────────┬──────────────────────────────────┘
                   │ REST / SSE streaming
┌──────────────────▼──────────────────────────────────┐
│  FastAPI Backend (Railway / Fly.io)                 │
│  ┌────────────────────────────────────────────────┐ │
│  │  Agent Orchestrator (LangGraph)                │ │
│  │  ├─ Query rewriter → Router → Retriever        │ │
│  │  ├─ Tools: SQL, Calculator, Citation lookup    │ │
│  │  └─ Answer synthesizer with citations          │ │
│  └────────────────────────────────────────────────┘ │
│  ┌────────────────────────────────────────────────┐ │
│  │  Ingestion Pipeline                            │ │
│  │  PDF → Unstructured.io → chunks + tables       │ │
│  │  → embeddings → Qdrant + DuckDB                │ │
│  └────────────────────────────────────────────────┘ │
│  ┌────────────────────────────────────────────────┐ │
│  │  Eval Harness (Ragas + custom)                 │ │
│  └────────────────────────────────────────────────┘ │
└──────────────────┬──────────────────────────────────┘
                   │
        ┌──────────┼──────────┬──────────┐
        ▼          ▼          ▼          ▼
    Qdrant      DuckDB    Cohere API   Local LLM
   (vectors)   (tables)   (rerank)    (edge mode)
```

---

## 3. Tech Stack

### Frontend
- **Next.js 14** (App Router) — modern React framework, easy Vercel deployment
- **Tailwind CSS + shadcn/ui** — clean component library, no fighting CSS
- **Vercel AI SDK** — streaming chat primitives
- **Layout**: split-pane — chat on the left, retrieved chunks with highlighted citations on the right

### Backend
- **Python 3.11 + FastAPI** — async-first, plays well with ML ecosystem
- **Pydantic** — schema validation for extracted financial data
- **uvicorn** — ASGI server

### Agent Orchestration
- **LangGraph** (not LangChain chains) — state-machine-based, produces a clean diagram for the README
- Node flow: `rewrite_query → route → retrieve → rerank → tool_call (loop) → synthesize`

### Retrieval Stack
- **Qdrant** (self-hosted via Docker, or Qdrant Cloud free tier) — vector storage with native hybrid search
- **rank_bm25** — lexical search component
- **Cohere Rerank v3** — cross-encoder reranker (explicit Cohere integration is a hiring signal)
- **Embeddings**:
  - Primary: `cohere-embed-v3`
  - Edge variant: `BAAI/bge-small-en-v1.5`
- **Fusion**: Reciprocal Rank Fusion (RRF) to combine BM25 + dense results

### Document Ingestion
- **Unstructured.io** (open-source library, run locally — no API cost)
  - Handles SEC 10-Ks well
  - Extracts tables as structured data
  - Preserves document hierarchy

### Structured Data Store
- **DuckDB** — extracted financial tables land here
- Agent uses a `sql_query` tool to compute ratios, trends, cross-year comparisons
- This is the killer differentiator — most student RAG projects skip it

### LLM
- **Primary**: Claude Sonnet 4.5 via Anthropic API
- **Edge variant**: Llama 3.2 3B quantized to Q4_K_M via `llama-cpp-python`
- Feature-flag toggle between the two

### Evaluation
- **Ragas** — faithfulness, answer relevancy, context precision/recall
- **Custom harness** — financial-reasoning accuracy (did it compute the ratio correctly? did it cite the right filing year?)

### Deployment
- **Frontend**: Vercel (free tier)
- **Backend**: Railway or Fly.io (~$5/mo, persistent volume for Qdrant)
- **Qdrant**: Docker on same box as backend, or Qdrant Cloud free tier
- **Edge demo**: recorded video — can't easily deploy a 3B model on free tier

---

## 4. Data Strategy

**Source**: SEC EDGAR (free JSON API at `data.sec.gov`)

**Initial corpus**: 6–10 10-K filings across 3 companies, 3 years each
- Suggested companies: Apple (tech), Tesla (auto/energy), JPMorgan (financials)
- Different sectors keep the retrieval challenge realistic
- Three years enables temporal/trend questions

**Why this corpus**:
- Public, no licensing concerns
- Rich mix of narrative + tables
- Forces real retrieval challenges (long docs, repeated section names across years/companies)
- Enables cross-document reasoning questions

---

## 5. Build Plan (Day-by-Day)

### Day 1 — Foundation
- [ ] Monorepo setup: `/frontend` (Next.js), `/backend` (FastAPI), `/infra` (docker-compose)
- [ ] `docker-compose.yml` with Qdrant + backend
- [ ] EDGAR scraper: download 6–10 10-K filings to `/data/raw`
- [ ] Ingestion v1: Unstructured → chunks → Cohere embeddings → Qdrant
- [ ] Basic `/query` endpoint: vector search, return top-k chunks
- [ ] Verify end-to-end: curl a question, get relevant chunks back

### Day 2 — Retrieval Quality + Frontend
- [ ] Add BM25 index alongside Qdrant
- [ ] Hybrid fusion via Reciprocal Rank Fusion
- [ ] Integrate Cohere Rerank v3 as final stage
- [ ] Extract tables during ingestion → write to DuckDB with schema metadata
- [ ] Next.js chat UI with streaming via Vercel AI SDK
- [ ] Split-pane layout: chat + citation viewer with highlighted source chunks

### Day 3 — Agent Layer
- [ ] LangGraph state machine with nodes:
  - `rewrite_query` — expand abbreviations, add temporal context
  - `route` — decide: vector retrieval, SQL, or both
  - `retrieve` — hybrid + rerank
  - `tool_call` — looped; SQL, calculator, citation lookup
  - `synthesize` — final answer with inline citations
- [ ] Tools:
  - `sql_query(question: str)` — LLM writes SQL against DuckDB, executes, returns results
  - `calculator(expression: str)` — safe eval for financial math
  - `lookup_citation(chunk_id: str)` — fetch full context for a chunk
- [ ] Stream agent steps to frontend (visible "agent thinking" trace)

### Day 4 — Evals + Polish
- [ ] Hand-write 30–50 eval questions across difficulty tiers:
  - **Tier 1 (factual)**: "What was Apple's revenue in FY2023?"
  - **Tier 2 (multi-doc)**: "How did Tesla's R&D spend change from 2021 to 2023?"
  - **Tier 3 (computational)**: "Compute JPMorgan's debt-to-equity ratio for 2023."
  - **Tier 4 (cross-company)**: "Which of Apple, Tesla, and JPMorgan had the highest operating margin in 2023?"
- [ ] Run Ragas → record faithfulness, answer relevancy, context precision/recall
- [ ] Custom harness → record financial-reasoning accuracy
- [ ] Eval dashboard page in frontend (charts of metrics by tier)
- [ ] Edge variant: swap in `llama-cpp-python` behind a feature flag
- [ ] Benchmark latency + accuracy: Claude Sonnet 4.5 vs Llama 3.2 3B Q4_K_M

### Day 5 — Ship
- [ ] Deploy frontend to Vercel, backend to Railway/Fly.io
- [ ] README with:
  - Architecture diagram (export from a tool, embed as PNG)
  - Benchmark table (cloud vs edge: latency, accuracy, cost per query)
  - Eval results table (Ragas metrics by tier)
  - Demo GIF (chat answering a Tier 3 question with citations + SQL tool call visible)
- [ ] 90-second Loom walkthrough — link from resume and GitHub README
- [ ] LinkedIn post announcing the project with link

---

## 6. Repository Structure

```
finrag/
├── README.md
├── PLAN.md
├── docker-compose.yml
├── .env.example
├── frontend/
│   ├── app/
│   │   ├── page.tsx                # Chat interface
│   │   ├── evals/page.tsx          # Eval dashboard
│   │   └── api/chat/route.ts       # Streams from backend
│   ├── components/
│   │   ├── ChatPane.tsx
│   │   ├── CitationViewer.tsx
│   │   └── AgentTrace.tsx
│   └── package.json
├── backend/
│   ├── app/
│   │   ├── main.py                 # FastAPI entrypoint
│   │   ├── agent/
│   │   │   ├── graph.py            # LangGraph definition
│   │   │   ├── nodes.py            # Individual node implementations
│   │   │   └── tools.py            # SQL, calculator, citation lookup
│   │   ├── ingestion/
│   │   │   ├── edgar.py            # SEC EDGAR scraper
│   │   │   ├── parse.py            # Unstructured.io wrapper
│   │   │   ├── tables.py           # Table extraction → DuckDB
│   │   │   └── embed.py            # Cohere embeddings → Qdrant
│   │   ├── retrieval/
│   │   │   ├── hybrid.py           # BM25 + dense + RRF
│   │   │   └── rerank.py           # Cohere Rerank wrapper
│   │   ├── llm/
│   │   │   ├── claude.py           # Anthropic client
│   │   │   └── local.py            # llama-cpp-python client
│   │   └── eval/
│   │       ├── ragas_runner.py
│   │       ├── custom_metrics.py
│   │       └── questions.json      # Eval set
│   ├── pyproject.toml
│   └── Dockerfile
├── data/
│   ├── raw/                        # Downloaded 10-Ks
│   ├── processed/                  # Chunked + embedded
│   └── duckdb/                     # Extracted tables
└── infra/
    └── qdrant/                     # Qdrant volume
```

---

## 7. Environment Variables (.env.example)

```
ANTHROPIC_API_KEY=
COHERE_API_KEY=
QDRANT_URL=http://localhost:6333
QDRANT_API_KEY=
DUCKDB_PATH=./data/duckdb/finrag.duckdb
LLM_MODE=cloud                     # cloud | edge
LOCAL_MODEL_PATH=./models/llama-3.2-3b-q4.gguf
```

---

## 8. What Makes This Resume-Grade vs Student-Grade

Three things, all of which must be visible on the README's first screen:

1. **Eval harness with real numbers** — most student projects skip this. Recruiters at ML-mature companies scan for it specifically.
2. **SQL-over-extracted-tables tool** — almost nobody does this in portfolio RAG projects, and it's exactly what enterprise AI looks like in practice.
3. **Edge inference variant with quantization benchmark table** — directly addresses webAI's hiring focus, and demonstrates that you understand the cost/latency/accuracy tradeoff space.

---

## 9. Stretch Goals (Post-Day-5)

- Multi-turn conversation memory with session-scoped chat history
- Document upload UI: user drops in any 10-K, ingests on the fly
- Comparative analysis mode: side-by-side answers from two companies
- Confidence calibration: show the agent's confidence in each claim
- Fine-tune the edge model on financial-domain Q&A pairs (bonus webAI signal)

---

## 10. Concepts to Learn While Building

For each, prefer learning from primary sources (papers, official docs) over tutorials:

- **Reciprocal Rank Fusion** — the original paper is short and readable
- **Cross-encoder rerankers** — read the Cohere Rerank docs and the BGE reranker paper
- **LangGraph state machines** — official LangGraph tutorial covers it well
- **Quantization formats** — read llama.cpp's GGUF quantization explainer for Q4_K_M
- **RAG evaluation** — the Ragas paper and the RAGAS docs
- **Agentic patterns** — Anthropic's "Building effective agents" blog post
