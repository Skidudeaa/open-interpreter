"""
Tests for the Search module.

Tests web search functionality including:
- SearchResult dataclass
- Search providers (DuckDuckGo, Tavily, Google)
- Search cache
- Rate limiter
- Search facade
"""

import time
import unittest
from unittest import mock

from interpreter.core.computer.search.providers.base import (
    ProviderNotAvailableError,
    SearchProvider,
    SearchResult,
)
from interpreter.core.computer.search.cache import SearchCache
from interpreter.core.computer.search.rate_limiter import ProviderLimits, RateLimiter


class TestSearchResult(unittest.TestCase):
    """Tests for SearchResult dataclass."""

    def test_basic_creation(self):
        """Test basic SearchResult creation."""
        result = SearchResult(
            title="Test Title",
            url="https://example.com",
            snippet="This is a test snippet",
            source="test_provider",
        )
        self.assertEqual(result.title, "Test Title")
        self.assertEqual(result.url, "https://example.com")
        self.assertEqual(result.snippet, "This is a test snippet")
        self.assertEqual(result.source, "test_provider")

    def test_optional_fields(self):
        """Test optional fields."""
        result = SearchResult(
            title="Test",
            url="https://example.com",
            snippet="Snippet",
            source="test",
            score=0.95,
            published_date="2024-01-01",
        )
        self.assertEqual(result.score, 0.95)
        self.assertEqual(result.published_date, "2024-01-01")

    def test_to_dict(self):
        """Test dictionary conversion."""
        result = SearchResult(
            title="Test",
            url="https://example.com",
            snippet="Snippet",
            source="test",
            score=0.9,
        )
        d = result.to_dict()
        self.assertEqual(d["title"], "Test")
        self.assertEqual(d["url"], "https://example.com")
        self.assertEqual(d["score"], 0.9)

    def test_to_markdown(self):
        """Test markdown formatting."""
        result = SearchResult(
            title="Test Title",
            url="https://example.com",
            snippet="This is a snippet",
            source="test",
        )
        md = result.to_markdown()
        self.assertIn("**[Test Title](https://example.com)**", md)
        self.assertIn("This is a snippet", md)


class TestSearchCache(unittest.TestCase):
    """Tests for SearchCache."""

    def test_cache_set_and_get(self):
        """Test basic cache operations."""
        cache = SearchCache(ttl_seconds=3600)

        results = [
            SearchResult(
                title="Result 1",
                url="https://example.com/1",
                snippet="Snippet 1",
                source="test",
            )
        ]

        cache.set("test query", "test_provider", results)
        cached = cache.get("test query", "test_provider")

        self.assertIsNotNone(cached)
        self.assertEqual(len(cached), 1)
        self.assertEqual(cached[0].title, "Result 1")

    def test_cache_miss(self):
        """Test cache miss."""
        cache = SearchCache()
        result = cache.get("nonexistent", "provider")
        self.assertIsNone(result)

    def test_cache_expiry(self):
        """Test cache TTL expiry."""
        cache = SearchCache(ttl_seconds=0)  # Immediate expiry

        results = [
            SearchResult(
                title="Test",
                url="https://example.com",
                snippet="Snippet",
                source="test",
            )
        ]

        cache.set("query", "provider", results)
        time.sleep(0.1)  # Wait for expiry
        cached = cache.get("query", "provider")
        self.assertIsNone(cached)

    def test_cache_clear(self):
        """Test cache clearing."""
        cache = SearchCache()
        results = [
            SearchResult(
                title="Test",
                url="https://example.com",
                snippet="Snippet",
                source="test",
            )
        ]
        cache.set("query", "provider", results)
        cache.clear()
        self.assertIsNone(cache.get("query", "provider"))

    def test_cache_stats(self):
        """Test cache statistics."""
        cache = SearchCache(ttl_seconds=3600, max_entries=100)
        stats = cache.stats()
        self.assertEqual(stats["entries"], 0)
        self.assertEqual(stats["max_entries"], 100)
        self.assertEqual(stats["ttl_seconds"], 3600)


class TestRateLimiter(unittest.TestCase):
    """Tests for RateLimiter."""

    def test_get_remaining(self):
        """Test remaining requests calculation."""
        limiter = RateLimiter()
        remaining = limiter.get_remaining("tavily")
        self.assertEqual(remaining, 100)  # Default limit

    def test_record_request(self):
        """Test request recording."""
        limiter = RateLimiter()
        initial = limiter.get_remaining("tavily")
        limiter.record_request("tavily")
        after = limiter.get_remaining("tavily")
        self.assertEqual(after, initial - 1)

    def test_reset(self):
        """Test rate limit reset."""
        limiter = RateLimiter()
        limiter.record_request("tavily")
        limiter.record_request("tavily")
        limiter.reset("tavily")
        remaining = limiter.get_remaining("tavily")
        self.assertEqual(remaining, 100)

    def test_reset_all(self):
        """Test resetting all providers."""
        limiter = RateLimiter()
        limiter.record_request("tavily")
        limiter.record_request("google")
        limiter.reset()
        self.assertEqual(limiter.get_remaining("tavily"), 100)
        self.assertEqual(limiter.get_remaining("google"), 100)


class TestDuckDuckGoProvider(unittest.TestCase):
    """Tests for DuckDuckGo provider."""

    def test_no_api_key_required(self):
        """Test that no API key is required."""
        from interpreter.core.computer.search.providers.duckduckgo import (
            DuckDuckGoProvider,
        )

        provider = DuckDuckGoProvider()
        self.assertFalse(provider.requires_api_key)

    def test_is_available_without_library(self):
        """Test availability check when library not installed."""
        from interpreter.core.computer.search.providers.duckduckgo import (
            DuckDuckGoProvider,
            _get_ddgs,
        )

        provider = DuckDuckGoProvider()
        # Will return False if duckduckgo-search not installed
        result = provider.is_available()
        self.assertIsInstance(result, bool)


class TestTavilyProvider(unittest.TestCase):
    """Tests for Tavily provider."""

    def test_requires_api_key(self):
        """Test that API key is required."""
        from interpreter.core.computer.search.providers.tavily import TavilyProvider

        provider = TavilyProvider()
        self.assertTrue(provider.requires_api_key)
        self.assertEqual(provider.api_key_env_var, "TAVILY_API_KEY")

    def test_not_available_without_key(self):
        """Test unavailable without API key."""
        from interpreter.core.computer.search.providers.tavily import TavilyProvider

        provider = TavilyProvider(api_key=None)
        self.assertFalse(provider.is_available())

    def test_available_with_key(self):
        """Test available with API key."""
        from interpreter.core.computer.search.providers.tavily import TavilyProvider

        provider = TavilyProvider(api_key="test_key")
        self.assertTrue(provider.is_available())

    @mock.patch('interpreter.core.computer.search.providers.tavily.requests')
    def test_search_with_mock(self, mock_requests):
        """Test search with mocked requests."""
        from interpreter.core.computer.search.providers.tavily import TavilyProvider

        mock_response = mock.Mock()
        mock_response.json.return_value = {
            "results": [
                {
                    "title": "Test Result",
                    "url": "https://example.com",
                    "content": "Test content",
                    "score": 0.9,
                }
            ]
        }
        mock_requests.post.return_value = mock_response

        provider = TavilyProvider(api_key="test_key")
        results = provider.search("test query")

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].title, "Test Result")
        self.assertEqual(results[0].score, 0.9)


class TestGoogleProvider(unittest.TestCase):
    """Tests for Google Custom Search provider."""

    def test_requires_api_key_and_cx(self):
        """Test that both API key and CX are required."""
        from interpreter.core.computer.search.providers.google import GoogleProvider

        provider = GoogleProvider(api_key="key", cx=None)
        self.assertFalse(provider.is_available())

        provider = GoogleProvider(api_key=None, cx="cx_id")
        self.assertFalse(provider.is_available())

        provider = GoogleProvider(api_key="key", cx="cx_id")
        self.assertTrue(provider.is_available())


class TestSearchFacade(unittest.TestCase):
    """Tests for Search facade class."""

    def setUp(self):
        """Set up test fixtures."""
        self.mock_computer = mock.Mock()

    def test_search_init(self):
        """Test Search initialization."""
        from interpreter.core.computer.search.search import Search

        search = Search(self.mock_computer)
        self.assertFalse(search._providers_initialized)
        self.assertEqual(search._providers, {})

    def test_get_available_providers(self):
        """Test getting available providers."""
        from interpreter.core.computer.search.search import Search

        search = Search(self.mock_computer)
        providers = search.get_available_providers()
        self.assertIsInstance(providers, list)

    def test_get_stats(self):
        """Test getting search stats."""
        from interpreter.core.computer.search.search import Search

        search = Search(self.mock_computer)
        stats = search.get_stats()
        self.assertIn("providers", stats)
        self.assertIn("cache", stats)

    def test_clear_cache(self):
        """Test cache clearing."""
        from interpreter.core.computer.search.search import Search

        search = Search(self.mock_computer)
        # Should not raise
        search.clear_cache()

    def test_set_default_provider_invalid(self):
        """Test setting invalid default provider."""
        from interpreter.core.computer.search.search import Search

        search = Search(self.mock_computer)
        with self.assertRaises(ValueError):
            search.set_default_provider("nonexistent_provider")


if __name__ == "__main__":
    unittest.main()
