"""
Rate Limiter - Per-provider rate limiting for search APIs.

Prevents exceeding API rate limits by tracking request times
and blocking when limits are approached.
"""

import threading
import time
from dataclasses import dataclass


@dataclass
class ProviderLimits:
    """Rate limits for a provider."""

    requests_per_minute: int
    requests_per_day: int | None = None


class RateLimiter:
    """
    Rate limiter for search providers.

    Tracks request times per provider and blocks when rate limits
    would be exceeded. Thread-safe.
    """

    # Default limits for known providers
    DEFAULT_LIMITS = {
        "tavily": ProviderLimits(100, 1000),
        "google": ProviderLimits(100, 10000),
        "duckduckgo": ProviderLimits(30, None),  # Conservative to avoid blocking
    }

    def __init__(self, custom_limits: dict[str, ProviderLimits] | None = None):
        """
        Initialize the rate limiter.

        Args:
            custom_limits: Override default limits for specific providers
        """
        self.limits = {**self.DEFAULT_LIMITS, **(custom_limits or {})}
        self._request_times: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def wait_if_needed(self, provider: str) -> float:
        """
        Block if rate limit would be exceeded.

        Args:
            provider: Provider name

        Returns:
            Time waited in seconds (0 if no wait needed)
        """
        limits = self.limits.get(provider)
        if not limits:
            return 0.0

        with self._lock:
            now = time.time()
            times = self._request_times.get(provider, [])

            # Clean old entries (older than 1 minute)
            minute_ago = now - 60
            times = [t for t in times if t > minute_ago]

            # Check per-minute limit
            if len(times) >= limits.requests_per_minute:
                # Calculate wait time
                oldest = times[0]
                wait_time = 60 - (now - oldest)
                if wait_time > 0:
                    # Release lock while sleeping
                    self._lock.release()
                    try:
                        time.sleep(wait_time)
                    finally:
                        self._lock.acquire()
                    return wait_time

            # Record this request
            times.append(time.time())
            self._request_times[provider] = times
            return 0.0

    def record_request(self, provider: str) -> None:
        """
        Record a request for rate limiting.

        Use this if you need to record a request without waiting.

        Args:
            provider: Provider name
        """
        with self._lock:
            times = self._request_times.get(provider, [])
            times.append(time.time())
            self._request_times[provider] = times

    def get_remaining(self, provider: str) -> int:
        """
        Get remaining requests before rate limit.

        Args:
            provider: Provider name

        Returns:
            Number of requests remaining in current minute
        """
        limits = self.limits.get(provider)
        if not limits:
            return 1000  # Unlimited

        with self._lock:
            now = time.time()
            times = self._request_times.get(provider, [])
            minute_ago = now - 60
            recent = [t for t in times if t > minute_ago]
            return max(0, limits.requests_per_minute - len(recent))

    def reset(self, provider: str | None = None) -> None:
        """
        Reset rate limit tracking.

        Args:
            provider: Specific provider to reset, or None for all
        """
        with self._lock:
            if provider:
                self._request_times.pop(provider, None)
            else:
                self._request_times.clear()
