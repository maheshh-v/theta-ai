"""
Server-side session store.

A random session id lives in an **encrypted cookie**; the actual data (API keys,
per-user preferences, conversation history) stays server-side, so a key never
travels to the browser. The whole store is encrypted at rest on disk (Fernet) so
a restart keeps settings intact. Secret values are registered with the log
scrubber the moment they are set.

The `SessionMiddleware` attaches a ready-to-use `Session` to every request as
`request.state.session`.
"""

from __future__ import annotations

import json
import secrets as _secrets
import threading
from pathlib import Path

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from config import settings
from server import security

# Keys whose values are secrets: registered for log-scrubbing, masked in the UI.
SECRET_KEYS = {"llm_api_key", "search_api_key"}

_COOKIE_MAX_AGE = 60 * 60 * 24 * 30  # 30 days


class Session:
    """A thin, save-on-write handle over one session's dict."""

    def __init__(self, sid: str, data: dict, store: "SessionStore") -> None:
        self.sid = sid
        self._data = data
        self._store = store

    def get(self, key: str, default=None):
        return self._data.get(key, default)

    def set(self, key: str, value) -> None:
        self._data[key] = value
        if key in SECRET_KEYS:
            security.register_secret(
                value if isinstance(value, str) else json.dumps(value)
            )
        self._store.save()

    def pop(self, key: str, default=None):
        v = self._data.pop(key, default)
        self._store.save()
        return v

    def has(self, key: str) -> bool:
        return self._data.get(key) not in (None, "", {}, [])

    @property
    def data(self) -> dict:
        return self._data


class SessionStore:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._sessions: dict[str, dict] = {}
        self._path: Path = settings.data_dir / "sessions.enc"
        self._persist: bool = settings.session_persist
        self._load()

    # -- persistence -------------------------------------------------------- #
    def _load(self) -> None:
        if not self._persist or not self._path.exists():
            return
        try:
            raw = security.decrypt(self._path.read_text(encoding="utf-8"))
            self._sessions = json.loads(raw)
            for data in self._sessions.values():
                for k in SECRET_KEYS:
                    v = data.get(k)
                    if v:
                        security.register_secret(
                            v if isinstance(v, str) else json.dumps(v)
                        )
        except Exception:
            # A stale/undecryptable file (e.g. key rotated) starts fresh.
            self._sessions = {}

    def save(self) -> None:
        if not self._persist:
            return
        with self._lock:
            try:
                blob = security.encrypt(json.dumps(self._sessions))
                tmp = self._path.with_suffix(".enc.tmp")
                tmp.write_text(blob, encoding="utf-8")
                tmp.replace(self._path)
            except Exception:
                pass

    # -- access ------------------------------------------------------------- #
    def get_or_create(self, sid: str | None) -> tuple[str, dict]:
        with self._lock:
            if sid and sid in self._sessions:
                return sid, self._sessions[sid]
            new_sid = sid or _secrets.token_urlsafe(32)
            self._sessions.setdefault(new_sid, {})
            return new_sid, self._sessions[new_sid]

    def session(self, sid: str | None) -> Session:
        resolved, data = self.get_or_create(sid)
        return Session(resolved, data, self)


class SessionMiddleware(BaseHTTPMiddleware):
    """Reads/sets the encrypted session cookie and attaches `state.session`."""

    def __init__(self, app, store: SessionStore) -> None:
        super().__init__(app)
        self.store = store

    async def dispatch(self, request: Request, call_next):
        cookie_val = request.cookies.get(settings.session_cookie)
        incoming_sid = security.try_decrypt(cookie_val) if cookie_val else None

        new_sid, data = self.store.get_or_create(incoming_sid)
        request.state.session = Session(new_sid, data, self.store)

        response = await call_next(request)

        if new_sid != incoming_sid:
            response.set_cookie(
                settings.session_cookie,
                security.encrypt(new_sid),
                max_age=_COOKIE_MAX_AGE,
                httponly=True,
                samesite="lax",
                secure=settings.cookie_secure,
                path="/",
            )
        return response


def current_session(request: Request) -> Session:
    """FastAPI dependency: the session attached by the middleware."""
    return request.state.session
