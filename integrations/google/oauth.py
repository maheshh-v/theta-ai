"""
Google OAuth 2.0 (web server flow) over plain REST — no Google SDK, matching the
project's REST-first style.

Flow:
  1. `build_auth_url(state)` — send the user to Google's consent screen.
  2. Google redirects back to the callback with `?code=…&state=…`.
  3. `exchange_code(code)` — access + refresh tokens.
  4. `ensure_fresh(token)` — transparently refreshes an expired access token.

Theta never sees the user's Google password: the whole point of this flow is
that the password is typed on Google's own domain. Tokens are plain dicts so they
live happily in the encrypted session store, and both are registered with the log
scrubber the moment they arrive.

Scopes are deliberately narrow — read, draft, send. No `gmail.modify`, so Theta
cannot label, archive or delete anything even if it were asked to.
"""

from __future__ import annotations

import time
from typing import Any
from urllib.parse import urlencode

import requests

from config import settings
from server import security

AUTH_URI = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URI = "https://oauth2.googleapis.com/token"
REVOKE_URI = "https://oauth2.googleapis.com/revoke"
USERINFO_URI = "https://openidconnect.googleapis.com/v1/userinfo"

SCOPES = [
    "openid",
    "email",
    "profile",
    "https://www.googleapis.com/auth/gmail.readonly",  # search + read
    "https://www.googleapis.com/auth/gmail.compose",   # drafts
    "https://www.googleapis.com/auth/gmail.send",      # send (approval-gated)
]

_EXPIRY_SKEW = 60  # refresh a little early


class GoogleAuthError(RuntimeError):
    """Raised when an OAuth exchange or refresh fails."""


# --------------------------------------------------------------------------- #
# Configuration                                                               #
# --------------------------------------------------------------------------- #
def redirect_uri() -> str:
    """The registered OAuth redirect URI. An explicit env value wins; otherwise
    it is derived from the public base URL (hosted) or host:port (local)."""
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
    params = {
        "client_id": settings.google_client_id,
        "redirect_uri": redirect_uri(),
        "response_type": "code",
        "scope": " ".join(SCOPES),
        "access_type": "offline",        # ask for a refresh token
        "include_granted_scopes": "true",
        "prompt": "consent",             # ensure one is issued on re-consent
        "state": state,
    }
    return f"{AUTH_URI}?{urlencode(params)}"


def exchange_code(code: str) -> dict[str, Any]:
    resp = _post(TOKEN_URI, {
        "code": code,
        "client_id": settings.google_client_id,
        "client_secret": settings.google_client_secret,
        "redirect_uri": redirect_uri(),
        "grant_type": "authorization_code",
    })
    if resp.status_code != 200:
        raise GoogleAuthError(f"Token exchange failed (HTTP {resp.status_code}).")
    token = _to_token(resp.json())
    _augment_with_identity(token)
    return token


def refresh(token: dict[str, Any]) -> dict[str, Any]:
    refresh_token = (token or {}).get("refresh_token")
    if not refresh_token:
        raise GoogleAuthError("No refresh token available — reconnect Gmail.")
    resp = _post(TOKEN_URI, {
        "refresh_token": refresh_token,
        "client_id": settings.google_client_id,
        "client_secret": settings.google_client_secret,
        "grant_type": "refresh_token",
    })
    if resp.status_code != 200:
        raise GoogleAuthError(f"Token refresh failed (HTTP {resp.status_code}).")
    fresh = _to_token(resp.json())
    # Google omits the refresh token on refresh responses — keep the old one, and
    # preserve the cached identity so the UI does not lose the account name.
    if not fresh.get("refresh_token"):
        fresh["refresh_token"] = refresh_token
    for key in ("email", "name", "picture", "connected_at"):
        if key in token and key not in fresh:
            fresh[key] = token[key]
    return fresh


def ensure_fresh(token: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    """Return (token, changed), refreshing if the access token has expired."""
    if not token:
        return token, False
    if token.get("access_token") and token.get("expiry", 0) > time.time() + _EXPIRY_SKEW:
        return token, False
    return refresh(token), True


def revoke(token: dict[str, Any]) -> None:
    """Best-effort revocation on disconnect, so 'Disconnect' means it."""
    value = (token or {}).get("refresh_token") or (token or {}).get("access_token")
    if not value:
        return
    try:
        _post(REVOKE_URI, {"token": value})
    except (requests.RequestException, GoogleAuthError):
        pass


def get_userinfo(access_token: str) -> dict[str, Any]:
    try:
        resp = requests.get(
            USERINFO_URI,
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=20,
        )
    except requests.RequestException as ex:
        raise GoogleAuthError(f"Could not reach Google: {ex}") from ex
    if resp.status_code != 200:
        raise GoogleAuthError(f"Userinfo failed (HTTP {resp.status_code}).")
    return resp.json()


def has_scope(token: dict[str, Any], suffix: str) -> bool:
    return suffix in (token or {}).get("scope", "")


# --------------------------------------------------------------------------- #
# Internals                                                                   #
# --------------------------------------------------------------------------- #
def _post(url: str, data: dict):
    try:
        return requests.post(url, data=data, timeout=30)
    except requests.RequestException as ex:
        raise GoogleAuthError(f"Could not reach Google: {ex}") from ex


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
    """Attach the account's email so the UI can show *which* mailbox is connected."""
    try:
        info = get_userinfo(token["access_token"])
        token["email"] = info.get("email", "")
        token["name"] = info.get("name", "")
        token["picture"] = info.get("picture", "")
    except (GoogleAuthError, KeyError):
        token.setdefault("email", "")
