"""
Tiny REST helper for the Gmail API.

Access tokens are guaranteed fresh by the caller (`server/accounts.py` refreshes
before dispatch), so these helpers just attach the bearer header and turn a
non-2xx response into a concise `GoogleAPIError` written for the user rather than
for a log file.
"""

from __future__ import annotations

from typing import Any

import requests

TIMEOUT = 30


class GoogleAPIError(RuntimeError):
    """A Gmail API call failed. The message is safe to show to the user."""

    def __init__(self, message: str, status: int = 0) -> None:
        super().__init__(message)
        self.status = status


def _request(method: str, url: str, access_token: str, **kw) -> Any:
    if not access_token:
        raise GoogleAPIError("Gmail isn't connected.")
    headers = {"Authorization": f"Bearer {access_token}"}
    headers.update(kw.pop("headers", {}))
    try:
        resp = requests.request(method, url, headers=headers, timeout=TIMEOUT, **kw)
    except requests.RequestException as ex:
        raise GoogleAPIError(f"Could not reach Gmail: {ex}") from ex

    if resp.status_code == 401:
        raise GoogleAPIError("Google sign-in expired — reconnect Gmail in Settings.", 401)
    if resp.status_code == 403:
        raise GoogleAPIError(
            "Google refused the request. The connected account may not have granted "
            "that permission — disconnect and reconnect Gmail in Settings.", 403,
        )
    if resp.status_code == 404:
        raise GoogleAPIError("Gmail has no message with that id.", 404)
    if resp.status_code == 429:
        raise GoogleAPIError("Gmail is rate-limiting Theta. Try again shortly.", 429)
    if resp.status_code >= 400:
        raise GoogleAPIError(_detail(resp), resp.status_code)
    if resp.status_code == 204 or not resp.content:
        return {}
    try:
        return resp.json()
    except ValueError as ex:
        raise GoogleAPIError("Gmail returned a response Theta could not read.") from ex


def _detail(resp) -> str:
    try:
        message = (resp.json().get("error") or {}).get("message", "")
    except ValueError:
        message = ""
    return message or f"Gmail returned HTTP {resp.status_code}."


def api_get(url: str, access_token: str, params: dict | None = None) -> Any:
    return _request("GET", url, access_token, params=params)


def api_post(url: str, access_token: str, json: dict | None = None,
             params: dict | None = None) -> Any:
    return _request("POST", url, access_token, json=json, params=params)
