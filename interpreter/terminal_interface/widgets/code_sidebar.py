"""
CodeSidebar Widget - Collapsible code panel for streaming code.

ARCHITECTURE: Docked right panel that receives CODE_* events and displays
code blocks stacked vertically. Decouples code display from main output.

WHY: Reduces cognitive load during execution by keeping code accessible
but not dominant. User can collapse to focus on activity timeline.

TRADEOFF: Code streams to separate panel vs. inline - requires toggle
(Alt+C) to view but keeps main area clean.

Part of Activity Timeline UI feature.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from rich.syntax import Syntax
from rich.text import Text
from textual.app import ComposeResult
from textual.containers import ScrollableContainer, Vertical
from textual.reactive import reactive
from textual.widgets import Static

from ..components.ui_events import EventType, UIEvent, get_event_bus

if TYPE_CHECKING:
    pass


class CodeBlockStatic(Static):
    """
    A single code block within the sidebar.

    Displays syntax-highlighted code with a language header.
    Supports streaming (code appended incrementally).
    """

    DEFAULT_CSS = """
    CodeBlockStatic {
        margin: 0 0 1 0;
        padding: 0;
        width: 100%;
    }

    CodeBlockStatic .code-header {
        background: $surface-darken-2;
        padding: 0 1;
        height: 1;
    }

    CodeBlockStatic .code-content {
        background: $surface-darken-1;
        padding: 0 1;
    }
    """

    code: reactive[str] = reactive("", layout=True)
    language: reactive[str] = reactive("python")

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
        self.code = code
        self.language = language

    def compose(self) -> ComposeResult:
        """Create child widgets."""
        yield Static(id="header", classes="code-header")
        yield Static(id="content", classes="code-content")

    def on_mount(self) -> None:
        """Initialize the display."""
        self._update_display()

    def watch_code(self, _old: str, _new: str) -> None:
        """React to code changes."""
        self._update_display()

    def watch_language(self, _old: str, _new: str) -> None:
        """React to language changes."""
        self._update_display()

    def _update_display(self) -> None:
        """Update the header and content display."""
        try:
            # Update header
            header = self.query_one("#header", Static)
            header_text = Text()
            header_text.append("▼ ", style="dim")
            header_text.append(self.language, style="cyan bold")
            line_count = len(self.code.splitlines()) if self.code else 0
            header_text.append(f" ({line_count} lines)", style="dim")
            header.update(header_text)

            # Update content with syntax highlighting
            content = self.query_one("#content", Static)
            if self.code:
                syntax = Syntax(
                    self.code,
                    self.language,
                    theme="monokai",
                    line_numbers=True,
                    word_wrap=False,
                )
                content.update(syntax)
            else:
                content.update(Text("(no code)", style="dim italic"))
        except Exception:
            # Widget not fully mounted yet
            pass

    def append_code(self, chunk: str) -> None:
        """Append code chunk (for streaming)."""
        self.code = self.code + chunk


class CodeSidebarWidget(Vertical):
    """
    Collapsible code sidebar panel.

    Receives CODE_START/CODE_CHUNK/CODE_END events and displays code blocks.
    Toggle visibility with Alt+C (CSS class toggle).

    Features:
    - Stacks code blocks vertically
    - Auto-scrolls to latest code
    - Shows language and line count
    - Syntax highlighting via Rich
    """

    DEFAULT_CSS = """
    CodeSidebarWidget {
        dock: right;
        width: 40%;
        min-width: 30;
        max-width: 80;
        background: $surface;
        border-left: solid $secondary;
        display: block;
    }

    CodeSidebarWidget.-hidden {
        display: none;
    }

    CodeSidebarWidget #sidebar-header {
        height: 1;
        background: $surface-darken-1;
        padding: 0 1;
        dock: top;
    }

    CodeSidebarWidget #code-container {
        height: 1fr;
        padding: 1;
        overflow-y: auto;
    }

    CodeSidebarWidget #sidebar-footer {
        height: 1;
        background: $surface-darken-1;
        padding: 0 1;
        dock: bottom;
    }
    """

    # Reactive state
    is_visible: reactive[bool] = reactive(True)
    block_count: reactive[int] = reactive(0)

    def __init__(
        self,
        *,
        name: str | None = None,
        id: str | None = None,
        classes: str | None = None,
    ):
        super().__init__(name=name, id=id, classes=classes)
        self._event_bus = get_event_bus()

        # Track current code block being streamed
        self._current_block_id: str | None = None
        self._code_blocks: dict[str, CodeBlockStatic] = {}

    def compose(self) -> ComposeResult:
        """Create child widgets."""
        yield Static("▼ Code", id="sidebar-header")
        yield ScrollableContainer(id="code-container")
        yield Static("[Alt+C] toggle", id="sidebar-footer")

    def on_mount(self) -> None:
        """Subscribe to code events."""
        self._event_bus.subscribe(EventType.CODE_START, self._on_code_start)
        self._event_bus.subscribe(EventType.CODE_CHUNK, self._on_code_chunk)
        self._event_bus.subscribe(EventType.CODE_END, self._on_code_end)

    def on_unmount(self) -> None:
        """Unsubscribe from events."""
        self._event_bus.unsubscribe(EventType.CODE_START, self._on_code_start)
        self._event_bus.unsubscribe(EventType.CODE_CHUNK, self._on_code_chunk)
        self._event_bus.unsubscribe(EventType.CODE_END, self._on_code_end)

    def _on_code_start(self, event: UIEvent) -> None:
        """Handle new code block start."""
        language = event.data.get("language", "python")
        block_id = str(uuid.uuid4())[:8]

        # Create new code block widget
        block = CodeBlockStatic(code="", language=language, id=f"code-{block_id}")
        self._code_blocks[block_id] = block
        self._current_block_id = block_id

        # Mount to container (thread-safe)
        self.app.call_from_thread(self._mount_block, block)

        # Emit code_block_id back for timeline linking
        event.data["code_block_id"] = block_id

    def _mount_block(self, block: CodeBlockStatic) -> None:
        """Mount a code block to the container (must run in main thread)."""
        try:
            container = self.query_one("#code-container", ScrollableContainer)
            container.mount(block)
            self.block_count = len(self._code_blocks)

            # Auto-scroll to new block
            self.call_after_refresh(lambda: container.scroll_end(animate=False))
        except Exception:
            pass  # Widget not ready

    def _on_code_chunk(self, event: UIEvent) -> None:
        """Handle code chunk (streaming)."""
        if not self._current_block_id:
            return

        content = event.data.get("content", "")
        block = self._code_blocks.get(self._current_block_id)
        if block and content:
            self.app.call_from_thread(block.append_code, content)

    def _on_code_end(self, _event: UIEvent) -> None:
        """Handle code block end."""
        self._current_block_id = None

    def watch_is_visible(self, visible: bool) -> None:
        """React to visibility changes."""
        if visible:
            self.remove_class("-hidden")
        else:
            self.add_class("-hidden")

    def toggle_visibility(self) -> None:
        """Toggle sidebar visibility."""
        self.is_visible = not self.is_visible

    def add_code_block(self, code: str, language: str = "python") -> str:
        """
        Add a complete code block (not streaming).

        Returns the block ID.
        """
        block_id = str(uuid.uuid4())[:8]
        block = CodeBlockStatic(code=code, language=language, id=f"code-{block_id}")
        self._code_blocks[block_id] = block

        self.app.call_from_thread(self._mount_block, block)
        return block_id

    def append_to_current_block(self, code: str) -> None:
        """Append code to the current streaming block."""
        if self._current_block_id:
            block = self._code_blocks.get(self._current_block_id)
            if block:
                self.app.call_from_thread(block.append_code, code)

    def get_current_block_id(self) -> str | None:
        """Get the ID of the current streaming block."""
        return self._current_block_id

    def clear_blocks(self) -> None:
        """Remove all code blocks."""
        self._code_blocks.clear()
        self._current_block_id = None
        try:
            container = self.query_one("#code-container", ScrollableContainer)
            container.remove_children()
        except Exception:
            pass
        self.block_count = 0
