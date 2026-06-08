"""
Base class for terminal visual blocks.

Provides shared console, timing tracking, and live display functionality.
Uses lazy initialization of Live display to prevent context conflicts.
"""

import threading
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
    - Rich Live display with lazy initialization (starts on first refresh)
    - Thread-safe Live management
    """

    _shared_console = None
    _console_lock = threading.Lock()

    # Rich permits only ONE Live display at a time per console. Blocks are
    # normally start→end→start, but an alternate execution backend (e.g. the
    # Hermes ACP backend) can emit back-to-back tool calls whose blocks overlap
    # before the previous one is ended. To keep Rich happy, all blocks share a
    # single "live slot": starting a block's Live flushes+stops whichever block
    # currently holds it. Reentrant so end()/cancel() can clear it while held.
    _active_live_block = None
    _active_live_lock = threading.RLock()

    def __init__(self):
        self.theme = THEME
        self.start_time = time.time()
        self._live = None  # Lazy initialization - don't start until needed
        self._live_started = False
        self._live_failed = False  # Track if init failed (allows retry)
        self._live_lock = threading.Lock()
        self._fallback_printed = False  # Track if we've already printed fallback
        self._render_mode = None  # 'live' or 'fallback' - once set, don't switch

    def _ensure_live(self) -> bool:
        """
        Ensure Live display is started. Returns True if Live is available.
        Uses lazy initialization to avoid conflicts with other Live contexts.
        Retries on transient failures (doesn't permanently lock out).
        """
        if self._live_started and self._live is not None:
            return True

        # Free Rich's single Live slot: stop any other block that still holds it.
        self._claim_live_slot()

        with self._live_lock:
            # Double-check after acquiring lock
            if self._live_started and self._live is not None:
                return True

            try:
                self._live = Live(
                    auto_refresh=False,
                    console=self.get_console(),
                    vertical_overflow="visible",
                )
                self._live.start()
                self._live_started = True
                self._live_failed = False
                return True
            except Exception as e:
                # Transient failure - don't lock out permanently, allow retry
                self._live = None
                self._live_failed = True
                # Print error so user knows something is wrong
                import sys

                print(f"[UI] Live display failed: {e}", file=sys.stderr)
                return False

    def _claim_live_slot(self):
        """Take ownership of Rich's single Live slot, stopping the prior holder.

        If another block currently owns the live slot, flush+stop it first so its
        final content lands in scrollback and Rich's one-Live constraint is met.
        """
        with BaseBlock._active_live_lock:
            prev = BaseBlock._active_live_block
            if prev is not None and prev is not self:
                try:
                    prev.end()
                except Exception:
                    pass
            BaseBlock._active_live_block = self

    def _release_live_slot(self):
        """Relinquish the live slot if this block currently owns it."""
        with BaseBlock._active_live_lock:
            if BaseBlock._active_live_block is self:
                BaseBlock._active_live_block = None

    def fallback_print(self, content, force: bool = False):
        """Print content directly when Live display isn't available.

        Only prints once during streaming to avoid spam. Use force=True for final render.
        """
        if self._fallback_printed and not force:
            return
        self._fallback_printed = True
        console = self.get_console()
        console.print(content)

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
            self._live_failed = False

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
        import logging

        logger = logging.getLogger(__name__)

        self._release_live_slot()

        with self._live_lock:
            if self._live is not None:
                try:
                    self.refresh(cursor=False)
                except Exception as e:
                    logger.debug(f"BaseBlock.end: refresh failed: {e}")
                try:
                    self._live.stop()
                except Exception as e:
                    logger.debug(
                        f"BaseBlock.end: stop failed (may already be stopped): {e}"
                    )
                self._live = None
            elif self._live_failed:
                # If Live failed, do a final fallback print
                try:
                    self._fallback_printed = False  # Reset to force final print
                    self.refresh(cursor=False)
                except Exception as e:
                    logger.debug(f"BaseBlock.end: fallback refresh failed: {e}")

    def cancel(self):
        """Cancel this block without rendering (for empty content)."""
        self._release_live_slot()
        with self._live_lock:
            if self._live is not None:
                try:
                    self._live.stop()
                except Exception:
                    pass  # Ignore errors on cancel
                self._live = None
            self._live_started = False

    def refresh(self, cursor=True):
        """Refresh the display. Subclasses must implement."""
        raise NotImplementedError("Subclasses must implement this method")
