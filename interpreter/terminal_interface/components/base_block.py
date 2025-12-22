"""
Base class for terminal visual blocks.

Provides shared console, timing tracking, and live display functionality.
Uses lazy initialization of Live display to prevent context conflicts.
"""

import time
import threading
from rich.console import Console
from rich.live import Live

from .theme import THEME


class BaseBlock:
    """
    A visual "block" on the terminal.

    Features:
    - Shared console singleton for consistent rendering
    - Timing tracking for execution duration
    - Rich Live display with lazy initialization (starts on first refresh)
    - Thread-safe Live management
    """

    _shared_console = None
    _console_lock = threading.Lock()

    def __init__(self):
        self.theme = THEME
        self.start_time = time.time()
        self._live = None  # Lazy initialization - don't start until needed
        self._live_started = False
        self._live_lock = threading.Lock()

    def _ensure_live(self) -> bool:
        """
        Ensure Live display is started. Returns True if Live is available.
        Uses lazy initialization to avoid conflicts with other Live contexts.
        """
        if self._live_started:
            return self._live is not None

        with self._live_lock:
            # Double-check after acquiring lock
            if self._live_started:
                return self._live is not None

            try:
                self._live = Live(
                    auto_refresh=False,
                    console=self.get_console(),
                    vertical_overflow="visible"
                )
                self._live.start()
                self._live_started = True
                return True
            except Exception:
                # If Live display fails, mark as started but unavailable
                self._live = None
                self._live_started = True
                return False

    @property
    def live(self):
        """
        Get the Live display instance (lazy initialization).
        For backward compatibility with code that accesses self.live directly.
        """
        self._ensure_live()
        return self._live

    @live.setter
    def live(self, value):
        """Allow setting live directly for backward compatibility."""
        with self._live_lock:
            self._live = value
            self._live_started = value is not None

    @classmethod
    def get_console(cls) -> Console:
        """Get or create the shared Console instance."""
        with cls._console_lock:
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
        """End the live display safely."""
        with self._live_lock:
            if self._live is not None:
                try:
                    self.refresh(cursor=False)
                except Exception:
                    pass  # Ignore refresh errors during cleanup
                try:
                    self._live.stop()
                except Exception:
                    pass  # Ignore stop errors (may already be stopped)
                self._live = None

    def refresh(self, cursor=True):
        """Refresh the display. Subclasses must implement."""
        raise NotImplementedError("Subclasses must implement this method")
