"""
UI Backend Abstraction

Defines the interface for terminal UI backends and provides two implementations:
1. RichStreamBackend - Current Rich-based streaming (fallback for pipes/CI)
2. PromptToolkitBackend - Interactive TUI with key bindings (Phase 1)

Part of Phase 0: Foundation (must be implemented before other UI phases)

Usage:
    # Auto-select based on terminal capabilities
    backend = create_backend(interpreter, state)

    # Or force a specific backend
    backend = RichStreamBackend(interpreter, state)
    backend = PromptToolkitBackend(interpreter, state)

    # Run the UI
    backend.start()
    try:
        for event in event_stream:
            backend.emit(event)
    finally:
        backend.stop()
"""

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Optional, Callable, Any
from enum import Enum, auto
import sys
import os

from .ui_state import UIState, UIMode
from .ui_events import UIEvent, EventType, EventBus, get_event_bus

if TYPE_CHECKING:
    from ...core.core import OpenInterpreter


class BackendType(Enum):
    """Available UI backend implementations"""
    RICH_STREAM = auto()      # Current behavior: Rich streaming
    PROMPT_TOOLKIT = auto()   # Interactive: prompt_toolkit app


class UIBackend(ABC):
    """
    Abstract base class for terminal UI backends.

    A backend owns the terminal screen and is responsible for:
    1. Rendering UI components
    2. Handling user input
    3. Processing events from the interpreter
    4. Managing the main UI loop

    IMPORTANT: Only ONE backend should be active at a time.
    The backend "owns" the terminal - this is the key architectural insight.
    """

    def __init__(self, interpreter: "OpenInterpreter", state: UIState):
        self.interpreter = interpreter
        self.state = state
        self.event_bus = get_event_bus()
        self._running = False
        self._on_input: Optional[Callable[[str], None]] = None

    @property
    def backend_type(self) -> BackendType:
        """Return the type of this backend"""
        raise NotImplementedError

    @property
    def supports_interactive(self) -> bool:
        """True if this backend supports key bindings and interactive features"""
        return False

    @abstractmethod
    def start(self) -> None:
        """
        Initialize and start the backend.

        Called once at the beginning of a session.
        Should set up the terminal, event handlers, etc.
        """
        pass

    @abstractmethod
    def stop(self) -> None:
        """
        Shutdown the backend.

        Called when the session ends.
        Should restore terminal state, clean up resources.
        """
        pass

    @abstractmethod
    def emit(self, event: UIEvent) -> None:
        """
        Process a UI event.

        Called for each event from the interpreter.
        Should update the display accordingly.
        """
        pass

    @abstractmethod
    def invalidate(self) -> None:
        """
        Request a redraw.

        Called when state changes that require visual update.
        May be rate-limited by the backend.
        """
        pass

    @abstractmethod
    def get_input(self, prompt: str = "") -> str:
        """
        Get user input.

        Blocks until user provides input.
        For interactive backends, this uses the full input system.
        For streaming backends, this falls back to basic input().
        """
        pass

    def set_input_handler(self, handler: Callable[[str], None]) -> None:
        """Set callback for when user provides input"""
        self._on_input = handler

    def set_mode(self, mode: UIMode) -> None:
        """Change the UI display mode"""
        self.state.mode = mode
        self.invalidate()

    def cancel_current(self) -> None:
        """Cancel any current operation (called on Esc)"""
        if hasattr(self.interpreter, 'stop'):
            self.interpreter.stop()
        self.emit(UIEvent(type=EventType.UI_CANCEL, source="ui"))


class RichStreamBackend(UIBackend):
    """
    Rich-based streaming backend.

    This is the current/legacy behavior:
    - Rich Live displays for each block
    - No global key bindings
    - Basic input() for user input

    Used as fallback when:
    - Terminal doesn't support interactive features
    - Running in a pipe (not a TTY)
    - User requests --no-tui flag
    - CI/testing environment
    """

    def __init__(self, interpreter: "OpenInterpreter", state: UIState):
        super().__init__(interpreter, state)
        self._console = None
        self._live = None

    @property
    def backend_type(self) -> BackendType:
        return BackendType.RICH_STREAM

    @property
    def supports_interactive(self) -> bool:
        return False

    def start(self) -> None:
        """Initialize Rich console"""
        from rich.console import Console

        self._running = True
        self._console = Console()

        # Subscribe to events for state updates
        self.event_bus.subscribe_all(self._on_event)

    def stop(self) -> None:
        """Clean up Rich resources"""
        self._running = False
        self.event_bus.unsubscribe_all(self._on_event)

        if self._live:
            try:
                self._live.stop()
            except Exception:
                pass
            self._live = None

    def emit(self, event: UIEvent) -> None:
        """
        Process event in Rich streaming mode.

        Delegates to the existing component rendering system.
        This is a bridge to maintain backwards compatibility.
        """
        # Update state based on event
        self._update_state(event)

        # The actual rendering is still done by the legacy terminal_interface
        # This backend just manages state; rendering bridge comes later

    def _update_state(self, event: UIEvent) -> None:
        """Update UI state based on event"""
        if event.type == EventType.AGENT_SPAWN:
            from .ui_state import AgentRole
            agent_id = event.data.get("agent_id", "unknown")
            role_str = event.data.get("role", "custom")
            try:
                role = AgentRole(role_str)
            except ValueError:
                role = AgentRole.CUSTOM
            self.state.add_agent(agent_id, role)

        elif event.type == EventType.AGENT_COMPLETE:
            from .ui_state import AgentStatus
            agent_id = event.data.get("agent_id")
            if agent_id:
                self.state.update_agent_status(agent_id, AgentStatus.COMPLETE)

        elif event.type == EventType.AGENT_ERROR:
            from .ui_state import AgentStatus
            agent_id = event.data.get("agent_id")
            error = event.data.get("error")
            if agent_id:
                self.state.update_agent_status(agent_id, AgentStatus.ERROR, error)

        elif event.type == EventType.SYSTEM_TOKEN_UPDATE:
            tokens = event.data.get("tokens", 0)
            self.state.context_tokens = tokens

        elif event.type == EventType.SYSTEM_START:
            self.state.is_responding = True

        elif event.type == EventType.SYSTEM_END:
            self.state.is_responding = False

    def _on_event(self, event: UIEvent) -> None:
        """Global event handler for state updates"""
        self._update_state(event)

    def invalidate(self) -> None:
        """Request redraw (no-op for stream backend, handled by Live)"""
        pass

    def get_input(self, prompt: str = "") -> str:
        """Get input using basic readline"""
        # Use the existing cli_input for multiline support
        try:
            from ..utils.cli_input import cli_input
            return cli_input(prompt)
        except ImportError:
            return input(prompt)


class PromptToolkitBackend(UIBackend):
    """
    prompt_toolkit-based interactive backend.

    Features:
    - prompt_toolkit owns the screen
    - Rich renders to ANSI, displayed in PT windows
    - Full key bindings (Esc, Ctrl+R, Alt+P, F2, etc.)
    - Multiline input with syntax highlighting
    - History with auto-suggestions
    - Magic command completion

    Phase 1 Implementation.
    """

    def __init__(self, interpreter: "OpenInterpreter", state: UIState):
        super().__init__(interpreter, state)
        self._session = None
        self._input_handler = None
        self._output_buffer = []

    @property
    def backend_type(self) -> BackendType:
        return BackendType.PROMPT_TOOLKIT

    @property
    def supports_interactive(self) -> bool:
        return True

    def start(self) -> None:
        """Initialize prompt_toolkit session and handlers"""
        from .input_handler import InputHandler
        from .completers import create_completer

        self._running = True

        # Create input handler with key bindings
        self._input_handler = InputHandler(
            self.interpreter,
            self.state,
            history_file=self._get_history_path(),
        )

        # Set up cancel handler
        self._input_handler.set_cancel_handler(self._on_cancel)

        # Subscribe to events
        self.event_bus.subscribe_all(self._on_event)

    def _get_history_path(self) -> str:
        """Get path to history file"""
        from platformdirs import user_data_dir
        data_dir = user_data_dir("open-interpreter", "openinterpreter")
        os.makedirs(data_dir, exist_ok=True)
        return os.path.join(data_dir, "prompt_history")

    def _on_cancel(self) -> None:
        """Handle cancel action"""
        self.cancel_current()

    def stop(self) -> None:
        """Shutdown prompt_toolkit resources"""
        self._running = False
        self.event_bus.unsubscribe_all(self._on_event)
        self._session = None
        self._input_handler = None

    def emit(self, event: UIEvent) -> None:
        """
        Process UI event.

        Updates state and buffers output for display.
        """
        self._update_state(event)

        # Buffer output for display
        if event.type == EventType.MESSAGE_CHUNK:
            content = event.data.get("content", "")
            if content:
                self._output_buffer.append(content)

        elif event.type == EventType.CODE_CHUNK:
            content = event.data.get("content", "")
            if content:
                self._output_buffer.append(content)

        elif event.type == EventType.CONSOLE_OUTPUT:
            content = event.data.get("content", "")
            if content:
                self._output_buffer.append(content)

    def _update_state(self, event: UIEvent) -> None:
        """Update UI state based on event"""
        from .ui_state import AgentRole, AgentStatus

        if event.type == EventType.AGENT_SPAWN:
            agent_id = event.data.get("agent_id", "unknown")
            role_str = event.data.get("role", "custom")
            try:
                role = AgentRole(role_str)
            except ValueError:
                role = AgentRole.CUSTOM
            self.state.add_agent(agent_id, role)

        elif event.type == EventType.AGENT_COMPLETE:
            agent_id = event.data.get("agent_id")
            if agent_id:
                self.state.update_agent_status(agent_id, AgentStatus.COMPLETE)

        elif event.type == EventType.AGENT_ERROR:
            agent_id = event.data.get("agent_id")
            error = event.data.get("error")
            if agent_id:
                self.state.update_agent_status(agent_id, AgentStatus.ERROR, error)

        elif event.type == EventType.SYSTEM_TOKEN_UPDATE:
            tokens = event.data.get("tokens", 0)
            self.state.context_tokens = tokens

        elif event.type == EventType.SYSTEM_START:
            self.state.is_responding = True
            self._output_buffer.clear()

        elif event.type == EventType.SYSTEM_END:
            self.state.is_responding = False

        elif event.type == EventType.UI_MODE_CHANGE:
            mode_name = event.data.get("mode", "ZEN")
            try:
                self.state.mode = UIMode[mode_name]
            except KeyError:
                pass

    def _on_event(self, event: UIEvent) -> None:
        """Global event handler"""
        self.emit(event)

    def invalidate(self) -> None:
        """Request redraw - handled by prompt_toolkit automatically"""
        pass

    def get_input(self, prompt: str = "❯ ") -> str:
        """
        Get input using prompt_toolkit with full features.

        Features:
        - Multiline editing
        - History with search (Ctrl+R)
        - Auto-suggestions
        - Syntax highlighting
        - Key bindings (Esc, F2, etc.)
        """
        if not self._input_handler:
            # Fallback if not started
            return input(prompt)

        from .completers import create_completer

        try:
            from prompt_toolkit import PromptSession
            from prompt_toolkit.auto_suggest import AutoSuggestFromHistory
            from prompt_toolkit.formatted_text import FormattedText

            # Create session with completer
            completer = create_completer(self.interpreter)

            session = PromptSession(
                history=self._input_handler.history,
                auto_suggest=AutoSuggestFromHistory(),
                completer=completer,
                multiline=True,
                key_bindings=self._input_handler.create_key_bindings(),
                enable_history_search=True,
                complete_while_typing=False,
            )

            # Format prompt with styling
            formatted_prompt = FormattedText([
                ('class:prompt', prompt),
            ])

            result = session.prompt(formatted_prompt)
            return result

        except KeyboardInterrupt:
            return ""
        except EOFError:
            raise
        except Exception:
            # Fallback to basic input on any error
            return input(prompt)

    def get_buffered_output(self) -> str:
        """Get and clear buffered output"""
        output = "".join(self._output_buffer)
        self._output_buffer.clear()
        return output


def is_tty() -> bool:
    """Check if running in a TTY (interactive terminal)"""
    return (
        hasattr(sys.stdin, 'isatty') and sys.stdin.isatty() and
        hasattr(sys.stdout, 'isatty') and sys.stdout.isatty()
    )


def prompt_toolkit_available() -> bool:
    """Check if prompt_toolkit is installed"""
    try:
        import prompt_toolkit
        return True
    except ImportError:
        return False


def create_backend(
    interpreter: "OpenInterpreter",
    state: Optional[UIState] = None,
    force_type: Optional[BackendType] = None
) -> UIBackend:
    """
    Create the appropriate backend based on environment.

    Selection logic:
    1. If force_type specified, use that
    2. If not a TTY (pipe/CI), use RichStream
    3. If NO_TUI env var set, use RichStream
    4. If prompt_toolkit available, use it
    5. Otherwise, fall back to RichStream

    Args:
        interpreter: The OpenInterpreter instance
        state: Optional UIState (creates new if not provided)
        force_type: Force a specific backend type

    Returns:
        Configured UIBackend instance
    """
    if state is None:
        state = UIState()

    # Check for forced type
    if force_type is not None:
        if force_type == BackendType.PROMPT_TOOLKIT:
            return PromptToolkitBackend(interpreter, state)
        else:
            return RichStreamBackend(interpreter, state)

    # Check for --no-tui or NO_TUI env
    if os.environ.get("NO_TUI", "").lower() in ("1", "true", "yes"):
        return RichStreamBackend(interpreter, state)

    # Check if running in a TTY
    if not is_tty():
        return RichStreamBackend(interpreter, state)

    # Check for prompt_toolkit availability
    if prompt_toolkit_available():
        return PromptToolkitBackend(interpreter, state)

    # Fallback to Rich streaming
    return RichStreamBackend(interpreter, state)


# Export convenience functions
__all__ = [
    "UIBackend",
    "RichStreamBackend",
    "PromptToolkitBackend",
    "BackendType",
    "create_backend",
    "is_tty",
    "prompt_toolkit_available",
]
