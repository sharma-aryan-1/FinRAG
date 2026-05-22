"""Fetch XBRL financial facts from SEC's Company Facts API and load into DuckDB.

Why XBRL and not HTML table parsing:
  SEC requires every filer to tag financial facts against the GAAP taxonomy.
  The Company Facts API serves these as JSON — already structured, already
  cross-filer-comparable. Parsing HTML tables ourselves would re-invent
  this work and produce worse data.

The result is a normalized `financial_facts` table that the agent (Day 3)
will query via a `sql_query` tool — the structured side of the
"structured + unstructured" fusion that's the project's headline
differentiator.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

import duckdb
import httpx

from finrag.ingestion.edgar import (
    HTTP_HEADERS,
    REQUEST_SLEEP_SECONDS,
    TARGET_TICKERS,
    TARGET_YEARS,
)

# ── Paths ─────────────────────────────────────────────────────────────────
# facts.py → ingestion/ → finrag/ → src/ → backend/ → ROOT
REPO_ROOT = Path(__file__).resolve().parents[4]
DUCKDB_PATH = REPO_ROOT / "data" / "duckdb" / "finrag.duckdb"

# ── HTTP ──────────────────────────────────────────────────────────────────
# Same User-Agent contract as the EDGAR scraper — SEC enforces it on this
# endpoint too. Reuse the headers module-level constant.
client = httpx.Client(headers=HTTP_HEADERS, timeout=30.0)


# ── Canonical line-item map ───────────────────────────────────────────────
# Each entry maps our canonical key (what the agent will query on) to one
# or more GAAP concept names. Multiple concepts per key absorb the
# inconsistency in how filers tag the same financial idea.
#
# Curated list — these are the high-value items for finance Q&A. Adding
# more is one line of code each; restraint is the design goal so the agent
# sees a tight, well-documented schema rather than an XBRL data dump.
CONCEPT_MAP: dict[str, list[str]] = {
    # Income statement
    "revenue": [
        "Revenues",
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "SalesRevenueNet",
    ],
    "cost_of_revenue": [
        "CostOfRevenue",
        "CostOfGoodsAndServicesSold",
        "CostOfGoodsSold",
    ],
    "gross_profit": ["GrossProfit"],
    "rd_expense": ["ResearchAndDevelopmentExpense"],
    "sga_expense": [
        "SellingGeneralAndAdministrativeExpense",
        "GeneralAndAdministrativeExpense",
    ],
    "operating_income": ["OperatingIncomeLoss"],
    "net_income": ["NetIncomeLoss"],
    # Balance sheet
    "total_assets": ["Assets"],
    "total_liabilities": ["Liabilities"],
    "stockholders_equity": ["StockholdersEquity"],
    "cash": ["CashAndCashEquivalentsAtCarryingValue", "Cash"],
    "long_term_debt": ["LongTermDebt", "LongTermDebtNoncurrent"],
    # Cash flow + capital
    "capex": ["PaymentsToAcquirePropertyPlantAndEquipment"],
    "operating_cash_flow": ["NetCashProvidedByUsedInOperatingActivities"],
    # Per-share
    "eps_basic": ["EarningsPerShareBasic"],
    "eps_diluted": ["EarningsPerShareDiluted"],
    # Banking-specific (for JPM)
    "net_interest_income": ["InterestIncomeOperating", "InterestAndDividendIncomeOperating"],
}

# Reverse lookup: gaap concept → canonical key
GAAP_TO_LINE_ITEM: dict[str, str] = {
    concept: key for key, concepts in CONCEPT_MAP.items() for concept in concepts
}


# ── Schema ────────────────────────────────────────────────────────────────
CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS financial_facts (
    ticker            TEXT    NOT NULL,
    company_name      TEXT    NOT NULL,
    cik               TEXT    NOT NULL,
    fiscal_year       INTEGER NOT NULL,
    fiscal_period     TEXT    NOT NULL,
    period_end_date   DATE    NOT NULL,
    line_item         TEXT    NOT NULL,
    gaap_concept      TEXT    NOT NULL,
    value             DOUBLE  NOT NULL,
    unit              TEXT    NOT NULL,
    accession_number  TEXT,
    form              TEXT,
    filed_date        DATE,
    PRIMARY KEY (ticker, fiscal_year, fiscal_period, line_item, gaap_concept)
);
"""


# ── SEC ticker → (cik, company_name) ──────────────────────────────────────
def _resolve_ticker_map() -> dict[str, tuple[str, str]]:
    """Same lookup as edgar.py — duplicated here so this module stands alone."""
    url = "https://www.sec.gov/files/company_tickers.json"
    response = client.get(url)
    response.raise_for_status()
    data = response.json()
    return {
        entry["ticker"].upper(): (str(entry["cik_str"]), entry["title"])
        for entry in data.values()
    }


# ── XBRL fetch ────────────────────────────────────────────────────────────
def fetch_company_facts(cik: str) -> dict[str, Any]:
    """Hit SEC's Company Facts API. One call returns everything XBRL-tagged
    for that filer across their entire filing history."""
    padded_cik = cik.zfill(10)
    url = f"https://data.sec.gov/api/xbrl/companyfacts/CIK{padded_cik}.json"
    response = client.get(url)
    response.raise_for_status()
    return response.json()


# ── Extract → flat rows ──────────────────────────────────────────────────
def extract_facts(
    company_data: dict[str, Any],
    ticker: str,
    target_years: list[int],
) -> list[dict[str, Any]]:
    """Walk the XBRL JSON, keep only concepts in CONCEPT_MAP and fiscal years
    in target_years, return a flat list of row dicts ready to insert.

    Deduplication: SEC's XBRL feed contains every restatement and amendment.
    The same (period, concept, unit) tuple can appear multiple times across
    successive filings. We keep the *most recently filed* value per logical
    fact — that's the canonical version (later restatements supersede
    earlier ones). The PK in `financial_facts` enforces single-version
    storage; this is where we collapse to it.

    The XBRL structure has multiple "units" per concept (USD, USD/shares,
    shares, etc.). We accept all units — the agent will filter by unit when
    semantically meaningful (e.g. only USD for revenue questions).
    """
    cik = str(company_data.get("cik", ""))
    company_name = company_data.get("entityName", "")
    facts_root = company_data.get("facts", {}).get("us-gaap", {})

    # Key includes `unit` because the same fact can legitimately be reported
    # in different units (e.g. EPS in 'USD/shares' vs 'USD'). The PK doesn't
    # have unit, so we'd later need to choose one unit per (period, concept).
    # In practice, our CONCEPT_MAP entries have one canonical unit each
    # (revenue → USD, eps_* → USD/shares), so this rarely collapses across
    # units in practice.
    best: dict[tuple[str, int, str, str, str, str], dict[str, Any]] = {}

    for gaap_concept, fact_block in facts_root.items():
        line_item = GAAP_TO_LINE_ITEM.get(gaap_concept)
        if line_item is None:
            continue

        for unit, datapoints in fact_block.get("units", {}).items():
            for dp in datapoints:
                fy = dp.get("fy")
                fp = dp.get("fp")
                if fy not in target_years or not fp:
                    continue

                end_str = dp.get("end")
                if not end_str:
                    continue

                filed_str = dp.get("filed")
                filed_date_val = date.fromisoformat(filed_str) if filed_str else None

                key = (ticker, fy, fp, line_item, gaap_concept, unit)
                existing = best.get(key)
                if existing is not None:
                    # Keep the most-recently-filed version. Treat missing
                    # filed_date as older than any concrete date.
                    existing_filed = existing["filed_date"]
                    if existing_filed and filed_date_val and filed_date_val <= existing_filed:
                        continue
                    if existing_filed and not filed_date_val:
                        continue

                best[key] = {
                    "ticker": ticker,
                    "company_name": company_name,
                    "cik": cik,
                    "fiscal_year": fy,
                    "fiscal_period": fp,
                    "period_end_date": date.fromisoformat(end_str),
                    "line_item": line_item,
                    "gaap_concept": gaap_concept,
                    "value": float(dp["val"]),
                    "unit": unit,
                    "accession_number": dp.get("accn"),
                    "form": dp.get("form"),
                    "filed_date": filed_date_val,
                }

    # Now also collapse the unit dimension. The PK in financial_facts is
    # (ticker, fy, fp, line_item, gaap_concept) — no unit. Pick the most
    # recently filed unit; ties broken by lexicographic unit name (stable).
    by_pk: dict[tuple[str, int, str, str, str], dict[str, Any]] = {}
    for (t, fy, fp, li, gc, _unit), row in best.items():
        pk = (t, fy, fp, li, gc)
        existing = by_pk.get(pk)
        if existing is None:
            by_pk[pk] = row
            continue
        ex_filed = existing["filed_date"]
        new_filed = row["filed_date"]
        if new_filed and (not ex_filed or new_filed > ex_filed):
            by_pk[pk] = row
        elif new_filed == ex_filed and row["unit"] < existing["unit"]:
            by_pk[pk] = row

    return list(by_pk.values())


# ── DuckDB write ──────────────────────────────────────────────────────────
def _ensure_db() -> duckdb.DuckDBPyConnection:
    DUCKDB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(DUCKDB_PATH))
    con.execute(CREATE_TABLE_SQL)
    return con


def upsert_facts(con: duckdb.DuckDBPyConnection, rows: list[dict[str, Any]]) -> int:
    """Idempotent insert: rows with matching primary key get replaced.

    DuckDB doesn't have native INSERT ON CONFLICT REPLACE for all cases, so
    we DELETE-then-INSERT inside a transaction. At our scale (~hundreds of
    rows per company) this is fast and bulletproof.
    """
    if not rows:
        return 0
    con.begin()
    try:
        for r in rows:
            con.execute(
                """
                DELETE FROM financial_facts
                WHERE ticker = ?
                  AND fiscal_year = ?
                  AND fiscal_period = ?
                  AND line_item = ?
                  AND gaap_concept = ?
                """,
                [
                    r["ticker"],
                    r["fiscal_year"],
                    r["fiscal_period"],
                    r["line_item"],
                    r["gaap_concept"],
                ],
            )
        con.executemany(
            """
            INSERT INTO financial_facts (
                ticker, company_name, cik, fiscal_year, fiscal_period,
                period_end_date, line_item, gaap_concept, value, unit,
                accession_number, form, filed_date
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    r["ticker"],
                    r["company_name"],
                    r["cik"],
                    r["fiscal_year"],
                    r["fiscal_period"],
                    r["period_end_date"],
                    r["line_item"],
                    r["gaap_concept"],
                    r["value"],
                    r["unit"],
                    r["accession_number"],
                    r["form"],
                    r["filed_date"],
                )
                for r in rows
            ],
        )
        con.commit()
    except Exception:
        con.rollback()
        raise
    return len(rows)


# ── Query interface (for sanity + future agent tool) ─────────────────────
def query(sql: str, params: list[Any] | None = None) -> list[dict[str, Any]]:
    """Read-only DuckDB query helper. Returns rows as list of dicts.

    Day 3's agent tool will be a thin wrapper around this with safety
    guards (READ ONLY connection, LIMIT enforcement, query timeout).
    """
    con = duckdb.connect(str(DUCKDB_PATH), read_only=True)
    try:
        result = con.execute(sql, params or []).fetchall()
        cols = [d[0] for d in con.description]
        return [dict(zip(cols, row)) for row in result]
    finally:
        con.close()


# ── CLI ───────────────────────────────────────────────────────────────────
def main() -> None:
    print(f"DuckDB at: {DUCKDB_PATH}")
    con = _ensure_db()

    import time

    ticker_map = _resolve_ticker_map()
    total_rows = 0

    for ticker in TARGET_TICKERS:
        cik, _ = ticker_map[ticker]
        print(f"\n[{ticker}] fetching XBRL company facts (CIK {cik})…")
        try:
            data = fetch_company_facts(cik)
        except httpx.HTTPStatusError as e:
            print(f"  ✗ {e}")
            continue

        rows = extract_facts(data, ticker, TARGET_YEARS)
        written = upsert_facts(con, rows)
        total_rows += written
        print(f"  ↳ {written} fact rows written")
        time.sleep(REQUEST_SLEEP_SECONDS)

    con.close()
    print(f"\nDone. {total_rows} total fact rows.\n")

    # Sanity-check queries — actual demonstrations of the modal-split value.
    print("=" * 60)
    print("Sample queries")
    print("=" * 60)

    examples = [
        (
            "Apple's revenue, FY 2022–2024",
            """
            SELECT fiscal_year, fiscal_period, value/1e9 AS billions_usd, gaap_concept
            FROM financial_facts
            WHERE ticker = 'AAPL'
              AND line_item = 'revenue'
              AND fiscal_period = 'FY'
              AND unit = 'USD'
            ORDER BY fiscal_year;
            """,
        ),
        (
            "Tesla R&D spend, FY 2022–2024",
            """
            SELECT fiscal_year, value/1e9 AS billions_usd
            FROM financial_facts
            WHERE ticker = 'TSLA'
              AND line_item = 'rd_expense'
              AND fiscal_period = 'FY'
            ORDER BY fiscal_year;
            """,
        ),
        (
            "Operating margin by company, FY 2023",
            """
            WITH p AS (
              SELECT ticker, line_item, SUM(value) AS v
              FROM financial_facts
              WHERE fiscal_year = 2023 AND fiscal_period = 'FY'
                AND line_item IN ('revenue', 'operating_income')
                AND unit = 'USD'
              GROUP BY ticker, line_item
            )
            SELECT
              ticker,
              MAX(CASE WHEN line_item='revenue' THEN v END)/1e9 AS revenue_b,
              MAX(CASE WHEN line_item='operating_income' THEN v END)/1e9 AS op_inc_b,
              MAX(CASE WHEN line_item='operating_income' THEN v END) * 1.0
                / NULLIF(MAX(CASE WHEN line_item='revenue' THEN v END), 0) AS op_margin
            FROM p
            GROUP BY ticker
            ORDER BY op_margin DESC NULLS LAST;
            """,
        ),
    ]

    for title, sql in examples:
        print(f"\n  ▸ {title}")
        rows = query(sql)
        if not rows:
            print("    (no rows)")
            continue
        for r in rows:
            print("    ", {k: (round(v, 3) if isinstance(v, float) else v) for k, v in r.items()})


if __name__ == "__main__":
    main()
