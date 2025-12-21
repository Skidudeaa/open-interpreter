"""
TraceContextGenerator - Converts execution traces to LLM-readable context.

This module transforms raw execution traces into structured context
that can be included in LLM prompts to enable "Execution-Informed Editing".

The context helps the LLM understand:
- What functions were called and in what order
- Where time was spent (performance bottlenecks)
- What exceptions occurred and where
- The actual execution path through the code
"""

from typing import Dict, Any, List, Optional
from dataclasses import dataclass

from .execution_tracer import ExecutionTrace
from .call_graph import CallGraph, CallNode


@dataclass
class TraceContext:
    """
    Structured context derived from an execution trace.
    """
    summary: str
    call_flow: str
    exceptions: str
    performance: str
    files_touched: List[str]

    def to_prompt_string(self, max_length: int = 2000) -> str:
        """
        Convert to a string suitable for LLM prompts.

        Args:
            max_length: Maximum length of the output string

        Returns:
            Formatted context string
        """
        parts = [
            "## Execution Context",
            "",
            "### Summary",
            self.summary,
            "",
        ]

        if self.exceptions:
            parts.extend([
                "### Exceptions",
                self.exceptions,
                "",
            ])

        parts.extend([
            "### Call Flow",
            self.call_flow,
            "",
        ])

        if self.performance:
            parts.extend([
                "### Performance",
                self.performance,
            ])

        result = "\n".join(parts)

        # Truncate if needed
        if len(result) > max_length:
            result = result[:max_length - 100] + "\n\n... [truncated]"

        return result


class TraceContextGenerator:
    """
    Generates LLM context from execution traces.
    """

    def __init__(
        self,
        max_call_depth: int = 5,
        max_calls_shown: int = 20,
        include_timing: bool = True,
    ):
        """
        Initialize the generator.

        Args:
            max_call_depth: Maximum depth of calls to show
            max_calls_shown: Maximum number of calls to include
            include_timing: Whether to include timing information
        """
        self.max_call_depth = max_call_depth
        self.max_calls_shown = max_calls_shown
        self.include_timing = include_timing

    def generate(self, trace: ExecutionTrace) -> TraceContext:
        """
        Generate context from an execution trace.

        Args:
            trace: The execution trace

        Returns:
            TraceContext with formatted sections
        """
        return TraceContext(
            summary=self._generate_summary(trace),
            call_flow=self._generate_call_flow(trace.call_graph),
            exceptions=self._generate_exceptions(trace),
            performance=self._generate_performance(trace.call_graph),
            files_touched=list(trace.call_graph.files_touched),
        )

    def _generate_summary(self, trace: ExecutionTrace) -> str:
        """Generate execution summary."""
        parts = []

        # Status
        status = "SUCCESS" if trace.success else "FAILED"
        parts.append(f"Execution: {status}")

        # Duration
        if trace.duration_ms:
            parts.append(f"Duration: {trace.duration_ms:.1f}ms")

        # Call statistics
        graph = trace.call_graph
        parts.append(f"Total calls: {graph.total_calls}")
        parts.append(f"Unique functions: {len(graph.functions_called)}")
        parts.append(f"Files touched: {len(graph.files_touched)}")

        # Exceptions
        if trace.exception_occurred:
            parts.append(f"Exception: {trace.exception_type}: {trace.exception_message}")

        # Output preview
        if trace.stdout:
            stdout_preview = trace.stdout[:100].replace("\n", " ")
            if len(trace.stdout) > 100:
                stdout_preview += "..."
            parts.append(f"Output: {stdout_preview}")

        return "\n".join(parts)

    def _generate_call_flow(self, graph: CallGraph) -> str:
        """Generate call flow representation."""
        if not graph.root_calls:
            return "No calls traced"

        lines = []
        calls_shown = 0

        def _format_call(node: CallNode, depth: int = 0):
            nonlocal calls_shown

            if calls_shown >= self.max_calls_shown:
                return

            if depth > self.max_call_depth:
                return

            indent = "  " * depth
            call_str = f"{indent}{node.function_name}()"

            if node.line_number:
                call_str += f" @ line {node.line_number}"

            if self.include_timing and node.duration_ms:
                call_str += f" [{node.duration_ms:.1f}ms]"

            if node.exception:
                call_str += f" RAISED {node.exception_type}"

            lines.append(call_str)
            calls_shown += 1

            for child in node.children:
                _format_call(child, depth + 1)

        for root in graph.root_calls:
            _format_call(root)
            if calls_shown >= self.max_calls_shown:
                remaining = graph.total_calls - calls_shown
                if remaining > 0:
                    lines.append(f"... and {remaining} more calls")
                break

        return "\n".join(lines) if lines else "No calls traced"

    def _generate_exceptions(self, trace: ExecutionTrace) -> str:
        """Generate exception details."""
        if not trace.exception_occurred:
            return ""

        parts = [
            f"Type: {trace.exception_type}",
            f"Message: {trace.exception_message}",
        ]

        if trace.exception_traceback:
            # Include last few lines of traceback
            tb_lines = trace.exception_traceback.strip().split("\n")
            if len(tb_lines) > 10:
                tb_lines = tb_lines[-10:]
            parts.append("Traceback (last 10 lines):")
            parts.extend(tb_lines)

        # Include exception call chain from graph
        if trace.call_graph.exceptions_raised:
            parts.append("")
            parts.append("Exception call chain:")
            for exc in trace.call_graph.exceptions_raised[:5]:
                parts.append(f"  - {exc}")

        return "\n".join(parts)

    def _generate_performance(self, graph: CallGraph) -> str:
        """Generate performance analysis."""
        if not graph.total_calls:
            return ""

        parts = []

        # Hot functions (most called)
        hot = graph.get_hot_functions(top_n=5)
        if hot:
            parts.append("Most called functions:")
            for name, count, total_time in hot:
                short_name = name.split(".")[-1] if "." in name else name
                parts.append(f"  {short_name}: {count}x ({total_time:.1f}ms total)")

        # Slow functions (most time)
        slow = graph.get_slow_functions(top_n=5)
        if slow:
            parts.append("")
            parts.append("Slowest functions:")
            for name, total_time, count in slow:
                short_name = name.split(".")[-1] if "." in name else name
                avg_time = total_time / count if count else 0
                parts.append(f"  {short_name}: {total_time:.1f}ms total ({avg_time:.1f}ms avg, {count}x)")

        return "\n".join(parts) if parts else ""

    def to_edit_context(
        self,
        trace: ExecutionTrace,
        focus_file: Optional[str] = None,
        focus_function: Optional[str] = None,
    ) -> str:
        """
        Generate context specifically for informing code edits.

        This provides a more targeted view of the trace, focused on
        what's relevant to making a specific edit.

        Args:
            trace: The execution trace
            focus_file: File to focus on (optional)
            focus_function: Function to focus on (optional)

        Returns:
            Formatted context string for edit prompts
        """
        parts = ["## Execution-Informed Edit Context", ""]

        # Overall status
        if trace.exception_occurred:
            parts.extend([
                "### Observed Issue",
                f"**Exception**: {trace.exception_type}: {trace.exception_message}",
                "",
            ])

            # Show the exception traceback focused on relevant file
            if trace.exception_traceback:
                parts.append("**Traceback**:")
                parts.append("```")
                parts.append(trace.exception_traceback)
                parts.append("```")
                parts.append("")

        # Show relevant call flow
        parts.append("### Execution Path")

        # Filter calls to focus file/function if specified
        relevant_calls = []
        for node in trace.call_graph.all_calls.values():
            if focus_file and focus_file not in node.file_path:
                continue
            if focus_function and focus_function not in node.function_name:
                continue
            relevant_calls.append(node)

        if relevant_calls:
            # Sort by depth then time
            relevant_calls.sort(key=lambda n: (n.depth, n.start_time or 0))

            for node in relevant_calls[:15]:
                indent = "  " * min(node.depth, 4)
                call_info = f"{indent}- `{node.function_name}()` at line {node.line_number}"

                if node.duration_ms:
                    call_info += f" ({node.duration_ms:.1f}ms)"

                if node.exception:
                    call_info += f" **RAISED {node.exception_type}**"

                parts.append(call_info)

            if len(relevant_calls) > 15:
                parts.append(f"  ... and {len(relevant_calls) - 15} more calls")
        else:
            parts.append("No relevant calls traced")

        parts.append("")

        # Output if relevant
        if trace.stdout and len(trace.stdout) < 500:
            parts.extend([
                "### Program Output",
                "```",
                trace.stdout.strip(),
                "```",
                "",
            ])

        # Recommendations based on trace
        parts.append("### Observations")
        observations = self._generate_observations(trace, focus_file, focus_function)
        parts.extend(observations)

        return "\n".join(parts)

    def _generate_observations(
        self,
        trace: ExecutionTrace,
        focus_file: Optional[str],
        focus_function: Optional[str],
    ) -> List[str]:
        """Generate observations/recommendations from the trace."""
        observations = []

        if trace.exception_occurred:
            observations.append(f"- Exception `{trace.exception_type}` occurred during execution")

            # Try to identify the line that caused it
            if trace.exception_traceback:
                lines = trace.exception_traceback.strip().split("\n")
                for line in reversed(lines):
                    if "line" in line.lower() and focus_file and focus_file in line:
                        observations.append(f"- Error location: {line.strip()}")
                        break

        # Performance observations
        slow = trace.call_graph.get_slow_functions(top_n=3)
        for name, total_time, count in slow:
            if total_time > 100:  # More than 100ms
                observations.append(f"- Function `{name}` is slow ({total_time:.0f}ms)")

        # Call count observations
        hot = trace.call_graph.get_hot_functions(top_n=3)
        for name, count, _ in hot:
            if count > 100:  # Called many times
                observations.append(f"- Function `{name}` called {count} times (consider optimization)")

        if not observations:
            observations.append("- Execution completed normally")

        return observations


# Convenience function
def generate_edit_context(trace: ExecutionTrace, **kwargs) -> str:
    """
    Generate edit context from an execution trace.

    Args:
        trace: The execution trace
        **kwargs: Additional arguments for TraceContextGenerator.to_edit_context

    Returns:
        Formatted context string
    """
    generator = TraceContextGenerator()
    return generator.to_edit_context(trace, **kwargs)
