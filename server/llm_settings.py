"""
Per-session LLM configuration.

Users can set their own provider / model / API key in the Settings page; those
override the environment defaults for their session only. Keys are stored in the
encrypted session (never returned to the browser in plaintext, always masked for
display, and registered with the log scrubber).

`test()` actually pings the provider and returns a friendly ok/error — without
the silent mock fallback `build_session_llm()` uses, so a bad key surfaces
clearly.
"""

from __future__ import annotations

from agent import llm as llm_mod
from agent.llm import BaseLLM, GeminiLLM, LLMError, OllamaLLM
from config import settings
from server import security
from server.session import Session

PROVIDERS = ["gemini", "ollama", "mock"]
_CFG_KEY = "llm"          # non-secret config dict in the session
_API_KEY = "llm_api_key"  # secret (see session.SECRET_KEYS)


def _cfg(session: Session) -> dict:
    return dict(session.get(_CFG_KEY, {}) or {})


def resolve(session: Session) -> dict:
    """Effective settings: session values override env/global defaults."""
    cfg = _cfg(session)
    return {
        "provider": cfg.get("provider") or settings.llm_provider,
        "api_key": session.get(_API_KEY) or settings.gemini_api_key,
        "gemini_model": cfg.get("gemini_model") or settings.gemini_model,
        "ollama_host": cfg.get("ollama_host") or settings.ollama_host,
        "ollama_model": cfg.get("ollama_model") or settings.ollama_model,
        "key_from_session": bool(session.get(_API_KEY)),
    }


def build_session_llm(session: Session) -> BaseLLM:
    r = resolve(session)
    return llm_mod.build_llm(
        provider=r["provider"],
        api_key=r["api_key"],
        gemini_model=r["gemini_model"],
        ollama_host=r["ollama_host"],
        ollama_model=r["ollama_model"],
    )


def public_config(session: Session) -> dict:
    """UI-safe view — never includes the raw key."""
    r = resolve(session)
    has_key = bool(r["api_key"])
    if r["key_from_session"]:
        source = "session"
    elif settings.gemini_api_key:
        source = "env"
    else:
        source = "none"
    return {
        "provider": r["provider"],
        "gemini_model": r["gemini_model"],
        "ollama_host": r["ollama_host"],
        "ollama_model": r["ollama_model"],
        "has_api_key": has_key,
        "api_key_masked": security.mask_secret(r["api_key"]) if has_key else "",
        "api_key_source": source,
        "providers": PROVIDERS,
        "active_label": build_session_llm(session).label,
    }


def update(session: Session, data: dict) -> None:
    cfg = _cfg(session)
    for field in ("provider", "gemini_model", "ollama_host", "ollama_model"):
        if data.get(field) is not None:
            cfg[field] = str(data[field]).strip()
    session.set(_CFG_KEY, cfg)

    if data.get("clear_api_key"):
        session.pop(_API_KEY, None)
    elif data.get("api_key"):  # non-empty only — avoids wiping the key on re-save
        session.set(_API_KEY, str(data["api_key"]).strip())


def test(session: Session, data: dict | None = None) -> tuple[bool, str]:
    """Test connectivity using pending values (if given) or the saved session."""
    data = data or {}
    r = resolve(session)
    provider = data.get("provider") or r["provider"]
    api_key = (data.get("api_key") or r["api_key"]).strip() if (data.get("api_key") or r["api_key"]) else ""
    gemini_model = data.get("gemini_model") or r["gemini_model"]
    ollama_host = data.get("ollama_host") or r["ollama_host"]
    ollama_model = data.get("ollama_model") or r["ollama_model"]

    security.register_secret(api_key)  # scrub even if the test is never saved
    try:
        if provider == "gemini":
            client: BaseLLM = GeminiLLM(api_key=api_key, model=gemini_model)
        elif provider == "ollama":
            client = OllamaLLM(host=ollama_host, model=ollama_model)
        else:
            return True, "Mock mode needs no key and is always available."
        client.complete("You are a connectivity test.", "Reply with the word OK.")
        return True, f"Connected — {client.label} responded."
    except LLMError as ex:
        return False, str(ex)
    except Exception as ex:  # pragma: no cover - defensive
        return False, f"Unexpected error: {ex}"
