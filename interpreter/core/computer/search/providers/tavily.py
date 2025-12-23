"""
Tavily Search Provider - AI-optimized search API.

Tavily is designed specifically for AI applications with:
- High-quality, relevant results
- Built-in answer synthesis
- Domain filtering
- Citation support
"""

from typing import Any

import requests

from .base import SearchProvider, SearchResult


class TavilyProvider(SearchProvider):
    """
    Tavily Search API - optimized for AI research tasks.

    Provides high-quality search results with optional AI-generated
    answers and comprehensive citations.

    Requires TAVILY_API_KEY environment variable.
    """

    name = "tavily"
    requires_api_key = True
    api_key_env_var = "TAVILY_API_KEY"
    BASE_URL = "https://api.tavily.com/search"

    def __init__(self, api_key: str | None = None):
        """
        Initialize Tavily provider.

        Args:
            api_key: API key (or set TAVILY_API_KEY env var)
        """
        super().__init__(api_key)

    def search(
        self,
        query: str,
        max_results: int = 10,
        search_depth: str = "basic",
        include_answer: bool = False,
        include_domains: list[str] | None = None,
        exclude_domains: list[str] | None = None,
        **options: Any,
    ) -> list[SearchResult]:
        """
        Search using Tavily API.

        Args:
            query: Search query
            max_results: Maximum results (1-20)
            search_depth: "basic" or "advanced" (more thorough)
            include_answer: Include AI-generated answer
            include_domains: Only search these domains
            exclude_domains: Exclude these domains
            **options: Additional options

        Returns:
            List of SearchResult objects
        """
        if not self.is_available():
            return []

        payload = {
            "api_key": self.api_key,
            "query": query,
            "search_depth": search_depth,
            "max_results": min(max_results, 20),
            "include_answer": include_answer,
        }

        if include_domains:
            payload["include_domains"] = include_domains
        if exclude_domains:
            payload["exclude_domains"] = exclude_domains

        try:
            response = requests.post(
                self.BASE_URL,
                json=payload,
                timeout=30,
            )
            response.raise_for_status()
            data = response.json()

            results = []
            for r in data.get("results", []):
                results.append(
                    SearchResult(
                        title=r.get("title", ""),
                        url=r.get("url", ""),
                        snippet=r.get("content", ""),
                        source=self.name,
                        score=r.get("score"),
                        published_date=r.get("published_date"),
                        raw_data=r,
                    )
                )

            # If answer was requested, prepend it as a special result
            if include_answer and data.get("answer"):
                results.insert(
                    0,
                    SearchResult(
                        title="AI Answer",
                        url="",
                        snippet=data["answer"],
                        source=f"{self.name}_answer",
                        score=1.0,
                        raw_data={"answer": data["answer"]},
                    ),
                )

            return results
        except Exception:
            return []

    def is_available(self) -> bool:
        """Check if API key is configured."""
        return bool(self.api_key)
