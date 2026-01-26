"""
Command Palette - Fuzzy command search widget.

Triggered by `/` prefix. Provides fuzzy matching for magic commands
with descriptions and recent command prioritization.

Part of Phase 1: prompt_toolkit Integration
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from prompt_toolkit import Application
from prompt_toolkit.formatted_text import FormattedText
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import FormattedTextControl, HSplit, Layout, Window
from prompt_toolkit.layout.dimension import D
from prompt_toolkit.styles import Style

from .completers import MAGIC_COMMANDS
from .theme import THEME

if TYPE_CHECKING:
    from ...core.core import OpenInterpreter


@dataclass
class CommandEntry:
    """A command palette entry."""

    command: str
    description: str
    category: str = "magic"
    usage_count: int = 0
    score: float = 0.0


@dataclass
class PaletteState:
    """Internal state for the command palette."""

    query: str = ""
    selected_index: int = 0
    results: list[CommandEntry] = field(default_factory=list)
    is_visible: bool = False


class CommandPalette:
    """
    Fuzzy command search overlay.

    Features:
    - Fuzzy matching on command name and description
    - Recent commands shown first
    - Category grouping (magic, agent, system)
    - Keyboard navigation (up/down, Enter, Esc)
    """

    MAX_RESULTS = 10

    def __init__(
        self,
        interpreter: "OpenInterpreter | None" = None,
        on_select: Callable[[str], None] | None = None,
    ):
        self.interpreter = interpreter
        self.on_select = on_select
        self.state = PaletteState()
        self._commands: list[CommandEntry] = []
        self._build_command_registry()
        self._usage_counts: dict[str, int] = {}

    def _build_command_registry(self) -> None:
        """Build the command registry from available sources."""
        self._commands = []

        for cmd, desc in MAGIC_COMMANDS.items():
            self._commands.append(
                CommandEntry(command=cmd, description=desc, category="magic")
            )

        if self.interpreter and getattr(self.interpreter, "enable_agents", False):
            agent_commands = {
                "%agents": "List active agents",
                "%agent scout": "Run scout agent for codebase exploration",
                "%agent surgeon": "Run surgeon agent for precise edits",
                "%kill": "Kill active agent",
            }
            for cmd, desc in agent_commands.items():
                self._commands.append(
                    CommandEntry(command=cmd, description=desc, category="agent")
                )

        system_commands = {
            "%exit": "Exit interpreter",
            "%version": "Show version info",
            "%settings": "Show current settings",
        }
        for cmd, desc in system_commands.items():
            self._commands.append(
                CommandEntry(command=cmd, description=desc, category="system")
            )

    def _fuzzy_match(self, query: str, text: str) -> tuple[bool, float]:
        """Fuzzy match query against text. Returns (matches, score)."""
        if not query:
            return True, 0.5

        query = query.lower()
        text = text.lower()

        if text.startswith(query):
            return True, 1.0

        if query in text:
            pos = text.find(query)
            return True, 0.9 - (pos / len(text) * 0.3)

        qi = 0
        last_match_pos = -1
        gaps = 0

        for i, char in enumerate(text):
            if qi < len(query) and char == query[qi]:
                if last_match_pos >= 0:
                    gaps += i - last_match_pos - 1
                last_match_pos = i
                qi += 1

        if qi == len(query):
            gap_penalty = gaps / max(len(text), 1)
            return True, 0.6 - gap_penalty * 0.3

        return False, 0.0

    def search(self, query: str) -> list[CommandEntry]:
        """Search commands with fuzzy matching."""
        results = []

        for cmd in self._commands:
            cmd_match, cmd_score = self._fuzzy_match(query, cmd.command)
            desc_match, desc_score = self._fuzzy_match(query, cmd.description)

            if cmd_match or desc_match:
                score = max(cmd_score * 1.2, desc_score)
                usage = self._usage_counts.get(cmd.command, 0)
                if usage > 0:
                    score += min(usage * 0.1, 0.3)

                cmd_copy = CommandEntry(
                    command=cmd.command,
                    description=cmd.description,
                    category=cmd.category,
                    usage_count=usage,
                    score=score,
                )
                results.append(cmd_copy)

        results.sort(key=lambda x: x.score, reverse=True)
        return results[: self.MAX_RESULTS]

    def record_usage(self, command: str) -> None:
        """Record command usage for prioritization."""
        self._usage_counts[command] = self._usage_counts.get(command, 0) + 1

    def show(self) -> str | None:
        """Show the command palette and return selected command."""
        self.state = PaletteState(
            query="", selected_index=0, results=self.search(""), is_visible=True
        )

        selected_command: list[str | None] = [None]
        kb = KeyBindings()

        @kb.add("escape")
        def handle_escape(event):
            event.app.exit()

        @kb.add("enter")
        def handle_enter(event):
            if self.state.results:
                idx = self.state.selected_index
                if 0 <= idx < len(self.state.results):
                    selected_command[0] = self.state.results[idx].command
            event.app.exit()

        @kb.add("up")
        def handle_up(event):
            if self.state.selected_index > 0:
                self.state.selected_index -= 1

        @kb.add("down")
        def handle_down(event):
            if self.state.selected_index < len(self.state.results) - 1:
                self.state.selected_index += 1

        @kb.add("c-c")
        def handle_ctrl_c(event):
            event.app.exit()

        @kb.add("backspace")
        def handle_backspace(event):
            if self.state.query:
                self.state.query = self.state.query[:-1]
                self.state.results = self.search(self.state.query)
                self.state.selected_index = 0

        def get_results_text() -> FormattedText:
            parts = []
            if not self.state.results:
                parts.append(("class:muted", "  No matching commands\n"))
                return FormattedText(parts)

            for i, cmd in enumerate(self.state.results):
                is_selected = i == self.state.selected_index
                if is_selected:
                    parts.append(("class:selected", " > "))
                else:
                    parts.append(("", "   "))

                style = "class:command-selected" if is_selected else "class:command"
                parts.append((style, f"{cmd.command:15}"))

                desc_style = "class:desc-selected" if is_selected else "class:desc"
                parts.append((desc_style, f" {cmd.description}"))

                if cmd.category != "magic":
                    parts.append(("class:muted", f" [{cmd.category}]"))

                parts.append(("", "\n"))

            return FormattedText(parts)

        style = Style.from_dict(
            {
                "search-prompt": f"bold {THEME['primary']}",
                "command": f"bold {THEME['secondary']}",
                "command-selected": f"bold {THEME['primary']}",
                "desc": THEME["text_muted"],
                "desc-selected": THEME["text_secondary"],
                "muted": "italic " + THEME["text_muted"],
                "selected": f"bold {THEME['success']}",
                "border": THEME["text_muted"],
            }
        )

        layout = Layout(
            HSplit(
                [
                    Window(
                        content=FormattedTextControl(
                            lambda: FormattedText(
                                [("class:search-prompt", " / "), ("", self.state.query)]
                            )
                        ),
                        height=1,
                    ),
                    Window(
                        content=FormattedTextControl(lambda: "-" * 40),
                        height=1,
                        style="class:border",
                    ),
                    Window(
                        content=FormattedTextControl(get_results_text),
                        height=D(min=3, max=12),
                    ),
                    Window(
                        content=FormattedTextControl(
                            lambda: FormattedText(
                                [
                                    (
                                        "class:muted",
                                        " Up/Down Navigate  Enter Select  Esc Cancel",
                                    )
                                ]
                            )
                        ),
                        height=1,
                    ),
                ]
            )
        )

        @kb.add("<any>")
        def handle_any(event):
            char = event.data
            if char.isprintable() and len(char) == 1:
                self.state.query += char
                self.state.results = self.search(self.state.query)
                self.state.selected_index = 0

        app: Application = Application(
            layout=layout,
            key_bindings=kb,
            style=style,
            full_screen=False,
            mouse_support=False,
        )

        app.run()

        result = selected_command[0]
        if result:
            self.record_usage(result)
            if self.on_select:
                self.on_select(result)

        return result

    def get_commands(self, category: str | None = None) -> list[CommandEntry]:
        """Get all registered commands."""
        if category:
            return [c for c in self._commands if c.category == category]
        return self._commands.copy()

    def add_command(
        self, command: str, description: str, category: str = "custom"
    ) -> None:
        """Add a custom command to the palette."""
        self._commands.append(
            CommandEntry(command=command, description=description, category=category)
        )


def show_command_palette(
    interpreter: "OpenInterpreter | None" = None,
) -> str | None:
    """Convenience function to show the command palette."""
    palette = CommandPalette(interpreter)
    return palette.show()
