"""
Textual Application for Open Interpreter

Main TUI application using Textual framework. Replaces the hybrid
Rich + prompt_toolkit approach with a unified Textual-based UI.

Features:
- Reactive state management
- CSS-based theming
- Built-in widget library
- Mouse support
- Hot reload during development

Usage:
    from textual_app import InterpreterTUI
    app = InterpreterTUI(interpreter)
    app.run()

Development:
    textual run --dev interpreter/terminal_interface/textual_app.py
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from rich.syntax import Syntax
from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, Vertical
from textual.reactive import reactive
from textual.screen import ModalScreen
from textual.widgets import Button, Footer, Header, LoadingIndicator, Static
from textual.worker import Worker, WorkerState

from .components.ui_events import EventType, UIEvent, get_event_bus
from .components.ui_state import AgentRole, AgentStatus, UIMode, UIState
from .widgets import (
    AgentTreeWidget,
    CodeBlockWidget,
    ContextPanelWidget,
    InputArea,
    MessageWidget,
    OutputPanel,
)


class ConfirmCodeScreen(ModalScreen[bool]):
    """
    Modal dialog for code execution confirmation.

    Returns True if user approves, False otherwise.
    """

    DEFAULT_CSS = """
    ConfirmCodeScreen {
        align: center middle;
    }

    ConfirmCodeScreen > Vertical {
        width: 80%;
        max-width: 100;
        height: auto;
        max-height: 80%;
        background: $surface;
        border: round $warning;
        padding: 1 2;
    }

    ConfirmCodeScreen .title {
        text-align: center;
        text-style: bold;
        color: $warning;
        margin-bottom: 1;
    }

    ConfirmCodeScreen .code-preview {
        height: auto;
        max-height: 20;
        overflow-y: auto;
        margin: 1 0;
        padding: 1;
        background: $background;
        border: round $secondary;
    }

    ConfirmCodeScreen .buttons {
        align: center middle;
        height: auto;
        margin-top: 1;
    }

    ConfirmCodeScreen Button {
        margin: 0 1;
    }
    """

    BINDINGS = [
        Binding("y", "approve", "Run", show=True),
        Binding("n", "reject", "Skip", show=True),
        Binding("escape", "reject", "Cancel", show=False),
        Binding("enter", "approve", "Run", show=False),
    ]

    def __init__(self, language: str, code: str):
        super().__init__()
        self.language = language
        self.code = code

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static(f"Execute {self.language} code?", classes="title")
            yield Static(
                Syntax(
                    self.code[:500] + ("..." if len(self.code) > 500 else ""),
                    self.language,
                    theme="monokai",
                    line_numbers=True,
                ),
                classes="code-preview",
            )
            with Horizontal(classes="buttons"):
                yield Button("Run [Y]", variant="warning", id="approve")
                yield Button("Skip [N]", variant="default", id="reject")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "approve":
            self.dismiss(True)
        else:
            self.dismiss(False)

    def action_approve(self) -> None:
        self.dismiss(True)

    def action_reject(self) -> None:
        self.dismiss(False)


if TYPE_CHECKING:
    from ..core.core import OpenInterpreter


class StatusBar(Static):
    """
    Top status bar showing model, mode, and token usage.

    Updates reactively based on app state. Shows:
    - Model name with context window size
    - UI mode (ZEN/STANDARD/POWER/DEBUG)
    - Token usage with color-coded percentage
    - Input/output token breakdown (when available)
    - Active features indicator
    """

    DEFAULT_CSS = """
    StatusBar {
        dock: top;
        height: 1;
        background: $surface;
        color: $text-muted;
        padding: 0 1;
    }

    StatusBar .label {
        color: $text-muted;
    }

    StatusBar .value {
        color: $primary;
        text-style: bold;
    }

    StatusBar .separator {
        color: $text-muted;
        margin: 0 1;
    }
    """

    def __init__(
        self,
        model: str = "unknown",
        mode: str = "ZEN",
        tokens: int = 0,
        token_limit: int = 128000,
        input_tokens: int = 0,
        output_tokens: int = 0,
        features: list[str] | None = None,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self._model = model
        self._mode = mode
        self._tokens = tokens
        self._token_limit = token_limit
        self._input_tokens = input_tokens
        self._output_tokens = output_tokens
        self._features = features or []

    def _format_tokens(self, count: int) -> str:
        """Format token count for display (e.g., 32k, 1.2M)."""
        if count >= 1_000_000:
            return f"{count / 1_000_000:.1f}M"
        elif count >= 1_000:
            return f"{count / 1_000:.0f}k"
        return str(count)

    def render(self) -> Text:
        """Render status bar content."""
        text = Text()

        # Model with context window
        text.append("🤖 ", style="dim")
        model_display = self._model
        if len(model_display) > 20:
            model_display = model_display[:17] + "..."
        text.append(model_display, style="bold cyan")

        # Show context window size
        if self._token_limit > 0:
            text.append(
                f" ({self._format_tokens(self._token_limit)})", style="dim cyan"
            )

        text.append(" │ ", style="dim")

        # Mode
        mode_styles = {
            "ZEN": "dim",
            "STANDARD": "white",
            "POWER": "bold yellow",
            "DEBUG": "bold red",
        }
        text.append(self._mode, style=mode_styles.get(self._mode, "white"))

        text.append(" │ ", style="dim")

        # Token usage bar
        pct = (self._tokens / self._token_limit * 100) if self._token_limit > 0 else 0
        token_style = "green" if pct < 60 else "yellow" if pct < 85 else "red bold"

        # Mini progress bar
        bar_width = 10
        filled = int(pct / 100 * bar_width)
        bar = "█" * filled + "░" * (bar_width - filled)
        text.append(bar, style=token_style)
        text.append(f" {pct:.0f}%", style=token_style)

        # Token breakdown (if we have input/output info)
        if self._input_tokens > 0 or self._output_tokens > 0:
            text.append(" (", style="dim")
            text.append(
                f"↑{self._format_tokens(self._input_tokens)}", style="dim green"
            )
            text.append("/", style="dim")
            text.append(
                f"↓{self._format_tokens(self._output_tokens)}", style="dim blue"
            )
            text.append(")", style="dim")

        # Features indicator
        if self._features:
            text.append(" │ ", style="dim")
            text.append("⚡", style="yellow")
            text.append(f"{len(self._features)}", style="dim")

        return text

    def update_model(self, model: str) -> None:
        """Update model name."""
        self._model = model
        self.refresh()

    def update_mode(self, mode: str) -> None:
        """Update UI mode."""
        self._mode = mode
        self.refresh()

    def update_tokens(
        self,
        tokens: int,
        limit: int | None = None,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
    ) -> None:
        """Update token counts with optional breakdown."""
        self._tokens = tokens
        if limit is not None:
            self._token_limit = limit
        if input_tokens is not None:
            self._input_tokens = input_tokens
        if output_tokens is not None:
            self._output_tokens = output_tokens
        self.refresh()

    def update_features(self, features: list[str]) -> None:
        """Update enabled features list."""
        self._features = features
        self.refresh()


class AgentBadge(Static):
    """Single agent status badge for the agent strip."""

    ICONS = {
        AgentStatus.PENDING: "○",
        AgentStatus.RUNNING: "⏳",
        AgentStatus.COMPLETE: "✓",
        AgentStatus.ERROR: "✗",
        AgentStatus.CANCELLED: "⊘",
    }

    ROLE_ICONS = {
        AgentRole.SCOUT: "🔍",
        AgentRole.SURGEON: "🔧",
        AgentRole.ARCHITECT: "🏗️",
        AgentRole.VALIDATOR: "✅",
        AgentRole.HISTORIAN: "📚",
        AgentRole.REVIEWER: "👁️",
        AgentRole.TESTER: "🧪",
        AgentRole.CUSTOM: "🤖",
    }

    DEFAULT_CSS = """
    AgentBadge {
        margin: 0 1;
        padding: 0 1;
    }

    AgentBadge.pending { color: $warning; }
    AgentBadge.running { color: $primary; }
    AgentBadge.complete { color: $success; }
    AgentBadge.error { color: $error; }
    """

    def __init__(
        self,
        agent_id: str,
        role: AgentRole,
        status: AgentStatus = AgentStatus.PENDING,
        elapsed: str = "0.0s",
        **kwargs,
    ):
        super().__init__(**kwargs)
        self.agent_id = agent_id
        self.role = role
        self.status = status
        self.elapsed = elapsed
        self._update_classes()

    def _update_classes(self) -> None:
        """Update CSS classes based on status."""
        for s in ["pending", "running", "complete", "error"]:
            self.remove_class(s)
        self.add_class(self.status.name.lower())

    def render(self) -> Text:
        """Render agent badge."""
        role_icon = self.ROLE_ICONS.get(self.role, "🤖")
        status_icon = self.ICONS.get(self.status, "?")
        role_name = self.role.value.title()

        text = Text()
        text.append("[", style="dim")
        text.append(f"{role_icon} {role_name}", style="bold")
        text.append(f": {status_icon} ", style="")
        text.append(self.elapsed, style="dim")
        text.append("]", style="dim")
        return text

    def update_status(self, status: AgentStatus, elapsed: str = "") -> None:
        """Update agent status."""
        self.status = status
        if elapsed:
            self.elapsed = elapsed
        self._update_classes()
        self.refresh()


class AgentStrip(Horizontal):
    """
    Bottom bar showing all active agents.

    Displays real-time status with icons and timing.
    Only visible when agents are active.
    """

    DEFAULT_CSS = """
    AgentStrip {
        dock: bottom;
        height: auto;
        min-height: 1;
        background: $surface;
        border-top: solid $secondary;
        padding: 0 1;
    }
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._badges: dict[str, AgentBadge] = {}

    def add_agent(
        self, agent_id: str, role: AgentRole, parent_id: str | None = None
    ) -> AgentBadge:
        """Add a new agent badge."""
        badge = AgentBadge(agent_id, role, id=f"agent-{agent_id}")
        self._badges[agent_id] = badge
        self.mount(badge)
        return badge

    def update_agent(
        self, agent_id: str, status: AgentStatus, elapsed: str = ""
    ) -> None:
        """Update an existing agent's status."""
        if agent_id in self._badges:
            self._badges[agent_id].update_status(status, elapsed)

    def remove_agent(self, agent_id: str) -> None:
        """Remove an agent badge."""
        if agent_id in self._badges:
            badge = self._badges.pop(agent_id)
            badge.remove()

    def clear_agents(self) -> None:
        """Remove all agent badges."""
        for badge in self._badges.values():
            badge.remove()
        self._badges.clear()

    @property
    def has_agents(self) -> bool:
        """Check if any agents are displayed."""
        return len(self._badges) > 0


class InterpreterTUI(App):
    """
    Main Textual application for Open Interpreter.

    Coordinates:
    - Input handling
    - Output display (messages, code blocks)
    - Agent visualization
    - Status bar
    - Event processing from interpreter

    Reactive State:
    - ui_mode: UIMode (ZEN/STANDARD/POWER/DEBUG)
    - is_responding: bool
    - is_streaming: bool
    """

    TITLE = "Open Interpreter"
    CSS_PATH = Path(__file__).parent / "interpreter.tcss"

    BINDINGS = [
        Binding("escape", "cancel", "Cancel", show=True),
        Binding("f2", "cycle_mode", "Mode", show=True),
        Binding("f3", "toggle_panel", "Panel", show=True),
        Binding("f4", "toggle_agents", "Agents", show=True),
        Binding("f5", "cycle_theme", "Theme", show=False),
        Binding("ctrl+l", "clear_output", "Clear", show=True),
        Binding("ctrl+d", "quit", "Exit", show=True),
        Binding("ctrl+r", "search_history", "History", show=False),
    ]

    # Available themes (CSS classes)
    THEMES = ["theme-dark", "theme-light", "theme-high-contrast"]

    # Reactive state - default to STANDARD mode (shows status bar)
    ui_mode: reactive[UIMode] = reactive(UIMode.STANDARD)
    ui_theme: reactive[str] = reactive("theme-dark")
    is_responding: reactive[bool] = reactive(False)
    is_streaming: reactive[bool] = reactive(False)

    def __init__(
        self,
        interpreter: OpenInterpreter | None = None,
        ui_state: UIState | None = None,
    ):
        super().__init__()
        self.interpreter = interpreter
        self.ui_state = ui_state or UIState()
        self._event_bus = get_event_bus()
        self._active_code_block: CodeBlockWidget | None = None
        self._active_message: MessageWidget | None = None
        self._loading: LoadingIndicator | None = None

    def compose(self) -> ComposeResult:
        """Compose the application layout."""
        # Get model name from interpreter.llm (the actual model location)
        model = "unknown"
        context_limit = 128000
        features: list[str] = []

        if self.interpreter:
            # Get model info from LLM
            if hasattr(self.interpreter, "llm"):
                model = getattr(self.interpreter.llm, "model", None) or "unknown"
                # Sync context window from LLM if available
                if self.interpreter.llm.context_window:
                    context_limit = self.interpreter.llm.context_window
                    self.ui_state.context_limit = context_limit

            # Detect enabled features
            if getattr(self.interpreter, "enable_semantic_memory", False):
                features.append("memory")
            if getattr(self.interpreter, "enable_validation", False):
                features.append("validation")
            if getattr(self.interpreter, "enable_agents", False):
                features.append("agents")
            if getattr(self.interpreter, "enable_tracing", False):
                features.append("tracing")

        yield Header()
        yield StatusBar(
            model=model,
            mode=self.ui_mode.name,
            tokens=self.ui_state.context_tokens,
            token_limit=context_limit,
            features=features,
            id="status-bar",
        )

        # Main content area with optional sidebars
        with Container(id="main-container"):
            yield OutputPanel(id="output-panel")
            # Context panel (shown in POWER/DEBUG modes)
            yield ContextPanelWidget(self.ui_state, id="context-panel")
            # Agent tree (shown when agents are active in POWER/DEBUG modes)
            yield AgentTreeWidget(self.ui_state, id="agent-tree")

        yield AgentStrip(id="agent-strip")
        yield InputArea(id="input-area")
        yield Footer()

    def on_mount(self) -> None:
        """Set up event handlers on mount."""
        # Subscribe to interpreter events
        self._subscribe_events()

        # Focus input
        self.query_one("#input-area", InputArea).focus()

        # Update mode class
        self._update_mode_class()

    def _subscribe_events(self) -> None:
        """Subscribe to EventBus events."""
        handlers = {
            EventType.AGENT_SPAWN: self._on_agent_spawn,
            EventType.AGENT_COMPLETE: self._on_agent_complete,
            EventType.AGENT_ERROR: self._on_agent_error,
            EventType.CODE_START: self._on_code_start,
            EventType.CODE_END: self._on_code_end,
            EventType.MESSAGE_START: self._on_message_start,
            EventType.MESSAGE_END: self._on_message_end,
            EventType.SYSTEM_TOKEN_UPDATE: self._on_token_update,
            EventType.SYSTEM_MODEL_CHANGE: self._on_model_change,
        }
        for event_type, handler in handlers.items():
            self._event_bus.subscribe(event_type, handler)

    def _on_agent_spawn(self, event: UIEvent) -> None:
        """Handle agent spawn event."""
        agent_id = event.data.get("agent_id", "unknown")
        role_str = event.data.get("role", "custom")
        try:
            role = AgentRole(role_str)
        except ValueError:
            role = AgentRole.CUSTOM

        # Thread-safe UI update
        self.call_from_thread(self._add_agent, agent_id, role)

    def _add_agent(self, agent_id: str, role: AgentRole) -> None:
        """Add agent to strip (must be called from main thread)."""
        strip = self.query_one("#agent-strip", AgentStrip)
        strip.add_agent(agent_id, role)

    def _on_agent_complete(self, event: UIEvent) -> None:
        """Handle agent completion."""
        agent_id = event.data.get("agent_id")
        if agent_id:
            self.call_from_thread(self._update_agent, agent_id, AgentStatus.COMPLETE)

    def _on_agent_error(self, event: UIEvent) -> None:
        """Handle agent error."""
        agent_id = event.data.get("agent_id")
        if agent_id:
            self.call_from_thread(self._update_agent, agent_id, AgentStatus.ERROR)

    def _update_agent(self, agent_id: str, status: AgentStatus) -> None:
        """Update agent status (must be called from main thread)."""
        strip = self.query_one("#agent-strip", AgentStrip)
        strip.update_agent(agent_id, status)

    def _on_code_start(self, event: UIEvent) -> None:
        """Handle code block start from EventBus."""
        language = event.data.get("language", "python")
        self.call_from_thread(self._start_code_block, language)

    def _on_code_end(self, event: UIEvent) -> None:
        """Handle code block end from EventBus."""
        self.call_from_thread(self._end_code_block)

    def _on_message_start(self, event: UIEvent) -> None:
        """Handle message start from EventBus."""
        role = event.data.get("role", "assistant")
        self.call_from_thread(self._start_message_block, role)

    def _on_message_end(self, event: UIEvent) -> None:
        """Handle message end from EventBus."""
        self.call_from_thread(self._end_message_block)

    def _on_token_update(self, event: UIEvent) -> None:
        """Handle token count update with input/output breakdown."""
        if isinstance(event.data, dict):
            tokens = event.data.get("tokens", 0)
            limit = event.data.get("limit")
            input_tokens = event.data.get("input_tokens", 0)
            output_tokens = event.data.get("output_tokens", 0)
            self.call_from_thread(
                self._update_tokens, tokens, limit, input_tokens, output_tokens
            )

    def _update_tokens(
        self,
        tokens: int,
        limit: int | None,
        input_tokens: int = 0,
        output_tokens: int = 0,
    ) -> None:
        """Update status bar tokens with breakdown."""
        status = self.query_one("#status-bar", StatusBar)
        status.update_tokens(tokens, limit, input_tokens, output_tokens)
        # Also update UIState for consistency
        self.ui_state.set_context_tokens(tokens, limit)

    def _on_model_change(self, event: UIEvent) -> None:
        """Handle model change event."""
        if isinstance(event.data, dict):
            model = event.data.get("model", "unknown")
            context_window = event.data.get("context_window")
            self.call_from_thread(self._update_model, model, context_window)

    def _update_model(self, model: str, context_window: int | None) -> None:
        """Update status bar model and context limit."""
        status = self.query_one("#status-bar", StatusBar)
        status.update_model(model)
        if context_window:
            self.ui_state.context_limit = context_window
            status.update_tokens(self.ui_state.context_tokens, context_window)

    # Actions

    def action_cancel(self) -> None:
        """Cancel current operation."""
        if self.interpreter and hasattr(self.interpreter, "stop"):
            self.interpreter.stop()
        self._event_bus.emit(UIEvent(type=EventType.UI_CANCEL, source="textual_app"))
        self.notify("Operation cancelled", severity="warning")

    def action_cycle_mode(self) -> None:
        """Cycle through UI modes."""
        modes = [UIMode.ZEN, UIMode.STANDARD, UIMode.POWER, UIMode.DEBUG]
        current_idx = modes.index(self.ui_mode)
        self.ui_mode = modes[(current_idx + 1) % len(modes)]
        self._update_mode_class()

        # Update status bar
        status = self.query_one("#status-bar", StatusBar)
        status.update_mode(self.ui_mode.name)

        self.notify(f"Mode → {self.ui_mode.name}")

    def _update_mode_class(self) -> None:
        """Update CSS class based on current mode."""
        for mode in UIMode:
            self.remove_class(f"mode-{mode.name.lower()}")
        self.add_class(f"mode-{self.ui_mode.name.lower()}")

    def action_toggle_panel(self) -> None:
        """Toggle context panel visibility."""
        try:
            context_panel = self.query_one("#context-panel", ContextPanelWidget)
            if context_panel.has_class("visible"):
                context_panel.remove_class("visible")
                self.notify("Context panel hidden")
            else:
                context_panel.add_class("visible")
                self.notify("Context panel shown")
        except Exception:
            self.notify("Context panel not available", severity="warning")

    def action_toggle_agents(self) -> None:
        """Toggle agent tree visibility."""
        try:
            from .widgets import AgentTreeWidget

            agent_tree = self.query_one("#agent-tree", AgentTreeWidget)
            if agent_tree.has_class("visible"):
                agent_tree.remove_class("visible")
                self.notify("Agent tree hidden")
            else:
                agent_tree.add_class("visible")
                self.notify("Agent tree shown")
        except Exception:
            self.notify("Agent tree not available", severity="warning")

    def action_cycle_theme(self) -> None:
        """Cycle through themes (dark/light/high-contrast)."""
        # Remove current theme class
        for theme in self.THEMES:
            self.remove_class(theme)

        # Get next theme
        current_idx = (
            self.THEMES.index(self.ui_theme) if self.ui_theme in self.THEMES else 0
        )
        self.ui_theme = self.THEMES[(current_idx + 1) % len(self.THEMES)]

        # Apply new theme class
        self.add_class(self.ui_theme)

        # Friendly name for notification
        theme_names = {
            "theme-dark": "Dark",
            "theme-light": "Light",
            "theme-high-contrast": "High Contrast",
        }
        self.notify(f"Theme → {theme_names.get(self.ui_theme, self.ui_theme)}")

    def action_clear_output(self) -> None:
        """Clear output panel."""
        panel = self.query_one("#output-panel", OutputPanel)
        panel.clear()
        self.notify("Output cleared")

    def action_search_history(self) -> None:
        """Show history search."""
        # History search implementation
        self.notify("History search (Ctrl+R) - not yet implemented")

    # Input handling

    def on_input_area_user_submitted(self, event: InputArea.UserSubmitted) -> None:
        """Handle input submission from InputArea."""
        text = event.value

        if not text:
            return

        # Handle magic commands
        if text.startswith("%"):
            self._handle_magic_command(text)
            return

        # Add user message to output
        try:
            panel = self.query_one("#output-panel", OutputPanel)
            panel.add_message(text, role="user")
        except Exception as e:
            self.notify(f"Error: {e}", severity="error")
            return

        # Process with interpreter in worker thread
        if self.interpreter:
            self._start_chat_worker(text)
        else:
            self.notify("No interpreter available", severity="error")

    def _handle_magic_command(self, command: str) -> None:
        """Handle % magic commands."""
        cmd = command[1:].strip().lower()

        if cmd == "help":
            self.notify("Commands: %help, %reset, %clear, %mode, %save, %load")
        elif cmd == "reset":
            if self.interpreter:
                self.interpreter.messages = []
            panel = self.query_one("#output-panel", OutputPanel)
            panel.clear()
            self.notify("Conversation reset")
        elif cmd == "clear":
            panel = self.query_one("#output-panel", OutputPanel)
            panel.clear()
            self.notify("Output cleared")
        elif cmd.startswith("mode"):
            self.action_cycle_mode()
        else:
            self.notify(f"Unknown command: {command}", severity="warning")

    def _start_chat_worker(self, message: str) -> None:
        """Start a worker thread to process chat."""
        self.is_responding = True

        # Show loading indicator
        panel = self.query_one("#output-panel", OutputPanel)
        self._loading = LoadingIndicator(id="loading")
        panel.mount(self._loading)
        panel.scroll_end()

        # Run chat in worker thread
        self.run_worker(
            lambda: self._chat_worker(message),
            name="chat_worker",
            exclusive=True,
            thread=True,
        )

    def _chat_worker(self, message: str) -> None:
        """
        Worker function that processes chat with interpreter.

        Runs in a separate thread. Uses call_from_thread for UI updates.
        """
        if not self.interpreter:
            self.call_from_thread(self.notify, "No interpreter!", severity="error")
            return

        try:
            chat_generator = self.interpreter.chat(message, display=False, stream=True)

            if chat_generator is None:
                self.call_from_thread(
                    self.notify, "No response from model", severity="warning"
                )
                return

            for chunk in chat_generator:
                self._process_chunk(chunk)

        except Exception as e:
            self.call_from_thread(
                self.notify, f"Error: {e}", severity="error", timeout=5
            )
            self.call_from_thread(self._show_error, str(e))
        finally:
            self.call_from_thread(self._remove_loading)

    def _process_chunk(self, chunk: dict) -> None:
        """Process a single chunk from interpreter.chat()."""
        chunk_type = chunk.get("type", "")
        role = chunk.get("role", "")

        # Status chunks - informational only, skip display
        if chunk_type == "status":
            return

        # Message chunks
        if chunk_type == "message":
            if "start" in chunk:
                self.call_from_thread(self._start_message_block, role)
            elif "content" in chunk and chunk["content"]:
                self.call_from_thread(self._append_message, chunk["content"])
            elif "end" in chunk:
                self.call_from_thread(self._end_message_block)

        # Code chunks
        elif chunk_type == "code" and role == "assistant":
            if "start" in chunk:
                language = chunk.get("format", "python")
                self.call_from_thread(self._start_code_block, language)
            elif "content" in chunk and chunk["content"]:
                self.call_from_thread(self._append_code, chunk["content"])

        # Confirmation (code execution approval)
        elif chunk_type == "confirmation":
            # Handle in main thread via event
            code_info = chunk.get("content", {})
            self.call_from_thread(self._request_confirmation, code_info)

        # Console output
        elif chunk_type == "console":
            if "format" in chunk and chunk["format"] == "output":
                content = chunk.get("content", "")
                if content:
                    self.call_from_thread(self._append_output, content)
            elif "format" in chunk and chunk["format"] == "active_line":
                # Update active line highlighting
                line = chunk.get("content")
                if self._active_code_block and line is not None:
                    self.call_from_thread(self._set_active_line, line)
            elif "end" in chunk:
                self.call_from_thread(self._end_code_block)

    def _start_message_block(self, role: str = "assistant") -> None:
        """Create new message widget (main thread)."""
        self._remove_loading()

        try:
            panel = self.query_one("#output-panel", OutputPanel)
            self._active_message = MessageWidget("", role=role)
            panel.mount(self._active_message)
            panel.scroll_end()
        except Exception as e:
            self.notify(f"Error creating message: {e}", severity="error", timeout=5)

    def _append_message(self, content: str) -> None:
        """Append to active message (main thread)."""
        if self._active_message:
            self._active_message.append(content)

    def _end_message_block(self) -> None:
        """Finalize message block (main thread)."""
        self._active_message = None

    def _start_code_block(self, language: str = "python") -> None:
        """Create new code block (main thread)."""
        # Remove loading indicator if present
        self._remove_loading()

        panel = self.query_one("#output-panel", OutputPanel)
        self._active_code_block = CodeBlockWidget("", language)
        panel.mount(self._active_code_block)
        panel.scroll_end()

    def _append_code(self, content: str) -> None:
        """Append to active code block (main thread)."""
        if self._active_code_block:
            self._active_code_block.code += content

    def _append_output(self, content: str) -> None:
        """Append console output to code block (main thread)."""
        if self._active_code_block:
            self._active_code_block.add_output(content)
            self._active_code_block.set_running()

    def _set_active_line(self, line: int) -> None:
        """Set active line in code block (main thread)."""
        if self._active_code_block:
            self._active_code_block.active_line = line
            self._active_code_block.set_running()

    def _end_code_block(self) -> None:
        """Finalize code block (main thread)."""
        if self._active_code_block:
            # Check output for errors
            output = self._active_code_block.output
            if "Traceback" in output or "Error" in output:
                self._active_code_block.set_error()
            else:
                self._active_code_block.set_success()
            self._active_code_block = None

    def _request_confirmation(self, code_info: dict) -> None:
        """Request user confirmation for code execution (main thread)."""
        language = code_info.get("format", "code")
        code = code_info.get("content", "")

        # For auto_run mode, no confirmation needed
        if self.interpreter and self.interpreter.auto_run:
            return

        # Show confirmation modal
        def handle_confirmation(approved: bool) -> None:
            if approved:
                self.notify("Code execution approved", severity="information")
            else:
                self.notify("Code execution skipped", severity="warning")
                # Add message to inform the LLM
                if self.interpreter:
                    self.interpreter.messages.append(
                        {
                            "role": "user",
                            "type": "message",
                            "content": "I have declined to run this code. Please continue with an alternative approach.",
                        }
                    )

        self.push_screen(ConfirmCodeScreen(language, code), handle_confirmation)

    def _show_error(self, error: str) -> None:
        """Display error message (main thread)."""
        self._remove_loading()
        self.notify(f"Error: {error}", severity="error", timeout=5)

    def _remove_loading(self) -> None:
        """Remove loading indicator if present."""
        if hasattr(self, "_loading") and self._loading:
            try:
                self._loading.remove()
            except Exception:
                pass
            self._loading = None

    def on_worker_state_changed(self, event: Worker.StateChanged) -> None:
        """Handle worker state changes."""
        if event.worker.name == "chat_worker":
            if event.state == WorkerState.SUCCESS:
                self.is_responding = False
                self._remove_loading()
            elif event.state == WorkerState.ERROR:
                self.is_responding = False
                self._remove_loading()
                self.notify("Chat worker error", severity="error")
            elif event.state == WorkerState.CANCELLED:
                self.is_responding = False
                self._remove_loading()
                self.notify("Operation cancelled", severity="warning")

    # Stream content (called from interpreter thread via TextualBackend)

    def stream_code(self, content: str) -> None:
        """Stream code content to active block."""
        if self._active_code_block:
            self.call_from_thread(self._append_code, content)

    def stream_message(self, content: str) -> None:
        """Stream message content."""
        if self._active_message:
            self.call_from_thread(self._append_message, content)

    def stream_output(self, content: str) -> None:
        """Stream console output."""
        if self._active_code_block:
            self.call_from_thread(self._append_output, content)


# Entry point for development
if __name__ == "__main__":
    app = InterpreterTUI()
    app.run()
