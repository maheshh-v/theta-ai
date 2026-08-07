"""
Tool manager.

Theta's tools come from three places, and this module presents them to the agent
as one flat list:

1. **MCP servers** (`tools/servers/*.py`) — launched as stdio subprocesses and
   discovered at runtime. Adding a tool there needs no change to the agent.
2. **The in-process fallback** (`tools/tool_specs.py`) — the same functions
   called directly, for hosts that cannot spawn subprocesses.
3. **Local tools** (`tools/local_tools.py`) — `research`, which needs the
   session's model and a live progress channel.

It runs a dedicated asyncio loop in a background thread so the synchronous agent
loop can call `list_tools()` / `call_tool()` without knowing about any of that.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import os
import sys
import threading
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from typing import Callable, Optional

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from config import settings
from tools import catalog, local_tools, tool_specs

_log = logging.getLogger("theta.tools")

CALL_TIMEOUT = 180  # generous: a research run reads a dozen pages


@dataclass
class ToolInfo:
    """A tool the agent can call, however it was discovered."""

    name: str
    description: str
    input_schema: dict
    server: str
    tag: str = "read"  # "read" (safe) or "confirm" (needs human approval)


@dataclass
class ToolContext:
    """Per-request execution context handed to `call_tool`.

    Carries the caller's session-scoped configuration so tools never read global
    state, and never receive a secret through the model.
    """

    llm: object = None
    search_provider: Optional[str] = None
    search_key: Optional[str] = None
    research_opts: dict = field(default_factory=dict)
    on_progress: Optional[Callable[[dict], None]] = None
    # Returns the file path for the next browser screenshot. The run owns naming;
    # the browser server just writes where it is told.
    shot_path_factory: Optional[Callable[[], str]] = None


@dataclass
class ToolResult:
    """Normalised outcome of a tool call."""

    ok: bool
    content: object                # dict / list / str
    source: str = "mcp"            # "mcp" | "in-process" | "local"

    def as_text(self) -> str:
        if isinstance(self.content, str):
            return self.content
        return json.dumps(self.content, ensure_ascii=False, indent=2)


def _server_specs() -> list[tuple[str, str]]:
    """Servers to launch: (logical name, path to the server script)."""
    d = settings.servers_dir
    return [
        ("browser", str(d / "browser_server.py")),
        ("workspace", str(d / "workspace_server.py")),
        ("web", str(d / "web_server.py")),
        ("briefs", str(d / "briefs_server.py")),
    ]


class MCPManager:
    def __init__(self) -> None:
        self._loop: asyncio.AbstractEventLoop | None = None
        self._fallback_loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._stop_event: asyncio.Event | None = None
        self._ready = threading.Event()

        self._sessions: dict[str, ClientSession] = {}
        self._tools: list[ToolInfo] = []
        self._tool_server: dict[str, str] = {}
        self._server_errors: dict[str, str] = {}

        self.connected: bool = False
        self.transport: str = "starting"

        self._fallback: dict[str, tuple] = {}   # name -> (callable, ToolInfo)
        self._local: dict[str, local_tools.LocalTool] = {}
        self._build_fallback()
        self._register_local()

    # ------------------------------------------------------------------ #
    # Registries                                                         #
    # ------------------------------------------------------------------ #
    def _build_fallback(self) -> None:
        for spec in tool_specs.TOOL_SPECS:
            info = ToolInfo(
                name=spec["name"],
                description=spec["description"],
                input_schema=tool_specs.spec_to_input_schema(spec),
                server=spec["server"],
                tag=catalog.tag_for(spec["name"]),
            )
            self._fallback[spec["name"]] = (spec["fn"], info)

    def _register_local(self) -> None:
        for tool in local_tools.LOCAL_TOOLS:
            self._local[tool.name] = tool
            info = ToolInfo(
                name=tool.name,
                description=tool.description,
                input_schema=tool.input_schema,
                server=tool.server,
                tag=tool.tag,
            )
            self._tools.append(info)
            self._tool_server[tool.name] = tool.server

    # ------------------------------------------------------------------ #
    # Lifecycle                                                          #
    # ------------------------------------------------------------------ #
    def start(self, timeout: float = 45.0) -> None:
        """Start the background loop and connect to the MCP servers, blocking
        until connected, failed, or timed out."""
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._thread_main, name="mcp-loop", daemon=True)
        self._thread.start()
        if not self._ready.wait(timeout):
            self.transport = "in-process (MCP connect timed out)"
            self._use_fallback_tools()

    def _thread_main(self) -> None:
        # Windows needs the Proactor loop to spawn subprocesses (stdio servers).
        if sys.platform == "win32":
            try:
                asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
            except Exception:
                pass
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._serve())
        except Exception as ex:  # pragma: no cover - defensive
            self._server_errors["_loop"] = repr(ex)
            self._ready.set()
        finally:
            try:
                self._loop.close()
            except Exception:
                pass

    async def _serve(self) -> None:
        """Connect to every server and hold the sessions open until stop. All
        context enter/exit happens in THIS task, satisfying anyio's cancel-scope
        rules."""
        self._stop_event = asyncio.Event()
        try:
            async with AsyncExitStack() as stack:
                for name, script in _server_specs():
                    try:
                        params = StdioServerParameters(
                            command=sys.executable,
                            args=[script],
                            cwd=str(settings.project_root),
                            env=os.environ.copy(),
                        )
                        read, write = await stack.enter_async_context(stdio_client(params))
                        session = await stack.enter_async_context(ClientSession(read, write))
                        await session.initialize()
                        listed = await session.list_tools()
                        for t in listed.tools:
                            # mcp >= 2.0 uses input_schema; older SDKs use inputSchema.
                            schema = (
                                getattr(t, "input_schema", None)
                                or getattr(t, "inputSchema", None)
                                or {}
                            )
                            self._tools.append(ToolInfo(
                                name=t.name,
                                description=t.description or "",
                                input_schema=schema,
                                server=name,
                                tag=catalog.tag_for(t.name),
                            ))
                            self._tool_server[t.name] = name
                        self._sessions[name] = session
                    except Exception as ex:
                        self._server_errors[name] = repr(ex)
                        _log.warning("MCP server '%s' failed to start: %r", name, ex)

                self.connected = len(self._sessions) > 0
                if self.connected:
                    n = len(self._sessions)
                    self.transport = f"MCP stdio ({n} server{'s' if n != 1 else ''})"
                else:
                    self.transport = "in-process (no MCP servers connected)"
                    self._use_fallback_tools()

                self._ready.set()
                await self._stop_event.wait()
        except Exception as ex:
            self._server_errors["_serve"] = repr(ex)
            self._use_fallback_tools()
            self._ready.set()

    def _use_fallback_tools(self) -> None:
        """Add the fallback specs for any tool MCP did not provide."""
        for name, (_fn, info) in self._fallback.items():
            if name not in self._tool_server:
                self._tools.append(info)
                self._tool_server[name] = info.server

    def stop(self) -> None:
        if self._loop and self._stop_event and self._loop.is_running():
            self._loop.call_soon_threadsafe(self._stop_event.set)

    # ------------------------------------------------------------------ #
    # Public API (synchronous)                                           #
    # ------------------------------------------------------------------ #
    def list_tools(self) -> list[ToolInfo]:
        return list(self._tools)

    def call_tool(
        self,
        name: str,
        arguments: dict | None = None,
        context: ToolContext | None = None,
    ) -> ToolResult:
        arguments = dict(arguments or {})

        # 1. Local tools run here, with the context.
        if name in self._local:
            return self._call_local(name, arguments, context)

        if name not in self._tool_server and name not in self._fallback:
            return ToolResult(ok=False, source="—",
                              content={"error": f"Unknown tool '{name}'."})

        server = self._tool_server.get(name) or self._server_of(name)

        # Inject the session's search configuration — never chosen by the model.
        if catalog.needs_search_config(server) and context is not None:
            if context.search_provider:
                arguments["search_provider"] = context.search_provider
            if context.search_key:
                arguments["search_api_key"] = context.search_key

        # Tell the browser where to put this step's screenshot.
        if catalog.needs_screenshot(server) and context is not None:
            if context.shot_path_factory and "shot_path" in self._param_names(name):
                try:
                    arguments["shot_path"] = context.shot_path_factory()
                except Exception:  # pragma: no cover - a failed shot must not stop a step
                    pass

        # 2. Prefer a live MCP session.
        session = self._sessions.get(server) if server else None
        if session is not None and self._loop is not None:
            try:
                fut = asyncio.run_coroutine_threadsafe(
                    session.call_tool(name, arguments), self._loop
                )
                return self._normalise(fut.result(timeout=CALL_TIMEOUT), source="mcp")
            except Exception as ex:
                _log.warning("MCP call to '%s' failed (%r); using in-process", name, ex)
                fb = self._call_fallback(name, arguments)
                if fb is not None:
                    return fb
                return ToolResult(ok=False, source="—", content={"error": repr(ex)})

        # 3. No live session: call the function directly.
        fb = self._call_fallback(name, arguments)
        if fb is not None:
            return fb
        return ToolResult(ok=False, source="—", content={"error": f"Unknown tool '{name}'."})

    # ------------------------------------------------------------------ #
    # Helpers                                                            #
    # ------------------------------------------------------------------ #
    def _call_local(self, name: str, arguments: dict, context) -> ToolResult:
        tool = self._local[name]
        try:
            out = tool.fn(arguments, context)
        except Exception as ex:  # pragma: no cover - defensive
            _log.exception("Local tool '%s' failed", name)
            return ToolResult(ok=False, source="local",
                              content={"error": f"{name} failed: {ex}"})
        ok = not (isinstance(out, dict) and out.get("error"))
        return ToolResult(ok=ok, content=out, source="local")

    def _server_of(self, name: str) -> str | None:
        entry = self._fallback.get(name)
        return entry[1].server if entry else None

    def _param_names(self, name: str) -> set[str]:
        for tool in self._tools:
            if tool.name == name:
                return set((tool.input_schema or {}).get("properties", {}))
        return set()

    def _async_loop(self) -> asyncio.AbstractEventLoop:
        """A persistent loop for async in-process tools.

        The browser tools are async and hold Playwright objects bound to the loop
        that created them, so every fallback call must land on the *same* loop —
        `asyncio.run` per call would silently break the browser after step one.
        """
        if self._loop is not None and self._loop.is_running():
            return self._loop
        if self._fallback_loop is None or not self._fallback_loop.is_running():
            loop = asyncio.new_event_loop()
            thread = threading.Thread(
                target=loop.run_forever, name="tool-async", daemon=True
            )
            thread.start()
            self._fallback_loop = loop
        return self._fallback_loop

    def _call_fallback(self, name: str, arguments: dict) -> ToolResult | None:
        entry = self._fallback.get(name)
        if entry is None:
            return None
        fn, _info = entry
        try:
            out = fn(**arguments)
            if inspect.iscoroutine(out):
                out = asyncio.run_coroutine_threadsafe(
                    out, self._async_loop()
                ).result(timeout=CALL_TIMEOUT)
        except TypeError as ex:
            return ToolResult(ok=False, source="in-process",
                              content={"error": f"Bad arguments for {name}: {ex}"})
        except Exception as ex:
            return ToolResult(ok=False, source="in-process",
                              content={"error": f"{name} failed: {ex}"})
        ok = not (isinstance(out, dict) and out.get("error"))
        return ToolResult(ok=ok, content=out, source="in-process")

    @staticmethod
    def _normalise(result, source: str) -> ToolResult:
        """Turn an MCP CallToolResult into a plain Python object."""
        is_error = bool(getattr(result, "is_error", False))

        structured = getattr(result, "structured_content", None)
        if structured is not None:
            content = structured
            # FastMCP/MCPServer wrap bare list/scalar returns under "result".
            if isinstance(content, dict) and set(content.keys()) == {"result"}:
                content = content["result"]
            ok = not is_error and not (isinstance(content, dict) and content.get("error"))
            return ToolResult(ok=ok, content=content, source=source)

        # Otherwise collect text blocks. The high-level server emits one block per
        # item for list returns, so parse each and keep them as a list.
        texts = [
            block.text
            for block in getattr(result, "content", []) or []
            if getattr(block, "text", None) is not None
        ]
        if not texts:
            return ToolResult(ok=not is_error, content="", source=source)
        if len(texts) == 1:
            content = _maybe_json(texts[0])
            ok = not is_error and not (isinstance(content, dict) and content.get("error"))
            return ToolResult(ok=ok, content=content, source=source)
        return ToolResult(ok=not is_error, content=[_maybe_json(t) for t in texts], source=source)

    def status(self) -> dict:
        return {
            "transport": self.transport,
            "connected": self.connected,
            "servers": sorted(self._sessions.keys()),
            "tool_count": len(self._tools),
            "errors": self._server_errors,
        }


def _maybe_json(text: str):
    """Parse a text block as JSON, or return the raw string on failure."""
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return text
