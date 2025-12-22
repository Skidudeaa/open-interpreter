"""
Interactive Menu - Arrow-key navigation for selections.

Features:
- Arrow key navigation (up/down)
- Enter to select
- Number keys for quick selection
- Visual highlighting of current selection
- Timeout protection to prevent infinite blocking
"""

import sys
from typing import List, Optional, Callable

from rich.console import Console
from rich.panel import Panel
from rich.text import Text

from .theme import THEME

# Platform-specific keyboard handling
try:
    import termios
    import tty
    import select as select_module  # For timeout on stdin.read()
    UNIX_AVAILABLE = True
except ImportError:
    UNIX_AVAILABLE = False
    select_module = None

try:
    import msvcrt
    WINDOWS_AVAILABLE = True
except ImportError:
    WINDOWS_AVAILABLE = False


def _read_with_timeout(fd, timeout: float = 30.0) -> str:
    """Read a single character from stdin with timeout protection.

    Args:
        fd: File descriptor for stdin
        timeout: Maximum seconds to wait for input (default 30s)

    Returns:
        Single character read, or empty string on timeout
    """
    if select_module is None:
        # No select available, fall back to blocking read
        return sys.stdin.read(1)

    # Use select to wait for input with timeout
    ready, _, _ = select_module.select([sys.stdin], [], [], timeout)
    if ready:
        return sys.stdin.read(1)
    else:
        # Timeout occurred
        return ''


def get_key(timeout: float = 30.0) -> str:
    """Get a single keypress, handling arrow keys.

    Args:
        timeout: Maximum seconds to wait for input (default 30s)

    Returns:
        Key name ('up', 'down', 'enter', etc.) or 'timeout' on timeout
    """
    if WINDOWS_AVAILABLE:
        # Windows uses msvcrt which has its own timeout handling
        # For now, use blocking read (Windows terminals are more reliable)
        key = msvcrt.getch()
        if key == b'\xe0':  # Arrow key prefix on Windows
            key = msvcrt.getch()
            if key == b'H':
                return 'up'
            elif key == b'P':
                return 'down'
            elif key == b'K':
                return 'left'
            elif key == b'M':
                return 'right'
        elif key == b'\r':
            return 'enter'
        elif key == b'\x1b':
            return 'escape'
        return key.decode('utf-8', errors='ignore')

    elif UNIX_AVAILABLE:
        fd = sys.stdin.fileno()
        old_settings = None
        try:
            old_settings = termios.tcgetattr(fd)
            tty.setraw(fd)

            ch = _read_with_timeout(fd, timeout)

            if ch == '':
                return 'timeout'

            if ch == '\x1b':  # Escape sequence
                # Short timeout for escape sequences (arrow keys send multiple chars quickly)
                ch2 = _read_with_timeout(fd, 0.1)
                if ch2 == '[':
                    ch3 = _read_with_timeout(fd, 0.1)
                    if ch3 == 'A':
                        return 'up'
                    elif ch3 == 'B':
                        return 'down'
                    elif ch3 == 'C':
                        return 'right'
                    elif ch3 == 'D':
                        return 'left'
                return 'escape'
            elif ch == '\r' or ch == '\n':
                return 'enter'
            elif ch == '\x03':  # Ctrl+C
                raise KeyboardInterrupt
            elif ch == '\x0c':  # Ctrl+L
                return 'clear'
            return ch

        except Exception as e:
            # If any error occurs, return timeout to allow fallback
            if 'KeyboardInterrupt' in str(type(e)):
                raise
            return 'timeout'
        finally:
            # CRITICAL: Always restore terminal settings
            if old_settings is not None:
                try:
                    termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
                except Exception:
                    pass  # Terminal may be in bad state, but at least we tried

    else:
        # Fallback to regular input
        return input()


class InteractiveMenu:
    """
    Arrow-key navigable menu for terminal selections.

    Usage:
        menu = InteractiveMenu(["Option 1", "Option 2", "Option 3"])
        selected = menu.show("Choose an option:")
    """

    def __init__(
        self,
        options: List[str],
        descriptions: List[str] = None,
        default_index: int = 0,
        allow_cancel: bool = True,
    ):
        """
        Initialize the interactive menu.

        Args:
            options: List of option labels
            descriptions: Optional descriptions for each option
            default_index: Initially selected index
            allow_cancel: Whether Escape key cancels selection
        """
        self.options = options
        self.descriptions = descriptions or [""] * len(options)
        self.selected_index = default_index
        self.allow_cancel = allow_cancel
        self.console = Console()

    def _render(self, title: str = None):
        """Render the current menu state."""
        # Clear previous render
        self.console.clear()

        # Build menu content
        content = Text()

        if title:
            content.append(f"{title}\n\n", style=f"bold {THEME['text_primary']}")

        content.append("Use ↑↓ arrows to navigate, Enter to select", style="dim")
        if self.allow_cancel:
            content.append(", Esc to cancel", style="dim")
        content.append("\n\n")

        for i, (option, desc) in enumerate(zip(self.options, self.descriptions)):
            if i == self.selected_index:
                # Highlighted option
                content.append("  ▶ ", style=f"bold {THEME['secondary']}")
                content.append(option, style=f"bold {THEME['text_primary']} on {THEME['bg_highlight']}")
            else:
                content.append("    ", style="dim")
                content.append(option, style=THEME['text_secondary'])

            # Add number hint
            if i < 9:
                content.append(f"  [{i + 1}]", style="dim")

            # Add description if present
            if desc:
                content.append(f"  {desc}", style="dim italic")

            content.append("\n")

        panel = Panel(
            content,
            box=None,
            padding=(1, 2),
        )
        self.console.print(panel)

    def show(self, title: str = None, timeout: float = 30.0) -> Optional[int]:
        """
        Display the menu and wait for selection.

        Args:
            title: Optional title to display above menu
            timeout: Maximum seconds to wait for each keypress (default 30s)

        Returns:
            Selected index, or None if cancelled/timeout
        """
        if not (UNIX_AVAILABLE or WINDOWS_AVAILABLE):
            # Fallback to simple numbered input
            return self._fallback_show(title)

        timeout_count = 0
        max_timeouts = 3  # Fall back after 3 consecutive timeouts

        try:
            while True:
                self._render(title)

                key = get_key(timeout=timeout)

                if key == 'timeout':
                    timeout_count += 1
                    if timeout_count >= max_timeouts:
                        # Too many timeouts - fall back to simple input
                        self.console.print("\n[dim]Interactive menu timed out. Falling back to simple input.[/dim]")
                        return self._fallback_show(title)
                    continue
                else:
                    timeout_count = 0  # Reset on successful input

                if key == 'up':
                    self.selected_index = (self.selected_index - 1) % len(self.options)
                elif key == 'down':
                    self.selected_index = (self.selected_index + 1) % len(self.options)
                elif key == 'enter':
                    self._safe_clear()
                    return self.selected_index
                elif key == 'escape' and self.allow_cancel:
                    self._safe_clear()
                    return None
                elif key.isdigit():
                    num = int(key)
                    if 1 <= num <= len(self.options):
                        self._safe_clear()
                        return num - 1

        except KeyboardInterrupt:
            self._safe_clear()
            return None
        except Exception:
            # Any other error - fall back to simple input
            return self._fallback_show(title)

    def _safe_clear(self):
        """Safely clear the console, handling potential errors."""
        try:
            self.console.clear()
        except Exception:
            # If clear fails, just print newlines to separate
            print("\n\n")

    def _fallback_show(self, title: str = None) -> Optional[int]:
        """Fallback for systems without raw keyboard access."""
        if title:
            self.console.print(f"\n[bold]{title}[/bold]\n")

        for i, (option, desc) in enumerate(zip(self.options, self.descriptions)):
            desc_text = f" - {desc}" if desc else ""
            self.console.print(f"  [{i + 1}] {option}{desc_text}")

        self.console.print()

        while True:
            try:
                response = input("Enter choice (number): ").strip()
                if response.lower() in ('q', 'quit', 'cancel', ''):
                    return None
                num = int(response)
                if 1 <= num <= len(self.options):
                    return num - 1
            except ValueError:
                pass
            self.console.print("[red]Invalid choice. Please enter a number.[/red]")


class ConfirmationMenu(InteractiveMenu):
    """Simplified yes/no confirmation with arrow keys."""

    def __init__(self, default_yes: bool = True):
        options = ["Yes", "No"]
        super().__init__(options, default_index=0 if default_yes else 1)

    def confirm(self, message: str) -> bool:
        """Show confirmation and return True/False."""
        result = self.show(message)
        return result == 0 if result is not None else False


def interactive_choice(
    options: List[str],
    title: str = None,
    descriptions: List[str] = None,
    default: int = 0,
) -> Optional[int]:
    """
    Convenience function for quick interactive selection.

    Args:
        options: List of options
        title: Optional title
        descriptions: Optional descriptions
        default: Default selected index

    Returns:
        Selected index or None if cancelled
    """
    menu = InteractiveMenu(options, descriptions, default)
    return menu.show(title)


def interactive_confirm(message: str, default: bool = True) -> bool:
    """
    Convenience function for quick interactive confirmation.

    Args:
        message: Confirmation message
        default: Default value (True = Yes selected)

    Returns:
        True if confirmed, False otherwise
    """
    return ConfirmationMenu(default_yes=default).confirm(message)
