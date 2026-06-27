# src/finrag/config.py
from pydantic_settings import BaseSettings, SettingsConfigDict
from pathlib import Path
REPO_ROOT = Path(__file__).resolve().parents[3]
# config.py → finrag/ → src/ → backend/ → REPO_ROOT

class Settings(BaseSettings):
    # Both provider keys are optional: the active one is decided by
    # `llm_provider`, and we validate presence lazily at call time so a
    # Gemini-only setup doesn't need an Anthropic key (and vice versa).
    anthropic_api_key: str | None = None
    gemini_api_key: str | None = None
    cohere_api_key: str
    qdrant_url: str = "http://localhost:6333"
    qdrant_api_key: str | None = None
    duckdb_path: str = "./data/duckdb/finrag.duckdb"
    llm_mode: str = "cloud"
    # Which backend the finrag.llm dispatchers use: "anthropic" | "gemini" | "local".
    llm_provider: str = "anthropic"
    # Anthropic model. Default = the eval-validated Sonnet; the public demo deploy
    # overrides this to Haiku (CLAUDE_MODEL=claude-haiku-4-5-20251001) to cut the
    # per-question cost ~10x behind the guardrails below.
    claude_model: str = "claude-sonnet-4-6"

    # ── Public-deploy guardrails (only bite when a real client calls) ──
    # CORS allow-list, comma-separated. Dev defaults to the Next.js dev server;
    # the prod deploy sets this to the exact Vercel origin (see docs/deploy.md).
    allowed_origins: str = "http://localhost:3000,http://127.0.0.1:3000"
    # Per-IP sliding-window limit on the PAID endpoints (/answer,/agent[,/stream]).
    rate_limit_per_min: int = 8
    # Global hard ceiling on paid questions per UTC day — the cost circuit-breaker.
    # 300 Haiku agent-questions ≈ a couple of dollars worst case; past it the API
    # returns 429 until midnight UTC instead of burning the key.
    daily_question_cap: int = 300

    @property
    def allowed_origins_list(self) -> list[str]:
        return [o.strip() for o in self.allowed_origins.split(",") if o.strip()]

    # ── Local / edge provider (Ollama, OpenAI-compatible API on :11434) ──
    # Used only when llm_provider == "local". base_url is the OpenAI-compat
    # endpoint, so the same backend would target vLLM/llama.cpp/LM Studio by
    # swapping this one value. local_use_tools is the kill-switch: True runs the
    # real agentic tool-loop (tests whether a 3B model can drive it); False runs
    # degraded synthesis-only over retrieved context (the documented fallback for
    # small models whose tool-calling is unreliable — see docs/handoff.md Day 5).
    local_model: str = "llama3.2:3b"
    local_base_url: str = "http://localhost:11434/v1"
    local_use_tools: bool = True

    model_config = SettingsConfigDict(env_file=REPO_ROOT / ".env", extra="ignore")

settings = Settings()  # validates at import time