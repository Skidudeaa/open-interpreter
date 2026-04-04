"""Shared clipboard utilities for both prompt_toolkit and Textual backends.

WHY: Both backends need copy-to-clipboard with the same error handling,
preview truncation, and fallback behavior. Extracting this prevents
divergent implementations that handle edge cases differently.
"""

from __future__ import annotations

from typing import Any


def get_last_content(
    interpreter: Any,
    *,
    prefer_code: str = "",
    prefer_assistant: str = "",
) -> str:
    """Extract the best content to copy from interpreter state.

    Args:
        interpreter: The OpenInterpreter instance.
        prefer_code: Pre-tracked code content (Textual backend tracks this).
        prefer_assistant: Pre-tracked assistant content (Textual backend).

    Returns:
        The content string to copy, or empty string if nothing found.
    """
    # WHY: Textual backend tracks content reactively via widget state,
    # while prompt_toolkit backend must search interpreter.messages.
    # Support both patterns through optional pre-tracked args.
    if prefer_code:
        return prefer_code
    if prefer_assistant:
        return prefer_assistant

    # Fallback: search interpreter's message history (prompt_toolkit path)
    if hasattr(interpreter, "messages"):
        for msg in reversed(interpreter.messages):
            if msg.get("role") == "assistant":
                content = msg.get("content", "")
                if content:
                    return content
    return ""


def copy_to_clipboard(content: str) -> tuple[bool, str]:
    """Copy content to clipboard with full error handling.

    Returns:
        (success, message) — message is either a preview string or an error.
    """
    try:
        import pyperclip
    except ImportError:
        return False, "pyperclip not installed"

    if not content:
        return False, "Nothing to copy"

    try:
        pyperclip.copy(content)
    except Exception as e:
        return False, f"Copy failed (no clipboard backend): {e}"

    preview = content[:50].replace("\n", " ")
    if len(content) > 50:
        preview += "..."
    return True, preview
