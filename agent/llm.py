"""
Language models behind one interface: `complete(system, user) -> str`.

Three providers, all plain REST — no SDKs, no vendor lock-in:

* **gemini** — Google AI Studio's free tier. The cheapest way to run Theta for
  real. Defaults to `gemini-flash-latest`, an alias that survives Google
  retiring pinned model names.
* **ollama** — a local model. Fully offline; nothing leaves the machine.
* **openai** — any OpenAI-compatible `/chat/completions` endpoint, which covers
  OpenAI, Groq, OpenRouter, Together, vLLM and LM Studio.

There is deliberately **no mock provider**. Theta's output is a researched brief;
a keyword-matching stand-in could only produce a convincing-looking fake, so when
no model is configured the app says so instead.

Every provider raises `LLMError` on failure, and calls are stateless so the
research pipeline can fan them out across threads.
"""

from __future__ import annotations

import requests

from config import settings


class LLMError(RuntimeError):
    """Raised when a provider cannot produce a completion."""


# Reasoning models (Gemini 2.5+, o-series) spend output tokens on internal
# thinking before writing a single visible character, and that spend counts
# against the limit. Budgets here are sized for thinking + answer; too small and
# replies come back truncated mid-JSON, which is far worse than a clean error.
DEFAULT_MAX_TOKENS = 4096


class BaseLLM:
    name: str = "base"
    label: str = "Base LLM"

    def complete(
        self, system: str, user: str, max_tokens: int = DEFAULT_MAX_TOKENS
    ) -> str:  # pragma: no cover
        raise NotImplementedError


# --------------------------------------------------------------------------- #
# Gemini                                                                      #
# --------------------------------------------------------------------------- #
class GeminiLLM(BaseLLM):
    name = "gemini"
    BASE = "https://generativelanguage.googleapis.com/v1beta/models"
    # Tried in order after the configured model, so a retired name self-heals.
    FALLBACK_MODELS = ["gemini-flash-latest", "gemini-2.0-flash", "gemini-flash-lite-latest"]

    def __init__(self, api_key: str | None = None, model: str | None = None) -> None:
        key = (api_key if api_key is not None else settings.gemini_api_key) or ""
        if not key.strip():
            raise LLMError("No Gemini API key set. Add one in Settings → Model.")
        self.api_key = key.strip()
        model = (model or settings.gemini_model).strip()
        self.models = [model] + [m for m in self.FALLBACK_MODELS if m != model]
        self.label = f"Gemini ({model})"

    def complete(
        self, system: str, user: str, max_tokens: int = DEFAULT_MAX_TOKENS
    ) -> str:
        text, truncated = self._attempt(system, user, max_tokens)
        if truncated:
            # The reply was cut off mid-sentence, so it is unparseable whatever it
            # says. Retry once with room to finish rather than handing the caller
            # half a JSON object.
            text, still_truncated = self._attempt(system, user, max_tokens * 2)
            if still_truncated and not text:
                raise LLMError(
                    "Gemini used its entire token budget on internal reasoning and "
                    "returned nothing. Try a smaller question or a non-thinking model."
                )
        return text

    def _attempt(self, system: str, user: str, max_tokens: int) -> tuple[str, bool]:
        payload = {
            "system_instruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": user}]}],
            "generationConfig": {"temperature": 0.2, "maxOutputTokens": max_tokens},
        }
        last_err = "unknown error"
        for model in self.models:
            url = f"{self.BASE}/{model}:generateContent?key={self.api_key}"
            try:
                resp = requests.post(url, json=payload, timeout=180)
            except requests.RequestException as ex:
                raise LLMError(f"Network error contacting Gemini: {ex}") from ex

            if resp.status_code == 404:
                last_err = f"model '{model}' not found"
                continue
            if resp.status_code == 429:
                # Free-tier quota is per-model, so the next candidate may work.
                last_err = f"model '{model}' is rate-limited (free-tier quota)"
                continue
            if resp.status_code in (400, 401, 403):
                raise LLMError(
                    f"Gemini rejected the request (HTTP {resp.status_code}). Check the "
                    f"API key in Settings → Model. Detail: {_short(resp.text)}"
                )
            if resp.status_code != 200:
                last_err = f"HTTP {resp.status_code}: {_short(resp.text)}"
                continue

            text, reason = _extract_gemini_text(resp.json())
            if text or reason == "MAX_TOKENS":
                return text, reason == "MAX_TOKENS"
            last_err = f"empty response (finish reason: {reason or 'unknown'})"

        raise LLMError(f"Gemini failed for every model candidate ({last_err}).")


def _extract_gemini_text(data: dict) -> tuple[str, str]:
    """Return (text, finishReason). A MAX_TOKENS finish means the reply was cut
    off — thinking tokens are billed against the same budget as the answer."""
    try:
        candidate = data["candidates"][0]
    except (KeyError, IndexError, TypeError):
        return "", ""
    parts = (candidate.get("content") or {}).get("parts") or []
    text = "".join(p.get("text", "") for p in parts if isinstance(p, dict)).strip()
    return text, str(candidate.get("finishReason") or "")


# --------------------------------------------------------------------------- #
# Ollama (local)                                                              #
# --------------------------------------------------------------------------- #
class OllamaLLM(BaseLLM):
    name = "ollama"

    def __init__(self, host: str | None = None, model: str | None = None) -> None:
        self.host = (host or settings.ollama_host).rstrip("/")
        self.model = model or settings.ollama_model
        self.label = f"Ollama ({self.model})"

    def complete(
        self, system: str, user: str, max_tokens: int = DEFAULT_MAX_TOKENS
    ) -> str:
        try:
            resp = requests.post(
                f"{self.host}/api/generate",
                json={
                    "model": self.model,
                    "system": system,
                    "prompt": user,
                    "stream": False,
                    "options": {"temperature": 0.2, "num_predict": max_tokens},
                },
                timeout=300,
            )
        except requests.RequestException as ex:
            raise LLMError(
                f"Could not reach Ollama at {self.host}. Is it running (`ollama serve`) "
                f"and is the model pulled (`ollama pull {self.model}`)? Detail: {ex}"
            ) from ex
        if resp.status_code == 404:
            raise LLMError(
                f"Ollama has no model named '{self.model}'. Pull it with "
                f"`ollama pull {self.model}`."
            )
        if resp.status_code != 200:
            raise LLMError(f"Ollama error HTTP {resp.status_code}: {_short(resp.text)}")
        return (resp.json().get("response") or "").strip()


# --------------------------------------------------------------------------- #
# OpenAI-compatible                                                           #
# --------------------------------------------------------------------------- #
class OpenAICompatLLM(BaseLLM):
    """Any `/chat/completions` endpoint: OpenAI, Groq, OpenRouter, vLLM, LM Studio."""

    name = "openai"

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
    ) -> None:
        self.base_url = (base_url or settings.openai_base_url).rstrip("/")
        self.model = model or settings.openai_model
        key = (api_key if api_key is not None else settings.openai_api_key) or ""
        self.api_key = key.strip()
        if not self.api_key and not _is_local(self.base_url):
            raise LLMError("No API key set for the OpenAI-compatible endpoint.")
        self.label = f"{_host_label(self.base_url)} ({self.model})"

    def complete(
        self, system: str, user: str, max_tokens: int = DEFAULT_MAX_TOKENS
    ) -> str:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        try:
            resp = requests.post(
                f"{self.base_url}/chat/completions",
                headers=headers,
                json={
                    "model": self.model,
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                    "temperature": 0.2,
                    "max_tokens": max_tokens,
                },
                timeout=180,
            )
        except requests.RequestException as ex:
            raise LLMError(f"Network error contacting {self.base_url}: {ex}") from ex

        if resp.status_code in (401, 403):
            raise LLMError("The endpoint rejected the API key. Check Settings → Model.")
        if resp.status_code == 429:
            raise LLMError("Rate-limited by the endpoint. Wait a moment and retry.")
        if resp.status_code != 200:
            raise LLMError(f"Endpoint error HTTP {resp.status_code}: {_short(resp.text)}")

        try:
            return (resp.json()["choices"][0]["message"]["content"] or "").strip()
        except (KeyError, IndexError, TypeError) as ex:
            raise LLMError(f"Unexpected response shape from {self.base_url}: {ex}") from ex


def _is_local(base_url: str) -> bool:
    return any(h in base_url for h in ("localhost", "127.0.0.1", "0.0.0.0"))


def _host_label(base_url: str) -> str:
    from urllib.parse import urlparse

    host = (urlparse(base_url).hostname or base_url).replace("api.", "")
    return host.split(".")[0].capitalize() or "OpenAI-compatible"


# --------------------------------------------------------------------------- #
# Factory                                                                     #
# --------------------------------------------------------------------------- #
def build_llm(
    provider: str | None = None,
    api_key: str | None = None,
    gemini_model: str | None = None,
    ollama_host: str | None = None,
    ollama_model: str | None = None,
    openai_base_url: str | None = None,
    openai_model: str | None = None,
) -> BaseLLM:
    """Build the requested provider, falling back to global settings for any
    unset field. Raises LLMError if it cannot be constructed — callers surface
    that to the user rather than silently substituting something else."""
    provider = (provider or settings.llm_provider).lower()
    if provider == "ollama":
        return OllamaLLM(host=ollama_host, model=ollama_model)
    if provider == "openai":
        return OpenAICompatLLM(
            api_key=api_key, base_url=openai_base_url, model=openai_model
        )
    return GeminiLLM(api_key=api_key, model=gemini_model)


def _short(text: str, n: int = 200) -> str:
    text = (text or "").replace("\n", " ")
    return text[:n] + ("…" if len(text) > n else "")
