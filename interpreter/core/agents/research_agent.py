"""
ResearchAgent - Multi-source research and synthesis agent.

Conducts multi-step research:
1. Parse query and determine research strategy
2. Search multiple sources (web, files, docs)
3. Fetch content in parallel
4. Extract and clean content
5. Synthesize findings with citations

Capabilities:
- Web search and page fetching
- Local file search
- Documentation search
- Parallel content fetching
- LLM-powered synthesis with citations
"""

import asyncio
import logging
import os
import re
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from .base_agent import BaseAgent
from .research.sources import (
    DocSourceProvider,
    FileSourceProvider,
    SearchResults,
    SourceProvider,
    SourceResult,
    WebSourceProvider,
)
from .research.synthesizer import ExtractedContent, ResearchReport, ResearchSynthesizer
from .types import AgentResult, AgentRole, create_result

if TYPE_CHECKING:
    from ..core import OpenInterpreter
    from ..memory import SemanticEditGraph

# Module logger for research agent debugging
logger = logging.getLogger(__name__)


@dataclass
class ResearchConfig:
    """Configuration for research depth and sources."""

    depth: str = "standard"  # "quick", "standard", "deep"
    max_sources: int = 10
    sources: list[str] = field(default_factory=lambda: ["web"])
    parallel_fetches: int = 5
    include_citations: bool = True

    @classmethod
    def quick(cls) -> "ResearchConfig":
        """Quick research - fewer sources, faster results."""
        return cls(depth="quick", max_sources=5)

    @classmethod
    def standard(cls) -> "ResearchConfig":
        """Standard research - balanced depth."""
        return cls(depth="standard", max_sources=10)

    @classmethod
    def deep(cls) -> "ResearchConfig":
        """Deep research - comprehensive analysis."""
        return cls(depth="deep", max_sources=20, parallel_fetches=10)


class ResearchAgent(BaseAgent):
    """
    Agent for multi-source research and synthesis.

    Searches multiple sources (web, files, docs), fetches content
    in parallel, extracts key information, and synthesizes findings
    into a coherent report with citations.

    Example:
        agent = ResearchAgent(interpreter)
        result = agent.execute("Compare Python web frameworks")
        print(result.content)  # Markdown report

        # Quick research
        report = agent.quick_research("What is asyncio?")

        # Deep research
        report = agent.deep_research("AI trends 2024")
    """

    role = AgentRole.SCOUT  # Reuse SCOUT for now, can add RESEARCHER later

    def __init__(
        self,
        interpreter: "OpenInterpreter",
        memory: "SemanticEditGraph | None" = None,
        root_path: str | None = None,
        config: ResearchConfig | None = None,
        plugins: Any = None,
        name: str | None = None,
    ):
        """
        Initialize the research agent.

        Args:
            interpreter: OpenInterpreter instance
            memory: Optional shared memory
            root_path: Root path for file searches
            config: Research configuration
            plugins: Optional plugins
            name: Optional agent name
        """
        super().__init__(
            interpreter, memory, plugins=plugins, name=name or "researcher"
        )
        self.root_path = root_path or os.getcwd()
        self.config = config or ResearchConfig()

        # Lazy-loaded components
        self._providers: dict[str, SourceProvider] = {}
        self._synthesizer: ResearchSynthesizer | None = None

        # Research session state
        self._current_query: str = ""
        self._search_results: SearchResults | None = None
        self._extracted_contents: list[ExtractedContent] = []
        self._last_report: ResearchReport | None = None

    def _get_provider(self, source_type: str) -> SourceProvider | None:
        """Get or create a source provider (lazy-loaded)."""
        if source_type not in self._providers:
            if source_type == "web":
                self._providers["web"] = WebSourceProvider(self.interpreter)
            elif source_type == "files":
                self._providers["files"] = FileSourceProvider(
                    self.interpreter, self.root_path
                )
            elif source_type == "docs":
                self._providers["docs"] = DocSourceProvider(self.interpreter)
        return self._providers.get(source_type)

    @property
    def synthesizer(self) -> ResearchSynthesizer:
        """Get the synthesizer (lazy-loaded)."""
        if self._synthesizer is None:
            self._synthesizer = ResearchSynthesizer(self.interpreter)
        return self._synthesizer

    def get_system_message(self) -> str:
        """Get the agent's system message."""
        return """You are a Research Agent specialized in gathering and synthesizing information.

Your workflow:
1. Understand the research question
2. Search multiple sources (web, local files, documentation)
3. Extract relevant information from each source
4. Synthesize findings into a coherent answer with citations

When researching:
- Focus on authoritative and relevant sources
- Cross-reference information across sources
- Note any conflicting information
- Cite all sources using numbered references [1], [2], etc.

Always provide:
- A clear summary answering the question
- Key findings with source citations
- Recommendations for further research if needed"""

    def execute(self, task: str, context: str | None = None) -> AgentResult:
        """
        Execute a research task.

        Args:
            task: The research query/question
            context: Optional additional context

        Returns:
            AgentResult with research report
        """
        self.log(f"Starting research: {task[:50]}...")
        start_time = time.time()

        self._current_query = task

        # Run async research workflow
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

        try:
            report = loop.run_until_complete(self._research_async(task, context))
        except Exception as e:
            # Fallback to basic search if async fails
            logger.debug(f"Async research failed, falling back to sync: {e}")
            report = self._research_sync(task)

        execution_time = time.time() - start_time
        self._last_report = report

        return create_result(
            role=self.role,
            success=True,
            content=report.to_markdown(),
            files_found=[
                c.source.url
                for c in self._extracted_contents
                if c.source.source_type == "file"
            ],
            context_for_next=report.summary,
            metadata={
                "report": report.to_dict(),
                "depth": self.config.depth,
                "sources_count": report.total_sources,
            },
            execution_time=execution_time,
        )

    async def _research_async(
        self,
        query: str,
        context: str | None = None,
    ) -> ResearchReport:
        """Async research workflow."""

        # Step 1: Search all sources in parallel
        self.log("Searching sources...")
        search_tasks = []
        for source_type in self.config.sources:
            provider = self._get_provider(source_type)
            if provider:
                search_tasks.append(self._search_source(provider, query))

        if not search_tasks:
            return ResearchReport(
                query=query,
                summary="No search providers configured.",
                research_depth=self.config.depth,
            )

        search_results = await asyncio.gather(*search_tasks, return_exceptions=True)

        # Aggregate results
        all_results: list[SourceResult] = []
        for result in search_results:
            if isinstance(result, list):
                all_results.extend(result)

        if not all_results:
            return ResearchReport(
                query=query,
                summary="No results found for this query.",
                research_depth=self.config.depth,
            )

        # Rank and select top sources
        all_results.sort(key=lambda x: x.relevance_score, reverse=True)
        top_results = all_results[: self.config.max_sources]

        self._search_results = SearchResults(
            query=query,
            results=top_results,
            sources_searched=self.config.sources,
        )

        self.log(f"Found {len(top_results)} sources to analyze")

        # Step 2: Fetch content in parallel
        self.log("Fetching content...")
        fetch_tasks = []
        for result in top_results:
            provider = self._get_provider(result.source_type)
            if provider and not result.content:
                fetch_tasks.append(self._fetch_content(provider, result))

        if fetch_tasks:
            await asyncio.gather(*fetch_tasks, return_exceptions=True)

        # Step 3: Extract and clean content
        self.log("Extracting content...")
        self._extracted_contents = []
        for result in top_results:
            if result.content:
                extracted = self._extract_content(result)
                self._extracted_contents.append(extracted)

        if not self._extracted_contents:
            return ResearchReport(
                query=query,
                summary="Could not extract content from sources.",
                citations=list(top_results),
                total_sources=len(top_results),
                research_depth=self.config.depth,
            )

        # Relevance-rerank extracted sources before synthesis so the strongest
        # evidence leads both the synthesis prompt and the citation order.
        # Reorder-only; no-op to the current order when the reranker is off.
        self._extracted_contents = self._rerank_contents(
            query, self._extracted_contents
        )

        # Step 4: Synthesize findings
        self.log("Synthesizing findings...")
        report = self.synthesizer.synthesize(
            query=query,
            contents=self._extracted_contents,
            depth=self.config.depth,
        )

        return report

    def _rerank_contents(self, query, contents):
        """Reorder extracted sources by relevance to the query via the shared
        reranker. Reorder-only: returns the input order unchanged when the
        reranker is disabled/unkeyed, so synthesis behaves exactly as before."""
        if len(contents) <= 1 or not query:
            return contents
        reranker = getattr(self.interpreter, "reranker", None)
        if reranker is None or not reranker.is_available():
            return contents

        def _doc(c):
            return getattr(c, "clean_text", "") or getattr(c, "summary", "") or ""

        try:
            ranked = reranker.rerank_items(query, contents, key=_doc)
            return [item for item, _score in ranked] or contents
        except Exception:  # never let ranking break research
            return contents

    def _research_sync(self, query: str) -> ResearchReport:
        """Synchronous fallback for research."""
        # Simple synchronous search using web provider
        provider = self._get_provider("web")
        if not provider:
            return ResearchReport(
                query=query,
                summary="No search providers available.",
                research_depth=self.config.depth,
            )

        try:
            # Use computer.search directly
            search = self.interpreter.computer.search
            results = search.web(query, max_results=self.config.max_sources)

            # Convert to SourceResults
            source_results = []
            for r in results:
                source_results.append(
                    SourceResult(
                        source_type="web",
                        url=r.url,
                        title=r.title,
                        content=r.snippet,
                        snippet=r.snippet,
                        relevance_score=r.score or 0.5,
                    )
                )

            # Extract content
            self._extracted_contents = [
                ExtractedContent(source=r, clean_text=r.content or r.snippet)
                for r in source_results
            ]

            # Synthesize
            return self.synthesizer.synthesize(
                query=query,
                contents=self._extracted_contents,
                depth=self.config.depth,
            )
        except Exception as e:
            return ResearchReport(
                query=query,
                summary=f"Research failed: {e}",
                research_depth=self.config.depth,
            )

    async def _search_source(
        self,
        provider: SourceProvider,
        query: str,
    ) -> list[SourceResult]:
        """Search a single source."""
        try:
            return await provider.search(query, max_results=self.config.max_sources)
        except Exception as e:
            self.log(f"Error searching {provider.source_type}: {e}")
            return []

    async def _fetch_content(
        self,
        provider: SourceProvider,
        result: SourceResult,
    ) -> None:
        """Fetch content for a source result."""
        try:
            start = time.time()
            content = await provider.fetch(result.url)
            result.content = content
            result.fetch_time_ms = (time.time() - start) * 1000
        except Exception as e:
            result.content = f"Error fetching: {e}"

    def _extract_content(self, source: SourceResult) -> ExtractedContent:
        """Extract and clean content from a source."""
        text = source.content or source.snippet

        # Clean text
        text = re.sub(r"\n{3,}", "\n\n", text)
        text = re.sub(r" {2,}", " ", text)
        text = text.strip()

        # Truncate if too long
        max_length = 5000 if self.config.depth == "deep" else 2000
        if len(text) > max_length:
            text = text[:max_length] + "\n\n[Content truncated...]"

        return ExtractedContent(
            source=source,
            clean_text=text,
        )

    # Convenience methods

    def quick_research(self, query: str) -> str:
        """Quick research - fewer sources, faster results."""
        original_config = self.config
        self.config = ResearchConfig.quick()
        result = self.execute(query)
        self.config = original_config
        return result.content

    def deep_research(self, query: str) -> str:
        """Deep research - more sources, comprehensive analysis."""
        original_config = self.config
        self.config = ResearchConfig.deep()
        result = self.execute(query)
        self.config = original_config
        return result.content

    def get_last_report(self) -> ResearchReport | None:
        """Get the last generated research report."""
        return self._last_report

    def get_sources(self) -> list[SourceResult]:
        """Get sources from last search."""
        return self._search_results.results if self._search_results else []
