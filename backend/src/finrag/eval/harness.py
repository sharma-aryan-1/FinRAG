"""Day-4 eval harness — runs the agent over the tiered set, scores each answer,
and reports per-tier + overall metrics. Provider-parametrized so the same run
drives the Claude vs Gemini A/B.

    uv run python -m finrag.eval.harness                  # full set, Claude
    uv run python -m finrag.eval.harness --smoke          # 1 per tier (cheap)
    uv run python -m finrag.eval.harness --tier factual   # one tier
    uv run python -m finrag.eval.harness --provider gemini --smoke   # A/B subset

The judge is always Claude (see metrics.py); only the *system under test* flips
with --provider, so the grader is held constant across the A/B.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from finrag.config import settings
from finrag.eval import dataset as ds
from finrag.eval import metrics as mt
from finrag.llm.base import format_chunks_for_prompt

REPO_ROOT = Path(__file__).resolve().parents[4]
OUT_DIR = REPO_ROOT / "data"

# Rough public list prices ($/M tokens) for an order-of-magnitude cost number.
# We only have the agent node's tokens (the plan call's usage isn't threaded
# into state), so this is a floor, labelled as such in the report.
_RATES = {"anthropic": (3.0, 15.0), "gemini": (0.10, 0.40), "local": (0.0, 0.0)}


@dataclass
class CaseResult:
    id: str
    tier: str
    question: str
    route_expected: str | None
    route_actual: str | None = None
    answer: str = ""
    n_chunks: int = 0
    n_tool_calls: int = 0
    latency_ms: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    # tier-specific scores (None when not applicable)
    correct: bool | None = None          # factual/multihop exact-match; honesty=declined
    route_match: bool | None = None
    citation_valid: bool | None = None   # narrative
    faithfulness: float | None = None
    relevance: float | None = None
    context_precision: float | None = None
    declined: bool | None = None         # honesty
    notes: list[str] = field(default_factory=list)
    error: str | None = None


def _grounding_context(final: dict) -> str:
    """Reconstruct what the agent was grounded on: retrieved chunks + every tool
    result it saw. This is the context the faithfulness judge scores against."""
    parts: list[str] = []
    chunks = final.get("chunks") or []
    if chunks:
        parts.append(format_chunks_for_prompt(chunks))
    for step in final.get("trace", []):
        if step.get("type") == "tool_call":
            d = step["data"]
            parts.append(f"[tool:{d['tool']}] args={d.get('args')} -> {d.get('result')}")
    return "\n\n".join(parts) if parts else "(no context — model answered without retrieval or tools)"


def _chunk_listing(chunks: list) -> str:
    lines = []
    for i, c in enumerate(chunks, 1):
        head = f"[{i}] {c.ticker} FY{c.fiscal_year} {getattr(c, 'section_title', '') or ''}".strip()
        lines.append(f"{head}: {c.text[:200]}")
    return "\n".join(lines)


def run_case(case: ds.EvalCase, *, judge: bool = True) -> CaseResult:
    from finrag.agent.graph import run_agent  # lazy: heavy import

    r = CaseResult(id=case.id, tier=case.tier, question=case.question,
                   route_expected=case.route_expected)
    t0 = time.perf_counter()
    try:
        final = run_agent(case.question)
    except Exception as e:  # a backend hiccup shouldn't abort the whole run
        r.error = f"{type(e).__name__}: {e}"
        r.latency_ms = int((time.perf_counter() - t0) * 1000)
        return r
    r.latency_ms = int((time.perf_counter() - t0) * 1000)

    r.answer = final.get("answer", "") or ""
    r.route_actual = final.get("route")
    r.route_match = (case.route_expected is None) or (r.route_actual == case.route_expected)
    chunks = final.get("chunks") or []
    r.n_chunks = len(chunks)
    r.n_tool_calls = sum(1 for s in final.get("trace", []) if s.get("type") == "tool_call")
    usage = final.get("usage") or {}
    r.input_tokens = usage.get("input_tokens", 0)
    r.output_tokens = usage.get("output_tokens", 0)

    # ── deterministic tier checks ──
    if case.tier == ds.HONESTY:
        declined = mt.looks_like_refusal(r.answer)
        if judge and not declined:  # keyword backstop missed — ask the judge
            verdict = mt.judge_refusal(case.question, r.answer)
            declined = bool(verdict.get("declined", False))
            if verdict.get("reason"):
                r.notes.append(f"refusal-judge: {verdict['reason']}")
        r.declined = declined
        r.correct = declined
    elif case.gt is not None:
        try:
            expected = case.gt()
            r.correct = mt.number_hit(expected, case.gt_kind, r.answer, case.tol)
            r.notes.append(f"expected≈{expected:.4g} ({case.gt_kind})")
        except Exception as e:
            r.notes.append(f"gt-error: {e}")
    elif case.expect_substring is not None:
        r.correct = case.expect_substring.lower() in r.answer.lower()
        r.notes.append(f"expect substring '{case.expect_substring}'")

    if case.tier == ds.NARRATIVE:
        valid, bad = mt.citation_validity(r.answer, r.n_chunks)
        r.citation_valid = valid
        if bad:
            r.notes.append(f"out-of-range citations: {bad}")

    # ── LLM-judge layer ──
    if judge and case.tier != ds.HONESTY:
        context = _grounding_context(final)
        f = mt.judge_faithfulness(context, r.answer)
        r.faithfulness = f.get("faithfulness")
        if f.get("unsupported_claims"):
            r.notes.append(f"unsupported: {f['unsupported_claims']}")
        rel = mt.judge_relevance(case.question, r.answer)
        r.relevance = rel.get("relevance")
        if chunks and case.tier in (ds.NARRATIVE, ds.MULTIHOP):
            p = mt.judge_precision(case.question, _chunk_listing(chunks))
            idxs = p.get("relevant_indices") or []
            if r.n_chunks:
                r.context_precision = len([i for i in idxs if 1 <= i <= r.n_chunks]) / r.n_chunks
    # Honesty tier is scored only by `declined`/accuracy — judging "relevance" of
    # a correct refusal is misleading (the judge penalizes not answering), so we
    # skip it.

    return r


# ── Aggregation ────────────────────────────────────────────────────────────
def _mean(vals: list[float | None]) -> float | None:
    nums = [v for v in vals if v is not None]
    return sum(nums) / len(nums) if nums else None


def _rate(vals: list[bool | None]) -> float | None:
    bs = [v for v in vals if v is not None]
    return sum(1 for v in bs if v) / len(bs) if bs else None


def aggregate(results: list[CaseResult]) -> dict:
    tiers: dict[str, list[CaseResult]] = {}
    for r in results:
        tiers.setdefault(r.tier, []).append(r)

    def block(rs: list[CaseResult]) -> dict:
        return {
            "n": len(rs),
            "errors": sum(1 for r in rs if r.error),
            "accuracy": _rate([r.correct for r in rs]),
            "route_match": _rate([r.route_match for r in rs]),
            "citation_valid": _rate([r.citation_valid for r in rs]),
            "faithfulness": _mean([r.faithfulness for r in rs]),
            "relevance": _mean([r.relevance for r in rs]),
            "context_precision": _mean([r.context_precision for r in rs]),
            "avg_latency_ms": int(_mean([float(r.latency_ms) for r in rs]) or 0),
        }

    return {
        "overall": block(results),
        "by_tier": {tier: block(rs) for tier, rs in sorted(tiers.items())},
    }


def _fmt(v) -> str:
    if v is None:
        return "  –  "
    if isinstance(v, float):
        return f"{v:5.2f}"
    return str(v)


def print_report(provider: str, results: list[CaseResult], agg: dict) -> None:
    in_tok = sum(r.input_tokens for r in results)
    out_tok = sum(r.output_tokens for r in results)
    ri, ro = _RATES.get(provider, (0, 0))
    cost = in_tok / 1e6 * ri + out_tok / 1e6 * ro

    print(f"\n{'='*78}\n  EVAL REPORT — provider={provider}  ({len(results)} cases)\n{'='*78}")
    print(f"  {'case':5} {'tier':9} {'ok':3} {'route':5} {'cite':4} {'faith':6} {'rel':6} {'prec':6} {'ms':6}")
    print(f"  {'-'*72}")
    for r in results:
        ok = "ERR" if r.error else ("✓" if r.correct else ("·" if r.correct is None else "✗"))
        print(f"  {r.id:5} {r.tier:9} {ok:3} "
              f"{('✓' if r.route_match else '✗') if r.route_match is not None else '–':5} "
              f"{('✓' if r.citation_valid else '✗') if r.citation_valid is not None else '–':4} "
              f"{_fmt(r.faithfulness):6} {_fmt(r.relevance):6} {_fmt(r.context_precision):6} {r.latency_ms:6}")

    print(f"\n  {'TIER':10} {'n':3} {'acc':6} {'route':6} {'cite':6} {'faith':6} {'rel':6} {'prec':6}")
    print(f"  {'-'*60}")
    for tier, b in agg["by_tier"].items():
        print(f"  {tier:10} {b['n']:3} {_fmt(b['accuracy']):6} {_fmt(b['route_match']):6} "
              f"{_fmt(b['citation_valid']):6} {_fmt(b['faithfulness']):6} "
              f"{_fmt(b['relevance']):6} {_fmt(b['context_precision']):6}")
    o = agg["overall"]
    print(f"  {'-'*60}")
    print(f"  {'OVERALL':10} {o['n']:3} {_fmt(o['accuracy']):6} {_fmt(o['route_match']):6} "
          f"{_fmt(o['citation_valid']):6} {_fmt(o['faithfulness']):6} "
          f"{_fmt(o['relevance']):6} {_fmt(o['context_precision']):6}")
    print(f"\n  errors={o['errors']}  agent-tokens in={in_tok} out={out_tok}  "
          f"approx-cost=${cost:.3f} (agent node only; excludes plan call)")
    # tokens/sec is the edge-relevant throughput number for the local model.
    # Derived from wall-clock latency (no separate decode timer), so it's a
    # coarse end-to-end rate, not a pure-decode tok/s.
    total_ms = sum(r.latency_ms for r in results)
    if provider == "local" and total_ms:
        print(f"  local throughput≈{out_tok / (total_ms / 1000):.1f} output tok/s "
              f"(end-to-end, over {total_ms/1000:.1f}s wall)\n")
    else:
        print()


def main() -> int:
    ap = argparse.ArgumentParser(description="FinRAG Day-4 eval harness")
    ap.add_argument("--provider", default=None, help="anthropic | gemini (default: current setting)")
    ap.add_argument("--tier", default=None, choices=[ds.FACTUAL, ds.NARRATIVE, ds.MULTIHOP, ds.HONESTY])
    ap.add_argument("--smoke", action="store_true", help="one case per tier (cheap pipeline check / A/B subset)")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--no-judge", action="store_true", help="skip LLM-judge metrics (deterministic only)")
    ap.add_argument("--out", default=None, help="results JSON path")
    args = ap.parse_args()

    if args.provider:
        settings.llm_provider = args.provider  # dispatcher reads this live
    provider = (settings.llm_provider or "anthropic").lower()

    if args.smoke:
        cases = ds.one_per_tier()
    else:
        cases = ds.cases_for(args.tier)
    if args.limit:
        cases = cases[: args.limit]

    print(f"Running {len(cases)} cases · provider={provider} · judge={not args.no_judge}")
    results: list[CaseResult] = []
    for i, case in enumerate(cases, 1):
        print(f"  [{i:2}/{len(cases)}] {case.id} {case.tier:9} {case.question[:54]}…", flush=True)
        r = run_case(case, judge=not args.no_judge)
        results.append(r)
        tag = "ERR" if r.error else ("✓" if r.correct else ("·" if r.correct is None else "✗"))
        print(f"        → {tag}  route={r.route_actual}  {r.latency_ms}ms", flush=True)
        if r.error:
            print(f"        ! {r.error}")

    agg = aggregate(results)
    print_report(provider, results, agg)

    out = Path(args.out) if args.out else OUT_DIR / f"eval_results_{provider}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    payload = {"provider": provider, "aggregate": agg, "cases": [asdict(r) for r in results]}
    out.write_text(json.dumps(payload, indent=2))
    print(f"  wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
