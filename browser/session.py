"""
Browser lifecycle.

One long-lived Chromium instance with one page, owned by the MCP server process
so state (cookies, login, scroll position, form entries) survives across tool
calls — an agent that lost the page between steps could not complete anything.

Headless by default. Run headful (`THETA_BROWSER_HEADLESS=0`) and you can watch
the agent work and take the keyboard yourself, which is exactly what Theta asks
you to do when a site wants a password.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

from config import settings

_log = logging.getLogger("theta.browser")

# A normal desktop UA. Automation defaults advertise themselves and get served
# degraded pages, which the agent then cannot operate.
_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)


class BrowserError(RuntimeError):
    """Raised when the browser cannot be started or a page cannot be reached."""


@dataclass
class NavResult:
    url: str
    title: str
    ok: bool = True
    error: str = ""


class BrowserSession:
    """Owns the Playwright instance, browser, context and page."""

    def __init__(self) -> None:
        self._pw = None
        self._browser = None
        self._context = None
        self._page = None
        self._lock = asyncio.Lock()
        self.downloads: list[str] = []

    # -- lifecycle ---------------------------------------------------------- #
    @property
    def started(self) -> bool:
        return self._page is not None

    async def start(self) -> None:
        async with self._lock:
            if self._page is not None:
                return
            try:
                from playwright.async_api import async_playwright
            except ImportError as ex:  # pragma: no cover - install-time failure
                raise BrowserError(
                    "Playwright is not installed. Run `pip install -r requirements.txt` "
                    "then `python -m playwright install chromium`."
                ) from ex

            try:
                self._pw = await async_playwright().start()
                self._browser = await self._pw.chromium.launch(
                    headless=settings.browser_headless,
                    args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
                )
                self._context = await self._browser.new_context(
                    viewport={
                        "width": settings.browser_width,
                        "height": settings.browser_height,
                    },
                    user_agent=_UA,
                    locale="en-US",
                    accept_downloads=True,
                )
                self._context.set_default_timeout(settings.browser_timeout * 1000)
                self._page = await self._context.new_page()
            except Exception as ex:
                await self.close()
                raise BrowserError(
                    f"Could not start Chromium: {ex}. If this is a fresh install, run "
                    "`python -m playwright install chromium`."
                ) from ex
            _log.info(
                "Browser started (headless=%s, %dx%d)",
                settings.browser_headless, settings.browser_width, settings.browser_height,
            )

    async def page(self):
        """The active page, starting the browser on first use."""
        if self._page is None:
            await self.start()
        # A click may have opened a new tab; operate on the newest one so the
        # agent follows the user's attention rather than acting on a hidden page.
        if self._context is not None and self._context.pages:
            newest = self._context.pages[-1]
            if newest is not self._page and not newest.is_closed():
                self._page = newest
        return self._page

    async def close(self) -> None:
        for obj, name in ((self._context, "context"), (self._browser, "browser")):
            try:
                if obj is not None:
                    await obj.close()
            except Exception:  # pragma: no cover - teardown is best-effort
                _log.debug("Error closing %s", name)
        try:
            if self._pw is not None:
                await self._pw.stop()
        except Exception:  # pragma: no cover
            pass
        self._pw = self._browser = self._context = self._page = None

    async def reset(self) -> None:
        """Drop all browsing state — the 'log me out of everything' button."""
        await self.close()
        await self.start()

    # -- navigation --------------------------------------------------------- #
    async def goto(self, url: str) -> NavResult:
        page = await self.page()
        try:
            await page.goto(url, wait_until="domcontentloaded",
                            timeout=settings.browser_timeout * 1000)
        except Exception as ex:
            return NavResult(url=page.url, title="", ok=False, error=_short(ex))
        await self.settle()
        return NavResult(url=page.url, title=await _safe_title(page))

    async def back(self) -> NavResult:
        page = await self.page()
        try:
            await page.go_back(wait_until="domcontentloaded")
        except Exception as ex:
            return NavResult(url=page.url, title="", ok=False, error=_short(ex))
        await self.settle()
        return NavResult(url=page.url, title=await _safe_title(page))

    async def settle(self, quiet_ms: int = 400) -> None:
        """Wait for the page to stop moving.

        `networkidle` hangs forever on pages with long-polling or analytics
        beacons, so we wait for it *briefly* and accept whatever we have — the
        snapshot that follows is the real source of truth.
        """
        page = await self.page()
        try:
            await page.wait_for_load_state("domcontentloaded", timeout=5000)
        except Exception:
            pass
        try:
            await page.wait_for_load_state("networkidle", timeout=2500)
        except Exception:
            pass
        await page.wait_for_timeout(quiet_ms)

    async def screenshot(self, path: str) -> str | None:
        """Save a JPEG of the viewport. Small on purpose: these stream to the UI
        after every step, so ~20 KB each keeps the live view genuinely live."""
        try:
            page = await self.page()
            await page.screenshot(path=path, type="jpeg", quality=55, full_page=False)
            return path
        except Exception as ex:  # pragma: no cover - never fail a step over a shot
            _log.debug("Screenshot failed: %s", ex)
            return None


async def _safe_title(page) -> str:
    try:
        return (await page.title()) or ""
    except Exception:
        return ""


def _short(ex: Exception, n: int = 180) -> str:
    text = str(ex).split("\n")[0]
    return text[:n] + ("…" if len(text) > n else "")


# The process-wide session. The MCP server owns exactly one browser.
session = BrowserSession()
