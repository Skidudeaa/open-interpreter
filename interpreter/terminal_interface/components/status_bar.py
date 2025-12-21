"""
Status Bar - Persistent session information display.

Features:
- Model name display
- Message count
- Mode indicators (AUTO, SAFE, OS)
- Themed styling
"""

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from .theme import THEME, BOX_STYLES, ROLE_ICONS


class StatusBar:
    """
    Persistent status bar showing session information.

    Layout:
    ┌──────────────────────────────────────────────────────────────┐
    │ 🤖 claude-3-opus   │   💬 5 messages   │   AUTO │ SAFE:ask  │
    └──────────────────────────────────────────────────────────────┘
    """

    def __init__(self, interpreter=None, console: Console = None):
        self.interpreter = interpreter
        self.console = console or Console()

    def render(self) -> Panel:
        """Render the status bar panel."""
        table = Table(
            show_header=False,
            box=None,
            padding=0,
            expand=True,
        )
        table.add_column(ratio=1)           # Left: Model
        table.add_column(justify="center", ratio=2)  # Center: Messages
        table.add_column(justify="right", ratio=1)   # Right: Modes

        # Left section: Model info
        model_section = self._build_model_section()

        # Center section: Message count
        message_section = self._build_message_section()

        # Right section: Mode indicators
        mode_section = self._build_mode_section()

        table.add_row(model_section, message_section, mode_section)

        return Panel(
            table,
            box=BOX_STYLES["status"],
            style=f"on {THEME['bg_dark']}",
            border_style=THEME["text_muted"],
            padding=(0, 1),
        )

    def _build_model_section(self) -> Text:
        """Build the model name display."""
        robot_icon = ROLE_ICONS.get("assistant", "\U0001F916")

        if self.interpreter and hasattr(self.interpreter, "llm"):
            model = self.interpreter.llm.model or "No model"
            # Truncate long model names
            if len(model) > 25:
                model = model[:22] + "..."
        else:
            model = "No model"

        return Text(f"{robot_icon} {model}", style=THEME["secondary"])

    def _build_message_section(self) -> Text:
        """Build the message count display."""
        speech_icon = "\U0001F4AC"  # Speech balloon

        if self.interpreter and hasattr(self.interpreter, "messages"):
            count = len(self.interpreter.messages)
        else:
            count = 0

        plural = "s" if count != 1 else ""
        return Text(f"{speech_icon} {count} message{plural}", style=THEME["text_muted"])

    def _build_mode_section(self) -> Text:
        """Build the mode indicators."""
        modes = []

        if self.interpreter:
            # Auto-run mode
            if getattr(self.interpreter, "auto_run", False):
                modes.append(f"[{THEME['success']}]AUTO[/{THEME['success']}]")

            # Safe mode
            safe_mode = getattr(self.interpreter, "safe_mode", "off")
            if safe_mode != "off":
                modes.append(f"[{THEME['warning']}]SAFE:{safe_mode}[/{THEME['warning']}]")

            # OS control mode
            if getattr(self.interpreter, "os", False):
                modes.append(f"[{THEME['primary']}]OS[/{THEME['primary']}]")

            # Loop mode
            if getattr(self.interpreter, "loop", False):
                modes.append(f"[{THEME['info']}]LOOP[/{THEME['info']}]")

        if modes:
            return Text.from_markup(" | ".join(modes))
        else:
            return Text("")

    def display(self):
        """Print the status bar to the console."""
        self.console.print(self.render())

    def get_summary(self) -> str:
        """Get a plain text summary of the status."""
        parts = []

        if self.interpreter and hasattr(self.interpreter, "llm"):
            model = self.interpreter.llm.model or "No model"
            parts.append(f"Model: {model}")

        if self.interpreter and hasattr(self.interpreter, "messages"):
            count = len(self.interpreter.messages)
            parts.append(f"Messages: {count}")

        return " | ".join(parts) if parts else "No session"


def display_status_bar(interpreter, console: Console = None):
    """
    Convenience function to display the status bar.

    Args:
        interpreter: The interpreter instance
        console: Optional console to use
    """
    StatusBar(interpreter, console).display()
