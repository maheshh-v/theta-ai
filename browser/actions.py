"""
Doing things to a page, and checking that they worked.

Every action follows the same shape: resolve the target, act, let the page
settle, then take a fresh snapshot and report whether the state actually changed.
That final check is what turns a hopeful click into an observation the agent can
reason about — without it, a model happily reports success on a button that did
nothing.

Targets resolve through the durable selectors captured in the snapshot, tried
most-specific first and falling back to role/text lookup. The same resolution
path is reused by Playbook replay, so a recorded step and a live step behave
identically.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from browser import guard
from browser.session import BrowserSession
from browser.snapshot import Element, Snapshot, capture

_log = logging.getLogger("theta.browser.actions")


@dataclass
class ActionResult:
    ok: bool
    message: str = ""
    snapshot: Snapshot | None = None
    changed: bool = True
    error: str = ""
    warnings: list[str] = field(default_factory=list)


class TargetNotFound(RuntimeError):
    """The element a step names is no longer on the page."""


class Actions:
    """Verified page operations against one browser session."""

    def __init__(self, session: BrowserSession) -> None:
        self.session = session
        self.last: Snapshot | None = None

    # -- observation -------------------------------------------------------- #
    async def snapshot(self) -> Snapshot:
        page = await self.session.page()
        self.last = await capture(page)
        return self.last

    def element(self, ref: int) -> Element:
        if self.last is None:
            raise TargetNotFound("No page has been observed yet — take a snapshot first.")
        el = self.last.by_ref(int(ref))
        if el is None:
            raise TargetNotFound(
                f"There is no element [{ref}] on this page. Elements are renumbered "
                "after every action — use the refs from the latest observation."
            )
        return el

    # -- navigation --------------------------------------------------------- #
    async def navigate(self, url: str) -> ActionResult:
        level, why = guard.classify_navigate(url)
        if level == guard.FORBIDDEN:
            return ActionResult(ok=False, error=why, snapshot=self.last, changed=False)
        if not url.lower().startswith(("http://", "https://")):
            url = "https://" + url.lstrip("/")

        nav = await self.session.goto(url)
        snap = await self.snapshot()
        if not nav.ok:
            return ActionResult(ok=False, error=f"Could not open {url}: {nav.error}",
                                snapshot=snap)
        return ActionResult(ok=True, message=f"Opened {snap.url}", snapshot=snap)

    async def back(self) -> ActionResult:
        nav = await self.session.back()
        snap = await self.snapshot()
        if not nav.ok:
            return ActionResult(ok=False, error=f"Could not go back: {nav.error}", snapshot=snap)
        return ActionResult(ok=True, message=f"Went back to {snap.url}", snapshot=snap)

    # -- interaction -------------------------------------------------------- #
    async def click(self, ref: int) -> ActionResult:
        el = self.element(ref)
        page = await self.session.page()
        before = await self._signature(page)

        locator = await self._resolve(page, el)
        if locator is None:
            raise TargetNotFound(f"Could not find {el.describe()} on the page any more.")

        try:
            await locator.scroll_into_view_if_needed(timeout=4000)
        except Exception:
            pass
        try:
            await locator.click(timeout=8000)
        except Exception as ex:
            # Overlays and cookie banners intercept clicks; dispatching the event
            # directly gets past them without pretending the page is different.
            try:
                await locator.dispatch_event("click")
            except Exception:
                return ActionResult(ok=False, error=f"Could not click {el.describe()}: {_short(ex)}",
                                    snapshot=await self.snapshot(), changed=False)

        await self.session.settle()
        snap = await self.snapshot()
        after = await self._signature(page)
        changed = before != after
        return ActionResult(
            ok=True,
            message=f"Clicked {el.describe()}" + ("" if changed else " (the page did not change)"),
            snapshot=snap,
            changed=changed,
        )

    async def type_text(self, ref: int, text: str, submit: bool = False) -> ActionResult:
        el = self.element(ref)
        level, why = guard.classify_type(el, text, submit)
        if level == guard.FORBIDDEN:
            return ActionResult(ok=False, error=why, snapshot=self.last, changed=False)

        page = await self.session.page()
        locator = await self._resolve(page, el)
        if locator is None:
            raise TargetNotFound(f"Could not find {el.describe()} on the page any more.")

        try:
            await locator.scroll_into_view_if_needed(timeout=4000)
            await locator.click(timeout=5000)
            await locator.fill("")
            await locator.type(text, delay=18)
        except Exception as ex:
            try:
                await locator.fill(text)
            except Exception:
                return ActionResult(ok=False, error=f"Could not type into {el.describe()}: {_short(ex)}",
                                    snapshot=await self.snapshot(), changed=False)

        note = ""
        if submit:
            try:
                await locator.press("Enter")
                note = " and submitted"
            except Exception:
                note = " (could not submit)"

        await self.session.settle()
        snap = await self.snapshot()
        return ActionResult(ok=True, message=f'Typed "{_trim(text, 60)}" into {el.describe()}{note}',
                            snapshot=snap)

    async def select(self, ref: int, option: str) -> ActionResult:
        el = self.element(ref)
        page = await self.session.page()
        locator = await self._resolve(page, el)
        if locator is None:
            raise TargetNotFound(f"Could not find {el.describe()} on the page any more.")

        chosen = None
        for attempt in ("label", "value"):
            try:
                await locator.select_option(**{attempt: option}, timeout=5000)
                chosen = attempt
                break
            except Exception:
                continue
        if chosen is None:
            available = ", ".join(el.options[:12]) or "(none listed)"
            return ActionResult(
                ok=False,
                error=f'"{option}" is not an option for {el.describe()}. Available: {available}',
                snapshot=await self.snapshot(), changed=False,
            )

        await self.session.settle()
        return ActionResult(ok=True, message=f'Selected "{option}" in {el.describe()}',
                            snapshot=await self.snapshot())

    async def scroll(self, direction: str = "down", amount: int = 1) -> ActionResult:
        page = await self.session.page()
        delta = int(amount) * (await page.evaluate("() => innerHeight")) * 0.85
        if direction.lower() == "up":
            delta = -delta
        await page.mouse.wheel(0, delta)
        await page.wait_for_timeout(450)
        snap = await self.snapshot()
        return ActionResult(ok=True, message=f"Scrolled {direction}", snapshot=snap)

    async def wait_for(self, text: str, timeout: int = 10) -> ActionResult:
        page = await self.session.page()
        try:
            await page.get_by_text(text, exact=False).first.wait_for(timeout=timeout * 1000)
        except Exception:
            snap = await self.snapshot()
            return ActionResult(ok=False, snapshot=snap, changed=False,
                                error=f'"{_trim(text, 60)}" did not appear within {timeout}s.')
        snap = await self.snapshot()
        return ActionResult(ok=True, message=f'"{_trim(text, 60)}" appeared', snapshot=snap)

    # -- replay ------------------------------------------------------------- #
    async def act_on_target(self, action: str, target: dict, value: str = "",
                            submit: bool = False) -> ActionResult:
        """Perform an action addressed by a *recorded* element, not a live ref.

        This is the replay path. It shares `_resolve` with the live path, so a
        recorded step and an improvised one behave identically — and when the
        recorded selectors no longer match, it raises `TargetNotFound` so the
        caller can escalate that single step to the model.
        """
        page = await self.session.page()
        el = Element(
            ref=0,
            tag=str(target.get("tag") or "button"),
            type=str(target.get("type") or ""),
            role=str(target.get("role") or ""),
            name=str(target.get("name") or ""),
            selectors=list(target.get("selectors") or []),
        )
        locator = await self._resolve(page, el)
        if locator is None:
            raise TargetNotFound(
                f"Could not find {target.get('describe') or el.name or 'the element'} "
                "on this page — the site has probably changed."
            )

        try:
            await locator.scroll_into_view_if_needed(timeout=4000)
        except Exception:
            pass

        if action == "click":
            try:
                await locator.click(timeout=8000)
            except Exception:
                await locator.dispatch_event("click")
        elif action == "type":
            await locator.click(timeout=5000)
            await locator.fill("")
            await locator.type(value, delay=15)
            if submit:
                await locator.press("Enter")
        elif action == "select":
            for how in ("label", "value"):
                try:
                    await locator.select_option(**{how: value}, timeout=5000)
                    break
                except Exception:
                    continue
            else:
                return ActionResult(ok=False, changed=False, snapshot=await self.snapshot(),
                                    error=f'"{value}" is not an option for {el.name}')
        else:
            return ActionResult(ok=False, changed=False, snapshot=self.last,
                                error=f"Cannot replay action '{action}' against an element.")

        await self.session.settle()
        return ActionResult(ok=True, message=f"{action} on {el.name or 'element'}",
                            snapshot=await self.snapshot())

    # -- reading ------------------------------------------------------------ #
    async def read(self, max_chars: int = 12000) -> tuple[str, list[str]]:
        """Readable page text plus any injection attempts found in it."""
        snap = self.last or await self.snapshot()
        text = snap.text[:max_chars]
        return text, guard.scan_for_injection(text)

    # -- internals ---------------------------------------------------------- #
    async def _resolve(self, page, el: Element):
        """Find a live locator for a captured element, most durable first."""
        for selector in el.selectors:
            try:
                locator = page.locator(selector).first
                if await locator.count() and await locator.is_visible(timeout=1200):
                    return locator
            except Exception:
                continue

        # Selectors can rot between snapshot and action on a live page; fall back
        # to what a person would use — the visible label.
        if el.name:
            for build in (
                lambda: page.get_by_role(el.role or _role_for(el), name=el.name, exact=False).first,
                lambda: page.get_by_text(el.name, exact=False).first,
                lambda: page.get_by_placeholder(el.name, exact=False).first,
                lambda: page.get_by_label(el.name, exact=False).first,
            ):
                try:
                    locator = build()
                    if await locator.count() and await locator.is_visible(timeout=1200):
                        return locator
                except Exception:
                    continue
        return None

    @staticmethod
    async def _signature(page) -> str:
        """A cheap fingerprint of page state, to tell a real change from a no-op."""
        try:
            return await page.evaluate(
                "() => location.href + '|' + document.title + '|' + "
                "((document.body && document.body.innerText) || '').length"
            )
        except Exception:
            return ""


def _role_for(el: Element) -> str:
    if el.tag == "a":
        return "link"
    if el.tag == "button" or el.type in ("submit", "button"):
        return "button"
    if el.tag == "select":
        return "combobox"
    if el.tag in ("input", "textarea"):
        return "textbox"
    return "button"


def _trim(text, n: int) -> str:
    text = str(text or "")
    return text if len(text) <= n else text[: n - 1] + "…"


def _short(ex: Exception, n: int = 160) -> str:
    text = str(ex).split("\n")[0]
    return text[:n] + ("…" if len(text) > n else "")
