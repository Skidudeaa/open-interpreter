"""
Toast Notifications - Ephemeral messages for mode changes and status updates.

Displays brief, non-blocking notifications that auto-dismiss after a timeout.
Used for mode transitions, agent status, and system messages.

Part of Phase 4: Adaptive Mode System
"""

from dataclasses import dataclass, field
from typing import Optional, List, Callable
from enum import Enum, auto
import time
import threading

from rich.console import Console
from rich.panel import Panel
from rich.text import Text
from rich.style import Style

from .theme import THEME


class ToastLevel(Enum):
    """Toast notification severity levels"""
    INFO = auto()      # Neutral information
    SUCCESS = auto()   # Operation completed successfully
    WARNING = auto()   # Attention needed
    ERROR = auto()     # Error occurred
    MODE = auto()      # Mode change notification


@dataclass
class Toast:
    """A single toast notification"""
    message: str
    level: ToastLevel = ToastLevel.INFO
    duration: float = 3.0  # seconds
    created_at: float = field(default_factory=time.time)
    icon: Optional[str] = None

    @property
    def is_expired(self) -> bool:
        """Check if toast has expired."""
        return time.time() - self.created_at > self.duration

    @property
    def remaining_seconds(self) -> float:
        """Seconds remaining until expiry."""
        return max(0, self.duration - (time.time() - self.created_at))

    def get_icon(self) -> str:
        """Get icon for toast level."""
        if self.icon:
            return self.icon
        return {
            ToastLevel.INFO: "ℹ",
            ToastLevel.SUCCESS: "✓",
            ToastLevel.WARNING: "⚠",
            ToastLevel.ERROR: "✗",
            ToastLevel.MODE: "◐",
        }.get(self.level, "•")

    def get_style(self) -> str:
        """Get Rich style for toast level."""
        return {
            ToastLevel.INFO: THEME.get("secondary", "cyan"),
            ToastLevel.SUCCESS: THEME.get("success", "green"),
            ToastLevel.WARNING: THEME.get("warning", "yellow"),
            ToastLevel.ERROR: THEME.get("error", "red"),
            ToastLevel.MODE: THEME.get("primary", "magenta"),
        }.get(self.level, "white")


class ToastManager:
    """
    Manages toast notification display and lifecycle.

    Features:
    - Queue multiple toasts
    - Auto-dismiss after timeout
    - Stack display (newest on top)
    - Rate limiting to prevent spam
    - Optional animation (fade out)

    Usage:
        manager = ToastManager(console)
        manager.show("Mode → POWER", level=ToastLevel.MODE)
        manager.show("Agent complete", level=ToastLevel.SUCCESS)
    """

    MAX_VISIBLE = 3  # Maximum toasts shown at once
    MIN_INTERVAL = 0.5  # Minimum seconds between toasts

    def __init__(self, console: Optional[Console] = None):
        """
        Initialize the toast manager.

        Args:
            console: Rich Console instance (creates one if not provided)
        """
        self.console = console or Console()
        self._toasts: List[Toast] = []
        self._last_show_time = 0.0
        self._lock = threading.Lock()

        # Callbacks
        self._on_show: Optional[Callable[[Toast], None]] = None
        self._on_dismiss: Optional[Callable[[Toast], None]] = None

        # Display state
        self._enabled = True
        self._position = "top-right"  # top-right, top-left, bottom-right, bottom-left

    @property
    def active_toasts(self) -> List[Toast]:
        """Get list of non-expired toasts."""
        self._cleanup_expired()
        return self._toasts[:self.MAX_VISIBLE]

    @property
    def toast_count(self) -> int:
        """Get count of active toasts."""
        return len(self.active_toasts)

    def show(
        self,
        message: str,
        level: ToastLevel = ToastLevel.INFO,
        duration: float = 3.0,
        icon: Optional[str] = None,
    ) -> Optional[Toast]:
        """
        Show a toast notification.

        Args:
            message: The message to display
            level: ToastLevel for styling
            duration: Seconds until auto-dismiss
            icon: Optional custom icon

        Returns:
            Toast instance if shown, None if rate-limited or disabled
        """
        if not self._enabled:
            return None

        # Rate limiting
        now = time.time()
        if now - self._last_show_time < self.MIN_INTERVAL:
            return None

        with self._lock:
            toast = Toast(
                message=message,
                level=level,
                duration=duration,
                icon=icon,
            )
            self._toasts.insert(0, toast)  # Newest first
            self._last_show_time = now

            # Trim excess toasts
            while len(self._toasts) > self.MAX_VISIBLE * 2:
                self._toasts.pop()

        # Callback
        if self._on_show:
            self._on_show(toast)

        return toast

    def show_mode_change(self, from_mode: str, to_mode: str, reason: str = "") -> Toast:
        """
        Show a mode change notification.

        Args:
            from_mode: Previous mode name
            to_mode: New mode name
            reason: Reason for change

        Returns:
            Toast instance
        """
        msg = f"Mode → {to_mode}"
        if reason:
            msg += f" ({reason})"
        return self.show(msg, level=ToastLevel.MODE, duration=2.5)

    def show_success(self, message: str, duration: float = 2.0) -> Toast:
        """Show a success toast."""
        return self.show(message, level=ToastLevel.SUCCESS, duration=duration)

    def show_error(self, message: str, duration: float = 4.0) -> Toast:
        """Show an error toast."""
        return self.show(message, level=ToastLevel.ERROR, duration=duration)

    def show_warning(self, message: str, duration: float = 3.0) -> Toast:
        """Show a warning toast."""
        return self.show(message, level=ToastLevel.WARNING, duration=duration)

    def show_info(self, message: str, duration: float = 2.5) -> Toast:
        """Show an info toast."""
        return self.show(message, level=ToastLevel.INFO, duration=duration)

    def dismiss(self, toast: Toast):
        """Manually dismiss a toast."""
        with self._lock:
            if toast in self._toasts:
                self._toasts.remove(toast)
                if self._on_dismiss:
                    self._on_dismiss(toast)

    def dismiss_all(self):
        """Dismiss all toasts."""
        with self._lock:
            for toast in self._toasts:
                if self._on_dismiss:
                    self._on_dismiss(toast)
            self._toasts.clear()

    def _cleanup_expired(self):
        """Remove expired toasts."""
        with self._lock:
            expired = [t for t in self._toasts if t.is_expired]
            for toast in expired:
                self._toasts.remove(toast)
                if self._on_dismiss:
                    self._on_dismiss(toast)

    # Rendering

    def render(self) -> Optional[Panel]:
        """
        Render active toasts as a Rich Panel.

        Returns:
            Rich Panel with toast stack, or None if no active toasts
        """
        toasts = self.active_toasts
        if not toasts:
            return None

        # Build content
        content = Text()
        for i, toast in enumerate(toasts):
            if i > 0:
                content.append("\n")

            icon = toast.get_icon()
            style = toast.get_style()

            content.append(f" {icon} ", style=f"bold {style}")
            content.append(toast.message, style=style)

            # Fade effect based on remaining time
            remaining = toast.remaining_seconds
            if remaining < 1.0:
                content.stylize("dim", len(content) - len(toast.message) - 4)

        return Panel(
            content,
            box=None,
            padding=(0, 1),
            style=f"on {THEME.get('bg_medium', '#1a1a2e')}",
        )

    def render_inline(self) -> Optional[Text]:
        """
        Render toasts as inline text (for status bar integration).

        Returns:
            Rich Text with most recent toast, or None
        """
        toasts = self.active_toasts
        if not toasts:
            return None

        toast = toasts[0]  # Most recent
        icon = toast.get_icon()
        style = toast.get_style()

        text = Text()
        text.append(f"{icon} ", style=f"bold {style}")
        text.append(toast.message, style=style)

        return text

    # Configuration

    def enable(self):
        """Enable toast notifications."""
        self._enabled = True

    def disable(self):
        """Disable toast notifications."""
        self._enabled = False
        self.dismiss_all()

    def set_position(self, position: str):
        """Set toast display position."""
        valid = ["top-right", "top-left", "bottom-right", "bottom-left"]
        if position in valid:
            self._position = position

    def set_show_handler(self, handler: Callable[[Toast], None]):
        """Set callback for toast show events."""
        self._on_show = handler

    def set_dismiss_handler(self, handler: Callable[[Toast], None]):
        """Set callback for toast dismiss events."""
        self._on_dismiss = handler


# Convenience functions for quick toasts

_default_manager: Optional[ToastManager] = None


def get_toast_manager() -> ToastManager:
    """Get or create the default toast manager."""
    global _default_manager
    if _default_manager is None:
        _default_manager = ToastManager()
    return _default_manager


def toast(message: str, level: ToastLevel = ToastLevel.INFO, duration: float = 3.0) -> Toast:
    """Show a toast using the default manager."""
    return get_toast_manager().show(message, level, duration)


def toast_mode(to_mode: str, reason: str = "") -> Toast:
    """Show a mode change toast."""
    return get_toast_manager().show_mode_change("", to_mode, reason)


def toast_success(message: str) -> Toast:
    """Show a success toast."""
    return get_toast_manager().show_success(message)


def toast_error(message: str) -> Toast:
    """Show an error toast."""
    return get_toast_manager().show_error(message)
