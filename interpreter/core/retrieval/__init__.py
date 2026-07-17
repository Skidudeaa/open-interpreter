"""Cross-cutting retrieval utilities shared by agents, memory, and the API.

Currently exposes the :class:`Reranker` — a relevance-ranking primitive that
reorders a candidate list against a query. It is deliberately placed outside
``agents/`` because it is reused by Scout, ResearchAgent, and semantic-memory
retrieval alike.
"""

from .reranker import Reranker

__all__ = ["Reranker"]
