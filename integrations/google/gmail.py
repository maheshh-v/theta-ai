"""
Gmail over REST (v1).

Read (search, read, thread) and write (draft a reply, send a reply). Sending is
approval-gated one layer up, in `tools/catalog.py` — this module will send if
asked, and the gate is what decides whether it is asked.

Both write paths **verify**: after creating a draft or sending a reply, the
message is fetched back and checked (a sent message must carry the `SENT` label
and the thread it was supposed to join). An agent that reports "sent" from an
HTTP 200 is guessing, and email is not a good place to guess.

ATTRIBUTION
    The reply-header handling — chaining `References` from the previous
    message's own `References` plus its `Message-ID`, rather than starting the
    chain afresh each time — follows Google's Workspace MCP server
    (`workspace-server/src/services/GmailService.ts`), Apache-2.0,
    © 2026 Google LLC — https://github.com/gemini-cli-extensions/workspace
    No code from that project is reproduced here. Theta keeps narrower OAuth
    scopes than it does (read/compose/send, never `gmail.modify`).
"""

from __future__ import annotations

import base64
import re
from email.message import EmailMessage
from typing import Any

from integrations.google._http import GoogleAPIError, api_get, api_post

BASE = "https://gmail.googleapis.com/gmail/v1/users/me"

MAX_BODY_CHARS = 20_000
_SUMMARY_HEADERS = ["From", "To", "Subject", "Date"]
_REPLY_HEADERS = ["From", "Reply-To", "Subject", "Message-ID", "References"]


# --------------------------------------------------------------------------- #
# Read                                                                        #
# --------------------------------------------------------------------------- #
def search_messages(access_token: str, query: str = "", max_results: int = 10) -> list[dict]:
    """Search mail with Gmail's own query syntax (`from:`, `subject:`, `is:unread`…)."""
    params: dict[str, Any] = {"maxResults": max(1, min(int(max_results or 10), 50))}
    if query:
        params["q"] = str(query)
    else:
        params["labelIds"] = "INBOX"
    listing = api_get(f"{BASE}/messages", access_token, params=params)
    return [
        _summary(access_token, m["id"])
        for m in listing.get("messages", []) or []
    ]


def read_message(access_token: str, message_id: str) -> dict:
    """One message in full: headers plus the decoded plain-text body."""
    msg = api_get(f"{BASE}/messages/{message_id}", access_token, params={"format": "full"})
    headers = _headers(msg)
    return {
        "id": msg.get("id", ""),
        "thread_id": msg.get("threadId", ""),
        "from": headers.get("from", ""),
        "to": headers.get("to", ""),
        "subject": headers.get("subject", ""),
        "date": headers.get("date", ""),
        "unread": "UNREAD" in (msg.get("labelIds") or []),
        "body": _extract_body(msg.get("payload", {}))[:MAX_BODY_CHARS],
    }


def read_thread(access_token: str, thread_id: str) -> dict:
    """A whole conversation, oldest first — what "summarise this thread" needs."""
    thread = api_get(f"{BASE}/threads/{thread_id}", access_token, params={"format": "full"})
    messages = []
    for msg in thread.get("messages", []) or []:
        headers = _headers(msg)
        messages.append({
            "id": msg.get("id", ""),
            "from": headers.get("from", ""),
            "to": headers.get("to", ""),
            "date": headers.get("date", ""),
            "subject": headers.get("subject", ""),
            "body": _extract_body(msg.get("payload", {}))[:MAX_BODY_CHARS],
        })
    subject = messages[0]["subject"] if messages else ""
    return {"id": thread.get("id", thread_id), "subject": subject,
            "count": len(messages), "messages": messages}


# --------------------------------------------------------------------------- #
# Reply context                                                               #
# --------------------------------------------------------------------------- #
def reply_context(access_token: str, message_id: str) -> dict:
    """Everything a reply needs, resolved from the message being replied to.

    The agent supplies a message id and the reply text; the recipient, the
    subject and the threading headers come from Gmail. That is one fewer thing
    for a model to get wrong, and it means the address can never be hallucinated.
    """
    msg = api_get(
        f"{BASE}/messages/{message_id}", access_token,
        params={"format": "metadata", "metadataHeaders": _REPLY_HEADERS},
    )
    headers = _headers(msg)
    subject = headers.get("subject", "")
    message_id_header = headers.get("message-id", "")

    # Chain References rather than replacing it, or mail clients lose the thread
    # from the third message onwards.
    previous = headers.get("references", "").strip()
    references = f"{previous} {message_id_header}".strip() if previous else message_id_header

    return {
        "to": headers.get("reply-to") or headers.get("from", ""),
        "subject": subject if subject.lower().startswith("re:") else f"Re: {subject}".strip(),
        "thread_id": msg.get("threadId", ""),
        "in_reply_to": message_id_header,
        "references": references,
    }


# --------------------------------------------------------------------------- #
# Write                                                                       #
# --------------------------------------------------------------------------- #
def draft_reply(access_token: str, message_id: str, body: str) -> dict:
    """Save a reply to Drafts. Nothing is sent."""
    ctx = reply_context(access_token, message_id)
    raw = _build_raw(access_token, ctx["to"], ctx["subject"], body,
                     ctx["in_reply_to"], ctx["references"])
    payload: dict[str, Any] = {"message": {"raw": raw}}
    if ctx["thread_id"]:
        payload["message"]["threadId"] = ctx["thread_id"]

    draft = api_post(f"{BASE}/drafts", access_token, json=payload)
    draft_id = draft.get("id", "")
    verified = _draft_exists(access_token, draft_id)
    return {
        "ok": True,
        "status": "drafted",
        "draft_id": draft_id,
        "to": ctx["to"],
        "subject": ctx["subject"],
        "thread_id": ctx["thread_id"],
        "verified": verified,
        "verified_by": "re-read the draft from Gmail after saving it",
        "message": (
            f"Draft saved to Gmail — a reply to {ctx['to']}. Nothing has been sent."
            if verified else
            "Gmail accepted the draft but Theta could not read it back — check Drafts."
        ),
    }


def send_reply(access_token: str, message_id: str, body: str) -> dict:
    """Send a reply. Gated on human approval upstream — never call this blind."""
    ctx = reply_context(access_token, message_id)
    raw = _build_raw(access_token, ctx["to"], ctx["subject"], body,
                     ctx["in_reply_to"], ctx["references"])
    payload: dict[str, Any] = {"raw": raw}
    if ctx["thread_id"]:
        payload["threadId"] = ctx["thread_id"]

    sent = api_post(f"{BASE}/messages/send", access_token, json=payload)
    sent_id = sent.get("id", "")
    check = _sent_check(access_token, sent_id, ctx["thread_id"])
    return {
        "ok": True,
        "status": "sent",
        "message_id": sent_id,
        "to": ctx["to"],
        "subject": ctx["subject"],
        "thread_id": sent.get("threadId", ctx["thread_id"]),
        "verified": check["verified"],
        "verified_by": "re-read the sent message from Gmail",
        "message": (
            f"Sent to {ctx['to']} — “{ctx['subject']}”. Confirmed in Sent Mail."
            if check["verified"] else
            f"Gmail accepted the message for {ctx['to']}, but Theta could not confirm "
            f"it in Sent Mail ({check['why']}). Check the mailbox before resending."
        ),
    }


# --------------------------------------------------------------------------- #
# Verification                                                                #
# --------------------------------------------------------------------------- #
def _draft_exists(access_token: str, draft_id: str) -> bool:
    if not draft_id:
        return False
    try:
        found = api_get(f"{BASE}/drafts/{draft_id}", access_token,
                        params={"format": "minimal"})
    except GoogleAPIError:
        return False
    return bool(found.get("id"))


def _sent_check(access_token: str, message_id: str, expected_thread: str) -> dict:
    """Confirm the message really is in Sent Mail, on the thread we meant."""
    if not message_id:
        return {"verified": False, "why": "Gmail returned no message id"}
    try:
        found = api_get(f"{BASE}/messages/{message_id}", access_token,
                        params={"format": "metadata", "metadataHeaders": ["To"]})
    except GoogleAPIError as ex:
        return {"verified": False, "why": str(ex)}

    labels = found.get("labelIds") or []
    if "SENT" not in labels:
        return {"verified": False, "why": "the message is not labelled SENT"}
    if expected_thread and found.get("threadId") != expected_thread:
        return {"verified": False, "why": "it landed on a different thread"}
    return {"verified": True, "why": ""}


# --------------------------------------------------------------------------- #
# Internals                                                                   #
# --------------------------------------------------------------------------- #
def profile_email(access_token: str) -> str:
    try:
        return api_get(f"{BASE}/profile", access_token).get("emailAddress", "me")
    except GoogleAPIError:
        return "me"


def _summary(access_token: str, message_id: str) -> dict:
    msg = api_get(
        f"{BASE}/messages/{message_id}", access_token,
        params={"format": "metadata", "metadataHeaders": _SUMMARY_HEADERS},
    )
    headers = _headers(msg)
    return {
        "id": msg.get("id", ""),
        "thread_id": msg.get("threadId", ""),
        "from": headers.get("from", ""),
        "subject": headers.get("subject", "(no subject)"),
        "date": headers.get("date", ""),
        "snippet": msg.get("snippet", ""),
        "unread": "UNREAD" in (msg.get("labelIds") or []),
    }


def _headers(msg: dict) -> dict[str, str]:
    out: dict[str, str] = {}
    for header in (msg.get("payload") or {}).get("headers", []) or []:
        out[str(header.get("name", "")).lower()] = header.get("value", "")
    return out


def _extract_body(payload: dict) -> str:
    """Depth-first search for text/plain; fall back to text/html with tags removed."""
    plain = _find_part(payload, "text/plain")
    if plain is not None:
        return plain.strip()
    html = _find_part(payload, "text/html")
    if html is not None:
        stripped = re.sub(r"(?is)<(script|style).*?</\1>", " ", html)
        stripped = re.sub(r"(?s)<[^>]+>", " ", stripped)
        return re.sub(r"[ \t]*\n\s*\n\s*", "\n\n", stripped).strip()
    return ""


def _find_part(part: dict, mime: str) -> str | None:
    if not isinstance(part, dict):
        return None
    data = (part.get("body") or {}).get("data")
    if part.get("mimeType") == mime and data:
        return _b64url_decode(data)
    for sub in part.get("parts") or []:
        found = _find_part(sub, mime)
        if found is not None:
            return found
    return None


def _build_raw(access_token: str, to: str, subject: str, body: str,
               in_reply_to: str = "", references: str = "") -> str:
    msg = EmailMessage()
    msg["To"] = to
    msg["From"] = profile_email(access_token)
    msg["Subject"] = subject
    if in_reply_to:
        msg["In-Reply-To"] = in_reply_to
        msg["References"] = references or in_reply_to
    msg.set_content(body or "")
    return base64.urlsafe_b64encode(msg.as_bytes()).decode("ascii")


def _b64url_decode(data: str) -> str:
    padded = data + "=" * (-len(data) % 4)
    try:
        return base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8", "replace")
    except (ValueError, TypeError):
        return ""
