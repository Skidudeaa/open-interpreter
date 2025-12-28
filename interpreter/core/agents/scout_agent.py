"""
ScoutAgent - Codebase exploration agent.

Optimized for fast, shallow reads to find relevant files and code.
Used as the first step in multi-agent workflows to gather context.

Capabilities:
- Search for files by pattern
- Find functions/classes by name
- Search for code patterns
- Build file/directory summaries
- Query semantic memory for institutional knowledge (past edits, context)
"""

import fnmatch
import logging
import os
import re
from dataclasses import dataclass

from .base_agent import AgentResult, AgentRole, BaseAgent, create_result

logger = logging.getLogger(__name__)


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
        root_path: str | None = None,
        plugins=None,
        name: str | None = None,
    ):
        super().__init__(interpreter, memory, plugins=plugins, name=name)
        self.root_path = root_path or os.getcwd()

        # File patterns to ignore
        self.ignore_patterns = {
            "__pycache__",
            ".git",
            ".svn",
            "node_modules",
            ".venv",
            "venv",
            "env",
            ".env",
            "*.pyc",
            "*.pyo",
            "*.so",
            "*.dylib",
            ".DS_Store",
            "Thumbs.db",
        }

        # File extensions to search
        self.code_extensions = {
            ".py",
            ".js",
            ".ts",
            ".jsx",
            ".tsx",
            ".java",
            ".go",
            ".rs",
            ".rb",
            ".php",
            ".c",
            ".cpp",
            ".h",
            ".hpp",
            ".sh",
            ".bash",
            ".zsh",
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

    def execute(self, task: str, context: str | None = None) -> AgentResult:
        """
        Execute a scouting task with semantic memory enrichment.

        ARCHITECTURE: Two-phase exploration - fast syntactic search followed
        by semantic memory enrichment for institutional context.

        WHY: Syntactic search (regex, glob, AST) is fast but lacks context.
        Semantic memory provides historical "why" behind code patterns.

        The task can be:
        - "find files matching X"
        - "search for function Y"
        - "explore directory Z"
        - General exploration request

        Args:
            task: The search/exploration task
            context: Optional context from other agents

        Returns:
            AgentResult with found files, symbols, and memory context
        """
        self.log(f"Starting scout task: {task[:50]}...")

        # Parse the task to determine search type
        task_lower = task.lower()

        files_found = []
        symbols_found = []
        symbol_names = []  # Track actual symbol names for memory queries
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
                symbol_names.append(name)  # Track for memory query
                results = self.search_symbol(name, symbol_type="function")
                for r in results[:20]:
                    symbols_found.append(f"{r.file_path}:{r.line_number}")
                    content.append(
                        f"  {r.file_path}:{r.line_number} - {r.content.strip()[:60]}"
                    )

                # Enrich with semantic memory for this symbol
                if self._has_memory() and name:
                    symbol_history = self._query_symbol_history(name, limit=3)
                    content.extend(symbol_history)

            elif "class" in task_lower:
                name = self._extract_identifier(task)
                symbol_names.append(name)  # Track for memory query
                results = self.search_symbol(name, symbol_type="class")
                for r in results[:20]:
                    symbols_found.append(f"{r.file_path}:{r.line_number}")
                    content.append(
                        f"  {r.file_path}:{r.line_number} - {r.content.strip()[:60]}"
                    )

                # Enrich with semantic memory for this symbol
                if self._has_memory() and name:
                    symbol_history = self._query_symbol_history(name, limit=3)
                    content.extend(symbol_history)

            elif "search" in task_lower or "grep" in task_lower:
                pattern = self._extract_pattern(task)
                results = self.search_content(pattern)
                for r in results[:20]:
                    files_found.append(r.file_path)
                    content.append(
                        f"  {r.file_path}:{r.line_number} - {r.content.strip()[:60]}"
                    )

            elif "structure" in task_lower or "explore" in task_lower:
                structure = self.get_directory_structure()
                content.append(structure)
                files_found = self.find_files("*")[:50]

            elif "history" in task_lower or "past" in task_lower or "why" in task_lower:
                # Explicit request for historical context - prioritize memory
                if self._has_memory():
                    keywords = self._extract_keywords_from_task(task)
                    if keywords:
                        intent_history = self._query_intent_history(
                            keywords[0], limit=10
                        )
                        content.extend(intent_history)

                    # Also check for file-specific history
                    pattern = self._extract_pattern(task)
                    if pattern and pattern != "*":
                        files_found = self.find_files(pattern)
                        for f in files_found[:5]:
                            knowledge = self._get_institutional_knowledge(f)
                            if knowledge:
                                content.append(f"\n{knowledge}")
                else:
                    content.append(
                        "Semantic memory not enabled. Enable with OI_ACTIVATE_ALL=true"
                    )

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

        # Phase 2: Enrich with semantic memory (if not already done inline)
        if self._has_memory() and (files_found or symbols_found):
            memory_context = self._enrich_results_with_memory(
                task, files_found, symbols_found
            )
            if memory_context:
                content.append("\n## Semantic Memory Context")
                content.extend(memory_context)

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

    def find_files(self, pattern: str, max_results: int = 100) -> list[str]:
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
                if (
                    fnmatch.fnmatch(filename, pattern)
                    or pattern.lower() in filename.lower()
                ):
                    rel_path = os.path.relpath(
                        os.path.join(root, filename), self.root_path
                    )
                    matches.append(rel_path)

                    if len(matches) >= max_results:
                        return matches

        return matches

    def search_symbol(
        self, name: str, symbol_type: str = "any", max_results: int = 50
    ) -> list[SearchResult]:
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
        if symbol_type == "function":
            pattern = rf"^\s*(async\s+)?def\s+{re.escape(name)}\s*\("
        elif symbol_type == "class":
            pattern = rf"^\s*class\s+{re.escape(name)}\s*[:\(]"
        else:
            pattern = rf"\b{re.escape(name)}\b"

        regex = re.compile(pattern)

        for root, dirs, files in os.walk(self.root_path):
            dirs[:] = [d for d in dirs if not self._should_ignore(d)]

            for filename in files:
                if not any(filename.endswith(ext) for ext in self.code_extensions):
                    continue

                filepath = os.path.join(root, filename)
                rel_path = os.path.relpath(filepath, self.root_path)

                try:
                    with open(filepath, encoding="utf-8", errors="ignore") as f:
                        for line_num, line in enumerate(f, 1):
                            if regex.search(line):
                                results.append(
                                    SearchResult(
                                        file_path=rel_path,
                                        line_number=line_num,
                                        content=line,
                                        match_type=symbol_type,
                                    )
                                )

                                if len(results) >= max_results:
                                    return results
                except Exception:
                    continue

        return results

    def search_content(
        self, pattern: str, file_pattern: str = "*", max_results: int = 50
    ) -> list[SearchResult]:
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
                    with open(filepath, encoding="utf-8", errors="ignore") as f:
                        for line_num, line in enumerate(f, 1):
                            if regex.search(line):
                                results.append(
                                    SearchResult(
                                        file_path=rel_path,
                                        line_number=line_num,
                                        content=line,
                                        match_type="pattern",
                                    )
                                )

                                if len(results) >= max_results:
                                    return results
                except Exception:
                    continue

        return results

    def get_directory_structure(
        self, max_depth: int = 3, max_files_per_dir: int = 10
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
                lines.append(
                    f"{prefix}    ... and {len(files) - max_files_per_dir} more files"
                )

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
            with open(full_path, encoding="utf-8", errors="ignore") as f:
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

    # =========================================================================
    # Semantic Memory Integration
    # =========================================================================

    def _has_memory(self) -> bool:
        """Check if semantic memory is available."""
        return self._memory is not None

    def _query_symbol_history(self, symbol_name: str, limit: int = 5) -> list[str]:
        """
        Query semantic memory for past edits affecting a symbol.

        ARCHITECTURE: Integrates semantic graph queries into exploration flow.
        WHY: Past edit context helps understand why code is structured this way.
        TRADEOFF: Slight overhead for memory queries vs richer context.

        Args:
            symbol_name: Name of the symbol to query
            limit: Maximum edits to return

        Returns:
            List of formatted edit history strings
        """
        if not self._has_memory():
            return []

        try:
            edits = self._memory.query_by_symbol(symbol_name, limit=limit)
            if not edits:
                return []

            history = [f"\n### Past Edits for '{symbol_name}' ({len(edits)} found)"]
            for edit in edits:
                # Format: [type] file:line - intent
                intent = (
                    edit.conversation_context.intent_summary
                    if edit.conversation_context
                    else "unknown"
                )
                history.append(
                    f"  - [{edit.edit_type.value}] {edit.file_path} - {intent[:60]}"
                )
            return history

        except Exception as e:
            logger.debug(f"Memory query failed for symbol '{symbol_name}': {e}")
            return []

    def _query_file_history(self, file_path: str, limit: int = 5) -> list[str]:
        """
        Query semantic memory for past edits to a file.

        Args:
            file_path: Path to the file
            limit: Maximum edits to return

        Returns:
            List of formatted edit history strings
        """
        if not self._has_memory():
            return []

        try:
            edits = self._memory.query_by_file(file_path, limit=limit)
            if not edits:
                return []

            history = [f"\n### Edit History for '{file_path}' ({len(edits)} found)"]
            for edit in edits:
                intent = (
                    edit.conversation_context.intent_summary
                    if edit.conversation_context
                    else "unknown"
                )
                primary = edit.primary_symbol.name if edit.primary_symbol else "unknown"
                history.append(
                    f"  - [{edit.edit_type.value}] {primary} - {intent[:50]}"
                )
            return history

        except Exception as e:
            logger.debug(f"Memory query failed for file '{file_path}': {e}")
            return []

    def _query_intent_history(self, keywords: str, limit: int = 5) -> list[str]:
        """
        Query semantic memory for past edits matching intent keywords.

        WHY: When user asks about a concept, past edits with similar intent
        provide valuable context about how this codebase handles that concept.

        Args:
            keywords: Keywords to search for in past intents
            limit: Maximum edits to return

        Returns:
            List of formatted edit history strings
        """
        if not self._has_memory():
            return []

        try:
            edits = self._memory.query_by_intent(keywords, limit=limit)
            if not edits:
                return []

            history = [f"\n### Past Work Related to '{keywords}' ({len(edits)} found)"]
            for edit in edits:
                intent = (
                    edit.conversation_context.intent_summary
                    if edit.conversation_context
                    else "unknown"
                )
                history.append(
                    f"  - [{edit.edit_type.value}] {edit.file_path} - {intent[:50]}"
                )
            return history

        except Exception as e:
            logger.debug(f"Memory query failed for intent '{keywords}': {e}")
            return []

    def _get_institutional_knowledge(self, file_path: str) -> str | None:
        """
        Get institutional knowledge for a file from semantic memory.

        This provides LLM-ready context about the file's edit history,
        including why changes were made and what symbols were affected.

        Args:
            file_path: Path to the file

        Returns:
            Formatted institutional knowledge string or None
        """
        if not self._has_memory():
            return None

        try:
            knowledge = self._memory.get_institutional_knowledge(
                file_path, max_edits=10
            )
            if "No edit history found" not in knowledge:
                return knowledge
            return None
        except Exception as e:
            logger.debug(
                f"Failed to get institutional knowledge for '{file_path}': {e}"
            )
            return None

    def _extract_keywords_from_task(self, task: str) -> list[str]:
        """
        Extract meaningful keywords from a task for intent search.

        Args:
            task: The exploration task

        Returns:
            List of keywords (excluding common words)
        """
        # Common words to filter out
        stopwords = {
            "find",
            "search",
            "for",
            "the",
            "a",
            "an",
            "in",
            "on",
            "at",
            "to",
            "is",
            "are",
            "was",
            "were",
            "be",
            "been",
            "being",
            "have",
            "has",
            "do",
            "does",
            "did",
            "will",
            "would",
            "could",
            "should",
            "may",
            "might",
            "must",
            "shall",
            "can",
            "need",
            "all",
            "any",
            "both",
            "each",
            "few",
            "more",
            "most",
            "other",
            "some",
            "such",
            "no",
            "not",
            "only",
            "same",
            "so",
            "than",
            "too",
            "very",
            "just",
            "where",
            "how",
            "what",
            "which",
            "who",
            "why",
            "when",
            "file",
            "files",
            "function",
            "method",
            "class",
            "code",
            "pattern",
        }

        words = re.findall(r"\b[a-zA-Z_][a-zA-Z0-9_]*\b", task.lower())
        keywords = [w for w in words if w not in stopwords and len(w) > 2]
        return keywords[:5]  # Limit to top 5

    def _enrich_results_with_memory(
        self,
        task: str,
        files_found: list[str],
        symbols_found: list[str],
    ) -> list[str]:
        """
        Enrich search results with semantic memory context.

        ARCHITECTURE: Post-search enrichment adds institutional knowledge
        without slowing down the initial fast search.

        WHY: Combining syntactic search with semantic memory provides
        both speed (direct file ops) and depth (historical context).

        Args:
            task: Original exploration task
            files_found: Files found by syntactic search
            symbols_found: Symbols found by syntactic search

        Returns:
            List of memory context strings to append to results
        """
        memory_context = []

        # 1. Query by symbols found
        for symbol_ref in symbols_found[:3]:  # Limit to first 3
            # Parse "file:line" format
            if ":" in symbol_ref:
                parts = symbol_ref.split(":")
                # Try to extract symbol name from file content
                pass  # Symbol name not directly available here

        # 2. Query by files found
        for file_path in files_found[:3]:  # Limit to first 3
            file_history = self._query_file_history(file_path, limit=3)
            memory_context.extend(file_history)

        # 3. Query by task keywords
        keywords = self._extract_keywords_from_task(task)
        if keywords:
            # Use first meaningful keyword for intent search
            intent_history = self._query_intent_history(keywords[0], limit=3)
            memory_context.extend(intent_history)

        return memory_context

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
        for keyword in ["matching", "pattern", "like", "for", "named"]:
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
        for keyword in ["function", "method", "class", "called", "named"]:
            if keyword in task.lower():
                idx = task.lower().find(keyword)
                remaining = task[idx + len(keyword) :].strip()
                # Get first word-like thing
                match = re.match(r"[\w_]+", remaining)
                if match:
                    return match.group(0)

        # Extract any identifier-like word
        words = re.findall(r"\b[A-Za-z_]\w*\b", task)
        if words:
            # Return longest word as it's likely the identifier
            return max(words, key=len)

        return ""

    def _format_context(
        self, files: list[str], symbols: list[str], content: list[str]
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
