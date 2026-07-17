"""Unit tests for the Reranker primitive.

litellm is mocked at the ``_get_litellm`` boundary (the fork's lazy loader), so
no network or API key is required. Covers the reorder path plus every arm of the
graceful no-op contract: missing key, provider raise, empty input, top_k.
"""

from unittest.mock import MagicMock, patch

import pytest

from interpreter.core.retrieval import Reranker

_LITELLM_PATH = "interpreter.core.llm.llm._get_litellm"


def _fake_litellm(results):
    """A mock litellm whose .rerank returns a response exposing `results`."""
    lit = MagicMock()
    resp = MagicMock()
    resp.results = results
    lit.rerank.return_value = resp
    return lit


@pytest.fixture(autouse=True)
def _clear_keys(monkeypatch):
    # Ensure ambient provider keys never leak into tests.
    monkeypatch.delenv("COHERE_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)


def test_reorders_by_provider_result():
    docs = ["alpha", "bravo", "charlie"]
    # Provider says index 2 is best, then 0, then 1.
    results = [
        {"index": 2, "relevance_score": 0.9},
        {"index": 0, "relevance_score": 0.5},
        {"index": 1, "relevance_score": 0.1},
    ]
    r = Reranker(model="cohere/rerank-v4.0-pro", api_key="test-key")
    with patch(_LITELLM_PATH, return_value=_fake_litellm(results)):
        ranked = r.rerank("query", docs)
    assert [idx for idx, _ in ranked] == [2, 0, 1]
    assert ranked[0][1] == 0.9


def test_top_k_truncates_and_is_passed_through():
    docs = ["a", "b", "c", "d"]
    results = [{"index": i, "relevance_score": 1.0 - i / 10} for i in range(2)]
    lit = _fake_litellm(results)
    r = Reranker(model="cohere/rerank-v4.0-pro", api_key="test-key")
    with patch(_LITELLM_PATH, return_value=lit):
        ranked = r.rerank("query", docs, top_k=2)
    assert len(ranked) == 2
    # top_n forwarded to the provider
    assert lit.rerank.call_args.kwargs["top_n"] == 2


def test_no_op_without_key_does_not_call_provider():
    docs = ["a", "b", "c"]
    r = Reranker(model="cohere/rerank-v4.0-pro")  # no key, env cleared
    assert r.is_available() is False
    with patch(_LITELLM_PATH) as mock_get:
        ranked = r.rerank("query", docs)
    mock_get.assert_not_called()  # never even loaded litellm
    assert ranked == [(0, 0.0), (1, 0.0), (2, 0.0)]  # identity order


def test_no_op_on_provider_exception():
    docs = ["a", "b"]
    lit = MagicMock()
    lit.rerank.side_effect = RuntimeError("provider down")
    r = Reranker(model="cohere/rerank-v4.0-pro", api_key="test-key")
    with patch(_LITELLM_PATH, return_value=lit):
        ranked = r.rerank("query", docs)
    assert ranked == [(0, 0.0), (1, 0.0)]  # degraded, no raise


def test_empty_documents():
    r = Reranker(model="cohere/rerank-v4.0-pro", api_key="test-key")
    assert r.rerank("query", []) == []


def test_empty_query_is_identity():
    docs = ["a", "b"]
    r = Reranker(model="cohere/rerank-v4.0-pro", api_key="test-key")
    with patch(_LITELLM_PATH) as mock_get:
        ranked = r.rerank("", docs)
    mock_get.assert_not_called()
    assert ranked == [(0, 0.0), (1, 0.0)]


def test_rerank_items_returns_reordered_objects():
    items = [{"path": "x.py"}, {"path": "y.py"}, {"path": "z.py"}]
    results = [
        {"index": 1, "relevance_score": 0.8},
        {"index": 2, "relevance_score": 0.4},
        {"index": 0, "relevance_score": 0.2},
    ]
    r = Reranker(model="cohere/rerank-v4.0-pro", api_key="test-key")
    with patch(_LITELLM_PATH, return_value=_fake_litellm(results)):
        ranked = r.rerank_items("query", items, key=lambda it: it["path"])
    assert [it["path"] for it, _ in ranked] == ["y.py", "z.py", "x.py"]


def test_rerank_items_empty():
    r = Reranker(model="cohere/rerank-v4.0-pro", api_key="test-key")
    assert r.rerank_items("q", [], key=str) == []


def test_key_resolution_by_prefix(monkeypatch):
    monkeypatch.setenv("COHERE_API_KEY", "cohere-env-key")
    assert Reranker(model="cohere/rerank-v4.0-pro").is_available() is True
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-env-key")
    assert Reranker(model="openrouter/cohere/rerank-v4.0-pro").is_available() is True
    # Unknown prefix, no explicit key → not available
    assert Reranker(model="mystery/model").is_available() is False


def test_object_shaped_results_are_parsed():
    """Provider items may be attribute objects, not dicts."""
    docs = ["a", "b"]
    item = MagicMock()
    item.index = 1
    item.relevance_score = 0.7
    r = Reranker(model="cohere/rerank-v4.0-pro", api_key="test-key")
    with patch(_LITELLM_PATH, return_value=_fake_litellm([item])):
        ranked = r.rerank("query", docs)
    assert ranked[0] == (1, 0.7)
