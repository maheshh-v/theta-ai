"""
LLM providers behind a single tiny interface: `complete(system, user) -> str`.

Three providers, selected by config.settings.llm_provider:
  * gemini  — Google AI Studio REST API (free tier). No SDK dependency; we call
              the REST endpoint directly with `requests`, and fall back across a
              few known model names so the demo survives model renames.
  * ollama  — a local Ollama server (free, offline). REST at /api/generate.
  * mock    — no network, no key. Produces a valid agent action by simple
              keyword heuristics so the app is fully demoable with zero setup.

Every provider raises LLMError on failure; the orchestrator catches it and
explains the problem instead of crashing.
"""

from __future__ import annotations

import json
import re

import requests

from config import settings


class LLMError(RuntimeError):
    """Raised when a provider cannot produce a completion."""


# --------------------------------------------------------------------------- #
# Base                                                                        #
# --------------------------------------------------------------------------- #
class BaseLLM:
    name: str = "base"
    label: str = "Base LLM"
    is_mock: bool = False

    def complete(self, system: str, user: str) -> str:  # pragma: no cover
        raise NotImplementedError


# --------------------------------------------------------------------------- #
# Gemini (Google AI Studio, free tier)                                        #
# --------------------------------------------------------------------------- #
class GeminiLLM(BaseLLM):
    name = "gemini"
    BASE = "https://generativelanguage.googleapis.com/v1beta/models"
    # Tried in order; resilient to Google renaming/retiring flash models.
    FALLBACK_MODELS = ["gemini-2.5-flash", "gemini-2.0-flash", "gemini-1.5-flash"]

    def __init__(self) -> None:
        if not settings.gemini_api_key:
            raise LLMError("GEMINI_API_KEY is not set.")
        self.api_key = settings.gemini_api_key
        # Configured model first, then fallbacks (de-duplicated).
        self.models = [settings.gemini_model] + [
            m for m in self.FALLBACK_MODELS if m != settings.gemini_model
        ]
        self.label = f"Gemini ({settings.gemini_model})"

    def complete(self, system: str, user: str) -> str:
        payload = {
            "system_instruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": user}]}],
            "generationConfig": {"temperature": 0.2, "maxOutputTokens": 1024},
        }
        last_err = None
        for model in self.models:
            url = f"{self.BASE}/{model}:generateContent?key={self.api_key}"
            try:
                resp = requests.post(url, json=payload, timeout=45)
            except requests.RequestException as ex:
                raise LLMError(f"Network error contacting Gemini: {ex}") from ex

            if resp.status_code == 404:
                # Model name not available for this key — try the next one.
                last_err = f"model '{model}' not found (404)"
                continue
            if resp.status_code == 429:
                raise LLMError("Gemini rate limit hit (HTTP 429). Try again shortly.")
            if resp.status_code in (400, 401, 403):
                raise LLMError(
                    f"Gemini rejected the request (HTTP {resp.status_code}). "
                    "Check that GEMINI_API_KEY is valid. "
                    f"Detail: {_short(resp.text)}"
                )
            if resp.status_code != 200:
                last_err = f"HTTP {resp.status_code}: {_short(resp.text)}"
                continue

            text = _extract_gemini_text(resp.json())
            if text:
                return text
            last_err = "empty response from Gemini"

        raise LLMError(f"Gemini failed for all model candidates ({last_err}).")


def _extract_gemini_text(data: dict) -> str:
    try:
        parts = data["candidates"][0]["content"]["parts"]
        return "".join(p.get("text", "") for p in parts).strip()
    except (KeyError, IndexError, TypeError):
        return ""


# --------------------------------------------------------------------------- #
# Ollama (local, free)                                                        #
# --------------------------------------------------------------------------- #
class OllamaLLM(BaseLLM):
    name = "ollama"

    def __init__(self) -> None:
        self.host = settings.ollama_host.rstrip("/")
        self.model = settings.ollama_model
        self.label = f"Ollama ({self.model})"

    def complete(self, system: str, user: str) -> str:
        url = f"{self.host}/api/generate"
        payload = {
            "model": self.model,
            "system": system,
            "prompt": user,
            "stream": False,
            "options": {"temperature": 0.2},
        }
        try:
            resp = requests.post(url, json=payload, timeout=120)
        except requests.RequestException as ex:
            raise LLMError(
                f"Could not reach Ollama at {self.host}. Is it running "
                f"(`ollama serve`) and is the model pulled (`ollama pull {self.model}`)? "
                f"Detail: {ex}"
            ) from ex
        if resp.status_code != 200:
            raise LLMError(f"Ollama error HTTP {resp.status_code}: {_short(resp.text)}")
        return (resp.json().get("response") or "").strip()


# --------------------------------------------------------------------------- #
# Mock (no key, no network)                                                   #
# --------------------------------------------------------------------------- #
class MockLLM(BaseLLM):
    """A keyless stand-in. It reads the orchestrator's prompt, picks a tool by
    keyword, and emits the same JSON action shape a real model would — so the
    full agent loop runs and the reasoning display is populated, clearly marked
    as mock mode."""

    name = "mock"
    label = "Mock LLM (no API key — demo mode)"
    is_mock = True

    def complete(self, system: str, user: str) -> str:
        command = _between(user, "USER COMMAND:", "AVAILABLE TOOLS:").strip()
        work = _after(user, "WORK SO FAR:").strip()
        already_acted = "(none yet)" not in work and work != ""

        if already_acted:
            # Second pass: wrap up using whatever the tool returned.
            return json.dumps(
                {
                    "thought": "[Mock LLM] A tool has run; summarising its output for the user.",
                    "action": "FINAL",
                    "action_input": (
                        "Here's what I found (mock mode — set GEMINI_API_KEY for real "
                        "AI-written answers). See the tool result above for details."
                    ),
                }
            )

        tool, args = _mock_choose_tool(command)
        return json.dumps(
            {
                "thought": (
                    f"[Mock LLM] Keyword match on the command suggests the '{tool}' tool. "
                    "A real LLM would reason more flexibly here."
                ),
                "action": tool,
                "action_input": args,
            }
        )


def _mock_choose_tool(command: str) -> tuple[str, dict]:
    c = command.lower()

    def has(*words: str) -> bool:
        return any(w in c for w in words)

    add = has("add", "create", "new", "save", "schedule", "remind", "write")
    reply = has("reply", "respond", "draft", "compose")

    # Drafting an email reply — resolve the recipient to a real email id.
    if reply and not has("note"):
        target = _resolve_email_target(c)
        if target is not None:
            return "email_draft_reply", {
                "email_id": target,
                "message": _draft_body(command),
            }
        return "email_list", {}

    if has("email", "inbox", "mail"):
        if has("unread"):
            return "email_list", {"unread_only": True}
        if has("search", "find", "from", "about"):
            return "email_search", {"query": _keywords(command)}
        return "email_list", {}

    if has("task", "todo", "to-do", "to do"):
        if add:
            due, priority = _extract_due(command), _extract_priority(command)
            return "tasks_add", {
                "title": _clean_task_title(command),
                "due": due,
                "priority": priority,
            }
        return "tasks_list", {}

    if has("event", "calendar", "meeting", "agenda", "appointment"):
        if add:
            due = _extract_due(command)
            title = _extract_quoted(command)
            return "calendar_add_event", {
                "title": title[0] if title else _clean_task_title(command),
                "date": due,
            }
        return "calendar_list_events", {}

    if has("note"):
        if add:
            quoted = _extract_quoted(command)
            if len(quoted) >= 2:
                return "notes_add", {"title": quoted[0], "content": quoted[1]}
            if len(quoted) == 1:
                return "notes_add", {"title": quoted[0], "content": quoted[0]}
            return "notes_add", {"title": _title(command), "content": command}
        if has("search", "find", "look"):
            return "notes_search", {"query": _keywords(command)}
        return "notes_list", {}

    # Safe default: list notes.
    return "notes_list", {}


def _resolve_email_target(lowered_command: str) -> str | None:
    """Best-effort: match a name in the command to a sender in the mock inbox."""
    try:
        from tools import backends

        for e in backends.email_list():
            sender = e["from"].split("<", 1)[0].strip().lower()
            for token in re.findall(r"[a-z]+", sender):
                if len(token) > 2 and token in lowered_command:
                    return e["id"]
    except Exception:
        return None
    return None


def _draft_body(command: str) -> str:
    """Pull the intended message out of a 'reply ... telling/saying/that ...' command."""
    m = re.search(
        r"(?i)\b(telling|tell|saying|say|that|to say|with)\b\s+"
        r"(?:her|him|them|me|us|you)?\s*[:,]?\s*(.+)$",
        command,
    )
    if m:
        body = m.group(2).strip().rstrip(".")
        # Rephrase first-person 'I'll' into the drafted reply voice as-is.
        return body[0].upper() + body[1:] + "." if body else _draft_fallback()
    return _draft_fallback()


def _draft_fallback() -> str:
    return "Thanks for your email — I'll follow up shortly. (drafted in mock mode)"


# --------------------------------------------------------------------------- #
# Small text helpers                                                          #
# --------------------------------------------------------------------------- #
_STOP = {
    "the", "a", "an", "my", "me", "please", "find", "search", "for", "about",
    "show", "list", "get", "all", "any", "with", "to", "of", "in", "on", "and",
    "note", "notes", "email", "emails", "task", "tasks", "event", "events",
}


def _keywords(command: str) -> str:
    words = re.findall(r"[A-Za-z0-9']+", command.lower())
    kept = [w for w in words if w not in _STOP]
    return " ".join(kept) or command


def _title(command: str) -> str:
    words = command.split()
    return " ".join(words[:6]) + ("..." if len(words) > 6 else "")


def _extract_quoted(command: str) -> list[str]:
    """Return text inside single, double, or curly quotes, in order."""
    return [m.strip() for m in re.findall(r"['\"“”‘’](.+?)['\"“”‘’]", command) if m.strip()]


def _extract_due(command: str) -> str:
    m = re.search(r"\b(\d{4}-\d{2}-\d{2})\b", command)
    return m.group(1) if m else ""


def _extract_priority(command: str) -> str:
    m = re.search(r"(?i)\b(low|medium|high)\b", command)
    return m.group(1).lower() if m else "medium"


def _clean_task_title(command: str) -> str:
    """Strip leading verbs and trailing 'due/priority' clauses to get a tidy title."""
    quoted = _extract_quoted(command)
    if quoted:
        return quoted[0]
    t = re.sub(
        r"(?i)^\s*(please\s+)?(add|create|new|set up|schedule|remind me to|remind me)\s+",
        "",
        command,
    )
    t = re.sub(r"(?i)^(a\s+|an\s+)?(task|to-?do|reminder|event|meeting)\s+(to\s+)?", "", t)
    t = re.split(r"(?i),?\s*\b(due|by|on)\b", t)[0]
    t = re.sub(r"(?i),?\s*(low|medium|high)\s+priority", "", t)
    return t.strip(" ,.\t").strip() or command


def _between(text: str, start: str, end: str) -> str:
    try:
        return text.split(start, 1)[1].split(end, 1)[0]
    except IndexError:
        return ""


def _after(text: str, marker: str) -> str:
    return text.split(marker, 1)[1] if marker in text else ""


def _short(text: str, n: int = 200) -> str:
    text = (text or "").replace("\n", " ")
    return text[:n] + ("..." if len(text) > n else "")


# --------------------------------------------------------------------------- #
# Factory                                                                     #
# --------------------------------------------------------------------------- #
def build_llm() -> BaseLLM:
    """Instantiate the configured provider, degrading to the mock LLM if the
    real provider cannot be constructed (e.g. missing key)."""
    provider = settings.llm_provider
    try:
        if provider == "gemini":
            return GeminiLLM()
        if provider == "ollama":
            return OllamaLLM()
    except LLMError:
        return MockLLM()
    return MockLLM()
