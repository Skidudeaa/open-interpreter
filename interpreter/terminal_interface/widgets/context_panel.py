"""
Context Panel Widget - Variables/functions/metrics sidebar for Textual TUI.

ARCHITECTURE: Static widget with three sections rendered via Rich Tables:
  1. Variables - Name, type, truncated value
  2. Functions - Name and signature
  3. Metrics - Execution time and memory

WHY: Provides visibility into execution context for debugging and development.
Visible in POWER and DEBUG modes, hidden in ZEN and STANDARD.

TRADEOFF: Periodic refresh (500ms) vs event-driven updates - simpler but slightly less responsive.

Part of Phase 3: Context Panel implementation.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from rich.table import Table
from rich.text import Text
from textual.reactive import reactive
from textual.widgets import Static

from ..components.ui_events import EventType, UIEvent, get_event_bus

if TYPE_CHECKING:
    from ..components.ui_state import UIState


class ContextPanelWidget(Static):
    """
    Context panel for displaying execution state.

    Shows:
    - Variables with types and values
    - Functions with signatures
    - Execution metrics (time, memory)

    Visibility controlled by CSS mode classes and context_panel_visible flag.

    Usage:
        panel = ContextPanelWidget(ui_state)
        # Updates happen automatically via reactive attributes + EventBus
    """

    # Reactive state - auto-triggers re-render
    variables: reactive[dict[str, str]] = reactive({}, layout=True)
    functions: reactive[dict[str, str]] = reactive({}, layout=True)
    execution_time_ms: reactive[float] = reactive(0.0)
    memory_mb: reactive[float] = reactive(0.0)

    # Type icons for common Python types
    TYPE_ICONS = {
        "int": "🔢",
        "float": "🔢",
        "str": "📝",
        "list": "📋",
        "dict": "📖",
        "tuple": "📦",
        "set": "🎯",
        "bool": "✓✗",
        "None": "∅",
        "DataFrame": "📊",
        "ndarray": "🔢",
        "Tensor": "🧮",
        "function": "ƒ",
        "class": "🏛️",
        "module": "📦",
    }

    DEFAULT_CSS = """
    ContextPanelWidget {
        dock: right;
        width: 30;
        min-width: 20;
        max-width: 50;
        background: #161b22;  /* $bg-medium */
        border-left: solid #8b949e;  /* $text-muted */
        padding: 1;
        display: none;
    }

    ContextPanelWidget.visible {
        display: block;
    }
    """

    MAX_VARIABLES = 10
    MAX_FUNCTIONS = 8
    MAX_VALUE_LENGTH = 30

    def __init__(
        self,
        ui_state: UIState,
        *,
        name: str | None = None,
        id: str | None = None,
        classes: str | None = None,
    ):
        super().__init__(name=name, id=id, classes=classes)
        self.ui_state = ui_state
        self._event_bus = get_event_bus()
        self._update_timer = None

    def on_mount(self) -> None:
        """Subscribe to EventBus on mount."""
        # Subscribe to relevant events
        self._event_bus.subscribe(EventType.CODE_END, self._on_code_end)
        self._event_bus.subscribe(EventType.AGENT_COMPLETE, self._on_agent_complete)
        self._event_bus.subscribe(
            EventType.SYSTEM_TOKEN_UPDATE, self._on_metrics_update
        )

        # Initial state sync
        self._sync_from_ui_state()

        # Update visibility based on mode
        self._update_visibility()

        # Set up periodic refresh (every 500ms during execution)
        self._update_timer = self.set_interval(0.5, self._sync_from_ui_state)

    def on_unmount(self) -> None:
        """Cleanup on unmount."""
        # Unsubscribe from events
        self._event_bus.unsubscribe(EventType.CODE_END, self._on_code_end)
        self._event_bus.unsubscribe(EventType.AGENT_COMPLETE, self._on_agent_complete)
        self._event_bus.unsubscribe(
            EventType.SYSTEM_TOKEN_UPDATE, self._on_metrics_update
        )

        # Cancel timer
        if self._update_timer:
            self._update_timer.stop()

    def render(self) -> Table | Text:
        """Render the context panel content."""
        # Check if we have any content
        has_content = (
            len(self.variables) > 0
            or len(self.functions) > 0
            or self.execution_time_ms > 0
            or self.memory_mb > 0
        )

        if not has_content:
            # Empty state
            return Text("No context captured", style="dim")

        # Build combined table with sections
        combined = Table(
            show_header=False,
            box=None,
            padding=0,
            expand=True,
        )
        combined.add_column()

        sections_added = 0

        # Variables section
        if self.variables:
            if sections_added > 0:
                combined.add_row(Text("─" * 28, style="dim"))
            combined.add_row(self._build_variables_section())
            sections_added += 1

        # Functions section
        if self.functions:
            if sections_added > 0:
                combined.add_row(Text("─" * 28, style="dim"))
            combined.add_row(self._build_functions_section())
            sections_added += 1

        # Metrics section
        if self.execution_time_ms > 0 or self.memory_mb > 0:
            if sections_added > 0:
                combined.add_row(Text("─" * 28, style="dim"))
            combined.add_row(self._build_metrics_section())

        return combined

    def _build_variables_section(self) -> Table:
        """Build variables section table."""
        table = Table(
            show_header=True,
            header_style="bold cyan",
            box=None,
            padding=(0, 1),
            expand=True,
        )
        table.add_column("Variables", justify="left")

        # Sort and limit
        sorted_vars = sorted(self.variables.items())[: self.MAX_VARIABLES]
        remaining = len(self.variables) - self.MAX_VARIABLES

        for var_name, type_preview in sorted_vars:
            row = self._format_variable(var_name, type_preview)
            table.add_row(row)

        if remaining > 0:
            table.add_row(Text(f"  ... +{remaining} more", style="dim"))

        return table

    def _format_variable(self, name: str, type_preview: str) -> Text:
        """Format a single variable row."""
        result = Text()

        # Parse type and value
        parts = type_preview.split(" = ", 1)
        var_type = parts[0]
        value = parts[1] if len(parts) > 1 else None

        # Type icon
        icon = self._get_type_icon(var_type)
        result.append(f"  {icon} ", style="dim")

        # Variable name
        result.append(name, style="bold cyan")

        # Type annotation
        result.append(f": {var_type}", style="dim italic")

        # Value preview (truncated)
        if value:
            truncated = self._truncate_value(value)
            result.append(f" = {truncated}", style="dim")

        return result

    def _get_type_icon(self, type_str: str) -> str:
        """Get icon for a type string."""
        # Exact match
        if type_str in self.TYPE_ICONS:
            return self.TYPE_ICONS[type_str]

        # Partial match (e.g., "list[int]" matches "list")
        for type_name, icon in self.TYPE_ICONS.items():
            if type_name.lower() in type_str.lower():
                return icon

        return "•"  # Default bullet

    def _truncate_value(self, value: str) -> str:
        """Truncate value to max length."""
        if len(value) <= self.MAX_VALUE_LENGTH:
            return value
        return value[: self.MAX_VALUE_LENGTH - 3] + "..."

    def _build_functions_section(self) -> Table:
        """Build functions section table."""
        table = Table(
            show_header=True,
            header_style="bold cyan",
            box=None,
            padding=(0, 1),
            expand=True,
        )
        table.add_column("Functions", justify="left")

        # Sort and limit
        sorted_funcs = sorted(self.functions.items())[: self.MAX_FUNCTIONS]
        remaining = len(self.functions) - self.MAX_FUNCTIONS

        for func_name, signature in sorted_funcs:
            row = self._format_function(func_name, signature)
            table.add_row(row)

        if remaining > 0:
            table.add_row(Text(f"  ... +{remaining} more", style="dim"))

        return table

    def _format_function(self, name: str, signature: str) -> Text:
        """Format a single function row."""
        result = Text()
        result.append("  ƒ ", style="dim cyan")
        result.append(name, style="bold cyan")
        result.append(signature, style="dim")
        return result

    def _build_metrics_section(self) -> Table:
        """Build metrics section table."""
        table = Table(
            show_header=True,
            header_style="bold cyan",
            box=None,
            padding=(0, 1),
            expand=True,
        )
        table.add_column("Metrics", justify="left")

        metrics = Text()

        # Execution time
        if self.execution_time_ms > 0:
            time_str = self._format_time(self.execution_time_ms)
            metrics.append("  ⏱️  ", style="dim")
            metrics.append(time_str, style="cyan")

        # Memory usage
        if self.memory_mb > 0:
            if self.execution_time_ms > 0:
                metrics.append("   ", style="dim")  # Separator
            mem_str = self._format_memory(self.memory_mb)
            metrics.append("💾 ", style="dim")
            metrics.append(mem_str, style="cyan")

        table.add_row(metrics)
        return table

    def _format_time(self, ms: float) -> str:
        """Format milliseconds to human-readable string."""
        if ms < 1000:
            return f"{ms:.0f}ms"
        elif ms < 60000:
            return f"{ms / 1000:.2f}s"
        else:
            return f"{ms / 60000:.1f}m"

    def _format_memory(self, mb: float) -> str:
        """Format megabytes to human-readable string."""
        if mb < 1:
            return f"{mb * 1024:.0f} KB"
        elif mb < 1024:
            return f"{mb:.1f} MB"
        else:
            return f"{mb / 1024:.2f} GB"

    # Reactive watchers - auto-trigger re-render

    def watch_variables(self, _old: dict, _new: dict) -> None:
        """React to variables changes."""
        self.refresh()
        self._update_visibility()

    def watch_functions(self, _old: dict, _new: dict) -> None:
        """React to functions changes."""
        self.refresh()
        self._update_visibility()

    def watch_execution_time_ms(self, _old: float, _new: float) -> None:
        """React to timing changes."""
        self.refresh()

    def watch_memory_mb(self, _old: float, _new: float) -> None:
        """React to memory changes."""
        self.refresh()

    # EventBus handlers

    def _on_code_end(self, event: UIEvent) -> None:
        """Handle CODE_END event - extract variables/functions from execution context."""
        if not isinstance(event.data, dict):
            return

        # Extract variables if available
        vars_data = event.data.get("variables", {})
        if vars_data:
            self.variables = vars_data

        # Extract functions if available
        funcs_data = event.data.get("functions", {})
        if funcs_data:
            self.functions = funcs_data

        # Extract metrics
        if "execution_time_ms" in event.data:
            self.execution_time_ms = event.data["execution_time_ms"]
        if "memory_mb" in event.data:
            self.memory_mb = event.data["memory_mb"]

    def _on_agent_complete(self, event: UIEvent) -> None:
        """Handle AGENT_COMPLETE - aggregate metrics."""
        if not isinstance(event.data, dict):
            return

        # Accumulate execution time from agent
        agent_time = event.data.get("execution_time_ms", 0)
        if agent_time > 0:
            self.execution_time_ms += agent_time

    def _on_metrics_update(self, event: UIEvent) -> None:
        """Handle SYSTEM_TOKEN_UPDATE with metrics."""
        if not isinstance(event.data, dict):
            return

        metrics = event.data.get("metrics", {})
        if "time_ms" in metrics:
            self.execution_time_ms = metrics["time_ms"]
        if "memory_mb" in metrics:
            self.memory_mb = metrics["memory_mb"]

    # State management

    def _sync_from_ui_state(self) -> None:
        """Sync reactive attributes from UIState.context (thread-safe)."""
        try:
            # Access context state - may have lock in UIState
            ctx = self.ui_state.context

            # Only update if changed (avoid unnecessary re-renders)
            if hasattr(ctx, "variables") and ctx.variables != self.variables:
                self.variables = dict(ctx.variables)

            if hasattr(ctx, "functions") and ctx.functions != self.functions:
                self.functions = dict(ctx.functions)

            if (
                hasattr(ctx, "execution_time_ms")
                and ctx.execution_time_ms != self.execution_time_ms
            ):
                self.execution_time_ms = ctx.execution_time_ms

            if hasattr(ctx, "memory_mb") and ctx.memory_mb != self.memory_mb:
                self.memory_mb = ctx.memory_mb
        except AttributeError:
            # UIState may not have context attribute yet
            pass

    def _update_visibility(self) -> None:
        """Update visible class based on content and mode."""
        # Show if we have content
        has_content = (
            len(self.variables) > 0
            or len(self.functions) > 0
            or self.execution_time_ms > 0
            or self.memory_mb > 0
        )

        if has_content:
            self.add_class("visible")
        else:
            self.remove_class("visible")

    # Public API

    def refresh_from_state(self) -> None:
        """Manually trigger state sync and refresh."""
        self._sync_from_ui_state()
        self.refresh()

    def update_variables(self, vars_dict: dict[str, str]) -> None:
        """Update variables directly (alternative to EventBus)."""
        self.variables = vars_dict

    def update_functions(self, funcs: dict[str, str]) -> None:
        """Update functions directly (alternative to EventBus)."""
        self.functions = funcs

    def update_metrics(self, time_ms: float = 0, mem_mb: float = 0) -> None:
        """Update metrics directly (alternative to EventBus)."""
        if time_ms > 0:
            self.execution_time_ms = time_ms
        if mem_mb > 0:
            self.memory_mb = mem_mb

    def clear(self) -> None:
        """Clear all context data."""
        self.variables = {}
        self.functions = {}
        self.execution_time_ms = 0.0
        self.memory_mb = 0.0
