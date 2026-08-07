"""
The workspace: the only place Theta may write.

Everything the agent produces — an extracted table, a CSV, a summary — lands in
`data/workspace/`. Paths are resolved against that root and rejected if they
escape it, so a task (or a page that manipulates one) cannot reach the rest of
the filesystem. The sandbox-root pattern is borrowed from the reference MCP
filesystem server, because it is the right shape and worth copying exactly.
"""

from __future__ import annotations

from pathlib import Path

from config import settings

MAX_BYTES = 2 * 1024 * 1024
# Executables and scripts have no business being written by a browser agent.
BLOCKED_SUFFIXES = {
    ".exe", ".dll", ".so", ".dylib", ".bat", ".cmd", ".com", ".msi", ".scr",
    ".ps1", ".sh", ".py", ".js", ".jar", ".vbs", ".lnk",
}


class WorkspaceError(RuntimeError):
    """Raised when a path escapes the workspace or is otherwise refused."""


def _root() -> Path:
    root = settings.workspace_dir
    root.mkdir(parents=True, exist_ok=True)
    return root.resolve()


def _resolve(name: str) -> Path:
    """Resolve a user/model-supplied path inside the workspace, or refuse it."""
    raw = (name or "").strip().replace("\\", "/")
    if not raw:
        raise WorkspaceError("No filename given.")
    root = _root()
    candidate = (root / raw).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        raise WorkspaceError(
            f"'{name}' is outside the workspace. Files can only be written under "
            "data/workspace."
        ) from None
    if candidate.suffix.lower() in BLOCKED_SUFFIXES:
        raise WorkspaceError(f"Refusing to write a '{candidate.suffix}' file.")
    return candidate


def file_write(name: str, content: str, append: bool = False) -> dict:
    """Write a text file into the workspace (CSV, Markdown, JSON, plain text)."""
    try:
        path = _resolve(name)
    except WorkspaceError as ex:
        return {"error": "refused", "message": str(ex)}
    body = content or ""
    if len(body.encode("utf-8")) > MAX_BYTES:
        return {"error": "too_large",
                "message": f"That file is over {MAX_BYTES // 1024 // 1024} MB."}
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a" if append else "w", encoding="utf-8", newline="") as fh:
        fh.write(body)
    return {
        "ok": True,
        "path": path.relative_to(_root()).as_posix(),
        "bytes": path.stat().st_size,
        "message": f"{'Appended to' if append else 'Wrote'} {path.name}",
    }


def file_read(name: str, max_chars: int = 20000) -> dict:
    """Read a text file back out of the workspace."""
    try:
        path = _resolve(name)
    except WorkspaceError as ex:
        return {"error": "refused", "message": str(ex)}
    if not path.exists():
        return {"error": "not_found", "message": f"No file named '{name}' in the workspace."}
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as ex:
        return {"error": "unreadable", "message": str(ex)}
    return {
        "ok": True,
        "path": path.relative_to(_root()).as_posix(),
        "chars": len(text),
        "content": text[: int(max_chars)],
    }


def file_list() -> dict:
    """List the files Theta has saved in the workspace."""
    root = _root()
    files = [
        {
            "path": p.relative_to(root).as_posix(),
            "bytes": p.stat().st_size,
            "modified": int(p.stat().st_mtime),
        }
        for p in sorted(root.rglob("*"), key=lambda x: -x.stat().st_mtime if x.is_file() else 0)
        if p.is_file()
    ]
    return {"ok": True, "count": len(files), "files": files[:100]}
