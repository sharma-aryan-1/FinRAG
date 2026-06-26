"""Day-4 evaluation set — the questions that make the system *provable*.

Four tiers, each probing a distinct failure mode of an agentic RAG system:

  factual   → sql route. One exact top-level figure. We assert the number
              appears in the answer. Ground truth is read from our own
              `financial_facts` table (correct-by-construction), so this tier
              is really a regression check: does the agent route to sql and
              report the figure *faithfully*, or hallucinate / mis-label it
              (the exact bug that survived Day 2)?
  narrative → vector route. Qualitative content. No numeric ground truth;
              graded by the LLM judge on faithfulness + answer relevance, and
              by a deterministic check that the answer actually cites [N].
  multihop  → both + calculator. A figure that must be *computed* (YoY growth,
              margin) or a cross-company comparison. Tests retrieval + tool use
              + arithmetic end to end.
  honesty   → unanswerable from the corpus (a company/year we don't have, or a
              forward-looking number). The agent must DECLINE, not fabricate —
              the single most important behaviour for a finance assistant.

Ground truth for the numeric tiers is a `gt` callable resolved lazily against
DuckDB, so this module imports without touching the DB and the numbers can
never drift from what the agent can actually query.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from finrag.ingestion.facts import query

# ── Tiers ────────────────────────────────────────────────────────────────
FACTUAL = "factual"
NARRATIVE = "narrative"
MULTIHOP = "multihop"
HONESTY = "honesty"


@dataclass(frozen=True)
class EvalCase:
    id: str
    tier: str
    question: str
    # Soft expectation — the plan node's route. We record match/mismatch but a
    # mismatch isn't a hard failure (the agent can still answer correctly via a
    # different path).
    route_expected: str | None = None
    # Numeric ground truth, resolved lazily from the DB at run time.
    gt: Callable[[], float] | None = None
    gt_kind: str = "currency"  # currency | per_share | percent
    # Relative tolerance for currency/per_share; for percent it's the absolute
    # percentage-point floor (combined with a 5% relative band in the matcher).
    tol: float = 0.015
    # For name-style answers ("which company…") and any case where a substring
    # must appear (e.g. the right company name).
    expect_substring: str | None = None
    # Honesty tier: the agent must refuse / say it's not in the corpus.
    must_decline: bool = False


# ── Ground-truth helper ──────────────────────────────────────────────────
def _v(ticker: str, line_item: str, fy: int, unit: str = "USD") -> float:
    """The single authoritative value for a (ticker, line_item, year) from
    financial_facts — the same table sql_query reads. LIMIT 1 because each of
    our three filers resolves to one surviving GAAP concept per annual period
    (see facts.py dedup); ordering by value desc is a stable tie-break."""
    rows = query(
        """
        SELECT value FROM financial_facts
        WHERE ticker = ? AND line_item = ? AND fiscal_year = ?
          AND fiscal_period = 'FY' AND unit = ?
        ORDER BY value DESC LIMIT 1
        """,
        [ticker, line_item, fy, unit],
    )
    if not rows:
        raise LookupError(f"no fact for {ticker} {line_item} FY{fy} ({unit})")
    return float(rows[0]["value"])


def _yoy(ticker: str, line_item: str, y0: int, y1: int) -> float:
    """Year-over-year percent growth from y0 to y1."""
    a, b = _v(ticker, line_item, y0), _v(ticker, line_item, y1)
    return (b - a) / a * 100.0


def _margin(ticker: str, part: str, whole: str, fy: int) -> float:
    """A margin in percent (e.g. gross_profit / revenue)."""
    return _v(ticker, part, fy) / _v(ticker, whole, fy) * 100.0


# ── The set ──────────────────────────────────────────────────────────────
CASES: list[EvalCase] = [
    # ─────────── FACTUAL (sql route, exact figure) ───────────
    EvalCase("f01", FACTUAL, "What was Apple's net income in fiscal 2023?",
             route_expected="sql", gt=lambda: _v("AAPL", "net_income", 2023)),
    EvalCase("f02", FACTUAL, "What was Apple's total revenue in fiscal 2024?",
             route_expected="sql", gt=lambda: _v("AAPL", "revenue", 2024)),
    EvalCase("f03", FACTUAL, "How much did Tesla spend on research and development in fiscal 2023?",
             route_expected="sql", gt=lambda: _v("TSLA", "rd_expense", 2023)),
    EvalCase("f04", FACTUAL, "What were JPMorgan's total assets at the end of fiscal 2023?",
             route_expected="sql", gt=lambda: _v("JPM", "total_assets", 2023)),
    EvalCase("f05", FACTUAL, "What was Apple's operating income in fiscal 2022?",
             route_expected="sql", gt=lambda: _v("AAPL", "operating_income", 2022)),
    EvalCase("f06", FACTUAL, "What was Tesla's total revenue in fiscal 2024?",
             route_expected="sql", gt=lambda: _v("TSLA", "revenue", 2024)),
    EvalCase("f07", FACTUAL, "What was JPMorgan's net income in fiscal 2024?",
             route_expected="sql", gt=lambda: _v("JPM", "net_income", 2024)),
    EvalCase("f08", FACTUAL, "What was Apple's gross profit in fiscal 2023?",
             route_expected="sql", gt=lambda: _v("AAPL", "gross_profit", 2023)),
    EvalCase("f09", FACTUAL, "What was Tesla's diluted earnings per share in fiscal 2023?",
             route_expected="sql", gt=lambda: _v("TSLA", "eps_diluted", 2023, "USD/shares"),
             gt_kind="per_share"),
    EvalCase("f10", FACTUAL, "What was JPMorgan's net interest income in fiscal 2024?",
             route_expected="sql", gt=lambda: _v("JPM", "net_interest_income", 2024)),

    # ─────────── NARRATIVE (vector route, faithfulness + citation) ───────────
    EvalCase("n01", NARRATIVE, "How does Apple describe the risks to its supply chain in its 10-K?",
             route_expected="vector"),
    EvalCase("n02", NARRATIVE, "What does Tesla say about competition in the electric-vehicle market?",
             route_expected="vector"),
    EvalCase("n03", NARRATIVE, "How does JPMorgan describe credit risk in its filing?",
             route_expected="vector"),
    EvalCase("n04", NARRATIVE, "How does Apple characterize its Services business and what drives its growth?",
             route_expected="vector"),
    EvalCase("n05", NARRATIVE, "What risks does Tesla cite related to its dependence on key personnel?",
             route_expected="vector"),
    EvalCase("n06", NARRATIVE, "How does Apple describe foreign-currency exchange-rate risk?",
             route_expected="vector"),
    EvalCase("n07", NARRATIVE, "What does Tesla say about risks in ramping production and manufacturing?",
             route_expected="vector"),
    EvalCase("n08", NARRATIVE, "How does JPMorgan describe the regulatory and capital requirements it faces?",
             route_expected="vector"),

    # ─────────── MULTIHOP (sql + calculator; route left soft) ───────────
    # route_expected is None: each of these is answerable from structured facts
    # plus arithmetic, so the router legitimately picks `sql` over `both`. The
    # tier tests multi-step reasoning (fetch figure(s) → compute), not the route.
    EvalCase("m01", MULTIHOP, "By what percentage did Apple's net income change from fiscal 2022 to fiscal 2023?",
             gt=lambda: _yoy("AAPL", "net_income", 2022, 2023), gt_kind="percent"),
    EvalCase("m02", MULTIHOP, "By what percentage did Tesla's revenue grow from fiscal 2023 to fiscal 2024?",
             gt=lambda: _yoy("TSLA", "revenue", 2023, 2024), gt_kind="percent"),
    EvalCase("m03", MULTIHOP, "What was Apple's gross margin in fiscal 2023?",
             gt=lambda: _margin("AAPL", "gross_profit", "revenue", 2023), gt_kind="percent"),
    EvalCase("m04", MULTIHOP, "What was Tesla's net profit margin in fiscal 2023?",
             gt=lambda: _margin("TSLA", "net_income", "revenue", 2023), gt_kind="percent"),
    EvalCase("m05", MULTIHOP, "What was Apple's operating margin in fiscal 2024?",
             gt=lambda: _margin("AAPL", "operating_income", "revenue", 2024), gt_kind="percent"),
    EvalCase("m06", MULTIHOP, "Which of the three companies had the highest net income in fiscal 2023?",
             expect_substring="Apple"),
    EvalCase("m07", MULTIHOP, "By what percentage did JPMorgan's net income change from fiscal 2023 to fiscal 2024?",
             gt=lambda: _yoy("JPM", "net_income", 2023, 2024), gt_kind="percent"),

    # ─────────── HONESTY (must decline — outside the corpus) ───────────
    EvalCase("h01", HONESTY, "What was Microsoft's net income in fiscal 2023?",
             must_decline=True),
    EvalCase("h02", HONESTY, "What was Apple's total revenue in fiscal 2019?",
             must_decline=True),  # outside our FY2022–2024 span
    EvalCase("h03", HONESTY, "How many employees does Amazon have according to its 10-K?",
             must_decline=True),  # company not in corpus
    EvalCase("h04", HONESTY, "What will Tesla's revenue be in fiscal 2026?",
             must_decline=True),  # forward-looking, not in any filing
    EvalCase("h05", HONESTY, "What was Google's operating margin in fiscal 2023?",
             must_decline=True),  # company not in corpus
]


def cases_for(tier: str | None = None) -> list[EvalCase]:
    return [c for c in CASES if tier is None or c.tier == tier]


def one_per_tier() -> list[EvalCase]:
    """A 4-case smoke subset — first case of each tier. Used to validate the
    harness end to end before spending on a full run, and as the Gemini A/B
    subset that fits the free-tier daily quota."""
    out: list[EvalCase] = []
    for tier in (FACTUAL, NARRATIVE, MULTIHOP, HONESTY):
        out.append(cases_for(tier)[0])
    return out
