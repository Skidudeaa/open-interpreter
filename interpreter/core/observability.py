"""
ObservabilityBridge — bridges the fork's EventBus to cc-sidecar.

Subscribes to the fork's internal EventBus (UIEvent/EventType) and
translates events into cc-sidecar format, sending them via the
sidecar's local transport (Unix domain socket, fire-and-forget).

Activation:
    interpreter.enable_observability = True
    # or: OI_ACTIVATE_ALL=true

Architecture:
    Fork EventBus (ui_events.py)
        │
        ├─ SYSTEM_START                →  UserPromptSubmit (session lifecycle)
        ├─ SYSTEM_END                  →  eventbus.ACTIVITY (session end)
        ├─ SYSTEM_ERROR                →  eventbus.ACTIVITY
        ├─ AGENT_SPAWN/COMPLETE/ERROR  →  eventbus.AGENT_SPAWN/COMPLETE/ERROR
        ├─ CODE_START/END              →  eventbus.ACTIVITY (execute)
        ├─ ACTIVITY                    →  eventbus.ACTIVITY
        ├─ FILE_CHANGE / GIT_COMMIT    →  eventbus.FILE_CHANGE
        ├─ SYSTEM_TOKEN_UPDATE         →  eventbus.SYSTEM_TOKEN_UPDATE
        ├─ TEST_START/END              →  eventbus.TEST_START/END
        ├─ VALIDATION_START/END        →  eventbus.ACTIVITY (validate)
        └─ MEMORY_RECORD / PLUGIN_HOOK →  eventbus.ACTIVITY
                │
                └──→  cc-sidecar transport  →  daemon (or spool)
"""

from __future__ import annotations

import logging
import os
import threading
import time
from typing import TYPE_CHECKING, Any

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from ..terminal_interface.components.ui_events import UIEvent


# Event type name → sidecar event name mapping
_EVENTBUS_MAP: dict[str, str] = {
    "AGENT_SPAWN": "eventbus.AGENT_SPAWN",
    "AGENT_COMPLETE": "eventbus.AGENT_COMPLETE",
    "AGENT_ERROR": "eventbus.AGENT_ERROR",
    "AGENT_CANCELLED": "eventbus.AGENT_ERROR",
    "ACTIVITY": "eventbus.ACTIVITY",
    "FILE_CHANGE": "eventbus.FILE_CHANGE",
    "GIT_COMMIT": "eventbus.FILE_CHANGE",
    "FILE_INCLUDE": "eventbus.FILE_INCLUDE",
    "SYSTEM_TOKEN_UPDATE": "eventbus.SYSTEM_TOKEN_UPDATE",
    # WHY: SYSTEM_ERROR is system-level (e.g. LLM connection failure), not agent-level.
    # Routing to ACTIVITY avoids creating phantom agent rows with empty IDs.
    "SYSTEM_ERROR": "eventbus.ACTIVITY",
    "TEST_START": "eventbus.TEST_START",
    "TEST_END": "eventbus.TEST_END",
}

# Events handled with special-case logic in _on_event (not in the map above)
# SYSTEM_START → UserPromptSubmit (session lifecycle marker)
# SYSTEM_END   → eventbus.ACTIVITY with activity_type=end

# Events that generate activity updates instead of direct mappings
_ACTIVITY_EVENTS: dict[str, str] = {
    "CODE_START": "execute",
    "CODE_END": "execute",
    "VALIDATION_START": "validate",
    "VALIDATION_END": "validate",
    "TRACING_START": "execute",
    "TRACING_END": "execute",
    "MESSAGE_START": "think",
    "MEMORY_RECORD": "memory",
    "PLUGIN_HOOK": "plugin",
}


# WHY: Direct-mapped events forward event.data to the sidecar. Without allowlists,
# AGENT_ERROR tracebacks may contain env vars / API keys, and large payloads
# bloat the SQLite store. Only pass the fields the reducer actually needs.
_PAYLOAD_ALLOWLISTS: dict[str, list[str]] = {
    "AGENT_SPAWN": ["agent_id", "id", "role"],
    "AGENT_COMPLETE": ["agent_id", "id", "output"],
    "AGENT_ERROR": ["agent_id", "id", "error", "reason"],
    "AGENT_CANCELLED": ["agent_id", "id", "error", "reason"],
    "ACTIVITY": ["activity_type", "type", "message", "agent_id"],
    "FILE_CHANGE": ["path", "file_path", "added_lines", "removed_lines"],
    "GIT_COMMIT": ["path", "file_path", "sha", "message"],
    "FILE_INCLUDE": ["path", "abs_path", "raw_bytes", "included_chars", "truncated"],
    "SYSTEM_TOKEN_UPDATE": ["total_tokens", "prompt_tokens", "completion_tokens"],
    "SYSTEM_ERROR": ["error", "message", "activity_type"],
    "TEST_START": ["test_name", "test_file", "agent_id"],
    "TEST_END": ["test_name", "test_file", "passed", "agent_id", "duration_ms"],
}

# Max length for string values in sanitized payloads
_MAX_FIELD_LEN = 500


def _sanitize_payload(event_type_name: str, data: dict[str, Any]) -> dict[str, Any]:
    """Extract only allowed fields and truncate strings."""
    allowed = _PAYLOAD_ALLOWLISTS.get(event_type_name)
    if allowed is None:
        return data  # Unknown event type — pass through

    result: dict[str, Any] = {}
    for key in allowed:
        if key in data:
            val = data[key]
            if isinstance(val, str) and len(val) > _MAX_FIELD_LEN:
                val = val[:_MAX_FIELD_LEN]
            result[key] = val
    return result


class ObservabilityBridge:
    """Bridges fork's EventBus to cc-sidecar transport.

    Usage:
        bridge = ObservabilityBridge(session_id="abc123")
        bridge.start()  # subscribes to EventBus
        # ... events flow automatically ...
        bridge.stop()   # unsubscribes
    """

    def __init__(self, session_id: str | None = None):
        self._session_id = session_id or os.environ.get("CLAUDE_SESSION_ID", "unknown")
        self._started = False
        self._transport = None

    def start(self) -> None:
        """Subscribe to the fork's EventBus and announce session to sidecar."""
        if self._started:
            return

        try:
            from ..terminal_interface.components.ui_events import get_event_bus

            bus = get_event_bus()
            bus.subscribe_all(self._on_event)
            logger.debug("ObservabilityBridge started for session %s", self._session_id)

            # WHY: Announce session to the reducer so it creates the session row.
            # Without this, events arrive before the reducer knows the session exists.
            self._send(
                "SessionStart",
                {
                    "session": {
                        "id": self._session_id,
                        "source": "open-interpreter",
                        "model": os.environ.get("OI_MODEL", ""),
                    }
                },
                time.time(),
            )
            # WHY: Only mark started after SessionStart succeeds — if transport
            # fails, _started=False prevents routing events to a dead pipe.
            self._started = True
        except Exception:
            logger.debug("ObservabilityBridge: failed to start", exc_info=True)

    def stop(self) -> None:
        """Stop bridge and unsubscribe from EventBus."""
        if not self._started:
            return
        self._started = False
        try:
            from ..terminal_interface.components.ui_events import get_event_bus

            bus = get_event_bus()
            bus.unsubscribe_all(self._on_event)
        except Exception:
            pass  # Best-effort cleanup

    @property
    def session_id(self) -> str:
        return self._session_id

    @session_id.setter
    def session_id(self, value: str) -> None:
        self._session_id = value

    def _get_transport(self):
        """Lazy-load transport to avoid import overhead if sidecar not running."""
        if self._transport is None:
            try:
                from cc_sidecar.ingest.transport import send_event

                self._transport = send_event
            except ImportError:
                # cc-sidecar not installed — use fallback inline transport
                self._transport = self._fallback_send
        return self._transport

    @staticmethod
    def _fallback_send(event: dict[str, Any]) -> bool:
        """Fallback when cc-sidecar package is not installed.

        Tries to send via Unix socket directly.
        """
        import json
        import socket
        from pathlib import Path

        sock_path = Path.home() / ".cc-sidecar" / "daemon.sock"
        if not sock_path.exists():
            return False

        try:
            data = json.dumps(event, separators=(",", ":")).encode("utf-8") + b"\n"
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            sock.settimeout(0.5)
            try:
                sock.connect(str(sock_path))
                sock.sendall(data)
                return True
            finally:
                sock.close()
        except Exception:
            return False

    def _on_event(self, event: UIEvent) -> None:
        """Handle an event from the fork's EventBus."""
        if not self._started:
            return

        try:
            event_type_name = event.type.name  # e.g. "AGENT_SPAWN"

            # WHY: SYSTEM_START/END are session lifecycle markers that need
            # special payloads the reducer expects, not generic activity events.
            if event_type_name == "SYSTEM_START":
                self._send(
                    "UserPromptSubmit",
                    {
                        "prompt": event.data.get("message", ""),
                        "session_id": self._session_id,
                    },
                    event.timestamp,
                )
                return
            if event_type_name == "SYSTEM_END":
                self._send(
                    "eventbus.ACTIVITY",
                    {"activity_type": "end", "message": "session_end"},
                    event.timestamp,
                )
                return

            # Check direct mapping first
            sidecar_event = _EVENTBUS_MAP.get(event_type_name)
            if sidecar_event:
                # WHY: Sanitize payload to prevent leaking secrets from error
                # tracebacks, env vars, or large code blocks into the sidecar DB.
                sanitized = _sanitize_payload(event_type_name, event.data)
                self._send(sidecar_event, sanitized, event.timestamp)
                return

            # Check activity mapping — only send type + message, not raw payloads
            # WHY: event.data can contain large code blocks / console output
            # that would bloat the sidecar's SQLite store unnecessarily
            activity_type = _ACTIVITY_EVENTS.get(event_type_name)
            if activity_type:
                self._send(
                    "eventbus.ACTIVITY",
                    {
                        "activity_type": activity_type,
                        "message": str(event.data.get("message", event_type_name))[
                            :200
                        ],
                        "agent_id": event.data.get("agent_id"),
                    },
                    event.timestamp,
                )
                return

        except Exception:
            # WHY: Never disrupt the fork's event flow, but log for diagnostics.
            logger.debug(
                "ObservabilityBridge: event handling failed for %s",
                event.type.name if hasattr(event, "type") else "unknown",
                exc_info=True,
            )

    def _send(
        self, event_name: str, data: dict[str, Any], timestamp: float | None = None
    ) -> None:
        """Send an event to the sidecar daemon."""
        ts_ms = round((timestamp or time.time()) * 1000)
        envelope = {
            "received_at_ms": ts_ms,
            "seq": 0,  # daemon assigns sequence
            "session_id": self._session_id,
            "source_kind": "eventbus",
            "event_name": event_name,
            "payload": data,
        }
        transport = self._get_transport()
        transport(envelope)


# --- Lazy-loaded singleton (follows fork's pattern from core.py) ---

_bridge_lock = threading.Lock()
_bridge_instance: ObservabilityBridge | None = None


def get_observability_bridge(session_id: str | None = None) -> ObservabilityBridge:
    """Get or create the global ObservabilityBridge (thread-safe, lazy)."""
    global _bridge_instance
    if _bridge_instance is not None:
        return _bridge_instance
    with _bridge_lock:
        if _bridge_instance is None:
            _bridge_instance = ObservabilityBridge(session_id=session_id)
    return _bridge_instance


def reset_observability_bridge() -> None:
    """Reset the bridge (for testing or new sessions)."""
    global _bridge_instance
    with _bridge_lock:
        if _bridge_instance:
            _bridge_instance.stop()
        _bridge_instance = None
