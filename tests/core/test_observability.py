"""Tests for ObservabilityBridge event routing.

Covers:
    - SessionStart emission on start()
    - SYSTEM_START → UserPromptSubmit routing
    - SYSTEM_END → eventbus.ACTIVITY routing
    - SYSTEM_ERROR → eventbus.ACTIVITY (not AGENT_ERROR)
    - Activity event mapping (CODE_START, MEMORY_RECORD, etc.)
    - _on_event exception handling (never raises)
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def bridge():
    """Create an ObservabilityBridge with a mock transport."""
    from interpreter.core.observability import ObservabilityBridge

    b = ObservabilityBridge(session_id="test-session-1")
    b._transport = MagicMock(return_value=True)
    return b


@pytest.fixture
def started_bridge(bridge):
    """Create a bridge that's already started (bypassing EventBus dependency)."""
    bridge._started = True
    return bridge


@pytest.fixture
def make_event():
    """Factory for fake UIEvent objects."""

    def _make(event_type_name: str, data: dict | None = None, timestamp: float = 1.0):
        event = MagicMock()
        event.type.name = event_type_name
        event.data = data or {}
        event.timestamp = timestamp
        return event

    return _make


class TestBridgeStart:
    @patch(
        "interpreter.terminal_interface.components.ui_events.get_event_bus",
        create=True,
    )
    def test_start_sends_session_start(self, mock_get_bus, bridge):
        """start() should send a SessionStart envelope to the transport."""
        mock_bus = MagicMock()
        mock_get_bus.return_value = mock_bus

        # Patch the inline import to return our mock
        with patch.dict(
            "sys.modules",
            {
                "interpreter.terminal_interface.components.ui_events": MagicMock(
                    get_event_bus=mock_get_bus
                )
            },
        ):
            bridge.start()

        assert bridge._started is True
        assert bridge._transport.call_count >= 1
        envelope = bridge._transport.call_args_list[0][0][0]
        assert envelope["event_name"] == "SessionStart"
        assert envelope["session_id"] == "test-session-1"
        assert envelope["payload"]["session"]["source"] == "open-interpreter"

    def test_start_transport_failure_leaves_stopped(self, bridge):
        """If SessionStart _send fails, bridge should not be marked started."""
        bridge._transport = MagicMock(side_effect=ConnectionError("no daemon"))

        with patch.dict(
            "sys.modules",
            {
                "interpreter.terminal_interface.components.ui_events": MagicMock(
                    get_event_bus=MagicMock(return_value=MagicMock())
                )
            },
        ):
            bridge.start()

        assert bridge._started is False


class TestEventRouting:
    def test_system_start_routed_to_user_prompt_submit(
        self, started_bridge, make_event
    ):
        """SYSTEM_START should produce a UserPromptSubmit envelope."""
        event = make_event("SYSTEM_START", {"message": "list all files"})
        started_bridge._on_event(event)

        envelope = started_bridge._transport.call_args[0][0]
        assert envelope["event_name"] == "UserPromptSubmit"
        assert envelope["payload"]["prompt"] == "list all files"

    def test_system_end_routed_to_activity(self, started_bridge, make_event):
        """SYSTEM_END should produce an eventbus.ACTIVITY with activity_type=end."""
        event = make_event("SYSTEM_END", {})
        started_bridge._on_event(event)

        envelope = started_bridge._transport.call_args[0][0]
        assert envelope["event_name"] == "eventbus.ACTIVITY"
        assert envelope["payload"]["activity_type"] == "end"

    def test_system_error_routed_to_activity_not_agent_error(
        self, started_bridge, make_event
    ):
        """SYSTEM_ERROR should route to eventbus.ACTIVITY, not eventbus.AGENT_ERROR."""
        event = make_event("SYSTEM_ERROR", {"error": "LLM connection failed"})
        started_bridge._on_event(event)

        envelope = started_bridge._transport.call_args[0][0]
        assert envelope["event_name"] == "eventbus.ACTIVITY"
        assert envelope["event_name"] != "eventbus.AGENT_ERROR"

    def test_agent_spawn_direct_mapped(self, started_bridge, make_event):
        """AGENT_SPAWN should be directly mapped to eventbus.AGENT_SPAWN."""
        event = make_event("AGENT_SPAWN", {"agent_id": "scout-1", "role": "scout"})
        started_bridge._on_event(event)

        envelope = started_bridge._transport.call_args[0][0]
        assert envelope["event_name"] == "eventbus.AGENT_SPAWN"
        assert envelope["payload"]["agent_id"] == "scout-1"

    def test_code_start_routed_as_activity(self, started_bridge, make_event):
        """CODE_START should route through _ACTIVITY_EVENTS with type=execute."""
        event = make_event("CODE_START", {"message": "running script"})
        started_bridge._on_event(event)

        envelope = started_bridge._transport.call_args[0][0]
        assert envelope["event_name"] == "eventbus.ACTIVITY"
        assert envelope["payload"]["activity_type"] == "execute"

    def test_memory_record_routed_as_activity(self, started_bridge, make_event):
        """MEMORY_RECORD should route through _ACTIVITY_EVENTS with type=memory."""
        event = make_event("MEMORY_RECORD", {"message": "stored edit graph"})
        started_bridge._on_event(event)

        envelope = started_bridge._transport.call_args[0][0]
        assert envelope["event_name"] == "eventbus.ACTIVITY"
        assert envelope["payload"]["activity_type"] == "memory"

    def test_activity_message_truncated(self, started_bridge, make_event):
        """Activity event messages should be truncated to 200 chars."""
        long_msg = "x" * 500
        event = make_event("CODE_START", {"message": long_msg})
        started_bridge._on_event(event)

        envelope = started_bridge._transport.call_args[0][0]
        assert len(envelope["payload"]["message"]) <= 200

    def test_unknown_event_ignored(self, started_bridge, make_event):
        """Unknown event types should be silently ignored."""
        started_bridge._transport.reset_mock()
        event = make_event("TOTALLY_UNKNOWN_EVENT", {})
        started_bridge._on_event(event)

        started_bridge._transport.assert_not_called()


class TestPayloadSanitization:
    def test_agent_error_traceback_truncated(self, started_bridge, make_event):
        """AGENT_ERROR with long traceback should be truncated to 500 chars."""
        long_traceback = "Traceback:\n" + "x" * 1000
        event = make_event(
            "AGENT_ERROR",
            {
                "agent_id": "scout-1",
                "error": long_traceback,
                "secret_env_var": "API_KEY=sk-1234",  # Should be stripped
            },
        )
        started_bridge._on_event(event)

        envelope = started_bridge._transport.call_args[0][0]
        payload = envelope["payload"]
        assert len(payload.get("error", "")) <= 500
        assert "secret_env_var" not in payload

    def test_agent_spawn_only_allowed_fields(self, started_bridge, make_event):
        """AGENT_SPAWN should only forward allowed fields."""
        event = make_event(
            "AGENT_SPAWN",
            {
                "agent_id": "scout-1",
                "role": "scout",
                "internal_state": {"memory": "sensitive data"},
                "api_key": "sk-secret",
            },
        )
        started_bridge._on_event(event)

        envelope = started_bridge._transport.call_args[0][0]
        payload = envelope["payload"]
        assert payload["agent_id"] == "scout-1"
        assert payload["role"] == "scout"
        assert "internal_state" not in payload
        assert "api_key" not in payload

    def test_cancelled_agent_reason_preserved(self, started_bridge, make_event):
        """AGENT_CANCELLED should forward 'reason' field."""
        event = make_event(
            "AGENT_CANCELLED",
            {"agent_id": "scout-1", "reason": "user cancelled"},
        )
        started_bridge._on_event(event)

        envelope = started_bridge._transport.call_args[0][0]
        assert envelope["payload"]["reason"] == "user cancelled"


class TestEventHandlingResilience:
    def test_on_event_never_raises(self, started_bridge):
        """_on_event should never raise, even with malformed events."""
        bad_event = MagicMock(spec=[])
        del bad_event.type
        started_bridge._on_event(bad_event)  # Should not raise

    def test_on_event_skips_when_stopped(self, bridge, make_event):
        """_on_event should no-op when bridge is not started."""
        event = make_event("AGENT_SPAWN", {"agent_id": "x"})
        bridge._on_event(event)

        bridge._transport.assert_not_called()
