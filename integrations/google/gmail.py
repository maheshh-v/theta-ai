"""
Gmail over REST (v1). Every function takes a fresh `access_token` and returns
plain JSON-friendly dicts/lists — the same shapes the old mock returned, so the
agent and UI are unchanged in spirit, just real now.

Read: list / search / read.  Write: draft (safe) / send (approval-gated).
"""

from __future__ import annotations

import base64
from email.message import EmailMessage
from typing import Any

from integrations.google._http import GoogleAPIError, api_get, api_post

BASE = "https://gmail.googleapis.com/gmail/v1/users/me"


# --------------------------------------------------------------------------- #
# Read                                                                        #
# --------------------------------------------------------------------------- #
def list_messages(access_token: str, unread_only: bool = False,
                  max_results: int = 15) -> list[dict]:
    """List inbox messages (id, from, subject, date, snippet, unread)."""
    params = {"maxResults": max_results, "labelIds": "INBOX"}
    if unread_only:
        params["q"] = "is:unread"
    listing = api_get(f"{BASE}/messages", access_token, params=params)
    ids = [m["id"] for m in listing.get("messages", [])]
    return [_message_summary(access_token, mid) for mid in ids]


def search_messages(access_token: str, query: str,
                    max_results: int = 15) -> list[dict]:
    """Search all mail with Gmail's query syntax (from:, subject:, keywords…)."""
    params = {"maxResults": max_results, "q": query or ""}
    listing = api_get(f"{BASE}/messages", access_token, params=params)
    ids = [m["id"] for m in listing.get("messages", [])]
    return [_message_summary(access_token, mid) for mid in ids]


def read_message(access_token: str, message_id: str) -> dict:
    """Full message: headers plus the decoded plain-text body."""
    msg = api_get(
        f"{BASE}/messages/{message_id}", access_token,
        params={"format": "full"},
    )
    headers = _headers(msg)
    return {
        "id": msg.get("id"),
        "threadId": msg.get("threadId"),
        "from": headers.get("from", ""),
        "to": headers.get("to", ""),
        "subject": headers.get("subject", ""),
        "date": headers.get("date", ""),
        "unread": "UNREAD" in msg.get("labelIds", []),
        "body": _extract_body(msg.get("payload", {})),
    }


# --------------------------------------------------------------------------- #
# Write                                                                       #
# --------------------------------------------------------------------------- #
def create_draft(access_token: str, to: str, subject: str, body: str,
                 thread_id: str | None = None,
                 in_reply_to: str | None = None) -> dict:
    """Create a draft (nothing is sent)."""
    raw = _build_raw(access_token, to, subject, body, in_reply_to)
    payload: dict[str, Any] = {"message": {"raw": raw}}
    if thread_id:
        payload["message"]["threadId"] = thread_id
    draft = api_post(f"{BASE}/drafts", access_token, json=payload)
    return {"status": "drafted", "id": draft.get("id"),
            "to": to, "subject": subject}


def send_message(access_token: str, to: str, subject: str, body: str,
                 thread_id: str | None = None,
                 in_reply_to: str | None = None) -> dict:
    """Send an email (gated behind human approval upstream)."""
    raw = _build_raw(access_token, to, subject, body, in_reply_to)
    payload: dict[str, Any] = {"raw": raw}
    if thread_id:
        payload["threadId"] = thread_id
    sent = api_post(f"{BASE}/messages/send", access_token, json=payload)
    return {"status": "sent", "id": sent.get("id"),
            "to": to, "subject": subject}


def reply_context(access_token: str, message_id: str) -> dict:
    """Everything needed to reply: recipient, threaded subject, threadId, Message-ID."""
    msg = api_get(
        f"{BASE}/messages/{message_id}", access_token,
        params={"format": "metadata",
                "metadataHeaders": ["From", "Subject", "Message-ID"]},
    )
    headers = _headers(msg)
    subject = headers.get("subject", "")
    return {
        "to": headers.get("from", ""),
        "subject": subject if subject.lower().startswith("re:") else f"Re: {subject}",
        "thread_id": msg.get("threadId"),
        "in_reply_to": headers.get("message-id", ""),
    }


# --------------------------------------------------------------------------- #
# Internals                                                                   #
# --------------------------------------------------------------------------- #
def _profile_email(access_token: str) -> str:
    try:
        return api_get(f"{BASE}/profile", access_token).get("emailAddress", "me")
    except GoogleAPIError:
        return "me"


def _message_summary(access_token: str, message_id: str) -> dict:
    msg = api_get(
        f"{BASE}/messages/{message_id}", access_token,
        params={"format": "metadata",
                "metadataHeaders": ["From", "Subject", "Date"]},
    )
    headers = _headers(msg)
    return {
        "id": msg.get("id"),
        "from": headers.get("from", ""),
        "subject": headers.get("subject", "(no subject)"),
        "date": headers.get("date", ""),
        "snippet": msg.get("snippet", ""),
        "unread": "UNREAD" in msg.get("labelIds", []),
    }


def _headers(msg: dict) -> dict[str, str]:
    out: dict[str, str] = {}
    for h in msg.get("payload", {}).get("headers", []):
        out[h.get("name", "").lower()] = h.get("value", "")
    return out


def _extract_body(payload: dict) -> str:
    """Depth-first search for a text/plain part; fall back to text/html stripped."""
    def walk(part: dict) -> str | None:
        mime = part.get("mimeType", "")
        data = part.get("body", {}).get("data")
        if mime == "text/plain" and data:
            return _b64url_decode(data)
        for sub in part.get("parts", []) or []:
            found = walk(sub)
            if found:
                return found
        return None

    text = walk(payload)
    if text is not None:
        return text.strip()
    # Fall back to any HTML we can find, tags crudely removed.
    data = payload.get("body", {}).get("data")
    if data:
        import re
        return re.sub(r"<[^>]+>", "", _b64url_decode(data)).strip()
    return ""


def _build_raw(access_token: str, to: str, subject: str, body: str,
               in_reply_to: str | None) -> str:
    msg = EmailMessage()
    msg["To"] = to
    msg["From"] = _profile_email(access_token)
    msg["Subject"] = subject
    if in_reply_to:
        msg["In-Reply-To"] = in_reply_to
        msg["References"] = in_reply_to
    msg.set_content(body)
    return base64.urlsafe_b64encode(msg.as_bytes()).decode("ascii")


def _b64url_decode(data: str) -> str:
    padded = data + "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8", "replace")
