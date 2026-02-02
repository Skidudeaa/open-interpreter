"""
Timeline State - Data model for activity timeline nodes.

ARCHITECTURE: Immutable node dataclass with thread-safe state container.
Events create new nodes; running nodes update in place for elapsed time.

WHY: Separates data model from widget rendering. TimelineState can be
shared across UI backends (Textual, Rich) and tested independently.

TRADEOFF: Full rebuild on hierarchy changes vs. incremental - simpler
and works well for <500 nodes with max_nodes purging.
"""

from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass


class NodeStatus(Enum):
    """Status of a timeline node."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETE = "complete"
    ERROR = "error"
    CANCELLED = "cancelled"


class NodeType(Enum):
    """Types of timeline nodes."""

    # Agent lifecycle
    AGENT = "agent"  # Agent spawn/complete
    AGENT_ACTION = "agent_action"  # Nested agent output

    # Code execution
    CODE_EXEC = "code_exec"  # Running code
    OUTPUT = "output"  # Console output preview

    # File operations
    FILE = "file"  # File change
    GIT = "git"  # Git commit

    # Validation & testing
    VALIDATION = "validation"
    TEST = "test"

    # System
    MESSAGE = "message"  # LLM message
    SYSTEM = "system"  # System event


# Icons for each node type
NODE_ICONS = {
    NodeType.AGENT: "○",
    NodeType.AGENT_ACTION: "├─",
    NodeType.CODE_EXEC: "▶",
    NodeType.OUTPUT: "│",
    NodeType.FILE: "💾",
    NodeType.GIT: "📦",
    NodeType.VALIDATION: "✓",
    NodeType.TEST: "🧪",
    NodeType.MESSAGE: "💬",
    NodeType.SYSTEM: "⚙️",
}

# Status icons (override node icon when status changes)
STATUS_ICONS = {
    NodeStatus.PENDING: "○",
    NodeStatus.RUNNING: "⏳",
    NodeStatus.COMPLETE: "✓",
    NodeStatus.ERROR: "✗",
    NodeStatus.CANCELLED: "⊘",
}

# Colors by status
STATUS_COLORS = {
    NodeStatus.PENDING: "yellow",
    NodeStatus.RUNNING: "cyan",
    NodeStatus.COMPLETE: "green",
    NodeStatus.ERROR: "red",
    NodeStatus.CANCELLED: "dim",
}

# Colors by node type (used when status is PENDING/RUNNING)
NODE_COLORS = {
    NodeType.AGENT: "cyan",
    NodeType.AGENT_ACTION: "dim",
    NodeType.CODE_EXEC: "bright_green",
    NodeType.OUTPUT: "dim",
    NodeType.FILE: "yellow",
    NodeType.GIT: "magenta",
    NodeType.VALIDATION: "bright_cyan",
    NodeType.TEST: "blue",
    NodeType.MESSAGE: "white",
    NodeType.SYSTEM: "dim",
}


@dataclass
class TimelineNode:
    """
    A single node in the activity timeline.

    Nodes form a tree structure via parent_id for hierarchical display.
    Agent actions nest under agent spawn nodes.

    Attributes:
        id: Unique node identifier
        timestamp: When the node was created (epoch seconds)
        elapsed_seconds: Time since creation (updated for running nodes)
        status: Current node status
        node_type: Type of activity
        icon: Display icon (can override based on status)
        primary_text: Main display text
        secondary_text: Additional context
        parent_id: ID of parent node (for nesting)
        code_block_id: Link to code sidebar block (if applicable)
        error_message: Error details (if status is ERROR)
        is_expanded: Whether children are expanded in tree view
    """

    id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    timestamp: float = field(default_factory=time.time)
    elapsed_seconds: float = 0.0
    status: NodeStatus = NodeStatus.PENDING
    node_type: NodeType = NodeType.SYSTEM
    icon: str = ""
    primary_text: str = ""
    secondary_text: str = ""
    parent_id: str | None = None
    code_block_id: str | None = None
    error_message: str | None = None
    is_expanded: bool = True

    def __post_init__(self):
        # Set default icon from node type if not provided
        if not self.icon:
            self.icon = NODE_ICONS.get(self.node_type, "•")

    @property
    def elapsed_display(self) -> str:
        """Human-readable elapsed time."""
        secs = self.elapsed_seconds or (time.time() - self.timestamp)
        if secs < 0.1:
            return "0.0s"
        elif secs < 60:
            return f"{secs:.1f}s"
        elif secs < 3600:
            mins = int(secs / 60)
            return f"{mins}m"
        else:
            hours = int(secs / 3600)
            mins = int((secs % 3600) / 60)
            return f"{hours}h {mins}m"

    @property
    def display_icon(self) -> str:
        """Get icon based on current status."""
        if self.status in (NodeStatus.COMPLETE, NodeStatus.ERROR, NodeStatus.CANCELLED):
            return STATUS_ICONS.get(self.status, self.icon)
        return self.icon

    @property
    def display_color(self) -> str:
        """Get color based on current status and type."""
        if self.status in (NodeStatus.COMPLETE, NodeStatus.ERROR, NodeStatus.CANCELLED):
            return STATUS_COLORS.get(self.status, "white")
        return NODE_COLORS.get(self.node_type, "white")

    def update_elapsed(self) -> None:
        """Update elapsed_seconds from timestamp."""
        self.elapsed_seconds = time.time() - self.timestamp

    def mark_complete(self, error_message: str | None = None) -> None:
        """Mark node as complete or error."""
        self.update_elapsed()
        if error_message:
            self.status = NodeStatus.ERROR
            self.error_message = error_message
        else:
            self.status = NodeStatus.COMPLETE


class TimelineState:
    """
    Thread-safe container for timeline nodes.

    Maintains a list of nodes with automatic purging when max_nodes is exceeded.
    Provides lookup by ID and parent for tree building.

    Thread Safety:
    - All mutations use the internal lock
    - get_* methods return copies to prevent external mutation
    """

    def __init__(self, max_nodes: int = 500):
        self._nodes: list[TimelineNode] = []
        self._nodes_by_id: dict[str, TimelineNode] = {}
        self._lock = threading.Lock()
        self.max_nodes = max_nodes

        # Track the "current" context for nesting (e.g., current agent)
        self._current_parent_id: str | None = None

    def add_node(self, node: TimelineNode) -> TimelineNode:
        """
        Add a new node to the timeline.

        If max_nodes is exceeded, removes the oldest nodes.
        Returns the added node.
        """
        with self._lock:
            self._nodes.append(node)
            self._nodes_by_id[node.id] = node

            # Purge oldest if over limit
            while len(self._nodes) > self.max_nodes:
                oldest = self._nodes.pop(0)
                del self._nodes_by_id[oldest.id]

            return node

    def get_node(self, node_id: str) -> TimelineNode | None:
        """Get a node by ID."""
        with self._lock:
            return self._nodes_by_id.get(node_id)

    def update_node(
        self,
        node_id: str,
        status: NodeStatus | None = None,
        primary_text: str | None = None,
        secondary_text: str | None = None,
        error_message: str | None = None,
    ) -> bool:
        """
        Update an existing node's properties.

        Returns True if node was found and updated.
        """
        with self._lock:
            node = self._nodes_by_id.get(node_id)
            if not node:
                return False

            if status is not None:
                node.status = status
            if primary_text is not None:
                node.primary_text = primary_text
            if secondary_text is not None:
                node.secondary_text = secondary_text
            if error_message is not None:
                node.error_message = error_message

            # Update elapsed time
            node.update_elapsed()

            return True

    def complete_node(self, node_id: str, error_message: str | None = None) -> bool:
        """Mark a node as complete or error."""
        with self._lock:
            node = self._nodes_by_id.get(node_id)
            if not node:
                return False
            node.mark_complete(error_message)
            return True

    def get_root_nodes(self) -> list[TimelineNode]:
        """Get all nodes without a parent (root level)."""
        with self._lock:
            return [n for n in self._nodes if n.parent_id is None]

    def get_children(self, parent_id: str) -> list[TimelineNode]:
        """Get all nodes with the given parent."""
        with self._lock:
            return [n for n in self._nodes if n.parent_id == parent_id]

    def get_all_nodes(self) -> list[TimelineNode]:
        """Get a copy of all nodes."""
        with self._lock:
            return list(self._nodes)

    @property
    def node_count(self) -> int:
        """Current number of nodes."""
        with self._lock:
            return len(self._nodes)

    def clear(self) -> None:
        """Remove all nodes."""
        with self._lock:
            self._nodes.clear()
            self._nodes_by_id.clear()
            self._current_parent_id = None

    # Context management for nesting

    def set_current_parent(self, parent_id: str | None) -> None:
        """Set the current parent for auto-nesting new nodes."""
        with self._lock:
            self._current_parent_id = parent_id

    def get_current_parent(self) -> str | None:
        """Get the current parent ID for nesting."""
        with self._lock:
            return self._current_parent_id

    # Convenience methods for common node types

    def add_agent_spawn(self, agent_id: str, role: str) -> TimelineNode:
        """Add an agent spawn node."""
        node = TimelineNode(
            id=agent_id,
            node_type=NodeType.AGENT,
            status=NodeStatus.RUNNING,
            icon="○",
            primary_text=f"{role.title()} started",
        )
        self.add_node(node)
        self.set_current_parent(agent_id)
        return node

    def add_agent_action(
        self, agent_id: str, message: str, context: str = ""
    ) -> TimelineNode:
        """Add an agent action (nested under agent)."""
        secondary = context[:40] + "..." if len(context) > 40 else context
        node = TimelineNode(
            node_type=NodeType.AGENT_ACTION,
            status=NodeStatus.RUNNING,
            icon="├─",
            primary_text=message,
            secondary_text=secondary,
            parent_id=agent_id,
        )
        return self.add_node(node)

    def add_code_execution(
        self, language: str, code_block_id: str | None = None
    ) -> TimelineNode:
        """Add a code execution node."""
        node = TimelineNode(
            node_type=NodeType.CODE_EXEC,
            status=NodeStatus.RUNNING,
            icon="▶",
            primary_text=f"Running {language}",
            code_block_id=code_block_id,
            parent_id=self.get_current_parent(),
        )
        return self.add_node(node)

    def add_file_change(
        self, filename: str, added: int = 0, removed: int = 0
    ) -> TimelineNode:
        """Add a file change node."""
        diff_text = ""
        if added or removed:
            diff_text = f" (+{added}, -{removed})"
        node = TimelineNode(
            node_type=NodeType.FILE,
            status=NodeStatus.COMPLETE,
            icon="💾",
            primary_text=f"{filename}{diff_text}",
            parent_id=self.get_current_parent(),
        )
        return self.add_node(node)

    def add_validation_result(self, passed: bool, error_count: int = 0) -> TimelineNode:
        """Add a validation result node."""
        if passed:
            node = TimelineNode(
                node_type=NodeType.VALIDATION,
                status=NodeStatus.COMPLETE,
                icon="✓",
                primary_text="Validation passed",
                parent_id=self.get_current_parent(),
            )
        else:
            node = TimelineNode(
                node_type=NodeType.VALIDATION,
                status=NodeStatus.ERROR,
                icon="✗",
                primary_text=f"{error_count} validation error{'s' if error_count != 1 else ''}",
                parent_id=self.get_current_parent(),
            )
        return self.add_node(node)

    def add_test_result(self, passed: int, total: int) -> TimelineNode:
        """Add a test result node."""
        all_passed = passed == total
        node = TimelineNode(
            node_type=NodeType.TEST,
            status=NodeStatus.COMPLETE if all_passed else NodeStatus.ERROR,
            icon="✓" if all_passed else "✗",
            primary_text=f"Tests: {passed}/{total} passed",
            parent_id=self.get_current_parent(),
        )
        return self.add_node(node)

    def add_git_commit(self, commit_hash: str, files_count: int = 0) -> TimelineNode:
        """Add a git commit node."""
        text = f"Committed {commit_hash[:7]}"
        if files_count:
            text += f" ({files_count} file{'s' if files_count != 1 else ''})"
        node = TimelineNode(
            node_type=NodeType.GIT,
            status=NodeStatus.COMPLETE,
            icon="📦",
            primary_text=text,
            parent_id=self.get_current_parent(),
        )
        return self.add_node(node)
