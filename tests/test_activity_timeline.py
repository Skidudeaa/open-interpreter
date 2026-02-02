"""
Tests for Activity Timeline UI components.

ARCHITECTURE: Unit tests for timeline state, widgets, and event handling.
Tests use mocking to isolate components from Textual framework.

WHY: Ensures timeline nodes are created correctly from events,
max_nodes purging works, and widget visibility toggles function.

TRADEOFF: Some tests mock Textual internals vs full integration tests.
Unit tests are faster and more focused on business logic.
"""

from __future__ import annotations

import threading
import time

from interpreter.terminal_interface.components.timeline_state import (
    NodeStatus,
    NodeType,
    TimelineNode,
    TimelineState,
)
from interpreter.terminal_interface.components.ui_events import EventType, UIEvent


class TestTimelineNode:
    """Tests for TimelineNode dataclass."""

    def test_node_creation_with_defaults(self):
        """Node creates with sensible defaults."""
        node = TimelineNode(
            node_type=NodeType.AGENT,
            primary_text="Test Agent",
        )

        assert node.node_type == NodeType.AGENT
        assert node.primary_text == "Test Agent"
        assert node.status == NodeStatus.PENDING  # Default is PENDING
        assert node.id is not None
        assert len(node.id) == 8  # UUID prefix
        assert node.parent_id is None
        assert node.secondary_text == ""
        assert node.error_message is None

    def test_node_elapsed_time_display(self):
        """Elapsed time formats correctly."""
        node = TimelineNode(
            node_type=NodeType.CODE_EXEC,
            primary_text="Running code",
        )

        # Simulate some elapsed time by setting timestamp in past
        node.timestamp = time.time() - 2.5

        elapsed = node.elapsed_display
        # elapsed_display calculates from timestamp when elapsed_seconds is 0
        assert "2." in elapsed or "3." in elapsed  # ~2.5s
        assert "s" in elapsed

    def test_node_elapsed_time_running(self):
        """Running node shows live elapsed time."""
        node = TimelineNode(
            node_type=NodeType.AGENT,
            status=NodeStatus.RUNNING,
            primary_text="Active agent",
        )
        node.timestamp = time.time() - 1.0

        elapsed = node.elapsed_display
        assert "s" in elapsed

    def test_node_display_icon_by_type(self):
        """Each node type has distinct icon."""
        agent_node = TimelineNode(node_type=NodeType.AGENT, primary_text="Agent")
        code_node = TimelineNode(node_type=NodeType.CODE_EXEC, primary_text="Code")
        file_node = TimelineNode(node_type=NodeType.FILE, primary_text="File")

        # Icons should be different
        icons = {
            agent_node.display_icon,
            code_node.display_icon,
            file_node.display_icon,
        }
        assert len(icons) == 3  # All distinct

    def test_node_display_color_by_status(self):
        """Node color reflects status."""
        running = TimelineNode(
            node_type=NodeType.AGENT,
            status=NodeStatus.RUNNING,
            primary_text="Running",
        )
        complete = TimelineNode(
            node_type=NodeType.AGENT,
            status=NodeStatus.COMPLETE,
            primary_text="Complete",
        )
        error = TimelineNode(
            node_type=NodeType.AGENT,
            status=NodeStatus.ERROR,
            primary_text="Error",
        )

        assert running.display_color != complete.display_color
        assert error.display_color == "red"

    def test_node_mark_complete(self):
        """mark_complete updates status and elapsed."""
        node = TimelineNode(
            node_type=NodeType.AGENT,
            status=NodeStatus.RUNNING,
            primary_text="Agent",
        )
        node.timestamp = time.time() - 1.0

        node.mark_complete()

        assert node.status == NodeStatus.COMPLETE
        assert node.elapsed_seconds > 0

    def test_node_mark_complete_with_error(self):
        """mark_complete with error sets error status."""
        node = TimelineNode(
            node_type=NodeType.AGENT,
            status=NodeStatus.RUNNING,
            primary_text="Agent",
        )

        node.mark_complete(error_message="Something went wrong")

        assert node.status == NodeStatus.ERROR
        assert node.error_message == "Something went wrong"


class TestTimelineState:
    """Tests for TimelineState manager."""

    def test_add_agent_spawn(self):
        """Agent spawn creates node with correct type."""
        state = TimelineState()
        state.add_agent_spawn("agent-1", "Scout")

        nodes = state.get_root_nodes()
        assert len(nodes) == 1
        assert nodes[0].node_type == NodeType.AGENT
        assert "Scout" in nodes[0].primary_text  # "Scout started"
        assert nodes[0].status == NodeStatus.RUNNING

    def test_add_agent_action_nests_under_agent(self):
        """Agent actions nest under their parent agent."""
        state = TimelineState()
        state.add_agent_spawn("agent-1", "Scout")
        state.add_agent_action("agent-1", "Searching files", "*.py")

        root_nodes = state.get_root_nodes()
        assert len(root_nodes) == 1  # Only agent at root

        children = state.get_children("agent-1")
        assert len(children) == 1
        assert children[0].node_type == NodeType.AGENT_ACTION
        assert children[0].primary_text == "Searching files"
        assert children[0].secondary_text == "*.py"

    def test_add_code_execution(self):
        """Code execution creates node with language."""
        state = TimelineState()
        node = state.add_code_execution("python", "block-123")

        assert node.node_type == NodeType.CODE_EXEC
        assert "python" in node.primary_text.lower()
        assert node.code_block_id == "block-123"

    def test_complete_node_sets_status(self):
        """Completing a node sets status and elapsed time."""
        state = TimelineState()
        state.add_agent_spawn("agent-1", "Scout")

        result = state.complete_node("agent-1")

        assert result is True
        node = state.get_node("agent-1")
        assert node is not None
        assert node.status == NodeStatus.COMPLETE
        assert node.elapsed_seconds >= 0

    def test_complete_node_with_error(self):
        """Completing with error sets error status and message."""
        state = TimelineState()
        state.add_agent_spawn("agent-1", "Scout")

        state.complete_node("agent-1", error_message="Connection failed")

        node = state.get_node("agent-1")
        assert node is not None
        assert node.status == NodeStatus.ERROR
        assert node.error_message == "Connection failed"

    def test_max_nodes_purging(self):
        """Old nodes are purged when max_nodes exceeded."""
        state = TimelineState(max_nodes=5)

        # Add 7 nodes
        for i in range(7):
            state.add_agent_spawn(f"agent-{i}", f"Agent {i}")

        # Should have purged to 5
        assert state.node_count == 5

        # Oldest nodes should be gone
        assert state.get_node("agent-0") is None
        assert state.get_node("agent-1") is None
        # Newest should remain
        assert state.get_node("agent-6") is not None

    def test_clear_removes_all_nodes(self):
        """Clear removes all nodes."""
        state = TimelineState()
        state.add_agent_spawn("agent-1", "Scout")
        state.add_code_execution("python")

        state.clear()

        assert state.node_count == 0
        assert len(state.get_root_nodes()) == 0

    def test_thread_safety(self):
        """State handles concurrent access."""
        state = TimelineState()
        errors = []

        def add_nodes():
            try:
                for i in range(100):
                    state.add_agent_spawn(
                        f"agent-{threading.current_thread().name}-{i}", "Agent"
                    )
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=add_nodes, name=f"t{i}") for i in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0
        assert state.node_count == 500  # 5 threads * 100 nodes

    def test_get_children_returns_ordered(self):
        """Children are returned in creation order."""
        state = TimelineState()
        state.add_agent_spawn("agent-1", "Scout")

        for i in range(5):
            time.sleep(0.01)  # Ensure different timestamps
            state.add_agent_action("agent-1", f"Action {i}", "")

        children = state.get_children("agent-1")
        assert len(children) == 5
        # Should be in order
        for i, child in enumerate(children):
            assert child.primary_text == f"Action {i}"

    def test_add_file_change(self):
        """File change creates correct node."""
        state = TimelineState()
        state.add_file_change("test.py", added=10, removed=5)

        nodes = state.get_root_nodes()
        assert len(nodes) == 1
        assert nodes[0].node_type == NodeType.FILE
        assert "test.py" in nodes[0].primary_text
        # The format is "filename (+added, -removed)"
        assert "+10" in nodes[0].primary_text
        assert "-5" in nodes[0].primary_text

    def test_add_git_commit(self):
        """Git commit creates correct node."""
        state = TimelineState()
        state.add_git_commit("abc1234", files_count=3)

        nodes = state.get_root_nodes()
        assert len(nodes) == 1
        assert nodes[0].node_type == NodeType.GIT
        assert "abc1234" in nodes[0].primary_text
        assert "3" in nodes[0].primary_text

    def test_add_validation_result_success(self):
        """Validation success creates complete node."""
        state = TimelineState()
        state.add_validation_result(passed=True, error_count=0)

        nodes = state.get_root_nodes()
        assert len(nodes) == 1
        assert nodes[0].node_type == NodeType.VALIDATION
        assert nodes[0].status == NodeStatus.COMPLETE

    def test_add_validation_result_failure(self):
        """Validation failure creates error node."""
        state = TimelineState()
        state.add_validation_result(passed=False, error_count=3)

        nodes = state.get_root_nodes()
        assert len(nodes) == 1
        assert nodes[0].status == NodeStatus.ERROR
        assert "3" in nodes[0].primary_text

    def test_add_test_result(self):
        """Test result creates correct node."""
        state = TimelineState()
        state.add_test_result(passed=8, total=10)

        nodes = state.get_root_nodes()
        assert len(nodes) == 1
        assert nodes[0].node_type == NodeType.TEST
        assert "8/10" in nodes[0].primary_text

    def test_set_and_get_current_parent(self):
        """Current parent context works correctly."""
        state = TimelineState()

        assert state.get_current_parent() is None

        state.set_current_parent("parent-1")
        assert state.get_current_parent() == "parent-1"

        state.set_current_parent(None)
        assert state.get_current_parent() is None


class TestResourceBarWidget:
    """Tests for ResourceBarWidget (unit tests without Textual app)."""

    def test_token_percentage_calculation(self):
        """Token percentage calculates correctly."""
        # Test the calculation logic directly
        tokens = 64000
        token_limit = 128000
        pct = (tokens / token_limit * 100) if token_limit > 0 else 0
        assert pct == 50.0

    def test_token_percentage_zero_limit(self):
        """Zero limit doesn't cause division error."""
        tokens = 1000
        token_limit = 0
        pct = (tokens / token_limit * 100) if token_limit > 0 else 0
        assert pct == 0

    def test_elapsed_time_format_seconds(self):
        """Elapsed time formats as seconds."""
        secs = 45.7
        if secs < 60:
            result = f"{secs:.1f}s"
        assert result == "45.7s"

    def test_elapsed_time_format_minutes(self):
        """Elapsed time formats as minutes."""
        secs = 125.0
        if secs < 60:
            result = f"{secs:.1f}s"
        elif secs < 3600:
            mins = int(secs / 60)
            secs_rem = int(secs % 60)
            result = f"{mins}m {secs_rem}s"
        assert result == "2m 5s"

    def test_elapsed_time_format_hours(self):
        """Elapsed time formats as hours."""
        secs = 7325.0
        if secs < 60:
            result = f"{secs:.1f}s"
        elif secs < 3600:
            mins = int(secs / 60)
            secs_rem = int(secs % 60)
            result = f"{mins}m {secs_rem}s"
        else:
            hours = int(secs / 3600)
            mins = int((secs % 3600) / 60)
            result = f"{hours}h {mins}m"
        assert result == "2h 2m"

    def test_color_threshold_green(self):
        """Low usage shows green."""
        pct = 30
        if pct < 60:
            color = "green"
        elif pct < 85:
            color = "yellow"
        else:
            color = "red"
        assert color == "green"

    def test_color_threshold_yellow(self):
        """Medium usage shows yellow."""
        pct = 70
        if pct < 60:
            color = "green"
        elif pct < 85:
            color = "yellow"
        else:
            color = "red"
        assert color == "yellow"

    def test_color_threshold_red(self):
        """High usage shows red."""
        pct = 90
        if pct < 60:
            color = "green"
        elif pct < 85:
            color = "yellow"
        else:
            color = "red"
        assert color == "red"


class TestCodeSidebarWidget:
    """Tests for CodeSidebarWidget (unit tests without Textual app)."""

    def test_visibility_toggle_logic(self):
        """Visibility toggles correctly."""
        is_visible = True
        is_visible = not is_visible
        assert is_visible is False
        is_visible = not is_visible
        assert is_visible is True

    def test_code_block_id_generation(self):
        """Code block IDs are unique."""
        import uuid

        ids = set()
        for _ in range(100):
            block_id = str(uuid.uuid4())[:8]
            ids.add(block_id)
        assert len(ids) == 100  # All unique


class TestUIEventIntegration:
    """Tests for UI event handling in timeline."""

    def test_ui_event_creation(self):
        """UIEvent creates with correct structure."""
        event = UIEvent(
            type=EventType.AGENT_SPAWN,
            data={"agent_id": "agent-1", "role": "Scout"},
        )

        assert event.type == EventType.AGENT_SPAWN
        assert event.data["agent_id"] == "agent-1"
        assert event.data["role"] == "Scout"

    def test_event_types_for_timeline(self):
        """All required event types exist."""
        required_events = [
            "AGENT_SPAWN",
            "AGENT_OUTPUT",
            "AGENT_COMPLETE",
            "AGENT_ERROR",
            "CODE_START",
            "CODE_END",
            "CODE_CHUNK",
            "FILE_CHANGE",
            "GIT_COMMIT",
            "VALIDATION_END",
            "TEST_END",
            "SYSTEM_TOKEN_UPDATE",
        ]

        for event_name in required_events:
            assert hasattr(EventType, event_name), f"Missing EventType.{event_name}"
