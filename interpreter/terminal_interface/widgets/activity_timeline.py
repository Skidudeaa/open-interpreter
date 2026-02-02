"""
ActivityTimeline Widget - Hierarchical activity visualization.

ARCHITECTURE: Textual Tree widget showing execution events as connected nodes.
Events flow from EventBus -> TimelineState -> Tree rebuild.

WHY: Timeline shows relationships between activities (agent spawns sub-tasks,
code execution triggers validation). Tree structure enables expand/collapse
and keyboard navigation built into Textual.

TRADEOFF: Full rebuild on updates vs. incremental - simpler implementation,
works well for <500 nodes with max_nodes purging in TimelineState.

Part of Activity Timeline UI feature.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from rich.text import Text
from textual.reactive import reactive
from textual.widgets import Tree
from textual.widgets.tree import TreeNode

from ..components.timeline_state import (
    NodeStatus,
    NodeType,
    TimelineNode,
    TimelineState,
)
from ..components.ui_events import EventType, UIEvent, get_event_bus

if TYPE_CHECKING:
    from ..components.ui_state import UIState


class ActivityTimelineWidget(Tree[str]):
    """
    Hierarchical activity timeline with real-time event updates.

    Features:
    - Auto-rebuilds on agent/code/file events
    - Shows parent→child relationships (agent actions under agent spawn)
    - Displays elapsed time on right side
    - Keyboard navigation: ↑↓ select, ←→ expand/collapse
    - Auto-scroll with pause on user interaction

    Events handled:
    - AGENT_SPAWN, AGENT_OUTPUT, AGENT_COMPLETE, AGENT_ERROR
    - CODE_START, CODE_END
    - FILE_CHANGE, GIT_COMMIT
    - VALIDATION_END, TEST_END
    - CONSOLE_OUTPUT (summarized)
    """

    DEFAULT_CSS = """
    ActivityTimelineWidget {
        width: 1fr;
        height: 100%;
        background: $surface;
        padding: 0 1;
        scrollbar-gutter: stable;
    }

    ActivityTimelineWidget:focus {
        border: solid $primary;
    }

    ActivityTimelineWidget > .tree--guides {
        color: $secondary;
    }

    ActivityTimelineWidget > .tree--cursor {
        background: $primary 20%;
    }

    ActivityTimelineWidget > .tree--highlight {
        background: $primary 10%;
    }

    ActivityTimelineWidget.-hidden {
        display: none;
    }
    """

    # Reactive state
    auto_scroll: reactive[bool] = reactive(True)
    selected_node_id: reactive[str | None] = reactive(None)

    def __init__(
        self,
        ui_state: UIState | None = None,
        *,
        name: str | None = None,
        id: str | None = None,
        classes: str | None = None,
    ):
        super().__init__(
            "⏱ Activity",
            data="root",
            name=name,
            id=id,
            classes=classes,
        )
        self.ui_state = ui_state
        self._event_bus = get_event_bus()
        self._state = TimelineState(max_nodes=500)

        # Map node_id -> TreeNode for quick updates
        self._timeline_nodes: dict[str, TreeNode[str]] = {}

        # Track current code execution for linking
        self._current_code_node_id: str | None = None

        # Track last rebuild time for throttling
        self._last_rebuild_time: float = 0
        self._rebuild_interval: float = 0.1  # 100ms minimum between rebuilds

    def on_mount(self) -> None:
        """Subscribe to events when widget is mounted."""
        # Agent events
        self._event_bus.subscribe(EventType.AGENT_SPAWN, self._on_agent_spawn)
        self._event_bus.subscribe(EventType.AGENT_OUTPUT, self._on_agent_output)
        self._event_bus.subscribe(EventType.AGENT_COMPLETE, self._on_agent_complete)
        self._event_bus.subscribe(EventType.AGENT_ERROR, self._on_agent_error)

        # Code events
        self._event_bus.subscribe(EventType.CODE_START, self._on_code_start)
        self._event_bus.subscribe(EventType.CODE_END, self._on_code_end)

        # File events
        self._event_bus.subscribe(EventType.FILE_CHANGE, self._on_file_change)
        self._event_bus.subscribe(EventType.GIT_COMMIT, self._on_git_commit)

        # Validation/test events
        self._event_bus.subscribe(EventType.VALIDATION_END, self._on_validation_end)
        self._event_bus.subscribe(EventType.TEST_END, self._on_test_end)

        # Activity events (for LLM thinking, searching, etc.)
        self._event_bus.subscribe(EventType.ACTIVITY, self._on_activity)

        # Build initial tree
        self._rebuild_tree()

    def on_unmount(self) -> None:
        """Unsubscribe from events."""
        self._event_bus.unsubscribe(EventType.AGENT_SPAWN, self._on_agent_spawn)
        self._event_bus.unsubscribe(EventType.AGENT_OUTPUT, self._on_agent_output)
        self._event_bus.unsubscribe(EventType.AGENT_COMPLETE, self._on_agent_complete)
        self._event_bus.unsubscribe(EventType.AGENT_ERROR, self._on_agent_error)
        self._event_bus.unsubscribe(EventType.CODE_START, self._on_code_start)
        self._event_bus.unsubscribe(EventType.CODE_END, self._on_code_end)
        self._event_bus.unsubscribe(EventType.FILE_CHANGE, self._on_file_change)
        self._event_bus.unsubscribe(EventType.GIT_COMMIT, self._on_git_commit)
        self._event_bus.unsubscribe(EventType.VALIDATION_END, self._on_validation_end)
        self._event_bus.unsubscribe(EventType.TEST_END, self._on_test_end)
        self._event_bus.unsubscribe(EventType.ACTIVITY, self._on_activity)

    # Event handlers

    def _on_agent_spawn(self, event: UIEvent) -> None:
        """Handle new agent spawn."""
        agent_id = event.data.get("agent_id", "")
        role = event.data.get("role", "agent")
        if agent_id:
            self._state.add_agent_spawn(agent_id, role)
            self._schedule_rebuild()

    def _on_agent_output(self, event: UIEvent) -> None:
        """Handle agent output (add as nested action)."""
        agent_id = event.data.get("agent_id", "")
        message = event.data.get("message", "")
        context = event.data.get("context", "")
        if agent_id and message:
            self._state.add_agent_action(agent_id, message, context)
            self._schedule_rebuild()

    def _on_agent_complete(self, event: UIEvent) -> None:
        """Handle agent completion."""
        agent_id = event.data.get("agent_id", "")
        if agent_id:
            self._state.complete_node(agent_id)
            # Clear parent context if this was the current parent
            if self._state.get_current_parent() == agent_id:
                self._state.set_current_parent(None)
            self._schedule_rebuild()

    def _on_agent_error(self, event: UIEvent) -> None:
        """Handle agent error."""
        agent_id = event.data.get("agent_id", "")
        error = event.data.get("error", "Unknown error")
        if agent_id:
            self._state.complete_node(agent_id, error_message=str(error))
            self._schedule_rebuild()

    def _on_code_start(self, event: UIEvent) -> None:
        """Handle code execution start."""
        language = event.data.get("language", "python")
        code_block_id = event.data.get("code_block_id")
        node = self._state.add_code_execution(language, code_block_id)
        self._current_code_node_id = node.id
        self._schedule_rebuild()

    def _on_code_end(self, event: UIEvent) -> None:
        """Handle code execution end."""
        if self._current_code_node_id:
            error = event.data.get("error")
            self._state.complete_node(
                self._current_code_node_id,
                error_message=str(error) if error else None,
            )
            self._current_code_node_id = None
            self._schedule_rebuild()

    def _on_file_change(self, event: UIEvent) -> None:
        """Handle file change."""
        filename = event.data.get("filename", event.data.get("file_path", ""))
        if filename:
            # Extract just the filename from path
            if "/" in filename:
                filename = filename.split("/")[-1]
            added = event.data.get("added", 0)
            removed = event.data.get("removed", 0)
            self._state.add_file_change(filename, added, removed)
            self._schedule_rebuild()

    def _on_git_commit(self, event: UIEvent) -> None:
        """Handle git commit."""
        commit_hash = event.data.get("commit_hash", "")
        files_count = event.data.get("files_count", 0)
        if commit_hash:
            self._state.add_git_commit(commit_hash, files_count)
            self._schedule_rebuild()

    def _on_validation_end(self, event: UIEvent) -> None:
        """Handle validation result."""
        valid = event.data.get("valid", True)
        error_count = event.data.get("error_count", 0)
        self._state.add_validation_result(valid, error_count)
        self._schedule_rebuild()

    def _on_test_end(self, event: UIEvent) -> None:
        """Handle test result."""
        passed = event.data.get("passed", 0)
        total = event.data.get("total", 0)
        if total > 0:
            self._state.add_test_result(passed, total)
            self._schedule_rebuild()

    def _on_activity(self, event: UIEvent) -> None:
        """Handle generic activity event."""
        activity_type = event.data.get("activity_type", "")
        message = event.data.get("message", "")
        context = event.data.get("context", "")
        agent = event.data.get("agent", "")

        # Map activity types to node types
        type_map = {
            "think": NodeType.MESSAGE,
            "search": NodeType.AGENT_ACTION,
            "read": NodeType.AGENT_ACTION,
            "plan": NodeType.AGENT_ACTION,
            "edit": NodeType.FILE,
            "execute": NodeType.CODE_EXEC,
            "validate": NodeType.VALIDATION,
            "wait": NodeType.SYSTEM,
        }

        # Map activity types to icons
        icon_map = {
            "think": "💭",
            "search": "🔍",
            "read": "📄",
            "plan": "📋",
            "edit": "✏️",
            "execute": "⚡",
            "validate": "✅",
            "wait": "⏳",
        }

        node_type = type_map.get(activity_type, NodeType.SYSTEM)
        icon = icon_map.get(activity_type, "•")

        # Determine parent
        parent_id = None
        if agent:
            # If from a specific agent, nest under it
            parent_id = agent
        else:
            parent_id = self._state.get_current_parent()

        secondary = context[:40] + "..." if len(context) > 40 else context

        node = TimelineNode(
            node_type=node_type,
            status=NodeStatus.COMPLETE,
            icon=icon,
            primary_text=message,
            secondary_text=secondary,
            parent_id=parent_id,
        )
        self._state.add_node(node)
        self._schedule_rebuild()

    # Tree building

    def _schedule_rebuild(self) -> None:
        """Schedule a tree rebuild (throttled, thread-safe)."""
        import time

        now = time.time()
        if now - self._last_rebuild_time < self._rebuild_interval:
            # Skip if too soon - but schedule a deferred rebuild
            self.set_timer(self._rebuild_interval, self._do_rebuild)
            return

        self._last_rebuild_time = now
        self.app.call_from_thread(self._do_rebuild)

    def _do_rebuild(self) -> None:
        """Perform the actual tree rebuild (must run in main thread)."""
        self._rebuild_tree()

    def _rebuild_tree(self) -> None:
        """Rebuild entire tree from TimelineState."""
        # Clear existing
        self.clear()
        self._timeline_nodes.clear()

        # Get root nodes
        root_nodes = self._state.get_root_nodes()

        # Build tree recursively
        for node in root_nodes:
            self._add_node_to_tree(self.root, node)

        # Expand all by default
        self.root.expand_all()

        # Auto-scroll to bottom
        if self.auto_scroll:
            self.call_after_refresh(self._scroll_to_bottom)

    def _add_node_to_tree(
        self, parent: TreeNode[str], node: TimelineNode
    ) -> TreeNode[str]:
        """Add a timeline node to the tree, including children."""
        # Build label
        label = self._build_node_label(node)

        # Add to tree
        tree_node = parent.add(label, data=node.id)
        self._timeline_nodes[node.id] = tree_node

        # Add children recursively
        children = self._state.get_children(node.id)
        for child in children:
            self._add_node_to_tree(tree_node, child)

        return tree_node

    def _build_node_label(self, node: TimelineNode) -> Text:
        """Build Rich Text label for a timeline node."""
        label = Text()

        # Icon
        icon = node.display_icon
        color = node.display_color

        label.append(f"{icon} ", style=color)

        # Primary text
        label.append(node.primary_text, style=color)

        # Secondary text (context) in parentheses
        if node.secondary_text:
            label.append(f" ({node.secondary_text})", style="dim")

        # Error message in red
        if node.error_message:
            error_preview = node.error_message[:30]
            if len(node.error_message) > 30:
                error_preview += "..."
            label.append(f" - {error_preview}", style="red")

        # Elapsed time on the right (padded)
        elapsed = node.elapsed_display
        # Calculate padding to right-align at ~60 chars
        current_len = len(label.plain)
        padding = max(1, 55 - current_len)
        label.append(" " * padding, style="dim")
        label.append(elapsed, style="dim italic")

        return label

    def _scroll_to_bottom(self) -> None:
        """Scroll to the bottom of the tree."""
        self.scroll_end(animate=False)

    # Keyboard navigation

    def on_key(self, event) -> None:
        """Handle keyboard input."""
        if event.key in ("up", "down", "pageup", "pagedown", "k", "j"):
            # Pause auto-scroll when user navigates
            self.auto_scroll = False
        elif event.key == "g":
            # 'g' goes to bottom and resumes auto-scroll
            self._scroll_to_bottom()
            self.auto_scroll = True
        elif event.key == "G":
            # 'G' goes to top
            self.scroll_home(animate=False)
            self.auto_scroll = False

    def on_tree_node_selected(self, event: Tree.NodeSelected) -> None:
        """Handle node selection."""
        if event.node.data and isinstance(event.node.data, str):
            self.selected_node_id = event.node.data

    # Public API

    def add_node(
        self,
        node_type: NodeType,
        primary_text: str,
        secondary_text: str = "",
        parent_id: str | None = None,
        code_block_id: str | None = None,
    ) -> str:
        """
        Add a node to the timeline.

        Returns the node ID for future reference.
        """
        node = TimelineNode(
            node_type=node_type,
            status=NodeStatus.RUNNING,
            primary_text=primary_text,
            secondary_text=secondary_text,
            parent_id=parent_id or self._state.get_current_parent(),
            code_block_id=code_block_id,
        )
        self._state.add_node(node)
        self._schedule_rebuild()
        return node.id

    def complete_node(self, node_id: str, error: str | None = None) -> None:
        """Mark a node as complete or error."""
        self._state.complete_node(node_id, error)
        self._schedule_rebuild()

    def clear_timeline(self) -> None:
        """Clear all nodes from the timeline."""
        self._state.clear()
        self._rebuild_tree()

    @property
    def node_count(self) -> int:
        """Number of nodes in the timeline."""
        return self._state.node_count
