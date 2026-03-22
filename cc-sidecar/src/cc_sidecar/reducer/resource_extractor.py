"""
Resource extractor — turn raw tool payloads into human-readable one-liners.

Examples:
    Read → "src/main.py:10-50"
    Bash → "git status"
    Agent/Task → "Explore: find auth endpoints"
    WebFetch → "https://example.com"
    Grep → "pattern='TODO' in src/"
"""

from __future__ import annotations

from typing import Any

# Max length for preview strings
MAX_PREVIEW = 120

# Tool names that are aliases (Task was renamed to Agent in Claude Code 2.1.63)
AGENT_TOOL_ALIASES = frozenset({"Agent", "Task"})


def extract_resource(tool_name: str, payload: dict[str, Any]) -> str:
    """Extract a human-readable resource description from a tool payload.

    Args:
        tool_name: The tool name (e.g. "Read", "Bash", "Agent")
        payload: The tool input or output payload

    Returns:
        A short string describing what the tool is operating on.
    """
    # Normalize mcp__* tools
    if tool_name.startswith("mcp__"):
        return _extract_mcp(tool_name, payload)

    extractor = _EXTRACTORS.get(tool_name)
    if extractor:
        try:
            return extractor(payload)
        except (KeyError, TypeError, AttributeError):
            pass

    # Handle Agent/Task alias
    if tool_name in AGENT_TOOL_ALIASES:
        return _extract_agent(payload)

    # Fallback: show tool name + first key
    return _truncate(f"{tool_name}: {_first_value(payload)}")


def _extract_read(p: dict) -> str:
    path = p.get("file_path", p.get("path", "?"))
    offset = p.get("offset")
    limit = p.get("limit")
    if offset and limit:
        return f"{path}:{offset}-{offset + limit}"
    elif offset:
        return f"{path}:{offset}-"
    return str(path)


def _extract_write(p: dict) -> str:
    return str(p.get("file_path", p.get("path", "?")))


def _extract_edit(p: dict) -> str:
    path = p.get("file_path", p.get("path", "?"))
    old = p.get("old_string", "")
    preview = old[:40].replace("\n", "\\n") if old else ""
    return _truncate(f"{path} [{preview}...]") if preview else str(path)


def _extract_bash(p: dict) -> str:
    cmd = p.get("command", "")
    # Show first line, truncated
    first_line = cmd.split("\n")[0].split("&&")[0].strip()
    return _truncate(first_line)


def _extract_glob(p: dict) -> str:
    pattern = p.get("pattern", "?")
    path = p.get("path", "")
    if path:
        return f"{pattern} in {path}"
    return str(pattern)


def _extract_grep(p: dict) -> str:
    pattern = p.get("pattern", "?")
    path = p.get("path", "")
    if path:
        return f"/{pattern}/ in {path}"
    return f"/{pattern}/"


def _extract_web_fetch(p: dict) -> str:
    url = p.get("url", "?")
    return _truncate(str(url))


def _extract_web_search(p: dict) -> str:
    return _truncate(str(p.get("query", "?")))


def _extract_agent(p: dict) -> str:
    agent_type = p.get("subagent_type", p.get("type", ""))
    prompt = p.get("prompt", p.get("description", ""))
    bg = " [bg]" if p.get("run_in_background") else ""
    snippet = prompt[:60].replace("\n", " ") if prompt else ""
    parts = [agent_type, snippet, bg]
    return _truncate(" ".join(part for part in parts if part))


def _extract_notebook_edit(p: dict) -> str:
    path = p.get("notebook_path", "?")
    mode = p.get("edit_mode", "replace")
    return f"{path} ({mode})"


def _extract_todo_write(p: dict) -> str:
    todos = p.get("todos", [])
    in_progress = [t for t in todos if isinstance(t, dict) and t.get("status") == "in_progress"]
    if in_progress:
        return _truncate(in_progress[0].get("content", "?"))
    return f"{len(todos)} items"


def _extract_mcp(tool_name: str, p: dict) -> str:
    # mcp__server__tool → "server/tool: first_arg_value"
    parts = tool_name.split("__", 2)
    if len(parts) >= 3:
        server, tool = parts[1], parts[2]
        label = f"{server}/{tool}"
    else:
        label = tool_name
    return _truncate(f"{label}: {_first_value(p)}")


def _first_value(p: dict) -> str:
    """Get the first non-empty string value from a dict."""
    if not isinstance(p, dict):
        return str(p)[:60]
    for v in p.values():
        if isinstance(v, str) and v.strip():
            return v.strip()[:60]
    return ""


def _truncate(s: str, max_len: int = MAX_PREVIEW) -> str:
    """Truncate string with ellipsis."""
    if len(s) <= max_len:
        return s
    return s[: max_len - 1] + "…"


# Registry of tool-specific extractors
_EXTRACTORS: dict[str, Any] = {
    "Read": _extract_read,
    "Write": _extract_write,
    "Edit": _extract_edit,
    "Bash": _extract_bash,
    "Glob": _extract_glob,
    "Grep": _extract_grep,
    "WebFetch": _extract_web_fetch,
    "WebSearch": _extract_web_search,
    "Agent": _extract_agent,
    "Task": _extract_agent,
    "NotebookEdit": _extract_notebook_edit,
    "TodoWrite": _extract_todo_write,
}
