"""
Context Meter - Token usage display.

Shows current token usage as a progress bar with percentage.
Color shifts from green → yellow → red as context fills.
Reads from UIState.context_tokens and context_limit.

Part of Phase 2: Agent Visualization
"""

from typing import Optional
from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, BarColumn, TextColumn
from rich.text import Text
from rich.table import Table

from .ui_state import UIState
from .theme import THEME, BOX_STYLES


class ContextMeter:
    """
    Token usage display with color-coded progress bar.

    Layout:
    ┌─────────────────────────────────────────┐
    │ [████████░░] 78% (32k/41k tokens)       │
    └─────────────────────────────────────────┘

    Colors:
    - 0-60%: Green (safe)
    - 60-85%: Yellow (warning)
    - 85-100%: Red (critical)
    """

    def __init__(self, state: UIState, console: Console = None):
        """
        Initialize the context meter.

        Args:
            state: The UIState instance
            console: Optional console to use
        """
        self.state = state
        self.console = console or Console()

    def get_usage_color(self, percent: float) -> str:
        """
        Get color based on usage percentage.

        Args:
            percent: Usage percentage (0-100)

        Returns:
            Theme color key
        """
        if percent < 60:
            return "success"  # Green
        elif percent < 85:
            return "warning"  # Yellow
        else:
            return "error"    # Red

    def render(self) -> Text:
        """
        Render the context meter as a Text object.

        Returns:
            Text with the meter display
        """
        tokens = self.state.context_tokens
        limit = self.state.context_limit
        percent = self.state.context_usage_percent

        # Build the meter text
        meter = Text()

        # Progress bar
        bar_width = 10
        filled = int((percent / 100) * bar_width)
        empty = bar_width - filled

        # Get color based on usage
        color_key = self.get_usage_color(percent)
        color = THEME[color_key]

        # Build bar
        meter.append("[", style="dim")
        meter.append("█" * filled, style=color)
        meter.append("░" * empty, style="dim")
        meter.append("]", style="dim")

        # Percentage
        meter.append(f" {percent:.0f}%", style=color)

        # Token counts (with K formatting for readability)
        tokens_k = self._format_token_count(tokens)
        limit_k = self._format_token_count(limit)
        meter.append(f" ({tokens_k}/{limit_k} tokens)", style="dim")

        return meter

    def render_panel(self) -> Panel:
        """
        Render as a standalone panel (for testing/standalone use).

        Returns:
            Panel with the meter
        """
        content = self.render()

        return Panel(
            content,
            title="📊 Context Usage",
            title_align="left",
            box=BOX_STYLES["status"],
            style=f"on {THEME['bg_dark']}",
            border_style=THEME["text_muted"],
            padding=(0, 1),
        )

    def _format_token_count(self, count: int) -> str:
        """
        Format token count with K/M suffix.

        Args:
            count: Token count

        Returns:
            Formatted string (e.g., "32k", "1.2M")
        """
        if count < 1000:
            return str(count)
        elif count < 1_000_000:
            return f"{count / 1000:.0f}k"
        else:
            return f"{count / 1_000_000:.1f}M"

    def display(self):
        """Print the context meter panel to the console."""
        self.console.print(self.render_panel())

    def get_summary(self) -> str:
        """
        Get a plain text summary of context usage.

        Returns:
            String like "78% (32k/41k tokens)"
        """
        percent = self.state.context_usage_percent
        tokens_k = self._format_token_count(self.state.context_tokens)
        limit_k = self._format_token_count(self.state.context_limit)
        return f"{percent:.0f}% ({tokens_k}/{limit_k} tokens)"

    def is_critical(self) -> bool:
        """
        Check if context usage is critical (>85%).

        Returns:
            True if usage is critical
        """
        return self.state.context_usage_percent >= 85

    def is_warning(self) -> bool:
        """
        Check if context usage is in warning range (60-85%).

        Returns:
            True if usage is in warning range
        """
        percent = self.state.context_usage_percent
        return 60 <= percent < 85

    def get_remaining_tokens(self) -> int:
        """
        Get the number of remaining tokens.

        Returns:
            Remaining token count
        """
        return max(0, self.state.context_limit - self.state.context_tokens)

    def get_remaining_percent(self) -> float:
        """
        Get the percentage of remaining context.

        Returns:
            Remaining percentage (0-100)
        """
        return 100 - self.state.context_usage_percent


def display_context_meter(state: UIState, console: Console = None):
    """
    Convenience function to display the context meter.

    Args:
        state: The UIState instance
        console: Optional console to use
    """
    ContextMeter(state, console).display()
