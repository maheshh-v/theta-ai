"""
Bridges the encrypted session store and the Google OAuth layer.

Keeps `integrations/google/oauth.py` pure (no session knowledge) while giving
the API and tool layers a simple, session-aware view of connected accounts and a
guaranteed-fresh access token.
"""

from __future__ import annotations

from config import settings
from integrations.google import oauth
from server.session import Session

GOOGLE_KEY = "google_token"


def is_connected(session: Session) -> bool:
    tok = session.get(GOOGLE_KEY)
    return bool(tok and tok.get("access_token"))


def get_access_token(session: Session) -> str | None:
    """Return a valid access token, refreshing (and persisting) if needed."""
    tok = session.get(GOOGLE_KEY)
    if not tok:
        return None
    try:
        fresh, changed = oauth.ensure_fresh(tok)
    except oauth.GoogleAuthError:
        return None
    if changed:
        session.set(GOOGLE_KEY, fresh)
    return fresh.get("access_token")


def status(session: Session) -> dict:
    """Connected-account summary for the UI (no secret values)."""
    tok = session.get(GOOGLE_KEY) or {}
    connected = bool(tok.get("access_token"))
    return {
        "google": {
            "configured": settings.google_configured,
            "connected": connected,
            "email": tok.get("email", ""),
            "name": tok.get("name", ""),
            "picture": tok.get("picture", ""),
            "connected_at": tok.get("connected_at"),
            "capabilities": {
                "gmail_read": oauth.has_scope(tok, "gmail.readonly"),
                "gmail_send": oauth.has_scope(tok, "gmail.send"),
                "gmail_compose": oauth.has_scope(tok, "gmail.compose"),
                "calendar_read": oauth.has_scope(tok, "calendar.readonly"),
                "calendar_write": oauth.has_scope(tok, "calendar.events"),
            },
        }
    }


def disconnect(session: Session) -> None:
    tok = session.get(GOOGLE_KEY)
    if tok:
        oauth.revoke(tok)
    session.pop(GOOGLE_KEY, None)
