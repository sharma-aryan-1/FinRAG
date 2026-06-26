# FinRAG — Demo Runbook

A scripted 3-question walkthrough for a recorded demo (loom/gif). Runs on the **live Claude agent** (the default, ~8s/question) through the split-pane web UI — fast and reliable for recording. Each question is chosen to showcase a *different* capability; together they cover the whole system in ~90 seconds.

> The edge/local model is **not** used for the demo (it's ~50–96s/question on CPU — see `docs/day5.md`). The demo shows the production path; the eval numbers tell the edge story.

---

## Pre-flight (do before recording)

```powershell
# 1. Qdrant up (Docker Desktop running first)
docker compose -f infra/docker-compose.yaml up -d
docker compose -f infra/docker-compose.yaml ps      # finrag-qdrant healthy

# 2. Backend — provider MUST be Claude (the default). Restart it fresh.
cd D:\FinRAG\backend
uv run --no-sync uvicorn finrag.main:app --reload --port 8000

# 3. Frontend
cd D:\FinRAG\frontend
npm run dev                                          # http://localhost:3000
```

Checklist before hitting record:
- `LLM_PROVIDER` is `anthropic` (not `local`/`gemini`) — the trace should stream token-by-token (only Claude streams; the others replay).
- Anthropic key in `D:\FinRAG\.env` is funded (~$0.04/question × 3 ≈ $0.12 for the whole demo).
- Browser zoomed so the split-pane (chat left, citation viewer right) both fit.
- Do one throwaway warm-up question first so the first recorded answer isn't cold.

---

## The three questions (ask in this order)

### 1. Narrative + citations — *the retrieval story*
> **How did Apple's services revenue change in fiscal 2023?**

**Expected:** +$7.1B / +9% to ~$85.2B, with `[N]` citations. **NOT** $383.285B (that's total revenue — the mis-route this question used to trigger).

**What to point at while it streams:**
- Trace: `rewrite` → `route: vector` (segment-level figures live in the filing text, not the SQL facts table — narrate this; it's a deliberate routing decision).
- **The money moment:** the agent first tries a `sql_query` tool call, sees the facts table has no segment-level data, and *narrates the fallback* — "The SQL database doesn't carry segment-level figures, so I'll rely on the context chunks directly." This is genuine agentic adaptivity, not a script. Call it out.
- The answer's `[N]` markers → **click a citation** to open the full source passage in the right pane, with the SEC.gov link.

*Showcases: the hybrid retrieval funnel + citation-clean-by-construction provenance.*

### 2. Structured figure via a tool — *the agent story*
> **What was Apple's net income in fiscal 2023?**

**Expected:** $96.995B, via the `sql_query` tool.

**What to point at:**
- Trace: `route: sql` → a `sql_query` **tool call with the generated SQL inline** (`SELECT … FROM financial_facts WHERE ticker='AAPL' AND line_item='net_income' …`).
- The exact figure, unrounded, in the answer.

*Showcases: the LangGraph tool-loop and the modal-split architecture (text in Qdrant, exact figures in DuckDB).*

### 3. Cross-company comparison — *the differentiator*
> **Compare net income for Apple, Tesla, and JPMorgan in fiscal 2023.**

**Expected:** three figures fetched and compared (Apple highest at ~$97.0B). Validated in the eval as case `m06`.

**What to point at:**
- Multiple `sql_query` tool calls in the trace (one resolution per company).
- A synthesized comparison sentence grounded in the fetched figures — *one natural-language question, several structured lookups, one cited answer.*

*Showcases: multi-step agentic reasoning over the structured store — the thing most portfolio RAG projects can't do.*

---

## After the three questions (optional closer)

Mention the evidence layer verbally or with a cut to the terminal:
- "Every one of these is covered by a 30-question eval harness — faithfulness, citation validity, honesty — scored 1.00 exact-match / ~0.92 faithfulness on Claude."
- "And the same agent runs on a local 3B model at $0 — where the eval surfaced that the agentic tools are what make the small model usable at all."

Pull the numbers from [`docs/day4.md`](./day4.md) and [`docs/day5.md`](./day5.md), summarized in the README's [What the eval proves](../README.md#what-the-eval-proves).

---

## Recording notes

- Keep it under ~2 minutes; the streaming trace is the star — let one answer stream fully so the token-by-token + live tool-call rendering is visible.
- If a question mis-routes or a tool errors on the day, re-ask once (temperature 0 is deterministic, but retrieval/judge variance is real). The three above are the validated canaries — they should hold.
- Export as a gif for the README header or a loom link in the project writeup. No public hosting of the app itself (no key exposure) — the recording *is* the demo.
