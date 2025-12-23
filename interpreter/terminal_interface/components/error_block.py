"""
Error Block - Displays structured error messages with formatting.

Features:
- Red-bordered error panels
- Error type icons
- Formatted tracebacks with syntax highlighting
- Suggested actions
"""

import re

from rich.console import Group
from rich.panel import Panel
from rich.text import Text

from .base_block import BaseBlock
from .theme import BOX_STYLES, THEME

# Error type icons
ERROR_ICONS = {
    "SyntaxError": "\u274c",  # Cross mark
    "TypeError": "\u26a0\ufe0f",  # Warning
    "ValueError": "\u26a0\ufe0f",  # Warning
    "KeyError": "\U0001f511",  # Key
    "IndexError": "\U0001f4ca",  # Chart
    "ImportError": "\U0001f4e6",  # Package
    "ModuleNotFoundError": "\U0001f4e6",  # Package
    "FileNotFoundError": "\U0001f4c1",  # Folder
    "PermissionError": "\U0001f512",  # Lock
    "ConnectionError": "\U0001f4e1",  # Antenna
    "TimeoutError": "\u23f1\ufe0f",  # Stopwatch
    "MemoryError": "\U0001f4be",  # Floppy disk
    "RuntimeError": "\u26a1",  # Lightning
    "Exception": "\u274c",  # Cross mark (default)
}


class ErrorBlock(BaseBlock):
    """
    Displays structured error messages.

    Visual structure:
    ┏━ ❌ SyntaxError ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
    ┃ invalid syntax                                  ┃
    ┗━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┛
    ┌─ 📜 Traceback ──────────────────────────────────┐
    │   File "script.py", line 10, in main            │
    │     x = 1 +                                     │
    └─────────────────────────────────────────────────┘
    """

    def __init__(self):
        super().__init__()
        self.error_type: str = "Exception"
        self.error_message: str = ""
        self.traceback_lines: list[str] = []
        self.suggestion: str | None = None
        self.file_path: str | None = None
        self.line_number: int | None = None

    def parse_error(self, error_text: str):
        """Parse error text to extract structured information."""
        lines = error_text.strip().split("\n")

        # Find the error type and message (usually the last line)
        for line in reversed(lines):
            match = re.match(
                r"^(\w+Error|\w+Exception|KeyboardInterrupt|SystemExit):\s*(.*)$", line
            )
            if match:
                self.error_type = match.group(1)
                self.error_message = match.group(2)
                break
            # Handle errors without message
            match = re.match(
                r"^(\w+Error|\w+Exception|KeyboardInterrupt|SystemExit)$", line
            )
            if match:
                self.error_type = match.group(1)
                self.error_message = ""
                break

        # Extract traceback lines
        in_traceback = False
        for line in lines:
            if line.startswith("Traceback"):
                in_traceback = True
                continue
            if in_traceback and not line.startswith(self.error_type):
                self.traceback_lines.append(line)
                # Extract file path and line number
                file_match = re.match(r'^\s*File "(.+)", line (\d+)', line)
                if file_match:
                    self.file_path = file_match.group(1)
                    self.line_number = int(file_match.group(2))

    def set_suggestion(self, suggestion: str):
        """Set a helpful suggestion for the error."""
        self.suggestion = suggestion

    def refresh(self, cursor: bool = True):
        """Refresh the error block display."""
        if not self.error_type:
            return

        components = []
        components.append(Text(""))  # Spacing

        # Error header panel
        error_panel = self._build_error_panel()
        components.append(error_panel)

        # Traceback panel (if available)
        if self.traceback_lines:
            traceback_panel = self._build_traceback_panel()
            components.append(traceback_panel)

        # Suggestion panel (if available)
        if self.suggestion:
            suggestion_panel = self._build_suggestion_panel()
            components.append(suggestion_panel)

        # Combine and display
        group = Group(*components)
        if self.live:
            self.live.update(group)
            self.live.refresh()

    def _build_error_panel(self) -> Panel:
        """Build the main error panel."""
        icon = ERROR_ICONS.get(self.error_type, ERROR_ICONS["Exception"])

        # Build title
        title = Text()
        title.append(f" {icon} ", style=f"bold {THEME['error']}")
        title.append(self.error_type, style=f"bold {THEME['error']}")

        # Build content
        content = Text(self.error_message, style=THEME["text_primary"])

        # Add file location if available
        if self.file_path and self.line_number:
            content.append("\n\n")
            content.append("Location: ", style="dim")
            content.append(self.file_path, style=THEME["warning"])
            content.append(":", style="dim")
            content.append(str(self.line_number), style=f"bold {THEME['secondary']}")

        return Panel(
            content,
            title=title,
            title_align="left",
            box=BOX_STYLES["code"],
            style=f"on {THEME['bg_medium']}",
            border_style=THEME["error"],
            padding=(0, 1),
        )

    def _build_traceback_panel(self) -> Panel:
        """Build the traceback panel with syntax highlighting."""
        styled_content = Text()

        for i, line in enumerate(self.traceback_lines[-6:]):  # Show last 6 lines
            if i > 0:
                styled_content.append("\n")

            # Highlight file references
            file_match = re.match(r'^(\s*File ")(.+)(", line )(\d+)(, in )(.+)$', line)
            if file_match:
                styled_content.append(
                    file_match.group(1), style=f"dim {THEME['error']}"
                )
                styled_content.append(file_match.group(2), style=THEME["warning"])
                styled_content.append(
                    file_match.group(3), style=f"dim {THEME['error']}"
                )
                styled_content.append(
                    file_match.group(4), style=f"bold {THEME['secondary']}"
                )
                styled_content.append(
                    file_match.group(5), style=f"dim {THEME['error']}"
                )
                styled_content.append(file_match.group(6), style=THEME["primary"])
            elif line.strip().startswith("^"):
                # Caret indicator
                styled_content.append(line, style=f"bold {THEME['error']}")
            else:
                # Code line
                styled_content.append(line, style=THEME["text_secondary"])

        scroll_icon = "\U0001f4dc"  # Scroll emoji
        title = f"{scroll_icon} Traceback"
        if len(self.traceback_lines) > 6:
            title += f" (showing last 6 of {len(self.traceback_lines)} lines)"

        return Panel(
            styled_content,
            title=title,
            title_align="left",
            box=BOX_STYLES["output"],
            style=f"on {THEME['bg_light']}",
            border_style=THEME["error"],
            padding=(0, 1),
        )

    def _build_suggestion_panel(self) -> Panel:
        """Build the suggestion panel."""
        bulb_icon = "\U0001f4a1"  # Light bulb

        content = Text()
        content.append(f"{bulb_icon} ", style=THEME["warning"])
        if self.suggestion:
            content.append(self.suggestion, style=THEME["text_secondary"])

        return Panel(
            content,
            box=BOX_STYLES["output"],
            style=f"on {THEME['bg_light']}",
            border_style=THEME["warning"],
            padding=(0, 1),
        )


def display_error(error_text: str, suggestion: str | None = None):
    """
    Convenience function to display a structured error.

    Args:
        error_text: The full error/traceback text
        suggestion: Optional helpful suggestion
    """
    block = ErrorBlock()
    try:
        block.parse_error(error_text)
        if suggestion:
            block.set_suggestion(suggestion)
        block.refresh()
    finally:
        block.end()
