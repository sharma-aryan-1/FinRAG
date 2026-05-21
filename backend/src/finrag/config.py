# src/finrag/config.py
from pydantic_settings import BaseSettings, SettingsConfigDict
from pathlib import Path
REPO_ROOT = Path(__file__).resolve().parents[3]
# config.py → finrag/ → src/ → backend/ → REPO_ROOT

class Settings(BaseSettings):
    anthropic_api_key: str
    cohere_api_key: str
    qdrant_url: str = "http://localhost:6333"
    qdrant_api_key: str | None = None
    duckdb_path: str = "./data/duckdb/finrag.duckdb"
    llm_mode: str = "cloud"

    model_config = SettingsConfigDict(env_file=REPO_ROOT / ".env", extra="ignore")

settings = Settings()  # validates at import time