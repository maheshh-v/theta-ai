"""
Central configuration for the Personal AI Assistant.

Reads settings from environment variables (populated from a local `.env` file
if present) and exposes a single `settings` object used across the app.

LLM selection logic (in priority order):
  1. If LLM_PROVIDER is explicitly set, use it.
  2. Else if a GEMINI_API_KEY is present, use Gemini.
  3. Else fall back to the built-in "mock" LLM so the app always runs.
"""

from __future__ import annotations

import os
from pathlib import Path

try:
    from dotenv import load_dotenv

    load_dotenv()  # load .env from the project root if it exists
except Exception:  # pragma: no cover - dotenv is optional at runtime
    pass


PROJECT_ROOT = Path(__file__).resolve().parent
DATA_DIR = PROJECT_ROOT / "data"
SERVERS_DIR = PROJECT_ROOT / "tools" / "servers"


def _resolve_provider() -> str:
    explicit = os.getenv("LLM_PROVIDER", "").strip().lower()
    if explicit in {"gemini", "ollama", "mock"}:
        return explicit
    if os.getenv("GEMINI_API_KEY", "").strip():
        return "gemini"
    return "mock"


class Settings:
    """Runtime configuration, resolved once at import time."""

    # --- LLM ---
    llm_provider: str = _resolve_provider()

    # Gemini (Google AI Studio free tier)
    gemini_api_key: str = os.getenv("GEMINI_API_KEY", "").strip()
    gemini_model: str = os.getenv("GEMINI_MODEL", "gemini-2.5-flash").strip()

    # Ollama (local, free)
    ollama_host: str = os.getenv("OLLAMA_HOST", "http://localhost:11434").strip()
    ollama_model: str = os.getenv("OLLAMA_MODEL", "llama3.2").strip()

    # Agent loop
    max_agent_steps: int = int(os.getenv("MAX_AGENT_STEPS", "5"))

    # Paths
    project_root: Path = PROJECT_ROOT
    data_dir: Path = DATA_DIR
    servers_dir: Path = SERVERS_DIR

    def summary(self) -> str:
        if self.llm_provider == "gemini":
            return f"Gemini ({self.gemini_model})"
        if self.llm_provider == "ollama":
            return f"Ollama ({self.ollama_model} @ {self.ollama_host})"
        return "Mock LLM (no API key — demo mode)"


settings = Settings()
