"""
Lightweight Edit Validation for Open Interpreter.

Validates code edits before applying them using:
- Syntax checking (AST parsing for Python, node --check for JS, etc.)
- Type checking (optional, via mypy/pyright)
- Test discovery and targeted test runs
- Git-based rollback for failed edits

No Docker required - uses temp files and subprocess isolation.
"""

from .rollback import EditRollback
from .syntax_checker import SyntaxChecker
from .test_discovery import TestDiscovery
from .validator import EditValidator, ValidationResult

__all__ = [
    "EditValidator",
    "ValidationResult",
    "SyntaxChecker",
    "TestDiscovery",
    "EditRollback",
]
