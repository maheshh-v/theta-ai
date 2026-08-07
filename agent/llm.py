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
    FALLBACK_MODELS = [
        "gemini-flash-latest",
        "gemini-2.5-flash",
        "gemini-2.0-flash",
        "gemini-1.5-flash",
    ]

    def __init__(self, api_key: str | None = None, model: str | None = None) -> None:
        key = api_key if api_key is not None else settings.gemini_api_key
        if not key:
            raise LLMError("No Gemini API key set. Add one in Settings or .env.")
        self.api_key = key
        model = model or settings.gemini_model
        # Configured model first, then fallbacks (de-duplicated).
        self.models = [model] + [m for m in self.FALLBACK_MODELS if m != model]
        self.label = f"Gemini ({model})"

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
                # Quota/rate limits are often per-model — try the next candidate
                # before giving up entirely.
                last_err = f"model '{model}' rate-limited (429)"
                continue
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

    def __init__(self, host: str | None = None, model: str | None = None) -> None:
        self.host = (host or settings.ollama_host).rstrip("/")
        self.model = model or settings.ollama_model
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
    """Keyword → tool mapping for the keyless demo LLM. It cannot resolve real
    Gmail message ids, so email intents fall back to listing; it shines on the
    local notes/tasks tools."""
    c = command.lower()

    def has(*words: str) -> bool:
        return any(w in c for w in words)

    add = has("add", "create", "new", "save", "schedule", "remind")

    if has("email", "inbox", "mail", "gmail"):
        if has("unread"):
            return "gmail_list", {"unread_only": True}
        if has("search", "find", "from", "about"):
            return "gmail_search", {"query": _keywords(command)}
        return "gmail_list", {}

    if has("reply", "respond", "draft", "compose"):
        # A real message id can't be resolved without Gmail; list instead.
        return "gmail_list", {}

    if has("event", "calendar", "meeting", "appointment"):
        if add:
            title = _extract_quoted(command)
            return "calendar_add", {
                "title": title[0] if title else _clean_task_title(command),
                "date": _extract_due(command),
            }
        return "calendar_list", {}

    if has("task", "todo", "to-do", "to do"):
        if add:
            return "tasks_add", {
                "title": _clean_task_title(command),
                "due": _extract_due(command),
                "priority": _extract_priority(command),
            }
        return "tasks_list", {}

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
def build_llm(
    provider: str | None = None,
    api_key: str | None = None,
    gemini_model: str | None = None,
    ollama_host: str | None = None,
    ollama_model: str | None = None,
) -> BaseLLM:
    """Instantiate the requested provider (falling back to global settings for
    any unset field), degrading to the mock LLM if it cannot be constructed
    (e.g. missing key). Callers pass per-session overrides; no args reproduces
    the original env-driven behaviour."""
    provider = provider or settings.llm_provider
    try:
        if provider == "gemini":
            return GeminiLLM(api_key=api_key, model=gemini_model)
        if provider == "ollama":
            return OllamaLLM(host=ollama_host, model=ollama_model)
    except LLMError:
        return MockLLM()
    return MockLLM()
