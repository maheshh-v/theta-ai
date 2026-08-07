"""
Per-session preferences: which model, which search provider, how deep to research.

Everything a user sets in the Settings page lives here, scoped to their session
and overriding the server's environment defaults. API keys are stored in the
encrypted session, never returned to the browser in plaintext, always masked for
display, and registered with the log scrubber.

`test_model` / `test_search` really do call out to the provider, so a bad key
fails loudly in Settings rather than silently mid-research.
"""

from __future__ import annotations

from agent import llm as llm_mod
from agent.llm import BaseLLM, LLMError
from config import LLM_PROVIDERS, SEARCH_PROVIDERS, settings
from server import security
from server.session import Session
from tools import web_tools
from tools.mcp_client import ToolContext
from web.search import provider_label

_CFG_KEY = "prefs"              # non-secret config dict in the session
_LLM_KEY = "llm_api_key"        # secret (see session.SECRET_KEYS)
_SEARCH_KEY = "search_api_key"  # secret

# Bounds mirror config._int so a hand-crafted request cannot ask for 500 sources.
_LIMITS = {
    "research_breadth": (1, 8),
    "sources_per_question": (1, 10),
    "max_sources": (1, 40),
}


def _cfg(session: Session) -> dict:
    return dict(session.get(_CFG_KEY, {}) or {})


def resolve(session: Session) -> dict:
    """Effective settings: session values override environment defaults."""
    cfg = _cfg(session)
    provider = cfg.get("provider") or settings.llm_provider
    search_provider = cfg.get("search_provider") or settings.search_provider
    return {
        # model
        "provider": provider,
        "api_key": session.get(_LLM_KEY) or _env_key(provider),
        "key_from_session": bool(session.get(_LLM_KEY)),
        "gemini_model": cfg.get("gemini_model") or settings.gemini_model,
        "ollama_host": cfg.get("ollama_host") or settings.ollama_host,
        "ollama_model": cfg.get("ollama_model") or settings.ollama_model,
        "openai_base_url": cfg.get("openai_base_url") or settings.openai_base_url,
        "openai_model": cfg.get("openai_model") or settings.openai_model,
        # search
        "search_provider": search_provider,
        "search_key": session.get(_SEARCH_KEY) or _env_search_key(search_provider),
        "search_key_from_session": bool(session.get(_SEARCH_KEY)),
        # research
        "research_breadth": _int(cfg, "research_breadth", settings.research_breadth),
        "sources_per_question": _int(cfg, "sources_per_question", settings.sources_per_question),
        "max_sources": _int(cfg, "max_sources", settings.max_sources),
        "approve_research": cfg.get("approve_research", True),
        # Browser mode is process-wide: the browser runs inside the MCP server
        # subprocess and reads its own environment, so this is reported, not set.
        "browser_headless": settings.browser_headless,
    }


def _env_key(provider: str) -> str:
    return settings.openai_api_key if provider == "openai" else settings.gemini_api_key


def _env_search_key(provider: str) -> str:
    if provider == "tavily":
        return settings.tavily_api_key
    if provider == "brave":
        return settings.brave_api_key
    return ""


def _int(cfg: dict, key: str, default: int) -> int:
    lo, hi = _LIMITS[key]
    try:
        return max(lo, min(hi, int(cfg.get(key, default))))
    except (TypeError, ValueError):
        return default


# --------------------------------------------------------------------------- #
# Building the runtime objects                                                #
# --------------------------------------------------------------------------- #
def build_llm(session: Session) -> BaseLLM:
    """Build this session's model. Raises LLMError when it isn't configured —
    callers turn that into a clear message rather than a fake answer."""
    r = resolve(session)
    return llm_mod.build_llm(
        provider=r["provider"],
        api_key=r["api_key"],
        gemini_model=r["gemini_model"],
        ollama_host=r["ollama_host"],
        ollama_model=r["ollama_model"],
        openai_base_url=r["openai_base_url"],
        openai_model=r["openai_model"],
    )


def tool_context(session: Session, llm: BaseLLM) -> ToolContext:
    """The execution context handed to every tool call for this session."""
    r = resolve(session)
    return ToolContext(
        llm=llm,
        search_provider=r["search_provider"],
        search_key=r["search_key"],
        research_opts={
            "breadth": r["research_breadth"],
            "per_question": r["sources_per_question"],
            "max_sources": r["max_sources"],
        },
    )


def approves_research(session: Session) -> bool:
    return bool(resolve(session)["approve_research"])


# --------------------------------------------------------------------------- #
# UI-facing config                                                            #
# --------------------------------------------------------------------------- #
def public_config(session: Session) -> dict:
    """UI-safe view — never includes a raw key."""
    r = resolve(session)
    has_key = bool(r["api_key"])
    has_search_key = bool(r["search_key"])

    try:
        label = build_llm(session).label
        model_ready = True
        model_error = ""
    except LLMError as ex:
        label = "Not configured"
        model_ready = False
        model_error = str(ex)

    return {
        "provider": r["provider"],
        "providers": list(LLM_PROVIDERS),
        "gemini_model": r["gemini_model"],
        "ollama_host": r["ollama_host"],
        "ollama_model": r["ollama_model"],
        "openai_base_url": r["openai_base_url"],
        "openai_model": r["openai_model"],
        "has_api_key": has_key,
        "api_key_masked": security.mask_secret(r["api_key"]) if has_key else "",
        "api_key_source": "session" if r["key_from_session"] else ("env" if has_key else "none"),
        "model_ready": model_ready,
        "model_error": model_error,
        "active_label": label,

        "search_provider": r["search_provider"],
        "search_providers": list(SEARCH_PROVIDERS),
        "search_label": provider_label(r["search_provider"]),
        "search_needs_key": r["search_provider"] in ("tavily", "brave"),
        "has_search_key": has_search_key,
        "search_key_masked": security.mask_secret(r["search_key"]) if has_search_key else "",

        "research_breadth": r["research_breadth"],
        "sources_per_question": r["sources_per_question"],
        "max_sources": r["max_sources"],
        "approve_research": r["approve_research"],

        "browser_headless": r["browser_headless"],
        "browser_size": f"{settings.browser_width}×{settings.browser_height}",
        "max_steps": settings.max_agent_steps,
    }


def update(session: Session, data: dict) -> None:
    cfg = _cfg(session)

    provider = str(data.get("provider", "")).strip().lower()
    if provider in LLM_PROVIDERS:
        cfg["provider"] = provider
    search_provider = str(data.get("search_provider", "")).strip().lower()
    if search_provider in SEARCH_PROVIDERS:
        cfg["search_provider"] = search_provider

    for field in ("gemini_model", "ollama_host", "ollama_model",
                  "openai_base_url", "openai_model"):
        if data.get(field) is not None:
            value = str(data[field]).strip()
            if value:
                cfg[field] = value

    for field in _LIMITS:
        if data.get(field) is not None:
            lo, hi = _LIMITS[field]
            try:
                cfg[field] = max(lo, min(hi, int(data[field])))
            except (TypeError, ValueError):
                pass

    if data.get("approve_research") is not None:
        cfg["approve_research"] = bool(data["approve_research"])

    session.set(_CFG_KEY, cfg)

    # Keys: an empty string means "leave as is" so re-saving the form never
    # silently wipes a stored key; clearing is explicit.
    if data.get("clear_api_key"):
        session.pop(_LLM_KEY, None)
    elif str(data.get("api_key", "")).strip():
        session.set(_LLM_KEY, str(data["api_key"]).strip())

    if data.get("clear_search_key"):
        session.pop(_SEARCH_KEY, None)
    elif str(data.get("search_api_key", "")).strip():
        session.set(_SEARCH_KEY, str(data["search_api_key"]).strip())


# --------------------------------------------------------------------------- #
# Connection tests                                                            #
# --------------------------------------------------------------------------- #
def test_model(session: Session, data: dict | None = None) -> tuple[bool, str]:
    """Test the model using pending form values, falling back to what's saved."""
    data = data or {}
    r = resolve(session)
    api_key = str(data.get("api_key") or r["api_key"] or "").strip()
    security.register_secret(api_key)  # scrub it even if the test is never saved
    try:
        client = llm_mod.build_llm(
            provider=str(data.get("provider") or r["provider"]),
            api_key=api_key,
            gemini_model=data.get("gemini_model") or r["gemini_model"],
            ollama_host=data.get("ollama_host") or r["ollama_host"],
            ollama_model=data.get("ollama_model") or r["ollama_model"],
            openai_base_url=data.get("openai_base_url") or r["openai_base_url"],
            openai_model=data.get("openai_model") or r["openai_model"],
        )
        reply = client.complete("You are a connectivity test.", "Reply with the word OK.", 1000)
    except LLMError as ex:
        return False, str(ex)
    except Exception as ex:  # pragma: no cover - defensive
        return False, f"Unexpected error: {ex}"
    if not reply.strip():
        return False, f"{client.label} connected but returned nothing."
    return True, f"Connected — {client.label} responded."


def test_search(session: Session, data: dict | None = None) -> tuple[bool, str]:
    """Run a real probe query against the chosen search provider."""
    data = data or {}
    r = resolve(session)
    provider = str(data.get("search_provider") or r["search_provider"])
    key = str(data.get("search_api_key") or r["search_key"] or "").strip()
    security.register_secret(key)
    try:
        return web_tools.search_status(provider, key)
    except Exception as ex:  # pragma: no cover - defensive
        return False, f"Unexpected error: {ex}"
