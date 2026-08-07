"""
Google OAuth 2.0 (web server flow) over plain REST — no google SDK dependency,
matching the project's REST-first style.

Flow:
  1. `build_auth_url(state)` -> send the user to Google's consent screen.
  2. Google redirects back to our callback with `?code=...&state=...`.
  3. `exchange_code(code)` -> access + refresh tokens.
  4. `ensure_fresh(token)` transparently refreshes an expired access token.

Tokens are dicts (JSON-serialisable) so they can live in the encrypted session
store. Access/refresh tokens are registered with the log scrubber the moment we
receive them.
"""

from __future__ import annotations

import time
from typing import Any

import requests

from config import settings
from server import security

AUTH_URI = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URI = "https://oauth2.googleapis.com/token"
REVOKE_URI = "https://oauth2.googleapis.com/revoke"
USERINFO_URI = "https://openidconnect.googleapis.com/v1/userinfo"

# Least-privilege scopes for what Theta actually does.
SCOPES = [
    "openid",
    "email",
    "profile",
    "https://www.googleapis.com/auth/gmail.readonly",   # list/search/read
    "https://www.googleapis.com/auth/gmail.compose",     # create drafts
    "https://www.googleapis.com/auth/gmail.send",        # send (approval-gated)
    "https://www.googleapis.com/auth/calendar.readonly",  # list events
    "https://www.googleapis.com/auth/calendar.events",    # add/update events
]

_EXPIRY_SKEW = 60  # refresh a bit early


class GoogleAuthError(RuntimeError):
    """Raised when an OAuth exchange or refresh fails."""


# --------------------------------------------------------------------------- #
# Configuration helpers                                                       #
# --------------------------------------------------------------------------- #
def redirect_uri() -> str:
    """The registered OAuth redirect URI. Explicit env wins; otherwise derived
    from the public base URL (hosted) or host:port (local)."""
    if settings.google_redirect_uri:
        return settings.google_redirect_uri
    if settings.public_base_url:
        base = settings.public_base_url
    else:
        host = settings.server_host
        if host in ("0.0.0.0", "::", ""):
            host = "localhost"
        base = f"http://{host}:{settings.server_port}"
    return f"{base}/api/auth/google/callback"


# --------------------------------------------------------------------------- #
# The flow                                                                    #
# --------------------------------------------------------------------------- #
def build_auth_url(state: str) -> str:
    from urllib.parse import urlencode

    params = {
        "client_id": settings.google_client_id,
        "redirect_uri": redirect_uri(),
        "response_type": "code",
        "scope": " ".join(SCOPES),
        "access_type": "offline",       # get a refresh token
        "include_granted_scopes": "true",
        "prompt": "consent",            # ensure a refresh token on re-consent
        "state": state,
    }
    return f"{AUTH_URI}?{urlencode(params)}"


def exchange_code(code: str) -> dict[str, Any]:
    resp = requests.post(
        TOKEN_URI,
        data={
            "code": code,
            "client_id": settings.google_client_id,
            "client_secret": settings.google_client_secret,
            "redirect_uri": redirect_uri(),
            "grant_type": "authorization_code",
        },
        timeout=30,
    )
    if resp.status_code != 200:
        raise GoogleAuthError(f"Token exchange failed (HTTP {resp.status_code}).")
    token = _to_token(resp.json())
    _augment_with_identity(token)
    return token


def refresh(token: dict[str, Any]) -> dict[str, Any]:
    refresh_token = token.get("refresh_token")
    if not refresh_token:
        raise GoogleAuthError("No refresh token available; reconnect the account.")
    resp = requests.post(
        TOKEN_URI,
        data={
            "refresh_token": refresh_token,
            "client_id": settings.google_client_id,
            "client_secret": settings.google_client_secret,
            "grant_type": "refresh_token",
        },
        timeout=30,
    )
    if resp.status_code != 200:
        raise GoogleAuthError(f"Token refresh failed (HTTP {resp.status_code}).")
    fresh = _to_token(resp.json())
    # Google omits the refresh_token on refresh responses — keep the old one and
    # preserve the cached identity fields.
    if not fresh.get("refresh_token"):
        fresh["refresh_token"] = refresh_token
    for k in ("email", "name", "picture", "connected_at"):
        if k in token and k not in fresh:
            fresh[k] = token[k]
    return fresh


def ensure_fresh(token: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    """Return (token, changed). Refreshes if the access token is expired."""
    if not token:
        return token, False
    if token.get("expiry", 0) > time.time() + _EXPIRY_SKEW and token.get("access_token"):
        return token, False
    return refresh(token), True


def revoke(token: dict[str, Any]) -> None:
    """Best-effort revocation on disconnect."""
    tok = token.get("refresh_token") or token.get("access_token")
    if not tok:
        return
    try:
        requests.post(REVOKE_URI, data={"token": tok}, timeout=15)
    except requests.RequestException:
        pass


# --------------------------------------------------------------------------- #
# Internals                                                                   #
# --------------------------------------------------------------------------- #
def _to_token(data: dict) -> dict[str, Any]:
    access = data.get("access_token", "")
    security.register_secret(access)
    if data.get("refresh_token"):
        security.register_secret(data["refresh_token"])
    expires_in = int(data.get("expires_in", 3600))
    return {
        "access_token": access,
        "refresh_token": data.get("refresh_token", ""),
        "scope": data.get("scope", ""),
        "token_type": data.get("token_type", "Bearer"),
        "expiry": time.time() + expires_in,
        "connected_at": time.time(),
    }


def _augment_with_identity(token: dict[str, Any]) -> None:
    """Attach the account's email/name so the UI can show what's connected."""
    try:
        info = get_userinfo(token["access_token"])
        token["email"] = info.get("email", "")
        token["name"] = info.get("name", "")
        token["picture"] = info.get("picture", "")
    except Exception:
        token.setdefault("email", "")


def get_userinfo(access_token: str) -> dict[str, Any]:
    resp = requests.get(
        USERINFO_URI,
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=20,
    )
    if resp.status_code != 200:
        raise GoogleAuthError(f"Userinfo failed (HTTP {resp.status_code}).")
    return resp.json()


def has_scope(token: dict[str, Any], scope_suffix: str) -> bool:
    return scope_suffix in (token or {}).get("scope", "")
