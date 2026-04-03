"""
cc-sidecar statusline — statusline script for Claude Code.

Called by Claude Code's statusLine config. Receives statusline JSON
on stdin, forwards to daemon as a statusline event.

The script itself outputs nothing to stdout (sidecar is passive).
"""

from __future__ import annotations

import json
import sys
import time

from .. import __version__
from .transport import send_event

# Monotonic sequence counter
_seq_counter = 0


def _next_seq() -> int:
    global _seq_counter
    _seq_counter += 1
    return _seq_counter


def run_statusline() -> int:
    """Main entry point for cc-sidecar statusline.

    Reads statusline JSON from stdin, wraps and sends to daemon.
    Always returns 0.
    """
    try:
        raw = sys.stdin.read()
        if not raw.strip():
            return 0

        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            return 0

        if not isinstance(payload, dict):
            return 0

        # Extract session id from statusline data
        session_id = "unknown"
        if "session" in payload and isinstance(payload["session"], dict):
            session_id = payload["session"].get("id", "unknown")

        envelope = {
            "received_at_ms": int(time.time() * 1000),
            "seq": _next_seq(),
            "session_id": session_id,
            "source_kind": "statusline",
            "event_name": "statusline",
            "payload": payload,
            "emitter_version": __version__,
        }

        send_event(envelope)

    except Exception:
        pass  # Never fail

    return 0
