"""ResearchAgent reranking integration — verifies `_rerank_contents` wiring.

Tests the surgical addition in isolation (mocked reranker): reorders extracted
sources when the reranker is available, strict no-op otherwise.
"""

from unittest.mock import MagicMock

import pytest

from interpreter.core.agents.research.sources import SourceResult
from interpreter.core.agents.research.synthesizer import ExtractedContent
from interpreter.core.agents.research_agent import ResearchAgent


def _contents():
    def mk(title, text):
        src = SourceResult(source_type="web", url="http://x", title=title, content=text)
        return ExtractedContent(source=src, clean_text=text)

    return [
        mk("A", "cooking recipes and cake"),
        mk("B", "how the execution backend is selected"),
        mk("C", "gardening tips"),
    ]


@pytest.fixture
def agent():
    interp = MagicMock()
    interp.computer = MagicMock()
    return ResearchAgent(interp)


def test_reorders_when_available(agent):
    reranker = MagicMock()
    reranker.is_available.return_value = True

    def _rerank_items(query, items, key):
        order = [1, 0, 2]  # provider ranks B first
        return [(items[i], 1.0 - n / 10) for n, i in enumerate(order)]

    reranker.rerank_items.side_effect = _rerank_items
    agent.interpreter.reranker = reranker

    out = agent._rerank_contents("where is the backend selected?", _contents())
    assert [c.source.title for c in out] == ["B", "A", "C"]


def test_no_op_when_none(agent):
    agent.interpreter.reranker = None
    original = _contents()
    assert agent._rerank_contents("q", original) == original


def test_no_op_when_unavailable(agent):
    reranker = MagicMock()
    reranker.is_available.return_value = False
    agent.interpreter.reranker = reranker
    original = _contents()
    assert agent._rerank_contents("q", original) == original
    reranker.rerank_items.assert_not_called()


def test_no_op_without_query(agent):
    reranker = MagicMock()
    reranker.is_available.return_value = True
    agent.interpreter.reranker = reranker
    original = _contents()
    assert agent._rerank_contents("", original) == original
    reranker.rerank_items.assert_not_called()


def test_single_content_short_circuits(agent):
    reranker = MagicMock()
    agent.interpreter.reranker = reranker
    one = _contents()[:1]
    assert agent._rerank_contents("q", one) == one
    reranker.is_available.assert_not_called()
