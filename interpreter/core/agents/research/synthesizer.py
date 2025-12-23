"""
Research Synthesizer - Combine sources into coherent reports.

Uses LLM to synthesize information from multiple sources
into structured reports with citations.
"""

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ...core import OpenInterpreter

from .sources import SourceResult


@dataclass
class ExtractedContent:
    """Cleaned and structured content from a source."""

    source: SourceResult
    clean_text: str
    summary: str = ""
    key_points: list[str] = field(default_factory=list)
    word_count: int = 0

    def __post_init__(self):
        if not self.word_count and self.clean_text:
            self.word_count = len(self.clean_text.split())


@dataclass
class ResearchReport:
    """Final research output with citations."""

    query: str
    summary: str
    sections: list[dict[str, str]] = field(default_factory=list)
    citations: list[SourceResult] = field(default_factory=list)
    methodology: str = ""
    total_sources: int = 0
    research_depth: str = "standard"

    def to_markdown(self) -> str:
        """Format as markdown report."""
        lines = [
            f"# Research Report: {self.query}",
            "",
            "## Summary",
            self.summary,
            "",
        ]

        for section in self.sections:
            title = section.get("title", "Section")
            content = section.get("content", "")
            lines.append(f"### {title}")
            lines.append(content)
            lines.append("")

        if self.citations:
            lines.append("## Sources")
            for i, citation in enumerate(self.citations, 1):
                lines.append(f"{i}. [{citation.title}]({citation.url})")

        lines.extend(
            [
                "",
                "---",
                f"*Research depth: {self.research_depth}, "
                f"Sources analyzed: {self.total_sources}*",
            ]
        )

        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "query": self.query,
            "summary": self.summary,
            "sections": self.sections,
            "citations": [c.to_citation() for c in self.citations],
            "total_sources": self.total_sources,
            "research_depth": self.research_depth,
        }


class ResearchSynthesizer:
    """
    Synthesize research findings using LLM.

    Takes extracted content from multiple sources and
    combines them into a coherent report with proper citations.
    """

    def __init__(self, interpreter: "OpenInterpreter"):
        self.interpreter = interpreter

    def synthesize(
        self,
        query: str,
        contents: list[ExtractedContent],
        depth: str = "standard",
    ) -> ResearchReport:
        """
        Synthesize extracted content into a coherent report.

        Args:
            query: Original research question
            contents: List of extracted content from sources
            depth: Research depth (quick/standard/deep)

        Returns:
            ResearchReport with synthesized findings
        """
        if not contents:
            return ResearchReport(
                query=query,
                summary="No sources found for this query.",
                research_depth=depth,
            )

        # Build context from all sources
        source_context = self._build_source_context(contents)

        # Generate synthesis prompt based on depth
        if depth == "quick":
            prompt = self._quick_synthesis_prompt(query, source_context)
        elif depth == "deep":
            prompt = self._deep_synthesis_prompt(query, source_context)
        else:
            prompt = self._standard_synthesis_prompt(query, source_context)

        # Use the AI module for synthesis
        try:
            synthesis = self.interpreter.computer.ai.chat(prompt)
        except Exception:
            # Fallback if chat not available
            synthesis = self._fallback_synthesis(query, contents)

        # Parse synthesis into structured report
        return self._parse_synthesis(synthesis, query, contents, depth)

    def _build_source_context(self, contents: list[ExtractedContent]) -> str:
        """Build context string from all sources."""
        parts = []
        for i, content in enumerate(contents, 1):
            source = content.source
            text = content.summary if content.summary else content.clean_text[:1500]
            parts.append(
                f"""
### Source {i}: {source.title}
URL: {source.url}
{text}
"""
            )
        return "\n".join(parts)

    def _quick_synthesis_prompt(self, query: str, context: str) -> str:
        return f"""You are a research assistant. Provide a BRIEF answer to this question based on the sources:

Question: {query}

Sources:
{context}

Provide:
1. A direct answer (2-3 sentences)
2. Key supporting points (3-5 bullets)

Be concise. Cite sources by number [1], [2], etc."""

    def _standard_synthesis_prompt(self, query: str, context: str) -> str:
        return f"""You are a research assistant. Synthesize information from these sources:

Question: {query}

Sources:
{context}

Provide a structured response:
1. **Summary**: 3-5 sentence overview
2. **Key Findings**: Main points from the research
3. **Details**: Expand on important aspects
4. **Gaps**: Note any areas needing more research

Cite sources using [1], [2], etc."""

    def _deep_synthesis_prompt(self, query: str, context: str) -> str:
        return f"""You are a senior research analyst. Provide comprehensive analysis:

Research Question: {query}

Sources:
{context}

Provide a detailed report:
1. **Executive Summary**: Key takeaways
2. **Background**: Context for the topic
3. **Analysis**: Deep dive into findings with source citations
4. **Comparisons**: Compare/contrast different viewpoints
5. **Conclusions**: Your synthesized understanding
6. **Recommendations**: Suggested next steps or areas for further research

Use citations [1], [2], etc. throughout."""

    def _fallback_synthesis(
        self, query: str, contents: list[ExtractedContent]
    ) -> str:
        """Create a basic synthesis without LLM."""
        lines = [f"Research findings for: {query}", ""]

        for i, content in enumerate(contents, 1):
            lines.append(f"**Source {i}: {content.source.title}**")
            lines.append(content.source.snippet or content.clean_text[:200])
            lines.append("")

        return "\n".join(lines)

    def _parse_synthesis(
        self,
        synthesis: str,
        query: str,
        contents: list[ExtractedContent],
        depth: str,
    ) -> ResearchReport:
        """Parse LLM synthesis into structured report."""

        sections = []
        current_section = None
        current_content = []

        for line in synthesis.split("\n"):
            # Detect section headers
            if line.startswith("**") and line.endswith("**"):
                if current_section:
                    sections.append(
                        {
                            "title": current_section,
                            "content": "\n".join(current_content).strip(),
                        }
                    )
                current_section = line.strip("*").strip(":")
                current_content = []
            elif line.startswith("## ") or line.startswith("### "):
                if current_section:
                    sections.append(
                        {
                            "title": current_section,
                            "content": "\n".join(current_content).strip(),
                        }
                    )
                current_section = line.lstrip("#").strip()
                current_content = []
            else:
                current_content.append(line)

        if current_section:
            sections.append(
                {
                    "title": current_section,
                    "content": "\n".join(current_content).strip(),
                }
            )

        # Extract summary (first 500 chars or first section)
        summary = synthesis[:500]
        if sections and sections[0].get("title", "").lower() in [
            "summary",
            "executive summary",
        ]:
            summary = sections[0].get("content", synthesis[:500])

        return ResearchReport(
            query=query,
            summary=summary,
            sections=sections,
            citations=[c.source for c in contents],
            total_sources=len(contents),
            research_depth=depth,
        )
