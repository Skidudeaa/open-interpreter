"""
Spinner Block - Loading indicators for async operations.

Features:
- Multiple spinner types (thinking, executing, loading, analyzing)
- Themed colors matching the Cyber Professional palette
- Start/stop/update functionality
- Success/failure completion indicators
"""

from rich.console import Console
from rich.live import Live
from rich.spinner import Spinner
from rich.table import Table
from rich.text import Text

from .theme import THEME


class SpinnerBlock:
    """
    Loading spinner for LLM calls and code execution.

    Spinner types:
    - thinking: Dots animation (cyan) - for LLM responses
    - executing: Line animation (green) - for code execution
    - loading: Dots2 animation (amber) - for general loading
    - analyzing: Dots12 animation (violet) - for analysis tasks
    """

    SPINNER_CONFIG = {
        "thinking": ("dots", THEME["secondary"], "Thinking"),
        "executing": ("line", THEME["success"], "Executing"),
        "loading": ("dots2", THEME["warning"], "Loading"),
        "analyzing": ("dots12", THEME["primary"], "Analyzing"),
    }

    def __init__(self, spinner_type: str = "thinking", console: Console = None):
        self.console = console or Console()
        self.spinner_type = spinner_type

        # Get spinner configuration
        spinner_name, self.color, self.default_text = self.SPINNER_CONFIG.get(
            spinner_type, self.SPINNER_CONFIG["thinking"]
        )

        self.spinner = Spinner(spinner_name, style=self.color)
        self.text = self.default_text
        self.live = None
        self.is_active = False

    def start(self, text: str = None):
        """
        Start the spinner with optional custom text.

        Args:
            text: Custom text to display (uses default if not provided)
        """
        if text:
            self.text = text

        self.live = Live(
            self._render(),
            console=self.console,
            refresh_per_second=12,
            transient=True,  # Spinner disappears when stopped
        )
        self.live.start()
        self.is_active = True

    def update(self, text: str):
        """
        Update the spinner text while running.

        Args:
            text: New text to display
        """
        self.text = text
        if self.is_active and self.live:
            self.live.update(self._render())

    def stop(self, final_message: str = None, success: bool = True):
        """
        Stop the spinner and optionally show a completion message.

        Args:
            final_message: Message to display after stopping
            success: Whether the operation succeeded (affects icon color)
        """
        if self.live:
            self.live.stop()
            self.is_active = False

        if final_message:
            if success:
                icon = "\u2713"  # Check mark
                color = THEME["success"]
            else:
                icon = "\u2717"  # X mark
                color = THEME["error"]

            self.console.print(f"[{color}]{icon}[/{color}] {final_message}")

    def _render(self) -> Table:
        """Render the spinner with text."""
        table = Table(
            show_header=False,
            box=None,
            padding=(0, 1),
        )
        table.add_column(width=3)
        table.add_column()
        table.add_row(self.spinner, Text(f"{self.text}...", style=self.color))
        return table


class ThinkingSpinner(SpinnerBlock):
    """Convenience class for LLM thinking spinner."""

    def __init__(self, console: Console = None):
        super().__init__(spinner_type="thinking", console=console)


class ExecutingSpinner(SpinnerBlock):
    """Convenience class for code execution spinner."""

    def __init__(self, console: Console = None):
        super().__init__(spinner_type="executing", console=console)


def with_spinner(spinner_type: str = "thinking", text: str = None):
    """
    Context manager for spinner display.

    Usage:
        with with_spinner("thinking", "Processing"):
            # do work
            pass
    """
    class SpinnerContext:
        def __init__(self):
            self.spinner = SpinnerBlock(spinner_type)

        def __enter__(self):
            self.spinner.start(text)
            return self.spinner

        def __exit__(self, exc_type, exc_val, exc_tb):
            if exc_type:
                self.spinner.stop(f"Failed: {exc_val}", success=False)
            else:
                self.spinner.stop()
            return False

    return SpinnerContext()
