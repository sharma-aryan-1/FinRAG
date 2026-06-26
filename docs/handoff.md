# FinRAG — Session Handoff

> Point the next session at this file. Auto-memory loads the user profile, working mode, and project overview first; then read this, then `docs/day4.md` (newest) and `docs/day3.md` for the durable references.

---

## Where we are

```
Day 1 ✓  foundation, dense retrieval                (docs/day1.md)
Day 2 ✓  hybrid funnel + Rerank v3 + DuckDB + UI     (docs/day2.md)
Day 3 ✓  agent layer — Decisions 14–18 COMPLETE      (docs/day3.md)
Day 4 ✓  eval harness + provider A/B + faithfulness loop  (docs/day4.md)
Day 5 ◑  edge Llama variant DONE; local demo polish next   (docs/day5.md)  ← START HERE
```

Days 1–4 done. **The Day-5 edge variant is built, benchmarked, and documented** — `docs/day5.md` is the durable reference (the `local` provider, the tools-on vs synthesis-only A/B, the counterintuitive finding, the 4GB-VRAM deployment constraint). What remains in Day 5 is the **local demo polish** (step 2 below). This file is the live checkpoint.

## End-of-session state (last touched 2026-06-26 — Day 5 edge variant)

- **Day 5 edge variant complete (not yet committed).** New: `backend/src/finrag/llm/local.py`. Modified: `config.py` (local_* settings), `llm/__init__.py` (4 dispatchers + `"local"` branch), `eval/harness.py` (local rate + tok/s), `pyproject.toml` (`openai>=1.50`), `uv.lock`. Doc: `docs/day5.md`. Results: `data/eval_results_local_tools.json`, `data/eval_results_local_synth.json`. Memory + MEMORY.md to update.
- **The finding (counterintuitive — inverts the original hypothesis):** the agentic tool-loop is what makes the 3B model viable, NOT a liability. Factual accuracy **0.80 (tools) vs 0.00 (synthesis-only)** — the model reliably *invokes* `sql_query` but can't read exact figures from prose or chain steps. Full A/B table + evidence in `docs/day5.md`. Honesty regresses slightly with tools (over-answers, 0.80 vs 1.00); multihop fails both ways (~0.14); narrative `[N]` citations fail in both modes (0.00).
- **Deployment constraint found the hard way:** GTX 1650 Ti has **4GB VRAM**; `llama3.2:3b` loads at 2.9GB → VRAM exhaustion crashed the NVIDIA driver (TDR → full system reboot) mid-run. **Fix: pin Ollama to CPU** (`CUDA_VISIBLE_DEVICES=-1`) → 2.4GB in RAM, 0 VRAM, stable. CPU throughput ~1.6–2.9 tok/s end-to-end, ~50–96s/question. See gotcha #9.
- **Eval baseline (Claude, Day 4, unchanged):** accuracy 1.00, faithfulness ~0.92, relevance 0.98, ctx precision 0.55, ~$0.39/30-Q.
- **Live app untouched:** `llm_provider="anthropic"` still default; local is opt-in via `--provider local` / `LLM_PROVIDER=local`. No uvicorn restart needed (no node/prompt change; only a new backend module + dispatcher branch).
- **Git:** Day-5 work NOT committed (working tree dirty). Commit only when the user asks.

## FIRST ACTION NEXT SESSION: Day 5 step 2 — local demo polish

The edge variant (step 1) is done. Remaining Day-5 scope (**locked with user:** local + recorded, no public URL, Ollama runtime):

1. ~~Edge variant~~ ✓ DONE — see `docs/day5.md`.
2. **Local demo polish** — scripted path (the two canaries + one multi-company question), a recorded loom/gif, and a README "what the eval proves" section pulling Day-4 + Day-5 numbers. No hosting. Decide with the user whether the demo runs on Claude (fast, the live default) or shows the local model (the edge story, but ~50–96s/question on CPU).
3. **Stretch / parked** (day4.md §10): tighten the top-8 funnel to raise context precision (0.55); thread plan-call tokens into `state["usage"]`; prompt-cache read not landing in the tool-loop (day3.md bug 18). Possible Day-5 follow-up: benchmark an 8B local model — does it close the multihop/citation gap, or is chained tool-reasoning a capability cliff?

## How to run (verify before building on it)

```powershell
# Infra (Qdrant on :6333) — Docker Desktop must be running first
docker compose -f infra/docker-compose.yaml up -d
docker compose -f infra/docker-compose.yaml ps

# Backend — restart REQUIRED after any Docker restart (lru_cached QdrantClient)
cd D:\FinRAG\backend
uv run --no-sync uvicorn finrag.main:app --reload --port 8000   # see gotcha #2 re: --no-sync

# Frontend
cd D:\FinRAG\frontend
npm run dev          # http://localhost:3000

# Eval harness (Day 4) — Qdrant must be up for narrative/vector cases
cd D:\FinRAG\backend
$env:PYTHONIOENCODING="utf-8"
uv run --no-sync python -m finrag.eval.harness            # full 30-Q on Claude
uv run --no-sync python -m finrag.eval.harness --smoke    # 1 per tier (cheap)
uv run --no-sync python -m finrag.eval.harness --no-judge # deterministic only (free)

# Local edge model (Day 5) — CPU-pinned Ollama must be up first (gotcha #9)
$env:LOCAL_USE_TOOLS="true"  # "false" = degraded synthesis-only mode
uv run --no-sync python -m finrag.eval.harness --provider local --out ..\data\eval_results_local_tools.json
```

Canaries (must stay true):
- `How did Apple's services revenue change in fiscal 2023?` → **+$7.1B / +9% (~$85.2B)** from vector chunks, cited. NOT $383.285B.
- `What was Apple's net income in fiscal 2023?` → **$96.995B** via `sql_query`.
- Eval: `--smoke` should pass factual (✓), narrative (cites, faithful), multihop (✓), honesty (declines).

## Final provider state

`llm_provider="anthropic"`, `CLAUDE_MODEL="claude-sonnet-4-6"`. **Three** providers now sit behind the seam, all selected live via `settings.llm_provider`: `anthropic` (default), `gemini` (`gemini-2.5-flash-lite`), and `local` (`llama3.2:3b` via Ollama — Day 5). The harness flips provider per-run with `--provider`. Anthropic key in `D:\FinRAG\.env` is funded; ~$0.04/agent question + ~$0.39 for a full 30-Q eval. Local runs are $0 (generation) but the Claude judge still bills ~$0.30–0.50/30-Q.

## Gotchas (carry forward — these bit us)

1. **Windows console cp1252** can't print unicode (`↳ ▸ ✓`) → run scripts with `PYTHONIOENCODING=utf-8`.
2. **`uv` websockets lock**: a running uvicorn holds `.venv/.../websockets/speedups.pyd`; `uv run`'s pre-sync then fails. Use `uv run --no-sync` while the server is up. (`--no-sync` warns "no effect outside a project" when run from repo root — run eval/uv commands from `backend/`.)
3. **Restart uvicorn whenever Docker restarts** (lru_cached QdrantClient holds a dead connection). Same after any `nodes.py`/prompt change.
4. **Don't run `npm run dev` on top of a `next build`** — dev serves mismatched chunks (unstyled page). `rm -rf .next` then `npm run dev`. Use `tsc --noEmit` to typecheck, not `next build`.
5. **`.env` is at repo root** `D:\FinRAG\.env` (not backend/).
6. **Run eval/python from `backend/`**, not repo root, or you get `ModuleNotFoundError: No module named 'finrag'`.
7. **Docker Desktop is a GUI app** — if the daemon is down, it must be started manually (the npipe error means it's not running); then `docker compose up -d`.
8. **Eval cost/quota**: a full 30-Q Claude run ≈ $0.39 and a few minutes (run in background). Gemini free tier = 20 req/day/model — an agent burns ~5–6/question, so only ~2–3 questions fit before 429s.
9. **Local Llama on a 4GB GPU CRASHES the machine.** `llama3.2:3b` loads at 2.9GB VRAM on the GTX 1650 Ti (4GB total) → desktop load tips it over → NVIDIA driver TDR → full reboot. **Always pin Ollama to CPU for local runs:** stop the desktop tray app + server, then `$env:CUDA_VISIBLE_DEVICES="-1"; $env:OLLAMA_KEEP_ALIVE="3m"; $env:OLLAMA_NUM_PARALLEL="1"; ollama serve`. Verify `ollama ps` shows `100% CPU`. CPU is slow (~50–96s/question) but stable.

## Working mode (load-bearing)

Explain-while-implementing: write the code, annotate the *why*, keep responses tight (the user is time-constrained). Default to **guide, not implement** unless explicitly asked to write code — but once asked, build and verify. If they say "let me try this one," flip to pure-guide for that step. Ask before changes that affect the live app or cost money.

## Conventions

- `docs/dayN.md` written at the **end** of each day (day4.md is current). This handoff.md is the live checkpoint. Don't touch `PLAN.md` or `docs/resume.md` (gitignored, personal). **Don't `git commit` unless asked.** **LangGraph only — no LangChain** (Ragas was rejected on Day 4 to keep `langchain_core` out of the tree; the eval harness is hand-rolled).
