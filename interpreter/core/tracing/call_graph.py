"""
Call graph structures for execution tracing.

Represents the call relationships observed during code execution,
enabling the system to understand how code flows and which functions
are involved in specific behaviors.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional, Any, Set
import json


@dataclass
class CallNode:
    """
    Represents a single function/method call in the execution trace.
    """
    function_name: str
    module: str
    file_path: str
    line_number: int

    # Call information
    call_id: str = ""
    parent_call_id: Optional[str] = None
    depth: int = 0

    # Timing
    start_time: Optional[float] = None
    end_time: Optional[float] = None

    # Arguments and return value (optional, can be expensive)
    arguments: Optional[Dict[str, Any]] = None
    return_value: Optional[Any] = None

    # Exception if raised
    exception: Optional[str] = None
    exception_type: Optional[str] = None

    # Children calls made from this function
    children: List["CallNode"] = field(default_factory=list)

    @property
    def duration_ms(self) -> Optional[float]:
        """Get call duration in milliseconds."""
        if self.start_time and self.end_time:
            return (self.end_time - self.start_time) * 1000
        return None

    @property
    def qualified_name(self) -> str:
        """Get fully qualified function name."""
        if self.module:
            return f"{self.module}.{self.function_name}"
        return self.function_name

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "function_name": self.function_name,
            "module": self.module,
            "file_path": self.file_path,
            "line_number": self.line_number,
            "call_id": self.call_id,
            "parent_call_id": self.parent_call_id,
            "depth": self.depth,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "duration_ms": self.duration_ms,
            "arguments": self._serialize_value(self.arguments),
            "return_value": self._serialize_value(self.return_value),
            "exception": self.exception,
            "exception_type": self.exception_type,
            "children": [c.to_dict() for c in self.children],
        }

    def _serialize_value(self, value: Any) -> Any:
        """Safely serialize a value for JSON."""
        if value is None:
            return None
        try:
            # Try JSON serialization
            json.dumps(value)
            return value
        except (TypeError, ValueError):
            # Fall back to string representation
            return repr(value)[:200]  # Truncate long reprs

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "CallNode":
        """Create from dictionary."""
        children_data = data.pop("children", [])
        data.pop("duration_ms", None)  # Computed property
        node = cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})
        node.children = [cls.from_dict(c) for c in children_data]
        return node

    def to_summary(self, include_args: bool = False) -> str:
        """Get a brief summary string."""
        parts = [f"{self.qualified_name}()"]

        if self.line_number:
            parts.append(f"at line {self.line_number}")

        if self.duration_ms is not None:
            parts.append(f"({self.duration_ms:.1f}ms)")

        if self.exception:
            parts.append(f"RAISED {self.exception_type}")

        return " ".join(parts)


@dataclass
class CallGraph:
    """
    Represents the complete call graph from an execution.
    """
    root_calls: List[CallNode] = field(default_factory=list)
    all_calls: Dict[str, CallNode] = field(default_factory=dict)

    # Metadata
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None
    total_calls: int = 0

    # Statistics
    functions_called: Set[str] = field(default_factory=set)
    files_touched: Set[str] = field(default_factory=set)
    exceptions_raised: List[str] = field(default_factory=list)

    def add_call(self, node: CallNode, parent_id: Optional[str] = None):
        """Add a call to the graph."""
        self.all_calls[node.call_id] = node
        self.total_calls += 1
        self.functions_called.add(node.qualified_name)

        if node.file_path:
            self.files_touched.add(node.file_path)

        if parent_id and parent_id in self.all_calls:
            parent = self.all_calls[parent_id]
            parent.children.append(node)
            node.parent_call_id = parent_id
            node.depth = parent.depth + 1
        else:
            self.root_calls.append(node)

    def record_exception(self, call_id: str, exception_type: str, exception_msg: str):
        """Record an exception for a call."""
        if call_id in self.all_calls:
            node = self.all_calls[call_id]
            node.exception_type = exception_type
            node.exception = exception_msg
            self.exceptions_raised.append(f"{exception_type}: {exception_msg}")

    def get_call_chain(self, call_id: str) -> List[CallNode]:
        """Get the chain of calls from root to the specified call."""
        chain = []
        current_id = call_id

        while current_id and current_id in self.all_calls:
            node = self.all_calls[current_id]
            chain.append(node)
            current_id = node.parent_call_id

        return list(reversed(chain))

    def get_hot_functions(self, top_n: int = 10) -> List[tuple]:
        """Get the most frequently called functions."""
        call_counts: Dict[str, int] = {}
        total_time: Dict[str, float] = {}

        for node in self.all_calls.values():
            name = node.qualified_name
            call_counts[name] = call_counts.get(name, 0) + 1
            if node.duration_ms:
                total_time[name] = total_time.get(name, 0) + node.duration_ms

        # Sort by call count
        sorted_funcs = sorted(
            call_counts.items(),
            key=lambda x: x[1],
            reverse=True
        )[:top_n]

        return [(name, count, total_time.get(name, 0)) for name, count in sorted_funcs]

    def get_slow_functions(self, top_n: int = 10) -> List[tuple]:
        """Get the slowest functions by total time."""
        total_time: Dict[str, float] = {}
        call_counts: Dict[str, int] = {}

        for node in self.all_calls.values():
            name = node.qualified_name
            call_counts[name] = call_counts.get(name, 0) + 1
            if node.duration_ms:
                total_time[name] = total_time.get(name, 0) + node.duration_ms

        # Sort by total time
        sorted_funcs = sorted(
            total_time.items(),
            key=lambda x: x[1],
            reverse=True
        )[:top_n]

        return [(name, time, call_counts.get(name, 0)) for name, time in sorted_funcs]

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "root_calls": [c.to_dict() for c in self.root_calls],
            "start_time": self.start_time.isoformat() if self.start_time else None,
            "end_time": self.end_time.isoformat() if self.end_time else None,
            "total_calls": self.total_calls,
            "functions_called": list(self.functions_called),
            "files_touched": list(self.files_touched),
            "exceptions_raised": self.exceptions_raised,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "CallGraph":
        """Create from dictionary."""
        graph = cls()
        graph.start_time = datetime.fromisoformat(data["start_time"]) if data.get("start_time") else None
        graph.end_time = datetime.fromisoformat(data["end_time"]) if data.get("end_time") else None
        graph.total_calls = data.get("total_calls", 0)
        graph.functions_called = set(data.get("functions_called", []))
        graph.files_touched = set(data.get("files_touched", []))
        graph.exceptions_raised = data.get("exceptions_raised", [])

        for root_data in data.get("root_calls", []):
            root = CallNode.from_dict(root_data)
            graph.root_calls.append(root)
            graph._index_node(root)

        return graph

    def _index_node(self, node: CallNode):
        """Recursively index a node and its children."""
        if node.call_id:
            self.all_calls[node.call_id] = node
        for child in node.children:
            self._index_node(child)

    def to_tree_string(self, max_depth: int = 10) -> str:
        """Generate a tree representation of the call graph."""
        lines = []

        def _format_node(node: CallNode, prefix: str = "", is_last: bool = True):
            connector = "└── " if is_last else "├── "
            lines.append(f"{prefix}{connector}{node.to_summary()}")

            if node.depth >= max_depth:
                if node.children:
                    lines.append(f"{prefix}    └── ... ({len(node.children)} more)")
                return

            child_prefix = prefix + ("    " if is_last else "│   ")
            for i, child in enumerate(node.children):
                _format_node(child, child_prefix, i == len(node.children) - 1)

        for i, root in enumerate(self.root_calls):
            _format_node(root, "", i == len(self.root_calls) - 1)

        return "\n".join(lines)
