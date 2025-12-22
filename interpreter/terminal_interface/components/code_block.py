"""
Code Block - Displays code with syntax highlighting and execution output.

Features:
- Language badge with emoji icon (e.g., 🐍 PYTHON)
- Execution status indicator (pending/running/success/error)
- Syntax highlighting with one-dark theme
- Contained output panel (fixes scrolling issue)
- Execution timing display
"""

import time

from rich.console import Group
from rich.panel import Panel
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
        self._output_lines: list[str] = []

        # Refresh throttling to prevent UI unresponsiveness
        self._last_refresh = 0
        self._min_refresh_interval = 0.033  # ~30 fps max

    def set_status(self, status: str):
        """Set execution status (pending/running/success/error)."""
        self.status = status
        self.refresh(cursor=False)

    def add_output(self, text: str):
        """Add output text (handles the scrolling issue)."""
        if text:
            # Split and buffer lines
            new_lines = text.split("\n")
            self._output_lines.extend(new_lines)
            # Update the output string for compatibility
            self.output = "\n".join(self._output_lines)

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
        """Build the contained output panel (fixes scrolling issue)."""
        total_lines = len(self._output_lines) if self._output_lines else 0

        # Get visible lines
        if total_lines == 0:
            # Use raw output if no buffered lines
            visible_lines = self.output.strip().split("\n")
            total_lines = len(visible_lines)
        else:
            visible_lines = self._output_lines

        # Limit visible lines to prevent scrolling
        if len(visible_lines) > self.MAX_OUTPUT_LINES:
            visible_lines = visible_lines[-self.MAX_OUTPUT_LINES:]
            showing = self.MAX_OUTPUT_LINES
        else:
            showing = len(visible_lines)

        content = "\n".join(visible_lines)

        # Build header with line count
        scroll_icon = "\U0001F4DC"  # Scroll emoji
        if total_lines > self.MAX_OUTPUT_LINES:
            title = f"{scroll_icon} Output ({total_lines} lines, showing last {showing})"
        else:
            title = f"{scroll_icon} Output"

        return Panel(
            Text(content),
            title=title,
            title_align="left",
            box=BOX_STYLES["output"],
            style=f"on {THEME['bg_light']}",
            border_style=THEME["text_muted"],
            padding=(0, 1),
        )

    def _build_footer(self) -> Text:
        """Build the timing footer."""
        elapsed = self.get_elapsed_str()
        timer_icon = "\u23F1"  # Stopwatch
        return Text(
            f"  {timer_icon} {elapsed}  ",
            style=f"dim",
            justify="right",
        )

    def _should_show_cursor(self) -> bool:
        """Determine if cursor/active line highlighting should show."""
        if self.highlight_active_line is not None:
            return self.highlight_active_line
        return True
