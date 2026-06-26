# FinRAG — Day 5 Reference

Day 4 made the system *provable* (eval harness + Claude baseline). **Day 5 makes it *portable* and asks a sharper question: can a small, local, edge-deployable model run this agentic RAG at all — and what breaks when it tries?** It adds a third provider behind the existing seam (Llama 3.2 3B via Ollama), benchmarks it on the Day-4 harness, and runs a tools-on vs synthesis-only A/B that **inverts the hypothesis the handoff started with**.

> Pre-req: `docs/day4.md` (the eval harness, the `llm_provider` seam, the tiered set). Day 5 reuses `run_agent`, the harness, and the judge unchanged — only a new generator is swapped in via `--provider local`.

---

## 1. At a glance

| Day-5 deliverable | Status |
|---|---|
| Third provider behind the seam — `llm/local.py` (Ollama, OpenAI-compatible API) | ✓ |
| `"local"` branch in all 4 dispatchers (`llm/__init__.py`); stream replays like Gemini | ✓ |
| `local_use_tools` kill-switch → degraded synthesis-only mode (`config.py`) | ✓ |
| Harness `--provider local` path + $0 cost + tokens/sec line (`eval/harness.py`) | ✓ |
| Full 30-Q judged run, tool-loop mode | ✓ 0 errors |
| Full 30-Q judged run, synthesis-only mode (the A/B) | ✓ 0 errors |
| The finding: **agentic tools make the small model viable, not the other way round** | ✓ |

| Metric (Llama 3.2 3B, local, CPU) | Tools-on | Synthesis-only | Claude (Day 4) |
|---|---|---|---|
| **Factual accuracy** (n=10) | **0.80** | **0.00** | 1.00 |
| Honesty / refusal (n=5) | 0.80 | **1.00** | 1.00 |
| Multihop accuracy (n=7) | 0.14 | 0.00 | 1.00 |
| Narrative citation validity (n=8) | 0.00 | 0.00 | 1.00 |
| Faithfulness (graded tiers) | 0.84 | 0.47 | ~0.92 |
| Answer relevance | 0.65 | 0.34 | 0.98 |
| Overall accuracy (n=30) | **0.59** | 0.23 | 1.00 |
| Cost / 30-Q run | **$0** | **$0** | ~$0.39 |
| Latency / question | ~96s (CPU) | ~50s (CPU) | ~8s |

---

## 2. The headline finding (it inverts the handoff hypothesis)

The Day-4 handoff predicted: *"Llama 3.2 3B tool-calling is weak — the agentic tool-loop may misfire/loop. If so, benchmark in a degraded synthesis-only mode and report that as the finding."* The implicit assumption was that **tools are the liability** and synthesis-only is the safe fallback for a small model.

**The data says the opposite.** Factual accuracy is **0.80 with tools vs 0.00 without**. The agentic tool-loop isn't what breaks the small model — it's what makes it usable.

Why: a 3B model can reliably **invoke a single tool** (`sql_query`) even though it cannot **read an exact 12-digit figure out of prose** or **reason across multiple steps**. With tools, the deterministic SQL layer supplies the precision the model lacks; the model only has to recognize "this needs a figure" and emit one well-formed call — which it does. Strip the tools away and it has to extract `$96,995,000,000` from a retrieved chunk by itself, and it fabricates instead.

The synthesis-only failure mode is vivid. Asked for Apple's FY23 net income **with no tools available**, the model answered:

> *"I'll use the sql_query tool to get the exact figure for Apple Inc.'s total revenue, net income, and total assets for fiscal year 2023. If that doesn't work, I'll fall bac[k]…"*

It narrates about calling a tool it no longer has, instead of reading the answer from the context sitting in its prompt. The agentic scaffold wasn't a tax on the small model — it was the crutch holding it up.

**Takeaway for the portfolio:** *"For agentic financial RAG, the agent architecture is what makes a small edge model viable — it offloads precision and arithmetic to deterministic tools, leaving the model to do only what a 3B can do: route and invoke. Removing the tools to 'simplify' for a small model is exactly backwards."*

---

## 3. The nuances (where each mode wins, and why)

- **Tools win decisively on factual/numeric** (0.80 vs 0.00). The single `sql_query` call is reliable; the figure comes from DuckDB, not the model's reading.

- **Synthesis-only wins on honesty** (1.00 vs 0.80). This is the real cost of giving a small model tools: it **over-answers**. Asked Tesla's FY**2026** revenue (forward-looking, unanswerable), the tool-mode model deflected to a number it *could* fetch — *"Tesla's revenue for 2024 was approximately $97.69 billion"* — instead of declining. With no tools and irrelevant context, it has nothing to grab and correctly refuses every time. A 3B model with a hammer treats every question as a nail.

- **Multihop fails both ways** (0.14 / 0.00). Chained `sql_query → calculator` reasoning is beyond the 3B model regardless of mode. On "% change in Apple net income FY22→FY23" it made **one** call (fetched the two figures, one slightly mis-transcribed) and **stopped** — never the second call to compute the percentage. It can take the first step of a plan, not the second.

- **Narrative citations fail in both modes** (validity 0.00). The 3B model never emits valid `[N]` anchors — its risk summaries are fluent and mostly grounded (faithfulness ~0.88) but ignore the citation-format instruction entirely. This is mode-independent: a structured-output-following weakness, not a tool-loop weakness.

- **Faithfulness tracks tools** (0.84 vs 0.47). Without tools the model invents numbers, so factual-tier faithfulness collapses (0.40). Tools ground it.

---

## 4. The deployment constraint (a real edge finding, not a footnote)

The host GPU is a **GTX 1650 Ti — 4GB VRAM**. `llama3.2:3b` (Q4) loads at **2.9GB on the GPU**, leaving ~1.1GB for the Windows desktop/compositor/browser. Under normal desktop load this crossed the 4GB ceiling and triggered an **NVIDIA driver timeout (TDR) → full system crash** mid-benchmark (lost both runs once).

Fix and finding in one: pin Ollama to **CPU** (`CUDA_VISIBLE_DEVICES=-1`, `OLLAMA_NUM_PARALLEL=1`, `OLLAMA_KEEP_ALIVE=3m`). The model then sits at **2.4GB in system RAM, 0 bytes VRAM** (15.8GB RAM, ~7GB free) — zero driver-crash risk for an unattended run.

> **Edge takeaway:** *"A 4GB consumer GPU cannot safely co-host even a 3B model alongside a desktop session — VRAM exhaustion crashes the driver. CPU inference is the stable edge path on this class of hardware."* This is a more honest and more portable edge story than a GPU number anyway: no GPU dependency at all.

CPU throughput: **12.2 tok/s** raw single-shot generation; **~1.6–2.9 tok/s end-to-end** across the agent loop (the loop re-processes a growing context each round-trip, and prompt-eval over 8 retrieved chunks dominates on CPU). Tools-on is ~2× slower per question than synthesis-only (~96s vs ~50s) precisely because of those extra round-trips. Versus Claude's ~8s/question at ~1.00 accuracy, the local model is the cheaper-but-slower-and-weaker corner of the tradeoff — exactly what an edge variant is for.

---

## 5. What was built (the seam held)

The entire edge variant is **one new backend module + a branch in each dispatcher**. No node, tool, or graph code changed — the Day-3 provider seam absorbed a third provider exactly as designed.

| File | Change |
|---|---|
| `llm/local.py` | **new** — `generate_text`, `synthesize_local`, `tool_loop` on Ollama's OpenAI-compatible endpoint (`:11434/v1`), mapping usage onto the shared `SynthesisResult`/`ToolLoopResult`. `tool_loop` honors `settings.local_use_tools`: `True` = real agentic loop; `False` = `_synthesis_only` over the provided context. |
| `llm/__init__.py` | `"local"` branch in `synthesize`, `generate_text`, `run_tool_loop`, and `run_tool_loop_stream` (the last replays the non-streaming result through the callbacks, same as Gemini). |
| `config.py` | `local_model` (`llama3.2:3b`), `local_base_url` (`http://localhost:11434/v1`), `local_use_tools` (`True`). |
| `eval/harness.py` | `_RATES["local"] = (0,0)` → cost reports $0; a `local throughput≈X tok/s` line derived from wall-clock latency. |
| `pyproject.toml` | `openai>=1.50` — used as the client *because* Ollama is OpenAI-compatible, so the same code targets vLLM / llama.cpp / LM Studio by swapping `local_base_url` alone. |

**Why the OpenAI client and not Ollama's native API:** portability. The seam isn't "Ollama" — it's "any OpenAI-compatible local server." Pointing `local_base_url` at a vLLM box on a Jetson would need zero code change.

---

## 6. How to reproduce

```powershell
# 1. Pin Ollama to CPU (avoids the 4GB-VRAM crash) and start the server
#    (stop the desktop tray app first; it would respawn a GPU-bound server)
$env:CUDA_VISIBLE_DEVICES="-1"; $env:OLLAMA_KEEP_ALIVE="3m"; $env:OLLAMA_NUM_PARALLEL="1"
ollama serve            # leave running; `ollama ps` should show 100% CPU

# 2. Qdrant up (narrative/vector cases), from repo root
docker compose -f infra/docker-compose.yaml up -d

# 3. Run both modes (judge = Claude, ~$0.30–0.50; local generation = $0)
cd D:\FinRAG\backend
$env:PYTHONIOENCODING="utf-8"
$env:LOCAL_USE_TOOLS="true"  ; uv run --no-sync python -m finrag.eval.harness --provider local --out ..\data\eval_results_local_tools.json
$env:LOCAL_USE_TOOLS="false" ; uv run --no-sync python -m finrag.eval.harness --provider local --out ..\data\eval_results_local_synth.json
```

Results land in `data/eval_results_local_tools.json` and `data/eval_results_local_synth.json`. Each full run is ~30–50 min on CPU. `LOCAL_USE_TOOLS` is read per-process by pydantic settings, so it flips the mode without a code edit.

> Gotcha carried from Day 4: run from `backend/` (not repo root) or `ModuleNotFoundError: finrag`; set `PYTHONIOENCODING=utf-8` for the unicode report glyphs.

---

## 7. Parked / next

- **Live app stays on Claude** (`llm_provider="anthropic"`). The local provider is opt-in via `--provider local` or setting `LLM_PROVIDER=local`; nothing about the demo path changed.
- **Demo polish** (Day-5 step 2, not yet done): scripted local path — the two canaries + one multi-company question — recorded (loom/gif), plus a README "what the eval proves" section pulling Day-4 + Day-5 numbers. No public hosting.
- **If revisited:** a 7B/8B local model (e.g. `llama3.1:8b`) would likely fix narrative citations and some multihop, at the cost of needing >4GB — i.e. a real GPU or heavier CPU latency. The interesting follow-up: does an 8B model close the multihop gap, or is chained tool-reasoning a capability cliff that only the frontier models clear?
- Day-4 stretch items still open: tighten the top-8 funnel (context precision 0.55→); thread plan-call tokens into `state["usage"]`.
