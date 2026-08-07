"""
Shared fixtures: isolated storage, deterministic encryption, a stubbed browser,
an offline tool manager, a TestClient, and scriptable stand-ins for the model.

Nothing here touches the network and nothing launches Chromium — the browser is
replaced by a small fake page model, so the agent loop, the gates and Playbook
replay are all testable in milliseconds. Theta ships no mock LLM; these doubles
live in the tests, where a fake belongs.
"""

from __future__ import annotations

import json

import pytest

from agent.llm import BaseLLM
from config import settings
from tools.mcp_client import MCPManager


@pytest.fixture(autouse=True)
def isolate(tmp_path, monkeypatch):
    """Point storage at a temp dir and use a fixed secret so each test is clean."""
    monkeypatch.setattr(settings, "data_dir", tmp_path, raising=False)
    monkeypatch.setattr(settings, "session_persist", False, raising=False)
    monkeypatch.setattr(settings, "secret_key", "unit-test-secret", raising=False)
    for name in ("briefs", "runs", "playbooks", "workspace"):
        (tmp_path / name).mkdir(parents=True, exist_ok=True)

    import server.security as sec
    sec._fernet = None            # rebuild Fernet from the test secret
    sec._registered.clear()
    yield
    sec._fernet = None


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """Fail loudly if a test reaches for the real internet."""
    import requests

    def blocked(*args, **kwargs):
        raise AssertionError("a test tried to make a real HTTP request")

    monkeypatch.setattr(requests, "get", blocked)
    monkeypatch.setattr(requests, "post", blocked)


# --------------------------------------------------------------------------- #
# A fake browser                                                              #
# --------------------------------------------------------------------------- #
class FakePage:
    """A tiny page model: enough structure to exercise refs, gates and replay."""

    def __init__(self) -> None:
        self.url = "about:blank"
        self.title = ""
        self.text = ""
        self.clicked: list[str] = []
        self.typed: list[tuple[str, str]] = []
        self.selected: list[tuple[str, str]] = []
        self.broken: set[str] = set()      # selectors that no longer resolve
        self.elements = [
            {"ref": 1, "tag": "input", "type": "text", "name": "Search",
             "selectors": ["#q"], "describe": 'field "Search"'},
            {"ref": 2, "tag": "button", "type": "submit", "name": "Search",
             "selectors": ["#go"], "describe": 'button "Search"'},
            {"ref": 3, "tag": "a", "type": "", "name": "About us",
             "selectors": ["#about"], "describe": 'link "About us"'},
            {"ref": 4, "tag": "input", "type": "password", "name": "Password",
             "selectors": ["#pw"], "describe": 'field "Password"'},
            {"ref": 5, "tag": "button", "type": "button", "name": "Place order",
             "selectors": ["#buy"], "describe": 'button "Place order"'},
        ]

    def by_ref(self, ref):
        return next((e for e in self.elements if e["ref"] == int(ref)), None)

    def render(self) -> str:
        return "URL: %s\nINTERACTIVE ELEMENTS:\n%s" % (
            self.url,
            "\n".join(f'  [{e["ref"]}] <{e["tag"]}> "{e["name"]}"' for e in self.elements),
        )

    def gates(self) -> dict:
        """Mirrors the real guard: consequential controls need approval."""
        from browser import guard
        from browser.snapshot import Element

        out = {}
        for e in self.elements:
            el = Element(ref=e["ref"], tag=e["tag"], type=e["type"], name=e["name"])
            level, why = guard.classify_click(el)
            if level == guard.CONFIRM:
                out[str(e["ref"])] = why
        return out

    def observation(self, message: str, shot_path: str = "") -> dict:
        out = {"ok": True, "message": message, "url": self.url, "title": self.title,
               "page": self.render(), "gates": self.gates()}
        if shot_path:
            out["screenshot"] = shot_path
        return out


@pytest.fixture
def page():
    return FakePage()


@pytest.fixture
def stub_browser(page, monkeypatch):
    """Swap the browser tools for fakes, so no Chromium ever starts."""
    from tools import tool_specs

    def nav(url, shot_path=""):
        page.url = url
        page.title = "Example"
        page.text = "Hello from the fake page."
        return page.observation(f"Opened {url}", shot_path)

    def click(ref, shot_path=""):
        el = page.by_ref(ref)
        if el is None:
            return {"ok": False, "error": f"There is no element [{ref}]."}
        page.clicked.append(el["name"])
        return {**page.observation(f'Clicked {el["describe"]}', shot_path),
                "target": {k: el[k] for k in ("name", "tag", "type", "selectors", "describe")}}

    def type_text(ref, text, submit=False, shot_path=""):
        el = page.by_ref(ref)
        if el is None:
            return {"ok": False, "error": f"There is no element [{ref}]."}
        from browser import guard
        from browser.snapshot import Element

        level, why = guard.classify_type(
            Element(ref=ref, tag=el["tag"], type=el["type"], name=el["name"]), text, submit)
        if level == guard.FORBIDDEN:
            return {"ok": False, "error": why}
        page.typed.append((el["name"], text))
        return {**page.observation(f'Typed into {el["describe"]}', shot_path),
                "target": {k: el[k] for k in ("name", "tag", "type", "selectors", "describe")}}

    def select(ref, option, shot_path=""):
        el = page.by_ref(ref)
        if el is None:
            return {"ok": False, "error": f"There is no element [{ref}]."}
        page.selected.append((el["name"], option))
        return {**page.observation(f'Selected {option}', shot_path),
                "target": {k: el[k] for k in ("name", "tag", "type", "selectors", "describe")}}

    def read(max_chars=12000):
        from browser import guard

        return {"ok": True, "chars": len(page.text), "text": guard.wrap_untrusted(page.text)}

    def step(action, target="", value="", submit=False, shot_path=""):
        data = json.loads(target) if isinstance(target, str) and target else (target or {})
        selectors = data.get("selectors") or []
        if any(s in page.broken for s in selectors) or not selectors:
            return {"ok": False,
                    "error": f"Could not find {data.get('describe', 'the element')} on this page."}
        if action == "click":
            page.clicked.append(data.get("name", ""))
        elif action == "type":
            page.typed.append((data.get("name", ""), value))
        elif action == "select":
            page.selected.append((data.get("name", ""), value))
        return page.observation(f"{action} on {data.get('name', '')}", shot_path)

    fakes = {"browser_navigate": nav, "browser_click": click, "browser_type": type_text,
             "browser_select": select, "browser_read": read, "browser_step": step,
             "browser_snapshot": lambda shot_path="": page.observation("Observed", shot_path),
             "browser_scroll": lambda direction="down", amount=1, shot_path="":
                 page.observation(f"Scrolled {direction}", shot_path),
             "browser_back": lambda shot_path="": page.observation("Went back", shot_path),
             "browser_wait_for": lambda text, timeout=10, shot_path="":
                 page.observation(f"Waited for {text}", shot_path)}

    for spec in tool_specs.TOOL_SPECS:
        if spec["name"] in fakes:
            monkeypatch.setitem(spec, "fn", fakes[spec["name"]])
    return page


@pytest.fixture
def manager(stub_browser):
    """A manager using the in-process path (no MCP subprocesses spawned)."""
    m = MCPManager()
    m._use_fallback_tools()
    return m


@pytest.fixture
def client(manager, monkeypatch):
    """A TestClient over a freshly built app (no lifespan → no subprocesses)."""
    monkeypatch.setattr(settings, "gemini_api_key", "test-key", raising=False)
    from starlette.testclient import TestClient
    from server.app_factory import create_app

    app = create_app()
    app.state.mcp = manager
    return TestClient(app)


# --------------------------------------------------------------------------- #
# Model doubles                                                               #
# --------------------------------------------------------------------------- #
class ScriptLLM(BaseLLM):
    """Replays a fixed list of replies, one per `complete()` call. The last reply
    repeats, so a loop that runs long still terminates."""

    label = "ScriptLLM"
    name = "script"

    def __init__(self, *replies: str) -> None:
        self.replies = list(replies) or ["{}"]
        self.calls: list[tuple[str, str]] = []

    def complete(self, system: str, user: str, max_tokens: int = 4096) -> str:
        self.calls.append((system, user))
        i = min(len(self.calls) - 1, len(self.replies) - 1)
        return self.replies[i]


class ToolThenFinal(BaseLLM):
    """Calls one tool on the first turn, then answers."""

    label = "ToolThenFinal"
    name = "script"

    def __init__(self, tool_json: str, final: str = "All done.") -> None:
        self.tool_json = tool_json
        self.final = final
        self.calls: list[tuple[str, str]] = []

    def complete(self, system: str, user: str, max_tokens: int = 4096) -> str:
        self.calls.append((system, user))
        work = user.split("WORK SO FAR:", 1)[1]
        if "(nothing yet)" not in work:
            return json.dumps({"thought": "wrap up", "action": "FINAL",
                               "action_input": self.final})
        return self.tool_json


@pytest.fixture
def script_llm():
    return ScriptLLM


@pytest.fixture
def tool_then_final():
    return ToolThenFinal


@pytest.fixture
def fake_web(monkeypatch):
    """Replace search + fetch with deterministic fixtures."""
    from web.fetch import Page

    class FakeWeb:
        def __init__(self) -> None:
            self.pages = {
                "https://a.example/1": ("Solar costs 2026", "Utility solar fell to $28/MWh in 2025."),
                "https://b.example/2": ("Wind costs 2026", "Offshore wind rose 12% on financing costs."),
                "https://c.example/3": ("Grid storage", "Battery storage additions doubled in 2025."),
            }
            self.unreadable: set[str] = set()
            self.search_error: str | None = None
            self.searched: list[str] = []

        def search(self, query, max_results=6, provider=None, api_key=None):
            from web.search import SearchError, SearchResult

            self.searched.append(query)
            if self.search_error:
                raise SearchError(self.search_error)
            return [SearchResult(title=t, url=u, snippet=body[:40])
                    for u, (t, body) in list(self.pages.items())[:max_results]]

        def fetch(self, url, timeout=None):
            if url in self.unreadable or url not in self.pages:
                return Page(url=url, ok=False, error="No readable text found.")
            title, body = self.pages[url]
            return Page(url=url, title=title, text=body * 6)

        def fetch_many(self, urls, timeout=None):
            return [self.fetch(u) for u in urls]

    fake = FakeWeb()
    # `web/__init__` re-exports nothing, so these dotted paths resolve to modules.
    for target in ("research.pipeline.search", "tools.web_tools.search", "web.search.search"):
        monkeypatch.setattr(target, fake.search)
    monkeypatch.setattr("research.pipeline.fetch_many", fake.fetch_many)
    monkeypatch.setattr("tools.web_tools.fetch", fake.fetch)
    monkeypatch.setattr("web.fetch.fetch", fake.fetch)
    return fake
