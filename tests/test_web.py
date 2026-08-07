"""
The web layer: result parsing, readable-text extraction, and the SSRF guard.

The guard tests matter: the agent chooses which URLs to fetch, so a page it reads
must never be able to steer it into the host's own network.
"""

from __future__ import annotations

import pytest

from web import fetch as fetch_mod
from web import search as search_mod
from web.fetch import FetchError, check_url
from web.search import SearchResult, _DDGParser, _dedupe, _unwrap_ddg

DDG_HTML = """
<div class="result results_links">
  <h2 class="result__title">
    <a rel="nofollow" class="result__a" href="https://example.org/a">First <b>hit</b></a>
  </h2>
  <a class="result__snippet" href="#">A snippet with <b>bold</b> text.</a>
</div>
<div class="result results_links">
  <h2 class="result__title">
    <a rel="nofollow" class="result__a"
       href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fb&amp;rut=x">Second</a>
  </h2>
  <a class="result__snippet" href="#">Another snippet.</a>
</div>
"""


# --------------------------------------------------------------------------- #
# Search parsing                                                              #
# --------------------------------------------------------------------------- #
def test_parses_titles_urls_and_snippets():
    parser = _DDGParser()
    parser.feed(DDG_HTML)

    assert [r.url for r in parser.results] == ["https://example.org/a", "https://example.com/b"]
    assert parser.results[0].title == "First hit"          # nested markup flattened
    assert parser.results[0].snippet == "A snippet with bold text."


def test_redirect_wrapped_urls_are_unwrapped():
    wrapped = "//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fpage&rut=abc"
    assert _unwrap_ddg(wrapped) == "https://example.com/page"


def test_direct_urls_pass_through():
    assert _unwrap_ddg("https://example.com/x") == "https://example.com/x"


def test_dedupe_ignores_trailing_slash_and_fragment():
    results = [
        SearchResult("A", "https://e.example/p"),
        SearchResult("B", "https://e.example/p/"),
        SearchResult("C", "https://e.example/p#section"),
        SearchResult("D", "https://e.example/other"),
    ]
    assert [r.title for r in _dedupe(results)] == ["A", "D"]


def test_site_is_derived_from_the_url():
    assert SearchResult("t", "https://www.nature.com/articles/x").site == "nature.com"


def test_search_ignores_an_empty_query():
    assert search_mod.search("   ") == []


# --------------------------------------------------------------------------- #
# The SSRF guard                                                              #
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("url", [
    "file:///etc/passwd",
    "ftp://example.com/x",
    "javascript:alert(1)",
])
def test_non_http_schemes_are_refused(url):
    with pytest.raises(FetchError, match="non-http"):
        check_url(url)


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "169.254.169.254"])
def test_private_and_loopback_addresses_are_refused(host):
    with pytest.raises(FetchError, match="private address"):
        check_url(f"http://{host}/admin")


def test_private_addresses_are_allowed_when_explicitly_enabled(monkeypatch):
    from config import settings
    monkeypatch.setattr(settings, "allow_private_urls", True, raising=False)
    assert check_url("http://127.0.0.1:7860/x") == "http://127.0.0.1:7860/x"


def test_a_bare_domain_gets_https():
    assert check_url("example.com/page").startswith("https://example.com")


def test_fetch_returns_a_failed_page_rather_than_raising():
    page = fetch_mod.fetch("http://127.0.0.1/secret")

    assert page.ok is False
    assert "private address" in page.error


# --------------------------------------------------------------------------- #
# Extraction                                                                  #
# --------------------------------------------------------------------------- #
HTML_PAGE = """
<html><head><title>  The   Real Title </title></head>
<body>
  <nav>Home About Contact</nav>
  <script>var tracking = 1;</script>
  <style>.x{color:red}</style>
  <article>
    <p>Solar generation grew substantially through 2025 across most markets.</p>
    <p>Analysts attribute the change to falling module prices and cheaper finance.</p>
    <p>Storage deployment followed a similar curve in the same period worldwide.</p>
  </article>
  <footer>Copyright notice</footer>
</body></html>
"""


def test_title_is_extracted_and_whitespace_collapsed():
    assert fetch_mod._html_title(HTML_PAGE) == "The Real Title"


def test_scripts_and_styles_never_reach_the_text():
    page = fetch_mod._from_html("https://e.example/a", HTML_PAGE)

    assert page.ok
    assert "Solar generation grew" in page.text
    assert "var tracking" not in page.text
    assert "color:red" not in page.text


def test_a_contentless_page_is_marked_unreadable():
    page = fetch_mod._from_html("https://e.example/b", "<html><body><p>Hi</p></body></html>")

    assert page.ok is False
    assert "No readable text" in page.error


def test_fallback_extractor_works_without_trafilatura(monkeypatch):
    """A minimal install (no trafilatura) must still read pages."""
    import builtins
    real_import = builtins.__import__

    def no_trafilatura(name, *args, **kwargs):
        if name == "trafilatura":
            raise ImportError("not installed")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", no_trafilatura)
    page = fetch_mod._from_html("https://e.example/a", HTML_PAGE)

    assert page.ok
    assert "falling module prices" in page.text


def test_tidy_collapses_blank_runs():
    assert fetch_mod._tidy("a\n\n\n\nb   c\n") == "a\n\nb c"
