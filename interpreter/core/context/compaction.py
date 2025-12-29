"""
Context Compactor for intelligent message management.

# ARCHITECTURE: Binary search to find optimal split point, then LLM flow generation.
# WHY: Preserve maximum context while staying within token budget.
# TRADEOFF: LLM call latency (~1-2s) vs. intelligent context preservation.
# NOTE: Falls back to tokentrim (delete oldest) if flow generation fails.
"""

import hashlib
import logging
from typing import TYPE_CHECKING, Any

from .flow_generator import TechnicalFlowGenerator
from .token_budget import TokenBudgetCalculator

if TYPE_CHECKING:
    from ..core import OpenInterpreter

logger = logging.getLogger(__name__)


class ContextCompactor:
    """
    Intelligent context compaction using LLM-generated technical flows.

    Instead of simply deleting old messages (like tokentrim), this compactor:
    1. Uses binary search to find the optimal split point
    2. Generates a technical flow document for older messages
    3. Preserves recent messages verbatim
    4. Caches summaries to avoid regeneration

    Usage:
        compactor = ContextCompactor(interpreter)
        compacted_messages = compactor.compact(messages, system_message)
    """

    def __init__(
        self,
        interpreter: "OpenInterpreter",
        preserve_recent: int | None = None,
        target_ratio: float = 0.75,
        cache_size: int = 50,
    ):
        """
        Initialize the context compactor.

        Args:
            interpreter: The OpenInterpreter instance
            preserve_recent: Messages to keep verbatim (default from interpreter)
            target_ratio: Target context usage after compaction (0.75 = 75%)
            cache_size: Maximum cached summaries
        """
        self.interpreter = interpreter
        self.preserve_recent = preserve_recent or getattr(
            interpreter, "context_preserve_recent", 8
        )
        self.target_ratio = target_ratio

        self.flow_generator = TechnicalFlowGenerator(interpreter)
        self.budget_calculator = TokenBudgetCalculator(interpreter)

        # Cache: hash of messages -> flow document
        self._flow_cache: dict[str, str] = {}
        self._cache_size = cache_size

    def compact(
        self,
        messages: list[dict[str, Any]],
        system_message: str,
    ) -> list[dict[str, Any]]:
        """
        Compact messages to fit within token budget.

        Args:
            messages: List of conversation messages (excluding system)
            system_message: The system message content

        Returns:
            Compacted message list with flow block if needed
        """
        if not messages:
            return messages

        # Calculate available budget
        available_tokens = self.budget_calculator.get_available_tokens(system_message)
        current_tokens = self.budget_calculator.count_messages(messages)

        logger.debug(
            f"Context check: {current_tokens} tokens, {available_tokens} available"
        )

        # Check if compaction is needed
        if current_tokens <= available_tokens:
            return messages  # No compaction needed

        # Find optimal split point via binary search
        split_point = self._find_optimal_split(messages, available_tokens)

        if split_point == 0:
            # Can't compact effectively, return as-is (tokentrim will handle)
            logger.debug("No effective split point found, deferring to tokentrim")
            return messages

        # Split messages
        old_messages = messages[:split_point]
        recent_messages = messages[split_point:]

        logger.debug(
            f"Compacting: {len(old_messages)} old + {len(recent_messages)} recent"
        )

        # Generate or retrieve cached flow
        flow_content = self._get_or_create_flow(old_messages)

        # Create flow message block
        flow_message = {
            "role": "system",
            "type": "context_flow",
            "content": flow_content,
            "metadata": {
                "summarized_count": len(old_messages),
                "flow_hash": self._hash_messages(old_messages),
                "is_compacted": True,
            },
        }

        return [flow_message] + recent_messages

    def _find_optimal_split(
        self,
        messages: list[dict[str, Any]],
        available_tokens: int,
    ) -> int:
        """
        Binary search to find optimal split point.

        # WHY: Maximize preserved messages while staying under budget.
        # TRADEOFF: O(log n) iterations vs. exact fit guarantee.

        Args:
            messages: All messages to consider
            available_tokens: Token budget available

        Returns:
            Optimal split index (messages before this become flow)
        """
        # Ensure we preserve minimum recent messages
        max_split = max(0, len(messages) - self.preserve_recent)

        if max_split == 0:
            return 0  # Must keep all messages

        target_tokens = int(available_tokens * self.target_ratio)

        low, high = 1, max_split
        best_split = low

        iterations = 0
        max_iterations = 10  # Limit iterations for performance

        while low <= high and iterations < max_iterations:
            iterations += 1
            mid = (low + high) // 2

            # Estimate tokens after compaction at this split
            estimated_tokens = self._estimate_compacted_tokens(messages, mid)

            if estimated_tokens <= target_tokens:
                # Under budget - try summarizing fewer messages
                best_split = mid
                high = mid - 1
            else:
                # Over budget - need to summarize more messages
                low = mid + 1

        logger.debug(
            f"Binary search: split at {best_split} after {iterations} iterations"
        )
        return best_split

    def _estimate_compacted_tokens(
        self,
        messages: list[dict[str, Any]],
        split_point: int,
    ) -> int:
        """
        Estimate tokens after compacting at a given split point.

        Args:
            messages: All messages
            split_point: Where to split (messages before become flow)

        Returns:
            Estimated total token count after compaction
        """
        # Tokens for flow document (estimated)
        flow_tokens = self.budget_calculator.estimate_summary_tokens(split_point)

        # Tokens for recent messages (exact)
        recent_messages = messages[split_point:]
        recent_tokens = self.budget_calculator.count_messages(recent_messages)

        return flow_tokens + recent_tokens

    def _get_or_create_flow(self, messages: list[dict[str, Any]]) -> str:
        """
        Get cached flow or generate new one.

        Args:
            messages: Messages to generate flow for

        Returns:
            Technical flow document content
        """
        cache_key = self._hash_messages(messages)

        if cache_key in self._flow_cache:
            logger.debug("Using cached flow document")
            return self._flow_cache[cache_key]

        # Generate new flow
        flow = self.flow_generator.generate(messages)

        # Cache it (with size limit)
        if len(self._flow_cache) >= self._cache_size:
            # Remove oldest entry
            oldest_key = next(iter(self._flow_cache))
            del self._flow_cache[oldest_key]

        self._flow_cache[cache_key] = flow

        return flow

    def _hash_messages(self, messages: list[dict[str, Any]]) -> str:
        """
        Create a hash of messages for cache keying.

        Args:
            messages: Messages to hash

        Returns:
            SHA256 hash string (first 16 chars)
        """
        # Hash based on message count and first/last content
        key_parts = [str(len(messages))]

        if messages:
            first_content = str(messages[0].get("content", ""))[:100]
            last_content = str(messages[-1].get("content", ""))[:100]
            key_parts.extend([first_content, last_content])

        key_string = "|".join(key_parts)
        return hashlib.sha256(key_string.encode()).hexdigest()[:16]

    def clear_cache(self):
        """Clear the flow cache."""
        self._flow_cache.clear()
        logger.debug("Flow cache cleared")

    @property
    def cache_stats(self) -> dict[str, int]:
        """Get cache statistics."""
        return {
            "size": len(self._flow_cache),
            "max_size": self._cache_size,
        }
