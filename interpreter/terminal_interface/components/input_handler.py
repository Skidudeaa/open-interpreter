"""
Input Handler and Key Bindings

Manages keyboard input, key bindings, and input session for the terminal UI.
Provides configurable keymaps with fallbacks for terminal portability.

Part of Phase 1: prompt_toolkit Integration

Usage:
    handler = InputHandler(interpreter, state)
    kb = handler.create_key_bindings()
    session = handler.create_prompt_session()
"""

from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum, auto
from typing import TYPE_CHECKING

from prompt_toolkit import PromptSession
from prompt_toolkit.auto_suggest import AutoSuggestFromHistory
from prompt_toolkit.history import FileHistory, InMemoryHistory
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.lexers import PygmentsLexer

from .ui_events import EventType, UIEvent, get_event_bus
from .ui_state import UIMode, UIState

if TYPE_CHECKING:
    from ...core.core import OpenInterpreter

try:
    from pygments.lexers.python import PythonLexer
    HAS_PYGMENTS = True
except ImportError:
    HAS_PYGMENTS = False


class KeyAction(Enum):
    """Available key binding actions"""
    CANCEL = auto()           # Cancel current operation
    CLEAR = auto()            # Clear screen
    EXIT = auto()             # Exit application
    SUBMIT = auto()           # Submit input
    NEWLINE = auto()          # Insert newline
    MODE_TOGGLE = auto()      # Toggle UI mode
    MODE_ZEN = auto()         # Switch to zen mode
    MODE_POWER = auto()       # Switch to power mode
    MODE_DEBUG = auto()       # Switch to debug mode
    HISTORY_SEARCH = auto()   # Search history
    COMPLETE = auto()         # Trigger completion
    AGENT_FOCUS = auto()      # Focus agent strip
    PANEL_TOGGLE = auto()     # Toggle context panel
    HELP = auto()             # Show help


@dataclass
class KeyBinding:
    """A key binding configuration"""
    action: KeyAction
    primary: str              # Primary key (e.g., 'c-l')
    fallback: str | None = None  # Fallback key (e.g., 'f5')
    description: str = ""


# Default key bindings with fallbacks
DEFAULT_BINDINGS: dict[KeyAction, KeyBinding] = {
    KeyAction.CANCEL: KeyBinding(
        action=KeyAction.CANCEL,
        primary='escape',
        description='Cancel current operation'
    ),
    KeyAction.CLEAR: KeyBinding(
        action=KeyAction.CLEAR,
        primary='c-l',
        description='Clear screen'
    ),
    KeyAction.EXIT: KeyBinding(
        action=KeyAction.EXIT,
        primary='c-d',
        description='Exit (on empty input)'
    ),
    KeyAction.SUBMIT: KeyBinding(
        action=KeyAction.SUBMIT,
        primary='enter',
        fallback='c-j',
        description='Submit input'
    ),
    KeyAction.NEWLINE: KeyBinding(
        action=KeyAction.NEWLINE,
        primary='escape enter',  # Alt+Enter
        fallback='c-o',
        description='Insert newline'
    ),
    KeyAction.MODE_TOGGLE: KeyBinding(
        action=KeyAction.MODE_TOGGLE,
        primary='escape p',  # Alt+P
        fallback='f2',
        description='Toggle UI mode'
    ),
    KeyAction.HISTORY_SEARCH: KeyBinding(
        action=KeyAction.HISTORY_SEARCH,
        primary='c-r',
        description='Search history'
    ),
    KeyAction.COMPLETE: KeyBinding(
        action=KeyAction.COMPLETE,
        primary='c-space',
        fallback='tab',
        description='Trigger completion'
    ),
    KeyAction.AGENT_FOCUS: KeyBinding(
        action=KeyAction.AGENT_FOCUS,
        primary='escape a',  # Alt+A
        fallback='f4',
        description='Focus agent strip'
    ),
    KeyAction.PANEL_TOGGLE: KeyBinding(
        action=KeyAction.PANEL_TOGGLE,
        primary='escape h',  # Alt+H
        fallback='f3',
        description='Toggle context panel'
    ),
    KeyAction.HELP: KeyBinding(
        action=KeyAction.HELP,
        primary='f1',
        description='Show help'
    ),
}


class InputHandler:
    """
    Manages input handling and key bindings.

    Features:
    - Configurable key bindings with fallbacks
    - Vi/Emacs editing mode support
    - History with persistence
    - Auto-suggestions from history
    - Syntax highlighting (optional)
    """

    def __init__(
        self,
        interpreter: "OpenInterpreter",
        state: UIState,
        history_file: str | None = None,
        editing_mode: str = "emacs",  # "emacs" or "vi"
    ):
        self.interpreter = interpreter
        self.state = state
        self.event_bus = get_event_bus()

        # History
        if history_file:
            self.history = FileHistory(history_file)
        else:
            self.history = InMemoryHistory()

        # Editing mode
        self.editing_mode = editing_mode

        # Custom key bindings
        self.bindings = DEFAULT_BINDINGS.copy()

        # Callbacks
        self._on_cancel: Callable[[], None] | None = None
        self._on_submit: Callable[[str], None] | None = None
        self._on_mode_change: Callable[[UIMode], None] | None = None

    def set_binding(self, action: KeyAction, primary: str, fallback: str | None = None) -> None:
        """Override a key binding"""
        if action in self.bindings:
            self.bindings[action] = KeyBinding(
                action=action,
                primary=primary,
                fallback=fallback,
                description=self.bindings[action].description
            )

    def create_key_bindings(self) -> KeyBindings:
        """Create prompt_toolkit key bindings"""
        kb = KeyBindings()

        # Helpers to keep multiline behavior predictable
        def _should_insert_newline(text: str) -> bool:
            """
            Treat Enter as submit for single-line input, but keep multiline editing
            when the user has started a block (triple quotes) or already has newlines.
            """
            if not text:
                return False
            if text.startswith('"""') and not text.endswith('"""'):
                return True
            return "\n" in text or text.startswith('"""')

        submit_binding = self.bindings.get(KeyAction.SUBMIT)
        newline_binding = self.bindings.get(KeyAction.NEWLINE)

        def _bind_keys(binding: KeyBinding | None, handler) -> None:
            """
            Register a binding's primary/fallback keys (supports sequences like
            'escape enter' to avoid prompt_toolkit parse errors).
            """
            if not binding:
                return

            def _add(key_spec: str):
                keys = key_spec.split() if isinstance(key_spec, str) else (key_spec,)
                kb.add(*keys)(handler)

            _add(binding.primary)
            if binding.fallback:
                _add(binding.fallback)

        def submit_or_newline(event):
            buffer = event.current_buffer
            text = buffer.text

            if _should_insert_newline(text):
                buffer.insert_text("\n")
                return

            if self._on_submit:
                self._on_submit(text)
            buffer.validate_and_handle()

        # Submit: Enter (and fallback)
        _bind_keys(submit_binding, submit_or_newline)

        # Explicit newline: Alt+Enter / Ctrl+O
        def insert_newline(event):
            event.current_buffer.insert_text("\n")

        _bind_keys(newline_binding, insert_newline)

        # Cancel
        @kb.add('escape')
        def cancel(event):
            if self._on_cancel:
                self._on_cancel()
            self.event_bus.emit(UIEvent(type=EventType.UI_CANCEL, source="input_handler"))

        # Clear screen
        @kb.add('c-l')
        def clear(event):
            event.app.renderer.clear()

        # Exit on Ctrl+D with empty buffer
        @kb.add('c-d')
        def exit_app(event):
            if not event.current_buffer.text:
                event.app.exit()

        # Mode toggle - Alt+P (as escape sequence) and F2
        @kb.add('escape', 'p')
        @kb.add('f2')
        def toggle_mode(event):
            self._cycle_mode()

        # Agent focus - Alt+A and F4
        @kb.add('escape', 'a')
        @kb.add('f4')
        def focus_agents(event):
            self.event_bus.emit(UIEvent(
                type=EventType.UI_PANEL_TOGGLE,
                data={"panel": "agents"},
                source="input_handler"
            ))

        # Panel toggle - Alt+H and F3
        @kb.add('escape', 'h')
        @kb.add('f3')
        def toggle_panel(event):
            if "context" in self.state.panels_visible:
                self.state.panels_visible.remove("context")
            else:
                self.state.panels_visible.add("context")
            self.event_bus.emit(UIEvent(
                type=EventType.UI_PANEL_TOGGLE,
                data={"panel": "context", "visible": "context" in self.state.panels_visible},
                source="input_handler"
            ))
            event.app.invalidate()

        # Help
        @kb.add('f1')
        def show_help(event):
            # TODO: Show help overlay
            pass

        return kb

    def _cycle_mode(self) -> None:
        """Cycle through UI modes"""
        modes = [UIMode.ZEN, UIMode.STANDARD, UIMode.POWER, UIMode.DEBUG]
        current_idx = modes.index(self.state.mode)
        next_idx = (current_idx + 1) % len(modes)
        self.state.mode = modes[next_idx]

        if self._on_mode_change:
            self._on_mode_change(self.state.mode)

        self.event_bus.emit(UIEvent(
            type=EventType.UI_MODE_CHANGE,
            data={"mode": self.state.mode.name},
            source="input_handler"
        ))

    def create_prompt_session(self) -> PromptSession:
        """
        Create a prompt_toolkit PromptSession for input.

        Features:
        - Multiline editing
        - History with auto-suggest
        - Optional syntax highlighting
        - Vi/Emacs mode
        """
        from prompt_toolkit.enums import EditingMode

        # Lexer for syntax highlighting
        lexer = None
        if HAS_PYGMENTS:
            lexer = PygmentsLexer(PythonLexer)

        # Editing mode
        edit_mode = (
            EditingMode.VI if self.editing_mode == "vi"
            else EditingMode.EMACS
        )

        session = PromptSession(
            history=self.history,
            auto_suggest=AutoSuggestFromHistory(),
            lexer=lexer,
            multiline=True,
            key_bindings=self.create_key_bindings(),
            editing_mode=edit_mode,
            enable_history_search=True,
            complete_while_typing=False,
        )

        return session

    def get_input(self, prompt: str = "❯ ") -> str:
        """
        Get user input using prompt_toolkit.

        This is a simpler interface that doesn't require the full Application.
        Useful for one-shot prompts.

        Args:
            prompt: The prompt string to display

        Returns:
            User input string
        """
        session = self.create_prompt_session()
        return session.prompt(prompt)

    def set_cancel_handler(self, handler: Callable[[], None]) -> None:
        """Set callback for cancel action"""
        self._on_cancel = handler

    def set_submit_handler(self, handler: Callable[[str], None]) -> None:
        """Set callback for submit action"""
        self._on_submit = handler

    def set_mode_change_handler(self, handler: Callable[[UIMode], None]) -> None:
        """Set callback for mode change"""
        self._on_mode_change = handler

    def get_binding_help(self) -> str:
        """Get help text for all key bindings"""
        lines = ["Key Bindings:", ""]
        for action, binding in self.bindings.items():
            key_str = binding.primary
            if binding.fallback:
                key_str += f" / {binding.fallback}"
            lines.append(f"  {key_str:20} {binding.description}")
        return "\n".join(lines)


def create_input_handler(
    interpreter: "OpenInterpreter",
    state: UIState,
    history_file: str | None = None,
) -> InputHandler:
    """
    Factory function to create an InputHandler.

    Args:
        interpreter: The OpenInterpreter instance
        state: The UIState instance
        history_file: Optional path to history file

    Returns:
        Configured InputHandler instance
    """
    return InputHandler(interpreter, state, history_file)
