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
import re

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
    # Connected-service credentials come from the environment via `.env`, so a
    # developer who has one configured would otherwise get different results
    # from the same test. Start every test with nothing connected.
    for name in ("notion_token", "google_client_id", "google_client_secret",
                 "google_redirect_uri"):
        monkeypatch.setattr(settings, name, "", raising=False)
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

    for verb in ("get", "post", "patch", "request"):
        monkeypatch.setattr(requests, verb, blocked)


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


# --------------------------------------------------------------------------- #
# Connected services: Notion and Gmail, in memory                             #
# --------------------------------------------------------------------------- #
class FakeResponse:
    """Just enough of `requests.Response` for the integration layers."""

    def __init__(self, status_code: int, payload=None) -> None:
        self.status_code = status_code
        self._payload = {} if payload is None else payload
        self.content = b"" if payload is None else b"{}"

    def json(self):
        return self._payload


class FakeService:
    """Base for a service double patched in at the `requests` boundary.

    Services chain: each one handles its own host and passes anything else to
    whatever was installed before it, so a test can ask for `fake_gmail` and
    `fake_notion` together and get both. Without the chain the second fixture
    would silently displace the first.
    """

    HOST = ""

    def __init__(self, nxt) -> None:
        self._next = nxt

    def __call__(self, method, url, **kw):
        if self.HOST not in url:
            return self._next(method, url, **kw)
        return self.handle(method, url, **kw)

    def handle(self, method, url, **kw):  # pragma: no cover - overridden
        raise NotImplementedError


def _install(monkeypatch, cls):
    import requests

    fake = cls(requests.request)
    monkeypatch.setattr(requests, "request", fake)
    return fake


class FakeNotion(FakeService):
    """A small Notion: pages with properties, page bodies as Markdown, and one
    database. Patched in at the `requests` boundary, so the real status-code
    handling, headers and version pinning in `integrations/notion/api.py` all run.
    """

    HOST = "api.notion.com"
    PAGE = "11111111-1111-1111-1111-111111111111"
    ROW = "22222222-2222-2222-2222-222222222222"
    DB = "33333333-3333-3333-3333-333333333333"
    SOURCE = "44444444-4444-4444-4444-444444444444"
    TOKEN = "ntn_test_token_value"

    def __init__(self, nxt) -> None:
        super().__init__(nxt)
        self.markdown = {
            self.PAGE: "# Roadmap\n\nShip the thing by Friday.\n",
            self.ROW: "",
        }
        self.pages = {
            self.PAGE: self._page(self.PAGE, "Roadmap", {}),
            self.ROW: self._page(self.ROW, "Fix the login wall", {
                "Status": {"id": "s", "type": "select", "select": {"name": "To do"}},
                "Priority": {"id": "p", "type": "number", "number": 3},
                "Tags": {"id": "t", "type": "multi_select", "multi_select": []},
                "Done": {"id": "d", "type": "checkbox", "checkbox": False},
                "Age": {"id": "a", "type": "formula",
                        "formula": {"type": "number", "number": 7}},
            }),
        }
        self.calls: list[tuple[str, str]] = []
        self.versions: list[str] = []
        self.forbidden: set[str] = set()      # ids the integration cannot see
        self.swallow_writes = False           # accept a write, then ignore it

    # -- fixtures ---------------------------------------------------------- #
    @staticmethod
    def _title(text):
        return {"id": "title", "type": "title",
                "title": [{"type": "text", "plain_text": text, "text": {"content": text}}]}

    def _page(self, pid, title, properties):
        return {
            "object": "page", "id": pid,
            "url": f"https://notion.so/{pid.replace('-', '')}",
            "last_edited_time": "2026-08-01T10:00:00.000Z",
            "properties": {"Name": self._title(title), **properties},
        }

    # -- the router -------------------------------------------------------- #
    def handle(self, method, url, **kw):
        path = url.split("api.notion.com", 1)[-1]
        self.calls.append((method, path))
        self.versions.append((kw.get("headers") or {}).get("Notion-Version", ""))
        body = kw.get("json") or {}

        if (kw.get("headers") or {}).get("Authorization") != f"Bearer {self.TOKEN}":
            return FakeResponse(401, {"object": "error", "code": "unauthorized",
                                      "message": "API token is invalid."})

        for pid in self.forbidden:
            if pid in path:
                return FakeResponse(404, {"object": "error", "code": "object_not_found",
                                          "message": "Could not find page."})

        if path == "/v1/users/me":
            return FakeResponse(200, {"id": "bot", "name": "Theta",
                                      "bot": {"workspace_name": "Acme"}})
        if path == "/v1/search":
            return FakeResponse(200, {"results": self._search(body), "has_more": False})
        if path == "/v1/pages" and method == "POST":
            return self._create(body)
        if path == f"/v1/databases/{self.DB}":
            return FakeResponse(200, {"object": "database", "id": self.DB,
                                      "url": f"https://notion.so/{self.DB}",
                                      "title": [{"plain_text": "Bugs"}],
                                      "data_sources": [{"id": self.SOURCE, "name": "Bugs"}]})
        if path == f"/v1/data_sources/{self.SOURCE}":
            return FakeResponse(200, {"object": "data_source", "id": self.SOURCE,
                                      "properties": self.pages[self.ROW]["properties"]})
        if path == f"/v1/data_sources/{self.SOURCE}/query":
            return FakeResponse(200, {"results": [self.pages[self.ROW]], "has_more": False})

        markdown = re.match(r"^/v1/pages/([0-9a-f-]+)/markdown$", path)
        if markdown:
            return self._markdown(method, markdown.group(1), body)
        page = re.match(r"^/v1/pages/([0-9a-f-]+)$", path)
        if page:
            return self._page_op(method, page.group(1), body)

        return FakeResponse(404, {"object": "error", "code": "object_not_found",
                                  "message": f"No route for {path}"})

    # -- operations -------------------------------------------------------- #
    def _search(self, body):
        query = str(body.get("query", "")).lower()
        wanted = ((body.get("filter") or {}).get("value")) or ""
        hits = []
        for page in self.pages.values():
            if wanted == "data_source":
                continue
            from integrations.notion.api import title_of

            if not query or query in title_of(page).lower():
                hits.append(page)
        if wanted in ("", "data_source") and (not query or "bug" in query):
            hits.append({"object": "data_source", "id": self.SOURCE,
                         "title": [{"plain_text": "Bugs"}],
                         "url": f"https://notion.so/{self.DB}"})
        return hits

    def _create(self, body):
        pid = "55555555-5555-5555-5555-555555555555"
        title = ""
        for prop in (body.get("properties") or {}).values():
            if prop.get("title"):
                title = prop["title"][0]["text"]["content"]
        self.pages[pid] = self._page(pid, title, {})
        self.markdown[pid] = ""
        return FakeResponse(200, self.pages[pid])

    def _markdown(self, method, pid, body):
        if pid not in self.markdown:
            return FakeResponse(404, {"object": "error", "code": "object_not_found",
                                      "message": "Could not find page."})
        if method == "GET":
            return FakeResponse(200, {"object": "page_markdown", "id": pid,
                                      "markdown": self.markdown[pid], "truncated": False})
        if self.swallow_writes:
            return FakeResponse(200, {"object": "page_markdown", "id": pid})
        kind = body.get("type")
        if kind == "replace_content":
            self.markdown[pid] = body["replace_content"]["new_str"]
        elif kind == "update_content":
            for edit in body["update_content"]["content_updates"]:
                count = -1 if edit.get("replace_all_matches") else 1
                self.markdown[pid] = self.markdown[pid].replace(
                    edit["old_str"], edit["new_str"], count)
        return FakeResponse(200, {"object": "page_markdown", "id": pid})

    def _page_op(self, method, pid, body):
        if pid not in self.pages:
            return FakeResponse(404, {"object": "error", "code": "object_not_found",
                                      "message": "Could not find page."})
        if method == "GET":
            return FakeResponse(200, self.pages[pid])
        if self.swallow_writes:
            return FakeResponse(200, self.pages[pid])
        for name, value in (body.get("properties") or {}).items():
            kind = next(k for k in value if k != "id")
            self.pages[pid]["properties"][name] = {"id": name[0], "type": kind, **value}
        return FakeResponse(200, self.pages[pid])


@pytest.fixture
def fake_notion(monkeypatch):
    return _install(monkeypatch, FakeNotion)


class FakeGmail(FakeService):
    """A tiny mailbox: two messages on one thread, drafts, and a Sent label."""

    HOST = "gmail.googleapis.com"
    MSG = "msg-1"
    THREAD = "thread-1"

    def __init__(self, nxt) -> None:
        super().__init__(nxt)
        self.messages = {
            self.MSG: self._message(
                self.MSG, self.THREAD,
                {"From": "Priya <priya@example.com>", "To": "me@example.com",
                 "Subject": "Invoice for July", "Date": "Mon, 3 Aug 2026 09:00:00 +0100",
                 "Message-ID": "<abc@example.com>", "References": "<older@example.com>"},
                "Hi — attaching July's invoice. Could you confirm the total?",
                ["INBOX", "UNREAD"],
            ),
            "msg-2": self._message(
                "msg-2", self.THREAD,
                {"From": "me@example.com", "To": "priya@example.com",
                 "Subject": "Re: Invoice for July", "Message-ID": "<def@example.com>"},
                "Looking now.", ["SENT"],
            ),
        }
        self.drafts: dict[str, dict] = {}
        self.sent: list[dict] = []
        self.calls: list[tuple[str, str]] = []
        self.label_sent = True       # set False to simulate a send that vanished

    @staticmethod
    def _message(mid, thread, headers, body, labels):
        import base64 as _b64

        return {
            "id": mid, "threadId": thread, "labelIds": list(labels),
            "snippet": body[:60],
            "payload": {
                "mimeType": "text/plain",
                "headers": [{"name": k, "value": v} for k, v in headers.items()],
                "body": {"data": _b64.urlsafe_b64encode(body.encode()).decode().rstrip("=")},
            },
        }

    def handle(self, method, url, **kw):
        path = url.split("/gmail/v1/users/me", 1)[-1].split("?")[0]
        self.calls.append((method, path))
        if not (kw.get("headers") or {}).get("Authorization", "").startswith("Bearer "):
            return FakeResponse(401, {"error": {"message": "Invalid Credentials"}})
        body = kw.get("json") or {}

        if path == "/profile":
            return FakeResponse(200, {"emailAddress": "me@example.com"})
        if path == "/messages":
            query = (kw.get("params") or {}).get("q", "")
            found = [
                {"id": m["id"], "threadId": m["threadId"]}
                for m in self.messages.values()
                if not query or query.lower() in m["snippet"].lower()
                or query.lower() in self._subject(m).lower()
            ]
            return FakeResponse(200, {"messages": found})
        if path == "/messages/send":
            return self._send(body)
        if path == "/drafts" and method == "POST":
            did = f"draft-{len(self.drafts) + 1}"
            self.drafts[did] = body.get("message", {})
            return FakeResponse(200, {"id": did, "message": {"id": f"m-{did}"}})
        if path.startswith("/drafts/"):
            did = path.rsplit("/", 1)[-1]
            if did not in self.drafts:
                return FakeResponse(404, {"error": {"message": "Draft not found"}})
            return FakeResponse(200, {"id": did})
        if path.startswith("/threads/"):
            tid = path.rsplit("/", 1)[-1]
            msgs = [m for m in self.messages.values() if m["threadId"] == tid]
            if not msgs:
                return FakeResponse(404, {"error": {"message": "Thread not found"}})
            return FakeResponse(200, {"id": tid, "messages": msgs})
        if path.startswith("/messages/"):
            mid = path.rsplit("/", 1)[-1]
            if mid not in self.messages:
                return FakeResponse(404, {"error": {"message": "Not Found"}})
            return FakeResponse(200, self.messages[mid])
        return FakeResponse(404, {"error": {"message": f"No route for {path}"}})

    @staticmethod
    def _subject(msg):
        for h in msg["payload"]["headers"]:
            if h["name"].lower() == "subject":
                return h["value"]
        return ""

    def _send(self, body):
        import base64 as _b64

        raw = _b64.urlsafe_b64decode(body["raw"] + "=" * (-len(body["raw"]) % 4)).decode()
        mid = f"sent-{len(self.sent) + 1}"
        self.sent.append({"raw": raw, "threadId": body.get("threadId", "")})
        self.messages[mid] = {
            "id": mid, "threadId": body.get("threadId", ""),
            "labelIds": ["SENT"] if self.label_sent else ["DRAFT"],
            "snippet": "", "payload": {"headers": [{"name": "To", "value": "priya@example.com"}]},
        }
        return FakeResponse(200, {"id": mid, "threadId": body.get("threadId", "")})


@pytest.fixture
def fake_gmail(monkeypatch):
    return _install(monkeypatch, FakeGmail)


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
