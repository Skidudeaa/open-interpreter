"""
Message Widget - Markdown-rendered assistant/user messages.

Replaces components/message_block.py with Textual's Markdown widget.
"""

from textual.reactive import reactive
from textual.widgets import Markdown


class MessageWidget(Markdown):
    """
    Markdown-rendered message display.

    Features:
    - Full markdown rendering (headers, lists, code, etc.)
    - Role-based styling (user vs assistant)
    - Streaming support via reactive content

    CSS Classes:
    - .message - Base styling
    - .message-user - User messages
    - .message-assistant - Assistant messages
    """

    role: reactive[str] = reactive("assistant")

    DEFAULT_CSS = """
    MessageWidget {
        margin: 1 0;
        padding: 1;
    }

    MessageWidget.message-user {
        background: #58a6ff 10%;  /* $primary 10% */
        border-left: thick #58a6ff;  /* $primary */
    }

    MessageWidget.message-assistant {
        background: #1a1a2e;  /* $surface */
        border-left: thick #8b949e;  /* $secondary */
    }
    """

    def __init__(
        self,
        markdown: str = "",
        role: str = "assistant",
        *,
        name: str | None = None,
        id: str | None = None,
        classes: str | None = None,
    ):
        super().__init__(markdown, name=name, id=id, classes=classes)
        self.role = role
        self.add_class("message")
        self.add_class(f"message-{role}")

    def watch_role(self, old_role: str, new_role: str) -> None:
        """Update styling when role changes."""
        if old_role:
            self.remove_class(f"message-{old_role}")
        self.add_class(f"message-{new_role}")

    def append(self, text: str) -> None:
        """Append text to the message (for streaming)."""
        current = self.document.source if hasattr(self, "document") else ""
        self.update(current + text)

    async def stream_content(self, content: str) -> None:
        """Update content for streaming display."""
        self.update(content)
