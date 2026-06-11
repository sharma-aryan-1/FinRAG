# FinRAG — Session Handoff

> Point the next session at this file. Auto-memory loads the user profile, working mode, and project overview first; then read this, then `docs/day3.md` for the full Day-3 reference.

---

## Where we are

```
Day 1 ✓  foundation, dense retrieval                (docs/day1.md)
Day 2 ✓  hybrid funnel + Rerank v3 + DuckDB + UI     (docs/day2.md)
Day 3 ✓  agent layer — Decisions 14–18 COMPLETE      (docs/day3.md)
         14 ✓ /answer synthesis + provider seam
         15 ✓ tools (sql_query / calculator / lookup_citation)
         16 ✓ LangGraph agent /agent  (verified on Claude)
         17 ✓ SSE streaming /agent/stream (trace + tokens)
         18 ✓ frontend live agent-trace UI
Day 4 →  eval harness + provider A/B + edge variant  ← START HERE
Day 5    deploy + demo
```

Day 3 is done and documented. **`docs/day3.md` is the durable reference** (decisions, the provider pivot, bugs, concepts, resume material). This file is just the live checkpoint into Day 4.

## End-of-session state (last touched 2026-06-09)

- **Verified live this session:** agent on Claude end-to-end — services canary (+$7.1B/+9%, ~$85.2B), net-income sql route ($96.995B), `/answer` + `/agent` + `/agent/stream` over HTTP, no regression after the streaming refactor. (Prompt-cache reads still 0 — parked, see below.)
- **Frontend (Decision 18) confirmed working:** trace + token streaming renders; fixed two UI bugs — prod-build-served-to-dev (assets 404 → unstyled/no-hydration) and the left-pane scroll (`min-h-0`). `npm run build` is clean.
- **Docs all landed:** `docs/day3.md` (incl. a redrawn architecture diagram), `README.md`, and memory (overview refreshed, day3→day4 pointer) are current.
- **Servers may still be running** from this session: backend `uvicorn` on :8000, frontend `npm run dev` on :3000. Restart per the run steps below if stale (and note gotcha #4 if the UI looks unstyled).
- **Git:** still all uncommitted — `agent/`, `llm/`, `tools/` untracked; `main.py`, `config.py`, `facts.py`, docs modified. Nothing committed (per convention — commit only when asked).

## FIRST ACTION NEXT SESSION: Day 4 — evaluation

The system now *asserts* it works (canaries pass); Day 4 makes it *provable*. Suggested order:

1. **Build a tiered eval set (30–50 Qs)** across four difficulty tiers:
   - factual / top-level → **sql** route (exact-match checkable; we have ground truth post-fix)
   - narrative / segment → **vector** route (faithfulness + citation)
   - multi-hop → **both** + calculator (e.g. YoY growth, cross-company margin)
   - honesty → unanswerable from corpus → agent must **decline**, not fabricate
2. **Ragas + a custom harness** — faithfulness, answer relevance, context precision/recall; plus exact-match assertions on the sql-route numbers.
3. **Provider A/B** — same retrieval, **Claude vs Gemini**, on the eval set. The seam makes this a `llm_provider` flip; the numbers justify the spend.
4. **Edge variant** — Llama 3.2 3B Q4_K_M locally, benchmarked vs the cloud agent (quality / latency / cost).

Note the existing `backend/src/finrag/eval/smoke.py` is **retrieval-only** (no LLM) — Day 4's harness is a new, LLM-in-the-loop thing alongside it.

## How to run (verify before building on it)

```powershell
# Infra (Qdrant on :6333)
docker compose -f infra/docker-compose.yaml ps

# Backend — restart REQUIRED after any Docker restart (lru_cached QdrantClient)
cd D:\FinRAG\backend
uv run --no-sync uvicorn finrag.main:app --reload --port 8000   # see gotcha #2 re: --no-sync

# Frontend
cd D:\FinRAG\frontend
npm run dev          # http://localhost:3000

# Fast agent sanity (no server): runs the graph directly
$env:PYTHONIOENCODING="utf-8"; uv run --no-sync python -m finrag.agent.graph
```

Canaries (must stay true):
- `How did Apple's services revenue change in fiscal 2023?` → **+$7.1B / +9% (~$85.2B)** from vector chunks, cited. NOT $383.285B.
- `What was Apple's net income in fiscal 2023?` → **$96.995B** via `sql_query`.

## Final provider state

`llm_provider="anthropic"`, `CLAUDE_MODEL="claude-sonnet-4-6"`. Gemini (`gemini-2.5-flash-lite`) stays fully wired behind the seam — flip `llm_provider=gemini` for the Day-4 A/B. The Anthropic key in `D:\FinRAG\.env` is funded. ~$0.04/agent question on Claude. **Why we left Gemini:** free tier = 20 req/DAY/model (an agent burns ~5/question) + flash-lite `MALFORMED_FUNCTION_CALL`. Full story in day3.md.

## Parked / known-open (not blocking Day 4)

1. **Prompt-cache read not landing in the tool-loop** (day3.md bug 18): `cache_read_input_tokens` stays 0; the write lands on the final call. Cost-only, correctness unaffected. Likely fix: add a `cache_control` breakpoint on the **tools** block (currently only `system` is marked in `claude.py:_cached_system`). Worth a focused 30-min look before Day 5 deploy, or fold into the A/B cost numbers.

## Gotchas (carry forward — these bit us)

1. **Windows console cp1252** can't print unicode (`↳ ▸`) → run scripts with `PYTHONIOENCODING=utf-8`.
2. **`uv` websockets lock**: a running uvicorn holds `.venv/.../websockets/speedups.pyd`; `uv run`'s pre-sync then fails (`Access is denied`). Use `uv run --no-sync` while the server is up, or stop the backend first; `UV_LINK_MODE=copy` helps. A clean `uv sync` (backend stopped) repairs the half-written `websockets` dist-info warnings.
3. **Restart uvicorn whenever Docker restarts** (lru_cached QdrantClient holds a dead connection).
4. **Don't run `npm run dev` on top of a `next build`** — dev serves mismatched hashed chunks (unstyled page, dead button). `rm -rf .next` then `npm run dev` (day3.md bug 19).
5. **`.env` is at repo root** `D:\FinRAG\.env` (not backend/).
6. **smoke.py is retrieval-only** — it will not catch a structured-value bug (that's how the 2-year fact mislabel survived Day 2; day3.md bug 15).

## Working mode (load-bearing)

Explain-while-implementing: write the code, annotate the *why*, don't punt to the user. They're time-constrained — keep responses tight; go deep only on genuinely conceptual moments. If they say "let me try this one," flip to pure-guide for that step.

## Conventions

- `docs/day4.md` is written at the **end** of Day 4 (mirrors day1–3). This handoff.md is the live checkpoint. Don't touch `PLAN.md` or `docs/resume.md` (gitignored, personal). Don't `git commit` unless asked. **LangGraph only — no LangChain.**
