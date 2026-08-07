"""
Web search behind one function: `search(query) -> list[SearchResult]`.

Three providers, all plain REST/HTML — no SDKs:

* **duckduckgo** — the default, because it needs no key and no signup. Theta
  works the moment it is installed. Parsed from the no-JavaScript HTML endpoint.
* **tavily** — a search API built for agents; better ranking and snippets.
* **brave** — an independent index.

Providers raise `SearchError` with a message that says what to do about it. We
never invent results: an empty list means the web genuinely returned nothing.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from html import unescape
from html.parser import HTMLParser
from urllib.parse import parse_qs, urlparse

import requests

from config import settings

_log = logging.getLogger("theta.search")

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)
_TIMEOUT = 20


class SearchError(RuntimeError):
    """Raised when a search provider cannot be reached or refuses the request."""


@dataclass
class SearchResult:
    title: str
    url: str
    snippet: str = ""

    @property
    def site(self) -> str:
        try:
            return urlparse(self.url).netloc.replace("www.", "")
        except ValueError:
            return ""

    def to_dict(self) -> dict:
        return {
            "title": self.title,
            "url": self.url,
            "snippet": self.snippet,
            "site": self.site,
        }


# --------------------------------------------------------------------------- #
# Public entry point                                                          #
# --------------------------------------------------------------------------- #
def search(
    query: str,
    max_results: int = 6,
    provider: str | None = None,
    api_key: str | None = None,
) -> list[SearchResult]:
    """Run a web search. `provider`/`api_key` override the configured defaults
    (the Settings page passes a session's choice through here)."""
    query = (query or "").strip()
    if not query:
        return []
    provider = (provider or settings.search_provider).lower()

    if provider == "tavily":
        return _tavily(query, max_results, api_key or settings.tavily_api_key)
    if provider == "brave":
        return _brave(query, max_results, api_key or settings.brave_api_key)
    return _duckduckgo(query, max_results)


def provider_label(provider: str | None = None) -> str:
    return {
        "duckduckgo": "DuckDuckGo",
        "tavily": "Tavily",
        "brave": "Brave Search",
    }.get((provider or settings.search_provider).lower(), "DuckDuckGo")


# --------------------------------------------------------------------------- #
# DuckDuckGo (keyless)                                                        #
# --------------------------------------------------------------------------- #
class _DDGParser(HTMLParser):
    """Pulls (title, url, snippet) triples out of DuckDuckGo's HTML results.

    The no-JS endpoint is stable and simple: each hit is an `<a class="result__a">`
    followed by an `<a class="result__snippet">`. We track which of the two we are
    inside and accumulate text, so nested markup (`<b>` highlights) is handled.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.results: list[SearchResult] = []
        self._mode: str | None = None   # "title" | "snippet"
        self._buf: list[str] = []
        self._href = ""

    def handle_starttag(self, tag, attrs):
        if tag != "a":
            return
        attr = dict(attrs)
        classes = (attr.get("class") or "").split()
        if "result__a" in classes:
            self._flush()
            self._mode, self._buf = "title", []
            self._href = _unwrap_ddg(attr.get("href", ""))
        elif "result__snippet" in classes:
            self._mode, self._buf = "snippet", []

    def handle_endtag(self, tag):
        if tag == "a" and self._mode:
            text = " ".join("".join(self._buf).split())
            if self._mode == "title":
                if self._href.startswith(("http://", "https://")):
                    self.results.append(SearchResult(title=text, url=self._href))
            elif self._mode == "snippet" and self.results:
                self.results[-1].snippet = text
            self._mode, self._buf = None, []

    def handle_data(self, data):
        if self._mode:
            self._buf.append(data)

    def _flush(self) -> None:
        self._mode, self._buf = None, []


def _unwrap_ddg(href: str) -> str:
    """DuckDuckGo sometimes wraps hits in `/l/?uddg=<encoded target>`."""
    href = unescape(href or "")
    if href.startswith("//"):
        href = "https:" + href
    if "duckduckgo.com/l/" in href:
        target = parse_qs(urlparse(href).query).get("uddg", [""])[0]
        return target or href
    return href


def _duckduckgo(query: str, max_results: int) -> list[SearchResult]:
    """Scrape the no-JavaScript endpoints. Keyless, and rate-limited accordingly:
    DuckDuckGo answers a burst of automated queries with HTTP 202 and a bot-check
    page instead of results. We retry briefly for the transient case and then say
    so plainly — a research agent that invents sources is worse than one that
    admits it cannot search."""
    endpoints = ("https://html.duckduckgo.com/html/", "https://lite.duckduckgo.com/lite/")
    throttled = False
    last_error = "no response"

    for attempt in range(2):
        for url in endpoints:
            try:
                resp = requests.post(
                    url,
                    data={"q": query, "kl": "wt-wt"},
                    headers={"User-Agent": _UA, "Accept-Language": "en-US,en;q=0.9"},
                    timeout=_TIMEOUT,
                )
            except requests.RequestException as ex:
                last_error = f"network error ({ex})"
                continue

            if resp.status_code in (202, 429):
                throttled = True
                last_error = f"HTTP {resp.status_code} (bot check)"
                continue
            if resp.status_code != 200:
                last_error = f"HTTP {resp.status_code}"
                continue

            parser = _DDGParser()
            parser.feed(resp.text)
            results = _dedupe(parser.results)
            if results:
                return results[:max_results]
            # A 200 carrying no parseable hits is also usually an interstitial.
            throttled = True
            last_error = "no results in the response"

        if attempt == 0:
            time.sleep(1.5)  # one short backoff covers the genuinely transient case

    if throttled:
        raise SearchError(
            "DuckDuckGo is rate-limiting this instance (it allows only light "
            "automated use). Add a free Tavily key in Settings → Search — it takes "
            "a minute and is built for exactly this."
        )
    raise SearchError(f"DuckDuckGo search failed: {last_error}.")


# --------------------------------------------------------------------------- #
# Tavily                                                                      #
# --------------------------------------------------------------------------- #
def _tavily(query: str, max_results: int, api_key: str) -> list[SearchResult]:
    if not api_key:
        raise SearchError("No Tavily API key set. Add one in Settings → Search.")
    try:
        resp = requests.post(
            "https://api.tavily.com/search",
            json={
                "api_key": api_key,
                "query": query,
                "max_results": max_results,
                "search_depth": "basic",
            },
            timeout=_TIMEOUT,
        )
    except requests.RequestException as ex:
        raise SearchError(f"Network error contacting Tavily: {ex}") from ex
    if resp.status_code in (401, 403):
        raise SearchError("Tavily rejected the API key. Check it in Settings → Search.")
    if resp.status_code != 200:
        raise SearchError(f"Tavily error HTTP {resp.status_code}: {_short(resp.text)}")

    out = [
        SearchResult(
            title=item.get("title") or item.get("url", ""),
            url=item.get("url", ""),
            snippet=(item.get("content") or "")[:400],
        )
        for item in (resp.json().get("results") or [])
        if item.get("url")
    ]
    return _dedupe(out)[:max_results]


# --------------------------------------------------------------------------- #
# Brave                                                                       #
# --------------------------------------------------------------------------- #
def _brave(query: str, max_results: int, api_key: str) -> list[SearchResult]:
    if not api_key:
        raise SearchError("No Brave API key set. Add one in Settings → Search.")
    try:
        resp = requests.get(
            "https://api.search.brave.com/res/v1/web/search",
            params={"q": query, "count": max_results},
            headers={"Accept": "application/json", "X-Subscription-Token": api_key},
            timeout=_TIMEOUT,
        )
    except requests.RequestException as ex:
        raise SearchError(f"Network error contacting Brave Search: {ex}") from ex
    if resp.status_code in (401, 403):
        raise SearchError("Brave rejected the API key. Check it in Settings → Search.")
    if resp.status_code != 200:
        raise SearchError(f"Brave error HTTP {resp.status_code}: {_short(resp.text)}")

    out = [
        SearchResult(
            title=item.get("title", ""),
            url=item.get("url", ""),
            snippet=_strip_tags(item.get("description", ""))[:400],
        )
        for item in ((resp.json().get("web") or {}).get("results") or [])
        if item.get("url")
    ]
    return _dedupe(out)[:max_results]


# --------------------------------------------------------------------------- #
# Helpers                                                                     #
# --------------------------------------------------------------------------- #
def _dedupe(results: list[SearchResult]) -> list[SearchResult]:
    """Drop repeat URLs, ignoring trailing slashes and tracking fragments."""
    seen: set[str] = set()
    out: list[SearchResult] = []
    for r in results:
        if not r.url:
            continue
        key = r.url.split("#")[0].rstrip("/")
        if key in seen:
            continue
        seen.add(key)
        out.append(r)
    return out


def _strip_tags(text: str) -> str:
    return " ".join(unescape(re.sub(r"<[^>]+>", "", text or "")).split())


def _short(text: str, n: int = 200) -> str:
    text = (text or "").replace("\n", " ")
    return text[:n] + ("…" if len(text) > n else "")
