"""
ScoutAgent - LLM-powered codebase exploration agent.

ARCHITECTURE: Two-phase exploration with LLM intelligence:
1. LLM analyzes task → generates targeted search queries
2. Fast syntactic search (regex, glob, AST)
3. LLM synthesizes findings into actionable context

WHY: Pure pattern matching is fast but dumb. LLM understands intent,
can generate better search terms, and synthesize meaningful summaries.

TRADEOFF: Adds LLM latency (~1-3s) but produces dramatically better results.
The LLM calls are minimal and focused - just analysis and synthesis.

Capabilities:
- LLM-powered task analysis for smart search query generation
- Search for files by pattern (glob, fuzzy match)
- Find functions/classes by name (AST-aware)
- Search for code patterns (regex)
- Build file/directory summaries
- Query semantic memory for institutional knowledge
- Synthesize findings into coherent context for downstream agents
"""

import fnmatch
import json
import logging
import os
import re
from dataclasses import dataclass, field

from .base_agent import AgentResult, AgentRole, BaseAgent, create_result

logger = logging.getLogger(__name__)

# Import activity stream for visibility
try:
    from ...terminal_interface.components.activity_stream import emit_activity
except ImportError:

    def emit_activity(*args, **kwargs):
        pass


@dataclass
class SearchQuery:
    """A single search query derived from LLM analysis."""

    query_type: str  # 'grep', 'glob', 'symbol', 'semantic'
    pattern: str  # The search pattern
    description: str  # What we're looking for


@dataclass
class SearchAnalysis:
    """LLM analysis of a search task."""

    understanding: str  # What the LLM understands about the task
    search_queries: list[SearchQuery] = field(default_factory=list)
    file_patterns: list[str] = field(default_factory=list)
    keywords: list[str] = field(default_factory=list)
    symbols: list[str] = field(default_factory=list)  # Function/class names
    semantic_query: str = ""  # For semantic memory search


@dataclass
class SearchResult:
    """Result from a code search."""

    file_path: str
    line_number: int
    content: str
    match_type: str  # 'filename', 'function', 'class', 'pattern'


class ScoutAgent(BaseAgent):
    """
    LLM-powered agent for exploring and searching codebases.

    ARCHITECTURE: Hybrid approach combining LLM intelligence with fast
    syntactic search. LLM understands intent and generates smart queries,
    then fast file operations execute the searches.

    WHY: Pure regex/glob is fast but misses context. Pure LLM is slow
    for file traversal. This hybrid gets the best of both.
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

        # Enable/disable LLM-powered analysis (can be disabled for speed)
        self.use_llm_analysis = True

        # Traversal limits to prevent hanging on large directories
        self.max_walk_depth = 5  # Don't traverse deeper than 5 levels
        self.max_files_scanned = 10000  # Stop after scanning 10k files

    def get_system_message(self) -> str:
        return """You are a Scout Agent specialized in exploring codebases.

Your job is to deeply understand what the user is looking for and find
all relevant files, functions, and code patterns.

When analyzing a task:
1. Understand the INTENT - what does the user actually want to know?
2. Generate smart search queries - what terms, patterns, file names?
3. Consider related concepts - what else might be relevant?
4. Think about code structure - where would this functionality live?

When presenting findings:
- File paths relative to the project root
- Line numbers for specific code
- Brief but informative descriptions
- Highlight key discoveries that answer the user's question

You can collaborate with other agents if needed:
- Ask Architect about code structure
- Provide context to Surgeon for edits"""

    def execute(self, task: str, context: str | None = None) -> AgentResult:
        """
        Execute a scouting task with LLM-powered analysis.

        ARCHITECTURE: Three-phase exploration:
        1. LLM analyzes task → generates smart search queries
        2. Fast syntactic search (regex, glob, file traversal)
        3. LLM synthesizes findings into actionable context

        WHY: LLM understands intent ("review the auth pipeline" → search for
        auth, login, session, token). Pure regex would miss related concepts.

        TRADEOFF: 2 LLM calls add ~2-4s latency but produce dramatically better
        results. For simple queries, can fall back to fast regex-only mode.

        Args:
            task: The search/exploration task
            context: Optional context from other agents

        Returns:
            AgentResult with found files, symbols, and synthesized findings
        """
        self.log(f"Starting scout task: {task[:50]}...")
        emit_activity("think", "Analyzing search task", task[:40], agent="scout")

        files_found = []
        symbols_found = []
        all_search_results: list[SearchResult] = []
        content = []

        try:
            # Phase 1: LLM analyzes the task
            if self.use_llm_analysis:
                emit_activity(
                    "think", "Understanding what to search for", agent="scout"
                )
                analysis = self._analyze_task(task, context)
                self.log(f"LLM understanding: {analysis.understanding[:60]}...")
                content.append(f"## Understanding\n{analysis.understanding}\n")
                # Show what we're looking for
                if analysis.keywords:
                    emit_activity(
                        "search",
                        f"Looking for: {', '.join(analysis.keywords[:3])}",
                        agent="scout",
                    )
            else:
                # Fallback to old keyword-based analysis
                analysis = self._fallback_analyze_task(task)

            # Phase 2: Execute smart searches based on LLM analysis
            for query in analysis.search_queries:
                results = self._execute_search_query(query)
                all_search_results.extend(results)

            # Also search file patterns
            for pattern in analysis.file_patterns:
                files = self.find_files(pattern)
                files_found.extend(files)

            # Search for specific symbols
            for symbol in analysis.symbols:
                results = self.search_symbol(symbol)
                for r in results:
                    symbols_found.append(f"{r.file_path}:{r.line_number}")
                    all_search_results.append(r)

            # Grep for keywords
            for keyword in analysis.keywords:
                results = self.search_content(keyword)
                all_search_results.extend(results[:10])  # Limit per keyword

            # Collect files from search results
            for result in all_search_results:
                if result.file_path not in files_found:
                    files_found.append(result.file_path)

            # Deduplicate
            files_found = list(dict.fromkeys(files_found))  # Preserve order

            # Report what we found
            if files_found:
                emit_activity(
                    "read",
                    f"Found {len(files_found)} relevant file(s)",
                    files_found[0]
                    if len(files_found) == 1
                    else f"{files_found[0]} +{len(files_found)-1} more",
                    agent="scout",
                )

            # Phase 3: LLM synthesizes findings
            if self.use_llm_analysis and (files_found or all_search_results):
                synthesis = self._synthesize_findings(
                    task, analysis, files_found, all_search_results
                )
                content.append(f"## Findings\n{synthesis}")
            else:
                # Fallback: Just list results
                if files_found:
                    content.append(f"## Files Found ({len(files_found)})")
                    for f in files_found[:30]:
                        content.append(f"  - {f}")

                if all_search_results:
                    content.append(f"\n## Code Matches ({len(all_search_results)})")
                    for r in all_search_results[:20]:
                        content.append(
                            f"  {r.file_path}:{r.line_number} - {r.content.strip()[:60]}"
                        )

            # Enrich with semantic memory
            if self._has_memory() and (files_found or symbols_found):
                memory_context = self._enrich_results_with_memory(
                    task, files_found, symbols_found
                )
                if memory_context:
                    content.append("\n## Historical Context")
                    content.extend(memory_context)

        except Exception as e:
            logger.exception(f"Scout error: {e}")
            return create_result(
                role=self.role,
                success=False,
                content=f"Scout error: {str(e)}",
            )

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

    # =========================================================================
    # LLM-Powered Analysis Methods
    # =========================================================================

    def _analyze_task(self, task: str, context: str | None = None) -> SearchAnalysis:
        """
        Use LLM to understand the task and generate smart search queries.

        ARCHITECTURE: Single focused LLM call to understand user intent
        and generate targeted search parameters.

        WHY: LLM can understand "review the auth pipeline" means searching
        for authentication, login, session, token, middleware, etc.

        Args:
            task: The exploration task
            context: Optional context from other agents

        Returns:
            SearchAnalysis with queries, patterns, keywords, symbols
        """
        prompt = f"""Analyze this codebase exploration task and generate search parameters.

Task: {task}

{f"Context: {context}" if context else ""}

Return a JSON object with:
{{
    "understanding": "Brief explanation of what the user wants to find",
    "file_patterns": ["*.py", "auth*.js"],  // Glob patterns for relevant files
    "keywords": ["authenticate", "login"],  // Terms to grep for
    "symbols": ["UserAuth", "login_handler"],  // Function/class names to find
    "search_queries": [
        {{"type": "grep", "pattern": "def login", "description": "Find login functions"}},
        {{"type": "glob", "pattern": "*auth*.py", "description": "Auth-related files"}}
    ],
    "semantic_query": "Natural language description for semantic search"
}}

Think about:
- What concepts are related to this task?
- What file names might contain this functionality?
- What function/class names are likely?
- What code patterns would be relevant?

Return ONLY valid JSON, no markdown or explanation."""

        messages = [{"role": "user", "type": "message", "content": prompt}]

        try:
            response = self.run_interpreter(messages, self.get_system_message())
            return self._parse_analysis_response(response, original_task=task)
        except Exception as e:
            self.log(f"LLM analysis failed: {e}, falling back to keyword extraction")
            return self._fallback_analyze_task(task)

    def _parse_analysis_response(
        self, response: str, original_task: str = ""
    ) -> SearchAnalysis:
        """Parse LLM JSON response into SearchAnalysis."""
        try:
            # Try to extract JSON from response
            response = response.strip()

            # Empty response - fall back immediately
            if not response:
                return self._fallback_analyze_task(original_task)

            # Handle markdown code blocks
            if "```json" in response:
                response = response.split("```json")[1].split("```")[0]
            elif "```" in response:
                response = response.split("```")[1].split("```")[0]

            data = json.loads(response)

            # Build search queries
            queries = []
            for q in data.get("search_queries", []):
                queries.append(
                    SearchQuery(
                        query_type=q.get("type", "grep"),
                        pattern=q.get("pattern", ""),
                        description=q.get("description", ""),
                    )
                )

            return SearchAnalysis(
                understanding=data.get("understanding", "Exploring codebase"),
                search_queries=queries,
                file_patterns=data.get("file_patterns", []),
                keywords=data.get("keywords", []),
                symbols=data.get("symbols", []),
                semantic_query=data.get("semantic_query", ""),
            )

        except (json.JSONDecodeError, KeyError, TypeError) as e:
            self.log(f"Failed to parse LLM response: {e}")
            # Fall back to task-based analysis
            return self._fallback_analyze_task(original_task)

    def _fallback_analyze_task(self, task: str) -> SearchAnalysis:
        """
        Fallback analysis when LLM is disabled or fails.

        ARCHITECTURE: Parse common search phrases to extract patterns,
        symbols, and keywords directly from the task text.

        WHY: Tests use mock interpreters with no LLM. Production can
        fall back here when LLM fails or is disabled for speed.
        """
        task_lower = task.lower()
        keywords = []
        patterns = []
        symbols = []
        search_queries = []

        # Extract any quoted strings as specific searches
        quoted = re.findall(r'["\']([^"\']+)["\']', task)

        # Handle file pattern searches: "find files matching *.py"
        for q in quoted:
            if "*" in q or q.startswith("."):
                patterns.append(q)
                search_queries.append(SearchQuery("glob", q, f"Files matching {q}"))
            elif q[0].isupper() or "_" in q:
                # Looks like a symbol name
                symbols.append(q)
                search_queries.append(SearchQuery("symbol", q, f"Symbol {q}"))
            else:
                keywords.append(q)
                search_queries.append(SearchQuery("grep", q, f"Pattern {q}"))

        # Look for file patterns in unquoted text
        for word in task.split():
            if "*" in word and word not in patterns:
                patterns.append(word)
            if word[0].isupper() and "_" not in word and len(word) > 2:
                if word not in symbols:
                    symbols.append(word)  # Likely class name

        # Extract remaining keywords
        extracted_kw = self._extract_keywords_from_task(task)
        for kw in extracted_kw:
            if kw not in keywords:
                keywords.append(kw)

        # Common task patterns
        if "function" in task_lower or "method" in task_lower:
            for sym in symbols + quoted:
                if sym not in [q.pattern for q in search_queries]:
                    search_queries.append(SearchQuery("symbol", sym, f"Function {sym}"))

        if "class" in task_lower:
            for sym in symbols + quoted:
                if sym not in [q.pattern for q in search_queries]:
                    search_queries.append(SearchQuery("symbol", sym, f"Class {sym}"))

        # Default file patterns if none found
        if not patterns:
            patterns = ["*.py"]

        understanding = "Fallback search: "
        if patterns:
            understanding += f"patterns={patterns[:3]} "
        if symbols:
            understanding += f"symbols={symbols[:3]} "
        if keywords:
            understanding += f"keywords={keywords[:3]}"

        return SearchAnalysis(
            understanding=understanding.strip(),
            keywords=keywords,
            file_patterns=patterns,
            symbols=symbols,
            search_queries=search_queries,
        )

    def _execute_search_query(self, query: SearchQuery) -> list[SearchResult]:
        """Execute a single search query."""
        if query.query_type == "grep":
            return self.search_content(query.pattern)[:20]
        elif query.query_type == "glob":
            files = self.find_files(query.pattern)
            return [SearchResult(f, 0, "", "filename") for f in files[:20]]
        elif query.query_type == "symbol":
            return self.search_symbol(query.pattern)[:20]
        else:
            return self.search_content(query.pattern)[:20]

    def _synthesize_findings(
        self,
        task: str,
        analysis: SearchAnalysis,
        files_found: list[str],
        search_results: list[SearchResult],
    ) -> str:
        """
        Use LLM to synthesize search findings into coherent context.

        WHY: Raw search results are noisy. LLM can identify what's actually
        relevant and explain how the pieces fit together.
        """
        # Prepare a summary of findings for the LLM
        files_summary = "\n".join(f"  - {f}" for f in files_found[:30])

        results_summary = []
        for r in search_results[:30]:
            results_summary.append(
                f"  {r.file_path}:{r.line_number}: {r.content.strip()[:80]}"
            )
        results_text = "\n".join(results_summary)

        prompt = f"""Synthesize these search findings to answer the user's question.

Original Task: {task}

Understanding: {analysis.understanding}

Files Found ({len(files_found)}):
{files_summary}

Code Matches ({len(search_results)}):
{results_text}

Provide a clear, concise summary that:
1. Answers the user's question directly
2. Highlights the most important files/code
3. Explains how the pieces fit together
4. Notes any gaps or areas that need more investigation

Be specific - reference actual file paths and line numbers."""

        messages = [{"role": "user", "type": "message", "content": prompt}]

        try:
            return self.run_interpreter(messages, self.get_system_message())
        except Exception as e:
            self.log(f"Synthesis failed: {e}")
            # Return raw findings
            return f"Files: {len(files_found)}, Matches: {len(search_results)}"

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
        files_scanned = 0

        for root, dirs, files in os.walk(self.root_path):
            # Check depth limit
            depth = root[len(self.root_path) :].count(os.sep)
            if depth >= self.max_walk_depth:
                dirs[:] = []  # Don't descend further
                continue

            # Filter out ignored directories
            dirs[:] = [d for d in dirs if not self._should_ignore(d)]

            for filename in files:
                files_scanned += 1
                if files_scanned >= self.max_files_scanned:
                    self.log(f"Hit max files scanned limit ({self.max_files_scanned})")
                    return matches

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
        files_scanned = 0

        # Build regex pattern based on symbol type
        if symbol_type == "function":
            pattern = rf"^\s*(async\s+)?def\s+{re.escape(name)}\s*\("
        elif symbol_type == "class":
            pattern = rf"^\s*class\s+{re.escape(name)}\s*[:\(]"
        else:
            pattern = rf"\b{re.escape(name)}\b"

        regex = re.compile(pattern)

        for root, dirs, files in os.walk(self.root_path):
            # Check depth limit
            depth = root[len(self.root_path) :].count(os.sep)
            if depth >= self.max_walk_depth:
                dirs[:] = []
                continue

            dirs[:] = [d for d in dirs if not self._should_ignore(d)]

            for filename in files:
                files_scanned += 1
                if files_scanned >= self.max_files_scanned:
                    self.log(f"Hit max files scanned limit ({self.max_files_scanned})")
                    return results

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
        files_scanned = 0

        try:
            regex = re.compile(pattern, re.IGNORECASE)
        except re.error:
            # If not valid regex, treat as literal string
            regex = re.compile(re.escape(pattern), re.IGNORECASE)

        for root, dirs, files in os.walk(self.root_path):
            # Check depth limit
            depth = root[len(self.root_path) :].count(os.sep)
            if depth >= self.max_walk_depth:
                dirs[:] = []
                continue

            dirs[:] = [d for d in dirs if not self._should_ignore(d)]

            for filename in files:
                files_scanned += 1
                if files_scanned >= self.max_files_scanned:
                    self.log(f"Hit max files scanned limit ({self.max_files_scanned})")
                    return results

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
            # Parse "file:line" format to extract file path
            if ":" in symbol_ref:
                file_path = symbol_ref.split(":")[0]
                file_history = self._query_file_history(file_path, limit=2)
                memory_context.extend(file_history)

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
