"""
ScoutAgent - Codebase exploration agent.

Optimized for fast, shallow reads to find relevant files and code.
Used as the first step in multi-agent workflows to gather context.

Capabilities:
- Search for files by pattern
- Find functions/classes by name
- Search for code patterns
- Build file/directory summaries
"""

import os
import fnmatch
import re
from pathlib import Path
from typing import List, Optional, Set, Dict, Any
from dataclasses import dataclass

from .base_agent import BaseAgent, AgentRole, AgentResult, create_result


@dataclass
class SearchResult:
    """Result from a code search."""
    file_path: str
    line_number: int
    content: str
    match_type: str  # 'filename', 'function', 'class', 'pattern'


class ScoutAgent(BaseAgent):
    """
    Agent for exploring and searching codebases.

    Uses file system operations and pattern matching rather than
    LLM calls for most operations, making it fast and reliable.
    """

    role = AgentRole.SCOUT

    def __init__(
        self,
        interpreter,
        memory=None,
        root_path: Optional[str] = None,
    ):
        super().__init__(interpreter, memory)
        self.root_path = root_path or os.getcwd()

        # File patterns to ignore
        self.ignore_patterns = {
            '__pycache__', '.git', '.svn', 'node_modules',
            '.venv', 'venv', 'env', '.env',
            '*.pyc', '*.pyo', '*.so', '*.dylib',
            '.DS_Store', 'Thumbs.db',
        }

        # File extensions to search
        self.code_extensions = {
            '.py', '.js', '.ts', '.jsx', '.tsx',
            '.java', '.go', '.rs', '.rb', '.php',
            '.c', '.cpp', '.h', '.hpp',
            '.sh', '.bash', '.zsh',
        }

    def get_system_message(self) -> str:
        return """You are a Scout Agent specialized in exploring codebases.

Your job is to quickly find relevant files, functions, and code patterns.
You should be fast and thorough, providing clear summaries of what you find.

When searching:
1. Start with file/directory structure
2. Look for relevant filenames
3. Search for specific patterns or symbols
4. Summarize your findings clearly

Always provide:
- File paths relative to the project root
- Line numbers when relevant
- Brief descriptions of what each file/function does"""

    def execute(self, task: str, context: Optional[str] = None) -> AgentResult:
        """
        Execute a scouting task.

        The task can be:
        - "find files matching X"
        - "search for function Y"
        - "explore directory Z"
        - General exploration request

        Args:
            task: The search/exploration task
            context: Optional context from other agents

        Returns:
            AgentResult with found files and symbols
        """
        self.log(f"Starting scout task: {task[:50]}...")

        # Parse the task to determine search type
        task_lower = task.lower()

        files_found = []
        symbols_found = []
        content = []

        try:
            if "file" in task_lower or "find" in task_lower:
                # Extract pattern from task
                pattern = self._extract_pattern(task)
                files_found = self.find_files(pattern)
                content.append(f"Found {len(files_found)} files matching pattern")
                for f in files_found[:20]:
                    content.append(f"  - {f}")

            elif "function" in task_lower or "method" in task_lower:
                # Search for function definitions
                name = self._extract_identifier(task)
                results = self.search_symbol(name, symbol_type='function')
                for r in results[:20]:
                    symbols_found.append(f"{r.file_path}:{r.line_number}")
                    content.append(f"  {r.file_path}:{r.line_number} - {r.content.strip()[:60]}")

            elif "class" in task_lower:
                name = self._extract_identifier(task)
                results = self.search_symbol(name, symbol_type='class')
                for r in results[:20]:
                    symbols_found.append(f"{r.file_path}:{r.line_number}")
                    content.append(f"  {r.file_path}:{r.line_number} - {r.content.strip()[:60]}")

            elif "search" in task_lower or "grep" in task_lower:
                pattern = self._extract_pattern(task)
                results = self.search_content(pattern)
                for r in results[:20]:
                    files_found.append(r.file_path)
                    content.append(f"  {r.file_path}:{r.line_number} - {r.content.strip()[:60]}")

            elif "structure" in task_lower or "explore" in task_lower:
                structure = self.get_directory_structure()
                content.append(structure)
                files_found = self.find_files("*")[:50]

            else:
                # General exploration - use LLM
                messages = self.prepare_messages(task, context)
                response = self.run_interpreter(messages)
                content.append(response)

        except Exception as e:
            return create_result(
                role=self.role,
                success=False,
                content=f"Scout error: {str(e)}",
            )

        # Deduplicate
        files_found = list(set(files_found))

        result = create_result(
            role=self.role,
            success=True,
            content="\n".join(content) if content else "No results found",
            files_found=files_found,
            symbols_found=symbols_found,
            context_for_next=self._format_context(files_found, symbols_found, content),
        )

        self._last_result = result
        return result

    def find_files(
        self,
        pattern: str,
        max_results: int = 100
    ) -> List[str]:
        """
        Find files matching a pattern.

        Args:
            pattern: Glob pattern or filename substring
            max_results: Maximum number of results

        Returns:
            List of file paths
        """
        matches = []

        for root, dirs, files in os.walk(self.root_path):
            # Filter out ignored directories
            dirs[:] = [d for d in dirs if not self._should_ignore(d)]

            for filename in files:
                if self._should_ignore(filename):
                    continue

                # Check pattern match
                if fnmatch.fnmatch(filename, pattern) or pattern.lower() in filename.lower():
                    rel_path = os.path.relpath(os.path.join(root, filename), self.root_path)
                    matches.append(rel_path)

                    if len(matches) >= max_results:
                        return matches

        return matches

    def search_symbol(
        self,
        name: str,
        symbol_type: str = 'any',
        max_results: int = 50
    ) -> List[SearchResult]:
        """
        Search for a symbol (function, class, variable) in the codebase.

        Args:
            name: Symbol name to search for
            symbol_type: 'function', 'class', 'any'
            max_results: Maximum number of results

        Returns:
            List of SearchResult objects
        """
        results = []

        # Build regex pattern based on symbol type
        if symbol_type == 'function':
            pattern = rf'^\s*(async\s+)?def\s+{re.escape(name)}\s*\('
        elif symbol_type == 'class':
            pattern = rf'^\s*class\s+{re.escape(name)}\s*[:\(]'
        else:
            pattern = rf'\b{re.escape(name)}\b'

        regex = re.compile(pattern)

        for root, dirs, files in os.walk(self.root_path):
            dirs[:] = [d for d in dirs if not self._should_ignore(d)]

            for filename in files:
                if not any(filename.endswith(ext) for ext in self.code_extensions):
                    continue

                filepath = os.path.join(root, filename)
                rel_path = os.path.relpath(filepath, self.root_path)

                try:
                    with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
                        for line_num, line in enumerate(f, 1):
                            if regex.search(line):
                                results.append(SearchResult(
                                    file_path=rel_path,
                                    line_number=line_num,
                                    content=line,
                                    match_type=symbol_type,
                                ))

                                if len(results) >= max_results:
                                    return results
                except Exception:
                    continue

        return results

    def search_content(
        self,
        pattern: str,
        file_pattern: str = "*",
        max_results: int = 50
    ) -> List[SearchResult]:
        """
        Search for a pattern in file contents.

        Args:
            pattern: Regex or string pattern
            file_pattern: Glob pattern for files to search
            max_results: Maximum number of results

        Returns:
            List of SearchResult objects
        """
        results = []

        try:
            regex = re.compile(pattern, re.IGNORECASE)
        except re.error:
            # If not valid regex, treat as literal string
            regex = re.compile(re.escape(pattern), re.IGNORECASE)

        for root, dirs, files in os.walk(self.root_path):
            dirs[:] = [d for d in dirs if not self._should_ignore(d)]

            for filename in files:
                if not fnmatch.fnmatch(filename, file_pattern):
                    continue

                if self._should_ignore(filename):
                    continue

                filepath = os.path.join(root, filename)
                rel_path = os.path.relpath(filepath, self.root_path)

                try:
                    with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
                        for line_num, line in enumerate(f, 1):
                            if regex.search(line):
                                results.append(SearchResult(
                                    file_path=rel_path,
                                    line_number=line_num,
                                    content=line,
                                    match_type='pattern',
                                ))

                                if len(results) >= max_results:
                                    return results
                except Exception:
                    continue

        return results

    def get_directory_structure(
        self,
        max_depth: int = 3,
        max_files_per_dir: int = 10
    ) -> str:
        """
        Get a tree representation of the directory structure.

        Args:
            max_depth: Maximum depth to traverse
            max_files_per_dir: Maximum files to show per directory

        Returns:
            Tree structure as string
        """
        lines = []

        def _walk(path: str, prefix: str = "", depth: int = 0):
            if depth > max_depth:
                return

            try:
                entries = sorted(os.listdir(path))
            except PermissionError:
                return

            dirs = []
            files = []

            for entry in entries:
                if self._should_ignore(entry):
                    continue

                full_path = os.path.join(path, entry)
                if os.path.isdir(full_path):
                    dirs.append(entry)
                else:
                    files.append(entry)

            # Show directories first
            for i, d in enumerate(dirs):
                is_last = (i == len(dirs) - 1) and not files
                connector = "└── " if is_last else "├── "
                lines.append(f"{prefix}{connector}{d}/")

                new_prefix = prefix + ("    " if is_last else "│   ")
                _walk(os.path.join(path, d), new_prefix, depth + 1)

            # Show files
            shown_files = files[:max_files_per_dir]
            for i, f in enumerate(shown_files):
                is_last = i == len(shown_files) - 1
                connector = "└── " if is_last else "├── "
                lines.append(f"{prefix}{connector}{f}")

            if len(files) > max_files_per_dir:
                lines.append(f"{prefix}    ... and {len(files) - max_files_per_dir} more files")

        lines.append(os.path.basename(self.root_path) + "/")
        _walk(self.root_path)

        return "\n".join(lines)

    def read_file_summary(self, file_path: str, max_lines: int = 50) -> str:
        """
        Read and summarize a file.

        Args:
            file_path: Path to the file
            max_lines: Maximum lines to include

        Returns:
            File summary string
        """
        full_path = os.path.join(self.root_path, file_path)

        try:
            with open(full_path, 'r', encoding='utf-8', errors='ignore') as f:
                lines = f.readlines()

            total_lines = len(lines)
            shown_lines = lines[:max_lines]

            summary = [f"# {file_path} ({total_lines} lines)"]
            summary.append("```")
            summary.extend(line.rstrip() for line in shown_lines)

            if total_lines > max_lines:
                summary.append(f"... ({total_lines - max_lines} more lines)")

            summary.append("```")

            return "\n".join(summary)

        except Exception as e:
            return f"Error reading {file_path}: {e}"

    def _should_ignore(self, name: str) -> bool:
        """Check if a file/directory should be ignored."""
        for pattern in self.ignore_patterns:
            if fnmatch.fnmatch(name, pattern):
                return True
        return False

    def _extract_pattern(self, task: str) -> str:
        """Extract a search pattern from a task string."""
        # Look for quoted strings
        match = re.search(r'["\']([^"\']+)["\']', task)
        if match:
            return match.group(1)

        # Look for patterns after keywords
        for keyword in ['matching', 'pattern', 'like', 'for', 'named']:
            if keyword in task.lower():
                parts = task.lower().split(keyword)
                if len(parts) > 1:
                    pattern = parts[1].strip().split()[0]
                    return pattern

        # Default: use the last word
        words = task.split()
        return words[-1] if words else "*"

    def _extract_identifier(self, task: str) -> str:
        """Extract an identifier (function/class name) from a task string."""
        # Look for quoted strings
        match = re.search(r'["\']([^"\']+)["\']', task)
        if match:
            return match.group(1)

        # Look for identifiers after keywords
        for keyword in ['function', 'method', 'class', 'called', 'named']:
            if keyword in task.lower():
                idx = task.lower().find(keyword)
                remaining = task[idx + len(keyword):].strip()
                # Get first word-like thing
                match = re.match(r'[\w_]+', remaining)
                if match:
                    return match.group(0)

        # Extract any identifier-like word
        words = re.findall(r'\b[A-Za-z_]\w*\b', task)
        if words:
            # Return longest word as it's likely the identifier
            return max(words, key=len)

        return ""

    def _format_context(
        self,
        files: List[str],
        symbols: List[str],
        content: List[str]
    ) -> str:
        """Format results as context for the next agent."""
        parts = ["## Scout Results"]

        if files:
            parts.append(f"\n### Files Found ({len(files)})")
            for f in files[:15]:
                parts.append(f"- {f}")
            if len(files) > 15:
                parts.append(f"- ... and {len(files) - 15} more")

        if symbols:
            parts.append(f"\n### Symbols Found ({len(symbols)})")
            for s in symbols[:15]:
                parts.append(f"- {s}")
            if len(symbols) > 15:
                parts.append(f"- ... and {len(symbols) - 15} more")

        if content and not files and not symbols:
            parts.append("\n### Content")
            parts.extend(content[:20])

        return "\n".join(parts)
