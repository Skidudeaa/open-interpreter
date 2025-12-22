"""
Message Block - Displays assistant/user/system messages with role indicators.

Features:
- Role-specific emoji icons and colors
- Styled panel borders
- Animated cursor during streaming
- Markdown rendering
"""

import re

from rich.markdown import Markdown
from rich.panel import Panel
from rich.text import Text

from .base_block import BaseBlock
from .theme import (
    THEME,
    BOX_STYLES,
    get_role_icon,
    get_role_style,
)


class MessageBlock(BaseBlock):
    """
    Displays text messages with role-specific styling.

    Roles:
    - assistant: AI responses (cyan border)
    - user: User input (violet border)
    - system: System messages (amber border)
    """

    # Role-specific border colors
    ROLE_BORDER_STYLES = {
        "assistant": THEME["assistant"],
        "user": THEME["user"],
        "computer": THEME["computer"],
        "system": THEME["system"],
    }

    def __init__(self, role: str = "assistant"):
        super().__init__()
        self.type = "message"
        self.role = role
        self.message = ""

    def _build_header(self) -> str:
        """Build the panel title with role icon and name."""
        icon = get_role_icon(self.role)
        role_name = self.role.capitalize()
        return f"{icon} {role_name}"

    def refresh(self, cursor: bool = True):
        """Refresh the message display."""
        # De-stylize any code blocks in markdown
        content = textify_markdown_code_blocks(self.message)

        # Add blinking cursor if streaming
        if cursor:
            content += "[blink]\u25cf[/blink]"  # Filled circle

        # Render markdown
        markdown = Markdown(content.strip())

        # Get role-specific styling
        border_color = self.ROLE_BORDER_STYLES.get(self.role, THEME["text_muted"])
        header = self._build_header()

        # Create styled panel
        panel = Panel(
            markdown,
            title=header,
            title_align="left",
            border_style=border_color,
            box=BOX_STYLES["message"],
            padding=(0, 1),
        )

        if self.live:
            self.live.update(panel)
            self.live.refresh()


def textify_markdown_code_blocks(text: str) -> str:
    """
    Convert markdown code blocks to text-only format.

    This differentiates inline markdown code from actual CodeBlocks
    by removing syntax highlighting from markdown code snippets.
    """
    replacement = "```text"
    lines = text.split("\n")
    inside_code_block = False

    for i in range(len(lines)):
        # Match ``` followed by optional language specifier
        if re.match(r"^```(\w*)$", lines[i].strip()):
            inside_code_block = not inside_code_block

            # If entering a code block, replace the marker
            if inside_code_block:
                lines[i] = replacement

    return "\n".join(lines)
