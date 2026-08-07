"""
Fetch a URL and return readable text.

Raw HTML is useless to a language model: navigation, cookie banners and footers
drown the article and burn the context window. `fetch()` returns just the main
content, using trafilatura (the extractor the research-agent ecosystem has
settled on) with a stdlib fallback so a minimal install still works. PDFs — a
large slice of primary sources — are extracted too.

**Safety:** the agent chooses these URLs, so `fetch` refuses anything that is not
http(s) and, unless `THETA_ALLOW_PRIVATE_URLS=1`, anything resolving to a private
or loopback address. Without that guard a crafted page could steer Theta into the
host's own network (SSRF).
"""

from __future__ import annotations

import ipaddress
import logging
import re
import socket
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
from html import unescape
from urllib.parse import urlparse

import requests

from config import settings

_log = logging.getLogger("theta.fetch")

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36 Theta/2.0"
)
_MAX_BYTES = 5 * 1024 * 1024  # stop reading a response after 5 MB


class FetchError(RuntimeError):
    """Raised when a URL cannot be retrieved or yields no readable text."""


@dataclass
class Page:
    url: str
    title: str = ""
    text: str = ""
    ok: bool = True
    error: str = ""
    fetched_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds")
    )

    @property
    def site(self) -> str:
        try:
            return urlparse(self.url).netloc.replace("www.", "")
        except ValueError:
            return ""

    def to_dict(self, max_chars: int | None = None) -> dict:
        text = self.text if max_chars is None else self.text[:max_chars]
        return {
            "url": self.url,
            "title": self.title,
            "site": self.site,
            "ok": self.ok,
            "error": self.error,
            "chars": len(self.text),
            "text": text,
        }


# --------------------------------------------------------------------------- #
# URL safety                                                                  #
# --------------------------------------------------------------------------- #
def check_url(url: str) -> str:
    """Validate and normalise a URL, or raise FetchError explaining the refusal."""
    url = (url or "").strip()
    if not url:
        raise FetchError("No URL given.")
    if not url.startswith(("http://", "https://")):
        # A bare domain is a common model slip; everything else is refused.
        if re.match(r"^[\w.-]+\.[a-z]{2,}(/|$)", url, re.I):
            url = "https://" + url
        else:
            raise FetchError(f"Refusing to fetch a non-http(s) URL: {url!r}")

    host = urlparse(url).hostname
    if not host:
        raise FetchError(f"Could not parse a hostname from {url!r}.")
    if settings.allow_private_urls:
        return url

    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as ex:
        raise FetchError(f"Could not resolve {host}: {ex}") from ex
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
            raise FetchError(
                f"Refusing to fetch {host} — it resolves to a private address "
                f"({ip}). Set THETA_ALLOW_PRIVATE_URLS=1 to allow this."
            )
    return url


# --------------------------------------------------------------------------- #
# Fetch                                                                       #
# --------------------------------------------------------------------------- #
def fetch(url: str, timeout: int | None = None) -> Page:
    """Retrieve one URL and extract its readable text. Never raises: failures
    come back as a Page with ok=False so one dead link cannot end a research run."""
    try:
        url = check_url(url)
    except FetchError as ex:
        return Page(url=url, ok=False, error=str(ex))

    timeout = timeout or settings.fetch_timeout
    try:
        resp = requests.get(
            url,
            headers={"User-Agent": _UA, "Accept-Language": "en-US,en;q=0.9"},
            timeout=timeout,
            stream=True,
        )
    except requests.RequestException as ex:
        return Page(url=url, ok=False, error=f"Network error: {ex}")

    with resp:
        if resp.status_code != 200:
            return Page(url=url, ok=False, error=f"HTTP {resp.status_code}")

        ctype = (resp.headers.get("Content-Type") or "").lower()
        try:
            body = _read_capped(resp)
        except requests.RequestException as ex:
            return Page(url=url, ok=False, error=f"Download failed: {ex}")
        final_url = resp.url or url

    if "pdf" in ctype or final_url.lower().endswith(".pdf"):
        return _from_pdf(final_url, body)
    if ctype and not any(t in ctype for t in ("html", "text", "xml", "json")):
        return Page(url=final_url, ok=False, error=f"Unsupported content type: {ctype}")

    html = body.decode(resp.encoding or "utf-8", errors="replace") if isinstance(body, bytes) else body
    return _from_html(final_url, html)


def fetch_many(urls: list[str], timeout: int | None = None) -> list[Page]:
    """Fetch several URLs in parallel, preserving input order."""
    urls = [u for u in urls if u]
    if not urls:
        return []
    workers = max(1, min(settings.fetch_workers, len(urls)))
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="fetch") as pool:
        return list(pool.map(lambda u: fetch(u, timeout), urls))


def _read_capped(resp: requests.Response) -> bytes:
    chunks, total = [], 0
    for chunk in resp.iter_content(64 * 1024):
        chunks.append(chunk)
        total += len(chunk)
        if total >= _MAX_BYTES:
            break
    return b"".join(chunks)


# --------------------------------------------------------------------------- #
# Extraction                                                                  #
# --------------------------------------------------------------------------- #
def _from_html(url: str, html: str) -> Page:
    title = _html_title(html)
    text = ""
    try:
        import trafilatura

        text = trafilatura.extract(
            html, include_comments=False, include_tables=True, favor_precision=True
        ) or ""
    except ImportError:
        _log.debug("trafilatura not installed; using the built-in extractor")
    except Exception as ex:  # pragma: no cover - extractor edge cases
        _log.debug("trafilatura failed on %s: %s", url, ex)

    if len(text.strip()) < 200:
        # Either trafilatura is absent or the page defeated it (JS-heavy shells,
        # unusual markup). The plain-text fallback is worse but often enough.
        fallback = _strip_html(html)
        if len(fallback) > len(text):
            text = fallback

    text = _tidy(text)
    if len(text) < 80:
        return Page(url=url, title=title, ok=False,
                    error="No readable text found (the page may require JavaScript).")
    return Page(url=url, title=title or url, text=text)


def _from_pdf(url: str, body: bytes) -> Page:
    try:
        import io

        from pypdf import PdfReader
    except ImportError:
        return Page(url=url, ok=False,
                    error="PDF support needs `pypdf` (pip install -r requirements.txt).")
    try:
        reader = PdfReader(io.BytesIO(body))
        title = (reader.metadata or {}).get("/Title") or ""
        pages = [(p.extract_text() or "") for p in reader.pages[:40]]
    except Exception as ex:
        return Page(url=url, ok=False, error=f"Could not read PDF: {ex}")

    text = _tidy("\n\n".join(pages))
    if len(text) < 80:
        return Page(url=url, ok=False, error="PDF contains no extractable text (likely a scan).")
    return Page(url=url, title=str(title) or url.rsplit("/", 1)[-1], text=text)


_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.I | re.S)
_DROP_RE = re.compile(
    r"<(script|style|noscript|template|svg|nav|footer|header|form)\b.*?</\1>", re.I | re.S
)
_TAG_RE = re.compile(r"<[^>]+>")


def _html_title(html: str) -> str:
    m = _TITLE_RE.search(html or "")
    return " ".join(unescape(m.group(1)).split())[:200] if m else ""


def _strip_html(html: str) -> str:
    """Minimal readable-text extraction: drop non-content elements, then tags."""
    cleaned = _DROP_RE.sub(" ", html or "")
    cleaned = re.sub(r"<(p|div|br|li|h[1-6]|tr)\b[^>]*>", "\n", cleaned, flags=re.I)
    return _tidy(unescape(_TAG_RE.sub(" ", cleaned)))


def _tidy(text: str) -> str:
    """Collapse runs of whitespace and blank lines without losing paragraphing."""
    lines = [" ".join(line.split()) for line in (text or "").splitlines()]
    out: list[str] = []
    for line in lines:
        if line or (out and out[-1]):
            out.append(line)
    return "\n".join(out).strip()
