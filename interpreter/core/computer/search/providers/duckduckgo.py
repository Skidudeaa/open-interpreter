"""
DuckDuckGo Search Provider - Free web search without API key.

Uses the ddgs library (formerly duckduckgo-search) for search results.
No API key required, but has rate limiting.
"""

from typing import Any

from .base import SearchProvider, SearchResult

# Lazy import
DDGS = None


def _get_ddgs():
    """Lazy load ddgs (or fallback to duckduckgo-search)."""
    global DDGS
    if DDGS is None:
        # Try new ddgs package first
        try:
            from ddgs import DDGS as _DDGS

            DDGS = _DDGS
        except ImportError:
            # Fall back to old package
            try:
                from duckduckgo_search import DDGS as _DDGS

                DDGS = _DDGS
            except ImportError:
                pass
    return DDGS


class DuckDuckGoProvider(SearchProvider):
    """
    DuckDuckGo Search - free, no API key required.

    Uses the duckduckgo-search library which scrapes DuckDuckGo results.
    Rate limited to avoid blocking.
    """

    name = "duckduckgo"
    requires_api_key = False
    api_key_env_var = ""

    def __init__(self, api_key: str | None = None):
        """Initialize DuckDuckGo provider (no API key needed)."""
        # No API key needed
        pass

    def search(
        self,
        query: str,
        max_results: int = 10,
        region: str = "wt-wt",
        safesearch: str = "moderate",
        **options: Any,
    ) -> list[SearchResult]:
        """
        Search DuckDuckGo.

        Args:
            query: Search query
            max_results: Maximum results to return
            region: Region code (default: worldwide)
            safesearch: Safe search level (off/moderate/strict)
            **options: Additional options

        Returns:
            List of SearchResult objects
        """
        ddgs_class = _get_ddgs()
        if ddgs_class is None:
            return []

        try:
            results = []
            with ddgs_class() as ddgs:
                for r in ddgs.text(
                    query,
                    region=region,
                    safesearch=safesearch,
                    max_results=max_results,
                ):
                    results.append(
                        SearchResult(
                            title=r.get("title", ""),
                            url=r.get("href", ""),
                            snippet=r.get("body", ""),
                            source=self.name,
                            raw_data=r,
                        )
                    )
            return results
        except Exception as e:
            import logging

            logging.getLogger(__name__).warning(f"DuckDuckGo search failed: {e}")
            return []

    def is_available(self) -> bool:
        """Check if duckduckgo-search is installed."""
        return _get_ddgs() is not None
