---
title: FinRAG API
emoji: 📊
colorFrom: blue
colorTo: indigo
sdk: docker
app_port: 8000
pinned: false
short_description: Agentic RAG over SEC 10-K filings — cost-capped public demo backend
---

# FinRAG — backend (Hugging Face Space)

This Space runs the **FinRAG** FastAPI backend as a Docker container. The chat
frontend is hosted separately (Vercel) and calls this Space's URL. Full source,
architecture, and the evaluation writeup live in the GitHub repo.

**Endpoints:** `/health` · `/query` · `/answer` · `/agent` · `/agent/stream`

**Guardrails are active** (this is a public demo on a funded key): a per-IP rate
limit and a global daily question cap (the agent runs on Claude Haiku). Hit
`/health` to see the remaining daily quota. For unlimited use, run it locally —
see the GitHub README.

> NOTE: this file is the **Space's** README (HF reads the frontmatter above for
> the Docker SDK + port). It is intentionally separate from the GitHub repo's
> README. Copy it to the Space repo root as `README.md`.
