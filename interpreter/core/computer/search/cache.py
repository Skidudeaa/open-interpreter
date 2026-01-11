"""
Search Cache - TTL-based caching for search results.

Reduces API calls by caching recent search results.
"""

import hashlib
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .providers.base import SearchResult


@dataclass
class CacheEntry:
    """Cache entry with timestamp."""

    results: list["SearchResult"]
    timestamp: float
    provider: str


class SearchCache:
    """
    TTL-based cache for search results.

    Thread-safe caching with configurable TTL and max entries.
    Automatically evicts expired and oldest entries.

    Uses OrderedDict for O(1) FIFO eviction instead of O(n) min() search.
    """

    def __init__(self, ttl_seconds: int = 3600, max_entries: int = 1000):
        """
        Initialize the cache.

        Args:
            ttl_seconds: Time-to-live for cache entries (default: 1 hour)
            max_entries: Maximum number of cached queries
        """
        self.ttl = ttl_seconds
        self.max_entries = max_entries
        # WHY: OrderedDict for O(1) FIFO eviction via popitem(last=False)
        # TRADEOFF: Slightly more memory overhead vs O(n) min() on every eviction
        self._cache: OrderedDict[str, CacheEntry] = OrderedDict()
        self._lock = threading.Lock()

    def _make_key(self, query: str, provider: str, **kwargs) -> str:
        """
        Create cache key from query and parameters.

        Args:
            query: Search query
            provider: Provider name
            **kwargs: Additional parameters

        Returns:
            SHA256-based cache key
        """
        # Sort kwargs for consistent key generation
        sorted_kwargs = sorted(kwargs.items())
        key_data = f"{provider}:{query}:{sorted_kwargs}"
        return hashlib.sha256(key_data.encode()).hexdigest()[:32]

    def get(self, query: str, provider: str, **kwargs) -> list["SearchResult"] | None:
        """
        Get cached results if available and not expired.

        Args:
            query: Search query
            provider: Provider name
            **kwargs: Additional parameters used in the search

        Returns:
            Cached results or None if not found/expired
        """
        key = self._make_key(query, provider, **kwargs)
        with self._lock:
            entry = self._cache.get(key)
            if entry:
                if (time.time() - entry.timestamp) < self.ttl:
                    return entry.results
                else:
                    # Expired, remove it
                    del self._cache[key]
        return None

    def set(
        self,
        query: str,
        provider: str,
        results: list["SearchResult"],
        **kwargs,
    ) -> None:
        """
        Cache search results.

        Args:
            query: Search query
            provider: Provider name
            results: Search results to cache
            **kwargs: Additional parameters used in the search
        """
        key = self._make_key(query, provider, **kwargs)
        with self._lock:
            # Evict if at capacity
            if len(self._cache) >= self.max_entries:
                self._evict_oldest()

            self._cache[key] = CacheEntry(
                results=results,
                timestamp=time.time(),
                provider=provider,
            )

    def _evict_oldest(self) -> None:
        """Remove the oldest cache entry (O(1) via OrderedDict FIFO)."""
        if not self._cache:
            return
        # WHY: popitem(last=False) is O(1) vs O(n) min() search
        self._cache.popitem(last=False)

    def clear(self) -> None:
        """Clear all cached entries."""
        with self._lock:
            self._cache.clear()

    def clear_expired(self) -> int:
        """
        Remove all expired entries.

        Returns:
            Number of entries removed
        """
        now = time.time()
        removed = 0
        with self._lock:
            expired_keys = [
                k for k, v in self._cache.items() if (now - v.timestamp) >= self.ttl
            ]
            for key in expired_keys:
                del self._cache[key]
                removed += 1
        return removed

    def stats(self) -> dict:
        """Get cache statistics."""
        with self._lock:
            return {
                "entries": len(self._cache),
                "max_entries": self.max_entries,
                "ttl_seconds": self.ttl,
            }
