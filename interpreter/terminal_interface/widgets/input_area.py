"""
Input Area - Text input with multiline support and completions.

Replaces prompt_toolkit input handling with Textual's Input widget.
"""

from collections.abc import Callable

from textual.message import Message
from textual.reactive import reactive
from textual.widgets import Input


class InputArea(Input):
    """
    User input area with multiline and completion support.

    Features:
    - Single-line by default, multiline with triple-quotes
    - Magic command completion (%)
    - File path completion (@)
    - History navigation (up/down)
    - Submit on Enter

    Messages:
    - InputArea.UserSubmitted - Fired after input is processed (use this in parent)

    CSS Classes:
    - .input-area - Base styling
    - .multiline - When in multiline mode
    """

    class UserSubmitted(Message):
        """
        Fired when user submits processed input.

        NOTE: Named UserSubmitted (not Submitted) to avoid conflicting with
        Input.Submitted which has a different signature.
        """

        def __init__(self, value: str) -> None:
            self.value = value
            super().__init__()

    is_multiline: reactive[bool] = reactive(False)

    DEFAULT_CSS = """
    InputArea {
        dock: bottom;
        height: auto;
        min-height: 1;
        max-height: 10;
        margin: 1 0;
        padding: 0 1;
        border: round #58a6ff;  /* $primary */
    }

    InputArea:focus {
        border: round #a855f7;  /* $accent */
    }

    InputArea.multiline {
        min-height: 3;
    }
    """

    def __init__(
        self,
        placeholder: str = "Enter message or % for commands...",
        *,
        name: str | None = None,
        id: str | None = None,
        classes: str | None = None,
    ):
        super().__init__(
            placeholder=placeholder,
            name=name,
            id=id,
            classes=classes,
        )
        self._history: list[str] = []
        self._history_index: int = -1
        self._on_submit: Callable[[str], None] | None = None
        self.add_class("input-area")

    def on_input_submitted(self, event: Input.Submitted) -> None:
        """Handle input submission from parent Input class."""
        text = event.value.strip()
        if not text:
            return

        # Check for multiline mode trigger
        if text == '"""':
            self.is_multiline = True
            self.add_class("multiline")
            return

        # Close multiline mode
        if self.is_multiline and text.endswith('"""'):
            self.is_multiline = False
            self.remove_class("multiline")
            text = text[:-3].strip()

        # Add to history
        if text and (not self._history or self._history[-1] != text):
            self._history.append(text)
        self._history_index = -1

        # Clear input
        self.value = ""

        # Post our own event for parent handlers
        self.post_message(self.UserSubmitted(text))

        # Callback (alternative to message-based handling)
        if self._on_submit:
            self._on_submit(text)

    def set_submit_handler(self, handler: Callable[[str], None]) -> None:
        """Set callback for input submission."""
        self._on_submit = handler

    def action_history_prev(self) -> None:
        """Navigate to previous history entry."""
        if not self._history:
            return
        if self._history_index < len(self._history) - 1:
            self._history_index += 1
            self.value = self._history[-(self._history_index + 1)]

    def action_history_next(self) -> None:
        """Navigate to next history entry."""
        if self._history_index > 0:
            self._history_index -= 1
            self.value = self._history[-(self._history_index + 1)]
        elif self._history_index == 0:
            self._history_index = -1
            self.value = ""

    def add_to_history(self, text: str) -> None:
        """Add entry to history."""
        if text and (not self._history or self._history[-1] != text):
            self._history.append(text)
