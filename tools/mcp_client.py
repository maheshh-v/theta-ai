"""
MCP client manager.

Connects to the local MCP tool servers (email / calendar / notes) over the
stdio transport and keeps the sessions alive for the life of the app. It runs a
dedicated asyncio event loop in a background thread so the synchronous Gradio
handlers can call `list_tools()` / `call_tool()` without worrying about async.

If the MCP transport can't be established (e.g. a sandboxed host that blocks
spawning subprocesses), the manager transparently falls back to calling the
same tool functions in-process, so the assistant still works. Which path is
active is reported via `transport` for display in the UI.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import threading
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from typing import Callable, Optional

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from config import settings
from tools import catalog, tool_specs


@dataclass
class ToolInfo:
    """A tool the agent can call, discovered from MCP (or the fallback specs)."""

    name: str
    description: str
    input_schema: dict
    server: str
    tag: str = "read"  # "read" (safe) or "confirm" (needs human approval)


@dataclass
class ToolContext:
    """Per-request execution context handed to `call_tool`. Carries a provider
    for the current session's Google access token so the manager can inject it
    into Gmail/Calendar calls without the LLM ever seeing it."""

    google_token_provider: Optional[Callable[[], Optional[str]]] = None

    def google_token(self) -> Optional[str]:
        return self.google_token_provider() if self.google_token_provider else None


def _maybe_json(text: str):
    """Parse a text block as JSON, or return the raw string on failure."""
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return text


@dataclass
class ToolResult:
    """Normalised outcome of a tool call."""

    ok: bool
    content: object  # dict / list / str
    source: str = "mcp"  # "mcp" or "in-process fallback"

    def as_text(self) -> str:
        if isinstance(self.content, str):
            return self.content
        return json.dumps(self.content, ensure_ascii=False, indent=2)


# Servers to launch: (logical name, absolute path to the server script).
def _server_specs() -> list[tuple[str, str]]:
    d = settings.servers_dir
    return [
        ("notes", str(d / "notes_server.py")),
        ("tasks", str(d / "tasks_server.py")),
        ("gmail", str(d / "gmail_server.py")),
        ("calendar", str(d / "calendar_server.py")),
    ]


class MCPManager:
    def __init__(self) -> None:
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._stop_event: asyncio.Event | None = None
        self._ready = threading.Event()

        self._sessions: dict[str, ClientSession] = {}
        self._tools: list[ToolInfo] = []
        self._tool_server: dict[str, str] = {}  # tool name -> server name
        self._server_errors: dict[str, str] = {}

        self.connected: bool = False  # at least one MCP session is live
        self.transport: str = "starting"

        # Fallback: name -> (callable, ToolInfo)
        self._fallback: dict[str, tuple] = {}
        self._build_fallback()

    # ------------------------------------------------------------------ #
    # Fallback registry                                                  #
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

    # ------------------------------------------------------------------ #
    # Lifecycle                                                          #
    # ------------------------------------------------------------------ #
    def start(self, timeout: float = 45.0) -> None:
        """Start the background loop and connect to the MCP servers (blocking
        until connected, failed, or timed out)."""
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(
            target=self._thread_main, name="mcp-loop", daemon=True
        )
        self._thread.start()
        connected = self._ready.wait(timeout)
        if not connected:
            self.transport = "in-process fallback (MCP connect timed out)"
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
        """Connect to every server, keep sessions open until stop, then tear
        down. All context enter/exit happens in THIS task, satisfying anyio's
        cancel-scope rules."""
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
                        read, write = await stack.enter_async_context(
                            stdio_client(params)
                        )
                        session = await stack.enter_async_context(
                            ClientSession(read, write)
                        )
                        await session.initialize()
                        listed = await session.list_tools()
                        for t in listed.tools:
                            # mcp >= 2.0 uses input_schema; older SDKs use inputSchema.
                            schema = (
                                getattr(t, "input_schema", None)
                                or getattr(t, "inputSchema", None)
                                or {}
                            )
                            self._tools.append(
                                ToolInfo(
                                    name=t.name,
                                    description=t.description or "",
                                    input_schema=schema,
                                    server=name,
                                    tag=catalog.tag_for(t.name),
                                )
                            )
                            self._tool_server[t.name] = name
                        self._sessions[name] = session
                    except Exception as ex:
                        self._server_errors[name] = repr(ex)

                self.connected = len(self._sessions) > 0
                if self.connected:
                    n = len(self._sessions)
                    self.transport = f"MCP stdio ({n} server{'s' if n != 1 else ''})"
                else:
                    self.transport = "in-process fallback (no MCP servers connected)"
                    self._use_fallback_tools()

                self._ready.set()
                await self._stop_event.wait()
        except Exception as ex:
            self._server_errors["_serve"] = repr(ex)
            self._use_fallback_tools()
            self._ready.set()

    def _use_fallback_tools(self) -> None:
        if not self._tools:
            self._tools = [info for _, info in self._fallback.values()]
            self._tool_server = {i.name: i.server for i in self._tools}

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
        context: "ToolContext | None" = None,
    ) -> ToolResult:
        arguments = dict(arguments or {})
        if name not in self._tool_server and name not in self._fallback:
            return ToolResult(ok=False, content={"error": f"Unknown tool '{name}'."})

        server = self._tool_server.get(name) or self._server_of(name)

        # Inject the session's Google token for auth'd tools (never from the LLM).
        if catalog.needs_auth(server):
            token = context.google_token() if context else None
            if not token:
                return ToolResult(
                    ok=False,
                    source="—",
                    content={
                        "error": "not_connected",
                        "message": "Connect your Google account in the Accounts "
                        "tab to use Gmail and Calendar.",
                    },
                )
            arguments["access_token"] = token

        # Prefer the live MCP session when the tool belongs to a connected server.
        session = self._sessions.get(server) if server else None
        if session is not None and self._loop is not None:
            try:
                fut = asyncio.run_coroutine_threadsafe(
                    session.call_tool(name, arguments), self._loop
                )
                result = fut.result(timeout=30)
                return self._normalise(result, source="mcp")
            except Exception as ex:
                # Fall through to the in-process fallback on any MCP hiccup.
                fb = self._call_fallback(name, arguments)
                if fb is not None:
                    fb.content = {
                        "note": f"MCP call failed ({ex!r}); used in-process fallback.",
                        "result": fb.content,
                    }
                    return fb
                return ToolResult(ok=False, content={"error": repr(ex)})

        # No live session: use the fallback directly.
        fb = self._call_fallback(name, arguments)
        if fb is not None:
            return fb
        return ToolResult(ok=False, content={"error": f"Unknown tool '{name}'."})

    # ------------------------------------------------------------------ #
    # Helpers                                                            #
    # ------------------------------------------------------------------ #
    def _server_of(self, name: str) -> str | None:
        entry = self._fallback.get(name)
        return entry[1].server if entry else None

    def _call_fallback(self, name: str, arguments: dict) -> ToolResult | None:
        entry = self._fallback.get(name)
        if entry is None:
            return None
        fn, _info = entry
        try:
            out = fn(**arguments)
            return ToolResult(ok=True, content=out, source="in-process fallback")
        except Exception as ex:
            return ToolResult(
                ok=False,
                content={"error": f"{name} failed: {ex}"},
                source="in-process fallback",
            )

    @staticmethod
    def _normalise(result, source: str) -> ToolResult:
        """Turn an MCP CallToolResult into a plain Python object."""
        is_error = bool(getattr(result, "is_error", False))

        # Prefer structured content when the tool provides it.
        structured = getattr(result, "structured_content", None)
        if structured is not None:
            content = structured
            # FastMCP/MCPServer wrap bare list/scalar returns under "result".
            if isinstance(content, dict) and set(content.keys()) == {"result"}:
                content = content["result"]
            return ToolResult(ok=not is_error, content=content, source=source)

        # Otherwise collect text blocks. The high-level MCP server emits ONE text
        # block per item for list returns, so parse each block and, when there
        # are several, return them as a list (not a broken concatenation).
        texts = [
            block.text
            for block in getattr(result, "content", []) or []
            if getattr(block, "text", None) is not None
        ]
        if not texts:
            return ToolResult(ok=not is_error, content="", source=source)
        if len(texts) == 1:
            return ToolResult(ok=not is_error, content=_maybe_json(texts[0]), source=source)
        return ToolResult(
            ok=not is_error,
            content=[_maybe_json(t) for t in texts],
            source=source,
        )

    def status(self) -> dict:
        return {
            "transport": self.transport,
            "connected": self.connected,
            "servers": sorted(self._sessions.keys()),
            "tool_count": len(self._tools),
            "errors": self._server_errors,
        }
