"""
cc-sidecar emit — ingest a hook event from Claude Code.

Called by Claude Code hooks via settings.json. Reads stdin JSON,
wraps it with metadata, and sends to the daemon.

Hard rules (from spec):
    - Read stdin JSON
    - Wrap with received_at, local monotonic sequence, emitter version
    - Write nothing to stdout
    - Exit 0 even when the daemon is down
    - Never return hook decision fields (no "decision", no "reason")
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
from typing import Any

from .. import __version__
from .transport import send_event

logger = logging.getLogger(__name__)

# Monotonic sequence counter (per-process)
_seq_counter = 0


def _next_seq() -> int:
    global _seq_counter
    _seq_counter += 1
    return _seq_counter


def _detect_event_name(payload: dict[str, Any]) -> str:
    """Detect the event name from the hook payload.

    Claude Code passes different fields depending on the hook event.
    We use heuristics to detect the event type.
    """
    # Check for explicit event field
    if "event" in payload:
        return str(payload["event"])
    if "hook_event" in payload:
        return str(payload["hook_event"])

    # Heuristic detection based on payload structure
    if "session" in payload and "tool_name" not in payload:
        session = payload.get("session", {})
        if isinstance(session, dict) and session.get("source"):
            return "SessionStart"
    if "tool_name" in payload:
        if "output" in payload or "result" in payload:
            if "error" in payload:
                return "PostToolUseFailure"
            return "PostToolUse"
        return "PreToolUse"
    if "agent_id" in payload:
        if "last_assistant_message" in payload or "summary" in payload:
            return "SubagentStop"
        return "SubagentStart"
    if "notification_type" in payload or "type" in payload:
        ptype = payload.get("notification_type", payload.get("type", ""))
        if ptype == "permission":
            return "PermissionRequest"
        return "Notification"
    if "prompt" in payload and "tool_name" not in payload:
        return "UserPromptSubmit"

    return "Unknown"


def _extract_session_id(payload: dict[str, Any]) -> str:
    """Extract session ID from the payload."""
    # Direct session_id field
    if "session_id" in payload:
        return str(payload["session_id"])
    # Nested in session object
    if "session" in payload and isinstance(payload["session"], dict):
        sid = payload["session"].get("id", payload["session"].get("session_id"))
        if sid:
            return str(sid)
    # Environment variable fallback
    env_sid = os.environ.get("CLAUDE_SESSION_ID", os.environ.get("CC_SESSION_ID"))
    if env_sid:
        return env_sid
    return "unknown"


def run_emit(subagent: bool = False, event_name_override: str | None = None) -> int:
    """Main entry point for cc-sidecar emit.

    Reads JSON from stdin, wraps with metadata, sends to daemon.
    Always returns 0 (hooks must never fail).
    """
    try:
        raw = sys.stdin.read()
        if not raw.strip():
            return 0

        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            # Not JSON — wrap as raw text
            payload = {"raw_text": raw.strip()}

        if not isinstance(payload, dict):
            payload = {"value": payload}

        event_name = event_name_override or _detect_event_name(payload)
        session_id = _extract_session_id(payload)

        envelope = {
            "received_at_ms": int(time.time() * 1000),
            "seq": _next_seq(),
            "session_id": session_id,
            "source_kind": "hook",
            "event_name": event_name,
            "payload": payload,
            "emitter_version": __version__,
        }

        if subagent:
            envelope["source_kind"] = "hook_subagent"
            envelope["subagent"] = True

        send_event(envelope)

    except Exception:
        logger.debug("emit failed", exc_info=True)

    return 0
