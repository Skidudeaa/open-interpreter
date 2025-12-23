"""
Google Custom Search Provider - Google's official search API.

Requires:
- GOOGLE_API_KEY: API key from Google Cloud Console
- GOOGLE_SEARCH_ENGINE_ID: Custom Search Engine ID (cx)
"""

import os
from typing import Any

import requests

from .base import SearchProvider, SearchResult


class GoogleProvider(SearchProvider):
    """
    Google Custom Search API.

    Provides access to Google search results via the Custom Search API.
    Requires both an API key and a Custom Search Engine ID.

    Environment variables:
    - GOOGLE_API_KEY: API key
    - GOOGLE_SEARCH_ENGINE_ID: Search engine ID (cx)
    """

    name = "google"
    requires_api_key = True
    api_key_env_var = "GOOGLE_API_KEY"
    CX_ENV_VAR = "GOOGLE_SEARCH_ENGINE_ID"
    BASE_URL = "https://www.googleapis.com/customsearch/v1"

    def __init__(self, api_key: str | None = None, cx: str | None = None):
        """
        Initialize Google Custom Search provider.

        Args:
            api_key: API key (or set GOOGLE_API_KEY env var)
            cx: Search Engine ID (or set GOOGLE_SEARCH_ENGINE_ID env var)
        """
        super().__init__(api_key)
        self.cx = cx or os.environ.get(self.CX_ENV_VAR, "")

    def search(
        self,
        query: str,
        max_results: int = 10,
        site_search: str | None = None,
        date_restrict: str | None = None,
        **options: Any,
    ) -> list[SearchResult]:
        """
        Search using Google Custom Search API.

        Args:
            query: Search query
            max_results: Maximum results (1-10 per request)
            site_search: Restrict to specific site
            date_restrict: Date restriction (e.g., "d7" for past week)
            **options: Additional options

        Returns:
            List of SearchResult objects
        """
        if not self.is_available():
            return []

        params = {
            "key": self.api_key,
            "cx": self.cx,
            "q": query,
            "num": min(max_results, 10),  # Google API max is 10 per request
        }

        if site_search:
            params["siteSearch"] = site_search
        if date_restrict:
            params["dateRestrict"] = date_restrict

        try:
            response = requests.get(
                self.BASE_URL,
                params=params,
                timeout=30,
            )
            response.raise_for_status()
            data = response.json()

            results = []
            for item in data.get("items", []):
                results.append(
                    SearchResult(
                        title=item.get("title", ""),
                        url=item.get("link", ""),
                        snippet=item.get("snippet", ""),
                        source=self.name,
                        raw_data=item,
                    )
                )

            return results
        except Exception:
            return []

    def is_available(self) -> bool:
        """Check if API key and Search Engine ID are configured."""
        return bool(self.api_key and self.cx)
