# FinRAG backend image — portable across hosts (Hugging Face Spaces, Render,
# Cloud Run, local). Build context = repo ROOT (it needs backend/ + data/):
#   docker build -t finrag-api .
#
# Image layout mirrors the dev tree so REPO_ROOT resolves identically:
#   config.py at /app/backend/src/finrag/config.py → parents[3] == /app
#   → DuckDB + BM25 (REPO_ROOT/data/...) live at /app/data. WORKDIR=/app keeps
#   settings.duckdb_path ("./data/...") valid too.
#
# Port: listens on $PORT if the host injects one (Render/Cloud Run), else 8000.
# On Hugging Face Spaces, declare `app_port: 8000` in the Space README frontmatter.

# ---- builder: resolve + install deps into a venv (no ingestion/dev groups) ----
FROM python:3.11-slim AS builder
COPY --from=ghcr.io/astral-sh/uv:0.11.15 /uv /uvx /bin/
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
WORKDIR /app/backend
COPY backend/pyproject.toml backend/uv.lock ./
RUN uv sync --frozen --no-install-project --no-dev
COPY backend/ ./
RUN uv sync --frozen --no-dev

# ---- runtime: slim, non-root ----
FROM python:3.11-slim AS runtime
# libgomp1 covers numpy/rank-bm25's OpenMP dependency on slim.
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 \
    && rm -rf /var/lib/apt/lists/*
RUN useradd -m -u 1000 appuser
WORKDIR /app

# Installed venv + editable project source (finrag → backend/src), then the two
# runtime data artifacts (Qdrant vectors live in the cloud cluster, not here).
COPY --from=builder --chown=appuser:appuser /app/backend /app/backend
COPY --chown=appuser:appuser data/duckdb /app/data/duckdb
COPY --chown=appuser:appuser data/bm25_index.pkl /app/data/bm25_index.pkl

ENV PATH="/app/backend/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONIOENCODING=utf-8 \
    PORT=8000
USER appuser
EXPOSE 8000

# Healthcheck honors $PORT so it stays correct on any host.
HEALTHCHECK --interval=30s --timeout=5s --start-period=25s --retries=3 \
  CMD python -c "import os,sys,urllib.request; p=os.environ.get('PORT','8000'); sys.exit(0 if urllib.request.urlopen(f'http://localhost:{p}/health',timeout=3).status==200 else 1)"

# Shell form so ${PORT} expands at runtime.
CMD ["sh", "-c", "uvicorn finrag.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
