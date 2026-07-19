"""Central configuration — all env vars read here, nowhere else."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

# Load .env from the project root if present
_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(_ROOT / ".env")


# === LLM ===
OPENAI_API_KEY: str | None = os.getenv("OPENAI_API_KEY")
OPENAI_MODEL_FAST: str = os.getenv("OPENAI_MODEL_FAST", "gpt-4o-mini")
OPENAI_MODEL_SMART: str = os.getenv("OPENAI_MODEL_SMART", "gpt-4o")

# === Sourcing tools ===
GITHUB_TOKEN: str | None = os.getenv("GITHUB_TOKEN")
TAVILY_API_KEY: str | None = os.getenv("TAVILY_API_KEY")
SERPAPI_API_KEY: str | None = os.getenv("SERPAPI_API_KEY")
SEMANTIC_SCHOLAR_API_KEY: str | None = os.getenv("SEMANTIC_SCHOLAR_API_KEY") or None

# === Database ===
DATABASE_URL: str | None = os.getenv("DATABASE_URL")

# === Runtime ===
CACHE_PATH: Path = Path(os.getenv("VENTUREOS_CACHE_PATH", ".cache/ventureos.db"))
LOG_LEVEL: str = os.getenv("VENTUREOS_LOG_LEVEL", "INFO")

# === Constants ===
HTTP_TIMEOUT_SECONDS: float = 15.0
HTTP_RETRIES: int = 3
CACHE_TTL_HOURS: int = 24

# Recency decay: ln(2) / 180 → ~6 month half-life for evidence
RECENCY_DECAY_LAMBDA: float = 0.003851

# Trust score base confidences per source type
BASE_CONFIDENCE = {
    "github": 0.90,
    "semantic_scholar": 0.90,
    "hn": 0.60,
    "tavily": 0.50,
    "serpapi": 0.50,
    "deck": 0.40,
}


def ensure_cache_dir() -> None:
    """Create the cache directory if it doesn't exist."""
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)