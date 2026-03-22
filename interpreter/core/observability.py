"""
ObservabilityBridge — bridges the fork's EventBus to cc-sidecar.

Subscribes to the fork's internal EventBus (UIEvent/EventType) and
translates events into cc-sidecar format, sending them via the
sidecar's local transport.

Also implements AgentPlugin for ON_TOOL_CALL and ON_ERROR hooks,
giving the sidecar full per-tool visibility inside fork agents.

Activation:
    interpreter.enable_observability = True
    # or: OI_ACTIVATE_ALL=true

Architecture:
    Fork EventBus (ui_events.py)
        │
        ├─ AGENT_SPAWN/COMPLETE/ERROR  →  eventbus.AGENT_SPAWN/COMPLETE/ERROR
        ├─ CODE_START/END              →  (mapped to tool lifecycle)
        ├─ ACTIVITY                    →  eventbus.ACTIVITY
        ├─ FILE_CHANGE                 →  eventbus.FILE_CHANGE
        ├─ SYSTEM_TOKEN_UPDATE         →  eventbus.SYSTEM_TOKEN_UPDATE
        ├─ TEST_START/END              →  eventbus.TEST_START/END
        ├─ VALIDATION_START/END        →  (mapped to activity)
        └─ GIT_COMMIT                  →  eventbus.FILE_CHANGE
                │
                └──→  cc-sidecar transport  →  daemon
"""

from __future__ import annotations

import logging
import os
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
    "SYSTEM_TOKEN_UPDATE": "eventbus.SYSTEM_TOKEN_UPDATE",
    "TEST_START": "eventbus.TEST_START",
    "TEST_END": "eventbus.TEST_END",
}

# Events that generate activity updates instead of direct mappings
_ACTIVITY_EVENTS: dict[str, str] = {
    "CODE_START": "execute",
    "CODE_END": "execute",
    "VALIDATION_START": "validate",
    "VALIDATION_END": "validate",
    "TRACING_START": "execute",
    "TRACING_END": "execute",
    "MESSAGE_START": "think",
}


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
        """Subscribe to the fork's EventBus."""
        if self._started:
            return

        try:
            from ..terminal_interface.components.ui_events import get_event_bus

            bus = get_event_bus()
            bus.subscribe_all(self._on_event)
            self._started = True
            logger.debug("ObservabilityBridge started for session %s", self._session_id)
        except Exception:
            logger.debug("ObservabilityBridge: EventBus not available", exc_info=True)

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

            # Check direct mapping first
            sidecar_event = _EVENTBUS_MAP.get(event_type_name)
            if sidecar_event:
                self._send(sidecar_event, event.data, event.timestamp)
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
            pass  # Never disrupt the fork's event flow

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

import threading

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
