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
        height: auto;
        min-height: 3;
        width: 100%;
        display: block;
    }

    MessageWidget.message-user {
        background: #1a3a5c;
        border-left: thick #58a6ff;
        color: #ffffff;
    }

    MessageWidget.message-assistant {
        background: #2a2a4e;
        border-left: thick #8b949e;
        color: #ffffff;
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
        # WHY: Track content ourselves because Textual's Markdown widget
        # doesn't expose the source content after update() calls.
        # TRADEOFF: Slight memory overhead vs ability to append content.
        self._content = markdown
        self.add_class("message")
        self.add_class(f"message-{role}")

    def watch_role(self, old_role: str, new_role: str) -> None:
        """Update styling when role changes."""
        if old_role:
            self.remove_class(f"message-{old_role}")
        self.add_class(f"message-{new_role}")

    def append(self, text: str) -> None:
        """Append text to the message (for streaming)."""
        self._content += text
        self.update(self._content)

    async def stream_content(self, content: str) -> None:
        """Update content for streaming display."""
        self._content = content
        self.update(content)
