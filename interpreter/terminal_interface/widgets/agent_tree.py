"""
AgentTree Widget - Hierarchical agent visualization.

ARCHITECTURE: Textual Tree widget with custom node labels showing:
  - Role icons (Scout, Surgeon, etc.)
  - Status icons (pending, running, complete, error)
  - Elapsed time
  - Last 3 output lines as preview

WHY: Tree structure shows parent→child agent relationships clearly.
Enables keyboard navigation (↑↓←→) via built-in Tree behavior.

TRADEOFF: Full rebuild on updates vs incremental - simpler, works well for <50 agents.

Part of Phase 3: Agent Visualization.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from rich.text import Text
from textual.reactive import reactive
from textual.widgets import Tree
from textual.widgets.tree import TreeNode

from ..components.ui_events import EventType, UIEvent, get_event_bus
from ..components.ui_state import AgentRole, AgentState, AgentStatus

if TYPE_CHECKING:
    from ..components.ui_state import UIState


class AgentTreeWidget(Tree[str]):
    """
    Hierarchical agent tree with real-time updates.

    Features:
    - Auto-rebuilds on agent lifecycle events
    - Shows parent→child relationships
    - Displays output preview (last 3 lines)
    - Keyboard navigation: ↑↓ select, ←→ expand/collapse
    - Color-coded status (pending/running/complete/error)

    CSS Classes:
    - .agent-tree - Base styling
    """

    # Role icons (matches agent_strip.py)
    ROLE_ICONS = {
        AgentRole.SCOUT: "🔍",
        AgentRole.SURGEON: "🔧",
        AgentRole.ARCHITECT: "🏗️",
        AgentRole.VALIDATOR: "✅",
        AgentRole.HISTORIAN: "📚",
        AgentRole.REVIEWER: "👁️",
        AgentRole.TESTER: "🧪",
        AgentRole.CUSTOM: "🤖",
    }

    # Status icons
    STATUS_ICONS = {
        AgentStatus.PENDING: "○",
        AgentStatus.RUNNING: "⏳",
        AgentStatus.COMPLETE: "✓",
        AgentStatus.ERROR: "✗",
        AgentStatus.CANCELLED: "⊘",
    }

    DEFAULT_CSS = """
    AgentTreeWidget {
        dock: right;
        width: 40;
        min-width: 30;
        max-width: 60;
        background: #1a1a2e;  /* $surface */
        border-left: solid #8b949e;  /* $secondary */
        padding: 1;
        scrollbar-gutter: stable;
        display: none;
    }

    AgentTreeWidget.visible {
        display: block;
    }

    AgentTreeWidget:focus {
        border-left: solid #58a6ff;  /* $primary */
    }

    AgentTreeWidget > .tree--guides {
        color: #8b949e;  /* $secondary */
    }

    AgentTreeWidget > .tree--cursor {
        background: #58a6ff 20%;  /* $primary 20% */
    }
    """

    PREVIEW_LINES = 3

    # Reactive state
    selected_agent_id: reactive[str | None] = reactive(None)

    def __init__(
        self,
        ui_state: UIState,
        *,
        name: str | None = None,
        id: str | None = None,
        classes: str | None = None,
    ):
        super().__init__(
            "🤖 Agent Workflow",
            data="root",
            name=name,
            id=id,
            classes=classes,
        )
        self.ui_state = ui_state
        self._event_bus = get_event_bus()

        # Map agent_id -> TreeNode for quick updates
        self._agent_nodes: dict[str, TreeNode[str]] = {}

        self.add_class("agent-tree")

    def on_mount(self) -> None:
        """Subscribe to events and build initial tree."""
        self._event_bus.subscribe(EventType.AGENT_SPAWN, self._on_agent_spawn)
        self._event_bus.subscribe(EventType.AGENT_COMPLETE, self._on_agent_update)
        self._event_bus.subscribe(EventType.AGENT_ERROR, self._on_agent_update)
        self._event_bus.subscribe(EventType.AGENT_OUTPUT, self._on_agent_output)

        # Build tree from existing state
        self.rebuild_tree()

    def on_unmount(self) -> None:
        """Unsubscribe from events."""
        self._event_bus.unsubscribe(EventType.AGENT_SPAWN, self._on_agent_spawn)
        self._event_bus.unsubscribe(EventType.AGENT_COMPLETE, self._on_agent_update)
        self._event_bus.unsubscribe(EventType.AGENT_ERROR, self._on_agent_update)
        self._event_bus.unsubscribe(EventType.AGENT_OUTPUT, self._on_agent_output)

    def rebuild_tree(self) -> None:
        """Rebuild entire tree from UIState.active_agents."""
        # Clear existing nodes
        self.clear()
        self._agent_nodes.clear()

        # Check if we have agents
        if not self.ui_state.active_agents:
            self.remove_class("visible")
            return

        self.add_class("visible")

        # Find root agents (no parent)
        root_agents = [
            (aid, agent)
            for aid, agent in self.ui_state.active_agents.items()
            if agent.parent_id is None
        ]

        # Build tree recursively
        for agent_id, agent in root_agents:
            self._add_agent_node(self.root, agent_id, agent)

        # Expand all by default
        self.root.expand_all()

    def _add_agent_node(
        self, parent_node: TreeNode[str], agent_id: str, agent: AgentState
    ) -> TreeNode[str]:
        """Add agent node with children recursively."""
        # Build label
        label = self._build_agent_label(agent)

        # Add node to tree
        node = parent_node.add(label, data=agent_id)
        self._agent_nodes[agent_id] = node

        # Add output preview lines as child nodes
        if agent.last_lines:
            preview_lines = list(agent.last_lines)[-self.PREVIEW_LINES :]
            for line in preview_lines:
                # Truncate long lines
                if len(line) > 60:
                    line = line[:57] + "..."
                preview_label = Text(line, style="dim italic")
                node.add_leaf(preview_label, data=f"{agent_id}:preview")

        # Add error summary if present
        if agent.status == AgentStatus.ERROR and agent.error_summary:
            error_text = Text("Error: ", style="bold red")
            error_text.append(agent.error_summary[:60], style="red")
            node.add_leaf(error_text, data=f"{agent_id}:error")

        # Find and add child agents
        child_agents = [
            (cid, child)
            for cid, child in self.ui_state.active_agents.items()
            if child.parent_id == agent_id
        ]

        for child_id, child in child_agents:
            self._add_agent_node(node, child_id, child)

        return node

    def _build_agent_label(self, agent: AgentState) -> Text:
        """Build Rich Text label for agent node."""
        label = Text()

        # Role icon and name
        role_icon = self.ROLE_ICONS.get(agent.role, "🤖")
        role_name = agent.role.value.title()

        # Selected agents get bold
        is_selected = agent.id == self.selected_agent_id
        name_style = "bold cyan" if is_selected else "white"

        label.append(f"{role_icon} {role_name}", style=name_style)

        # Status bracket
        status_icon = self.STATUS_ICONS.get(agent.status, "?")
        status_color = self._get_status_color(agent.status)

        label.append(" [", style="dim")
        label.append(status_icon, style=status_color)
        label.append(f" {agent.elapsed_display}", style="dim")
        label.append("]", style="dim")

        return label

    def _get_status_color(self, status: AgentStatus) -> str:
        """Map status to color."""
        return {
            AgentStatus.PENDING: "yellow",
            AgentStatus.RUNNING: "cyan",
            AgentStatus.COMPLETE: "green",
            AgentStatus.ERROR: "red",
            AgentStatus.CANCELLED: "dim",
        }.get(status, "white")

    # Event handlers (called from EventBus)

    def _on_agent_spawn(self, event: UIEvent) -> None:
        """Handle new agent spawn."""
        agent_id = event.data.get("agent_id")
        if agent_id and agent_id in self.ui_state.active_agents:
            # Rebuild tree to maintain hierarchy
            self.app.call_from_thread(self.rebuild_tree)

    def _on_agent_update(self, _event: UIEvent) -> None:
        """Handle agent status update."""
        agent_id = _event.data.get("agent_id")
        if agent_id and agent_id in self._agent_nodes:
            # Update node label
            if agent_id in self.ui_state.active_agents:
                agent = self.ui_state.active_agents[agent_id]
                new_label = self._build_agent_label(agent)
                self.app.call_from_thread(self._update_node_label, agent_id, new_label)

    def _on_agent_output(self, _event: UIEvent) -> None:
        """Handle new agent output (update preview)."""
        # Rebuild to update preview lines
        self.app.call_from_thread(self.rebuild_tree)

    def _update_node_label(self, agent_id: str, new_label: Text) -> None:
        """Update node label (must run in main thread)."""
        if agent_id in self._agent_nodes:
            node = self._agent_nodes[agent_id]
            node.set_label(new_label)
            self.refresh()

    # Keyboard navigation

    def on_tree_node_selected(self, event: Tree.NodeSelected) -> None:
        """Handle node selection."""
        # Update selected_agent_id if it's an agent node
        if event.node.data and isinstance(event.node.data, str):
            if ":" not in event.node.data:  # Filter out preview/error nodes
                self.selected_agent_id = event.node.data

    def watch_selected_agent_id(self, old_id: str | None, new_id: str | None) -> None:
        """React to selection change."""
        # Rebuild to update bold styling
        if old_id != new_id:
            self.rebuild_tree()

    # Public API

    def add_agent(
        self, _agent_id: str, _role: AgentRole, _parent_id: str | None = None
    ) -> None:
        """Add a new agent (convenience method)."""
        self.rebuild_tree()

    def update_agent(self, agent_id: str, _status: AgentStatus) -> None:
        """Update agent status (convenience method)."""
        if agent_id in self.ui_state.active_agents:
            agent = self.ui_state.active_agents[agent_id]
            new_label = self._build_agent_label(agent)
            self._update_node_label(agent_id, new_label)

    def remove_agent(self, agent_id: str) -> None:
        """Remove agent from tree."""
        if agent_id in self._agent_nodes:
            # Remove from tree
            node = self._agent_nodes[agent_id]
            node.remove()
            del self._agent_nodes[agent_id]

        # Update visibility
        if not self.ui_state.active_agents:
            self.remove_class("visible")

    def clear_agents(self) -> None:
        """Clear all agents."""
        self.clear()
        self._agent_nodes.clear()
        self.remove_class("visible")
