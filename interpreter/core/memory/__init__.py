"""
Memory module for Open Interpreter.

Provides semantic memory capabilities including:
- SemanticEditGraph: Tracks edits with context, intent, and relationships
- EditRecord: Data structures for edit tracking
- SymbolExtractor: Extracts code symbols from source files
- ConversationLinker: Links edits to conversation context
"""

from .edit_record import (
    Edit,
    EditType,
    EditResult,
    ConversationContext,
    SymbolReference,
    TestResult,
)
from .semantic_graph import SemanticEditGraph
from .symbol_extractor import (
    PythonSymbolExtractor,
    DiffSymbolExtractor,
    extract_affected_symbols,
)
from .conversation_linker import (
    ConversationLinker,
    create_edit_from_file_change,
)

__all__ = [
    # Data structures
    "Edit",
    "EditType",
    "EditResult",
    "ConversationContext",
    "SymbolReference",
    "TestResult",
    # Core components
    "SemanticEditGraph",
    "PythonSymbolExtractor",
    "DiffSymbolExtractor",
    "ConversationLinker",
    # Convenience functions
    "extract_affected_symbols",
    "create_edit_from_file_change",
]
