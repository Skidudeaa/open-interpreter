"""
Agent Tree - Expandable hierarchical agent view.

Shows parent → child agent relationships in a tree structure.
Displays last 3 lines of output preview for each agent.
Supports selection tracking for keyboard navigation.

Part of Phase 2: Agent Visualization
"""

from typing import Optional, List
from rich.console import Console
from rich.panel import Panel
from rich.tree import Tree
from rich.text import Text

from .ui_state import UIState, AgentState, AgentStatus, AgentRole
from .theme import THEME, BOX_STYLES


class AgentTree:
    """
    Expandable hierarchical view of agents.

    Layout:
    ┌─ Agents ──────────────────────────────────────────┐
    │ 🔍 Scout [✓ 2.3s]                                 │
    │   └─ Found 12 files                               │
    │       Located auth logic                          │
    │       Identified dependencies                     │
    │   🔧 Surgeon [⏳ running]                          │
    │     └─ Applying edit to auth.py                   │
    │         Updating imports                          │
    │         Adding error handling                     │
    └───────────────────────────────────────────────────┘

    Features:
    - Shows parent-child relationships via parent_id
    - Displays last 3 lines of output as preview
    - Color-coded by status
    - Selected agent highlighted
    """

    # Role-specific emoji icons (matches agent_strip.py)
    ROLE_ICONS = {
        AgentRole.SCOUT: "🔍",
        AgentRole.SURGEON: "🔧",
        AgentRole.ARCHITECT: "🏗️",
        AgentRole.VALIDATOR: "✅",
        AgentRole.HISTORIAN: "📚",
        AgentRole.CUSTOM: "🤖",
    }

    # Status-specific colors
    STATUS_COLORS = {
        AgentStatus.PENDING: "warning",
        AgentStatus.RUNNING: "secondary",
        AgentStatus.COMPLETE: "success",
        AgentStatus.ERROR: "error",
        AgentStatus.CANCELLED: "text_muted",
    }

    def __init__(self, state: UIState, console: Console = None):
        """
        Initialize the agent tree.

        Args:
            state: The UIState instance
            console: Optional console to use
        """
        self.state = state
        self.console = console or Console()
        self.preview_lines = 3  # Number of output lines to show per agent

    def render(self) -> Optional[Panel]:
        """
        Render the agent tree panel.

        Returns:
            Panel with agent tree, or None if no agents
        """
        if not self.state.active_agents:
            return None

        # Build the tree structure
        tree = self._build_tree()

        return Panel(
            tree,
            title="🤖 Agents",
            title_align="left",
            box=BOX_STYLES["message"],
            style=f"on {THEME['bg_dark']}",
            border_style=THEME["primary"],
            padding=(0, 1),
        )

    def _build_tree(self) -> Tree:
        """
        Build the Rich Tree structure with agent hierarchy.

        Returns:
            Tree with all agents and their relationships
        """
        # Create root tree
        root = Tree(
            Text("Agent Workflow", style=f"bold {THEME['primary']}"),
            guide_style=THEME["text_muted"],
        )

        # Find root agents (no parent_id)
        root_agents = [
            (agent_id, agent)
            for agent_id, agent in self.state.active_agents.items()
            if agent.parent_id is None
        ]

        # Build tree recursively
        for agent_id, agent in root_agents:
            self._add_agent_node(root, agent_id, agent)

        return root

    def _add_agent_node(self, parent_tree: Tree, agent_id: str, agent: AgentState):
        """
        Add an agent node and its children to the tree.

        Args:
            parent_tree: Parent Tree or branch to add to
            agent_id: ID of the agent
            agent: AgentState instance
        """
        # Build agent header
        header = self._build_agent_header(agent, agent_id == self.state.selected_agent_id)

        # Add node to tree
        branch = parent_tree.add(header)

        # Add output preview if available
        if agent.last_lines:
            preview_lines = list(agent.last_lines)[-self.preview_lines:]
            for line in preview_lines:
                # Truncate long lines
                if len(line) > 60:
                    line = line[:57] + "..."
                # Dim the preview text
                branch.add(Text(line, style=f"dim {THEME['text_secondary']}"))

        # Add error summary if in error state
        if agent.status == AgentStatus.ERROR and agent.error_summary:
            error_text = Text("Error: ", style=f"bold {THEME['error']}")
            error_text.append(agent.error_summary, style=THEME['error'])
            branch.add(error_text)

        # Find and add child agents
        child_agents = [
            (child_id, child)
            for child_id, child in self.state.active_agents.items()
            if child.parent_id == agent_id
        ]

        for child_id, child in child_agents:
            self._add_agent_node(branch, child_id, child)

    def _build_agent_header(self, agent: AgentState, is_selected: bool) -> Text:
        """
        Build the header text for an agent node.

        Format: 🔍 Scout [✓ 2.3s]

        Args:
            agent: AgentState instance
            is_selected: True if this agent is selected

        Returns:
            Text with the header
        """
        header = Text()

        # Role icon and name
        role_icon = self.ROLE_ICONS.get(agent.role, "🤖")
        role_name = agent.role.value.title()

        # Apply bold if selected
        name_style = f"bold {THEME['primary']}" if is_selected else None
        header.append(f"{role_icon} {role_name}", style=name_style)

        # Status bracket with icon and time
        status_icon = agent.status_icon
        status_color_key = self.STATUS_COLORS.get(agent.status, "text_muted")
        status_color = THEME[status_color_key]

        header.append(" [", style="dim")
        header.append(status_icon, style=status_color)
        header.append(f" {agent.elapsed_display}", style="dim")
        header.append("]", style="dim")

        return header

    def display(self):
        """Print the agent tree to the console."""
        panel = self.render()
        if panel:
            self.console.print(panel)

    def get_agent_hierarchy(self) -> List[tuple]:
        """
        Get a flat list of agents in tree order.

        Returns:
            List of (agent_id, agent, depth) tuples
        """
        hierarchy = []

        def traverse(agent_id: str, depth: int = 0):
            if agent_id not in self.state.active_agents:
                return

            agent = self.state.active_agents[agent_id]
            hierarchy.append((agent_id, agent, depth))

            # Find children
            for child_id, child in self.state.active_agents.items():
                if child.parent_id == agent_id:
                    traverse(child_id, depth + 1)

        # Start with root agents
        for agent_id, agent in self.state.active_agents.items():
            if agent.parent_id is None:
                traverse(agent_id)

        return hierarchy

    def select_next_agent(self):
        """
        Select the next agent in the hierarchy.

        Updates UIState.selected_agent_id.
        """
        hierarchy = self.get_agent_hierarchy()
        if not hierarchy:
            return

        # Find current selection index
        current_index = -1
        if self.state.selected_agent_id:
            for i, (agent_id, _, _) in enumerate(hierarchy):
                if agent_id == self.state.selected_agent_id:
                    current_index = i
                    break

        # Move to next
        next_index = (current_index + 1) % len(hierarchy)
        self.state.selected_agent_id = hierarchy[next_index][0]

    def select_prev_agent(self):
        """
        Select the previous agent in the hierarchy.

        Updates UIState.selected_agent_id.
        """
        hierarchy = self.get_agent_hierarchy()
        if not hierarchy:
            return

        # Find current selection index
        current_index = 0
        if self.state.selected_agent_id:
            for i, (agent_id, _, _) in enumerate(hierarchy):
                if agent_id == self.state.selected_agent_id:
                    current_index = i
                    break

        # Move to previous
        prev_index = (current_index - 1) % len(hierarchy)
        self.state.selected_agent_id = hierarchy[prev_index][0]


def display_agent_tree(state: UIState, console: Console = None):
    """
    Convenience function to display the agent tree.

    Args:
        state: The UIState instance
        console: Optional console to use
    """
    AgentTree(state, console).display()
