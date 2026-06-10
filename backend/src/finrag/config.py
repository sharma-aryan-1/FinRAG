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
    # Which backend the finrag.llm dispatchers use: "anthropic" | "gemini".
    llm_provider: str = "anthropic"

    model_config = SettingsConfigDict(env_file=REPO_ROOT / ".env", extra="ignore")

settings = Settings()  # validates at import time