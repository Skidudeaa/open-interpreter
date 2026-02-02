"""
Tests for the ResearchAgent.

Tests research functionality including:
- ResearchConfig
- SourceResult and SearchResults
- ResearchReport
- ResearchSynthesizer
- ResearchAgent
"""

import unittest
from unittest import mock

from interpreter.core.agents.research.sources import SearchResults, SourceResult
from interpreter.core.agents.research.synthesizer import (
    ExtractedContent,
    ResearchReport,
    ResearchSynthesizer,
)
from interpreter.core.agents.research_agent import ResearchAgent, ResearchConfig


class TestResearchConfig(unittest.TestCase):
    """Tests for ResearchConfig."""

    def test_default_config(self):
        """Test default configuration."""
        config = ResearchConfig()
        self.assertEqual(config.depth, "standard")
        self.assertEqual(config.max_sources, 10)
        self.assertEqual(config.sources, ["web"])
        self.assertEqual(config.parallel_fetches, 5)
        self.assertTrue(config.include_citations)

    def test_quick_config(self):
        """Test quick configuration preset."""
        config = ResearchConfig.quick()
        self.assertEqual(config.depth, "quick")
        self.assertEqual(config.max_sources, 5)

    def test_standard_config(self):
        """Test standard configuration preset."""
        config = ResearchConfig.standard()
        self.assertEqual(config.depth, "standard")
        self.assertEqual(config.max_sources, 10)

    def test_deep_config(self):
        """Test deep configuration preset."""
        config = ResearchConfig.deep()
        self.assertEqual(config.depth, "deep")
        self.assertEqual(config.max_sources, 20)
        self.assertEqual(config.parallel_fetches, 10)

    def test_custom_config(self):
        """Test custom configuration."""
        config = ResearchConfig(
            depth="deep",
            max_sources=15,
            sources=["web", "files"],
            parallel_fetches=8,
        )
        self.assertEqual(config.max_sources, 15)
        self.assertEqual(config.sources, ["web", "files"])


class TestSourceResult(unittest.TestCase):
    """Tests for SourceResult."""

    def test_basic_creation(self):
        """Test basic SourceResult creation."""
        result = SourceResult(
            source_type="web",
            url="https://example.com",
            title="Test Title",
            content="Test content",
            snippet="Test snippet",
            relevance_score=0.9,
        )
        self.assertEqual(result.source_type, "web")
        self.assertEqual(result.url, "https://example.com")
        self.assertEqual(result.relevance_score, 0.9)

    def test_to_citation(self):
        """Test citation formatting."""
        result = SourceResult(
            source_type="web",
            url="https://example.com/page",
            title="Test Page",
            content="Content",
        )
        citation = result.to_citation()
        self.assertEqual(citation, "[Test Page](https://example.com/page)")

    def test_to_dict(self):
        """Test dictionary conversion."""
        result = SourceResult(
            source_type="file",
            url="/path/to/file.py",
            title="file.py",
            content="Code here",
            relevance_score=0.8,
        )
        d = result.to_dict()
        self.assertEqual(d["source_type"], "file")
        self.assertEqual(d["url"], "/path/to/file.py")
        self.assertEqual(d["relevance_score"], 0.8)


class TestSearchResults(unittest.TestCase):
    """Tests for SearchResults."""

    def test_top_n(self):
        """Test getting top N results."""
        results = SearchResults(
            query="test",
            results=[
                SourceResult(
                    source_type="web",
                    url="https://a.com",
                    title="A",
                    content="",
                    relevance_score=0.5,
                ),
                SourceResult(
                    source_type="web",
                    url="https://b.com",
                    title="B",
                    content="",
                    relevance_score=0.9,
                ),
                SourceResult(
                    source_type="web",
                    url="https://c.com",
                    title="C",
                    content="",
                    relevance_score=0.7,
                ),
            ],
        )
        top = results.top_n(2)
        self.assertEqual(len(top), 2)
        self.assertEqual(top[0].title, "B")  # Highest score
        self.assertEqual(top[1].title, "C")

    def test_by_type(self):
        """Test filtering by source type."""
        results = SearchResults(
            query="test",
            results=[
                SourceResult(source_type="web", url="", title="Web", content=""),
                SourceResult(source_type="file", url="", title="File", content=""),
                SourceResult(source_type="web", url="", title="Web2", content=""),
            ],
        )
        web_results = results.by_type("web")
        self.assertEqual(len(web_results), 2)
        file_results = results.by_type("file")
        self.assertEqual(len(file_results), 1)


class TestExtractedContent(unittest.TestCase):
    """Tests for ExtractedContent."""

    def test_word_count_computed(self):
        """Test word count computation."""
        source = SourceResult(
            source_type="web",
            url="https://example.com",
            title="Test",
            content="Original content",
        )
        extracted = ExtractedContent(
            source=source,
            clean_text="one two three four five",
        )
        self.assertEqual(extracted.word_count, 5)


class TestResearchReport(unittest.TestCase):
    """Tests for ResearchReport."""

    def test_basic_creation(self):
        """Test basic ResearchReport creation."""
        report = ResearchReport(
            query="Test query",
            summary="Test summary",
            research_depth="standard",
        )
        self.assertEqual(report.query, "Test query")
        self.assertEqual(report.summary, "Test summary")

    def test_to_markdown(self):
        """Test markdown formatting."""
        report = ResearchReport(
            query="Test query",
            summary="Test summary",
            sections=[
                {"title": "Section 1", "content": "Content 1"},
            ],
            citations=[
                SourceResult(
                    source_type="web",
                    url="https://example.com",
                    title="Source 1",
                    content="",
                ),
            ],
            total_sources=1,
            research_depth="standard",
        )
        md = report.to_markdown()
        self.assertIn("# Research Report:", md)
        self.assertIn("Test summary", md)
        self.assertIn("### Section 1", md)
        self.assertIn("[Source 1](https://example.com)", md)

    def test_to_dict(self):
        """Test dictionary conversion."""
        report = ResearchReport(
            query="Test",
            summary="Summary",
            total_sources=5,
            research_depth="deep",
        )
        d = report.to_dict()
        self.assertEqual(d["query"], "Test")
        self.assertEqual(d["total_sources"], 5)
        self.assertEqual(d["research_depth"], "deep")


class TestResearchSynthesizer(unittest.TestCase):
    """Tests for ResearchSynthesizer."""

    def setUp(self):
        """Set up test fixtures."""
        self.mock_interpreter = mock.Mock()
        self.mock_interpreter.computer = mock.Mock()
        self.mock_interpreter.computer.ai = mock.Mock()
        self.mock_interpreter.computer.ai.chat = mock.Mock(
            return_value="**Summary**: This is a test summary.\n\n**Key Findings**: Point 1, Point 2"
        )

    def test_synthesize_empty(self):
        """Test synthesis with no content."""
        synthesizer = ResearchSynthesizer(self.mock_interpreter)
        report = synthesizer.synthesize("test query", [], depth="quick")
        self.assertIn("No sources found", report.summary)

    def test_synthesize_with_content(self):
        """Test synthesis with content."""
        synthesizer = ResearchSynthesizer(self.mock_interpreter)

        source = SourceResult(
            source_type="web",
            url="https://example.com",
            title="Test Source",
            content="Test content",
        )
        contents = [
            ExtractedContent(source=source, clean_text="Extracted text here"),
        ]

        report = synthesizer.synthesize("test query", contents, depth="standard")
        self.assertEqual(report.query, "test query")
        self.assertEqual(len(report.citations), 1)
        self.assertEqual(report.total_sources, 1)


class TestResearchAgent(unittest.TestCase):
    """Tests for ResearchAgent."""

    def setUp(self):
        """Set up test fixtures."""
        self.mock_interpreter = mock.Mock()
        self.mock_interpreter.computer = mock.Mock()
        self.mock_interpreter.computer.search = mock.Mock()
        self.mock_interpreter.computer.search.web = mock.Mock(return_value=[])
        self.mock_interpreter.computer.documents = mock.Mock()
        self.mock_interpreter.computer.ai = mock.Mock()
        self.mock_interpreter.computer.ai.chat = mock.Mock(
            return_value="Summary of research findings."
        )

    def test_agent_init(self):
        """Test agent initialization."""
        agent = ResearchAgent(self.mock_interpreter)
        self.assertEqual(agent.config.depth, "standard")
        self.assertIsNone(agent._last_report)

    def test_agent_init_with_config(self):
        """Test agent initialization with custom config."""
        config = ResearchConfig(depth="deep", max_sources=15)
        agent = ResearchAgent(self.mock_interpreter, config=config)
        self.assertEqual(agent.config.depth, "deep")
        self.assertEqual(agent.config.max_sources, 15)

    def test_get_system_message(self):
        """Test system message generation."""
        agent = ResearchAgent(self.mock_interpreter)
        message = agent.get_system_message()
        self.assertIn("Research Agent", message)
        self.assertIn("synthesize", message.lower())

    def test_quick_research_method(self):
        """Test quick_research convenience method."""
        agent = ResearchAgent(self.mock_interpreter)

        with mock.patch.object(agent, "execute") as mock_execute:
            mock_execute.return_value = mock.Mock(content="Quick result")
            result = agent.quick_research("test query")

            self.assertEqual(result, "Quick result")

    def test_deep_research_method(self):
        """Test deep_research convenience method."""
        agent = ResearchAgent(self.mock_interpreter)

        with mock.patch.object(agent, "execute") as mock_execute:
            mock_execute.return_value = mock.Mock(content="Deep result")
            result = agent.deep_research("test query")

            self.assertEqual(result, "Deep result")

    def test_get_last_report(self):
        """Test getting last report."""
        agent = ResearchAgent(self.mock_interpreter)
        self.assertIsNone(agent.get_last_report())

    def test_get_sources(self):
        """Test getting sources."""
        agent = ResearchAgent(self.mock_interpreter)
        sources = agent.get_sources()
        self.assertEqual(sources, [])

    def test_execute_returns_result(self):
        """Test that execute returns AgentResult."""
        agent = ResearchAgent(self.mock_interpreter)

        # Mock the internal methods
        with mock.patch.object(agent, "_research_sync") as mock_research:
            mock_research.return_value = ResearchReport(
                query="test",
                summary="Test summary",
                research_depth="standard",
            )

            result = agent.execute("test query")

            self.assertTrue(result.success)
            self.assertIn("test", result.content.lower())


if __name__ == "__main__":
    unittest.main()
