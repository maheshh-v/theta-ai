"""
Tiny REST helper shared by the Gmail and Calendar modules.

Access tokens are guaranteed fresh by the caller (the session layer refreshes
before dispatch), so these helpers just attach the bearer header and turn
non-2xx responses into a concise `GoogleAPIError`.
"""

from __future__ import annotations

from typing import Any

import requests


class GoogleAPIError(RuntimeError):
    """A Gmail/Calendar API call failed."""


def _request(method: str, url: str, access_token: str, **kw) -> Any:
    if not access_token:
        raise GoogleAPIError("Google account not connected.")
    headers = {"Authorization": f"Bearer {access_token}"}
    headers.update(kw.pop("headers", {}))
    try:
        resp = requests.request(method, url, headers=headers, timeout=30, **kw)
    except requests.RequestException as ex:
        raise GoogleAPIError(f"Network error calling Google: {ex}") from ex

    if resp.status_code == 401:
        raise GoogleAPIError("Google authorization expired — please reconnect.")
    if resp.status_code == 403:
        raise GoogleAPIError(
            "Google denied the request (missing scope or API not enabled)."
        )
    if resp.status_code >= 400:
        raise GoogleAPIError(f"Google API error (HTTP {resp.status_code}).")
    if resp.status_code == 204 or not resp.content:
        return {}
    return resp.json()


def api_get(url: str, access_token: str, params: dict | None = None) -> Any:
    return _request("GET", url, access_token, params=params)


def api_post(url: str, access_token: str, json: dict | None = None,
             params: dict | None = None) -> Any:
    return _request("POST", url, access_token, json=json, params=params)


def api_patch(url: str, access_token: str, json: dict | None = None) -> Any:
    return _request("PATCH", url, access_token, json=json)
