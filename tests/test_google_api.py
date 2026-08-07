"""Gmail + Calendar REST wrappers, with the HTTP layer mocked."""

import pytest

import integrations.google._http as http
from integrations.google import calendar, gmail
from integrations.google._http import GoogleAPIError


class _Resp:
    def __init__(self, code, data):
        self.status_code = code
        self._data = data
        self.content = b"{}"

    def json(self):
        return self._data


@pytest.fixture
def google_http(monkeypatch):
    """Install a router for requests.request; tests register handlers."""
    routes = []  # list of (predicate, response)

    def request(method, url, headers=None, timeout=None, params=None, json=None):
        assert headers["Authorization"].startswith("Bearer ")
        for pred, resp in routes:
            if pred(method, url, params, json):
                return resp
        return _Resp(200, {})

    monkeypatch.setattr(http.requests, "request", request)
    return routes


def test_gmail_list_and_summary(google_http):
    google_http.append((lambda m, u, p, j: u.endswith("/messages"),
                        _Resp(200, {"messages": [{"id": "m1"}, {"id": "m2"}]})))
    google_http.append((lambda m, u, p, j: "/messages/m" in u, _Resp(200, {
        "id": "m1", "labelIds": ["UNREAD"], "snippet": "hi",
        "payload": {"headers": [
            {"name": "From", "value": "Priya <priya@x.com>"},
            {"name": "Subject", "value": "Q3 roadmap"},
            {"name": "Date", "value": "Wed"}]}})))
    out = gmail.list_messages("TOKEN", unread_only=True)
    assert len(out) == 2
    assert out[0]["subject"] == "Q3 roadmap"
    assert out[0]["unread"] is True


def test_gmail_reply_context(google_http):
    google_http.append((lambda m, u, p, j: "/messages/m1" in u, _Resp(200, {
        "id": "m1", "threadId": "t1", "payload": {"headers": [
            {"name": "From", "value": "Priya <priya@x.com>"},
            {"name": "Subject", "value": "Q3 roadmap"},
            {"name": "Message-ID", "value": "<abc@x>"}]}})))
    ctx = gmail.reply_context("TOKEN", "m1")
    assert ctx["to"] == "Priya <priya@x.com>"
    assert ctx["subject"] == "Re: Q3 roadmap"
    assert ctx["thread_id"] == "t1"
    assert ctx["in_reply_to"] == "<abc@x>"


def test_gmail_send(google_http):
    google_http.append((lambda m, u, p, j: u.endswith("/profile"),
                        _Resp(200, {"emailAddress": "me@x.com"})))
    google_http.append((lambda m, u, p, j: u.endswith("/messages/send"),
                        _Resp(200, {"id": "sent1"})))
    res = gmail.send_message("TOKEN", "priya@x.com", "Re: Hi", "See attached.")
    assert res["status"] == "sent" and res["id"] == "sent1"


def test_calendar_add_timed_event(google_http):
    google_http.append((lambda m, u, p, j: u.endswith("/primary") and m == "GET",
                        _Resp(200, {"timeZone": "Asia/Kolkata"})))
    captured = {}

    def create(m, u, p, j):
        if u.endswith("/events") and m == "POST":
            captured.update(j)
            return True
        return False
    google_http.append((create, _Resp(200, {"id": "e1", "summary": "Demo",
                        "start": {"dateTime": "2026-08-20T14:00:00"}, "end": {}})))
    res = calendar.add_event("TOKEN", "Demo", "2026-08-20", "14:00", 30)
    assert res["status"] == "created"
    assert captured["start"]["timeZone"] == "Asia/Kolkata"
    assert captured["start"]["dateTime"].startswith("2026-08-20T14:00")
    # end = start + 30 min
    assert captured["end"]["dateTime"].startswith("2026-08-20T14:30")


def test_http_errors_map_to_google_api_error(monkeypatch):
    monkeypatch.setattr(http.requests, "request",
                        lambda *a, **k: _Resp(401, {}))
    with pytest.raises(GoogleAPIError):
        gmail.list_messages("TOKEN")


def test_missing_token_raises():
    with pytest.raises(GoogleAPIError):
        gmail.list_messages("")
