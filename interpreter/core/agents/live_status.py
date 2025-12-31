"""
Live Status Panel - Updates in place during agent execution.

Shows a minimal, non-scrolling status panel while agents work.
Uses Rich's Live display for flicker-free updates.
"""

import threading
import time
from typing import TYPE_CHECKING

from rich.console import Console
from rich.live import Live
from rich.panel import Panel
from rich.text import Text

if TYPE_CHECKING:
    from .orchestrator import AgentOrchestrator, WorkflowResult, WorkflowType


# Compact icons and short names
ROLE_ICONS = {
    "scout": "🔍",
    "surgeon": "🔧",
    "architect": "🏗️",
    "validator": "✅",
    "historian": "📚",
    "reviewer": "👁️",
    "tester": "🧪",
    "custom": "🤖",
}

# Short display names (max 8 chars)
ROLE_NAMES = {
    "scout": "Scout",
    "surgeon": "Surgeon",
    "architect": "Arch",
    "validator": "Valid",
    "historian": "History",
    "reviewer": "Review",
    "tester": "Test",
    "custom": "Agent",
}

STATUS_ICONS = {
    "pending": ("○", "dim"),
    "running": ("◉", "cyan"),
    "complete": ("✓", "green"),
    "error": ("✗", "red"),
    "cancelled": ("⊘", "dim"),
}


class AgentStatusTracker:
    """
    Tracks agent status for live display.

    Subscribes to events and maintains current state.
    """

    def __init__(self):
        self.agents = {}  # agent_id -> {role, status, started_at}
        self.start_time = time.time()
        self._lock = threading.Lock()
        self._subscribed = False

    def subscribe(self):
        """Subscribe to agent events."""
        if self._subscribed:
            return

        try:
            from ...terminal_interface.components.ui_events import (
                EventType,
                get_event_bus,
            )

            bus = get_event_bus()
            bus.subscribe(EventType.AGENT_SPAWN, self._on_spawn)
            bus.subscribe(EventType.AGENT_COMPLETE, self._on_complete)
            bus.subscribe(EventType.AGENT_ERROR, self._on_error)
            self._subscribed = True
        except ImportError:
            pass

    def _on_spawn(self, event):
        with self._lock:
            agent_id = event.data.get("agent_id", "unknown")
            role = event.data.get("role", "custom")
            self.agents[agent_id] = {
                "role": role,
                "status": "running",
                "started_at": time.time(),
            }

    def _on_complete(self, event):
        with self._lock:
            agent_id = event.data.get("agent_id")
            if agent_id and agent_id in self.agents:
                self.agents[agent_id]["status"] = "complete"

    def _on_error(self, event):
        with self._lock:
            agent_id = event.data.get("agent_id")
            if agent_id and agent_id in self.agents:
                self.agents[agent_id]["status"] = "error"
                self.agents[agent_id]["error"] = event.data.get("error", "")

    def render(self) -> Panel:
        """Render current status as a Rich Panel."""
        elapsed = time.time() - self.start_time

        lines = []

        with self._lock:
            if self.agents:
                for _agent_id, info in self.agents.items():
                    role = info["role"]
                    status = info["status"]
                    icon = ROLE_ICONS.get(role.lower(), "🤖")
                    status_icon, status_style = STATUS_ICONS.get(status, ("?", "white"))
                    name = ROLE_NAMES.get(role.lower(), role.title()[:8])

                    # Calculate agent elapsed time
                    agent_elapsed = time.time() - info["started_at"]

                    line = Text()
                    line.append(f"{icon} ", style="bold")
                    line.append(f"{name:<8}", style="white")
                    line.append(f" {status_icon}", style=status_style)
                    line.append(f" {agent_elapsed:.1f}s", style="dim")
                    lines.append(line)
            else:
                lines.append(Text(" starting...", style="dim"))

        # Add total elapsed
        lines.append(Text())
        lines.append(Text(f" total: {elapsed:.1f}s", style="dim"))

        content = Text("\n").join(lines)

        return Panel(
            content,
            title="[dim]agents[/dim]",
            title_align="left",
            border_style="dim",
            width=26,
            padding=(0, 1),
        )


def run_with_live_status(
    orchestrator: "AgentOrchestrator",
    task: str,
    workflow: "WorkflowType",
    auto_apply: bool = False,
    plain_text: bool = False,
) -> "WorkflowResult":
    """
    Run orchestrator with a live updating status panel.

    The panel updates in place - no scrolling.

    Args:
        orchestrator: The agent orchestrator
        task: Task to execute
        workflow: Workflow type
        auto_apply: Auto-apply edits
        plain_text: Skip live display if True

    Returns:
        WorkflowResult from orchestrator
    """
    if plain_text:
        # No live display in plain text mode
        return orchestrator.handle_task(task, workflow=workflow, auto_apply=auto_apply)

    console = Console()
    tracker = AgentStatusTracker()
    tracker.subscribe()

    result = None
    error = None

    def run_task():
        nonlocal result, error
        try:
            result = orchestrator.handle_task(
                task, workflow=workflow, auto_apply=auto_apply
            )
        except Exception as e:
            error = e

    # Run task in background thread
    task_thread = threading.Thread(target=run_task, daemon=True)

    # Start live display
    with Live(
        tracker.render(), console=console, refresh_per_second=4, transient=True
    ) as live:
        task_thread.start()

        # Update display while task runs
        while task_thread.is_alive():
            live.update(tracker.render())
            time.sleep(0.1)

        # Final update
        live.update(tracker.render())

    # Wait for thread to fully complete
    task_thread.join(timeout=1.0)

    if error:
        raise error

    return result
