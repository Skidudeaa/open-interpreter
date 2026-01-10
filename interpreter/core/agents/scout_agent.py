"""\
ScoutAgent - LLM-powered codebase exploration agent.

Architecture (hybrid):
1) LLM analyzes the task and proposes targeted searches (optional)
2) One filesystem scan builds a bounded file index (fast, deterministic)
3) Regex/symbol/file searches run against the index (no repeated full-tree walks)
4) LLM synthesizes findings into actionable context (optional)

WHY: Pure pattern matching is fast but context-blind. Pure LLM is slow for filesystem work.
This gets most of the speed of grep with enough brains to not miss the obvious.
"""

from __future__ import annotations

import fnmatch
import json
import logging
import os
import re
import shutil
import subprocess
import threading
import time
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .base_agent import AgentResult, AgentRole, BaseAgent, create_result

logger = logging.getLogger(__name__)

# Import activity stream for visibility (optional).
try:
    from ...terminal_interface.components.activity_stream import emit_activity
except ImportError:  # pragma: no cover

    def emit_activity(*args, **kwargs):
        pass


_GLOB_CHARS = set("*?[")

# Ripgrep availability check (run once at import)
# WHY: rg is 10-100x faster than Python line-by-line grep. Falls back gracefully.
_RG_AVAILABLE: bool = shutil.which("rg") is not None


@dataclass(slots=True)
class SearchQuery:
    """A single search query derived from task analysis."""

    query_type: str  # 'grep', 'glob', 'symbol', 'semantic'
    pattern: str
    description: str = ""


@dataclass(slots=True)
class SearchAnalysis:
    """Analysis of a search task."""

    understanding: str
    search_queries: list[SearchQuery] = field(default_factory=list)
    file_patterns: list[str] = field(default_factory=list)
    keywords: list[str] = field(default_factory=list)
    symbols: list[str] = field(default_factory=list)
    semantic_query: str = ""


@dataclass(slots=True, frozen=True)
class SearchResult:
    """Single match within a file."""

    file_path: str
    line_number: int
    content: str
    match_type: str  # 'filename', 'symbol', 'pattern', 'keyword'


@dataclass(slots=True, frozen=True)
class FileEntry:
    """Indexed file entry for bounded scanning."""

    rel_path: str
    abs_path: str
    name: str
    ext: str
    size: int


@dataclass(slots=True)
class IndexCache:
    """Cached file index with TTL invalidation.

    WHY: Scanning 15k files takes 100-500ms. Caching with short TTL (30s)
    gives 10x speedup for rapid repeated queries while still catching changes.
    """

    entries: list[FileEntry]
    root_path: str
    created_at: float
    file_count: int

    def is_valid(self, root_path: str, ttl_seconds: float) -> bool:
        """Check if cache is still valid for the given root and TTL."""
        if self.root_path != root_path:
            return False
        if time.monotonic() - self.created_at > ttl_seconds:
            return False
        return True


class ScoutAgent(BaseAgent):
    """Agent for exploring and searching codebases."""

    role = AgentRole.SCOUT

    # Hard caps: don't let a "search" become a full backup/restore operation.
    DEFAULT_MAX_WALK_DEPTH = 6
    DEFAULT_MAX_FILES_SCANNED = 15_000
    DEFAULT_MAX_FILE_BYTES = 2_000_000  # 2MB; skips minified/vendor blobs by default.
    DEFAULT_MAX_MATCHES_PER_FILE = 25
    DEFAULT_INDEX_CACHE_TTL_S = 30.0  # Cache file index for 30 seconds.

    # Shared index cache across instances (same project root).
    # WHY: Repeated Scout calls in same session shouldn't rebuild identical index.
    _index_cache: IndexCache | None = None
    _cache_lock = threading.Lock()

    def __init__(
        self,
        interpreter,
        memory=None,
        root_path: str | None = None,
        plugins=None,
        name: str | None = None,
    ):
        super().__init__(
            interpreter=interpreter, memory=memory, plugins=plugins, name=name
        )

        # Normalize early. Relative root_path makes everything harder for no benefit.
        self.root_path = str(Path(root_path or os.getcwd()).expanduser().resolve())

        # Directories (exact name) to prune aggressively.
        self.ignore_dirnames: set[str] = {
            "__pycache__",
            ".git",
            ".hg",
            ".svn",
            "node_modules",
            ".venv",
            "venv",
            "env",
            ".tox",
            "dist",
            "build",
            ".mypy_cache",
            ".pytest_cache",
            ".ruff_cache",
            ".cache",
        }

        # Filename globs to ignore.
        self.ignore_globs: set[str] = {
            "*.pyc",
            "*.pyo",
            "*.so",
            "*.dylib",
            "*.dll",
            "*.exe",
            ".DS_Store",
            "Thumbs.db",
            "*.min.js.map",
            "*.map",
        }

        # Extensions we treat as "code" for symbol search.
        self.code_extensions: set[str] = {
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
            ".cs",
            ".swift",
            ".kt",
            ".sh",
            ".bash",
            ".zsh",
        }

        # "Text-ish" extensions worth grepping by default.
        self.text_extensions: set[str] = set(self.code_extensions) | {
            ".md",
            ".txt",
            ".rst",
            ".json",
            ".toml",
            ".yaml",
            ".yml",
            ".ini",
            ".cfg",
            ".conf",
            ".sql",
            ".proto",
            ".graphql",
            ".gql",
            ".env",
            ".example",
        }

        self.text_filenames: set[str] = {
            "Dockerfile",
            "Makefile",
            "CMakeLists.txt",
            "Pipfile",
            "Pipfile.lock",
            "poetry.lock",
            "requirements.txt",
            "pyproject.toml",
            "package.json",
            "package-lock.json",
            "yarn.lock",
            "pnpm-lock.yaml",
            "README",
            "README.md",
            "LICENSE",
        }

        # Enable/disable LLM-powered analysis/synthesis (tests can disable).
        self.use_llm_analysis = True

        # Traversal limits to prevent hanging on huge directories.
        self.max_walk_depth = self.DEFAULT_MAX_WALK_DEPTH
        self.max_files_scanned = self.DEFAULT_MAX_FILES_SCANNED
        self.max_file_bytes = self.DEFAULT_MAX_FILE_BYTES
        self.max_matches_per_file = self.DEFAULT_MAX_MATCHES_PER_FILE

        # Ripgrep integration: use rg when available, fall back to Python.
        # WHY: 10-100x faster for content search. Disabled in tests for determinism.
        self.use_ripgrep = _RG_AVAILABLE

        # Index caching: avoid rebuilding file index on repeated calls.
        # WHY: Scanning 15k files takes 100-500ms; cache gives 10x speedup.
        self.use_index_cache = True
        self.index_cache_ttl_s = self.DEFAULT_INDEX_CACHE_TTL_S

    # =========================================================================
    # Agent core
    # =========================================================================

    def get_system_message(self) -> str:
        return (
            "You are a Scout Agent specialized in exploring codebases.\n\n"
            "Your job is to understand what the user wants and find relevant files, symbols, and patterns.\n\n"
            "When analyzing a task:\n"
            "1. Determine intent\n"
            "2. Generate targeted file patterns, keywords, and symbols\n"
            "3. Consider related concepts and common naming\n\n"
            "When presenting findings:\n"
            "- Use file paths relative to project root\n"
            "- Include line numbers for specific code\n"
            "- Be concise but specific\n"
        )

    def execute(self, task: str, context: str | None = None) -> AgentResult:
        """Run analysis -> search -> synthesis."""
        self.log(f"Starting scout task: {task[:80]}{'...' if len(task) > 80 else ''}")
        emit_activity("think", "Analyzing search task", task[:40], agent="scout")

        start = time.perf_counter()
        files_found: list[str] = []
        symbols_found: list[str] = []
        search_results: list[SearchResult] = []
        content_sections: list[str] = []

        try:
            # Phase 1: task analysis (LLM optional).
            if self.use_llm_analysis:
                emit_activity(
                    "think", "Understanding what to search for", agent="scout"
                )
                analysis = self._analyze_task(task, context)
            else:
                analysis = self._fallback_analyze_task(task)

            content_sections.append(f"## Understanding\n{analysis.understanding}\n")
            if analysis.keywords:
                emit_activity(
                    "search",
                    f"Looking for: {', '.join(analysis.keywords[:3])}",
                    agent="scout",
                )

            # Phase 2: build a bounded file index once.
            file_index = self._build_file_index()

            # Merge analysis knobs from the different sources and cap them.
            file_patterns, symbols, keywords, grep_patterns = self._normalize_analysis(
                analysis
            )

            # File patterns first.
            for pat in file_patterns:
                files_found.extend(
                    self.find_files(pat, max_results=100, file_index=file_index)
                )

            # Symbol search (single scan for multiple symbols).
            if symbols:
                symbol_results = self.search_symbols(
                    symbols, symbol_type="any", max_results=80, file_index=file_index
                )
                search_results.extend(symbol_results)
                for r in symbol_results:
                    symbols_found.append(f"{r.file_path}:{r.line_number}")

            # Regex grep patterns (usually few; keep bounded).
            for pat in grep_patterns:
                search_results.extend(
                    self.search_content(pat, max_results=60, file_index=file_index)
                )

            # Keyword grep (single scan across keywords).
            if keywords:
                search_results.extend(
                    self.search_keywords(
                        keywords, max_results=80, file_index=file_index
                    )
                )

            # Deduplicate results (same file/line/content).
            search_results = self._dedupe_results(search_results)

            # Pull files from search results.
            for r in search_results:
                files_found.append(r.file_path)

            files_found = self._dedupe_strings(files_found)
            symbols_found = self._dedupe_strings(symbols_found)

            if files_found:
                emit_activity(
                    "read",
                    f"Found {len(files_found)} relevant file(s)",
                    files_found[0]
                    if len(files_found) == 1
                    else f"{files_found[0]} +{len(files_found) - 1} more",
                    agent="scout",
                )

            # Phase 3: structured output only (no LLM synthesis here).
            # WHY: Scout returns structured findings to the orchestrator.
            # The orchestrator decides when to synthesize for the user (EXPLORE)
            # vs passing raw context to downstream agents (EDIT/FULL).
            # TRADEOFF: Faster Scout in EDIT/FULL workflows; single responsibility.

            # Semantic memory enrichment (doesn't require LLM; keep separate section).
            memory_lines = self._enrich_results_with_memory(
                task, analysis, files_found, symbols_found
            )

            elapsed_ms = (time.perf_counter() - start) * 1000.0

            # Store raw findings and metadata for orchestrator to use
            result = create_result(
                role=self.role,
                success=True,
                content="",  # Orchestrator will synthesize if needed
                files_found=files_found,
                symbols_found=symbols_found,
                context_for_next=self._format_context(
                    files_found, symbols_found, search_results
                ),
                metadata={
                    "task": task,
                    "analysis": asdict(analysis) if analysis else {},
                    "search_results": [
                        {
                            "file": r.file_path,
                            "line": r.line_number,
                            "content": r.content[:200],
                        }
                        for r in search_results[:50]  # Cap for memory
                    ],
                    "memory_context": memory_lines,
                    "elapsed_ms": elapsed_ms,
                },
            )
            self._last_result = result
            return result

        except Exception as e:
            logger.exception("Scout error")
            return create_result(
                role=self.role, success=False, content=f"Scout error: {e}"
            )

    # =========================================================================
    # Analysis (LLM + fallback)
    # =========================================================================

    def _analyze_task(self, task: str, context: str | None = None) -> SearchAnalysis:
        """Use the LLM to propose file patterns, symbols, and keywords."""
        prompt = f"""Analyze this codebase exploration task and generate search parameters.

Task: {task}

{f"Context: {context}" if context else ""}

Return a JSON object with:
{{
  "understanding": "Brief explanation of what the user wants to find",
  "file_patterns": ["*.py", "auth*.js"],
  "keywords": ["authenticate", "login"],
  "symbols": ["UserAuth", "login_handler"],
  "search_queries": [
    {{"type": "grep", "pattern": "def login", "description": "Find login functions"}},
    {{"type": "glob", "pattern": "*auth*.py", "description": "Auth-related files"}},
    {{"type": "symbol", "pattern": "AuthMiddleware", "description": "Middleware symbol"}}
  ],
  "semantic_query": "Natural language description for semantic search"
}}

Return ONLY valid JSON. No markdown. No commentary."""

        messages = [{"role": "user", "type": "message", "content": prompt}]

        try:
            response = self.run_interpreter(messages, self.get_system_message())
            return self._parse_analysis_response(response, original_task=task)
        except Exception as e:
            self.log(f"LLM analysis failed: {e}. Falling back.")
            return self._fallback_analyze_task(task)

    def _parse_analysis_response(
        self, response: str, original_task: str = ""
    ) -> SearchAnalysis:
        """Parse a JSON-ish response into SearchAnalysis."""
        raw = (response or "").strip()
        if not raw:
            return self._fallback_analyze_task(original_task)

        # Strip fenced blocks if present.
        if "```" in raw:
            # Try json fenced first.
            if "```json" in raw:
                raw = raw.split("```json", 1)[1]
                raw = raw.split("```", 1)[0]
            else:
                raw = raw.split("```", 1)[1]
                raw = raw.split("```", 1)[0]
            raw = raw.strip()

        # If there's extra chatter, salvage the first JSON object.
        if not raw.startswith("{"):
            first = raw.find("{")
            last = raw.rfind("}")
            if first != -1 and last != -1 and last > first:
                raw = raw[first : last + 1]

        try:
            data = json.loads(raw)
        except Exception as e:
            self.log(f"Failed to parse LLM JSON: {e}")
            return self._fallback_analyze_task(original_task)

        def _as_list(x) -> list[str]:
            if x is None:
                return []
            if isinstance(x, list):
                return [str(i) for i in x if i is not None]
            return [str(x)]

        queries: list[SearchQuery] = []
        for q in data.get("search_queries", []) or []:
            if not isinstance(q, dict):
                continue
            qt = str(q.get("type", "")).strip().lower() or "grep"
            pat = str(q.get("pattern", "")).strip()
            if not pat:
                continue
            queries.append(
                SearchQuery(
                    query_type=qt,
                    pattern=pat,
                    description=str(q.get("description", "")),
                )
            )

        analysis = SearchAnalysis(
            understanding=str(data.get("understanding", "Exploring codebase")).strip()
            or "Exploring codebase",
            search_queries=queries,
            file_patterns=_as_list(data.get("file_patterns")),
            keywords=_as_list(data.get("keywords")),
            symbols=_as_list(data.get("symbols")),
            semantic_query=str(data.get("semantic_query", "") or "").strip(),
        )
        return self._cap_analysis(analysis)

    def _fallback_analyze_task(self, task: str) -> SearchAnalysis:
        """Cheap, deterministic extraction when the LLM is disabled/unavailable."""
        keywords: list[str] = []
        patterns: list[str] = []
        symbols: list[str] = []
        search_queries: list[SearchQuery] = []

        # Quoted strings are usually "exactly this".
        quoted = re.findall(r"[\"']([^\"']+)[\"']", task)
        for q in quoted:
            q = q.strip()
            if not q:
                continue
            if any(ch in q for ch in _GLOB_CHARS) or (
                q.startswith(".") and len(q) <= 10
            ):
                patterns.append(q)
                search_queries.append(SearchQuery("glob", q, f"Files matching {q}"))
            elif q[0].isupper() or "_" in q:
                symbols.append(q)
                search_queries.append(SearchQuery("symbol", q, f"Symbol {q}"))
            else:
                keywords.append(q)
                search_queries.append(SearchQuery("grep", q, f"Pattern {q}"))

        # Inline globs and likely class/function names.
        for word in task.split():
            w = word.strip().strip(",;()[]{}")
            if not w:
                continue
            if any(ch in w for ch in _GLOB_CHARS) and w not in patterns:
                patterns.append(w)
            if w[0].isupper() and "_" not in w and len(w) > 2 and w not in symbols:
                symbols.append(w)

        keywords.extend(
            [kw for kw in self._extract_keywords_from_task(task) if kw not in keywords]
        )
        if not patterns:
            patterns = ["*.py"]

        understanding = "Fallback search"
        if patterns:
            understanding += f" patterns={patterns[:3]}"
        if symbols:
            understanding += f" symbols={symbols[:3]}"
        if keywords:
            understanding += f" keywords={keywords[:3]}"

        return self._cap_analysis(
            SearchAnalysis(
                understanding=understanding,
                keywords=keywords,
                file_patterns=patterns,
                symbols=symbols,
                search_queries=search_queries,
            )
        )

    def _cap_analysis(self, analysis: SearchAnalysis) -> SearchAnalysis:
        """Bound LLM output so it can't DOS the filesystem."""
        analysis.file_patterns = self._dedupe_strings(analysis.file_patterns)[:10]
        analysis.keywords = self._dedupe_strings([k for k in analysis.keywords if k])[
            :10
        ]
        analysis.symbols = self._dedupe_strings([s for s in analysis.symbols if s])[:10]

        capped_queries: list[SearchQuery] = []
        seen = set()
        for q in analysis.search_queries:
            qt = (q.query_type or "").strip().lower()
            pat = (q.pattern or "").strip()
            if not pat:
                continue
            key = (qt, pat)
            if key in seen:
                continue
            seen.add(key)
            capped_queries.append(SearchQuery(qt, pat, q.description or ""))
            if len(capped_queries) >= 12:
                break
        analysis.search_queries = capped_queries
        return analysis

    def _normalize_analysis(
        self, analysis: SearchAnalysis
    ) -> tuple[list[str], list[str], list[str], list[str]]:
        """Merge analysis fields + search_queries into bounded buckets."""
        file_patterns = list(analysis.file_patterns)
        symbols = list(analysis.symbols)
        keywords = list(analysis.keywords)
        grep_patterns: list[str] = []

        for q in analysis.search_queries:
            qt = (q.query_type or "").strip().lower()
            pat = (q.pattern or "").strip()
            if not pat:
                continue
            if qt in {"glob", "file", "filename"}:
                file_patterns.append(pat)
            elif qt in {"symbol", "identifier"}:
                symbols.append(pat)
            elif qt in {"grep", "regex", "search"}:
                grep_patterns.append(pat)
            elif qt in {"semantic"}:
                # No-op here. Semantic handled in memory enrichment.
                pass
            else:
                # Unknown query type: treat as grep.
                grep_patterns.append(pat)

        return (
            self._dedupe_strings(file_patterns)[:12],
            self._dedupe_strings(symbols)[:10],
            self._dedupe_strings(keywords)[:10],
            self._dedupe_strings(grep_patterns)[:5],
        )

    # =========================================================================
    # Synthesis
    # =========================================================================

    def _synthesize_findings(
        self,
        task: str,
        analysis: SearchAnalysis,
        files_found: list[str],
        search_results: list[SearchResult],
    ) -> str:
        """Ask the LLM to summarize the findings."""
        files_summary = "\n".join(f"  - {f}" for f in files_found[:30])
        results_text = self._format_results_for_llm(
            search_results, max_files=12, max_lines_per_file=4
        )

        prompt = f"""Synthesize these search findings to answer the user's question.

Original Task: {task}

Understanding: {analysis.understanding}

Files Found ({len(files_found)}):
{files_summary}

Code Matches ({len(search_results)}):
{results_text}

Provide a concise summary that:
1) Answers the user's question directly
2) Highlights the most important files/code
3) Explains how pieces fit together
4) Notes gaps / next places to look

Be specific: reference file paths and line numbers."""

        messages = [{"role": "user", "type": "message", "content": prompt}]

        try:
            return self.run_interpreter(messages, self.get_system_message())
        except Exception as e:
            self.log(f"Synthesis failed: {e}")
            return self._format_raw_findings(files_found, search_results)

    def _format_results_for_llm(
        self,
        results: list[SearchResult],
        max_files: int = 10,
        max_lines_per_file: int = 3,
    ) -> str:
        """Group results by file to keep the prompt readable."""
        by_file: dict[str, list[SearchResult]] = {}
        for r in results:
            by_file.setdefault(r.file_path, []).append(r)

        # Stable ordering: files by first appearance.
        ordered_files = list(by_file.keys())[:max_files]
        lines: list[str] = []
        for fp in ordered_files:
            lines.append(f"- {fp}")
            for r in by_file[fp][:max_lines_per_file]:
                snippet = (r.content or "").strip().replace("\t", " ")
                if len(snippet) > 140:
                    snippet = snippet[:137] + "..."
                lines.append(f"    {r.line_number}: {snippet}")
        return "\n".join(lines)

    def _format_raw_findings(
        self, files_found: list[str], search_results: list[SearchResult]
    ) -> str:
        """Deterministic fallback formatting when LLM synthesis is disabled."""
        parts: list[str] = []
        if files_found:
            parts.append(f"## Files Found ({len(files_found)})")
            for f in files_found[:30]:
                parts.append(f"  - {f}")
            if len(files_found) > 30:
                parts.append(f"  - ... and {len(files_found) - 30} more")

        if search_results:
            parts.append(f"\n## Code Matches ({len(search_results)})")
            for r in search_results[:40]:
                snippet = (r.content or "").strip()
                if len(snippet) > 120:
                    snippet = snippet[:117] + "..."
                parts.append(f"  {r.file_path}:{r.line_number} - {snippet}")

        return "\n".join(parts) if parts else "No results found"

    # =========================================================================
    # File index + traversal
    # =========================================================================

    def _build_file_index(self) -> list[FileEntry]:
        """Scan the project tree once and return a bounded list of files.

        Uses caching with TTL to avoid repeated scans on rapid queries.
        """
        # Check cache first
        if self.use_index_cache:
            with ScoutAgent._cache_lock:
                cache = ScoutAgent._index_cache
                if cache is not None and cache.is_valid(
                    self.root_path, self.index_cache_ttl_s
                ):
                    age_s = time.monotonic() - cache.created_at
                    self.log(
                        f"Using cached index ({cache.file_count} files, age={age_s:.1f}s)"
                    )
                    emit_activity(
                        "search",
                        f"Using cached index ({cache.file_count} files)",
                        f"age={age_s:.0f}s",
                        agent="scout",
                    )
                    return list(cache.entries)  # Return copy to avoid mutation

        emit_activity(
            "search", "Scanning filesystem", "building file index", agent="scout"
        )
        root = Path(self.root_path)
        if not root.exists():
            return []

        entries: list[FileEntry] = []
        files_scanned = 0

        # Use an explicit stack so we can enforce max depth and avoid os.walk quirks.
        stack: list[tuple[Path, int]] = [(root, 0)]
        while stack:
            dir_path, depth = stack.pop()
            try:
                with os.scandir(dir_path) as it:
                    for de in it:
                        name = de.name

                        try:
                            is_dir = de.is_dir(follow_symlinks=False)
                        except OSError:
                            continue

                        if is_dir:
                            if self._should_ignore_dir(name):
                                continue
                            if depth < self.max_walk_depth:
                                stack.append((Path(de.path), depth + 1))
                            continue

                        try:
                            is_file = de.is_file(follow_symlinks=False)
                        except OSError:
                            continue
                        if not is_file:
                            continue

                        if self._should_ignore_file(name):
                            continue

                        files_scanned += 1
                        if files_scanned > self.max_files_scanned:
                            self.log(
                                f"Hit max files scanned limit ({self.max_files_scanned})"
                            )
                            stack.clear()
                            break

                        try:
                            st = de.stat(follow_symlinks=False)
                            size = int(getattr(st, "st_size", 0) or 0)
                        except OSError:
                            size = 0

                        rel_path = os.path.relpath(de.path, self.root_path)
                        rel_path = rel_path.replace("\\", "/")
                        ext = Path(name).suffix.lower()
                        entries.append(
                            FileEntry(
                                rel_path=rel_path,
                                abs_path=str(Path(de.path).resolve()),
                                name=name,
                                ext=ext,
                                size=size,
                            )
                        )
            except (PermissionError, FileNotFoundError, NotADirectoryError):
                continue

        # Deterministic ordering helps downstream agents and tests.
        entries.sort(key=lambda e: e.rel_path)

        # Update cache
        if self.use_index_cache:
            with ScoutAgent._cache_lock:
                ScoutAgent._index_cache = IndexCache(
                    entries=entries,
                    root_path=self.root_path,
                    created_at=time.monotonic(),
                    file_count=len(entries),
                )

        return entries

    @classmethod
    def invalidate_cache(cls) -> None:
        """Force cache invalidation (call after file system changes).

        WHY: External tools (git, editors) may modify files. Call this when you
        know the filesystem has changed to ensure fresh index on next search.
        """
        with cls._cache_lock:
            cls._index_cache = None

    def clear_cache(self) -> None:
        """Instance method to clear cache (convenience wrapper)."""
        ScoutAgent.invalidate_cache()

    def _should_ignore_dir(self, dirname: str) -> bool:
        if dirname in self.ignore_dirnames:
            return True
        if dirname.startswith(".") and dirname not in {".", ".."}:
            # Most hidden dirs are noise. Keep explicit allow via patterns if needed.
            return dirname not in {".github"}
        return False

    def _should_ignore_file(self, filename: str) -> bool:
        if filename in {".", ".."}:
            return True
        for pat in self.ignore_globs:
            if fnmatch.fnmatch(filename, pat):
                return True
        return False

    def _match_glob(self, pattern: str, entry: FileEntry) -> bool:
        """Match glob patterns against basename and path."""
        pat = pattern.strip()
        if not pat:
            return False

        # Extension shorthand: ".py" means "any python file".
        if (
            pat.startswith(".")
            and ("/" not in pat)
            and not any(ch in pat for ch in _GLOB_CHARS)
        ):
            return entry.ext == pat.lower()

        # If the glob includes path separators, match against rel_path.
        if "/" in pat or "\\" in pat:
            return fnmatch.fnmatch(entry.rel_path, pat.replace("\\", "/"))

        # Otherwise match basename, but also allow matching the full rel_path
        # so patterns like "*auth*" can match directory components too.
        return fnmatch.fnmatch(entry.name, pat) or fnmatch.fnmatch(
            entry.rel_path, f"*{pat}*"
        )

    def _match_file_pattern(self, file_pattern: str, entry: FileEntry) -> bool:
        """Match an optional file glob against a FileEntry."""
        fp = (file_pattern or "*").strip() or "*"
        if fp == "*":
            return True
        if "/" in fp or "\\" in fp:
            return fnmatch.fnmatch(entry.rel_path, fp.replace("\\", "/"))
        return fnmatch.fnmatch(entry.name, fp)

    def _is_text_candidate(self, entry: FileEntry) -> bool:
        if entry.name in self.text_filenames:
            return True
        if entry.ext in self.text_extensions:
            return True
        # Extensionless files can still be text (e.g., "LICENSE").
        if not entry.ext and entry.size and entry.size < 512_000:
            return entry.name.isupper() or entry.name.lower() in {"readme", "license"}
        return False

    # =========================================================================
    # Ripgrep integration (fast path)
    # =========================================================================

    def _rg_search(
        self,
        pattern: str,
        file_pattern: str = "*",
        max_results: int = 50,
        case_insensitive: bool = True,
    ) -> list[SearchResult]:
        """Run ripgrep and parse JSON output.

        WHY: rg is 10-100x faster than Python line-by-line grep.
        TRADEOFF: Requires rg installed; falls back to Python if unavailable.
        """
        args = [
            "rg",
            "--json",
            "--no-heading",
            "--max-count",
            str(self.max_matches_per_file),
        ]
        if case_insensitive:
            args.append("-i")

        # File type filtering via glob
        if file_pattern and file_pattern != "*":
            args.extend(["--glob", file_pattern])

        # Ignore patterns (match self.ignore_dirnames)
        for dirname in self.ignore_dirnames:
            args.extend(["--glob", f"!{dirname}/**"])

        # Ignore hidden dirs (except .github) like _should_ignore_dir
        args.extend(["--glob", "!.*/**"])
        args.extend(["--glob", ".github/**"])  # Re-include .github

        args.append("--")  # End of options
        args.append(pattern)
        args.append(self.root_path)

        try:
            proc = subprocess.run(
                args,
                capture_output=True,
                text=True,
                timeout=30,
                cwd=self.root_path,
            )
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as e:
            self.log(f"ripgrep failed: {e}, falling back to Python")
            return []

        results: list[SearchResult] = []
        for line in proc.stdout.splitlines():
            if len(results) >= max_results:
                break
            try:
                obj = json.loads(line)
                if obj.get("type") != "match":
                    continue
                data = obj.get("data", {})
                path_obj = data.get("path", {})
                lines_obj = data.get("lines", {})

                abs_path = path_obj.get("text", "")
                if not abs_path:
                    continue

                rel_path = os.path.relpath(abs_path, self.root_path).replace("\\", "/")
                line_number = data.get("line_number", 0)
                content = lines_obj.get("text", "").rstrip("\n")

                results.append(
                    SearchResult(
                        file_path=rel_path,
                        line_number=line_number,
                        content=content,
                        match_type="pattern",
                    )
                )
            except (json.JSONDecodeError, KeyError, TypeError):
                continue

        return results

    # =========================================================================
    # Public search API
    # =========================================================================

    def find_files(
        self,
        pattern: str,
        max_results: int = 100,
        file_index: list[FileEntry] | None = None,
    ) -> list[str]:
        """Find files matching a glob or substring."""
        idx = file_index if file_index is not None else self._build_file_index()
        pat = (pattern or "").strip()
        if not pat:
            return []

        is_glob = any(ch in pat for ch in _GLOB_CHARS) or (
            pat.startswith(".") and len(pat) <= 10
        )
        pat_lower = pat.lower()

        out: list[str] = []
        for e in idx:
            if is_glob:
                if not self._match_glob(pat, e):
                    continue
            else:
                # Substring match against both basename and rel_path.
                if (
                    pat_lower not in e.name.lower()
                    and pat_lower not in e.rel_path.lower()
                ):
                    continue
            out.append(e.rel_path)
            if len(out) >= max_results:
                break
        return out

    def search_content(
        self,
        pattern: str,
        file_pattern: str = "*",
        max_results: int = 50,
        file_index: list[FileEntry] | None = None,
    ) -> list[SearchResult]:
        """Search for a regex (or literal) in file contents.

        Uses ripgrep when available for 10-100x speedup; falls back to Python.
        """
        pat = (pattern or "").strip()
        if not pat:
            return []

        # Fast path: use ripgrep when available
        if self.use_ripgrep and _RG_AVAILABLE:
            emit_activity("search", "Using ripgrep", pat[:30], agent="scout")
            results = self._rg_search(pat, file_pattern, max_results)
            if results:  # rg succeeded
                return results
            # Fall through to Python if rg returned empty (might be rg error)

        # Fallback: Python implementation
        emit_activity("search", "Python grep", pat[:30], agent="scout")
        idx = file_index if file_index is not None else self._build_file_index()

        try:
            regex = re.compile(pat, re.IGNORECASE)
        except re.error:
            regex = re.compile(re.escape(pat), re.IGNORECASE)

        results: list[SearchResult] = []
        for e in idx:
            if not self._match_file_pattern(file_pattern, e):
                continue
            if self._should_ignore_file(e.name):
                continue

            # Default behavior: only grep text-like files.
            if (
                file_pattern == "*" or not file_pattern
            ) and not self._is_text_candidate(e):
                continue

            if self.max_file_bytes and e.size and e.size > self.max_file_bytes:
                continue

            matches_in_file = 0
            try:
                with open(e.abs_path, encoding="utf-8", errors="ignore") as f:
                    for line_num, line in enumerate(f, 1):
                        if regex.search(line):
                            results.append(
                                SearchResult(
                                    file_path=e.rel_path,
                                    line_number=line_num,
                                    content=line.rstrip("\n"),
                                    match_type="pattern",
                                )
                            )
                            matches_in_file += 1
                            if matches_in_file >= self.max_matches_per_file:
                                break
                            if len(results) >= max_results:
                                return results
            except OSError:
                continue
        return results

    def search_keywords(
        self,
        keywords: list[str],
        file_pattern: str = "*",
        max_results: int = 50,
        file_index: list[FileEntry] | None = None,
    ) -> list[SearchResult]:
        """Search for any of the keywords in a single filesystem pass.

        Uses ripgrep when available for 10-100x speedup; falls back to Python.
        """
        kws = [k.strip().lower() for k in keywords if isinstance(k, str) and k.strip()]
        kws = self._dedupe_strings(kws)[:10]
        if not kws:
            return []

        # Fast path: use ripgrep with alternation pattern
        if self.use_ripgrep and _RG_AVAILABLE:
            emit_activity(
                "search", "Using ripgrep (keywords)", ", ".join(kws[:3]), agent="scout"
            )
            # Combine keywords into alternation: "foo|bar|baz"
            rg_pattern = "|".join(re.escape(k) for k in kws)
            results = self._rg_search(rg_pattern, file_pattern, max_results)
            # Update match_type to "keyword" for consistency
            results = [
                SearchResult(
                    file_path=r.file_path,
                    line_number=r.line_number,
                    content=r.content,
                    match_type="keyword",
                )
                for r in results
            ]
            if results:
                return results
            # Fall through to Python if rg returned empty

        # Fallback: Python implementation
        emit_activity(
            "search", "Python keyword search", ", ".join(kws[:3]), agent="scout"
        )
        idx = file_index if file_index is not None else self._build_file_index()

        results: list[SearchResult] = []
        for e in idx:
            if not self._match_file_pattern(file_pattern, e):
                continue
            if self._should_ignore_file(e.name):
                continue
            if (
                file_pattern == "*" or not file_pattern
            ) and not self._is_text_candidate(e):
                continue
            if self.max_file_bytes and e.size and e.size > self.max_file_bytes:
                continue

            matches_in_file = 0
            try:
                with open(e.abs_path, encoding="utf-8", errors="ignore") as f:
                    for line_num, line in enumerate(f, 1):
                        ll = line.lower()
                        if any(k in ll for k in kws):
                            results.append(
                                SearchResult(
                                    file_path=e.rel_path,
                                    line_number=line_num,
                                    content=line.rstrip("\n"),
                                    match_type="keyword",
                                )
                            )
                            matches_in_file += 1
                            if matches_in_file >= self.max_matches_per_file:
                                break
                            if len(results) >= max_results:
                                return results
            except OSError:
                continue

        return results

    def search_symbol(
        self,
        name: str,
        symbol_type: str = "any",
        max_results: int = 50,
        file_index: list[FileEntry] | None = None,
    ) -> list[SearchResult]:
        """Search for a single symbol in common language constructs."""
        return self.search_symbols(
            [name],
            symbol_type=symbol_type,
            max_results=max_results,
            file_index=file_index,
        )

    def search_symbols(
        self,
        names: list[str],
        symbol_type: str = "any",
        max_results: int = 50,
        file_index: list[FileEntry] | None = None,
    ) -> list[SearchResult]:
        """Search for multiple symbols in one pass."""
        idx = file_index if file_index is not None else self._build_file_index()
        clean = [n.strip() for n in names if isinstance(n, str) and n.strip()]
        clean = self._dedupe_strings(clean)[:10]
        if not clean:
            return []

        sym = symbol_type.strip().lower() if symbol_type else "any"
        alt = "|".join(re.escape(n) for n in clean)

        patterns: list[str] = []
        if sym in {"function", "any"}:
            # Python
            patterns.append(rf"^\s*(?:async\s+)?def\s+(?:{alt})\s*\(")
            # JS/TS
            patterns.append(
                rf"^\s*(?:export\s+)?(?:async\s+)?function\s+(?:{alt})\s*\("
            )
            patterns.append(
                rf"^\s*(?:export\s+)?(?:const|let|var)\s+(?:{alt})\s*=\s*(?:async\s+)?(?:function\s*)?\("
            )
            patterns.append(
                rf"^\s*(?:export\s+)?(?:const|let|var)\s+(?:{alt})\s*=\s*(?:async\s+)?(?:\([^)]*\)|[A-Za-z_]\w*)\s*=>"
            )
            # Go
            patterns.append(rf"^\s*func\s+(?:\([^)]*\)\s*)?(?:{alt})\s*\(")
            # Rust
            patterns.append(rf"^\s*(?:pub\s+)?(?:async\s+)?fn\s+(?:{alt})\s*\(")

        if sym in {"class", "any"}:
            patterns.append(rf"^\s*(?:export\s+)?class\s+(?:{alt})\b")
            # Java/C#/C++ style.
            patterns.append(
                rf"^\s*(?:public|private|protected|internal)?\s*(?:partial\s+)?class\s+(?:{alt})\b"
            )

        if sym == "any":
            # Fallback "mentioned anywhere".
            patterns.append(rf"\b(?:{alt})\b")

        regex = re.compile("|".join(f"(?:{p})" for p in patterns))
        results: list[SearchResult] = []
        for e in idx:
            if e.ext and e.ext not in self.code_extensions:
                continue
            if self.max_file_bytes and e.size and e.size > self.max_file_bytes:
                continue

            matches_in_file = 0
            try:
                with open(e.abs_path, encoding="utf-8", errors="ignore") as f:
                    for line_num, line in enumerate(f, 1):
                        if regex.search(line):
                            results.append(
                                SearchResult(
                                    file_path=e.rel_path,
                                    line_number=line_num,
                                    content=line.rstrip("\n"),
                                    match_type="symbol" if sym == "any" else sym,
                                )
                            )
                            matches_in_file += 1
                            if matches_in_file >= self.max_matches_per_file:
                                break
                            if len(results) >= max_results:
                                return results
            except OSError:
                continue
        return results

    def get_directory_structure(
        self, max_depth: int = 3, max_files_per_dir: int = 10
    ) -> str:
        """Return a tree representation of the directory structure."""
        root = Path(self.root_path)
        if not root.exists():
            return f"{self.root_path}/ (missing)"

        lines: list[str] = [root.name + "/"]

        def _walk(path: Path, prefix: str, depth: int) -> None:
            if depth > max_depth:
                return

            try:
                entries = sorted(
                    os.scandir(path),
                    key=lambda d: (not d.is_dir(follow_symlinks=False), d.name.lower()),
                )
            except (PermissionError, FileNotFoundError):
                return

            dirs = [
                e
                for e in entries
                if e.is_dir(follow_symlinks=False)
                and not self._should_ignore_dir(e.name)
            ]
            files = [
                e
                for e in entries
                if e.is_file(follow_symlinks=False)
                and not self._should_ignore_file(e.name)
            ]

            for i, d in enumerate(dirs):
                is_last = (i == len(dirs) - 1) and not files
                connector = "└── " if is_last else "├── "
                lines.append(f"{prefix}{connector}{d.name}/")
                _walk(Path(d.path), prefix + ("    " if is_last else "│   "), depth + 1)

            shown = files[:max_files_per_dir]
            for i, f in enumerate(shown):
                is_last = i == len(shown) - 1
                connector = "└── " if is_last else "├── "
                lines.append(f"{prefix}{connector}{f.name}")
            if len(files) > max_files_per_dir:
                lines.append(
                    f"{prefix}    ... and {len(files) - max_files_per_dir} more files"
                )

        _walk(root, "", 0)
        return "\n".join(lines)

    def read_file_summary(self, file_path: str, max_lines: int = 60) -> str:
        """Read and summarize a file (head only)."""
        fp = (file_path or "").strip()
        if not fp:
            return "(no file)"

        full_path = Path(self.root_path) / fp
        try:
            text_lines: list[str] = []
            with open(full_path, encoding="utf-8", errors="ignore") as f:
                for i, line in enumerate(f, 1):
                    text_lines.append(line.rstrip("\n"))
                    if i >= max_lines:
                        break

            # Try to get total line count cheaply only if file is small.
            total_lines = None
            try:
                if full_path.stat().st_size <= 512_000:
                    with open(full_path, encoding="utf-8", errors="ignore") as f:
                        total_lines = sum(1 for _ in f)
            except OSError:
                total_lines = None

            header = f"# {fp}" + (
                f" ({total_lines} lines)" if total_lines is not None else ""
            )
            body = "\n".join(text_lines)
            return f"{header}\n```\n{body}\n```"

        except Exception as e:
            return f"Error reading {fp}: {e}"

    # =========================================================================
    # Semantic memory integration
    # =========================================================================

    def _has_memory(self) -> bool:
        mem = self.memory
        return mem is not None

    def _enrich_results_with_memory(
        self,
        task: str,
        analysis: SearchAnalysis,
        files_found: list[str],
        symbols_found: list[str],
    ) -> list[str]:
        """Pull a small amount of edit-history context from memory."""
        mem = self.memory
        if mem is None:
            return []

        out: list[str] = []

        # 1) By files.
        for fp in files_found[:3]:
            out.extend(self._query_file_history(fp, limit=3))

        # 2) By symbol refs (file:line).
        for symref in symbols_found[:3]:
            if ":" in symref:
                fp = symref.split(":", 1)[0]
                out.extend(self._query_file_history(fp, limit=2))

        # 3) By semantic query / keywords.
        intent = (analysis.semantic_query or "").strip() or " ".join(
            self._extract_keywords_from_task(task)[:2]
        )
        if intent:
            out.extend(self._query_intent_history(intent, limit=3))

        return out

    def _query_file_history(self, file_path: str, limit: int = 5) -> list[str]:
        mem = self.memory
        if mem is None or not hasattr(mem, "query_by_file"):
            return []
        try:
            edits = mem.query_by_file(file_path, limit=limit)
            if not edits:
                return []
            history = [f"\n### Edit History for '{file_path}' ({len(edits)} found)"]
            for edit in edits:
                intent = (
                    edit.conversation_context.intent_summary
                    if getattr(edit, "conversation_context", None)
                    else "unknown"
                )
                primary = (
                    edit.primary_symbol.name
                    if getattr(edit, "primary_symbol", None)
                    else "unknown"
                )
                etype = getattr(getattr(edit, "edit_type", None), "value", "edit")
                history.append(f"  - [{etype}] {primary} - {str(intent)[:70]}")
            return history
        except Exception as e:
            logger.debug(f"Memory query failed for file '{file_path}': {e}")
            return []

    def _query_intent_history(self, keywords: str, limit: int = 5) -> list[str]:
        mem = self.memory
        if mem is None or not hasattr(mem, "query_by_intent"):
            return []
        try:
            edits = mem.query_by_intent(keywords, limit=limit)
            if not edits:
                return []
            history = [f"\n### Past Work Related to '{keywords}' ({len(edits)} found)"]
            for edit in edits:
                intent = (
                    edit.conversation_context.intent_summary
                    if getattr(edit, "conversation_context", None)
                    else "unknown"
                )
                etype = getattr(getattr(edit, "edit_type", None), "value", "edit")
                history.append(f"  - [{etype}] {edit.file_path} - {str(intent)[:70]}")
            return history
        except Exception as e:
            logger.debug(f"Memory query failed for intent '{keywords}': {e}")
            return []

    def _extract_keywords_from_task(self, task: str) -> list[str]:
        """Extract a small set of meaningful keywords from a task."""
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

        words = re.findall(r"\b[a-zA-Z_][a-zA-Z0-9_]*\b", (task or "").lower())
        kws = [w for w in words if w not in stopwords and len(w) > 2]
        return kws[:8]

    # =========================================================================
    # Formatting helpers
    # =========================================================================

    def _format_context(
        self, files: list[str], symbols: list[str], results: list[SearchResult]
    ) -> str:
        """Compact context for downstream agents (Surgeon/Architect)."""
        parts: list[str] = ["## Scout Results"]

        if files:
            parts.append(f"\n### Files Found ({len(files)})")
            for f in files[:20]:
                parts.append(f"- {f}")
            if len(files) > 20:
                parts.append(f"- ... and {len(files) - 20} more")

        if symbols:
            parts.append(f"\n### Symbols Found ({len(symbols)})")
            for s in symbols[:20]:
                parts.append(f"- {s}")
            if len(symbols) > 20:
                parts.append(f"- ... and {len(symbols) - 20} more")

        if results:
            parts.append(f"\n### Top Matches ({min(len(results), 25)} shown)")
            for r in results[:25]:
                snippet = (r.content or "").strip()
                if len(snippet) > 160:
                    snippet = snippet[:157] + "..."
                parts.append(f"- {r.file_path}:{r.line_number} {snippet}")

        return "\n".join(parts)

    @staticmethod
    def _dedupe_strings(items: Iterable[str]) -> list[str]:
        seen: set[str] = set()
        out: list[str] = []
        for s in items:
            if not s:
                continue
            if s in seen:
                continue
            seen.add(s)
            out.append(s)
        return out

    @staticmethod
    def _dedupe_results(results: list[SearchResult]) -> list[SearchResult]:
        seen: set[tuple[str, int, str]] = set()
        out: list[SearchResult] = []
        for r in results:
            key = (r.file_path, r.line_number, r.content)
            if key in seen:
                continue
            seen.add(key)
            out.append(r)
        return out
