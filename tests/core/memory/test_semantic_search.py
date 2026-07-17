"""Tests for SemanticEditGraph.semantic_search — relevance-ranked recall.

Covers the reranked path (mocked reranker), the recency fallback (no reranker),
the result shape the /memory/search endpoint consumes, and empty-query safety.
"""

from unittest.mock import MagicMock

import pytest

from interpreter.core.memory.edit_record import Edit, EditType
from interpreter.core.memory.semantic_graph import SemanticEditGraph


@pytest.fixture
def graph():
    g = SemanticEditGraph(db_path=None)  # in-memory
    for intent, path in [
        ("fix the backend selector bug", "core.py"),
        ("add docstrings to the parser", "parser.py"),
        ("optimize the socket listener", "server.py"),
    ]:
        g.record_edit(
            Edit(file_path=path, user_intent=intent, edit_type=EditType.FEATURE)
        )
    return g


def test_empty_query_returns_empty(graph):
    assert graph.semantic_search("") == []


def test_recency_fallback_without_reranker(graph):
    results = graph.semantic_search("anything", limit=10)
    assert len(results) == 3
    # Shape the API endpoint expects
    for r in results:
        assert set(r) == {"type", "content", "score", "metadata"}
        assert r["type"] == "edit"
        assert r["score"] == 0.0  # recency fallback → zero scores
        assert "file_path" in r["metadata"]


def test_reranked_order_and_scores(graph):
    reranker = MagicMock()
    reranker.is_available.return_value = True

    # Rank whichever candidate mentions "backend" first, deterministically.
    def _rerank_items(query, items, key, top_k=None):
        docs = [(i, key(it)) for i, it in enumerate(items)]
        docs.sort(key=lambda d: ("backend" not in d[1], d[0]))
        out = [(items[i], 0.9 - n * 0.1) for n, (i, _) in enumerate(docs)]
        return out[:top_k] if top_k else out

    reranker.rerank_items.side_effect = _rerank_items

    results = graph.semantic_search("where is the backend selected?", reranker=reranker)
    assert results[0]["content"] == "fix the backend selector bug"
    assert results[0]["score"] == pytest.approx(0.9)
    reranker.rerank_items.assert_called_once()


def test_top_k_limit_forwarded(graph):
    reranker = MagicMock()
    reranker.is_available.return_value = True
    reranker.rerank_items.side_effect = lambda q, items, key, top_k=None: [
        (it, 0.5) for it in items
    ][:top_k]
    results = graph.semantic_search("q", limit=2, reranker=reranker)
    assert len(results) == 2
    assert reranker.rerank_items.call_args.kwargs["top_k"] == 2


def test_unavailable_reranker_falls_back_to_recency(graph):
    reranker = MagicMock()
    reranker.is_available.return_value = False
    results = graph.semantic_search("q", reranker=reranker)
    assert len(results) == 3
    reranker.rerank_items.assert_not_called()
