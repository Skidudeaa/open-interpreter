"""Reranker — relevance-ranking primitive backed by ``litellm.rerank``.

Takes a query plus a list of candidate documents and returns them reordered by
relevance (best first). Used to precision-order retrieval candidates before they
are consumed — Scout search hits, ResearchAgent fetched sources, semantic-memory
recall.

Design contract — **always safe, never blocking**. When the reranker is
unconfigured (no resolvable API key), handed empty input, or the provider call
raises, it returns an *identity* ordering rather than erroring. Callers therefore
get a usable ordering unconditionally and can wire it in without defensive code,
mirroring the fork's non-blocking memory/validation paths.

Provider is chosen entirely by the model string (litellm routes on the prefix):
``cohere/rerank-v4.0-pro`` (default, via ``COHERE_API_KEY``) or
``openrouter/<model>`` (via ``OPENROUTER_API_KEY``; OpenRouter aliases the same
model as ``rerank-4-pro``). Switching providers is a config change, not code.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Callable

logger = logging.getLogger(__name__)

# Model-prefix → environment variable holding that provider's key. Mirrors the
# dispatch in intent_refiner._call_mistral so credential handling stays uniform.
_KEY_ENV_BY_PREFIX: dict[str, str] = {
    "cohere/": "COHERE_API_KEY",
    "openrouter/": "OPENROUTER_API_KEY",
}


class Reranker:
    """Reorders candidate documents by relevance to a query.

    Args:
        model: litellm rerank model id, e.g. ``"cohere/rerank-v4.0-pro"``.
        api_key: explicit key; if omitted, resolved from the env var matching the
            model prefix.
        api_base: optional base URL override (for gateways / self-hosted).
    """

    def __init__(
        self,
        model: str = "cohere/rerank-v4.0-pro",
        api_key: str | None = None,
        api_base: str | None = None,
    ) -> None:
        self.model = model or ""
        self.api_key = api_key
        self.api_base = api_base
        self._warned = False  # log a provider failure at most once per instance

    # ---------------------------------------------------------------- config

    def _resolve_key(self) -> str | None:
        """Explicit key wins; otherwise pull the env var for the model's prefix."""
        if self.api_key:
            return self.api_key
        for prefix, env_name in _KEY_ENV_BY_PREFIX.items():
            if self.model.startswith(prefix):
                return os.getenv(env_name)
        return None

    def is_available(self) -> bool:
        """True when a model is set and a key is resolvable — i.e. rerank can run."""
        return bool(self.model) and bool(self._resolve_key())

    def _auth_kwargs(self) -> dict[str, Any]:
        kwargs: dict[str, Any] = {}
        key = self._resolve_key()
        if key:
            kwargs["api_key"] = key
        if self.api_base:
            kwargs["api_base"] = self.api_base
        return kwargs

    # ---------------------------------------------------------------- core

    @staticmethod
    def _identity(n: int, limit: int) -> list[tuple[int, float]]:
        return [(i, 0.0) for i in range(min(limit, n))]

    def rerank(
        self,
        query: str,
        documents: list[str],
        top_k: int | None = None,
    ) -> list[tuple[int, float]]:
        """Rank ``documents`` against ``query``.

        Returns a list of ``(original_index, relevance_score)`` pairs, best first,
        truncated to ``top_k`` (all if None). On any unavailability/failure returns
        an identity ordering with zero scores — never raises.
        """
        n = len(documents)
        if n == 0:
            return []
        limit = n if top_k is None else max(0, min(top_k, n))
        if limit == 0:
            return []
        if not query or not self.is_available():
            return self._identity(n, limit)

        self._emit("RERANK_START", {"model": self.model, "candidates": n})
        try:
            # Reuse the fork's lazy litellm loader (~300ms cold-start amortized).
            from ..llm.llm import _get_litellm

            litellm = _get_litellm()
            response = litellm.rerank(
                model=self.model,
                query=query,
                documents=documents,
                top_n=limit,
                **self._auth_kwargs(),
            )
            results = getattr(response, "results", None) or []
            ranked: list[tuple[int, float]] = []
            for r in results:
                idx = _field(r, "index")
                score = _field(r, "relevance_score", 0.0)
                if idx is None:
                    continue
                ranked.append((int(idx), float(score)))
            if not ranked:
                self._emit("RERANK_END", {"ranked": 0, "message": "no results"})
                return self._identity(n, limit)
            ranked = ranked[:limit]
            self._emit(
                "RERANK_END",
                {
                    "ranked": len(ranked),
                    "top_score": ranked[0][1],
                    "message": f"ranked {len(ranked)} (top {ranked[0][1]:.2f})",
                },
            )
            return ranked
        except Exception as e:  # provider/network/parse — degrade to identity
            self._warn_once(e)
            self._emit(
                "RERANK_END",
                {"ranked": 0, "error": type(e).__name__, "message": "rerank failed"},
            )
            return self._identity(n, limit)

    def rerank_items(
        self,
        query: str,
        items: list[Any],
        key: Callable[[Any], str],
        top_k: int | None = None,
    ) -> list[tuple[Any, float]]:
        """Rank arbitrary objects, extracting rank text from each via ``key``.

        Returns ``(item, score)`` pairs, best first. The ergonomic wrapper most
        callers use — map domain objects to text, get reordered objects back.
        """
        if not items:
            return []
        documents = [key(it) or "" for it in items]
        ranked = self.rerank(query, documents, top_k=top_k)
        return [(items[i], score) for i, score in ranked]

    # ---------------------------------------------------------------- logging

    def _warn_once(self, exc: Exception) -> None:
        if not self._warned:
            logger.debug("Rerank failed (%s); falling back to identity order", exc)
            self._warned = True

    @staticmethod
    def _emit(event_name: str, data: dict[str, Any]) -> None:
        """Emit a rerank event to the global bus (feeds cc-sidecar). Best-effort:
        lazily imported and fully swallowed so retrieval stays decoupled from the
        UI and a missing/erroring bus never affects ranking."""
        try:
            from ...terminal_interface.components.ui_events import (
                EventType,
                UIEvent,
                get_event_bus,
            )

            get_event_bus().emit(
                UIEvent(
                    type=getattr(EventType, event_name),
                    data=data,
                    source="reranker",
                )
            )
        except Exception:
            pass


def _field(item: Any, name: str, default: Any = None) -> Any:
    """Read a field from a rerank result item, dict- or attribute-shaped."""
    if isinstance(item, dict):
        return item.get(name, default)
    return getattr(item, name, default)
