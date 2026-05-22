"""End-to-end smoke test for retrieval.

Runs a fixed canary set of questions through `retrieval.vector.search` and
flags structural breakage (zero results, filter violations, suspiciously
low scores). Does NOT grade answer quality — that's Day 4's eval harness.

Usage:
    uv run python -m finrag.eval.smoke

Exits with code 0 on full pass, 1 if any case fails. Useful in CI later.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from finrag.retrieval.rerank import rerank_search
from finrag.retrieval.vector import RetrievedChunk

# parse.py-style root resolution
REPO_ROOT = Path(__file__).resolve().parents[4]
RESULTS_PATH = REPO_ROOT / "data" / "smoke_results.json"

# Smoke now hits rerank_search — same path as /query. Score is Cohere
# Rerank v3's relevance_score in [0, 1]. A relevant top-1 is typically
# > 0.5 for well-formed queries. < 0.1 means the reranker thinks none of
# the candidates actually answer the query — usually a sign of retrieval
# upstream returning irrelevant candidates.
SCORE_FLOOR = 0.10


# ── Canary cases ─────────────────────────────────────────────────────────
class Case(BaseModel):
    name: str
    question: str
    top_k: int = 5
    ticker: str | None = None
    fiscal_year: int | None = None
    chunk_type: str | None = None
    # Optional soft expectation — we don't fail the case if this doesn't
    # match, but we surface it in output so you eyeball whether the right
    # company is showing up in the top results.
    expect_ticker_in_top: str | None = None


CASES: list[Case] = [
    Case(
        name="01_aapl_services_revenue",
        question="How did Apple's services revenue change in 2023?",
        expect_ticker_in_top="AAPL",
    ),
    Case(
        name="02_tsla_rnd_spend",
        question="How much did Tesla spend on research and development?",
        expect_ticker_in_top="TSLA",
    ),
    Case(
        name="03_supply_chain_risks",
        question="What are the risks related to supply chain disruptions?",
    ),
    Case(
        name="04_jpm_net_interest_income",
        question="What was JPMorgan's net interest income?",
        expect_ticker_in_top="JPM",
    ),
    Case(
        name="05_aapl_2024_filter",
        question="total revenue",
        ticker="AAPL",
        fiscal_year=2024,
        expect_ticker_in_top="AAPL",
    ),
    Case(
        name="06_tables_only_filter",
        question="income statement",
        chunk_type="table",
    ),
    Case(
        name="07_tsla_deliveries_multi_year",
        question="How have Tesla vehicle deliveries changed year over year?",
        expect_ticker_in_top="TSLA",
        top_k=8,
    ),
    Case(
        name="08_cross_company_ai",
        question="risks related to artificial intelligence",
        top_k=8,
    ),
]


# ── Execution ─────────────────────────────────────────────────────────────
class CaseResult(BaseModel):
    name: str
    passed: bool
    reasons: list[str]
    n_chunks: int
    top_score: float | None
    top_ticker: str | None
    top_fiscal_year: int | None
    duration_ms: int


def _check_case(case: Case, chunks: list[RetrievedChunk]) -> CaseResult:
    """Apply pass/fail rules to a case's results."""
    reasons: list[str] = []

    if not chunks:
        reasons.append("returned zero chunks")
        return CaseResult(
            name=case.name,
            passed=False,
            reasons=reasons,
            n_chunks=0,
            top_score=None,
            top_ticker=None,
            top_fiscal_year=None,
            duration_ms=0,  # filled in by caller
        )

    top = chunks[0]

    # Score floor — catches embedder mismatches (e.g. wrong input_type).
    if top.score < SCORE_FLOOR:
        reasons.append(f"top score {top.score:.3f} below floor {SCORE_FLOOR}")

    # Filter compliance — every returned chunk must satisfy any filter we set.
    if case.ticker:
        bad = [c for c in chunks if c.ticker != case.ticker]
        if bad:
            reasons.append(
                f"ticker filter violated: {len(bad)}/{len(chunks)} chunks have "
                f"ticker != {case.ticker}"
            )
    if case.fiscal_year:
        bad = [c for c in chunks if c.fiscal_year != case.fiscal_year]
        if bad:
            reasons.append(
                f"fiscal_year filter violated: {len(bad)}/{len(chunks)} chunks have "
                f"fiscal_year != {case.fiscal_year}"
            )
    if case.chunk_type:
        bad = [c for c in chunks if c.chunk_type != case.chunk_type]
        if bad:
            reasons.append(
                f"chunk_type filter violated: {len(bad)}/{len(chunks)} chunks have "
                f"chunk_type != {case.chunk_type}"
            )

    # Soft expectation — log only, don't fail
    if case.expect_ticker_in_top:
        top_tickers = {c.ticker for c in chunks[:3]}
        if case.expect_ticker_in_top not in top_tickers:
            reasons.append(
                f"⚠ soft: expected {case.expect_ticker_in_top} in top-3 tickers, "
                f"got {sorted(top_tickers)}"
            )

    # Only hard failures (filter violations, empty results, score floor)
    # count toward `passed`. Soft warnings start with "⚠".
    hard_failures = [r for r in reasons if not r.startswith("⚠")]

    return CaseResult(
        name=case.name,
        passed=not hard_failures,
        reasons=reasons,
        n_chunks=len(chunks),
        top_score=top.score,
        top_ticker=top.ticker,
        top_fiscal_year=top.fiscal_year,
        duration_ms=0,
    )


def run_case(case: Case) -> CaseResult:
    t0 = time.perf_counter()
    chunks = rerank_search(
        question=case.question,
        top_k=case.top_k,
        ticker=case.ticker,
        fiscal_year=case.fiscal_year,
        chunk_type=case.chunk_type,
    )
    elapsed_ms = int((time.perf_counter() - t0) * 1000)
    result = _check_case(case, chunks)
    result.duration_ms = elapsed_ms
    return result


# ── CLI ───────────────────────────────────────────────────────────────────
def main() -> int:
    print(f"Running {len(CASES)} smoke cases against retrieval.search\n")

    results: list[CaseResult] = []
    for case in CASES:
        r = run_case(case)
        results.append(r)

        status = "PASS" if r.passed else "FAIL"
        top = f"{r.top_ticker} FY{r.top_fiscal_year} @ {r.top_score:.3f}" if r.top_score else "—"
        print(f"  [{status}] {r.name:35s}  n={r.n_chunks}  top={top:24s}  {r.duration_ms}ms")
        for reason in r.reasons:
            print(f"          {reason}")

    n_pass = sum(1 for r in results if r.passed)
    n_total = len(results)
    print(f"\n{n_pass}/{n_total} cases passed.")

    # Persist results for future diffing / regression tracking
    payload: dict[str, Any] = {
        "summary": {
            "passed": n_pass,
            "total": n_total,
            "all_passed": n_pass == n_total,
        },
        "cases": [r.model_dump() for r in results],
    }
    RESULTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    RESULTS_PATH.write_text(json.dumps(payload, indent=2))
    print(f"Wrote {RESULTS_PATH}")

    return 0 if n_pass == n_total else 1


if __name__ == "__main__":
    sys.exit(main())
