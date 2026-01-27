"""
Textual Backend - UIBackend implementation using Textual framework.

Integrates InterpreterTUI with the existing backend abstraction layer,
allowing gradual migration from Rich + prompt_toolkit.

Usage:
    from textual_backend import TextualBackend
    backend = TextualBackend(interpreter, state)
    backend.start()

Or via factory:
    backend = create_backend(interpreter, force_type=BackendType.TEXTUAL)
"""

from __future__ import annotations

import asyncio
import threading
from typing import TYPE_CHECKING

from .components.ui_backend import BackendType, UIBackend
from .components.ui_events import EventType, UIEvent
from .components.ui_state import UIMode, UIState

if TYPE_CHECKING:
    from ..core.core import OpenInterpreter
    from .textual_app import InterpreterTUI


class TextualBackend(UIBackend):
    """
    Textual-based interactive backend.

    Features:
    - Full Textual App with CSS theming
    - Reactive state management
    - Mouse support
    - Built-in widget library
    - Hot reload during development

    Threading Model:
    - Textual runs in main thread
    - Interpreter chat runs in worker thread
    - Events bridge worker → main thread via call_from_thread

    Phase 0 Implementation - Proof of concept.
    """

    def __init__(self, interpreter: OpenInterpreter, state: UIState):
        super().__init__(interpreter, state)
        self._app: InterpreterTUI | None = None
        self._app_thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None

    @property
    def backend_type(self) -> BackendType:
        return BackendType.TEXTUAL

    @property
    def supports_interactive(self) -> bool:
        return True

    def start(self) -> None:
        """
        Initialize and start the Textual application.

        The app runs in its own event loop. For synchronous usage,
        call run() instead which blocks until exit.
        """
        from .textual_app import InterpreterTUI

        self._running = True

        # Create app instance
        self._app = InterpreterTUI(self.interpreter, self.state)

        # Subscribe to events for state forwarding
        self.event_bus.subscribe_all(self._on_event)

    def stop(self) -> None:
        """Shutdown Textual application."""
        self._running = False
        self.event_bus.unsubscribe_all(self._on_event)

        if self._app:
            try:
                self._app.exit()
            except Exception:
                pass
            self._app = None

    def run(self) -> None:
        """
        Run the Textual application (blocking).

        This is the main entry point for interactive use.
        Blocks until the user exits.
        """
        if not self._app:
            self.start()

        if self._app:
            self._app.run()

    async def run_async(self) -> None:
        """
        Run the Textual application asynchronously.

        Use this when integrating with an existing async event loop.
        """
        if not self._app:
            self.start()

        if self._app:
            await self._app.run_async()

    def emit(self, event: UIEvent) -> None:
        """
        Process a UI event.

        Routes events to appropriate app methods.
        Thread-safe - uses call_from_thread for cross-thread updates.
        """
        if not self._app or not self._running:
            return

        # Route based on event type
        if event.type == EventType.MESSAGE_CHUNK:
            content = event.data.get("content", "")
            if content:
                self._app.stream_message(content)

        elif event.type == EventType.CODE_CHUNK:
            content = event.data.get("content", "")
            if content:
                self._app.stream_code(content)

        elif event.type == EventType.CONSOLE_OUTPUT:
            content = event.data.get("content", "")
            if content:
                self._app.stream_output(content)

        elif event.type == EventType.SYSTEM_START:
            self.state.is_responding = True
            if self._app:
                self._app.is_responding = True

        elif event.type == EventType.SYSTEM_END:
            self.state.is_responding = False
            if self._app:
                self._app.is_responding = False

        elif event.type == EventType.UI_MODE_CHANGE:
            mode_name = event.data.get("mode", "ZEN")
            try:
                mode = UIMode[mode_name]
                self.state.mode = mode
                if self._app:
                    self._app.ui_mode = mode
            except KeyError:
                pass

    def _on_event(self, event: UIEvent) -> None:
        """Global event handler - routes all events."""
        self.emit(event)

    def invalidate(self) -> None:
        """Request a redraw - Textual handles this automatically."""
        if self._app:
            self._app.refresh()

    def get_input(self, prompt: str = "❯ ") -> str:
        """
        Get user input.

        In Textual mode, input is handled by the InputArea widget.
        This method is for compatibility - actual input flows through
        the app's on_input_area_submitted handler.

        Returns:
            Empty string - input is async through the app
        """
        # Input is handled async by Textual
        # This is called for compatibility but actual input
        # comes through the InputArea widget
        return ""

    def show_toast(
        self, message: str, severity: str = "information", timeout: float = 3.0
    ) -> None:
        """
        Show a toast notification.

        Uses Textual's built-in notify system.

        Args:
            message: Notification text
            severity: One of "information", "warning", "error"
            timeout: Seconds until auto-dismiss
        """
        if self._app:
            self._app.call_from_thread(
                self._app.notify, message, severity=severity, timeout=timeout
            )


def textual_available() -> bool:
    """Check if Textual is installed and usable."""
    try:
        import textual  # noqa: F401

        return True
    except ImportError:
        return False


def create_textual_backend(
    interpreter: OpenInterpreter,
    state: UIState | None = None,
) -> TextualBackend:
    """
    Factory function to create a TextualBackend.

    Args:
        interpreter: The OpenInterpreter instance
        state: Optional UIState (creates new if not provided)

    Returns:
        Configured TextualBackend instance

    Raises:
        ImportError: If Textual is not installed
    """
    if not textual_available():
        raise ImportError("Textual is not installed. Install with: pip install textual")

    if state is None:
        state = UIState()

    return TextualBackend(interpreter, state)


__all__ = [
    "TextualBackend",
    "textual_available",
    "create_textual_backend",
]
