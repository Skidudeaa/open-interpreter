"""
Prompt Toolkit Application

The core interactive TUI application. prompt_toolkit owns the screen,
Rich renders to ANSI strings which are displayed in PT windows.

Part of Phase 1: prompt_toolkit Integration

Usage:
    app = InterpreterApp(interpreter, state)
    app.run()  # Blocks until exit
"""

from typing import TYPE_CHECKING, Optional, Callable, List
import asyncio
from threading import Thread
from queue import Queue

from prompt_toolkit import Application
from prompt_toolkit.application import get_app
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.document import Document
from prompt_toolkit.filters import Condition
from prompt_toolkit.formatted_text import ANSI, FormattedText
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import (
    Layout,
    HSplit,
    VSplit,
    Window,
    WindowAlign,
    FormattedTextControl,
    BufferControl,
    ScrollablePane,
)
from prompt_toolkit.layout.containers import ConditionalContainer, Float, FloatContainer
from prompt_toolkit.layout.dimension import Dimension, D
from prompt_toolkit.layout.margins import ScrollbarMargin
from prompt_toolkit.lexers import PygmentsLexer
from prompt_toolkit.styles import Style
from prompt_toolkit.widgets import TextArea, Frame

from rich.console import Console
from rich.text import Text

from .ui_state import UIState, UIMode
from .ui_events import UIEvent, EventType, EventBus, get_event_bus
from .theme import THEMES

if TYPE_CHECKING:
    from ...core.core import OpenInterpreter


def render_rich_to_ansi(renderable, width: int = 120) -> str:
    """
    Render a Rich renderable to ANSI escape codes.

    Args:
        renderable: Any Rich renderable (Text, Panel, Table, etc.)
        width: Console width for rendering

    Returns:
        ANSI string suitable for prompt_toolkit
    """
    console = Console(force_terminal=True, width=width, record=True)
    with console.capture() as capture:
        console.print(renderable, end="")
    return capture.get()


class OutputBuffer:
    """
    Thread-safe buffer for streaming output.

    Accumulates Rich renderables and converts to ANSI for display.
    """

    def __init__(self, max_lines: int = 1000):
        self._lines: List[str] = []
        self._max_lines = max_lines
        self._width = 120

    def append(self, text: str) -> None:
        """Add text to the buffer"""
        self._lines.extend(text.split('\n'))
        # Trim to max lines
        if len(self._lines) > self._max_lines:
            self._lines = self._lines[-self._max_lines:]

    def clear(self) -> None:
        """Clear the buffer"""
        self._lines.clear()

    def get_content(self) -> str:
        """Get buffer content as string"""
        return '\n'.join(self._lines)

    def set_width(self, width: int) -> None:
        """Update rendering width"""
        self._width = width


class InterpreterApp:
    """
    The main prompt_toolkit Application for Open Interpreter.

    Owns the terminal screen and coordinates:
    - Input area with multiline editing
    - Output window with streaming content
    - Status bar
    - Agent strip (when agents active)
    - Key bindings

    Architecture:
    - prompt_toolkit Application runs in main thread
    - Interpreter runs in background thread, emits events
    - Events are consumed and trigger UI invalidation
    """

    def __init__(
        self,
        interpreter: "OpenInterpreter",
        state: Optional[UIState] = None,
        event_bus: Optional[EventBus] = None,
    ):
        self.interpreter = interpreter
        self.state = state or UIState()
        self.event_bus = event_bus or get_event_bus()

        # Output buffer
        self.output_buffer = OutputBuffer()

        # Input buffer
        self.input_buffer = Buffer(
            multiline=True,
            accept_handler=self._on_input_accept,
        )

        # Create layout components
        self._create_styles()
        self._create_key_bindings()
        self._create_layout()

        # Create the application
        self.app = Application(
            layout=self.layout,
            key_bindings=self.kb,
            style=self.style,
            full_screen=True,
            mouse_support=True,
            erase_when_done=False,
        )

        # Callbacks
        self._on_input: Optional[Callable[[str], None]] = None
        self._running = False

    def _create_styles(self) -> None:
        """Create prompt_toolkit styles from theme"""
        theme = THEMES.get("dark", THEMES["dark"])

        self.style = Style.from_dict({
            # Input area
            'input-area': f'bg:{theme["bg_dark"]}',
            'input-border': f'{theme["primary"]}',

            # Output area
            'output-area': '',

            # Status bar
            'status-bar': f'bg:{theme["bg_medium"]} {theme["secondary"]}',
            'status-bar.key': f'{theme["primary"]} bold',
            'status-bar.value': f'{theme["text_muted"]}',

            # Agent strip
            'agent-strip': f'bg:{theme["bg_medium"]}',
            'agent.pending': f'{theme["text_muted"]}',
            'agent.running': f'{theme["warning"]}',
            'agent.complete': f'{theme["success"]}',
            'agent.error': f'{theme["error"]}',

            # Prompt
            'prompt': f'{theme["primary"]} bold',
        })

    def _create_key_bindings(self) -> None:
        """Create key bindings with fallbacks for portability"""
        self.kb = KeyBindings()

        # Cancel current operation
        @self.kb.add('escape')
        def _(event):
            self._cancel_operation()

        # Clear screen
        @self.kb.add('c-l')
        def _(event):
            self.output_buffer.clear()
            event.app.invalidate()

        # Exit application
        @self.kb.add('c-d')
        def _(event):
            if not self.input_buffer.text:
                event.app.exit()

        # Toggle power mode (Alt+P or F2)
        @self.kb.add('escape', 'p')  # Alt+P as escape sequence
        @self.kb.add('f2')
        def _(event):
            self._toggle_mode()

        # Focus agent strip (Alt+A or F4)
        @self.kb.add('escape', 'a')  # Alt+A as escape sequence
        @self.kb.add('f4')
        def _(event):
            self._focus_agents()

        # History search (Ctrl+R)
        @self.kb.add('c-r')
        def _(event):
            # TODO: Implement history search overlay
            pass

        # Submit input (Enter in single-line mode, Ctrl+Enter or Alt+Enter in multiline)
        @self.kb.add('c-m')  # Enter
        def _(event):
            # Check if we should submit or add newline
            text = self.input_buffer.text
            if text.startswith('"""') and not text.endswith('"""'):
                # In multiline mode, add newline
                self.input_buffer.insert_text('\n')
            elif '\n' in text or text.startswith('"""'):
                # Already multiline, add newline
                self.input_buffer.insert_text('\n')
            else:
                # Single line, submit
                self.input_buffer.validate_and_handle()

        # Force submit (Alt+Enter)
        @self.kb.add('escape', 'enter')
        def _(event):
            self.input_buffer.validate_and_handle()

    def _create_layout(self) -> None:
        """Create the application layout"""

        # Status bar at top
        self.status_bar = Window(
            content=FormattedTextControl(self._get_status_bar_text),
            height=1,
            style='class:status-bar',
        )

        # Output area (scrollable)
        self.output_window = Window(
            content=FormattedTextControl(self._get_output_text),
            wrap_lines=True,
            right_margins=[ScrollbarMargin(display_arrows=True)],
        )

        # Agent strip (conditional)
        self.agent_strip = ConditionalContainer(
            content=Window(
                content=FormattedTextControl(self._get_agent_strip_text),
                height=1,
                style='class:agent-strip',
            ),
            filter=Condition(lambda: self.state.agent_strip_visible),
        )

        # Input area
        self.input_area = TextArea(
            height=D(min=1, max=10, preferred=3),
            prompt=FormattedText([('class:prompt', '❯ ')]),
            multiline=True,
            wrap_lines=True,
            accept_handler=self._on_accept,
            style='class:input-area',
        )

        # Replace input buffer with our configured one
        self.input_area.buffer = self.input_buffer

        # Help bar at bottom
        self.help_bar = Window(
            content=FormattedTextControl(
                lambda: [
                    ('class:status-bar.key', ' Esc '),
                    ('class:status-bar.value', 'Cancel '),
                    ('class:status-bar.key', ' F2 '),
                    ('class:status-bar.value', 'Mode '),
                    ('class:status-bar.key', ' Ctrl+L '),
                    ('class:status-bar.value', 'Clear '),
                    ('class:status-bar.key', ' Ctrl+D '),
                    ('class:status-bar.value', 'Exit '),
                ]
            ),
            height=1,
            style='class:status-bar',
        )

        # Main layout
        self.layout = Layout(
            HSplit([
                self.status_bar,
                self.output_window,
                self.agent_strip,
                Frame(self.input_area, title='Input'),
                self.help_bar,
            ]),
            focused_element=self.input_area,
        )

    def _get_status_bar_text(self) -> List[tuple]:
        """Generate status bar content"""
        mode_name = self.state.mode.name
        model = getattr(self.interpreter, 'model', 'unknown')
        tokens = self.state.context_tokens
        limit = self.state.context_limit

        # Token usage percentage
        pct = (tokens / limit * 100) if limit > 0 else 0
        token_str = f"{tokens:,}/{limit:,} ({pct:.0f}%)"

        return [
            ('class:status-bar.key', f' {model} '),
            ('class:status-bar.value', '│ '),
            ('class:status-bar.key', f'Mode: '),
            ('class:status-bar.value', f'{mode_name} '),
            ('class:status-bar.value', '│ '),
            ('class:status-bar.key', f'Tokens: '),
            ('class:status-bar.value', f'{token_str} '),
        ]

    def _get_output_text(self) -> ANSI:
        """Get output buffer as ANSI formatted text"""
        content = self.output_buffer.get_content()
        if not content:
            return ANSI("")
        return ANSI(content)

    def _get_agent_strip_text(self) -> List[tuple]:
        """Generate agent strip content"""
        if not self.state.active_agents:
            return []

        parts = []
        for agent_id, agent in self.state.active_agents.items():
            status_style = {
                'PENDING': 'class:agent.pending',
                'RUNNING': 'class:agent.running',
                'COMPLETE': 'class:agent.complete',
                'ERROR': 'class:agent.error',
            }.get(agent.status.name, 'class:agent.pending')

            icon = agent.status_icon
            role = agent.role.value.capitalize()
            elapsed = agent.elapsed_display

            parts.append((status_style, f' [{role}: {icon} {elapsed}] '))

        return parts

    def _on_accept(self, buff: Buffer) -> bool:
        """Handle input submission"""
        text = buff.text.strip()
        if text and self._on_input:
            self._on_input(text)
        buff.reset()
        return True

    def _on_input_accept(self, buff: Buffer) -> bool:
        """Handle input buffer accept"""
        return self._on_accept(buff)

    def _cancel_operation(self) -> None:
        """Cancel current operation"""
        self.event_bus.emit(UIEvent(type=EventType.UI_CANCEL, source="pt_app"))
        # Signal interpreter to stop
        if hasattr(self.interpreter, 'stop'):
            self.interpreter.stop()

    def _toggle_mode(self) -> None:
        """Toggle between UI modes"""
        modes = [UIMode.ZEN, UIMode.STANDARD, UIMode.POWER, UIMode.DEBUG]
        current_idx = modes.index(self.state.mode)
        next_idx = (current_idx + 1) % len(modes)
        self.state.mode = modes[next_idx]

        self.event_bus.emit(UIEvent(
            type=EventType.UI_MODE_CHANGE,
            data={"mode": self.state.mode.name},
            source="pt_app"
        ))
        self.app.invalidate()

    def _focus_agents(self) -> None:
        """Focus the agent strip for navigation"""
        # TODO: Implement agent navigation
        pass

    def set_input_handler(self, handler: Callable[[str], None]) -> None:
        """Set callback for input submission"""
        self._on_input = handler

    def append_output(self, text: str) -> None:
        """Add text to output buffer and refresh"""
        self.output_buffer.append(text)
        self.app.invalidate()

    def append_ansi(self, ansi: str) -> None:
        """Add ANSI-formatted text to output"""
        self.output_buffer.append(ansi)
        self.app.invalidate()

    def clear_output(self) -> None:
        """Clear output buffer"""
        self.output_buffer.clear()
        self.app.invalidate()

    def update_state(self, state: UIState) -> None:
        """Update UI state and refresh"""
        self.state = state
        self.app.invalidate()

    def run(self) -> None:
        """Run the application (blocking)"""
        self._running = True
        try:
            self.app.run()
        finally:
            self._running = False

    async def run_async(self) -> None:
        """Run the application asynchronously"""
        self._running = True
        try:
            await self.app.run_async()
        finally:
            self._running = False

    def exit(self) -> None:
        """Exit the application"""
        if self._running:
            self.app.exit()


def create_interpreter_app(
    interpreter: "OpenInterpreter",
    state: Optional[UIState] = None,
) -> InterpreterApp:
    """
    Factory function to create an InterpreterApp.

    Args:
        interpreter: The OpenInterpreter instance
        state: Optional UIState (creates new if not provided)

    Returns:
        Configured InterpreterApp instance
    """
    return InterpreterApp(interpreter, state)
