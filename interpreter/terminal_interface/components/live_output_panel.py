"""
Live Output Panel - Contained, non-scrolling output display.

Critical UX fix: During code execution, output streams in real-time.
Instead of scrolling thousands of lines, this panel:
1. Shows only the last N visible lines
2. Displays total line count
3. Updates in-place using Rich's Live display
4. Buffers all output for post-execution access
"""

from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel
from rich.text import Text

from .theme import THEME, BOX_STYLES


class LiveOutputPanel:
    """
    A fixed-height output viewport that prevents scroll overflow.

    Features:
    - Shows last N lines in a contained viewport
    - Displays total line count: "📜 142 lines (showing last 10)"
    - Updates in-place without terminal scrolling
    - Buffers all lines for later retrieval
    """

    MAX_VISIBLE_LINES = 8
    SCROLL_ICON = "\U0001F4DC"  # Scroll emoji

    def __init__(self, console: Console = None):
        self.console = console or Console()
        self.lines: list[str] = []
        self.live: Live = None
        self.is_active = False

    def start(self):
        """Start the live output display."""
        if not self.is_active:
            self.live = Live(
                self._render(),
                console=self.console,
                refresh_per_second=10,
                vertical_overflow="visible",
            )
            self.live.start()
            self.is_active = True

    def stop(self):
        """Stop the live output display."""
        if self.is_active and self.live:
            self.live.stop()
            self.is_active = False

    def add_output(self, text: str):
        """
        Add output text, splitting into lines.

        Args:
            text: Raw output text (may contain newlines)
        """
        if not text:
            return

        # Split text into lines and add each
        new_lines = text.split("\n")
        for line in new_lines:
            self.lines.append(line)

        self._refresh()

    def add_line(self, line: str):
        """Add a single line of output."""
        self.lines.append(line)
        self._refresh()

    def clear(self):
        """Clear all buffered output."""
        self.lines = []
        self._refresh()

    def get_all_output(self) -> str:
        """Get all buffered output as a single string."""
        return "\n".join(self.lines)

    def get_line_count(self) -> int:
        """Get total number of output lines."""
        return len(self.lines)

    def _refresh(self):
        """Refresh the live display with current content."""
        if self.is_active and self.live:
            self.live.update(self._render())

    def _render(self) -> Panel:
        """Render the output panel with visible lines."""
        total_lines = len(self.lines)

        # Get visible lines (last N)
        if total_lines <= self.MAX_VISIBLE_LINES:
            visible_lines = self.lines
            showing = total_lines
        else:
            visible_lines = self.lines[-self.MAX_VISIBLE_LINES:]
            showing = self.MAX_VISIBLE_LINES

        # Build content
        if visible_lines:
            content = "\n".join(visible_lines)
        else:
            content = "[dim]No output yet...[/dim]"

        # Build header with line count
        if total_lines > 0:
            if total_lines > self.MAX_VISIBLE_LINES:
                title = f"{self.SCROLL_ICON} Output ({total_lines} lines, showing last {showing})"
            else:
                title = f"{self.SCROLL_ICON} Output ({total_lines} lines)"
        else:
            title = f"{self.SCROLL_ICON} Output"

        # Create panel
        return Panel(
            Text(content),
            title=title,
            title_align="left",
            box=BOX_STYLES["output"],
            style=f"on {THEME['bg_light']}",
            border_style=THEME["text_muted"],
            padding=(0, 1),
        )

    def render_static(self) -> Panel:
        """
        Render a static (non-live) panel for final display.

        Use this after stopping the live panel to show the final state.
        """
        return self._render()


class OutputBuffer:
    """
    Simple output buffer without live display.

    Use this for collecting output during execution,
    then render once at the end.
    """

    def __init__(self, max_display_lines: int = 20):
        self.lines: list[str] = []
        self.max_display_lines = max_display_lines

    def add(self, text: str):
        """Add output text."""
        if text:
            self.lines.extend(text.split("\n"))

    def get_display_text(self) -> str:
        """Get text for display, with truncation indicator if needed."""
        total = len(self.lines)

        if total <= self.max_display_lines:
            return "\n".join(self.lines)

        # Truncate
        visible = self.lines[-self.max_display_lines:]
        hidden = total - self.max_display_lines
        header = f"[dim]... {hidden} earlier lines hidden ...[/dim]\n"
        return header + "\n".join(visible)

    def get_summary(self) -> str:
        """Get summary of buffered output."""
        return f"{len(self.lines)} lines of output"

    def clear(self):
        """Clear the buffer."""
        self.lines = []
