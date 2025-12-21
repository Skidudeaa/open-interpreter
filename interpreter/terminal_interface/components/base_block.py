"""
Base class for terminal visual blocks.

Provides shared console, timing tracking, and live display functionality.
"""

import time
from rich.console import Console
from rich.live import Live

from .theme import THEME


class BaseBlock:
    """
    A visual "block" on the terminal.

    Features:
    - Shared console singleton for consistent rendering
    - Timing tracking for execution duration
    - Rich Live display with manual refresh control
    """

    _shared_console = None

    def __init__(self):
        self.theme = THEME
        self.start_time = None
        self.live = Live(
            auto_refresh=False,
            console=self.get_console(),
            vertical_overflow="visible"
        )
        self.live.start()
        self.start_time = time.time()

    @classmethod
    def get_console(cls) -> Console:
        """Get or create the shared Console instance."""
        if cls._shared_console is None:
            cls._shared_console = Console(
                force_terminal=True,
                color_system="truecolor",
                highlight=False,
            )
        return cls._shared_console

    def get_elapsed(self) -> float:
        """Get elapsed time since block started."""
        if self.start_time:
            return time.time() - self.start_time
        return 0.0

    def get_elapsed_str(self) -> str:
        """Get formatted elapsed time string."""
        elapsed = self.get_elapsed()
        if elapsed < 1:
            return f"{elapsed:.2f}s"
        elif elapsed < 60:
            return f"{elapsed:.1f}s"
        else:
            mins = int(elapsed // 60)
            secs = int(elapsed % 60)
            return f"{mins}m {secs}s"

    def update_from_message(self, message):
        """Update block content from a message. Subclasses must implement."""
        raise NotImplementedError("Subclasses must implement this method")

    def end(self):
        """End the live display."""
        self.refresh(cursor=False)
        self.live.stop()

    def refresh(self, cursor=True):
        """Refresh the display. Subclasses must implement."""
        raise NotImplementedError("Subclasses must implement this method")
