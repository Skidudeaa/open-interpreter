"""
Output Panel - Scrollable container for conversation history.

Main display area that holds message and code blocks.
Auto-scrolls to bottom on new content.
"""

from textual.containers import VerticalScroll
from textual.reactive import reactive


class OutputPanel(VerticalScroll):
    """
    Scrollable output container for conversation.

    Features:
    - Auto-scroll to bottom on new messages
    - Contains MessageWidget and CodeBlockWidget instances
    - Keyboard navigation (j/k to scroll, Space to fold/unfold)

    Usage:
        panel = OutputPanel()
        panel.mount(MessageWidget("Hello", role="user"))
        panel.mount(CodeBlockWidget("print('hi')", language="python"))
    """

    auto_scroll: reactive[bool] = reactive(True)

    DEFAULT_CSS = """
    OutputPanel {
        height: 1fr;
        min-height: 10;
        width: 100%;
        scrollbar-gutter: stable;
        padding: 0 1;
        overflow-y: auto;
    }
    """

    BINDINGS = [
        ("j", "scroll_down", "Scroll Down"),
        ("k", "scroll_up", "Scroll Up"),
        ("g", "scroll_home", "Top"),
        ("G", "scroll_end", "Bottom"),
        ("space", "toggle_fold", "Fold/Unfold"),
    ]

    def __init__(
        self,
        *,
        name: str | None = None,
        id: str | None = None,
        classes: str | None = None,
    ):
        super().__init__(name=name, id=id, classes=classes)

    def on_mount(self) -> None:
        """Set up panel on mount."""
        self.scroll_end(animate=False)

    async def watch_children(self) -> None:
        """Auto-scroll when new children added."""
        if self.auto_scroll:
            self.scroll_end(animate=True)

    def action_scroll_down(self) -> None:
        """Scroll down one line."""
        self.scroll_relative(y=3)

    def action_scroll_up(self) -> None:
        """Scroll up one line."""
        self.scroll_relative(y=-3)

    def action_toggle_fold(self) -> None:
        """Toggle fold on focused code block."""
        from .code_block import CodeBlockWidget

        # Find focused or last code block
        code_blocks = self.query(CodeBlockWidget)
        if code_blocks:
            code_blocks.last().toggle_fold()

    def add_message(self, content: str, role: str = "assistant") -> None:
        """Convenience method to add a message."""
        from .message_block import MessageWidget

        widget = MessageWidget(content, role=role)
        self.mount(widget)
        if self.auto_scroll:
            self.scroll_end(animate=True)

    def add_code(self, code: str, language: str = "python") -> None:
        """Convenience method to add a code block."""
        from .code_block import CodeBlockWidget

        widget = CodeBlockWidget(code, language)
        self.mount(widget)
        if self.auto_scroll:
            self.scroll_end(animate=True)

    def clear(self) -> None:
        """Remove all children."""
        self.remove_children()
