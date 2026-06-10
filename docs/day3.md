# FinRAG — Day 3 Reference

Day 3 turned "smart search" into an **agent**. On top of Day 2's retrieval funnel and DuckDB facts, Day 3 added: LLM **synthesis** (`/answer`), three **tools** (sql / calculator / citation), a **LangGraph agent** (`/agent`) that plans, routes, retrieves, and calls tools, **SSE streaming** of the agent's reasoning (`/agent/stream`), and a **live agent-trace UI**. It also forced a hard infrastructure lesson — a free-tier provider that can't survive an agent's call volume — and surfaced a two-year data-labeling bug that retrieval-only testing had hidden.

> Pre-req: `docs/day1.md` (dense retrieval) and `docs/day2.md` (full funnel + DuckDB + UI shell). Read those first if returning cold.

---

## 1. At a glance

| Day-3 deliverable | Status |
|---|---|
| Provider seam (`llm/`) — swappable Anthropic / Gemini | ✓ |
| `/answer` — grounded synthesis over retrieved chunks (Decision 14) | ✓ |
| Three tools: `sql_query`, `calculator`, `lookup_citation` (Decision 15) | ✓ |
| LangGraph agent: plan → (retrieve) → tool-loop → synthesize (Decision 16) | ✓ verified on Claude |
| `/agent/stream` — Server-Sent Events: trace + token streaming (Decision 17) | ✓ |
| Frontend live agent-trace UI (Decision 18) | ✓ |
| Data fix: `financial_facts` 2-year mislabel | ✓ rebuilt 500 → 131 rows |

| Metric | Value |
|---|---|
| Final agent/LLM provider | Anthropic **Claude Sonnet 4.6** (`claude-sonnet-4-6`) |
| Alternate provider (wired, A/B-able) | Google **Gemini 2.5 Flash-Lite** |
| LLM calls per agent question | ~3–5 (plan + tool-loop turns + any sub-LLM SQL) |
| Cost per agent question (Claude) | ~$0.04 ($3 / $15 per M tok in/out) |
| Agent tools | 3 (sql_query, calculator, lookup_citation) |
| Graph nodes | 3 (plan, retrieve, agent) + 1 conditional edge |
| `financial_facts` rows after fix | 131 (annual `FY` only) |
| Verified canaries | AAPL FY23 services +$7.1B/+9% (~$85.2B); net income $96.995B |

---

## 2. System architecture (Day 3 state)

```
                         user question
                              │
                              ▼
                      FastAPI  /agent  ·  /agent/stream (SSE)
                              │
                ┌─────────────┴──────────────────────────┐
                ▼                   LangGraph StateGraph   │
         ┌──────────────┐                                  │
         │  plan node   │  one LLM call: rewrite + route   │
         └──────┬───────┘  → vector | sql | both           │
                │ conditional edge (_after_route)          │
        route∈{vector,both}        route=sql               │
                ▼                      │                    │
         ┌──────────────┐             │                    │
         │ retrieve node│  Day-2 funnel (rerank_search)    │
         │  top-8 chunks│             │                    │
         └──────┬───────┘             │                    │
                └──────────┬──────────┘                    │
                           ▼                               │
                   ┌───────────────┐                       │
                   │  agent node   │  native tool-loop     │
                   │  (tool_use)   │  ── calls tools ──┐    │
                   └──────┬────────┘                   │    │
                          │ final answer        ┌──────▼─────────┐
                          ▼                     │  tools/        │
                         END                    │  sql_query     │→ DuckDB
                                                │  calculator    │  (SELECT/WITH
        provider seam (llm/):                   │  lookup_citation│   guard)
        synthesize · generate_text ·            └────────────────┘→ Qdrant
        run_tool_loop · run_tool_loop_stream
        dispatch by settings.llm_provider
        ┌──────────────┐   ┌──────────────┐
        │  claude.py   │   │  gemini.py   │   ← both implement the same
        │  tool_use    │   │ func-calling │     neutral ToolLoopResult
        └──────────────┘   └──────────────┘

   streaming path: graph.stream(stream_mode=["updates","custom"])
     updates → trace milestones (rewrite/route/retrieve)
     custom  → live token deltas + tool_call events (LangGraph stream writer)
                              │ SSE frames
                              ▼
              Next.js: streamAgent() → AgentTrace + AgentAnswer (react-markdown)
```

---

## 3. Repository layout (delta from Day 2)

```
backend/src/finrag/
├── config.py                    ← UPDATED: llm_provider (default "anthropic"),
│                                   anthropic/gemini keys both optional
├── main.py                      ← UPDATED: /answer, /agent, /agent/stream (SSE)
├── llm/                         ← NEW: provider seam
│   ├── __init__.py              dispatchers: synthesize / generate_text /
│   │                             run_tool_loop / run_tool_loop_stream
│   ├── base.py                  SYSTEM_PROMPT, SynthesisResult, ToolCall,
│   │                             ToolLoopResult, json_safe, format helpers
│   ├── claude.py                Sonnet 4.6; retry wrapper; synthesize; tool_loop;
│   │                             tool_loop_stream (messages.stream); prompt caching
│   └── gemini.py                Flash-Lite; function-calling loop; schema mappers
├── tools/                       ← NEW
│   ├── __init__.py              ToolSpec registry + dispatch() (provider-neutral)
│   ├── calculator.py            AST safe-eval (no eval(); blocks injection/exp-bombs)
│   ├── citation.py              lookup_citation → Qdrant re-fetch by chunk_id
│   └── sql.py                   sql_query: NL→SQL sub-LLM over DuckDB; read-only
│                                 guard; emits NO_QUERY for non-top-level metrics
├── agent/                       ← NEW
│   ├── state.py                 AgentState TypedDict; `trace` additive reducer
│   ├── nodes.py                 plan / retrieve / agent (streaming tool-loop)
│   ├── graph.py                 StateGraph wiring; run_agent / get_agent
│   └── __init__.py
└── ingestion/
    └── facts.py                 ← UPDATED: key period on XBRL `end` date (bug fix)

frontend/src/
├── app/page.tsx                 ← UPDATED: drives streamAgent() instead of query()
├── components/
│   ├── ChatPane.tsx             ← UPDATED: renders trace + streaming answer + sources
│   ├── AgentTrace.tsx           ← NEW: live step list (rewrite/route/retrieve/tool)
│   └── AgentAnswer.tsx          ← NEW: react-markdown + remark-gfm answer renderer
└── lib/
    ├── api.ts                   ← UPDATED: streamAgent() SSE client
    └── types.ts                 ← UPDATED: TraceEvent union, AgentDone, ChatMessage
```

New frontend deps: `react-markdown`, `remark-gfm`.

---

## 4. Data journey — two questions, two routes

**Q1 (segment / narrative): "How did Apple's services revenue change in fiscal 2023, and by what percent?"**

| Stage | What happens |
|---|---|
| **plan** | One LLM call rewrites to a self-contained query and routes. Services revenue is *segment-level* → not in `financial_facts` → route = **vector**. |
| **retrieve** | `rerank_search(top_k=8)` — the full Day-2 funnel returns the Services-revenue chunks. |
| **agent (tool-loop)** | Model still *tries* `sql_query` ("Apple services revenue 2022/2023") — `sql_query` returns **NO_QUERY** (segment metric, not top-level). Model falls back to the narrative chunks and reads the figure. |
| **answer** | "+$7.1 billion, +9%" with `[N]` citations; ~$85.2B implied. **Not** $383.285B (total net sales — the pre-fix mis-answer). |

**Q2 (top-level figure): "What was Apple's net income in fiscal 2023?"**

| Stage | What happens |
|---|---|
| **plan** | Top-level metric → route = **sql** → the conditional edge *skips* retrieval. |
| **agent (tool-loop)** | Model calls `sql_query`. NL→SQL sub-LLM emits `SELECT fiscal_year, value FROM financial_facts WHERE ticker='AAPL' AND line_item='net_income' AND fiscal_period='FY' AND fiscal_year=2023`. Guard passes (SELECT-only). Returns one row: `96995000000`. |
| **answer** | "$96,995,000,000 (~$97.0 billion)". Exact, no LLM arithmetic. |

**The point:** the agent picks the *representation* per question — narrative chunks vs structured SQL — under one citation envelope, exactly as Day 2's modal-split design anticipated.

---

## 5. Decisions — concise summaries

### Decision 14 — `/answer` synthesis + the provider seam

Grounded synthesis over retrieved chunks: the model answers using only the context, citing `[N]`. The lasting artifact is the **provider seam** in `llm/`:

- `llm/base.py` holds provider-neutral types (`SynthesisResult`, `ToolCall`, `ToolLoopResult`) and the shared `SYSTEM_PROMPT`.
- `llm/__init__.py` exposes dispatchers (`synthesize`, `generate_text`, `run_tool_loop`, `run_tool_loop_stream`) that pick a backend lazily from `settings.llm_provider`.
- `claude.py` and `gemini.py` each map their native API onto the neutral shapes.

> **Provider churn:** Day 3 started on Anthropic, swapped to **Gemini 2.5 Flash** for its free tier, then (Decision 16) swapped **back to Claude**. The seam is *why* that churn cost hours, not days — callers (`main.py`, the nodes, the tools) never name a provider.

### Decision 15 — three tools (and a data bug)

A provider-neutral `ToolSpec` registry + `dispatch()`; the tool's `parameters` are JSON-schema, which is already Anthropic's `input_schema` shape and trivially mapped to Gemini's.

| Tool | Design |
|---|---|
| `calculator` | **AST safe-eval** — parses the expression and walks the tree; no `eval()`. Blocks attribute access / dunder injection and exponent bombs (`9**9**9`). |
| `lookup_citation` | Re-fetches a chunk's full text from Qdrant by `chunk_id` — lets the model quote exactly. |
| `sql_query` | NL→SQL via a sub-LLM (`generate_text`), executed read-only over DuckDB. Guard: **SELECT/WITH only** + a keyword blocklist. **Honesty fix:** emits a `NO_QUERY` sentinel for non-top-level metrics (segment/product revenue) instead of inventing SQL that returns the wrong number. |

**The bug this surfaced (important):** `financial_facts` had **every annual figure mislabeled by ~2 years**. `extract_facts` keyed period identity on the XBRL `fy`/`fp` — the *filing's* fiscal year — but a single 10-K reports **three** comparative years all sharing that `fy`, so dedup collapsed them and kept the wrong year's value. **Fix:** key on the XBRL `end` date, set `fiscal_year = period_end.year`, keep only annual (`fp='FY'`) facts. Table rebuilt **500 → 131 rows**; verified AAPL FY2023 revenue $383.285B, net income $96.995B. Day-2's smoke harness never caught it because `/query` retrieval never touches DuckDB *values*.

### Decision 16 — the LangGraph agent

`StateGraph`: `START → plan → (conditional) → [retrieve] → agent → END`.

- **`plan`** merges *rewrite* and *route* into **one** LLM call (a request-budget optimization born under Gemini's caps; kept because it's simply leaner). Route ∈ {vector, sql, both}. The route prompt explicitly sends segment-level figures to **vector** — fixing the earlier mis-route that answered "services revenue" with total revenue.
- **conditional edge**: `sql` skips `retrieve` and goes straight to the tool-loop (the agent fetches its own numbers); `vector`/`both` pre-fetch chunks first.
- **`agent`** runs the native tool-loop until the model stops emitting tool calls; that terminal text *is* the synthesis (with native function-calling, "tool-loop" and "synthesize" are one node — we still emit a distinct `synthesize` trace event for the UI). A **reliability floor**: if the loop yields no answer, fall back to plain `/answer` synthesis over the retrieved chunks.
- `trace` uses an **additive reducer** so every node appends its step; that list is the agent's visible reasoning.

**Verified on Claude this session:** services canary (+$7.1B/+9%, ~$85.2B, not $383B), clean `tool_use`, `sql_query` net income $96.995B, both HTTP endpoints, no regression after the streaming refactor.

### THE PIVOT — Gemini → Claude Sonnet 4.6

The headline operational lesson of Day 3.

- **Why we tried Gemini:** free tier, no billing.
- **Why it failed for an agent:** the free tier (no billing attached) is **20 requests/DAY, per model**. An agent makes ~5 LLM calls/question → **~4 questions/day** before a hard 429 wall. We exhausted both `gemini-2.5-flash` and `gemini-2.5-flash-lite`. Flash-Lite also intermittently returned `MALFORMED_FUNCTION_CALL` (botched native function calls).
- **The choice:** pay for **Claude Sonnet 4.6** — reliable `tool_use`, smoother deploy, and a genuine résumé signal (Anthropic tool use). ~$0.04/agent question; $5 ≈ ~125 questions.
- **Gemini stays fully wired** behind the seam — flip `llm_provider=gemini` for a Day-4 eval A/B.

### Decision 17 — SSE streaming (`/agent/stream`)

Stream both the **reasoning** and the **answer**, **without LangChain** (project rule). Since we use the raw Anthropic SDK, LangGraph's built-in message-token streaming can't see our tokens — so:

- **Trace milestones** come from `graph.stream(stream_mode="updates")` (each node's state delta as it finishes).
- **Token deltas + live tool calls** come from a LangGraph **custom stream writer** (`get_stream_writer()`), written from *inside* the agent node. `claude.py:tool_loop_stream` consumes each turn via `messages.stream` and forwards text deltas / dispatched tools through callbacks.
- One `graph.stream(stream_mode=["updates","custom"])` interleaves both in true execution order. The endpoint emits SSE frames: `rewrite`, `route`, `retrieve`, `tool_call`, `token`, then a terminal `done` (full answer + chunks + usage + trace), or `error`.
- The *same* agent node serves `/agent` and `/agent/stream` — the writer is a **no-op** under plain `invoke`, so there's no branching.

### Decision 18 — frontend live agent-trace UI

- `lib/api.ts:streamAgent()` POSTs to `/agent/stream` and **hand-parses** the SSE byte stream (the browser `EventSource` is GET-only, so it can't carry a POST body).
- `AgentTrace.tsx` renders the steps as they arrive: ✎ rewrite, ⇄ route badge (color per route), ▤ retrieve count, ⚙ **collapsible** `tool_call` showing the generated SQL + result.
- `AgentAnswer.tsx` renders the markdown answer (react-markdown + remark-gfm: real tables, headers, `[N]`), with a blinking cursor while streaming. Styled via Tailwind arbitrary-variant selectors (no typography plugin installed).
- `page.tsx` switches the chat from `query()` to `streamAgent()`; `/query` stays for the eval harness. **UX nicety:** when a `tool_call` arrives, the bubble's pre-tool narration is cleared so the real answer streams clean, then `done` snaps to the authoritative answer.

---

## 6. Bugs encountered

| # | What broke | Why | Lesson |
|---|---|---|---|
| 15 | Every annual figure in `financial_facts` off by ~2 years | `extract_facts` keyed period identity on the filing's XBRL `fy`/`fp`; one 10-K reports 3 comparative years sharing that `fy`, so dedup kept the wrong year | Key facts on the **period's own `end` date**, not the filing's fiscal year. And: a retrieval smoke harness won't catch a *structured-value* bug — different surfaces need different tests |
| 16 | Gemini agent died after ~4 questions/day | Free tier (no billing) = 20 req/DAY **per model**; an agent spends ~5 calls/question | Per-day quotas are incompatible with agents. Price the call-volume before betting an architecture on a free tier |
| 17 | Flash-Lite intermittently returned `MALFORMED_FUNCTION_CALL` | Smaller model botches native function-call serialization under load | Tool reliability is a model-capability axis, not a given. It drove the Claude decision |
| 18 | Tool-loop prompt cache: `cache_read` stayed 0 (write landed on the *last* call) | `cache_control` is on the `system` block only; tools carry no breakpoint — reuse isn't landing across turns | **Parked** as a cost-only follow-up (correctness unaffected). Likely fix: a `cache_control` breakpoint on the tools block too |
| 19 | UI rendered unstyled with a dead button | Ran `next build` (production) then `next dev` on the leftover `.next/`; dev served mismatched hashed chunks → CSS/JS 404 → no styles, no hydration (so `onChange` never enabled the button) | Don't start the dev server on top of a production `.next`. `rm -rf .next` then `npm run dev` |
| 20 | Left chat pane wouldn't scroll; Ask bar pushed off-screen | Flex/grid children default to `min-height:auto`, so they grow to content instead of letting the inner `overflow-y-auto` scroll | Add `min-h-0` at every flex/grid ancestor of a scroll container |
| — | (carryover) `uv add` fails when uvicorn holds the `websockets/speedups.pyd` lock | Running server pins the file | Stop the backend first; `UV_LINK_MODE=copy` |
| — | (carryover) Windows console cp1252 can't print `↳ ▸` unicode | Console encoding | Run scripts with `PYTHONIOENCODING=utf-8` |

---

## 7. Concepts internalized

### The provider seam (ports & adapters for LLMs)

Three provider swaps in one day (Anthropic → Gemini → Anthropic) cost hours, not a rewrite, because the *callers never name a provider*. Neutral result types (`ToolLoopResult`, `SynthesisResult`) + lazy dispatchers (`run_tool_loop`, `synthesize`) are the port; `claude.py` / `gemini.py` are interchangeable adapters. The seam is also what makes a Day-4 eval **A/B** (same retrieval, different model) a one-line config flip.

### Native tool_use vs function-calling — same neutral contract

Claude's `tool_use` and Gemini's function-calling are different wire protocols (content blocks + `tool_result` messages vs `functionCall`/`functionResponse` parts). Both collapse to the same `ToolLoopResult` so the agent node is provider-blind. The reliability gap between them (Flash-Lite's `MALFORMED_FUNCTION_CALL`) is real and is a model-selection criterion, not an implementation detail.

### Agentic routing is a cost/latency lever

The `plan` node decides *vector vs sql vs both* before any retrieval, and merges rewrite+route into one call. SQL-only questions skip the entire embed→search→rerank funnel. Routing isn't just correctness (segment vs top-level figures) — it's a spend decision made once, up front.

### Honest tool boundaries

`sql_query` returning `NO_QUERY` for segment-level metrics is the structured-data twin of "say so if the context doesn't answer it." A tool that fabricates a plausible-but-wrong query is worse than one that declines — the pre-fix system answered "services revenue" with *total* revenue precisely because nothing refused.

### Streaming an agent without a streaming framework

LangGraph's **custom stream writer** streams tokens out of a node that calls the raw Anthropic SDK — no LangChain chat-model wrapper required. Combining `stream_mode=["updates","custom"]` yields node milestones *and* intra-node tokens interleaved. The same node stays usable by the non-streaming endpoint because the writer no-ops under `invoke`.

### Different surfaces need different tests

The 2-year fact bug lived through all of Day 2 because the smoke harness exercises *retrieval*, which never reads DuckDB values. A structured-data layer needs its own value-level assertions. "Tests pass" is scoped to what the tests actually touch.

---

## 8. Resume / interview material (Day 3 additions)

### Resume bullets (add to the Day 1+2 set)

- **Built an agentic RAG system with LangGraph**: a plan→route→retrieve→tool-loop→synthesize state machine over Claude Sonnet 4.6 native tool use, that chooses per question between semantic retrieval and structured SQL and grounds every claim in citations.
- **Designed a provider-abstraction seam** that made the synthesis/agent backend swappable between Anthropic and Google Gemini behind neutral result types and lazy dispatchers — swapped providers three times during development with zero caller changes, and kept both wired for evaluation A/B.
- **Implemented three agent tools incl. a guarded NL→SQL tool** over DuckDB (read-only SELECT/WITH guard + keyword blocklist) that declines non-top-level metrics rather than fabricating a wrong query; a sandboxed AST-based calculator (no `eval()`); and a citation re-fetch tool.
- **Streamed the agent's reasoning end-to-end over Server-Sent Events** — node-level trace milestones plus token-by-token answer streaming — using LangGraph's custom stream writer with the raw Anthropic SDK (no LangChain), rendered as a live agent-trace UI in Next.js.
- **Caught and fixed a two-year data-labeling bug** in the financial-facts table (period identity keyed on the filing's fiscal year instead of each fact's XBRL period-end date), rebuilding the table and adding value-level verification — a class of bug retrieval-only testing structurally cannot catch.

### Interview questions you should be ready for (Day 3 additions)

| Question | What to say |
|---|---|
| "Why LangGraph and not a plain while-loop?" | The tool-loop itself *is* a while-loop (native function-calling). LangGraph earns its place at the *orchestration* layer: typed shared state, a conditional route edge, an additive `trace` reducer, and `stream_mode` for free SSE. Not LangChain — just the graph. |
| "How does the agent decide vector vs SQL?" | A `plan` node does rewrite+route in one LLM call. Top-level figures (revenue, net income) → SQL; segment/narrative (services revenue, risk factors) → vector. SQL-only routes skip retrieval entirely. The routing rule encodes a real data fact: segment figures live in narrative, not our XBRL facts table. |
| "Why pay for Claude over free Gemini?" | The Gemini free tier is 20 requests/day per model; an agent burns ~5/question, so ~4 questions/day. Flash-Lite also intermittently malformed function calls. For an agent, tool-call reliability and call volume dominate token price. Gemini stays wired for eval A/B. |
| "How do you stream tokens without LangChain?" | LangGraph's custom stream writer. The agent node consumes each Claude turn via `messages.stream` and pushes deltas to the writer; the endpoint reads `stream_mode=["updates","custom"]` and emits SSE. The same node no-ops the writer under `invoke`, so one node serves both endpoints. |
| "How is the SQL tool safe?" | NL→SQL by a sub-LLM, then a guard: SELECT/WITH only, keyword blocklist, read-only DuckDB connection, row cap. And a `NO_QUERY` sentinel so it declines metrics it can't answer instead of guessing — that honesty is what fixed the "services revenue = total revenue" class of error. |
| "Tell me about a bug testing didn't catch." | Every annual fact was 2 years off — period identity was keyed on the filing's fiscal year, but one 10-K carries three comparative years under that same year, so dedup kept the wrong value. The Day-2 smoke harness tests retrieval, which never reads DuckDB values, so it sailed through. Fixed by keying on each fact's XBRL period-end date; added value-level checks. |

### Glossary additions

| Term | Definition |
|---|---|
| **Provider seam** | A ports-and-adapters boundary where callers depend on neutral types + dispatchers, never a concrete LLM SDK. Enables provider swap and eval A/B without caller changes. |
| **Native tool use / function calling** | Model-native structured tool invocation (Anthropic `tool_use` blocks; Gemini `functionCall` parts), as opposed to prompt-parsed tool calls. |
| **Conditional edge (LangGraph)** | A graph edge whose target is computed from state at runtime — here, route ∈ {vector, sql} decides whether to visit `retrieve`. |
| **Custom stream writer** | LangGraph mechanism (`get_stream_writer()`) to emit arbitrary events from inside a node to the stream consumer — used to stream tokens from the raw Anthropic SDK without LangChain. |
| **`stream_mode=["updates","custom"]`** | Multi-mode LangGraph streaming yielding `(mode, chunk)` tuples: `updates` = per-node state deltas, `custom` = writer events. Interleaved in execution order. |
| **NO_QUERY sentinel** | A tool's explicit "I cannot answer this" return, preferred over a fabricated query. The structured-data analogue of "honest absence." |
| **Prompt-cache floor** | Anthropic caches a prefix only if it clears ~1024 tokens. The tiny `/answer` prompt never qualified; tools+system in the agent loop do (cache read landing is still being tuned — see bug 18). |
| **`min-h-0`** | The CSS escape hatch (`min-height:0`) that lets a flex/grid child shrink below content size so a nested `overflow-y-auto` can actually scroll. |

---

## 9. What's deliberately not done yet

| Capability | Day |
|---|---|
| Prompt-cache read landing in the tool-loop (cost optimization) | 3.5 / follow-up |
| Ragas + custom evaluation harness, 30–50 question tiered set | 4 |
| Provider A/B (Claude vs Gemini) on identical retrieval | 4 |
| Llama 3.2 3B Q4_K_M edge variant + benchmark table | 4 |
| Multi-turn conversation / memory | later |
| Deployment (Vercel + Railway/Fly.io) + 90s demo | 5 |

---

## 10. Day-4 preview

Day 4 makes the agent *provable*:

1. **Tiered eval set (30–50 Qs)** — factual (SQL), narrative (vector), multi-hop (both + calculator), and honesty (unanswerable → must decline). The eval set is the spec.
2. **Ragas + a custom harness** — faithfulness, answer relevance, context precision/recall; plus exact-match checks on the SQL-route numbers (where we now have ground truth post-fix).
3. **Provider A/B** — same retrieval, Claude vs Gemini, on the eval set. The seam makes this a config flip; the numbers justify the spend.
4. **Edge variant** — Llama 3.2 3B Q4_K_M locally, benchmarked against the cloud agent on the same questions: quality vs latency vs cost.

After Day 4 the system stops asserting it works and starts showing it. Day 5 deploys and records the demo.
