"""
MCP server: a real Chromium browser.

Exposes the action surface an agent needs to operate the web — navigate, look,
click, type, select, scroll, read — over stdio. The browser lives in this
process, so cookies, logins and form state persist across calls.

This server is self-contained: point any MCP client at it and you have a
browser-operating tool set.

Run standalone:  python tools/servers/browser_server.py
"""

from __future__ import annotations

import sys
from pathlib import Path

# Launched as a standalone script, so sys.path[0] is this directory — the project
# root has to go on the path before any `tools.*` import can resolve.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.servers._common import make_server  # noqa: E402

mcp = make_server("theta-browser")


@mcp.tool()
async def browser_navigate(url: str, shot_path: str = "") -> dict:
    """Open a URL. Returns the page title and its numbered interactive elements."""
    from tools import browser_tools

    return await browser_tools.browser_navigate(url, shot_path)


@mcp.tool()
async def browser_snapshot(shot_path: str = "") -> dict:
    """Re-read the current page and list its interactive elements, without acting."""
    from tools import browser_tools

    return await browser_tools.browser_snapshot(shot_path)


@mcp.tool()
async def browser_click(ref: int, shot_path: str = "") -> dict:
    """Click an element by its ref number from the most recent observation."""
    from tools import browser_tools

    return await browser_tools.browser_click(ref, shot_path)


@mcp.tool()
async def browser_type(ref: int, text: str, submit: bool = False, shot_path: str = "") -> dict:
    """Type text into a field by ref. submit=true presses Enter afterwards."""
    from tools import browser_tools

    return await browser_tools.browser_type(ref, text, submit, shot_path)


@mcp.tool()
async def browser_select(ref: int, option: str, shot_path: str = "") -> dict:
    """Choose an option in a dropdown by ref, using the option's visible label."""
    from tools import browser_tools

    return await browser_tools.browser_select(ref, option, shot_path)


@mcp.tool()
async def browser_scroll(direction: str = "down", amount: int = 1, shot_path: str = "") -> dict:
    """Scroll the page by whole screens. direction is "down" or "up"."""
    from tools import browser_tools

    return await browser_tools.browser_scroll(direction, amount, shot_path)


@mcp.tool()
async def browser_back(shot_path: str = "") -> dict:
    """Go back to the previous page."""
    from tools import browser_tools

    return await browser_tools.browser_back(shot_path)


@mcp.tool()
async def browser_wait_for(text: str, timeout: int = 10, shot_path: str = "") -> dict:
    """Wait for some text to appear on the page before continuing."""
    from tools import browser_tools

    return await browser_tools.browser_wait_for(text, timeout, shot_path)


@mcp.tool()
async def browser_read(max_chars: int = 12000) -> dict:
    """Read the visible text of the current page, to extract information from it."""
    from tools import browser_tools

    return await browser_tools.browser_read(max_chars)


@mcp.tool()
async def browser_step(action: str, target: str = "", value: str = "",
                       submit: bool = False, shot_path: str = "") -> dict:
    """Replay a recorded step by selector. Used by Playbook replay, not by the agent."""
    from tools import browser_tools

    return await browser_tools.browser_step(action, target, value, submit, shot_path)


@mcp.tool()
async def browser_reset() -> dict:
    """Close the browser and start a clean one, clearing cookies and any login."""
    from tools import browser_tools

    return await browser_tools.browser_reset()


if __name__ == "__main__":
    mcp.run(transport="stdio")
