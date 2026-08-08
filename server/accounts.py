"""
Connected accounts: the bridge between the encrypted session store and the
services Theta talks to over their own APIs.

Keeping this separate is what lets `integrations/google/oauth.py` stay ignorant
of sessions and `integrations/notion/*` stay ignorant of everything but a token.
Callers get one thing: a credential that is valid *now* — Google's access token
is refreshed and re-persisted here, transparently, rather than at every call site.

Nothing in this module returns a secret to the browser. `status()` is the UI's
view and carries only which account is connected, never the token.
"""

from __future__ import annotations

import logging

from config import settings
from integrations.google import oauth
from server import security
from server.session import Session

GOOGLE_KEY = "google_token"
NOTION_KEY = "notion_token"

_log = logging.getLogger("theta.accounts")


# --------------------------------------------------------------------------- #
# Google / Gmail                                                              #
# --------------------------------------------------------------------------- #
def google_token(session: Session) -> dict:
    token = session.get(GOOGLE_KEY)
    return token if isinstance(token, dict) else {}


def google_connected(session: Session) -> bool:
    return bool(google_token(session).get("access_token"))


def google_access_token(session: Session) -> str | None:
    """A valid access token, refreshing and re-persisting it if it has expired."""
    token = google_token(session)
    if not token:
        return None
    try:
        fresh, changed = oauth.ensure_fresh(token)
    except oauth.GoogleAuthError as ex:
        _log.warning("Could not refresh the Google token: %s", ex)
        return None
    if changed:
        session.set(GOOGLE_KEY, fresh)
    value = fresh.get("access_token") or None
    security.register_secret(value)
    return value


def google_disconnect(session: Session) -> None:
    token = google_token(session)
    if token:
        oauth.revoke(token)
    session.pop(GOOGLE_KEY, None)


# --------------------------------------------------------------------------- #
# Notion                                                                      #
# --------------------------------------------------------------------------- #
def notion_token(session: Session) -> str:
    """The session's Notion token, or the deployment-wide one from the env."""
    value = session.get(NOTION_KEY)
    return str(value or "").strip() or settings.notion_token


def notion_connected(session: Session) -> bool:
    return bool(notion_token(session))


def notion_disconnect(session: Session) -> None:
    session.pop(NOTION_KEY, None)


# --------------------------------------------------------------------------- #
# UI view                                                                     #
# --------------------------------------------------------------------------- #
def status(session: Session) -> dict:
    """What the Settings page shows. Never includes a credential."""
    token = google_token(session)
    notion = notion_token(session)
    from_session = bool(str(session.get(NOTION_KEY) or "").strip())

    return {
        "notion": {
            "configured": True,          # a user can supply their own token
            "connected": bool(notion),
            "token_masked": security.mask_secret(notion) if notion else "",
            "token_source": "session" if from_session else ("env" if notion else "none"),
        },
        "google": {
            "configured": settings.google_configured,
            "connected": bool(token.get("access_token")),
            "email": token.get("email", ""),
            "name": token.get("name", ""),
            "connected_at": token.get("connected_at"),
            "capabilities": {
                "read": oauth.has_scope(token, "gmail.readonly"),
                "draft": oauth.has_scope(token, "gmail.compose"),
                "send": oauth.has_scope(token, "gmail.send"),
            },
        },
    }
