"""
Code Block - Displays code with syntax highlighting and execution output.

Features:
- Language badge with emoji icon (e.g., 🐍 PYTHON)
- Execution status indicator (pending/running/success/error)
- Syntax highlighting with one-dark theme
- Contained output panel (fixes scrolling issue)
- Execution timing display
- Syntax-highlighted tracebacks
- Stderr distinction (red coloring)
"""

import re
import time

from rich.console import Group
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn, TimeElapsedColumn
from rich.syntax import Syntax
from rich.table import Table
from rich.text import Text

from .base_block import BaseBlock
from .theme import (
    THEME,
    BOX_STYLES,
    get_language_icon,
    get_status_display,
)

# Regex patterns for traceback detection
TRACEBACK_PATTERN = re.compile(r'^Traceback \(most recent call last\):')
FILE_LINE_PATTERN = re.compile(r'^  File "(.+)", line (\d+), in (.+)$')
ERROR_LINE_PATTERN = re.compile(r'^(\w+Error|\w+Exception|KeyboardInterrupt|SystemExit).*$')


class CodeBlock(BaseBlock):
    """
    Displays code blocks with execution output.

    Visual structure:
    ┏━ 🐍 PYTHON ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ ▶️ ━┓
    ┃ code here                                        ┃
    ┗━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ ⏱️ 0.12s ━┛
    ┌─ 📜 Output (42 lines, showing last 8) ───────────┐
    │ output here                                       │
    └───────────────────────────────────────────────────┘
    """

    # Maximum lines to show in output panel during execution
    MAX_OUTPUT_LINES = 8

    def __init__(self, interpreter=None):
        super().__init__()

        self.type = "code"
        self.highlight_active_line = (
            interpreter.highlight_active_line if interpreter else None
        )

        # Code properties
        self.language = ""
        self.code = ""
        self.output = ""
        self.active_line = None
        self.margin_top = True

        # Execution status: pending, running, success, error
        self.status = "pending"

        # Output buffering for contained display
        # Each entry is (text, output_type) where output_type is 'stdout', 'stderr', or 'traceback'
        self._output_lines: list[tuple[str, str]] = []
        self._in_traceback = False  # Track if we're inside a traceback

        # Refresh throttling to prevent UI unresponsiveness
        self._last_refresh = 0
        self._min_refresh_interval = 0.033  # ~30 fps max

    def set_status(self, status: str):
        """Set execution status (pending/running/success/error)."""
        self.status = status
        self.refresh(cursor=False)

    def add_output(self, text: str, output_type: str = "stdout"):
        """Add output text with type detection (handles the scrolling issue).

        Args:
            text: The output text to add
            output_type: 'stdout', 'stderr', or auto-detected
        """
        if text:
            # Split and buffer lines with type detection
            new_lines = text.split("\n")
            for line in new_lines:
                detected_type = self._detect_output_type(line, output_type)
                self._output_lines.append((line, detected_type))
            # Update the output string for compatibility
            self.output = "\n".join(line for line, _ in self._output_lines)

    def _detect_output_type(self, line: str, default_type: str) -> str:
        """Detect if a line is part of a traceback or error output."""
        # Check for traceback start
        if TRACEBACK_PATTERN.match(line):
            self._in_traceback = True
            return "traceback"

        # Check for error line (ends traceback)
        if ERROR_LINE_PATTERN.match(line):
            self._in_traceback = False
            return "error"

        # Check for file/line references in traceback
        if FILE_LINE_PATTERN.match(line):
            return "traceback"

        # If we're in a traceback, continue marking as such
        if self._in_traceback:
            return "traceback"

        # Default to provided type
        return default_type

    def end(self):
        """End the code block display."""
        self.active_line = None
        self.refresh(cursor=False)
        super().end()

    def refresh(self, cursor: bool = True):
        """Refresh the code block display."""
        if not self.code and not self.output:
            return

        # Throttle refresh rate to prevent UI unresponsiveness
        current_time = time.time()
        if current_time - self._last_refresh < self._min_refresh_interval:
            return  # Skip this refresh - too soon
        self._last_refresh = current_time

        # Build all components
        components = []

        if self.margin_top:
            components.append(Text(""))  # Spacing

        # Language header with status
        header = self._build_header()
        components.append(header)

        # Code panel
        code_panel = self._build_code_panel(cursor)
        components.append(code_panel)

        # Timing footer
        if self.status in ("success", "error") or self.get_elapsed() > 0.5:
            footer = self._build_footer()
            if footer:
                components.append(footer)

        # Output panel (contained, non-scrolling)
        if self.output and self.output.strip() and self.output != "None":
            output_panel = self._build_output_panel()
            components.append(output_panel)

        # Combine and display
        group = Group(*components)
        self.live.update(group)
        self.live.refresh()

    def _build_header(self) -> Table:
        """Build the language header with status indicator."""
        header_table = Table(
            show_header=False,
            box=None,
            padding=0,
            expand=True,
        )
        header_table.add_column(ratio=1)
        header_table.add_column(justify="right", width=4)

        # Language badge
        icon = get_language_icon(self.language)
        lang_name = self.language.upper() if self.language else "CODE"
        lang_text = Text(f" {icon} {lang_name} ", style=f"bold on {THEME['bg_medium']}")

        # Status indicator
        status_icon, status_color_key = get_status_display(self.status)
        status_color = THEME.get(status_color_key, THEME["text_muted"])
        status_text = Text(f" {status_icon} ", style=status_color)

        header_table.add_row(lang_text, status_text)
        return header_table

    def _build_code_panel(self, cursor: bool) -> Panel:
        """Build the code panel with syntax highlighting."""
        code = self.code

        # Add cursor indicator
        if cursor and self._should_show_cursor():
            code += "\u25cf"  # Filled circle

        # Build code table with line-by-line rendering for active line highlighting
        code_table = Table(
            show_header=False,
            show_footer=False,
            box=None,
            padding=0,
            expand=True,
        )
        code_table.add_column()

        code_lines = code.strip().split("\n")
        for i, line in enumerate(code_lines, start=1):
            if i == self.active_line and self._should_show_cursor():
                # Active line: inverted colors
                syntax = Syntax(
                    line,
                    self.language,
                    theme="bw",
                    line_numbers=False,
                    word_wrap=True,
                )
                code_table.add_row(syntax, style="black on white")
            else:
                # Normal line
                syntax = Syntax(
                    line,
                    self.language,
                    theme=THEME["code_theme"],
                    line_numbers=False,
                    word_wrap=True,
                )
                code_table.add_row(syntax)

        return Panel(
            code_table,
            box=BOX_STYLES["code"],
            style=f"on {THEME['bg_medium']}",
            border_style=THEME["primary"],
            padding=(0, 1),
        )

    def _build_output_panel(self) -> Panel:
        """Build the contained output panel with syntax-highlighted tracebacks."""
        total_lines = len(self._output_lines) if self._output_lines else 0

        # Get visible lines
        if total_lines == 0:
            # Use raw output if no buffered lines (legacy compatibility)
            raw_lines = self.output.strip().split("\n")
            visible_lines = [(line, "stdout") for line in raw_lines]
            total_lines = len(visible_lines)
        else:
            visible_lines = self._output_lines

        # Limit visible lines to prevent scrolling
        if len(visible_lines) > self.MAX_OUTPUT_LINES:
            visible_lines = visible_lines[-self.MAX_OUTPUT_LINES:]
            showing = self.MAX_OUTPUT_LINES
        else:
            showing = len(visible_lines)

        # Build styled content with colors based on output type
        styled_content = Text()
        for i, (line, output_type) in enumerate(visible_lines):
            if i > 0:
                styled_content.append("\n")

            if output_type == "error":
                # Error lines: bold red
                styled_content.append(line, style=f"bold {THEME['error']}")
            elif output_type == "stderr":
                # Stderr: red
                styled_content.append(line, style=THEME["error"])
            elif output_type == "traceback":
                # Traceback lines: dim red with file highlights
                match = FILE_LINE_PATTERN.match(line)
                if match:
                    # Highlight file path and line number
                    styled_content.append('  File "', style=f"dim {THEME['error']}")
                    styled_content.append(match.group(1), style=f"{THEME['warning']}")  # file path
                    styled_content.append('", line ', style=f"dim {THEME['error']}")
                    styled_content.append(match.group(2), style=f"bold {THEME['secondary']}")  # line num
                    styled_content.append(', in ', style=f"dim {THEME['error']}")
                    styled_content.append(match.group(3), style=f"{THEME['primary']}")  # function
                else:
                    styled_content.append(line, style=f"dim {THEME['error']}")
            else:
                # Normal stdout: default color
                styled_content.append(line, style=THEME["computer"])

        # Build header with line count
        scroll_icon = "\U0001F4DC"  # Scroll emoji
        if total_lines > self.MAX_OUTPUT_LINES:
            title = f"{scroll_icon} Output ({total_lines} lines, showing last {showing})"
        else:
            title = f"{scroll_icon} Output"

        # Check if output contains errors for border styling
        has_errors = any(otype in ("error", "stderr", "traceback") for _, otype in visible_lines)
        border_color = THEME["error"] if has_errors else THEME["text_muted"]

        return Panel(
            styled_content,
            title=title,
            title_align="left",
            box=BOX_STYLES["output"],
            style=f"on {THEME['bg_light']}",
            border_style=border_color,
            padding=(0, 1),
        )

    def _build_footer(self) -> Text:
        """Build the timing footer with progress indicator for long-running code."""
        elapsed_secs = self.get_elapsed()
        elapsed_str = self.get_elapsed_str()
        timer_icon = "\u23F1"  # Stopwatch

        # For long-running code (>5s), show a more prominent progress indicator
        if self.status == "running" and elapsed_secs > 5:
            # Spinning indicator for long-running code
            spinner_chars = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
            spinner_idx = int(elapsed_secs * 10) % len(spinner_chars)
            spinner = spinner_chars[spinner_idx]

            footer = Text()
            footer.append(f"  {spinner} ", style=f"bold {THEME['secondary']}")
            footer.append(f"Running... ", style=THEME["secondary"])
            footer.append(f"{timer_icon} {elapsed_str}  ", style="dim")
            return footer

        return Text(
            f"  {timer_icon} {elapsed_str}  ",
            style="dim",
            justify="right",
        )

    def _should_show_cursor(self) -> bool:
        """Determine if cursor/active line highlighting should show."""
        if self.highlight_active_line is not None:
            return self.highlight_active_line
        return True
