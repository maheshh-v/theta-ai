"""Google OAuth flow: auth URL, code exchange, and token refresh."""

import time

import pytest

from config import settings
from integrations.google import oauth


class _Resp:
    def __init__(self, code, data):
        self.status_code = code
        self._data = data

    def json(self):
        return self._data


@pytest.fixture(autouse=True)
def _google_config(monkeypatch):
    monkeypatch.setattr(settings, "google_client_id", "CID.apps", raising=False)
    monkeypatch.setattr(settings, "google_client_secret", "CSECRET", raising=False)
    monkeypatch.setattr(settings, "google_redirect_uri",
                        "http://localhost:7860/api/auth/google/callback", raising=False)


def test_build_auth_url_has_required_params():
    url = oauth.build_auth_url("state123")
    for frag in ("client_id=CID", "response_type=code", "access_type=offline",
                 "state=state123", "gmail.readonly", "calendar.events"):
        assert frag in url


def test_exchange_code(monkeypatch):
    def fake_post(url, data=None, timeout=None):
        assert data["grant_type"] == "authorization_code"
        return _Resp(200, {"access_token": "AT1", "refresh_token": "RT1",
                           "expires_in": 3600, "scope": " ".join(oauth.SCOPES)})
    monkeypatch.setattr(oauth.requests, "post", fake_post)
    monkeypatch.setattr(oauth, "get_userinfo", lambda t: {"email": "me@x.com", "name": "Me"})

    tok = oauth.exchange_code("code")
    assert tok["access_token"] == "AT1"
    assert tok["refresh_token"] == "RT1"
    assert tok["email"] == "me@x.com"
    assert tok["expiry"] > time.time()


def test_refresh_preserves_refresh_token_and_identity(monkeypatch):
    def fake_post(url, data=None, timeout=None):
        assert data["grant_type"] == "refresh_token"
        return _Resp(200, {"access_token": "AT2", "expires_in": 3600})
    monkeypatch.setattr(oauth.requests, "post", fake_post)

    old = {"access_token": "AT1", "refresh_token": "RT1", "expiry": 0,
           "email": "me@x.com", "scope": "s"}
    fresh = oauth.refresh(old)
    assert fresh["access_token"] == "AT2"
    assert fresh["refresh_token"] == "RT1"   # Google omits it; we keep the old
    assert fresh["email"] == "me@x.com"


def test_ensure_fresh_refreshes_when_expired(monkeypatch):
    calls = {"n": 0}

    def fake_post(url, data=None, timeout=None):
        calls["n"] += 1
        return _Resp(200, {"access_token": "AT2", "expires_in": 3600})
    monkeypatch.setattr(oauth.requests, "post", fake_post)

    valid = {"access_token": "AT1", "refresh_token": "RT1", "expiry": time.time() + 999}
    tok, changed = oauth.ensure_fresh(valid)
    assert changed is False and calls["n"] == 0

    expired = {"access_token": "AT1", "refresh_token": "RT1", "expiry": time.time() - 5}
    tok, changed = oauth.ensure_fresh(expired)
    assert changed is True and tok["access_token"] == "AT2"


def test_has_scope():
    tok = {"scope": "openid https://www.googleapis.com/auth/gmail.send"}
    assert oauth.has_scope(tok, "gmail.send")
    assert not oauth.has_scope(tok, "gmail.readonly")
