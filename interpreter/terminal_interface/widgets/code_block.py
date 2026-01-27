"""
CodeBlock Widget - Syntax-highlighted code display with console output.

ARCHITECTURE: Vertical container with two children:
  1. CodeStatic - Syntax-highlighted code
  2. OutputStatic - Console output with error highlighting

WHY: Separating code and output into child widgets enables:
  - Independent reactive updates (code streams separately from output)
  - Clean CSS targeting for different status states
  - Proper layout management via Textual containers

TRADEOFF: Container approach vs single render() - more complex but cleaner updates.

Replaces components/code_block.py with Textual's reactive model.
Uses Rich's Syntax and Panel for rendering (Textual renders Rich natively).
"""

import re

from rich.panel import Panel
from rich.syntax import Syntax
from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.reactive import reactive
from textual.widgets import Static

# Regex patterns for error detection
TRACEBACK_PATTERN = re.compile(r"^Traceback \(most recent call last\):")
ERROR_LINE_PATTERN = re.compile(r"^(\w+Error|Exception): ")
FILE_LINE_PATTERN = re.compile(r'^\s+File "(.+)", line (\d+), in (.+)')


class OutputStatic(Static):
    """
    Console output display with error highlighting.

    Features:
    - Color-coded tracebacks (red tones)
    - File/line highlighting in tracebacks
    - Fold/unfold for long outputs
    - Error border styling
    - Auto-hide when empty
    """

    output_lines: reactive[list[tuple[str, str]]] = reactive(
        default=list, init=False, layout=True
    )
    is_folded: reactive[bool] = reactive(False)

    FOLD_THRESHOLD = 20
    PREVIEW_LINES = 3

    DEFAULT_CSS = """
    OutputStatic {
        margin-top: 1;
        padding: 1;
        background: #0d1117;  /* $bg-dark */
        border: round #8b949e;  /* $text-muted */
        display: none;
    }

    OutputStatic.visible {
        display: block;
    }

    OutputStatic.has-error {
        border: round #f85149;  /* $error */
    }

    OutputStatic.folded {
        max-height: 8;
    }
    """

    def watch_output_lines(
        self, _old_lines: list[tuple[str, str]], output_lines: list[tuple[str, str]]
    ) -> None:
        """React to output changes - show/hide and style border."""
        if output_lines:
            self.add_class("visible")
            # Check for errors
            has_errors = any(
                t in ("error", "stderr", "traceback") for _, t in output_lines
            )
            if has_errors:
                self.add_class("has-error")
            else:
                self.remove_class("has-error")
        else:
            self.remove_class("visible")
            self.remove_class("has-error")

    def watch_is_folded(self, is_folded: bool) -> None:
        """Toggle folded class."""
        if is_folded:
            self.add_class("folded")
        else:
            self.remove_class("folded")

    def render(self) -> Panel | Text:
        """Render output panel with styled text."""
        if not self.output_lines:
            return Text("")

        total_lines = len(self.output_lines)

        # Apply folding
        if self.is_folded and total_lines > self.FOLD_THRESHOLD:
            visible_lines = self.output_lines[: self.PREVIEW_LINES]
        else:
            visible_lines = self.output_lines

        # Build styled content
        content = Text()
        for i, (line, output_type) in enumerate(visible_lines):
            if i > 0:
                content.append("\n")

            if output_type == "error":
                # Error lines: bold red
                content.append(line, style="bold #f85149")
            elif output_type == "stderr":
                # Stderr: red
                content.append(line, style="#f85149")
            elif output_type == "traceback":
                # Traceback lines: dim red with file highlights
                match = FILE_LINE_PATTERN.match(line)
                if match:
                    content.append('  File "', style="dim #f85149")
                    content.append(match.group(1), style="#d29922")  # file path
                    content.append('", line ', style="dim #f85149")
                    content.append(match.group(2), style="bold #8b949e")  # line num
                    content.append(", in ", style="dim #f85149")
                    content.append(match.group(3), style="#58a6ff")  # function
                else:
                    content.append(line, style="dim #f85149")
            else:
                # Normal stdout: default color
                content.append(line, style="#c9d1d9")

        # Build title
        fold_icon = "▶" if self.is_folded else "▼"
        if self.is_folded and total_lines > self.FOLD_THRESHOLD:
            title = f"{fold_icon} Output ({total_lines} lines, showing {len(visible_lines)})"
        else:
            title = f"{fold_icon} Output"

        return Panel(
            content,
            title=title,
            title_align="left",
            border_style="dim",
            padding=(0, 1),
        )


class CodeStatic(Static):
    """
    Syntax-highlighted code display.

    Features:
    - Syntax highlighting via Rich
    - Active line highlighting
    - Fold/unfold for long code
    - Language-specific themes
    """

    code: reactive[str] = reactive("", layout=True)
    language: reactive[str] = reactive("python")
    is_folded: reactive[bool] = reactive(False)
    active_line: reactive[int | None] = reactive(None)

    FOLD_THRESHOLD = 20
    PREVIEW_LINES = 3

    def render(self) -> Syntax | Text:
        """Render syntax-highlighted code."""
        if not self.code:
            return Text("", style="dim")

        display_code = self.code
        line_count = self.code.count("\n") + 1

        # Auto-fold long code
        if self.is_folded and line_count > self.FOLD_THRESHOLD:
            lines = self.code.split("\n")
            display_code = "\n".join(lines[: self.PREVIEW_LINES])
            display_code += f"\n... ({line_count - self.PREVIEW_LINES} more lines)"

        return Syntax(
            display_code,
            self.language,
            theme="monokai",
            line_numbers=not self.is_folded,
            word_wrap=True,
            highlight_lines={self.active_line} if self.active_line else None,
        )


class CodeBlockWidget(Vertical):
    """
    Composite code block with syntax highlighting and console output.

    ARCHITECTURE: Vertical container with two children:
      1. CodeStatic - Syntax-highlighted code
      2. OutputStatic - Console output with error highlighting

    WHY: This approach enables independent reactive updates for code and output,
    allowing streaming to work correctly without full re-renders.

    Features:
    - Syntax highlighting via Rich
    - Fold/unfold for long code/output
    - Status indicator (pending/running/success/error)
    - Traceback highlighting
    - Streaming output support

    CSS Classes:
    - .code-block - Base styling
    - .status-pending, .status-running, .status-success, .status-error
    """

    # Reactive attributes - propagated to children
    code: reactive[str] = reactive("", layout=True)
    language: reactive[str] = reactive("python")
    output: reactive[str] = reactive("")
    status: reactive[str] = reactive("pending")
    is_folded: reactive[bool] = reactive(False)
    active_line: reactive[int | None] = reactive(None)

    DEFAULT_CSS = """
    CodeBlockWidget {
        margin: 1 0;
        padding: 1;
        border: round #8b949e;  /* $secondary */
        background: #1a1a2e;  /* $surface */
        height: auto;
    }

    CodeBlockWidget.status-pending {
        border: round #8b949e;  /* $text-muted */
    }

    CodeBlockWidget.status-running {
        border: round #d29922;  /* $warning */
    }

    CodeBlockWidget.status-success {
        border: round #3fb950;  /* $success */
    }

    CodeBlockWidget.status-error {
        border: round #f85149;  /* $error */
    }

    CodeBlockWidget.folded {
        max-height: 15;
    }
    """

    def __init__(
        self,
        code: str = "",
        language: str = "python",
        *,
        name: str | None = None,
        id: str | None = None,
        classes: str | None = None,
    ):
        super().__init__(name=name, id=id, classes=classes)
        # Store initial values - will be applied after compose()
        self._initial_code = code
        self._initial_language = language
        # Internal state for output parsing
        self._output_lines: list[tuple[str, str]] = []
        self._in_traceback: bool = False
        self.add_class("code-block")

    def compose(self) -> ComposeResult:
        """Compose child widgets."""
        yield CodeStatic(id="code-section")
        yield OutputStatic(id="output-section")

    def on_mount(self) -> None:
        """Apply initial values after mounting."""
        # Now that children exist, set initial values
        if self._initial_code:
            self.code = self._initial_code
        if self._initial_language:
            self.language = self._initial_language

    # Reactive watchers - propagate to children

    def watch_code(self, new_code: str) -> None:
        """Propagate code to CodeStatic."""
        try:
            code_static = self.query_one("#code-section", CodeStatic)
            code_static.code = new_code
        except Exception:
            pass  # Widget not mounted yet

    def watch_language(self, new_language: str) -> None:
        """Propagate language to CodeStatic."""
        try:
            code_static = self.query_one("#code-section", CodeStatic)
            code_static.language = new_language
        except Exception:
            pass

    def watch_output(self, _new_output: str) -> None:
        """Parse output and propagate to OutputStatic."""
        try:
            output_static = self.query_one("#output-section", OutputStatic)
            # Copy the list to trigger reactive update
            output_static.output_lines = list(self._output_lines)
        except Exception:
            pass

    def watch_is_folded(self, is_folded: bool) -> None:
        """Toggle folded class and propagate to children."""
        if is_folded:
            self.add_class("folded")
        else:
            self.remove_class("folded")

        try:
            code_static = self.query_one("#code-section", CodeStatic)
            output_static = self.query_one("#output-section", OutputStatic)
            code_static.is_folded = is_folded
            output_static.is_folded = is_folded
        except Exception:
            pass

    def watch_active_line(self, new_line: int | None) -> None:
        """Propagate active line to CodeStatic."""
        try:
            code_static = self.query_one("#code-section", CodeStatic)
            code_static.active_line = new_line
        except Exception:
            pass

    def watch_status(self, old_status: str, new_status: str) -> None:
        """React to status changes by updating CSS classes."""
        if old_status:
            self.remove_class(f"status-{old_status}")
        self.add_class(f"status-{new_status}")

    # Public methods (API compatibility)

    def toggle_fold(self) -> None:
        """Toggle fold state."""
        self.is_folded = not self.is_folded

    def set_running(self) -> None:
        """Mark code as currently executing."""
        self.status = "running"

    def set_success(self) -> None:
        """Mark execution as successful."""
        self.status = "success"

    def set_error(self) -> None:
        """Mark execution as failed."""
        self.status = "error"

    def add_output(self, text: str) -> None:
        """
        Append to output buffer with type detection.

        Supports streaming - call multiple times to append.
        Automatically detects tracebacks and errors.
        """
        if not text:
            return

        # Split into lines and detect types
        new_lines = text.split("\n")
        for line in new_lines:
            output_type = self._detect_output_type(line)
            self._output_lines.append((line, output_type))

        # Update output string to trigger reactive update
        self.output = "\n".join(line for line, _ in self._output_lines)

    def clear_output(self) -> None:
        """Clear output buffer."""
        self._output_lines = []
        self.output = ""
        self._in_traceback = False

    # Private helpers

    def _detect_output_type(self, line: str) -> str:
        """Detect if a line is part of a traceback or error."""
        if TRACEBACK_PATTERN.match(line):
            self._in_traceback = True
            return "traceback"

        if ERROR_LINE_PATTERN.match(line):
            self._in_traceback = False
            return "error"

        if FILE_LINE_PATTERN.match(line):
            return "traceback"

        if self._in_traceback:
            return "traceback"

        return "stdout"
