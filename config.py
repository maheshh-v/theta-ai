"""
Central configuration for Theta.

Reads from environment variables (populated from a local `.env` if present) and
exposes a single `settings` object. Everything has a working default except the
language model, which Theta cannot fake — see `llm_configured`.

Two external services matter:

* **The language model** — Gemini, a local Ollama, or any OpenAI-compatible
  endpoint (Groq, OpenRouter, LM Studio, OpenAI itself). One is required.
* **Web search** — DuckDuckGo needs no key and is the default. Tavily and Brave
  are optional upgrades for higher-quality results.
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

LLM_PROVIDERS = ("gemini", "ollama", "openai")
SEARCH_PROVIDERS = ("duckduckgo", "tavily", "brave")


def _env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def _int(name: str, default: int, lo: int, hi: int) -> int:
    """Read a bounded integer, falling back to `default` on anything unparseable."""
    try:
        return max(lo, min(hi, int(_env(name, str(default)) or default)))
    except ValueError:
        return default


def _resolve_llm_provider() -> str:
    explicit = _env("LLM_PROVIDER").lower()
    if explicit in LLM_PROVIDERS:
        return explicit
    if _env("GEMINI_API_KEY"):
        return "gemini"
    if _env("OPENAI_API_KEY") or _env("OPENAI_BASE_URL"):
        return "openai"
    return "gemini"  # the documented default; unconfigured is handled in the UI


def _resolve_search_provider() -> str:
    explicit = _env("SEARCH_PROVIDER").lower()
    if explicit in SEARCH_PROVIDERS:
        return explicit
    if _env("TAVILY_API_KEY"):
        return "tavily"
    if _env("BRAVE_API_KEY"):
        return "brave"
    return "duckduckgo"


class Settings:
    """Runtime configuration, resolved once at import time."""

    # --- Language model ---------------------------------------------------- #
    llm_provider: str = _resolve_llm_provider()

    # Gemini (Google AI Studio free tier). "gemini-flash-latest" always points at
    # the current flash model — pinned versions get retired without warning.
    gemini_api_key: str = _env("GEMINI_API_KEY")
    gemini_model: str = _env("GEMINI_MODEL", "gemini-flash-latest")

    # Ollama (local, offline, free)
    ollama_host: str = _env("OLLAMA_HOST", "http://localhost:11434")
    ollama_model: str = _env("OLLAMA_MODEL", "llama3.1")

    # Any OpenAI-compatible chat-completions endpoint
    openai_api_key: str = _env("OPENAI_API_KEY")
    openai_base_url: str = _env("OPENAI_BASE_URL", "https://api.openai.com/v1")
    openai_model: str = _env("OPENAI_MODEL", "gpt-4o-mini")

    # --- Web search -------------------------------------------------------- #
    search_provider: str = _resolve_search_provider()
    tavily_api_key: str = _env("TAVILY_API_KEY")
    brave_api_key: str = _env("BRAVE_API_KEY")

    # --- Connected services (optional) ------------------------------------- #
    # Notion: an internal integration secret ("ntn_…"). A token set per-session
    # in Settings wins; this is the server-wide default for a single-user deploy.
    notion_token: str = _env("NOTION_TOKEN")

    # Gmail: a standard OAuth 2.0 web-application client. Theta never sees a
    # Google password — the user consents in Google's own flow and Theta keeps
    # only the resulting tokens, encrypted.
    google_client_id: str = _env("GOOGLE_CLIENT_ID")
    google_client_secret: str = _env("GOOGLE_CLIENT_SECRET")
    google_redirect_uri: str = _env("GOOGLE_REDIRECT_URI")

    # --- Browser ----------------------------------------------------------- #
    # Headful lets you watch the agent work and take the keyboard yourself —
    # which is what Theta asks you to do whenever a site wants a password.
    browser_headless: bool = _env("THETA_BROWSER_HEADLESS", "1").lower() not in {
        "0", "false", "no",
    }
    browser_width: int = _int("THETA_BROWSER_WIDTH", 1280, 640, 2560)
    browser_height: int = _int("THETA_BROWSER_HEIGHT", 900, 480, 1600)
    browser_timeout: int = _int("THETA_BROWSER_TIMEOUT", 30, 5, 120)

    # --- Research behaviour ------------------------------------------------ #
    # Sub-questions per brief, sources fetched per sub-question, and the amount
    # of each page handed to the extraction pass.
    research_breadth: int = _int("RESEARCH_BREADTH", 4, 1, 8)
    sources_per_question: int = _int("SOURCES_PER_QUESTION", 4, 1, 10)
    max_sources: int = _int("MAX_SOURCES", 14, 1, 40)
    source_chars: int = _int("SOURCE_CHARS", 6000, 1000, 30000)
    fetch_workers: int = _int("FETCH_WORKERS", 8, 1, 16)
    fetch_timeout: int = _int("FETCH_TIMEOUT", 20, 5, 120)

    # Agent loop. Browser work needs far more steps than a chat turn — a
    # navigate-search-filter-extract journey is easily fifteen actions.
    max_agent_steps: int = _int("MAX_AGENT_STEPS", 28, 1, 80)
    history_turns: int = _int("HISTORY_TURNS", 6, 0, 40)

    # --- Server ------------------------------------------------------------ #
    server_host: str = _env("THETA_HOST", "127.0.0.1")
    # PORT is the convention most hosts (Render, Railway, Fly, Heroku) inject.
    server_port: int = _int("THETA_PORT", _int("PORT", 7860, 1, 65535), 1, 65535)
    public_base_url: str = _env("PUBLIC_BASE_URL").rstrip("/")

    # --- Security / sessions ----------------------------------------------- #
    secret_key: str = _env("THETA_SECRET_KEY")
    session_cookie: str = _env("THETA_SESSION_COOKIE", "theta_session")
    session_persist: bool = _env("THETA_SESSION_PERSIST", "1").lower() not in {
        "0", "false", "no", "",
    }
    cookie_secure: bool = _env("THETA_COOKIE_SECURE").lower() in {"1", "true", "yes"}

    # Allow fetching private/loopback addresses. Off by default: the agent picks
    # URLs, so without this it cannot be steered into the host's own network.
    allow_private_urls: bool = _env("THETA_ALLOW_PRIVATE_URLS").lower() in {
        "1", "true", "yes",
    }

    # Paths
    project_root: Path = PROJECT_ROOT
    data_dir: Path = DATA_DIR
    servers_dir: Path = SERVERS_DIR

    @property
    def briefs_dir(self) -> Path:
        return self.data_dir / "briefs"

    @property
    def runs_dir(self) -> Path:
        return self.data_dir / "runs"

    @property
    def playbooks_dir(self) -> Path:
        return self.data_dir / "playbooks"

    @property
    def workspace_dir(self) -> Path:
        """Where the agent may write files. Everything it produces lands here and
        nowhere else."""
        return self.data_dir / "workspace"

    @property
    def llm_configured(self) -> bool:
        """False means Theta genuinely cannot answer — the UI says so plainly
        instead of degrading to a fake model."""
        if self.llm_provider == "gemini":
            return bool(self.gemini_api_key)
        if self.llm_provider == "openai":
            return bool(self.openai_api_key)
        return True  # Ollama needs no key; reachability is checked on use

    @property
    def google_configured(self) -> bool:
        """Whether Gmail *can* be connected. Unlike a Notion token, a user cannot
        supply this themselves — an OAuth client belongs to the deployment."""
        return bool(self.google_client_id and self.google_client_secret)

    @property
    def search_configured(self) -> bool:
        if self.search_provider == "tavily":
            return bool(self.tavily_api_key)
        if self.search_provider == "brave":
            return bool(self.brave_api_key)
        return True  # DuckDuckGo needs no key

    def summary(self) -> str:
        if self.llm_provider == "gemini":
            return f"Gemini ({self.gemini_model})"
        if self.llm_provider == "ollama":
            return f"Ollama ({self.ollama_model})"
        return f"OpenAI-compatible ({self.openai_model})"


settings = Settings()
