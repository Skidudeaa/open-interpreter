"""
Execution tracing module for Open Interpreter.

Provides runtime tracing capabilities to capture:
- Function call graphs
- Variable states
- Exception traces
- Execution flow

This enables "Execution-Informed Editing" - making code edits
based on observed runtime behavior rather than static analysis.
"""

from .call_graph import CallGraph, CallNode
from .execution_tracer import ExecutionTrace, ExecutionTracer, TraceContext
from .trace_context import TraceContextGenerator

__all__ = [
    "ExecutionTracer",
    "ExecutionTrace",
    "TraceContext",
    "CallGraph",
    "CallNode",
    "TraceContextGenerator",
]
