"""
Symbol extractor for Python code.

Extracts function, class, method, and variable definitions from Python source code
using AST parsing. This enables the Semantic Edit Graph to track which symbols
are affected by each edit.

For other languages, tree-sitter can be used as an optional extension.
"""

import ast
import difflib
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Set, Tuple

from .edit_record import SymbolReference


class PythonSymbolExtractor:
    """
    Extracts code symbols from Python source code using AST.
    """

    def extract_symbols(self, source_code: str, file_path: str = "") -> List[SymbolReference]:
        """
        Extract all symbols from Python source code.

        Args:
            source_code: The Python source code
            file_path: Path to the file (for reference)

        Returns:
            List of SymbolReference objects
        """
        symbols = []

        try:
            tree = ast.parse(source_code)
        except SyntaxError:
            return symbols

        for node in ast.walk(tree):
            symbol = self._node_to_symbol(node, file_path, source_code)
            if symbol:
                symbols.append(symbol)

        return symbols

    def _node_to_symbol(
        self,
        node: ast.AST,
        file_path: str,
        source_code: str
    ) -> Optional[SymbolReference]:
        """Convert an AST node to a SymbolReference if applicable."""

        if isinstance(node, ast.FunctionDef):
            return SymbolReference(
                name=node.name,
                kind="function",
                file_path=file_path,
                line_start=node.lineno,
                line_end=node.end_lineno or node.lineno,
                signature=self._get_function_signature(node),
                docstring=ast.get_docstring(node),
            )

        elif isinstance(node, ast.AsyncFunctionDef):
            return SymbolReference(
                name=node.name,
                kind="async_function",
                file_path=file_path,
                line_start=node.lineno,
                line_end=node.end_lineno or node.lineno,
                signature=self._get_function_signature(node),
                docstring=ast.get_docstring(node),
            )

        elif isinstance(node, ast.ClassDef):
            return SymbolReference(
                name=node.name,
                kind="class",
                file_path=file_path,
                line_start=node.lineno,
                line_end=node.end_lineno or node.lineno,
                signature=self._get_class_signature(node),
                docstring=ast.get_docstring(node),
            )

        elif isinstance(node, ast.Assign):
            # Module-level variable assignments
            if hasattr(node, 'lineno'):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        return SymbolReference(
                            name=target.id,
                            kind="variable",
                            file_path=file_path,
                            line_start=node.lineno,
                            line_end=node.end_lineno or node.lineno,
                        )

        elif isinstance(node, ast.Import):
            # Import statements
            for alias in node.names:
                name = alias.asname if alias.asname else alias.name
                return SymbolReference(
                    name=name,
                    kind="import",
                    file_path=file_path,
                    line_start=node.lineno,
                    line_end=node.end_lineno or node.lineno,
                )

        elif isinstance(node, ast.ImportFrom):
            # From imports
            module = node.module or ""
            for alias in node.names:
                name = alias.asname if alias.asname else alias.name
                return SymbolReference(
                    name=f"{module}.{name}" if module else name,
                    kind="import",
                    file_path=file_path,
                    line_start=node.lineno,
                    line_end=node.end_lineno or node.lineno,
                )

        return None

    def _get_function_signature(self, node: ast.FunctionDef) -> str:
        """Extract function signature as a string."""
        args = []

        # Regular arguments
        for arg in node.args.args:
            arg_str = arg.arg
            if arg.annotation:
                try:
                    arg_str += f": {ast.unparse(arg.annotation)}"
                except Exception:
                    pass
            args.append(arg_str)

        # *args
        if node.args.vararg:
            args.append(f"*{node.args.vararg.arg}")

        # **kwargs
        if node.args.kwarg:
            args.append(f"**{node.args.kwarg.arg}")

        signature = f"def {node.name}({', '.join(args)})"

        # Return type annotation
        if node.returns:
            try:
                signature += f" -> {ast.unparse(node.returns)}"
            except Exception:
                pass

        return signature

    def _get_class_signature(self, node: ast.ClassDef) -> str:
        """Extract class signature as a string."""
        bases = []
        for base in node.bases:
            try:
                bases.append(ast.unparse(base))
            except Exception:
                bases.append("...")

        if bases:
            return f"class {node.name}({', '.join(bases)})"
        return f"class {node.name}"


class DiffSymbolExtractor:
    """
    Identifies which symbols are affected by a diff between two versions of code.
    """

    def __init__(self):
        self.python_extractor = PythonSymbolExtractor()

    def find_affected_symbols(
        self,
        original_code: str,
        new_code: str,
        file_path: str = ""
    ) -> Tuple[List[SymbolReference], List[SymbolReference], List[SymbolReference]]:
        """
        Find symbols affected by a code change.

        Args:
            original_code: The original source code
            new_code: The new source code
            file_path: Path to the file

        Returns:
            Tuple of (added_symbols, removed_symbols, modified_symbols)
        """
        # Extract symbols from both versions
        original_symbols = self.python_extractor.extract_symbols(original_code, file_path)
        new_symbols = self.python_extractor.extract_symbols(new_code, file_path)

        # Create lookup dictionaries
        original_by_name = {s.name: s for s in original_symbols}
        new_by_name = {s.name: s for s in new_symbols}

        original_names = set(original_by_name.keys())
        new_names = set(new_by_name.keys())

        # Find added, removed, and potentially modified
        added_names = new_names - original_names
        removed_names = original_names - new_names
        common_names = original_names & new_names

        added_symbols = [new_by_name[name] for name in added_names]
        removed_symbols = [original_by_name[name] for name in removed_names]

        # Check for modifications in common symbols
        modified_symbols = []
        for name in common_names:
            orig = original_by_name[name]
            new = new_by_name[name]

            # Check if signature or line range changed significantly
            if (orig.signature != new.signature or
                abs(orig.line_start - new.line_start) > 0 or
                abs((orig.line_end - orig.line_start) - (new.line_end - new.line_start)) > 0):
                modified_symbols.append(new)

        return added_symbols, removed_symbols, modified_symbols

    def find_symbols_in_diff_range(
        self,
        code: str,
        changed_lines: Set[int],
        file_path: str = ""
    ) -> List[SymbolReference]:
        """
        Find symbols that overlap with changed line numbers.

        Args:
            code: The source code
            changed_lines: Set of line numbers that were changed
            file_path: Path to the file

        Returns:
            List of symbols that overlap with changed lines
        """
        all_symbols = self.python_extractor.extract_symbols(code, file_path)

        affected = []
        for symbol in all_symbols:
            symbol_lines = set(range(symbol.line_start, symbol.line_end + 1))
            if symbol_lines & changed_lines:
                affected.append(symbol)

        return affected

    def get_changed_lines_from_diff(self, original: str, new: str) -> Set[int]:
        """
        Get the line numbers that changed between two versions.

        Args:
            original: Original source code
            new: New source code

        Returns:
            Set of line numbers in the new code that were added or modified
        """
        original_lines = original.splitlines(keepends=True)
        new_lines = new.splitlines(keepends=True)

        changed_lines = set()

        differ = difflib.unified_diff(original_lines, new_lines, lineterm='')

        current_line = 0
        for line in differ:
            if line.startswith('@@'):
                # Parse the line range
                # Format: @@ -start,count +start,count @@
                parts = line.split()
                if len(parts) >= 3:
                    new_range = parts[2]  # +start,count
                    if new_range.startswith('+'):
                        range_parts = new_range[1:].split(',')
                        current_line = int(range_parts[0])
            elif line.startswith('+') and not line.startswith('+++'):
                changed_lines.add(current_line)
                current_line += 1
            elif line.startswith(' '):
                current_line += 1
            # Lines starting with '-' are removed, don't increment

        return changed_lines


def extract_affected_symbols(
    original_code: str,
    new_code: str,
    file_path: str = ""
) -> Tuple[Optional[SymbolReference], List[SymbolReference]]:
    """
    Convenience function to extract affected symbols from a code change.

    Returns:
        Tuple of (primary_symbol, affected_symbols)
        primary_symbol is the most significant affected symbol (if any)
    """
    extractor = DiffSymbolExtractor()

    added, removed, modified = extractor.find_affected_symbols(
        original_code, new_code, file_path
    )

    # Combine all affected symbols
    all_affected = added + modified + removed

    if not all_affected:
        # Fall back to finding symbols in the diff range
        changed_lines = extractor.get_changed_lines_from_diff(original_code, new_code)
        all_affected = extractor.find_symbols_in_diff_range(new_code, changed_lines, file_path)

    if not all_affected:
        return None, []

    # Primary symbol is typically a function/class over a variable/import
    priority_order = ['class', 'function', 'async_function', 'method', 'variable', 'import']

    def priority(symbol: SymbolReference) -> int:
        try:
            return priority_order.index(symbol.kind)
        except ValueError:
            return len(priority_order)

    all_affected.sort(key=priority)

    primary = all_affected[0] if all_affected else None
    others = all_affected[1:] if len(all_affected) > 1 else []

    return primary, others
