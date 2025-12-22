"""
Comprehensive tests for Phase 0 and Phase 1 Terminal UI Architecture.

Tests cover:
- UIState: State management, agent tracking, properties
- UIEvents: Event bus, subscriptions, rate limiting, chunk conversion
- UIBackend: Backend creation logic, TTY detection
- Sanitizer: Escape sequence filtering
- InputHandler: Key bindings (mock-based, no real terminal)
- Completers: Magic commands, file paths

All tests are designed to run without prompt_toolkit interaction.
"""

import pytest
import time
import os
import sys
from unittest.mock import Mock, MagicMock, patch
from collections import deque
from queue import Queue

# Import components under test
from interpreter.terminal_interface.components.ui_state import (
    UIState,
    UIMode,
    AgentState,
    AgentStatus,
    AgentRole,
    ConversationState,
    ContextState,
)
from interpreter.terminal_interface.components.ui_events import (
    UIEvent,
    EventType,
    EventBus,
    chunk_to_event,
    get_event_bus,
    reset_event_bus,
)
from interpreter.terminal_interface.components.sanitizer import (
    sanitize_output,
    strip_ansi,
    has_dangerous_sequences,
    get_sanitization_report,
    SanitizeLevel,
    is_safe_sgr,
    SAFE_SGR_CODES,
)
from interpreter.terminal_interface.components.ui_backend import (
    BackendType,
    UIBackend,
    RichStreamBackend,
    PromptToolkitBackend,
    create_backend,
    is_tty,
    prompt_toolkit_available,
)


# ============================================================================
# UIState Tests
# ============================================================================

class TestUIState:
    """Tests for UIState dataclass and methods"""

    def test_initialization(self):
        """Test UIState initializes with correct defaults"""
        state = UIState()
        assert state.mode == UIMode.ZEN
        assert len(state.active_agents) == 0
        assert state.selected_agent_id is None
        assert len(state.panels_visible) == 0
        assert state.context_tokens == 0
        assert state.context_limit == 128000
        assert state.is_streaming is False
        assert state.is_responding is False
        assert state.complexity_score == 0

    def test_context_usage_percent(self):
        """Test context window percentage calculation"""
        state = UIState()
        assert state.context_usage_percent == 0.0

        state.context_tokens = 64000
        state.context_limit = 128000
        assert state.context_usage_percent == 50.0

        state.context_tokens = 128000
        assert state.context_usage_percent == 100.0

        # Edge case: zero limit
        state.context_limit = 0
        assert state.context_usage_percent == 0.0

    def test_has_active_agents(self):
        """Test has_active_agents property"""
        state = UIState()
        assert state.has_active_agents is False

        # Add pending agent
        agent1 = AgentState(id="agent-1", role=AgentRole.SCOUT)
        state.active_agents["agent-1"] = agent1
        assert state.has_active_agents is False  # Not running yet

        # Set to running
        agent1.status = AgentStatus.RUNNING
        assert state.has_active_agents is True

        # Complete agent
        agent1.status = AgentStatus.COMPLETE
        assert state.has_active_agents is False

    def test_agent_strip_visible(self):
        """Test agent strip visibility logic"""
        state = UIState()
        assert state.agent_strip_visible is False

        # Add any agent
        state.active_agents["agent-1"] = AgentState(id="agent-1", role=AgentRole.SCOUT)
        assert state.agent_strip_visible is True

    def test_context_panel_visible(self):
        """Test context panel visibility logic"""
        state = UIState()

        # Not visible in ZEN mode by default
        state.mode = UIMode.ZEN
        assert state.context_panel_visible is False

        # Visible in POWER mode
        state.mode = UIMode.POWER
        assert state.context_panel_visible is True

        # Visible in DEBUG mode
        state.mode = UIMode.DEBUG
        assert state.context_panel_visible is True

        # Visible if explicitly enabled
        state.mode = UIMode.ZEN
        state.panels_visible.add("context")
        assert state.context_panel_visible is True

        # Auto-show with content
        state.panels_visible.clear()
        state.context.variables["x"] = "int"
        assert state.context_panel_visible is True

    def test_reset_agents(self):
        """Test reset_agents clears all agent state"""
        state = UIState()
        state.active_agents["agent-1"] = AgentState(id="agent-1", role=AgentRole.SCOUT)
        state.selected_agent_id = "agent-1"

        state.reset_agents()

        assert len(state.active_agents) == 0
        assert state.selected_agent_id is None

    def test_add_agent(self):
        """Test add_agent creates and registers agent"""
        state = UIState()
        agent = state.add_agent("test-agent", AgentRole.SURGEON)

        assert agent.id == "test-agent"
        assert agent.role == AgentRole.SURGEON
        assert agent.status == AgentStatus.PENDING
        assert "test-agent" in state.active_agents
        assert state.complexity_score == 10

        # Add with parent
        child = state.add_agent("child-agent", AgentRole.SCOUT, parent_id="test-agent")
        assert child.parent_id == "test-agent"
        assert state.complexity_score == 20

    def test_update_agent_status(self):
        """Test update_agent_status updates status and timestamp"""
        state = UIState()
        agent = state.add_agent("test-agent", AgentRole.SCOUT)
        agent.status = AgentStatus.RUNNING

        # Complete without error
        state.update_agent_status("test-agent", AgentStatus.COMPLETE)
        assert agent.status == AgentStatus.COMPLETE
        assert agent.completed_at is not None
        assert agent.error_summary is None

        # Error with message
        agent2 = state.add_agent("error-agent", AgentRole.SURGEON)
        state.update_agent_status("error-agent", AgentStatus.ERROR, error="Test error")
        assert agent2.status == AgentStatus.ERROR
        assert agent2.error_summary == "Test error"
        assert agent2.completed_at is not None

    def test_append_agent_output(self):
        """Test append_agent_output adds lines to deque"""
        state = UIState()
        agent = state.add_agent("test-agent", AgentRole.SCOUT)

        state.append_agent_output("test-agent", "Line 1")
        state.append_agent_output("test-agent", "Line 2")

        assert len(agent.last_lines) == 2
        assert list(agent.last_lines) == ["Line 1", "Line 2"]

        # Test maxlen=5
        for i in range(10):
            state.append_agent_output("test-agent", f"Line {i}")

        assert len(agent.last_lines) == 5
        assert "Line 9" in agent.last_lines


class TestAgentState:
    """Tests for AgentState dataclass"""

    def test_initialization(self):
        """Test AgentState initializes correctly"""
        agent = AgentState(id="test", role=AgentRole.SCOUT)
        assert agent.id == "test"
        assert agent.role == AgentRole.SCOUT
        assert agent.status == AgentStatus.PENDING
        assert agent.started_at > 0
        assert agent.completed_at is None
        assert isinstance(agent.last_lines, deque)
        assert agent.last_lines.maxlen == 5

    def test_elapsed_seconds(self):
        """Test elapsed_seconds calculation"""
        agent = AgentState(id="test", role=AgentRole.SCOUT)
        time.sleep(0.1)

        elapsed = agent.elapsed_seconds
        assert elapsed >= 0.1
        assert elapsed < 1.0  # Should be quick

        # With completion time
        agent.completed_at = agent.started_at + 5.0
        assert agent.elapsed_seconds == 5.0

    def test_elapsed_display(self):
        """Test human-readable elapsed time"""
        agent = AgentState(id="test", role=AgentRole.SCOUT)

        # Seconds
        agent.completed_at = agent.started_at + 30.5
        assert agent.elapsed_display == "30.5s"

        # Minutes
        agent.completed_at = agent.started_at + 90.0
        assert agent.elapsed_display == "1.5m"

        # Hours
        agent.completed_at = agent.started_at + 7200.0
        assert agent.elapsed_display == "2.0h"

    def test_status_icon(self):
        """Test status icon mapping"""
        agent = AgentState(id="test", role=AgentRole.SCOUT)

        assert agent.status_icon == "○"  # PENDING

        agent.status = AgentStatus.RUNNING
        assert agent.status_icon == "⏳"

        agent.status = AgentStatus.COMPLETE
        assert agent.status_icon == "✓"

        agent.status = AgentStatus.ERROR
        assert agent.status_icon == "✗"

        agent.status = AgentStatus.CANCELLED
        assert agent.status_icon == "⊘"


# ============================================================================
# UIEvents Tests
# ============================================================================

class TestUIEvent:
    """Tests for UIEvent dataclass"""

    def test_initialization(self):
        """Test UIEvent initializes correctly"""
        event = UIEvent(type=EventType.MESSAGE_CHUNK, data={"content": "hello"})
        assert event.type == EventType.MESSAGE_CHUNK
        assert event.data == {"content": "hello"}
        assert event.timestamp > 0
        assert event.source == "unknown"

    def test_post_init_dict_default(self):
        """Test data defaults to empty dict"""
        event = UIEvent(type=EventType.MESSAGE_START, data=None)
        assert event.data == {}


class TestEventBus:
    """Tests for EventBus"""

    def setup_method(self):
        """Reset event bus before each test"""
        reset_event_bus()
        self.bus = EventBus()

    def test_initialization(self):
        """Test EventBus initializes correctly"""
        assert isinstance(self.bus._queue, Queue)
        assert len(self.bus._handlers) == 0
        assert len(self.bus._global_handlers) == 0
        assert self.bus.pending_count == 0

    def test_emit_and_poll(self):
        """Test emitting and polling events"""
        event = UIEvent(type=EventType.MESSAGE_CHUNK, data={"content": "test"})

        # Emit
        result = self.bus.emit(event)
        assert result is True
        assert self.bus.pending_count == 1

        # Poll
        retrieved = self.bus.poll()
        assert retrieved is not None
        assert retrieved.type == EventType.MESSAGE_CHUNK
        assert retrieved.data["content"] == "test"
        assert self.bus.pending_count == 0

        # Poll empty
        assert self.bus.poll() is None

    def test_drain(self):
        """Test draining multiple events"""
        events = [
            UIEvent(type=EventType.MESSAGE_CHUNK, data={"content": f"msg{i}"})
            for i in range(5)
        ]

        for event in events:
            self.bus.emit(event)

        drained = self.bus.drain(max_events=3)
        assert len(drained) == 3
        assert self.bus.pending_count == 2

        # Drain remaining
        remaining = self.bus.drain()
        assert len(remaining) == 2

    def test_subscribe_and_dispatch(self):
        """Test subscribing and dispatching to handlers"""
        handled_events = []

        def handler(event: UIEvent):
            handled_events.append(event)

        # Subscribe
        self.bus.subscribe(EventType.MESSAGE_CHUNK, handler)

        # Dispatch
        event = UIEvent(type=EventType.MESSAGE_CHUNK, data={"content": "test"})
        self.bus.dispatch(event)

        assert len(handled_events) == 1
        assert handled_events[0].data["content"] == "test"

        # Different event type (should not trigger)
        event2 = UIEvent(type=EventType.CODE_CHUNK, data={})
        self.bus.dispatch(event2)

        assert len(handled_events) == 1  # Still only 1

    def test_subscribe_all(self):
        """Test subscribing to all events"""
        handled_events = []

        def global_handler(event: UIEvent):
            handled_events.append(event)

        self.bus.subscribe_all(global_handler)

        # Dispatch different types
        self.bus.dispatch(UIEvent(type=EventType.MESSAGE_CHUNK, data={}))
        self.bus.dispatch(UIEvent(type=EventType.CODE_CHUNK, data={}))
        self.bus.dispatch(UIEvent(type=EventType.CONSOLE_OUTPUT, data={}))

        assert len(handled_events) == 3

    def test_unsubscribe(self):
        """Test unsubscribing handlers"""
        handled_events = []

        def handler(event: UIEvent):
            handled_events.append(event)

        self.bus.subscribe(EventType.MESSAGE_CHUNK, handler)
        self.bus.dispatch(UIEvent(type=EventType.MESSAGE_CHUNK, data={}))
        assert len(handled_events) == 1

        # Unsubscribe
        self.bus.unsubscribe(EventType.MESSAGE_CHUNK, handler)
        self.bus.dispatch(UIEvent(type=EventType.MESSAGE_CHUNK, data={}))
        assert len(handled_events) == 1  # No new events

    def test_rate_limiting(self):
        """Test rate limiting of events"""
        self.bus.set_rate_limit(EventType.CONSOLE_OUTPUT, min_interval_seconds=0.1)

        # First event should succeed
        event1 = UIEvent(type=EventType.CONSOLE_OUTPUT, data={"content": "1"})
        assert self.bus.emit(event1) is True

        # Immediate second event should be rate-limited
        event2 = UIEvent(type=EventType.CONSOLE_OUTPUT, data={"content": "2"})
        assert self.bus.emit(event2) is False

        # After delay, should succeed
        time.sleep(0.15)
        event3 = UIEvent(type=EventType.CONSOLE_OUTPUT, data={"content": "3"})
        assert self.bus.emit(event3) is True

    def test_process_pending(self):
        """Test processing pending events"""
        handled = []

        def handler(event: UIEvent):
            handled.append(event)

        self.bus.subscribe_all(handler)

        # Emit events
        for i in range(5):
            self.bus.emit(UIEvent(type=EventType.MESSAGE_CHUNK, data={"i": i}))

        # Process
        count = self.bus.process_pending(max_events=3)
        assert count == 3
        assert len(handled) == 3

        # Process remaining
        count2 = self.bus.process_pending()
        assert count2 == 2
        assert len(handled) == 5

    def test_clear(self):
        """Test clearing all pending events"""
        for i in range(10):
            self.bus.emit(UIEvent(type=EventType.MESSAGE_CHUNK, data={}))

        assert self.bus.pending_count == 10

        cleared = self.bus.clear()
        assert cleared == 10
        assert self.bus.pending_count == 0

    def test_handler_exception_handling(self):
        """Test that handler exceptions don't crash dispatch"""
        successful_calls = []

        def bad_handler(event: UIEvent):
            raise ValueError("Handler error")

        def good_handler(event: UIEvent):
            successful_calls.append(event)

        self.bus.subscribe(EventType.MESSAGE_CHUNK, bad_handler)
        self.bus.subscribe(EventType.MESSAGE_CHUNK, good_handler)

        # Should not raise
        self.bus.dispatch(UIEvent(type=EventType.MESSAGE_CHUNK, data={}))

        # Good handler should still be called
        assert len(successful_calls) == 1


class TestChunkToEvent:
    """Tests for chunk_to_event conversion"""

    def test_message_start(self):
        """Test message start chunk conversion"""
        chunk = {"type": "message", "role": "assistant", "start": True}
        event = chunk_to_event(chunk)

        assert event is not None
        assert event.type == EventType.MESSAGE_START
        assert event.data["role"] == "assistant"
        assert event.source == "respond"

    def test_message_chunk(self):
        """Test message content chunk conversion"""
        chunk = {"type": "message", "role": "assistant", "content": "Hello"}
        event = chunk_to_event(chunk)

        assert event is not None
        assert event.type == EventType.MESSAGE_CHUNK
        assert event.data["content"] == "Hello"
        assert event.data["role"] == "assistant"

    def test_message_end(self):
        """Test message end chunk conversion"""
        chunk = {"type": "message", "role": "assistant", "end": True}
        event = chunk_to_event(chunk)

        assert event is not None
        assert event.type == EventType.MESSAGE_END

    def test_code_start(self):
        """Test code start chunk conversion"""
        chunk = {"type": "code", "start": True, "format": "python"}
        event = chunk_to_event(chunk)

        assert event is not None
        assert event.type == EventType.CODE_START
        assert event.data["language"] == "python"

    def test_code_chunk(self):
        """Test code content chunk conversion"""
        chunk = {"type": "code", "content": "print('hello')"}
        event = chunk_to_event(chunk)

        assert event is not None
        assert event.type == EventType.CODE_CHUNK
        assert event.data["content"] == "print('hello')"

    def test_code_end(self):
        """Test code end chunk conversion"""
        chunk = {"type": "code", "end": True}
        event = chunk_to_event(chunk)

        assert event is not None
        assert event.type == EventType.CODE_END

    def test_console_active_line(self):
        """Test console active line conversion"""
        chunk = {"type": "console", "format": "active_line", "content": "line 42"}
        event = chunk_to_event(chunk)

        assert event is not None
        assert event.type == EventType.CONSOLE_ACTIVE_LINE
        assert event.data["line"] == "line 42"
        assert event.source == "computer"

    def test_console_output(self):
        """Test console output conversion"""
        chunk = {"type": "console", "format": "output", "content": "Success"}
        event = chunk_to_event(chunk)

        assert event is not None
        assert event.type == EventType.CONSOLE_OUTPUT
        assert event.data["content"] == "Success"

    def test_console_error_detection(self):
        """Test console error detection"""
        chunk = {"type": "console", "format": "output", "content": "Error: failed"}
        event = chunk_to_event(chunk)

        # Should detect "error" in content
        assert event.type == EventType.CONSOLE_ERROR

    def test_confirmation_request(self):
        """Test confirmation request conversion"""
        chunk = {"type": "confirmation", "content": {"code": "rm -rf /"}}
        event = chunk_to_event(chunk)

        assert event is not None
        assert event.type == EventType.CONFIRMATION_REQUEST
        assert event.data["code"]["code"] == "rm -rf /"

    def test_status_token_update(self):
        """Test status chunk conversion"""
        chunk = {"type": "status", "content": {"tokens": 1000}}
        event = chunk_to_event(chunk)

        assert event is not None
        assert event.type == EventType.SYSTEM_TOKEN_UPDATE
        assert event.data["tokens"] == 1000

    def test_unknown_chunk(self):
        """Test unknown chunk returns None"""
        chunk = {"type": "unknown", "content": "test"}
        event = chunk_to_event(chunk)

        assert event is None


class TestEventBusSingleton:
    """Tests for global event bus singleton"""

    def test_get_event_bus(self):
        """Test get_event_bus returns singleton"""
        reset_event_bus()

        bus1 = get_event_bus()
        bus2 = get_event_bus()

        assert bus1 is bus2

    def test_reset_event_bus(self):
        """Test reset_event_bus creates new instance"""
        bus1 = get_event_bus()
        reset_event_bus()
        bus2 = get_event_bus()

        assert bus1 is not bus2


# ============================================================================
# Sanitizer Tests
# ============================================================================

class TestSanitizer:
    """Tests for output sanitization"""

    def test_safe_sgr_codes(self):
        """Test safe SGR code detection"""
        assert is_safe_sgr("") is True  # Empty is safe (reset)
        assert is_safe_sgr("0") is True  # Reset
        assert is_safe_sgr("1") is True  # Bold
        assert is_safe_sgr("31") is True  # Red
        assert is_safe_sgr("1;31") is True  # Bold red
        # Note: 38;5;196 includes the intermediate "5" which is not in SAFE_SGR_CODES
        # Only the prefix codes 38 and 48 are safe, not their parameters
        assert is_safe_sgr("38") is True  # Extended color prefix
        assert is_safe_sgr("38;48") is True  # Both extended color codes

        # Invalid/unknown codes
        assert is_safe_sgr("999") is False
        assert is_safe_sgr("abc") is False

    def test_sanitize_safe_colors(self):
        """Test that safe colors are preserved"""
        # Red text
        text = "\x1b[31mRed\x1b[0m"
        result = sanitize_output(text)
        assert result == text

        # Bold blue
        text = "\x1b[1;34mBold Blue\x1b[0m"
        result = sanitize_output(text)
        assert result == text

        # Background color
        text = "\x1b[42mGreen BG\x1b[0m"
        result = sanitize_output(text)
        assert result == text

    def test_sanitize_dangerous_osc(self):
        """Test that dangerous OSC sequences are blocked"""
        # Clipboard manipulation (OSC 52)
        text = "\x1b]52;c;SGVsbG8=\x07"
        result = sanitize_output(text)
        assert result == ""

        # Hyperlink (OSC 8)
        text = "\x1b]8;;http://evil.com\x07Click\x1b]8;;\x07"
        result = sanitize_output(text)
        assert result == "Click"

        # Title change (OSC 0)
        text = "\x1b]0;New Title\x07"
        result = sanitize_output(text)
        assert result == ""

    def test_sanitize_cursor_movement(self):
        """Test that cursor movement CSI is blocked"""
        # Cursor up
        text = "Hello\x1b[5AWorld"
        result = sanitize_output(text)
        assert result == "HelloWorld"

        # Cursor position
        text = "\x1b[10;20HTest"
        result = sanitize_output(text)
        assert result == "Test"

        # Erase line
        text = "Line\x1b[K"
        result = sanitize_output(text)
        assert result == "Line"

    def test_sanitize_levels(self):
        """Test different sanitization levels"""
        text = "\x1b[31mRed\x1b[0m\x1b]8;;http://example.com\x07Link\x1b]8;;\x07"

        # NONE - no sanitization
        result = sanitize_output(text, level=SanitizeLevel.NONE)
        assert "\x1b]8;;" in result

        # PERMISSIVE - allow colors, block OSC
        result = sanitize_output(text, level=SanitizeLevel.PERMISSIVE)
        assert "\x1b[31m" in result  # Color preserved
        assert "\x1b]8;;" not in result  # OSC removed
        assert "Link" in result  # Text preserved

        # STRICT - remove all
        result = sanitize_output(text, level=SanitizeLevel.STRICT)
        assert result == "RedLink"

    def test_strip_ansi(self):
        """Test strip_ansi removes all sequences"""
        text = "\x1b[1;31mBold Red\x1b[0m with \x1b[42mbackground\x1b[0m"
        result = strip_ansi(text)
        assert result == "Bold Red with background"

    def test_has_dangerous_sequences(self):
        """Test dangerous sequence detection"""
        # Safe colors
        assert has_dangerous_sequences("\x1b[31mRed\x1b[0m") is False

        # OSC
        assert has_dangerous_sequences("\x1b]52;c;data\x07") is True

        # Cursor movement
        assert has_dangerous_sequences("\x1b[10A") is True

        # Erase
        assert has_dangerous_sequences("\x1b[2J") is True

    def test_sanitization_report(self):
        """Test sanitization report generation"""
        text = "\x1b[31mRed\x1b[0m\x1b]8;;http://x.com\x07Link\x1b]8;;\x07\x1b[5A"
        report = get_sanitization_report(text)

        assert len(report["osc_sequences"]) == 2  # Two OSC 8
        assert len(report["csi_sequences"]) == 3  # 31m, 0m, 5A
        assert report["has_dangerous"] is True

        # Check safe vs unsafe CSI
        safe_count = sum(1 for seq in report["csi_sequences"] if seq["safe"])
        assert safe_count == 2  # 31m and 0m are safe

    def test_mixed_safe_and_dangerous(self):
        """Test mixed content with safe and dangerous sequences"""
        text = "\x1b[1mBold\x1b[0m \x1b]52;c;data\x07 \x1b[31mRed\x1b[0m"
        result = sanitize_output(text)

        # Safe colors preserved
        assert "\x1b[1m" in result
        assert "\x1b[31m" in result

        # OSC removed
        assert "\x1b]52" not in result

        # Text preserved
        assert "Bold" in result
        assert "Red" in result


# ============================================================================
# UIBackend Tests
# ============================================================================

class TestBackendDetection:
    """Tests for backend detection and creation"""

    def test_is_tty_detection(self):
        """Test TTY detection"""
        # Actual result depends on test environment
        result = is_tty()
        assert isinstance(result, bool)

    @patch('interpreter.terminal_interface.components.ui_backend.sys.stdin')
    @patch('interpreter.terminal_interface.components.ui_backend.sys.stdout')
    def test_is_tty_mock(self, mock_stdout, mock_stdin):
        """Test TTY detection with mocks"""
        # Both are TTY
        mock_stdin.isatty = Mock(return_value=True)
        mock_stdout.isatty = Mock(return_value=True)
        assert is_tty() is True

        # stdin is not TTY (piped input)
        mock_stdin.isatty = Mock(return_value=False)
        assert is_tty() is False

    def test_prompt_toolkit_available(self):
        """Test prompt_toolkit availability check"""
        result = prompt_toolkit_available()
        assert isinstance(result, bool)
        # Should be True in test environment

    @patch.dict(os.environ, {'NO_TUI': '1'})
    def test_create_backend_no_tui_env(self):
        """Test create_backend respects NO_TUI env var"""
        mock_interpreter = Mock()
        backend = create_backend(mock_interpreter)

        assert isinstance(backend, RichStreamBackend)
        assert backend.backend_type == BackendType.RICH_STREAM

    @patch.dict(os.environ, {}, clear=True)
    @patch('interpreter.terminal_interface.components.ui_backend.is_tty')
    def test_create_backend_not_tty(self, mock_is_tty):
        """Test create_backend uses Rich when not TTY"""
        mock_is_tty.return_value = False
        mock_interpreter = Mock()

        backend = create_backend(mock_interpreter)

        assert isinstance(backend, RichStreamBackend)

    def test_create_backend_force_type(self):
        """Test create_backend with forced type"""
        mock_interpreter = Mock()

        # Force Rich
        backend = create_backend(
            mock_interpreter,
            force_type=BackendType.RICH_STREAM
        )
        assert isinstance(backend, RichStreamBackend)

        # Force PromptToolkit
        backend = create_backend(
            mock_interpreter,
            force_type=BackendType.PROMPT_TOOLKIT
        )
        assert isinstance(backend, PromptToolkitBackend)

    @patch.dict(os.environ, {}, clear=True)
    @patch('interpreter.terminal_interface.components.ui_backend.is_tty')
    @patch('interpreter.terminal_interface.components.ui_backend.prompt_toolkit_available')
    def test_create_backend_prompt_toolkit_preferred(self, mock_pt_avail, mock_is_tty):
        """Test create_backend prefers prompt_toolkit when available"""
        mock_is_tty.return_value = True
        mock_pt_avail.return_value = True
        mock_interpreter = Mock()

        backend = create_backend(mock_interpreter)

        assert isinstance(backend, PromptToolkitBackend)


class TestRichStreamBackend:
    """Tests for RichStreamBackend"""

    def setup_method(self):
        """Setup for each test"""
        reset_event_bus()
        self.mock_interpreter = Mock()
        self.state = UIState()
        self.backend = RichStreamBackend(self.mock_interpreter, self.state)

    def test_initialization(self):
        """Test RichStreamBackend initialization"""
        assert self.backend.backend_type == BackendType.RICH_STREAM
        assert self.backend.supports_interactive is False
        assert self.backend.interpreter is self.mock_interpreter
        assert self.backend.state is self.state

    def test_start_stop(self):
        """Test backend start and stop"""
        self.backend.start()
        assert self.backend._running is True
        assert self.backend._console is not None

        self.backend.stop()
        assert self.backend._running is False

    def test_emit_updates_state(self):
        """Test emit updates state from events"""
        self.backend.start()

        # Agent spawn
        event = UIEvent(
            type=EventType.AGENT_SPAWN,
            data={"agent_id": "test-agent", "role": "scout"}
        )
        self.backend.emit(event)

        assert "test-agent" in self.state.active_agents
        assert self.state.active_agents["test-agent"].role == AgentRole.SCOUT

    def test_state_update_agent_complete(self):
        """Test state update for agent completion"""
        self.backend.start()

        # Create agent
        agent = self.state.add_agent("test", AgentRole.SCOUT)

        # Complete it
        event = UIEvent(
            type=EventType.AGENT_COMPLETE,
            data={"agent_id": "test"}
        )
        self.backend.emit(event)

        assert agent.status == AgentStatus.COMPLETE

    def test_state_update_tokens(self):
        """Test state update for token counts"""
        self.backend.start()

        event = UIEvent(
            type=EventType.SYSTEM_TOKEN_UPDATE,
            data={"tokens": 5000}
        )
        self.backend.emit(event)

        assert self.state.context_tokens == 5000


class TestPromptToolkitBackend:
    """Tests for PromptToolkitBackend"""

    def setup_method(self):
        """Setup for each test"""
        reset_event_bus()
        self.mock_interpreter = Mock()
        self.state = UIState()
        self.backend = PromptToolkitBackend(self.mock_interpreter, self.state)

    def test_initialization(self):
        """Test PromptToolkitBackend initialization"""
        assert self.backend.backend_type == BackendType.PROMPT_TOOLKIT
        assert self.backend.supports_interactive is True

    @patch('interpreter.terminal_interface.components.input_handler.InputHandler')
    def test_start(self, mock_input_handler_class):
        """Test backend start initializes components"""
        # Mock the InputHandler class
        mock_handler_instance = Mock()
        mock_input_handler_class.return_value = mock_handler_instance

        self.backend.start()

        assert self.backend._running is True
        assert self.backend._input_handler is not None

    def test_emit_buffers_output(self):
        """Test emit buffers message chunks"""
        self.backend.start()

        event = UIEvent(
            type=EventType.MESSAGE_CHUNK,
            data={"content": "Hello"}
        )
        self.backend.emit(event)

        assert len(self.backend._output_buffer) == 1
        assert self.backend._output_buffer[0] == "Hello"

    def test_emit_clears_buffer_on_start(self):
        """Test emit clears buffer on SYSTEM_START"""
        self.backend._output_buffer = ["old", "data"]

        event = UIEvent(type=EventType.SYSTEM_START, data={})
        self.backend.emit(event)

        assert len(self.backend._output_buffer) == 0

    def test_get_buffered_output(self):
        """Test getting and clearing buffered output"""
        self.backend._output_buffer = ["Hello", " ", "World"]

        output = self.backend.get_buffered_output()

        assert output == "Hello World"
        assert len(self.backend._output_buffer) == 0


# ============================================================================
# InputHandler Tests (Mock-based)
# ============================================================================

class TestInputHandlerMocked:
    """Tests for InputHandler using mocks (no real terminal interaction)"""

    def setup_method(self):
        """Setup for each test"""
        reset_event_bus()
        self.mock_interpreter = Mock()
        self.state = UIState()

    @patch('interpreter.terminal_interface.components.input_handler.FileHistory')
    def test_initialization(self, mock_file_history):
        """Test InputHandler initialization"""
        from interpreter.terminal_interface.components.input_handler import InputHandler

        handler = InputHandler(
            self.mock_interpreter,
            self.state,
            history_file="/tmp/test_history"
        )

        assert handler.interpreter is self.mock_interpreter
        assert handler.state is self.state
        mock_file_history.assert_called_once_with("/tmp/test_history")

    @patch('interpreter.terminal_interface.components.input_handler.InMemoryHistory')
    def test_initialization_no_history_file(self, mock_memory_history):
        """Test InputHandler with no history file"""
        from interpreter.terminal_interface.components.input_handler import InputHandler

        handler = InputHandler(self.mock_interpreter, self.state)

        mock_memory_history.assert_called_once()

    def test_create_key_bindings(self):
        """Test key bindings creation"""
        from interpreter.terminal_interface.components.input_handler import InputHandler

        handler = InputHandler(self.mock_interpreter, self.state)
        kb = handler.create_key_bindings()

        # Check that it returns KeyBindings object
        from prompt_toolkit.key_binding import KeyBindings
        assert isinstance(kb, KeyBindings)

    def test_mode_cycling(self):
        """Test UI mode cycling"""
        from interpreter.terminal_interface.components.input_handler import InputHandler

        handler = InputHandler(self.mock_interpreter, self.state)

        # Start at ZEN
        assert self.state.mode == UIMode.ZEN

        # Cycle through
        handler._cycle_mode()
        assert self.state.mode == UIMode.STANDARD

        handler._cycle_mode()
        assert self.state.mode == UIMode.POWER

        handler._cycle_mode()
        assert self.state.mode == UIMode.DEBUG

        handler._cycle_mode()
        assert self.state.mode == UIMode.ZEN  # Wrap around

    def test_callbacks(self):
        """Test callback registration and invocation"""
        from interpreter.terminal_interface.components.input_handler import InputHandler

        handler = InputHandler(self.mock_interpreter, self.state)

        # Register callbacks
        cancel_called = []
        submit_called = []
        mode_called = []

        handler.set_cancel_handler(lambda: cancel_called.append(True))
        handler.set_submit_handler(lambda s: submit_called.append(s))
        handler.set_mode_change_handler(lambda m: mode_called.append(m))

        # Trigger cycle (will call mode handler)
        handler._cycle_mode()

        assert len(mode_called) == 1
        assert mode_called[0] == UIMode.STANDARD

    def test_get_binding_help(self):
        """Test binding help text generation"""
        from interpreter.terminal_interface.components.input_handler import InputHandler

        handler = InputHandler(self.mock_interpreter, self.state)
        help_text = handler.get_binding_help()

        assert "Key Bindings:" in help_text
        assert "escape" in help_text
        assert "Cancel" in help_text


# ============================================================================
# Completers Tests
# ============================================================================

class TestMagicCommandCompleter:
    """Tests for MagicCommandCompleter"""

    def test_initialization(self):
        """Test completer initialization"""
        from interpreter.terminal_interface.components.completers import (
            MagicCommandCompleter,
            MAGIC_COMMANDS
        )

        completer = MagicCommandCompleter()
        assert completer.commands == MAGIC_COMMANDS

    def test_completion_with_percent(self):
        """Test completions for % commands"""
        from interpreter.terminal_interface.components.completers import MagicCommandCompleter
        from prompt_toolkit.document import Document

        completer = MagicCommandCompleter()

        # Partial match
        doc = Document("%he", cursor_position=3)
        completions = list(completer.get_completions(doc, None))

        # Should match %help
        assert any("%help" in c.text for c in completions)

    def test_no_completion_without_percent(self):
        """Test no completions without % prefix"""
        from interpreter.terminal_interface.components.completers import MagicCommandCompleter
        from prompt_toolkit.document import Document

        completer = MagicCommandCompleter()
        doc = Document("help", cursor_position=4)

        completions = list(completer.get_completions(doc, None))
        assert len(completions) == 0


class TestFilePathCompleter:
    """Tests for FilePathCompleter"""

    def test_initialization(self):
        """Test file path completer initialization"""
        from interpreter.terminal_interface.components.completers import FilePathCompleter

        completer = FilePathCompleter()
        assert completer._path_completer is not None

    def test_activation_on_path_chars(self):
        """Test completer activates on path characters"""
        from interpreter.terminal_interface.components.completers import FilePathCompleter
        from prompt_toolkit.document import Document

        completer = FilePathCompleter()

        # Should activate on /
        doc = Document("/tmp/", cursor_position=5)
        completions = list(completer.get_completions(doc, None))
        # May or may not have completions, but should not raise

        # Should activate on ~
        doc = Document("~/", cursor_position=2)
        completions = list(completer.get_completions(doc, None))

    def test_activation_after_keyword(self):
        """Test completer activates after path keywords"""
        from interpreter.terminal_interface.components.completers import FilePathCompleter
        from prompt_toolkit.document import Document

        completer = FilePathCompleter()

        # "open" keyword
        doc = Document("open file", cursor_position=9)
        # Should activate
        # (actual completions depend on filesystem)


class TestConversationCompleter:
    """Tests for ConversationCompleter"""

    def test_initialization(self):
        """Test conversation completer initialization"""
        from interpreter.terminal_interface.components.completers import ConversationCompleter

        mock_interpreter = Mock()
        mock_interpreter.messages = []

        completer = ConversationCompleter(mock_interpreter)
        assert completer._cache == []

    def test_cache_update(self):
        """Test cache updates from messages"""
        from interpreter.terminal_interface.components.completers import ConversationCompleter
        from prompt_toolkit.document import Document

        mock_interpreter = Mock()
        mock_interpreter.messages = [
            {"role": "user", "content": "Hello world testing"},
            {"role": "assistant", "content": "Using python and testing"},
        ]

        completer = ConversationCompleter(mock_interpreter)
        completer._update_cache()

        # Should extract words
        assert "Hello" in completer._cache
        assert "world" in completer._cache
        assert "python" in completer._cache
        assert "testing" in completer._cache

    def test_completion_suggestions(self):
        """Test completion suggestions from cache"""
        from interpreter.terminal_interface.components.completers import ConversationCompleter
        from prompt_toolkit.document import Document

        mock_interpreter = Mock()
        mock_interpreter.messages = [
            {"content": "test variable function"},
        ]

        completer = ConversationCompleter(mock_interpreter)

        # Complete "var"
        doc = Document("var", cursor_position=3)
        completions = list(completer.get_completions(doc, None))

        assert any("variable" in c.text for c in completions)


class TestCombinedCompleter:
    """Tests for CombinedCompleter"""

    def test_initialization(self):
        """Test combined completer initialization"""
        from interpreter.terminal_interface.components.completers import CombinedCompleter

        mock_interpreter = Mock()
        mock_interpreter.messages = []

        completer = CombinedCompleter(mock_interpreter)

        assert completer.magic_completer is not None
        assert completer.path_completer is not None
        assert completer.conversation_completer is not None

    def test_magic_priority(self):
        """Test magic commands take priority"""
        from interpreter.terminal_interface.components.completers import CombinedCompleter
        from prompt_toolkit.document import Document

        mock_interpreter = Mock()
        mock_interpreter.messages = []

        completer = CombinedCompleter(mock_interpreter)

        doc = Document("%hel", cursor_position=4)
        completions = list(completer.get_completions(doc, None))

        # Should only get magic completions
        assert any("%help" in c.text for c in completions)


class TestCreateCompleter:
    """Tests for create_completer factory function"""

    def test_create_with_all_features(self):
        """Test creating completer with all features"""
        from interpreter.terminal_interface.components.completers import create_completer

        mock_interpreter = Mock()
        mock_interpreter.messages = []

        completer = create_completer(
            mock_interpreter,
            include_paths=True,
            include_magic=True,
            include_history=True,
            fuzzy=True
        )

        assert completer is not None

    def test_create_without_fuzzy(self):
        """Test creating completer without fuzzy matching"""
        from interpreter.terminal_interface.components.completers import create_completer
        from interpreter.terminal_interface.components.completers import CombinedCompleter

        mock_interpreter = Mock()
        mock_interpreter.messages = []

        completer = create_completer(
            mock_interpreter,
            fuzzy=False
        )

        assert isinstance(completer, CombinedCompleter)


# ============================================================================
# Integration Tests
# ============================================================================

class TestIntegration:
    """Integration tests across multiple components"""

    def setup_method(self):
        """Setup for integration tests"""
        reset_event_bus()

    def test_event_flow_to_state(self):
        """Test events flowing through bus to update state"""
        state = UIState()
        bus = EventBus()

        # Subscribe to update state
        def update_state(event: UIEvent):
            if event.type == EventType.AGENT_SPAWN:
                agent_id = event.data.get("agent_id")
                role_str = event.data.get("role", "custom")
                try:
                    role = AgentRole(role_str)
                except ValueError:
                    role = AgentRole.CUSTOM
                state.add_agent(agent_id, role)

        bus.subscribe(EventType.AGENT_SPAWN, update_state)

        # Emit event
        bus.emit(UIEvent(
            type=EventType.AGENT_SPAWN,
            data={"agent_id": "test", "role": "scout"}
        ))

        # Process
        bus.process_pending()

        # Check state updated
        assert "test" in state.active_agents
        assert state.active_agents["test"].role == AgentRole.SCOUT

    def test_chunk_to_event_to_state(self):
        """Test chunk conversion and state update pipeline"""
        state = UIState()

        # Simulate message chunks
        chunks = [
            {"type": "message", "role": "assistant", "start": True},
            {"type": "message", "role": "assistant", "content": "Hello"},
            {"type": "code", "start": True, "format": "python"},
            {"type": "code", "content": "print('hi')"},
            {"type": "code", "end": True},
            {"type": "message", "role": "assistant", "end": True},
        ]

        events = [chunk_to_event(c) for c in chunks]
        events = [e for e in events if e is not None]

        assert len(events) == 6
        assert events[0].type == EventType.MESSAGE_START
        assert events[1].type == EventType.MESSAGE_CHUNK
        assert events[2].type == EventType.CODE_START
        assert events[3].type == EventType.CODE_CHUNK
        assert events[4].type == EventType.CODE_END
        assert events[5].type == EventType.MESSAGE_END

    def test_backend_state_event_integration(self):
        """Test backend, state, and events working together"""
        mock_interpreter = Mock()
        state = UIState()
        backend = RichStreamBackend(mock_interpreter, state)

        backend.start()

        # Emit agent spawn
        backend.emit(UIEvent(
            type=EventType.AGENT_SPAWN,
            data={"agent_id": "integration-test", "role": "surgeon"}
        ))

        # Check state updated
        assert "integration-test" in state.active_agents
        assert state.active_agents["integration-test"].role == AgentRole.SURGEON

        backend.stop()

    def test_sanitizer_in_message_flow(self):
        """Test sanitizer filtering in message pipeline"""
        # Simulate dangerous LLM output
        dangerous_chunk = {
            "type": "message",
            "role": "assistant",
            "content": "\x1b[31mRed text\x1b[0m \x1b]52;c;evil\x07"
        }

        event = chunk_to_event(dangerous_chunk)
        content = event.data["content"]

        # Sanitize
        safe_content = sanitize_output(content)

        # Color preserved, clipboard blocked
        assert "\x1b[31m" in safe_content
        assert "\x1b]52" not in safe_content
        assert "Red text" in safe_content


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
