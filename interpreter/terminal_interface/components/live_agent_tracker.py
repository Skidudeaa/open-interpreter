"""
Live Agent Tracker - Minimalist real-time agent visualization.

Shows a persistent status line at the bottom of the terminal that updates
in real-time as agents work. Uses Rich's Live display for flicker-free updates.

Format: [🔍 Scout ⏳ 2.3s] [🔧 Surgeon ✓ 1.1s] [🏗️ Architect ▶ running]
"""

import logging
import threading
import time
from typing import TYPE_CHECKING

logger = logging.getLogger(__name__)

from rich.console import Console
from rich.live import Live
from rich.panel import Panel
from rich.text import Text

from .theme import BOX_STYLES, THEME
from .ui_events import EventType, UIEvent, get_event_bus
from .ui_state import AgentRole, AgentStatus, UIState

if TYPE_CHECKING:
    from ...core.core import OpenInterpreter


# Compact icons for each role
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

# Status icons and colors
STATUS_DISPLAY = {
    AgentStatus.PENDING: ("○", "yellow"),
    AgentStatus.RUNNING: ("⏳", "cyan"),
    AgentStatus.COMPLETE: ("✓", "green"),
    AgentStatus.ERROR: ("✗", "red"),
    AgentStatus.CANCELLED: ("⊘", "dim"),
}


class LiveAgentTracker:
    """
    Real-time agent status tracker with persistent terminal display.

    Usage:
        tracker = LiveAgentTracker(interpreter)
        tracker.start()  # Begin tracking
        # ... agents run ...
        tracker.stop()   # Stop tracking

    Or as context manager:
        with LiveAgentTracker(interpreter):
            # ... agents run ...
    """

    def __init__(
        self,
        interpreter: "OpenInterpreter" = None,
        state: UIState = None,
        console: Console = None,
        refresh_rate: float = 4.0,  # Updates per second
    ):
        """
        Initialize the live tracker.

        Args:
            interpreter: OpenInterpreter instance (optional, for getting ui_state)
            state: UIState instance (optional, creates new if not provided)
            console: Rich Console to use (optional)
            refresh_rate: How many times per second to refresh display
        """
        self.interpreter = interpreter
        self.console = console or Console()
        self.refresh_rate = refresh_rate

        # Get or create UIState
        if state:
            self.state = state
        elif interpreter and hasattr(interpreter, "_ui_state"):
            self.state = interpreter._ui_state
        else:
            self.state = UIState()

        # Live display context
        self._live: Live = None
        self._running = False
        self._update_thread: threading.Thread = None
        self._event_subscriptions = []

    def _render(self) -> Panel | Text:
        """Render the current agent status."""
        if not self.state.active_agents:
            return Text("")

        # Build compact status line
        parts = []
        for _agent_id, agent in self.state.active_agents.items():
            # Get icons
            role_icon = ROLE_ICONS.get(agent.role, "🤖")
            status_icon, status_color = STATUS_DISPLAY.get(agent.status, ("?", "white"))

            # Format: icon name status time
            role_name = agent.role.value[:4].title()  # Shortened name
            elapsed = agent.elapsed_display

            # Build text segment
            segment = Text()
            segment.append("[", style="dim")
            segment.append(f"{role_icon} ", style="bold")
            segment.append(f"{role_name} ", style="white")
            segment.append(f"{status_icon}", style=status_color)
            segment.append(f" {elapsed}", style="dim")
            segment.append("]", style="dim")

            parts.append(segment)

        # Join with spaces
        if not parts:
            return Text("")

        result = Text(" ")
        for i, part in enumerate(parts):
            if i > 0:
                result.append(" ")
            result.append_text(part)

        # Wrap in minimal panel
        return Panel(
            result,
            box=BOX_STYLES.get("status", BOX_STYLES.get("minimal")),
            style=f"on {THEME.get('bg_dark', 'black')}",
            border_style=THEME.get("text_muted", "dim"),
            padding=(0, 1),
            title="[dim]agents[/dim]",
            title_align="left",
        )

    def _subscribe_events(self):
        """Subscribe to agent events from EventBus."""
        event_bus = get_event_bus()

        def handle_spawn(event: UIEvent):
            agent_id = event.data.get("agent_id", "unknown")
            role_str = event.data.get("role", "custom")
            parent_id = event.data.get("parent_id")

            try:
                role = (
                    AgentRole(role_str)
                    if isinstance(role_str, str)
                    else AgentRole.CUSTOM
                )
            except ValueError:
                role = AgentRole.CUSTOM

            self.state.add_agent(agent_id, role, parent_id)
            self._refresh()

        def handle_complete(event: UIEvent):
            agent_id = event.data.get("agent_id")
            if agent_id:
                self.state.update_agent_status(agent_id, AgentStatus.COMPLETE)
                self._refresh()

        def handle_error(event: UIEvent):
            agent_id = event.data.get("agent_id")
            error = event.data.get("error", "Unknown error")
            if agent_id:
                self.state.update_agent_status(agent_id, AgentStatus.ERROR, error)
                self._refresh()

        def handle_output(event: UIEvent):
            agent_id = event.data.get("agent_id")
            line = event.data.get("line", "")
            if agent_id:
                self.state.append_agent_output(agent_id, line)
                # Don't refresh on every output line - too frequent

        # Subscribe to events
        self._event_subscriptions = [
            event_bus.subscribe(EventType.AGENT_SPAWN, handle_spawn),
            event_bus.subscribe(EventType.AGENT_COMPLETE, handle_complete),
            event_bus.subscribe(EventType.AGENT_ERROR, handle_error),
            event_bus.subscribe(EventType.AGENT_OUTPUT, handle_output),
        ]

    def _unsubscribe_events(self):
        """Unsubscribe from all events."""
        event_bus = get_event_bus()
        for sub_id in self._event_subscriptions:
            event_bus.unsubscribe(sub_id)
        self._event_subscriptions = []

    def _refresh(self):
        """Trigger a display refresh."""
        if self._live and self._running:
            try:
                self._live.update(self._render())
            except Exception as e:
                logger.debug(f"Render error during shutdown: {e}")

    def _update_loop(self):
        """Background thread for periodic updates."""
        interval = 1.0 / self.refresh_rate
        while self._running:
            self._refresh()
            time.sleep(interval)

    def start(self):
        """Start the live tracker display."""
        if self._running:
            return

        self._running = True
        self._subscribe_events()

        # Start Live display
        self._live = Live(
            self._render(),
            console=self.console,
            refresh_per_second=self.refresh_rate,
            transient=True,  # Remove on stop
        )
        self._live.start()

        # Start background update thread for elapsed time updates
        self._update_thread = threading.Thread(target=self._update_loop, daemon=True)
        self._update_thread.start()

    def stop(self):
        """Stop the live tracker display."""
        if not self._running:
            return

        self._running = False
        self._unsubscribe_events()

        # Stop Live display
        if self._live:
            try:
                self._live.stop()
            except Exception as e:
                logger.debug(f"Error stopping live display: {e}")
            self._live = None

        # Wait for update thread
        if self._update_thread and self._update_thread.is_alive():
            self._update_thread.join(timeout=0.5)
        self._update_thread = None

    def __enter__(self):
        """Context manager entry."""
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit."""
        self.stop()
        return False


class SimpleAgentDisplay:
    """
    Simpler non-Live agent display for inline updates.

    Prints agent status line, can be called repeatedly to update in-place
    using terminal control codes.
    """

    def __init__(self, state: UIState, console: Console = None):
        self.state = state
        self.console = console or Console()
        self._last_line_length = 0

    def render_line(self) -> str:
        """Render agents as a single line string."""
        if not self.state.active_agents:
            return ""

        parts = []
        for agent in self.state.active_agents.values():
            role_icon = ROLE_ICONS.get(agent.role, "🤖")
            status_icon, _ = STATUS_DISPLAY.get(agent.status, ("?", "white"))
            role_name = agent.role.value[:4].title()
            elapsed = agent.elapsed_display
            parts.append(f"[{role_icon} {role_name} {status_icon} {elapsed}]")

        return " ".join(parts)

    def print_update(self, clear_previous: bool = True):
        """Print agent status, optionally clearing previous line."""
        line = self.render_line()
        if not line:
            return

        if clear_previous and self._last_line_length > 0:
            # Move cursor up and clear line
            self.console.print(f"\r{' ' * self._last_line_length}\r", end="")

        self.console.print(f"[dim]{line}[/dim]", end="\r")
        self._last_line_length = len(line) + 10  # Account for markup


def display_agent_status(state: UIState, console: Console = None) -> str:
    """
    Quick function to print current agent status.

    Returns the rendered text for logging/debugging.
    """
    display = SimpleAgentDisplay(state, console)
    line = display.render_line()
    if line:
        console = console or Console()
        console.print(f"[dim]agents:[/dim] {line}")
    return line
