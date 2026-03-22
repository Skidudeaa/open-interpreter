"""
Context Panel - Variables/functions/metrics sidebar.

Displays execution context: variables, functions defined, timing, and memory usage.
Appears in POWER/DEBUG mode or when content exists.
Reads from UIState.context.

Part of Phase 3: Context Panel
"""

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from .theme import BOX_STYLES, THEME
from .ui_state import ContextState, UIMode, UIState


class ContextPanel:
    """
    Variables/functions/metrics sidebar.

    Layout:
    ┌─ 📋 Context ───────────────┐
    │ Variables                   │
    │   x: int = 42              │
    │   df: DataFrame (1000×5)   │
    │   result: str = "hello..." │
    ├─────────────────────────────┤
    │ Functions                   │
    │   process_data(df, n)      │
    │   validate_input(x)        │
    ├─────────────────────────────┤
    │ Metrics                     │
    │   ⏱️ 1.23s   💾 45.2 MB     │
    └─────────────────────────────┘

    Visibility:
    - POWER/DEBUG mode: Always visible
    - Other modes: Visible when variables/functions exist
    - Can be toggled with Alt+H
    """

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

    def __init__(self, state: UIState, console: Console | None = None):
        """
        Initialize the context panel.

        Args:
            state: The UIState instance
            console: Optional console to use
        """
        self.state = state
        self.console = console or Console()
        self.max_value_length = 30  # Truncate long values
        self.max_variables = 10  # Max variables to show
        self.max_functions = 8  # Max functions to show

    def should_show(self) -> bool:
        """
        Determine if the context panel should be displayed.

        Returns:
            True if panel should be shown
        """
        return self.state.context_panel_visible

    def render(self) -> Panel | None:
        """
        Render the context panel.

        Returns:
            Panel with context info, or None if empty/hidden
        """
        if not self.should_show():
            return None

        ctx = self.state.context
        sections = []

        # Variables section
        if ctx.variables:
            var_section = self._build_variables_section(ctx.variables)
            sections.append(var_section)

        # Functions section
        if ctx.functions:
            func_section = self._build_functions_section(ctx.functions)
            sections.append(func_section)

        # Metrics section (always show if we have timing or memory)
        if ctx.execution_time_ms > 0 or ctx.memory_mb > 0:
            metrics_section = self._build_metrics_section(ctx)
            sections.append(metrics_section)

        # If no content, return minimal panel in POWER/DEBUG mode
        if not sections:
            if self.state.mode in (UIMode.POWER, UIMode.DEBUG):
                return Panel(
                    Text("No context captured", style="dim"),
                    title="📋 Context",
                    title_align="left",
                    box=BOX_STYLES["message"],
                    style=f"on {THEME['bg_dark']}",
                    border_style=THEME["text_muted"],
                    padding=(0, 1),
                    width=32,
                )
            return None

        # Combine sections with separators
        content = self._combine_sections(sections)

        return Panel(
            content,
            title="📋 Context",
            title_align="left",
            box=BOX_STYLES["message"],
            style=f"on {THEME['bg_dark']}",
            border_style=THEME["primary"],
            padding=(0, 1),
            width=32,
        )

    def _build_variables_section(self, variables: dict) -> Table:
        """
        Build the variables section.

        Args:
            variables: Dict of name -> type/preview

        Returns:
            Table with variable display
        """
        table = Table(
            show_header=True,
            header_style=f"bold {THEME['secondary']}",
            box=None,
            padding=(0, 1),
            expand=True,
        )
        table.add_column("Variables", justify="left")

        # Sort by name, limit count
        sorted_vars = sorted(variables.items())[: self.max_variables]
        remaining = len(variables) - self.max_variables

        for name, type_preview in sorted_vars:
            row = self._format_variable(name, type_preview)
            table.add_row(row)

        if remaining > 0:
            table.add_row(Text(f"  ... +{remaining} more", style="dim"))

        return table

    def _format_variable(self, name: str, type_preview: str) -> Text:
        """
        Format a single variable for display.

        Args:
            name: Variable name
            type_preview: Type and/or preview value

        Returns:
            Formatted Text
        """
        result = Text()

        # Extract type from preview (format: "type = value" or just "type")
        parts = type_preview.split(" = ", 1)
        var_type = parts[0]
        value = parts[1] if len(parts) > 1 else None

        # Get type icon
        icon = self._get_type_icon(var_type)
        result.append(f"  {icon} ", style="dim")

        # Variable name
        result.append(name, style=f"bold {THEME['primary']}")

        # Type
        result.append(f": {var_type}", style=THEME["text_muted"])

        # Value preview (truncated)
        if value:
            truncated = self._truncate_value(value)
            result.append(f" = {truncated}", style="dim")

        return result

    def _get_type_icon(self, type_str: str) -> str:
        """Get icon for a type string."""
        # Check for exact match first
        if type_str in self.TYPE_ICONS:
            return self.TYPE_ICONS[type_str]

        # Check for partial matches (e.g., "list[int]" matches "list")
        for type_name, icon in self.TYPE_ICONS.items():
            if type_name.lower() in type_str.lower():
                return icon

        return "•"  # Default bullet

    def _truncate_value(self, value: str) -> str:
        """Truncate a value preview to max length."""
        if len(value) <= self.max_value_length:
            return value
        return value[: self.max_value_length - 3] + "..."

    def _build_functions_section(self, functions: dict) -> Table:
        """
        Build the functions section.

        Args:
            functions: Dict of name -> signature

        Returns:
            Table with function display
        """
        table = Table(
            show_header=True,
            header_style=f"bold {THEME['secondary']}",
            box=None,
            padding=(0, 1),
            expand=True,
        )
        table.add_column("Functions", justify="left")

        # Sort by name, limit count
        sorted_funcs = sorted(functions.items())[: self.max_functions]
        remaining = len(functions) - self.max_functions

        for name, signature in sorted_funcs:
            row = self._format_function(name, signature)
            table.add_row(row)

        if remaining > 0:
            table.add_row(Text(f"  ... +{remaining} more", style="dim"))

        return table

    def _format_function(self, name: str, signature: str) -> Text:
        """
        Format a single function for display.

        Args:
            name: Function name
            signature: Function signature (e.g., "(x, y) -> int")

        Returns:
            Formatted Text
        """
        result = Text()
        result.append("  ƒ ", style=f"dim {THEME['secondary']}")
        result.append(name, style=f"bold {THEME['primary']}")
        result.append(signature, style="dim")
        return result

    def _build_metrics_section(self, ctx: ContextState) -> Table:
        """
        Build the metrics section.

        Args:
            ctx: ContextState with timing and memory

        Returns:
            Table with metrics display
        """
        table = Table(
            show_header=True,
            header_style=f"bold {THEME['secondary']}",
            box=None,
            padding=(0, 1),
            expand=True,
        )
        table.add_column("Metrics", justify="left")

        metrics = Text()

        # Execution time
        if ctx.execution_time_ms > 0:
            time_str = self._format_time(ctx.execution_time_ms)
            metrics.append("  ⏱️ ", style="dim")
            metrics.append(time_str, style=THEME["text_secondary"])

        # Memory usage
        if ctx.memory_mb > 0:
            if ctx.execution_time_ms > 0:
                metrics.append("   ", style="dim")  # Separator
            mem_str = self._format_memory(ctx.memory_mb)
            metrics.append("💾 ", style="dim")
            metrics.append(mem_str, style=THEME["text_secondary"])

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

    def _combine_sections(self, sections: list[Table]) -> Table:
        """
        Combine multiple sections into a single table.

        Args:
            sections: List of Table sections

        Returns:
            Combined Table
        """
        combined = Table(
            show_header=False,
            box=None,
            padding=0,
            expand=True,
        )
        combined.add_column()

        for i, section in enumerate(sections):
            combined.add_row(section)
            # Add separator between sections (except after last)
            if i < len(sections) - 1:
                combined.add_row(Text("─" * 28, style="dim"))

        return combined

    def display(self):
        """Print the context panel to the console."""
        panel = self.render()
        if panel:
            self.console.print(panel)

    def update_variable(self, name: str, type_preview: str):
        """
        Update a variable in the context.

        Args:
            name: Variable name
            type_preview: Type and/or preview (e.g., "int = 42")
        """
        self.state.context.variables[name] = type_preview

    def update_function(self, name: str, signature: str):
        """
        Update a function in the context.

        Args:
            name: Function name
            signature: Function signature (e.g., "(x, y) -> int")
        """
        self.state.context.functions[name] = signature

    def set_metrics(self, execution_time_ms: float = None, memory_mb: float = None):
        """
        Update execution metrics.

        Args:
            execution_time_ms: Execution time in milliseconds
            memory_mb: Memory usage in megabytes
        """
        if execution_time_ms is not None:
            self.state.context.execution_time_ms = execution_time_ms
        if memory_mb is not None:
            self.state.context.memory_mb = memory_mb

    def clear(self):
        """Clear all context state."""
        self.state.context.variables.clear()
        self.state.context.functions.clear()
        self.state.context.execution_time_ms = 0.0
        self.state.context.memory_mb = 0.0


def display_context_panel(state: UIState, console: Console | None = None):
    """
    Convenience function to display the context panel.

    Args:
        state: The UIState instance
        console: Optional console to use
    """
    ContextPanel(state, console).display()
