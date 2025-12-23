"""
Search Module - Unified web search for Open Interpreter.

Provides a unified interface for web search across multiple providers:
- Tavily (AI-optimized, requires API key)
- DuckDuckGo (free, no API key)
- Google Custom Search (requires API key + Search Engine ID)

Features:
- Automatic provider selection based on availability
- Result caching to reduce API calls
- Rate limiting to avoid exceeding quotas
- Normalized results across all providers

Example:
    # Basic search (auto-selects best available provider)
    results = computer.search.web("python async tutorial")

    # Specific provider
    results = computer.search.web("AI news", provider="tavily")

    # Domain-restricted search
    results = computer.search.web(
        "react hooks",
        include_domains=["reactjs.org", "github.com"]
    )
"""

from typing import TYPE_CHECKING, Any

from .cache import SearchCache
from .providers.base import ProviderNotAvailableError, SearchProvider, SearchResult
from .rate_limiter import RateLimiter

if TYPE_CHECKING:
    from .providers.duckduckgo import DuckDuckGoProvider
    from .providers.google import GoogleProvider
    from .providers.tavily import TavilyProvider


class Search:
    """
    Unified web search facade for Open Interpreter.

    Manages multiple search providers with automatic selection,
    caching, and rate limiting.
    """

    # Provider priority for automatic selection
    PROVIDER_PRIORITY = ["tavily", "google", "duckduckgo"]

    def __init__(self, computer: Any):
        """
        Initialize the Search module.

        Args:
            computer: The Computer instance
        """
        self.computer = computer
        self._providers: dict[str, SearchProvider] = {}
        self._cache = SearchCache()
        self._rate_limiter = RateLimiter()
        self._default_provider: str | None = None
        self._providers_initialized = False

    def _init_providers(self) -> None:
        """Initialize available providers (lazy-loaded)."""
        if self._providers_initialized:
            return

        # Try Tavily
        try:
            from .providers.tavily import TavilyProvider

            provider = TavilyProvider()
            if provider.is_available():
                self._providers["tavily"] = provider
        except Exception:
            pass

        # Try Google
        try:
            from .providers.google import GoogleProvider

            provider = GoogleProvider()
            if provider.is_available():
                self._providers["google"] = provider
        except Exception:
            pass

        # Try DuckDuckGo (always try, no API key needed)
        try:
            from .providers.duckduckgo import DuckDuckGoProvider

            provider = DuckDuckGoProvider()
            if provider.is_available():
                self._providers["duckduckgo"] = provider
        except Exception:
            pass

        self._providers_initialized = True

    def web(
        self,
        query: str,
        max_results: int = 10,
        provider: str | None = None,
        use_cache: bool = True,
        **options: Any,
    ) -> list[SearchResult]:
        """
        Search the web.

        Args:
            query: Search query string
            max_results: Maximum number of results to return
            provider: Specific provider to use (tavily/duckduckgo/google)
            use_cache: Whether to use cached results
            **options: Provider-specific options
                - include_domains: List of domains to include (Tavily)
                - exclude_domains: List of domains to exclude (Tavily)
                - search_depth: "basic" or "advanced" (Tavily)
                - region: Region code (DuckDuckGo)
                - site_search: Restrict to site (Google)

        Returns:
            List of SearchResult objects

        Raises:
            ProviderNotAvailableError: If no provider is available
        """
        self._init_providers()

        # Select provider
        provider_name = provider or self._select_provider()
        if provider_name not in self._providers:
            available = list(self._providers.keys())
            if provider:
                raise ProviderNotAvailableError(
                    f"Provider '{provider}' not available. "
                    f"Available: {available or 'none'}"
                )
            else:
                raise ProviderNotAvailableError(
                    "No search providers available. "
                    "Set TAVILY_API_KEY or install duckduckgo-search."
                )

        search_provider = self._providers[provider_name]

        # Check cache
        if use_cache:
            cached = self._cache.get(
                query, provider_name, max_results=max_results, **options
            )
            if cached:
                return cached

        # Rate limit check
        self._rate_limiter.wait_if_needed(provider_name)

        # Execute search
        results = search_provider.search(query, max_results=max_results, **options)

        # Cache results
        if use_cache and results:
            self._cache.set(
                query, provider_name, results, max_results=max_results, **options
            )

        return results

    def _select_provider(self) -> str:
        """
        Select best available provider.

        Returns:
            Provider name

        Raises:
            ProviderNotAvailableError: If no provider available
        """
        if self._default_provider and self._default_provider in self._providers:
            return self._default_provider

        for name in self.PROVIDER_PRIORITY:
            if name in self._providers:
                return name

        raise ProviderNotAvailableError(
            "No search providers available. "
            "Configure TAVILY_API_KEY or install duckduckgo-search."
        )

    def get_available_providers(self) -> list[str]:
        """
        List available provider names.

        Returns:
            List of provider names that are configured and available
        """
        self._init_providers()
        return list(self._providers.keys())

    def set_default_provider(self, name: str) -> None:
        """
        Set the default search provider.

        Args:
            name: Provider name to use by default

        Raises:
            ValueError: If provider not available
        """
        self._init_providers()
        if name not in self._providers:
            raise ValueError(
                f"Provider '{name}' not available. "
                f"Available: {list(self._providers.keys())}"
            )
        self._default_provider = name

    def clear_cache(self) -> None:
        """Clear the search result cache."""
        self._cache.clear()

    def get_stats(self) -> dict[str, Any]:
        """
        Get search module statistics.

        Returns:
            Dictionary with cache and provider stats
        """
        self._init_providers()
        return {
            "providers": list(self._providers.keys()),
            "default_provider": self._default_provider,
            "cache": self._cache.stats(),
        }

    # Convenience methods for specific providers

    def tavily(
        self,
        query: str,
        max_results: int = 10,
        search_depth: str = "basic",
        include_answer: bool = False,
        **options: Any,
    ) -> list[SearchResult]:
        """
        Search using Tavily API directly.

        Args:
            query: Search query
            max_results: Maximum results
            search_depth: "basic" or "advanced"
            include_answer: Include AI-generated answer
            **options: Additional Tavily options

        Returns:
            List of SearchResult objects
        """
        return self.web(
            query,
            max_results=max_results,
            provider="tavily",
            search_depth=search_depth,
            include_answer=include_answer,
            **options,
        )

    def duckduckgo(
        self,
        query: str,
        max_results: int = 10,
        region: str = "wt-wt",
        **options: Any,
    ) -> list[SearchResult]:
        """
        Search using DuckDuckGo directly.

        Args:
            query: Search query
            max_results: Maximum results
            region: Region code
            **options: Additional options

        Returns:
            List of SearchResult objects
        """
        return self.web(
            query,
            max_results=max_results,
            provider="duckduckgo",
            region=region,
            **options,
        )

    def google(
        self,
        query: str,
        max_results: int = 10,
        site_search: str | None = None,
        **options: Any,
    ) -> list[SearchResult]:
        """
        Search using Google Custom Search directly.

        Args:
            query: Search query
            max_results: Maximum results
            site_search: Restrict to specific site
            **options: Additional options

        Returns:
            List of SearchResult objects
        """
        return self.web(
            query,
            max_results=max_results,
            provider="google",
            site_search=site_search,
            **options,
        )
