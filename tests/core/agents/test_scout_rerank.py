"""Scout reranking integration — verifies `_rerank_search_results` wiring.

Tests the surgical addition in isolation (mocked reranker), not the full
`execute()` filesystem machinery: reorders when the reranker is available,
and is a strict no-op (unchanged order) when disabled/unavailable/no-query.
"""

from unittest.mock import MagicMock

import pytest

from interpreter.core.agents.scout_agent import ScoutAgent, SearchAnalysis, SearchResult


def _results():
    return [
        SearchResult("a.py", 1, "unrelated line", "keyword"),
        SearchResult("b.py", 2, "the backend is selected here", "keyword"),
        SearchResult("c.py", 3, "another line", "keyword"),
    ]


@pytest.fixture
def scout(tmp_path):
    return ScoutAgent(interpreter=MagicMock(), root_path=str(tmp_path))


def test_reorders_when_reranker_available(scout):
    reranker = MagicMock()
    reranker.is_available.return_value = True

    # Pretend the provider ranks index 1 best, then 2, then 0.
    def _rerank_items(query, items, key):
        order = [1, 2, 0]
        return [(items[i], 1.0 - n / 10) for n, i in enumerate(order)]

    reranker.rerank_items.side_effect = _rerank_items
    scout.interpreter.reranker = reranker

    out = scout._rerank_search_results(
        "where is the backend selected?", None, _results()
    )
    assert [r.file_path for r in out] == ["b.py", "c.py", "a.py"]
    reranker.rerank_items.assert_called_once()


def test_no_op_when_reranker_none(scout):
    scout.interpreter.reranker = None
    original = _results()
    out = scout._rerank_search_results("query", None, original)
    assert out == original  # unchanged order/identity


def test_no_op_when_unavailable(scout):
    reranker = MagicMock()
    reranker.is_available.return_value = False
    scout.interpreter.reranker = reranker
    original = _results()
    out = scout._rerank_search_results("query", None, original)
    assert out == original
    reranker.rerank_items.assert_not_called()


def test_no_op_without_query(scout):
    reranker = MagicMock()
    reranker.is_available.return_value = True
    scout.interpreter.reranker = reranker
    original = _results()
    # empty task and no analysis -> no query -> skip
    out = scout._rerank_search_results("", None, original)
    assert out == original
    reranker.rerank_items.assert_not_called()


def test_uses_semantic_query_fallback(scout):
    reranker = MagicMock()
    reranker.is_available.return_value = True
    reranker.rerank_items.side_effect = lambda q, items, key: [
        (it, 0.0) for it in items
    ]
    scout.interpreter.reranker = reranker
    analysis = SearchAnalysis(
        understanding="",
        search_queries=[],
        file_patterns=[],
        keywords=[],
        symbols=[],
        semantic_query="find the backend selector",
    )
    scout._rerank_search_results("", analysis, _results())
    # query threaded through is the semantic_query fallback
    assert reranker.rerank_items.call_args.args[0] == "find the backend selector"


def test_single_result_short_circuits(scout):
    reranker = MagicMock()
    reranker.is_available.return_value = True
    scout.interpreter.reranker = reranker
    one = [SearchResult("a.py", 1, "x", "keyword")]
    out = scout._rerank_search_results("q", None, one)
    assert out == one
    reranker.is_available.assert_not_called()  # short-circuited before touching reranker
