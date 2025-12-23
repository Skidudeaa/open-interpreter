"""
Source abstraction for research agent.

Provides unified interface for different data sources:
- Web search results
- Local files
- Documentation
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ...core import OpenInterpreter


@dataclass
class SourceResult:
    """
    Result from a single source.

    Used to normalize results from different source types
    (web, files, docs) into a common format.
    """

    source_type: str  # "web", "file", "doc"
    url: str  # URL or file path
    title: str
    content: str
    snippet: str = ""  # Short preview
    relevance_score: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)
    fetch_time_ms: float = 0.0

    def to_citation(self) -> str:
        """Format as markdown citation."""
        return f"[{self.title}]({self.url})"

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "source_type": self.source_type,
            "url": self.url,
            "title": self.title,
            "snippet": self.snippet,
            "relevance_score": self.relevance_score,
            "metadata": self.metadata,
        }


@dataclass
class SearchResults:
    """Aggregated search results from multiple sources."""

    query: str
    results: list[SourceResult] = field(default_factory=list)
    sources_searched: list[str] = field(default_factory=list)
    total_time_ms: float = 0.0

    def top_n(self, n: int = 5) -> list[SourceResult]:
        """Get top N results by relevance score."""
        return sorted(
            self.results, key=lambda x: x.relevance_score, reverse=True
        )[:n]

    def by_type(self, source_type: str) -> list[SourceResult]:
        """Get results filtered by source type."""
        return [r for r in self.results if r.source_type == source_type]


class SourceProvider(ABC):
    """Abstract base for source providers."""

    @property
    @abstractmethod
    def source_type(self) -> str:
        """Type identifier for this source."""
        pass

    @abstractmethod
    async def search(self, query: str, max_results: int = 10) -> list[SourceResult]:
        """Search this source."""
        pass

    @abstractmethod
    async def fetch(self, url: str) -> str:
        """Fetch full content from URL/path."""
        pass


class WebSourceProvider(SourceProvider):
    """Web search and page fetching."""

    source_type = "web"

    def __init__(self, interpreter: "OpenInterpreter"):
        self.interpreter = interpreter
        self._cache: dict[str, tuple[str, float]] = {}
        self._cache_ttl = 3600

    async def search(self, query: str, max_results: int = 10) -> list[SourceResult]:
        """Search the web using the search module."""
        try:
            search = self.interpreter.computer.search
            raw_results = search.web(query, max_results=max_results)

            results = []
            for i, r in enumerate(raw_results):
                results.append(
                    SourceResult(
                        source_type="web",
                        url=r.url,
                        title=r.title,
                        content="",  # Fetched separately
                        snippet=r.snippet,
                        relevance_score=1.0 - (i * 0.05),  # Position-based
                    )
                )
            return results
        except Exception:
            return []

    async def fetch(self, url: str) -> str:
        """Fetch and extract content from web page."""
        try:
            documents = self.interpreter.computer.documents
            doc = documents.parse_webpage(url, mode="readability")
            return doc.text
        except Exception as e:
            return f"Error fetching {url}: {e}"


class FileSourceProvider(SourceProvider):
    """Local file search."""

    source_type = "file"

    def __init__(self, interpreter: "OpenInterpreter", root_path: str | None = None):
        import os

        self.interpreter = interpreter
        self.root_path = root_path or os.getcwd()

    async def search(self, query: str, max_results: int = 10) -> list[SourceResult]:
        """Search local files using existing file tools."""
        try:
            files = self.interpreter.computer.files
            raw_results = files.search(query)

            results = []
            for r in raw_results[:max_results]:
                import os

                results.append(
                    SourceResult(
                        source_type="file",
                        url=os.path.abspath(r.get("path", "")),
                        title=os.path.basename(r.get("path", "")),
                        content="",
                        snippet=r.get("content", "")[:200],
                        relevance_score=0.8,
                    )
                )
            return results
        except Exception:
            return []

    async def fetch(self, url: str) -> str:
        """Read file content."""
        try:
            with open(url, encoding="utf-8", errors="ignore") as f:
                return f.read()
        except Exception as e:
            return f"Error reading {url}: {e}"


class DocSourceProvider(SourceProvider):
    """Documentation search."""

    source_type = "doc"

    def __init__(self, interpreter: "OpenInterpreter"):
        self.interpreter = interpreter

    async def search(self, query: str, max_results: int = 10) -> list[SourceResult]:
        """Search documentation using docs module."""
        try:
            docs = self.interpreter.computer.docs
            raw_results = docs.search(query)

            results = []
            for r in raw_results[:max_results]:
                results.append(
                    SourceResult(
                        source_type="doc",
                        url=r.get("path", ""),
                        title=r.get("title", r.get("path", "")),
                        content=r.get("content", ""),
                        snippet=r.get("content", "")[:200],
                        relevance_score=r.get("score", 0.5),
                    )
                )
            return results
        except Exception:
            return []

    async def fetch(self, url: str) -> str:
        """Documentation content is already in search results."""
        return ""
