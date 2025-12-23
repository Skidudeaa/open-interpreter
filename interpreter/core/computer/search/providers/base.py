"""
Base classes for search providers.

Provides:
- SearchResult: Normalized search result dataclass
- SearchProvider: Abstract base class for providers
"""

import os
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class SearchResult:
    """
    Normalized search result from any provider.

    All providers return results in this format for consistency.
    """

    title: str
    url: str
    snippet: str
    source: str  # Provider name: 'tavily', 'duckduckgo', 'google'
    score: float | None = None  # Relevance score if available
    published_date: str | None = None
    raw_data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "title": self.title,
            "url": self.url,
            "snippet": self.snippet,
            "source": self.source,
            "score": self.score,
            "published_date": self.published_date,
        }

    def to_markdown(self) -> str:
        """Format as markdown."""
        return f"**[{self.title}]({self.url})**\n{self.snippet}"


class SearchProvider(ABC):
    """
    Abstract base class for search providers.

    Subclasses must implement:
    - search(): Execute search and return results
    - is_available(): Check if provider is configured

    Providers should handle their own API keys via environment variables.
    """

    name: str = "base"
    requires_api_key: bool = True
    api_key_env_var: str = ""

    def __init__(self, api_key: str | None = None):
        """
        Initialize the provider.

        Args:
            api_key: Optional API key (overrides environment variable)
        """
        self.api_key = api_key or os.environ.get(self.api_key_env_var, "")
        if self.requires_api_key and not self.api_key:
            # Don't raise here, just mark as unavailable
            pass

    @abstractmethod
    def search(
        self,
        query: str,
        max_results: int = 10,
        **options: Any,
    ) -> list[SearchResult]:
        """
        Execute search and return normalized results.

        Args:
            query: Search query string
            max_results: Maximum number of results to return
            **options: Provider-specific options

        Returns:
            List of SearchResult objects
        """
        pass

    @abstractmethod
    def is_available(self) -> bool:
        """
        Check if provider is configured and available.

        Returns:
            True if provider can be used
        """
        pass


class SearchError(Exception):
    """Base exception for search errors."""

    pass


class ProviderNotAvailableError(SearchError):
    """Raised when no search provider is available."""

    pass


class RateLimitError(SearchError):
    """Raised when rate limit is exceeded."""

    pass
