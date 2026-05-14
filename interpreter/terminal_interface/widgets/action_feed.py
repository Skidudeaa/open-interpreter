"""Textual action feed for interpreter and agent visibility."""

from collections import deque
from dataclasses import dataclass

from rich.text import Text
from textual.widget import Widget


@dataclass(frozen=True)
class ActionFeedEntry:
    """A single visible action in the Textual feed."""

    message: str
    detail: str = ""


class ActionFeedWidget(Widget):
    """Compact rolling list of recent interpreter actions."""

    DEFAULT_CSS = """
    ActionFeedWidget {
        dock: bottom;
        height: 4;
        min-height: 1;
        background: $surface;
        border-top: solid $secondary;
        padding: 0 1;
    }

    ActionFeedWidget.hidden {
        display: none;
    }
    """

    def __init__(
        self,
        max_actions: int = 4,
        *,
        name: str | None = None,
        id: str | None = None,
        classes: str | None = None,
    ):
        super().__init__(name=name, id=id, classes=classes)
        self.max_actions = max_actions
        self._actions: deque[ActionFeedEntry] = deque(maxlen=max_actions)

    def add_action(self, message: str, detail: str = "") -> None:
        """Record an action and refresh the feed."""
        message = message.strip()
        detail = detail.strip()
        if not message:
            return
        entry = ActionFeedEntry(message=message, detail=detail)
        if self._actions and self._actions[-1] == entry:
            return
        self._actions.append(entry)
        self.refresh()

    def clear(self) -> None:
        """Clear all recorded actions."""
        self._actions.clear()
        self.refresh()

    def render(self) -> Text:
        text = Text()
        if not self._actions:
            text.append("actions: idle", style="dim")
            return text

        text.append("actions", style="bold")
        for action in self._actions:
            text.append("\n> ", style="dim")
            text.append(action.message, style="cyan")
            if action.detail:
                text.append(f" ({action.detail})", style="dim")
        return text
