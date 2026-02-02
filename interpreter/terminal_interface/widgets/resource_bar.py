"""
ResourceBar Widget - Bottom bar showing token usage and metrics.

ARCHITECTURE: Docked bottom bar with reactive properties for metrics.
Updates from SYSTEM_TOKEN_UPDATE events.

WHY: Always-visible resource tracking prevents context window surprises.
Color-coded thresholds (green/yellow/red) provide at-a-glance status.

TRADEOFF: Takes 1 line of vertical space vs. always knowing resource usage.

Part of Activity Timeline UI feature.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from rich.text import Text
from textual.reactive import reactive
from textual.widgets import Static

from ..components.ui_events import EventType, UIEvent, get_event_bus

if TYPE_CHECKING:
    pass


class ResourceBarWidget(Static):
    """
    Bottom resource bar showing token usage, memory, and elapsed time.

    Features:
    - Token usage with progress bar
    - Color-coded thresholds (green <60%, yellow 60-85%, red >85%)
    - Memory usage
    - Session elapsed time
    - Auto-updates from SYSTEM_TOKEN_UPDATE events

    Display format:
    "Tokens: 1,234 ▮▮▮░░░░░ 28%  │  Memory: 45MB  │  Time: 3.2s"
    """

    DEFAULT_CSS = """
    ResourceBarWidget {
        dock: bottom;
        height: 1;
        background: $surface-darken-1;
        padding: 0 1;
        color: $text;
    }

    ResourceBarWidget.-hidden {
        display: none;
    }
    """

    # Reactive state
    tokens: reactive[int] = reactive(0)
    token_limit: reactive[int] = reactive(128000)
    memory_mb: reactive[float] = reactive(0.0)
    elapsed_seconds: reactive[float] = reactive(0.0)

    def __init__(
        self,
        *,
        name: str | None = None,
        id: str | None = None,
        classes: str | None = None,
    ):
        super().__init__(name=name, id=id, classes=classes)
        self._event_bus = get_event_bus()
        self._session_start = time.time()

        # Timer for updating elapsed time
        self._update_timer = None

    def on_mount(self) -> None:
        """Subscribe to events and start update timer."""
        self._event_bus.subscribe(EventType.SYSTEM_TOKEN_UPDATE, self._on_token_update)
        self._event_bus.subscribe(EventType.SYSTEM_MODEL_CHANGE, self._on_model_change)

        # Update elapsed time every second
        self._update_timer = self.set_interval(1.0, self._update_elapsed)

    def on_unmount(self) -> None:
        """Unsubscribe from events."""
        self._event_bus.unsubscribe(
            EventType.SYSTEM_TOKEN_UPDATE, self._on_token_update
        )
        self._event_bus.unsubscribe(
            EventType.SYSTEM_MODEL_CHANGE, self._on_model_change
        )

        if self._update_timer:
            self._update_timer.stop()

    def _on_token_update(self, event: UIEvent) -> None:
        """Handle token count update."""
        data = event.data
        if "total_tokens" in data:
            self.tokens = data["total_tokens"]
        elif "input_tokens" in data or "output_tokens" in data:
            input_tokens = data.get("input_tokens", 0)
            output_tokens = data.get("output_tokens", 0)
            self.tokens = input_tokens + output_tokens

        if "memory_mb" in data:
            self.memory_mb = data["memory_mb"]

    def _on_model_change(self, event: UIEvent) -> None:
        """Handle model change (update context window size)."""
        context_window = event.data.get("context_window", 128000)
        self.token_limit = context_window

    def _update_elapsed(self) -> None:
        """Update elapsed time display."""
        self.elapsed_seconds = time.time() - self._session_start
        self.refresh()

    def render(self) -> Text:
        """Render the resource bar content."""
        text = Text()

        # Calculate token percentage
        pct = (self.tokens / self.token_limit * 100) if self.token_limit > 0 else 0

        # Progress bar (10 chars)
        bar_fill = int(pct / 10)
        bar_fill = min(bar_fill, 10)  # Cap at 10
        bar = "▮" * bar_fill + "░" * (10 - bar_fill)

        # Color based on threshold
        if pct < 60:
            color = "green"
        elif pct < 85:
            color = "yellow"
        else:
            color = "red"

        # Tokens section
        text.append("Tokens: ", style="dim")
        text.append(f"{self.tokens:,}", style=color)
        text.append(f" {bar} ", style=color)
        text.append(f"{pct:.0f}%", style=color)

        # Separator
        text.append("  │  ", style="dim")

        # Memory section
        text.append("Memory: ", style="dim")
        text.append(f"{self.memory_mb:.0f}MB", style="cyan")

        # Separator
        text.append("  │  ", style="dim")

        # Time section
        text.append("Time: ", style="dim")
        text.append(self._format_elapsed(), style="cyan")

        return text

    def _format_elapsed(self) -> str:
        """Format elapsed time nicely."""
        secs = self.elapsed_seconds
        if secs < 60:
            return f"{secs:.1f}s"
        elif secs < 3600:
            mins = int(secs / 60)
            secs = int(secs % 60)
            return f"{mins}m {secs}s"
        else:
            hours = int(secs / 3600)
            mins = int((secs % 3600) / 60)
            return f"{hours}h {mins}m"

    def reset_session(self) -> None:
        """Reset session start time and tokens."""
        self._session_start = time.time()
        self.tokens = 0
        self.elapsed_seconds = 0.0
        self.refresh()

    def set_tokens(self, count: int) -> None:
        """Set token count directly."""
        self.tokens = count
        self.refresh()

    def set_token_limit(self, limit: int) -> None:
        """Set token limit directly."""
        self.token_limit = limit
        self.refresh()
