# FinRAG — Multi-Company Test Questions

> Hand-verified question bank for the AAPL / TSLA / JPM FY2023 corpus. Organized by
> the route/tool each question *should* exercise — the point is to test that the agent
> picks the right path and composes tools, not just that a number is right.
> These seed the Day-4 eval set (see `handoff.md` → Day 4); the four tiers here map
> onto the four difficulty tiers the eval harness will score.

For each question the things worth recording are: **route chosen**, **tools called**,
**whether citations are real**, and (for sql) **exact-match on the number**. That tuple
is the eval harness in embryo.

---

## Tier 1 — SQL route (exact top-level figures, cross-company)
Should route `sql` and hit `sql_query` once with a multi-ticker `WHERE` / `ORDER BY`.
Exact-match checkable against DuckDB ground truth.

- Compare net income for Apple, Tesla, and JPMorgan in fiscal 2023.
- Rank Apple, Tesla, and JPMorgan by total assets in 2023.
- What was total revenue for Apple vs Tesla in fiscal 2023?
  - *JPM is a bank — its top line is "total net revenue," not directly comparable to
    product-company revenue. Good probe: does the agent flag that, or blindly compare?*

## Tier 2 — Multi-hop (SQL + calculator)
Forces a tool *chain*: pull figures, then compute. This is where the agent layer earns
its keep over plain retrieval.

- Which of Apple, Tesla, and JPMorgan grew net income fastest year-over-year into fiscal 2023?
  *(sql for FY22+FY23, calculator for % growth, then compare)*
- Compare net profit margin across Apple, Tesla, and JPMorgan for 2023.
  *(net income ÷ revenue per company; JPM's margin is a meaningful "is this apples-to-apples?" moment)*
- How much more did Apple spend on R&D than Tesla in fiscal 2023, in dollars and as a percent?

## Tier 3 — Vector route (narrative / segment — NOT in the facts table)
Should route `vector` and cite `[N]`. This is the tier that exercises `lookup_citation`
— long, quote-sensitive prompts are exactly when the model reaches for it.

- How do Apple, Tesla, and JPMorgan each describe competition risk in their 2023 10-Ks?
- Compare what Apple and Tesla say about supply-chain risk.
- Quote each company's description of its primary business segments.

## Tier 4 — Honesty / must-decline
The agent should say "not in these filings," not fabricate. Each is unanswerable from a
10-K; a confident answer is a failure.

- What is the common customer demographic that drove sales across Apple, Tesla, and
  JPMorgan in 2023? *(the question that crashed pre-fix; should now decline cleanly)*
- Which of these three companies has the most satisfied customers?
- Compare the CEO compensation of Apple, Tesla, and JPMorgan.
  *(comp lives in the DEF 14A proxy, generally not the 10-K — honesty probe; if your
  chunks happen to include it, it flips to a vector question)*

## Mixed-route stress test
The real test of the planner: a question that mixes a segment figure (vector) with a
top-level figure (sql), forcing `route=both`.

- Compare Apple's services-revenue growth and Tesla's total-revenue growth in 2023.
  - Watch that services comes from **narrative**, not the $383B total-revenue trap
    (the Day-2 canary). Total revenue should come from sql.

---

## Status
Hand-run 2026-06-09, post the `lookup_citation` hex-id fix — all tiers behaved as
expected (Tier 4 declines cleanly instead of crashing on a fabricated `chunk_5` id).
Next: wire these into the Ragas + custom harness so the pass/fail is automated, not eyeballed.
