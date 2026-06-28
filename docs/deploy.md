# FinRAG — Public Deploy Runbook (Hugging Face Spaces)

A cost-capped public demo: **Vercel** (frontend) → **Hugging Face Space** (Docker backend), with **all retrieval data baked into the image** (embedded Qdrant + DuckDB + BM25). The agent runs on **Claude Haiku 4.5** behind guardrails (per-IP rate limit + a global daily question cap) so a public URL can't run up the Anthropic bill.

> Why HF Spaces: free **16GB RAM** (no OOM — the agent loads the vector store + BM25 index + clients comfortably), builds from the same `Dockerfile`, and runs a **single replica** so the in-process daily cap stays a true global ceiling. Sleeps after ~48h idle, wakes on the next visit.

```
 Browser ──► Vercel (Next.js, NEXT_PUBLIC_API_BASE) ──► HF Space (FastAPI + guardrails)
                                                          ├─► Embedded Qdrant + DuckDB + BM25 (in image)
                                                          └─► Anthropic (Haiku) / Cohere (query embed)
```

## Architecture facts that matter
- **The daily cap is in-process** (`finrag/guardrails.py`). It's a true global ceiling because a free HF Space runs **one replica** — keep it that way (don't add replicas).
- **Runtime data is fully self-contained** = embedded Qdrant (`data/qdrant/`) + `data/duckdb/finrag.duckdb` + `data/bm25_index.pkl`, all baked into the image. No external vector cluster, so nothing idle-wipes. The raw/processed corpus is *not* shipped.
- **Secrets never touch the repo** — they're HF Space *Secrets* (runtime env vars). The image build excludes `.env` via `.dockerignore`.
- **Image is host-portable.** The root `Dockerfile` listens on `$PORT` (Render/Cloud Run) or 8000 (HF, via `app_port`). Nothing here is HF-locked.

---

## Prerequisites
- Accounts: [Hugging Face](https://huggingface.co), [Vercel](https://vercel.com) (both free). No Qdrant Cloud account needed — the vector store is embedded.
- Local tools: `git` + **`git-lfs`** (`git lfs install` — needed for the BM25 pickle + DuckDB binaries), `vercel` CLI (`npm i -g vercel`).
- A funded **Anthropic** key and a **Cohere** key.
- The data layer already built (`data/duckdb/finrag.duckdb`, `data/bm25_index.pkl` present).

---

## Step 1 — Build the embedded vector store (baked into the image)

The vectors ship **inside the image** as an on-disk Qdrant store (`data/qdrant/`),
alongside DuckDB + BM25. No external cluster, so nothing can idle-wipe the demo and
there's no `QDRANT_URL`/`QDRANT_API_KEY` secret to manage. Setting `QDRANT_PATH`
flips `make_qdrant_client()` (in `ingestion/embed.py`) from remote to embedded.

Build it once locally (re-runs Cohere embed over the corpus, ~**$0.09**, ~10–15 min):

```powershell
cd D:\FinRAG\backend
$env:QDRANT_PATH="./data/qdrant"     # resolves to D:\FinRAG\data\qdrant
uv run --no-sync python -m finrag.ingestion.embed
```

The run ends with `... contains 4011 total (embedded ./data/qdrant).` Confirm the
store exists at `D:\FinRAG\data\qdrant\` — the Dockerfile COPYs it into the image.

> Dev still works against a remote/local-server Qdrant: just **don't** set
> `QDRANT_PATH`, and it falls back to `QDRANT_URL` (the docker-compose cluster).

---

## Step 2 — Create the HF Space and push the backend

1. On HF: **New → Space** → SDK **Docker** → blank template → name it e.g. `finrag-api`. Note the URL: `https://huggingface.co/spaces/<user>/finrag-api`.
2. Clone the (empty) Space repo and copy in **only what the backend needs**:

```powershell
git clone https://huggingface.co/spaces/<user>/finrag-api
cd finrag-api

# From the FinRAG repo, copy: the Dockerfile, the backend package, the three data
# artifacts (DuckDB, BM25, embedded Qdrant store), the .dockerignore, and the
# Space README (NOT the GitHub README).
copy D:\FinRAG\Dockerfile            .
copy D:\FinRAG\.dockerignore         .
copy D:\FinRAG\docs\hf-space-README.md  README.md
robocopy D:\FinRAG\backend  backend  /E /XD .venv __pycache__ /XF "*.pyc"
robocopy D:\FinRAG\data\duckdb  data\duckdb  /E
copy D:\FinRAG\data\bm25_index.pkl   data\
robocopy D:\FinRAG\data\qdrant  data\qdrant  /E /XF ".lock"   # skip the runtime lock

# LFS for the binaries HF won't take as plain blobs: the BM25 pickle, the DuckDB
# file, and the embedded store's storage.sqlite (~48MB). meta.json stays plain text.
git lfs install
git lfs track "*.pkl" "*.duckdb" "*.sqlite"
git add .gitattributes .

git commit -m "FinRAG backend (embedded vector store)"
git push
```

3. HF starts building the image (watch the Space's **Logs/Build** tab). The build takes ~1–2 min. It will then fail to *start* until you set the secrets below — that's expected (the Cohere key is required at import).

> The Space's `README.md` frontmatter (`sdk: docker`, `app_port: 8000`) is what tells HF how to build/route. Don't overwrite it with the GitHub README.

---

## Step 3 — Set Space secrets + variables

Space → **Settings → Variables and secrets**:

**Secrets** (private, runtime env):
- `ANTHROPIC_API_KEY` = your Anthropic key
- `COHERE_API_KEY` = your Cohere key (still needed at runtime to embed the live query)

> No `QDRANT_URL`/`QDRANT_API_KEY` — the vector store is embedded in the image.
> `QDRANT_PATH=./data/qdrant` is already set in the Dockerfile, so there's nothing
> Qdrant-related to configure on the Space.

**Variables** (non-sensitive):
- `LLM_PROVIDER` = `anthropic`
- `CLAUDE_MODEL` = `claude-haiku-4-5-20251001`
- `RATE_LIMIT_PER_MIN` = `8`
- `DAILY_QUESTION_CAP` = `300`
- `ALLOWED_ORIGINS` = `https://localhost:3000` (placeholder; set the real Vercel URL in Step 5)

The Space restarts on each change. When it's up, your API is at **`https://<user>-finrag-api.hf.space`**:

```powershell
curl https://<user>-finrag-api.hf.space/health
# {status:ok, provider:anthropic, daily_cap:300, remaining_today:300, ...}
```

`/health` returning the cap fields = the backend is live with guardrails active.

---

## Step 4 — Frontend on Vercel

```powershell
cd D:\FinRAG\frontend
vercel
vercel env add NEXT_PUBLIC_API_BASE production   # paste https://<user>-finrag-api.hf.space
vercel --prod                                     # note the *.vercel.app URL
```

(Or in the dashboard: Root Directory = `frontend`, env `NEXT_PUBLIC_API_BASE=https://<user>-finrag-api.hf.space`.)

---

## Step 5 — Lock CORS to the frontend

Until now the backend only allows `localhost`. Point it at the live frontend:

- Space → Settings → Variables → set `ALLOWED_ORIGINS` = `https://<your-project>.vercel.app` (exact origin, no trailing slash). The Space restarts.

Multiple origins later (custom domain)? Comma-separate them.

---

## Step 6 — Smoke test the public path

```powershell
$api = "https://<user>-finrag-api.hf.space"
# the sql canary
curl -Method POST "$api/agent" -ContentType "application/json" `
  -Body '{"question":"What was Apple net income in fiscal 2023?"}'   # → $96,995,000,000

# rate limit trips after RATE_LIMIT_PER_MIN in a minute → 429 with Retry-After
1..10 | % { (iwr -Method POST "$api/agent" -ContentType "application/json" -Body '{"question":"hi"}' -SkipHttpErrorCheck).StatusCode }

curl "$api/health"   # used_today should have incremented
```

Then open the Vercel URL and run the three demo questions (`docs/demo.md`) through the UI — the streamed trace should work end-to-end.

---

## Cost monitoring (do once)
- **Anthropic console** → billing alert. Haiku agent-question ≈ a few tenths of a cent; the 300/day cap ≈ a couple dollars worst case.
- **Adjust the cap with no redeploy:** change the `DAILY_QUESTION_CAP` (or `RATE_LIMIT_PER_MIN`) Variable in Space Settings; it restarts and picks it up. `/health` is your live dashboard.
- **Kill switch:** Space → Settings → **Pause** the Space (or set `DAILY_QUESTION_CAP=0`).

## Known tradeoffs
- **Sleep / cold start:** a free Space sleeps after extended inactivity; the first request after wakes it (~tens of seconds). Acceptable for a demo.
- **Haiku vs Sonnet:** the public path uses Haiku for cost; the `docs/day4.md` eval numbers are on Sonnet, so the live demo is slightly weaker than the benchmark. Set `CLAUDE_MODEL` back to `claude-sonnet-4-6` (and lower the cap) to match the eval exactly.
- **Single replica:** required for the global cap *and* the embedded vector store — Qdrant's on-disk local mode locks to one process. Don't scale the Space up.
- **Embedded store concurrency:** the local Qdrant client is shared across request threads (one lru_cached instance per worker). Fine under the per-IP rate limit and demo-level traffic; it is *not* a high-concurrency store. Refreshing the vectors means rebuilding `data/qdrant/` (Step 1) and re-pushing — a deliberate, deploy-time action, which is exactly the point: nothing changes it at runtime, so nothing can wipe it.

## Other hosts (same image)
The root `Dockerfile` is host-agnostic (honors `$PORT`). Render: New → Web Service → from the Dockerfile, set the same env, deploy (free tier spins down on idle). Cloud Run: `gcloud run deploy --source .` with the same env (scales to zero). Fly: `fly.toml` is still in the repo (`dockerfile = "Dockerfile"`) if you add billing.
