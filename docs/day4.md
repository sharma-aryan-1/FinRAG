# FinRAG — Day 4 Reference

Day 3 made the system *assert* it works (canaries passed). **Day 4 makes it *provable*.** It adds a tiered evaluation set, a two-layer scoring harness (deterministic + LLM-judge), a full quality run on Claude, a Claude-vs-Gemini A/B, and — the point of building an eval at all — a closed **eval → improve → re-measure** loop that caught and fixed a real faithfulness defect.

> Pre-req: `docs/day3.md` (the agent, tools, provider seam). Day 4 builds directly on `run_agent` and the `llm_provider` seam from Day 3.

---

## 1. At a glance

| Day-4 deliverable | Status |
|---|---|
| Tiered eval set — 30 Qs × 4 tiers, DB-derived ground truth (`eval/dataset.py`) | ✓ |
| Deterministic metrics — number/citation/refusal (`eval/metrics.py`) | ✓ |
| LLM-judge metrics — faithfulness / relevance / context-precision (Claude judge) | ✓ |
| Provider-parametrized runner + per-tier aggregation (`eval/harness.py`) | ✓ |
| Full 30-Q baseline on Claude | ✓ 0 errors |
| Claude vs Gemini A/B | ✓ (quota-bounded, n=2 — see §6) |
| Eval→improve loop: faithfulness prompt fix + re-measure | ✓ multihop 0.88→0.97 |
| Edge variant (local Llama 3.2 3B) | deferred → Day 5 |

| Metric (Claude, post-fix) | Value |
|---|---|
| Exact-match accuracy (factual + multihop + honesty refusal, n=22) | **1.00** |
| Citation validity (narrative, n=8) | **1.00** |
| Faithfulness (graded tiers, weighted) | **~0.92** |
| Answer relevance | **0.98** |
| Context precision (narrative top-8) | **0.55** |
| Errors across 30 cases | **0** |
| Cost / 30-Q run (agent node) | ~$0.39 |

---

## 2. Why this phase matters

"I built RAG" is common. "I built RAG and can show you its faithfulness, citation-validity, and honesty numbers, plus a cost/quality provider A/B, and I closed the loop by fixing a defect the eval found" is the production-RAG signal. Day 4 is that evidence layer. Three design decisions shaped it:

1. **Ground truth is derived from our own `financial_facts` table at run time**, not typed from memory. A `gt` callable resolves lazily against DuckDB, so the expected numbers are correct-by-construction and can never drift from what the agent can actually query. This turns the factual tier into a *regression check*: does the agent route to sql and report the figure faithfully, or mislabel it (the exact class of bug that survived Day 2's retrieval-only smoke test)?
2. **The judge is pinned to Claude even when the system under test is Gemini.** Holding the grader constant is what makes the A/B a fair comparison of *generators*. The cost: self-preference bias when Claude grades Claude — named here, not hidden.
3. **Two scoring layers.** Deterministic checks (number match, citation range, refusal) are objective and free; the LLM judge covers what regex can't (is every claim grounded? does the answer address the question?). Hard facts get hard checks; soft qualities get the judge.

---

## 3. The eval set (`eval/dataset.py`)

30 questions across four tiers, each probing a distinct failure mode:

| Tier | n | Route | What it tests | How it's scored |
|---|---|---|---|---|
| **factual** | 10 | sql | One exact top-level figure (revenue, net income, EPS…) | Exact-match vs DB ground truth |
| **narrative** | 8 | vector | Qualitative content (risk, competition, segments) | Faithfulness + citation validity + precision |
| **multihop** | 7 | sql+calc | A *computed* value (YoY growth, margin) or cross-company compare | Exact-match of the computed number |
| **honesty** | 5 | — | Unanswerable: company/year not in corpus, or forward-looking | Must **decline**, not fabricate |

Ground-truth helpers: `_v(ticker, line_item, fy)` (one authoritative figure), `_yoy(...)` (percent growth), `_margin(...)` (ratio). Numeric cases carry a `gt` lambda + `gt_kind` (`currency | per_share | percent`) + tolerance.

Honesty cases are deliberately clean negatives: **Microsoft / Amazon / Google** (companies not in corpus), **Apple FY2019** (outside the FY2022–2024 span), **Tesla FY2026** (forward-looking). Each must trip the corpus-grounding guard added in the session before Day 4.

> Note on multihop routing: every multihop question is answerable from structured facts + arithmetic, so the router legitimately picks `sql` over `both`. `route_expected` is left `None` for that tier — the tier tests multi-step *reasoning* (fetch → compute), not the route.

---

## 4. The metrics (`eval/metrics.py`)

**Deterministic layer (no LLM):**
- **Number matching** — format-robust extraction normalizes `$96.995 billion`, `$96,995 million`, `$97 billion`, `$4.30`, `-2.8%` to comparable magnitudes, then matches within tolerance (relative for currency/per-share; absolute pp floor OR'd with a 5% band for percent). Bare integers like "2023" are *not* treated as currency (must carry `$` or a scale word) — kills false positives.
- **Citation validity** — every `[N]` anchor must resolve to a retrieved chunk (1..n); an answer with zero citations or an out-of-range `[99]` fails.
- **Refusal detection** — keyword backstop for the honesty tier ("not in the corpus", "cannot answer"…).

**LLM-judge layer (Claude, strict-JSON prompts):**
- **Faithfulness** — fraction of the answer's claims supported by the context (retrieved chunks + tool results). The judge is given exactly what the agent was grounded on, reconstructed from `final["chunks"]` + every `tool_call` result in the trace.
- **Answer relevance** — does the answer address the question (a correct refusal counts as fully relevant).
- **Context precision** — of the top-8 retrieved chunks, what fraction the judge deems relevant.

The judge calls `claude.generate_text` **directly**, bypassing the provider dispatcher, so a Gemini-under-test run is still graded by Claude. JSON parsing is tolerant (strips code fences, returns `{}` on failure) so a flaky judge degrades to a missing score, never a crashed run.

---

## 5. Baseline results — Claude (`claude-sonnet-4-6`)

Full 30-case run, **zero errors**, ~$0.39:

| Tier | n | Accuracy | Route | Citation | Faithfulness | Relevance | Ctx-Precision |
|------|---|----------|-------|----------|--------------|-----------|---------------|
| factual | 10 | **1.00** | 1.00 | – | 0.74→**0.82** | 0.95 | – |
| narrative | 8 | – | 1.00 | **1.00** | **0.99** | **1.00** | 0.55 |
| multihop | 7 | **1.00** | 1.00 | – | 0.88→**0.97** | 1.00 | – |
| honesty | 5 | **1.00** | 1.00 | – | – | – | – |

**Headlines:**
- **Perfect exact-match accuracy** on every checkable figure (10 factual + 7 multihop), and **perfect refusal** on all 5 unanswerable questions — the corpus-grounding guard holds; no fabrication of Microsoft/Google/Amazon or out-of-span years.
- **Perfect citation validity** — narrative answers always cite, always in range.
- **High narrative faithfulness (0.99)** — qualitative answers stay grounded in retrieved text.

**Two honest weak spots:**
- **Context precision 0.55** — the top-8 funnel pulls ~45% marginal chunks before the answer is written. The *answer* stays faithful (the model ignores the noise), but it's a real retrieval-tightness signal: a smaller top-k or a relevance threshold post-rerank would raise it. Parked for Day 5.
- **Factual faithfulness was 0.74** — see §6, the defect the eval caught.

---

## 6. The eval→improve loop (the payoff)

**The defect.** Factual answers were always numerically correct (accuracy 1.00) but scored only **0.74** faithfulness. Inspecting the judge's `unsupported_claims` showed why — the agent decorated correct figures with claims the *context didn't support*:
- false provenance — *"as disclosed in its 10-K filing"* (the figure came from the XBRL facts DB, not the filing text),
- editorializing — *"a record-setting profit", "reflecting strong performance across its diversified business lines"*,
- outside facts — *"FY2024 ended September 28, 2024"* (true, but not in the tool result).

This is a genuine finance-assistant risk: an answer that *sounds* sourced but adds ungrounded narrative. Retrieval-only testing (Day 2's smoke) could never have caught it; only an answer-faithfulness judge does.

**The fix.** One rule added to the agent system prompt (`nodes.py`):
> *"Do not embellish. State only what the context or tool results actually support. Do not add provenance you cannot see, characterizations, or outside facts. A correct figure with ungrounded commentary is still a faithfulness failure."*

**The re-measure.** Re-ran the two affected tiers:

| Tier | Faithfulness before | after | Accuracy |
|------|--------------------|-------|----------|
| multihop | 0.88 | **0.97** | 1.00 (unchanged) |
| factual | 0.74 | **0.82** | 1.00 (unchanged) |

Multihop is a clean +0.09 to near-perfect; factual +0.08 with accuracy untouched. **No correct answer was lost to the stricter prompt.** A residual caveat worth stating: the LLM judge has run-to-run variance even at temperature 0 (one factual case wobbled 0.75→0.67 on an equivalent answer), so the factual lift sits partly within judge noise — multihop is the cleaner, more defensible win. The loop is the headline regardless: *the eval found a defect, a one-line prompt change fixed it, and the numbers moved.*

---

## 7. Provider A/B — Claude vs Gemini

Same retrieval, same judge (Claude), generator flipped via `--provider gemini`. The free-tier **20-requests/day** cap (each agent question burns ~5–6 calls) exhausted after ~2 questions, so 2 of the 4-case subset completed — **quota, not quality, ended the run.** That outcome is itself the finding.

| | f01 factual | n01 narrative | m01 multihop | h01 honesty |
|---|---|---|---|---|
| **Claude** | ✓ 8.4s | ✓ 22.4s | ✓ 10.9s | ✓ 4.4s |
| **Gemini** | ✓ **92.1s** | ✓ **45.0s** | ⛔ quota (429) | ⛔ quota (429) |

On the two completed cases, **quality was comparable** — both correct, both faithful (Gemini's terse factual answer actually scored *higher* faithfulness, 1.00, by not editorializing). But **latency was 4–11× worse** (92s vs 8s on the factual; non-streaming path + backoff), and **context precision was identical (0.38)** — confirming retrieval is provider-independent and the harness isolates the generator cleanly.

**Verdict:** Gemini flash-lite's free tier is *operationally unusable* for a call-heavy agent — the daily cap and latency disqualify it regardless of per-answer quality. This is the data behind Day 3's decision to run on Claude. The free quota resets daily, so a complete one-per-tier subset can be re-run later; a true 30-Q A/B needs a billing-enabled Gemini key.

---

## 8. How to run

```powershell
cd D:\FinRAG\backend
# Infra must be up (Qdrant on :6333) for vector/narrative cases:
#   docker compose -f ..\infra\docker-compose.yaml up -d

$env:PYTHONIOENCODING="utf-8"
uv run --no-sync python -m finrag.eval.harness                 # full 30-Q, Claude
uv run --no-sync python -m finrag.eval.harness --smoke         # 1 per tier (cheap)
uv run --no-sync python -m finrag.eval.harness --tier factual  # one tier
uv run --no-sync python -m finrag.eval.harness --provider gemini --smoke   # A/B subset
uv run --no-sync python -m finrag.eval.harness --no-judge      # deterministic only (free)
```

Flags: `--tier`, `--limit N`, `--out <path>` (defaults to `data/eval_results_<provider>.json`). Results JSON carries per-case metrics + `notes` (the judge's unsupported-claims, expected values) for drill-down.

---

## 9. Concepts worth keeping

- **Eval is a regression test for behavior, not just retrieval.** `eval/smoke.py` (Day 2) checks retrieval structure; it cannot see a faithfulness or honesty failure. Day 4's harness is LLM-in-the-loop and catches exactly those.
- **Derive ground truth from the source of truth.** DB-backed `gt` callables stay correct-by-construction — the eval can't rot as the data changes.
- **Hold the judge constant across an A/B.** Otherwise you're comparing grader + generator, not generators.
- **Deterministic where you can, judge where you must.** Numbers and citations are checkable with regex; faithfulness and relevance need a model. Use the cheap objective check first.
- **The loop is the deliverable.** A static scorecard is nice; *find → fix → re-measure* is the production-engineering signal.

---

## 10. Carry-forward / open items

1. **Context precision 0.55** — tighten the top-8 funnel (smaller k or a post-rerank score threshold) and re-measure narrative precision. Day 5.
2. **LLM-judge variance** — single-shot judging is noisy at the ±0.05 level; a multi-sample or rubric-anchored judge would tighten the faithfulness numbers if a harder claim is needed.
3. **Gemini A/B is n=2** — re-run the subset on a fresh daily quota for a complete one-per-tier comparison, or fund the key for the full 30-Q A/B.
4. **Edge variant (Llama 3.2 3B)** deferred to Day 5, paired with the deploy phase (quality/latency/cost vs cloud).
5. **Plan-call tokens** aren't threaded into `state["usage"]`, so the reported cost is an agent-node floor.
