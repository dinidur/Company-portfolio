"""
Environment variables & app settings.

Everything is read from .env once, at import time. I kept it as a plain class
(not pydantic-settings) so it is easy to read and easy to override in tests.
"""
import os
from pathlib import Path

import yaml
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


def _bool(name: str, default: bool = False) -> bool:
    return os.getenv(name, str(default)).strip().lower() in ("1", "true", "yes", "on")


class Settings:
    # ---- App ----
    APP_NAME = "Nila - Enterprise Knowledge Assistant"
    ENV = os.getenv("APP_ENV", "dev")
    API_URL = os.getenv("API_URL", "http://localhost:8000")

    # ---- LLM ----
    # gemini = free tier, ollama = local (same as SupermarketAI)
    LLM_PROVIDER = os.getenv("LLM_PROVIDER", "gemini")
    LLM_FALLBACK_PROVIDER = os.getenv("LLM_FALLBACK_PROVIDER", "ollama")
    GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY", "")
    GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
    OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
    OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "gemma4")
    LLM_TEMPERATURE = float(os.getenv("LLM_TEMPERATURE", "0.2"))
    LLM_TIMEOUT_SECONDS = float(os.getenv("LLM_TIMEOUT_SECONDS", "60"))

    # ---- Embeddings (local, free) ----
    EMBEDDING_BACKEND = os.getenv("EMBEDDING_BACKEND", "fastembed")  # fastembed | hashing (offline tests)
    DENSE_MODEL = os.getenv("DENSE_MODEL", "BAAI/bge-small-en-v1.5")
    DENSE_DIM = int(os.getenv("DENSE_DIM", "384"))
    SPARSE_MODEL = os.getenv("SPARSE_MODEL", "Qdrant/bm25")
    RERANK_MODEL = os.getenv("RERANK_MODEL", "Xenova/ms-marco-MiniLM-L-6-v2")
    RERANK_ENABLED = _bool("RERANK_ENABLED", True)

    # ---- Pinecone ----
    PINECONE_API_KEY = os.getenv("PINECONE_API_KEY", "")
    PINECONE_INDEX = os.getenv("PINECONE_INDEX", "enterprise-kb")
    PINECONE_CLOUD = os.getenv("PINECONE_CLOUD", "aws")
    PINECONE_REGION = os.getenv("PINECONE_REGION", "us-east-1")

    # ---- Retrieval ----
    HYBRID_ALPHA = float(os.getenv("HYBRID_ALPHA", "0.6"))  # 1.0 = only dense, 0.0 = only sparse
    TOP_K = int(os.getenv("TOP_K", "6"))
    CANDIDATE_K = int(os.getenv("CANDIDATE_K", "15"))  # fetch more, then rerank down to TOP_K
    CHUNK_SIZE = int(os.getenv("CHUNK_SIZE", "900"))
    CHUNK_OVERLAP = int(os.getenv("CHUNK_OVERLAP", "120"))

    # ---- RLM (research agent) ----
    RLM_MAX_DEPTH = int(os.getenv("RLM_MAX_DEPTH", "2"))
    RLM_BATCH_SIZE = int(os.getenv("RLM_BATCH_SIZE", "3"))
    RLM_MAX_CHARS_PER_CALL = int(os.getenv("RLM_MAX_CHARS_PER_CALL", "2000"))
    RLM_CONCURRENCY = int(os.getenv("RLM_CONCURRENCY", "3"))

    # ---- MCP ----
    MCP_SERVER_URL = os.getenv("MCP_SERVER_URL", "http://localhost:8001/mcp")
    TOOL_TIMEOUT_SECONDS = float(os.getenv("TOOL_TIMEOUT_SECONDS", "15"))

    # ---- Security ----
    JWT_SECRET = os.getenv("JWT_SECRET", "dev-only-secret-change-me-in-the-env-file")
    JWT_EXPIRE_MINUTES = int(os.getenv("JWT_EXPIRE_MINUTES", "240"))
    MAX_QUERY_CHARS = int(os.getenv("MAX_QUERY_CHARS", "2000"))

    # ---- Rate limit (token bucket) ----
    RATE_LIMIT_CAPACITY = int(os.getenv("RATE_LIMIT_CAPACITY", "10"))
    RATE_LIMIT_REFILL_PER_SEC = float(os.getenv("RATE_LIMIT_REFILL_PER_SEC", "0.2"))

    # ---- Memory ----
    SHORT_TERM_TURNS = int(os.getenv("SHORT_TERM_TURNS", "6"))
    SQLITE_PATH = os.getenv("SQLITE_PATH", str(BASE_DIR / "data" / "app.db"))

    # ---- Observability ----
    LANGSMITH_TRACING = _bool("LANGSMITH_TRACING", False)
    LANGSMITH_PROJECT = os.getenv("LANGSMITH_PROJECT", "enterprise-ai-assistant")

    # ---- Paths ----
    DOCS_DIR = BASE_DIR / "data" / "documents"
    CHUNKS_CACHE = BASE_DIR / "data" / "processed" / "chunks.json"
    CONFIG_DIR = BASE_DIR / "config"


settings = Settings()


def load_yaml(name: str) -> dict:
    with open(settings.CONFIG_DIR / name, encoding="utf-8") as f:
        return yaml.safe_load(f)
