"""
Data structures for tracking code edits with semantic context.

These structures form the foundation of the Semantic Edit Graph,
enabling institutional memory for codebases by linking:
- Code symbols (functions, classes, variables)
- Conversation turns that prompted changes
- Edit operations that modified code
- Test results that validated changes
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import List, Optional, Dict, Any
import uuid


class EditType(Enum):
    """Classification of edit intent."""
    BUG_FIX = "bug_fix"
    FEATURE = "feature"
    REFACTOR = "refactor"
    OPTIMIZATION = "optimization"
    DOCUMENTATION = "documentation"
    TEST = "test"
    DEPENDENCY = "dependency"
    CONFIGURATION = "configuration"
    UNKNOWN = "unknown"


@dataclass
class SymbolReference:
    """Reference to a code symbol (function, class, variable, etc.)."""
    name: str
    kind: str  # 'function', 'class', 'method', 'variable', 'import'
    file_path: str
    line_start: int
    line_end: int
    signature: Optional[str] = None  # For functions/methods
    docstring: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "kind": self.kind,
            "file_path": self.file_path,
            "line_start": self.line_start,
            "line_end": self.line_end,
            "signature": self.signature,
            "docstring": self.docstring,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SymbolReference":
        return cls(**data)


@dataclass
class TestResult:
    """Result of a test run for edit validation."""
    test_name: str
    passed: bool
    duration_ms: float
    error_message: Optional[str] = None
    stack_trace: Optional[str] = None
    timestamp: datetime = field(default_factory=datetime.now)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "test_name": self.test_name,
            "passed": self.passed,
            "duration_ms": self.duration_ms,
            "error_message": self.error_message,
            "stack_trace": self.stack_trace,
            "timestamp": self.timestamp.isoformat(),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "TestResult":
        data = data.copy()
        data["timestamp"] = datetime.fromisoformat(data["timestamp"])
        return cls(**data)


@dataclass
class ConversationContext:
    """Context from the conversation that prompted an edit."""
    conversation_id: str
    turn_index: int
    user_message: str
    assistant_response: Optional[str] = None
    intent_summary: Optional[str] = None  # LLM-generated summary of intent
    timestamp: datetime = field(default_factory=datetime.now)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "conversation_id": self.conversation_id,
            "turn_index": self.turn_index,
            "user_message": self.user_message,
            "assistant_response": self.assistant_response,
            "intent_summary": self.intent_summary,
            "timestamp": self.timestamp.isoformat(),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ConversationContext":
        data = data.copy()
        data["timestamp"] = datetime.fromisoformat(data["timestamp"])
        return cls(**data)


@dataclass
class EditResult:
    """Result of an edit operation."""
    success: bool
    syntax_valid: bool = True
    type_check_passed: Optional[bool] = None
    tests_passed: Optional[bool] = None
    test_results: List[TestResult] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "success": self.success,
            "syntax_valid": self.syntax_valid,
            "type_check_passed": self.type_check_passed,
            "tests_passed": self.tests_passed,
            "test_results": [tr.to_dict() for tr in self.test_results],
            "errors": self.errors,
            "warnings": self.warnings,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "EditResult":
        data = data.copy()
        data["test_results"] = [TestResult.from_dict(tr) for tr in data.get("test_results", [])]
        return cls(**data)


@dataclass
class Edit:
    """
    A semantic edit record linking code changes to context and intent.

    This is the core data structure of the Semantic Edit Graph, capturing
    not just WHAT changed, but WHY it changed and what concepts were affected.
    """
    id: str = field(default_factory=lambda: str(uuid.uuid4()))

    # File and content info
    file_path: str = ""
    original_content: str = ""
    new_content: str = ""
    diff: Optional[str] = None

    # Semantic information
    edit_type: EditType = EditType.UNKNOWN
    primary_symbol: Optional[SymbolReference] = None
    affected_symbols: List[SymbolReference] = field(default_factory=list)
    related_symbols: List[SymbolReference] = field(default_factory=list)

    # Context
    conversation_context: Optional[ConversationContext] = None
    user_intent: Optional[str] = None  # Natural language description

    # Validation
    result: Optional[EditResult] = None
    confidence: float = 0.0  # LLM's confidence in the edit (0-1)

    # Execution context (if available)
    execution_trace_id: Optional[str] = None  # Links to ExecutionTrace

    # Metadata
    timestamp: datetime = field(default_factory=datetime.now)
    git_commit_hash: Optional[str] = None
    parent_edit_id: Optional[str] = None  # For edit chains/refinements

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for storage."""
        return {
            "id": self.id,
            "file_path": self.file_path,
            "original_content": self.original_content,
            "new_content": self.new_content,
            "diff": self.diff,
            "edit_type": self.edit_type.value,
            "primary_symbol": self.primary_symbol.to_dict() if self.primary_symbol else None,
            "affected_symbols": [s.to_dict() for s in self.affected_symbols],
            "related_symbols": [s.to_dict() for s in self.related_symbols],
            "conversation_context": self.conversation_context.to_dict() if self.conversation_context else None,
            "user_intent": self.user_intent,
            "result": self.result.to_dict() if self.result else None,
            "confidence": self.confidence,
            "execution_trace_id": self.execution_trace_id,
            "timestamp": self.timestamp.isoformat(),
            "git_commit_hash": self.git_commit_hash,
            "parent_edit_id": self.parent_edit_id,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Edit":
        """Create from dictionary."""
        data = data.copy()
        data["edit_type"] = EditType(data.get("edit_type", "unknown"))
        data["timestamp"] = datetime.fromisoformat(data["timestamp"])

        if data.get("primary_symbol"):
            data["primary_symbol"] = SymbolReference.from_dict(data["primary_symbol"])

        data["affected_symbols"] = [
            SymbolReference.from_dict(s) for s in data.get("affected_symbols", [])
        ]
        data["related_symbols"] = [
            SymbolReference.from_dict(s) for s in data.get("related_symbols", [])
        ]

        if data.get("conversation_context"):
            data["conversation_context"] = ConversationContext.from_dict(data["conversation_context"])

        if data.get("result"):
            data["result"] = EditResult.from_dict(data["result"])

        return cls(**data)

    def get_affected_symbol_names(self) -> List[str]:
        """Get names of all affected symbols."""
        names = []
        if self.primary_symbol:
            names.append(self.primary_symbol.name)
        names.extend([s.name for s in self.affected_symbols])
        return names

    def to_context_string(self) -> str:
        """
        Convert to a string suitable for LLM context.
        Used when providing historical edit information to the LLM.
        """
        parts = [
            f"Edit [{self.id[:8]}] on {self.file_path}",
            f"  Type: {self.edit_type.value}",
        ]

        if self.user_intent:
            parts.append(f"  Intent: {self.user_intent}")

        if self.primary_symbol:
            parts.append(f"  Primary symbol: {self.primary_symbol.name} ({self.primary_symbol.kind})")

        if self.affected_symbols:
            symbol_list = ", ".join([s.name for s in self.affected_symbols[:5]])
            if len(self.affected_symbols) > 5:
                symbol_list += f" (+{len(self.affected_symbols) - 5} more)"
            parts.append(f"  Affected: {symbol_list}")

        if self.result:
            status = "SUCCESS" if self.result.success else "FAILED"
            parts.append(f"  Result: {status}")
            if self.result.errors:
                parts.append(f"  Errors: {'; '.join(self.result.errors[:2])}")

        if self.conversation_context and self.conversation_context.intent_summary:
            parts.append(f"  Context: {self.conversation_context.intent_summary}")

        return "\n".join(parts)
