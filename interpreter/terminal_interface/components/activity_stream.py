"""
Activity Stream - Shows what the agent is currently working on.

Provides visibility into agent actions without requiring user control.
Displays a rolling window of recent activities with context.

WHY: Users reported feeling disconnected from what the agent is doing.
The agent knows its intent but wasn't surfacing it to the user.
This component bridges that gap.

ARCHITECTURE: Event-driven display that subscribes to ACTIVITY events.
Activities are categorized by type (search, read, plan, edit, execute)
and shown with contextual icons and descriptions.
"""

import time
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING

from rich.console import Console
from rich.live import Live
from rich.panel import Panel
from rich.text import Text

from .theme import BOX_STYLES, THEME
from .ui_events import EventType, UIEvent, get_event_bus

if TYPE_CHECKING:
    pass


class ActivityType(Enum):
    """Types of activities the agent can perform."""

    THINK = "think"  # LLM is reasoning/planning
    SEARCH = "search"  # Searching for files/code
    READ = "read"  # Reading a file
    PLAN = "plan"  # Planning an edit or action
    EDIT = "edit"  # Editing a file
    EXECUTE = "execute"  # Running code
    VALIDATE = "validate"  # Checking/testing
    WAIT = "wait"  # Waiting for user/external


# Icons for each activity type
ACTIVITY_ICONS = {
    ActivityType.THINK: "💭",
    ActivityType.SEARCH: "🔍",
    ActivityType.READ: "📄",
    ActivityType.PLAN: "📋",
    ActivityType.EDIT: "✏️",
    ActivityType.EXECUTE: "⚡",
    ActivityType.VALIDATE: "✅",
    ActivityType.WAIT: "⏳",
}

# Colors for each activity type
ACTIVITY_COLORS = {
    ActivityType.THINK: "cyan",
    ActivityType.SEARCH: "yellow",
    ActivityType.READ: "blue",
    ActivityType.PLAN: "magenta",
    ActivityType.EDIT: "green",
    ActivityType.EXECUTE: "bright_green",
    ActivityType.VALIDATE: "bright_cyan",
    ActivityType.WAIT: "dim",
}


@dataclass
class Activity:
    """A single activity entry."""

    activity_type: ActivityType
    message: str
    context: str = ""  # Additional context (file path, search query, etc.)
    timestamp: float = field(default_factory=time.time)
    agent: str = ""  # Which agent is performing this (empty = main LLM)

    @property
    def age_seconds(self) -> float:
        return time.time() - self.timestamp

    @property
    def age_display(self) -> str:
        """Human-readable age."""
        age = self.age_seconds
        if age < 1:
            return "now"
        elif age < 60:
            return f"{int(age)}s"
        else:
            return f"{int(age / 60)}m"


class ActivityStream:
    """
    Real-time activity stream showing what the agent is doing.

    Maintains a rolling window of recent activities and displays them
    in a compact format. Subscribes to ACTIVITY events from the event bus.

    Usage:
        stream = ActivityStream()
        stream.start()  # Begin displaying
        # ... agent runs ...
        stream.stop()

    Or emit activities directly:
        stream.add_activity(ActivityType.SEARCH, "Finding auth files", "src/auth/*")
    """

    def __init__(
        self,
        max_activities: int = 5,
        console: Console | None = None,
        show_timestamps: bool = True,
        inline_mode: bool = True,  # Print each activity inline instead of Live display
    ):
        self.max_activities = max_activities
        self.console = console or Console()
        self.show_timestamps = show_timestamps
        self.inline_mode = inline_mode

        self._activities: deque[Activity] = deque(maxlen=max_activities)
        self._live: Live | None = None
        self._running = False
        self._subscribed = False
        self._last_activity_text = ""  # For deduplication

    def add_activity(
        self,
        activity_type: ActivityType,
        message: str,
        context: str = "",
        agent: str = "",
    ) -> None:
        """Add a new activity to the stream."""
        activity = Activity(
            activity_type=activity_type,
            message=message,
            context=context,
            agent=agent,
        )
        self._activities.append(activity)

        if self.inline_mode:
            self._print_activity_inline(activity)
        else:
            self._refresh()

    def _print_activity_inline(self, activity: Activity) -> None:
        """Print a single activity inline (for non-Live mode)."""
        icon = ACTIVITY_ICONS.get(activity.activity_type, "•")
        color = ACTIVITY_COLORS.get(activity.activity_type, "white")

        # Build the activity text
        parts = []
        if activity.agent:
            parts.append(f"[dim]{activity.agent}:[/dim] ")
        parts.append(f"[{color}]{icon} {activity.message}[/{color}]")
        if activity.context:
            ctx = activity.context
            if len(ctx) > 40:
                ctx = ctx[:37] + "..."
            parts.append(f" [dim]({ctx})[/dim]")

        activity_text = "".join(parts)

        # Deduplicate - don't print the same activity twice
        if activity_text == self._last_activity_text:
            return
        self._last_activity_text = activity_text

        # Print with visual distinction
        self.console.print(f"  [dim]▸[/dim] {activity_text}")

    def clear(self) -> None:
        """Clear all activities."""
        self._activities.clear()
        self._refresh()

    def _handle_event(self, event: UIEvent) -> None:
        """Handle ACTIVITY events from the event bus."""
        if event.type != EventType.ACTIVITY:
            return

        data = event.data
        try:
            activity_type = ActivityType(data.get("activity_type", "think"))
        except ValueError:
            activity_type = ActivityType.THINK

        self.add_activity(
            activity_type=activity_type,
            message=data.get("message", ""),
            context=data.get("context", ""),
            agent=data.get("agent", ""),
        )

    def _render(self) -> Panel | Text:
        """Render the activity stream."""
        if not self._activities:
            return Text("")

        lines = []
        for activity in self._activities:
            icon = ACTIVITY_ICONS.get(activity.activity_type, "•")
            color = ACTIVITY_COLORS.get(activity.activity_type, "white")

            line = Text()

            # Timestamp (optional)
            if self.show_timestamps:
                line.append(f"[{activity.age_display}] ", style="dim")

            # Agent prefix (if from a specific agent)
            if activity.agent:
                line.append(f"{activity.agent}: ", style="dim italic")

            # Icon and message
            line.append(f"{icon} ", style=color)
            line.append(activity.message, style=color)

            # Context (truncated if long)
            if activity.context:
                ctx = activity.context
                if len(ctx) > 40:
                    ctx = ctx[:37] + "..."
                line.append(f" ({ctx})", style="dim")

            lines.append(line)

        # Join lines
        content = Text()
        for i, line in enumerate(lines):
            if i > 0:
                content.append("\n")
            content.append_text(line)

        return Panel(
            content,
            box=BOX_STYLES.get("minimal", BOX_STYLES.get("status")),
            style=f"on {THEME.get('bg_dark', 'black')}",
            border_style=THEME.get("text_muted", "dim"),
            padding=(0, 1),
            title="[dim]activity[/dim]",
            title_align="left",
        )

    def _refresh(self) -> None:
        """Refresh the live display."""
        if self._live and self._running:
            try:
                self._live.update(self._render())
            except Exception:
                pass  # Ignore render errors during shutdown

    def _subscribe(self) -> None:
        """Subscribe to ACTIVITY events."""
        if self._subscribed:
            return
        event_bus = get_event_bus()
        event_bus.subscribe(EventType.ACTIVITY, self._handle_event)
        self._subscribed = True

    def _unsubscribe(self) -> None:
        """Unsubscribe from events."""
        if not self._subscribed:
            return
        event_bus = get_event_bus()
        event_bus.unsubscribe(EventType.ACTIVITY, self._handle_event)
        self._subscribed = False

    def start(self) -> None:
        """Start the live activity stream display."""
        if self._running:
            return

        self._running = True
        self._subscribe()

        self._live = Live(
            self._render(),
            console=self.console,
            refresh_per_second=4,
            transient=True,
        )
        self._live.start()

    def stop(self) -> None:
        """Stop the activity stream display."""
        if not self._running:
            return

        self._running = False
        self._unsubscribe()

        if self._live:
            try:
                self._live.stop()
            except Exception:
                pass
            self._live = None

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.stop()
        return False


# Convenience functions for emitting activities


def emit_activity(
    activity_type: str | ActivityType,
    message: str,
    context: str = "",
    agent: str = "",
) -> None:
    """
    Emit an activity event to the event bus.

    This is the primary way to add activities from anywhere in the codebase.

    Args:
        activity_type: Type of activity (think, search, read, plan, edit, execute, validate, wait)
        message: What is happening
        context: Additional context (file path, search query, etc.)
        agent: Which agent is performing this (empty = main LLM)

    Example:
        emit_activity("search", "Finding authentication files", "src/auth/*")
        emit_activity("read", "Analyzing login handler", "src/auth/login.py")
        emit_activity("plan", "Will add rate limiting to login()")
    """
    if isinstance(activity_type, ActivityType):
        activity_type = activity_type.value

    event_bus = get_event_bus()
    event_bus.emit(
        UIEvent(
            type=EventType.ACTIVITY,
            data={
                "activity_type": activity_type,
                "message": message,
                "context": context,
                "agent": agent,
            },
            source="activity",
        )
    )


def activity_think(message: str, context: str = "", agent: str = "") -> None:
    """Emit a thinking activity."""
    emit_activity(ActivityType.THINK, message, context, agent)


def activity_search(message: str, context: str = "", agent: str = "") -> None:
    """Emit a search activity."""
    emit_activity(ActivityType.SEARCH, message, context, agent)


def activity_read(message: str, context: str = "", agent: str = "") -> None:
    """Emit a read activity."""
    emit_activity(ActivityType.READ, message, context, agent)


def activity_plan(message: str, context: str = "", agent: str = "") -> None:
    """Emit a plan activity."""
    emit_activity(ActivityType.PLAN, message, context, agent)


def activity_edit(message: str, context: str = "", agent: str = "") -> None:
    """Emit an edit activity."""
    emit_activity(ActivityType.EDIT, message, context, agent)


def activity_execute(message: str, context: str = "", agent: str = "") -> None:
    """Emit an execute activity."""
    emit_activity(ActivityType.EXECUTE, message, context, agent)


def activity_validate(message: str, context: str = "", agent: str = "") -> None:
    """Emit a validate activity."""
    emit_activity(ActivityType.VALIDATE, message, context, agent)


def activity_wait(message: str, context: str = "", agent: str = "") -> None:
    """Emit a wait activity."""
    emit_activity(ActivityType.WAIT, message, context, agent)
