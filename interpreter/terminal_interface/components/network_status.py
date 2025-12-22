"""
Network Status Indicator - Shows API connection state.

Features:
- Connection status display
- Retry attempt counter
- Latency indicator
"""

import time
from typing import Optional

from rich.console import Console
from rich.text import Text

from .theme import THEME


class NetworkStatus:
    """
    Displays network/API connection status.

    Status states:
    - connected: Successfully connected to API
    - connecting: Attempting to connect
    - retrying: Retrying after failure
    - error: Connection failed
    - timeout: Request timed out
    """

    STATUS_ICONS = {
        "connected": ("\u2705", "success"),      # Check mark, green
        "connecting": ("\u23F3", "warning"),     # Hourglass, yellow
        "retrying": ("\U0001F504", "warning"),   # Arrows circle, yellow
        "error": ("\u274C", "error"),            # Cross, red
        "timeout": ("\u23F1", "error"),          # Stopwatch, red
        "offline": ("\U0001F4E1", "text_muted"), # Antenna, gray
    }

    def __init__(self, console: Console = None):
        self.console = console or Console()
        self.status: str = "connecting"
        self.retry_count: int = 0
        self.last_latency: Optional[float] = None
        self.error_message: Optional[str] = None
        self._request_start: Optional[float] = None

    def start_request(self):
        """Mark the start of an API request."""
        self._request_start = time.time()
        self.status = "connecting"
        self.error_message = None

    def end_request(self, success: bool = True):
        """Mark the end of an API request."""
        if self._request_start:
            self.last_latency = time.time() - self._request_start
            self._request_start = None

        if success:
            self.status = "connected"
            self.retry_count = 0
        else:
            self.status = "error"

    def set_retrying(self, attempt: int, max_attempts: int = None):
        """Update status for retry attempt."""
        self.status = "retrying"
        self.retry_count = attempt

    def set_timeout(self):
        """Set timeout status."""
        self.status = "timeout"
        if self._request_start:
            self.last_latency = time.time() - self._request_start
            self._request_start = None

    def set_error(self, message: str):
        """Set error status with message."""
        self.status = "error"
        self.error_message = message

    def get_status_text(self) -> Text:
        """Get formatted status text for display."""
        icon, color_key = self.STATUS_ICONS.get(
            self.status, self.STATUS_ICONS["connecting"]
        )
        color = THEME.get(color_key, THEME["text_muted"])

        text = Text()
        text.append(f"{icon} ", style=color)

        if self.status == "connected":
            text.append("Connected", style=color)
            if self.last_latency:
                latency_ms = int(self.last_latency * 1000)
                latency_color = (
                    THEME["success"] if latency_ms < 500
                    else THEME["warning"] if latency_ms < 2000
                    else THEME["error"]
                )
                text.append(f" ({latency_ms}ms)", style=latency_color)

        elif self.status == "connecting":
            text.append("Connecting...", style=color)

        elif self.status == "retrying":
            text.append(f"Retrying (attempt {self.retry_count})...", style=color)

        elif self.status == "timeout":
            text.append("Request timed out", style=color)
            if self.last_latency:
                text.append(f" after {self.last_latency:.1f}s", style="dim")

        elif self.status == "error":
            text.append("Connection error", style=color)
            if self.error_message:
                # Truncate long error messages
                msg = self.error_message[:50]
                if len(self.error_message) > 50:
                    msg += "..."
                text.append(f": {msg}", style="dim")

        elif self.status == "offline":
            text.append("Offline", style=color)

        return text

    def display(self):
        """Print status to console."""
        self.console.print(self.get_status_text())

    def display_inline(self) -> str:
        """Get status as plain text for inline display."""
        icon, _ = self.STATUS_ICONS.get(
            self.status, self.STATUS_ICONS["connecting"]
        )

        if self.status == "connected":
            latency = f" ({int(self.last_latency * 1000)}ms)" if self.last_latency else ""
            return f"{icon} Connected{latency}"
        elif self.status == "connecting":
            return f"{icon} Connecting..."
        elif self.status == "retrying":
            return f"{icon} Retry #{self.retry_count}"
        elif self.status == "timeout":
            return f"{icon} Timeout"
        elif self.status == "error":
            return f"{icon} Error"
        elif self.status == "offline":
            return f"{icon} Offline"

        return f"{icon} Unknown"


# Global network status instance
_network_status: Optional[NetworkStatus] = None


def get_network_status() -> NetworkStatus:
    """Get the global network status instance."""
    global _network_status
    if _network_status is None:
        _network_status = NetworkStatus()
    return _network_status
