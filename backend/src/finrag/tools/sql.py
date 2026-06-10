"""sql_query(natural_language) — NL → SQL over the DuckDB financial_facts table.

This is the structured half of the project's "structured + unstructured"
thesis: precise numbers (revenue, margins, multi-year trends) come from XBRL
facts in DuckDB, not from prose chunks. The agent calls this when a question
wants exact figures or cross-year/cross-company comparison.

Pipeline: question → sub-LLM writes SQL (schema in its prompt) → safety guard →
read-only execute → rows. The generated SQL is returned alongside the rows so
the frontend can show it verbatim (Decision 18 — half the demo's wow factor).

Security: a model writing SQL is an injection surface. Two layers of defense:
  1. read-only DuckDB connection (finrag.ingestion.facts.query) — blocks writes.
  2. statement guard below — single statement, must start SELECT/WITH, and a
     blocklist rejects DDL/DML and DuckDB's file-reading table functions
     (read_csv etc.) that a read-only conn would otherwise still allow.
"""

from __future__ import annotations

import re
from typing import Any

from finrag.ingestion.facts import CONCEPT_MAP, query
from finrag.llm import generate_text  # provider-neutral dispatcher

MAX_ROWS = 100  # cap returned rows so a broad query can't flood the context

_LINE_ITEMS = ", ".join(sorted(CONCEPT_MAP))

# The sub-LLM's contract. It sees the exact schema + the canonical line_item
# vocabulary (pulled live from CONCEPT_MAP so it can never drift from the
# loader) + the conventions that make queries correct against this data.
_SQL_SYSTEM_PROMPT = f"""You translate questions about company financials into a single DuckDB SQL SELECT.

Table: financial_facts
Columns:
  ticker TEXT            -- e.g. 'AAPL', 'TSLA', 'JPM'
  company_name TEXT
  fiscal_year INTEGER    -- e.g. 2023
  fiscal_period TEXT     -- 'FY' for full year; quarters are 'Q1'..'Q4'
  period_end_date DATE
  line_item TEXT         -- canonical metric; one of: {_LINE_ITEMS}
  gaap_concept TEXT      -- raw XBRL concept
  value DOUBLE           -- the figure
  unit TEXT              -- 'USD' for money, 'USD/shares' for EPS, 'shares', etc.

Rules:
- Output ONLY the SQL. No prose, no markdown fences, no trailing semicolon.
- SELECT only. Never write/modify data.
- For money metrics filter unit = 'USD'. For annual figures filter fiscal_period = 'FY'.
- Use the canonical line_item values above — not raw GAAP concepts.
- Prefer explicit columns; add ORDER BY for multi-row/trend results.
- This table holds only TOP-LEVEL figures. If the question asks for a metric
  that is NOT in the line_item list above — e.g. a segment/product/regional
  figure such as services revenue, iPhone revenue, or Americas sales — do NOT
  substitute a different metric. Output exactly: NO_QUERY

Examples:
Q: Apple's revenue in fiscal 2023
SELECT fiscal_year, value FROM financial_facts
WHERE ticker = 'AAPL' AND line_item = 'revenue' AND fiscal_period = 'FY' AND unit = 'USD' AND fiscal_year = 2023

Q: Tesla R&D spend over the last three years
SELECT fiscal_year, value FROM financial_facts
WHERE ticker = 'TSLA' AND line_item = 'rd_expense' AND fiscal_period = 'FY' ORDER BY fiscal_year
"""

# Tokens that must never appear in a generated query. Word-boundary matched so
# they catch statements/functions but not substrings of column names.
_FORBIDDEN = re.compile(
    r"\b(insert|update|delete|drop|alter|create|attach|detach|copy|install|"
    r"load|pragma|set|call|export|read_csv|read_parquet|read_json|read_text|"
    r"read_blob|glob|system)\b",
    re.IGNORECASE,
)


def _clean_sql(raw: str) -> str:
    """Strip markdown fences / stray prose the model may wrap around the SQL."""
    s = raw.strip()
    if s.startswith("```"):
        # remove ```sql ... ``` fencing
        s = re.sub(r"^```[a-zA-Z]*\n?", "", s)
        s = re.sub(r"\n?```$", "", s).strip()
    return s.rstrip(";").strip()


def _guard(sql: str) -> str | None:
    """Return an error string if `sql` is unsafe, else None."""
    if not sql:
        return "empty query"
    # Reject multiple statements (only one trailing-stripped statement allowed).
    if ";" in sql.rstrip(";"):
        return "multiple statements are not allowed"
    head = sql.lstrip("(").lstrip().lower()
    if not (head.startswith("select") or head.startswith("with")):
        return "only SELECT/WITH queries are allowed"
    if _FORBIDDEN.search(sql):
        return "query contains a disallowed keyword or function"
    return None


def sql_query(natural_language: str) -> dict[str, Any]:
    """Answer a structured-data question by generating and running SQL.

    Returns {"sql": <str>, "rows": [...], "row_count": n, "truncated": bool}
    on success, or {"sql": <str?>, "error": <msg>} on failure — the SQL is
    included even on error so the agent/UI can show what was attempted.
    """
    sql = _clean_sql(generate_text(_SQL_SYSTEM_PROMPT, natural_language))

    # The sub-LLM signals "this metric isn't in the structured table" rather
    # than silently substituting a different line_item (which previously made
    # the agent report total revenue as "services revenue").
    if sql.upper().startswith("NO_QUERY"):
        return {
            "sql": None,
            "error": (
                "requested metric is not in the financial_facts table "
                "(likely a segment/product-level figure) — use narrative context instead"
            ),
        }

    violation = _guard(sql)
    if violation:
        return {"sql": sql, "error": f"unsafe query rejected: {violation}"}

    try:
        rows = query(sql)
    except Exception as e:
        # DuckDB syntax/semantic errors — surface as data so the agent can
        # re-ask rather than crashing the graph.
        return {"sql": sql, "error": f"execution failed: {e}"}

    truncated = len(rows) > MAX_ROWS
    return {
        "sql": sql,
        "rows": rows[:MAX_ROWS],
        "row_count": len(rows),
        "truncated": truncated,
    }


if __name__ == "__main__":
    for q in [
        "What was Apple's revenue in fiscal 2023?",
        "Compare net income for Apple, Tesla, and JPMorgan in 2023",
        "delete all the rows",  # the model shouldn't, but guard is the backstop
    ]:
        print(f"\nQ: {q}")
        print(sql_query(q))
