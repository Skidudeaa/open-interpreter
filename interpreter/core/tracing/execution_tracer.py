"""
ExecutionTracer - Captures runtime execution traces from Python code.

Uses Python's sys.settrace to capture:
- Function calls and returns
- Variable states
- Exceptions
- Call graphs

This enables "Execution-Informed Editing" where code changes are
informed by actual observed runtime behavior.
"""

import sys
import time
import uuid
import threading
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set
import traceback

from .call_graph import CallGraph, CallNode


@dataclass
class ExecutionTrace:
    """
    Complete execution trace from a code run.
    """
    trace_id: str = field(default_factory=lambda: str(uuid.uuid4())[:16])

    # Call graph
    call_graph: CallGraph = field(default_factory=CallGraph)

    # Captured output
    stdout: str = ""
    stderr: str = ""

    # Exception info
    exception_occurred: bool = False
    exception_type: Optional[str] = None
    exception_message: Optional[str] = None
    exception_traceback: Optional[str] = None

    # Timing
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None

    # Code that was executed
    source_code: str = ""
    file_path: str = ""

    @property
    def duration_ms(self) -> Optional[float]:
        """Get execution duration in milliseconds."""
        if self.start_time and self.end_time:
            delta = self.end_time - self.start_time
            return delta.total_seconds() * 1000
        return None

    @property
    def success(self) -> bool:
        """Was the execution successful (no exceptions)?"""
        return not self.exception_occurred

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "trace_id": self.trace_id,
            "call_graph": self.call_graph.to_dict(),
            "stdout": self.stdout,
            "stderr": self.stderr,
            "exception_occurred": self.exception_occurred,
            "exception_type": self.exception_type,
            "exception_message": self.exception_message,
            "exception_traceback": self.exception_traceback,
            "start_time": self.start_time.isoformat() if self.start_time else None,
            "end_time": self.end_time.isoformat() if self.end_time else None,
            "duration_ms": self.duration_ms,
            "source_code": self.source_code,
            "file_path": self.file_path,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ExecutionTrace":
        """Create from dictionary."""
        trace = cls()
        trace.trace_id = data.get("trace_id", trace.trace_id)
        trace.call_graph = CallGraph.from_dict(data.get("call_graph", {}))
        trace.stdout = data.get("stdout", "")
        trace.stderr = data.get("stderr", "")
        trace.exception_occurred = data.get("exception_occurred", False)
        trace.exception_type = data.get("exception_type")
        trace.exception_message = data.get("exception_message")
        trace.exception_traceback = data.get("exception_traceback")
        trace.start_time = datetime.fromisoformat(data["start_time"]) if data.get("start_time") else None
        trace.end_time = datetime.fromisoformat(data["end_time"]) if data.get("end_time") else None
        trace.source_code = data.get("source_code", "")
        trace.file_path = data.get("file_path", "")
        return trace


class ExecutionTracer:
    """
    Traces Python code execution using sys.settrace.

    Usage:
        tracer = ExecutionTracer()
        trace = tracer.trace_code(code_string)
        print(trace.call_graph.to_tree_string())
    """

    def __init__(
        self,
        capture_args: bool = False,
        capture_return: bool = False,
        max_depth: int = 50,
        exclude_modules: Optional[Set[str]] = None,
        include_only: Optional[Set[str]] = None,
    ):
        """
        Initialize the tracer.

        Args:
            capture_args: Whether to capture function arguments (can be slow)
            capture_return: Whether to capture return values (can be slow)
            max_depth: Maximum call depth to trace
            exclude_modules: Module prefixes to exclude from tracing
            include_only: If set, only trace these module prefixes
        """
        self.capture_args = capture_args
        self.capture_return = capture_return
        self.max_depth = max_depth

        # Default exclusions for stdlib and common packages
        self.exclude_modules = exclude_modules or {
            "sys", "os", "io", "re", "json", "collections",
            "threading", "multiprocessing", "asyncio",
            "traceback", "linecache", "tokenize", "token",
            "abc", "functools", "inspect", "types",
            "importlib", "_", "<",
        }
        self.include_only = include_only

        # Thread-local storage for tracing state
        self._local = threading.local()

    def _get_state(self):
        """Get thread-local tracing state."""
        if not hasattr(self._local, 'state'):
            self._local.state = {
                'active': False,
                'call_stack': [],
                'call_graph': None,
                'depth': 0,
            }
        return self._local.state

    def _should_trace(self, filename: str, module: str) -> bool:
        """Determine if a function should be traced."""
        # Check include_only first
        if self.include_only:
            return any(module.startswith(prefix) for prefix in self.include_only)

        # Check exclusions
        for prefix in self.exclude_modules:
            if module.startswith(prefix):
                return False

        # Exclude internal Python files
        if "<" in filename or filename.startswith("<"):
            return False

        return True

    def _trace_function(self, frame, event, arg):
        """
        Trace function for sys.settrace.

        This is called for every line, call, return, and exception.
        """
        state = self._get_state()

        if not state['active']:
            return None

        # Extract info from frame
        code = frame.f_code
        filename = code.co_filename
        function_name = code.co_name
        line_number = frame.f_lineno

        # Determine module name
        module = frame.f_globals.get('__name__', '')

        # Check if we should trace this
        if not self._should_trace(filename, module):
            return self._trace_function

        if event == 'call':
            # Don't exceed max depth
            if state['depth'] >= self.max_depth:
                return None

            # Create call node
            call_id = f"{state['depth']}_{len(state['call_graph'].all_calls)}"

            node = CallNode(
                function_name=function_name,
                module=module,
                file_path=filename,
                line_number=line_number,
                call_id=call_id,
                start_time=time.time(),
            )

            # Capture arguments if enabled
            if self.capture_args:
                try:
                    args = {}
                    for key, value in frame.f_locals.items():
                        if not key.startswith('_'):
                            args[key] = self._safe_repr(value)
                    node.arguments = args
                except Exception:
                    pass

            # Add to graph
            parent_id = state['call_stack'][-1] if state['call_stack'] else None
            state['call_graph'].add_call(node, parent_id)

            # Update state
            state['call_stack'].append(call_id)
            state['depth'] += 1

        elif event == 'return':
            if state['call_stack']:
                call_id = state['call_stack'].pop()
                state['depth'] -= 1

                if call_id in state['call_graph'].all_calls:
                    node = state['call_graph'].all_calls[call_id]
                    node.end_time = time.time()

                    if self.capture_return:
                        node.return_value = self._safe_repr(arg)

        elif event == 'exception':
            if state['call_stack']:
                call_id = state['call_stack'][-1]
                exc_type, exc_value, _ = arg
                state['call_graph'].record_exception(
                    call_id,
                    exc_type.__name__ if exc_type else "Unknown",
                    str(exc_value) if exc_value else ""
                )

        return self._trace_function

    def _safe_repr(self, value: Any, max_len: int = 100) -> str:
        """Safely get string representation of a value."""
        try:
            r = repr(value)
            if len(r) > max_len:
                return r[:max_len - 3] + "..."
            return r
        except Exception:
            return "<unrepresentable>"

    def trace_code(
        self,
        code: str,
        filename: str = "<traced>",
        globals_dict: Optional[Dict] = None,
        locals_dict: Optional[Dict] = None,
    ) -> ExecutionTrace:
        """
        Execute code with tracing enabled.

        Args:
            code: Python code to execute
            filename: Filename to use for the code
            globals_dict: Global namespace for execution
            locals_dict: Local namespace for execution

        Returns:
            ExecutionTrace with call graph and execution info
        """
        trace = ExecutionTrace(
            source_code=code,
            file_path=filename,
            start_time=datetime.now(),
        )

        state = self._get_state()
        state['active'] = True
        state['call_stack'] = []
        state['call_graph'] = CallGraph(start_time=datetime.now())
        state['depth'] = 0

        # Prepare execution namespace
        if globals_dict is None:
            globals_dict = {'__name__': '__main__', '__file__': filename}
        if locals_dict is None:
            locals_dict = globals_dict

        # Capture stdout/stderr
        import io
        old_stdout = sys.stdout
        old_stderr = sys.stderr
        captured_stdout = io.StringIO()
        captured_stderr = io.StringIO()

        try:
            sys.stdout = captured_stdout
            sys.stderr = captured_stderr

            # Set the trace function
            sys.settrace(self._trace_function)

            # Compile and execute
            compiled = compile(code, filename, 'exec')
            exec(compiled, globals_dict, locals_dict)

        except Exception as e:
            trace.exception_occurred = True
            trace.exception_type = type(e).__name__
            trace.exception_message = str(e)
            trace.exception_traceback = traceback.format_exc()

        finally:
            # Disable tracing
            sys.settrace(None)
            state['active'] = False

            # Restore stdout/stderr
            sys.stdout = old_stdout
            sys.stderr = old_stderr

            trace.stdout = captured_stdout.getvalue()
            trace.stderr = captured_stderr.getvalue()

        # Complete the trace
        trace.end_time = datetime.now()
        trace.call_graph = state['call_graph']
        trace.call_graph.end_time = datetime.now()

        return trace

    def inject_tracing_hooks(self, code: str) -> str:
        """
        Inject tracing calls into code without using sys.settrace.

        This is a lighter-weight alternative that modifies the code
        to include explicit trace calls. Useful when sys.settrace
        is too expensive or causes issues.

        Args:
            code: Original Python code

        Returns:
            Modified code with tracing hooks
        """
        # This is a simplified version - a full implementation would
        # use AST transformation

        preamble = '''
# Auto-injected tracing
import time as _trace_time
_trace_calls = []
_trace_start = _trace_time.time()

def _trace_call(name, line):
    _trace_calls.append({"name": name, "line": line, "time": _trace_time.time() - _trace_start})

'''
        postamble = '''
# Tracing summary
if _trace_calls:
    print(f"\\n[Trace: {len(_trace_calls)} calls]")
'''
        return preamble + code + postamble


# Convenience function
def trace_execution(code: str, **kwargs) -> ExecutionTrace:
    """
    Trace the execution of Python code.

    Args:
        code: Python code to execute
        **kwargs: Additional arguments for ExecutionTracer

    Returns:
        ExecutionTrace with execution details
    """
    tracer = ExecutionTracer(**kwargs)
    return tracer.trace_code(code)
