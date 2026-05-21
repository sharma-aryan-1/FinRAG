from fastapi import FastAPI
from finrag.config import settings

app = FastAPI(title="FinRAG")

@app.get("/health")
def health():
    return {
        "status": "ok",
        "llm_mode": settings.llm_mode,
    }

