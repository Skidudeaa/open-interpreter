"""
Agent Strip - Bottom bar showing all active agents.

Displays real-time agent status with icons, timing, and selection.
Reads from UIState.active_agents and updates via EventBus.

Part of Phase 2: Agent Visualization
"""

from typing import Optional
from rich.console import Console, Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from .ui_state import UIState, AgentStatus, AgentRole
from .theme import THEME, BOX_STYLES


class AgentStrip:
    """
    Bottom bar showing all active agents.

    Layout:
    ┌─────────────────────────────────────────────────────────────────┐
    │ [Scout: ✓ 2.3s] [Surgeon: ⏳ thinking...] [Validator: ▶ running] │
    └─────────────────────────────────────────────────────────────────┘

    Status icons:
    - ○ pending
    - ⏳ running
    - ✓ complete
    - ✗ error
    - ⊘ cancelled

    Colors:
    - pending: yellow
    - running: cyan
    - complete: green
    - error: red
    - cancelled: dim
    """

    # Role-specific emoji icons
    ROLE_ICONS = {
        AgentRole.SCOUT: "🔍",        # Magnifying glass
        AgentRole.SURGEON: "🔧",      # Wrench
        AgentRole.ARCHITECT: "🏗️",    # Building construction
        AgentRole.VALIDATOR: "✅",    # Check mark button
        AgentRole.HISTORIAN: "📚",    # Books
        AgentRole.CUSTOM: "🤖",       # Robot
    }

    # Status-specific colors (theme keys)
    STATUS_COLORS = {
        AgentStatus.PENDING: "warning",
        AgentStatus.RUNNING: "secondary",
        AgentStatus.COMPLETE: "success",
        AgentStatus.ERROR: "error",
        AgentStatus.CANCELLED: "text_muted",
    }

    def __init__(self, state: UIState, console: Console = None):
        """
        Initialize the agent strip.

        Args:
            state: The UIState instance
            console: Optional console to use
        """
        self.state = state
        self.console = console or Console()

    def render(self) -> Optional[Panel]:
        """
        Render the agent strip panel.

        Returns:
            Panel with agent status, or None if no agents
        """
        if not self.state.active_agents:
            return None

        # Build table with agent badges
        table = Table(
            show_header=False,
            show_footer=False,
            box=None,
            padding=0,
            expand=True,
        )
        table.add_column(justify="left")

        # Build agent badges
        badges = []
        for agent_id, agent in self.state.active_agents.items():
            badge = self._build_agent_badge(agent, agent_id == self.state.selected_agent_id)
            badges.append(badge)

        # Join badges with spaces
        content = Text(" ").join(badges)
        table.add_row(content)

        return Panel(
            table,
            box=BOX_STYLES["status"],
            style=f"on {THEME['bg_dark']}",
            border_style=THEME["text_muted"],
            padding=(0, 1),
        )

    def _build_agent_badge(self, agent, is_selected: bool) -> Text:
        """
        Build a single agent badge.

        Format: [Scout: ✓ 2.3s]

        Args:
            agent: AgentState instance
            is_selected: True if this agent is selected

        Returns:
            Text with the badge
        """
        badge = Text()

        # Opening bracket
        bracket_style = "bold" if is_selected else "dim"
        badge.append("[", style=bracket_style)

        # Role icon and name
        role_icon = self.ROLE_ICONS.get(agent.role, "🤖")
        role_name = agent.role.value.title()
        badge.append(f"{role_icon} {role_name}", style="bold" if is_selected else None)

        # Status icon
        status_icon = agent.status_icon
        status_color_key = self.STATUS_COLORS.get(agent.status, "text_muted")
        status_color = THEME[status_color_key]
        badge.append(f": {status_icon}", style=status_color)

        # Elapsed time or status message
        if agent.status == AgentStatus.RUNNING:
            # Show last output line preview if available
            if agent.last_lines:
                preview = agent.last_lines[-1]
                # Truncate long previews
                if len(preview) > 20:
                    preview = preview[:17] + "..."
                badge.append(f" {preview}", style="dim")
            else:
                badge.append(" thinking...", style="dim")
        elif agent.status == AgentStatus.ERROR:
            # Show error summary if available
            if agent.error_summary:
                error = agent.error_summary
                if len(error) > 20:
                    error = error[:17] + "..."
                badge.append(f" {error}", style=f"dim {THEME['error']}")
        else:
            # Show elapsed time for completed/pending agents
            badge.append(f" {agent.elapsed_display}", style="dim")

        # Closing bracket
        badge.append("]", style=bracket_style)

        return badge

    def display(self):
        """Print the agent strip to the console."""
        panel = self.render()
        if panel:
            self.console.print(panel)

    def get_agent_count(self) -> int:
        """Get the count of active agents."""
        return len(self.state.active_agents)

    def get_running_count(self) -> int:
        """Get the count of currently running agents."""
        return sum(
            1 for agent in self.state.active_agents.values()
            if agent.status == AgentStatus.RUNNING
        )

    def get_summary(self) -> str:
        """
        Get a plain text summary of agent status.

        Returns:
            String like "3 agents (2 running)"
        """
        total = self.get_agent_count()
        running = self.get_running_count()

        if total == 0:
            return "No agents"
        elif running == 0:
            plural = "s" if total != 1 else ""
            return f"{total} agent{plural}"
        else:
            plural1 = "s" if total != 1 else ""
            plural2 = "s" if running != 1 else ""
            return f"{total} agent{plural1} ({running} running)"


def display_agent_strip(state: UIState, console: Console = None):
    """
    Convenience function to display the agent strip.

    Args:
        state: The UIState instance
        console: Optional console to use
    """
    AgentStrip(state, console).display()
