"""
Turning a web page into something a model can act on reliably.

The agent never sees pixels and never guesses coordinates. Each observation is a
numbered list of the page's *visible, interactive* elements:

    [4] <input type=text> "Search Wikipedia" (empty)
    [5] <button> "Search"
    [7] <a> "Sign in" → /login

The model then says `browser_click(ref=5)`. That one decision buys most of this
project's reliability: actions are unambiguous, cheap (no vision tokens), and —
because every element also carries durable selectors — **replayable later without
a model at all**, which is what Playbooks are built on.

Extraction happens in one pass of in-page JavaScript, because a round trip per
element would take seconds on a page with two hundred of them.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# One pass over the DOM: visibility, labelling, and durable selectors.
_COLLECT_JS = r"""
() => {
  const SELECTOR = [
    'a[href]', 'button', 'input', 'select', 'textarea', 'summary', 'label[for]',
    '[role=button]', '[role=link]', '[role=textbox]', '[role=checkbox]',
    '[role=radio]', '[role=tab]', '[role=menuitem]', '[role=combobox]',
    '[role=searchbox]', '[role=switch]', '[role=option]',
    '[contenteditable=""]', '[contenteditable=true]', '[onclick]', '[tabindex]'
  ].join(',');

  const clean = (s) => (s || '').replace(/\s+/g, ' ').trim();

  const visible = (el) => {
    const r = el.getBoundingClientRect();
    if (r.width < 4 || r.height < 4) return false;
    const st = getComputedStyle(el);
    if (st.visibility === 'hidden' || st.display === 'none') return false;
    if (parseFloat(st.opacity || '1') < 0.05) return false;
    if (el.disabled) return false;
    if (el.getAttribute('aria-hidden') === 'true') return false;
    // Keep anything within a few screens of the viewport; the agent can scroll.
    return r.bottom > -innerHeight && r.top < innerHeight * 4;
  };

  // What a person would call this control.
  const nameOf = (el) => {
    const aria = el.getAttribute('aria-label');
    if (aria) return clean(aria);
    const by = el.getAttribute('aria-labelledby');
    if (by) {
      const parts = by.split(/\s+/).map((id) => {
        const n = document.getElementById(id);
        return n ? clean(n.innerText || n.textContent) : '';
      }).filter(Boolean);
      if (parts.length) return clean(parts.join(' '));
    }
    if (el.labels && el.labels.length) {
      const lbl = clean(el.labels[0].innerText || el.labels[0].textContent);
      if (lbl) return lbl;
    }
    const own = clean(el.innerText || el.textContent);
    if (own) return own.slice(0, 90);
    for (const attr of ['placeholder', 'title', 'alt', 'value', 'name']) {
      const v = clean(el.getAttribute(attr));
      if (v) return v.slice(0, 90);
    }
    const img = el.querySelector && el.querySelector('img[alt]');
    if (img) return clean(img.getAttribute('alt')).slice(0, 90);
    return '';
  };

  // A selector that still finds this element on a later visit. Ordered from
  // most durable to least, so replay degrades gracefully instead of snapping.
  const selectorsFor = (el) => {
    const out = [];
    const esc = (v) => (window.CSS && CSS.escape ? CSS.escape(v) : v.replace(/["\\]/g, '\\$&'));
    for (const attr of ['data-testid', 'data-test', 'data-cy', 'data-qa']) {
      const v = el.getAttribute(attr);
      if (v) out.push(`[${attr}="${v}"]`);
    }
    if (el.id && !/^[0-9]/.test(el.id)) {
      const s = '#' + esc(el.id);
      try { if (document.querySelectorAll(s).length === 1) out.push(s); } catch (e) {}
    }
    const nm = el.getAttribute('name');
    if (nm) {
      const s = `${el.tagName.toLowerCase()}[name="${nm}"]`;
      try { if (document.querySelectorAll(s).length === 1) out.push(s); } catch (e) {}
    }
    // Structural path as the last resort.
    let node = el, path = [];
    while (node && node.nodeType === 1 && path.length < 6) {
      let part = node.tagName.toLowerCase();
      if (node.id && !/^[0-9]/.test(node.id)) { path.unshift('#' + esc(node.id)); break; }
      const parent = node.parentElement;
      if (parent) {
        const sibs = [...parent.children].filter((c) => c.tagName === node.tagName);
        if (sibs.length > 1) part += `:nth-of-type(${sibs.indexOf(node) + 1})`;
      }
      path.unshift(part);
      node = node.parentElement;
    }
    if (path.length) out.push(path.join(' > '));
    return out;
  };

  const seen = new Set();
  const items = [];
  document.querySelectorAll(SELECTOR).forEach((el) => {
    if (seen.has(el) || !visible(el)) return;
    seen.add(el);
    const tag = el.tagName.toLowerCase();
    const type = clean(el.getAttribute('type')).toLowerCase();
    const r = el.getBoundingClientRect();
    const item = {
      tag, type,
      role: clean(el.getAttribute('role')),
      name: nameOf(el),
      value: tag === 'input' || tag === 'textarea' ? clean(el.value).slice(0, 90) : '',
      href: tag === 'a' ? clean(el.getAttribute('href')).slice(0, 120) : '',
      checked: !!el.checked,
      selectors: selectorsFor(el),
      box: [Math.round(r.x), Math.round(r.y), Math.round(r.width), Math.round(r.height)],
      in_view: r.bottom > 0 && r.top < innerHeight,
    };
    if (tag === 'select') {
      item.options = [...el.options].map((o) => clean(o.textContent) || o.value)
                                    .filter(Boolean).slice(0, 25);
    }
    items.push(item);
  });

  return {
    url: location.href,
    title: document.title || '',
    text: clean(document.body ? document.body.innerText : '').slice(0, 20000),
    iframes: document.querySelectorAll('iframe').length,
    scroll_y: Math.round(scrollY),
    scroll_max: Math.max(0, Math.round(document.body.scrollHeight - innerHeight)),
    elements: items,
  };
}
"""

# How many elements to show the model. Beyond this a page is almost always a
# listing, and the extra rows cost tokens without adding decisions.
MAX_ELEMENTS = 110


@dataclass
class Element:
    ref: int
    tag: str
    type: str = ""
    role: str = ""
    name: str = ""
    value: str = ""
    href: str = ""
    checked: bool = False
    options: list[str] = field(default_factory=list)
    selectors: list[str] = field(default_factory=list)
    box: list[int] = field(default_factory=list)
    in_view: bool = True

    @property
    def is_password(self) -> bool:
        if self.type == "password":
            return True
        hint = f"{self.name} {' '.join(self.selectors)}".lower()
        return self.tag in ("input", "textarea") and bool(
            re.search(r"\b(password|passwd|pwd|passcode)\b", hint)
        )

    @property
    def kind(self) -> str:
        """A short human word for this control, used in approval cards."""
        if self.tag == "select":
            return "dropdown"
        if self.tag == "a":
            return "link"
        if self.tag == "button" or self.type in ("submit", "button") or self.role == "button":
            return "button"
        if self.type in ("checkbox", "radio") or self.role in ("checkbox", "radio"):
            return self.type or self.role
        if self.tag in ("input", "textarea"):
            return "field"
        return self.tag

    def describe(self) -> str:
        label = self.name or self.value or self.href or self.tag
        return f'{self.kind} "{_trim(label, 60)}"'

    def render(self) -> str:
        head = f"<{self.tag}"
        if self.type:
            head += f" type={self.type}"
        head += ">"
        parts = [f"[{self.ref}]", head, f'"{_trim(self.name, 80)}"' if self.name else '""']
        if self.tag in ("input", "textarea") and not self.is_password:
            parts.append(f"(={_trim(self.value, 40)})" if self.value else "(empty)")
        if self.is_password:
            parts.append("(password — Theta will not type here)")
        if self.type in ("checkbox", "radio"):
            parts.append("[x]" if self.checked else "[ ]")
        if self.options:
            parts.append("options: " + ", ".join(_trim(o, 24) for o in self.options[:12]))
        if self.href and not self.href.startswith("javascript:"):
            parts.append(f"→ {_trim(self.href, 60)}")
        if not self.in_view:
            parts.append("(off-screen)")
        return " ".join(parts)

    def to_dict(self) -> dict:
        return {
            "ref": self.ref, "tag": self.tag, "type": self.type, "role": self.role,
            "name": self.name, "value": self.value, "href": self.href,
            "checked": self.checked, "options": self.options,
            "selectors": self.selectors, "box": self.box, "in_view": self.in_view,
        }


@dataclass
class Snapshot:
    url: str = ""
    title: str = ""
    text: str = ""
    elements: list[Element] = field(default_factory=list)
    iframes: int = 0
    scroll_y: int = 0
    scroll_max: int = 0

    def by_ref(self, ref: int) -> Element | None:
        for el in self.elements:
            if el.ref == ref:
                return el
        return None

    def render(self, max_elements: int = MAX_ELEMENTS, marks: dict | None = None) -> str:
        """The observation the model reads.

        `marks` annotates refs that will pause for approval or be refused, so the
        model can plan around them instead of discovering the gate by hitting it.
        """
        marks = marks or {}
        lines = [f"URL: {self.url}", f"TITLE: {_trim(self.title, 120)}"]
        if self.scroll_max > 60:
            pct = round(100 * self.scroll_y / max(self.scroll_max, 1))
            lines.append(f"SCROLL: {pct}% down a {self.scroll_max}px page")
        if self.iframes:
            lines.append(
                f"NOTE: {self.iframes} iframe(s) on this page — their contents are "
                "not listed and cannot be clicked."
            )
        lines.append("")
        lines.append("INTERACTIVE ELEMENTS:")
        shown = self.elements[:max_elements]
        lines += [
            f"  {el.render()}" + (f"  ⚠ {marks[str(el.ref)]}" if str(el.ref) in marks else "")
            for el in shown
        ] or ["  (none found)"]
        if len(self.elements) > len(shown):
            lines.append(f"  … {len(self.elements) - len(shown)} more not shown")
        return "\n".join(lines)

    def to_dict(self) -> dict:
        return {
            "url": self.url, "title": self.title, "iframes": self.iframes,
            "scroll_y": self.scroll_y, "scroll_max": self.scroll_max,
            "elements": [e.to_dict() for e in self.elements],
        }


async def capture(page) -> Snapshot:
    """Run the collector and build a Snapshot with stable 1-based refs."""
    try:
        raw = await page.evaluate(_COLLECT_JS)
    except Exception as ex:
        # Mid-navigation the document can vanish under us; one retry is enough.
        try:
            await page.wait_for_timeout(600)
            raw = await page.evaluate(_COLLECT_JS)
        except Exception:
            return Snapshot(url=getattr(page, "url", ""), title="",
                            text=f"(could not read the page: {ex})")

    elements = [
        Element(
            ref=i,
            tag=item.get("tag", ""),
            type=item.get("type", ""),
            role=item.get("role", ""),
            name=item.get("name", ""),
            value=item.get("value", ""),
            href=item.get("href", ""),
            checked=bool(item.get("checked")),
            options=list(item.get("options") or []),
            selectors=list(item.get("selectors") or []),
            box=list(item.get("box") or []),
            in_view=bool(item.get("in_view", True)),
        )
        for i, item in enumerate(raw.get("elements") or [], start=1)
    ]
    return Snapshot(
        url=raw.get("url", ""),
        title=raw.get("title", ""),
        text=raw.get("text", ""),
        elements=elements,
        iframes=int(raw.get("iframes") or 0),
        scroll_y=int(raw.get("scroll_y") or 0),
        scroll_max=int(raw.get("scroll_max") or 0),
    )


def _trim(text, n: int) -> str:
    text = str(text or "")
    return text if len(text) <= n else text[: n - 1] + "…"
