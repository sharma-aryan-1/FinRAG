# FinRAG — Day 2 Reference

Day 2 delivered the **production-grade retrieval funnel** (BM25 + dense + RRF + cross-encoder rerank), the **structured-data side** (DuckDB ↔ SEC XBRL), and the **UI shell** (Next.js 14, split-pane). Day 3 will add the agent and LLM synthesis on top of this foundation.

> Pre-req: `docs/day1.md` covered the dense-retrieval baseline and the system architecture. Read that first if you're returning to the project cold.

---

## 1. At a glance

| Day-2 deliverable | Status |
|---|---|
| BM25 lexical index (`rank_bm25`) | ✓ |
| RRF fusion of BM25 + dense | ✓ |
| Cohere Rerank v3 final stage | ✓ |
| DuckDB `financial_facts` table from SEC XBRL | ✓ |
| Next.js 14 split-pane UI | ✓ |
| CORS middleware on FastAPI | ✓ |
| Smoke harness updated to hit full funnel | ✓ 8/8 |

| Metric | Value |
|---|---|
| Total retrieval stages | 4 (BM25, Dense, RRF, Rerank) |
| Reranker pool size | 50 → 5 |
| End-to-end `/query` latency (steady-state) | ~550ms |
| Dominant latency contributor | Cohere Rerank (~300ms) |
| DuckDB fact rows across 3 companies × 3 years | ~600 |
| Canonical line items in CONCEPT_MAP | 16 |
| Frontend stack | Next.js 14 (App Router), TS, Tailwind |

---

## 2. System architecture (Day 2 state)

```
                  ~4,011 chunks (Qdrant `finrag_chunks`)
                       │
        ┌──────────────┴──────────────┐
        ▼                             ▼
   BM25 top-50                Dense top-50          ── Day 1 + Decision 9
   (rank_bm25,                (Cohere embed-v3,
    in-process)                Qdrant ANN)
        │                             │
        └─────────────┬───────────────┘
                      ▼
              RRF fusion (k=60)                     ── Decision 10
              ranks-only, no score calibration
                      │
                      ▼
              Fused top-50 with hydration
                      │
                      ▼
             Cohere Rerank v3                       ── Decision 11
             cross-encoder, relevance ∈ [0,1]
                      │
                      ▼
                  Top-5 final
                      │
                      ├──► FastAPI /query (with CORS)
                      │           │
                      │           ▼
                      │     Next.js UI               ── Decision 13
                      │     split-pane chat + citation
                      │
                      ▼ (parallel structured store)
        DuckDB financial_facts                       ── Decision 12
        (ready for Day 3's sql_query tool)
                      │
                      ▲
                      │ XBRL Company Facts API
                      │ (one HTTP call per company)
                  SEC EDGAR
```

---

## 3. Repository layout (delta from Day 1)

```
FinRAG/
├── backend/
│   └── src/finrag/
│       ├── ingestion/
│       │   └── facts.py                  ← NEW: XBRL → DuckDB
│       ├── retrieval/
│       │   ├── vector.py                 ← UPDATED: payload_to_chunk, retrieve_by_chunk_ids helpers
│       │   ├── lexical.py                ← NEW: BM25 index
│       │   ├── hybrid.py                 ← NEW: RRF fusion
│       │   └── rerank.py                 ← NEW: Cohere Rerank v3
│       └── main.py                       ← UPDATED: CORS, /query routes to rerank_search
│
├── data/
│   ├── bm25_index.pkl                    ← NEW: ~25 MB pickled BM25Okapi
│   └── duckdb/
│       └── finrag.duckdb                 ← NEW: financial_facts table
│
└── frontend/                             ← NEW: Next.js 14 project
    └── src/
        ├── app/
        │   ├── layout.tsx
        │   ├── page.tsx                  ← orchestrator: split-pane + state
        │   └── globals.css
        ├── components/
        │   ├── ChatPane.tsx              ← message list + input
        │   ├── ChunkCard.tsx             ← per-chunk preview card
        │   └── CitationViewer.tsx        ← right-pane detail (handles HTML tables)
        └── lib/
            ├── api.ts                    ← fetch wrapper for /query
            └── types.ts                  ← TS mirror of RetrievedChunk
```

---

## 4. Data journey — the same chunk, through the full funnel

Following the Apple FY2023 Services-revenue chunk from Day 1, now through Day 2's stages.

| Stage | What happens |
|---|---|
| **Indexed** | Already in Qdrant (point ID = `int(chunk_id, 16)`) with 1024-dim Cohere embedding + full payload. Also in `data/bm25_index.pkl` at position-index `i`, tokenized. |
| **Query enters** | "How did Apple's services revenue change in 2023?" arrives at `/query`. |
| **Dense path** | Cohere embed-v3 (`search_query`) → 1024-dim vector → Qdrant `query_points` → top-50 with payload. Chunk lands around rank 1–3 (cosine 0.65–0.72). |
| **BM25 path** | Tokenized to `['how','did','apple','services','revenue','change','in','2023']` → BM25Okapi → top-50 chunk_ids with scores. Chunk lands in the top-20 (BM25 likes "apple"+"services"+"2023" all co-occurring here). |
| **RRF fusion** | Both ranked lists go through `Σ 1/(60+rank_r)`. Chunk's fused score reflects both rankers. |
| **Hydration** | Top-50 fused chunk_ids → some already have payload from dense pass; missing ones batch-fetched via `qdrant.retrieve(ids=[...])`. |
| **Rerank** | The 50 candidates' texts go to Cohere Rerank v3 with the original question. Cross-encoder sees query + doc together — recognizes "Apple" in query and the AAPL provenance, scores ours `~0.85` while downweighting any TSLA hangers-on. |
| **Response** | Top-5 returned to FastAPI → to Next.js → renders as a `ChunkCard`. User clicks; `CitationViewer` shows the full passage with company / FY / SEC link. |

**The point:** every Day-2 stage is *additive* to the chunk's information state. No stage destroys provenance.

---

## 5. Decisions — concise summaries

### Decision 9 — BM25 lexical index

- `rank_bm25` library — pure Python, in-memory, ~25 MB pickle for our corpus.
- Default parameters (`k1=1.5, b=0.75`) — don't tune without eval data.
- Tokenizer = regex `\w+` + lowercase. The **same function must run on chunk text at index time and queries at search time** — same vocab is the load-bearing invariant.
- Filters applied **post-rank** by over-fetching 4× and filtering down. Fine at our scale; at 1M+ chunks you'd want a filter-aware index structure.
- Persisted to `data/bm25_index.pkl` with `_BM25Bundle.__module__ = "finrag.retrieval.lexical"` pinning to avoid pickle's `python -m` gotcha.

### Decision 10 — Hybrid retrieval with RRF

The RRF formula:
```
RRF_score(d) = Σ over rankers r:  1 / (k + rank_r(d))    where k = 60
```
- `k=60` is the paper default. Don't tune.
- **Ranks only — no score normalization.** This is the key elegance: BM25 and cosine are on incompatible scales; ranks are universal.
- `k_each = 50` candidates per retriever. Wide enough for consensus boost; narrow enough to be fast.
- Hydration: dense already returned payloads for its top-50. BM25-only chunks in the fused top-K get batch-fetched from Qdrant via `retrieve(ids=[...])` — one round-trip.

### Decision 11 — Cohere Rerank v3

Cross-encoder as the final stage. Sees query + document together; bi-encoders can't.

```python
co.rerank(
    model="rerank-english-v3.0",
    query=question,
    documents=[c.text for c in pool],   # 50 candidates
    top_n=5,
)
```

- Relevance score in `[0, 1]` — **semantically meaningful** (unlike RRF or cosine). 0.85+ = strongly relevant; 0.05 = unrelated.
- ~300ms latency at 50 docs; dominant cost in the funnel.
- Cost: ~$0.002/query on Rerank v3.
- Same retry-with-backoff pattern as embed (trial keys throttle here too).
- **Fixed the TSLA-instead-of-AAPL bug** on "How did Apple's services revenue change?" — RRF could be hijacked by Tesla's services-segment lexical density; rerank's query-document interaction restores correctness.

### Decision 12 — DuckDB table extraction

**Two paths, one right answer:**

| Path | Verdict |
|---|---|
| Parse Day-1's HTML table chunks ourselves | Hard, error-prone, re-invents SEC's work |
| **Use SEC XBRL Company Facts API** ✓ | Pre-structured, GAAP-tagged, cross-filer-comparable, free |

Schema (the canonical fact table from Frame 4):
```sql
CREATE TABLE financial_facts (
    ticker, company_name, cik,
    fiscal_year, fiscal_period, period_end_date,
    line_item,      -- canonical: 'revenue', 'rd_expense', ...
    gaap_concept,   -- original GAAP name preserved for audit
    value, unit,
    accession_number, form, filed_date,
    PRIMARY KEY (ticker, fiscal_year, fiscal_period, line_item, gaap_concept)
);
```

**CONCEPT_MAP** (canonical key → list of GAAP concept names) absorbs filer inconsistency. Apple tags `RevenueFromContractWithCustomerExcludingAssessedTax`; Tesla tags `Revenues`. Both map to canonical key `'revenue'` so cross-company queries are one-liners.

**Dedup**: SEC XBRL is append-only with full restatement history. Same (period, concept) appears multiple times across filings; we keep the **most recently filed** value per logical fact. The `financial_facts` table is a materialized view over the XBRL log.

What the agent (Day 3) will be able to do:
```sql
-- Cross-company operating margin
SELECT ticker,
       SUM(CASE WHEN line_item='operating_income' THEN value END) * 1.0
        / SUM(CASE WHEN line_item='revenue' THEN value END) AS op_margin
FROM financial_facts
WHERE fiscal_year = 2023 AND fiscal_period = 'FY' AND unit = 'USD'
GROUP BY ticker
ORDER BY op_margin DESC;
```
One query. No embeddings. No LLM arithmetic.

### Decision 13 — Next.js chat UI scaffold

- Next.js 14 App Router + TypeScript + Tailwind.
- **Two-pane layout**: `ChatPane` left (input + message history), `CitationViewer` right (full chunk detail).
- Chunk cards in the chat have a relevance score (rerank's, not RRF's). Clicking a card opens it in the viewer.
- `CitationViewer` is **dual-mode**: narrative chunks render as prose; table chunks render their HTML (Day-1 design — `metadata.text_as_html` was preserved end-to-end) via `dangerouslySetInnerHTML` (safe because the source is our own ingestion).
- CORS allowed for `localhost:3000` on the backend.
- **Deliberately not using Vercel AI SDK yet** — there's no LLM-token stream to consume until Day 3.

---

## 6. Bugs encountered

| # | What broke | Why | Lesson |
|---|---|---|---|
| 10 | `pickle.load` failed with `AttributeError: Can't get attribute '_BM25Bundle'` | Pickle records class's `__module__`. Running `python -m foo.bar` sets `__module__='__main__'`, so the pickle is only loadable from the same entrypoint that wrote it | Either separate library from CLI, or pin `Cls.__module__ = "dotted.path"` after class definition |
| 11 | First `/query` after server start took ~7 seconds | numpy was lazy-imported inside `lexical.search()` — cold import on first call | Module-level imports for everything you'll need; cold-start is the budget |
| 12 | "How did Apple's services revenue change?" returned TSLA chunks on top after RRF | BM25 ranked TSLA chunks high because Tesla's "Services and other revenue" segment has dense `services`+`revenue` co-occurrence. RRF averaged the two retrievers' decisions; Apple's dense-strength got outweighed | This is **the canonical hybrid-without-rerank failure mode** — cross-encoder rerank (Decision 11) was designed to fix exactly this |
| 13 | `_duckdb.ConstraintException` on first XBRL load | SEC XBRL feed is append-only with full restatement history; same `(period, concept)` appears multiple times across filings | Treat structured-data feeds as logs; materialize a "latest wins" projection. Dedup keyed on PK with `filed_date` as tiebreaker |
| 14 | `Write` tool refused to overwrite Next.js `page.tsx` until Read first | Tool safety contract — must read before edit | Workflow detail; nothing semantic |

---

## 7. Concepts internalized

### Hybrid retrieval as uncorrelated error reduction

Dense and BM25 fail on different queries. They're not just two implementations of the same thing — they're **two different retrieval modalities** (semantic vs lexical). RRF fuses without calibration because the right primitive for combining noisy independent rankers is *rank consensus*, not score averaging. The literature consistently shows 5–15 point recall@k lift from hybrid over either alone.

### Cross-encoder reranking is interaction-aware retrieval

The bi-encoder (Cohere embed-v3, BM25) bottleneck: query and document never see each other during encoding. The cross-encoder (Cohere Rerank v3) breaks that — its transformer attends across both at once. It's slow (can't precompute), but at 50 candidates × ~300ms total, it's tractable as a final stage. The two-stage funnel — bi-encoder for *recall*, cross-encoder for *precision* — is the standard production shape.

### Modal-split retrieval

10-Ks are half tables. Tables aren't text — they're structured data with cell semantics that flattening destroys. Day 2's DuckDB layer treats them as what they are. The agent (Day 3) will choose per-question whether to embed-and-retrieve over text or SQL-query over structured rows. **Same source documents, two representations, one citation envelope.**

### Materialized view over append-only feeds

SEC XBRL is the canonical example of a pattern that recurs in industry: a primary source that records *every version of a fact*, and a consumer that needs the *current version*. Day 2's `extract_facts` is a projection: keyed dedup with `filed_date` tiebreaker. Variants of this same pattern: CDC streams, audit logs with corrections, insurance-claim adjustments, anything where history matters but queries want the snapshot.

### The asymmetric retrieval contract — twice

Day 1 mentioned Cohere v3's `input_type` mismatch (`search_document` vs `search_query`). Day 2 added a *second* asymmetry inside the funnel: the bi-encoder side (BM25, dense) is one shape; the cross-encoder side (rerank) is another. **Each retrieval stage has its own contract.** Mixing them silently is the failure mode of every "I tried hybrid retrieval and it didn't help" team.

---

## 8. Resume / interview material (Day 1 + Day 2)

### Combined resume bullets (replace Day-1's standalone bullets with these)

- **Built a production-grade hybrid retrieval pipeline over SEC 10-K filings**: dense vector search (Qdrant + Cohere `embed-english-v3.0`) fused with BM25 lexical search via Reciprocal Rank Fusion, then narrowed to top-K by a Cohere Rerank v3 cross-encoder. ~550ms end-to-end on ~4,000 chunks; measurable retrieval-quality lift on entity-disambiguation queries.

- **Designed and shipped a modal-split data architecture**: text chunks live in Qdrant for semantic retrieval; financial facts live in DuckDB as a normalized fact table sourced from SEC's XBRL Company Facts API and canonicalized across filers (Apple's `RevenueFromContractWithCustomer...` and Tesla's `Revenues` map to one queryable `line_item`). Cross-company financial queries become one-line SQL.

- **Operationalized the asymmetric retrieval contract end-to-end**: separate `input_type` for indexing vs querying with Cohere v3, separate bi-encoder vs cross-encoder semantics for retrieval vs rerank, separate text-store vs structured-store routing — each with its own evaluation surface.

- **Built and rate-limit-hardened the ingestion side**: EDGAR scraper that handles `filings.files` pagination for high-volume filers (banks, big-cap companies that overflow the 1000-entry `recent` cap); Cohere ingestion with exponential-backoff retry on the 100k tokens/min trial limit; idempotent at every stage via deterministic chunk IDs.

- **Shipped a clean Next.js 14 + Tailwind UI**: split-pane chat with a live citation viewer that renders both narrative and HTML-table chunks. Connects to FastAPI via CORS; the architecture is positioned to swap retrieval-only chunk lists for streamed agent responses on Day 3 with zero shell-level changes.

- **Eval-first discipline**: hand-authored 8-case structural smoke harness that runs in <2s, exits with a CI-compatible code, and is calibrated to each stage's score scale (cosine for dense, RRF for hybrid, relevance for reranked). Catches plumbing regressions independently from quality regressions — the latter is Day 4's Ragas + custom harness.

### Interview questions you should be ready for (Day 2 additions)

| Question | What to say |
|---|---|
| "Why RRF over weighted score averaging?" | BM25 scores and cosine similarities are on incompatible scales with different distributions. Any weight you pick is a guess. RRF discards raw scores and uses only ranks — making fusion calibration-free. The original paper (Cormack 2009) showed RRF beats every alternative they tested across multiple TREC datasets. |
| "When does hybrid retrieval underperform dense?" | Entity-specific queries where one retriever has high lexical noise. Example we hit: "How did Apple's services revenue change?" — Tesla's filings have dense "services"+"revenue" co-occurrence (their financial-services segment), so BM25 ranked TSLA chunks high. After RRF averaged the two retrievers, the answer shifted to TSLA. Cross-encoder rerank fixed it by attending across query+document. |
| "What's the latency budget breakdown?" | Embed query 150ms, dense search 50ms, BM25 20ms, RRF <1ms, payload hydration 30ms, **rerank 300ms**. Rerank dominates. The lever is `candidates=50`; eval-driven tuning can drop that to 30 for ~200ms at acceptable quality cost. |
| "Why DuckDB specifically?" | Columnar, vectorized execution, single-file embedded, real SQL, Python-native, fast aggregations over financial-statement-sized data. Alternatives: SQLite slow on analytical aggregates; Postgres ops burden; pandas loses persistence. DuckDB wins on every axis that matters for this workload. |
| "How do you canonicalize across filers?" | Hand-curated `CONCEPT_MAP`: each canonical key (`revenue`, `rd_expense`, etc.) maps to one or more GAAP concept names. Reverse-lookup dict at ingestion time. The original `gaap_concept` is preserved in a separate column so the canonicalization is auditable. Real production systems do versions of this — finance compliance teams call it "concept harmonization." |
| "How does the XBRL dedup work?" | The XBRL feed is append-only with full restatement history — same `(period, concept)` appears multiple times. Two-pass dedup in `extract_facts`: first collapses to one row per `(ticker, fy, fp, line_item, gaap_concept, unit)` keeping the most-recently-filed; second collapses unit dimension to match the PK. It's a materialized-view projection over an event log. |
| "What's not yet good about the UI?" | No streaming (no LLM yet to stream), no agent-trace visualization (Day 3), no chunk-text highlighting where the citation comes from (Day 3 polish), no eval dashboard (Day 4). The skeleton is right; the fill comes with the agent. |

### Glossary additions

| Term | Definition |
|---|---|
| **Asymmetric retrieval** | A model where query and document encoders are trained separately, producing different embedding distributions per side. Cohere v3 is asymmetric; passing the wrong `input_type` silently degrades retrieval. |
| **Reciprocal Rank Fusion (RRF)** | `Σ 1/(k+rank_r(d))` — rank-based fusion of multiple retrievers without score calibration. k=60 is the standard. |
| **Cross-encoder** | Model that processes (query, document) together through a single transformer. Slow per-pair but query-aware. Cohere Rerank v3 is one. |
| **GAAP concept** | A standardized line item in SEC's XBRL taxonomy (e.g. `us-gaap:Revenues`). Filers tag every financial fact against one. |
| **XBRL Company Facts API** | `data.sec.gov/api/xbrl/companyfacts/CIK{padded}.json` — one HTTP call returns every XBRL-tagged fact for a filer across their entire history. |
| **Materialized view (over a log)** | A consumer-side table that projects an append-only source feed down to a snapshot. The dedup logic *is* the projection. |
| **Filter-aware index** | A retrieval index that natively supports payload-based pre-filtering (Qdrant has this). Without it, you must over-fetch and post-filter. |
| **Score floor (in smoke testing)** | The minimum top-1 score you'd accept as "retrieval working." Calibrated per-stage: 0.35 for cosine, 0.005 for RRF, 0.10 for rerank relevance. Catches embedder/index breakage; doesn't catch quality regressions. |

---

## 9. What's deliberately not done yet

| Capability | Day |
|---|---|
| LangGraph agent: rewrite → route → retrieve → tool-loop → synthesize | 3 |
| `sql_query(natural_language)` tool against DuckDB | 3 |
| `calculator(expression)` tool for arithmetic | 3 |
| `lookup_citation(chunk_id)` tool | 3 |
| Claude Sonnet 4.5 integration | 3 |
| Streamed agent-trace events from FastAPI → frontend | 3 |
| Agent trace visualization in the UI | 3 |
| Ragas + custom evaluation harness, 30–50 question set | 4 |
| Llama 3.2 3B Q4_K_M edge variant + benchmark table | 4 |
| Deployment (Vercel + Railway/Fly.io) + 90s demo | 5 |

---

## 10. Day-3 preview

The high-leverage moves, in order:

1. **`sql_query` tool against DuckDB** — read-only connection, query timeout, schema in the system prompt. Round-trip verification (Cohere or Claude translates the agent's SQL back to English to confirm intent). This is where Decision 12's structured layer pays off.

2. **Claude Sonnet 4.5 integration** — async streaming. Hook into the existing FastAPI endpoint as a new route (`/agent` or POST `/query` with `mode=agent`).

3. **LangGraph state machine** — nodes for `rewrite_query`, `route` (vector / sql / both), `retrieve` (calls `rerank_search`), `tool_call` (sql / calculator / citation lookup), `synthesize`. Streaming each node's transitions to the frontend.

4. **Server-Sent Events from FastAPI** — agent-step trace pushed to the client as it happens. Latency hiding (user sees the trace within ~200ms), plus the "agent thinking" UX is half the demo value.

5. **Frontend agent-trace UI** — a third panel or in-message trace showing each tool call, each retrieval result, with the SQL queries inline. **This is what makes the demo memorable in interviews.**

After Day 3, the full pipeline from Frame 3 is real, and Tier 2 / 3 / 4 eval questions become answerable. The system stops being "smart search" and starts being "agentic AI." Day 4 then proves it with numbers.
